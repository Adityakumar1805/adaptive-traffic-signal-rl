// Connection to the server: a WebSocket first, with exponential back-off, a heartbeat
// watchdog and an application-level ping; plain HTTP polling of /api/state only as a last
// resort - when WebSockets are blocked on the visitor's network, or when the server says it
// has none (the zero-dependency stdlib server marks its page data-transport="poll").
//
// Status values reported through `onStatus(state, info)`:
//   "connecting"   first attempt                    "live"      WebSocket streaming
//   "waking"       server unreachable, never seen  "polling"   HTTP fallback
//   "reconnecting" connection lost, retrying       "busy"      server full, retrying later
//   "hidden"       tab hidden for a while, disconnected to save the server's bandwidth

const HEARTBEAT_TIMEOUT_MS = 7000;   // the server sends a frame or a heartbeat at least every 2 s
const PING_EVERY_MS = 15000;
const BACKOFF_MIN_MS = 500;
const BACKOFF_MAX_MS = 15000;
const CONNECT_TIMEOUT_MS = 15000;   // a sleeping free instance can hold the handshake while it boots
const POLL_EVERY_MS = 1000;
const WS_RETRY_WHILE_POLLING_MS = 30000;
const FAILS_BEFORE_POLLING = 3;
const HIDDEN_DISCONNECT_MS = 60000;
const HTTP_TIMEOUT_MS = 8000;
const COMMAND_TIMEOUT_MS = 6000;

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws`;
}

async function fetchJson(path, options = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), HTTP_TIMEOUT_MS);
  try {
    const res = await fetch(path, { cache: "no-store", ...options, signal: ctrl.signal });
    let body = null;
    try { body = await res.json(); } catch (e) { body = null; }
    if (!res.ok) {
      const err = new Error((body && (body.error || body.err)) || `HTTP ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return body;
  } finally {
    clearTimeout(timer);
  }
}

export class Transport {
  /** @param {{onMessage: (msg: object) => void, onStatus: (state: string, info: object) => void}} h */
  constructor(h) {
    this.h = h;
    this.ws = null;
    this.state = "connecting";
    this.wsAllowed = document.documentElement.dataset.transport !== "poll";
    this.fails = 0;               // consecutive failed attempts
    this.attempts = 0;            // attempts since the last success (shown on the wake screen)
    this.everLive = false;
    this.lastMsgAt = 0;
    this.rtt = null;
    this.retryAt = 0;
    this.timers = { retry: 0, poll: 0, watchdog: 0, ping: 0, hidden: 0 };
    this.pending = new Map();
    this.nextRef = 1;
    this.polling = false;         // a poll loop is running (it owns the "live" data then)
    this.pollGen = 0;
    this.hidden = false;
    this.busyUntil = 0;
  }

  start() {
    document.addEventListener("visibilitychange", () => this._visibility());
    window.addEventListener("online", () => { if (!this.ws && !this.polling) this._connectNow(); });
    this._connect();
  }

  _status(state, extra = {}) {
    this.state = state;
    this.h.onStatus(state, { attempts: this.attempts, retryAt: this.retryAt, rtt: this.rtt, ...extra });
  }

  _offlineStatus() {
    if (!this.polling) this._status(this.everLive ? "reconnecting" : (this.attempts > 1 ? "waking" : "connecting"));
  }

  _schedule(delay) {
    clearTimeout(this.timers.retry);
    this.retryAt = Date.now() + delay;
    this.timers.retry = setTimeout(() => this._connect(), delay);
  }

  _backoff() {
    const base = Math.min(BACKOFF_MAX_MS, BACKOFF_MIN_MS * 2 ** Math.min(10, this.fails));
    return base * (0.75 + Math.random() * 0.5);
  }

  _connectNow() {
    clearTimeout(this.timers.retry);
    this._connect();
  }

  /** One connection attempt: a WebSocket, or a poll loop when the server has no sockets. */
  _connect() {
    if (this.hidden || this.ws) return;
    if (!this.wsAllowed) {
      if (this.polling) return;
      this.attempts += 1;
      this._offlineStatus();
      this._startPolling().then((ok) => {
        if (ok) return;
        this.fails += 1;
        this._offlineStatus();
        this._schedule(this._backoff());
      });
      return;
    }
    this.attempts += 1;
    this._offlineStatus();
    let ws;
    try {
      ws = new WebSocket(wsUrl());
    } catch (e) {
      this._wsFailed(false);
      return;
    }
    this.ws = ws;
    let gotHello = false;
    const connectTimer = setTimeout(() => { if (!gotHello) this._drop(ws); }, CONNECT_TIMEOUT_MS);
    ws.onmessage = (ev) => {
      if (this.ws !== ws) return;
      this.lastMsgAt = performance.now();
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (!msg || typeof msg !== "object") return;
      switch (msg.t) {
        case "hello":
          gotHello = true;
          clearTimeout(connectTimer);
          this.fails = 0;
          this.attempts = 0;
          this.everLive = true;
          this._stopPolling();
          this._startWatchdog(ws);
          this._status("live");
          break;
        case "pong":
          if (typeof msg.ts === "number") this.rtt = Math.max(0, performance.now() - msg.ts);
          if (this.state === "live") this._status("live");
          return;
        case "ack":
          this._settle(msg.i, msg.ok ? { ok: true } : { ok: false, error: msg.error || "rejected" });
          return;
        case "busy":
          this.busyUntil = Date.now() + 1000 * Math.max(5, +msg.retry_s || 30);
          return;
        case "hb":
          return;
        default:
          break;
      }
      this.h.onMessage(msg);
    };
    ws.onclose = () => {
      clearTimeout(connectTimer);
      if (this.ws !== ws) return;
      this.ws = null;
      this._stopWatchdog();
      this._failPending("the connection was lost");
      if (this.hidden) return;
      this._wsFailed(gotHello);
    };
    ws.onerror = () => { /* onclose follows and handles the retry */ };
  }

  _drop(ws) {
    try { ws.close(); } catch (e) { /* already closed */ }
  }

  _wsFailed(wasLive) {
    if (wasLive) this.fails = 0; else this.fails += 1;
    if (Date.now() < this.busyUntil) {
      this._schedule(this.busyUntil - Date.now());
      this._status("busy", { retryAt: this.retryAt });
      return;
    }
    if (this.polling) {                                  // polling works: retry sockets rarely
      this._schedule(WS_RETRY_WHILE_POLLING_MS);
      return;
    }
    this._schedule(wasLive ? BACKOFF_MIN_MS : this._backoff());
    this._offlineStatus();
    if (this.fails >= FAILS_BEFORE_POLLING) this._startPolling();
  }

  _startWatchdog(ws) {
    this._stopWatchdog();
    this.timers.watchdog = setInterval(() => {
      if (this.ws !== ws) return;
      if (performance.now() - this.lastMsgAt > HEARTBEAT_TIMEOUT_MS) this._drop(ws);   // silent link
    }, 1000);
    const ping = () => {
      if (this.ws === ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({ a: "ping", ts: performance.now() })); } catch (e) { /* closing */ }
      }
    };
    ping();
    this.timers.ping = setInterval(ping, PING_EVERY_MS);
  }

  _stopWatchdog() {
    clearInterval(this.timers.watchdog);
    clearInterval(this.timers.ping);
  }

  // ------------------------------------------------------------ polling
  /** Fetch hello and start polling keyframes; resolves to false if the server is unreachable. */
  async _startPolling() {
    if (this.polling) return true;
    let hello;
    try {
      hello = await fetchJson("/api/hello");
    } catch (e) {
      return false;
    }
    if (this.ws || this.polling || this.hidden) return true;
    this.polling = true;
    const gen = ++this.pollGen;
    this.fails = 0;
    this.attempts = 0;
    this.everLive = true;
    this.h.onMessage(hello);
    this._status("polling");
    const tick = async () => {
      if (gen !== this.pollGen) return;
      try {
        const frame = await fetchJson("/api/state");
        if (gen === this.pollGen) this.h.onMessage(frame);
      } catch (e) {
        if (gen !== this.pollGen) return;
        this._stopPolling();
        this.fails += 1;
        this._offlineStatus();
        if (!this.ws) this._schedule(this._backoff());
        return;
      }
      if (gen === this.pollGen) this.timers.poll = setTimeout(tick, POLL_EVERY_MS);
    };
    this.timers.poll = setTimeout(tick, POLL_EVERY_MS);
    return true;
  }

  _stopPolling() {
    this.polling = false;
    this.pollGen += 1;
    clearTimeout(this.timers.poll);
  }

  // ------------------------------------------------------------ commands
  /** Send a command; resolves to {ok: true} or {ok: false, error}. Never rejects. */
  send(cmd) {
    const ws = this.ws;
    if (ws && ws.readyState === WebSocket.OPEN && this.state === "live") {
      const ref = this.nextRef++;
      return new Promise((resolve) => {
        const timer = setTimeout(() => this._settle(ref, { ok: false, error: "no answer from the server" }),
                                 COMMAND_TIMEOUT_MS);
        this.pending.set(ref, { resolve, timer });
        try {
          ws.send(JSON.stringify({ ...cmd, i: ref }));
        } catch (e) {
          this._settle(ref, { ok: false, error: "the connection was lost" });
        }
      });
    }
    return fetchJson("/api/cmd", { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify(cmd) })
      .then(() => ({ ok: true }), (e) => ({ ok: false, error: e.message || "request failed" }));
  }

  _settle(ref, result) {
    const p = this.pending.get(ref);
    if (!p) return;
    clearTimeout(p.timer);
    this.pending.delete(ref);
    p.resolve(result);
  }

  _failPending(error) {
    for (const ref of [...this.pending.keys()]) this._settle(ref, { ok: false, error });
  }

  // ------------------------------------------------------------ visibility
  _visibility() {
    if (document.visibilityState === "hidden") {
      clearTimeout(this.timers.hidden);
      this.timers.hidden = setTimeout(() => {
        this.hidden = true;
        this._stopPolling();
        clearTimeout(this.timers.retry);
        const ws = this.ws;
        this.ws = null;
        this._stopWatchdog();
        this._failPending("the tab was hidden");
        if (ws) { try { ws.close(1000, "hidden"); } catch (e) { /* ignore */ } }
        this._status("hidden");
      }, HIDDEN_DISCONNECT_MS);
    } else {
      clearTimeout(this.timers.hidden);
      if (this.hidden) {
        this.hidden = false;
        this.fails = 0;
        this._connectNow();
      }
    }
  }
}
