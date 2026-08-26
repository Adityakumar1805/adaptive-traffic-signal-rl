"""Shared simulation schema, grid topology, metrics, and the backend interface.

This module is backend-independent. It defines:
  * cardinal directions and the standard phase sets,
  * dataclasses describing the network topology (used by *both* backends and the
    dashboard for rendering),
  * :func:`build_topology`, which turns the ``config.yaml`` grid spec into a concrete
    :class:`NetworkTopo`,
  * :class:`MetricsAccumulator`, which turns per-second simulation events into the
    aggregate KPIs reported by the evaluation harness,
  * :class:`SimBackend`, the abstract interface both simulators implement.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

# --------------------------------------------------------------------------- #
# Directions.  An *approach* is the side of the intersection a vehicle arrives
# from.  A south-bound vehicle arrives from the North side  -> approach "N".
# --------------------------------------------------------------------------- #
NORTH, EAST, SOUTH, WEST = "N", "E", "S", "W"
APPROACHES: Tuple[str, ...] = (NORTH, EAST, SOUTH, WEST)

# The direction a vehicle travels, and the approach it therefore queues on.
TRAVEL_TO_APPROACH = {"S": NORTH, "N": SOUTH, "E": WEST, "W": EAST}
APPROACH_TO_TRAVEL = {v: k for k, v in TRAVEL_TO_APPROACH.items()}
OPPOSITE_TRAVEL = {"N": "S", "S": "N", "E": "W", "W": "E"}


@dataclass(frozen=True)
class PhaseDef:
    """A controllable green phase: which incoming approaches receive green."""

    index: int
    name: str
    green_approaches: Tuple[str, ...]


@dataclass
class IntersectionTopo:
    """Static description of one signalised intersection."""

    id: str
    row: int
    col: int
    x: float
    y: float
    phases: List[PhaseDef]
    # approach -> neighbouring intersection id, or None if that side is a network boundary
    neighbors: Dict[str, Optional[str]]

    @property
    def n_phases(self) -> int:
        return len(self.phases)

    def neighbor_ids(self) -> List[str]:
        return [n for n in self.neighbors.values() if n is not None]

    def approach_of_phase(self, phase_index: int) -> Tuple[str, ...]:
        return self.phases[phase_index].green_approaches


@dataclass
class NetworkTopo:
    """The whole grid: intersections plus a stable ordering."""

    intersections: Dict[str, IntersectionTopo]
    order: List[str]
    grid_rows: int
    grid_cols: int
    link_length_m: float

    def __iter__(self):
        for iid in self.order:
            yield self.intersections[iid]

    def get(self, iid: str) -> IntersectionTopo:
        return self.intersections[iid]

    @property
    def n_intersections(self) -> int:
        return len(self.order)


def _phase_set(scheme: str) -> List[PhaseDef]:
    """Return the list of green phases for a phase scheme."""
    if scheme == "ns_ew":
        return [
            PhaseDef(0, "NS", (NORTH, SOUTH)),  # north-south traffic flows
            PhaseDef(1, "EW", (EAST, WEST)),    # east-west traffic flows
        ]
    if scheme == "quad":
        # Four protected phases, one approach at a time (used for the 3x3+ upgrade path).
        return [
            PhaseDef(0, "N", (NORTH,)),
            PhaseDef(1, "E", (EAST,)),
            PhaseDef(2, "S", (SOUTH,)),
            PhaseDef(3, "W", (WEST,)),
        ]
    raise ValueError(f"Unknown phase_scheme '{scheme}' (use 'ns_ew' or 'quad').")


def build_topology(cfg) -> NetworkTopo:
    """Construct a :class:`NetworkTopo` from the ``network`` section of the config."""
    rows = int(cfg.network.grid_rows)
    cols = int(cfg.network.grid_cols)
    link = float(cfg.network.link_length_m)
    scheme = str(cfg.network.phase_scheme)

    intersections: Dict[str, IntersectionTopo] = {}
    order: List[str] = []
    for r in range(rows):
        for c in range(cols):
            iid = f"J{r}_{c}"
            order.append(iid)
            neighbors = {
                NORTH: f"J{r-1}_{c}" if r - 1 >= 0 else None,
                SOUTH: f"J{r+1}_{c}" if r + 1 < rows else None,
                WEST: f"J{r}_{c-1}" if c - 1 >= 0 else None,
                EAST: f"J{r}_{c+1}" if c + 1 < cols else None,
            }
            intersections[iid] = IntersectionTopo(
                id=iid,
                row=r,
                col=c,
                x=c * link,
                y=r * link,
                phases=_phase_set(scheme),
                neighbors=neighbors,
            )
    return NetworkTopo(intersections, order, rows, cols, link)


# --------------------------------------------------------------------------- #
# Per-step outcome and episode metrics
# --------------------------------------------------------------------------- #
@dataclass
class StepOutcome:
    """Events produced by advancing the simulation by one second."""

    time: float
    arrivals: int = 0                     # vehicles that entered the network this step
    completed: int = 0                    # vehicles that finished (throughput) this step
    emergency_cleared: List[str] = field(default_factory=list)  # ids that just exited


class MetricsAccumulator:
    """Aggregates per-second simulation state into episode-level KPIs.

    All averages ignore the warm-up period. ``avg_waiting_time`` is per vehicle
    (the headline metric); ``avg_queue`` is the time-averaged number of halted
    vehicles per intersection.
    """

    def __init__(self, n_intersections: int, warmup_s: float = 0.0) -> None:
        self.n_intersections = max(1, n_intersections)
        self.warmup_s = warmup_s
        # time-integrated quantities
        self._queue_area = 0.0          # sum over seconds of total halted vehicles
        self._speed_sum = 0.0           # sum over seconds of mean speed
        self._active_veh_area = 0.0     # sum over seconds of vehicles present
        self._seconds = 0
        # per-vehicle tallies
        self._total_wait = 0.0          # sum of accumulated wait over completed+present vehicles
        self._n_vehicles = 0            # vehicles counted for the wait average
        self._throughput = 0            # vehicles completed
        # energy proxies
        self._fuel_ml = 0.0
        self._co2_g = 0.0
        # emergency clearance times (seconds in network)
        self.emergency_times: List[float] = []

    def record_second(
        self,
        time_s: float,
        halted_total: int,
        vehicles_present: int,
        mean_speed: float,
        fuel_ml: float,
        co2_g: float,
    ) -> None:
        if time_s < self.warmup_s:
            return
        self._queue_area += halted_total
        self._active_veh_area += vehicles_present
        self._speed_sum += mean_speed
        self._seconds += 1
        self._fuel_ml += fuel_ml
        self._co2_g += co2_g

    def record_completion(self, wait_time: float, is_emergency: bool, time_in_network: float) -> None:
        self._throughput += 1
        self._total_wait += wait_time
        self._n_vehicles += 1
        if is_emergency:
            self.emergency_times.append(time_in_network)

    def record_present_wait(self, total_wait: float, count: int) -> None:
        """Fold in vehicles still present at episode end so the average is unbiased."""
        self._total_wait += total_wait
        self._n_vehicles += count

    def as_dict(self) -> Dict[str, float]:
        seconds = max(1, self._seconds)
        n_veh = max(1, self._n_vehicles)
        return {
            "avg_waiting_time": self._total_wait / n_veh,
            "avg_queue": self._queue_area / seconds / self.n_intersections,
            "avg_queue_total": self._queue_area / seconds,
            "throughput": float(self._throughput),
            "avg_speed": self._speed_sum / seconds,
            "avg_vehicles": self._active_veh_area / seconds,
            "fuel_ml": self._fuel_ml,
            "co2_g": self._co2_g,
            "emergency_clearance_time": (
                sum(self.emergency_times) / len(self.emergency_times)
                if self.emergency_times else float("nan")
            ),
            "n_emergencies": float(len(self.emergency_times)),
        }


# --------------------------------------------------------------------------- #
# Backend interface
# --------------------------------------------------------------------------- #
class SimBackend(abc.ABC):
    """Abstract simulation backend.

    Lifecycle per episode::

        backend.reset(scenario_params, seed)
        while not done:
            for each intersection:  backend.set_green(iid, green_approaches)
            outcome = backend.step()          # advance one simulated second
            ... query queue()/waiting_time()/pop_delay() ...
        backend.close()
    """

    topo: NetworkTopo

    # -- lifecycle -------------------------------------------------------- #
    @abc.abstractmethod
    def reset(self, scenario_params: Dict, seed: int) -> None: ...

    @abc.abstractmethod
    def close(self) -> None: ...

    # -- stepping --------------------------------------------------------- #
    @abc.abstractmethod
    def set_green(self, tls_id: str, green_approaches: Iterable[str]) -> None:
        """Declare which approaches have green at ``tls_id`` for the next second(s)."""

    @abc.abstractmethod
    def step(self) -> StepOutcome:
        """Advance the simulation by one second and return the events."""

    @property
    @abc.abstractmethod
    def time(self) -> float: ...

    # -- observation queries ---------------------------------------------- #
    @abc.abstractmethod
    def queue(self, tls_id: str, approach: str) -> int:
        """Number of halted vehicles on an approach."""

    @abc.abstractmethod
    def waiting_time(self, tls_id: str, approach: str) -> float:
        """Sum of accumulated waiting time (s) of vehicles queued on an approach."""

    @abc.abstractmethod
    def emergency_present(self, tls_id: str, approach: str) -> bool: ...

    @abc.abstractmethod
    def pop_delay(self, tls_id: str) -> float:
        """Delay (vehicle-seconds) accrued at an intersection since the last call."""

    @abc.abstractmethod
    def pop_emergency_cleared(self, tls_id: str) -> int:
        """Emergency vehicles that cleared this intersection since the last call."""

    # -- metrics & rendering ---------------------------------------------- #
    @abc.abstractmethod
    def metrics(self) -> MetricsAccumulator: ...

    @abc.abstractmethod
    def render_state(self) -> Dict:
        """Return a JSON-serialisable snapshot for the dashboard."""

    # -- emergencies ------------------------------------------------------ #
    @abc.abstractmethod
    def inject_emergency(self, corridor: Optional[str] = None) -> Optional[str]:
        """Spawn one emergency vehicle; return its id (or None if not possible)."""

    # -- convenience ------------------------------------------------------ #
    def finalize(self) -> None:
        """Fold any end-of-episode bookkeeping into metrics. Default: no-op."""
        return None

    def intersection_pressure(self, tls_id: str) -> float:
        """Max-pressure style pressure = incoming queue minus downstream queue.

        Used by the max-pressure baseline and as an optional reward term. Positive
        pressure means vehicles are piling up faster than they can leave.
        """
        topo = self.topo.get(tls_id)
        incoming = sum(self.queue(tls_id, a) for a in APPROACHES)
        downstream = 0
        for approach, neigh in topo.neighbors.items():
            if neigh is None:
                continue
            # vehicles leaving via this side feed the neighbour's opposite approach
            opp = OPPOSITE_TRAVEL_APPROACH[approach]
            downstream += self.queue(neigh, opp)
        return float(incoming - 0.5 * downstream)


# Approach on the neighbour that receives traffic leaving through a given side.
# Leaving north (through the N side) enters the north neighbour from its South side.
OPPOSITE_TRAVEL_APPROACH = {NORTH: SOUTH, SOUTH: NORTH, EAST: WEST, WEST: EAST}
