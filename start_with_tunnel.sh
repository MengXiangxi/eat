#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Starting Eat App stack (Docker + management server)..."
echo ""

# Verify Docker/Compose availability
if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker is not installed or not on PATH."
    exit 1
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
else
    echo "ERROR: docker compose/ docker-compose is not available."
    exit 1
fi

# Bring up the main site (app + cloudflared) in detached mode
echo "Building + starting Docker services (serving main site on port 5000)..."
$COMPOSE_CMD up --build -d

# Pick python interpreter (prefer local venv)
if [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
else
    echo "ERROR: No python interpreter found."
    exit 1
fi

# Start management server in detached mode (0.0.0.0:5001 for LAN/Tailscale use)
echo "Starting management server on port 5001 (local/Tailscale only)..."
MANAGE_CMD="cd \"$SCRIPT_DIR\" && exec \"$PYTHON_BIN\" server_manage.py"
MANAGE_SESSION="eat-manage"

if command -v tmux >/dev/null 2>&1; then
    if tmux has-session -t "$MANAGE_SESSION" 2>/dev/null; then
        echo "Existing tmux session '$MANAGE_SESSION' detected. Replacing it..."
        tmux kill-session -t "$MANAGE_SESSION"
    fi
    tmux new-session -d -s "$MANAGE_SESSION" "$MANAGE_CMD"
    echo "Management server running inside tmux session '$MANAGE_SESSION'."
    echo "Attach via: tmux attach -t $MANAGE_SESSION"
else
    echo "tmux not found. Falling back to nohup background process."
    nohup bash -c "$MANAGE_CMD" > server_manage.log 2>&1 &
    echo $! > server_manage.pid
    echo "Management server started (PID $(cat server_manage.pid)). Logs: server_manage.log"
fi

echo ""
echo "Main site: http://localhost:5000 (via Docker)"
echo "Management: http://localhost:5001 (this machine/Tailscale IP)"
echo "Docker services keep running until you run '$COMPOSE_CMD down'."
