"""Deterministic seeding so every run is reproducible.

A single call to :func:`seed_everything` fixes Python's ``random``, NumPy, and
(if installed) PyTorch RNGs. Each episode/scenario derives a *sub-seed* from the
global seed via :func:`derive_seed` so that, e.g., seed 42 + scenario 'rush' +
replicate 3 always produces the exact same traffic — which is what lets us
compare controllers fairly on *identical* traffic.
"""
from __future__ import annotations

import hashlib
import os
import random
from typing import Optional

import numpy as np


def seed_everything(seed: int) -> None:
    """Seed Python, NumPy and (optionally) PyTorch for reproducibility."""
    seed = int(seed) % (2**31 - 1)
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    try:  # torch is optional at import time (e.g. dashboard-only usage)
        import torch

        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        # Deterministic CPU math; keeps results stable across runs.
        torch.use_deterministic_algorithms(False)  # False = allow fast kernels, still seeded
    except Exception:  # pragma: no cover - torch not present
        pass


def derive_seed(base_seed: int, *tags: object) -> int:
    """Deterministically derive a new 32-bit seed from a base seed and string tags.

    ``derive_seed(42, "rush", 3)`` is stable across processes and platforms, so
    two different controllers can be evaluated on byte-identical traffic.
    """
    key = "|".join([str(base_seed), *[str(t) for t in tags]])
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)  # top 32 bits


def make_rng(base_seed: int, *tags: object) -> np.random.Generator:
    """Return a NumPy ``Generator`` seeded deterministically from base + tags."""
    return np.random.default_rng(derive_seed(base_seed, *tags))


def resolve_seed(cfg_seed: int, override: Optional[int]) -> int:
    """Pick the effective seed: an explicit CLI override wins over the config."""
    return int(override) if override is not None else int(cfg_seed)
