"""Real-time web dashboard (FastAPI + WebSocket, stdlib polling fallback, canvas front end)."""
from atsc.dashboard.server import build_app, run_dashboard
from atsc.dashboard.session import CommandError, DashboardSession

__all__ = ["run_dashboard", "build_app", "DashboardSession", "CommandError"]
