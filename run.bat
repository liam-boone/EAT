@echo off
REM Sets up the virtual environment on first run, then starts the EAT
REM server and opens the app in the default browser.
REM
REM NOTE: this script has only been logic-reviewed, not run end-to-end on
REM real Windows (no Windows machine was available at build time). If it
REM fails, the comments below mark each step so you can tell which one
REM broke -- run it from a `cmd.exe` window (not by double-clicking) so
REM the error output stays visible, and see README.md's "Known gaps"
REM section.
setlocal

REM --- Step 0: make sure we're running from the project root, regardless
REM     of the working directory this was launched from (e.g. double-click
REM     from Explorer vs. `run.bat` typed in an already-open terminal). ---
cd /d "%~dp0"

set VENV_DIR=.venv

REM --- Step 1: confirm a Python interpreter is on PATH at all. If this
REM     fails: Python isn't installed, or the installer's "Add Python to
REM     PATH" checkbox wasn't ticked -- reinstall Python 3.12+ from
REM     python.org and make sure that box is checked. ---
where python >nul 2>nul
if errorlevel 1 (
    echo Error: no Python interpreter found on PATH. Install Python 3.12+ and re-run.
    exit /b 1
)

REM --- Step 2: create the virtual environment and install dependencies,
REM     but only on first run (skipped if .venv already has a python.exe).
REM     If venv creation fails: check the Python version with
REM     `python --version` (need 3.12+; sectionproperties' compiled
REM     dependencies may not have wheels for very new or very old
REM     versions -- see README.md). If pip install fails: check your
REM     internet connection, or re-run
REM     ".venv\Scripts\pip install -r requirements.txt" by hand (without
REM     -q) to see the actual error instead of it being suppressed. ---
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Setting up virtual environment ^(first run only^)...
    python -m venv "%VENV_DIR%"
    "%VENV_DIR%\Scripts\pip" install --upgrade pip -q
    "%VENV_DIR%\Scripts\pip" install -r requirements.txt -q
)

REM --- Step 3: open the browser a couple seconds after this script keeps
REM     going, giving uvicorn (started in step 4) time to come up. This
REM     runs in a separate background cmd window so it doesn't block
REM     step 4. If the browser opens to a "can't connect" page: the
REM     2-second delay wasn't enough (slow machine / antivirus scanning
REM     the new venv) -- just refresh the page once the terminal shows
REM     uvicorn's "Application startup complete" line. ---
start "" cmd /c "timeout /t 2 >nul && start "" "http://127.0.0.1:8000/""

REM --- Step 4: start the actual server (this is the long-running
REM     foreground process; closing this window stops the server). If
REM     this fails with "uvicorn is not recognized" or similar: step 2's
REM     install likely failed silently -- delete the .venv folder and
REM     re-run this script to retry it, watching for errors this time. ---
"%VENV_DIR%\Scripts\uvicorn" eat.api:app --host 127.0.0.1 --port 8000

endlocal
