"""Signal controllers: fixed-time, max-pressure, RL, plus emergency preemption."""
from atsc.control.base import Controller
from atsc.control.emergency import EmergencyController
from atsc.control.fixed_time import FixedTimeController
from atsc.control.max_pressure import MaxPressureController


def build_controller(name: str, cfg, topo, **kwargs) -> Controller:
    """Factory: return a controller by name ('fixed_time'|'max_pressure'|'rl')."""
    name = name.lower()
    if name in ("fixed_time", "fixed", "fixedtime"):
        return FixedTimeController(cfg, topo)
    if name in ("max_pressure", "maxpressure", "mp"):
        return MaxPressureController(cfg, topo)
    if name in ("rl", "dqn"):
        from atsc.control.rl_controller import RLController
        return RLController(cfg, topo, **kwargs)
    raise ValueError(f"Unknown controller '{name}'. "
                     f"Choose from: fixed_time, max_pressure, rl.")


__all__ = [
    "Controller", "FixedTimeController", "MaxPressureController",
    "EmergencyController", "build_controller",
]
