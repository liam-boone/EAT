@echo off
REM Sets up the virtual environment on first run, then starts the EAT
REM server and opens the app in the default browser.
setlocal

cd /d "%~dp0"

set VENV_DIR=.venv

where python >nul 2>nul
if errorlevel 1 (
    echo Error: no Python interpreter found on PATH. Install Python 3.12+ and re-run.
    exit /b 1
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Setting up virtual environment ^(first run only^)...
    python -m venv "%VENV_DIR%"
    "%VENV_DIR%\Scripts\pip" install --upgrade pip -q
    "%VENV_DIR%\Scripts\pip" install -r requirements.txt -q
)

REM Open the browser shortly after the server has had time to start.
start "" cmd /c "timeout /t 2 >nul && start "" "http://127.0.0.1:8000/""

"%VENV_DIR%\Scripts\uvicorn" eat.api:app --host 127.0.0.1 --port 8000

endlocal
