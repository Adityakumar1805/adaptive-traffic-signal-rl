"""QMIX — optional centralised-training / decentralised-execution (CTDE) upgrade.

STRETCH GOAL, OFF BY DEFAULT (enable with ``qmix.enabled: true``). QMIX (Rashid et al.,
2018) trains cooperative agents by mixing their individual Q-values into a joint
``Q_tot`` through a hypernetwork-parameterised **monotonic** mixing network. Monotonicity
(non-negative mixing weights) guarantees that ``argmax`` over each agent's local Q also
maximises ``Q_tot``, so execution stays fully decentralised.

This module is a runnable implementation used only when QMIX is enabled (it needs PyTorch;
``tests/test_agents_compat.py`` trains it for a few steps). It is not what produced the
shipped checkpoint or any published number: that is independent Double+Dueling DQN with
neighbour observations (:mod:`atsc.agents.multi_agent`), and the shipped checkpoint does
not load into QMIX - train a QMIX model of its own first.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from atsc.agents.dqn import build_network
from atsc.agents.policies import EpsilonGreedy


class QMixer(nn.Module):
    """Monotonic mixing network with hypernetworks conditioned on the global state."""

    def __init__(self, n_agents: int, state_dim: int, embed_dim: int = 32) -> None:
        super().__init__()
        self.n_agents = n_agents
        self.state_dim = state_dim
        self.embed_dim = embed_dim

        # hypernetworks produce (non-negative) weights and biases for the mixer
        self.hyper_w1 = nn.Linear(state_dim, n_agents * embed_dim)
        self.hyper_w2 = nn.Linear(state_dim, embed_dim)
        self.hyper_b1 = nn.Linear(state_dim, embed_dim)
        self.hyper_b2 = nn.Sequential(
            nn.Linear(state_dim, embed_dim), nn.ReLU(), nn.Linear(embed_dim, 1)
        )

    def forward(self, agent_qs: torch.Tensor, state: torch.Tensor) -> torch.Tensor:
        # agent_qs: (B, n_agents), state: (B, state_dim)
        b = agent_qs.size(0)
        w1 = torch.abs(self.hyper_w1(state)).view(b, self.n_agents, self.embed_dim)
        b1 = self.hyper_b1(state).view(b, 1, self.embed_dim)
        hidden = F.elu(torch.bmm(agent_qs.view(b, 1, self.n_agents), w1) + b1)  # (B,1,E)

        w2 = torch.abs(self.hyper_w2(state)).view(b, self.embed_dim, 1)
        b2 = self.hyper_b2(state).view(b, 1, 1)
        q_tot = torch.bmm(hidden, w2) + b2                                       # (B,1,1)
        return q_tot.view(b, 1)


class QMIXLearner:
    """Centralised QMIX learner over homogeneous agents sharing one local Q-network."""

    def __init__(self, cfg, agent_ids: List[str], obs_dim: int, n_actions: int,
                 device: str = "cpu") -> None:
        self.cfg = cfg
        self.agent_ids = list(agent_ids)
        self.n_agents = len(agent_ids)
        self.obs_dim = int(obs_dim)
        self.n_actions = int(n_actions)
        self.device = torch.device(device)
        rl = cfg.rl
        self.gamma = float(rl.gamma)
        self.batch_size = int(rl.batch_size)
        self.grad_clip = float(rl.grad_clip)
        self.target_update_every = int(rl.target_update_every)

        hidden = list(rl.hidden_sizes)
        self.q = build_network(obs_dim, n_actions, hidden, bool(rl.dueling)).to(self.device)
        self.q_target = build_network(obs_dim, n_actions, hidden, bool(rl.dueling)).to(self.device)
        self.q_target.load_state_dict(self.q.state_dict())

        state_dim = obs_dim * self.n_agents
        embed = int(cfg.qmix.mixing_embed_dim)
        self.mixer = QMixer(self.n_agents, state_dim, embed).to(self.device)
        self.mixer_target = QMixer(self.n_agents, state_dim, embed).to(self.device)
        self.mixer_target.load_state_dict(self.mixer.state_dict())

        params = list(self.q.parameters()) + list(self.mixer.parameters())
        self.optimizer = torch.optim.Adam(params, lr=float(rl.lr))
        self._eps = EpsilonGreedy(float(rl.epsilon_start), float(rl.epsilon_end),
                                  int(rl.epsilon_decay_steps))
        self.backend_name = "torch"

        # one flat transition per timestep holds all agents' obs/actions
        self.buffer: List[dict] = []
        self.capacity = int(rl.buffer_size)
        self.min_buffer = int(rl.min_buffer)
        self.learner_steps = 0
        self.last_loss = 0.0

    @torch.no_grad()
    def act(self, obs: Dict[str, np.ndarray], explore: bool = True) -> Dict[str, int]:
        actions = {}
        for aid in self.agent_ids:
            q = self.q(torch.as_tensor(obs[aid], dtype=torch.float32,
                                       device=self.device).unsqueeze(0)).cpu().numpy()[0]
            actions[aid] = self._eps.select(q, self.n_actions, explore=explore)
        return actions

    @property
    def epsilon(self) -> float:
        """Current exploration rate (a float, like MultiAgentDQN.epsilon - the trainer logs it)."""
        return self._eps.epsilon

    def greedy(self, obs):
        return self.act(obs, explore=False)

    def observe(self, obs, actions, rewards, next_obs, dones) -> None:
        team_reward = float(np.mean([rewards[a] for a in self.agent_ids]))
        done = dones[self.agent_ids[0]] if isinstance(dones, dict) else bool(dones)
        self.buffer.append({
            "obs": np.stack([obs[a] for a in self.agent_ids]),
            "actions": np.array([actions[a] for a in self.agent_ids], dtype=np.int64),
            "reward": team_reward,
            "next_obs": np.stack([next_obs[a] for a in self.agent_ids]),
            "done": float(done),
        })
        if len(self.buffer) > self.capacity:
            self.buffer.pop(0)

    def learn(self) -> Optional[float]:
        self._eps.step()
        if len(self.buffer) < max(self.min_buffer, self.batch_size):
            return None
        idx = np.random.randint(0, len(self.buffer), size=self.batch_size)
        obs = torch.as_tensor(np.stack([self.buffer[i]["obs"] for i in idx]),
                              dtype=torch.float32, device=self.device)
        actions = torch.as_tensor(np.stack([self.buffer[i]["actions"] for i in idx]),
                                  dtype=torch.int64, device=self.device)
        rewards = torch.as_tensor(np.array([self.buffer[i]["reward"] for i in idx]),
                                  dtype=torch.float32, device=self.device).unsqueeze(1)
        next_obs = torch.as_tensor(np.stack([self.buffer[i]["next_obs"] for i in idx]),
                                   dtype=torch.float32, device=self.device)
        dones = torch.as_tensor(np.array([self.buffer[i]["done"] for i in idx]),
                                dtype=torch.float32, device=self.device).unsqueeze(1)

        b = obs.size(0)
        flat_obs = obs.view(b * self.n_agents, self.obs_dim)
        q_all = self.q(flat_obs).view(b, self.n_agents, self.n_actions)
        chosen = q_all.gather(2, actions.unsqueeze(2)).squeeze(2)              # (B, n)
        state = obs.view(b, -1)
        q_tot = self.mixer(chosen, state)

        with torch.no_grad():
            flat_next = next_obs.view(b * self.n_agents, self.obs_dim)
            next_q_all = self.q_target(flat_next).view(b, self.n_agents, self.n_actions)
            next_max = next_q_all.max(dim=2)[0]                                # (B, n)
            next_state = next_obs.view(b, -1)
            next_q_tot = self.mixer_target(next_max, next_state)
            target = rewards + self.gamma * (1.0 - dones) * next_q_tot

        loss = F.smooth_l1_loss(q_tot, target)
        self.optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(
            list(self.q.parameters()) + list(self.mixer.parameters()), self.grad_clip
        )
        self.optimizer.step()

        self.learner_steps += 1
        if self.learner_steps % self.target_update_every == 0:
            self.q_target.load_state_dict(self.q.state_dict())
            self.mixer_target.load_state_dict(self.mixer.state_dict())
        self.last_loss = float(loss.item())
        return self.last_loss

    def save(self, path: str, metadata: Optional[dict] = None) -> None:
        torch.save({
            "q": self.q.state_dict(), "mixer": self.mixer.state_dict(),
            "obs_dim": self.obs_dim, "n_actions": self.n_actions,
            "agent_ids": self.agent_ids, "algo": "qmix", "metadata": metadata or {},
        }, path)

    def load(self, path: str, load_optimizer: bool = False) -> dict:
        from atsc.logging_utils import friendly_error
        try:
            p = torch.load(path, map_location=self.device)
        except Exception as exc:  # e.g. the shipped DQN checkpoint (a plain pickle)
            p = {"_error": str(exc)}
        if not isinstance(p, dict) or p.get("algo") != "qmix":
            raise ValueError(friendly_error(
                "Checkpoint is not a QMIX model",
                f"{path}\nqmix.enabled is true, but this file was not trained with QMIX "
                "(the shipped models/pretrained checkpoint is a Double+Dueling DQN).",
                fix="Set qmix.enabled: false to use the shipped model, or train a QMIX model:\n"
                    "  python run.py train --quick   (with qmix.enabled: true)"))
        if int(p.get("obs_dim", self.obs_dim)) != self.obs_dim or \
                int(p.get("n_actions", self.n_actions)) != self.n_actions:
            raise ValueError(friendly_error(
                "QMIX checkpoint does not match config.yaml", path,
                fix="Restore the settings it was trained with, or retrain."))
        self.q.load_state_dict(p["q"])
        self.q_target.load_state_dict(p["q"])
        self.mixer.load_state_dict(p["mixer"])
        self.mixer_target.load_state_dict(p["mixer"])
        return p.get("metadata", {})
