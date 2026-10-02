"""Measure the dashboard server: per-tick cost and payload, and (optionally) a load test.

    python tools/bench_server.py                       # in-process: tick ms + frame bytes
    python tools/bench_server.py --clients 50 --url http://127.0.0.1:8000 --seconds 60

The in-process mode times ``session.tick()`` plus JSON encoding for each scenario and speed,
exactly the work the broadcast loop does per tick. The load-test mode opens N real
WebSocket viewers (needs the ``websockets`` package), counts what each receives, and reads
``/healthz`` for the server's own tick statistics and resident memory.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def in_process(ticks: int) -> None:
    import logging
    logging.disable(logging.INFO)
    from atsc.dashboard.runtime import encode
    from atsc.dashboard.session import DashboardSession

    print(f"{'scenario':8s} {'speed':>5s} | {'tick+encode ms (mean / p95)':>27s} | "
          f"{'delta bytes (mean / max)':>24s} | {'KB/s/viewer':>11s} | hello B")
    for scenario in ("medium", "rush"):
        for speed in (1, 8):
            s = DashboardSession()
            s.set_scenario(scenario)
            s.set_speed(speed)
            s.play()
            s.tick()
            wall, sizes = [], []
            for _ in range(ticks):
                t0 = time.perf_counter()
                frame = s.tick()
                payload = encode(frame) if frame is not None else ""
                wall.append((time.perf_counter() - t0) * 1000)
                if frame is not None and not frame.get("kf"):
                    sizes.append(len(payload.encode()))
            p95 = sorted(wall)[int(0.95 * len(wall))]
            rate = st.mean(sizes) * (1000.0 / s.tick_ms) / 1024
            print(f"{scenario:8s} {speed:5d} | {st.mean(wall):12.2f} / {p95:6.2f}        | "
                  f"{st.mean(sizes):10.0f} / {max(sizes):6d}    | {rate:11.1f} | "
                  f"{len(encode(s.hello()))}")


def load_test(url: str, clients: int, seconds: float) -> None:
    from websockets.sync.client import connect

    base = url.rstrip("/")
    ws_url = base.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    stats = [{"bytes": 0, "msgs": 0, "frames": 0, "error": None} for _ in range(clients)]
    stop = threading.Event()

    def viewer(i: int) -> None:
        try:
            with connect(ws_url, open_timeout=20, max_size=2 ** 22) as ws:
                while not stop.is_set():
                    try:
                        msg = ws.recv(timeout=1)
                    except TimeoutError:
                        continue
                    stats[i]["bytes"] += len(msg)
                    stats[i]["msgs"] += 1
                    stats[i]["frames"] += msg.startswith('{"t":"f"')
        except Exception as exc:  # report, do not crash the run
            stats[i]["error"] = repr(exc)

    threads = [threading.Thread(target=viewer, args=(i,), daemon=True) for i in range(clients)]
    for t in threads:
        t.start()
        time.sleep(0.02)
    time.sleep(seconds)
    with urllib.request.urlopen(base + "/healthz", timeout=10) as r:
        health = json.loads(r.read())
    stop.set()
    for t in threads:
        t.join(5)
    ok = [s for s in stats if s["error"] is None]
    kbps = [s["bytes"] / seconds / 1024 for s in ok]
    print(json.dumps({
        "clients": clients, "connected": len(ok),
        "errors": sorted({s["error"] for s in stats if s["error"]})[:3],
        "per_viewer_KB_s": round(st.mean(kbps), 2) if kbps else None,
        "frames_per_viewer_s": round(st.mean(s["frames"] for s in ok) / seconds, 2) if ok else None,
        "server": {k: health.get(k) for k in ("viewers", "tick_ms", "payload_bytes", "rss_mb",
                                               "peak_rss_mb", "tick_errors", "speed", "scenario")},
    }, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticks", type=int, default=400)
    ap.add_argument("--url", help="server base URL for the load test, e.g. http://127.0.0.1:8000")
    ap.add_argument("--clients", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=30.0)
    args = ap.parse_args()
    if args.clients:
        if not args.url:
            ap.error("--clients needs --url")
        load_test(args.url, args.clients, args.seconds)
    else:
        in_process(args.ticks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
