"""The zero-dependency fallback server: same page and JSON as the FastAPI one, polling only."""
import http.client
import json
import threading

import pytest

from atsc.dashboard.assets import build_id
from atsc.dashboard.session import DashboardSession
from atsc.dashboard.stdlib_server import make_server, start_stepper


@pytest.fixture(scope="module")
def server():
    session = DashboardSession()
    session.play()
    httpd = make_server(session, "127.0.0.1", 0)
    stop = threading.Event()
    start_stepper(session, httpd.stats, stop)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    stop.set()
    httpd.shutdown()
    httpd.server_close()


def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, {k.lower(): v for k, v in res.getheaders()}, data


def test_page_tells_the_browser_to_poll(server):
    status, headers, body = _req(server, "GET", "/")
    text = body.decode()
    assert status == 200 and 'data-transport="poll"' in text and build_id() in text
    assert headers["x-atsc-build"] == build_id() and "content-security-policy" in headers


def test_head_has_no_body(server):
    status, headers, body = _req(server, "HEAD", "/")
    assert status == 200 and body == b"" and int(headers["content-length"]) > 0


def test_json_endpoints(server):
    status, _h, body = _req(server, "GET", "/api/hello")
    assert status == 200 and json.loads(body)["t"] == "hello"
    status, _h, body = _req(server, "GET", "/api/state")
    assert status == 200 and json.loads(body)["kf"] == 1
    status, _h, body = _req(server, "GET", "/healthz")
    health = json.loads(body)
    assert status == 200 and health["ok"] and health["transport"] == "polling"


def test_commands(server):
    ok = _req(server, "POST", "/api/cmd", json.dumps({"a": "inject", "kind": "fire"}),
              {"Content-Type": "application/json"})
    assert ok[0] == 200 and json.loads(ok[2]) == {"ok": True}
    bad = _req(server, "POST", "/api/cmd", json.dumps({"a": "speed", "v": "x"}))
    assert bad[0] == 400 and "number" in json.loads(bad[2])["error"]
    junk = _req(server, "POST", "/api/cmd", b"{oops")
    assert junk[0] == 400
    huge = _req(server, "POST", "/api/cmd", b"x" * 4000)
    assert huge[0] == 413
    assert _req(server, "POST", "/elsewhere", b"{}")[0] == 404


@pytest.mark.parametrize("path", ["/static/../asgi.py", "/static/%2e%2e/config.yaml",
                                  "/static/js/../../server.py", "/static/..%2f..%2fconfig.yaml",
                                  "/static/", "/nope", "/ws"])
def test_nothing_outside_static_is_served(server, path):
    status, _h, body = _req(server, "GET", path)
    assert status == 404 and b"seed:" not in body


def test_static_and_service_worker(server):
    status, headers, _b = _req(server, "GET", "/static/js/renderer.js")
    assert status == 200 and headers["content-type"].startswith("text/javascript")
    again = _req(server, "GET", "/static/js/renderer.js", headers={"If-None-Match": headers["etag"]})
    assert again[0] == 304
    status, headers, body = _req(server, "GET", "/sw.js")
    assert status == 200 and headers["service-worker-allowed"] == "/" and build_id().encode() in body
