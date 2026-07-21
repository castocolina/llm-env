#!/usr/bin/env bash
# LLM Server Stop - Kill running server
set -e

WORKSPACE_DIR="${HOME}/llm-workspace"
PID_FILE="${WORKSPACE_DIR}/.config/server.pid"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [ ! -f "$PID_FILE" ]; then
    echo -e "${YELLOW}No server PID file found. Server may not be running.${NC}"
    exit 0
fi

PID=$(cat "$PID_FILE")

if kill -0 "$PID" 2>/dev/null; then
    echo "Stopping server (PID: ${PID})..."
    kill "$PID"
    sleep 2
    if kill -0 "$PID" 2>/dev/null; then
        echo "Force killing..."
        kill -9 "$PID"
    fi
    rm "$PID_FILE"
    echo -e "${GREEN}✓ Server stopped.${NC}"
else
    echo -e "${YELLOW}Server process ${PID} not found. Cleaning up PID file.${NC}"
    rm "$PID_FILE"
fi
