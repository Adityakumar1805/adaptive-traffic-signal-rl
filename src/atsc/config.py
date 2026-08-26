"""
Configuration loading and validation.

The whole system is driven by ``config.yaml``. Rather than a rigid schema of
dataclasses (which is brittle as the config grows), we load the YAML into an
``AttrDict`` that supports both attribute access (``cfg.rl.lr``) and item access
(``cfg["rl"]["lr"]``), then run a light validation pass that raises *friendly,
actionable* errors for the handful of things that must be correct.

Example
-------
>>> cfg = load_config("config.yaml")
>>> cfg.network.grid_rows
2
>>> cfg.scenario_params("rush")["arrival_scale"]
1.0
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import yaml


class AttrDict(dict):
    """A dict whose keys are also accessible as attributes, recursively.

    Missing attributes raise ``AttributeError`` with a helpful message so a
    typo in a config key is easy to diagnose instead of returning ``None``.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for key, value in list(self.items()):
            self[key] = self._wrap(value)

    @classmethod
    def _wrap(cls, value: Any) -> Any:
        if isinstance(value, AttrDict):
            return value
        if isinstance(value, dict):
            return cls(value)
        if isinstance(value, (list, tuple)):
            return [cls._wrap(v) for v in value]
        return value

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(
                f"config has no key '{name}'. Available keys here: "
                f"{sorted(self.keys())}"
            ) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = self._wrap(value)

    def get_path(self, dotted: str, default: Any = None) -> Any:
        """Fetch a nested value by a dotted path, e.g. ``cfg.get_path('rl.per.alpha')``."""
        node: Any = self
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node


class Config(AttrDict):
    """Top-level configuration object with a few convenience helpers."""

    def scenario_params(self, name: str) -> Dict[str, Any]:
        """Return the merged demand parameters for a named density scenario."""
        if name not in self.scenarios:
            raise KeyError(
                f"Unknown scenario '{name}'. Known scenarios: "
                f"{sorted(self.scenarios.keys())}"
            )
        params = dict(self.demand)
        params.update(dict(self.scenarios[name]))
        params["name"] = name
        return params

    @property
    def n_intersections(self) -> int:
        return int(self.network.grid_rows) * int(self.network.grid_cols)

    @property
    def n_phases(self) -> int:
        scheme = str(self.network.phase_scheme)
        return 2 if scheme == "ns_ew" else 4


# --------------------------------------------------------------------------- #
# Loading & validation
# --------------------------------------------------------------------------- #
def project_root() -> Path:
    """Return the repository root (two levels up from this file: src/atsc/..)."""
    return Path(__file__).resolve().parents[2]


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load and validate ``config.yaml``.

    Parameters
    ----------
    path:
        Path to the YAML file. Defaults to ``<project_root>/config.yaml``.
    """
    if path is None:
        path = project_root() / "config.yaml"
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Could not find config file at '{path}'. Run commands from the project "
            f"root, or pass --config <path>."
        )
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"Config file '{path}' did not parse to a mapping.")
    cfg = Config(raw)
    validate_config(cfg)
    return cfg


def _require(cfg: AttrDict, dotted: str, kind: type | tuple, errors: List[str]) -> None:
    value = cfg.get_path(dotted, default=_MISSING)
    if value is _MISSING:
        errors.append(f"  - missing required key: '{dotted}'")
    elif kind is not None and not isinstance(value, kind):
        errors.append(
            f"  - key '{dotted}' should be {kind}, got {type(value).__name__} ({value!r})"
        )


_MISSING = object()


def validate_config(cfg: Config) -> None:
    """Validate the most important invariants and raise one friendly error."""
    errors: List[str] = []

    _require(cfg, "network.grid_rows", int, errors)
    _require(cfg, "network.grid_cols", int, errors)
    _require(cfg, "network.phase_scheme", str, errors)
    _require(cfg, "signal.min_green_s", (int, float), errors)
    _require(cfg, "signal.max_green_s", (int, float), errors)
    _require(cfg, "signal.yellow_s", (int, float), errors)
    _require(cfg, "signal.all_red_s", (int, float), errors)
    _require(cfg, "rl.lr", (int, float), errors)
    _require(cfg, "rl.hidden_sizes", list, errors)
    _require(cfg, "reward.normalize", (int, float), errors)
    _require(cfg, "scenarios", dict, errors)

    # semantic checks
    if not errors:
        if cfg.network.grid_rows < 1 or cfg.network.grid_cols < 1:
            errors.append("  - network grid must be at least 1x1")
        if cfg.network.phase_scheme not in ("ns_ew", "quad"):
            errors.append("  - network.phase_scheme must be 'ns_ew' or 'quad'")
        if cfg.signal.min_green_s > cfg.signal.max_green_s:
            errors.append("  - signal.min_green_s must be <= signal.max_green_s")
        if cfg.reward.normalize <= 0:
            errors.append("  - reward.normalize must be > 0")
        if cfg.backend not in ("auto", "sumo", "mini"):
            errors.append("  - backend must be one of: auto | sumo | mini")

    if errors:
        raise ValueError(
            "config.yaml failed validation:\n" + "\n".join(errors) +
            "\n\nFix the above keys and try again."
        )


if __name__ == "__main__":  # pragma: no cover - manual sanity check
    c = load_config()
    print(f"Loaded config OK: {c.n_intersections} intersections, "
          f"{c.n_phases} phases, backend={c.backend}")
