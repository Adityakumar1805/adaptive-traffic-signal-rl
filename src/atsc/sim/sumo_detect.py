"""Locate a SUMO installation across Windows / macOS / Linux.

SUMO is the *primary* simulator but is optional: if it cannot be found we fall back
to the built-in :class:`~atsc.sim.mini_backend.MiniBackend`. This module centralises
all the "where is SUMO?" logic and produces an exact, OS-specific install message
when it is missing (the operator will not debug).
"""
from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class SumoInfo:
    found: bool
    sumo_home: Optional[str] = None
    binary: Optional[str] = None       # path to `sumo`
    gui_binary: Optional[str] = None   # path to `sumo-gui`
    has_libsumo: bool = False
    has_traci: bool = False
    tools_on_path: bool = False


_COMMON_WINDOWS = [
    r"C:\Program Files (x86)\Eclipse\Sumo",
    r"C:\Program Files\Eclipse\Sumo",
    r"C:\Sumo",
    r"C:\sumo",
]
_COMMON_POSIX = [
    "/usr/share/sumo",
    "/usr/local/share/sumo",
    "/usr/local/opt/sumo/share/sumo",   # homebrew (intel)
    "/opt/homebrew/share/sumo",         # homebrew (apple silicon)
    "/opt/sumo",
]


def _candidate_homes() -> List[str]:
    homes: List[str] = []
    env = os.environ.get("SUMO_HOME")
    if env:
        homes.append(env)
    homes.extend(_COMMON_WINDOWS if platform.system() == "Windows" else _COMMON_POSIX)
    # de-dup, keep order
    seen, out = set(), []
    for h in homes:
        if h and h not in seen and Path(h).exists():
            seen.add(h)
            out.append(h)
    return out


def _bin_names() -> tuple:
    if platform.system() == "Windows":
        return "sumo.exe", "sumo-gui.exe"
    return "sumo", "sumo-gui"


def detect_sumo() -> SumoInfo:
    """Best-effort SUMO discovery. Never raises."""
    sumo_name, gui_name = _bin_names()

    binary = shutil.which("sumo") or shutil.which(sumo_name)
    gui = shutil.which("sumo-gui") or shutil.which(gui_name)
    sumo_home = os.environ.get("SUMO_HOME")

    if binary is None or sumo_home is None:
        for home in _candidate_homes():
            sumo_home = sumo_home or home
            cand = Path(home) / "bin" / sumo_name
            cand_gui = Path(home) / "bin" / gui_name
            if binary is None and cand.exists():
                binary = str(cand)
            if gui is None and cand_gui.exists():
                gui = str(cand_gui)

    # ensure <SUMO_HOME>/tools is importable for traci/sumolib
    tools_on_path = False
    if sumo_home:
        tools = Path(sumo_home) / "tools"
        if tools.exists():
            import sys
            if str(tools) not in sys.path:
                sys.path.append(str(tools))
            tools_on_path = True

    has_libsumo = _importable("libsumo")
    has_traci = _importable("traci")

    found = binary is not None or has_libsumo
    return SumoInfo(
        found=found,
        sumo_home=sumo_home,
        binary=binary,
        gui_binary=gui,
        has_libsumo=has_libsumo,
        has_traci=has_traci,
        tools_on_path=tools_on_path,
    )


def _importable(module: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def install_instructions() -> str:
    """Return exact, OS-specific SUMO install guidance."""
    sysname = platform.system()
    if sysname == "Windows":
        return (
            "SUMO was not found. To use the SUMO backend (optional):\n"
            "  1. Download the installer from https://www.eclipse.dev/sumo/ (SUMO 1.18+).\n"
            "  2. Run it; the default path is C:\\Program Files (x86)\\Eclipse\\Sumo.\n"
            "  3. Set SUMO_HOME:  setx SUMO_HOME \"C:\\Program Files (x86)\\Eclipse\\Sumo\"\n"
            "  4. Add %SUMO_HOME%\\bin to PATH, then reopen your terminal.\n"
            "  5. pip install traci sumolib\n"
            "NOTE: You do NOT need SUMO to run this project — it will use the built-in\n"
            "simulator automatically. SUMO just adds the microscopic view / SUMO-GUI."
        )
    if sysname == "Darwin":
        return (
            "SUMO was not found. To use the SUMO backend (optional):\n"
            "  brew install --cask sumo        # or download from https://www.eclipse.dev/sumo/\n"
            "  export SUMO_HOME=\"$(brew --prefix sumo)/share/sumo\"\n"
            "  pip install traci sumolib\n"
            "NOTE: SUMO is optional; the built-in simulator runs everything without it."
        )
    return (
        "SUMO was not found. To use the SUMO backend (optional):\n"
        "  sudo apt-get install sumo sumo-tools sumo-doc     # Debian/Ubuntu\n"
        "  export SUMO_HOME=/usr/share/sumo\n"
        "  pip install traci sumolib\n"
        "NOTE: SUMO is optional; the built-in simulator runs everything without it."
    )
