"""Evaluation: benchmarking harness, metrics, and comparison plots.

The public names are imported lazily, so ``import atsc.eval.metrics`` (one episode, no
tables) does not drag in pandas and matplotlib.
"""
from importlib import import_module

_EXPORTS = {
    "run_benchmark": "atsc.eval.benchmark",
    "summarize": "atsc.eval.benchmark",
    "improvement_table": "atsc.eval.benchmark",
    "run_controlled_episode": "atsc.eval.metrics",
    "make_all_plots": "atsc.eval.plots",
    "plot_training_curve": "atsc.eval.plots",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name in _EXPORTS:
        return getattr(import_module(_EXPORTS[name]), name)
    raise AttributeError(f"module 'atsc.eval' has no attribute {name!r}")
