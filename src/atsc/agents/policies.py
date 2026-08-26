"""Exploration policy: linear epsilon-greedy decay."""
from __future__ import annotations

import numpy as np


class EpsilonGreedy:
    """Linearly decays epsilon from ``start`` to ``end`` over ``decay_steps`` calls."""

    def __init__(self, start: float, end: float, decay_steps: int) -> None:
        self.start = float(start)
        self.end = float(end)
        self.decay_steps = max(1, int(decay_steps))
        self._steps = 0

    @property
    def epsilon(self) -> float:
        frac = min(1.0, self._steps / self.decay_steps)
        return self.start + frac * (self.end - self.start)

    def step(self) -> None:
        self._steps += 1

    def select(self, q_values: np.ndarray, n_actions: int, explore: bool = True) -> int:
        """Return an action index; explore with probability epsilon."""
        if explore and np.random.random() < self.epsilon:
            return int(np.random.randint(n_actions))
        return int(np.argmax(q_values))

    def state_dict(self) -> dict:
        return {"steps": self._steps, "start": self.start,
                "end": self.end, "decay_steps": self.decay_steps}

    def load_state_dict(self, d: dict) -> None:
        self._steps = int(d.get("steps", 0))
        self.start = float(d.get("start", self.start))
        self.end = float(d.get("end", self.end))
        self.decay_steps = int(d.get("decay_steps", self.decay_steps))
