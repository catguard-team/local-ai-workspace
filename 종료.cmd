@echo off
setlocal
chcp 65001 >nul
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Python environment not found. Run bootstrap.ps1 first.
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0server_control.py" stop
set "stop_result=%errorlevel%"
if not "%stop_result%"=="0" pause
exit /b %stop_result%
