#!/usr/bin/env python3
"""Draw the figures of docs/HARDWARE.md: the model's layout and its wiring.

    python tools/hardware_diagrams.py   # -> docs/hardware/board_layout.svg, wiring_leds.svg,
                                        #    wiring_inputs.svg

Both are generated from the same numbers the firmware uses (head = junction * 4 + side,
LED = head * 3 + colour), so the pictures cannot drift from the code.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "hardware"

FONT = "font-family='Helvetica, Arial, sans-serif'"
JUNCTIONS = [("J0_0", 250, 250), ("J0_1", 650, 250), ("J1_0", 250, 650), ("J1_1", 650, 650)]
SIDES = "NESW"
ROAD = 100          # road width (two lanes)
HALF = ROAD // 2


class Svg:
    def __init__(self, w: int, h: int, bg: str) -> None:
        self.w, self.h = w, h
        self.parts: List[str] = [f"<rect width='{w}' height='{h}' fill='{bg}'/>"]

    def add(self, s: str) -> None:
        self.parts.append(s)

    def text(self, x, y, s, size=14, fill="#111", anchor="middle", weight="normal", rotate=None) -> None:
        tr = f" transform='rotate({rotate} {x} {y})'" if rotate is not None else ""
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.add(f"<text x='{x}' y='{y}' font-size='{size}' fill='{fill}' text-anchor='{anchor}' "
                 f"font-weight='{weight}' {FONT}{tr}>{s}</text>")

    def rect(self, x, y, w, h, fill, stroke="none", sw=1, rx=0) -> None:
        self.add(f"<rect x='{x}' y='{y}' width='{w}' height='{h}' fill='{fill}' stroke='{stroke}' "
                 f"stroke-width='{sw}' rx='{rx}'/>")

    def line(self, x1, y1, x2, y2, stroke="#111", sw=2, dash=None) -> None:
        d = f" stroke-dasharray='{dash}'" if dash else ""
        self.add(f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' stroke='{stroke}' stroke-width='{sw}'{d}/>")

    def poly(self, pts, stroke="#111", sw=2, fill="none") -> None:
        p = " ".join(f"{x},{y}" for x, y in pts)
        self.add(f"<polyline points='{p}' fill='{fill}' stroke='{stroke}' stroke-width='{sw}' "
                 f"stroke-linejoin='round'/>")

    def circle(self, x, y, r, fill, stroke="none", sw=1) -> None:
        self.add(f"<circle cx='{x}' cy='{y}' r='{r}' fill='{fill}' stroke='{stroke}' stroke-width='{sw}'/>")

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n  ".join(self.parts)
        path.write_text(f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {self.w} {self.h}' "
                        f"width='{self.w}' height='{self.h}'>\n  {body}\n</svg>\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# 1. layout of the model, seen from above
# --------------------------------------------------------------------------- #
def arrow(svg: Svg, x, y, dx, dy, color="#f4f4f4") -> None:
    """A lane arrow pointing along (dx, dy)."""
    L = 26
    x2, y2 = x + dx * L, y + dy * L
    svg.line(x - dx * L, y - dy * L, x2, y2, stroke=color, sw=3)
    # head
    px, py = -dy, dx
    svg.add(f"<polygon points='{x2 + dx * 10},{y2 + dy * 10} {x2 + px * 7},{y2 + py * 7} "
            f"{x2 - px * 7},{y2 - py * 7}' fill='{color}'/>")


def head_glyph(svg: Svg, x, y, label: str, vertical: bool, above: bool = True, dx: float = 0) -> None:
    """A three-lamp signal head with its number written above or below it."""
    w, h = (18, 46) if vertical else (46, 18)
    svg.rect(x - w / 2, y - h / 2, w, h, "#1d1d1d", "#000", 1, rx=4)
    for i, col in enumerate(("#e53935", "#ffb300", "#43a047")):
        cx = x if vertical else x - 14 + i * 14
        cy = y - 14 + i * 14 if vertical else y
        svg.circle(cx, cy, 5, col)
    if label:
        svg.text(x + dx, y - h / 2 - 6 if above else y + h / 2 + 15, label, size=13, weight="bold")


def board_layout() -> Svg:
    s = Svg(1000, 1080, "#ffffff")
    s.rect(50, 50, 900, 900, "#d9cfb5", "#8a7d5c", 3, rx=6)                 # the base board
    s.text(500, 36, "Top view of the 2x2 model (about 90 cm x 90 cm), North up. Traffic keeps LEFT.",
           size=18, weight="bold")
    # roads
    for c in (250, 650):
        s.rect(50, 50 + c - HALF, 900, ROAD, "#3a3d42")
        s.rect(50 + c - HALF, 50, ROAD, 900, "#3a3d42")
    for c in (250, 650):                                                     # centre lines
        s.line(50, 50 + c, 950, 50 + c, "#f7d046", 2, "14 10")
        s.line(50 + c, 50, 50 + c, 950, "#f7d046", 2, "14 10")
    # emergency corridors, in the lane they drive in
    s.line(70, 700 - 25, 930, 700 - 25, "#e53935", 4, "3 9")         # eastbound: north half
    s.line(700 + 25, 70, 700 + 25, 930, "#42a5f5", 4, "3 9")         # southbound: east half
    for name, jx, jy in JUNCTIONS:
        x, y = 50 + jx, 50 + jy
        s.rect(x - HALF, y - HALF, ROAD, ROAD, "#45484e")
        s.text(x, y + 6, name, size=18, fill="#ffffff", weight="bold")
        # stop lines on the inbound (left-hand) lane of each arm
        s.line(x, y - HALF - 4, x + HALF, y - HALF - 4, "#ffffff", 5)          # N arm, east half
        s.line(x + HALF + 4, y, x + HALF + 4, y + HALF, "#ffffff", 5)          # E arm, south half
        s.line(x - HALF, y + HALF + 4, x, y + HALF + 4, "#ffffff", 5)          # S arm, west half
        s.line(x - HALF - 4, y - HALF, x - HALF - 4, y, "#ffffff", 5)          # W arm, north half
        arrow(s, x + HALF / 2, y - HALF - 70, 0, 1)        # from the north, driving south
        arrow(s, x + HALF + 70, y + HALF / 2, -1, 0)       # from the east, driving west
        arrow(s, x - HALF / 2, y + HALF + 70, 0, -1)       # from the south, driving north
        arrow(s, x - HALF - 70, y - HALF / 2, 1, 0)        # from the west, driving east
        # each head stands on the left kerb of the lane it controls, at its stop line
        j = [n for n, _x, _y in JUNCTIONS].index(name)
        head_glyph(s, x + HALF + 16, y - HALF - 30, f"H{j * 4 + 0} N", True, above=True, dx=8)
        head_glyph(s, x + HALF + 30, y + HALF + 16, f"H{j * 4 + 1} E", False, above=False)
        head_glyph(s, x - HALF - 16, y + HALF + 30, f"H{j * 4 + 2} S", True, above=False, dx=-8)
        head_glyph(s, x - HALF - 30, y - HALF - 16, f"H{j * 4 + 3} W", False, above=True)

    # IR sensors at J0_0, on the same kerb: queue sensor just behind the stop line (where the
    # first waiting car stands), arrival sensor further back
    x, y = 300, 300
    sensors = [  # (centre x, centre y, label, label position)
        (x + HALF + 9, y - HALF - 90, "Q-N  D6", "right"), (x + HALF + 9, y - HALF - 175, "A-N  D2", "right"),
        (x + HALF + 90, y + HALF + 9, "Q-E  D7", "below"), (x + HALF + 175, y + HALF + 9, "A-E  D3", "below"),
        (x - HALF - 9, y + HALF + 90, "Q-S  D8", "left"), (x - HALF - 9, y + HALF + 175, "A-S  D4", "left"),
        (x - HALF - 90, y - HALF - 9, "Q-W  D9", "above"), (x - HALF - 175, y - HALF - 9, "A-W  D5", "above"),
    ]
    for sx, sy, label, where in sensors:
        s.rect(sx - 9, sy - 9, 18, 18, "#1565c0", "#0d47a1", 2, rx=3)
        if where == "right":
            s.text(sx + 15, sy + 5, label, size=13, fill="#0d47a1", anchor="start", weight="bold")
        elif where == "left":
            s.text(sx - 15, sy + 5, label, size=13, fill="#0d47a1", anchor="end", weight="bold")
        elif where == "below":
            s.text(sx, sy + 26, label, size=13, fill="#0d47a1", weight="bold")
        else:
            s.text(sx, sy - 15, label, size=13, fill="#0d47a1", weight="bold")

    # legend
    ly = 980
    s.rect(70, ly - 12, 14, 14, "#1565c0", rx=3)
    s.text(92, ly, "IR sensor at J0_0 (A = arrival, Q = queue) and its Arduino pin", size=13, anchor="start")
    head_glyph(s, 560, ly - 5, "", False)
    s.text(590, ly, "signal head Hn: n = junction x 4 + side (N 0, E 1, S 2, W 3)", size=13, anchor="start")
    s.line(70, ly + 30, 120, ly + 30, "#e53935", 4, "3 9")
    s.text(130, ly + 35, "ambulance and fire engine (remote A, C): enter J1_0 from the west, leave J1_1 "
           "to the east", size=13, anchor="start")
    s.line(70, ly + 60, 120, ly + 60, "#42a5f5", 4, "3 9")
    s.text(130, ly + 65, "police car (remote B): enters J0_1 from the north, leaves J1_1 to the south",
           size=13, anchor="start")
    # compass
    s.poly([(905, 140), (915, 110), (925, 140)], "#111", 2, "#111")
    s.text(915, 100, "N", size=16, weight="bold")
    return s


# --------------------------------------------------------------------------- #
# 2. the shift-register chain that drives the 48 LEDs
# --------------------------------------------------------------------------- #
RED, BLK, BLU, ORG, GRN, PUR, TEAL, BRN = ("#d32f2f", "#222", "#1565c0", "#ef6c00", "#2e7d32",
                                           "#6a1b9a", "#00838f", "#6d4c41")


def net(s: Svg, x, y, name: str, color: str, up: bool) -> None:
    """A short stub ending in a net label (5V / GND), schematic style."""
    y2 = y - 18 if up else y + 18
    s.line(x, y, x, y2, color, 2)
    s.line(x - 7, y2, x + 7, y2, color, 3)
    s.text(x, y2 - 5 if up else y2 + 15, name, size=11, fill=color, weight="bold")


def chain() -> Svg:
    s = Svg(1500, 700, "#ffffff")
    s.text(750, 32, "LED driver: six 74HC595 shift registers in a chain (all 48 LEDs from 3 Arduino pins)",
           size=19, weight="bold")
    # Arduino (only the pins this part uses)
    ax, ay = 40, 120
    s.rect(ax, ay, 170, 250, "#0b7a8a", "#055", 3, rx=10)
    s.text(ax + 85, ay + 30, "Arduino Uno", size=18, fill="#fff", weight="bold")
    pin_y = {"D11": ay + 70, "D13": ay + 120, "D10": ay + 170, "5V": ay + 205, "GND": ay + 230}
    for p, y in pin_y.items():
        s.rect(ax + 158, y - 7, 18, 14, "#e0e0e0", "#555", 1)
        s.text(ax + 150, y + 5, p, size=13, fill="#fff", anchor="end", weight="bold")
    s.text(ax + 85, ay + 280, "5V and GND feed every chip,", size=12)
    s.text(ax + 85, ay + 296, "the LED modules and the sensors", size=12)

    cx0, cy, cw, ch, step = 300, 130, 150, 230, 180
    left = [("14", "DS", "data in"), ("11", "SH_CP", "clock"), ("12", "ST_CP", "latch")]
    yrow = {"DS": cy + 60, "SH_CP": cy + 100, "ST_CP": cy + 140, "Q7'": cy + 60}
    clock_bus, latch_bus = cy + ch + 70, cy + ch + 100
    for k in range(6):
        x = cx0 + k * step
        s.rect(x, cy, cw, ch, "#263238", "#000", 2, rx=6)
        s.text(x + cw / 2, cy + 26, f"74HC595  #{k + 1}", size=15, fill="#fff", weight="bold")
        for num, name, _w in left:
            s.text(x + 8, yrow[name] + 4, f"{num} {name}", size=12, fill="#cfd8dc", anchor="start")
        s.text(x + cw - 8, yrow["Q7'"] + 4, "Q7' 9", size=12, fill="#cfd8dc", anchor="end")
        s.text(x + cw / 2, cy + 178, f"Q0-Q7 -> LEDs {k * 8}-{k * 8 + 7}", size=12, fill="#ffe082")
        heads = sorted({n // 3 for n in range(k * 8, k * 8 + 8)})
        s.text(x + cw / 2, cy + 196, "heads " + ", ".join(f"H{h}" for h in heads), size=12, fill="#ffe082")
        # power: VCC (16) and MR (10) to 5V on top, GND (8) and OE (13) to GND below
        s.text(x + 34, cy + 222, "16 VCC", size=10, fill="#ffcdd2")
        s.text(x + 116, cy + 222, "8 GND", size=10, fill="#cfd8dc")
        net(s, x + 34, cy, "5V", RED, True)
        net(s, x + 116, cy, "GND", BLK, True)
        # decoupling capacitor between VCC and GND, next to the chip
        s.line(x + 46, cy - 14, x + 104, cy - 14, "#777", 1)
        s.line(x + 72, cy - 22, x + 72, cy - 6, "#555", 3)
        s.line(x + 78, cy - 22, x + 78, cy - 6, "#555", 3)
        # chain wiring
        if k == 0:
            s.poly([(ax + 176, pin_y["D11"]), (250, pin_y["D11"]), (250, yrow["DS"]), (x, yrow["DS"])], BLU, 3)
        else:
            prev = x - step + cw
            mid = prev + (x - prev) / 2
            s.poly([(prev, yrow["Q7'"]), (mid, yrow["Q7'"]), (mid, yrow["DS"] + 0.1), (x, yrow["DS"])], BLU, 3)
        # taps from the clock / latch buses
        s.poly([(x, yrow["SH_CP"]), (x - 14, yrow["SH_CP"]), (x - 14, clock_bus)], ORG, 2)
        s.poly([(x, yrow["ST_CP"]), (x - 24, yrow["ST_CP"]), (x - 24, latch_bus)], GRN, 2)
        s.circle(x - 14, clock_bus, 4, ORG)
        s.circle(x - 24, latch_bus, 4, GRN)
    end_x = cx0 + 5 * step - 14
    s.poly([(ax + 176, pin_y["D13"]), (230, pin_y["D13"]), (230, clock_bus), (end_x, clock_bus)], ORG, 3)
    s.poly([(ax + 176, pin_y["D10"]), (220, pin_y["D10"]), (220, latch_bus), (end_x - 10, latch_bus)], GRN, 3)
    s.text(end_x + 14, clock_bus + 4, "D13 clock: pin 11 of every chip", size=12, fill=ORG, anchor="start",
           weight="bold")
    s.text(end_x + 14, latch_bus + 4, "D10 latch: pin 12 of every chip", size=12, fill=GRN, anchor="start",
           weight="bold")
    s.text(end_x + 14, clock_bus - 44, "D11 data: chip #1 pin 14, then", size=12, fill=BLU, anchor="start",
           weight="bold")
    s.text(end_x + 14, clock_bus - 28, "pin 9 of each chip -> pin 14 of the next", size=12, fill=BLU, anchor="start",
           weight="bold")
    s.text(cx0 + 2.5 * step + 75, cy - 48, "On every chip: pins 16 (VCC) and 10 (MR) to 5V, pins 8 (GND) and 13 (OE) "
           "to GND, and a 100 nF capacitor from pin 16 to pin 8, as close to the chip as you can.",
           size=13, fill="#333")

    # one signal head, wired to chip #1
    hx, hy = 330, 500
    s.rect(hx, hy, 120, 110, "#1d1d1d", "#000", 2, rx=8)
    s.text(hx + 60, hy - 8, "signal head H0 (J0_0 north)", size=13, weight="bold")
    for i, (col, lab, q, pin) in enumerate((("#e53935", "R", "Q0", 15), ("#ffb300", "Y", "Q1", 1),
                                            ("#43a047", "G", "Q2", 2))):
        y = hy + 25 + i * 28
        s.circle(hx + 25, y, 10, col)
        s.text(hx + 45, y + 5, lab, size=13, fill="#fff", anchor="start", weight="bold")
        s.line(hx + 120, y, hx + 180, y, BRN, 2.5)
        s.text(hx + 186, y + 5, f"chip #1 {q} (pin {pin})", size=12, anchor="start")
    s.line(hx + 60, hy + 110, hx + 60, hy + 124, BLK, 2)
    net(s, hx + 60, hy + 124, "GND", BLK, False)
    s.text(800, hy + 20, "Every head is wired the same way: its red, amber and green pins to three outputs",
           size=13, anchor="start")
    s.text(800, hy + 40, "in a row, its GND pin to GND. Head Hn uses LEDs 3n, 3n+1, 3n+2; LED m is", size=13,
           anchor="start")
    s.text(800, hy + 60, "output Q(m mod 8) of chip #(m div 8 + 1) - the table in HARDWARE.md, step 4.",
           size=13, anchor="start")
    s.text(800, hy + 86, "Modules without built-in resistors need 220-330 ohm in series with each LED.",
           size=13, anchor="start", fill=RED)
    return s


# --------------------------------------------------------------------------- #
# 3. sensors, remote receiver, OLED, buzzer
# --------------------------------------------------------------------------- #
def peripherals() -> Svg:
    s = Svg(1200, 640, "#ffffff")
    s.text(600, 32, "Inputs and extras: 8 IR sensors, 433 MHz receiver, OLED, buzzer", size=19, weight="bold")
    pins = ["D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D12", "A0", "A1", "A2", "A3", "A4", "A5"]
    ax, ay, gap = 60, 70, 33
    s.rect(ax, ay, 200, gap * len(pins) + 30, "#0b7a8a", "#055", 3, rx=10)
    s.text(ax + 100, ay - 10 + gap * len(pins) + 70, "Arduino Uno", size=16, weight="bold", fill="#0b7a8a")
    y_of = {}
    for i, p in enumerate(pins):
        y = ay + 30 + i * gap
        y_of[p] = y
        s.rect(ax + 188, y - 7, 18, 14, "#e0e0e0", "#555", 1)
        s.text(ax + 180, y + 5, p, size=13, fill="#fff", anchor="end", weight="bold")
    x_pin = ax + 206

    def device(y_top, y_bot, title, color, x=560, w=330):
        s.rect(x, y_top - 16, w, y_bot - y_top + 32, color, "#000", 1.5, rx=8)
        s.text(x + w - 10, y_top + (y_bot - y_top) / 2 + 5, title, size=13, fill="#fff", anchor="end",
               weight="bold")
        return x

    sensors = [("D2", "arrival N"), ("D3", "arrival E"), ("D4", "arrival S"), ("D5", "arrival W"),
               ("D6", "queue N"), ("D7", "queue E"), ("D8", "queue S"), ("D9", "queue W")]
    for pin, what in sensors:
        y = y_of[pin]
        s.rect(560, y - 13, 330, 26, "#1565c0", "#0d47a1", 1.5, rx=5)
        s.text(570, y + 5, "OUT", size=12, fill="#fff", anchor="start", weight="bold")
        s.text(880, y + 5, f"IR sensor (FC-51) at J0_0, {what}", size=12, fill="#e3f2fd", anchor="end")
        s.line(x_pin, y, 560, y, BLU, 2.5)
    y = y_of["D12"]
    s.rect(560, y - 13, 330, 26, "#37474f", "#000", 1.5, rx=5)
    s.text(570, y + 5, "I/O", size=12, fill="#fff", anchor="start", weight="bold")
    s.text(880, y + 5, "active buzzer module (optional)", size=12, fill="#fff", anchor="end")
    s.line(x_pin, y, 560, y, BRN, 2.5)
    device(y_of["A0"], y_of["A3"], "433 MHz receiver RX480E-4", "#6a1b9a")
    for pin, out, what in (("A0", "D0", "button A: ambulance"), ("A1", "D1", "button B: police car"),
                           ("A2", "D2", "button C: fire engine"), ("A3", "D3", "button D: pause / resume")):
        y = y_of[pin]
        s.line(x_pin, y, 560, y, PUR, 2.5)
        s.text(570, y + 5, f"{out}   {what}", size=12, fill="#fff", anchor="start")
    device(y_of["A4"], y_of["A5"], "0.96\" I2C OLED (optional)", "#212121")
    for pin, sig in (("A4", "SDA"), ("A5", "SCL")):
        y = y_of[pin]
        s.line(x_pin, y, 560, y, TEAL, 2.5)
        s.text(570, y + 5, sig, size=12, fill="#fff", anchor="start", weight="bold")
    # power note
    s.rect(920, 70, 250, 236, "#fff8e1", "#ffb300", 1.5, rx=8)
    for i, line in enumerate(["Every module also has", "VCC -> 5V and GND -> GND.", "",
                              "IR sensors: OUT goes LOW", "when they see a car.", "",
                              "Receiver: D0-D3 go HIGH", "while a button is held", "(pair it in momentary mode).",
                              "", "OLED address 0x3C (or 0x3D)."]):
        s.text(935, 96 + i * 19, line, size=12, anchor="start")
    s.text(415, y_of["D2"] - 22, "one wire per sensor, in this order", size=12, fill=BLU)
    return s


def main() -> int:
    board_layout().save(OUT / "board_layout.svg")
    chain().save(OUT / "wiring_leds.svg")
    peripherals().save(OUT / "wiring_inputs.svg")
    print(f"wrote board_layout.svg, wiring_leds.svg, wiring_inputs.svg in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
