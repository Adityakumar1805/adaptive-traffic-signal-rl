"""Vehicle catalogue shared by the simulator, the dashboard protocol and the browser sprites.

Every vehicle in the built-in simulator carries a ``kind``. Kinds are **cosmetic**: the
point-queue model treats every vehicle identically (same saturation flow, same link travel
time), and the kind of each spawned vehicle is drawn from its own random stream
(``make_rng(seed, "kind")`` in :mod:`atsc.sim.mini_backend`). Changing the traffic mix
therefore changes what the dashboard draws and nothing else - arrivals, routes, queues and
every published benchmark number stay bit-for-bit identical (``tests/test_vehicles.py``
proves it).

Emergency kinds are the exception that *does* matter to the controllers: they are injected on
demand, they set the emergency flag in the observation, and they trigger the rule-based
preemption in :mod:`atsc.control.emergency` (always through the safety FSM).

The order of :data:`VEHICLE_TYPES` defines the integer / letter codes used on the wire, so it
must only ever be appended to.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

import numpy as np


@dataclass(frozen=True)
class VehicleType:
    """Static description of one vehicle kind (dimensions are used for drawing only)."""

    kind: str
    label: str
    group: str
    length_m: float
    width_m: float
    emergency: bool = False


#: Wire order. Append only - the index is the protocol code of the kind.
VEHICLE_TYPES: Tuple[VehicleType, ...] = (
    VehicleType("bike", "Motorcycle", "two-wheeler", 2.1, 0.80),
    VehicleType("scooter", "Scooter", "two-wheeler", 1.9, 0.75),
    VehicleType("bicycle", "Bicycle", "two-wheeler", 1.8, 0.60),
    VehicleType("cycle_rickshaw", "Cycle rickshaw", "three-wheeler", 2.8, 1.20),
    VehicleType("auto", "Auto-rickshaw", "three-wheeler", 2.7, 1.35),
    VehicleType("erick", "E-rickshaw", "three-wheeler", 2.8, 1.25),
    VehicleType("car", "Car", "car", 4.0, 1.70),
    VehicleType("taxi", "Taxi", "car", 4.1, 1.65),
    VehicleType("suv", "SUV", "car", 4.6, 1.85),
    VehicleType("van", "Van", "car", 3.8, 1.55),
    VehicleType("tempo", "Tempo / mini-truck", "goods", 4.2, 1.60),
    VehicleType("truck", "Truck", "goods", 8.0, 2.45),
    VehicleType("tractor", "Tractor + trolley", "goods", 7.0, 2.10),
    VehicleType("bus", "City bus", "bus", 11.0, 2.55),
    VehicleType("school_bus", "School bus", "bus", 9.0, 2.40),
    VehicleType("ambulance", "Ambulance", "emergency", 5.2, 2.00, emergency=True),
    VehicleType("police", "Police car", "emergency", 4.6, 1.80, emergency=True),
    VehicleType("fire", "Fire engine", "emergency", 9.5, 2.50, emergency=True),
)

_BY_KIND: Dict[str, VehicleType] = {v.kind: v for v in VEHICLE_TYPES}

#: Every kind, in wire order.
KINDS: Tuple[str, ...] = tuple(v.kind for v in VEHICLE_TYPES)
#: Kinds that ordinary traffic can be drawn from.
REGULAR_KINDS: Tuple[str, ...] = tuple(v.kind for v in VEHICLE_TYPES if not v.emergency)
#: Kinds that can be injected as emergency vehicles.
EMERGENCY_KINDS: Tuple[str, ...] = tuple(v.kind for v in VEHICLE_TYPES if v.emergency)

#: Default Indian urban mix (relative weights, normalised at load time): two-wheelers are about
#: half of all vehicles, autos and e-rickshaws the next big block, buses and trucks are rare.
DEFAULT_MIX: Dict[str, float] = {
    "bike": 30.0, "scooter": 22.0, "bicycle": 3.0, "cycle_rickshaw": 1.0,
    "auto": 10.0, "erick": 4.0,
    "car": 15.0, "taxi": 3.0, "suv": 4.0, "van": 2.0,
    "tempo": 2.5, "truck": 1.2, "tractor": 0.5,
    "bus": 1.3, "school_bus": 0.5,
}

#: Defaults for the emergency types offered by the dashboard. The ambulance has no speed
#: factor of its own: it always uses ``emergency.vtype_speed_factor`` because the published
#: benchmark injects exactly that ambulance.
DEFAULT_EMERGENCY_TYPES: Dict[str, Dict[str, object]] = {
    "ambulance": {"label": "Ambulance", "corridor": "ew"},
    "police": {"label": "Police car", "corridor": "ns", "speed_factor": 1.4},
    "fire": {"label": "Fire engine", "corridor": "ew", "speed_factor": 1.15},
}

CORRIDORS: Tuple[str, ...] = ("ew", "ns")


def get(kind: str) -> VehicleType:
    """Return the :class:`VehicleType` for ``kind`` (``KeyError`` with the valid list)."""
    try:
        return _BY_KIND[kind]
    except KeyError:
        raise KeyError(f"unknown vehicle kind '{kind}'. Known kinds: {list(KINDS)}") from None


def code_of(kind: str) -> int:
    """Wire code (index) of a kind."""
    return KINDS.index(kind)


def is_emergency(kind: str) -> bool:
    v = _BY_KIND.get(kind)
    return bool(v and v.emergency)


# --------------------------------------------------------------------------- #
# config helpers
# --------------------------------------------------------------------------- #
def _section(cfg, dotted: str):
    getter = getattr(cfg, "get_path", None)
    return getter(dotted, None) if getter else None


def mix_errors(raw: Optional[Mapping]) -> List[str]:
    """Validation messages for a ``traffic_mix`` mapping (empty list = valid)."""
    if raw is None:
        return []
    if not isinstance(raw, Mapping):
        return ["  - traffic_mix must be a mapping of vehicle kind -> weight"]
    errors: List[str] = []
    total = 0.0
    for kind, weight in raw.items():
        if kind not in REGULAR_KINDS:
            hint = " (emergency vehicles are injected, not mixed in)" if kind in EMERGENCY_KINDS else ""
            errors.append(f"  - traffic_mix: unknown kind '{kind}'{hint}. "
                          f"Allowed: {list(REGULAR_KINDS)}")
            continue
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight < 0 \
                or weight != weight or weight == float("inf"):
            errors.append(f"  - traffic_mix.{kind} must be a finite number >= 0, got {weight!r}")
            continue
        total += float(weight)
    if not errors and total <= 0:
        errors.append("  - traffic_mix weights must not all be zero")
    return errors


def traffic_mix(cfg=None) -> Tuple[Tuple[str, ...], np.ndarray]:
    """``(kinds, probabilities)`` for spawning ordinary vehicles, normalised to sum to 1.

    Reads ``traffic_mix`` from the config; a missing section falls back to
    :data:`DEFAULT_MIX`. Kinds with weight 0 are dropped.
    """
    raw = _section(cfg, "traffic_mix") if cfg is not None else None
    if raw is None:
        raw = DEFAULT_MIX
    errors = mix_errors(raw)
    if errors:
        raise ValueError("invalid traffic_mix:\n" + "\n".join(errors))
    pairs = [(k, float(w)) for k, w in raw.items() if float(w) > 0]
    # keep the catalogue order so the result does not depend on YAML key order
    pairs.sort(key=lambda kw: KINDS.index(kw[0]))
    kinds = tuple(k for k, _ in pairs)
    weights = np.array([w for _, w in pairs], dtype=np.float64)
    return kinds, weights / weights.sum()


def emergency_errors(raw: Optional[Mapping]) -> List[str]:
    """Validation messages for ``emergency.types``."""
    if raw is None:
        return []
    if not isinstance(raw, Mapping) or not raw:
        return ["  - emergency.types must be a non-empty mapping of emergency kind -> settings"]
    errors: List[str] = []
    for kind, spec in raw.items():
        if kind not in EMERGENCY_KINDS:
            errors.append(f"  - emergency.types: unknown kind '{kind}'. Allowed: {list(EMERGENCY_KINDS)}")
            continue
        if spec is None:
            continue
        if not isinstance(spec, Mapping):
            errors.append(f"  - emergency.types.{kind} must be a mapping")
            continue
        unknown = set(spec) - {"label", "corridor", "speed_factor"}
        if unknown:
            errors.append(f"  - emergency.types.{kind}: unknown keys {sorted(unknown)}")
        corridor = spec.get("corridor", "ew")
        if corridor not in CORRIDORS:
            errors.append(f"  - emergency.types.{kind}.corridor must be one of {list(CORRIDORS)}")
        if "speed_factor" in spec:
            sf = spec["speed_factor"]
            if kind == "ambulance":
                errors.append("  - emergency.types.ambulance.speed_factor is not allowed: the "
                              "ambulance uses emergency.vtype_speed_factor (the benchmark's value)")
            elif isinstance(sf, bool) or not isinstance(sf, (int, float)) or not (0.3 <= sf <= 3.0):
                errors.append(f"  - emergency.types.{kind}.speed_factor must be a number in [0.3, 3]")
        label = spec.get("label")
        if label is not None and (not isinstance(label, str) or not label.strip() or len(label) > 40):
            errors.append(f"  - emergency.types.{kind}.label must be a short non-empty string")
    return errors


def emergency_types(cfg=None) -> List[Dict[str, object]]:
    """Resolved emergency types in catalogue order: ``kind, label, corridor, speed_factor``."""
    raw = _section(cfg, "emergency.types") if cfg is not None else None
    if raw is None:
        raw = DEFAULT_EMERGENCY_TYPES
    errors = emergency_errors(raw)
    if errors:
        raise ValueError("invalid emergency.types:\n" + "\n".join(errors))
    out: List[Dict[str, object]] = []
    for kind in EMERGENCY_KINDS:
        if kind not in raw:
            continue
        spec = _spec(raw, kind)
        out.append({
            "kind": kind,
            "label": str(spec.get("label") or _BY_KIND[kind].label),
            "corridor": str(spec.get("corridor", "ew")),
            "speed_factor": speed_factor(cfg, kind),
        })
    return out


def _spec(raw: Mapping, kind: str) -> Mapping:
    """Settings of one emergency type; a type listed without settings (``police:``) or not
    listed at all gets its built-in defaults."""
    spec = raw.get(kind)
    return spec if spec is not None else DEFAULT_EMERGENCY_TYPES.get(kind, {})


def speed_factor(cfg, kind: str) -> float:
    """Speed factor of an emergency kind (the ambulance always gets ``vtype_speed_factor``).

    A known emergency kind that the config does not list falls back to its built-in default,
    so the simulator can always inject any emergency kind; the dashboard is the layer that
    restricts injections to the configured types.
    """
    if kind not in EMERGENCY_KINDS:
        raise KeyError(f"'{kind}' is not an emergency kind. Emergency kinds: {list(EMERGENCY_KINDS)}")
    base = float((_section(cfg, "emergency.vtype_speed_factor") if cfg is not None else None) or 1.3)
    if kind == "ambulance":
        return base
    raw = (_section(cfg, "emergency.types") if cfg is not None else None) or DEFAULT_EMERGENCY_TYPES
    return float(_spec(raw, kind).get("speed_factor", base))
