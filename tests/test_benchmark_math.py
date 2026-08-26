"""Tests for the evaluation math + backend/topology fundamentals."""
import numpy as np
import pandas as pd

from atsc.eval.benchmark import improvement_table, summarize
from atsc.sim.backend import build_topology
from atsc.sim.mini_backend import MiniBackend


def _fake_df():
    rows = []
    # fixed slower than RL -> positive improvement
    for seed in range(2):
        rows.append(dict(scenario="high", controller="fixed_time", seed=seed,
                         avg_waiting_time=100.0, avg_queue=10.0, throughput=1000,
                         avg_speed=3.0, fuel_ml=1.0, co2_g=2.0, emergency_clearance_time=50.0))
        rows.append(dict(scenario="high", controller="rl", seed=seed,
                         avg_waiting_time=55.0, avg_queue=6.0, throughput=1100,
                         avg_speed=4.0, fuel_ml=0.8, co2_g=1.6, emergency_clearance_time=20.0))
    return pd.DataFrame(rows)


def test_improvement_table_math():
    df = _fake_df()
    impr = improvement_table(df)
    assert abs(impr.loc["high", "wait_reduction_%"] - 45.0) < 1e-6
    assert abs(impr.loc["high", "queue_reduction_%"] - 40.0) < 1e-6
    assert abs(impr.loc["high", "throughput_gain_%"] - 10.0) < 1e-6


def test_summarize_groups():
    df = _fake_df()
    s = summarize(df)
    assert ("high", "fixed_time") in s.index
    assert ("high", "rl") in s.index
    assert abs(s.loc[("high", "rl"), "avg_waiting_time"] - 55.0) < 1e-6


def test_topology_grid_and_neighbors(cfg):
    topo = build_topology(cfg)
    assert topo.n_intersections == cfg.network.grid_rows * cfg.network.grid_cols
    # neighbour relation is symmetric
    for it in topo:
        for approach, nid in it.neighbors.items():
            if nid is not None:
                back = topo.get(nid).neighbors
                assert it.id in back.values()


def test_mini_backend_conservation(cfg):
    """Vehicles that arrive either complete, are queued, or are in transit — nothing vanishes."""
    topo = build_topology(cfg)
    be = MiniBackend(cfg, topo)
    be.reset(cfg.scenario_params("medium"), seed=0)
    phases = topo.get(topo.order[0]).phases
    total_arrivals = 0
    total_completed = 0
    for t in range(600):
        for iid in topo.order:
            be.set_green(iid, phases[(t // 30) % 2].green_approaches)
        oc = be.step()
        total_arrivals += oc.arrivals
        total_completed += oc.completed
    queued = sum(be.queue(iid, a) for iid in topo.order for a in ["N", "E", "S", "W"])
    in_transit = len(be._transit)
    assert total_completed + queued + in_transit == total_arrivals
