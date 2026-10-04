"""Wire protocol between the Python controller and the hardware node.

Pure functions only — no serial, no threads, no config — so the whole protocol is
unit-testable without a board attached (see ``tests/test_hardware.py``).

Frames are short ASCII lines terminated by ``\\n``. ASCII was chosen over a packed
binary format on purpose: a student debugging this can open a serial monitor and
*read* the traffic, which is worth more than the handful of bytes a binary encoding
would save at 115200 baud.

Downlink (PC -> node), sentinel ``>``::

    >L,<aspects>,<crc>\\n        lamp command, one char per (intersection, phase)
    >T,<row>,<text>,<crc>\\n     a line of text for the node's OLED, rows 0-3

``aspects`` is ``n_intersections * n_phases`` characters, in topology order, each one
of ``R`` (red), ``A`` (amber) or ``G`` (green). For the shipped ``ns_ew`` scheme that
is two characters per intersection — NS then EW — so a 2x2 grid sends 8 characters.
The whole board is sent in every frame rather than per-lamp deltas, which makes the
command **idempotent**: a dropped line cannot leave the board out of sync, because the
next frame restates the complete truth.

``text`` is at most :data:`TEXT_COLS` printable ASCII characters and never contains ``,``,
``<`` or ``>`` (:func:`encode_text` replaces them): the node treats ``>`` as the start of a
new frame, which is how it resynchronises after a damaged line.

Uplink (node -> PC), sentinel ``<``::

    <D,<tls>,<approach>,<crc>\\n          vehicle detected entering an approach
    <Q,<tls>,<approach>,<n>,<crc>\\n      quantised queue occupancy on an approach
    <E,<corridor>[,<vehicle>],<crc>\\n    RF emergency-vehicle request (ambulance, ...)
    <C,<action>,<crc>\\n                  control button (``toggle``: pause / resume)
    <H,<uptime_ms>,<crc>\\n               heartbeat / liveness
    <I,<text>,<crc>\\n                    node identity, sent at boot and whenever the
                                         node sees the PC again (``atsc-node 1.2.0 ...``)

``crc`` is CRC-8 (polynomial 0x07, init 0x00) over the payload — everything after the
sentinel up to, but excluding, the final comma — as two uppercase hex digits. A frame
whose CRC does not match is discarded silently; the 433 MHz receiver produces noise
bursts that would otherwise be read as commands.

The firmware side of every rule here is ``firmware/atsc_signal_node/atsc_core.h``;
``tests/test_hardware.py`` runs both against the same vectors.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

DOWNLINK_SENTINEL = ">"
UPLINK_SENTINEL = "<"

RED = "R"
AMBER = "A"
GREEN = "G"

#: aspect characters a well-formed lamp frame may contain
ASPECTS = (RED, AMBER, GREEN)

#: OLED text: rows the PC may write, and characters per row
TEXT_ROWS = 4
TEXT_COLS = 21


# --------------------------------------------------------------------------- #
# CRC-8
# --------------------------------------------------------------------------- #
def crc8(data: bytes | str, poly: int = 0x07, init: int = 0x00) -> int:
    """CRC-8/ATM over ``data``. Mirrors ``crc8()`` in the Arduino sketch."""
    if isinstance(data, str):
        data = data.encode("ascii", errors="replace")
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if (crc & 0x80) else ((crc << 1) & 0xFF)
    return crc


def _frame(sentinel: str, payload: str) -> bytes:
    return f"{sentinel}{payload},{crc8(payload):02X}\n".encode("ascii")


def _split_checked(line: str, sentinel: str) -> Optional[List[str]]:
    """Strip the sentinel, verify the CRC, return the payload fields (or ``None``)."""
    line = line.strip()
    if len(line) < 4 or not line.startswith(sentinel):
        return None
    head, _, crc_text = line[1:].rpartition(",")
    if not head or len(crc_text) != 2:
        return None
    try:
        if int(crc_text, 16) != crc8(head):
            return None
    except ValueError:
        return None
    return head.split(",")


# --------------------------------------------------------------------------- #
# Aspect derivation + lamp frames
# --------------------------------------------------------------------------- #
def aspect_chars(state: str, current_phase: int, n_phases: int) -> str:
    """Lamp aspects for one intersection, one character per phase group.

    ``state`` is the :class:`~atsc.envs.phases.SignalState` *value* (``"green"``,
    ``"yellow"`` or ``"all_red"``). ``current_phase`` is the FSM's ``current``, which
    during a transition still refers to the phase being **terminated** — so amber is
    shown on the outgoing group, as traffic engineering requires.

    >>> aspect_chars("green", 0, 2)
    'GR'
    >>> aspect_chars("yellow", 0, 2)
    'AR'
    >>> aspect_chars("all_red", 1, 2)
    'RR'
    """
    if n_phases < 1:
        raise ValueError("n_phases must be >= 1")
    if not (0 <= current_phase < n_phases):
        raise ValueError(f"current_phase {current_phase} outside [0,{n_phases})")
    if state == "green":
        lit = GREEN
    elif state == "yellow":
        lit = AMBER
    else:                      # all_red, or anything unrecognised -> safest aspect
        lit = RED
    return "".join(lit if p == current_phase else RED for p in range(n_phases))


def encode_lamps(per_intersection: Iterable[Tuple[str, int]], n_phases: int) -> bytes:
    """Build one ``>L`` frame from ``(state_value, current_phase)`` in topology order."""
    aspects = "".join(aspect_chars(s, p, n_phases) for s, p in per_intersection)
    if not aspects:
        raise ValueError("no intersections given")
    return _frame(DOWNLINK_SENTINEL, f"L,{aspects}")


def decode_lamps(line: str) -> Optional[str]:
    """Inverse of :func:`encode_lamps`: the aspect string, or ``None`` if invalid.

    Used by the tests and by the loopback link to prove firmware parity.
    """
    fields = _split_checked(line, DOWNLINK_SENTINEL)
    if not fields or fields[0] != "L" or len(fields) != 2:
        return None
    aspects = fields[1]
    if not aspects or any(ch not in ASPECTS for ch in aspects):
        return None
    return aspects


def sanitize_text(text: str) -> str:
    """``text`` as the node can show it: printable ASCII, no ``,`` ``<`` ``>``, at most
    :data:`TEXT_COLS` characters."""
    out = []
    for ch in str(text):
        if ch in ",<>" or not (" " <= ch <= "~"):
            ch = "?" if ch not in ",<>" else {",": ";", "<": "(", ">": ")"}[ch]
        out.append(ch)
    return "".join(out)[:TEXT_COLS].rstrip()


def encode_text(row: int, text: str) -> bytes:
    """Build one ``>T`` frame: show ``text`` on OLED row ``row`` (0-3) of the node."""
    if not 0 <= int(row) < TEXT_ROWS:
        raise ValueError(f"row must be 0..{TEXT_ROWS - 1}")
    return _frame(DOWNLINK_SENTINEL, f"T,{int(row)},{sanitize_text(text)}")


def decode_text(line: str) -> Optional[Tuple[int, str]]:
    """Inverse of :func:`encode_text`: ``(row, text)``, or ``None`` if invalid."""
    fields = _split_checked(line, DOWNLINK_SENTINEL)
    if not fields or fields[0] != "T" or len(fields) != 3:
        return None
    if len(fields[1]) != 1 or not fields[1].isdigit() or int(fields[1]) >= TEXT_ROWS:
        return None
    text = fields[2]
    if len(text) > TEXT_COLS or sanitize_text(text) != text.rstrip():
        return None
    return int(fields[1]), text


# --------------------------------------------------------------------------- #
# Uplink events
# --------------------------------------------------------------------------- #
VALID_APPROACHES = ("N", "S", "E", "W")
VALID_CORRIDORS = ("ns", "ew")
VALID_ACTIONS = ("toggle",)


@dataclass(frozen=True)
class UplinkEvent:
    """One decoded message from the hardware node.

    ``kind`` is ``"detect"``, ``"queue"``, ``"emergency"``, ``"control"``, ``"heartbeat"``
    or ``"info"``; the remaining fields are populated only where they apply. ``vehicle``
    is the emergency vehicle the button asks for (empty: the default for the corridor).
    """

    kind: str
    tls: str = ""
    approach: str = ""
    count: int = 0
    corridor: str = ""
    uptime_ms: int = 0
    vehicle: str = ""
    action: str = ""
    text: str = ""


def _is_word(s: str) -> bool:
    return 0 < len(s) <= 24 and all(c.islower() or c.isdigit() or c == "_" for c in s)


def decode_uplink(line: str) -> Optional[UplinkEvent]:
    """Decode one uplink line. Returns ``None`` for noise, bad CRC or unknown types.

    Silence rather than exceptions is deliberate: this is fed straight from a serial
    port that shares a breadboard with a 433 MHz receiver, so malformed input is
    normal operating conditions, not an error.
    """
    fields = _split_checked(line, UPLINK_SENTINEL)
    if not fields:
        return None
    kind = fields[0]

    if kind == "D" and len(fields) == 3:
        tls, approach = fields[1], fields[2].upper()
        if approach in VALID_APPROACHES and tls:
            return UplinkEvent("detect", tls=tls, approach=approach)
        return None

    if kind == "Q" and len(fields) == 4:
        tls, approach = fields[1], fields[2].upper()
        if approach not in VALID_APPROACHES or not tls:
            return None
        try:
            count = int(fields[3])
        except ValueError:
            return None
        return UplinkEvent("queue", tls=tls, approach=approach, count=max(0, count))

    if kind == "E" and len(fields) in (2, 3):
        corridor = fields[1].lower()
        vehicle = fields[2].lower() if len(fields) == 3 else ""
        if corridor in VALID_CORRIDORS and (not vehicle or _is_word(vehicle)):
            return UplinkEvent("emergency", corridor=corridor, vehicle=vehicle)
        return None

    if kind == "C" and len(fields) == 2:
        action = fields[1].lower()
        if action in VALID_ACTIONS:
            return UplinkEvent("control", action=action)
        return None

    if kind == "I" and len(fields) == 2:
        text = fields[1].strip()
        if text and all(" " <= c <= "~" for c in text):
            return UplinkEvent("info", text=text[:64])
        return None

    if kind == "H" and len(fields) == 2:
        try:
            uptime = int(fields[1])
        except ValueError:
            return None
        return UplinkEvent("heartbeat", uptime_ms=max(0, uptime))

    return None


def encode_uplink(event: UplinkEvent) -> bytes:
    """Build the uplink line for ``event`` — the firmware's job, used here by tests."""
    if event.kind == "detect":
        payload = f"D,{event.tls},{event.approach}"
    elif event.kind == "queue":
        payload = f"Q,{event.tls},{event.approach},{event.count}"
    elif event.kind == "emergency":
        payload = f"E,{event.corridor}" + (f",{event.vehicle}" if event.vehicle else "")
    elif event.kind == "control":
        payload = f"C,{event.action}"
    elif event.kind == "heartbeat":
        payload = f"H,{event.uptime_ms}"
    elif event.kind == "info":
        payload = f"I,{event.text}"
    else:
        raise ValueError(f"unknown event kind '{event.kind}'")
    return _frame(UPLINK_SENTINEL, payload)
