"""Reference Dueling-DQN network in idiomatic PyTorch (NOT on the executed path).

The network actually used at train/eval time is in :mod:`atsc.agents.net`
(``TorchQNet``/``NumpyQNet``), because that pair shares one portable checkpoint format so a
model trains and loads with or without PyTorch installed. This module is kept as the concise,
textbook statement of the same dueling architecture — useful for reading and for a
torch-only variant — and is imported by nothing.

The Dueling DQN (Wang et al., 2016) splits the Q head into a state-value stream V(s) and
an advantage stream A(s,a), recombined as

    Q(s,a) = V(s) + ( A(s,a) - mean_a A(s,a) )

This lets the network learn *which states are valuable* independently of the effect of
each action, which helps when many actions have similar value (common in signal
control, where "hold" vs "switch" are often close).
"""
from __future__ import annotations

from typing import List

import torch
import torch.nn as nn


class DuelingQNetwork(nn.Module):
    """MLP feature extractor + dueling value/advantage heads."""

    def __init__(self, obs_dim: int, n_actions: int, hidden_sizes: List[int]) -> None:
        super().__init__()
        self.obs_dim = int(obs_dim)
        self.n_actions = int(n_actions)

        layers: List[nn.Module] = []
        last = obs_dim
        for h in hidden_sizes:
            layers.append(nn.Linear(last, h))
            layers.append(nn.ReLU())
            last = h
        self.feature = nn.Sequential(*layers)

        self.value_head = nn.Sequential(
            nn.Linear(last, last), nn.ReLU(), nn.Linear(last, 1)
        )
        self.advantage_head = nn.Sequential(
            nn.Linear(last, last), nn.ReLU(), nn.Linear(last, n_actions)
        )
        self.apply(self._init_weights)

    @staticmethod
    def _init_weights(module: nn.Module) -> None:
        if isinstance(module, nn.Linear):
            nn.init.kaiming_uniform_(module.weight, nonlinearity="relu")
            if module.bias is not None:
                nn.init.zeros_(module.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        feat = self.feature(x)
        value = self.value_head(feat)                      # (B, 1)
        advantage = self.advantage_head(feat)              # (B, A)
        q = value + (advantage - advantage.mean(dim=1, keepdim=True))
        return q


class MLPQNetwork(nn.Module):
    """Plain (non-dueling) Q-network, kept for ablation via ``rl.dueling: false``."""

    def __init__(self, obs_dim: int, n_actions: int, hidden_sizes: List[int]) -> None:
        super().__init__()
        layers: List[nn.Module] = []
        last = obs_dim
        for h in hidden_sizes:
            layers.append(nn.Linear(last, h))
            layers.append(nn.ReLU())
            last = h
        layers.append(nn.Linear(last, n_actions))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def build_network(obs_dim: int, n_actions: int, hidden_sizes: List[int], dueling: bool):
    if dueling:
        return DuelingQNetwork(obs_dim, n_actions, hidden_sizes)
    return MLPQNetwork(obs_dim, n_actions, hidden_sizes)
