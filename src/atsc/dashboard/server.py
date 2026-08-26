"""FastAPI + WebSocket server for the real-time dashboard.

Serves the single-page UI and pushes a JSON snapshot every dashboard.tick_ms over a
WebSocket. Control messages (play/pause/step/speed/scenario/emergency) arrive on the same
socket (or via POST /api/cmd for polling clients). A background asyncio task advances the
shared DashboardSession so all connected clients see the same synchronized race.

If FastAPI/uvicorn are unavailable, run_dashboard falls back to a zero-dependency
standard-library server (atsc.dashboard.stdlib_server) so the demo still runs.
"""
from __future__ import annotations

import asyncio
import json
import threading
import webbrowser
from pathlib import Path
from typing import Optional, Set

from atsc.dashboard.session import DashboardSession
from atsc.logging_utils import get_logger

log = get_logger("atsc.dashboard")
STATIC_DIR = Path(__file__).parent / "static"


def build_app(session: DashboardSession):
    """Construct the FastAPI application bound to a session."""
    from fastapi import FastAPI, WebSocket
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="Adaptive Traffic Signal Control - Live Dashboard")
    clients: Set = set()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/state")
    async def state():
        return session.snapshot()

    @app.post("/api/cmd")
    async def cmd(payload: dict):
        _handle_command(session, payload or {})
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(sock: "WebSocket"):
        await sock.accept()
        clients.add(sock)
        try:
            await sock.send_text(json.dumps(session.snapshot()))
            while True:
                msg = await sock.receive_text()
                _handle_command(session, json.loads(msg))
        except Exception:
            pass
        finally:
            clients.discard(sock)

    async def broadcaster():
        tick_s = max(0.05, int(session.cfg.dashboard.tick_ms) / 1000.0)
        while True:
            session.maybe_step()
            if clients:
                payload = json.dumps(session.snapshot())
                for c in list(clients):
                    try:
                        await c.send_text(payload)
                    except Exception:
                        clients.discard(c)
            await asyncio.sleep(tick_s)

    @app.on_event("startup")
    async def _startup():
        asyncio.create_task(broadcaster())

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


def _handle_command(session: DashboardSession, cmd: dict) -> None:
    action = cmd.get("action")
    if action == "play":
        session.play()
    elif action == "pause":
        session.pause()
    elif action == "step":
        session.step_once()
    elif action == "reset":
        session.reset()
    elif action == "speed":
        session.set_speed(cmd.get("value", 1.0))
    elif action == "scenario":
        session.set_scenario(cmd.get("value", "medium"))
    elif action == "emergency":
        session.inject_emergency(cmd.get("corridor", "ew"))


def _has_fastapi() -> bool:
    import importlib.util
    return (importlib.util.find_spec("fastapi") is not None
            and importlib.util.find_spec("uvicorn") is not None)


def _open_later(url: str) -> None:
    def _open():
        import time
        time.sleep(1.5)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()


def run_dashboard(config_path: Optional[str] = None, host: Optional[str] = None,
                  port: Optional[int] = None, open_browser: Optional[bool] = None) -> None:
    """Start the dashboard. Uses FastAPI+uvicorn (WebSocket) when available, else a
    zero-dependency standard-library server with polling."""
    session = DashboardSession(config_path)
    session.play()

    host = host or str(session.cfg.dashboard.host)
    port = int(port or session.cfg.dashboard.port)
    do_open = session.cfg.dashboard.open_browser if open_browser is None else open_browser

    if not session.ckpt_exists:
        log.warning("No trained checkpoint found - RL panel uses an UNTRAINED policy. "
                    "Run 'python run.py train --quick' for a real model.")

    if _has_fastapi():
        import uvicorn
        app = build_app(session)
        log.info("Dashboard starting (FastAPI/WebSocket) at http://%s:%d", host, port)
        if do_open:
            _open_later("http://%s:%d" % (host, port))
        uvicorn.run(app, host=host, port=port, log_level="warning")
    else:
        log.info("FastAPI not installed - using the built-in standard-library server.")
        from atsc.dashboard.stdlib_server import run_stdlib_dashboard
        run_stdlib_dashboard(session, host, port, do_open, _handle_command)
