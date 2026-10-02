"""Hardware-in-the-loop layer: drive real signal heads and read real detectors.

The package is a **leaf**: it imports nothing from :mod:`atsc.envs`, :mod:`atsc.sim` or
:mod:`atsc.rl`, so it can be unit-tested on its own and cannot create an import cycle.
Everything is optional at runtime — with ``hardware.enabled: false`` in ``config.yaml``
(the shipped default) not one line of this package runs, and ``pyserial`` need not be
installed.

Typical use from the session layer::

    bridge = make_hardware(cfg, valid_tls=env.possible_agents, n_phases=env.n_phases)
    ...
    bridge.publish((fsm.state.value, fsm.current) for fsm in fsms)   # once per sim second
    for event in bridge.poll():                                     # once per decision
        ...
    bridge.close()
"""
from __future__ import annotations

from typing import Sequence

from atsc.hw.bridge import HardwareBridge
from atsc.hw.link import (
    HardwareUnavailable,
    Link,
    LoopbackLink,
    NullLink,
    SerialLink,
    autodetect_port,
    open_link,
)
from atsc.hw.protocol import (
    AMBER,
    ASPECTS,
    GREEN,
    RED,
    UplinkEvent,
    aspect_chars,
    crc8,
    decode_lamps,
    decode_uplink,
    encode_lamps,
    encode_uplink,
)

__all__ = [
    "AMBER", "ASPECTS", "GREEN", "RED",
    "HardwareBridge", "HardwareUnavailable", "Link", "LoopbackLink", "NullLink",
    "SerialLink", "UplinkEvent", "aspect_chars", "autodetect_port", "crc8",
    "decode_lamps", "decode_uplink", "encode_lamps", "encode_uplink", "make_hardware",
    "open_link",
]
def make_hardware(
    cfg,
    valid_tls: Sequence[str],
    n_phases: int,
    quiet: bool = False,
) -> HardwareBridge:
    """Build the bridge described by the ``hardware:`` block of ``config.yaml``.

    Reads every value through :meth:`Config.get_path`, so a config file with no
    ``hardware:`` block at all is valid and yields a disabled bridge. A board that cannot
    be opened is reported once and then degraded to :class:`NullLink` — a missing cable
    must never stop a demo or a benchmark run.
    """
    enabled = bool(cfg.get_path("hardware.enabled", False))
    if not enabled:
        return HardwareBridge(NullLink(), n_phases, valid_tls)

    port = str(cfg.get_path("hardware.port", "auto"))
    baud = int(cfg.get_path("hardware.baud", 115200))
    settle_s = float(cfg.get_path("hardware.settle_s", 1.5))
    heartbeat_s = float(cfg.get_path("hardware.heartbeat_s", 1.0))
    cooldown_s = float(cfg.get_path("hardware.detect_cooldown_s", 0.25))

    try:
        link: Link = open_link(port, baud, settle_s)
    except HardwareUnavailable as exc:
        if not quiet:
            print(f"[hardware] {exc}")
            print("[hardware] continuing in software-only mode")
        link = NullLink()
    else:
        if not quiet:
            print(f"[hardware] {link.describe()} at {baud} baud")

    return HardwareBridge(
        link, n_phases, valid_tls,
        heartbeat_s=heartbeat_s,
        detect_cooldown_s=cooldown_s,
    )
