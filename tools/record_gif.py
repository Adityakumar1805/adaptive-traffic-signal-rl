"""Record the README animation: a rush-hour race with one ambulance dispatched.

    python tools/record_gif.py                       # -> docs/screenshots/dashboard_demo.gif

Starts the hosted entrypoint (``uvicorn asgi:app``) on a free local port, opens the page in
headless Chromium at 1440 x 900, switches to Rush hour, fast-forwards until queues have built
up, drops to 0.5x, presses **Ambulance** and records until both grids have cleared it, then
holds the last frame. Frames come from Chromium's screencast (about 20 per second) and are
turned into a GIF with an optimised palette.

Needs ``pip install playwright`` (plus ``python -m playwright install chromium``) and
ffmpeg on the PATH. ``--python`` picks the interpreter that runs the server, e.g. a venv
made from requirements-deploy.txt, so the status bar shows the NumPy network as on the
hosted site.
"""
from __future__ import annotations

import argparse
import base64
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
W, H = 1440, 900


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(python: str, port: int) -> subprocess.Popen:
    proc = subprocess.Popen([python, "-m", "uvicorn", "asgi:app", "--host", "127.0.0.1",
                             "--port", str(port), "--ws-max-size", "65536", "--log-level", "warning"],
                            cwd=ROOT, env=dict(os.environ, PYTHONUNBUFFERED="1"),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2)
            return proc
        except Exception:
            time.sleep(0.3)
    proc.terminate()
    raise SystemExit("the dashboard server did not start")


def record(url: str, frames_dir: Path, warm_s: float, speed: float, max_s: float):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1)
        page = ctx.new_page()
        page.goto(url, wait_until="load")
        page.wait_for_function("() => document.getElementById('statusText').textContent === 'live · streaming'",
                               timeout=30000)
        speeds = page.evaluate("() => window.__ATSC__.model.hello.speeds")

        def set_speed(x: float) -> None:
            page.eval_on_selector("#speed", "(el, i) => { el.value = String(i); el.dispatchEvent(new Event('change')); }",
                                  speeds.index(x))
            page.wait_for_function(f"() => window.__ATSC__.model.g.sp === {x}", timeout=8000)

        page.select_option("#scenario", "rush")
        page.wait_for_function("() => window.__ATSC__.model.g.sc === 'rush'", timeout=8000)
        set_speed(speeds[-1])                          # fast-forward until the queues build up
        page.wait_for_function(f"() => window.__ATSC__.model.g.e >= {warm_s}", timeout=120000)
        set_speed(speed)
        page.wait_for_timeout(2500)                    # let the playback settle at the new speed

        bottom = page.evaluate("""() => Math.max(...[...document.querySelectorAll('.stage, .kpi')]
                                    .map((el) => el.getBoundingClientRect().bottom))""")
        crop_h = int(min(H, bottom + 14)) // 2 * 2

        cdp = ctx.new_cdp_session(page)
        frames = []

        def on_frame(ev) -> None:
            frames.append((ev["metadata"]["timestamp"], ev["data"]))
            cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})

        cdp.on("Page.screencastFrame", on_frame)
        cdp.send("Page.startScreencast", {"format": "png", "maxWidth": W, "maxHeight": H})
        t0 = time.time()
        clicked = done_at = None
        while time.time() - t0 < max_s:
            now = time.time() - t0
            if clicked is None and now > 1.6:
                page.click("#injectRow button[data-kind='ambulance']")
                clicked = now
            if clicked is not None and done_at is None and page.evaluate(
                    """() => document.getElementById('emBanner').hidden &&
                             document.querySelector('[data-k="em-ft"]').textContent.trim() !== '–'"""):
                done_at = now                          # both grids have cleared it
            if done_at is not None and now > done_at + 0.7:
                break
            page.wait_for_timeout(20)
        cdp.send("Page.stopScreencast")
        page.wait_for_timeout(300)
        result = page.evaluate("() => document.querySelector('[data-k=\"em-change\"]').textContent")
        browser.close()

    times = []
    for i, (ts, data) in enumerate(frames):
        (frames_dir / f"f{i:05d}.png").write_bytes(base64.b64decode(data))
        times.append(ts - frames[0][0])
    return times, crop_h, result, done_at is not None


def encode(frames_dir: Path, times, crop_h: int, out: Path, width: int, fps: int, hold_s: float) -> None:
    lines = ["ffconcat version 1.0"]
    for i, t in enumerate(times):
        lines += [f"file 'f{i:05d}.png'", f"duration {(times[i + 1] - t) if i + 1 < len(times) else hold_s:.4f}"]
    lines.append(f"file 'f{len(times) - 1:05d}.png'")       # the concat demuxer needs this repeat
    (frames_dir / "list.ffconcat").write_text("\n".join(lines))
    chain = f"crop={W}:{crop_h}:0:0,fps={fps},scale={width}:-1:flags=lanczos"
    src = ["-f", "concat", "-safe", "0", "-i", str(frames_dir / "list.ffconcat")]
    pal = frames_dir / "palette.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *src, "-vf",
                    f"{chain},palettegen=max_colors=128:stats_mode=diff", str(pal)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", *src, "-i", str(pal), "-lavfi",
                    f"{chain} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",
                    "-loop", "0", str(out)], check=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "docs" / "screenshots" / "dashboard_demo.gif"))
    ap.add_argument("--python", default=sys.executable, help="interpreter that runs the server")
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--warm", type=float, default=600.0, help="simulated seconds before recording")
    ap.add_argument("--speed", type=float, default=0.5)
    ap.add_argument("--hold", type=float, default=2.8, help="seconds the last frame is held")
    ap.add_argument("--max", type=float, default=40.0, help="longest recording, wall seconds")
    args = ap.parse_args(argv)
    if shutil.which("ffmpeg") is None:
        print("ffmpeg is not on the PATH")
        return 1

    port = free_port()
    server = start_server(args.python, port)
    work = Path(tempfile.mkdtemp(prefix="atsc_gif_"))
    try:
        times, crop_h, result, finished = record(f"http://127.0.0.1:{port}", work, args.warm,
                                                 args.speed, args.max)
        if not finished:
            print(f"warning: the fixed-time grid had not cleared the ambulance after {args.max:.0f} s")
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        encode(work, times, crop_h, out, args.width, args.fps, args.hold)
        print(f"{out}  {out.stat().st_size / 1e6:.1f} MB  {len(times)} frames over "
              f"{times[-1] + args.hold:.1f} s  emergency clearance: {result}")
    finally:
        server.terminate()
        server.wait(10)
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
