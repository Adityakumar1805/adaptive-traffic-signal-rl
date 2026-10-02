// Vehicle sprites: every kind in the catalogue, drawn top-down with plain canvas paths.
//
// Each painter draws one vehicle facing +x (front at x = +L/2), centred on (0, 0), into a
// box L px long and W px wide. Sprites are painted once per (kind, colour variant, size,
// pixel ratio) into an offscreen canvas and then only blitted, so drawing hundreds of
// vehicles per frame costs one drawImage each. All artwork is original.
//
// Kinds come from the server (`hello.kinds`); a kind without a painter falls back to the
// generic car body, so a new catalogue entry can never break the page.

const TAU = Math.PI * 2;

// ---------------------------------------------------------------- helpers
function rr(ctx, x, y, w, h, r) {
  const k = Math.max(0, Math.min(r, Math.abs(w) / 2, Math.abs(h) / 2));
  ctx.beginPath();
  ctx.moveTo(x + k, y);
  ctx.lineTo(x + w - k, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + k);
  ctx.lineTo(x + w, y + h - k);
  ctx.quadraticCurveTo(x + w, y + h, x + w - k, y + h);
  ctx.lineTo(x + k, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - k);
  ctx.lineTo(x, y + k);
  ctx.quadraticCurveTo(x, y, x + k, y);
  ctx.closePath();
}
function fillRR(ctx, x, y, w, h, r, color) { rr(ctx, x, y, w, h, r); ctx.fillStyle = color; ctx.fill(); }
function ellipse(ctx, cx, cy, rx, ry, color) {
  ctx.beginPath(); ctx.ellipse(cx, cy, Math.max(0.1, rx), Math.max(0.1, ry), 0, 0, TAU);
  ctx.fillStyle = color; ctx.fill();
}
function line(ctx, x1, y1, x2, y2, color, width) {
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
  ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineCap = "round"; ctx.stroke();
}
function hex(c) {
  const v = c.replace("#", "");
  return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
}
/** Lighten (amt > 0) or darken (amt < 0) a #rrggbb colour. */
export function shade(color, amt) {
  const [r, g, b] = hex(color);
  const f = (x) => Math.round(amt >= 0 ? x + (255 - x) * amt : x * (1 + amt));
  return `rgb(${f(r)},${f(g)},${f(b)})`;
}
/** Body with a soft highlight along the roof line, so flat colours read as a solid. */
function body(ctx, x, y, w, h, r, color) {
  const g = ctx.createLinearGradient(0, y, 0, y + h);
  g.addColorStop(0, shade(color, -0.22));
  g.addColorStop(0.5, shade(color, 0.12));
  g.addColorStop(1, shade(color, -0.22));
  rr(ctx, x, y, w, h, r);
  ctx.fillStyle = g; ctx.fill();
  ctx.lineWidth = Math.max(0.6, h * 0.04);
  ctx.strokeStyle = "rgba(0,0,0,.45)"; ctx.stroke();
}
const GLASS = "#18222f";
const GLASS_HI = "rgba(160,200,255,.28)";
function glass(ctx, x, y, w, h, r) {
  fillRR(ctx, x, y, w, h, r, GLASS);
  fillRR(ctx, x + w * 0.15, y + h * 0.12, w * 0.3, h * 0.76, r * 0.5, GLASS_HI);
}
function lamps(ctx, L, W, front = true, rear = true, inset = 0.06) {
  const r = Math.max(0.5, W * 0.07);
  if (front) {
    ellipse(ctx, L / 2 - r * 0.9, -W / 2 + W * (0.12 + inset), r * 0.7, r, "#fff4c2");
    ellipse(ctx, L / 2 - r * 0.9, W / 2 - W * (0.12 + inset), r * 0.7, r, "#fff4c2");
  }
  if (rear) {
    ellipse(ctx, -L / 2 + r * 0.8, -W / 2 + W * (0.12 + inset), r * 0.6, r * 0.9, "#ff3b3b");
    ellipse(ctx, -L / 2 + r * 0.8, W / 2 - W * (0.12 + inset), r * 0.6, r * 0.9, "#ff3b3b");
  }
}
function mirrors(ctx, x, W, color) {
  const s = Math.max(0.6, W * 0.09);
  fillRR(ctx, x - s, -W / 2 - s * 0.9, s * 1.6, s, s * 0.4, color);
  fillRR(ctx, x - s, W / 2 - s * 0.1, s * 1.6, s, s * 0.4, color);
}
function rider(ctx, x, W, jacket, helmet, bareHead = false) {
  ellipse(ctx, x - W * 0.08, 0, W * 0.24, W * 0.44, jacket);             // shoulders
  ellipse(ctx, x + W * 0.16, 0, W * 0.25, W * 0.25, bareHead ? "#2b1d14" : helmet);
  if (!bareHead) ellipse(ctx, x + W * 0.28, 0, W * 0.07, W * 0.16, "rgba(255,255,255,.55)");
}
function wheel(ctx, cx, cy, len, wid) { fillRR(ctx, cx - len / 2, cy - wid / 2, len, wid, wid * 0.4, "#0d0f12"); }

// ---------------------------------------------------------------- palettes (4 variants)
const PALETTE = {
  bike: [["#c81d25", "#1f2937", "#e11d48"], ["#16181d", "#334155", "#f8fafc"],
         ["#1d4ed8", "#7c2d12", "#111827"], ["#6b7280", "#0f766e", "#facc15"]],
  scooter: [["#f1f5f9", "#be123c", "#111827"], ["#a3acb9", "#1e3a8a", "#f8fafc"],
            ["#7f1d1d", "#ca8a04", "#1f2937"], ["#2563eb", "#4b5563", "#e5e7eb"]],
  bicycle: [["#1f2937", "#f8fafc"], ["#b91c1c", "#1d4ed8"], ["#065f46", "#f59e0b"],
            ["#1e3a8a", "#e5e7eb"]],
  cycle_rickshaw: ["#1d4ed8", "#b91c1c", "#15803d", "#92400e"],
  auto: [["#15803d", "#facc15"], ["#166534", "#fde047"], ["#111111", "#facc15"],
         ["#16a34a", "#eab308"]],
  erick: ["#e5e7eb", "#2563eb", "#16a34a", "#0e7490"],
  car: ["#f3f4f6", "#b8c0cc", "#b91c1c", "#1e3a8a"],
  taxi: [["#121212", "#f5c400"], ["#121212", "#f5c400"], ["#f5c400", "#f5c400"],
         ["#f8fafc", "#f5c400"]],
  suv: ["#f8fafc", "#141821", "#6b7280", "#7c2d12"],
  van: ["#f8fafc", "#cbd5e1", "#1e40af", "#b91c1c"],
  tempo: [["#f8fafc", "load"], ["#2563eb", "veg"], ["#f59e0b", "load"], ["#16a34a", "crates"]],
  truck: [["#f97316", "#1d4ed8"], ["#dc2626", "#15803d"], ["#eab308", "#b45309"],
          ["#2563eb", "#475569"]],
  tractor: [["#d61f26", "#7c4a1e"], ["#1e5aa8", "#7c4a1e"], ["#2e7d32", "#a16207"],
            ["#e65100", "#57534e"]],
  bus: ["#c62828", "#2e7d32", "#ef6c00", "#1565c0"],
  school_bus: ["#f9b300", "#f9b300", "#f5a300", "#fbbf24"],
};
const pick = (list, v) => list[((v % list.length) + list.length) % list.length];

// ---------------------------------------------------------------- painters
function paintCar(ctx, L, W, color, opt = {}) {
  const roofColor = opt.roof || shade(color, 0.1);
  body(ctx, -L / 2, -W / 2, L, W, W * 0.32, color);
  // bonnet crease + boot
  line(ctx, L * 0.3, -W * 0.22, L * 0.45, -W * 0.18, "rgba(0,0,0,.18)", Math.max(0.5, W * 0.03));
  line(ctx, L * 0.3, W * 0.22, L * 0.45, W * 0.18, "rgba(0,0,0,.18)", Math.max(0.5, W * 0.03));
  // side windows (slivers either side of the roof)
  fillRR(ctx, -L * 0.25, -W * 0.43, L * 0.42, W * 0.1, W * 0.04, GLASS);
  fillRR(ctx, -L * 0.25, W * 0.33, L * 0.42, W * 0.1, W * 0.04, GLASS);
  glass(ctx, L * 0.12, -W * 0.38, L * 0.15, W * 0.76, W * 0.12);               // windscreen
  glass(ctx, -L * 0.36, -W * 0.34, L * 0.11, W * 0.68, W * 0.1);               // rear window
  fillRR(ctx, -L * 0.25, -W * 0.34, L * 0.38, W * 0.68, W * 0.12, roofColor);  // roof
  if (opt.sign) fillRR(ctx, -L * 0.07, -W * 0.15, L * 0.1, W * 0.3, W * 0.05, "#fff7cc");
  mirrors(ctx, L * 0.14, W, shade(color, -0.25));
  lamps(ctx, L, W);
}

function paintSuv(ctx, L, W, color, opt = {}) {
  body(ctx, -L / 2, -W / 2, L, W, W * 0.2, color);
  fillRR(ctx, -L * 0.36, -W * 0.44, L * 0.52, W * 0.1, W * 0.03, GLASS);
  fillRR(ctx, -L * 0.36, W * 0.34, L * 0.52, W * 0.1, W * 0.03, GLASS);
  glass(ctx, L * 0.16, -W * 0.39, L * 0.13, W * 0.78, W * 0.1);
  glass(ctx, -L * 0.45, -W * 0.36, L * 0.07, W * 0.72, W * 0.08);
  fillRR(ctx, -L * 0.37, -W * 0.36, L * 0.53, W * 0.72, W * 0.08, opt.roof || shade(color, 0.08));
  line(ctx, -L * 0.33, -W * 0.3, L * 0.12, -W * 0.3, "rgba(0,0,0,.35)", Math.max(0.6, W * 0.05));
  line(ctx, -L * 0.33, W * 0.3, L * 0.12, W * 0.3, "rgba(0,0,0,.35)", Math.max(0.6, W * 0.05));
  if (opt.band) {                                           // police livery
    fillRR(ctx, -L * 0.5, -W * 0.5, L, W * 0.1, W * 0.04, opt.band);
    fillRR(ctx, -L * 0.5, W * 0.4, L, W * 0.1, W * 0.04, opt.band);
    fillRR(ctx, L * 0.3, -W * 0.16, L * 0.12, W * 0.32, W * 0.05, opt.band);
  }
  if (opt.spare !== false) ellipse(ctx, -L * 0.5 + W * 0.05, 0, W * 0.07, W * 0.22, "#0d0f12");
  mirrors(ctx, L * 0.17, W, shade(color, -0.3));
  lamps(ctx, L, W);
}

function paintVan(ctx, L, W, color, opt = {}) {
  body(ctx, -L / 2, -W / 2, L, W, W * 0.24, color);
  glass(ctx, L * 0.28, -W * 0.39, L * 0.12, W * 0.78, W * 0.1);
  fillRR(ctx, -L * 0.44, -W * 0.44, L * 0.7, W * 0.09, W * 0.03, GLASS);
  fillRR(ctx, -L * 0.44, W * 0.35, L * 0.7, W * 0.09, W * 0.03, GLASS);
  fillRR(ctx, -L * 0.45, -W * 0.36, L * 0.72, W * 0.72, W * 0.1, opt.roof || shade(color, 0.08));
  line(ctx, -L * 0.05, -W * 0.36, -L * 0.05, W * 0.36, "rgba(0,0,0,.12)", Math.max(0.5, W * 0.025));
  if (opt.stripe) {
    fillRR(ctx, -L * 0.5, -W * 0.5, L, W * 0.09, W * 0.03, opt.stripe);
    fillRR(ctx, -L * 0.5, W * 0.41, L, W * 0.09, W * 0.03, opt.stripe);
  }
  if (opt.cross) {
    const c = W * 0.34, t = W * 0.11;
    fillRR(ctx, -L * 0.12 - c / 2, -t / 2, c, t, t * 0.2, opt.cross);
    fillRR(ctx, -L * 0.12 - t / 2, -c / 2, t, c, t * 0.2, opt.cross);
  }
  mirrors(ctx, L * 0.3, W, shade(color, -0.3));
  lamps(ctx, L, W);
}

function paintBike(ctx, L, W, pal, variant) {
  const [paint, jacket, helmet] = pal;
  wheel(ctx, L * 0.36, 0, L * 0.26, W * 0.26);
  wheel(ctx, -L * 0.36, 0, L * 0.26, W * 0.3);
  fillRR(ctx, -L * 0.3, -W * 0.2, L * 0.55, W * 0.4, W * 0.18, paint);         // tank + frame
  fillRR(ctx, -L * 0.34, -W * 0.15, L * 0.3, W * 0.3, W * 0.13, "#111");       // seat
  line(ctx, L * 0.22, -W * 0.48, L * 0.22, W * 0.48, "#3a3f47", Math.max(0.7, W * 0.1));
  ellipse(ctx, L * 0.46, 0, W * 0.09, W * 0.13, "#fff4c2");
  if (variant === 3) rider(ctx, -L * 0.28, W * 0.92, shade(jacket, 0.35), "#f8fafc", true);  // pillion
  rider(ctx, -L * 0.04, W, jacket, helmet);
}

function paintScooter(ctx, L, W, pal, variant) {
  const [paint, jacket, helmet] = pal;
  wheel(ctx, L * 0.36, 0, L * 0.2, W * 0.24);
  wheel(ctx, -L * 0.38, 0, L * 0.2, W * 0.28);
  fillRR(ctx, -L * 0.48, -W * 0.36, L * 0.5, W * 0.72, W * 0.32, paint);       // rear body
  fillRR(ctx, -L * 0.06, -W * 0.2, L * 0.24, W * 0.4, W * 0.08, "#2a2f36");    // floorboard
  fillRR(ctx, L * 0.16, -W * 0.4, L * 0.2, W * 0.8, W * 0.34, paint);          // front apron
  line(ctx, L * 0.3, -W * 0.46, L * 0.3, W * 0.46, "#3a3f47", Math.max(0.6, W * 0.09));
  ellipse(ctx, L * 0.4, 0, W * 0.1, W * 0.14, "#fff4c2");
  ellipse(ctx, -L * 0.48, 0, W * 0.06, W * 0.14, "#ff3b3b");
  if (variant === 2) rider(ctx, -L * 0.3, W * 0.9, "#db2777", "#2b1d14", true);   // pillion
  rider(ctx, -L * 0.1, W, jacket, helmet);
}

function paintBicycle(ctx, L, W, pal) {
  const [frame, shirt] = pal;
  const lw = Math.max(0.6, W * 0.1);
  ctx.lineWidth = lw; ctx.strokeStyle = "#0d0f12";
  ctx.beginPath(); ctx.ellipse(L * 0.32, 0, L * 0.16, W * 0.1, 0, 0, TAU); ctx.stroke();
  ctx.beginPath(); ctx.ellipse(-L * 0.32, 0, L * 0.16, W * 0.1, 0, 0, TAU); ctx.stroke();
  line(ctx, -L * 0.32, 0, L * 0.3, 0, frame, Math.max(0.8, W * 0.14));
  line(ctx, L * 0.22, -W * 0.46, L * 0.22, W * 0.46, "#3a3f47", lw);
  rider(ctx, -L * 0.04, W * 1.05, shirt, "#000", true);
}

function paintCycleRickshaw(ctx, L, W, canopy) {
  wheel(ctx, -L * 0.24, -W * 0.46, L * 0.22, W * 0.1);
  wheel(ctx, -L * 0.24, W * 0.46, L * 0.22, W * 0.1);
  fillRR(ctx, -L * 0.42, -W * 0.42, L * 0.36, W * 0.84, W * 0.16, "#6b4f2a");   // seat box
  // folded hood with ribs
  fillRR(ctx, -L * 0.5, -W * 0.46, L * 0.3, W * 0.92, W * 0.3, canopy);
  for (let i = 1; i <= 3; i++) {
    const x = -L * 0.5 + (L * 0.3 * i) / 4;
    line(ctx, x, -W * 0.4, x, W * 0.4, shade(canopy, -0.35), Math.max(0.5, W * 0.04));
  }
  line(ctx, -L * 0.06, 0, L * 0.4, 0, "#2f3640", Math.max(0.7, W * 0.07));       // frame
  wheel(ctx, L * 0.4, 0, L * 0.16, W * 0.08);
  line(ctx, L * 0.32, -W * 0.3, L * 0.32, W * 0.3, "#3a3f47", Math.max(0.6, W * 0.06));
  rider(ctx, L * 0.16, W * 0.72, "#e5e7eb", "#000", true);
}

function paintAuto(ctx, L, W, pal) {
  const [bodyColor, roof] = pal;
  // cabin + tapering nose
  ctx.beginPath();
  ctx.moveTo(-L * 0.46, -W * 0.5);
  ctx.lineTo(L * 0.12, -W * 0.5);
  ctx.quadraticCurveTo(L * 0.42, -W * 0.42, L * 0.5, -W * 0.16);
  ctx.lineTo(L * 0.5, W * 0.16);
  ctx.quadraticCurveTo(L * 0.42, W * 0.42, L * 0.12, W * 0.5);
  ctx.lineTo(-L * 0.46, W * 0.5);
  ctx.quadraticCurveTo(-L * 0.5, W * 0.5, -L * 0.5, W * 0.4);
  ctx.lineTo(-L * 0.5, -W * 0.4);
  ctx.quadraticCurveTo(-L * 0.5, -W * 0.5, -L * 0.46, -W * 0.5);
  ctx.closePath();
  ctx.fillStyle = bodyColor; ctx.fill();
  ctx.lineWidth = Math.max(0.6, W * 0.04); ctx.strokeStyle = "rgba(0,0,0,.45)"; ctx.stroke();
  glass(ctx, L * 0.14, -W * 0.32, L * 0.1, W * 0.64, W * 0.08);                 // windscreen
  // canvas roof with ribs
  const g = ctx.createLinearGradient(0, -W * 0.46, 0, W * 0.46);
  g.addColorStop(0, shade(roof, -0.18)); g.addColorStop(0.5, shade(roof, 0.12)); g.addColorStop(1, shade(roof, -0.18));
  rr(ctx, -L * 0.48, -W * 0.46, L * 0.62, W * 0.92, W * 0.2); ctx.fillStyle = g; ctx.fill();
  for (let i = 1; i <= 3; i++) {
    const x = -L * 0.48 + (L * 0.62 * i) / 4;
    line(ctx, x, -W * 0.42, x, W * 0.42, shade(roof, -0.3), Math.max(0.5, W * 0.035));
  }
  ellipse(ctx, L * 0.47, 0, W * 0.06, W * 0.1, "#fff4c2");
  ellipse(ctx, -L * 0.49, -W * 0.34, W * 0.04, W * 0.06, "#ff3b3b");
  ellipse(ctx, -L * 0.49, W * 0.34, W * 0.04, W * 0.06, "#ff3b3b");
}

function paintErick(ctx, L, W, roof) {
  fillRR(ctx, -L * 0.5, -W * 0.5, L * 0.86, W, W * 0.14, "#3f4652");           // open chassis
  fillRR(ctx, -L * 0.4, -W * 0.36, L * 0.18, W * 0.72, W * 0.08, "#7a6a55");    // rear bench
  fillRR(ctx, -L * 0.12, -W * 0.36, L * 0.16, W * 0.72, W * 0.08, "#7a6a55");   // front bench
  fillRR(ctx, L * 0.26, -W * 0.22, L * 0.24, W * 0.44, W * 0.14, shade(roof, -0.1));  // nose
  body(ctx, -L * 0.48, -W * 0.48, L * 0.76, W * 0.96, W * 0.12, roof);          // flat roof
  glass(ctx, L * 0.28, -W * 0.36, L * 0.09, W * 0.72, W * 0.06);
  line(ctx, -L * 0.44, 0, L * 0.24, 0, shade(roof, -0.15), Math.max(0.5, W * 0.03));
  ellipse(ctx, L * 0.48, 0, W * 0.07, W * 0.1, "#fff4c2");
  ellipse(ctx, -L * 0.49, -W * 0.36, W * 0.04, W * 0.06, "#ff3b3b");
  ellipse(ctx, -L * 0.49, W * 0.36, W * 0.04, W * 0.06, "#ff3b3b");
}

function cargoLoad(ctx, x, y, w, h, style) {
  if (style === "veg") {
    const colors = ["#16a34a", "#dc2626", "#f59e0b", "#65a30d"];
    for (let i = 0; i < 8; i++) {
      const cx = x + w * (0.12 + 0.25 * (i % 4)), cy = y + h * (i < 4 ? 0.3 : 0.7);
      ellipse(ctx, cx, cy, w * 0.1, h * 0.2, colors[i % 4]);
    }
  } else if (style === "crates") {
    for (let i = 0; i < 6; i++) {
      const cx = x + w * (0.04 + 0.32 * (i % 3)), cy = y + h * (i < 3 ? 0.06 : 0.53);
      fillRR(ctx, cx, cy, w * 0.28, h * 0.41, h * 0.05, i % 2 ? "#b45309" : "#92400e");
      line(ctx, cx, cy + h * 0.2, cx + w * 0.28, cy + h * 0.2, "rgba(0,0,0,.3)", Math.max(0.4, h * 0.03));
    }
  } else {                                                   // gunny sacks
    for (let i = 0; i < 6; i++) {
      const cx = x + w * (0.18 + 0.32 * (i % 3)), cy = y + h * (i < 3 ? 0.3 : 0.72);
      ellipse(ctx, cx, cy, w * 0.15, h * 0.22, i % 2 ? "#d6c39a" : "#c8b27f");
    }
  }
}

function paintTempo(ctx, L, W, pal) {
  const [cab, load] = pal;
  fillRR(ctx, -L * 0.5, -W * 0.5, L * 0.66, W, W * 0.06, "#8a6b3f");            // cargo bed
  fillRR(ctx, -L * 0.47, -W * 0.42, L * 0.6, W * 0.84, W * 0.04, "#5b4a33");
  cargoLoad(ctx, -L * 0.46, -W * 0.4, L * 0.58, W * 0.8, load);
  body(ctx, L * 0.17, -W * 0.46, L * 0.33, W * 0.92, W * 0.16, cab);           // cab
  glass(ctx, L * 0.3, -W * 0.36, L * 0.11, W * 0.72, W * 0.08);
  fillRR(ctx, L * 0.18, -W * 0.36, L * 0.12, W * 0.72, W * 0.08, shade(cab, 0.12));
  mirrors(ctx, L * 0.36, W * 0.92, "#2a2f36");
  lamps(ctx, L, W, true, false);
}

function paintTruck(ctx, L, W, pal) {
  const [cab, tarp] = pal;
  fillRR(ctx, -L * 0.5, -W * 0.5, L * 0.77, W, W * 0.05, "#5b4630");            // body
  const g = ctx.createLinearGradient(0, -W * 0.46, 0, W * 0.46);
  g.addColorStop(0, shade(tarp, -0.25)); g.addColorStop(0.5, shade(tarp, 0.1)); g.addColorStop(1, shade(tarp, -0.25));
  rr(ctx, -L * 0.49, -W * 0.46, L * 0.74, W * 0.92, W * 0.08); ctx.fillStyle = g; ctx.fill();
  for (let i = 1; i < 8; i++) {                                                  // tarpaulin ridges
    const x = -L * 0.49 + (L * 0.74 * i) / 8;
    line(ctx, x, -W * 0.44, x, W * 0.44, shade(tarp, -0.35), Math.max(0.5, W * 0.025));
  }
  line(ctx, -L * 0.47, -W * 0.4, L * 0.2, W * 0.4, "rgba(240,220,170,.45)", Math.max(0.4, W * 0.02));
  line(ctx, -L * 0.47, W * 0.4, L * 0.2, -W * 0.4, "rgba(240,220,170,.45)", Math.max(0.4, W * 0.02));
  body(ctx, L * 0.28, -W * 0.48, L * 0.22, W * 0.96, W * 0.12, cab);           // cab
  fillRR(ctx, L * 0.29, -W * 0.4, L * 0.07, W * 0.8, W * 0.05, "#facc15");      // painted crown
  fillRR(ctx, L * 0.31, -W * 0.3, L * 0.03, W * 0.6, W * 0.03, "#dc2626");
  glass(ctx, L * 0.39, -W * 0.4, L * 0.07, W * 0.8, W * 0.06);
  mirrors(ctx, L * 0.42, W, "#1f2937");
  lamps(ctx, L, W, true, true, 0.02);
}

function paintTractor(ctx, L, W, pal) {
  const [paint, trolley] = pal;
  // trolley
  wheel(ctx, -L * 0.24, -W * 0.5, L * 0.12, W * 0.1);
  wheel(ctx, -L * 0.24, W * 0.5, L * 0.12, W * 0.1);
  fillRR(ctx, -L * 0.5, -W * 0.48, L * 0.5, W * 0.96, W * 0.06, shade(trolley, -0.2));
  fillRR(ctx, -L * 0.47, -W * 0.41, L * 0.44, W * 0.82, W * 0.05, "#c9a96e");   // hay / sand
  for (let i = 0; i < 9; i++) {
    const x = -L * 0.45 + (L * 0.4 * i) / 8;
    line(ctx, x, -W * 0.36, x + L * 0.02, W * 0.36, "rgba(120,90,40,.45)", Math.max(0.4, W * 0.02));
  }
  line(ctx, -L * 0.01, 0, L * 0.08, 0, "#2a2f36", Math.max(0.8, W * 0.08));      // hitch
  // tractor: big rear wheels, narrow bonnet, small front wheels
  fillRR(ctx, L * 0.06, -W * 0.5, L * 0.17, W * 0.3, W * 0.08, "#101215");
  fillRR(ctx, L * 0.06, W * 0.2, L * 0.17, W * 0.3, W * 0.08, "#101215");
  for (let i = 0; i < 4; i++) {
    const x = L * 0.075 + (L * 0.15 * i) / 3;
    line(ctx, x, -W * 0.48, x, -W * 0.22, "#2b2f36", Math.max(0.4, W * 0.02));
    line(ctx, x, W * 0.22, x, W * 0.48, "#2b2f36", Math.max(0.4, W * 0.02));
  }
  fillRR(ctx, L * 0.4, -W * 0.36, L * 0.08, W * 0.14, W * 0.05, "#101215");
  fillRR(ctx, L * 0.4, W * 0.22, L * 0.08, W * 0.14, W * 0.05, "#101215");
  fillRR(ctx, L * 0.08, -W * 0.2, L * 0.16, W * 0.4, W * 0.06, "#2a2f36");       // seat / fender
  body(ctx, L * 0.22, -W * 0.17, L * 0.28, W * 0.34, W * 0.1, paint);           // bonnet
  ellipse(ctx, L * 0.3, -W * 0.08, W * 0.04, W * 0.04, "#0b0b0b");              // exhaust
  ellipse(ctx, L * 0.14, 0, W * 0.11, W * 0.11, "#e7d3b0");                     // driver
  ellipse(ctx, L * 0.49, -W * 0.1, W * 0.04, W * 0.05, "#fff4c2");
  ellipse(ctx, L * 0.49, W * 0.1, W * 0.04, W * 0.05, "#fff4c2");
}

function paintBus(ctx, L, W, color, opt = {}) {
  body(ctx, -L / 2, -W / 2, L, W, W * 0.14, color);
  // side window bands with pillars
  const top = -W * 0.47, bot = W * 0.37, band = W * 0.1;
  fillRR(ctx, -L * 0.44, top, L * 0.84, band, band * 0.3, GLASS);
  fillRR(ctx, -L * 0.44, bot, L * 0.84, band, band * 0.3, GLASS);
  for (let i = 1; i < 9; i++) {
    const x = -L * 0.44 + (L * 0.84 * i) / 9;
    line(ctx, x, top, x, top + band, color, Math.max(0.6, L * 0.008));
    line(ctx, x, bot, x, bot + band, color, Math.max(0.6, L * 0.008));
  }
  if (opt.band) {
    fillRR(ctx, -L * 0.5, -W * 0.36, L, W * 0.07, 0, opt.band);
    fillRR(ctx, -L * 0.5, W * 0.29, L, W * 0.07, 0, opt.band);
  }
  glass(ctx, L * 0.43, -W * 0.42, L * 0.05, W * 0.84, W * 0.08);               // windscreen
  fillRR(ctx, L * 0.4, -W * 0.25, L * 0.025, W * 0.5, W * 0.03, "#ffb020");     // route board
  fillRR(ctx, -L * 0.49, -W * 0.34, L * 0.025, W * 0.68, W * 0.04, GLASS);
  fillRR(ctx, -L * 0.12, -W * 0.22, L * 0.2, W * 0.44, W * 0.06, "#cfd6df");    // AC unit
  fillRR(ctx, -L * 0.34, -W * 0.14, L * 0.08, W * 0.28, W * 0.04, shade(color, 0.25));  // hatches
  fillRR(ctx, L * 0.2, -W * 0.14, L * 0.08, W * 0.28, W * 0.04, shade(color, 0.25));
  lamps(ctx, L, W, true, true, 0.0);
}

function paintFireEngine(ctx, L, W) {
  const red = "#c62828";
  body(ctx, -L / 2, -W / 2, L, W, W * 0.1, red);
  fillRR(ctx, -L * 0.5, -W * 0.5, L * 0.75, W * 0.08, 0, "#f8fafc");            // white flash
  fillRR(ctx, -L * 0.5, W * 0.42, L * 0.75, W * 0.08, 0, "#f8fafc");
  // ladder
  const rail = Math.max(0.6, W * 0.05);
  line(ctx, -L * 0.45, -W * 0.2, L * 0.22, -W * 0.2, "#d7dde5", rail);
  line(ctx, -L * 0.45, W * 0.2, L * 0.22, W * 0.2, "#d7dde5", rail);
  for (let i = 0; i <= 14; i++) {
    const x = -L * 0.45 + (L * 0.67 * i) / 14;
    line(ctx, x, -W * 0.2, x, W * 0.2, "#b8c0cc", Math.max(0.4, W * 0.025));
  }
  ellipse(ctx, -L * 0.36, -W * 0.33, W * 0.1, W * 0.1, "#9ca3af");             // hose reels
  ellipse(ctx, -L * 0.36, W * 0.33, W * 0.1, W * 0.1, "#9ca3af");
  body(ctx, L * 0.26, -W * 0.48, L * 0.24, W * 0.96, W * 0.12, red);           // cab
  glass(ctx, L * 0.4, -W * 0.4, L * 0.07, W * 0.8, W * 0.06);
  fillRR(ctx, L * 0.29, -W * 0.3, L * 0.07, W * 0.6, W * 0.04, "#1f2937");      // light-bar base
  mirrors(ctx, L * 0.43, W, "#1f2937");
  lamps(ctx, L, W, true, true, 0.02);
}

const PAINTERS = {
  bike: (c, L, W, v) => paintBike(c, L, W, pick(PALETTE.bike, v), v),
  scooter: (c, L, W, v) => paintScooter(c, L, W, pick(PALETTE.scooter, v), v),
  bicycle: (c, L, W, v) => paintBicycle(c, L, W, pick(PALETTE.bicycle, v)),
  cycle_rickshaw: (c, L, W, v) => paintCycleRickshaw(c, L, W, pick(PALETTE.cycle_rickshaw, v)),
  auto: (c, L, W, v) => paintAuto(c, L, W, pick(PALETTE.auto, v)),
  erick: (c, L, W, v) => paintErick(c, L, W, pick(PALETTE.erick, v)),
  car: (c, L, W, v) => paintCar(c, L, W, pick(PALETTE.car, v)),
  taxi: (c, L, W, v) => {
    const [b, roof] = pick(PALETTE.taxi, v);
    paintCar(c, L, W, b, { roof, sign: v !== 2 });
  },
  suv: (c, L, W, v) => paintSuv(c, L, W, pick(PALETTE.suv, v)),
  van: (c, L, W, v) => paintVan(c, L, W, pick(PALETTE.van, v)),
  tempo: (c, L, W, v) => paintTempo(c, L, W, pick(PALETTE.tempo, v)),
  truck: (c, L, W, v) => paintTruck(c, L, W, pick(PALETTE.truck, v)),
  tractor: (c, L, W, v) => paintTractor(c, L, W, pick(PALETTE.tractor, v)),
  bus: (c, L, W, v) => paintBus(c, L, W, pick(PALETTE.bus, v)),
  school_bus: (c, L, W, v) => paintBus(c, L, W, pick(PALETTE.school_bus, v), { band: "#5b3a1a" }),
  ambulance: (c, L, W) => paintVan(c, L, W, "#f8fafc", { stripe: "#d61f26", cross: "#d61f26", roof: "#ffffff" }),
  police: (c, L, W) => paintSuv(c, L, W, "#f8fafc", { band: "#1d4ed8", roof: "#ffffff", spare: false }),
  fire: (c, L, W) => paintFireEngine(c, L, W),
};

/** Kinds with a dedicated painter (the test suite checks this covers the whole catalogue). */
export const PAINTED_KINDS = Object.freeze(Object.keys(PAINTERS));

// Roof light positions of the emergency kinds: [x, y] as fractions of L and W, and the two
// alternating colours. The lights are drawn live (they flash), on top of the cached body.
const BEACONS = {
  ambulance: { spots: [[0.16, -0.26, 0], [0.16, 0.26, 1], [-0.44, -0.3, 0], [-0.44, 0.3, 1]],
               colors: ["#ff2d3d", "#2d7bff"] },
  police: { spots: [[-0.02, -0.24, 0], [-0.02, 0.24, 1]], colors: ["#ff2d3d", "#2d7bff"] },
  fire: { spots: [[0.325, -0.26, 0], [0.325, 0.26, 1], [-0.46, -0.32, 1], [-0.46, 0.32, 0]],
          colors: ["#ff2d3d", "#ffd0d0"] },
};
export const BEACON_KINDS = Object.freeze(Object.keys(BEACONS));

/** Paint one vehicle into `ctx` (already translated / rotated) - used by the cache and the
 *  legend thumbnails. */
export function paintVehicle(ctx, kind, variant, L, W) {
  const painter = PAINTERS[kind] || PAINTERS.car;
  ctx.save();
  painter(ctx, L, W, variant | 0);
  ctx.restore();
}

// ---------------------------------------------------------------- the sprite cache
export class SpriteCache {
  constructor() {
    this.dpr = 1;
    this.ppm = 1;
    this.map = new Map();
    this.glow = new Map();
  }

  /** Change the scale (px per metre of vehicle body) or the pixel ratio; drops the cache. */
  configure(ppm, dpr) {
    const p = Math.round(ppm * 100) / 100;
    if (p === this.ppm && dpr === this.dpr) return;
    this.ppm = p;
    this.dpr = dpr;
    this.map.clear();
    this.glow.clear();
  }

  /** The offscreen canvas for one kind + colour variant at the current scale. */
  get(kind, variant, lengthM, widthM) {
    const key = kind + "|" + variant;
    let entry = this.map.get(key);
    if (entry) return entry;
    const L = Math.max(4, lengthM * this.ppm);
    const W = Math.max(2.5, widthM * this.ppm);
    const pad = Math.ceil(Math.max(2, W * 0.25));
    const canvas = makeCanvas(Math.ceil((L + 2 * pad) * this.dpr), Math.ceil((W + 2 * pad) * this.dpr));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(this.dpr, 0, 0, this.dpr, (L / 2 + pad) * this.dpr, (W / 2 + pad) * this.dpr);
    // soft contact shadow so vehicles sit on the road
    ctx.save();
    ctx.translate(W * 0.06, W * 0.08);
    rr(ctx, -L / 2, -W / 2, L, W, W * 0.3);
    ctx.fillStyle = "rgba(0,0,0,.35)"; ctx.fill();
    ctx.restore();
    paintVehicle(ctx, kind, variant, L, W);
    entry = { canvas, L, W, pad, w: L + 2 * pad, h: W + 2 * pad };
    this.map.set(key, entry);
    return entry;
  }

  /** A radial glow sprite (for flashing beacons), cached per colour and size. */
  glowSprite(color, radius) {
    const r = Math.max(2, Math.round(radius * 2) / 2);
    const key = color + "|" + r;
    let c = this.glow.get(key);
    if (c) return c;
    const size = Math.ceil(r * 2 * this.dpr);
    c = makeCanvas(size, size);
    const ctx = c.getContext("2d");
    const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    g.addColorStop(0, "rgba(255,255,255,1)");
    g.addColorStop(0.18, color);
    g.addColorStop(0.45, hexA(color, 0.45));
    g.addColorStop(1, hexA(color, 0));
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, size, size);
    c.r = r;
    this.glow.set(key, c);
    return c;
  }
}

function hexA(color, a) {
  const [r, g, b] = hex(color);
  return `rgba(${r},${g},${b},${a})`;
}

// OffscreenCanvas with a 2D context exists in every current browser, but not in Safari
// before 16.4 - fall back to a detached <canvas> there.
let offscreen2d = null;
function makeCanvas(w, h) {
  w = Math.max(1, w); h = Math.max(1, h);
  if (offscreen2d === null) {
    try { offscreen2d = typeof OffscreenCanvas !== "undefined" && !!new OffscreenCanvas(1, 1).getContext("2d"); }
    catch (e) { offscreen2d = false; }
  }
  if (offscreen2d) return new OffscreenCanvas(w, h);
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  return c;
}
export { makeCanvas };

/**
 * Draw a vehicle centred at (x, y) CSS px, heading `angle` (radians, 0 = +x), on a context
 * whose backing store is `dpr` x the CSS size. `flash` is a phase in [0, 1) for emergency
 * beacons, or null for steady lights (reduced motion). Leaves the transform modified: the
 * caller resets it once after a batch (cheaper than save/restore per vehicle).
 */
export function drawVehicle(ctx, cache, spec, variant, x, y, angle, flash, dpr) {
  const s = cache.get(spec.kind, variant, spec.len, spec.wid);
  const c = Math.cos(angle), n = Math.sin(angle);
  ctx.setTransform(dpr * c, dpr * n, -dpr * n, dpr * c, dpr * x, dpr * y);
  ctx.drawImage(s.canvas, -s.L / 2 - s.pad, -s.W / 2 - s.pad, s.w, s.h);
  const beacon = BEACONS[spec.kind];
  if (beacon) drawBeacons(ctx, cache, beacon, s.L, s.W, flash);
}

function drawBeacons(ctx, cache, beacon, L, W, flash) {
  const on = flash == null ? -1 : (flash < 0.5 ? 0 : 1);
  ctx.globalCompositeOperation = "lighter";
  for (const [fx, fy, which] of beacon.spots) {
    const lit = on === -1 || which === on;
    const color = beacon.colors[which];
    const glow = cache.glowSprite(color, Math.max(3, W * (lit ? 0.62 : 0.28)));
    const r = glow.r * (lit ? 1 : 0.6);
    ctx.globalAlpha = lit ? (on === -1 ? 0.7 : 1) : 0.35;
    ctx.drawImage(glow, fx * L - r, fy * W - r, r * 2, r * 2);
  }
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = "source-over";
}

/** Small standalone thumbnail (legend, inject buttons): fits the vehicle into w x h CSS px. */
export function thumbnail(canvas, spec, variant, w, h, dpr) {
  const scale = Math.min((w - 4) / spec.len, (h - 4) / spec.wid);
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  canvas.style.width = w + "px";
  canvas.style.height = h + "px";
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, (w / 2) * dpr, (h / 2) * dpr);
  paintVehicle(ctx, spec.kind, variant, spec.len * scale, spec.wid * scale);
  const beacon = BEACONS[spec.kind];
  if (beacon) {
    const cache = new SpriteCache();
    cache.configure(1, dpr);
    drawBeacons(ctx, cache, beacon, spec.len * scale, spec.wid * scale, null);
  }
}
