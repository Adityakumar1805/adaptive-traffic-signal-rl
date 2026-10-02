"""RL agents: Double + Dueling DQN with prioritized replay, and an optional QMIX upgrade.

The default learner is MultiAgentDQN (Double+Dueling DQN, shared parameters,
neighbour-aware observations). The network runs on PyTorch when available and
transparently falls back to a built-in NumPy network (atsc.agents.net). QMIX is available
behind the qmix.enabled config flag (PyTorch required for QMIX only).

This package imports cleanly without PyTorch installed.
"""
from atsc.agents.double_dqn_agent import DoubleDQNAgent
from atsc.agents.multi_agent import MultiAgentDQN
from atsc.agents.net import NumpyQNet, create_qnet, torch_available
from atsc.agents.policies import EpsilonGreedy
from atsc.agents.replay import PrioritizedReplayBuffer, UniformReplayBuffer


def build_learner(cfg, agent_ids, obs_dim, n_actions, device="cpu"):
    """Return the configured multi-agent learner (MultiAgentDQN or QMIXLearner)."""
    if bool(cfg.get_path("qmix.enabled", False)):
        try:
            from atsc.agents.qmix import QMIXLearner  # imported lazily (PyTorch required)
        except ImportError as exc:
            raise RuntimeError("qmix.enabled: true needs PyTorch (pip install torch); "
                               f"it could not be imported: {exc}") from None
        return QMIXLearner(cfg, agent_ids, obs_dim, n_actions, device=device)
    return MultiAgentDQN(cfg, agent_ids, obs_dim, n_actions, device=device)


__all__ = [
    "DoubleDQNAgent", "MultiAgentDQN", "build_learner",
    "NumpyQNet", "create_qnet", "torch_available",
    "EpsilonGreedy", "PrioritizedReplayBuffer", "UniformReplayBuffer",
]
