@echo off
REM ==========================================================================
REM  Adaptive Traffic Signal Control (RL) - one-click launcher for Windows.
REM  Creates a virtual env with a supported Python (3.10 - 3.12), installs the
REM  pinned dependencies, then opens the live dashboard. Pass a subcommand to
REM  override, e.g.:  run.bat train --quick      run.bat eval      run.bat doctor
REM ==========================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM --- locate a supported Python (the pinned torch 2.2.2 / numpy 1.26.4 have no 3.13 wheels) ---
set "PY="
for %%V in (3.12 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python 3.10, 3.11 or 3.12 is required.
  echo         The pinned PyTorch 2.2.2 and NumPy 1.26.4 publish no wheels for Python 3.13+.
  echo         Install Python 3.12 from https://www.python.org/downloads/ ^(tick "Add to PATH"^)
  echo         and run this file again.
  pause
  exit /b 1
)

REM --- create the virtual environment once (and replace one made by an unsupported Python) ---
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>nul
  if errorlevel 1 (
    echo [setup] .venv was created with an unsupported Python - recreating it.
    rmdir /s /q .venv
  )
)
if not exist ".venv\Scripts\python.exe" (
  echo [setup] Creating virtual environment .venv with %PY% ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment.
    pause
    exit /b 1
  )
)
set "VPY=.venv\Scripts\python.exe"

REM --- install dependencies when requirements.txt is new or changed ---
REM  NOTE: we intentionally do NOT run "pip install --upgrade pip". On Windows
REM  pip upgrading itself can be blocked mid-uninstall (WinError 32, a locked
REM  __pycache__) which leaves pip broken. The bundled pip installs everything
REM  fine, so we skip that fragile step entirely.
set "NEED_INSTALL=1"
if exist ".venv\.deps_installed" (
  fc /b requirements.txt ".venv\.deps_installed" >nul 2>nul && set "NEED_INSTALL=0"
)
if "%NEED_INSTALL%"=="1" (
  echo [setup] Installing dependencies ^(first run: ~2-4 min^)...
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
  copy /y requirements.txt ".venv\.deps_installed" >nul
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
