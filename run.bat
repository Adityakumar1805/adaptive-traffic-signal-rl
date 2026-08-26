@echo off
REM ==========================================================================
REM  Adaptive Traffic Signal Control (RL) - one-click launcher for Windows.
REM  Creates a virtual env, installs pinned dependencies, then opens the live
REM  dashboard. Pass a subcommand to override, e.g.:  run.bat train --quick
REM ==========================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM --- locate Python ---
set PY=python
where python >nul 2>nul || set PY=py
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python 3.10+ was not found on PATH.
  echo         Install it from https://www.python.org/downloads/ ^(tick "Add to PATH"^).
  pause
  exit /b 1
)

REM --- create the virtual environment once ---
if not exist ".venv\Scripts\python.exe" (
  echo [setup] Creating virtual environment .venv ...
  %PY% -m venv .venv
)
set VPY=.venv\Scripts\python.exe

REM --- install dependencies once (marker file) ---
REM  NOTE: we intentionally do NOT run "pip install --upgrade pip". On Windows
REM  pip upgrading itself can be blocked mid-uninstall (WinError 32, a locked
REM  __pycache__) which leaves pip broken. The bundled pip installs everything
REM  fine, so we skip that fragile step entirely.
if not exist ".venv\.deps_installed" (
  echo [setup] Installing dependencies ^(this runs only once, ~2-4 min^)...
  "%VPY%" -m pip install --no-input --no-cache-dir -r requirements.txt
  if errorlevel 1 (
    echo.
    echo [ERROR] Dependency installation failed. See messages above.
    echo         Common fix on Windows: your antivirus/OneDrive locked a file.
    echo         1^) close this window, 2^) delete the .venv folder,
    echo         3^) add this project folder to your antivirus exclusions,
    echo         4^) run run.bat again.
    pause
    exit /b 1
  )
  echo done> ".venv\.deps_installed"
)

REM --- auto-detect SUMO_HOME (optional; the app runs fine without SUMO) ---
if "%SUMO_HOME%"=="" (
  if exist "C:\Program Files (x86)\Eclipse\Sumo\bin\sumo.exe" set "SUMO_HOME=C:\Program Files (x86)\Eclipse\Sumo"
  if exist "C:\Program Files\Eclipse\Sumo\bin\sumo.exe" set "SUMO_HOME=C:\Program Files\Eclipse\Sumo"
)

REM --- run (default: demo) ---
if "%~1"=="" (
  "%VPY%" run.py demo
) else (
  "%VPY%" run.py %*
)
endlocal
