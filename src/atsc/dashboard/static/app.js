/* ================================================================
   Dashboard client: live state stream -> canvas network render +
   native canvas line charts + controls. No build step, NO external
   libraries, NO internet — works fully offline for the live demo.
   ================================================================ */
(() => {
  "use strict";

  const COL = {
    go: "#24d17e", amber: "#ffb020", stop: "#ff5468", road: "#2a3752",
    ink: "#e8eefc", dim: "#8fa2c4", rl: "#24d17e", ft: "#7c8aa5",
    emergency: "#ff5468",
  };

  let latest = null;
  let ws = null;
  let wsOk = false;
  let pollTimer = null;
  let wsRetry = null;
  let lastDataAt = 0;

  // ---------- transport: prefer WebSocket, fall back to polling /api/state ----------
  // Status reflects whether DATA is flowing (freshness), not which transport is up, so a
  // flaky WebSocket quietly falls back to polling and never sticks on "reconnecting".
  function gotData(s) {
    latest = s; lastDataAt = Date.now();
    ingest("rl", s.rl && s.rl.moving); ingest("ft", s.fixed && s.fixed.moving);
    render(s);
  }

  function connect() {
    try {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => { wsOk = true; stopPolling(); };
      ws.onmessage = (ev) => { wsOk = true; try { gotData(JSON.parse(ev.data)); } catch (e) {} };
      ws.onerror = () => { wsOk = false; startPolling(); };
      ws.onclose = () => { wsOk = false; startPolling(); scheduleWsRetry(); };
    } catch (e) {
      startPolling();
    }
  }
  function scheduleWsRetry() { clearTimeout(wsRetry); wsRetry = setTimeout(connect, 15000); }
  function startPolling() {
    if (pollTimer) return;
    const tick = () => fetch("/api/state").then(r => r.json()).then(gotData).catch(() => {});
    tick();
    pollTimer = setInterval(tick, 250);
  }
  function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }
  function send(obj) {
    if (wsOk && ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
    else fetch("/api/cmd", { method: "POST", headers: { "Content-Type": "application/json" },
                             body: JSON.stringify(obj) }).catch(() => {});
  }
  function refreshStatus() {
    const fresh = Date.now() - lastDataAt < 1500;
    const dot = document.getElementById("connDot");
    const txt = document.getElementById("statusText");
    if (dot) dot.className = "conn " + (fresh ? "on" : "off");
    if (txt) txt.textContent = fresh ? (wsOk ? "live · streaming" : "live · polling") : "reconnecting…";
  }

  // ---------- vehicle sprites (native canvas; one draw fn per real vehicle type) ----------
  // len/wid are in metres-ish units scaled by the view; hue pairs are body/roof.
  const VEHICLES = {
    bike:      { len: 2.2, wid: 0.95, body: "#c2412f", trim: "#2a4a63" },
    car:       { len: 4.2, wid: 1.75, body: "#dbe8ff", trim: "#93b4e8" },
    taxi:      { len: 4.2, wid: 1.75, body: "#ffc93c", trim: "#2b2b2b" },
    auto:      { len: 2.9, wid: 1.35, body: "#f5c518", trim: "#0f7a4f" },
    erick:     { len: 3.0, wid: 1.40, body: "#2f7fd6", trim: "#c9e2ff" },
    van:       { len: 5.2, wid: 1.95, body: "#9be7bf", trim: "#5fb98c" },
    bus:       { len: 9.5, wid: 2.45, body: "#ffd766", trim: "#c98f14" },
    truck:     { len: 8.5, wid: 2.40, body: "#ff9d5c", trim: "#54657f" },
    ambulance: { len: 6.0, wid: 2.20, body: "#ffffff", trim: "#ff2d55" },
  };
  // heading angle per travel direction; 0 = east and canvas y grows downwards
  const HEADING = { N: -Math.PI / 2, S: Math.PI / 2, E: 0, W: Math.PI };

  // A car with an odd id gets a taxi livery. Still one real simulated car — just paint.
  function kindOf(v) {
    const k = v.k || "car";
    if (k !== "car") return k;
    const s = String(v.id || "");
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) & 0xffff;
    return (h % 5 === 0) ? "taxi" : "car";
  }

  // Draw one vehicle centred at x,y, nose pointing along `ang` (radians, 0 = east).
  function drawVehicle(ctx, x, y, v, ang) {
    const kind = (v.e || v.k === "ambulance") ? "ambulance" : kindOf(v);
    const spec = VEHICLES[kind] || VEHICLES.car;
    const L = spec.len * PPM, W = spec.wid * PPM;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(ang || 0);
    ctx.fillStyle = "rgba(0,0,0,.34)";
    const sw = (kind === "bike") ? W * 0.62 : W;             // a bike casts a bike-sized shadow
    rr(ctx, -L / 2 + 0.8, -sw / 2 + 1.2, L, sw, sw * 0.28); ctx.fill();
    if (kind === "ambulance") drawAmbulanceBody(ctx, L, W);
    else if (kind === "bike") drawBike(ctx, L, W, spec);
    else if (kind === "auto" || kind === "erick") drawThreeWheeler(ctx, L, W, spec, kind === "erick");
    else if (kind === "bus") drawBus(ctx, L, W, spec);
    else if (kind === "truck") drawTruck(ctx, L, W, spec);
    else drawCar(ctx, L, W, spec, kind === "taxi");
    ctx.restore();
  }

  function rr(ctx, x, y, w, h, r) {
    const k = Math.min(r, h / 2, w / 2);
    ctx.beginPath();
    ctx.moveTo(x + k, y);
    ctx.arcTo(x + w, y, x + w, y + h, k);
    ctx.arcTo(x + w, y + h, x, y + h, k);
    ctx.arcTo(x, y + h, x, y, k);
    ctx.arcTo(x, y, x + w, y, k);
    ctx.closePath();
  }
  function wheels(ctx, L, W, pairs) {
    ctx.fillStyle = "#0b0f18";
    const r = Math.max(1, W * 0.17);
    pairs.forEach((fx) => {
      rr(ctx, fx * L / 2 - r, -W / 2 - r * 0.5, r * 2, r * 1.4, r * 0.5); ctx.fill();
      rr(ctx, fx * L / 2 - r,  W / 2 - r * 0.9, r * 2, r * 1.4, r * 0.5); ctx.fill();
    });
  }
  function lamps(ctx, L, W) {
    ctx.fillStyle = "rgba(255,246,214,.95)";                 // headlights
    ctx.fillRect(L / 2 - 1.2, -W / 2 + W * 0.16, 1.2, W * 0.2);
    ctx.fillRect(L / 2 - 1.2,  W / 2 - W * 0.36, 1.2, W * 0.2);
    ctx.fillStyle = "rgba(255,72,88,.9)";                    // tail lights
    ctx.fillRect(-L / 2, -W / 2 + W * 0.16, 1.1, W * 0.2);
    ctx.fillRect(-L / 2,  W / 2 - W * 0.36, 1.1, W * 0.2);
  }

  function drawCar(ctx, L, W, spec, isTaxi) {
    wheels(ctx, L, W, [-0.62, 0.62]);
    ctx.fillStyle = spec.body;
    rr(ctx, -L / 2, -W / 2, L, W, W * 0.30); ctx.fill();
    ctx.fillStyle = "rgba(12,20,36,.55)";                    // cabin glass
    rr(ctx, -L * 0.20, -W / 2 + W * 0.14, L * 0.44, W * 0.72, W * 0.16); ctx.fill();
    ctx.fillStyle = spec.trim;                               // bonnet crease
    ctx.fillRect(L * 0.26, -W / 2 + W * 0.2, L * 0.16, W * 0.6);
    if (isTaxi) {                                            // roof sign + checker
      ctx.fillStyle = "#2b2b2b";
      ctx.fillRect(-L * 0.05, -W * 0.16, L * 0.12, W * 0.32);
      ctx.fillStyle = "rgba(0,0,0,.75)";
      for (let i = 0; i < 4; i++) ctx.fillRect(-L / 2 + i * L * 0.14, W / 2 - 1.6, L * 0.07, 1.6);
    }
    lamps(ctx, L, W);
  }

  // Motorcycle from above. The readable cues at small scale are the T of the handlebar
  // across a narrow body and the white helmet, so those are drawn at full contrast.
  function drawBike(ctx, L, W, spec) {
    const px = (v) => Math.max(1, v);
    ctx.fillStyle = "#0b0f18";                               // rear tyre, then front tyre
    rr(ctx, -L / 2, -W * 0.16, L * 0.26, W * 0.32, W * 0.15); ctx.fill();
    rr(ctx,  L * 0.26, -W * 0.14, L * 0.24, W * 0.28, W * 0.13); ctx.fill();
    ctx.fillStyle = "#52658a";                               // frame, tank and seat
    rr(ctx, -L * 0.40, -W * 0.20, L * 0.80, W * 0.40, W * 0.19); ctx.fill();
    ctx.fillStyle = "#8b98ad";                               // exhaust along the near side
    ctx.fillRect(-L * 0.34, W * 0.20, L * 0.34, px(W * 0.09));
    ctx.fillStyle = "#9db0cf";                               // handlebar, spans the full width
    rr(ctx, L * 0.19, -W * 0.46, px(L * 0.09), W * 0.92, W * 0.12); ctx.fill();
    ctx.fillStyle = spec.body;                               // rider's jacket and shoulders
    rr(ctx, -L * 0.20, -W * 0.36, L * 0.38, W * 0.72, W * 0.32); ctx.fill();
    ctx.fillStyle = "#dbe4f2";                               // helmet
    ctx.beginPath(); ctx.arc(L * 0.05, 0, px(W * 0.23), 0, 7); ctx.fill();
    ctx.fillStyle = "rgba(12,20,36,.72)";                    // visor, facing the nose
    rr(ctx, L * 0.09, -W * 0.15, px(W * 0.11), W * 0.30, W * 0.1); ctx.fill();
    ctx.fillStyle = "rgba(255,72,88,.95)";                   // tail lamp
    ctx.fillRect(-L / 2, -W * 0.13, px(L * 0.05), W * 0.26);
    ctx.fillStyle = "rgba(255,246,214,.95)";                 // headlamp
    ctx.fillRect(L / 2 - px(L * 0.06), -W * 0.13, px(L * 0.06), W * 0.26);
  }

  // Indian three-wheeler from above: one front wheel, two rear, a shell that tapers to a
  // rounded nose, and a dark canopy over the passenger bench. `isErick` swaps in the
  // flat-roofed battery-rickshaw livery instead of the auto's canvas hood.
  function drawThreeWheeler(ctx, L, W, spec, isErick) {
    const px = (v) => Math.max(1, v);
    wheels(ctx, L, W, [-0.56]);                              // rear pair, stubs outboard
    ctx.fillStyle = spec.body;                               // shell: squared tail, narrow nose
    ctx.beginPath();
    ctx.moveTo(-L * 0.50, -W * 0.44);
    ctx.lineTo(L * 0.10, -W * 0.48);
    ctx.bezierCurveTo(L * 0.32, -W * 0.44, L * 0.46, -W * 0.30, L * 0.50, -W * 0.13);
    ctx.lineTo(L * 0.50, W * 0.13);
    ctx.bezierCurveTo(L * 0.46, W * 0.30, L * 0.32, W * 0.44, L * 0.10, W * 0.48);
    ctx.lineTo(-L * 0.50, W * 0.44);
    ctx.closePath(); ctx.fill();
    ctx.fillStyle = isErick ? "#12395f" : "#141b2d";         // canopy over the bench
    rr(ctx, -L * 0.44, -W * 0.36, L * 0.52, W * 0.72, W * 0.16); ctx.fill();
    if (isErick) {                                           // open sides: bright roof pillars
      ctx.fillStyle = spec.trim;
      ctx.fillRect(-L * 0.44, -W * 0.36, L * 0.52, px(W * 0.10));
      ctx.fillRect(-L * 0.44,  W * 0.36 - px(W * 0.10), L * 0.52, px(W * 0.10));
    }
    ctx.fillStyle = "rgba(150,200,255,.55)";                 // windscreen ahead of the canopy
    ctx.beginPath();
    ctx.moveTo(L * 0.10, -W * 0.30); ctx.lineTo(L * 0.34, -W * 0.17);
    ctx.lineTo(L * 0.34, W * 0.17); ctx.lineTo(L * 0.10, W * 0.30);
    ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#0b0f18";                               // single steered front wheel
    rr(ctx, L * 0.34, -W * 0.10, px(L * 0.13), W * 0.20, W * 0.08); ctx.fill();
    ctx.fillStyle = spec.trim;                                // rear livery panel
    rr(ctx, -L * 0.50, -W * 0.30, px(L * 0.09), W * 0.60, W * 0.1); ctx.fill();
    ctx.fillStyle = "rgba(255,246,214,.95)";                 // single headlamp
    ctx.beginPath(); ctx.arc(L * 0.46, 0, px(W * 0.10), 0, 7); ctx.fill();
  }

  // Top-down bus: long roof, side-window strips, windscreen, route board.
  function drawBus(ctx, L, W, spec) {
    wheels(ctx, L, W, [-0.74, 0.44]);
    ctx.fillStyle = spec.body;
    rr(ctx, -L / 2, -W / 2, L, W, W * 0.18); ctx.fill();
    ctx.fillStyle = spec.trim;                               // roof spine
    ctx.fillRect(-L * 0.44, -W * 0.16, L * 0.80, W * 0.32);
    ctx.fillStyle = "rgba(12,20,36,.5)";                     // side windows
    for (let i = 0; i < 6; i++) {
      const x = -L * 0.40 + i * L * 0.125;
      ctx.fillRect(x, -W / 2 + W * 0.06, L * 0.09, W * 0.14);
      ctx.fillRect(x,  W / 2 - W * 0.20, L * 0.09, W * 0.14);
    }
    ctx.fillStyle = "rgba(12,20,36,.62)";                    // windscreen
    rr(ctx, L * 0.34, -W / 2 + W * 0.10, L * 0.12, W * 0.80, W * 0.10); ctx.fill();
    ctx.fillStyle = "#f2f6ff";                               // route board
    ctx.fillRect(L * 0.28, -W * 0.26, L * 0.04, W * 0.52);
    lamps(ctx, L, W);
  }

  // Top-down truck: short cab, gap, then a pale cargo box.
  function drawTruck(ctx, L, W, spec) {
    wheels(ctx, L, W, [-0.80, -0.34, 0.66]);
    ctx.fillStyle = spec.body;                               // cab
    rr(ctx, L * 0.18, -W / 2, L * 0.32, W, W * 0.22); ctx.fill();
    ctx.fillStyle = "rgba(12,20,36,.58)";                    // cab windscreen
    ctx.fillRect(L * 0.22, -W / 2 + W * 0.14, L * 0.07, W * 0.72);
    ctx.fillStyle = spec.trim;                               // chassis between units
    ctx.fillRect(L * 0.10, -W * 0.22, L * 0.12, W * 0.44);
    ctx.fillStyle = "#c9d6ea";                               // cargo box
    rr(ctx, -L / 2, -W / 2 + W * 0.03, L * 0.60, W * 0.94, W * 0.10); ctx.fill();
    ctx.strokeStyle = "rgba(40,56,84,.75)"; ctx.lineWidth = 0.6;
    for (let i = 1; i < 4; i++) {                            // box ribs
      const x = -L / 2 + i * L * 0.15;
      ctx.beginPath(); ctx.moveTo(x, -W / 2 + W * 0.06); ctx.lineTo(x, W / 2 - W * 0.06); ctx.stroke();
    }
    lamps(ctx, L, W);
  }

  // Ambulance: white box body, red belt stripe, roof cross, alternating lightbar.
  function drawAmbulanceBody(ctx, L, W) {
    wheels(ctx, L, W, [-0.66, 0.58]);
    ctx.fillStyle = "#ffffff";
    rr(ctx, -L / 2, -W / 2, L, W, W * 0.20); ctx.fill();
    ctx.fillStyle = "#ff2d55";                               // belt stripe both sides
    ctx.fillRect(-L * 0.46, -W / 2 + W * 0.02, L * 0.86, W * 0.10);
    ctx.fillRect(-L * 0.46,  W / 2 - W * 0.12, L * 0.86, W * 0.10);
    ctx.fillStyle = "rgba(12,20,36,.60)";                    // windscreen
    rr(ctx, L * 0.30, -W / 2 + W * 0.12, L * 0.14, W * 0.76, W * 0.10); ctx.fill();
    ctx.fillStyle = "#ff2d55";                               // roof cross
    ctx.fillRect(-L * 0.20, -W * 0.09, L * 0.26, W * 0.18);
    ctx.fillRect(-L * 0.11, -W * 0.28, L * 0.08, W * 0.56);
    const blue = (performance.now() % 600) < 300;            // alternating beacons
    const bar = L * 0.06, off = W * 0.24;
    ctx.shadowBlur = 9;
    ctx.fillStyle = blue ? "#4da6ff" : "rgba(90,150,220,.35)";
    ctx.shadowColor = blue ? "#4da6ff" : "transparent";
    ctx.fillRect(L * 0.16, -off - W * 0.09, bar, W * 0.18);
    ctx.fillStyle = blue ? "rgba(255,80,96,.35)" : "#ff5468";
    ctx.shadowColor = blue ? "transparent" : "#ff5468";
    ctx.fillRect(L * 0.16, off - W * 0.09, bar, W * 0.18);
    ctx.shadowBlur = 0;
    lamps(ctx, L, W);
  }

  // ---------- smooth motion: interpolate positions between data frames ----------
  const anim = { rl: { A: null, B: null }, ft: { A: null, B: null } };
  function ingest(side, moving) {
    const m = {};
    (moving || []).forEach(v => { if (v.id != null) m[v.id] = v; });
    anim[side].A = anim[side].B;
    anim[side].B = { t: performance.now(), m };
    if (!anim[side].A) anim[side].A = anim[side].B;
  }
  function interp(side) {
    const f = anim[side]; if (!f.B) return [];
    const dur = Math.max(1, f.B.t - f.A.t);
    const alpha = Math.max(0, Math.min(1, (performance.now() - f.B.t) / dur));
    const out = [];
    for (const id in f.B.m) {
      const b = f.B.m[id], a = f.A.m[id] || b;
      out.push({ x: a.x + (b.x - a.x) * alpha, y: a.y + (b.y - a.y) * alpha,
                 e: b.e, k: b.k, o: b.o, d: b.d, id: b.id });
    }
    return out;
  }
  function animate() {
    if (latest) {
      drawNetwork(document.getElementById("netRL"), latest.rl, interp("rl"));
      drawNetwork(document.getElementById("netFT"), latest.fixed, interp("ft"));
    }
    requestAnimationFrame(animate);
  }

  // ---------- road geometry (shared by every network draw helper) ----------
  const AP = { N: [0, -1], S: [0, 1], W: [-1, 0], E: [1, 0] };  // screen dir of an approach
  const INBOUND = { N: "S", S: "N", E: "W", W: "E" };           // travel dir of its traffic
  let PPM = 2.4;                        // px per metre for bodies; recomputed per canvas
  const LANE_M = 2.9;                   // drawn lane width in metres (2 lanes each way)

  // Size the backing store to the CSS box x dpr so nothing is blurry, then draw in CSS px.
  function fitCanvas(canvas) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = canvas.clientWidth || canvas.width;
    const h = canvas.clientHeight || w;
    if (canvas.width !== Math.round(w * dpr)) canvas.width = Math.round(w * dpr);
    if (canvas.height !== Math.round(h * dpr)) canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx, w, h };
  }

  // axis-aligned bar, `la` long along the approach axis and `lp` across it
  function bar(ctx, x, y, la, lp, a) {
    const w = a[0] ? la : lp, h = a[0] ? lp : la;
    ctx.fillRect(x - w / 2, y - h / 2, w, h);
  }

  // built-up land between the roads — cheap, but it stops the grid floating in space
  function drawBlocks(ctx, xs, ys, hr) {
    for (let i = 0; i < xs.length - 1; i++) {
      for (let j = 0; j < ys.length - 1; j++) {
        const x0 = xs[i] + hr + 2, x1 = xs[i + 1] - hr - 2;
        const y0 = ys[j] + hr + 2, y1 = ys[j + 1] - hr - 2;
        if (x1 - x0 < 6 || y1 - y0 < 6) continue;
        ctx.fillStyle = "#0d1526";
        rr(ctx, x0, y0, x1 - x0, y1 - y0, 5); ctx.fill();
        ctx.strokeStyle = "rgba(56,80,126,.22)"; ctx.lineWidth = 1; ctx.stroke();
      }
    }
  }
  // one road link: kerb, asphalt, double centre line, dashed lane dividers
  function drawLink(ctx, x1, y1, x2, y2, hr) {
    const horiz = Math.abs(y2 - y1) < 0.5;
    const line = (o, col, lw, dash) => {
      ctx.strokeStyle = col; ctx.lineWidth = lw; ctx.setLineDash(dash || []);
      ctx.beginPath();
      if (horiz) { ctx.moveTo(x1, y1 + o); ctx.lineTo(x2, y2 + o); }
      else { ctx.moveTo(x1 + o, y1); ctx.lineTo(x2 + o, y2); }
      ctx.stroke(); ctx.setLineDash([]);
    };
    ctx.lineCap = "butt";
    line(0, "#151d2c", hr * 2 + 3.5);                     // kerb
    line(0, COL.road, hr * 2);                            // asphalt
    const t = Math.max(0.7, hr * 0.05);
    line(-hr * 0.07, "rgba(255,205,90,.50)", t);          // double centre line
    line(+hr * 0.07, "rgba(255,205,90,.50)", t);
    line(-hr * 0.50, "rgba(226,236,255,.30)", t, [5, 7]); // lane dividers
    line(+hr * 0.50, "rgba(226,236,255,.30)", t, [5, 7]);
  }

  // junction apron + zebra crossings + a stop line on each inbound half
  function drawJunction(ctx, cx, cy, hr, ped) {
    ctx.fillStyle = "#31405e";                          // paved apron, a shade above asphalt
    rr(ctx, cx - hr, cy - hr, hr * 2, hr * 2, hr * 0.14); ctx.fill();
    ctx.strokeStyle = "rgba(120,145,190,.18)"; ctx.lineWidth = 1; ctx.stroke();
    Object.keys(AP).forEach((ap) => {
      const a = AP[ap], L = [-a[1], a[0]];                // L points into the inbound half
      const bx = cx + a[0] * (hr + ped * 0.6), by = cy + a[1] * (hr + ped * 0.6);
      ctx.fillStyle = "rgba(226,236,255,.24)";
      for (let k = 0; k < 5; k++) {                       // zebra
        const u = (k - 2) * (hr * 0.4);
        bar(ctx, bx + L[0] * u, by + L[1] * u, ped * 0.7, hr * 0.22, a);
      }
      ctx.fillStyle = "rgba(236,244,255,.70)";            // stop line
      const d = hr + ped * 1.55;
      bar(ctx, cx + a[0] * d + L[0] * hr * 0.5, cy + a[1] * d + L[1] * hr * 0.5,
          Math.max(1.4, hr * 0.085), hr, a);
    });
  }
  // 3-aspect head on the near-side kerb; aspect comes straight from the safety FSM
  function drawSignal(ctx, cx, cy, ap, aspect, hr, ped) {
    const a = AP[ap], L = [-a[1], a[0]];
    const d = hr + ped * 1.1, u = hr + Math.max(5, hr * 0.34);
    const x = cx + a[0] * d + L[0] * u, y = cy + a[1] * d + L[1] * u;
    ctx.strokeStyle = "#46587a"; ctx.lineWidth = 1.2;                    // mast arm
    ctx.beginPath();
    ctx.moveTo(cx + a[0] * d + L[0] * (hr + 1), cy + a[1] * d + L[1] * (hr + 1));
    ctx.lineTo(x, y); ctx.stroke();
    const r = Math.max(1.15, hr * 0.085), g = r * 2.5;
    const bl = g * 3.15, bw = r * 3.0;
    ctx.fillStyle = "#0a1020"; ctx.strokeStyle = "#3d5177"; ctx.lineWidth = 1;
    rr(ctx, x - (a[0] ? bl : bw) / 2, y - (a[0] ? bw : bl) / 2,
       a[0] ? bl : bw, a[0] ? bw : bl, r); ctx.fill(); ctx.stroke();
    [["stop", COL.stop], ["amber", COL.amber], ["go", COL.go]].forEach(([k, col], i) => {
      const o = (i - 1) * g, on = k === aspect;
      ctx.fillStyle = on ? col : "rgba(126,146,182,.20)";
      if (on) { ctx.shadowColor = col; ctx.shadowBlur = 8; }
      ctx.beginPath();
      ctx.arc(x + (a[0] ? o : 0), y + (a[0] ? 0 : o), r, 0, 7); ctx.fill();
      ctx.shadowBlur = 0;
    });
  }

  // Lay the real queued vehicles back from the stop line across two lanes, using each
  // one's real length. Returns how many fitted (so the caller can badge the overflow) and
  // where each lane ends, so arriving traffic can be stacked behind the standing queue.
  function drawQueue(ctx, cx, cy, ap, kinds, hr, ped, maxD) {
    const a = AP[ap], L = [-a[1], a[0]], ang = HEADING[INBOUND[ap]];
    const lanes = [hr * 0.25, hr * 0.75];
    const start = hr + ped * 1.55 + Math.max(1, hr * 0.09);
    const cur = [start, start], gap = Math.max(1.3, 1.2 * PPM);
    let n = 0;
    for (let i = 0; i < kinds.length; i++) {
      const li = i % 2, u = lanes[li];
      const spec = VEHICLES[kinds[i]] || VEHICLES.car;
      const half = spec.len * PPM / 2;
      const d = cur[li] + half;
      if (d + half > maxD) break;
      cur[li] = d + half + gap;
      drawVehicle(ctx, cx + a[0] * d + L[0] * u, cy + a[1] * d + L[1] * u,
                  { k: kinds[i], e: kinds[i] === "ambulance", id: ap + i }, ang);
      n++;
    }
    return { n, tail: cur };
  }
  // Density cue on the kerb: bar length grows with the queue; "+n" = didn't fit on screen.
  function pressureBar(ctx, cx, cy, ap, n, shown, hr, ped, room) {
    if (!n) return;
    const a = AP[ap], L = [-a[1], a[0]], u = hr + 2.5;
    const d0 = hr + ped * 1.55;
    const len = Math.min(1, n / 14) * room * 0.92;
    ctx.fillStyle = n > 9 ? COL.stop : n > 4 ? COL.amber : "rgba(143,162,196,.6)";
    bar(ctx, cx + a[0] * (d0 + len / 2) + L[0] * u, cy + a[1] * (d0 + len / 2) + L[1] * u,
        len, Math.max(1.4, hr * 0.1), a);
    if (n > shown) {
      ctx.fillStyle = COL.dim; ctx.font = "8px ui-monospace, monospace";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText("+" + (n - shown), cx + a[0] * (d0 + len + 8) + L[0] * u,
                                      cy + a[1] * (d0 + len + 8) + L[1] * u);
    }
  }

  // Phase badge on the block corner, plus the emergency-preemption ring.
  function junctionBadge(ctx, cx, cy, hr, it, preempted) {
    if (preempted) {
      ctx.strokeStyle = COL.emergency; ctx.lineWidth = 2;
      ctx.shadowColor = COL.emergency; ctx.shadowBlur = 12;
      rr(ctx, cx - hr - 2, cy - hr - 2, hr * 2 + 4, hr * 2 + 4, hr * 0.2);
      ctx.stroke(); ctx.shadowBlur = 0;
    }
    const st = it.state || "green";
    const txt = (it.phase_name || "") + (st === "green" ? "" : st === "yellow" ? "·Y" : "·R");
    const bw = 30, bh = 12, x = cx - hr - 5 - bw, y = cy - hr - 5 - bh;
    ctx.fillStyle = preempted ? "rgba(255,84,104,.92)" : "rgba(14,22,38,.92)";
    ctx.strokeStyle = preempted ? COL.emergency : "#38507e"; ctx.lineWidth = 1;
    rr(ctx, x, y, bw, bh, 3); ctx.fill(); ctx.stroke();
    ctx.fillStyle = COL.ink; ctx.font = "8px ui-monospace, monospace";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(txt, x + bw / 2, y + bh / 2 + 0.5);
  }
  // Vehicles between intersections. Every correction here is display-only and order
  // preserving: keep left of the centre line, spread over the two lanes, squeeze link
  // progress into [junction exit .. stop line] so nobody sits inside the junction box,
  // and hold a minimum headway so two vehicles the point-queue left at the same spot
  // are not drawn on top of each other.
  function drawMoving(ctx, list, tx, ty, hr, ped, centres, linkPx, tails) {
    const stopD = hr + ped * 1.55 + 2.1 * PPM;
    const span = Math.max(1, linkPx - hr - stopD);
    const groups = new Map(), loose = [];
    list.forEach((v) => {
      const px = tx(v.x), py = ty(v.y), dir = v.d ? AP[v.d] : null;
      if (!dir) return loose.push([px, py, v, (v.o === "v") ? Math.PI / 2 : 0]);
      const L = [dir[1], -dir[0]];
      for (let i = 0; i < centres.length; i++) {
        const c = centres[i];
        const rem = (c[0] - px) * dir[0] + (c[1] - py) * dir[1];
        if (rem < -1 || rem > linkPx + 1) continue;
        if (Math.abs((c[0] - px) * L[0] + (c[1] - py) * L[1]) > hr) continue;
        const key = i + v.d;
        if (!groups.has(key)) groups.set(key, { c, dir, L, key, vs: [] });
        const kind = (v.e || v.k === "ambulance") ? "ambulance" : (v.k || "car");
        groups.get(key).vs.push({ v, len: (VEHICLES[kind] || VEHICLES.car).len * PPM,
                                  d: stopD + Math.max(0, Math.min(1, rem / linkPx)) * span });
        return;
      }
      loose.push([px, py, v, HEADING[v.d]]);
    });
    loose.forEach(([x, y, v, a]) => drawVehicle(ctx, x, y, v, a));
    const lanes = [hr * 0.25, hr * 0.75], gap = Math.max(1.3, 1.2 * PPM);
    groups.forEach((g) => {
      const t = tails && tails.get(g.key);              // stack behind the standing queue
      const cur = t ? t.slice() : [0, 0];
      g.vs.sort((p, q) => p.d - q.d).forEach((e, i) => {
        const li = i % 2, d = Math.max(e.d, cur[li] + e.len / 2);
        if (d > linkPx - hr - e.len / 2) return;      // no room left on the link
        cur[li] = d + e.len / 2 + gap;
        drawVehicle(ctx, g.c[0] - g.dir[0] * d + g.L[0] * lanes[li],
                         g.c[1] - g.dir[1] * d + g.L[1] * lanes[li], e.v, HEADING[e.v.d]);
      });
    });
  }

  // ---------- network rendering ----------
  function drawNetwork(canvas, data, movingList) {
    if (!canvas) return;
    const fit = fitCanvas(canvas), ctx = fit.ctx, W = fit.w, H = fit.h;
    if (!data || !data.intersections || W < 40) return;
    const its = data.intersections, ids = Object.keys(its);
    if (!ids.length) return;

    const xsW = [...new Set(ids.map((i) => its[i].x))].sort((p, q) => p - q);
    const ysW = [...new Set(ids.map((i) => its[i].y))].sort((p, q) => p - q);
    const linkM = xsW.length > 1 ? xsW[1] - xsW[0] : ysW.length > 1 ? ysW[1] - ysW[0] : 200;
    const pad = 22;
    const minX = xsW[0] - linkM, maxX = xsW[xsW.length - 1] + linkM;
    const minY = ysW[0] - linkM, maxY = ysW[ysW.length - 1] + linkM;
    const s = Math.min((W - 2 * pad) / Math.max(1, maxX - minX),
                       (H - 2 * pad) / Math.max(1, maxY - minY));
    const tx = (x) => pad + (x - minX) * s, ty = (y) => pad + (y - minY) * s;
    // One exaggeration factor for both the road width and the bodies, so the picture stays
    // internally consistent: at true scale a 4.2 m car would be ~2 px on this canvas.
    PPM = Math.max(2.2, Math.min(5.2, 2.45 * Math.min(W, H) / 460));
    const hr = LANE_M * 2 * PPM;              // half road width = two lanes each way
    const ped = Math.max(3, hr * 0.26);
    const linkPx = linkM * s;
    drawBlocks(ctx, [minX, ...xsW, maxX].map(tx), [minY, ...ysW, maxY].map(ty), hr);

    const done = new Set();                   // one asphalt stroke per link, stubs included
    ids.forEach((id) => {
      const it = its[id];
      const [r, c] = id.replace("J", "").split("_").map(Number);
      [[r - 1, c], [r + 1, c], [r, c - 1], [r, c + 1]].forEach(([nr, nc]) => {
        const nid = `J${nr}_${nc}`, key = [id, nid].sort().join("|");
        if (done.has(key)) return;
        done.add(key);
        const nb = its[nid];
        const x2 = nb ? nb.x : it.x + (nc - c) * linkM;
        const y2 = nb ? nb.y : it.y + (nr - r) * linkM;
        drawLink(ctx, tx(it.x), ty(it.y), tx(x2), ty(y2), hr);
      });
    });

    const centres = ids.map((id) => [tx(its[id].x), ty(its[id].y)]);
    ids.forEach((id, i) => drawJunction(ctx, centres[i][0], centres[i][1], hr, ped));

    const room = Math.max(4, linkPx - hr - (hr + ped * 1.55));
    const tails = new Map();                  // where each approach queue ends, per lane
    ids.forEach((id, i) => {
      const it = its[id], cx = centres[i][0], cy = centres[i][1];
      const green = new Set(it.green || []);
      const st = it.state || "green";
      const qs = it.queues || {}, qk = it.qk || {};
      Object.keys(AP).forEach((ap) => {
        const n = qs[ap] || 0;
        // qk is the real queue contents (front first). SUMO's stream has no types, so
        // fall back to plain cars — still one sprite per actually-queued vehicle.
        const kinds = (qk[ap] && qk[ap].length) ? qk[ap] : new Array(Math.min(n, 16)).fill("car");
        const q = drawQueue(ctx, cx, cy, ap, kinds, hr, ped, linkPx - hr);
        tails.set(i + INBOUND[ap], q.tail);
        pressureBar(ctx, cx, cy, ap, n, q.n, hr, ped, room);
        const aspect = green.has(ap) ? "go"
          : (st === "yellow" && (it.phase_name || "").includes(ap)) ? "amber" : "stop";
        drawSignal(ctx, cx, cy, ap, aspect, hr, ped);
      });
      junctionBadge(ctx, cx, cy, hr, it, (data.preempted || []).includes(id));
    });

    drawMoving(ctx, (movingList && movingList.length) ? movingList : (data.moving || []),
               tx, ty, hr, ped, centres, linkPx, tails);
  }

  // ---------- native line charts (no external library) ----------
  function drawLineChart(canvasId, labels, series) {
    const c = document.getElementById(canvasId);
    if (!c || !c.parentElement) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = c.parentElement.clientWidth, h = c.parentElement.clientHeight;
    if (w < 4 || h < 4) return;
    if (c.width !== Math.round(w * dpr)) c.width = Math.round(w * dpr);
    if (c.height !== Math.round(h * dpr)) c.height = Math.round(h * dpr);
    const ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const x0 = 36, x1 = w - 12, yb = h - 18, yt = 16;
    let ymax = 1;
    series.forEach(s => (s.data || []).forEach(v => { if (v > ymax) ymax = v; }));
    ymax = Math.ceil(ymax * 1.15) || 1;
    const n = (labels || []).length;
    const X = i => x0 + (x1 - x0) * (n <= 1 ? 0 : i / (n - 1));
    const Y = v => yb - (yb - yt) * (v / ymax);
    ctx.font = "10px ui-monospace, monospace";
    [0, ymax / 2, ymax].forEach(v => {
      const y = Y(v);
      ctx.strokeStyle = "#1b2740"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
      ctx.fillStyle = COL.dim; ctx.textAlign = "right"; ctx.textBaseline = "middle";
      ctx.fillText(Math.round(v), x0 - 5, y);
    });
    series.forEach(s => {
      const d = s.data || [];
      if (!d.length) return;
      ctx.strokeStyle = s.color; ctx.lineWidth = 2;
      ctx.setLineDash(s.dashed ? [5, 4] : []);
      ctx.beginPath();
      d.forEach((v, i) => { const x = X(i), y = Y(v); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
      ctx.stroke(); ctx.setLineDash([]);
    });
    let lx = x1 - 118;
    [["RL", COL.rl, false], ["Fixed", COL.ft, true]].forEach(([name, color, dash]) => {
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.setLineDash(dash ? [4, 3] : []);
      ctx.beginPath(); ctx.moveTo(lx, yt); ctx.lineTo(lx + 16, yt); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = COL.ink; ctx.textAlign = "left"; ctx.textBaseline = "middle";
      ctx.fillText(name, lx + 20, yt); lx += 20 + ctx.measureText(name).width + 16;
    });
  }

  function updateCharts(h) {
    if (!h) return;
    drawLineChart("chartWait", h.t, [
      { data: h.rl_wait, color: COL.rl }, { data: h.ft_wait, color: COL.ft, dashed: true }]);
    drawLineChart("chartQueue", h.t, [
      { data: h.rl_queue, color: COL.rl }, { data: h.ft_queue, color: COL.ft, dashed: true }]);
  }

  // ---------- top-level render (each field guarded so one bad value can't freeze the UI) ----------
  function render(state) {
    if (!state) return;
    try {
      // networks are drawn by the requestAnimationFrame loop (smooth motion)
      const k = state.kpi || {};
      setText("waitImpr", fmtSigned(k.wait_improvement));
      setText("queueImpr", fmtSigned(k.queue_improvement));
      setText("thruRl", k.throughput_rl ?? 0);
      setText("thruFt", k.throughput_fixed ?? 0);
      const gw = document.getElementById("greenWave");
      if (gw) { gw.textContent = k.green_wave ? "ON" : "OFF"; gw.className = "badge " + (k.green_wave ? "on" : "off"); }
      if (state.rl && state.rl.metrics) {
        setText("rlWait", state.rl.metrics.avg_waiting_time);
        setText("rlQueue", state.rl.metrics.avg_queue);
      }
      if (state.fixed && state.fixed.metrics) {
        setText("ftWait", state.fixed.metrics.avg_waiting_time);
        setText("ftQueue", state.fixed.metrics.avg_queue);
      }
      setText("elapsed", Math.round(state.elapsed || 0));
      setText("epLen", state.episode_seconds);
      setText("gridLabel", (state.meta && state.meta.grid) || "2×2");
      setText("nnBackend", (state.meta && state.meta.rl_backend) || "–");
      setText("ckpt", state.meta && state.meta.checkpoint ? "loaded" : "untrained");
      const btn = document.getElementById("playPause");
      if (btn) btn.textContent = state.playing ? "Pause" : "Play";
      const _ints = (state.rl && state.rl.intersections) || {};
      const ambActive = !!(state.rl && (((state.rl.moving||[]).some(v=>v.e)) ||
        ((state.rl.preempted||[]).length) ||
        Object.values(_ints).some(it => it.emergency && Object.values(it.emergency).some(Boolean))));
      const _banner = document.getElementById("ambBanner");
      if (_banner) _banner.hidden = !ambActive;
      updateCharts(state.history);
    } catch (e) { console.error("render error:", e); }
  }

  function fmtSigned(v) {
    if (v === undefined || v === null || isNaN(v)) return "–";
    return (v > 0 ? "+" : "") + v.toFixed(0);
  }
  function setText(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }

  // ---------- controls ----------
  function wireControls() {
    const on = (id, ev, fn) => { const el = document.getElementById(id); if (el) el[ev] = fn; };
    on("playPause", "onclick", () => send({ action: (latest && latest.playing) ? "pause" : "play" }));
    on("stepBtn", "onclick", () => send({ action: "step" }));
    on("resetBtn", "onclick", () => send({ action: "reset" }));
    on("emergencyBtn", "onclick", () => send({ action: "emergency", corridor: "ew" }));
    on("scenario", "onchange", (e) => send({ action: "scenario", value: e.target.value }));
    on("speed", "oninput", (e) => {
      const lbl = document.getElementById("speedLabel");
      if (lbl) lbl.textContent = e.target.value + "×";
      send({ action: "speed", value: Number(e.target.value) });
    });
  }

  // Startup: nothing here can throw hard enough to stop the live data loop.
  window.addEventListener("DOMContentLoaded", () => {
    try { wireControls(); } catch (e) { console.error(e); }
    connect();
    refreshStatus();
    setInterval(refreshStatus, 500);
    requestAnimationFrame(animate);
  });
})();
