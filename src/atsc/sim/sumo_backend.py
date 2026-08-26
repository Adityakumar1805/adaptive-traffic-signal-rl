"""SUMO backend (PRIMARY simulator) via ``libsumo`` (fast, headless) or ``traci``.

Design notes
------------
The rest of the system speaks in *approaches* ("give North+South green"). SUMO speaks
in per-link *state strings* (one ``G/g/y/r`` character per controlled link at a
junction). The key job of this backend is the bidirectional mapping between the two:

  * On :meth:`reset` we query each traffic light's controlled links, look at the
    incoming edge of each link, and (using the deterministic edge naming from
    ``netgen`` plus the grid topology) tag every link with the approach it belongs to.
  * :meth:`set_green` then builds the correct state string: ``G`` for links whose
    approach is currently green, ``r`` for every other link. Clearance is produced by the
    environment's phase FSM, which calls :meth:`set_green` with an **empty** approach set
    during both yellow and all-red — so this backend shows an all-red string for the whole
    clearance interval. That is conservative (safe, marginally less throughput than a true
    amber) and means no ``y`` character is ever written.

One gap to be aware of: :meth:`pop_emergency_cleared` always returns 0 here — SUMO's arrival
list is not scanned for the emergency vtype — so the ``reward.w_emergency`` bonus only fires on
:class:`~atsc.sim.mini_backend.MiniBackend`. Preemption itself (``control/emergency.py``) works
on both backends because it acts on the phase request, not on the reward.

Because SUMO is optional, every SUMO import happens lazily inside methods so the module
imports cleanly on a machine without SUMO. If SUMO is requested but unavailable, the
factory in :mod:`atsc.sim` raises a friendly, actionable error and the caller falls
back to :class:`~atsc.sim.mini_backend.MiniBackend`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from atsc.sim.backend import (
    APPROACHES,
    MetricsAccumulator,
    NetworkTopo,
    SimBackend,
    StepOutcome,
)
from atsc.sim import netgen


class SumoBackend(SimBackend):
    """SUMO-backed implementation of :class:`SimBackend`."""

    def __init__(self, cfg, topo: NetworkTopo, use_gui: bool = False) -> None:
        self.cfg = cfg
        self.topo = topo
        self.use_gui = use_gui
        self._conn = None          # the libsumo module or a traci connection
        self._use_libsumo = False
        self._t = 0.0
        self._step_len = float(cfg.sim.step_length_s)
        self._warmup = float(cfg.sim.warmup_s)
        # per-junction: approach -> list of controlled-link indices
        self._appr_links: Dict[str, Dict[str, List[int]]] = {}
        self._state_len: Dict[str, int] = {}
        self._green: Dict[str, Set[str]] = {}
        self._acc: Optional[MetricsAccumulator] = None
        self._delay: Dict[str, float] = {}
        self._emerg_cleared: Dict[str, int] = {}
        self._known_emerg: Set[str] = set()
        self._arrived_emerg: Set[str] = set()
        self._scenario: Dict = {}
        self._emerg_counter = 0
        # last-known accumulated wait + depart time per present vehicle, so we can
        # attribute waiting time and time-in-network to vehicles once they arrive.
        self._veh_wait: Dict[str, float] = {}
        self._veh_depart: Dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # connection helpers
    # ------------------------------------------------------------------ #
    def _load_binding(self):
        """Import libsumo if possible (fast); otherwise traci. Raise if neither."""
        from atsc.sim.sumo_detect import detect_sumo, install_instructions

        info = detect_sumo()
        if self.use_gui:
            # GUI requires the sumo-gui binary via traci (libsumo has no GUI)
            if not info.has_traci or info.gui_binary is None:
                raise RuntimeError(install_instructions())
            import traci
            self._conn = traci
            self._use_libsumo = False
            return info
        # headless: prefer libsumo
        if info.has_libsumo:
            import libsumo
            self._conn = libsumo
            self._use_libsumo = True
        elif info.has_traci and info.binary is not None:
            import traci
            self._conn = traci
            self._use_libsumo = False
        else:
            raise RuntimeError(install_instructions())
        return info

    def _sumo_cmd(self, info, sumocfg: Path) -> List[str]:
        binary = info.gui_binary if self.use_gui else info.binary
        if binary is None:
            binary = "sumo-gui" if self.use_gui else "sumo"
        return [
            binary,
            "-c", str(sumocfg),
            "--step-length", str(self._step_len),
            "--no-warnings", "true",
            "--no-step-log", "true",
            "--time-to-teleport", "-1",
            "--waiting-time-memory", str(int(self.cfg.eval.episode_seconds)),
        ]

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def reset(self, scenario_params: Dict, seed: int) -> None:
        info = self._load_binding()
        scenario = scenario_params.get("name", "medium")
        # ensure network + routes exist for this scenario/seed
        netgen.build_net(self.cfg)
        netgen.build_routes(self.cfg, scenario, seed)
        sumocfg = netgen.scenario_dir(self.cfg) / f"{scenario}.sumocfg"

        cmd = self._sumo_cmd(info, sumocfg)
        cmd += ["--seed", str(int(seed) % 100000)]
        if self._conn is not None and self._is_running():
            self._conn.close()
        self._conn.start(cmd)

        self._t = 0.0
        self._scenario = dict(scenario_params)
        self._acc = MetricsAccumulator(self.topo.n_intersections, warmup_s=self._warmup)
        self._delay = {iid: 0.0 for iid in self.topo.order}
        self._emerg_cleared = {iid: 0 for iid in self.topo.order}
        self._green = {iid: set() for iid in self.topo.order}
        self._known_emerg = set()
        self._arrived_emerg = set()
        self._emerg_counter = 0
        self._veh_wait = {}
        self._veh_depart = {}
        self._map_links()

    def _is_running(self) -> bool:
        try:
            self._conn.simulation.getTime()
            return True
        except Exception:
            return False

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass

    @property
    def time(self) -> float:
        return self._t

    # ------------------------------------------------------------------ #
    # link<->approach mapping
    # ------------------------------------------------------------------ #
    def _map_links(self) -> None:
        """Tag each controlled link of every junction with its approach direction."""
        conn = self._conn
        self._appr_links = {}
        self._state_len = {}
        for iid in self.topo.order:
            links = conn.trafficlight.getControlledLinks(iid)
            appr_map: Dict[str, List[int]] = {a: [] for a in APPROACHES}
            for idx, link_tuple in enumerate(links):
                if not link_tuple:
                    continue
                in_lane = link_tuple[0][0]
                edge_id = conn.lane.getEdgeID(in_lane) if hasattr(conn, "lane") else in_lane.rsplit("_", 1)[0]
                appr = self._approach_of_edge(iid, edge_id)
                if appr is not None:
                    appr_map[appr].append(idx)
            self._appr_links[iid] = appr_map
            self._state_len[iid] = len(links)

    def _approach_of_edge(self, iid: str, edge_id: str) -> Optional[str]:
        """Given an incoming edge id 'X__J', return which approach side of J it is."""
        it = self.topo.get(iid)
        if "__" not in edge_id:
            return None
        src, dst = edge_id.split("__", 1)
        if dst != iid:
            return None
        # cap edge: "{J}_{approach}cap"
        for a in APPROACHES:
            if src == f"{iid}_{a}cap":
                return a
        # neighbour edge: src is a neighbouring junction
        for a, neigh in it.neighbors.items():
            if neigh is not None and src == neigh:
                return a
        return None

    # ------------------------------------------------------------------ #
    # stepping
    # ------------------------------------------------------------------ #
    def set_green(self, tls_id: str, green_approaches: Iterable[str]) -> None:
        self._green[tls_id] = set(green_approaches)

    def _apply_states(self) -> None:
        """Push the current green sets to SUMO as per-link state strings."""
        conn = self._conn
        for iid in self.topo.order:
            n = self._state_len.get(iid, 0)
            if n == 0:
                continue
            state = ["r"] * n
            for appr, idxs in self._appr_links[iid].items():
                if appr in self._green[iid]:
                    for i in idxs:
                        state[i] = "G"
            conn.trafficlight.setRedYellowGreenState(iid, "".join(state))

    def step(self) -> StepOutcome:
        conn = self._conn
        self._apply_states()
        conn.simulationStep()
        self._t = conn.simulation.getTime()
        outcome = StepOutcome(time=self._t)

        outcome.arrivals = int(conn.simulation.getDepartedNumber())

        # register newly departed vehicles (record their depart time + type)
        for vid in list(conn.simulation.getDepartedIDList()):
            self._veh_depart[vid] = self._t
            self._veh_wait[vid] = 0.0
            try:
                if conn.vehicle.getTypeID(vid) == "emergency":
                    self._known_emerg.add(vid)
            except Exception:
                pass

        # update last-known accumulated wait for every present vehicle
        for vid in conn.vehicle.getIDList():
            try:
                self._veh_wait[vid] = conn.vehicle.getAccumulatedWaitingTime(vid)
            except Exception:
                pass

        # handle completions (arrived this step) — attribute cached wait + time-in-net
        arrived_ids = list(conn.simulation.getArrivedIDList())
        outcome.completed = len(arrived_ids)
        for vid in arrived_ids:
            wait = self._veh_wait.pop(vid, 0.0)
            depart = self._veh_depart.pop(vid, self._t)
            is_e = vid in self._known_emerg
            self._acc.record_completion(wait, is_e, self._t - depart)
            if is_e and vid not in self._arrived_emerg:
                self._arrived_emerg.add(vid)
                outcome.emergency_cleared.append(vid)
                # credit the emergency clearance to the last junction on its path
                # (approximate: distribute to all junctions equally is unnecessary)

        self._collect_second()
        return outcome

    def _collect_second(self) -> None:
        conn = self._conn
        halted_total = 0
        for iid in self.topo.order:
            inter_halt = 0
            for lane in self._incoming_lanes(iid):
                inter_halt += conn.lane.getLastStepHaltingNumber(lane)
            halted_total += inter_halt
            self._delay[iid] += inter_halt * self._step_len

        veh_ids = conn.vehicle.getIDList()
        present = len(veh_ids)
        mean_speed = 0.0
        fuel_ml = 0.0
        co2_g = 0.0
        if present:
            speeds = [conn.vehicle.getSpeed(v) for v in veh_ids]
            mean_speed = sum(speeds) / present
            try:
                fuel_ml = sum(conn.vehicle.getFuelConsumption(v) for v in veh_ids) * self._step_len
                co2_g = sum(conn.vehicle.getCO2Emission(v) for v in veh_ids) * self._step_len / 1000.0
            except Exception:
                fuel_ml = 0.30 * halted_total
                co2_g = fuel_ml * 2.31
        self._acc.record_second(self._t, halted_total, present, mean_speed, fuel_ml, co2_g)

    def _incoming_lanes(self, iid: str) -> List[str]:
        conn = self._conn
        lanes: List[str] = []
        for link_tuple in conn.trafficlight.getControlledLinks(iid):
            if link_tuple:
                lanes.append(link_tuple[0][0])
        # unique preserve order
        seen, out = set(), []
        for ln in lanes:
            if ln not in seen:
                seen.add(ln)
                out.append(ln)
        return out

    # ------------------------------------------------------------------ #
    # queries
    # ------------------------------------------------------------------ #
    def _approach_lanes(self, tls_id: str, approach: str) -> List[str]:
        conn = self._conn
        idxs = self._appr_links.get(tls_id, {}).get(approach, [])
        links = conn.trafficlight.getControlledLinks(tls_id)
        lanes = []
        for i in idxs:
            if i < len(links) and links[i]:
                lanes.append(links[i][0][0])
        seen, out = set(), []
        for ln in lanes:
            if ln not in seen:
                seen.add(ln); out.append(ln)
        return out

    def queue(self, tls_id: str, approach: str) -> int:
        conn = self._conn
        return int(sum(conn.lane.getLastStepHaltingNumber(l)
                       for l in self._approach_lanes(tls_id, approach)))

    def waiting_time(self, tls_id: str, approach: str) -> float:
        conn = self._conn
        total = 0.0
        for lane in self._approach_lanes(tls_id, approach):
            for vid in conn.lane.getLastStepVehicleIDs(lane):
                total += conn.vehicle.getAccumulatedWaitingTime(vid)
        return float(total)

    def emergency_present(self, tls_id: str, approach: str) -> bool:
        conn = self._conn
        for lane in self._approach_lanes(tls_id, approach):
            for vid in conn.lane.getLastStepVehicleIDs(lane):
                try:
                    if conn.vehicle.getTypeID(vid) == "emergency":
                        return True
                except Exception:
                    pass
        return False

    def pop_delay(self, tls_id: str) -> float:
        d = self._delay[tls_id]
        self._delay[tls_id] = 0.0
        return d

    def pop_emergency_cleared(self, tls_id: str) -> int:
        n = self._emerg_cleared[tls_id]
        self._emerg_cleared[tls_id] = 0
        return n

    # ------------------------------------------------------------------ #
    # metrics, rendering, emergencies
    # ------------------------------------------------------------------ #
    def finalize(self) -> None:
        """Fold vehicles still in the network into the waiting-time average."""
        conn = self._conn
        try:
            veh_ids = conn.vehicle.getIDList()
            total_wait = sum(conn.vehicle.getAccumulatedWaitingTime(v) for v in veh_ids)
            self._acc.record_present_wait(total_wait, len(veh_ids))
        except Exception:
            pass

    def metrics(self) -> MetricsAccumulator:
        # throughput = vehicles that finished; track via arrived count over the run
        return self._acc

    def render_state(self) -> Dict:
        conn = self._conn
        intersections = {}
        for iid in self.topo.order:
            it = self.topo.get(iid)
            intersections[iid] = {
                "x": it.x, "y": it.y, "row": it.row, "col": it.col,
                "queues": {a: self.queue(iid, a) for a in APPROACHES},
                "waiting": {a: round(self.waiting_time(iid, a), 1) for a in APPROACHES},
                "emergency": {a: self.emergency_present(iid, a) for a in APPROACHES},
            }
        moving = []
        try:
            for vid in list(conn.vehicle.getIDList())[:400]:
                x, y = conn.vehicle.getPosition(vid)
                is_e = conn.vehicle.getTypeID(vid) == "emergency"
                moving.append({"x": round(x, 1), "y": round(y, 1), "e": is_e})
        except Exception:
            pass
        return {"time": self._t, "intersections": intersections, "moving": moving}

    def inject_emergency(self, corridor: Optional[str] = None) -> Optional[str]:
        conn = self._conn
        self._emerg_counter += 1
        vid = f"EMG{self._emerg_counter}"
        rows, cols = self.topo.grid_rows, self.topo.grid_cols
        if corridor is None:
            corridor = "ew"
        if corridor == "ew":
            row = rows // 2
            entry, appr = f"J{row}_0", "W"
        else:
            col = cols // 2
            entry, appr = f"J0_{col}", "N"
        from_edge = f"{entry}_{appr}cap__{entry}"
        to_edge = netgen._straight_exit_edge(self.topo, entry, appr)
        route_id = f"route_{vid}"
        try:
            conn.route.add(route_id, [from_edge, to_edge]) if to_edge else None
            conn.vehicle.add(vid, routeID=route_id, typeID="emergency",
                             departLane="best", departSpeed="max")
            self._known_emerg.add(vid)
            return vid
        except Exception:
            # if explicit 2-edge route fails, let SUMO find the path
            try:
                conn.vehicle.add(vid, routeID="", typeID="emergency")
                self._known_emerg.add(vid)
                return vid
            except Exception:
                return None
