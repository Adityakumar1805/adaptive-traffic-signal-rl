"""ASGI entrypoint for hosted deployments (Render, Hugging Face Spaces, Fly, Railway).

`atsc.dashboard.server.build_app()` is a *factory*: it needs a live DashboardSession
before it can register any route, so there is no module-level `app` inside the package
and `uvicorn atsc.dashboard.server:app` cannot work. This module is the missing piece —
it does exactly what `run.py demo` does, minus the two things a server must not do:

  * it never calls `uvicorn.run()`  — the platform owns the host and the port
  * it never calls `webbrowser`     — there is no browser on the host

Everything else is the same objects the local demo uses, so the deployed site is the
local demo: the same DashboardSession, the same two ControllerRunners (trained RL vs
fixed-time), the same simulator, the same vehicle sprites.

    uvicorn asgi:app --host 0.0.0.0 --port $PORT \
        --ws-max-size 65536 --ws-ping-interval 20 --ws-ping-timeout 20

(render.yaml, Procfile and the Dockerfile all use exactly this command.) Run exactly ONE
worker: the race is a single in-memory simulation shared by every visitor, so extra workers
would each run their own race.

Optional environment variables:

    ATSC_CONFIG    path to an alternative config.yaml (default: the repo's own)
    ATSC_SCENARIO  low | medium | high | rush   (default: dashboard.default_scenario;
                   an unknown value is logged and ignored)
    OPENBLAS_NUM_THREADS, OMP_NUM_THREADS, MKL_NUM_THREADS
                   default to 1 in this process (atsc/threads.py explains why); a value
                   set by the platform is respected

No secrets, no API keys, no database: the simulation is in-memory and the process
makes no outbound network calls.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:                    # mirrors run.py
    sys.path.insert(0, str(SRC))

# Before anything imports NumPy: one BLAS thread. The default pool's idle workers spin and
# cost about half a CPU on an otherwise idle server (see atsc/threads.py).
from atsc.threads import limit_math_threads     # noqa: E402

limit_math_threads()

from atsc.dashboard.assets import build_id      # noqa: E402
from atsc.dashboard.server import build_app     # noqa: E402
from atsc.dashboard.session import DashboardSession  # noqa: E402
from atsc.logging_utils import get_logger       # noqa: E402

log = get_logger("atsc.asgi")


def create_app():
    """Build the FastAPI app for a hosted process. Called once at import time."""
    session = DashboardSession(os.environ.get("ATSC_CONFIG") or None)

    scenario = (os.environ.get("ATSC_SCENARIO") or "").strip()
    if scenario:
        try:
            session.set_scenario(scenario)
        except ValueError as exc:               # a typo must not take the site down
            log.error("Ignoring ATSC_SCENARIO=%r: %s", scenario, exc)

    session.play()                              # a visitor should never land on a frozen grid

    if not session.ckpt_exists:
        log.warning("No trained checkpoint found - the RL panel would run an UNTRAINED "
                    "policy. Expected models/pretrained/atsc_2x2.pt to be present.")

    log.info("ASGI app ready (scenario=%s, backend=%s, checkpoint=%s, build=%s) - one shared "
             "session serves every visitor", session.scenario, session.rl_backend_name,
             session.ckpt_name or "none", build_id())
    return build_app(session)


app = create_app()
