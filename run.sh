#!/usr/bin/env bash
# ==========================================================================
#  Adaptive Traffic Signal Control (RL) - one-command launcher (macOS/Linux).
#  Creates a virtual env with a supported Python (3.10 - 3.12), installs the
#  pinned dependencies, then opens the live dashboard. Pass a subcommand to
#  override, e.g.:  ./run.sh train --quick      ./run.sh eval      ./run.sh doctor
# ==========================================================================
set -euo pipefail
cd "$(dirname "$0")"

supported() {  # exit 0 if "$1" is a Python 3.10 - 3.12 interpreter
  "$1" -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)' >/dev/null 2>&1
}

# --- locate a supported Python (the pinned torch 2.2.2 / numpy 1.26.4 have no 3.13 wheels) ---
PY=""
for cand in "${PYTHON:-}" python3.12 python3.11 python3.10 python3 python; do
  [ -n "$cand" ] || continue
  if command -v "$cand" >/dev/null 2>&1 && supported "$cand"; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  found="$(python3 --version 2>/dev/null || python --version 2>/dev/null || echo 'none')"
  echo "[ERROR] Python 3.10, 3.11 or 3.12 is required (found: $found)."
  echo "        The pinned PyTorch 2.2.2 and NumPy 1.26.4 publish no wheels for Python 3.13+."
  echo "        Install Python 3.12 (https://www.python.org/downloads/ or 'pyenv install 3.12'),"
  echo "        or point this script at one:  PYTHON=/path/to/python3.12 ./run.sh"
  exit 1
fi

# --- create the virtual environment once (and replace one made by an unsupported Python) ---
if [ -x ".venv/bin/python" ] && ! supported ".venv/bin/python"; then
  echo "[setup] .venv was created with an unsupported Python - recreating it with $PY"
  rm -rf .venv
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "[setup] Creating virtual environment .venv with $("$PY" --version) ..."
  "$PY" -m venv .venv
fi
VPY=".venv/bin/python"

# --- install dependencies when requirements.txt is new or changed ---
if ! cmp -s requirements.txt .venv/.deps_installed 2>/dev/null; then
  echo "[setup] Installing dependencies (first run: ~2-4 min)..."
  # On Linux the default torch wheel bundles CUDA (~2.5 GB); the CPU wheel is enough.
  if [ "$(uname -s)" = "Linux" ]; then
    "$VPY" -m pip install torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu \
      || echo "[setup] CPU wheel index unreachable - pip will fetch the default torch wheel."
  fi
  "$VPY" -m pip install -r requirements.txt
  cp requirements.txt .venv/.deps_installed
fi

# --- auto-detect SUMO_HOME (optional; the built-in simulator needs nothing) ---
if [ -z "${SUMO_HOME:-}" ]; then
  for d in /usr/share/sumo /usr/local/share/sumo /opt/homebrew/share/sumo /usr/local/opt/sumo/share/sumo; do
    if [ -d "$d" ]; then export SUMO_HOME="$d"; break; fi
  done
fi

# --- run (default: demo) ---
if [ "$#" -eq 0 ]; then
  exec "$VPY" run.py demo
else
  exec "$VPY" run.py "$@"
fi
