"""Hardware-in-the-loop layer: drive real signal heads and read real detectors.

The package is a **leaf**: it imports nothing from :mod:`atsc.envs`, :mod:`atsc.sim` or
:mod:`atsc.agents`, so it can be unit-tested on its own and cannot create an import cycle.
Everything is optional at runtime — with ``hardware.enabled: false`` in ``config.yaml``
(the shipped default) and no ``--hardware`` flag, nothing imports this package, and
``pyserial`` need not be installed.

The board is an Arduino Uno running ``firmware/atsc_signal_node`` (wiring and the build
in ``docs/HARDWARE.md``). The live dashboard drives it through a
:class:`~atsc.hw.mirror.HardwareMirror`::

    settings = hardware_settings(cfg, {"enabled": True, "port": "COM5"})
    mirror = make_mirror(settings, valid_tls=env.possible_agents, n_phases=env.n_phases)
    env.set_signal_sink(lambda aspects: mirror.push(env.backend.time, aspects))
    mirror.start()
    ...
    for event in mirror.take_events():          # detections, remote buttons
        ...
    mirror.close()
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from atsc.hw.bridge import HardwareBridge
from atsc.hw.link import (
    HardwareUnavailable,
    Link,
    LoopbackLink,
    NullLink,
    SerialLink,
    autodetect_port,
    list_ports,
    open_link,
)
from atsc.hw.mirror import HardwareMirror
from atsc.hw.protocol import (
    AMBER,
    ASPECTS,
    GREEN,
    RED,
    TEXT_COLS,
    TEXT_ROWS,
    UplinkEvent,
    aspect_chars,
    crc8,
    decode_lamps,
    decode_text,
    decode_uplink,
    encode_lamps,
    encode_text,
    encode_uplink,
    sanitize_text,
)

__all__ = [
    "AMBER", "ASPECTS", "GREEN", "RED", "TEXT_COLS", "TEXT_ROWS",
    "HardwareBridge", "HardwareMirror", "HardwareUnavailable", "Link", "LoopbackLink",
    "NullLink", "SerialLink", "UplinkEvent", "aspect_chars", "autodetect_port", "crc8",
    "decode_lamps", "decode_text", "decode_uplink", "encode_lamps", "encode_text",
    "encode_uplink", "hardware_settings", "list_ports", "make_hardware", "make_mirror",
    "open_link", "sanitize_text",
]

_DEFAULTS: Dict[str, Any] = {
    "enabled": False, "port": "auto", "baud": 115200, "settle_s": 2.0, "heartbeat_s": 1.0,
    "detect_cooldown_s": 0.25, "lamps": True, "detectors": True, "rf_preemption": True,
    "instrumented_tls": "J0_0", "mirror": "rl", "realtime": True,
}


def hardware_settings(cfg, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The ``hardware:`` block of ``config.yaml`` with defaults filled in and ``overrides``
    (from the command line) applied on top. ``mirror`` is normalised to ``rl`` or ``ft``."""
    out = dict(_DEFAULTS)
    for key in _DEFAULTS:
        value = cfg.get_path(f"hardware.{key}", None) if cfg is not None else None
        if value is not None:
            out[key] = value
    for key, value in (overrides or {}).items():
        if value is not None:
            out[key] = value
    mirror = str(out["mirror"]).lower()
    out["mirror"] = "ft" if mirror in ("ft", "fixed", "fixed_time", "fixed-time") else "rl"
    out["enabled"] = bool(out["enabled"])
    return out


def make_mirror(settings: Dict[str, Any], valid_tls: Sequence[str], n_phases: int,
                decision_s: float = 5.0) -> HardwareMirror:
    """A started-later :class:`HardwareMirror` for ``settings`` (see :func:`hardware_settings`).

    The port is opened by the mirror's own thread - immediately after :meth:`start`, and
    again every two seconds while it fails - so a board plugged in after the dashboard
    started, or unplugged and plugged back, connects by itself and never delays a tick.
    """
    port = str(settings.get("port", "auto"))
    baud = int(settings.get("baud", 115200))
    settle_s = float(settings.get("settle_s", 2.0))
    bridge = HardwareBridge(NullLink(), n_phases, valid_tls,
                            heartbeat_s=float(settings.get("heartbeat_s", 1.0)),
                            detect_cooldown_s=float(settings.get("detect_cooldown_s", 0.25)))
    if port.strip().lower() == "loopback":
        bridge.link = LoopbackLink()
        return HardwareMirror(bridge, decision_s=decision_s)
    return HardwareMirror(bridge, decision_s=decision_s,
                          reopen=lambda: open_link(port, baud, settle_s))


def make_hardware(
    cfg,
    valid_tls: Sequence[str],
    n_phases: int,
    quiet: bool = False,
) -> HardwareBridge:
    """Build a bare bridge described by the ``hardware:`` block of ``config.yaml``.

    Reads every value through :meth:`Config.get_path`, so a config file with no
    ``hardware:`` block at all is valid and yields a disabled bridge. A board that cannot
    be opened is reported once and then degraded to :class:`NullLink` — a missing cable
    must never stop a demo or a benchmark run. (The dashboard uses :func:`make_mirror`.)
    """
    settings = hardware_settings(cfg)
    if not settings["enabled"]:
        return HardwareBridge(NullLink(), n_phases, valid_tls)

    try:
        link: Link = open_link(str(settings["port"]), int(settings["baud"]),
                               float(settings["settle_s"]))
    except HardwareUnavailable as exc:
        if not quiet:
            print(f"[hardware] {exc}")
            print("[hardware] continuing in software-only mode")
        link = NullLink()
    else:
        if not quiet:
            print(f"[hardware] {link.describe()} at {settings['baud']} baud")

    return HardwareBridge(
        link, n_phases, valid_tls,
        heartbeat_s=float(settings["heartbeat_s"]),
        detect_cooldown_s=float(settings["detect_cooldown_s"]),
    )
