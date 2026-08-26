"""Observation / action space construction and the per-agent state vector.

The state fed to each intersection's agent is assembled here so the exact layout is in
one documented place (and testable). For agent *i* the observation is the concatenation
of:

    [ per-approach queue (normalised) ]            # 4 values (N,E,S,W)
    [ per-approach waiting time (normalised) ]     # 4 values
    [ current-phase one-hot ]                      # n_phases values
    [ green-time-so-far / max_green ]              # 1 value
    [ per-approach emergency flag ]                # 4 values
    [ neighbour pressure (normalised), 4 sides ]   # 4 values (0 where no neighbour)
    [ neighbour active-phase index / n_phases ]    # 4 values

With the default 2-phase scheme this is 4+4+2+1+4+4+4 = 23 features per agent.
Set ``rl.neighbor_obs: false`` to drop the last 8 (coordination) features.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np

from atsc.sim.backend import APPROACHES, NetworkTopo

# normalisation constants (state features are scaled to roughly [0,1])
QUEUE_NORM = 20.0        # ~ a long queue on one approach
WAIT_NORM = 100.0        # ~ a long cumulative wait (s)
PRESSURE_NORM = 40.0


def obs_size(n_phases: int, neighbor_obs: bool) -> int:
    base = len(APPROACHES) * 2 + n_phases + 1 + len(APPROACHES)  # queue,wait,phase,green,emg
    if neighbor_obs:
        base += len(APPROACHES) * 2  # neighbour pressure + neighbour phase per side
    return base


def build_observation(
    backend,
    topo: NetworkTopo,
    tls_id: str,
    fsm,
    fsms: Dict[str, "object"],
    neighbor_obs: bool,
) -> np.ndarray:
    """Assemble the normalised observation vector for one intersection."""
    it = topo.get(tls_id)
    feats: List[float] = []

    # 1) queues per approach
    for a in APPROACHES:
        feats.append(min(1.0, backend.queue(tls_id, a) / QUEUE_NORM))
    # 2) waiting time per approach
    for a in APPROACHES:
        feats.append(min(1.0, backend.waiting_time(tls_id, a) / WAIT_NORM))
    # 3) current phase one-hot
    feats.extend(fsm.phase_onehot)
    # 4) normalised green time so far
    feats.append(fsm.time_since_change_frac())
    # 5) emergency flags per approach
    for a in APPROACHES:
        feats.append(1.0 if backend.emergency_present(tls_id, a) else 0.0)

    # 6) neighbour coordination features
    if neighbor_obs:
        for a in APPROACHES:
            neigh = it.neighbors.get(a)
            if neigh is None:
                feats.append(0.0)  # pressure
            else:
                p = backend.intersection_pressure(neigh)
                feats.append(float(np.tanh(p / PRESSURE_NORM)))
        for a in APPROACHES:
            neigh = it.neighbors.get(a)
            if neigh is None or neigh not in fsms:
                feats.append(0.0)
            else:
                nf = fsms[neigh]
                feats.append(nf.current / max(1, nf.n_phases))

    return np.asarray(feats, dtype=np.float32)
