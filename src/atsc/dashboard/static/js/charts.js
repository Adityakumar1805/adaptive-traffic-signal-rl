// Small hand-drawn line charts (no library): RL vs fixed-time over the episode so far.

const RL = "#22d47b";
const FT = "#8b99b5";

function niceStep(range, ticks) {
  const raw = range / Math.max(1, ticks);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
}

function fmtTime(s) {
  if (s < 60) return `${Math.round(s)} s`;
  const m = s / 60;
  return Number.isInteger(m) ? `${m} min` : `${m.toFixed(1)} min`;
}

export class LineChart {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {{rl: number, ft: number, unit: string, label: string, digits: number}} spec
   *   indices of the RL / fixed values inside each history point [e, rlW, ftW, rlQ, ftQ]
   */
  constructor(canvas, spec) {
    this.canvas = canvas;
    this.spec = spec;
    this.ctx = canvas.getContext("2d");
    this.size = null;
    this.lastLabel = "";
  }

  resize() {
    const box = this.canvas.parentElement.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.max(1, Math.round(box.width)), h = Math.max(1, Math.round(box.height));
    if (this.size && this.size.w === w && this.size.h === h && this.size.dpr === dpr) return false;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.size = { w, h, dpr };
    return true;
  }

  draw(points) {
    if (!this.size) this.resize();
    const { w, h, dpr } = this.size;
    const ctx = this.ctx;
    const { rl, ft, unit, digits } = this.spec;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.font = font;
    const x0 = 44, x1 = w - 58, yt = 22, yb = h - 22;
    if (x1 - x0 < 40 || yb - yt < 30) return;

    if (!points || points.length < 2) {
      ctx.fillStyle = "#8fa2c4"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText("collecting data…", (x0 + x1) / 2, (yt + yb) / 2);
      return;
    }
    const e0 = points[0][0], e1 = points[points.length - 1][0];
    let ymax = 0;
    for (const p of points) ymax = Math.max(ymax, p[rl], p[ft]);
    const step = niceStep(Math.max(ymax, 1e-6) * 1.1, 4);
    const top = Math.max(step, Math.ceil((ymax * 1.08) / step) * step);
    const X = (e) => x0 + ((e - e0) / Math.max(1e-9, e1 - e0)) * (x1 - x0);
    const Y = (v) => yb - (v / top) * (yb - yt);

    // grid + y labels
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (let v = 0; v <= top + 1e-9; v += step) {
      const y = Math.round(Y(v)) + 0.5;
      ctx.strokeStyle = v === 0 ? "#2a3857" : "#1a2640"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
      ctx.fillStyle = "#8fa2c4";
      ctx.fillText(v.toFixed(step < 1 ? 1 : 0), x0 - 6, y);
    }
    // x labels (episode time)
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const span = Math.max(1, e1 - e0);
    const xs = niceStep(span, 4);
    for (let e = Math.ceil(e0 / xs) * xs; e <= e1 + 1e-9; e += xs) {
      ctx.fillStyle = "#8fa2c4";
      ctx.fillText(fmtTime(e), X(e), yb + 5);
    }

    const series = (idx, color, dash) => {
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = "round";
      ctx.setLineDash(dash);
      ctx.beginPath();
      points.forEach((p, i) => (i ? ctx.lineTo(X(p[0]), Y(p[idx])) : ctx.moveTo(X(p[0]), Y(p[idx]))));
      ctx.stroke();
      ctx.setLineDash([]);
    };
    series(ft, FT, [6, 4]);
    series(rl, RL, []);

    // end labels with the current values
    const last = points[points.length - 1];
    const labels = [[last[rl], RL], [last[ft], FT]].sort((a, b) => b[0] - a[0]);
    let prevY = -1e9;
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.font = "600 11px ui-monospace, SFMono-Regular, Menlo, monospace";
    for (const [v, color] of labels) {
      let y = Y(v);
      if (y - prevY < 13) y = prevY + 13;
      prevY = y;
      ctx.fillStyle = color;
      ctx.fillText(`${v.toFixed(digits)}${unit}`, x1 + 6, Math.min(yb, y));
    }
    // legend
    ctx.font = font;
    let lx = x0 + 4;
    for (const [name, color, dash] of [["RL agents", RL, []], ["Fixed-time", FT, [6, 4]]]) {
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.setLineDash(dash);
      ctx.beginPath(); ctx.moveTo(lx, 9); ctx.lineTo(lx + 18, 9); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = "#c9d5ec"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
      ctx.fillText(name, lx + 23, 9);
      lx += 23 + ctx.measureText(name).width + 18;
    }

    const label = `${this.spec.label}: RL ${last[rl].toFixed(digits)}${unit}, ` +
                  `fixed-time ${last[ft].toFixed(digits)}${unit}`;
    if (label !== this.lastLabel) {
      this.canvas.setAttribute("aria-label", label);
      this.lastLabel = label;
    }
  }
}
