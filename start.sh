#!/usr/bin/env bash
# LLM Server Start - Router mode with multiple models
set -e

WORKSPACE_DIR="${HOME}/llm-workspace"
CONFIG_FILE="${WORKSPACE_DIR}/.config"
PRESETS_FILE="${WORKSPACE_DIR}/presets.ini"
PID_FILE="${WORKSPACE_DIR}/.config/server.pid"
LOG_FILE="${WORKSPACE_DIR}/.config/server.log"
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

# Check presets file
if [ ! -f "$PRESETS_FILE" ]; then
    echo -e "${RED}Error: Presets file not found at ${PRESETS_FILE}${NC}"
    echo "Run ./setup.sh first to generate presets."
    exit 1
fi

# Check if server already running
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo -e "${YELLOW}Server already running (PID: ${PID})${NC}"
        echo -e "  ${BLUE}URL:${NC} http://localhost:${SERVER_PORT}/docs"
        echo -e "  ${BLUE}Models:${NC} ${MODELS}"
        exit 0
    else
        echo -e "${YELLOW}Stale PID file found. Cleaning up...${NC}"
        rm "$PID_FILE"
    fi
fi

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Starting LLM Server (Router Mode)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}Models: ${MODELS}${NC}"
echo -e "${YELLOW}Max concurrent: ${MODELS_MAX}${NC}"
echo ""

# Start server in router mode
echo "Starting llama-server in router mode..."
distrobox enter "${CONTAINER_NAME}" -- bash -c "
cd '${WORKSPACE_DIR}/llama.cpp' || { echo 'ERROR: llama.cpp directory not found'; exit 1; }
./build/bin/llama-server \
    --models-preset '${PRESETS_FILE}' \
    --models-max ${MODELS_MAX} \
    --host ${SERVER_HOST} \
    --port ${SERVER_PORT} \
    > '${LOG_FILE}' 2>&1 &
echo \$! > '${PID_FILE}'
"

# Wait for server to be ready
echo "Waiting for server to be ready..."
TIMEOUT=60
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
        echo -e "${BLUE}Available models:${NC}"
        echo "  - gemma4 (Gemma 4 12B)"
        echo "  - ornith (Ornith 1.0 9B)"
        echo ""
        echo -e "${BLUE}Usage:${NC}"
        echo "  curl http://localhost:${SERVER_PORT}/v1/chat/completions \\"
        echo "    -H 'Content-Type: application/json' \\"
        echo "    -d '{\"model\": \"gemma4\", \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}]}'"
        echo ""
        exit 0
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
    echo -n "."
done

echo ""
echo -e "${RED}✗ Server failed to start within ${TIMEOUT}s${NC}"
echo -e "${YELLOW}Check logs:${NC} cat ${LOG_FILE}"
exit 1
