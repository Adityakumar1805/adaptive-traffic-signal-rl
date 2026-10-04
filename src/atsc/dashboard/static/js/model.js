// What the browser knows about the race, built from `hello` + frames (protocol v2; the
// format is documented in src/atsc/dashboard/session.py).
//
// This file has no DOM dependencies on purpose: tests/test_dashboard_model_js.py runs it in
// Node against the Python reference (tests/dashboard_model.py) on one recorded stream, so the
// two must stay line-for-line equivalent in `apply` and `display`.

export const PROTOCOL_VERSION = 2;
// events are kept this long (simulated s) after they stop mattering at the newest time, so a
// display clock running up to a couple of frames behind (40 s per frame at 8x) still has them
export const KEEP_S = 150;

const MV_KEY = 1048576;     // id * 2^20 + t0*10  (t0 < ~29 h of simulated time)
const AP_KEY = 256;         // id * 256 + approach index

export class Side {
  constructor(model) {
    this.model = model;
    const n = model.nAppr;
    this.s = "";
    this.q = new Array(n).fill(0);
    this.qk = new Array(n).fill("");
    this.mv = new Map();          // key -> [kind, src, dest, t0_10, id]
    this.x = new Map();           // key -> [kind, src, dir, t_10, id]
    this.m = [];
    this.pr = [];
    this.dep = new Array(n).fill(0);
    this.tl = [];                 // lamp windows {t0, str}, newest last (at most 3)
  }

  // -- applying frames ------------------------------------------------------ //
  _adds(flat, bt10, count) {
    for (let i = 0; i + 4 < flat.length; i += 5) {
      const id = flat[i], kind = flat[i + 1], src = flat[i + 2], dest = flat[i + 3];
      const t0 = bt10 + flat[i + 4];
      const key = id * MV_KEY + t0;
      if (this.mv.has(key)) continue;
      this.mv.set(key, [kind, src, dest, t0, id]);
      if (count) this.dep[src] += 1;
    }
  }

  _exits(flat, bt10) {
    for (let i = 0; i + 4 < flat.length; i += 5) {
      const id = flat[i], kind = flat[i + 1], src = flat[i + 2], dir = flat[i + 3];
      const t = bt10 + flat[i + 4];
      const key = id * MV_KEY + t;
      if (this.x.has(key)) continue;
      this.x.set(key, [kind, src, dir, t, id]);
      this.dep[src] += 1;
    }
  }

  _queues(d) {
    if (d.q) this.q = d.q.slice();
    if (d.qk) for (const [i, codes] of d.qk) this.qk[i] = codes;
  }

  keyframe(d, bt10, newEpisode) {
    const n = this.model.nAppr;
    if (newEpisode) {
      this.mv = new Map(); this.x = new Map(); this.dep = new Array(n).fill(0);
    }
    this.s = d.s;
    this.q = d.q.slice();
    this.qk = new Array(n).fill("");
    this._queues({ qk: d.qk });
    if (d.a) this._adds(d.a, bt10, true);         // the tick's own events first ...
    if (d.x) this._exits(d.x, bt10);
    this._adds(d.mv || [], bt10, false);          // ... then merge the snapshot
    this.m = d.m.slice();
    this.pr = d.pr.slice();
    this.tl = d.tl ? [{ t0: d.tl[0], str: d.tl[1] }] : [];
  }

  delta(d, bt10) {
    if (d.s !== undefined) this.s = d.s;
    this._queues(d);
    if (d.a) this._adds(d.a, bt10, true);
    if (d.x) this._exits(d.x, bt10);
    if (d.m) this.m = d.m.slice();
    if (d.pr) this.pr = d.pr.slice();
    if (d.tl) {
      this.tl.push({ t0: d.tl[0], str: d.tl[1] });
      if (this.tl.length > 3) this.tl.shift();
    }
  }

  prune(bt) {
    const model = this.model;
    for (const [key, v] of this.mv) if (!(bt < model.joinTime(v[0], v[3]) + KEEP_S)) this.mv.delete(key);
    for (const [key, v] of this.x) if (!(bt < v[3] / 10 + KEEP_S)) this.x.delete(key);
  }

  // -- views ---------------------------------------------------------------- //
  /** Lamp letters of every junction at display time `vt` (per-second history when the
   *  server sent it, else the newest state). */
  lampsAt(vt) {
    const n = this.model.nJunctions;
    for (let i = this.tl.length - 1; i >= 0; i--) {
      const w = this.tl[i];
      const idx = Math.floor(vt - w.t0 + 1e-6);
      if (idx >= 0 && (idx + 1) * n <= w.str.length) return w.str.substr(idx * n, n);
    }
    return this.s;
  }

  /** Same contract as Side.display in tests/dashboard_model.py. */
  display(vt) {
    const model = this.model;
    const step = model.stepS;
    const bt = model.bt;
    const n = model.nAppr;
    const join = new Map();
    const departs = new Map();
    for (const v of this.mv.values()) {
      join.set(v[4] * AP_KEY + v[2], model.joinTime(v[0], v[3]));
      departs.set(v[4] * AP_KEY + v[1], v[3] / 10 - step);
    }
    for (const v of this.x.values()) departs.set(v[4] * AP_KEY + v[1], v[3] / 10 - step);

    const front = [];
    const movers = [];
    for (let i = 0; i < n; i++) { front.push([]); movers.push([]); }
    const futureDeps = new Array(n).fill(0);
    const tailRemove = new Array(n).fill(0);
    const crossing = [];
    const exiting = [];
    let seq = 0;
    for (const v of this.mv.values()) {
      seq += 1;
      const kind = v[0], src = v[1], dest = v[2], id = v[4];
      const t0 = v[3] / 10;
      const d = t0 - step;
      const jt = join.get(id * AP_KEY + dest);
      if (vt < d) {
        futureDeps[src] += 1;
        const js = join.get(id * AP_KEY + src);
        if (js === undefined || js <= vt) front[src].push([d, seq, id, kind]);
      } else if (vt < t0) {
        crossing.push([id, kind, src, dest, d]);
      } else if (vt < jt) {
        const dd = departs.get(id * AP_KEY + dest);
        if (dd === undefined || dd > vt) movers[dest].push([jt, id, kind, src, t0]);
      }
      if (vt < jt && jt <= bt + 1e-9 && !departs.has(id * AP_KEY + dest)) tailRemove[dest] += 1;
    }
    for (const v of this.x.values()) {
      seq += 1;
      const kind = v[0], src = v[1], dir = v[2], id = v[4];
      const t = v[3] / 10;
      const d = t - step;
      if (vt < d) {
        futureDeps[src] += 1;
        const js = join.get(id * AP_KEY + src);
        if (js === undefined || js <= vt) front[src].push([d, seq, id, kind]);
      } else if (vt < t) {
        crossing.push([id, kind, src, -1 - dir, d]);
      } else {
        exiting.push([id, kind, src, dir, t]);
      }
    }

    const queues = [];
    for (let ai = 0; ai < n; ai++) {
      const f = front[ai].sort(cmpTuple);
      const nBase = Math.max(0, this.q[ai] - tailRemove[ai]);
      const qk = this.qk[ai] || "";
      const codes = [];
      const ids = [];
      for (const [, , id, kind] of f) { codes.push(model.code(kind, id)); ids.push(id); }
      const take = Math.min(qk.length, nBase);
      for (let i = 0; i < take; i++) { codes.push(model.decode(qk[i])); ids.push(-1); }
      queues.push({ codes, ids, total: f.length + nBase, abs0: this.dep[ai] - futureDeps[ai],
                    front_ids: f.map((e) => e[2]) });
    }
    for (const lst of movers) lst.sort(cmpTuple);
    return { queues, movers, crossing, exiting };
  }
}

function cmpTuple(a, b) {
  for (let i = 0; i < a.length; i++) {
    if (a[i] < b[i]) return -1;
    if (a[i] > b[i]) return 1;
  }
  return 0;
}

export class Model {
  constructor() {
    this.hello = null;
    this.kinds = [];          // [{kind, label, group, len, wid, em, sf, code}]
    this.travelOf = [];
    this.sides = {};
    this.g = {};
    this.h = [];
    this.window = 120;
    this.nAppr = 0;
    this.nJunctions = 0;
    this.stepS = 1;
    this.alphabet = "";
    this.alphaIndex = new Map();
    this.nv = 1;
    this.bt = 0;
    this.ep = null;
    this.newEpisode = false;  // the last applied message started a new episode (or was hello)
    this.rev = 0;             // bumped on every applied frame (cheap change detection)
    this.hRev = 0;            // bumped whenever the chart history changed
  }

  joinTime(kind, t010) {
    const travel = this.travelOf[kind];
    return t010 / 10 + this.stepS * Math.ceil(travel / this.stepS - 1e-6);
  }

  code(kind, id) { return kind * this.nv + (id % this.nv); }

  decode(ch) {
    const c = this.alphaIndex.get(ch);
    return c === undefined ? 0 : c;
  }

  /** Apply one server message. Returns "hello", "kf", "delta" or null. */
  apply(msg) {
    const t = msg && msg.t;
    if (t === "hello") {
      this.hello = msg;
      this.window = msg.win | 0;
      this.nJunctions = msg.grid.ids.length;
      this.nAppr = this.nJunctions * 4;
      this.stepS = +msg.step_s;
      this.alphabet = msg.qka;
      this.alphaIndex = new Map();
      for (let i = 0; i < this.alphabet.length; i++) this.alphaIndex.set(this.alphabet[i], i);
      this.nv = msg.nv | 0;
      const link = +msg.grid.link, free = +msg.free_mps;
      this.kinds = msg.kinds.map((k, code) => ({ kind: k[0], label: k[1], group: k[2], len: +k[3],
                                                 wid: +k[4], em: !!k[5], sf: +k[6], code }));
      this.travelOf = msg.kinds.map((k) => link / (free * +k[6]));
      this.sides = { rl: new Side(this), ft: new Side(this) };
      this.g = {}; this.h = []; this.ep = null;
      this.apply(msg.state);
      this.newEpisode = true;
      return "hello";
    }
    if (t !== "f" || !this.hello) return null;
    if (msg.bt !== undefined) this.g.bt = msg.bt;
    this.bt = +(this.g.bt || 0);
    const bt10 = Math.round(this.bt * 10);
    let kind;
    if (msg.kf) {
      const newEpisode = msg.ep !== this.ep;
      this.ep = msg.ep;
      this.newEpisode = newEpisode;
      for (const name of ["rl", "ft"]) this.sides[name].keyframe(msg[name], bt10, newEpisode);
      this.h = (msg.h || []).map((p) => p.slice());
      this.hRev += 1;
      kind = "kf";
    } else {
      this.newEpisode = false;
      for (const name of ["rl", "ft"]) {
        const side = this.sides[name];
        if (msg[name]) side.delta(msg[name], bt10);
        // per-second lamps only cover the newest frame; without them (speed >= 4) the
        // newest state is used
        if (msg.bt !== undefined && !(msg[name] && msg[name].tl)) side.tl = [];
      }
      if (msg.h) {
        for (const p of msg.h) this.h.push(p.slice());
        if (this.h.length > this.window) this.h.splice(0, this.h.length - this.window);
        this.hRev += 1;
      }
      kind = "delta";
    }
    for (const key of ["k", "e", "p", "sp", "sc", "hw"]) if (msg[key] !== undefined) this.g[key] = msg[key];
    for (const side of Object.values(this.sides)) side.prune(this.bt);
    this.rev += 1;
    return kind;
  }
}

/**
 * The display clock: simulated time to draw, running smoothly between frames.
 *
 * It trails the newest frame by about one frame's worth of simulated time, so it is always
 * interpolating between two known states rather than guessing ahead, and it follows the rate
 * the frames actually arrive at (a slow network or a busy server just slows it down).
 */
export class SimClock {
  constructor() {
    this.vt = 0;
    this.bt = 0;
    this.span = 5;          // simulated seconds per frame (EMA)
    this.rate = 25;         // simulated seconds per wall second (EMA)
    this.nominalRate = 0;
    this.sinceFrame = 0;    // wall seconds since bt last moved
    this.lastWall = 0;
    this.playing = false;
    this.started = false;
  }

  /**
   * The rate and frame span the server intends at its current speed. Snapping to them when
   * the speed changes makes a new speed right at once; the running estimate in frame() then
   * only follows a server or network that falls behind. At "real time" (hardware mode) a
   * frame advances the simulation only every 5 s, which an estimate alone would take
   * several such frames to learn.
   */
  nominal(rate, span) {
    if (!(rate > 0) || rate === this.nominalRate) return;
    this.nominalRate = rate;
    this.rate = rate;
    if (span > 0) this.span = span;
  }

  /** A frame with simulator time `bt` arrived at wall time `now` (ms). */
  frame(bt, playing, now, reset) {
    const resumed = playing && !this.playing;
    this.playing = playing;
    if (!this.started || reset || bt < this.bt - 1e-6) {
      this.bt = bt; this.vt = bt; this.lastWall = now; this.started = true; this.sinceFrame = 0;
      return;
    }
    const dbt = bt - this.bt;
    if (dbt > 0) this.sinceFrame = 0;
    const dw = (now - this.lastWall) / 1000;
    if (!playing || resumed) {
      this.lastWall = now;      // time spent paused says nothing about the playback rate
    } else if (dbt > 0) {
      if (dw > 0.02 && dw < 30) {
        this.rate += 0.25 * (dbt / dw - this.rate);
        this.span += 0.25 * (dbt - this.span);
      }
      this.lastWall = now;
    }
    this.bt = bt;
  }

  /** Advance to wall time `now` given `dt` seconds since the last call; returns vt. */
  advance(dt) {
    if (!this.started) return this.vt;
    const span = Math.max(1, this.span);
    if (!this.playing) {
      this.vt += (this.bt - this.vt) * Math.min(1, dt * 6);   // ease onto stepped states
      if (Math.abs(this.bt - this.vt) < 0.01) this.vt = this.bt;
      return this.vt;
    }
    // The target moves on at the playback rate between frames instead of jumping when one
    // arrives, so the speed stays steady even when frames are 5 s apart (real time), and it
    // trails the newest frame by 0.5 to 1.5 spans - the same average lag as aiming a fixed
    // span behind, with half a span in hand for a late frame.
    this.sinceFrame += dt;
    const target = this.bt - 1.5 * span + Math.min(span, this.sinceFrame * this.rate);
    const err = target - this.vt;
    if (Math.abs(err) > 3 * span) this.vt = target;           // far off (tab was hidden)
    else this.vt += this.rate * dt * (1 + Math.max(-0.5, Math.min(0.5, err / span)));
    if (this.vt > this.bt) this.vt = this.bt;                 // never draw past known data
    return this.vt;
  }
}
