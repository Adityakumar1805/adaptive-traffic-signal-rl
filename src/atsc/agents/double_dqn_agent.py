"""A single Double + Dueling DQN learner with Prioritized Experience Replay.

Backend-agnostic: the network forward/gradient step is delegated to a NumpyQNet or
TorchQNet (selected by rl.nn_backend), so the same learning logic runs with or without
PyTorch. Combines Double DQN (online selects, target evaluates the next action), a
Dueling network, prioritized replay with importance-sampling weights, a periodically
hard-synced target network, Huber loss and gradient clipping.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from atsc.agents.net import create_qnet
from atsc.agents.policies import EpsilonGreedy
from atsc.agents.replay import PrioritizedReplayBuffer, UniformReplayBuffer


class DoubleDQNAgent:
    """One learner; shared across intersections (shared params) or one per intersection."""

    def __init__(self, cfg, obs_dim: int, n_actions: int, device: str = "cpu",
                 shared_epsilon: Optional[EpsilonGreedy] = None, seed: int = 0) -> None:
        self.cfg = cfg
        self.obs_dim = int(obs_dim)
        self.n_actions = int(n_actions)
        rl = cfg.rl

        self.gamma = float(rl.gamma)
        self.batch_size = int(rl.batch_size)
        self.target_update_every = int(rl.target_update_every)
        self.double = bool(rl.double)

        self.online, self.backend_name = create_qnet(cfg, obs_dim, n_actions, seed=seed)
        self.target, _ = create_qnet(cfg, obs_dim, n_actions, seed=seed + 1)
        self.target.copy_weights_from(self.online)

        if bool(rl.per.enabled):
            self.buffer = PrioritizedReplayBuffer(
                capacity=int(rl.buffer_size), obs_dim=obs_dim,
                alpha=float(rl.per.alpha), beta_start=float(rl.per.beta_start),
                beta_end=float(rl.per.beta_end), beta_steps=int(rl.per.beta_steps),
                eps=float(rl.per.eps),
            )
            self.prioritized = True
        else:
            self.buffer = UniformReplayBuffer(int(rl.buffer_size), obs_dim)
            self.prioritized = False

        self.epsilon = shared_epsilon or EpsilonGreedy(
            float(rl.epsilon_start), float(rl.epsilon_end), int(rl.epsilon_decay_steps)
        )
        self.min_buffer = int(rl.min_buffer)
        self.learner_steps = 0
        self.last_loss = 0.0

    # -- action --
    def act(self, obs: np.ndarray, explore: bool = True) -> int:
        q = self.online.q(np.asarray(obs)[None, :])[0]
        return self.epsilon.select(q, self.n_actions, explore=explore)

    def q_values(self, obs: np.ndarray) -> np.ndarray:
        return self.online.q(np.asarray(obs)[None, :])[0]

    # -- storage --
    def observe(self, state, action, reward, next_state, done) -> None:
        self.buffer.add(state, action, reward, next_state, done)

    # -- learning --
    def learn(self) -> Optional[float]:
        if len(self.buffer) < max(self.min_buffer, self.batch_size):
            return None
        batch = self.buffer.sample(self.batch_size)

        next_q_online = self.online.q(batch.next_states)
        next_actions = np.argmax(next_q_online, axis=1)
        next_q_target = self.target.q(batch.next_states)
        idx = np.arange(len(next_actions))
        if self.double:
            next_q = next_q_target[idx, next_actions]
        else:
            next_q = next_q_target.max(axis=1)
        targets = batch.rewards + self.gamma * (1.0 - batch.dones) * next_q

        loss, td_errors = self.online.train_batch(
            batch.states, batch.actions, targets, batch.weights
        )
        if self.prioritized:
            self.buffer.update_priorities(batch.indices, td_errors)

        self.learner_steps += 1
        if self.learner_steps % self.target_update_every == 0:
            self.target.copy_weights_from(self.online)
        self.last_loss = float(loss)
        return self.last_loss

    # -- persistence (portable: dict of numpy arrays) --
    def state_dict(self) -> dict:
        return {
            "online": self.online.get_weights(),
            "target": self.target.get_weights(),
            "epsilon": self.epsilon.state_dict(),
            "learner_steps": self.learner_steps,
        }

    def load_state_dict(self, d: dict, load_optimizer: bool = True) -> None:
        self.online.set_weights(d["online"])
        self.target.set_weights(d.get("target", d["online"]))
        if "epsilon" in d:
            self.epsilon.load_state_dict(d["epsilon"])
        self.learner_steps = int(d.get("learner_steps", 0))
