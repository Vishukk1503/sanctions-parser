@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title Sanctions Data Manager

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo The application has not been set up yet.
    echo Starting first-time setup...
    echo.
    call "%~dp0SETUP.bat"
    if errorlevel 1 (
        echo Setup did not complete. The application cannot start.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Could not activate the Python environment.
    echo Run SETUP.bat and try again.
    pause
    exit /b 1
)

python "main.py"
set "APP_EXIT=%ERRORLEVEL%"

if not "%APP_EXIT%"=="0" (
    echo.
    echo The application closed with an error.
    echo See logs\run.log for technical details.
)

echo.
echo Application closed. Press any key to close this window.
pause >nul
exit /b %APP_EXIT%
