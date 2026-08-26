"""Transport for the hardware link: real serial, in-memory loopback, or nothing.

Three implementations behind one tiny interface:

``SerialLink``
    A real USB-serial connection to the node. ``pyserial`` is imported lazily and is
    **optional**, exactly like ``torch``, ``fastapi`` and SUMO elsewhere in this project
    — a missing package degrades a feature, it never breaks an import.
``LoopbackLink``
    In-memory queues. Lets the entire hardware path run, and be unit-tested, with no
    board attached; also gives a rehearsal mode where detector and RF events can be
    injected from code.
``NullLink``
    Discards writes, returns nothing. What you get when ``hardware.enabled`` is false,
    so the disabled path costs one attribute lookup and no branching elsewhere.

Every method is non-blocking and swallows transport errors: unplugging the USB cable
mid-demo degrades the board to its own failsafe (flashing amber) and leaves the
simulation and dashboard running.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Deque, List, Optional

#: partial-line buffer ceiling; a wedged port cannot grow memory without bound
_MAX_BUFFER = 4096


class HardwareUnavailable(RuntimeError):
    """Raised when a requested transport cannot be opened."""


class Link:
    """Minimal transport interface. Subclasses must not block."""

    name = "link"

    def write(self, payload: bytes) -> bool:            # pragma: no cover - interface
        raise NotImplementedError

    def read_lines(self) -> List[str]:                  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:                            # pragma: no cover - interface
        raise NotImplementedError

    @property
    def connected(self) -> bool:
        return True

    def describe(self) -> str:
        return self.name


class NullLink(Link):
    """Transport that goes nowhere (hardware disabled)."""

    name = "null"

    def write(self, payload: bytes) -> bool:
        return False

    def read_lines(self) -> List[str]:
        return []

    def close(self) -> None:
        return None

    @property
    def connected(self) -> bool:
        return False


class LoopbackLink(Link):
    """In-memory link. ``written`` records downlink frames; ``feed`` queues uplink lines."""

    name = "loopback"

    def __init__(self) -> None:
        self.written: List[bytes] = []
        self._inbox: Deque[str] = deque()
        self._open = True

    def write(self, payload: bytes) -> bool:
        if not self._open:
            return False
        self.written.append(bytes(payload))
        return True

    def feed(self, line: str) -> None:
        """Queue a line as if the node had sent it."""
        self._inbox.append(line)

    def read_lines(self) -> List[str]:
        lines, self._inbox = list(self._inbox), deque()
        return lines

    def close(self) -> None:
        self._open = False

    @property
    def connected(self) -> bool:
        return self._open

    def last_aspects(self) -> Optional[str]:
        """Aspect string of the most recent lamp frame — handy in tests and demos."""
        from atsc.hw.protocol import decode_lamps
        for raw in reversed(self.written):
            aspects = decode_lamps(raw.decode("ascii", errors="replace"))
            if aspects is not None:
                return aspects
        return None


class SerialLink(Link):
    """USB-serial link to the hardware node. Non-blocking reads, error-tolerant writes."""

    name = "serial"

    def __init__(self, port: str, baud: int = 115200, settle_s: float = 0.0) -> None:
        try:
            import serial                     # noqa: F401  (optional dependency)
        except Exception as exc:              # pragma: no cover - depends on env
            raise HardwareUnavailable(
                "pyserial is not installed. Install it with 'pip install pyserial==3.5', "
                "or set hardware.port: loopback to run the hardware path without a board."
            ) from exc

        import serial
        self._port_name = port
        try:
            self._ser = serial.Serial(port=port, baudrate=int(baud), timeout=0, write_timeout=0.2)
        except Exception as exc:
            raise HardwareUnavailable(
                f"could not open serial port '{port}': {exc}. Check the cable, close the "
                f"Arduino IDE serial monitor, and confirm the port in Device Manager."
            ) from exc
        self._buf = bytearray()
        self._open = True
        # an ESP32 reboots when the port opens; give the bootloader a moment if asked
        if settle_s > 0:
            time.sleep(float(settle_s))

    def write(self, payload: bytes) -> bool:
        if not self._open:
            return False
        try:
            self._ser.write(payload)
            return True
        except Exception:
            self._open = False               # cable pulled: stop trying, keep running
            return False

    def read_lines(self) -> List[str]:
        if not self._open:
            return []
        try:
            waiting = self._ser.in_waiting
            if waiting:
                self._buf.extend(self._ser.read(waiting))
        except Exception:
            self._open = False
            return []
        if len(self._buf) > _MAX_BUFFER:      # desynchronised: keep only the tail
            del self._buf[:-_MAX_BUFFER]
        lines: List[str] = []
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                break
            raw = bytes(self._buf[:idx])
            del self._buf[:idx + 1]
            lines.append(raw.decode("ascii", errors="replace"))
        return lines

    def close(self) -> None:
        self._open = False
        try:
            self._ser.close()
        except Exception:
            pass

    @property
    def connected(self) -> bool:
        return self._open

    def describe(self) -> str:
        return f"serial {self._port_name}"


# --------------------------------------------------------------------------- #
# Port discovery and the factory
# --------------------------------------------------------------------------- #
#: substrings identifying the USB-UART bridges these dev boards actually ship with
_USB_UART_HINTS = ("CH340", "CH910", "CP210", "FTDI", "FT232", "USB-SERIAL",
                   "SILICON LABS", "USB SERIAL", "WCH", "ESP32")


def autodetect_port() -> Optional[str]:
    """Best guess at the node's serial port, or ``None`` if nothing plausible is present.

    Bluetooth virtual ports are excluded explicitly: on Windows they appear as ordinary
    COM ports and are the single most common wrong answer.
    """
    try:
        from serial.tools import list_ports
    except Exception:
        return None
    candidates = []
    for info in list_ports.comports():
        blob = f"{info.description} {info.manufacturer or ''} {info.hwid or ''}".upper()
        if "BLUETOOTH" in blob:
            continue
        if any(hint in blob for hint in _USB_UART_HINTS):
            candidates.append(info.device)
    return candidates[0] if candidates else None


def open_link(port: str, baud: int = 115200, settle_s: float = 0.0) -> Link:
    """Open the transport named by ``port``.

    ``none`` (or empty) gives a :class:`NullLink`, ``loopback`` a :class:`LoopbackLink`,
    ``auto`` searches for a USB-UART bridge, and anything else is taken as a device name
    such as ``COM5`` or ``/dev/ttyUSB0``.
    """
    key = (port or "none").strip().lower()
    if key in ("none", "off", "disabled", ""):
        return NullLink()
    if key == "loopback":
        return LoopbackLink()
    if key == "auto":
        found = autodetect_port()
        if found is None:
            raise HardwareUnavailable(
                "no USB-serial adapter found. Plug the board in, or set hardware.port "
                "to the exact port (e.g. COM5), or to 'loopback' to run without a board."
            )
        return SerialLink(found, baud, settle_s)
    return SerialLink(port, baud, settle_s)
