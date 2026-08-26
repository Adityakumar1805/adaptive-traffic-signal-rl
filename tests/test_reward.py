"""Reward-shaping tests: sign, penalties, and the emergency bonus."""
import numpy as np

from atsc.envs.traffic_env import MultiAgentTrafficEnv


def test_reward_is_nonpositive_without_emergency(cfg):
    """With no emergencies, reward = -(delay+pressure+switch terms) <= 0."""
    env = MultiAgentTrafficEnv(cfg, backend_name="mini")
    env._episode_seconds = 300
    obs = env.reset(scenario="high", seed=0)
    saw_negative = False
    while env.agents:
        _, rewards, _, _, _ = env.step({a: 0 for a in env.agents})
        for r in rewards.values():
            assert r <= 1e-6, "reward should be <= 0 without emergency clearance"
            if r < 0:
                saw_negative = True
    assert saw_negative, "expected some negative (congestion) reward at high density"
    env.close()


def test_switch_penalty_reduces_reward(cfg):
    """A policy that switches every decision should earn <= a hold policy, other things equal."""
    def total_reward(switch):
        env = MultiAgentTrafficEnv(cfg, backend_name="mini")
        env._episode_seconds = 400
        env.reset(scenario="low", seed=3)
        tot = 0.0
        i = 0
        while env.agents:
            if switch:
                act = {a: (i % 2) for a in env.agents}
            else:
                act = {a: 0 for a in env.agents}
            _, rewards, _, _, _ = env.step(act)
            tot += sum(rewards.values())
            i += 1
        env.close()
        return tot
    assert total_reward(switch=True) <= total_reward(switch=False) + 1e-6


def test_emergency_bonus_positive_component(cfg):
    """Clearing an emergency injects a positive reward component for some agent."""
    from atsc.control import EmergencyController
    env = MultiAgentTrafficEnv(cfg, backend_name="mini")
    env.set_emergency_controller(EmergencyController(cfg, env.topo))
    env._episode_seconds = 400
    env.reset(scenario="low", seed=2)
    env.inject_emergency("ew")
    best = -1e9
    while env.agents:
        _, rewards, _, _, _ = env.step({a: 0 for a in env.agents})
        best = max(best, max(rewards.values()))
    # at least one step should show a positive spike from the clearance bonus
    assert best > 0, "expected a positive reward spike when the emergency clears"
    env.close()
