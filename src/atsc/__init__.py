"""
atsc — AI-Based Adaptive Traffic Signal Control System using Reinforcement Learning.

A multi-agent Deep-RL controller (Double + Dueling DQN with Prioritized Experience
Replay) for coordinated traffic-signal control on a grid of intersections, running on
the SUMO microscopic simulator (primary) with a built-in point-queue simulator fallback,
benchmarked against fixed-time and max-pressure baselines, with a live web dashboard.

Package layout
--------------
    atsc.config      configuration loading / validation
    atsc.sim         simulation backends (SUMO + built-in) and network generation
    atsc.envs        Gymnasium / PettingZoo-style multi-agent environment + safety FSM
    atsc.agents      DQN network, prioritized replay, learner, optional QMIX
    atsc.control     controllers: fixed-time, max-pressure, RL, emergency preemption
    atsc.train       training loop + curriculum
    atsc.eval        benchmarking harness, metrics, plots
    atsc.vehicles    vehicle catalogue: Indian traffic mix + emergency types (cosmetic)
    atsc.dashboard   FastAPI + WebSocket real-time dashboard
"""

__version__ = "1.1.0"
__all__ = ["__version__"]
