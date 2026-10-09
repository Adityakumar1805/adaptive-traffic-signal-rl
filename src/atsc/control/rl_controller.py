"""RL controller — wraps the trained multi-agent DQN policy for greedy inference.

Loads a checkpoint (independent Double+Dueling DQN with shared parameters, or QMIX) and,
at each decision, feeds every intersection's observation through its policy to pick the
greedy target phase. This is the controller the whole project is built to showcase.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from atsc.agents import build_learner
from atsc.control.base import Controller
from atsc.logging_utils import friendly_error, get_logger

log = get_logger("atsc.control.rl")


class RLController(Controller):
    name = "rl"

    def __init__(self, cfg, topo, checkpoint: Optional[str] = None,
                 device: str = "cpu", allow_untrained: bool = False) -> None:
        self.cfg = cfg
        self.topo = topo
        self.device = device
        self.learner = build_learner(
            cfg, list(topo.order), cfg_obs_dim(cfg), cfg.n_phases, device=device
        )
        ckpt = Path(checkpoint) if checkpoint else default_checkpoint_path(cfg)
        # Fall back to a --quick-trained checkpoint if the full one is absent.
        if not ckpt.exists() and checkpoint is None:
            quick = ckpt.with_name(ckpt.stem + "_quick.pt")
            if quick.exists():
                ckpt = quick
        if Path(ckpt).exists():
            self.metadata = self.learner.load(str(ckpt), load_optimizer=False)
            log.info("Loaded RL checkpoint: %s", ckpt)
        elif allow_untrained:
            log.warning("No checkpoint at %s; using an UNTRAINED policy (demo only).", ckpt)
            self.metadata = {}
        else:
            raise FileNotFoundError(friendly_error(
                "RL checkpoint not found",
                f"Expected a trained model at:\n  {ckpt}\n"
                "The RL controller needs a trained policy to run.",
                fix="Train one with:\n  python run.py train --quick   (under a minute)\n"
                    "or ship/restore the pre-trained checkpoint in models/pretrained/.",
            ))

    def act(self, env) -> Dict[str, int]:
        obs = env._observations()
        return self.learner.greedy(obs)


def cfg_obs_dim(cfg) -> int:
    from atsc.envs.spaces import obs_size
    return obs_size(cfg.n_phases, bool(cfg.rl.neighbor_obs))


def default_checkpoint_path(cfg) -> Path:
    from atsc.config import project_root
    grid = f"{cfg.network.grid_rows}x{cfg.network.grid_cols}"
    return project_root() / cfg.train.checkpoint_dir / f"atsc_{grid}.pt"
