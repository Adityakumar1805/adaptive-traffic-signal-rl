"""Training curriculum: which density scenario to train on at each episode.

Cycling low -> medium -> high -> rush exposes the agents to progressively harder demand
(rush = *sustained* heavy demand; every scenario in ``config.yaml`` sets
``time_varying: false``). Cycling rather than sorting-then-fixing keeps the
replay buffer's distribution balanced across densities so the final policy generalises to
all of them instead of overfitting the last one it saw.
"""
from __future__ import annotations

from typing import List


class Curriculum:
    def __init__(self, scenarios: List[str]) -> None:
        assert scenarios, "curriculum needs at least one scenario"
        self.scenarios = list(scenarios)

    def scenario_for_episode(self, episode: int) -> str:
        return self.scenarios[episode % len(self.scenarios)]
