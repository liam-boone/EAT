#!/usr/bin/env bash
# Sets up the virtual environment on first run, then starts the EAT
# server and opens the app in the default browser.
set -e

cd "$(dirname "${BASH_SOURCE[0]}")"

VENV_DIR=".venv"

# Prefer python3.12 (the version this project's dependencies — notably
# sectionproperties' compiled wheels — are verified against); fall back
# to whatever python3 the system provides.
PYTHON_BIN="$(command -v python3.12 || command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "Error: no Python 3 interpreter found. Install Python 3.12+ and re-run." >&2
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "Setting up virtual environment (first run only)..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install -r requirements.txt -q
fi

# Open the browser shortly after the server has had time to start.
( sleep 1.5 && (open "http://127.0.0.1:8000/" 2>/dev/null || xdg-open "http://127.0.0.1:8000/" 2>/dev/null || true) ) &

exec "$VENV_DIR/bin/uvicorn" eat.api:app --host 127.0.0.1 --port 8000
