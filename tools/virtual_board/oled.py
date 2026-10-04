"""Read the virtual board's OLED: decode its RAM back into text, or draw it as an image.

The glyphs are taken from the firmware source itself (FONT5X7 in atsc_signal_node.ino),
so the decoder can never disagree with what the board draws.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[2]
SKETCH = ROOT / "firmware" / "atsc_signal_node" / "atsc_signal_node.ino"
COLS, CELL = 21, 6


def font() -> Dict[bytes, str]:
    """5-byte glyph -> character, parsed from the sketch's FONT5X7 table."""
    src = SKETCH.read_text(encoding="utf-8")
    body = src.split("FONT5X7[] PROGMEM = {", 1)[1].split("};", 1)[0]
    values = [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{2})", body)]
    if len(values) != 95 * 5:
        raise ValueError(f"FONT5X7 has {len(values)} bytes, expected {95 * 5}")
    table: Dict[bytes, str] = {}
    for i in range(95):
        glyph = bytes(values[i * 5:(i + 1) * 5])
        table.setdefault(glyph, chr(32 + i))
    return table


def parse_dump(lines: Sequence[str]) -> List[bytes]:
    """``OLED <page> <hex>`` lines (from vboard's ``oled`` command) -> 8 pages of 128 bytes."""
    pages = [bytes(128)] * 8
    for line in lines:
        parts = line.split()
        if len(parts) == 3 and parts[0] == "OLED":
            pages[int(parts[1])] = bytes.fromhex(parts[2])
    return pages


def text_rows(pages: Sequence[bytes]) -> List[str]:
    """The 8 text rows on the display ('?' marks a cell that is not a known glyph)."""
    table = font()
    rows = []
    for page in pages:
        row = []
        for c in range(COLS):
            cell = page[c * CELL:c * CELL + 5]
            row.append(table.get(bytes(cell), "?"))
        rows.append("".join(row).rstrip())
    return rows


def save_png(pages: Sequence[bytes], path: Path, scale: int = 4) -> None:
    """Draw the display (white on black, like the module) to a PNG."""
    from PIL import Image                     # optional: only for pictures

    img = Image.new("L", (128 * scale + 16, 64 * scale + 16), 0)
    px = img.load()
    for p, page in enumerate(pages):
        for col, byte in enumerate(page):
            for bit in range(8):
                if byte >> bit & 1:
                    x0, y0 = 8 + col * scale, 8 + (p * 8 + bit) * scale
                    for dx in range(scale - 1):
                        for dy in range(scale - 1):
                            px[x0 + dx, y0 + dy] = 235
    img.save(path)
