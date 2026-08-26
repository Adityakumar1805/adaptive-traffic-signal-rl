"""Emergency-vehicle preemption — a deterministic safe-corridor override.

Real preemption systems (e.g. Opticom) are rule-based overrides layered on top of the
normal controller, and that is exactly what this implements. When an emergency vehicle is
detected on an approach to an intersection, the environment's FSM for that intersection
is forced towards the phase that serves the emergency's approach — creating a green
corridor ahead of the vehicle. The safety FSM still inserts yellow + all-red, so the
override never causes an unsafe transition.

This override wraps *any* base controller (RL, fixed-time, max-pressure), and the RL
reward additionally gives a bonus for quickly clearing emergencies, so the learned policy
is emergency-aware even before the override kicks in. The evaluation harness measures each
emergency vehicle's clearance time to quantify the benefit.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

from atsc.sim.backend import APPROACHES


class EmergencyController:
    """Preemption override attached to the environment via ``set_emergency_controller``."""

    def __init__(self, cfg, topo) -> None:
        self.cfg = cfg
        self.topo = topo
        self.enabled = bool(cfg.emergency.preemption)
        self.active: Set[str] = set()          # intersections currently preempted
        self._last_corridor: Optional[str] = None

    def reset(self) -> None:
        self.active = set()
        self._last_corridor = None

    def notify_injection(self, corridor: str) -> None:
        self._last_corridor = corridor

    def apply(self, env) -> None:
        """Override FSM targets for any intersection with an emergency present."""
        if not self.enabled:
            return
        backend = env.backend
        self.active = set()
        for iid in self.topo.order:
            it = self.topo.get(iid)
            emg_approach = None
            for a in APPROACHES:
                if backend.emergency_present(iid, a):
                    emg_approach = a
                    break
            if emg_approach is None:
                continue
            # find the phase serving that approach and force it
            for phase in it.phases:
                if emg_approach in phase.green_approaches:
                    env.fsms[iid].request(phase.index)
                    self.active.add(iid)
                    break

    def preempted_intersections(self) -> List[str]:
        return sorted(self.active)
