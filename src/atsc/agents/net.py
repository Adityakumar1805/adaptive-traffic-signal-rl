"""Pluggable Q-network backend: PyTorch (primary) or pure-NumPy (fallback).

The declared stack is PyTorch, and :class:`TorchQNet` is used whenever PyTorch is
importable. But because a hands-off demo machine (or a locked-down college lab) might not
have PyTorch installed, we also ship :class:`NumpyQNet` — a from-scratch dueling MLP with
manual back-propagation and an Adam optimiser — so the *entire* RL system (training,
inference, dashboard) still runs with nothing but NumPy. This mirrors the SUMO/built-in
simulator dual-path design.

Both backends expose the same tiny interface and, crucially, the **same portable weight
format** (a dict of NumPy arrays with identical keys/shapes). A checkpoint trained with
one backend therefore loads into the other unchanged — e.g. a NumPy-trained checkpoint
runs on a PyTorch machine and vice-versa.

Interface
---------
    q(X)                      -> (B, A) Q-values (inference)
    train_batch(s, a, tgt, w) -> (loss, td_errors) : one optimiser step
    get_weights() / set_weights(dict)
    copy_weights_from(other)  : hard target-network sync
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np


# --------------------------------------------------------------------------- #
# NumPy backend
# --------------------------------------------------------------------------- #
class NumpyQNet:
    """Dueling MLP implemented in NumPy with manual backprop + Adam."""

    def __init__(self, obs_dim: int, n_actions: int, hidden_sizes: List[int],
                 lr: float = 5e-4, dueling: bool = True, seed: int = 0,
                 adam_betas: Tuple[float, float] = (0.9, 0.999), adam_eps: float = 1e-8):
        self.obs_dim = int(obs_dim)
        self.n_actions = int(n_actions)
        self.hidden_sizes = list(hidden_sizes)
        self.dueling = bool(dueling)
        self.lr = float(lr)
        self.b1, self.b2 = adam_betas
        self.eps = adam_eps
        rng = np.random.default_rng(seed)

        self.params: Dict[str, np.ndarray] = {}
        last = obs_dim
        for i, h in enumerate(self.hidden_sizes):
            self.params[f"trunk{i}.W"] = _he(rng, last, h)
            self.params[f"trunk{i}.b"] = np.zeros(h, dtype=np.float64)
            last = h
        if self.dueling:
            self.params["value.W"] = _he(rng, last, 1)
            self.params["value.b"] = np.zeros(1, dtype=np.float64)
            self.params["adv.W"] = _he(rng, last, n_actions)
            self.params["adv.b"] = np.zeros(n_actions, dtype=np.float64)
        else:
            self.params["out.W"] = _he(rng, last, n_actions)
            self.params["out.b"] = np.zeros(n_actions, dtype=np.float64)

        # Adam moments
        self._m = {k: np.zeros_like(v) for k, v in self.params.items()}
        self._v = {k: np.zeros_like(v) for k, v in self.params.items()}
        self._t = 0

    # -- forward ---------------------------------------------------------- #
    def _forward(self, X: np.ndarray):
        X = np.asarray(X, dtype=np.float64)
        cache = {"inp": [], "pre": []}
        z = X
        for i in range(len(self.hidden_sizes)):
            W, b = self.params[f"trunk{i}.W"], self.params[f"trunk{i}.b"]
            pre = z @ W + b
            cache["inp"].append(z)
            cache["pre"].append(pre)
            z = np.maximum(pre, 0.0)
        feat = z
        cache["feat"] = feat
        if self.dueling:
            V = feat @ self.params["value.W"] + self.params["value.b"]         # (B,1)
            A = feat @ self.params["adv.W"] + self.params["adv.b"]             # (B,An)
            Q = V + (A - A.mean(axis=1, keepdims=True))
        else:
            Q = feat @ self.params["out.W"] + self.params["out.b"]
        return Q, cache

    def q(self, X: np.ndarray) -> np.ndarray:
        Q, _ = self._forward(X)
        return Q

    # -- backward + update ----------------------------------------------- #
    def train_batch(self, states, actions, targets, weights):
        states = np.asarray(states, dtype=np.float64)
        actions = np.asarray(actions, dtype=np.int64)
        targets = np.asarray(targets, dtype=np.float64)
        weights = np.asarray(weights, dtype=np.float64)
        B = states.shape[0]

        Q, cache = self._forward(states)
        idx = np.arange(B)
        q_taken = Q[idx, actions]
        td = targets - q_taken                                # (B,)

        # gradient of weighted Huber loss wrt the taken-action Q
        delta = np.clip(td, -1.0, 1.0)
        dq_taken = -(weights * delta) / B                     # (B,)
        dQ = np.zeros_like(Q)
        dQ[idx, actions] = dq_taken

        grads = self._backward(dQ, cache)
        self._adam_step(grads)

        huber = np.where(np.abs(td) < 1.0, 0.5 * td ** 2, np.abs(td) - 0.5)
        loss = float(np.mean(weights * huber))
        return loss, td

    def _backward(self, dQ: np.ndarray, cache) -> Dict[str, np.ndarray]:
        grads: Dict[str, np.ndarray] = {}
        feat = cache["feat"]
        if self.dueling:
            dV = dQ.sum(axis=1, keepdims=True)                # (B,1)
            dA = dQ - dQ.mean(axis=1, keepdims=True)          # (B,An)
            grads["value.W"] = feat.T @ dV
            grads["value.b"] = dV.sum(axis=0)
            grads["adv.W"] = feat.T @ dA
            grads["adv.b"] = dA.sum(axis=0)
            dfeat = dV @ self.params["value.W"].T + dA @ self.params["adv.W"].T
        else:
            grads["out.W"] = feat.T @ dQ
            grads["out.b"] = dQ.sum(axis=0)
            dfeat = dQ @ self.params["out.W"].T

        dz = dfeat
        for i in reversed(range(len(self.hidden_sizes))):
            pre = cache["pre"][i]
            inp = cache["inp"][i]
            dpre = dz * (pre > 0.0)
            grads[f"trunk{i}.W"] = inp.T @ dpre
            grads[f"trunk{i}.b"] = dpre.sum(axis=0)
            dz = dpre @ self.params[f"trunk{i}.W"].T
        return grads

    def _adam_step(self, grads: Dict[str, np.ndarray]) -> None:
        self._t += 1
        for k, g in grads.items():
            self._m[k] = self.b1 * self._m[k] + (1 - self.b1) * g
            self._v[k] = self.b2 * self._v[k] + (1 - self.b2) * (g * g)
            m_hat = self._m[k] / (1 - self.b1 ** self._t)
            v_hat = self._v[k] / (1 - self.b2 ** self._t)
            self.params[k] -= self.lr * m_hat / (np.sqrt(v_hat) + self.eps)

    # -- portable weights ------------------------------------------------- #
    def get_weights(self) -> Dict[str, np.ndarray]:
        return {k: v.copy() for k, v in self.params.items()}

    def set_weights(self, weights: Dict[str, np.ndarray]) -> None:
        for k in self.params:
            self.params[k] = np.asarray(weights[k], dtype=np.float64).reshape(self.params[k].shape)

    def copy_weights_from(self, other: "NumpyQNet") -> None:
        self.set_weights(other.get_weights())


def _he(rng, fan_in: int, fan_out: int) -> np.ndarray:
    std = np.sqrt(2.0 / max(1, fan_in))
    return (rng.standard_normal((fan_in, fan_out)) * std).astype(np.float64)


# --------------------------------------------------------------------------- #
# PyTorch backend
# --------------------------------------------------------------------------- #
class TorchQNet:
    """Same dueling MLP in PyTorch; weights interchange with :class:`NumpyQNet`."""

    def __init__(self, obs_dim: int, n_actions: int, hidden_sizes: List[int],
                 lr: float = 5e-4, dueling: bool = True, seed: int = 0, device: str = "cpu"):
        import torch
        import torch.nn as nn

        self.torch = torch
        self.nn = nn
        self.obs_dim = int(obs_dim)
        self.n_actions = int(n_actions)
        self.hidden_sizes = list(hidden_sizes)
        self.dueling = bool(dueling)
        self.device = torch.device(device)
        torch.manual_seed(seed)

        trunk = []
        last = obs_dim
        self._trunk_linears = []
        for h in hidden_sizes:
            lin = nn.Linear(last, h)
            self._trunk_linears.append(lin)
            trunk += [lin, nn.ReLU()]
            last = h
        self.trunk = nn.Sequential(*trunk).to(self.device)
        if dueling:
            self.value = nn.Linear(last, 1).to(self.device)
            self.adv = nn.Linear(last, n_actions).to(self.device)
            params = list(self.trunk.parameters()) + list(self.value.parameters()) + list(self.adv.parameters())
        else:
            self.out = nn.Linear(last, n_actions).to(self.device)
            params = list(self.trunk.parameters()) + list(self.out.parameters())
        self._he_init()
        self.optimizer = torch.optim.Adam(params, lr=lr)

    def _he_init(self):
        for m in self._modules_with_linear():
            self.nn.init.kaiming_uniform_(m.weight, nonlinearity="relu")
            self.nn.init.zeros_(m.bias)

    def _modules_with_linear(self):
        mods = list(self._trunk_linears)
        mods += [self.value, self.adv] if self.dueling else [self.out]
        return mods

    def _forward(self, x):
        feat = self.trunk(x)
        if self.dueling:
            v = self.value(feat)
            a = self.adv(feat)
            return v + (a - a.mean(dim=1, keepdim=True))
        return self.out(feat)

    def q(self, X: np.ndarray) -> np.ndarray:
        t = self.torch
        with t.no_grad():
            x = t.as_tensor(np.asarray(X), dtype=t.float32, device=self.device)
            if x.ndim == 1:
                x = x.unsqueeze(0)
            return self._forward(x).cpu().numpy()

    def train_batch(self, states, actions, targets, weights):
        t = self.torch
        s = t.as_tensor(np.asarray(states), dtype=t.float32, device=self.device)
        a = t.as_tensor(np.asarray(actions), dtype=t.long, device=self.device).unsqueeze(1)
        tgt = t.as_tensor(np.asarray(targets), dtype=t.float32, device=self.device).unsqueeze(1)
        w = t.as_tensor(np.asarray(weights), dtype=t.float32, device=self.device).unsqueeze(1)

        q_all = self._forward(s)
        q_taken = q_all.gather(1, a)
        td = tgt - q_taken
        loss = (w * t.nn.functional.smooth_l1_loss(q_taken, tgt, reduction="none")).mean()
        self.optimizer.zero_grad()
        loss.backward()
        t.nn.utils.clip_grad_norm_([p for g in self.optimizer.param_groups for p in g["params"]], 10.0)
        self.optimizer.step()
        return float(loss.item()), td.detach().cpu().numpy().squeeze(1)

    # -- portable weights (mapped to the same keys as NumpyQNet) ---------- #
    def get_weights(self) -> Dict[str, np.ndarray]:
        w = {}
        for i, lin in enumerate(self._trunk_linears):
            w[f"trunk{i}.W"] = lin.weight.detach().cpu().numpy().T.copy()  # (in,out)
            w[f"trunk{i}.b"] = lin.bias.detach().cpu().numpy().copy()
        if self.dueling:
            w["value.W"] = self.value.weight.detach().cpu().numpy().T.copy()
            w["value.b"] = self.value.bias.detach().cpu().numpy().copy()
            w["adv.W"] = self.adv.weight.detach().cpu().numpy().T.copy()
            w["adv.b"] = self.adv.bias.detach().cpu().numpy().copy()
        else:
            w["out.W"] = self.out.weight.detach().cpu().numpy().T.copy()
            w["out.b"] = self.out.bias.detach().cpu().numpy().copy()
        return w

    def set_weights(self, weights: Dict[str, np.ndarray]) -> None:
        t = self.torch
        with t.no_grad():
            for i, lin in enumerate(self._trunk_linears):
                lin.weight.copy_(t.as_tensor(weights[f"trunk{i}.W"].T, dtype=t.float32))
                lin.bias.copy_(t.as_tensor(weights[f"trunk{i}.b"], dtype=t.float32))
            if self.dueling:
                self.value.weight.copy_(t.as_tensor(weights["value.W"].T, dtype=t.float32))
                self.value.bias.copy_(t.as_tensor(weights["value.b"], dtype=t.float32))
                self.adv.weight.copy_(t.as_tensor(weights["adv.W"].T, dtype=t.float32))
                self.adv.bias.copy_(t.as_tensor(weights["adv.b"], dtype=t.float32))
            else:
                self.out.weight.copy_(t.as_tensor(weights["out.W"].T, dtype=t.float32))
                self.out.bias.copy_(t.as_tensor(weights["out.b"], dtype=t.float32))

    def copy_weights_from(self, other: "TorchQNet") -> None:
        self.set_weights(other.get_weights())


# --------------------------------------------------------------------------- #
# factory
# --------------------------------------------------------------------------- #
def torch_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("torch") is not None


def create_qnet(cfg, obs_dim: int, n_actions: int, seed: int = 0):
    """Return a Q-network backend honouring ``rl.nn_backend`` (auto|torch|numpy)."""
    backend = str(cfg.get_path("rl.nn_backend", "auto")).lower()
    kwargs = dict(
        obs_dim=obs_dim, n_actions=n_actions,
        hidden_sizes=list(cfg.rl.hidden_sizes),
        lr=float(cfg.rl.lr), dueling=bool(cfg.rl.dueling), seed=seed,
    )
    if backend == "numpy":
        return NumpyQNet(**kwargs), "numpy"
    if backend == "torch":
        return TorchQNet(**kwargs), "torch"
    # auto
    if torch_available():
        try:
            return TorchQNet(**kwargs), "torch"
        except Exception:
            pass
    return NumpyQNet(**kwargs), "numpy"
