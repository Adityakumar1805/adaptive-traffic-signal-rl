"""FastAPI + WebSocket server for the live dashboard.

One background task advances the shared :class:`~atsc.dashboard.session.DashboardSession`
every ``dashboard.tick_ms`` and pushes one compact frame to every connected browser; see the
session module for the wire protocol. Each viewer has its own outbox and sender task, so a
slow phone on a weak network can never stall the simulation or the other viewers: when its
backlog grows too long it is dropped and replaced by a fresh keyframe.

Endpoints

=========  =====  ==========================================================================
``/``      GET    the page (build id embedded)
``/sw.js`` GET    service worker: shows "Waking up the server..." while a sleeping free
                  instance boots, for returning visitors
``/static``GET    js/css, ETag-revalidated on every load so redeploys are picked up
``/ws``    WS     ``hello`` + frames out; commands, ``ping`` in
``/api/hello`` GET  same ``hello`` the socket sends (polling fallback)
``/api/state`` GET  current keyframe (polling fallback)
``/api/cmd``   POST a command; 400 with a readable error when it is invalid
``/healthz``   GET  cheap liveness + tick cost, payload size, memory, viewers, build
=========  =====  ==========================================================================

If FastAPI/uvicorn are unavailable, :func:`run_dashboard` falls back to a zero-dependency
standard-library server (:mod:`atsc.dashboard.stdlib_server`) with polling only.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import webbrowser
from contextlib import asynccontextmanager, suppress
from typing import Any, Callable, Dict, Optional, Set

# These are imported at MODULE level on purpose. This module uses
# `from __future__ import annotations`, so FastAPI sees every annotation as a string and
# resolves it against this module's globals. When WebSocket was imported only inside
# build_app(), FastAPI could not resolve "WebSocket", treated the socket argument as a
# required query parameter and rejected every connection with HTTP 403 (the live-site
# "WebSocket connection failed" bug). tests/test_dashboard_ws.py guards against a regression.
try:
    from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import Response
except ImportError:  # pragma: no cover - the stdlib fallback server does not need FastAPI
    FastAPI = Request = WebSocket = WebSocketDisconnect = Response = None  # type: ignore[assignment]

from atsc.dashboard.assets import PROTOCOL_VERSION, build_id, index_html, service_worker_js
from atsc.dashboard.runtime import (NO_CACHE, TickStats, TokenBucket, encode, memory_mb,
                                    page_headers, static_response)
from atsc.dashboard.session import CommandError, DashboardSession
from atsc.logging_utils import get_logger

log = get_logger("atsc.dashboard")

MAX_COMMAND_BYTES = 2048          # commands are tiny JSON objects
MAX_BACKLOG = 25                  # queued frames before a viewer is resynced with a keyframe
HEARTBEAT_S = 2.0                 # "still alive" message while nothing changes (paused)
WS_COMMAND_RATE = (10.0, 20.0)    # per viewer: tokens/s, burst
HTTP_COMMAND_RATE = (20.0, 40.0)  # all POST /api/cmd callers together


# --------------------------------------------------------------------------- #
# viewers
# --------------------------------------------------------------------------- #
class _Viewer:
    __slots__ = ("sock", "queue", "task", "bucket")

    def __init__(self, sock) -> None:
        self.sock = sock
        self.queue: "asyncio.Queue[Optional[str]]" = asyncio.Queue()
        self.task: Optional[asyncio.Task] = None
        self.bucket = TokenBucket(*WS_COMMAND_RATE)


class Hub:
    """The set of connected viewers and their per-viewer sender tasks."""

    def __init__(self) -> None:
        self.viewers: Set[_Viewer] = set()

    def add(self, sock, hello: str) -> _Viewer:
        viewer = _Viewer(sock)
        viewer.queue.put_nowait(hello)            # first message, before any broadcast frame
        viewer.task = asyncio.create_task(self._sender(viewer))
        self.viewers.add(viewer)
        return viewer

    async def _sender(self, viewer: _Viewer) -> None:
        try:
            while True:
                msg = await viewer.queue.get()
                if msg is None:
                    return
                await viewer.sock.send_text(msg)
        except Exception:
            self.viewers.discard(viewer)

    def send(self, viewer: _Viewer, msg: str) -> None:
        if viewer in self.viewers:
            viewer.queue.put_nowait(msg)

    def broadcast(self, payload: str, resync: Callable[[], str]) -> None:
        """Queue ``payload`` for every viewer; a viewer that has fallen too far behind gets
        its backlog dropped and a keyframe of the current state instead."""
        keyframe: Optional[str] = None
        for viewer in list(self.viewers):
            if viewer.queue.qsize() >= MAX_BACKLOG:
                while not viewer.queue.empty():
                    viewer.queue.get_nowait()
                if keyframe is None:
                    keyframe = resync()
                viewer.queue.put_nowait(keyframe)
            else:
                viewer.queue.put_nowait(payload)

    async def remove(self, viewer: _Viewer) -> None:
        self.viewers.discard(viewer)
        if viewer.task is not None and not viewer.task.done():
            viewer.queue.put_nowait(None)
            viewer.task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await viewer.task

    async def close_all(self) -> None:
        for viewer in list(self.viewers):
            with suppress(Exception):
                await viewer.sock.close(code=1001)
            await self.remove(viewer)


# --------------------------------------------------------------------------- #
# the broadcast loop
# --------------------------------------------------------------------------- #
async def broadcast_loop(session: DashboardSession, hub: Hub, stats: TickStats) -> None:
    """Advance the race every tick and push one frame to every viewer. Never dies: an
    exception in one tick is logged and counted, and the next tick runs on schedule."""
    loop = asyncio.get_running_loop()
    next_at = loop.time()
    last_sent = loop.time()
    while True:
        started = time.perf_counter()
        try:
            frame = session.tick()
            if hub.viewers:
                now = loop.time()
                if frame is not None:
                    payload = encode(frame)
                    hub.broadcast(payload, lambda: encode(session.snapshot()))
                    stats.record_payload(len(payload), bool(frame.get("kf")))
                    last_sent = now
                elif now - last_sent >= HEARTBEAT_S:
                    hub.broadcast(encode({"t": "hb", "k": session.tick_count}),
                                  lambda: encode(session.snapshot()))
                    last_sent = now
        except Exception:
            stats.record_error()
            log.exception("dashboard tick failed - continuing with the next tick")
        stats.record_tick((time.perf_counter() - started) * 1000.0)
        next_at += session.tick_s
        delay = next_at - loop.time()
        if delay < 0:                    # overloaded: skip ahead instead of spiralling
            next_at = loop.time()
            delay = 0.0
        await asyncio.sleep(delay)


def health_payload(session: DashboardSession, stats: TickStats, viewers: int,
                   transport: str) -> Dict[str, Any]:
    from atsc import __version__
    body: Dict[str, Any] = {"ok": True, "version": __version__, "build": build_id(),
                            "protocol": PROTOCOL_VERSION, "transport": transport,
                            "viewers": viewers}
    body.update(session.info())
    body.update(stats.snapshot())
    body.update(memory_mb())
    return body


# --------------------------------------------------------------------------- #
# the FastAPI app
# --------------------------------------------------------------------------- #
def build_app(session: DashboardSession):
    """Construct the FastAPI application bound to a session."""
    if FastAPI is None:  # pragma: no cover - guarded by run_dashboard
        raise RuntimeError("FastAPI is not installed; use the stdlib server instead")

    hub = Hub()
    stats = TickStats()
    http_bucket = TokenBucket(*HTTP_COMMAND_RATE)

    @asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(broadcast_loop(session, hub, stats))
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await hub.close_all()

    app = FastAPI(title="Adaptive Traffic Signal Control - Live Dashboard", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.session = session
    app.state.hub = hub
    app.state.stats = stats

    def json_response(obj: Any, status: int = 200) -> Response:
        return Response(content=encode(obj), status_code=status, media_type="application/json",
                        headers={"Cache-Control": "no-store"})

    # HEAD as well as GET on the page and the health check: uptime monitors (often used to
    # keep a free instance awake) probe with HEAD, and a 405 reads as "down".
    @app.api_route("/", methods=["GET", "HEAD"])
    async def index() -> Response:
        return Response(content=index_html(), media_type="text/html; charset=utf-8",
                        headers=page_headers())

    @app.get("/sw.js")
    async def service_worker() -> Response:
        return Response(content=service_worker_js(), media_type="text/javascript; charset=utf-8",
                        headers={"Cache-Control": NO_CACHE, "Service-Worker-Allowed": "/"})

    @app.get("/static/{rel:path}")
    async def static(rel: str, request: Request) -> Response:
        status, headers, body = static_response(rel, request.headers.get("if-none-match"))
        media = headers.pop("Content-Type")
        return Response(content=body, status_code=status, media_type=media, headers=headers)

    @app.get("/api/hello")
    async def api_hello() -> Response:
        return json_response(session.hello())

    @app.get("/api/state")
    async def api_state() -> Response:
        return json_response(session.snapshot())

    @app.post("/api/cmd")
    async def api_cmd(request: Request) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_COMMAND_BYTES):
            return json_response({"ok": False, "error": "command too large"}, 413)
        body = await request.body()
        if len(body) > MAX_COMMAND_BYTES:
            return json_response({"ok": False, "error": "command too large"}, 413)
        if not http_bucket.allow():
            return json_response({"ok": False, "error": "too many commands, slow down"}, 429)
        try:
            session.submit(json.loads(body or b"{}"))
        except (ValueError, CommandError) as exc:   # JSONDecodeError is a ValueError
            message = str(exc) if isinstance(exc, CommandError) else "body must be JSON"
            return json_response({"ok": False, "error": message}, 400)
        return json_response({"ok": True})

    @app.api_route("/healthz", methods=["GET", "HEAD"])
    async def healthz() -> Response:
        return json_response(health_payload(session, stats, len(hub.viewers), "websocket"))

    @app.websocket("/ws")
    async def ws_endpoint(sock: WebSocket) -> None:
        await sock.accept()
        if len(hub.viewers) >= session.max_clients:
            with suppress(Exception):
                await sock.send_text(encode({"t": "busy", "retry_s": 30}))
                await sock.close(code=1013)
            return
        viewer = hub.add(sock, encode(session.hello()))
        try:
            while True:
                raw = await sock.receive_text()
                _on_message(session, hub, viewer, raw)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # malformed frames, binary frames, network errors
            log.debug("viewer dropped: %s", exc)
        finally:
            await hub.remove(viewer)

    return app


def _on_message(session: DashboardSession, hub: Hub, viewer: _Viewer, raw: str) -> None:
    """Handle one message from a viewer: ``ping`` or a command (answered with an ``ack``)."""
    if len(raw) > MAX_COMMAND_BYTES:
        return
    try:
        msg = json.loads(raw)
    except ValueError:
        return
    if not isinstance(msg, dict):
        return
    if msg.get("a") == "ping":
        hub.send(viewer, encode({"t": "pong", "ts": msg.get("ts"), "k": session.tick_count}))
        return
    ref = msg.get("i")
    if not viewer.bucket.allow():
        hub.send(viewer, encode({"t": "ack", "i": ref, "ok": False,
                                 "error": "too many commands, slow down"}))
        return
    try:
        session.submit(msg)
    except CommandError as exc:
        hub.send(viewer, encode({"t": "ack", "i": ref, "ok": False, "error": str(exc)}))
        return
    hub.send(viewer, encode({"t": "ack", "i": ref, "ok": True}))


# --------------------------------------------------------------------------- #
# local entry point (python run.py demo)
# --------------------------------------------------------------------------- #
def _has_fastapi() -> bool:
    import importlib.util
    return (FastAPI is not None
            and importlib.util.find_spec("uvicorn") is not None
            and (importlib.util.find_spec("websockets") is not None
                 or importlib.util.find_spec("wsproto") is not None))


def _open_later(url: str) -> None:
    def _open() -> None:
        time.sleep(1.5)
        with suppress(Exception):
            webbrowser.open(url)
    threading.Thread(target=_open, daemon=True).start()


def run_dashboard(config_path: Optional[str] = None, host: Optional[str] = None,
                  port: Optional[int] = None, open_browser: Optional[bool] = None) -> None:
    """Start the dashboard: FastAPI + uvicorn (WebSocket) when available, else the
    zero-dependency standard-library server (polling)."""
    session = DashboardSession(config_path)
    session.play()

    host = host or str(session.cfg.dashboard.host)
    port = int(port or session.cfg.dashboard.port)
    do_open = session.cfg.dashboard.open_browser if open_browser is None else open_browser
    url = "http://%s:%d" % ("127.0.0.1" if host in ("0.0.0.0", "::") else host, port)

    if not session.ckpt_exists:
        log.warning("No trained checkpoint found - the RL panel runs an UNTRAINED policy. "
                    "Run 'python run.py train --quick' for a real model.")

    if _has_fastapi():
        import uvicorn
        app = build_app(session)
        log.info("Dashboard starting (FastAPI/WebSocket) at %s", url)
        if do_open:
            _open_later(url)
        uvicorn.run(app, host=host, port=port, log_level="warning",
                    ws_max_size=65536, ws_ping_interval=20.0, ws_ping_timeout=20.0)
    else:
        log.info("FastAPI/uvicorn not installed - using the built-in standard-library server.")
        from atsc.dashboard.stdlib_server import run_stdlib_dashboard
        run_stdlib_dashboard(session, host, port, do_open)
