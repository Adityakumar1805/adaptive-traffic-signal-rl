// Everything in the page that is not a canvas: controls, KPI cards, legend, banner, status
// bar, toasts and the "waking up" screen. Text is only written when it actually changed, so
// a 5 Hz frame stream causes almost no DOM work.

import { thumbnail } from "./sprites.js";

const MINUS = "−";

const $ = (id) => document.getElementById(id);

function setText(el, text) {
  if (el && el.textContent !== text) el.textContent = text;
}
function setClass(el, name, on) {
  if (el && el.classList.contains(name) !== on) el.classList.toggle(name, on);
}
function fmtNum(v, digits) {
  return Number.isFinite(v) ? v.toFixed(digits) : "–";
}
/** "+7.6 %" / "−26.5 %": the signed change of RL relative to fixed-time (README convention). */
export function signedChange(rl, ft) {
  if (!(Number.isFinite(rl) && Number.isFinite(ft)) || ft <= 0) return { text: "–", value: null };
  const v = ((rl - ft) / ft) * 100;
  const mag = Math.abs(v);
  const shown = mag < 9.95 ? mag.toFixed(1) : mag.toFixed(0);
  if (Number(shown) === 0) return { text: "0 %", value: 0 };
  return { text: (v > 0 ? "+" : MINUS) + shown + " %", value: v };
}
/** Speed label. With a signal board attached the server names its real-time speed `rt`,
 *  and every speed is shown as a multiple of real time. */
export function fmtSpeed(s, rt) {
  if (rt > 0) {
    const r = s / rt;
    return Math.abs(r - 1) < 1e-6 ? "real time" : `${+r.toFixed(1)}× real time`;
  }
  return (Number.isInteger(s) ? String(s) : String(+s.toFixed(2))) + "×";
}

// signal-board pill: [state class, short text, details shown on wider screens (they start
// with a no-break space: a leading plain space would be dropped inside the flex pill)]
const BOARD = [
  ["off", () => "board offline", (hw) => `\u00a0- plug it in (${hw.port})`],
  ["wait", () => "board connecting", (hw) => `\u00a0- port ${hw.port} open, waiting for the board`],
  ["on", () => "signal board live",
   (hw, cars, em) => `\u00a0· ${cars} car${cars === 1 ? "" : "s"} sensed · ${em} remote call${em === 1 ? "" : "s"}`],
];

const STATUS = {
  connecting: ["connecting…", "off"],
  waking: ["waking up the server…", "off"],
  reconnecting: ["reconnecting…", "off"],
  live: ["live · streaming", "on"],
  polling: ["live · polling", "poll"],
  busy: ["server full · retrying", "off"],
  hidden: ["paused while the tab is hidden", "off"],
};

export class UI {
  /** @param {{command: (cmd: object) => Promise<{ok: boolean, error?: string}>}} handlers */
  constructor(handlers) {
    this.h = handlers;
    this.el = {
      scenario: $("scenario"), play: $("playPause"), step: $("stepBtn"), reset: $("resetBtn"),
      speed: $("speed"), speedOut: $("speedOut"), injectRow: $("injectRow"),
      banner: $("emBanner"), bannerText: $("emBannerText"), legend: $("legendList"),
      status: $("statusText"), dot: $("connDot"), elapsed: $("elapsed"), epLen: $("epLen"),
      progress: $("epProgress"), ckpt: $("ckpt"), nn: $("nnBackend"), rtt: $("rtt"),
      toast: $("toast"), announce: $("announce"), wake: $("wake"), wakeTitle: $("wakeTitle"),
      wakeText: $("wakeText"), wakeElapsed: $("wakeElapsed"), wakeAttempt: $("wakeAttempt"),
      grid: $("gridLabel"), untrained: $("untrained"), hw: $("hwPill"), hwText: $("hwText"),
      hwMore: $("hwMore"),
    };
    this.kpi = {};
    for (const node of document.querySelectorAll("[data-k]")) this.kpi[node.dataset.k] = node;
    this.speeds = [1];
    this.rt = 0;
    this.boardState = -1;
    this.playing = true;
    this.speedTouchedAt = -1e9;     // last time the user moved the slider (never yet)
    this.legendItems = [];
    this.legendAt = 0;
    this.lastEmergency = "";
    this.bannerShown = "";
    this.wakeSince = 0;
    this.notLiveSince = performance.now();
    this.state = "connecting";
    this.toastTimer = 0;
    this.wire();
    setInterval(() => this.tickWake(), 500);
  }

  // ------------------------------------------------------------------ controls
  wire() {
    const el = this.el;
    el.play.addEventListener("click", () => this.send({ a: this.playing ? "pause" : "play" }));
    el.step.addEventListener("click", () => this.send({ a: "step" }));
    el.reset.addEventListener("click", () => this.send({ a: "reset" }));
    el.scenario.addEventListener("change", () => this.send({ a: "scenario", v: el.scenario.value }));
    let debounce = 0;
    const speedValue = () => this.speeds[Math.max(0, Math.min(this.speeds.length - 1, +el.speed.value))];
    el.speed.addEventListener("input", () => {
      this.speedTouchedAt = performance.now();
      const v = speedValue();
      setText(el.speedOut, fmtSpeed(v, this.rt));
      el.speed.setAttribute("aria-valuetext", fmtSpeed(v, this.rt));
      clearTimeout(debounce);
      debounce = setTimeout(() => this.send({ a: "speed", v }), 120);
    });
    el.speed.addEventListener("change", () => {
      clearTimeout(debounce);
      this.speedTouchedAt = performance.now();
      this.send({ a: "speed", v: speedValue() });
    });
  }

  async send(cmd, button) {
    if (button) button.disabled = true;
    try {
      const res = await this.h.command(cmd);
      if (!res.ok) this.toast(res.error || "the server rejected that");
      return res;
    } finally {
      if (button) setTimeout(() => { button.disabled = false; }, 350);
    }
  }

  // ------------------------------------------------------------------ hello
  setHello(model) {
    const hello = model.hello;
    const el = this.el;
    // scenarios
    const current = el.scenario.value;
    el.scenario.replaceChildren(...hello.scen.map(([name, description]) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name === "rush" ? "Rush hour" : name[0].toUpperCase() + name.slice(1);
      opt.title = description;
      return opt;
    }));
    el.scenario.value = model.g.sc || current || hello.scen[0][0];
    // speeds
    this.speeds = hello.speeds.slice();
    this.rt = +hello.rt || 0;
    el.speed.min = "0";
    el.speed.max = String(this.speeds.length - 1);
    el.speed.step = "1";
    this.showSpeed(model.g.sp);
    // emergency buttons, one per configured type
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    el.injectRow.replaceChildren(...hello.emg.map(([kind, label, corridor]) => {
      const spec = model.kinds.find((k) => k.kind === kind);
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn inject";
      b.dataset.kind = kind;
      b.title = `Send a ${label.toLowerCase()} along the ${corridor === "ew" ? "east-west" : "north-south"} corridor (both grids)`;
      if (spec) {
        const c = document.createElement("canvas");
        c.setAttribute("aria-hidden", "true");
        thumbnail(c, spec, 0, 34, 16, dpr);
        b.append(c);
      }
      const t = document.createElement("span");
      t.textContent = label;
      b.append(t);
      b.addEventListener("click", () => this.send({ a: "inject", kind }, b));
      return b;
    }));
    const injectGroup = el.injectRow.closest(".ctl");
    if (injectGroup) injectGroup.hidden = hello.emg.length === 0;
    // legend: every vehicle type the server knows
    this.legendItems = model.kinds.map((spec) => {
      const li = document.createElement("li");
      const c = document.createElement("canvas");
      c.setAttribute("aria-hidden", "true");
      thumbnail(c, spec, 0, 44, 20, dpr);
      const name = document.createElement("span");
      name.className = "lg-name";
      name.textContent = spec.label;
      const share = hello.mix[spec.kind];
      if (share) name.title = `${(share * 100).toFixed(1)} % of the configured traffic mix`;
      else if (spec.em) name.title = "emergency vehicle - dispatched with the Inject buttons";
      const count = document.createElement("b");
      count.className = "lg-count";
      count.textContent = "0";
      li.className = spec.em ? "em" : "";
      li.append(c, name, count);
      return { li, count, last: -1 };
    });
    el.legend.replaceChildren(...this.legendItems.map((x) => x.li));
    // static facts
    setText(el.epLen, String(hello.ep_s));
    setText(el.grid, `${hello.grid.rows}×${hello.grid.cols}`);
    setText(el.ckpt, hello.meta.ckpt ? (hello.meta.ckpt_name || "loaded") : "untrained");
    setText(el.nn, hello.meta.nn || "–");
    if (el.untrained) el.untrained.hidden = !!hello.meta.ckpt;
    if (el.hw) el.hw.hidden = !hello.hw;
    this.boardState = -1;
  }

  /** The signal-board pill (hardware mode only): offline / waiting / live + counts. */
  showBoard(model) {
    const el = this.el, hw = model.hello.hw, g = model.g.hw;
    if (!hw || !el.hw || !Array.isArray(g)) return;
    const [state, cars, em] = g;
    const [cls, text, more] = BOARD[state] || BOARD[0];
    if (el.hw.dataset.state !== cls) el.hw.dataset.state = cls;
    setText(el.hwText, text());
    setText(el.hwMore, more(hw, cars, em));
    if (state !== this.boardState) {
      if (this.boardState >= 0) this.announce(state === 2 ? "Signal board connected" : "Signal board disconnected");
      this.boardState = state;
    }
  }

  showSpeed(sp) {
    if (sp === undefined) return;
    const el = this.el;
    let idx = this.speeds.indexOf(sp);
    if (idx < 0) idx = this.speeds.reduce((best, s, i) => (Math.abs(s - sp) < Math.abs(this.speeds[best] - sp) ? i : best), 0);
    if (performance.now() - this.speedTouchedAt > 1500 && document.activeElement !== el.speed) {
      if (el.speed.value !== String(idx)) el.speed.value = String(idx);
      setText(el.speedOut, fmtSpeed(sp, this.rt));
      el.speed.setAttribute("aria-valuetext", fmtSpeed(sp, this.rt));
    }
  }

  // ------------------------------------------------------------------ frames
  update(model) {
    const g = model.g, el = this.el;
    this.playing = !!g.p;
    setText(el.play, this.playing ? "Pause" : "Play");
    el.play.setAttribute("aria-pressed", String(this.playing));
    setClass(el.play, "primary", this.playing);
    if (g.sc && el.scenario.value !== g.sc && document.activeElement !== el.scenario) el.scenario.value = g.sc;
    this.showSpeed(g.sp);
    this.showBoard(model);
    const e = Math.round(g.e || 0);
    setText(el.elapsed, String(e));
    if (el.progress) {
      const max = String(model.hello.ep_s);
      if (el.progress.max !== +max) el.progress.max = +max;
      if (el.progress.value !== e) el.progress.value = e;
    }

    const rl = model.sides.rl.m, ft = model.sides.ft.m;
    if (!rl.length || !ft.length) return;
    // m: [wait, queue, throughput, speed, clearance, cleared, paired clearance, pairs]
    const [rlW, rlQ, rlT, , rlC, rlN, rlP = -1, pairs = 0] = rl;
    const [ftW, ftQ, ftT, , ftC, ftN, ftP = -1] = ft;
    const k = this.kpi;
    this.change("wait", signedChange(rlW, ftW), true);
    setText(k["wait-rl"], fmtNum(rlW, 1));
    setText(k["wait-ft"], fmtNum(ftW, 1));
    this.change("queue", signedChange(rlQ, ftQ), true);
    setText(k["queue-rl"], fmtNum(rlQ, 1));
    setText(k["queue-ft"], fmtNum(ftQ, 1));
    this.change("thru", signedChange(rlT, ftT), false);
    setText(k["thru-rl"], String(rlT));
    setText(k["thru-ft"], String(ftT));
    // emergency clearance: "N x faster" as in the README, and only ever between the same
    // vehicles (those that have cleared both grids); until one has, RL's own time
    let em = "–", emClass = null;
    let shownRL = rlC, shownFT = ftC;
    if (pairs > 0 && rlP > 0 && ftP >= 0) {
      shownRL = rlP;
      shownFT = ftP;
      const ratio = ftP / rlP;
      em = ratio >= 1.05 ? `${ratio.toFixed(1)}× faster` : ratio <= 0.95 ? `${(1 / ratio).toFixed(1)}× slower` : "about the same";
      emClass = ratio >= 1.05 ? "good" : ratio <= 0.95 ? "bad" : null;
    } else if (rlC >= 0) {
      em = `${rlC.toFixed(0)} s`;
    }
    setText(k["em-change"], em);
    this.tone(k["em-change"], emClass);
    setText(k["em-rl"], shownRL >= 0 ? shownRL.toFixed(0) : "–");
    setText(k["em-ft"], shownFT >= 0 ? shownFT.toFixed(0) : "–");
    setText(k["em-n"], `${rlN} / ${ftN}`);
    // panel readouts
    setText(k["rl-wait"], fmtNum(rlW, 1)); setText(k["rl-queue"], fmtNum(rlQ, 1)); setText(k["rl-thru"], String(rlT));
    setText(k["ft-wait"], fmtNum(ftW, 1)); setText(k["ft-queue"], fmtNum(ftQ, 1)); setText(k["ft-thru"], String(ftT));
  }

  change(key, ch, lowerIsBetter) {
    const node = this.kpi[`${key}-change`];
    setText(node, ch.text);
    let tone = null;
    if (ch.value !== null && Math.abs(ch.value) >= 0.05) {
      tone = (ch.value < 0) === lowerIsBetter ? "good" : "bad";
    }
    this.tone(node, tone);
  }

  tone(node, tone) {
    if (!node) return;
    setClass(node, "good", tone === "good");
    setClass(node, "bad", tone === "bad");
  }

  /** Per animation frame (throttled): legend counts from the RL grid, and the emergency
   *  banner, which follows the vehicle on both grids - it usually clears the RL grid first
   *  and is still stuck in fixed-time traffic, which is the point of the comparison. */
  frame(stats, ftStats, model, now) {
    if (now - this.legendAt < 400) return;
    this.legendAt = now;
    const counts = stats.counts || [];
    this.legendItems.forEach((item, i) => {
      const n = counts[i] || 0;
      if (n !== item.last) { item.count.textContent = String(n); item.last = n; }
    });
    // emergency banner
    const label = (kind) => (model.kinds.find((k) => k.kind === kind) || {}).label || kind;
    const ids = (side) => ((model.sides[side] && model.sides[side].pr) || []).map((j) => model.hello.grid.ids[j]);
    const rlKinds = stats.emergency || [];
    const ftKinds = (ftStats && ftStats.emergency) || [];
    const rlWhere = ids("rl");
    const ftWhere = ids("ft");
    const onRL = rlKinds.length > 0 || rlWhere.length > 0;
    const onFT = ftKinds.length > 0 || ftWhere.length > 0;
    const names = [...new Set((onRL ? rlKinds : ftKinds).map(label))];
    const who = names.length ? names.join(" + ") : "Emergency vehicle";
    let text = "";
    if (onRL) {
      text = `${who} on the road — ` + (rlWhere.length ? `signals pre-empted at ${rlWhere.join(", ")} (green corridor)`
                                                     : "signals will give it a green corridor");
    } else if (onFT) {
      text = `${who} through the RL grid — still in fixed-time traffic` +
             (ftWhere.length ? ` (pre-empting ${ftWhere.join(", ")})` : "");
    }
    if (text !== this.bannerShown) {
      this.bannerShown = text;
      this.el.banner.hidden = !text;
      if (text) setText(this.el.bannerText, text);
    }
    // screen readers hear when the situation changes, not every junction it passes
    const phase = onRL ? `rl:${names.join("+")}` : onFT ? `ft:${names.join("+")}` : "";
    if (phase !== this.lastEmergency) {
      if (phase) this.announce(onRL ? `${names.join(" and ") || "Emergency vehicle"} on the road.`
                                    : `${who} through the RL grid, still in fixed-time traffic.`);
      else if (this.lastEmergency) this.announce("Emergency cleared on both grids.");
      this.lastEmergency = phase;
    }
  }

  // ------------------------------------------------------------------ status
  setStatus(state, info) {
    const [text, dot] = STATUS[state] || [state, "off"];
    let shown = text;
    if (state === "busy" && info.retryAt) shown = `server full · retrying in ${Math.max(1, Math.round((info.retryAt - Date.now()) / 1000))} s`;
    setText(this.el.status, shown);
    for (const c of ["on", "off", "poll"]) setClass(this.el.dot, c, c === dot);
    setText(this.el.rtt, info.rtt != null && (state === "live") ? `ping ${Math.round(info.rtt)} ms` : "");
    if ((state === "live" || state === "polling") !== (this.state === "live" || this.state === "polling")) {
      this.notLiveSince = performance.now();
    }
    this.state = state;
    this.attempts = info.attempts || 0;
    this.tickWake();
  }

  tickWake() {
    const live = this.state === "live" || this.state === "polling";
    const waitedMs = performance.now() - this.notLiveSince;
    const show = !live && this.state !== "hidden" &&
                 (this.state === "waking" || waitedMs > 3000);
    const el = this.el;
    if (show && el.wake.hidden) {
      el.wake.hidden = false;
      this.wakeSince = performance.now();
    } else if (!show && !el.wake.hidden) {
      el.wake.hidden = true;
    }
    if (show) {
      const everLive = el.wake.dataset.everLive === "1";
      setText(el.wakeTitle, everLive ? "Reconnecting…" : "Waking up the server…");
      setText(el.wakeText, everLive
        ? "The connection to the simulation dropped. The page reconnects by itself as soon as the server answers again."
        : "This free demo goes to sleep when nobody is watching. Starting it again takes up to a minute — the page connects by itself as soon as it is up.");
      setText(el.wakeElapsed, String(Math.round((performance.now() - this.wakeSince) / 1000)));
      setText(el.wakeAttempt, String(Math.max(1, this.attempts)));
    }
    if (live) el.wake.dataset.everLive = "1";
  }

  toast(message) {
    const el = this.el.toast;
    el.textContent = message;
    el.hidden = false;
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => { el.hidden = true; }, 4500);
  }

  announce(text) {
    const el = this.el.announce;
    el.textContent = "";
    setTimeout(() => { el.textContent = text; }, 30);
  }
}
