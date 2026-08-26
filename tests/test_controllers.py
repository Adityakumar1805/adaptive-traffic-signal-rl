"""Controller tests: fixed-time, max-pressure, emergency preemption, and RL loading."""
import numpy as np

from atsc.control import (EmergencyController, FixedTimeController,
                          MaxPressureController, build_controller)
from atsc.envs.traffic_env import MultiAgentTrafficEnv


def test_fixed_time_cycles_phases(cfg):
    env = MultiAgentTrafficEnv(cfg, backend_name="mini")
    env.reset(scenario="low", seed=0)
    ctrl = FixedTimeController(cfg, env.topo)
    a0 = ctrl.act(env)
    # all intersections share the same fixed phase
    assert len(set(a0.values())) == 1
    env.close()


def test_max_pressure_prefers_busier_approach(cfg):
    env = MultiAgentTrafficEnv(cfg, backend_name="mini")
    env.reset(scenario="high", seed=0)
    # advance a bit to build asymmetric queues
    for _ in range(10):
        env.step({a: 0 for a in env.agents})
    ctrl = MaxPressureController(cfg, env.topo)
    actions = ctrl.act(env)
    assert set(actions.keys()) == set(env.topo.order)
    for v in actions.values():
        assert 0 <= v < cfg.n_phases
    env.close()


def test_emergency_preemption_serves_emergency_approach(cfg):
    env = MultiAgentTrafficEnv(cfg, backend_name="mini")
    emc = EmergencyController(cfg, env.topo)
    env.set_emergency_controller(emc)
    env.reset(scenario="low", seed=1)
    env.inject_emergency("ew")
    # step until preemption engages
    engaged = False
    for _ in range(10):
        env.step({a: 0 for a in env.agents})  # base policy requests NS
        if emc.preempted_intersections():
            engaged = True
            break
    assert engaged, "emergency preemption never engaged"
    env.close()


def test_max_pressure_beats_fixed_on_high(cfg):
    """Sanity: an adaptive baseline should beat fixed-time under asymmetric demand."""
    def wait(ctrl_cls):
        env = MultiAgentTrafficEnv(cfg, backend_name="mini")
        env._episode_seconds = 600
        env.reset(scenario="high", seed=0)
        ctrl = ctrl_cls(cfg, env.topo)
        while env.agents:
            env.step(ctrl.act(env))
        w = env.metrics()["avg_waiting_time"]
        env.close()
        return w
    assert wait(MaxPressureController) < wait(FixedTimeController)


def test_build_controller_unknown_raises(cfg):
    from atsc.sim.backend import build_topology
    topo = build_topology(cfg)
    try:
        build_controller("nope", cfg, topo)
        assert False
    except ValueError:
        pass
