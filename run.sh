#!/usr/bin/env bash
# ==========================================================================
#  Adaptive Traffic Signal Control (RL) - one-command launcher (macOS/Linux).
#  Creates a virtual env, installs pinned dependencies, then opens the live
#  dashboard. Pass a subcommand to override, e.g.:  ./run.sh train --quick
# ==========================================================================
set -e
cd "$(dirname "$0")"

# --- locate Python ---
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
if ! "$PY" --version >/dev/null 2>&1; then
  echo "[ERROR] Python 3.10+ not found. Install it from https://www.python.org/downloads/"
  exit 1
fi

# --- create the virtual environment once ---
if [ ! -x ".venv/bin/python" ]; then
  echo "[setup] Creating virtual environment .venv ..."
  "$PY" -m venv .venv
fi
VPY=".venv/bin/python"

# --- install dependencies once ---
if [ ! -f ".venv/.deps_installed" ]; then
  echo "[setup] Installing dependencies (runs once, ~2-4 min)..."
  # On Linux the default torch wheel bundles CUDA; use the smaller CPU wheel.
  OS="$(uname -s)"
  if [ "$OS" = "Linux" ]; then
    "$VPY" -m pip install torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu || true
  fi
  "$VPY" -m pip install -r requirements.txt
  touch ".venv/.deps_installed"
fi

# --- auto-detect SUMO_HOME (optional) ---
if [ -z "${SUMO_HOME:-}" ]; then
  for d in /usr/share/sumo /usr/local/share/sumo /opt/homebrew/share/sumo /usr/local/opt/sumo/share/sumo; do
    [ -d "$d" ] && export SUMO_HOME="$d" && break
  done
fi

# --- run (default: demo) ---
if [ "$#" -eq 0 ]; then
  exec "$VPY" run.py demo
else
  exec "$VPY" run.py "$@"
fi
