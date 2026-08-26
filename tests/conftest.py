"""Shared pytest fixtures. Adds src/ to the path so `import atsc` works uninstalled."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(scope="session")
def cfg():
    from atsc.config import load_config
    return load_config(ROOT / "config.yaml")
