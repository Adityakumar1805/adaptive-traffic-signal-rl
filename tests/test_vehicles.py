"""Vehicle catalogue, traffic mix and emergency types - and proof that they are cosmetic."""
import copy
import re
from pathlib import Path

import numpy as np
import pytest

from atsc import vehicles
from atsc.config import Config, validate_config
from atsc.control import EmergencyController, build_controller
from atsc.envs.traffic_env import MultiAgentTrafficEnv
from atsc.seeding import make_rng
from atsc.sim.mini_backend import _pick_kind, mix_cdf

ROOT = Path(__file__).resolve().parents[1]
SPRITES_JS = ROOT / "src" / "atsc" / "dashboard" / "static" / "js" / "sprites.js"

#: the wire order is part of the protocol: kinds may only ever be appended
WIRE_ORDER = ["bike", "scooter", "bicycle", "cycle_rickshaw", "auto", "erick", "car", "taxi",
              "suv", "van", "tempo", "truck", "tractor", "bus", "school_bus", "ambulance",
              "police", "fire"]


def test_catalogue_is_append_only_and_sane():
    assert list(vehicles.KINDS[:len(WIRE_ORDER)]) == WIRE_ORDER
    assert len(set(vehicles.KINDS)) == len(vehicles.KINDS)
    for v in vehicles.VEHICLE_TYPES:
        assert 0.4 < v.width_m < v.length_m <= 14 and v.label and v.group
    assert set(vehicles.EMERGENCY_KINDS) == {"ambulance", "police", "fire"}
    assert not set(vehicles.REGULAR_KINDS) & set(vehicles.EMERGENCY_KINDS)


def test_every_kind_has_a_sprite_and_every_emergency_kind_has_beacons():
    js = SPRITES_JS.read_text(encoding="utf-8")
    painters = js[js.index("const PAINTERS = {"):js.index("};", js.index("const PAINTERS = {"))]
    painted = set(re.findall(r"^\s{2}(\w+):", painters, flags=re.M))
    assert painted == set(vehicles.KINDS), painted ^ set(vehicles.KINDS)
    beacons = js[js.index("const BEACONS = {"):js.index("};", js.index("const BEACONS = {"))]
    assert set(re.findall(r"^\s{2}(\w+):", beacons, flags=re.M)) == set(vehicles.EMERGENCY_KINDS)


def test_default_mix_is_an_indian_city_mix():
    kinds, probs = vehicles.traffic_mix(None)
    assert abs(probs.sum() - 1) < 1e-12 and (probs > 0).all()
    share = dict(zip(kinds, probs))
    two_wheelers = share["bike"] + share["scooter"] + share["bicycle"]
    assert 0.4 < two_wheelers < 0.65                      # about half of all vehicles
    assert share["auto"] + share["erick"] > share["bus"] + share["truck"]
    assert set(kinds) == set(vehicles.REGULAR_KINDS)


def test_mix_from_config_drops_zero_weights_and_keeps_catalogue_order():
    cfg = Config({"traffic_mix": {"bus": 1, "bike": 3, "car": 0}})
    kinds, probs = vehicles.traffic_mix(cfg)
    assert kinds == ("bike", "bus") and np.allclose(probs, [0.75, 0.25])


@pytest.mark.parametrize("mix, fragment", [
    ({"hovercraft": 1}, "unknown kind"),
    ({"ambulance": 1}, "injected, not mixed in"),
    ({"car": -1}, ">= 0"),
    ({"car": "lots"}, ">= 0"),
    ({"car": True}, ">= 0"),
    ({"car": float("nan")}, ">= 0"),
    ({"car": 0, "bike": 0}, "all be zero"),
    (["car"], "mapping"),
])
def test_bad_mixes_are_reported(mix, fragment):
    errors = vehicles.mix_errors(mix)
    assert errors and fragment in " ".join(errors)


@pytest.mark.parametrize("types, fragment", [
    ({"ambulance": {"speed_factor": 2}}, "not allowed"),
    ({"police": {"speed_factor": 9}}, "[0.3, 3]"),
    ({"police": {"corridor": "diagonal"}}, "corridor"),
    ({"fire": {"label": ""}}, "label"),
    ({"fire": {"siren": "loud"}}, "unknown keys"),
    ({"taxi": {}}, "unknown kind"),
    ({}, "non-empty"),
])
def test_bad_emergency_types_are_reported(types, fragment):
    errors = vehicles.emergency_errors(types)
    assert errors and fragment in " ".join(errors)


def test_emergency_types_resolve_with_defaults(cfg):
    resolved = {e["kind"]: e for e in vehicles.emergency_types(cfg)}
    assert resolved["ambulance"]["speed_factor"] == float(cfg.emergency.vtype_speed_factor)
    assert resolved["police"]["corridor"] == "ns" and resolved["police"]["speed_factor"] == 1.4
    bare = Config({"emergency": {"vtype_speed_factor": 1.3, "types": {"police": None}}})
    (police,) = vehicles.emergency_types(bare)
    assert police == {"kind": "police", "label": "Police car", "corridor": "ns", "speed_factor": 1.4}


def test_bisect_sampling_equals_generator_choice():
    kinds, probs = vehicles.traffic_mix(None)
    cdf = mix_cdf(probs)
    a, b = make_rng(7, "kind"), make_rng(7, "kind")
    for _ in range(50_000):
        assert _pick_kind(a, kinds, cdf) == kinds[b.choice(len(kinds), p=probs)]


def _run(cfg, scenario="high", seed=3, steps=120, inject=None):
    env = MultiAgentTrafficEnv(cfg, backend_name="mini")
    ctrl = build_controller("fixed_time", cfg, env.topo)
    env.set_emergency_controller(EmergencyController(cfg, env.topo))
    env.reset(scenario=scenario, seed=seed)
    kinds = []
    for i in range(steps):
        if inject and i == 5:
            env.inject_emergency(inject[1], kind=inject[0])
        env.step(ctrl.act(env))
        kinds.extend(v.kind for q in env.backend._queues.values() for v in q)
    return env.metrics(), kinds


def _same_metrics(a, b):
    assert a.keys() == b.keys()
    for k in a:
        assert a[k] == b[k] or (np.isnan(a[k]) and np.isnan(b[k])), k


def test_traffic_mix_never_changes_the_traffic(cfg):
    """The published numbers cannot depend on the (cosmetic) mix: completely different mixes
    give bit-identical metrics, while the vehicles drawn really do follow the mix."""
    base, kinds_a = _run(cfg)
    other = copy.deepcopy(cfg)
    other["traffic_mix"] = {"bus": 5.0, "tractor": 1.0}
    alt, kinds_b = _run(Config(other))
    _same_metrics(base, alt)
    assert set(kinds_b) == {"bus", "tractor"}
    assert len(set(kinds_a)) > 8


@pytest.mark.parametrize("kind", ["ambulance", "police", "fire"])
@pytest.mark.parametrize("corridor", ["ew", "ns"])
def test_every_emergency_kind_can_be_injected_on_every_corridor(cfg, kind, corridor):
    env = MultiAgentTrafficEnv(cfg, backend_name="mini")
    env.reset(scenario="low", seed=1)
    vid = env.inject_emergency(corridor, kind=kind)
    assert vid and vid.startswith("EMG")
    found = [v for q in env.backend._queues.values() for v in q if v.id == vid]
    assert len(found) == 1 and found[0].kind == kind and found[0].is_emergency
    assert found[0].speed_factor == vehicles.speed_factor(cfg, kind)
    assert len(found[0].legs) == (cfg.network.grid_cols if corridor == "ew" else cfg.network.grid_rows)


def test_injection_rejects_unknown_kinds_and_corridors(cfg):
    env = MultiAgentTrafficEnv(cfg, backend_name="mini")
    env.reset(scenario="low", seed=1)
    with pytest.raises(ValueError):
        env.inject_emergency("ew", kind="taxi")
    with pytest.raises(ValueError):
        env.inject_emergency("diagonal", kind="police")


def test_injection_draws_no_random_numbers(cfg):
    """An emergency vehicle changes signals, never the arrival stream."""
    env_a = MultiAgentTrafficEnv(cfg, backend_name="mini")
    env_b = MultiAgentTrafficEnv(cfg, backend_name="mini")
    env_a.reset(scenario="medium", seed=9)
    env_b.reset(scenario="medium", seed=9)
    env_b.inject_emergency("ns", kind="fire")
    state_a = env_a.backend._rng.bit_generator.state
    state_b = env_b.backend._rng.bit_generator.state
    assert state_a == state_b
    assert env_a.backend._kind_rng.bit_generator.state == env_b.backend._kind_rng.bit_generator.state


def test_config_validation_covers_mix_emergencies_and_dashboard(cfg):
    for patch, fragment in [
        ({"traffic_mix": {"rocket": 1}}, "traffic_mix"),
        ({"emergency": dict(cfg.emergency, types={"ambulance": {"speed_factor": 3}})}, "ambulance"),
        ({"dashboard": dict(cfg.dashboard, speeds=[1, 1])}, "strictly increasing"),
        ({"dashboard": dict(cfg.dashboard, default_speed=3)}, "default_speed"),
        ({"dashboard": dict(cfg.dashboard, tick_ms=1)}, "tick_ms"),
        ({"dashboard": dict(cfg.dashboard, default_scenario="storm")}, "default_scenario"),
        ({"dashboard": dict(cfg.dashboard, backend="sumo")}, "dashboard.backend"),
    ]:
        raw = copy.deepcopy(dict(cfg))
        raw.update(patch)
        with pytest.raises(ValueError, match=re.escape(fragment)):
            validate_config(Config(raw))
    validate_config(Config(copy.deepcopy(dict(cfg))))       # the shipped config is valid
