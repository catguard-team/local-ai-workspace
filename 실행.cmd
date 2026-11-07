@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "start_result=%errorlevel%"
if not "%start_result%"=="0" goto failed
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0open_browser.py"
set "start_result=%errorlevel%"
if not "%start_result%"=="0" goto failed
exit /b 0
:failed
echo Server startup or browser launch failed. See the error above.
pause
exit /b %start_result%
