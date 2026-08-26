"""Simulation backends and network generation.

Two interchangeable backends implement the same SimBackend interface and the same
state/metric schema:

  * SumoBackend  -- SUMO microscopic simulator (PRIMARY, via libsumo or traci);
  * MiniBackend  -- a self-contained NumPy point-queue simulator (FALLBACK) so the
    project runs with zero external dependencies.

Because the environment, agents, dashboard and evaluation code all talk to the
interface, the RL system behaves identically regardless of which backend is active.
"""
from atsc.sim.backend import (
    APPROACHES,
    NORTH,
    EAST,
    SOUTH,
    WEST,
    IntersectionTopo,
    MetricsAccumulator,
    NetworkTopo,
    PhaseDef,
    SimBackend,
    StepOutcome,
    build_topology,
)


def make_backend(cfg, topo=None, prefer=None, use_gui=False):
    """Return a ready backend honouring the backend policy.

    prefer is one of 'auto' | 'sumo' | 'mini' (defaults to cfg.backend). 'auto' tries
    SUMO and silently falls back to the built-in simulator if SUMO is unavailable.
    Returns a tuple (backend, backend_name).
    """
    from atsc.logging_utils import get_logger

    log = get_logger("atsc.sim")
    if topo is None:
        topo = build_topology(cfg)
    prefer = prefer or str(cfg.backend)

    def _mini():
        from atsc.sim.mini_backend import MiniBackend
        return MiniBackend(cfg, topo), "mini"

    if prefer == "mini":
        return _mini()

    if prefer in ("sumo", "auto"):
        from atsc.sim.sumo_detect import detect_sumo, install_instructions
        info = detect_sumo()
        if info.found and (info.has_libsumo or info.has_traci):
            try:
                from atsc.sim.sumo_backend import SumoBackend
                log.info("Using SUMO backend (%s).",
                         "libsumo" if info.has_libsumo and not use_gui else "traci")
                return SumoBackend(cfg, topo, use_gui=use_gui), "sumo"
            except Exception as exc:  # pragma: no cover - depends on SUMO install
                if prefer == "sumo":
                    raise
                log.warning("SUMO backend failed to initialise (%s); "
                            "falling back to built-in simulator.", exc)
                return _mini()
        if prefer == "sumo":
            raise RuntimeError(install_instructions())
        log.warning("SUMO not detected; using the built-in simulator fallback. "
                    "(This is expected if SUMO is not installed.)")
        return _mini()

    raise ValueError(f"Unknown backend preference '{prefer}'.")


__all__ = [
    "APPROACHES", "NORTH", "EAST", "SOUTH", "WEST",
    "PhaseDef", "IntersectionTopo", "NetworkTopo",
    "SimBackend", "StepOutcome", "MetricsAccumulator", "build_topology",
    "make_backend",
]
