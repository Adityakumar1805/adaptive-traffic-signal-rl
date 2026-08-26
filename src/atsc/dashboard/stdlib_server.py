"""Zero-dependency dashboard server (Python standard library only).

Used automatically when FastAPI / uvicorn are not installed, so the live dashboard runs
on *any* Python 3.10+ with nothing extra. It serves the same single-page UI and exposes:

    GET  /              -> index.html
    GET  /static/*      -> css / js assets
    GET  /api/state     -> JSON snapshot (the browser polls this)
    POST /api/cmd       -> control command (play/pause/step/speed/scenario/emergency)

A background thread advances the shared session on the configured tick. The front end
uses a WebSocket when the FastAPI server is running and transparently falls back to
polling ``/api/state`` here — same UI, same behaviour, just a slightly lower refresh rate.
"""
from __future__ import annotations

import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from atsc.dashboard.session import DashboardSession
from atsc.logging_utils import get_logger

log = get_logger("atsc.dashboard")
STATIC_DIR = Path(__file__).parent / "static"

_CTYPES = {".css": "text/css", ".js": "application/javascript",
           ".html": "text/html", ".png": "image/png"}


def run_stdlib_dashboard(session: DashboardSession, host: str, port: int,
                         open_browser: bool, handle_command) -> None:
    tick_s = max(0.05, int(session.cfg.dashboard.tick_ms) / 1000.0)

    def stepper():
        while True:
            try:
                session.maybe_step()
            except Exception as exc:  # keep the demo alive no matter what
                log.warning("step error: %s", exc)
            time.sleep(tick_s)

    threading.Thread(target=stepper, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self._send(200, (STATIC_DIR / "index.html").read_bytes(), "text/html")
            elif path == "/api/state":
                self._send(200, json.dumps(session.snapshot()).encode(), "application/json")
            elif path.startswith("/static/"):
                f = STATIC_DIR / path[len("/static/"):]
                if f.exists() and f.is_file():
                    self._send(200, f.read_bytes(),
                               _CTYPES.get(f.suffix, "application/octet-stream"))
                else:
                    self._send(404, b"not found", "text/plain")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/cmd":
                n = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(n) if n else b"{}"
                try:
                    handle_command(session, json.loads(raw or b"{}"))
                except Exception:
                    pass
                self._send(200, b'{"ok":true}', "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *args):  # silence default request logging
            return

    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    log.info("Dashboard (built-in server) running at %s", url)
    log.info("Tip: install FastAPI + uvicorn for the WebSocket version (smoother updates).")

    if open_browser:
        def _open():
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
        httpd.shutdown()
