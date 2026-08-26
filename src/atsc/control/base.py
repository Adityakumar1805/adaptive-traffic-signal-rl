"""Controller interface shared by every signal-control strategy.

A controller maps the current environment state to a *target green phase* per
intersection. The environment's safety FSM then realises that target safely (min/max
green, yellow, all-red). Keeping a single interface means the evaluation harness and the
dashboard can swap RL, fixed-time and max-pressure controllers interchangeably.
"""
from __future__ import annotations

import abc
from typing import Dict


class Controller(abc.ABC):
    """Abstract signal controller."""

    name: str = "base"

    def reset(self) -> None:
        """Reset any internal state at the start of an episode."""

    @abc.abstractmethod
    def act(self, env) -> Dict[str, int]:
        """Return a dict ``{intersection_id: target_phase_index}``."""

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Controller {self.name}>"
