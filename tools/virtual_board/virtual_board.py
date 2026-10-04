#!/usr/bin/env python3
"""The signal node's real firmware on a simulated Arduino Uno - no hardware needed.

``vboard`` (vboard.c, built on the simavr AVR simulator) runs the compiled firmware on an
ATmega328P at 16 MHz in real time, connects its USB serial port to a pseudo-terminal and
models the six 74HC595s, the OLED, the buzzer, the IR sensors and the 433 MHz receiver.
This module builds it, starts it, and gives Python access to all of that:

    with VirtualBoard.start() as board:
        print(board.port)                 # /dev/pts/N - pass it to: run.py demo --hardware --port
        board.press("arrive", "N")        # a toy car passes the north arrival sensor
        board.press("button", "A")        # remote button A (ambulance)
        print(board.aspects())            # what the 16 heads show, e.g. 'GRARRRRR'
        print(board.oled_text())          # the 8 OLED rows

Command line (Linux; apt install gcc-avr avr-libc arduino-core-avr simavr libsimavr-dev
libelf-dev):

    python tools/virtual_board/virtual_board.py      # prints the port, then the lamp changes
"""
from __future__ import annotations

import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import build_firmware  # noqa: E402
import oled as oled_mod  # noqa: E402

BUILD = ROOT / "build" / "virtual_board"
SIDES = "NESW"
ARRIVE_PINS = {"N": "D2", "E": "D3", "S": "D4", "W": "D5"}
QUEUE_PINS = {"N": "D6", "E": "D7", "S": "B0", "W": "B1"}     # Uno D8, D9 are PB0, PB1
BUTTON_PINS = {"A": "C0", "B": "C1", "C": "C2", "D": "C3"}     # Uno A0..A3 are PC0..PC3
LETTER = {0: "R", 1: "A", 2: "G"}


def simulator_available() -> bool:
    return (build_firmware.toolchain_available() and shutil.which("cc") is not None
            and Path("/usr/include/simavr/sim_avr.h").is_file())


def build_vboard(out_dir: Path = BUILD) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    exe = out_dir / "vboard"
    src = HERE / "vboard.c"
    if exe.exists() and exe.stat().st_mtime >= src.stat().st_mtime:
        return exe
    cmd = ["cc", "-O2", "-o", str(exe), str(src), "-I/usr/include/simavr",
           "-I/usr/include/simavr/parts", "-lsimavrparts", "-lsimavr", "-lelf", "-lpthread", "-lutil"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("building vboard failed:\n" + proc.stderr)
    return exe


def image_to_heads(image: int, heads: int = 16) -> List[str]:
    """48-bit lamp image -> per head the lit lamps, e.g. ['G', 'R', ...] ('' = dark,
    'RA' = two lamps lit, which the firmware never does except in its lamp test)."""
    out = []
    for h in range(heads):
        out.append("".join(LETTER[c] for c in range(3) if image >> (h * 3 + c) & 1))
    return out


def heads_to_aspects(heads: List[str]) -> Optional[str]:
    """Per-junction NS/EW aspects ('GRARRRRG'), or None when the heads do not form one
    consistent picture (dark, lamp test, or N and S heads disagreeing)."""
    out = []
    for j in range(len(heads) // 4):
        n, e, s, w = heads[j * 4:(j + 1) * 4]
        if n != s or e != w or len(n) != 1 or len(e) != 1:
            return None
        out += [n, e]
    return "".join(out)


class VirtualBoard:
    """A running ``vboard`` process."""

    def __init__(self, proc: subprocess.Popen, port: str) -> None:
        self.proc = proc
        self.port = port
        self.lamps: List[Tuple[float, int]] = []     # (board ms, image) for every change
        self.buzzer: List[Tuple[float, int]] = []
        self._lines: "queue.Queue[str]" = queue.Queue()
        self._oled: List[str] = []
        self._oled_done = threading.Event()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    # ------------------------------------------------------------------ #
    @classmethod
    def start(cls, firmware_defines: Optional[Dict[str, str]] = None, oled: bool = True,
              timeout: float = 20.0) -> "VirtualBoard":
        tag = "-".join(f"{k}{v}" for k, v in sorted((firmware_defines or {}).items())) or "default"
        fw = build_firmware.build(BUILD / f"fw-{tag}", firmware_defines or {})
        exe = build_vboard()
        cmd = [str(exe)] + ([] if oled else ["--no-oled"]) + [str(fw["elf"])]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, bufsize=1)
        deadline = time.monotonic() + timeout
        port = None
        early: List[str] = []
        while port is None:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError("vboard exited before it was ready")
            m = re.match(r"READY pty=(\S+)", line)
            if m:
                port = m.group(1)
            else:
                early.append(line)
            if time.monotonic() > deadline:
                proc.kill()
                raise RuntimeError("vboard did not start")
        board = cls(proc, port)
        for line in early:
            board._handle(line.rstrip("\n"))
        return board

    def __enter__(self) -> "VirtualBoard":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def stop(self) -> None:
        if self.proc.poll() is None:
            try:
                self._send("quit")
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()

    # ------------------------------------------------------------------ #
    def _read(self) -> None:
        for raw in self.proc.stdout:
            self._handle(raw.rstrip("\n"))

    def _handle(self, line: str) -> None:
        parts = line.split()
        if not parts:
            return
        if parts[0] == "LAMPS" and len(parts) == 3:
            self.lamps.append((float(parts[1]), int(parts[2], 16)))
        elif parts[0] == "BUZZ" and len(parts) == 3:
            self.buzzer.append((float(parts[1]), int(parts[2])))
        elif parts[0] == "OLED":
            self._oled.append(line)
        elif parts[0] == "OLEDEND":
            self._oled_done.set()
        elif parts[0] in ("TIME", "ERR", "HALT"):
            self._lines.put(line)

    def _send(self, command: str) -> None:
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()

    # ------------------------------------------------------------------ #
    def set_pin(self, pin: str, level: int) -> None:
        """Drive an input, e.g. ``set_pin("D2", 0)``: an IR sensor sees a car."""
        self._send(f"pin {pin[0]}{pin[1:]} {int(level)}")

    def hold(self, sensor: str, side_or_button: str, active: bool) -> None:
        """Block / clear an IR sensor ("arrive"/"queue" + N E S W) or hold / release a
        remote button ("button" + A B C D)."""
        if sensor == "button":
            self.set_pin(BUTTON_PINS[side_or_button], 1 if active else 0)
        else:
            pins = ARRIVE_PINS if sensor == "arrive" else QUEUE_PINS
            self.set_pin(pins[side_or_button], 0 if active else 1)     # IR modules: LOW = car

    def press(self, sensor: str, side_or_button: str, seconds: float = 0.2) -> None:
        self.hold(sensor, side_or_button, True)
        time.sleep(seconds)
        self.hold(sensor, side_or_button, False)

    def image(self) -> Optional[int]:
        return self.lamps[-1][1] if self.lamps else None

    def aspects(self) -> Optional[str]:
        img = self.image()
        return None if img is None else heads_to_aspects(image_to_heads(img))

    def oled_pages(self, timeout: float = 5.0) -> List[bytes]:
        self._oled, self._oled_done = [], threading.Event()
        self._send("oled")
        if not self._oled_done.wait(timeout):
            raise RuntimeError("no OLED dump from vboard")
        return oled_mod.parse_dump(self._oled)

    def oled_text(self) -> List[str]:
        return oled_mod.text_rows(self.oled_pages())


def main() -> int:
    if not simulator_available():
        print("needs Linux with: apt install gcc-avr avr-libc arduino-core-avr simavr "
              "libsimavr-dev libelf-dev")
        return 1
    with VirtualBoard.start() as board:
        print(f"Virtual signal board running. Its serial port: {board.port}")
        print(f"  python run.py demo --hardware --port {board.port}")
        print("Lamp changes follow (Ctrl+C to stop).")
        shown = 0
        try:
            while board.proc.poll() is None:
                while shown < len(board.lamps):
                    t, img = board.lamps[shown]
                    shown += 1
                    heads = image_to_heads(img)
                    print(f"  {t / 1000:8.1f} s  {heads_to_aspects(heads) or ' '.join(h or '-' for h in heads)}")
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
