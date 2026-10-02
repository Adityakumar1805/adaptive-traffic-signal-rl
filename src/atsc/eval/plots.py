"""Comparison plots for the benchmark results (saved as PNGs under outputs/).

Uses matplotlib's non-interactive Agg backend so it works headless on any machine.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# consistent colours per controller
_COLORS = {
    "fixed_time": "#9e9e9e",
    "max_pressure": "#ffb300",
    "rl": "#2e7d32",
}
_LABELS = {
    "fixed_time": "Fixed-Time",
    "max_pressure": "Max-Pressure",
    "rl": "RL (ours)",
}


def _grouped_bar(ax, df, value, ylabel, title):
    scenarios = list(df["scenario"].unique())
    controllers = list(df["controller"].unique())
    x = np.arange(len(scenarios))
    width = 0.8 / max(1, len(controllers))
    for i, ctrl in enumerate(controllers):
        vals = [df[(df.scenario == s) & (df.controller == ctrl)][value].mean()
                for s in scenarios]
        ax.bar(x + i * width, vals, width, label=_LABELS.get(ctrl, ctrl),
               color=_COLORS.get(ctrl, None), edgecolor="black", linewidth=0.5)
    ax.set_xticks(x + width * (len(controllers) - 1) / 2)
    ax.set_xticklabels([s.capitalize() for s in scenarios])
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)


def make_all_plots(df: pd.DataFrame, out_dir: str) -> List[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []

    # 1) three-panel KPI comparison
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    _grouped_bar(axes[0], df, "avg_waiting_time", "Avg waiting time (s)",
                 "Average Waiting Time")
    _grouped_bar(axes[1], df, "avg_queue", "Avg queue (veh/intersection)",
                 "Average Queue Length")
    _grouped_bar(axes[2], df, "throughput", "Throughput (veh completed)",
                 "Throughput")
    axes[0].legend()
    fig.suptitle("RL vs Fixed-Time vs Max-Pressure across Traffic Densities",
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    p = out / "comparison_kpis.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    paths.append(p)

    # 2) RL % improvement over fixed-time
    from atsc.eval.benchmark import improvement_table
    impr = improvement_table(df)
    if not impr.empty:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        scenarios = list(impr.index)
        x = np.arange(len(scenarios))
        ax.bar(x - 0.2, impr["wait_reduction_%"], 0.4, label="Waiting-time reduction",
               color="#2e7d32", edgecolor="black", linewidth=0.5)
        ax.bar(x + 0.2, impr["queue_reduction_%"], 0.4, label="Queue reduction",
               color="#1565c0", edgecolor="black", linewidth=0.5)
        ax.axhspan(40, 50, color="green", alpha=0.08, label="40-50% target")
        ax.set_xticks(x)
        ax.set_xticklabels([s.capitalize() for s in scenarios])
        ax.set_ylabel("Improvement vs Fixed-Time (%)")
        ax.set_title("RL Improvement over Fixed-Time Control", fontweight="bold")
        ax.legend()
        ax.grid(axis="y", alpha=0.3)
        fig.tight_layout()
        p = out / "rl_improvement.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        paths.append(p)

    # 3) emergency clearance time (if measured)
    if "emergency_clearance_time" in df.columns and df["emergency_clearance_time"].notna().any():
        fig, ax = plt.subplots(figsize=(8, 4.5))
        _grouped_bar(ax, df.dropna(subset=["emergency_clearance_time"]),
                     "emergency_clearance_time", "Clearance time (s)",
                     "Emergency-Vehicle Clearance Time")
        ax.legend()
        fig.tight_layout()
        p = out / "emergency_clearance.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        paths.append(p)

    return paths


def plot_training_curve(csv_path: str, out_dir: str,
                        name: str = "training_curve.png") -> Optional[Path]:
    """Plot the training avg-waiting-time curve from a train CSV log into ``out_dir/name``."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return None
    df = pd.read_csv(csv_path)
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(df["episode"], df["avg_wait"], marker="o", ms=3, color="#2e7d32")
    ax.set_xlabel("Episode")
    ax.set_ylabel("Avg waiting time (s)")
    ax.set_title("Training progress (lower is better)", fontweight="bold")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / name
    fig.savefig(p, dpi=130)
    plt.close(fig)
    return p
