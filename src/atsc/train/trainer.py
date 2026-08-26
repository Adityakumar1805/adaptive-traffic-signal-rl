"""Training loop: curriculum multi-agent DQN with CSV (+ optional TensorBoard) logging.

Runs episodes across the density curriculum, stores transitions in prioritized replay,
updates the Double+Dueling DQN each step, periodically evaluates greedily, and checkpoints
the best model. Deterministic per-episode seeds make every run reproducible.
"""
from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Dict, Optional

import numpy as np

from atsc.agents import build_learner
from atsc.config import project_root
from atsc.control.rl_controller import default_checkpoint_path
from atsc.envs.spaces import obs_size
from atsc.envs.traffic_env import MultiAgentTrafficEnv
from atsc.logging_utils import get_logger
from atsc.seeding import derive_seed, seed_everything

log = get_logger("atsc.train")


class Trainer:
    def __init__(self, cfg, quick: bool = False, device: str = "cpu") -> None:
        self.cfg = cfg
        self.quick = quick
        self.device = device
        self.n_episodes = int(cfg.train.quick_episodes if quick else cfg.train.episodes)
        self.curriculum = list(cfg.train.curriculum)
        self.episode_seconds = int(cfg.train.episode_seconds)

        # env used for training (override episode length to the shorter training horizon)
        self.env = MultiAgentTrafficEnv(cfg, backend_name="mini")
        self.env._episode_seconds = self.episode_seconds

        self.obs_dim = obs_size(cfg.n_phases, bool(cfg.rl.neighbor_obs))
        self.learner = build_learner(cfg, list(self.env.possible_agents),
                                     self.obs_dim, cfg.n_phases, device=device)

        self.log_dir = project_root() / cfg.train.log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # Full training writes the shipped checkpoint; --quick writes a separate file so a
        # fast demo-time train never clobbers the curated pre-trained model.
        base = default_checkpoint_path(cfg)
        self.ckpt_path = (base.with_name(base.stem + "_quick.pt") if quick else base)
        self.ckpt_path.parent.mkdir(parents=True, exist_ok=True)

        self._writer = None
        if bool(cfg.train.tensorboard):
            try:
                from torch.utils.tensorboard import SummaryWriter
                self._writer = SummaryWriter(log_dir=str(self.log_dir / "tb"))
            except Exception as exc:  # pragma: no cover
                log.warning("TensorBoard unavailable (%s); CSV logging only.", exc)

    # ------------------------------------------------------------------ #
    def train(self) -> Path:
        cfg = self.cfg
        seed_everything(cfg.seed)
        csv_path = self.log_dir / ("train_quick.csv" if self.quick else "train.csv")
        csv_file = open(csv_path, "w", newline="")
        writer = csv.writer(csv_file)
        writer.writerow(["episode", "scenario", "steps", "ep_reward", "avg_wait",
                         "avg_queue", "throughput", "epsilon", "loss", "seconds"])

        best_score = float("inf")
        log.info("Starting %s training: %d episodes on %s grid (device=%s)",
                 "QUICK" if self.quick else "FULL", self.n_episodes,
                 f"{cfg.network.grid_rows}x{cfg.network.grid_cols}", self.device)

        for ep in range(self.n_episodes):
            scenario = self.curriculum[ep % len(self.curriculum)]
            seed = derive_seed(cfg.seed, "train", scenario, ep)
            t0 = time.time()
            ep_reward, steps, loss = self._run_episode(scenario, seed)
            metrics = self.env.metrics()
            dt = time.time() - t0

            writer.writerow([ep, scenario, steps, round(ep_reward, 1),
                             round(metrics["avg_waiting_time"], 2),
                             round(metrics["avg_queue"], 2),
                             int(metrics["throughput"]),
                             round(self.learner.epsilon, 3),
                             round(loss or 0.0, 4), round(dt, 1)])
            csv_file.flush()
            if self._writer:
                self._writer.add_scalar("train/avg_wait", metrics["avg_waiting_time"], ep)
                self._writer.add_scalar("train/ep_reward", ep_reward, ep)
                self._writer.add_scalar("train/epsilon", self.learner.epsilon, ep)

            log.info("ep %2d/%d | %-6s | wait=%5.1fs q=%4.1f thru=%4d | eps=%.2f "
                     "loss=%.3f | %.1fs",
                     ep + 1, self.n_episodes, scenario, metrics["avg_waiting_time"],
                     metrics["avg_queue"], int(metrics["throughput"]),
                     self.learner.epsilon, loss or 0.0, dt)

            # periodic greedy checkpoint on a fixed eval scenario
            if (ep + 1) % int(cfg.train.eval_every) == 0 or ep == self.n_episodes - 1:
                score = self._greedy_eval_all(derive_seed(cfg.seed, "eval", ep))
                log.info("   greedy eval (mean over scenarios): avg_wait=%.1fs", score)
                if score < best_score:
                    best_score = score
                    self._save(best=True, note=f"ep{ep+1}_wait{score:.1f}")
                    log.info("   ** new best (%.1fs) -> saved checkpoint", score)

        # The best-by-greedy-eval checkpoint is the shipped model (already at ckpt_path).
        # Also keep the final-weights model alongside it for reference / resuming.
        final_path = self.ckpt_path.with_name(self.ckpt_path.stem + "_final.pt")
        self.learner.save(str(final_path), metadata={"note": "final_weights"})
        csv_file.close()
        if self._writer:
            self._writer.close()
        log.info("Training complete. Best greedy avg_wait=%.1fs. Checkpoint: %s",
                 best_score, self.ckpt_path)
        return self.ckpt_path

    # ------------------------------------------------------------------ #
    def _run_episode(self, scenario: str, seed: int):
        env = self.env
        obs = env.reset(scenario=scenario, seed=seed)
        ep_reward = 0.0
        steps = 0
        last_loss = None
        while env.agents:
            actions = self.learner.act(obs, explore=True)
            next_obs, rewards, terminated, truncated, infos = env.step(actions)
            done = len(env.agents) == 0
            self.learner.observe(obs, actions, rewards, next_obs,
                                 {a: done for a in env.possible_agents})
            loss = self.learner.learn()
            if loss is not None:
                last_loss = loss
            obs = next_obs
            ep_reward += float(sum(rewards.values()))
            steps += 1
        return ep_reward, steps, last_loss

    def _greedy_eval(self, scenario: str, seed: int) -> float:
        """Run one greedy (no-exploration) episode and return avg waiting time."""
        eval_env = MultiAgentTrafficEnv(self.cfg, backend_name="mini")
        eval_env._episode_seconds = self.episode_seconds
        obs = eval_env.reset(scenario=scenario, seed=seed)
        while eval_env.agents:
            actions = self.learner.greedy(obs)
            obs, _, _, _, _ = eval_env.step(actions)
        score = eval_env.metrics()["avg_waiting_time"]
        eval_env.close()
        return score

    def _greedy_eval_all(self, seed: int) -> float:
        """Greedy avg-waiting-time for model selection.

        Averaged over the non-saturated scenarios (low/medium/high). Rush hour is
        (near-)saturated for every controller and extremely noisy, so including it in the
        *selection* metric would just add variance; the agent still *trains* on rush.
        """
        select_scenarios = [s for s in self.curriculum if s != "rush"] or self.curriculum
        scores = [self._greedy_eval(scen, seed + i)
                  for i, scen in enumerate(select_scenarios)]
        return float(np.mean(scores))

    def _save(self, best: bool, note: str = "") -> None:
        metadata = {
            "grid": f"{self.cfg.network.grid_rows}x{self.cfg.network.grid_cols}",
            "phase_scheme": self.cfg.network.phase_scheme,
            "obs_dim": self.obs_dim,
            "n_actions": self.cfg.n_phases,
            "neighbor_obs": bool(self.cfg.rl.neighbor_obs),
            "share_parameters": bool(self.cfg.rl.share_parameters),
            "algo": "qmix" if bool(self.cfg.get_path("qmix.enabled", False))
                    else "double_dueling_dqn",
            "curriculum": self.curriculum,
            "note": note,
        }
        self.learner.save(str(self.ckpt_path), metadata=metadata)


def train_main(cfg, quick: bool = False, device: str = "cpu") -> Path:
    return Trainer(cfg, quick=quick, device=device).train()
