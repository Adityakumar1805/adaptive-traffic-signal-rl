"""Checkpoint / config compatibility, and the optional QMIX learner actually running."""
import copy
from pathlib import Path

import pytest

from atsc.config import Config
from atsc.control.rl_controller import RLController, default_checkpoint_path
from atsc.sim.backend import build_topology


def _cfg(cfg, **changes):
    raw = copy.deepcopy(dict(cfg))
    for dotted, value in changes.items():
        node = raw
        *parents, leaf = dotted.split(".")
        for key in parents:
            node = node.setdefault(key, {})
        node[leaf] = value
    return Config(raw)


def test_shipped_checkpoint_loads_with_the_shipped_config(cfg):
    ctrl = RLController(cfg, build_topology(cfg))
    assert ctrl.metadata.get("algo") == "double_dueling_dqn"


@pytest.mark.parametrize("dotted, value, fragment", [
    ("rl.neighbor_obs", False, "rl.neighbor_obs"),
    ("rl.share_parameters", False, "rl.share_parameters"),
    ("rl.hidden_sizes", [64, 64], "rl.hidden_sizes"),
])
def test_mismatched_settings_give_a_readable_error(cfg, dotted, value, fragment):
    changed = _cfg(cfg, **{dotted: value})
    with pytest.raises(ValueError) as err:
        RLController(changed, build_topology(changed), checkpoint=str(default_checkpoint_path(cfg)))
    assert fragment in str(err.value) and "python run.py train --quick" in str(err.value)


def test_a_file_that_is_not_a_checkpoint_is_rejected(cfg, tmp_path):
    junk = tmp_path / "junk.pt"
    junk.write_bytes(b"not a pickle at all")
    with pytest.raises(ValueError, match="does not match config.yaml"):
        RLController(cfg, build_topology(cfg), checkpoint=str(junk))


def test_qmix_trains_saves_and_reloads(cfg, tmp_path):
    pytest.importorskip("torch")
    from atsc.train.trainer import Trainer
    q = _cfg(cfg, **{"qmix.enabled": True, "train.quick_episodes": 1, "train.episode_seconds": 60,
                     "train.curriculum": ["low"], "rl.min_buffer": 4, "rl.batch_size": 4,
                     "train.checkpoint_dir": str(tmp_path / "models"),
                     "train.log_dir": str(tmp_path / "logs")})
    path = Trainer(q, quick=True).train()
    assert Path(path).exists() and (tmp_path / "logs" / "train_quick.csv").exists()
    ctrl = RLController(q, build_topology(q), checkpoint=str(path))
    assert ctrl.metadata.get("algo") == "qmix"


def test_qmix_refuses_the_shipped_dqn_checkpoint(cfg):
    pytest.importorskip("torch")
    q = _cfg(cfg, **{"qmix.enabled": True})
    with pytest.raises(ValueError, match="not a QMIX model"):
        RLController(q, build_topology(q), checkpoint=str(default_checkpoint_path(cfg)))
