#!/usr/bin/env bash
# Launcher for the Live Streaming Server (Python + HTML)
# Usage: ./run.sh   (then open http://localhost:8080)

set -e
cd "$(dirname "$0")"

# Pick the correct python binary (handles both venv and system installs)
PYTHON=""
for c in python3 /home/z/.venv/bin/python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c "import flask, flask_socketio" 2>/dev/null; then
      PYTHON="$c"; break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo "[!] Flask not found. Installing dependencies…"
  pip3 install --user -r requirements.txt 2>/dev/null || \
    pip3 install --break-system-packages -r requirements.txt 2>/dev/null
  PYTHON=python3
fi

echo "================================================"
echo "  RUANG SIARAN LANGSUNG - Live Streaming Server"
echo "  Using: $PYTHON"
echo "  Open:  http://localhost:8080"
echo "================================================"

STREAM_PORT="${STREAM_PORT:-8080}" exec "$PYTHON" server.py
