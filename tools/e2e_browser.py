"""End-to-end browser check of the live dashboard (headless Chromium via Playwright).

    pip install playwright && python -m playwright install chromium     # once
    python tools/e2e_browser.py --local                  # starts its own server, full run
    python tools/e2e_browser.py https://adaptive-traffic-signal-rl.onrender.com

Checks, each reported PASS / FAIL:

  * the server's ``/healthz``: answers, no failed ticks, CPU not pinned at a free
    instance's 0.1-CPU limit
  * the status bar reaches "live · streaming" (WebSocket) - or "live · polling" with
    --expect-polling (the stdlib server)
  * no console errors, no uncaught exceptions, no Chrome "Issues" (DevTools Audits, e.g.
    "No label associated with a form field")
  * desktop 1440x900, laptops 1366x657 / 1280x720 and phone 390x844: no horizontal
    scrolling, both grids completely on the page, both grids (phone: the RL grid) entirely on
    the first screen without scrolling, canvases drawn at the device pixel ratio
  * every Inject button puts that emergency type on the RL grid
  * every vehicle type of the catalogue is drawn at least once (needs a few minutes of
    simulated traffic; locally the run switches to rush hour at 8x to get there quickly)
  * playback controls (pause / step / play / speed / scenario) take effect
  * frames per second while animating
  * --local only: the "Waking up the server..." screen. The page is loaded once (the service
    worker caches it), the server is replaced by one that accepts connections and never
    answers (what a sleeping free instance looks like), the page is reloaded: the cached page
    must show the wake screen, and connect by itself once the real server is back.

On a shared live site the controls test changes the race for everyone for a few seconds and
then restores the previous scenario / speed; pass --no-controls to skip it.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
CHROMIUM_ARGS = ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"]
IGNORED_CONSOLE = ("Service Worker registration blocked by Playwright",)


# --------------------------------------------------------------------------- #
# local servers
# --------------------------------------------------------------------------- #
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 60.0) -> bool:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/healthz", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


class LocalServer:
    """``uvicorn asgi:app`` (the exact production command) or the stdlib fallback server."""

    def __init__(self, port: int, stdlib: bool = False, python: Optional[str] = None) -> None:
        self.port, self.stdlib, self.proc = port, stdlib, None
        self.python = python or sys.executable

    def start(self) -> None:
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        if self.stdlib:
            code = ("import sys; sys.path.insert(0, 'src');"
                    "from atsc.threads import limit_math_threads; limit_math_threads();"  # as run.py demo
                    "from atsc.dashboard.session import DashboardSession;"
                    "from atsc.dashboard.stdlib_server import run_stdlib_dashboard;"
                    "s = DashboardSession(); s.play();"
                    f"run_stdlib_dashboard(s, '127.0.0.1', {self.port}, False)")
            cmd = [self.python, "-c", code]
        else:
            cmd = [self.python, "-m", "uvicorn", "asgi:app", "--host", "127.0.0.1",
                   "--port", str(self.port), "--ws-max-size", "65536",
                   "--ws-ping-interval", "20", "--ws-ping-timeout", "20", "--log-level", "warning"]
        self.proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        if not wait_http(f"http://127.0.0.1:{self.port}"):
            self.stop()
            raise RuntimeError("the local server did not start")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


class HangingServer:
    """Accepts TCP connections and never answers - a free instance that is still booting."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.conns: List[socket.socket] = []
        self.running = False

    def start(self) -> None:
        self.sock.bind(("127.0.0.1", self.port))
        self.sock.listen(64)
        self.running = True
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        self.sock.settimeout(0.2)
        while self.running:
            try:
                conn, _ = self.sock.accept()
                self.conns.append(conn)
            except OSError:
                continue

    def stop(self) -> None:
        self.running = False
        for c in self.conns:
            try:
                c.close()
            except OSError:
                pass
        self.sock.close()


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
class Report:
    def __init__(self) -> None:
        self.rows: List[dict] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""), flush=True)
        return ok

    @property
    def ok(self) -> bool:
        return all(r["ok"] for r in self.rows)


def wait_for(page, fn: str, timeout_s: float, arg=None) -> bool:
    try:
        page.wait_for_function(fn, arg=arg, timeout=int(timeout_s * 1000), polling=200)
        return True
    except Exception:
        return False


def open_page(browser, url: str, width: int, height: int, scale: float, service_workers="allow"):
    mobile = width < 700
    ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=scale,
                              is_mobile=mobile, has_touch=mobile, service_workers=service_workers)
    page = ctx.new_page()
    errors: List[str] = []
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
            if m.type in ("error", "warning") and not m.text.startswith(IGNORED_CONSOLE) else None)
    page.on("pageerror", lambda e: errors.append(f"uncaught: {e}"))
    issues: List[str] = []
    cdp = ctx.new_cdp_session(page)
    cdp.on("Audits.issueAdded", lambda ev: issues.append(json.dumps(ev.get("issue", {}))[:300]))
    cdp.send("Audits.enable")
    page.goto(url, wait_until="load")
    return ctx, page, errors, issues


LAYOUT_JS = """() => {
  window.scrollTo(0, 0);
  const de = document.documentElement;
  const stages = [...document.querySelectorAll('.stage')].map((s) => {
    const r = s.getBoundingClientRect();
    const c = s.querySelector('canvas');
    return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width,
            height: r.height, cw: c.width, ch: c.height};
  });
  return {scrollW: de.scrollWidth, clientW: de.clientWidth, stages,
          dpr: window.devicePixelRatio,
          topbarBottom: document.querySelector('.topbar').getBoundingClientRect().bottom,
          statusTop: document.querySelector('.statusbar').getBoundingClientRect().top};
}"""


def check_layout(rep: Report, page, label: str, first_screen: int) -> None:
    """``first_screen``: how many grids must be entirely visible without scrolling, between
    the top bar and the status bar (both on a desktop or laptop, the RL grid on a phone)."""
    lay = page.evaluate(LAYOUT_JS)
    rep.check(f"{label}: no horizontal scroll", lay["scrollW"] <= lay["clientW"],
              f"scrollWidth {lay['scrollW']} / viewport {lay['clientW']}")
    inside = all(s["left"] >= -0.5 and s["right"] <= lay["clientW"] + 0.5 for s in lay["stages"])
    square = all(abs(s["width"] - s["height"]) <= 1.5 for s in lay["stages"])
    sharp = all(s["cw"] >= s["width"] * min(lay["dpr"], 1.0) - 2 for s in lay["stages"])
    rep.check(f"{label}: both grids fully on the page", len(lay["stages"]) == 2 and inside and square,
              ", ".join(f"{s['width']:.0f}x{s['height']:.0f}" for s in lay["stages"]))
    wanted = lay["stages"][:first_screen]
    seen = [s for s in wanted
            if s["top"] >= lay["topbarBottom"] - 0.5 and s["bottom"] <= lay["statusTop"] + 0.5]
    rep.check(f"{label}: {'both grids' if first_screen == 2 else 'the RL grid'} entirely on the first screen",
              len(wanted) == first_screen and len(seen) == first_screen,
              ", ".join(f"y {s['top']:.0f}-{s['bottom']:.0f}" for s in wanted)
              + f" within y {lay['topbarBottom']:.0f}-{lay['statusTop']:.0f}")
    rep.check(f"{label}: canvases drawn at device resolution", sharp,
              ", ".join(f"{s['cw']}x{s['ch']}" for s in lay["stages"]))


def check_health(rep: Report, url: str) -> None:
    """The server's own numbers, read before the browser touches anything."""
    import urllib.request
    health, error = None, ""
    deadline = time.time() + 90                  # a sleeping free instance takes ~a minute
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=30) as r:
                health = json.loads(r.read())
            if (health.get("cpu_percent") or {}).get("last_30s") is not None or "cpu_percent" not in health:
                break                            # wait out the first half second after a boot
        except Exception as exc:                 # report, keep trying until the deadline
            error = repr(exc)
        time.sleep(1.0)
    if health is None:
        rep.check("server: /healthz answers", False, error)
        return
    rep.check("server: /healthz answers", health.get("ok") is True,
              f"version {health.get('version')}, build {health.get('build')}, "
              f"{health.get('viewers')} viewer(s), {health.get('nn_backend')} network")
    rep.check("server: no failed ticks", health.get("tick_errors") == 0, str(health.get("tick_errors")))
    cpu = (health.get("cpu_percent") or {}).get("last_30s")
    rep.check("server: CPU not saturated", cpu is not None and cpu < 9.0,
              f"{cpu} % of one core over the last 30 s (a free Render instance has 10 %)"
              if cpu is not None else "no cpu_percent in /healthz: the server is older than 1.1.0")


def run(url: str, args, rep: Report, out: Path, server: Optional[LocalServer] = None) -> None:
    from playwright.sync_api import sync_playwright
    want = "live · polling" if args.expect_polling else "live · streaming"
    check_health(rep, url)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=CHROMIUM_ARGS)

        # ---------------- desktop
        ctx, page, errors, issues = open_page(browser, url, 1440, 900, 1)
        live = wait_for(page, "(w) => document.getElementById('statusText').textContent === w", 25, want)
        rep.check("desktop: status bar says " + want, live,
                  page.evaluate("() => document.getElementById('statusText').textContent"))
        page.wait_for_timeout(2500)
        check_layout(rep, page, "desktop", 2)
        page.screenshot(path=str(out / "desktop.png"))

        kinds = page.evaluate("() => window.__ATSC__.model.kinds.map((k) => k.kind)")
        emg = page.evaluate("() => window.__ATSC__.model.hello.emg.map((e) => e[0])")
        legend = page.evaluate("() => document.querySelectorAll('#legendList li').length")
        rep.check("legend lists every vehicle type", legend == len(kinds) and len(kinds) >= 18,
                  f"{legend} entries, {len(kinds)} kinds")
        buttons = page.evaluate("() => [...document.querySelectorAll('#injectRow button')].map((b) => b.dataset.kind)")
        rep.check("one Inject button per emergency type", buttons == emg, ", ".join(buttons))

        saved = page.evaluate("() => ({sc: window.__ATSC__.model.g.sc, sp: window.__ATSC__.model.g.sp})")

        # ---------------- every emergency type
        page.evaluate("""() => {
            window.__bannerTexts = [];
            const banner = document.getElementById('emBanner');
            const note = () => { if (!banner.hidden) window.__bannerTexts.push(banner.textContent); };
            new MutationObserver(note).observe(banner, {attributes: true, childList: true,
                                                        subtree: true, characterData: true});
        }""")
        labels = dict(page.evaluate("() => window.__ATSC__.model.hello.emg.map((e) => [e[0], e[1]])"))
        seen_em = set()
        for kind in buttons:
            page.click(f"#injectRow button[data-kind='{kind}']")
            ok = wait_for(page, """(k) => {
                const v = window.__ATSC__.views.rl.stats;
                return v.emergency.includes(k);
            }""", 30, kind)
            if ok:
                seen_em.add(kind)
            rep.check(f"Inject {kind}: drawn on the RL grid with its beacons", ok)
            if ok:
                banner = wait_for(page, "(label) => window.__bannerTexts.some((t) => t.includes(label))",
                                  5, labels[kind])
                rep.check(f"Inject {kind}: emergency banner names it", banner)
            page.wait_for_timeout(1200)
        page.screenshot(path=str(out / "desktop_emergency.png"))

        # ---------------- controls
        if not args.no_controls:
            def g(key):
                return page.evaluate(f"() => window.__ATSC__.model.g.{key}")
            page.click("#playPause") if g("p") else None
            rep.check("Pause stops the race", wait_for(page, "() => window.__ATSC__.model.g.p === 0", 8))
            e0 = g("e")
            page.click("#stepBtn")
            dec = page.evaluate("() => window.__ATSC__.model.hello.dec_s")
            rep.check("Step advances one decision", wait_for(page, f"() => window.__ATSC__.model.g.e >= {e0 + dec}", 8),
                      f"{e0} -> {g('e')} s")
            page.click("#playPause")
            rep.check("Play resumes", wait_for(page, "() => window.__ATSC__.model.g.p === 1", 8))
            speeds = page.evaluate("() => window.__ATSC__.model.hello.speeds")
            page.eval_on_selector("#speed", "(el, i) => { el.value = String(i); el.dispatchEvent(new Event('change')); }",
                                  len(speeds) - 1)
            rep.check("Speed slider reaches the server", wait_for(page, f"() => window.__ATSC__.model.g.sp === {speeds[-1]}", 8),
                      f"{g('sp')}x")
            page.select_option("#scenario", "rush")
            rep.check("Scenario select restarts the race", wait_for(page, "() => window.__ATSC__.model.g.sc === 'rush'", 8))

        # ---------------- every vehicle type is drawn
        budget = args.vehicle_seconds
        t_end = time.time() + budget
        seen = set(seen_em)
        while time.time() < t_end and len(seen) < len(kinds):
            counts = page.evaluate("() => window.__ATSC__.views.rl.stats.counts.slice()")
            counts_ft = page.evaluate("() => window.__ATSC__.views.ft.stats.counts.slice()")
            for i, (a, b) in enumerate(zip(counts, counts_ft)):
                if a or b:
                    seen.add(kinds[i])
            page.wait_for_timeout(150)
        missing = [k for k in kinds if k not in seen]
        rep.check("every vehicle type appears on screen", not missing,
                  "missing: " + ", ".join(missing) if missing else f"all {len(kinds)} seen")

        # ---------------- frame rate
        fps = page.evaluate("""() => new Promise((done) => {
            const t = []; let last = performance.now(); const t0 = last;
            function f(now) { t.push(now - last); last = now;
              if (now - t0 < 5000) requestAnimationFrame(f);
              else { t.shift(); const m = t.reduce((a, b) => a + b, 0) / t.length;
                     const s = t.slice().sort((a, b) => a - b);
                     done({fps: 1000 / m, p95: s[Math.floor(s.length * .95)], jank: t.filter((x) => x > 50).length}); } }
            requestAnimationFrame(f); })""")
        rep.check("animation frame rate", fps["fps"] >= args.min_fps,
                  f"{fps['fps']:.1f} fps, p95 frame {fps['p95']:.1f} ms, {fps['jank']} frames > 50 ms")
        rep.rows[-1]["fps"] = fps

        if not args.no_controls and saved.get("sc"):
            page.select_option("#scenario", saved["sc"])
            speeds = page.evaluate("() => window.__ATSC__.model.hello.speeds")
            if saved.get("sp") in speeds:
                page.eval_on_selector("#speed", "(el, i) => { el.value = String(i); el.dispatchEvent(new Event('change')); }",
                                      speeds.index(saved["sp"]))
            page.wait_for_timeout(1000)

        rep.check("desktop: no console errors or uncaught exceptions", not errors, "; ".join(errors[:5]))
        rep.check("desktop: no DevTools issues (labels, CSP, ...)", not issues, "; ".join(issues[:3]))
        ctx.close()

        # ---------------- phone
        ctx, page, errors, issues = open_page(browser, url, 390, 844, 3)
        live = wait_for(page, "(w) => document.getElementById('statusText').textContent === w", 25, want)
        rep.check("phone: status bar says " + want, live)
        page.wait_for_timeout(2000)
        check_layout(rep, page, "phone", 1)
        page.screenshot(path=str(out / "phone.png"), full_page=True)
        rep.check("phone: no console errors", not errors, "; ".join(errors[:5]))
        rep.check("phone: no DevTools issues", not issues, "; ".join(issues[:3]))
        ctx.close()

        # ---------------- the small laptop screens people actually present from
        for w, h in ((1366, 657), (1280, 720)):
            ctx, page, errors, issues = open_page(browser, url, w, h, 1)
            live = wait_for(page, "(w) => document.getElementById('statusText').textContent === w", 25, want)
            page.wait_for_timeout(1500)
            if live:
                check_layout(rep, page, f"laptop {w}x{h}", 2)
            else:
                rep.check(f"laptop {w}x{h}: status bar says " + want, False)
            page.screenshot(path=str(out / f"laptop_{w}x{h}.png"))
            ctx.close()

        # ---------------- reduced motion
        ctx = browser.new_context(viewport={"width": 1024, "height": 768}, reduced_motion="reduce")
        page = ctx.new_page()
        errs: List[str] = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(url, wait_until="load")
        ok = wait_for(page, "(w) => document.getElementById('statusText').textContent === w", 25, want)
        page.wait_for_timeout(1500)
        rep.check("reduced motion: renders without errors", ok and not errs, "; ".join(errs[:3]))
        ctx.close()

        # ---------------- wake screen (needs control over the server)
        if server is not None and not args.no_wake:
            wake_test(browser, url, server, rep, out)
        browser.close()


def wake_test(browser, url: str, server: LocalServer, rep: Report, out: Path) -> None:
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    errors: List[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url, wait_until="load")
    ok = wait_for(page, "() => document.getElementById('statusText').textContent.startsWith('live')", 25)
    sw = page.evaluate("""() => navigator.serviceWorker.ready.then(() => caches.keys())
                                   .then((k) => k.filter((n) => n.startsWith('atsc-shell-')).length)""")
    page.wait_for_timeout(1500)                       # let the pre-cache finish
    rep.check("service worker installed and cached the page", ok and sw >= 1, f"{sw} cache(s)")

    server.stop()
    hang = HangingServer(server.port)
    hang.start()
    t0 = time.time()
    try:
        page.reload(wait_until="commit", timeout=30000)
    except Exception:
        pass
    shown = wait_for(page, """() => { const w = document.getElementById('wake');
                                      return !!w && !w.hidden; }""", 25)
    took = time.time() - t0
    title = page.evaluate("() => (document.getElementById('wakeTitle') || {}).textContent || ''") if shown else ""
    rep.check("sleeping server: 'Waking up the server…' screen appears", shown and "Waking" in title,
              f"after {took:.1f} s: {title!r}")
    page.screenshot(path=str(out / "wake.png"))
    hang.stop()
    server.start()
    back = wait_for(page, "() => document.getElementById('statusText').textContent === 'live · streaming'", 60)
    hidden = page.evaluate("() => document.getElementById('wake').hidden")
    rep.check("server back: the page connects by itself and hides the screen", back and hidden)
    rep.check("wake test: no uncaught exceptions", not errors, "; ".join(errors[:3]))
    ctx.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", nargs="?", help="dashboard URL (omit with --local)")
    ap.add_argument("--local", action="store_true", help="start uvicorn asgi:app on a free port")
    ap.add_argument("--stdlib", action="store_true", help="with --local: use the stdlib polling server")
    ap.add_argument("--server-python", help="with --local: interpreter that has requirements-deploy.txt "
                                            "installed (default: this one)")
    ap.add_argument("--expect-polling", action="store_true")
    ap.add_argument("--no-controls", action="store_true", help="do not touch the shared race settings")
    ap.add_argument("--no-wake", action="store_true")
    ap.add_argument("--vehicle-seconds", type=float, default=120.0,
                    help="how long to wait for every vehicle type to show up")
    ap.add_argument("--min-fps", type=float, default=30.0)
    ap.add_argument("--out", default=str(ROOT / "outputs" / "e2e"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rep = Report()
    server = None
    if args.local:
        server = LocalServer(free_port(), stdlib=args.stdlib, python=args.server_python)
        server.start()
        url = f"http://127.0.0.1:{server.port}/"
        if args.stdlib:
            args.expect_polling = True
            args.no_wake = True
    else:
        if not args.url:
            ap.error("give a URL or --local")
        url = args.url
    try:
        run(url, args, rep, out, server)
    finally:
        if server is not None:
            server.stop()
    (out / "report.json").write_text(json.dumps(rep.rows, indent=2), encoding="utf-8")
    print(f"\n{sum(r['ok'] for r in rep.rows)}/{len(rep.rows)} checks passed; screenshots in {out}")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
