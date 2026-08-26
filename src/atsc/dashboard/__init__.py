"""Real-time web dashboard (FastAPI + WebSocket + single-page front end)."""
from atsc.dashboard.server import build_app, run_dashboard
from atsc.dashboard.session import DashboardSession

__all__ = ["run_dashboard", "build_app", "DashboardSession"]
