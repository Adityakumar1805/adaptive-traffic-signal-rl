"""Fixed-time controller — the honest, standard baseline.

Cycles through the phases giving each a fixed green split (``fixed_time.green_split_s``),
identical at every intersection and independent of traffic. This is the conventional
signal-timing scheme the project aims to beat. It is *not* deliberately crippled: it uses
a sensible 30 s split and the same yellow/all-red clearance as every other controller.
Its weakness is simply that it cannot adapt to asymmetric or time-varying demand.
"""
from __future__ import annotations

from typing import Dict

from atsc.control.base import Controller


class FixedTimeController(Controller):
    name = "fixed_time"

    def __init__(self, cfg, topo) -> None:
        self.cfg = cfg
        self.topo = topo
        self.n_phases = cfg.n_phases
        self.green_split = int(cfg.fixed_time.green_split_s)

    def act(self, env) -> Dict[str, int]:
        # target phase advances every green_split seconds of simulated time, in lockstep
        t = int(env._elapsed)
        phase = (t // self.green_split) % self.n_phases
        return {iid: phase for iid in self.topo.order}
