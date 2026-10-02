"""Multi-agent wrapper coordinating one Double+Dueling DQN policy per intersection.

Modes (config rl.share_parameters):
  * shared (default): a single DoubleDQNAgent is used by every intersection and all
    intersections' transitions are pooled into one prioritized replay buffer. The
    intersections are homogeneous (same obs layout + action set), so sharing weights is
    far more sample-efficient -- a standard cooperative-MARL technique (shared-parameter
    independent Q-learning). It is still "one policy per intersection"; they share weights.
  * independent: one DoubleDQNAgent per intersection, each with its own net + buffer.

Coordination comes from the neighbour observations baked into the state (see
atsc.envs.spaces): each agent sees neighbours' pressures/phases, enabling green-waves.

Checkpoints are a portable dict of NumPy arrays (see atsc.agents.net), so a model trained
with either the NumPy or PyTorch backend loads under the other unchanged.
"""
from __future__ import annotations

import pickle
from typing import Dict, List, Optional

import numpy as np

from atsc.agents.double_dqn_agent import DoubleDQNAgent
from atsc.agents.policies import EpsilonGreedy


class _CompatUnpickler(pickle.Unpickler):
    """Unpickler that also reads checkpoints written by a newer NumPy.

    NumPy >= 2 pickles arrays with a ``numpy._core.*`` module reference; that module does
    not exist on NumPy 1.x (which ``requirements.txt`` pins for torch/SUMO ABI safety), so
    a plain ``pickle.load`` of a shipped checkpoint would raise ``ModuleNotFoundError``.
    The stored data is just float64 buffers, so falling back to the old ``numpy.core.*``
    location is safe. NumPy 2 loads take the normal path and are unaffected.
    """

    def find_class(self, module: str, name: str):
        try:
            return super().find_class(module, name)
        except ModuleNotFoundError:
            if module.startswith("numpy._core"):
                return super().find_class(
                    module.replace("numpy._core", "numpy.core", 1), name)
            raise


class MultiAgentDQN:
    """Owns the learner(s) and exposes act / observe / learn over agent-id dicts."""

    def __init__(self, cfg, agent_ids: List[str], obs_dim: int, n_actions: int,
                 device: str = "cpu") -> None:
        self.cfg = cfg
        self.agent_ids = list(agent_ids)
        self.obs_dim = int(obs_dim)
        self.n_actions = int(n_actions)
        self.device = device
        self.shared = bool(cfg.rl.share_parameters)
        self.train_every = int(cfg.rl.train_every)
        self._env_steps = 0

        self._epsilon = EpsilonGreedy(
            float(cfg.rl.epsilon_start), float(cfg.rl.epsilon_end),
            int(cfg.rl.epsilon_decay_steps),
        )

        if self.shared:
            self._agent = DoubleDQNAgent(cfg, obs_dim, n_actions, device=device,
                                         shared_epsilon=self._epsilon, seed=int(cfg.seed))
            self.agents = {aid: self._agent for aid in self.agent_ids}
            self.backend_name = self._agent.backend_name
        else:
            self._agent = None
            self.agents = {
                aid: DoubleDQNAgent(cfg, obs_dim, n_actions, device=device,
                                    shared_epsilon=self._epsilon, seed=int(cfg.seed) + i)
                for i, aid in enumerate(self.agent_ids)
            }
            self.backend_name = next(iter(self.agents.values())).backend_name

    # -- policy --
    def act(self, obs: Dict[str, np.ndarray], explore: bool = True) -> Dict[str, int]:
        return {aid: self.agents[aid].act(obs[aid], explore=explore) for aid in obs}

    def greedy(self, obs: Dict[str, np.ndarray]) -> Dict[str, int]:
        return self.act(obs, explore=False)

    def q_values(self, aid: str, obs: np.ndarray) -> np.ndarray:
        return self.agents[aid].q_values(obs)

    # -- experience & learning --
    def observe(self, obs, actions, rewards, next_obs, dones) -> None:
        for aid in self.agent_ids:
            done = dones[aid] if isinstance(dones, dict) else bool(dones)
            self.agents[aid].observe(obs[aid], actions[aid], rewards[aid],
                                     next_obs[aid], done)

    def learn(self) -> Optional[float]:
        self._env_steps += 1
        self._epsilon.step()
        if self._env_steps % self.train_every != 0:
            return None
        if self.shared:
            return self._agent.learn()
        losses = [a.learn() for a in self.agents.values()]
        losses = [l for l in losses if l is not None]
        return float(np.mean(losses)) if losses else None

    @property
    def epsilon(self) -> float:
        return self._epsilon.epsilon

    # -- persistence (portable pickle of numpy arrays) --
    def save(self, path: str, metadata: Optional[dict] = None) -> None:
        payload = {
            "format": "atsc-portable-v1",
            "shared": self.shared,
            "obs_dim": self.obs_dim,
            "n_actions": self.n_actions,
            "agent_ids": self.agent_ids,
            "metadata": metadata or {},
        }
        if self.shared:
            payload["agent"] = self._agent.state_dict()
        else:
            payload["agents"] = {aid: a.state_dict() for aid, a in self.agents.items()}
        with open(path, "wb") as fh:
            pickle.dump(payload, fh, protocol=4)

    def load(self, path: str, load_optimizer: bool = False) -> dict:
        """Load a checkpoint written by :meth:`save`, after checking that it fits the config.

        A checkpoint only works with the settings it was trained with; loading one under
        different ``rl.neighbor_obs`` / ``rl.share_parameters`` / network settings used to fail
        with an obscure shape error (or, for independent agents, silently leave some agents
        untrained). Now it raises one readable ``ValueError`` that names every mismatch.
        """
        try:
            with open(path, "rb") as fh:
                payload = _CompatUnpickler(fh).load()
        except (pickle.UnpicklingError, EOFError, AttributeError, ImportError, IndexError) as exc:
            raise ValueError(_incompatible(path, [f"not a checkpoint of this project ({exc})"])) from None
        if not isinstance(payload, dict) or payload.get("format") != "atsc-portable-v1":
            raise ValueError(_incompatible(path, [
                "it is not a Double+Dueling DQN checkpoint (a QMIX checkpoint needs "
                "qmix.enabled: true)"]))
        problems = []
        if int(payload.get("obs_dim", self.obs_dim)) != self.obs_dim:
            problems.append(f"observation size {payload['obs_dim']} in the checkpoint, {self.obs_dim} "
                            "with this config (rl.neighbor_obs or network.phase_scheme changed)")
        if int(payload.get("n_actions", self.n_actions)) != self.n_actions:
            problems.append(f"{payload['n_actions']} phases in the checkpoint, {self.n_actions} with "
                            "this config (network.phase_scheme changed)")
        shared = bool(payload.get("shared", True))
        if shared != self.shared:
            problems.append(f"rl.share_parameters is {str(shared).lower()} in the checkpoint, "
                            f"{str(self.shared).lower()} in config.yaml")
        if not shared and set(payload.get("agents", {})) != set(self.agent_ids):
            problems.append("the checkpoint's intersections do not match this grid")
        if problems:
            raise ValueError(_incompatible(path, problems))
        try:
            if shared:
                self._agent.load_state_dict(payload["agent"])
            else:
                for aid, sd in payload["agents"].items():
                    self.agents[aid].load_state_dict(sd)
        except (KeyError, ValueError, RuntimeError) as exc:
            raise ValueError(_incompatible(path, [
                f"the network layout differs (rl.hidden_sizes or rl.dueling changed): {exc}"])) from None
        return payload.get("metadata", {})


def _incompatible(path: str, problems: List[str]) -> str:
    from atsc.logging_utils import friendly_error
    return friendly_error(
        "RL checkpoint does not match config.yaml",
        f"{path}\n" + "\n".join(f"  - {p}" for p in problems),
        fix="Put the rl / network settings back to the ones the model was trained with, "
            "or train a model for the new settings:\n  python run.py train --quick")
