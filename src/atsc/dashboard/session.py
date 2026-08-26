"""Dashboard session: runs RL and fixed-time controllers side-by-side on identical traffic.

The whole point of the live view is a fair race: both controllers see the *same* vehicles
(same scenario + seed), stepped in lockstep, so any difference on screen is purely the
controller. This class owns the two environments, advances them one decision at a time,
maintains rolling KPI histories, and produces a single JSON snapshot for the browser.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Deque, Dict, List, Optional

from atsc.config import load_config
from atsc.control import EmergencyController, build_controller
from atsc.control.rl_controller import default_checkpoint_path
from atsc.envs.traffic_env import MultiAgentTrafficEnv
from atsc.logging_utils import get_logger

log = get_logger("atsc.dashboard.session")


class ControllerRunner:
    """Wraps one environment + controller and exposes single-step advancement."""

    def __init__(self, cfg, controller_name: str, scenario: str, seed: int,
                 checkpoint: Optional[str] = None, allow_untrained: bool = True):
        self.cfg = cfg
        self.name = controller_name
        self.env = MultiAgentTrafficEnv(cfg, backend_name="mini")
        kwargs = {}
        if controller_name == "rl":
            kwargs = {"checkpoint": checkpoint, "allow_untrained": allow_untrained}
        self.controller = build_controller(controller_name, cfg, self.env.topo, **kwargs)
        self.emergency = EmergencyController(cfg, self.env.topo)
        self.env.set_emergency_controller(self.emergency)
        self._obs = None
        self.reset(scenario, seed)

    def reset(self, scenario: str, seed: int) -> None:
        self.controller.reset()
        self._obs = self.env.reset(scenario=scenario, seed=seed)
        self.done = False

    def step(self) -> None:
        if self.done or not self.env.agents:
            self.done = True
            return
        actions = self.controller.act(self.env)
        self._obs, _, _, trunc, _ = self.env.step(actions)
        if not self.env.agents:
            self.done = True

    def inject_emergency(self, corridor: str = "ew") -> Optional[str]:
        return self.env.inject_emergency(corridor)

    def snapshot(self) -> Dict:
        state = self.env.render_state()
        m = self.env.metrics()
        state["metrics"] = {
            "avg_waiting_time": round(m["avg_waiting_time"], 1),
            "avg_queue": round(m["avg_queue"], 2),
            "throughput": int(m["throughput"]),
            "avg_speed": round(m["avg_speed"], 2),
        }
        state["preempted"] = self.emergency.preempted_intersections()
        return state


class DashboardSession:
    """Owns the RL and fixed-time runners and the play/pause/step state machine."""

    def __init__(self, config_path: Optional[str] = None):
        self.cfg = load_config(config_path)
        self.scenario = str(self.cfg.dashboard.default_scenario)
        self.seed = int(self.cfg.seed)
        self.speed = 1.0
        self.playing = False
        self.tick_count = 0
        self._lock = threading.Lock()

        window = int(self.cfg.dashboard.chart_window)
        self.hist_time: Deque[float] = deque(maxlen=window)
        self.hist_rl_wait: Deque[float] = deque(maxlen=window)
        self.hist_ft_wait: Deque[float] = deque(maxlen=window)
        self.hist_rl_queue: Deque[float] = deque(maxlen=window)
        self.hist_ft_queue: Deque[float] = deque(maxlen=window)

        ckpt = str(default_checkpoint_path(self.cfg))
        self.ckpt_exists = default_checkpoint_path(self.cfg).exists()
        self.rl = ControllerRunner(self.cfg, "rl", self.scenario, self.seed, checkpoint=ckpt)
        self.ft = ControllerRunner(self.cfg, "fixed_time", self.scenario, self.seed)
        self.rl_backend = getattr(self.rl.controller, "learner", None)
        self.rl_backend_name = (self.rl.controller.learner.backend_name
                                if hasattr(self.rl.controller, "learner") else "n/a")
        log.info("Dashboard session ready (scenario=%s, seed=%s, RL backend=%s, checkpoint=%s)",
                 self.scenario, self.seed, self.rl_backend_name,
                 "loaded" if self.ckpt_exists else "UNTRAINED")

    # -- controls --------------------------------------------------------- #
    def play(self) -> None:
        self.playing = True

    def pause(self) -> None:
        self.playing = False

    def set_speed(self, speed: float) -> None:
        self.speed = max(0.25, min(8.0, float(speed)))

    def set_scenario(self, scenario: str) -> None:
        with self._lock:
            self.scenario = scenario
            self.reset()

    def reset(self) -> None:
        self.tick_count = 0
        self.rl.reset(self.scenario, self.seed)
        self.ft.reset(self.scenario, self.seed)
        for d in (self.hist_time, self.hist_rl_wait, self.hist_ft_wait,
                  self.hist_rl_queue, self.hist_ft_queue):
            d.clear()

    def inject_emergency(self, corridor: str = "ew") -> Dict[str, Optional[str]]:
        with self._lock:
            rl_id = self.rl.inject_emergency(corridor)
            ft_id = self.ft.inject_emergency(corridor)
        return {"rl": rl_id, "fixed": ft_id, "corridor": corridor}

    # -- stepping --------------------------------------------------------- #
    def step_once(self) -> None:
        with self._lock:
            self._advance()

    def _advance(self) -> None:
        if self.rl.done and self.ft.done:
            # auto-loop for a continuous demo
            self.reset()
        self.rl.step()
        self.ft.step()
        self.tick_count += 1
        rl_m = self.rl.env.metrics()
        ft_m = self.ft.env.metrics()
        self.hist_time.append(round(self.rl.env._elapsed, 0))
        self.hist_rl_wait.append(round(rl_m["avg_waiting_time"], 1))
        self.hist_ft_wait.append(round(ft_m["avg_waiting_time"], 1))
        self.hist_rl_queue.append(round(rl_m["avg_queue"], 2))
        self.hist_ft_queue.append(round(ft_m["avg_queue"], 2))

    def maybe_step(self) -> None:
        """Called by the broadcast loop; advances if playing (speed = steps per tick)."""
        if not self.playing:
            return
        with self._lock:
            n = max(1, int(round(self.speed)))
            for _ in range(n):
                self._advance()

    # -- snapshot for the browser ----------------------------------------- #
    def snapshot(self) -> Dict:
        with self._lock:
            rl_s = self.rl.snapshot()
            ft_s = self.ft.snapshot()
            rl_wait = rl_s["metrics"]["avg_waiting_time"]
            ft_wait = ft_s["metrics"]["avg_waiting_time"]
            rl_q = rl_s["metrics"]["avg_queue"]
            ft_q = ft_s["metrics"]["avg_queue"]
            wait_impr = _pct(ft_wait, rl_wait)
            queue_impr = _pct(ft_q, rl_q)
            green_wave = self._green_wave_score(self.rl)
            return {
                "tick": self.tick_count,
                "playing": self.playing,
                "speed": self.speed,
                "scenario": self.scenario,
                "elapsed": round(self.rl.env._elapsed, 0),
                "episode_seconds": int(self.cfg.sim.episode_seconds),
                "rl": rl_s,
                "fixed": ft_s,
                "kpi": {
                    "wait_improvement": wait_impr,
                    "queue_improvement": queue_impr,
                    "throughput_rl": rl_s["metrics"]["throughput"],
                    "throughput_fixed": ft_s["metrics"]["throughput"],
                    "green_wave": green_wave,
                },
                "history": {
                    "t": list(self.hist_time),
                    "rl_wait": list(self.hist_rl_wait),
                    "ft_wait": list(self.hist_ft_wait),
                    "rl_queue": list(self.hist_rl_queue),
                    "ft_queue": list(self.hist_ft_queue),
                },
                "meta": {
                    "grid": f"{self.cfg.network.grid_rows}x{self.cfg.network.grid_cols}",
                    "rl_backend": self.rl_backend_name,
                    "checkpoint": self.ckpt_exists,
                },
            }

    def _green_wave_score(self, runner: "ControllerRunner") -> bool:
        """Heuristic: a green wave is 'on' when >=2 intersections on the arterial share
        the East-West green simultaneously (coordinated progression)."""
        topo = runner.env.topo
        ew_green = 0
        for iid in topo.order:
            fsm = runner.env.fsms[iid]
            if "E" in fsm.active_green() or "W" in fsm.active_green():
                ew_green += 1
        return ew_green >= max(2, topo.n_intersections // 2)


def _pct(base: float, val: float) -> float:
    if base <= 1e-9:
        return 0.0
    return round(100.0 * (base - val) / base, 1)
