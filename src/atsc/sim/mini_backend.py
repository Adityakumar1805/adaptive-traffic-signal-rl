"""Built-in point-queue traffic simulator (the SUMO-free fallback backend).

This is a compact, deterministic *store-and-forward* (point-queue) network model:

  * Vehicles arrive at boundary approaches following a Poisson process whose rate
    depends on the density scenario. A time-of-day profile is also supported
    (``_time_factor``), but every scenario shipped in ``config.yaml`` sets
    ``time_varying: false``, so rush hour is modelled as *sustained* heavy demand and that
    code path is inactive in all reported results.
  * Each vehicle carries a pre-computed route (a straight corridor across the grid,
    with an occasional single turn) so every trip terminates deterministically.
  * At each intersection, an approach that currently has **green** discharges vehicles
    at the saturation flow rate; vehicles then travel the link to the next
    intersection (incurring a realistic travel-time delay that makes *green-wave*
    coordination valuable) or leave the network.
  * Halted vehicles accrue waiting time; this drives both the reported KPIs and the
    RL reward.

It is intentionally simpler than SUMO's microscopic car-following model (no
spill-back, aggregated speeds), and every such simplification is documented. Its job
is to let the whole RL system — training, evaluation and the live dashboard — run with
**zero external dependencies** while producing the same schema SUMO produces.
"""
from __future__ import annotations

import bisect
import math
from collections import deque
from typing import Callable, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from atsc import vehicles as vehicle_catalogue
from atsc.seeding import make_rng
from atsc.sim.backend import (
    APPROACH_TO_TRAVEL,
    APPROACHES,
    MetricsAccumulator,
    NetworkTopo,
    SimBackend,
    StepOutcome,
    TRAVEL_TO_APPROACH,
)

# travel direction -> (d_row, d_col)
_TRAVEL_DELTA = {"N": (-1, 0), "S": (1, 0), "E": (0, 1), "W": (0, -1)}
# perpendicular travel options for turns
_PERPENDICULAR = {"N": ("E", "W"), "S": ("E", "W"), "E": ("N", "S"), "W": ("N", "S")}

# a vehicle arriving on approach N is travelling south; used only for dashboard heading
_OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}


def mix_cdf(probs) -> List[float]:
    """Normalised cumulative distribution, computed exactly as ``Generator.choice`` does."""
    cdf = np.asarray(probs, dtype=np.float64).cumsum()
    cdf /= cdf[-1]
    return cdf.tolist()


def _pick_kind(rng, kinds: Sequence[str], cdf: List[float]) -> str:
    """Weighted random vehicle type from the configured ``traffic_mix``.

    Drawn from a *separate* RNG stream (see :meth:`MiniBackend.reset`) because the kind is
    cosmetic: keeping it off the main stream means arrivals, routes and therefore every
    reported KPI stay bit-identical whatever the mix is. Emergencies are set explicitly.
    One uniform draw + a right-bisect of the cdf is exactly what
    ``rng.choice(len(kinds), p=probs)`` does, without re-validating ``p`` on every spawn.
    """
    return kinds[bisect.bisect_right(cdf, rng.random())]


def vehicle_number(vid: str) -> int:
    """Numeric part of a vehicle id (``"v17" -> 17``, ``"EMG18" -> 18``); ids share one counter."""
    return int(vid.lstrip("vEMG"))


class Vehicle:
    """A lightweight vehicle. ``__slots__`` keeps thousands of these cheap."""

    __slots__ = (
        "id", "legs", "leg_index", "wait_time", "is_emergency",
        "depart_time", "distance", "speed_factor", "kind", "exit_dir",
    )

    def __init__(self, vid: str, legs: List[Tuple[str, str]], depart_time: float,
                 is_emergency: bool = False, speed_factor: float = 1.0, kind: str = "car",
                 exit_dir: str = "") -> None:
        self.id = vid
        self.legs = legs                 # list of (intersection_id, arrival_approach)
        self.leg_index = 0
        self.wait_time = 0.0
        self.is_emergency = is_emergency
        self.depart_time = depart_time
        self.distance = 0.0
        self.speed_factor = speed_factor
        self.kind = kind
        self.exit_dir = exit_dir         # travel direction when it leaves the grid (drawing only)

    @property
    def tls(self) -> str:
        return self.legs[self.leg_index][0]

    @property
    def approach(self) -> str:
        return self.legs[self.leg_index][1]


class MiniBackend(SimBackend):
    """Deterministic point-queue simulator implementing :class:`SimBackend`."""

    def __init__(self, cfg, topo: NetworkTopo) -> None:
        self.cfg = cfg
        self.topo = topo
        self._link_length = float(cfg.network.link_length_m)
        self._free_speed = float(cfg.sim.free_speed_mps)
        self._sat_flow = float(cfg.sim.saturation_flow_vps)
        self._lanes = int(cfg.network.lanes_per_approach)
        self._step_len = float(cfg.sim.step_length_s)
        self._warmup = float(cfg.sim.warmup_s)
        self._emergency_speed = float(cfg.emergency.vtype_speed_factor)
        # cosmetic vehicle-type mix (separate RNG stream; never affects the traffic itself)
        self._mix_kinds, mix_probs = vehicle_catalogue.traffic_mix(cfg)
        self._mix_cdf = mix_cdf(mix_probs)

        # optional observers for the dashboard (pure callbacks: they never change the state).
        # on_enter(veh, travel_s, dest_tls, dest_appr) - a vehicle starts crossing a link
        # on_complete(veh, at_tls)                      - it leaves the network
        self._on_enter: Optional[Callable] = None
        self._on_complete: Optional[Callable] = None

        # entry points: (tls_id, approach) where that side is a network boundary
        self._entries: List[Tuple[str, str]] = []
        for iid in topo.order:
            it = topo.get(iid)
            for approach, neigh in it.neighbors.items():
                if neigh is None:
                    self._entries.append((iid, approach))

        # state (populated in reset)
        self._rng: Optional[np.random.Generator] = None
        self._kind_rng: Optional[np.random.Generator] = None
        self._t = 0.0
        self._veh_counter = 0
        self._scenario: Dict = {}
        self._queues: Dict[Tuple[str, str], Deque[Vehicle]] = {}
        self._transit: List[Tuple[Vehicle, float, str, str]] = []  # (veh, remaining_s, dest_tls, dest_appr)
        self._credit: Dict[Tuple[str, str], float] = {}
        self._green: Dict[str, set] = {}
        self._delay: Dict[str, float] = {}
        self._emerg_cleared: Dict[str, int] = {}
        self._acc: Optional[MetricsAccumulator] = None
        self._finalized = False

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def reset(self, scenario_params: Dict, seed: int) -> None:
        self._rng = np.random.default_rng(seed)
        # cosmetic draws (vehicle kind) live on their own stream, derived from the same
        # seed: still fully deterministic, but they cannot shift the traffic itself.
        self._kind_rng = make_rng(seed, "kind")
        self._t = 0.0
        self._veh_counter = 0
        self._scenario = dict(scenario_params)
        self._queues = {(iid, a): deque() for iid in self.topo.order for a in APPROACHES}
        self._transit = []
        self._credit = {(iid, a): 0.0 for iid in self.topo.order for a in APPROACHES}
        self._green = {iid: set() for iid in self.topo.order}
        self._delay = {iid: 0.0 for iid in self.topo.order}
        self._emerg_cleared = {iid: 0 for iid in self.topo.order}
        self._acc = MetricsAccumulator(self.topo.n_intersections, warmup_s=self._warmup)
        self._finalized = False

    def close(self) -> None:  # nothing to release
        return None

    @property
    def time(self) -> float:
        return self._t

    # ------------------------------------------------------------------ #
    # route generation
    # ------------------------------------------------------------------ #
    def _build_route(self, entry_tls: str, entry_approach: str) -> Tuple[List[Tuple[str, str]], str]:
        """Build a corridor route from a boundary entry, with an occasional turn.

        Returns ``(legs, exit_dir)``: the junctions visited with their arrival approaches, and
        the direction of travel when the vehicle leaves the grid (used only for drawing).
        """
        rng = self._rng
        rows, cols = self.topo.grid_rows, self.topo.grid_cols
        turn_prob = float(self._scenario.get("turn_prob", 0.0))

        it = self.topo.get(entry_tls)
        r, c = it.row, it.col
        travel = APPROACH_TO_TRAVEL[entry_approach]      # direction of motion into the grid
        arrival_approach = entry_approach
        legs: List[Tuple[str, str]] = []
        turned = False
        guard = 0
        while 0 <= r < rows and 0 <= c < cols and guard < rows * cols * 2:
            guard += 1
            legs.append((f"J{r}_{c}", arrival_approach))
            # possibly turn once (not on the very first node so it enters straight)
            if (not turned) and len(legs) >= 1 and rng.random() < turn_prob and guard > 1:
                travel = _PERPENDICULAR[travel][int(rng.random() < 0.5)]
                turned = True
            dr, dc = _TRAVEL_DELTA[travel]
            r, c = r + dr, c + dc
            arrival_approach = TRAVEL_TO_APPROACH[travel]
        return legs, travel

    def _arrival_rate(self, approach: str) -> float:
        """Per-second Poisson rate for a boundary entry, with arterial boost + time profile."""
        base = float(self._scenario.get("base_arrival_vps", 0.1))
        scale = float(self._scenario.get("arrival_scale", 1.0))
        boost = float(self._scenario.get("arterial_boost", 1.0)) if approach in ("E", "W") else 1.0
        rate = base * scale * boost * self._lanes
        if self._scenario.get("time_varying", False):
            rate *= self._time_factor()
        return rate

    def _time_factor(self) -> float:
        """Gaussian rush-hour multiplier centred within the episode."""
        horizon = float(self.cfg.train.episode_seconds)  # nominal; scaling only needs a shape
        center = self._scenario.get("peak_center_frac", 0.5) * horizon
        width = max(1.0, self._scenario.get("peak_width_frac", 0.2) * horizon)
        bump = math.exp(-0.5 * ((self._t - center) / width) ** 2)
        return 0.6 + 1.0 * bump      # ranges from 0.6 (off-peak) to 1.6 (peak)

    # ------------------------------------------------------------------ #
    # stepping
    # ------------------------------------------------------------------ #
    def set_green(self, tls_id: str, green_approaches: Iterable[str]) -> None:
        self._green[tls_id] = set(green_approaches)

    def step(self) -> StepOutcome:
        rng = self._rng
        outcome = StepOutcome(time=self._t)

        # 1) spawn arrivals at every boundary entry
        for (iid, approach) in self._entries:
            rate = self._arrival_rate(approach) * self._step_len
            n = rng.poisson(rate)
            for _ in range(int(n)):
                legs, exit_dir = self._build_route(iid, approach)
                if not legs:
                    continue
                self._veh_counter += 1
                veh = Vehicle(f"v{self._veh_counter}", legs, self._t,
                              kind=_pick_kind(self._kind_rng, self._mix_kinds, self._mix_cdf),
                              exit_dir=exit_dir)
                self._queues[(veh.tls, veh.approach)].append(veh)
                outcome.arrivals += 1

        # 2) advance vehicles in transit between intersections
        still: List[Tuple[Vehicle, float, str, str]] = []
        for veh, remaining, dest_tls, dest_appr in self._transit:
            remaining -= self._step_len
            if remaining <= 0.0:
                self._queues[(dest_tls, dest_appr)].append(veh)
            else:
                still.append((veh, remaining, dest_tls, dest_appr))
        self._transit = still

        # 3) discharge green approaches at saturation flow
        for iid in self.topo.order:
            green = self._green.get(iid, set())
            for approach in APPROACHES:
                key = (iid, approach)
                q = self._queues[key]
                if approach in green and q:
                    self._credit[key] += self._sat_flow * self._lanes * self._step_len
                else:
                    self._credit[key] = 0.0
                    continue
                while self._credit[key] >= 1.0 and q:
                    self._credit[key] -= 1.0
                    veh = q.popleft()
                    self._advance_vehicle(veh, iid, outcome)

        # 4) accrue waiting time for everything still queued, and delay for reward
        halted_total = 0
        for iid in self.topo.order:
            inter_halt = 0
            for approach in APPROACHES:
                q = self._queues[(iid, approach)]
                for veh in q:
                    veh.wait_time += self._step_len
                inter_halt += len(q)
            halted_total += inter_halt
            self._delay[iid] += inter_halt * self._step_len

        # 5) metrics for this second
        moving = len(self._transit)
        present = halted_total + moving
        mean_speed = self._free_speed * (moving / present) if present else self._free_speed
        fuel_ml = 0.30 * halted_total + 0.90 * moving          # estimated proxy
        co2_g = fuel_ml * 2.31                                  # petrol ~2.31 g CO2 / ml
        self._acc.record_second(self._t, halted_total, present, mean_speed, fuel_ml, co2_g)

        self._t += self._step_len
        return outcome

    def _advance_vehicle(self, veh: Vehicle, at_tls: str, outcome: StepOutcome) -> None:
        """Move a vehicle across the intersection it just cleared."""
        veh.distance += self._link_length
        if veh.is_emergency:
            self._emerg_cleared[at_tls] += 1
        veh.leg_index += 1
        if veh.leg_index >= len(veh.legs):
            # left the network -> trip complete
            outcome.completed += 1
            time_in_net = self._t - veh.depart_time
            self._acc.record_completion(veh.wait_time, veh.is_emergency, time_in_net)
            if veh.is_emergency:
                outcome.emergency_cleared.append(veh.id)
            if self._on_complete is not None:
                self._on_complete(veh, at_tls)
        else:
            dest_tls, dest_appr = veh.legs[veh.leg_index]
            speed = self._free_speed * veh.speed_factor
            travel_time = self._link_length / max(1e-6, speed)
            self._transit.append((veh, travel_time, dest_tls, dest_appr))
            if self._on_enter is not None:
                self._on_enter(veh, travel_time, dest_tls, dest_appr)

    # ------------------------------------------------------------------ #
    # queries
    # ------------------------------------------------------------------ #
    def queue(self, tls_id: str, approach: str) -> int:
        return len(self._queues[(tls_id, approach)])

    def waiting_time(self, tls_id: str, approach: str) -> float:
        return float(sum(v.wait_time for v in self._queues[(tls_id, approach)]))

    def emergency_present(self, tls_id: str, approach: str) -> bool:
        return any(v.is_emergency for v in self._queues[(tls_id, approach)])

    def pop_delay(self, tls_id: str) -> float:
        d = self._delay[tls_id]
        self._delay[tls_id] = 0.0
        return d

    def pop_emergency_cleared(self, tls_id: str) -> int:
        n = self._emerg_cleared[tls_id]
        self._emerg_cleared[tls_id] = 0
        return n

    # ------------------------------------------------------------------ #
    # metrics & rendering
    # ------------------------------------------------------------------ #
    def finalize(self) -> None:
        """Fold vehicles still in the network into the waiting-time average (once)."""
        if self._finalized:
            return
        total_wait = 0.0
        count = 0
        for q in self._queues.values():
            for v in q:
                total_wait += v.wait_time
                count += 1
        for (v, _r, _t, _a) in self._transit:
            total_wait += v.wait_time
            count += 1
        self._acc.record_present_wait(total_wait, count)
        self._finalized = True

    def metrics(self) -> MetricsAccumulator:
        return self._acc

    def render_state(self) -> Dict:
        topo = self.topo
        link = self._link_length
        intersections = {}
        for iid in topo.order:
            it = topo.get(iid)
            intersections[iid] = {
                "x": it.x, "y": it.y, "row": it.row, "col": it.col,
                "queues": {a: self.queue(iid, a) for a in APPROACHES},
                "waiting": {a: round(self.waiting_time(iid, a), 1) for a in APPROACHES},
                "emergency": {a: self.emergency_present(iid, a) for a in APPROACHES},
                # the real kinds of the vehicles waiting at the stop line, front first,
                # so the dashboard can draw each queued vehicle as what it actually is
                "qk": {a: self._queued_kinds(iid, a) for a in APPROACHES},
            }
        # moving vehicles -> interpolated dots for animation (cap for bandwidth)
        moving = []
        for veh, remaining, dest_tls, dest_appr in self._transit[:400]:
            dest = topo.get(dest_tls)
            src_id = dest.neighbors.get(dest_appr)
            if src_id is not None:
                src = topo.get(src_id)
                sx, sy = src.x, src.y
            else:  # entered from outside the grid
                sx, sy = self._boundary_point(dest, dest_appr, link)
            travel_time = link / max(1e-6, self._free_speed * veh.speed_factor)
            progress = 1.0 - max(0.0, min(1.0, remaining / travel_time))
            x = sx + (dest.x - sx) * progress
            y = sy + (dest.y - sy) * progress
            moving.append({"id": veh.id, "x": round(x, 1), "y": round(y, 1), "e": veh.is_emergency,
                           "k": veh.kind, "o": "v" if dest_appr in ("N", "S") else "h",
                           # heading: arriving on approach N means travelling south, etc.
                           "d": _OPPOSITE[dest_appr]})
        return {"time": self._t, "intersections": intersections, "moving": moving}

    def _queued_kinds(self, tls_id: str, approach: str, cap: int = 16) -> List[str]:
        """Kinds of the vehicles queued on one approach, closest to the stop line first."""
        q = self._queues[(tls_id, approach)]
        return [q[i].kind for i in range(min(cap, len(q)))]

    # ------------------------------------------------------------------ #
    # read-only views for the live dashboard (never change the simulation)
    # ------------------------------------------------------------------ #
    def set_event_hooks(self, on_enter: Optional[Callable] = None,
                        on_complete: Optional[Callable] = None) -> None:
        """Register observers (see ``__init__``). Pass nothing to detach both.

        The callbacks only *receive* vehicles; they must not mutate them. They exist so the
        dashboard can stream link entries and exits as events instead of resending every
        vehicle position each tick: a vehicle that entered a link at ``t0`` with travel time
        ``T`` is on that link exactly while ``t0 <= t < t0 + T``.
        """
        self._on_enter, self._on_complete = on_enter, on_complete

    def queue_snapshot(self, cap: int) -> List[Tuple[int, List[Vehicle]]]:
        """``(length, the first `cap` vehicles, stop line first)`` per approach, in topology
        order x N, E, S, W. The vehicles are the live objects: callers must only read them."""
        out: List[Tuple[int, List[Vehicle]]] = []
        for iid in self.topo.order:
            for a in APPROACHES:
                q = self._queues[(iid, a)]
                out.append((len(q), [q[i] for i in range(min(cap, len(q)))]))
        return out

    def transit_snapshot(self) -> List[Tuple[Vehicle, float, float, str, str]]:
        """``(vehicle, remaining_s, travel_s, dest_tls, dest_appr)`` for every vehicle on a link."""
        free = self._free_speed
        link = self._link_length
        return [(veh, remaining, link / max(1e-6, free * veh.speed_factor), dest_tls, dest_appr)
                for veh, remaining, dest_tls, dest_appr in self._transit]

    def present_wait(self) -> Tuple[float, int]:
        """Accumulated waiting time and count of every vehicle still in the network."""
        total, count = 0.0, 0
        for q in self._queues.values():
            for v in q:
                total += v.wait_time
            count += len(q)
        for v, _r, _t, _a in self._transit:
            total += v.wait_time
            count += 1
        return total, count

    def active_emergencies(self) -> int:
        """Emergency vehicles currently in the network (queued or on a link)."""
        n = sum(1 for q in self._queues.values() for v in q if v.is_emergency)
        return n + sum(1 for v, _r, _t, _a in self._transit if v.is_emergency)

    @staticmethod
    def _boundary_point(dest, approach: str, link: float) -> Tuple[float, float]:
        if approach == "N":
            return dest.x, dest.y - link
        if approach == "S":
            return dest.x, dest.y + link
        if approach == "W":
            return dest.x - link, dest.y
        return dest.x + link, dest.y

    # ------------------------------------------------------------------ #
    # emergencies
    # ------------------------------------------------------------------ #
    def inject_emergency(self, corridor: Optional[str] = None,
                         kind: str = "ambulance") -> Optional[str]:
        """Spawn an emergency vehicle traversing a full corridor of the grid.

        ``corridor`` is ``"ew"`` (eastbound along row ``rows // 2``) or ``"ns"`` (southbound
        down column ``cols // 2``). ``kind`` is an emergency kind from :mod:`atsc.vehicles`;
        the default ambulance with ``emergency.vtype_speed_factor`` is exactly the vehicle the
        published benchmark injects. No random number is drawn, so injecting never shifts the
        arrival stream.
        """
        if kind not in vehicle_catalogue.EMERGENCY_KINDS:
            raise ValueError(f"'{kind}' is not an emergency kind; choose from "
                             f"{list(vehicle_catalogue.EMERGENCY_KINDS)}")
        rows, cols = self.topo.grid_rows, self.topo.grid_cols
        if corridor is None:
            corridor = "ew"
        if corridor == "ew":
            row = rows // 2
            entry_tls, entry_appr = f"J{row}_0", "W"     # eastbound across the middle row
        elif corridor == "ns":
            col = cols // 2
            entry_tls, entry_appr = f"J0_{col}", "N"     # southbound down the middle column
        else:
            raise ValueError(f"corridor must be 'ew' or 'ns', got {corridor!r}")
        # emergencies always run straight across a full corridor (no turns)
        legs = self._straight_corridor(entry_tls, entry_appr)
        if not legs:
            return None
        speed = (self._emergency_speed if kind == "ambulance"
                 else vehicle_catalogue.speed_factor(self.cfg, kind))
        self._veh_counter += 1
        vid = f"EMG{self._veh_counter}"
        veh = Vehicle(vid, legs, self._t, is_emergency=True, speed_factor=speed, kind=kind,
                      exit_dir=APPROACH_TO_TRAVEL[entry_appr])
        self._queues[(veh.tls, veh.approach)].append(veh)
        return vid

    def _straight_corridor(self, entry_tls: str, entry_approach: str) -> List[Tuple[str, str]]:
        rows, cols = self.topo.grid_rows, self.topo.grid_cols
        it = self.topo.get(entry_tls)
        r, c = it.row, it.col
        travel = APPROACH_TO_TRAVEL[entry_approach]
        arrival_approach = entry_approach
        legs: List[Tuple[str, str]] = []
        while 0 <= r < rows and 0 <= c < cols:
            legs.append((f"J{r}_{c}", arrival_approach))
            dr, dc = _TRAVEL_DELTA[travel]
            r, c = r + dr, c + dc
            arrival_approach = TRAVEL_TO_APPROACH[travel]
        return legs
