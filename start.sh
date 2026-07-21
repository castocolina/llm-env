#!/usr/bin/env bash
# LLM Server Start - Launch and wait for health
set -e

WORKSPACE_DIR="${HOME}/llm-workspace"
CONFIG_FILE="${WORKSPACE_DIR}/.config"
PID_FILE="${WORKSPACE_DIR}/.config/server.pid"
CONTAINER_NAME="llm-env"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

# Check config exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${RED}Error: Config file not found at ${CONFIG_FILE}${NC}"
    echo "Run ./setup.sh first to configure your environment."
    exit 1
fi

# Source config
# shellcheck source=/dev/null
source "$CONFIG_FILE"

# Check if server already running
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo -e "${YELLOW}Server already running (PID: ${PID})${NC}"
        echo -e "  ${BLUE}URL:${NC} http://localhost:${SERVER_PORT}/docs"
        exit 0
    else
        echo -e "${YELLOW}Stale PID file found. Cleaning up...${NC}"
        rm "$PID_FILE"
    fi
fi

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Starting LLM Server${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}Model: ${MODEL_NAME}${NC}"
echo ""

# Start server in distrobox
echo "Starting llama-server in distrobox..."
distrobox enter "${CONTAINER_NAME}" -- bash -c "
cd llama.cpp
./build/bin/llama-server \
    -m ../models/${MODEL_NAME} \
    -ngl 99 \
    --jinja \
    --host ${SERVER_HOST} \
    --port ${SERVER_PORT} \
    --ctx-size 8192 \
    > ${WORKSPACE_DIR}/.config/server.log 2>&1 &
echo \$! > ${PID_FILE}
"

# Wait for server to be ready
echo "Waiting for server to be ready..."
TIMEOUT=30
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if curl -s "http://localhost:${SERVER_PORT}/health" > /dev/null 2>&1; then
        echo ""
        echo -e "${GREEN}✓ Server is ready!${NC}"
        echo ""
        echo -e "${BLUE}Connection info:${NC}"
        echo -e "  Local:   http://localhost:${SERVER_PORT}/docs"
        NETWORK_IP=$(hostname -I | awk '{print $1}')
        echo -e "  Network: http://${NETWORK_IP}:${SERVER_PORT}/docs"
        echo ""
        echo -e "${BLUE}OpenCode config:${NC}"
        echo -e "  model: http://${NETWORK_IP}:${SERVER_PORT}/v1/chat/completions"
        echo ""
        exit 0
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
    echo -n "."
done

echo ""
echo -e "${RED}✗ Server failed to start within ${TIMEOUT}s${NC}"
echo -e "${YELLOW}Check logs:${NC} cat ${WORKSPACE_DIR}/.config/server.log"
exit 1
