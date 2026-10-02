"""Run a single controlled episode and collect its KPIs.

Shared by the benchmarking harness and the training-time evaluation. Optionally injects
an emergency vehicle partway through the episode so emergency clearance time can be
measured under each controller.
"""
from __future__ import annotations

from typing import Dict, Optional

from atsc.control import EmergencyController, build_controller
from atsc.envs.traffic_env import MultiAgentTrafficEnv


def run_controlled_episode(
    cfg,
    controller_name: str,
    scenario: str,
    seed: int,
    backend_name: Optional[str] = None,
    checkpoint: Optional[str] = None,
    inject_emergency: bool = False,
    emergency_time_frac: float = 0.4,
    with_preemption: bool = True,
) -> Dict[str, float]:
    """Run one episode under a controller and return its metric dict.

    The episode lasts ``eval.episode_seconds`` (falling back to ``sim.episode_seconds``);
    with ``inject_emergency`` one ambulance is sent along the east-west corridor after
    ``emergency_time_frac`` of it.
    """
    env = MultiAgentTrafficEnv(cfg, backend_name=backend_name)
    episode_s = int(cfg.get_path("eval.episode_seconds") or cfg.sim.episode_seconds)
    env._episode_seconds = episode_s
    topo = env.topo

    ctrl_kwargs = {}
    if controller_name == "rl" and checkpoint is not None:
        ctrl_kwargs["checkpoint"] = checkpoint
    controller = build_controller(controller_name, cfg, topo, **ctrl_kwargs)
    controller.reset()

    if inject_emergency and with_preemption:
        env.set_emergency_controller(EmergencyController(cfg, topo))

    env.reset(scenario=scenario, seed=seed)
    inject_at = emergency_time_frac * episode_s
    injected = False

    while env.agents:
        if inject_emergency and not injected and env._elapsed >= inject_at:
            env.inject_emergency("ew")
            injected = True
        actions = controller.act(env)
        env.step(actions)

    metrics = dict(env.metrics())
    metrics["controller"] = controller_name
    metrics["scenario"] = scenario
    metrics["seed"] = seed
    env.close()
    return metrics
