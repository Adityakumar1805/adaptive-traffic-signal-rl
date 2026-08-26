"""Environment API + determinism tests (built-in backend, no torch needed)."""
import numpy as np

from atsc.envs.spaces import obs_size
from atsc.envs.traffic_env import MultiAgentTrafficEnv


def test_reset_returns_obs_per_agent(cfg):
    env = MultiAgentTrafficEnv(cfg, backend_name="mini")
    obs = env.reset(scenario="medium", seed=0)
    assert set(obs.keys()) == set(env.possible_agents)
    dim = obs_size(cfg.n_phases, bool(cfg.rl.neighbor_obs))
    for v in obs.values():
        assert v.shape == (dim,)
        assert np.isfinite(v).all()
    env.close()


def test_step_shapes_and_termination(cfg):
    env = MultiAgentTrafficEnv(cfg, backend_name="mini")
    env._episode_seconds = 200
    obs = env.reset(scenario="low", seed=1)
    steps = 0
    while env.agents:
        actions = {a: 0 for a in env.agents}
        obs, rewards, term, trunc, info = env.step(actions)
        assert set(rewards.keys()) == set(env.possible_agents)
        assert all(np.isfinite(list(rewards.values())))
        steps += 1
        assert steps < 1000
    assert steps == 200 // cfg.signal.decision_interval_s
    env.close()


def test_determinism_same_seed(cfg):
    def run():
        env = MultiAgentTrafficEnv(cfg, backend_name="mini")
        env._episode_seconds = 300
        env.reset(scenario="high", seed=7)
        while env.agents:
            env.step({a: 1 for a in env.agents})
        m = env.metrics()
        env.close()
        return m["avg_waiting_time"], m["throughput"]
    assert run() == run()


def test_metrics_are_sane(cfg):
    env = MultiAgentTrafficEnv(cfg, backend_name="mini")
    env._episode_seconds = 600
    env.reset(scenario="high", seed=0)
    while env.agents:
        # fixed alternating pattern
        ph = (int(env._elapsed) // 30) % 2
        env.step({a: ph for a in env.agents})
    m = env.metrics()
    assert m["throughput"] > 0
    assert m["avg_waiting_time"] >= 0
    assert m["avg_queue"] >= 0
    env.close()
