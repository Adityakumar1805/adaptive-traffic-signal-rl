// Entry point: wires the model, the transport, the two network views, the charts and the UI,
// and runs the single requestAnimationFrame loop that draws everything.

import { Model, SimClock, PROTOCOL_VERSION } from "./model.js";
import { Transport } from "./transport.js";
import { NetworkView } from "./renderer.js";
import { LineChart } from "./charts.js";
import { UI } from "./ui.js";

const model = new Model();
const clock = new SimClock();
const reduceMotion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };

const stages = { rl: document.getElementById("stageRL"), ft: document.getElementById("stageFT") };
const views = {
  rl: new NetworkView(document.getElementById("netRL"), stages.rl, model, "rl"),
  ft: new NetworkView(document.getElementById("netFT"), stages.ft, model, "ft"),
};
const charts = [
  new LineChart(document.getElementById("chartWait"), { rl: 1, ft: 2, unit: " s", label: "Average waiting time", digits: 1 }),
  new LineChart(document.getElementById("chartQueue"), { rl: 3, ft: 4, unit: "", label: "Average queue per junction", digits: 1 }),
];

let transport = null;
const ui = new UI({ command: (cmd) => (transport ? transport.send(cmd) : Promise.resolve({ ok: false, error: "not connected" })) });

// ---------------------------------------------------------------- build check
// The page embeds the build id it was served with; the server reports its own in `hello`.
// A different id means this tab (or the service-worker copy it came from) predates a
// redeploy: reload once to pick up the new page instead of running old code against it.
function checkBuild(hello) {
  const mine = document.documentElement.dataset.build || "";
  if (!mine || mine.startsWith("__") || !hello.build || hello.build === mine) return false;
  let tried = "";
  try { tried = sessionStorage.getItem("atsc-reloaded-for") || ""; } catch (e) { /* storage blocked */ }
  if (tried === hello.build) return false;               // already tried once: carry on
  try { sessionStorage.setItem("atsc-reloaded-for", hello.build); } catch (e) { /* ignore */ }
  location.reload();
  return true;
}

// ---------------------------------------------------------------- messages
let reloading = false;
function onMessage(msg) {
  if (reloading) return;
  if (msg.t === "hello" && msg.v !== PROTOCOL_VERSION) {
    // this copy of the page cannot read the server's messages: reload to the new version
    reloading = true;
    if (!checkBuild(msg)) ui.toast("This page is out of date - please reload it.");
    return;
  }
  const kind = model.apply(msg);
  if (!kind) return;
  const now = performance.now();
  if (kind === "hello") {
    if (checkBuild(msg)) { reloading = true; return; }
    views.rl.setHello();
    views.ft.setHello();
    const aspect = views.rl.aspect();
    for (const stage of Object.values(stages)) stage.style.setProperty("--aspect", String(aspect));
    ui.setHello(model);
    resizeAll();
  }
  if (model.g.sp > 0) {
    // speed = decisions per tick: below 1 a frame still advances one decision at a time
    const dec = +model.hello.dec_s || 5, tick = (+model.hello.tick_ms || 200) / 1000;
    clock.nominal(model.g.sp * dec / tick, Math.max(1, model.g.sp) * dec);
  }
  clock.frame(model.bt, !!model.g.p, now, model.newEpisode);
  ui.update(model);
}

transport = new Transport({
  onMessage: (msg) => {
    try { onMessage(msg); } catch (e) { console.error("frame handling failed:", e); }
  },
  onStatus: (state, info) => ui.setStatus(state, info),
});

// ---------------------------------------------------------------- layout
let chartsRev = -1;
let resizes = 0;
function resizeAll() {
  if (!model.hello) return;
  if (views.rl.resize()) resizes += 1;
  if (views.ft.resize()) resizes += 1;
  for (const c of charts) c.resize();
  chartsRev = -1;
}
const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => resizeAll()) : null;
if (ro) {
  for (const stage of Object.values(stages)) ro.observe(stage);
  for (const c of charts) ro.observe(c.canvas.parentElement);
} else {
  window.addEventListener("resize", resizeAll);
}

// The grids are sized to fit under the sticky top bar (styles.css, .stage). How tall that bar
// is depends on how its controls wrap at this width, so measure it rather than guess.
const topbar = document.querySelector(".topbar");
function measureTopbar() {
  if (!topbar) return;
  const h = Math.ceil(topbar.getBoundingClientRect().height);
  if (h > 0) document.documentElement.style.setProperty("--topbar-h", `${h}px`);
}
measureTopbar();
if (topbar && typeof ResizeObserver !== "undefined") new ResizeObserver(measureTopbar).observe(topbar);
else window.addEventListener("resize", measureTopbar);

// ---------------------------------------------------------------- the frame loop
let last = performance.now();
let lastKey = "";
let lastStats = views.rl.stats;
let lastStatsFT = views.ft.stats;
let dpr = window.devicePixelRatio || 1;
const frameMs = [];
function loop(now) {
  requestAnimationFrame(loop);
  const dt = Math.min(0.25, Math.max(0, (now - last) / 1000));
  frameMs.push(now - last);
  if (frameMs.length > 240) frameMs.shift();
  last = now;
  if (!model.hello) return;
  try {
    if ((window.devicePixelRatio || 1) !== dpr) { dpr = window.devicePixelRatio || 1; resizeAll(); }
    const vt = clock.advance(dt);
    const motion = !reduceMotion.matches;
    // nothing moves while paused: redraw only when something changed (or beacons flash)
    const key = `${vt}|${model.rev}|${resizes}`;
    const flashing = motion && (views.rl.stats.emergency.length || views.ft.stats.emergency.length ||
                                (model.sides.rl && model.sides.rl.pr.length) ||
                                (model.sides.ft && model.sides.ft.pr.length));
    if (key !== lastKey || flashing) {
      lastKey = key;
      lastStats = views.rl.draw(vt, now, motion);
      lastStatsFT = views.ft.draw(vt, now, motion);
    }
    ui.frame(lastStats, lastStatsFT, model, now);
    if (model.hRev !== chartsRev) {
      chartsRev = model.hRev;
      for (const c of charts) c.draw(model.h);
    }
  } catch (e) {
    console.error("draw failed:", e);
  }
}
requestAnimationFrame(loop);
transport.start();

// ---------------------------------------------------------------- service worker
// Lets a returning visitor see the "waking up" screen instantly while a sleeping free
// instance boots, instead of a blank tab. Network-first, so it never serves stale code
// while the server answers.
if ("serviceWorker" in navigator && window.isSecureContext) {
  if (new URLSearchParams(location.search).has("nosw")) {
    navigator.serviceWorker.getRegistrations().then((regs) => regs.forEach((r) => r.unregister()), () => {});
  } else {
    navigator.serviceWorker.register("/sw.js").catch((e) => console.info("service worker not registered:", e && e.message));
  }
}

// a small debugging / testing hook (read-only use)
window.__ATSC__ = {
  model, clock, views, get transport() { return transport; },
  fps() {
    const recent = frameMs.slice(-120).filter((x) => x > 0);
    return recent.length ? 1000 / (recent.reduce((a, b) => a + b, 0) / recent.length) : 0;
  },
  status() { return transport ? transport.state : "none"; },
};
