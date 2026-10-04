"""Zero-dependency dashboard server (Python standard library only).

Used automatically when FastAPI / uvicorn are not installed, so the live dashboard runs on any
Python 3.10+ with nothing extra. It serves the same page and the same JSON as the FastAPI
server, minus the WebSocket: the page it serves is marked ``data-transport="poll"``, so the browser
polls ``/api/state`` once a second instead of trying ``/ws``.

    GET  /              index.html            GET  /api/hello   static data + keyframe
    GET  /sw.js         service worker        GET  /api/state   current keyframe
    GET  /static/<f>    js / css              POST /api/cmd     command (400 if invalid)
    GET  /healthz       liveness + stats

A background thread advances the shared session at the configured tick rate. All session
methods take the session lock, so the request threads and the stepper never race.
"""
from __future__ import annotations

import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import unquote, urlparse

from atsc.dashboard.assets import index_html, service_worker_js
from atsc.dashboard.runtime import (NO_CACHE, TickStats, TokenBucket, encode, page_headers,
                                    static_response)
from atsc.dashboard.server import HTTP_COMMAND_RATE, MAX_COMMAND_BYTES, health_payload
from atsc.dashboard.session import CommandError, DashboardSession
from atsc.logging_utils import get_logger

log = get_logger("atsc.dashboard")


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(session: DashboardSession, host: str, port: int) -> DashboardHTTPServer:
    """Build (but do not start) the HTTP server; :func:`start_stepper` drives the session."""
    stats = TickStats()
    bucket = TokenBucket(*HTTP_COMMAND_RATE)

    class Handler(BaseHTTPRequestHandler):
        server_version = "atsc-dashboard"
        sys_version = ""

        def _send(self, code: int, body: bytes, ctype: str, extra: Optional[dict] = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, encode(obj).encode("utf-8"), "application/json",
                       {"Cache-Control": "no-store"})

        def do_HEAD(self) -> None:  # noqa: N802 - http.server naming
            self.do_GET()

        def do_GET(self) -> None:  # noqa: N802
            path = unquote(urlparse(self.path).path)
            if path == "/":
                self._send(200, index_html("poll"), "text/html; charset=utf-8", page_headers())
            elif path == "/sw.js":
                self._send(200, service_worker_js(), "text/javascript; charset=utf-8",
                           {"Cache-Control": NO_CACHE, "Service-Worker-Allowed": "/"})
            elif path.startswith("/static/"):
                status, headers, body = static_response(path[len("/static/"):],
                                                        self.headers.get("If-None-Match"))
                ctype = headers.pop("Content-Type")
                self._send(status, body, ctype, headers)
            elif path == "/api/hello":
                self._json(session.hello())
            elif path == "/api/state":
                self._json(session.snapshot())
            elif path == "/healthz":
                self._json(health_payload(session, stats, 0, "polling"))
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/api/cmd":
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            declared = self.headers.get("Content-Length", "0") or "0"
            if not declared.isdigit() or int(declared) > MAX_COMMAND_BYTES:
                self._json({"ok": False, "error": "command too large"}, 413)
                return
            raw = self.rfile.read(int(declared)) if int(declared) else b"{}"
            if not bucket.allow():
                self._json({"ok": False, "error": "too many commands, slow down"}, 429)
                return
            try:
                session.submit(json.loads(raw or b"{}"))
            except CommandError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
                return
            except ValueError:
                self._json({"ok": False, "error": "body must be JSON"}, 400)
                return
            self._json({"ok": True})

        def log_message(self, *args) -> None:  # keep the console quiet
            return

    httpd = DashboardHTTPServer((host, port), Handler)
    httpd.stats = stats  # type: ignore[attr-defined]
    return httpd


def start_stepper(session: DashboardSession, stats: TickStats,
                  stop: threading.Event) -> threading.Thread:
    """Advance the session every tick on a daemon thread (fixed rate, never dies)."""
    def run() -> None:
        next_at = time.monotonic()
        while not stop.is_set():
            started = time.perf_counter()
            try:
                session.tick()          # frames are discarded: polling clients read keyframes
            except Exception:
                stats.record_error()
                log.exception("dashboard tick failed - continuing with the next tick")
            stats.record_tick((time.perf_counter() - started) * 1000.0)
            next_at += session.tick_s
            delay = next_at - time.monotonic()
            if delay < 0:
                next_at = time.monotonic()
                delay = 0.0
            stop.wait(delay)

    thread = threading.Thread(target=run, name="atsc-stepper", daemon=True)
    thread.start()
    return thread


def run_stdlib_dashboard(session: DashboardSession, host: str, port: int,
                         open_browser: bool) -> None:
    httpd = make_server(session, host, port)
    stop = threading.Event()
    start_stepper(session, httpd.stats, stop)  # type: ignore[attr-defined]
    url = "http://%s:%d" % ("127.0.0.1" if host in ("0.0.0.0", "::") else host, port)
    log.info("Dashboard (built-in server, polling) running at %s", url)
    log.info("Tip: pip install fastapi uvicorn[standard] for the WebSocket version.")
    if open_browser:
        def _open() -> None:
            time.sleep(1.2)
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("Dashboard stopped.")
    finally:
        stop.set()
        httpd.server_close()
        session.close()                      # a signal board, if any: all-red, port closed
