"""The dashboard process runs NumPy's BLAS on one thread.

OpenBLAS's idle worker threads spin-wait after every call; with the default pool the hosted
server used about half a CPU while idle. The thread count can only be chosen before NumPy is
loaded, so these tests check *when* the setting happens, in fresh interpreters.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from atsc.threads import MATH_THREAD_VARS, limit_math_threads

ROOT = Path(__file__).resolve().parents[1]

# Runs in a fresh interpreter: records OPENBLAS_NUM_THREADS at the moment numpy is first
# imported, then runs BODY.
_PROBE = r'''
import builtins, logging, os, sys
ROOT = sys.argv[1]
logging.disable(logging.CRITICAL)
seen = {}
_orig = builtins.__import__
def _hook(name, globals=None, locals=None, fromlist=(), level=0):
    if level == 0 and name.split(".")[0] == "numpy" and "numpy" not in seen:
        seen["numpy"] = os.environ.get("OPENBLAS_NUM_THREADS")
    return _orig(name, globals, locals, fromlist, level)
builtins.__import__ = _hook
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]
BODY
print("AT_NUMPY_IMPORT=%s" % seen.get("numpy", "never"))
'''


def _probe(body: str, **env_overrides) -> str:
    env = {k: v for k, v in os.environ.items() if k not in MATH_THREAD_VARS}
    env.update(env_overrides)
    code = _PROBE.replace("BODY", body)
    out = subprocess.run([sys.executable, "-c", code, str(ROOT)], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout.strip().splitlines()[-1]


def test_the_threads_module_loads_nothing_heavy():
    out = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, 'src'); import atsc.threads; "
                               "print(sorted(m for m in ('numpy', 'torch') if m in sys.modules))"],
        cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_defaults_to_one_thread_and_respects_an_explicit_value(monkeypatch):
    for var in MATH_THREAD_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    limit_math_threads()
    assert os.environ["OPENBLAS_NUM_THREADS"] == "1"
    assert os.environ["MKL_NUM_THREADS"] == "1"
    assert os.environ["OMP_NUM_THREADS"] == "3"


def test_asgi_pins_blas_threads_before_numpy_loads():
    pytest.importorskip("fastapi")
    assert _probe("import asgi") == "AT_NUMPY_IMPORT=1"


def test_asgi_keeps_a_value_the_platform_set():
    pytest.importorskip("fastapi")
    assert _probe("import asgi", OPENBLAS_NUM_THREADS="2") == "AT_NUMPY_IMPORT=2"


def test_run_py_demo_pins_blas_threads_before_numpy_loads():
    # Stand-ins for the dashboard modules so cmd_demo returns instead of serving forever;
    # everything before that (config, checkpoint lookup) is the real code.
    body = r'''
import types
pkg = types.ModuleType("atsc.dashboard"); pkg.__path__ = []
srv = types.ModuleType("atsc.dashboard.server"); srv.run_dashboard = lambda **kw: None
sys.modules["atsc.dashboard"] = pkg; sys.modules["atsc.dashboard.server"] = srv
import importlib.util
spec = importlib.util.spec_from_file_location("run_entry", os.path.join(ROOT, "run.py"))
run_entry = importlib.util.module_from_spec(spec); spec.loader.exec_module(run_entry)
assert run_entry.cmd_demo(types.SimpleNamespace(config=None)) == 0
assert "numpy" in sys.modules
'''
    assert _probe(body) == "AT_NUMPY_IMPORT=1"
