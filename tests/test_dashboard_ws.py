"""The FastAPI server: WebSocket streaming, the HTTP API, static files and their guards.

The first test is the regression test for the live-site bug: with WebSocket imported only
inside build_app(), FastAPI could not resolve the string annotation of the socket argument,
treated it as a required *query parameter* and answered every /ws handshake with HTTP 403.
"""
import asyncio
import http.client
import json
import socket
import threading
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from atsc.dashboard import server as server_mod  # noqa: E402
from atsc.dashboard.assets import build_id  # noqa: E402
from atsc.dashboard.server import build_app  # noqa: E402
from atsc.dashboard.session import DashboardSession  # noqa: E402


@pytest.fixture(scope="module")
def session():
    s = DashboardSession()
    s.play()
    return s


@pytest.fixture(scope="module")
def client(session):
    with TestClient(build_app(session)) as c:      # runs the lifespan: the broadcast loop ticks
        yield c


def _receive_until(ws, kind, limit=200):
    for _ in range(limit):
        msg = json.loads(ws.receive_text())
        if msg.get("t") == kind:
            return msg
    raise AssertionError(f"no {kind!r} message")


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #
def test_websocket_route_takes_no_query_parameters(session):
    app = build_app(session)
    route = next(r for r in app.routes if getattr(r, "path", None) == "/ws")
    assert route.dependant.query_params == [], "the socket argument became a query parameter (HTTP 403)"
    assert server_mod.WebSocket is not None


def test_websocket_sends_hello_then_frames(client):
    with client.websocket_connect("/ws") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["t"] == "hello" and hello["v"] == 2 and hello["build"] == build_id()
        assert hello["state"]["kf"] == 1
        frame = _receive_until(ws, "f")
        assert "k" in frame


def test_websocket_commands_are_acknowledged(client):
    with client.websocket_connect("/ws") as ws:
        _receive_until(ws, "hello")
        ws.send_text(json.dumps({"a": "speed", "v": 2, "i": 7}))
        ack = _receive_until(ws, "ack")
        assert ack == {"t": "ack", "i": 7, "ok": True}
        ws.send_text(json.dumps({"a": "scenario", "v": "nowhere", "i": 8}))
        ack = _receive_until(ws, "ack")
        assert ack["i"] == 8 and ack["ok"] is False and "unknown scenario" in ack["error"]
        ws.send_text(json.dumps({"a": "ping", "ts": 123.5}))
        pong = _receive_until(ws, "pong")
        assert pong["ts"] == 123.5
        ws.send_text("not json")                                   # ignored, socket stays up
        ws.send_text(json.dumps([1, 2, 3]))
        ws.send_text(json.dumps({"a": "speed", "v": 1, "i": 9}))
        assert _receive_until(ws, "ack")["i"] == 9


def test_websocket_rate_limit(client):
    with client.websocket_connect("/ws") as ws:
        _receive_until(ws, "hello")
        for i in range(40):
            ws.send_text(json.dumps({"a": "play", "i": i}))
        acks = [_receive_until(ws, "ack") for _ in range(40)]
        assert any(not a["ok"] and "slow down" in a["error"] for a in acks)


def test_websocket_full_server_says_busy(session):
    original = session.max_clients
    session.max_clients = 0
    try:
        with TestClient(build_app(session)) as c, c.websocket_connect("/ws") as ws:
            assert json.loads(ws.receive_text()) == {"t": "busy", "retry_s": 30}
    finally:
        session.max_clients = original


def test_viewer_backlog_is_replaced_by_a_keyframe():
    """A stalled viewer never holds more than MAX_BACKLOG frames."""
    hub = server_mod.Hub()

    class Sock:
        async def send_text(self, _msg):
            await asyncio.sleep(3600)

    async def scenario():
        viewer = hub.add(Sock(), "hello")
        for i in range(server_mod.MAX_BACKLOG * 3):
            hub.broadcast(f"frame{i}", lambda: "KEYFRAME")
        size = viewer.queue.qsize()
        await hub.remove(viewer)
        return size

    assert asyncio.run(scenario()) <= server_mod.MAX_BACKLOG + 1


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def test_index_is_templated_and_hardened(client):
    res = client.get("/")
    assert res.status_code == 200
    body = res.text
    assert "__ATSC_" not in body and f'data-build="{build_id()}"' in body
    assert 'data-transport="ws"' in body
    assert res.headers["cache-control"] == "no-cache"
    assert res.headers["x-atsc-build"] == build_id()
    csp = res.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp


def test_page_and_health_answer_head(client):
    # uptime monitors probe with HEAD; a 405 would read as "site down"
    for path in ("/", "/healthz"):
        res = client.head(path)
        assert res.status_code == 200, path
        assert res.content == b""
    assert client.head("/").headers["x-atsc-build"] == build_id()


def test_service_worker_is_served_from_the_root(client):
    res = client.get("/sw.js")
    assert res.status_code == 200 and res.headers["service-worker-allowed"] == "/"
    assert build_id() in res.text and "__ATSC_BUILD__" not in res.text


def test_static_files_revalidate_with_etags(client):
    res = client.get("/static/js/main.js")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/javascript")
    assert res.headers["x-content-type-options"] == "nosniff"
    etag = res.headers["etag"]
    again = client.get("/static/js/main.js", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""


@pytest.mark.parametrize("path", [
    "/static/../asgi.py", "/static/%2e%2e/config.yaml", "/static/js/../../server.py",
    "/static/%2e%2e%2f%2e%2e%2fconfig.yaml", "/static/", "/static/js", "/static/nope.js",
    "/static/..%5c..%5cconfig.yaml", "/static/%00.js",
])
def test_static_files_cannot_escape_the_static_folder(client, path):
    res = client.get(path)
    assert res.status_code == 404
    assert b"seed:" not in res.content and b"import" not in res.content


def test_api_hello_state_and_health(client):
    hello = client.get("/api/hello").json()
    assert hello["t"] == "hello"
    state = client.get("/api/state").json()
    assert state["t"] == "f" and state["kf"] == 1
    health = client.get("/healthz").json()
    for key in ("ok", "build", "protocol", "viewers", "tick_ms", "payload_bytes", "rss_mb",
                "uptime_s", "tick_errors", "checkpoint", "cpu_percent", "version"):
        assert key in health
    assert health["ok"] is True and health["tick_errors"] == 0
    assert set(health["cpu_percent"]) == {"last_30s", "since_start"}


def test_tick_stats_measure_process_cpu():
    from atsc.dashboard.runtime import TickStats
    clock = {"wall": 1000.0, "cpu": 50.0}
    stats = TickStats(wall_clock=lambda: clock["wall"], cpu_clock=lambda: clock["cpu"])
    assert stats.cpu_percent() == {"last_30s": None, "since_start": None}   # nothing elapsed
    for _ in range(40):                  # 40 s of ticks at 25 % of a core...
        clock["wall"] += 1.0
        clock["cpu"] += 0.25
        stats.record_tick(1.0)
    for _ in range(30):                  # ...then 30 s at 2 %
        clock["wall"] += 1.0
        clock["cpu"] += 0.02
        stats.record_tick(1.0)
    cpu = stats.snapshot()["cpu_percent"]
    assert cpu["last_30s"] == 2.0
    assert cpu["since_start"] == round(100 * (40 * 0.25 + 30 * 0.02) / 70, 1)


def test_api_cmd_validates(client):
    assert client.post("/api/cmd", json={"a": "speed", "v": 1}).json() == {"ok": True}
    bad = client.post("/api/cmd", json={"a": "scenario", "v": "mars"})
    assert bad.status_code == 400 and "unknown scenario" in bad.json()["error"]
    junk = client.post("/api/cmd", content=b"{not json", headers={"content-type": "application/json"})
    assert junk.status_code == 400
    huge = client.post("/api/cmd", content=b"x" * 5000)
    assert huge.status_code == 413


def test_docs_are_not_exposed(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


# --------------------------------------------------------------------------- #
# the real production stack: uvicorn + the websockets library
# --------------------------------------------------------------------------- #
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_real_uvicorn_serves_the_websocket():
    uvicorn = pytest.importorskip("uvicorn")
    ws_client = pytest.importorskip("websockets.sync.client")
    session = DashboardSession()
    session.play()
    port = _free_port()
    config = uvicorn.Config(build_app(session), host="127.0.0.1", port=port, log_level="error",
                            ws="websockets", ws_max_size=65536)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 20
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started, "uvicorn did not start"
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("HEAD", "/healthz")
        res = conn.getresponse()
        assert res.status == 200 and res.read() == b""
        conn.close()
        with ws_client.connect(f"ws://127.0.0.1:{port}/ws", open_timeout=10) as ws:
            hello = json.loads(ws.recv(timeout=10))
            assert hello["t"] == "hello" and hello["v"] == 2
            for _ in range(50):
                msg = json.loads(ws.recv(timeout=10))
                if msg["t"] == "f":
                    break
            else:
                raise AssertionError("no frame streamed")
            ws.send(json.dumps({"a": "inject", "kind": "police", "i": 1}))
            for _ in range(50):
                msg = json.loads(ws.recv(timeout=10))
                if msg["t"] == "ack":
                    assert msg == {"t": "ack", "i": 1, "ok": True}
                    break
            else:
                raise AssertionError("no ack")
    finally:
        server.should_exit = True
        thread.join(10)
