"""Benchmarking harness: RL vs fixed-time vs max-pressure on identical seeds.

For every (controller, scenario, seed) it runs one episode, aggregates the KPIs into a tidy
table, writes outputs/benchmark_results.csv and a human-readable summary, and computes the
headline % improvement of the RL controller over fixed-time. Plots are produced by
atsc.eval.plots.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from atsc.config import project_root
from atsc.eval.metrics import run_controlled_episode
from atsc.logging_utils import get_logger

log = get_logger("atsc.eval")

METRIC_KEYS = [
    "avg_waiting_time", "avg_queue", "throughput", "avg_speed",
    "fuel_ml", "co2_g", "emergency_clearance_time",
]


def run_benchmark(cfg, controllers: Optional[List[str]] = None,
                  scenarios: Optional[List[str]] = None,
                  seeds: Optional[List[int]] = None,
                  checkpoint: Optional[str] = None,
                  backend_name: Optional[str] = None,
                  inject_emergency: Optional[bool] = None,
                  out_dir: Optional[str] = None) -> pd.DataFrame:
    controllers = controllers or list(cfg.eval.controllers)
    scenarios = scenarios or list(cfg.eval.scenarios)
    seeds = seeds or list(cfg.eval.seeds)
    inject_emergency = cfg.eval.inject_emergency if inject_emergency is None else inject_emergency
    out = Path(out_dir) if out_dir else (project_root() / cfg.eval.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    rows: List[Dict] = []
    total = len(controllers) * len(scenarios) * len(seeds)
    done = 0
    log.info("Benchmarking %d runs: controllers=%s scenarios=%s seeds=%s",
             total, controllers, scenarios, seeds)

    for controller in controllers:
        skip_controller = False
        for scenario in scenarios:
            if skip_controller:
                break
            for seed in seeds:
                try:
                    m = run_controlled_episode(
                        cfg, controller, scenario, seed,
                        backend_name=backend_name, checkpoint=checkpoint,
                        inject_emergency=inject_emergency,
                    )
                    rows.append(m)
                except FileNotFoundError as exc:
                    log.error("Skipping '%s' controller: %s",
                              controller, str(exc).splitlines()[0])
                    skip_controller = True
                    break
                done += 1
                if done % max(1, total // 10) == 0 or done == total:
                    log.info("  progress %d/%d", done, total)

    df = pd.DataFrame(rows)
    if df.empty:
        log.warning("No benchmark rows produced.")
        return df

    csv_path = out / "benchmark_results.csv"
    df.to_csv(csv_path, index=False)
    log.info("Wrote raw results -> %s", csv_path)

    summary = summarize(df)
    summary.to_csv(out / "benchmark_summary.csv")
    _print_summary(summary, df)
    _write_markdown_table(summary, df, out / "benchmark_summary.md", cfg)
    return df


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    keys = [k for k in METRIC_KEYS if k in df.columns]
    return df.groupby(["scenario", "controller"])[keys].mean()


def improvement_table(df: pd.DataFrame, baseline: str = "fixed_time",
                      target: str = "rl") -> pd.DataFrame:
    means = df.groupby(["scenario", "controller"]).mean(numeric_only=True)
    out = {}
    for scen in df["scenario"].unique():
        try:
            base = means.loc[(scen, baseline)]
            tgt = means.loc[(scen, target)]
        except KeyError:
            continue
        out[scen] = {
            "wait_reduction_%": round(100.0 * (base["avg_waiting_time"] - tgt["avg_waiting_time"]) / base["avg_waiting_time"], 1),
            "queue_reduction_%": round(100.0 * (base["avg_queue"] - tgt["avg_queue"]) / base["avg_queue"], 1),
            "throughput_gain_%": round(100.0 * (tgt["throughput"] - base["throughput"]) / max(1e-9, base["throughput"]), 1),
        }
    return pd.DataFrame(out).T


def _print_summary(summary: pd.DataFrame, df: pd.DataFrame) -> None:
    log.info("\n===== MEAN METRICS (per scenario x controller) =====\n%s",
             summary.round(2).to_string())
    impr = improvement_table(df)
    if not impr.empty:
        log.info("\n===== RL IMPROVEMENT vs FIXED-TIME =====\n%s", impr.to_string())
        avg = impr["wait_reduction_%"].mean()
        verdict = "PASS" if avg > 0 else "REVIEW"
        log.info("HEADLINE: RL reduces average waiting time by %.1f%% vs fixed-time "
                 "(mean across scenarios) [%s]", avg, verdict)


def _write_markdown_table(summary: pd.DataFrame, df: pd.DataFrame, path: Path, cfg) -> None:
    impr = improvement_table(df)
    seeds = sorted(int(s) for s in df["seed"].unique())
    lines = ["# Benchmark Summary", ""]
    lines.append(f"Grid: {cfg.network.grid_rows}x{cfg.network.grid_cols} | "
                 f"seeds: {seeds} | episode: {cfg.eval.episode_seconds}s\n")
    lines.append("## Average waiting time (s) by scenario and controller\n")
    piv = df.pivot_table(index="scenario", columns="controller",
                         values="avg_waiting_time", aggfunc="mean").round(1)
    lines.append(piv.to_markdown())
    lines.append("\n## Average queue (veh/intersection)\n")
    pivq = df.pivot_table(index="scenario", columns="controller",
                          values="avg_queue", aggfunc="mean").round(1)
    lines.append(pivq.to_markdown())
    if not impr.empty:
        lines.append("\n## RL improvement vs fixed-time (%)\n")
        lines.append(impr.to_markdown())
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Wrote markdown summary -> %s", path)
