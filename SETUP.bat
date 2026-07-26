@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
title Sanctions Data Manager - Setup

echo.
echo ============================================================
echo   SANCTIONS DATA MANAGER - FIRST-TIME SETUP
echo ============================================================
echo.

set "PYTHON_CMD="

where py >nul 2>&1
if not errorlevel 1 (
    py -3.14 -c "import struct; raise SystemExit(0 if struct.calcsize('P') == 8 else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=py -3.14"
        goto python_found
    )
    py -3.13 -c "import struct; raise SystemExit(0 if struct.calcsize('P') == 8 else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=py -3.13"
        goto python_found
    )
    py -3.12 -c "import struct; raise SystemExit(0 if struct.calcsize('P') == 8 else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=py -3.12"
        goto python_found
    )
    py -3.11 -c "import struct; raise SystemExit(0 if struct.calcsize('P') == 8 else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=py -3.11"
        goto python_found
    )
)

if not defined PYTHON_CMD (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import struct, sys; raise SystemExit(0 if (3, 11) <= sys.version_info < (3, 15) and struct.calcsize('P') == 8 else 1)" >nul 2>&1
        if not errorlevel 1 set "PYTHON_CMD=python"
    )
)

if not defined PYTHON_CMD (
    echo [ERROR] A supported Python version was not found.
    echo.
    echo Install 64-bit Python 3.11, 3.12, 3.13, or 3.14 from:
    echo https://www.python.org/downloads/windows/
    echo.
    echo During installation, select "Add python.exe to PATH".
    start "" "https://www.python.org/downloads/windows/"
    pause
    exit /b 1
)

:python_found
echo [1/4] Compatible Python found.
%PYTHON_CMD% --version

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import struct, sys; raise SystemExit(0 if (3, 11) <= sys.version_info < (3, 15) and struct.calcsize('P') == 8 else 1)" >nul 2>&1
    if not errorlevel 1 goto install_dependencies
)

if exist ".venv" (
    if exist ".venv-backup" (
        echo.
        echo [ERROR] The existing .venv is incompatible and .venv-backup already exists.
        echo Rename or remove one of those folders, then run SETUP.bat again.
        pause
        exit /b 1
    )
    echo Backing up the incompatible environment as .venv-backup...
    move ".venv" ".venv-backup" >nul
    if errorlevel 1 (
        echo [ERROR] Could not back up the existing .venv folder.
        pause
        exit /b 1
    )
)

echo [2/4] Creating the private Python environment...
%PYTHON_CMD% -m venv ".venv"
if errorlevel 1 goto setup_failed

:install_dependencies
echo [2/4] Python environment is ready.
echo [3/4] Installing required packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto setup_failed

".venv\Scripts\python.exe" -m pip install -r "requirements.txt"
if errorlevel 1 goto setup_failed

echo [4/4] Verifying the installation...
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto setup_failed

".venv\Scripts\python.exe" -c "from pathlib import Path; from sanctions_parser.config import load_sources; sources = load_sources(Path('config/sources.yaml')); assert sources; print('Verified', len(sources), 'configured sanctions sources.')"
if errorlevel 1 goto setup_failed

echo.
echo ============================================================
echo   SETUP COMPLETE
echo ============================================================
echo.
echo Double-click RUN_APP.bat whenever you want to start the app.
echo.
pause
exit /b 0

:setup_failed
echo.
echo ============================================================
echo   SETUP FAILED
echo ============================================================
echo.
echo Check your internet connection and the messages above.
echo Then run SETUP.bat again.
echo.
pause
exit /b 1
