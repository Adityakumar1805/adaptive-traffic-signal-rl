"""``python run.py hwtest`` - check a newly built signal board, one part at a time.

1. finds and opens the board's serial port and waits for the firmware to introduce itself;
2. walks every signal head through green, amber and red, junction by junction, saying on
   screen which heads should be lit, so a swapped wire is obvious;
3. listens to the 8 IR sensors and the 4 remote buttons, ticking each one off as it is
   seen, and lights the matching approach at J0_0 green so the model answers back.

Needs only pyserial - no trained model, no dashboard, no browser.
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional, Tuple

from atsc.hw.bridge import HardwareBridge
from atsc.hw.link import HardwareUnavailable, list_ports, open_link

JUNCTIONS = ("J0_0", "J0_1", "J1_0", "J1_1")
SIDE_WORDS = {"N": "north", "E": "east", "S": "south", "W": "west"}
ARRIVE_PINS = {"N": "D2", "E": "D3", "S": "D4", "W": "D5"}
QUEUE_PINS = {"N": "D6", "E": "D7", "S": "D8", "W": "D9"}
BUTTONS = {"ambulance": "A", "police": "B", "fire": "C", "toggle": "D"}


def _all_red() -> List[Tuple[str, int]]:
    return [("all_red", 0)] * len(JUNCTIONS)


def _show(bridge: HardwareBridge, aspects: List[Tuple[str, int]], seconds: float,
          on_event: Optional[Callable] = None) -> None:
    """Hold ``aspects`` for ``seconds``, refreshing often enough to keep the board live."""
    end = time.monotonic() + seconds
    while True:
        bridge.publish(aspects)
        for event in bridge.poll():
            if on_event is not None:
                on_event(event)
        if time.monotonic() >= end:
            return
        time.sleep(0.05)


def run_selftest(port: str = "auto", baud: int = 115200, listen_s: float = 60.0,
                 walk: bool = True, out: Callable[[str], None] = print) -> int:
    out("=" * 64)
    out("  Signal board self-test")
    out("=" * 64)
    ports = list_ports()
    if not ports:
        out("  No serial ports found (or pyserial is missing: pip install pyserial==3.5).")
    for device, desc, board in ports:
        out(f"  {'*' if board else ' '} {device:14s} {desc}")
    try:
        link = open_link(port, baud, settle_s=2.0)
    except HardwareUnavailable as exc:
        out(f"\n  Could not open the board: {exc}")
        return 1
    out(f"\n  Opened {link.describe()} at {baud} baud; the Uno restarts when the port opens.")

    bridge = HardwareBridge(link, n_phases=2, valid_tls=JUNCTIONS, heartbeat_s=0.5)
    # 1. the firmware says hello (at boot) and then sends a heartbeat every second
    deadline = time.monotonic() + 8.0
    heard = False
    while time.monotonic() < deadline and not heard:
        bridge.publish(_all_red())
        bridge.poll()
        heard = bridge.node_info is not None or bridge.stats["heartbeats"] > 0
        time.sleep(0.1)
    if not heard:
        out("  The port opened but the board said nothing for 8 s. Check that:")
        out("    - firmware/atsc_signal_node was uploaded to THIS board (Arduino IDE > Upload),")
        out("    - the Arduino IDE Serial Monitor is closed (only one program can use the port),")
        out("    - you picked the right port (try --port COM5 or --port /dev/ttyUSB0).")
        bridge.close()
        return 1
    out(f"  Board answered: {bridge.node_info or 'heartbeat only (firmware older than 1.2?)'}")

    # 2. lamp walk
    if walk:
        out("\n  Lamp walk - watch the model. All heads are RED except the ones named.")
        _show(bridge, _all_red(), 1.0)
        for j, tls in enumerate(JUNCTIONS):
            for phase, heads in ((0, "north + south"), (1, "east + west")):
                for state, word, secs in (("green", "GREEN", 1.5), ("yellow", "AMBER", 1.0)):
                    aspects = _all_red()
                    aspects[j] = (state, phase)
                    bridge.send_text(0, f"test {tls} {'NS' if phase == 0 else 'EW'} {word}")
                    out(f"    {tls}: {heads} heads {word}")
                    _show(bridge, aspects, secs)
                _show(bridge, _all_red(), 0.4)
        out("  If a head showed the wrong colour or lit at the wrong junction, compare its")
        out("  wires with the LED table in docs/HARDWARE.md (section 5).")

    # 3. sensors and remote
    seen: Dict[str, bool] = {}
    wanted = ([f"arrive {s}" for s in "NESW"] + [f"queue {s}" for s in "NESW"]
              + [f"button {b}" for b in "ABCD"])
    lit: Dict[str, float] = {}

    def on_event(event) -> None:
        if event.kind == "detect":
            key, what = f"arrive {event.approach}", (f"arrival sensor {SIDE_WORDS[event.approach]}"
                                                    f" ({ARRIVE_PINS[event.approach]})")
        elif event.kind == "queue":
            key = f"queue {event.approach}"
            what = (f"queue sensor {SIDE_WORDS[event.approach]} ({QUEUE_PINS[event.approach]})"
                    f" -> {event.count} waiting")
        elif event.kind == "emergency":
            key, what = f"button {BUTTONS.get(event.vehicle, '?')}", f"remote: {event.vehicle or event.corridor}"
        elif event.kind == "control":
            key, what = "button D", "remote: pause / resume"
        else:
            return
        first = key in wanted and not seen.get(key)
        seen[key] = True
        out(f"    {'NEW ' if first else '    '}{what}   [{sum(seen.values())}/{len(wanted)}]")
        if event.kind in ("detect", "queue"):
            lit[event.approach] = time.monotonic() + 1.0

    out(f"\n  Sensors - for up to {listen_s:.0f} s: pass a toy car (or your hand) in front of")
    out("  each of the 8 IR sensors at J0_0 and press each button on the remote.")
    out("  The matching approach at J0_0 turns green for a second each time.")
    end = time.monotonic() + listen_s
    while time.monotonic() < end and not all(seen.get(k) for k in wanted):
        now = time.monotonic()
        aspects = _all_red()
        green = [s for s, until in lit.items() if until > now]
        if green:
            aspects[0] = ("green", 0 if green[-1] in "NS" else 1)
        bridge.send_text(1, f"sensors seen {sum(seen.get(k, False) for k in wanted)}/12")
        _show(bridge, aspects, 0.1, on_event)

    missing = [k for k in wanted if not seen.get(k)]
    out("\n  Summary")
    out(f"    lamps    : {'walked - check by eye' if walk else 'skipped'}")
    out(f"    sensors  : {8 - sum(1 for k in missing if not k.startswith('button'))}/8 seen")
    out(f"    remote   : {4 - sum(1 for k in missing if k.startswith('button'))}/4 buttons seen")
    if missing:
        out("    not seen : " + ", ".join(missing))
        out("    (a sensor needs its potentiometer turned until its LED lights only when a")
        out("     car is in front; a remote button needs the receiver paired in momentary mode)")
    bridge.send_text(1, "self-test done")
    bridge.close()
    out("  When this program exits the board restarts and waits with flashing amber - normal.")
    return 0 if not missing else 2
