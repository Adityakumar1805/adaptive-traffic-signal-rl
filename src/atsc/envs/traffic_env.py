"""Multi-agent traffic-signal environment (PettingZoo-style ParallelEnv).

One agent controls one intersection. The API mirrors PettingZoo's parallel API
(``reset`` / ``step`` return dicts keyed by agent id) without hard-depending on the
``pettingzoo`` package, which keeps the dependency surface small and stable.

Timing model
------------
Agents act every ``signal.decision_interval_s`` simulated seconds. Between decisions the
environment advances the simulation one second at a time, ticking every intersection's
:class:`~atsc.envs.phases.SignalFSM` (which enforces min/max green + yellow + all-red)
and pushing the resulting green approaches to the simulation backend. This means a
single ``step`` call corresponds to ``decision_interval_s`` seconds of simulation.

Reward (per agent i), computed over the decision interval — see REPORT.md::

    r_i = - w_wait * delay_i / N
          - w_pressure * pressure_i / N
          - w_switch * switched_i
          + w_emergency * emergencies_cleared_i

where ``delay_i`` is vehicle-seconds of delay accrued at i during the interval,
``pressure_i`` is the max-pressure quantity at the end of the interval, ``switched_i``
is 1 if a phase change was initiated, ``emergencies_cleared_i`` counts emergency
vehicles that cleared i, and ``N = reward.normalize``.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

try:  # gymnasium is used only for the space objects; keep import soft for tooling
    from gymnasium import spaces as gym_spaces
except Exception:  # pragma: no cover
    gym_spaces = None

from atsc.envs.phases import SignalFSM
from atsc.envs.spaces import build_observation, obs_size
from atsc.sim import make_backend
from atsc.sim.backend import build_topology


class MultiAgentTrafficEnv:
    """Parallel multi-agent environment over a grid of signalised intersections."""

    metadata = {"name": "atsc_multiagent_v1"}

    def __init__(self, cfg, backend_name: Optional[str] = None, backend=None, topo=None):
        self.cfg = cfg
        self.topo = topo or build_topology(cfg)
        if backend is not None:
            self.backend = backend
            self.backend_name = backend_name or "external"
        else:
            self.backend, self.backend_name = make_backend(
                cfg, self.topo, prefer=backend_name
            )

        self.agents: List[str] = list(self.topo.order)
        self.possible_agents = list(self.agents)
        self.n_phases = cfg.n_phases
        self.neighbor_obs = bool(cfg.rl.neighbor_obs)

        self._decision_interval = int(cfg.signal.decision_interval_s)
        self._episode_seconds = int(cfg.sim.episode_seconds)
        self._obs_dim = obs_size(self.n_phases, self.neighbor_obs)

        self.fsms: Dict[str, SignalFSM] = {}
        self._emergency_ctrl = None      # optional EmergencyController (set externally)
        self._signal_sink = None         # optional lamp sink, e.g. the hardware bridge
        self._elapsed = 0.0
        self._scenario: Dict = {}

        # gymnasium spaces (identical across homogeneous agents)
        if gym_spaces is not None:
            self._obs_space = gym_spaces.Box(
                low=-1.0, high=1.0, shape=(self._obs_dim,), dtype=np.float32
            )
            self._act_space = gym_spaces.Discrete(self.n_phases)
        else:  # pragma: no cover
            self._obs_space = None
            self._act_space = None

    # -- space accessors (PettingZoo-style) ------------------------------- #
    def observation_space(self, agent: str):
        return self._obs_space

    def action_space(self, agent: str):
        return self._act_space

    @property
    def obs_dim(self) -> int:
        return self._obs_dim

    @property
    def num_actions(self) -> int:
        return self.n_phases

    def set_emergency_controller(self, ctrl) -> None:
        """Attach an :class:`~atsc.control.emergency.EmergencyController` (optional)."""
        self._emergency_ctrl = ctrl

    # -- optional hardware taps ------------------------------------------- #
    def set_signal_sink(self, sink) -> None:
        """Attach a callable that receives the lamp aspects once per simulated second.

        The sink is handed an iterable of ``(state_value, current_phase)`` tuples in
        topology order and must not raise; one that does is detached rather than allowed
        to kill the episode. It has to be called per simulated *second*, not per decision
        interval, or the 3 s amber and 2 s all-red would never reach real lamps.

        Pass ``None`` to detach. Nothing in this module knows what the sink does — the
        hardware layer stays entirely outside the environment.
        """
        self._signal_sink = sink

    def signal_aspects(self) -> List[Tuple[str, int]]:
        """``(state_value, current_phase)`` per intersection, in topology order.

        ``current_phase`` is the FSM's ``current``, which during a transition still names
        the phase being *terminated*, so amber lands on the outgoing group as traffic
        engineering requires. :meth:`SignalFSM.active_green` returns an empty tuple during
        yellow and all-red and therefore cannot be used to derive lamp aspects.
        """
        return [(self.fsms[iid].state.value, self.fsms[iid].current)
                for iid in self.possible_agents]

    def _publish_signals(self) -> None:
        if self._signal_sink is None or not self.fsms:
            return
        try:
            self._signal_sink(self.signal_aspects())
        except Exception:
            self._signal_sink = None

    def feed_detection(self, tls: str, approach: str, count: int = 1) -> int:
        """Inject ``count`` vehicles seen by a *real* detector; returns how many landed.

        Hardware hook (see :mod:`atsc.hw`), used by the dashboard's hardware mode
        (``python run.py demo --hardware``) when an IR sensor on the model sees a car. It
        delegates to the backend's optional ``add_detected_arrival``: the built-in simulator
        implements it without drawing a random number, so the simulated arrival sequence is
        unchanged; SUMO does not, and gets 0. Training and the benchmark never call it.
        """
        if tls not in self.fsms or count <= 0:
            return 0
        admit = getattr(self.backend, "add_detected_arrival", None)
        if admit is None:
            return 0
        return int(admit(tls, approach, int(count)))

    # -- lifecycle -------------------------------------------------------- #
    def reset(self, scenario: str = "medium", seed: int = 0) -> Dict[str, np.ndarray]:
        self._scenario = self.cfg.scenario_params(scenario)
        self.backend.reset(self._scenario, seed=seed)
        self.agents = list(self.possible_agents)
        self._elapsed = 0.0

        self.fsms = {
            iid: SignalFSM(
                phases=self.topo.get(iid).phases,
                min_green_s=self.cfg.signal.min_green_s,
                max_green_s=self.cfg.signal.max_green_s,
                yellow_s=self.cfg.signal.yellow_s,
                all_red_s=self.cfg.signal.all_red_s,
                start_phase=0,
            )
            for iid in self.possible_agents
        }
        if self._emergency_ctrl is not None:
            self._emergency_ctrl.reset()

        # warm the simulation so the first observation is not all-zeros
        self._advance_seconds(int(self.cfg.sim.warmup_s))
        self._publish_signals()          # show the opening aspects on any real lamps
        return self._observations()

    def close(self) -> None:
        self.backend.close()

    # -- stepping --------------------------------------------------------- #
    def step(
        self, actions: Dict[str, int]
    ) -> Tuple[Dict[str, np.ndarray], Dict[str, float], Dict[str, bool], Dict[str, bool], Dict[str, dict]]:
        """Apply one action per agent and advance ``decision_interval_s`` seconds."""
        # 1) register the requested target phases (subject to FSM safety rules)
        for iid, act in actions.items():
            self.fsms[iid].request(int(act))

        # 2) emergency preemption may override requests (safe corridor)
        if self._emergency_ctrl is not None:
            self._emergency_ctrl.apply(self)

        # 3) advance the interval, accumulating per-agent delay & switches & clearances
        switched = {iid: 0 for iid in self.agents}
        cleared = {iid: 0 for iid in self.agents}
        for _ in range(self._decision_interval):
            self._advance_one_second(switched, cleared)
            self._elapsed += 1.0
            if self._elapsed >= self._episode_seconds:
                break

        # 4) rewards + next observations
        rewards = self._rewards(switched, cleared)
        obs = self._observations()
        done = self._elapsed >= self._episode_seconds
        terminated = {iid: False for iid in self.agents}
        truncated = {iid: done for iid in self.agents}
        infos = {iid: {"scenario": self._scenario.get("name")} for iid in self.agents}

        if done:
            self.backend.finalize()
            self.agents = []
        return obs, rewards, terminated, truncated, infos

    def _advance_one_second(self, switched: Dict[str, int], cleared: Dict[str, int]) -> None:
        # tick every FSM, set backend greens, then step the simulator one second
        for iid in self.possible_agents:
            greens = self.fsms[iid].tick()
            if self.fsms[iid].switch_started:
                switched[iid] = 1
            self.backend.set_green(iid, greens)
        self._publish_signals()          # lamps must follow every second, amber included
        self.backend.step()
        # collect emergency clearances credited by the backend
        for iid in self.possible_agents:
            cleared[iid] += self.backend.pop_emergency_cleared(iid)

    def _advance_seconds(self, n: int) -> None:
        for _ in range(max(0, n)):
            for iid in self.possible_agents:
                greens = self.fsms[iid].tick()
                self.backend.set_green(iid, greens)
            self.backend.step()
            # drain per-interval counters so warmup doesn't leak into the first reward
            for iid in self.possible_agents:
                self.backend.pop_delay(iid)
                self.backend.pop_emergency_cleared(iid)

    # -- observation & reward -------------------------------------------- #
    def _observations(self) -> Dict[str, np.ndarray]:
        return {
            iid: build_observation(
                self.backend, self.topo, iid, self.fsms[iid], self.fsms, self.neighbor_obs
            )
            for iid in self.possible_agents
        }

    def _rewards(self, switched: Dict[str, int], cleared: Dict[str, int]) -> Dict[str, float]:
        rc = self.cfg.reward
        norm = float(rc.normalize)
        rewards: Dict[str, float] = {}
        for iid in self.possible_agents:
            delay = self.backend.pop_delay(iid)              # vehicle-seconds this interval
            pressure = max(0.0, self.backend.intersection_pressure(iid))
            r = -(rc.w_wait * delay) / norm
            r -= (rc.w_pressure * pressure) / norm
            r -= rc.w_switch * switched.get(iid, 0)
            r += rc.w_emergency * cleared.get(iid, 0)
            rewards[iid] = float(r)
        return rewards

    # -- metrics & rendering --------------------------------------------- #
    def metrics(self) -> Dict[str, float]:
        return self.backend.metrics().as_dict()

    def render_state(self) -> Dict:
        state = self.backend.render_state()
        # attach signal phase info for the dashboard
        for iid in self.possible_agents:
            fsm = self.fsms[iid]
            if iid in state.get("intersections", {}):
                state["intersections"][iid]["phase"] = fsm.current
                state["intersections"][iid]["phase_name"] = fsm.phases[fsm.current].name
                state["intersections"][iid]["state"] = fsm.state.value
                state["intersections"][iid]["green"] = list(fsm.active_green())
        state["scenario"] = self._scenario.get("name")
        state["elapsed"] = self._elapsed
        return state

    def inject_emergency(self, corridor: Optional[str] = None,
                         kind: str = "ambulance") -> Optional[str]:
        """Spawn an emergency vehicle (ambulance by default, as the benchmark does)."""
        vid = self.backend.inject_emergency(corridor, kind=kind)
        if vid is not None and self._emergency_ctrl is not None:
            self._emergency_ctrl.notify_injection(corridor or "ew")
        return vid
