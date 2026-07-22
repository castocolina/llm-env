#!/usr/bin/env bash
# start.sh — Launch server based on presets.ini
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/models.sh"

PID_FILE="${WORKSPACE_DIR}/.config/server.pid"
LOG_FILE="${WORKSPACE_DIR}/.config/server.log"

# Check config exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${RED}Error: Config file not found at ${CONFIG_FILE}${NC}"
    echo "Run ./setup.sh first."
    exit 1
fi

# Check presets file
if [ ! -f "$PRESETS_FILE" ]; then
    echo -e "${RED}Error: Presets file not found at ${PRESETS_FILE}${NC}"
    echo "Run ./setup.sh first."
    exit 1
fi

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

# Show what models will be served
AVAILABLE_MODELS=$(grep -E '^\[' "$PRESETS_FILE" | tr -d '[]' || true)

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Starting LLM Server (Router Mode)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}Models: ${AVAILABLE_MODELS}${NC}"
echo -e "${YELLOW}Max concurrent: ${MODELS_MAX}${NC}"
echo ""

# Start server
echo "Starting llama-server in router mode..."
mkdir -p "$(dirname "$PID_FILE")"
distrobox enter "${CONTAINER_NAME}" -- bash -c "
cd '${WORKSPACE_DIR}/llama.cpp' || { echo 'ERROR: llama.cpp not found'; exit 1; }
./build/bin/llama-server \
    --models-preset '${PRESETS_FILE}' \
    --models-max ${MODELS_MAX} \
    --host ${SERVER_HOST} \
    --port ${SERVER_PORT} \
    > '${LOG_FILE}' 2>&1 &
echo \$! > '${PID_FILE}'
"

# Wait for health check
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
        for model in $AVAILABLE_MODELS; do
            echo "  - ${model}"
        done
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
