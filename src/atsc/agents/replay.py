"""Prioritized Experience Replay (PER) with a sum-tree.

Transitions are sampled with probability proportional to their TD-error priority, so the
learner spends more updates on surprising transitions. Importance-sampling (IS) weights
correct the bias this introduces. A binary sum-tree gives O(log n) sampling and update.

Reference: Schaul et al., "Prioritized Experience Replay", ICLR 2016.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import numpy as np


class SumTree:
    """Fixed-capacity binary tree where each leaf stores a priority and each internal
    node stores the sum of its children — enabling proportional sampling in O(log n)."""

    def __init__(self, capacity: int) -> None:
        self.capacity = int(capacity)
        self.tree = np.zeros(2 * self.capacity - 1, dtype=np.float64)
        self.data_pointer = 0
        self.size = 0

    def add(self, priority: float, data_index: int) -> None:
        tree_index = data_index + self.capacity - 1
        self.update(tree_index, priority)

    def update(self, tree_index: int, priority: float) -> None:
        change = priority - self.tree[tree_index]
        self.tree[tree_index] = priority
        # propagate the change up to the root
        while tree_index != 0:
            tree_index = (tree_index - 1) // 2
            self.tree[tree_index] += change

    def get_leaf(self, value: float) -> Tuple[int, float, int]:
        """Return (tree_index, priority, data_index) for a cumulative ``value``."""
        parent = 0
        while True:
            left = 2 * parent + 1
            right = left + 1
            if left >= len(self.tree):
                leaf = parent
                break
            if value <= self.tree[left]:
                parent = left
            else:
                value -= self.tree[left]
                parent = right
        data_index = leaf - (self.capacity - 1)
        return leaf, self.tree[leaf], data_index

    @property
    def total(self) -> float:
        return float(self.tree[0])


@dataclass
class Batch:
    states: np.ndarray
    actions: np.ndarray
    rewards: np.ndarray
    next_states: np.ndarray
    dones: np.ndarray
    indices: np.ndarray
    weights: np.ndarray


class PrioritizedReplayBuffer:
    """Ring buffer of transitions with proportional prioritisation."""

    def __init__(
        self,
        capacity: int,
        obs_dim: int,
        alpha: float = 0.6,
        beta_start: float = 0.4,
        beta_end: float = 1.0,
        beta_steps: int = 20000,
        eps: float = 1e-6,
    ) -> None:
        self.capacity = int(capacity)
        self.obs_dim = int(obs_dim)
        self.alpha = float(alpha)
        self.beta_start = float(beta_start)
        self.beta_end = float(beta_end)
        self.beta_steps = int(beta_steps)
        self.eps = float(eps)

        self.tree = SumTree(self.capacity)
        self.states = np.zeros((self.capacity, obs_dim), dtype=np.float32)
        self.next_states = np.zeros((self.capacity, obs_dim), dtype=np.float32)
        self.actions = np.zeros(self.capacity, dtype=np.int64)
        self.rewards = np.zeros(self.capacity, dtype=np.float32)
        self.dones = np.zeros(self.capacity, dtype=np.float32)

        self._max_priority = 1.0
        self._write = 0
        self._size = 0
        self._beta_step = 0

    def __len__(self) -> int:
        return self._size

    def add(self, state, action, reward, next_state, done) -> None:
        idx = self._write
        self.states[idx] = state
        self.actions[idx] = action
        self.rewards[idx] = reward
        self.next_states[idx] = next_state
        self.dones[idx] = float(done)
        # new transitions get max priority so they are seen at least once
        self.tree.add(self._max_priority ** self.alpha, idx)
        self._write = (self._write + 1) % self.capacity
        self._size = min(self._size + 1, self.capacity)

    def _beta(self) -> float:
        frac = min(1.0, self._beta_step / max(1, self.beta_steps))
        return self.beta_start + frac * (self.beta_end - self.beta_start)

    def sample(self, batch_size: int) -> Batch:
        assert self._size >= batch_size, "not enough samples in buffer"
        self._beta_step += 1
        beta = self._beta()

        indices = np.empty(batch_size, dtype=np.int64)
        tree_indices = np.empty(batch_size, dtype=np.int64)
        priorities = np.empty(batch_size, dtype=np.float64)
        segment = self.tree.total / batch_size

        for i in range(batch_size):
            a, b = segment * i, segment * (i + 1)
            value = np.random.uniform(a, b)
            tree_idx, priority, data_idx = self.tree.get_leaf(value)
            # guard against numerical edge cases (empty segment)
            if data_idx < 0 or data_idx >= self._size:
                data_idx = np.random.randint(0, self._size)
                tree_idx = data_idx + self.tree.capacity - 1
                priority = self.tree.tree[tree_idx]
            indices[i] = data_idx
            tree_indices[i] = tree_idx
            priorities[i] = max(priority, 1e-12)

        probs = priorities / self.tree.total
        weights = (self._size * probs) ** (-beta)
        weights /= weights.max()

        batch = Batch(
            states=self.states[indices],
            actions=self.actions[indices],
            rewards=self.rewards[indices],
            next_states=self.next_states[indices],
            dones=self.dones[indices],
            indices=tree_indices,
            weights=weights.astype(np.float32),
        )
        return batch

    def update_priorities(self, tree_indices: np.ndarray, td_errors: np.ndarray) -> None:
        priorities = (np.abs(td_errors) + self.eps) ** self.alpha
        for ti, p in zip(tree_indices, priorities):
            self.tree.update(int(ti), float(p))
            self._max_priority = max(self._max_priority, float(np.abs(p) ** (1.0 / self.alpha)))


class UniformReplayBuffer:
    """Simple uniform replay (used when ``rl.per.enabled`` is false)."""

    def __init__(self, capacity: int, obs_dim: int) -> None:
        self.capacity = int(capacity)
        self.states = np.zeros((capacity, obs_dim), dtype=np.float32)
        self.next_states = np.zeros((capacity, obs_dim), dtype=np.float32)
        self.actions = np.zeros(capacity, dtype=np.int64)
        self.rewards = np.zeros(capacity, dtype=np.float32)
        self.dones = np.zeros(capacity, dtype=np.float32)
        self._write = 0
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def add(self, state, action, reward, next_state, done) -> None:
        idx = self._write
        self.states[idx] = state
        self.actions[idx] = action
        self.rewards[idx] = reward
        self.next_states[idx] = next_state
        self.dones[idx] = float(done)
        self._write = (self._write + 1) % self.capacity
        self._size = min(self._size + 1, self.capacity)

    def sample(self, batch_size: int) -> Batch:
        indices = np.random.randint(0, self._size, size=batch_size)
        return Batch(
            states=self.states[indices],
            actions=self.actions[indices],
            rewards=self.rewards[indices],
            next_states=self.next_states[indices],
            dones=self.dones[indices],
            indices=indices,
            weights=np.ones(batch_size, dtype=np.float32),
        )

    def update_priorities(self, indices, td_errors) -> None:  # no-op for uniform
        return None
