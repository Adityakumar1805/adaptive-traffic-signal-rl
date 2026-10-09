"""Dashboard session: RL vs fixed-time on identical traffic, streamed as compact frames.

The live view is a fair race: both controllers see the *same* vehicles (same scenario + seed),
stepped in lockstep, so any difference on screen is purely the controller. Both networks run
the same emergency-preemption override, exactly as in the benchmark.

Wire protocol (version :data:`~atsc.dashboard.assets.PROTOCOL_VERSION`)
------------------------------------------------------------------------
Every message is a JSON object with a type ``t``.

``hello``  sent once per connection: static data (grid geometry, vehicle catalogue,
           emergency types, scenarios, speeds, timing) plus a full ``state`` frame and the
           whole chart ``h`` history.
``f``      a frame. With ``kf: 1`` it is a *keyframe* that replaces everything the browser
           knows; otherwise it is a *delta* holding only what changed since the previous
           frame. Deltas are absolute values (never increments), so they are idempotent.
``hb``     heartbeat while nothing changes (paused), so the browser can tell a quiet
           connection from a dead one.

A side (``rl`` / ``ft``) carries, when they changed:

``s``   lamp state per junction, one letter each: ``chr(97 + phase * 3 + state)`` with
        state 0 green, 1 amber, 2 all-red
``tl``  ``[t0, "..."]`` the lamp letters for every simulated second since the last frame,
        so amber and all-red can be replayed even when a frame covers several decisions
        (omitted at speeds >= 4, where a 3 s amber lasts a few milliseconds on screen)
``q``   queue length per approach (junction-major, N E S W; "approach index" below means
        ``junction * 4 + side``)
``qk``  ``[[approach, "codes"], ...]`` the first vehicles of changed queues, stop line
        first, one character per vehicle: ``hello.qka[kind code * hello.nv + id % hello.nv]``.
        ``id % nv`` is the vehicle's colour variant, so a vehicle keeps its colour when it
        leaves a queue and shows up in ``a`` / ``x`` (which carry its id)
``a``   vehicles that started crossing a link, flat: ``id, kind, source approach, dest
        approach, dt0`` per vehicle, ``dt0 = (t0 - bt) * 10``. The vehicle left the stop line
        of its source approach during the second before ``t0`` (it was at the head of that
        queue until then). Travel time is ``link / (free_speed * kind speed factor)`` (both
        in ``hello``), and a vehicle is on its link exactly while ``t0 <= t < t0 + travel``,
        so the browser computes every position and expires every vehicle itself - positions
        and "arrived" events are never sent. Keyframes carry the same layout as ``mv``.
``x``   vehicles that left the network, flat: ``id, kind, source approach, exit dir (N E S W
        = 0..3), dt``. Always sent: together with ``a`` it tells the browser exactly when each
        vehicle left the head of its queue, which is what makes the between-frame picture
        exact at every speed
``m``   metrics ``[avg wait s, avg queue, throughput, moving-share speed, emergency
        clearance s (-1 none), emergencies cleared, paired clearance s (-1 none), pairs]``.
        The last two compare like with like: the mean clearance time of only those
        emergency vehicles that have already cleared *both* grids, and how many there are
        (the same on both sides).  ``pr`` pre-empted junctions.  ``ne`` emergency vehicles
        in the grid (queued or on a link), counted on the server, so the browser can follow
        one that is waiting in the part of a long queue it is not sent (the ``+n`` badge)

Global fields: ``k`` tick, ``bt`` simulator time, ``e`` episode time, ``p`` playing,
``sp`` speed, ``sc`` scenario, ``h`` new chart points ``[e, rl wait, ft wait, rl q, ft q]``,
and only with a physical signal board attached (``run.py demo --hardware``) ``hw``
``[board 0 offline / 1 silent / 2 live, vehicles its sensors added, emergencies it sent]``;
``hello`` then also carries ``rt``, the speed that is real time, and ``hw`` (port, the grid
the lamps follow, the junction with the sensors).
Keyframes also carry ``ep``, an episode counter that changes on every reset: a keyframe of
the same episode can be *merged* into what the browser already has (events it already knows
stay, so vehicles it is still animating do not vanish), one of a new episode replaces it. A
keyframe built by a tick also carries that tick's ``a`` / ``x`` / ``tl`` events, so the event
stream has no gaps; the snapshots in ``hello`` and ``/api/state`` describe a single instant
and carry none.

Commands are validated when they arrive (:meth:`DashboardSession.submit` returns the error
for the browser) and applied at the start of the next tick, so the simulation only ever
changes inside :meth:`tick`. That is what keeps one shared delta stream consistent for every
viewer: between ticks the live state always equals the last broadcast state.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from atsc import vehicles as vehicle_catalogue
from atsc.config import load_config
from atsc.control import EmergencyController, build_controller
from atsc.control.rl_controller import default_checkpoint_path
from atsc.dashboard.assets import PROTOCOL_VERSION, build_id
from atsc.envs.traffic_env import MultiAgentTrafficEnv
from atsc.logging_utils import get_logger
from atsc.sim.backend import APPROACHES
from atsc.sim.mini_backend import MiniBackend, vehicle_number

log = get_logger("atsc.dashboard.session")

_STATE_CODE = {"green": 0, "yellow": 1, "all_red": 2}
_DIR_INDEX = {d: i for i, d in enumerate("NESW")}
KIND_CODE: Dict[str, int] = {k: i for i, k in enumerate(vehicle_catalogue.KINDS)}
# queue characters: printable ASCII without '\\' (and without '"', which is below 40), so a
# queue string never needs escaping in JSON. One character = kind + colour variant.
QK_ALPHABET = "".join(chr(c) for c in range(40, 127) if c != 92)
COLOUR_VARIANTS = 4
if len(vehicle_catalogue.KINDS) * COLOUR_VARIANTS > len(QK_ALPHABET):  # pragma: no cover
    raise RuntimeError("too many vehicle kinds for QK_ALPHABET - extend the alphabet")


def queue_char(kind: str, vid: str) -> str:
    """The ``qk`` character of one queued vehicle (kind + colour variant)."""
    return QK_ALPHABET[KIND_CODE.get(kind, 0) * COLOUR_VARIANTS
                       + vehicle_number(vid) % COLOUR_VARIANTS]


_MAX_PENDING_COMMANDS = 64
_MAX_MANUAL_STEPS = 10
_MAX_STEPS_PER_TICK = 64
# at or above this speed a 3 s amber lasts a few milliseconds on screen, so the per-second
# lamp strings (``tl``) are neither built nor sent
_LITE_SPEED = 4.0


class CommandError(ValueError):
    """A browser command was rejected; the message is safe to show to the user."""


def _lamp_char(state_value: str, phase: int) -> str:
    return chr(97 + int(phase) * 3 + _STATE_CODE.get(state_value, 2))


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# --------------------------------------------------------------------------- #
# per-network recorder: link events + per-second lamps between two frames
# --------------------------------------------------------------------------- #
class _Recorder:
    """Collects what happened on one network since the last frame.

    Hooked into :meth:`MiniBackend.set_event_hooks` and the environment's signal sink. The
    callbacks only read the vehicles they are handed, so the simulation is unaffected.
    """

    def __init__(self, env: MultiAgentTrafficEnv, ai_of: Dict[Tuple[str, str], int],
                 j_of: Dict[str, int], step_s: float) -> None:
        self.env = env
        self.backend = env.backend
        self._ai_of = ai_of
        self._j_of = j_of
        self._step = step_s
        self.adds: List[Tuple[int, int, int, int, int]] = []   # id, kind, src, dest, t0*10
        self.exits: List[Tuple[int, int, int, int, int]] = []  # id, kind, src, dir, t*10
        self.tl0: Optional[float] = None
        self.tl: List[str] = []
        self.em_times: Dict[str, float] = {}     # emergency vehicle id -> time through the grid
        self._lamps_on = True
        self._tap = None                         # hardware: (sim time, aspects) every second
        self.backend.set_event_hooks(self._on_enter, self._on_complete)
        env.set_signal_sink(self._on_signals)

    def set_lamp_recording(self, on: bool) -> None:
        """Record the per-second lamp strings for the browser or not (not needed at speeds
        >= 4). The sink itself stays attached while a hardware tap needs it."""
        if on != self._lamps_on:
            self._lamps_on = on
            self._attach()

    def set_tap(self, tap) -> None:
        """Also hand every simulated second's lamp states to ``tap(sim_time, aspects)``
        (the physical signal board). ``tap`` must not raise."""
        self._tap = tap
        self._attach()

    def _attach(self) -> None:
        on = self._lamps_on or self._tap is not None
        self.env.set_signal_sink(self._on_signals if on else None)

    # The backend calls these mid-step, at its pre-increment clock, right after the vehicle
    # was discharged from the head of a queue and its leg index moved on: the vehicle crosses
    # the junction during the current simulated second and is on its link (or gone) when it
    # ends. ``legs[leg_index - 1]`` is therefore the approach it was queued on.
    def _on_enter(self, veh, travel_s: float, dest_tls: str, dest_appr: str) -> None:
        t0 = self.backend.time + self._step
        self.adds.append((vehicle_number(veh.id), KIND_CODE.get(veh.kind, 0),
                          self._ai_of[veh.legs[veh.leg_index - 1]],
                          self._ai_of[(dest_tls, dest_appr)], int(round(t0 * 10))))

    def _on_complete(self, veh, at_tls: str) -> None:
        t = self.backend.time + self._step
        self.exits.append((vehicle_number(veh.id), KIND_CODE.get(veh.kind, 0),
                           self._ai_of[veh.legs[-1]], _DIR_INDEX.get(veh.exit_dir, -1),
                           int(round(t * 10))))
        if veh.is_emergency:
            # the backend's own clearance figure: its clock minus the departure time
            self.em_times[veh.id] = self.backend.time - veh.depart_time

    def flat_adds(self, bt10: int) -> List[int]:
        out: List[int] = []
        for vid, kind, src, dest, t0 in self.adds:
            out += (vid, kind, src, dest, t0 - bt10)
        return out

    def flat_exits(self, bt10: int) -> List[int]:
        out: List[int] = []
        for vid, kind, src, direction, t in self.exits:
            if direction >= 0:
                out += (vid, kind, src, direction, t - bt10)
        return out

    def _on_signals(self, aspects) -> None:
        if self._tap is not None:
            self._tap(self.backend.time, aspects)
        if not self._lamps_on:
            return
        if self.tl0 is None:
            self.tl0 = self.backend.time
        self.tl.append("".join(_lamp_char(state, phase) for state, phase in aspects))

    def clear(self) -> None:
        self.adds, self.exits = [], []
        self.tl0, self.tl = None, []


# --------------------------------------------------------------------------- #
# one controller on one network
# --------------------------------------------------------------------------- #
class ControllerRunner:
    """One environment + controller + preemption override, advanced one decision at a time."""

    def __init__(self, cfg, controller_name: str, scenario: str, seed: int,
                 checkpoint: Optional[str] = None, allow_untrained: bool = True) -> None:
        self.cfg = cfg
        self.name = controller_name
        self.env = MultiAgentTrafficEnv(cfg, backend_name="mini")
        if not isinstance(self.env.backend, MiniBackend):  # pragma: no cover - defensive
            raise RuntimeError("the dashboard needs the built-in simulator (backend 'mini')")
        topo = self.env.topo
        kwargs: Dict[str, Any] = {}
        if controller_name == "rl":
            kwargs = {"checkpoint": checkpoint, "allow_untrained": allow_untrained}
        self.controller = build_controller(controller_name, cfg, topo, **kwargs)
        self.emergency = EmergencyController(cfg, topo)
        self.env.set_emergency_controller(self.emergency)

        self.j_of = {iid: j for j, iid in enumerate(topo.order)}
        self.ai_of = {(iid, a): j * len(APPROACHES) + k
                      for j, iid in enumerate(topo.order) for k, a in enumerate(APPROACHES)}
        self.recorder = _Recorder(self.env, self.ai_of, self.j_of, float(cfg.sim.step_length_s))
        self.done = False
        self.reset(scenario, seed)

    def reset(self, scenario: str, seed: int) -> None:
        self.controller.reset()
        self.env.reset(scenario=scenario, seed=seed)
        self.done = False
        self.recorder.clear()
        self.recorder.em_times.clear()

    def step(self) -> None:
        if self.done or not self.env.agents:
            self.done = True
            return
        self.env.step(self.controller.act(self.env))
        if not self.env.agents:
            self.done = True

    def inject(self, kind: str, corridor: str) -> Optional[str]:
        return self.env.inject_emergency(corridor, kind=kind)

    def active_emergencies(self) -> int:
        return self.env.backend.active_emergencies()

    # -- views (read-only) -------------------------------------------------- #
    def lamps(self) -> str:
        return "".join(_lamp_char(fsm.state.value, fsm.current)
                       for fsm in (self.env.fsms[i] for i in self.env.possible_agents))

    def queues(self, cap: int) -> Tuple[List[int], List[str]]:
        snap = self.env.backend.queue_snapshot(cap)
        return ([n for n, _v in snap],
                ["".join(queue_char(v.kind, v.id) for v in vehs) for _n, vehs in snap])

    def moving(self) -> List[int]:
        """Every vehicle on a link, flat ``id, kind, source approach, dest approach, dt0``
        (the layout of ``a``)."""
        bt = self.env.backend.time
        bt10 = int(round(bt * 10))
        out: List[int] = []
        for veh, remaining, travel, dest_tls, dest_appr in self.env.backend.transit_snapshot():
            t0 = bt - travel + remaining
            out += (vehicle_number(veh.id), KIND_CODE.get(veh.kind, 0),
                    self.ai_of[veh.legs[veh.leg_index - 1]],
                    self.ai_of[(dest_tls, dest_appr)], int(round(t0 * 10)) - bt10)
        return out

    def live_metrics(self) -> List[float]:
        """Episode-so-far KPIs. The waiting-time average includes vehicles still in the
        network (their wait so far), so a jammed approach counts before it clears; at the
        end of the episode it equals the benchmark's ``avg_waiting_time`` exactly."""
        m = self.env.metrics()
        if self.done:
            wait = m["avg_waiting_time"]
        else:
            done_wait, done_n = self.env.backend.metrics().wait_totals()
            live_wait, live_n = self.env.backend.present_wait()
            n = done_n + live_n
            wait = (done_wait + live_wait) / n if n else 0.0
        clear = m["emergency_clearance_time"]
        return [round(float(wait), 1), round(float(m["avg_queue"]), 2), int(m["throughput"]),
                round(float(m["avg_speed"]), 2),
                round(float(clear), 1) if _finite(clear) else -1, int(m["n_emergencies"])]

    def preempted(self) -> List[int]:
        return sorted(self.j_of[i] for i in self.emergency.preempted_intersections())


# --------------------------------------------------------------------------- #
# the shared session
# --------------------------------------------------------------------------- #
class DashboardSession:
    """Owns the RL and fixed-time runners, the command queue and the frame builder."""

    def __init__(self, config_path: Optional[str] = None,
                 hardware: Optional[Dict[str, Any]] = None) -> None:
        """``hardware`` overrides the ``hardware:`` block of the config (``run.py demo
        --hardware`` passes ``{"enabled": True, "port": ...}``); with hardware disabled,
        the default, nothing hardware-related is imported or run."""
        self.cfg = cfg = load_config(config_path)

        def dash(key: str, default: Any) -> Any:
            value = cfg.get_path(f"dashboard.{key}", None)
            return default if value is None else value

        self.tick_ms = int(dash("tick_ms", 200))
        self.tick_s = self.tick_ms / 1000.0
        self.speeds = [float(s) for s in dash("speeds", [0.2, 0.5, 1, 2, 4, 8])]
        self.speed = self._snap_speed(float(dash("default_speed", 1)))
        self.max_clients = int(dash("max_clients", 100))
        self.max_active_emergencies = int(dash("max_active_emergencies", 4))
        self.queue_cap = int(dash("queue_render_cap", 24))
        self.keyframe_every_s = float(dash("keyframe_every_s", 10))
        self.window = int(dash("chart_window", 120))
        scenarios = list(cfg.scenarios.keys())
        default_scenario = str(dash("default_scenario", "medium"))
        self.scenario = default_scenario if default_scenario in scenarios else scenarios[0]
        self.seed = int(cfg.seed)
        self.emergency_enabled = bool(cfg.get_path("emergency.enabled", True))
        self.emergency_types = vehicle_catalogue.emergency_types(cfg) if self.emergency_enabled else []
        self._emergency_corridor = {e["kind"]: e["corridor"] for e in self.emergency_types}

        # checkpoint: the full model, else the --quick one, else an untrained policy
        full = default_checkpoint_path(cfg)
        quick = full.with_name(full.stem + "_quick.pt")
        ckpt = full if full.exists() else quick if quick.exists() else None
        self.ckpt_exists = ckpt is not None
        self.ckpt_name = ckpt.name if ckpt is not None else None

        self._lock = threading.RLock()
        self.rl = ControllerRunner(cfg, "rl", self.scenario, self.seed,
                                   checkpoint=str(ckpt if ckpt is not None else full))
        self.ft = ControllerRunner(cfg, "fixed_time", self.scenario, self.seed)
        learner = getattr(self.rl.controller, "learner", None)
        self.rl_backend_name = getattr(learner, "backend_name", "n/a")

        self.playing = False
        self.tick_count = 0
        self.episode = 0
        self._credit = 0.0
        self._manual_steps = 0
        self._commands: Deque[Tuple] = deque()
        self._em_pairs: List[Tuple[str, str]] = []    # (RL id, fixed-time id) per dispatch
        self._hist: Deque[List[float]] = deque(maxlen=self.window)
        self._new_hist: List[List[float]] = []
        self._base: Dict[str, Dict[str, Any]] = {}
        self._base_global: Dict[str, Any] = {}
        self._force_keyframe = True
        self._last_keyframe = 0.0
        self._advanced = False
        self._record_history()

        # optional physical signal board (docs/HARDWARE.md)
        self.hw = None                     # atsc.hw.HardwareMirror while a board is attached
        self.hw_settings: Optional[Dict[str, Any]] = None
        self.rt_speed: Optional[float] = None
        self._hw_counts = {"cars": 0, "queues": 0, "emergencies": 0, "ignored": 0}
        self._hw_text_at = -1e9
        wanted = (hardware or {}).get("enabled")
        if wanted if wanted is not None else bool(cfg.get_path("hardware.enabled", False)):
            self._setup_hardware(hardware)

        log.info("Dashboard session ready (scenario=%s, seed=%s, RL backend=%s, checkpoint=%s)",
                 self.scenario, self.seed, self.rl_backend_name, self.ckpt_name or "UNTRAINED")

    # ------------------------------------------------------------------ #
    # hardware (python run.py demo --hardware)
    # ------------------------------------------------------------------ #
    def _setup_hardware(self, overrides: Optional[Dict[str, Any]]) -> None:
        from atsc.hw import hardware_settings, make_mirror

        hw = hardware_settings(self.cfg, dict(overrides or {}, enabled=True))
        self.hw_settings = hw
        dec_s = float(self.cfg.signal.decision_interval_s)
        topo = self.rl.env.topo
        self.hw = make_mirror(hw, valid_tls=list(topo.order), n_phases=self.cfg.n_phases,
                              decision_s=dec_s)
        self._hw_runner = self.ft if hw["mirror"] == "ft" else self.rl
        if hw["lamps"]:
            self._hw_runner.recorder.set_tap(self.hw.push)
            env = self._hw_runner.env          # the opening aspects were published before
            self.hw.push(env.backend.time, env.signal_aspects())   # the tap existed
        # "real time": one simulated second per real second, so a 3 s amber lasts 3 s
        self.rt_speed = round(self.tick_s / dec_s, 6)
        self.speeds = sorted(set(self.speeds) | {self.rt_speed, round(2 * self.rt_speed, 6)})
        if hw["realtime"]:
            self.speed = self.rt_speed
        self.hw.start()
        log.info("Hardware mode: port=%s, lamps follow the %s grid, sensors at %s, "
                 "speed %s", hw["port"], "fixed-time" if hw["mirror"] == "ft" else "RL",
                 hw["instrumented_tls"], self._speed_label(self.speed))

    def _speed_label(self, speed: float) -> str:
        if not self.rt_speed:
            return f"{speed:g}x"
        ratio = speed / self.rt_speed
        return "real time" if abs(ratio - 1) < 1e-6 else f"{ratio:g}x real time"

    def _hw_tick(self) -> None:
        """Turn what the board reported into commands, and tell it how fast to play."""
        for event in self.hw.take_events():
            self._hw_event(event)
        dec_s = float(self.cfg.signal.decision_interval_s)
        # paused: play any seconds still queued (a manual step) at real time
        self.hw.set_rate(self.speed * dec_s / self.tick_s if self.playing else 1.0)
        now = time.monotonic()
        if now - self._hw_text_at >= 1.0:
            self._hw_text_at = now
            self.hw.set_text(self._hw_text())

    def _hw_event(self, event) -> None:
        hw = self.hw_settings or {}
        full = len(self._commands) >= _MAX_PENDING_COMMANDS
        if event.kind in ("detect", "queue"):
            if not hw.get("detectors", True) or full:
                self._hw_counts["ignored"] += 1
            elif event.kind == "detect":
                self._commands.append(("detect", event.tls, event.approach, 1))
            elif event.count > 0:
                self._commands.append(("queue", event.tls, event.approach, min(event.count, 10)))
        elif event.kind == "emergency":
            kind = event.vehicle if event.vehicle in self._emergency_corridor else next(
                (k for k, c in self._emergency_corridor.items() if c == event.corridor), None)
            if not hw.get("rf_preemption", True) or kind is None:
                self._hw_counts["ignored"] += 1
                return
            try:
                self.submit({"a": "inject", "kind": kind, "corridor": event.corridor})
                self._hw_counts["emergencies"] += 1
            except CommandError as exc:
                self._hw_counts["ignored"] += 1
                log.info("Remote button ignored: %s", exc)
        elif event.kind == "control" and event.action == "toggle" and not full:
            self._commands.append(("pause",) if self.playing else ("play",))

    def _hw_feed(self, tls: str, approach: str, n: int) -> int:
        """Add ``n`` detected vehicles to both grids (the race stays fair)."""
        if n <= 0 or self.rl.done or self.ft.done:
            return 0
        added = self.rl.env.feed_detection(tls, approach, n)
        self.ft.env.feed_detection(tls, approach, n)
        self._hw_counts["cars"] += added
        return added

    def _hw_text(self) -> List[str]:
        """Four lines for the board's OLED (21 characters each)."""
        rl_m, ft_m = self.rl.live_metrics(), self.ft.live_metrics()
        e = int(self.rl.env._elapsed)
        shown = "Fixed" if self._hw_runner is self.ft else "AI"
        if not self.playing:
            status = "PAUSED"
        elif self.rl.active_emergencies():
            status = f"EMERGENCY x{self.rl.active_emergencies()}"
        else:
            status = self._speed_label(self.speed)
        return [f"{shown} lamps {e // 60:02d}:{e % 60:02d} {self.scenario}",
                f"AI wait    {rl_m[0]:6.1f} s", f"Fixed wait {ft_m[0]:6.1f} s", status]

    def close(self) -> None:
        """Release the hardware (all-red, then close the port). Safe to call twice."""
        with self._lock:
            hw, self.hw = self.hw, None
        if hw is not None:
            hw.close()
            for runner in (self.rl, self.ft):
                runner.recorder.set_tap(None)

    # ------------------------------------------------------------------ #
    # commands
    # ------------------------------------------------------------------ #
    def _snap_speed(self, value: float) -> float:
        return min(self.speeds, key=lambda s: (abs(s - value), s))

    def submit(self, cmd: Any) -> None:
        """Validate a browser command and queue it for the next tick.

        Accepts ``{"a": ...}`` (current) and ``{"action": ...}`` (version 1) shapes. Raises
        :class:`CommandError` with a user-facing message when the command is invalid.
        """
        if not isinstance(cmd, dict):
            raise CommandError("command must be a JSON object")
        action = cmd.get("a", cmd.get("action"))
        value = cmd.get("v", cmd.get("value"))
        if action in ("play", "pause", "step", "reset"):
            parsed: Tuple = (action,)
        elif action == "speed":
            if not _finite(value):
                raise CommandError("speed must be a number")
            parsed = ("speed", self._snap_speed(float(value)))
        elif action == "scenario":
            if not isinstance(value, str) or value not in self.cfg.scenarios:
                raise CommandError(f"unknown scenario {value!r}; choose from "
                                   f"{list(self.cfg.scenarios.keys())}")
            parsed = ("scenario", value)
        elif action in ("inject", "emergency"):
            if not self.emergency_enabled or not self.emergency_types:
                raise CommandError("emergency vehicles are disabled on this server")
            kind = cmd.get("kind") or "ambulance"
            if kind not in self._emergency_corridor:
                raise CommandError(f"unknown emergency type {kind!r}; choose from "
                                   f"{list(self._emergency_corridor)}")
            corridor = cmd.get("corridor") or self._emergency_corridor[kind]
            if corridor not in vehicle_catalogue.CORRIDORS:
                raise CommandError("corridor must be 'ew' or 'ns'")
            parsed = ("inject", kind, corridor)
        else:
            raise CommandError(f"unknown action {action!r}")

        with self._lock:
            if parsed[0] == "inject":
                pending = sum(1 for c in self._commands if c[0] == "inject")
                if self.rl.active_emergencies() + pending >= self.max_active_emergencies:
                    raise CommandError(f"{self.max_active_emergencies} emergency vehicles are "
                                       "already on the road - wait for one to clear")
            if len(self._commands) >= _MAX_PENDING_COMMANDS:
                raise CommandError("the server is busy, try again in a moment")
            self._commands.append(parsed)

    # convenience used by asgi.py / run.py (applied immediately, before serving)
    def set_scenario(self, scenario: str) -> None:
        if scenario not in self.cfg.scenarios:
            raise ValueError(f"unknown scenario {scenario!r}; choose from "
                             f"{list(self.cfg.scenarios.keys())}")
        with self._lock:
            self.scenario = scenario
            self._reset_runners()

    def play(self) -> None:
        with self._lock:
            self.playing = True

    def pause(self) -> None:
        with self._lock:
            self.playing = False

    def set_speed(self, speed: float) -> None:
        with self._lock:
            self.speed = self._snap_speed(float(speed))

    def reset(self) -> None:
        with self._lock:
            self._reset_runners()

    def _reset_runners(self) -> None:
        self.rl.reset(self.scenario, self.seed)
        self.ft.reset(self.scenario, self.seed)
        self._em_pairs = []
        self.episode += 1
        self._credit = 0.0
        self._manual_steps = 0
        self._hist.clear()
        self._new_hist = []
        self._record_history()
        self._force_keyframe = True

    def _apply(self, cmd: Tuple) -> None:
        action = cmd[0]
        if action == "play":
            self.playing = True
        elif action == "pause":
            self.playing = False
        elif action == "step":
            self._manual_steps = min(_MAX_MANUAL_STEPS, self._manual_steps + 1)
        elif action == "reset":
            self._reset_runners()
        elif action == "speed":
            self.speed = cmd[1]
        elif action == "scenario":
            self.scenario = cmd[1]
            self._reset_runners()
        elif action == "inject":
            _a, kind, corridor = cmd
            if self.rl.active_emergencies() >= self.max_active_emergencies:
                return
            if self.rl.done or self.ft.done:
                return
            rl_id = self.rl.inject(kind, corridor)
            ft_id = self.ft.inject(kind, corridor)
            if rl_id is not None and ft_id is not None:
                self._em_pairs.append((rl_id, ft_id))
        elif action == "detect":                  # a car passed an IR sensor on the model
            _a, tls, approach, n = cmd
            self._hw_feed(tls, approach, n)
        elif action == "queue":                   # a car is parked on a queue sensor
            _a, tls, approach, n = cmd
            if self.rl.done or self.ft.done:
                return
            self._hw_counts["queues"] += 1
            # top the queue up to what the sensor implies, on the grid the board shows
            self._hw_feed(tls, approach, n - self._hw_runner.env.backend.queue(tls, approach))

    # ------------------------------------------------------------------ #
    # stepping
    # ------------------------------------------------------------------ #
    def tick(self) -> Optional[Dict[str, Any]]:
        """Apply queued commands, advance the race, and return the frame to broadcast
        (``None`` when nothing changed)."""
        with self._lock:
            if self.hw is not None:
                self._hw_tick()
            while self._commands:
                self._apply(self._commands.popleft())
            steps = self._manual_steps
            self._manual_steps = 0
            if self.playing:
                self._credit += self.speed
                whole = int(self._credit)
                self._credit -= whole
                steps += whole
            self._advanced = False
            lamps = self.speed < _LITE_SPEED
            self.rl.recorder.set_lamp_recording(lamps)
            self.ft.recorder.set_lamp_recording(lamps)
            for _ in range(min(steps, _MAX_STEPS_PER_TICK)):
                if self.rl.done and self.ft.done:
                    self._reset_runners()          # auto-loop for a continuous demo
                self.rl.step()
                self.ft.step()
                self._record_history()
                self._advanced = True
            self.tick_count += 1
            return self._build_frame()

    def _record_history(self) -> None:
        rl_m, ft_m = self.rl.live_metrics(), self.ft.live_metrics()
        point = [int(round(self.rl.env._elapsed)), rl_m[0], ft_m[0], rl_m[1], ft_m[1]]
        self._hist.append(point)
        self._new_hist.append(point)
        if len(self._new_hist) > self.window:
            del self._new_hist[: len(self._new_hist) - self.window]

    # ------------------------------------------------------------------ #
    # frames
    # ------------------------------------------------------------------ #
    def _global(self) -> Dict[str, Any]:
        g: Dict[str, Any] = {"p": int(self.playing), "sp": self.speed, "sc": self.scenario}
        if self.hw is not None:
            # board: 0 offline, 1 port open but silent, 2 live; cars and emergencies it sent
            g["hw"] = [self.hw.state(), self._hw_counts["cars"], self._hw_counts["emergencies"]]
        return g

    def _paired_clearance(self) -> Tuple[int, float, float]:
        """Mean clearance time on each grid over the emergency vehicles that have cleared
        both, so that the comparison is always between the same vehicles."""
        rl_t, ft_t = self.rl.recorder.em_times, self.ft.recorder.em_times
        both = [(rl_t[a], ft_t[b]) for a, b in self._em_pairs if a in rl_t and b in ft_t]
        if not both:
            return 0, -1.0, -1.0
        n = len(both)
        return (n, round(sum(r for r, _f in both) / n, 1), round(sum(f for _r, f in both) / n, 1))

    def _metrics(self, name: str) -> List[float]:
        n, rl_mean, ft_mean = self._paired_clearance()
        runner = self.rl if name == "rl" else self.ft
        return runner.live_metrics() + [rl_mean if name == "rl" else ft_mean, n]

    def _side_full(self, name: str) -> Dict[str, Any]:
        runner = self.rl if name == "rl" else self.ft
        q, qk = runner.queues(self.queue_cap)
        return {"s": runner.lamps(), "q": q, "qk": [[i, s] for i, s in enumerate(qk)],
                "mv": runner.moving(), "m": self._metrics(name), "pr": runner.preempted(),
                "ne": runner.active_emergencies()}

    def _side_events(self, runner: ControllerRunner, side: Dict[str, Any]) -> None:
        """Add the events recorded since the last frame (``a``, ``x``, ``tl``) to ``side``."""
        rec = runner.recorder
        bt10 = int(round(runner.env.backend.time * 10))
        if rec.adds:
            side["a"] = rec.flat_adds(bt10)
        if rec.exits:
            exits = rec.flat_exits(bt10)
            if exits:
                side["x"] = exits
        if rec.tl and self.speed < _LITE_SPEED:
            side["tl"] = [round(rec.tl0, 1), "".join(rec.tl)]

    def _keyframe_locked(self, events: bool = False) -> Dict[str, Any]:
        frame: Dict[str, Any] = {"t": "f", "kf": 1, "k": self.tick_count, "ep": self.episode,
                                 "bt": round(self.rl.env.backend.time, 1),
                                 "e": int(round(self.rl.env._elapsed))}
        frame.update(self._global())
        frame["rl"] = self._side_full("rl")
        frame["ft"] = self._side_full("ft")
        if events:
            self._side_events(self.rl, frame["rl"])
            self._side_events(self.ft, frame["ft"])
        frame["h"] = [list(p) for p in self._hist]
        return frame

    def _commit(self, name: str, runner: ControllerRunner) -> Dict[str, Any]:
        """Compute the side delta against the baseline and move the baseline forward."""
        base = self._base.get(name)
        q, qk = runner.queues(self.queue_cap)
        s, m, pr = runner.lamps(), self._metrics(name), runner.preempted()
        ne = runner.active_emergencies()
        rec = runner.recorder
        side: Dict[str, Any] = {}
        if base is None or s != base["s"]:
            side["s"] = s
        if base is None or q != base["q"]:
            side["q"] = q
        changed_qk = [[i, k] for i, k in enumerate(qk) if base is None or k != base["qk"][i]]
        if changed_qk:
            side["qk"] = changed_qk
        self._side_events(runner, side)
        if base is None or m != base["m"]:
            side["m"] = m
        if base is None or pr != base["pr"]:
            side["pr"] = pr
        if base is None or ne != base["ne"]:
            side["ne"] = ne
        self._base[name] = {"s": s, "q": q, "qk": qk, "m": m, "pr": pr, "ne": ne}
        rec.clear()
        return side

    def _commit_full(self) -> None:
        for name, runner in (("rl", self.rl), ("ft", self.ft)):
            q, qk = runner.queues(self.queue_cap)
            self._base[name] = {"s": runner.lamps(), "q": q, "qk": qk,
                                "m": self._metrics(name), "pr": runner.preempted(),
                                "ne": runner.active_emergencies()}
            runner.recorder.clear()
        self._base_global = self._global()
        self._new_hist = []

    def _build_frame(self) -> Optional[Dict[str, Any]]:
        now = time.monotonic()
        if self._force_keyframe or now - self._last_keyframe >= self.keyframe_every_s:
            frame = self._keyframe_locked(events=True)
            self._commit_full()
            self._force_keyframe = False
            self._last_keyframe = now
            return frame
        frame: Dict[str, Any] = {"t": "f", "k": self.tick_count}
        changed = False
        for name, runner in (("rl", self.rl), ("ft", self.ft)):
            side = self._commit(name, runner)
            if side:
                frame[name] = side
                changed = True
        if self._advanced or changed:
            frame["bt"] = round(self.rl.env.backend.time, 1)
            frame["e"] = int(round(self.rl.env._elapsed))
        glob = self._global()
        for key, value in glob.items():
            if self._base_global.get(key) != value:
                frame[key] = value
                changed = True
        self._base_global = glob
        if self._new_hist:
            frame["h"] = self._new_hist
            self._new_hist = []
            changed = True
        return frame if (changed or self._advanced) else None

    def request_keyframe(self) -> None:
        """Make the next tick broadcast a keyframe (used after dropping a slow client's backlog)."""
        with self._lock:
            self._force_keyframe = True

    # ------------------------------------------------------------------ #
    # read-only snapshots (new viewers, polling, health)
    # ------------------------------------------------------------------ #
    def snapshot(self) -> Dict[str, Any]:
        """A keyframe of the current state. Between ticks this equals the last broadcast
        state, so a new viewer can apply the next delta on top of it."""
        with self._lock:
            return self._keyframe_locked()

    def hello(self) -> Dict[str, Any]:
        with self._lock:
            topo = self.rl.env.topo
            # [kind, label, group, length m, width m, emergency 0/1, speed factor]; the speed
            # factor gives the browser each vehicle's link travel time: link / (free * sf)
            kinds = [[v.kind, v.label, v.group, v.length_m, v.width_m, int(v.emergency),
                      vehicle_catalogue.speed_factor(self.cfg, v.kind) if v.emergency else 1.0]
                     for v in vehicle_catalogue.VEHICLE_TYPES]
            mix_kinds, mix_probs = vehicle_catalogue.traffic_mix(self.cfg)
            return {
                "t": "hello", "v": PROTOCOL_VERSION, "build": build_id(),
                "grid": {
                    "rows": topo.grid_rows, "cols": topo.grid_cols, "link": topo.link_length_m,
                    "ids": list(topo.order),
                    "xy": [[topo.get(i).x, topo.get(i).y] for i in topo.order],
                    "phases": [[p.name, "".join(p.green_approaches)]
                               for p in topo.get(topo.order[0]).phases],
                },
                "kinds": kinds, "qka": QK_ALPHABET, "nv": COLOUR_VARIANTS,
                "mix": {k: round(float(p), 4) for k, p in zip(mix_kinds, mix_probs)},
                "emg": [[e["kind"], e["label"], e["corridor"]] for e in self.emergency_types],
                "scen": [[name, str(spec.get("description", name))]
                         for name, spec in self.cfg.scenarios.items()],
                "speeds": self.speeds, "tick_ms": self.tick_ms,
                "dec_s": int(self.cfg.signal.decision_interval_s),
                "step_s": float(self.cfg.sim.step_length_s),
                "ep_s": int(self.cfg.sim.episode_seconds),
                "free_mps": float(self.cfg.sim.free_speed_mps),
                "win": self.window, "qcap": self.queue_cap,
                "meta": {"nn": self.rl_backend_name, "ckpt": self.ckpt_exists,
                         "ckpt_name": self.ckpt_name, "max_em": self.max_active_emergencies},
                **self._hw_hello(),
                "state": self._keyframe_locked(),
            }

    def _hw_hello(self) -> Dict[str, Any]:
        if self.hw is None or not self.hw_settings:
            return {}
        hw = self.hw_settings
        return {"rt": self.rt_speed,
                "hw": {"port": str(hw["port"]), "mirror": hw["mirror"],
                       "tls": str(hw["instrumented_tls"])}}

    def info(self) -> Dict[str, Any]:
        with self._lock:
            out = {"tick": self.tick_count, "playing": self.playing, "speed": self.speed,
                   "scenario": self.scenario, "elapsed_s": int(self.rl.env._elapsed),
                   "nn_backend": self.rl_backend_name, "checkpoint": self.ckpt_name}
            if self.hw is not None:
                out["hardware"] = dict(self.hw.snapshot(), **self._hw_counts)
            return out
