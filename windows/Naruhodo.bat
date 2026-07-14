@echo off
rem Naruhodo launcher for Windows.
rem First run: creates a venv and installs dependencies (needs Python 3.10+).
rem After setup it starts the app without a console window.
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "LOG=%~dp0naruhodo.log"
echo [%date% %time%] === Naruhodo launch === >> "%LOG%"

rem ---- find Python 3 ----
set "PYTHON="
where py >nul 2>nul && set "PYTHON=py -3"
if not defined PYTHON (
  where python >nul 2>nul && set "PYTHON=python"
)
if not defined PYTHON (
  echo Python 3 not found. Please install it from https://www.python.org/downloads/
  echo (check "Add python.exe to PATH" in the installer)
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

rem ---- create venv on first run ----
if not exist ".venv\Scripts\python.exe" (
  echo Setting up Naruhodo (first run only, a few minutes)...
  %PYTHON% -m venv .venv >> "%LOG%" 2>&1
  if errorlevel 1 (
    echo Failed to create Python venv. See naruhodo.log for details.
    pause
    exit /b 1
  )
)

rem ---- install dependencies ----
echo Checking dependencies...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip >> "%LOG%" 2>&1
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 (
  echo Failed to install dependencies. See naruhodo.log for details.
  pause
  exit /b 1
)
rem Codex SDK only (the codex binary itself comes from a system install, e.g. npm)
".venv\Scripts\python.exe" -m pip install -q --no-deps openai-codex >> "%LOG%" 2>&1

rem ---- start the app (no console window) ----
echo Starting Naruhodo...
start "" ".venv\Scripts\pythonw.exe" desktop.py
exit /b 0
