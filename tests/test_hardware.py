"""The physical signal board: protocol, firmware logic, playback, and the dashboard link.

Layers, each tested on its own and then together:

* ``atsc.hw.protocol`` - the wire format (pure functions);
* ``firmware/atsc_signal_node/atsc_core.h`` - the board's logic, compiled for the PC and
  checked against the Python side (skipped without a C++ compiler);
* ``HardwareBridge`` / ``HardwareMirror`` - dedup, real-time playback, sensors, reconnect,
  with a fake clock and an in-memory link;
* ``DashboardSession`` in hardware mode, over the in-memory link;
* the real firmware on a simulated ATmega328P (``tools/virtual_board``), driven by the real
  dashboard session (skipped unless avr-gcc and simavr are installed).

With hardware off - the default - none of this code runs; the last tests check that.
"""
from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from atsc.config import load_config
from atsc.envs.traffic_env import MultiAgentTrafficEnv
from atsc.hw import (
    HardwareBridge,
    HardwareMirror,
    HardwareUnavailable,
    LoopbackLink,
    NullLink,
    UplinkEvent,
    aspect_chars,
    crc8,
    decode_lamps,
    decode_text,
    decode_uplink,
    encode_lamps,
    encode_text,
    encode_uplink,
    hardware_settings,
    sanitize_text,
)

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / "firmware" / "atsc_signal_node"
TLS = ["J0_0", "J0_1", "J1_0", "J1_1"]


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class TimedLoopback(LoopbackLink):
    """Records when (fake clock) each frame went out."""

    def __init__(self, clock) -> None:
        super().__init__()
        self.clock = clock
        self.log = []

    def write(self, payload: bytes) -> bool:
        ok = super().write(payload)
        if ok:
            self.log.append((self.clock(), payload.decode("ascii")))
        return ok

    def lamp_changes(self):
        out = []
        for t, line in self.log:
            aspects = decode_lamps(line)
            if aspects and (not out or out[-1][1] != aspects):
                out.append((round(t, 3), aspects))
        return out


def up(payload: str) -> str:
    return f"<{payload},{crc8(payload):02X}"


# --------------------------------------------------------------------------- #
# protocol
# --------------------------------------------------------------------------- #
def test_crc8_is_crc8_atm():
    assert crc8("123456789") == 0xF4          # the published check value of CRC-8/ATM
    assert crc8("") == 0


def test_lamp_frames_round_trip_and_follow_the_fsm():
    assert aspect_chars("green", 1, 2) == "RG"
    assert aspect_chars("yellow", 1, 2) == "RA"       # amber on the phase being ended
    assert aspect_chars("all_red", 0, 2) == "RR"
    frame = encode_lamps([("green", 0), ("yellow", 1), ("all_red", 0), ("green", 1)], 2)
    assert frame == f">L,GRRARRRG,{crc8('L,GRRARRRG'):02X}\n".encode()
    assert decode_lamps(frame.decode()) == "GRRARRRG"
    assert decode_lamps(frame.decode().replace("GRR", "GRG")) is None       # CRC mismatch


def test_text_frames_are_sanitised_for_the_oled():
    assert sanitize_text("wait, 23 <s> ×") == "wait; 23 (s) ?"
    assert len(sanitize_text("x" * 40)) == 21
    frame = encode_text(2, "Fixed wait 41.0 s").decode()
    assert decode_text(frame) == (2, "Fixed wait 41.0 s")
    assert ">" not in frame[1:] and frame.count(",") == 3
    with pytest.raises(ValueError):
        encode_text(4, "no such row")


@pytest.mark.parametrize("event", [
    UplinkEvent("detect", tls="J0_0", approach="N"),
    UplinkEvent("queue", tls="J0_0", approach="W", count=3),
    UplinkEvent("emergency", corridor="ew", vehicle="ambulance"),
    UplinkEvent("emergency", corridor="ns"),
    UplinkEvent("control", action="toggle"),
    UplinkEvent("heartbeat", uptime_ms=123456),
    UplinkEvent("info", text="atsc-node 1.2.0 J0_0 heads=16"),
])
def test_uplink_events_round_trip(event):
    assert decode_uplink(encode_uplink(event).decode()) == event


@pytest.mark.parametrize("line", [
    "", "garbage", "<D,J0_0,N,00", "<D,J0_0,X," + f"{crc8('D,J0_0,X'):02X}",
    up("E,diagonal"), up("E,ew,Ambulance!"), up("C,reboot"), up("Q,J0_0,N,lots"),
    up("Z,1"), ">L,GRGRGRGR," + f"{crc8('L,GRGRGRGR'):02X}",
])
def test_uplink_noise_is_ignored(line):
    assert decode_uplink(line) is None


# --------------------------------------------------------------------------- #
# the firmware's logic, compiled for the PC and checked against the Python side
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def core(tmp_path_factory):
    cxx = next((c for c in (os.environ.get("CXX"), "c++", "g++", "clang++") if c and shutil.which(c)), None)
    if cxx is None:
        pytest.skip("no C++ compiler")
    exe = tmp_path_factory.mktemp("fw") / "core_harness"
    subprocess.run([cxx, "-std=c++11", "-O1", "-Wall", "-Wextra", "-Werror", "-I", str(FIRMWARE),
                    "-o", str(exe), str(ROOT / "tests" / "firmware" / "core_harness.cpp")],
                   check=True, capture_output=True, text=True)

    def run(*commands):
        out = subprocess.run([str(exe)], input="".join("\t".join(c) + "\n" for c in commands),
                             capture_output=True, text=True, check=True, timeout=30).stdout
        return out.splitlines()
    return run


def test_firmware_crc_matches_python(core):
    rng = random.Random(7)
    texts = ["L,GRGRGRGR", "D,J0_0,N", "123456789", ""] + [
        "".join(chr(rng.randrange(32, 127)) for _ in range(rng.randrange(1, 40))).replace("\t", " ")
        for _ in range(200)]
    assert core(*[("crc", t) for t in texts]) == [f"{crc8(t):02X}" for t in texts]


def test_firmware_accepts_exactly_what_python_sends(core):
    good = [encode_lamps([("green", 0), ("yellow", 1), ("all_red", 0), ("green", 1)], 2),
            encode_lamps([("all_red", 0)] * 4, 2),
            encode_text(0, "AI lamps 12:34 medium"), encode_text(3, ""),
            encode_text(1, "x" * 30)]
    lines = [g.decode().rstrip("\n") for g in good]
    assert core(*[("parse", line) for line in lines]) == [
        "LAMPS GRRARRRG", "LAMPS RRRRRRRR", "TEXT 0 AI lamps 12:34 medium", "TEXT 3 ", "TEXT 1 " + "x" * 21]
    payload = "L,GRGRGRGR"
    bad = [
        f">{payload},{crc8(payload) ^ 1:02X}",               # wrong CRC
        f">L,GRGRGRG,{crc8('L,GRGRGRG'):02X}",              # 7 aspects
        f">L,GRGRGRGX,{crc8('L,GRGRGRGX'):02X}",            # not R/A/G
        f">T,4,hi,{crc8('T,4,hi'):02X}",                    # no row 4
        f">T,0,{'y' * 22},{crc8('T,0,' + 'y' * 22):02X}",   # too long
        f"<{payload},{crc8(payload):02X}",                   # wrong direction
        f">{payload},{crc8(payload):02X}0",                 # three CRC digits
        "", ">", ">L", "nonsense",
    ]
    assert core(*[("parse", b) for b in bad]) == ["INVALID"] * len(bad)
    # lower-case CRC digits are accepted, as by the Python decoder
    assert core(("parse", f">{payload},{crc8(payload):02x}")) == ["LAMPS GRGRGRGR"]


def _image(aspects: str, heads: int = 16) -> int:
    """The wiring rule, independently: LED head*3+colour, heads N E S W per junction."""
    value = 0
    for h in range(heads):
        junction, side = divmod(h, 4)
        letter = aspects[junction * 2 + (0 if side in (0, 2) else 1)]
        value |= 1 << (h * 3 + "RAG".index(letter))
    return value


def test_firmware_lamp_wiring_rule(core):
    rng = random.Random(3)
    cases = ["GRARRAAG", "RRRRRRRR", "GRGRGRGR"] + ["".join(rng.choice("RAG") for _ in range(8)) for _ in range(50)]
    cmds = [("image", a, "16") for a in cases] + [("image", "GRARRAAG", "4"), ("image", "GRARRAAG", "8")]
    expected = [f"{_image(a):012x}" for a in cases] + [f"{_image('GRARRAAG', 4):012x}",
                                                       f"{_image('GRARRAAG', 8):012x}"]
    assert core(*cmds) == expected
    assert core(("image", "GRARRAAX", "16"), ("image", "GRA", "16")) == ["BAD", "BAD"]


def test_firmware_builds_the_lines_python_reads(core):
    payloads = ["D,J0_0,N", "Q,J0_0,W,3", "E,ew,ambulance", "C,toggle", "H,4294967295",
                "I,atsc-node 1.2.0 J0_0 heads=16"]
    lines = core(*[("uplink", p) for p in payloads])
    assert lines == [up(p) for p in payloads]
    assert all(decode_uplink(line) is not None for line in lines)


def test_firmware_line_reader_resynchronises(core):
    stream = (b"garbage>L,RRRRRRRR,00\r\n" b"\n" + b"x" * 60 + b"\n"
              b">L,GRGR>T,0,hi,AB\n")
    assert core(("reader", stream.hex())) == ["LINE >L,RRRRRRRR,00", "LINE >T,0,hi,AB", "END"]


def test_firmware_debounce_and_link_watchdog(core):
    # a 5 ms flicker is ignored; a level held for 20 ms is accepted once
    assert core(("debounce", "15", "0:0,10:1,15:0,30:1,44:1,45:1,60:1,70:0,90:0")) == [
        "CHANGES 45:1 90:0"]
    # lamp frames start the link, a 3 s silence loses it, text frames alone cannot start it
    assert core(("link", "3000", "T@0,P@100,L@200,P@3000,T@3100,P@5000,P@6101,L@7000")) == [
        "START@200 LOST@6101 START@7000 STATE 1"]


# --------------------------------------------------------------------------- #
# bridge
# --------------------------------------------------------------------------- #
def test_bridge_sends_changes_and_a_heartbeat_only():
    clock = FakeClock()
    link = TimedLoopback(clock)
    bridge = HardwareBridge(link, 2, TLS, heartbeat_s=1.0, clock=clock)
    state = [("green", 0)] * 4
    for i in range(20):                       # 2 s of identical states, every 0.1 s
        clock.t = i * 0.1
        bridge.publish(state)
    assert [round(t, 1) for t, _ in link.log] == [0.0, 1.0]
    clock.t = 2.05
    bridge.publish([("yellow", 0)] + state[1:])
    assert decode_lamps(link.log[-1][1]) == "ARGRGRGR"


def test_bridge_text_rows_and_node_restart():
    link = LoopbackLink()
    bridge = HardwareBridge(link, 2, TLS)
    assert bridge.send_text(0, "AI lamps 00:30 medium") and not bridge.send_text(0, "AI lamps 00:30 medium")
    assert bridge.text_pending(["AI lamps 00:30 medium", "x"]) == 1
    bridge.publish([("green", 0)] * 4)
    n = len(link.written)
    link.feed(up("I,atsc-node 1.2.0 J0_0 heads=16"))        # the board rebooted
    assert bridge.poll() == []
    assert bridge.node_info == "atsc-node 1.2.0 J0_0 heads=16"
    assert bridge.text_pending(["AI lamps 00:30 medium"]) == 0  # everything goes out again
    bridge.publish([("green", 0)] * 4)
    assert len(link.written) == n + 1


def test_bridge_filters_the_uplink():
    clock = FakeClock()
    link = LoopbackLink()
    bridge = HardwareBridge(link, 2, TLS, detect_cooldown_s=0.25, clock=clock)
    for line in [up("D,J0_0,N"), up("D,J0_0,N"), up("D,J9_9,N"), up("H,5000"), "noise",
                 up("Q,J0_0,S,3"), up("E,ns,police"), up("C,toggle")]:
        link.feed(line)
    kinds = [(e.kind, e.tls or e.corridor or e.action) for e in bridge.poll()]
    assert kinds == [("detect", "J0_0"), ("queue", "J0_0"), ("emergency", "ns"), ("control", "toggle")]
    s = bridge.stats
    assert (s["cooldown_drops"], s["unknown_tls"], s["heartbeats"], s["lines_rejected"]) == (1, 1, 1, 1)
    clock.t = 0.3
    link.feed(up("D,J0_0,N"))
    assert [e.kind for e in bridge.poll()] == ["detect"]


def test_bridge_close_leaves_all_red():
    link = LoopbackLink()
    bridge = HardwareBridge(link, 2, TLS)
    bridge.publish([("green", 1)] * 4)
    bridge.close()
    assert link.last_aspects() == "RRRRRRRR" and not link.connected


# --------------------------------------------------------------------------- #
# mirror: real-time playback of bursty simulation output
# --------------------------------------------------------------------------- #
def _mirror(rate=1.0):
    clock = FakeClock()
    link = TimedLoopback(clock)
    bridge = HardwareBridge(link, 2, TLS, heartbeat_s=1.0, clock=clock)
    mirror = HardwareMirror(bridge, decision_s=5.0, clock=clock)
    mirror.set_rate(rate)
    return clock, link, mirror


def _run(clock, mirror, seconds, dt=0.02):
    end = clock.t + seconds
    while clock.t < end - 1e-9:
        mirror.step()
        clock.t = round(clock.t + dt, 6)


def _decision(mirror, t0, junction0):
    """Push one 5 s decision: junction 0 goes through the given states, the rest stay green."""
    for k, state in enumerate(junction0):
        mirror.push(t0 + k, [state] + [("green", 0)] * 3)


def test_mirror_plays_amber_and_all_red_for_their_real_duration():
    clock, link, mirror = _mirror(rate=1.0)
    _decision(mirror, 100, [("yellow", 0)] * 3 + [("all_red", 0)] * 2)
    _run(clock, mirror, 4.9)
    clock.t = 5.0                                       # the next decision arrives on time
    _decision(mirror, 105, [("green", 1)] * 5)
    _run(clock, mirror, 2.0)
    assert link.lamp_changes() == [(0.0, "ARGRGRGR"), (3.0, "RRGRGRGR"), (5.0, "RGGRGRGR")]


def test_mirror_follows_the_dashboard_speed():
    clock, link, mirror = _mirror(rate=25.0)            # the dashboard's default: 25x
    _decision(mirror, 100, [("yellow", 0)] * 3 + [("all_red", 0)] * 2)
    _run(clock, mirror, 0.3, dt=0.01)
    assert link.lamp_changes() == [(0.0, "ARGRGRGR"), (0.12, "RRGRGRGR")]   # 3 s / 25


def test_mirror_gives_the_first_second_its_full_length():
    # an episode's opening aspects are published at its start time and then again, changed
    # by the first decision, for that same second: the second version must last 1 s
    clock, link, mirror = _mirror(rate=1.0)
    mirror.push(30, [("green", 0)] * 4)
    _run(clock, mirror, 5.0)                             # real time: the first decision comes 5 s later
    _decision(mirror, 30, [("yellow", 0)] * 3 + [("all_red", 0)] * 2)
    _run(clock, mirror, 4.0)
    assert link.lamp_changes() == [(0.0, "GRGRGRGR"), (5.0, "ARGRGRGR"), (8.0, "RRGRGRGR")]


def test_mirror_does_not_skip_seconds_after_a_stall():
    # opening the port blocks for the Uno's boot; the seconds queued meanwhile must still
    # each get their full length afterwards
    clock = FakeClock()
    link = TimedLoopback(clock)
    bridge = HardwareBridge(NullLink(), 2, TLS, clock=clock)

    def reopen():
        clock.t += 2.0
        return link

    mirror = HardwareMirror(bridge, reopen=reopen, clock=clock)
    _decision(mirror, 100, [("yellow", 0)] * 3 + [("all_red", 0)] * 2)
    _run(clock, mirror, 6.0)
    (t_amber, a), (t_red, b) = link.lamp_changes()[:2]
    assert (a, b) == ("ARGRGRGR", "RRGRGRGR") and t_red - t_amber == pytest.approx(3.0, abs=0.03)


def test_mirror_restarts_with_a_new_episode_and_skips_a_stale_backlog():
    clock, link, mirror = _mirror(rate=200.0)
    for t in range(100, 140):
        mirror.push(t, [("green", t % 2)] * 4)
    mirror.step()
    clock.t = 0.05
    mirror.set_rate(1.0)                                 # speed lowered to real time
    for t in range(140, 180):
        mirror.push(t, [("green", t % 2)] * 4)
    mirror.step()
    assert mirror.lag_s() <= 5.0 and mirror.skipped > 20
    mirror.push(30, [("all_red", 0)] * 4)                # reset: time goes backwards
    clock.t = 0.1
    mirror.step()
    assert link.lamp_changes()[-1] == (0.1, "RRRRRRRR")


def test_mirror_hands_over_events_text_and_reconnects():
    clock = FakeClock()
    bridge = HardwareBridge(NullLink(), 2, TLS, clock=clock)
    fresh = LoopbackLink()
    attempts = []

    def reopen():
        attempts.append(clock.t)
        if len(attempts) == 1:
            raise HardwareUnavailable("no USB-serial adapter found")
        return fresh

    mirror = HardwareMirror(bridge, reopen=reopen, reconnect_s=2.0, clock=clock)
    mirror.step()
    assert mirror.state() == 0 and mirror.last_error.startswith("no USB")
    clock.t = 1.0
    mirror.step()
    assert attempts == [0.0]                             # retries only every 2 s
    clock.t = 2.0
    mirror.step()
    assert bridge.link is fresh and mirror.reconnects == 1 and mirror.last_error is None
    assert mirror.state() == 1                           # open, but the node has not spoken
    fresh.feed(up("H,1000"))
    fresh.feed(up("D,J0_0,E"))
    mirror.set_text(["a", "b"])
    for _ in range(4):
        mirror.step()
    assert mirror.state() == 2
    assert [(e.kind, e.approach) for e in mirror.take_events()] == [("detect", "E")]
    assert mirror.take_events() == []
    assert [decode_text(w.decode()) for w in fresh.written] == [(0, "a"), (1, "b"), (2, ""), (3, "")]


# --------------------------------------------------------------------------- #
# the simulator side
# --------------------------------------------------------------------------- #
def test_detected_vehicles_never_shift_the_simulated_traffic():
    cfg = load_config(None)
    plain = MultiAgentTrafficEnv(cfg, backend_name="mini")
    fed = MultiAgentTrafficEnv(cfg, backend_name="mini")
    plain.reset("high", 11)
    fed.reset("high", 11)
    for k in range(30):
        if k in (3, 9):
            assert fed.feed_detection("J0_0", "N", 2) == 2
        actions = {iid: (k // 3) % 2 for iid in plain.possible_agents}
        plain.step(actions)
        fed.step(actions)
    for a, b in ((plain.backend._rng, fed.backend._rng), (plain.backend._kind_rng, fed.backend._kind_rng)):
        assert a.bit_generator.state == b.bit_generator.state     # the same draws, in order
    assert fed.backend._veh_counter == plain.backend._veh_counter + 4
    assert fed.feed_detection("J9_9", "N") == 0 and fed.feed_detection("J0_0", "N", 0) == 0


def test_settings_merge_config_and_command_line():
    cfg = load_config(None)
    s = hardware_settings(cfg)
    assert s["enabled"] is False and s["port"] == "auto" and s["mirror"] == "rl"
    s = hardware_settings(cfg, {"enabled": True, "port": "COM5", "mirror": "fixed", "realtime": None})
    assert (s["enabled"], s["port"], s["mirror"], s["realtime"]) == (True, "COM5", "ft", True)


# --------------------------------------------------------------------------- #
# the dashboard in hardware mode, over the in-memory link
# --------------------------------------------------------------------------- #
@pytest.fixture
def hw_session():
    from atsc.dashboard.session import DashboardSession
    s = DashboardSession(None, hardware={"enabled": True, "port": "loopback"})
    s.play()
    yield s
    s.close()


def _until(session, cond, timeout=5.0, period=0.02):
    """Tick ``session`` every ``period`` seconds until ``cond()`` holds. The playback rate
    the board is told assumes the real tick (``session.tick_s``): pass that whenever the
    lamps' timing matters, as ticking faster runs the simulation ahead of the lamps."""
    end = time.monotonic() + timeout
    next_at = time.monotonic()
    while time.monotonic() < end:
        session.tick()
        if cond():
            return True
        next_at += period
        time.sleep(max(0.0, next_at - time.monotonic()))
    return False


def test_hardware_mode_starts_at_real_time_and_says_so(hw_session):
    s = hw_session
    assert s.rt_speed == pytest.approx(0.04) and s.speed == s.rt_speed
    assert s.speeds[:2] == [0.04, 0.08] and 1 in s.speeds
    hello = s.hello()
    assert hello["rt"] == s.rt_speed and hello["hw"] == {"port": "loopback", "mirror": "rl", "tls": "J0_0"}
    frame = s.snapshot()
    assert frame["hw"][1:] == [0, 0]
    assert s.info()["hardware"]["transport"] == "loopback"


def test_board_events_reach_both_grids(hw_session):
    s = hw_session
    s.pause()                                            # hold the traffic still while we look
    link = s.hw.bridge.link
    rl, ft = s.rl.env.backend, s.ft.env.backend
    n0 = rl._veh_counter
    link.feed(up("D,J0_0,N"))
    link.feed(up("D,J9_9,N"))                            # not a junction of this grid: ignored
    assert _until(s, lambda: s._hw_counts["cars"] == 1)
    assert rl._veh_counter == ft._veh_counter == n0 + 1  # the race stays fair
    assert any(v.id == f"v{n0 + 1}" for v in rl._queues[("J0_0", "N")])

    link.feed(up("Q,J0_0,W,3"))                          # a car parked on the queue sensor
    assert _until(s, lambda: s._hw_counts["queues"] == 1)
    assert rl.queue("J0_0", "W") >= 3

    link.feed(up("E,ns,police"))
    assert _until(s, lambda: s.rl.active_emergencies() == 1)
    assert s.ft.active_emergencies() == 1
    assert any(v.kind == "police" for q in rl._queues.values() for v in q)
    link.feed(up("E,ew"))                                # no vehicle named: the corridor's default
    assert _until(s, lambda: s.rl.active_emergencies() == 2)
    assert s._hw_counts["emergencies"] == 2 and s.snapshot()["hw"][2] == 2

    link.feed(up("C,toggle"))                            # remote button D: pause / resume
    assert _until(s, lambda: s.playing)
    link.feed(up("C,toggle"))
    assert _until(s, lambda: not s.playing)


def test_lamps_follow_the_rl_grid_and_the_oled_gets_figures(hw_session):
    s = hw_session
    link = s.hw.bridge.link
    def chars(aspects):
        return "".join(aspect_chars(st, ph, 2) for st, ph in aspects)
    seen = [chars(s.rl.env.signal_aspects())]            # already pushed when the board attached
    s.rl.recorder.set_tap(lambda t, aspects: (seen.append(chars(aspects)), s.hw.push(t, aspects)))
    s.set_speed(8)                                       # 200 simulated seconds per real second
    end = time.monotonic() + 1.5
    while time.monotonic() < end:
        s.tick()
        time.sleep(0.02)
    sent = [decode_lamps(w.decode()) for w in list(link.written)]
    sent = [a for a in sent if a]
    assert len(set(sent)) >= 3 and set(sent) <= set(seen)
    texts = [decode_text(w.decode()) for w in list(link.written)]
    rows = {t[0]: t[1] for t in texts if t}
    assert rows[0].startswith("AI lamps") and rows[1].startswith("AI wait") and rows[2].startswith("Fixed wait")
    s.close()
    assert link.last_aspects() == "RRRRRRRR" and s.hw is None
    s.close()                                            # twice is fine


def test_hardware_off_runs_none_of_it():
    code = r"""
import sys
sys.path.insert(0, "src")
from atsc.dashboard.session import DashboardSession
s = DashboardSession()
s.play()
frames = [s.tick() for _ in range(30)]
hello = s.hello()
assert "rt" not in hello and "hw" not in hello and 0.04 not in s.speeds
assert not any(f and "hw" in f for f in frames)
assert "hardware" not in s.info()
assert not [m for m in sys.modules if m.startswith("atsc.hw")], "atsc.hw was imported"
s.close()
print("ok")
"""
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().endswith("ok")


# --------------------------------------------------------------------------- #
# the real firmware on a simulated ATmega328P, driven by the real dashboard
# --------------------------------------------------------------------------- #
def _virtual_board():
    sys.path.insert(0, str(ROOT / "tools" / "virtual_board"))
    try:
        import virtual_board
    finally:
        sys.path.pop(0)
    if not virtual_board.simulator_available():
        pytest.skip("needs avr-gcc, the Arduino AVR core and simavr (Linux)")
    pytest.importorskip("serial")
    return virtual_board


def test_virtual_board_end_to_end():
    vb = _virtual_board()
    from atsc.dashboard.session import DashboardSession

    with vb.VirtualBoard.start() as board:
        # boot: lamp test (every head red, amber, green, red), then flashing amber
        _wait(lambda: len(board.lamps) >= 6, 6.0)
        heads = [vb.image_to_heads(img) for _t, img in board.lamps[:6]]
        assert [h[0] for h in heads[1:5]] == ["R", "A", "G", "R"]
        assert all(len(set(h)) == 1 for h in heads[1:5])
        assert vb.heads_to_aspects(heads[5]) in ("AAAAAAAA", None)

        s = DashboardSession(None, hardware={"enabled": True, "port": board.port})
        try:
            s.set_speed(0.2)                             # 5x real time keeps the test short
            s.play()
            seen = []
            orig = s.hw.push
            s.rl.recorder.set_tap(lambda t, a: (seen.append("".join(aspect_chars(st, ph, 2) for st, ph in a)),
                                                orig(t, a)))
            tick = s.tick_s
            assert _until(s, lambda: s.hw.state() == 2, timeout=8.0, period=tick), s.hw.snapshot()
            board.press("arrive", "N")
            board.press("button", "B")
            assert _until(s, lambda: s._hw_counts["cars"] == 1 and s.rl.active_emergencies() == 1,
                          timeout=6.0, period=tick)
            _until(s, lambda: False, timeout=6.0, period=tick)   # let the lamps run a while
            assert s.hw.skipped == 0                     # the lamps kept up, second by second
            assert s.info()["hardware"]["node"].startswith("atsc-node 1.2.0")
            rows = board.oled_text()
            assert rows[0] == "ATSC node 1.2.0 J0_0" and rows[1] == "PC: live"
            assert rows[3] == "B: POLICE CAR" and rows[5].startswith("AI wait")
        finally:
            s.close()
        _wait(lambda: board.aspects() == "RRRRRRRR", 3.0)  # the all-red sent by close()
        shown = []
        for _t, img in board.lamps:
            a = vb.heads_to_aspects(vb.image_to_heads(img))
            if a and (not shown or shown[-1] != a):
                shown.append(a)
        live = shown[shown.index(seen[0]):] if seen and seen[0] in shown else []
        assert len(live) >= 4
        dedup = [a for i, a in enumerate(seen) if i == 0 or seen[i - 1] != a]
        # every picture the board showed came from the RL grid, in the same order
        assert live[:-1] == dedup[:len(live) - 1]
        assert live[-1] == "RRRRRRRR"                    # close(): all red
        _wait(lambda: vb.heads_to_aspects(vb.image_to_heads(board.lamps[-1][1])) in ("AAAAAAAA", None), 5.0)


def _wait(cond, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False
