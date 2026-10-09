"""Dashboard session: protocol completeness, between-frame reconstruction, commands, budgets."""
import json
import math
from pathlib import Path

import pytest
import yaml

from atsc import vehicles
from atsc.dashboard.runtime import encode
from atsc.dashboard.session import (COLOUR_VARIANTS, KIND_CODE, QK_ALPHABET, CommandError,
                                    ControllerRunner, DashboardSession, queue_char)
from atsc.envs.traffic_env import MultiAgentTrafficEnv
from atsc.control import EmergencyController, build_controller
from atsc.sim.backend import APPROACHES
from atsc.sim.mini_backend import vehicle_number

from dashboard_model import Model, keyframe_comparable

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path, **dashboard):
    """A copy of config.yaml with dashboard overrides (and optionally a shorter episode)."""
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    episode = dashboard.pop("episode_seconds", None)
    raw.setdefault("dashboard", {}).update(dashboard)
    if episode is not None:
        raw["sim"]["episode_seconds"] = episode
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return str(path)


@pytest.fixture(scope="module")
def session():
    s = DashboardSession()
    s.play()
    return s


def _json(obj):
    return json.loads(encode(obj))


# --------------------------------------------------------------------------- #
# hello
# --------------------------------------------------------------------------- #
def test_hello_describes_the_whole_dashboard(session):
    hello = _json(session.hello())
    assert hello["t"] == "hello" and hello["v"] == 2 and len(hello["build"]) == 12
    grid = hello["grid"]
    assert grid["rows"] * grid["cols"] == len(grid["ids"]) == len(grid["xy"])
    assert [k[0] for k in hello["kinds"]] == list(vehicles.KINDS)
    for kind, label, group, length, width, emergency, speed in hello["kinds"]:
        assert label and group and 0 < width < length and speed > 0
        assert emergency == int(vehicles.is_emergency(kind))
    assert hello["qka"] == QK_ALPHABET and hello["nv"] == COLOUR_VARIANTS
    assert '"' not in QK_ALPHABET and "\\" not in QK_ALPHABET
    assert [e[0] for e in hello["emg"]] == ["ambulance", "police", "fire"]
    assert {name for name, _d in hello["scen"]} == {"low", "medium", "high", "rush"}
    assert hello["speeds"] == sorted(hello["speeds"]) and 1 in hello["speeds"]
    assert abs(sum(hello["mix"].values()) - 1.0) < 1e-3
    assert hello["state"]["kf"] == 1
    assert len(encode(session.hello())) < 9000


def test_queue_char_round_trips():
    for kind in vehicles.KINDS:
        for vid in ("v1", "v2", "v3", "v4", "EMG77"):
            code = QK_ALPHABET.index(queue_char(kind, vid))
            assert code // COLOUR_VARIANTS == KIND_CODE[kind]
            assert code % COLOUR_VARIANTS == vehicle_number(vid) % COLOUR_VARIANTS


# --------------------------------------------------------------------------- #
# the delta stream loses nothing
# --------------------------------------------------------------------------- #
SCRIPT = {
    3: {"a": "inject", "kind": "ambulance"},
    6: {"a": "speed", "v": 4},
    9: {"a": "inject", "kind": "police"},
    14: {"a": "speed", "v": 8},
    20: {"a": "inject", "kind": "fire", "corridor": "ns"},
    26: {"a": "speed", "v": 0.5},
    40: {"a": "pause"},
    42: {"a": "step"},
    43: {"a": "step"},
    45: {"a": "play"},
    48: {"a": "scenario", "v": "rush"},
    60: {"a": "speed", "v": 2},
    75: {"a": "reset"},
    80: {"action": "emergency", "corridor": "ew"},   # version-1 shape still accepted
    90: {"a": "speed", "v": 1},
}


def test_delta_stream_reconstructs_every_keyframe(tmp_path):
    s = DashboardSession(_config(tmp_path, keyframe_every_s=3600))
    s.play()
    model = Model()
    model.apply(_json(s.hello()))
    frames = deltas = 0
    for tick in range(120):
        if tick in SCRIPT:
            s.submit(SCRIPT[tick])
        frame = s.tick()
        if frame is None:
            continue
        frames += 1
        deltas += 0 if frame.get("kf") else 1
        model.apply(_json(frame))
        expected = keyframe_comparable(model.hello, _json(s.snapshot()))
        assert model.comparable() == expected, f"diverged at tick {tick}"
    assert frames > 100 and deltas > 90


def test_snapshot_equals_last_broadcast_state(session):
    """A viewer joining between two ticks must be able to apply the next delta."""
    for _ in range(3):
        session.tick()
    model = Model()
    model.apply(_json(session.hello()))
    for _ in range(10):
        frame = session.tick()
        if frame is not None:
            model.apply(_json(frame))
    assert model.comparable() == keyframe_comparable(model.hello, _json(session.snapshot()))


# --------------------------------------------------------------------------- #
# between-frame reconstruction = the simulator's real state
# --------------------------------------------------------------------------- #
class _Truth:
    """Records every simulated second of one network: queues and links before the step."""

    def __init__(self, runner: ControllerRunner, session: DashboardSession) -> None:
        self.backend = runner.env.backend
        self.session = session
        self.ai_of = runner.ai_of
        self.boundary = {runner.ai_of[(iid, a)] for iid in runner.env.topo.order
                         for a in APPROACHES if runner.env.topo.get(iid).neighbors[a] is None}
        self.at = {}
        original = self.backend.step

        def step():
            t = int(round(self.backend.time))
            queues = []
            for iid in runner.env.topo.order:
                for a in APPROACHES:
                    queues.append([(vehicle_number(v.id), v.kind)
                                   for v in self.backend._queues[(iid, a)]])
            transit = {vehicle_number(v.id) for v, *_ in self.backend._transit}
            self.at[(session.episode, t)] = (queues, transit)
            return original()

        self.backend.step = step


def _code(kind, vid):
    return KIND_CODE[kind] * COLOUR_VARIANTS + vid % COLOUR_VARIANTS


@pytest.mark.parametrize("scenario", ["medium", "rush"])
def test_between_frames_the_browser_draws_the_real_simulation(tmp_path, scenario):
    s = DashboardSession(_config(tmp_path, default_scenario=scenario, keyframe_every_s=3600))
    truths = {"rl": _Truth(s.rl, s), "ft": _Truth(s.ft, s)}
    s.play()
    model = Model()
    model.apply(_json(s.hello()))
    departures = {"rl": {}, "ft": {}}       # (ai) -> sorted list of departure seconds
    checked = 0
    for tick in range(90):
        if tick in (5, 30):
            s.submit({"a": "inject", "kind": "ambulance" if tick == 5 else "police"})
        if tick in (20, 50):
            s.request_keyframe()               # a same-episode keyframe is merged, not swapped
        prev_bt, prev_ep = model.bt, model.ep
        frame = s.tick()
        if frame is None:
            continue
        frame = _json(frame)
        model.apply(frame)
        if frame.get("kf") and frame.get("ep") != prev_ep:
            continue                           # a new episode: nothing to compare against
        bt10 = int(round(model.bt * 10))
        for name in ("rl", "ft"):
            side_msg = frame.get(name, {})
            crossing_now = {}
            flat = side_msg.get("a", [])
            for i in range(0, len(flat), 5):
                vid, _k, src, _dst, dt0 = flat[i:i + 5]
                d = (bt10 + dt0) / 10.0 - 1.0
                departures[name].setdefault(src, []).append(d)
                crossing_now.setdefault(int(round(d)), set()).add(vid)
            flat = side_msg.get("x", [])
            for i in range(0, len(flat), 5):
                vid, _k, src, _dir, dt = flat[i:i + 5]
                d = (bt10 + dt) / 10.0 - 1.0
                departures[name].setdefault(src, []).append(d)
                crossing_now.setdefault(int(round(d)), set()).add(vid)
            truth = truths[name]
            for t in range(int(round(prev_bt)), int(round(model.bt))):
                queues, transit = truth.at[(s.episode, t)]
                gone = crossing_now.get(t, set())
                shown = model.sides[name].display(float(t))
                for ai, real in enumerate(queues):
                    real = [_code(k, vid) for vid, k in real if vid not in gone]
                    q = shown["queues"][ai]
                    codes = q["codes"]
                    if ai in truth.boundary:      # later spawns may already be at the tail
                        assert q["total"] >= len(real)
                        assert codes[:len(real)] == real[:len(codes)]
                    else:
                        assert q["total"] == len(real), (name, t, ai)
                        assert codes == real[:len(codes)], (name, t, ai)
                    done = sum(1 for d in departures[name].get(ai, []) if d <= t)
                    assert q["abs0"] == done, (name, t, ai)
                movers = {vid for lst in shown["movers"] for _j, vid, *_r in lst}
                assert movers == transit - gone, (name, t)
                assert {c[0] for c in shown["crossing"]} == gone, (name, t)
                checked += 1
    assert checked > 300


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cmd, message", [
    ("play", "JSON object"),
    ({"a": "fly"}, "unknown action"),
    ({"a": "speed", "v": "fast"}, "number"),
    ({"a": "speed", "v": float("nan")}, "number"),
    ({"a": "speed", "v": True}, "number"),
    ({"a": "scenario", "v": "gridlock"}, "unknown scenario"),
    ({"a": "scenario", "v": 3}, "unknown scenario"),
    ({"a": "inject", "kind": "taxi"}, "unknown emergency type"),
    ({"a": "inject", "kind": "police", "corridor": "diagonal"}, "corridor"),
])
def test_bad_commands_are_rejected_with_a_reason(session, cmd, message):
    with pytest.raises(CommandError, match=message):
        session.submit(cmd)


def test_speed_snaps_to_the_configured_steps(tmp_path):
    s = DashboardSession(_config(tmp_path))
    s.submit({"a": "speed", "v": 3.1})
    s.submit({"action": "speed", "value": 100})
    s.tick()
    assert s.speed == 8.0
    s.submit({"a": "speed", "v": 0.3})
    s.tick()
    assert s.speed == 0.2


def test_bad_scenario_never_freezes_the_session(tmp_path):
    s = DashboardSession(_config(tmp_path))
    s.play()
    with pytest.raises(CommandError):
        s.submit({"a": "scenario", "v": "nope"})
    before = s.tick_count
    for _ in range(5):
        s.tick()
    assert s.tick_count == before + 5 and s.scenario == "medium"


def test_emergency_cap_is_enforced(tmp_path):
    s = DashboardSession(_config(tmp_path, max_active_emergencies=2))
    s.submit({"a": "inject", "kind": "ambulance"})
    s.submit({"a": "inject", "kind": "fire"})
    with pytest.raises(CommandError, match="already on the road"):
        s.submit({"a": "inject", "kind": "police"})
    s.tick()
    assert s.rl.active_emergencies() == 2 and s.ft.active_emergencies() == 2


def test_every_emergency_type_reaches_both_networks(tmp_path):
    s = DashboardSession(_config(tmp_path, max_active_emergencies=6))
    s.play()
    for kind in ("ambulance", "police", "fire"):
        s.submit({"a": "inject", "kind": kind})
    s.tick()
    for runner in (s.rl, s.ft):
        backend = runner.env.backend
        on_road = [v for q in backend._queues.values() for v in q]
        on_road += [v for v, *_rest in backend._transit]
        assert sorted(v.kind for v in on_road if v.is_emergency) == ["ambulance", "fire", "police"]
    for _ in range(40):
        s.tick()
    assert s.rl.live_metrics()[5] >= 1, "no emergency vehicle was ever cleared"


def test_clearance_kpi_compares_the_same_vehicles(tmp_path):
    """The KPI card's "N x faster" must compare like with like: the paired fields of ``m``
    average only the emergency vehicles that have already cleared *both* grids."""
    s = DashboardSession(_config(tmp_path, max_active_emergencies=6))
    s.set_scenario("rush")
    s.play()
    s.set_speed(4)
    for kind in ("ambulance", "police", "fire"):
        s.submit({"a": "inject", "kind": kind})
    saw_pair = saw_partial = False
    for _ in range(120):
        frame = s.tick() or {}
        rl, ft = s.snapshot()["rl"]["m"], s.snapshot()["ft"]["m"]
        assert len(rl) == len(ft) == 8
        pairs = rl[7]
        assert ft[7] == pairs and pairs <= min(rl[5], ft[5])
        if pairs == 0:
            assert rl[6] == ft[6] == -1
            continue
        saw_pair = True
        rl_t, ft_t = s.rl.recorder.em_times, s.ft.recorder.em_times
        both = [(rl_t[a], ft_t[b]) for a, b in s._em_pairs if a in rl_t and b in ft_t]
        assert len(both) == pairs
        assert rl[6] == round(sum(r for r, _ in both) / pairs, 1)
        assert ft[6] == round(sum(f for _, f in both) / pairs, 1)
        if rl[5] == ft[5] == pairs:              # the same vehicles: the plain means agree
            assert abs(rl[6] - rl[4]) <= 0.11 and abs(ft[6] - ft[4]) <= 0.11
        else:
            saw_partial = True
        if pairs == 3:
            break
    assert saw_pair, "no emergency vehicle cleared both grids"
    assert saw_partial or pairs == 3


def test_frames_count_emergency_vehicles_the_browser_is_not_sent(tmp_path):
    """``ne`` follows an emergency vehicle the browser cannot draw: one waiting behind the
    ``queue_render_cap`` vehicles that are sent of a long queue (the "+n" badge). The
    emergency banner relies on it, so it stays up until the vehicle has left that grid."""
    s = DashboardSession(_config(tmp_path, queue_render_cap=1, keyframe_every_s=3600))
    s.set_scenario("rush")
    s.play()
    for _ in range(12):                          # let the queues build up
        s.tick()
    model = Model()
    model.apply(_json(s.hello()))
    s.submit({"a": "inject", "kind": "ambulance"})
    hidden = seen = False
    for _ in range(300):
        frame = s.tick()
        if frame is not None:
            model.apply(_json(frame))
        for name, runner in (("rl", s.rl), ("ft", s.ft)):
            n = runner.active_emergencies()
            assert model.sides[name].ne == n, name
            seen = seen or n > 0
            for q in runner.env.backend._queues.values():
                hidden = hidden or any(v.is_emergency for v in list(q)[1:])
        if seen and not s.rl.active_emergencies() and not s.ft.active_emergencies():
            break
    assert seen and hidden, "the ambulance was never out of sight behind the drawn vehicles"
    assert model.sides["rl"].ne == model.sides["ft"].ne == 0


def test_emergencies_can_be_disabled(tmp_path):
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw["emergency"]["enabled"] = False
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    s = DashboardSession(str(path))
    assert s.hello()["emg"] == []
    with pytest.raises(CommandError, match="disabled"):
        s.submit({"a": "inject"})


def test_pending_command_queue_is_bounded(tmp_path):
    s = DashboardSession(_config(tmp_path))
    for _ in range(64):
        s.submit({"a": "play"})
    with pytest.raises(CommandError, match="busy"):
        s.submit({"a": "play"})


def test_paused_session_sends_nothing_but_still_answers(tmp_path):
    s = DashboardSession(_config(tmp_path))
    s.tick()                                   # initial keyframe
    assert s.tick() is None                    # paused and unchanged -> no frame
    s.submit({"a": "step"})
    frame = s.tick()
    assert frame is not None and frame["e"] == s.hello()["dec_s"]


# --------------------------------------------------------------------------- #
# episode loop, metrics, invariance
# --------------------------------------------------------------------------- #
def test_episode_end_loops_and_live_wait_matches_the_benchmark_metric(tmp_path):
    s = DashboardSession(_config(tmp_path, episode_seconds=120))
    s.play()
    s.submit({"a": "speed", "v": 8})
    final = None
    for _ in range(200):
        frame = s.tick()
        if s.rl.done and s.ft.done and final is None:
            final = (s.rl.live_metrics(), s.rl.env.metrics()["avg_waiting_time"])
        if frame and frame.get("kf") and s.episode > 1:
            break
    assert final is not None, "the episode never finished"
    live, benchmark = final
    assert live[0] == round(benchmark, 1)
    assert s.episode > 1, "the session did not loop to a new episode"


def test_dashboard_observers_do_not_change_the_simulation(cfg):
    runner = ControllerRunner(cfg, "fixed_time", "high", 3)
    env = MultiAgentTrafficEnv(cfg, backend_name="mini")
    ctrl = build_controller("fixed_time", cfg, env.topo)
    env.set_emergency_controller(EmergencyController(cfg, env.topo))
    env.reset(scenario="high", seed=3)
    ctrl.reset()
    for _ in range(80):
        runner.step()
        env.step(ctrl.act(env))
    seen, plain = runner.env.metrics(), env.metrics()
    assert seen.keys() == plain.keys()
    for key, value in plain.items():                # NaN-aware (no emergency -> NaN)
        assert value == seen[key] or (math.isnan(value) and math.isnan(seen[key])), key


# --------------------------------------------------------------------------- #
# budgets (generous bounds; tools/bench_server.py reports the real numbers)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("scenario, speed, mean_cap, max_cap", [
    ("medium", 1, 1400, 4000), ("rush", 1, 2000, 5000), ("rush", 8, 6000, 10000)])
def test_frame_payload_budget(tmp_path, scenario, speed, mean_cap, max_cap):
    s = DashboardSession(_config(tmp_path, default_scenario=scenario, keyframe_every_s=3600))
    s.play()
    s.submit({"a": "speed", "v": speed})
    sizes = []
    for _ in range(80):
        frame = s.tick()
        if frame is not None and not frame.get("kf"):
            sizes.append(len(encode(frame)))
    assert sizes
    assert sum(sizes) / len(sizes) < mean_cap and max(sizes) < max_cap
    assert len(encode(s.snapshot())) < 6000
    assert math.isfinite(sum(sizes))
