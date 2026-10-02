"""Max-pressure controller — a strong, well-known adaptive baseline.

At each decision it selects, per intersection, the phase whose served approaches have the
greatest *pressure* (upstream queue minus downstream queue). Max-pressure is provably
throughput-optimal under idealised assumptions and is a much tougher baseline than
fixed-time — beating it is a meaningful result for the RL controller.

For a served approach the upstream queue is the queue on that approach; the downstream queue
is the one its straight-through traffic joins at the next junction (vehicles arriving from
the north travel south, so they join the southern neighbour's North approach). Boundary exits
have no downstream queue.

Reference: Varaiya, "Max pressure control of a network of signalized intersections",
Transportation Research Part C, 2013.
"""
from __future__ import annotations

from typing import Dict

from atsc.control.base import Controller
from atsc.sim.backend import APPROACH_TO_TRAVEL, TRAVEL_TO_APPROACH


class MaxPressureController(Controller):
    name = "max_pressure"

    def __init__(self, cfg, topo) -> None:
        self.cfg = cfg
        self.topo = topo

    def act(self, env) -> Dict[str, int]:
        backend = env.backend
        actions: Dict[str, int] = {}
        for iid in self.topo.order:
            it = self.topo.get(iid)
            best_phase, best_pressure = 0, -1e9
            for phase in it.phases:
                pressure = 0.0
                for approach in phase.green_approaches:
                    up = backend.queue(iid, approach)
                    travel = APPROACH_TO_TRAVEL[approach]          # e.g. from N -> heading S
                    neigh = it.neighbors.get(travel)               # the junction it drives to
                    down = 0
                    if neigh is not None:
                        down = backend.queue(neigh, TRAVEL_TO_APPROACH[travel])
                    pressure += up - down
                if pressure > best_pressure:
                    best_pressure = pressure
                    best_phase = phase.index
            actions[iid] = best_phase
        return actions
