#!/usr/bin/env bash
# LLM Server Test - Verify server and agent integration
set -e

WORKSPACE_DIR="${HOME}/llm-workspace"
CONFIG_FILE="${WORKSPACE_DIR}/.config"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

PASS=0
FAIL=0

# Check config
if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${RED}Error: Config file not found at ${CONFIG_FILE}${NC}"
    echo "Run ./setup.sh first."
    exit 1
fi

# shellcheck source=/dev/null
source "$CONFIG_FILE"
SERVER_URL="http://localhost:${SERVER_PORT}"

# Test 1: Health check
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Test 1: Server Health Check${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if curl -s "${SERVER_URL}/health" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Server is responding${NC}"
    PASS=$((PASS + 1))
else
    echo -e "${RED}✗ Server not responding at ${SERVER_URL}${NC}"
    echo "  Start server with: ./start.sh"
    FAIL=$((FAIL + 1))
fi

# Test 2: curl inference test
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Test 2: curl Inference Test${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
RESPONSE=$(curl -s "${SERVER_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "local",
        "messages": [{"role": "user", "content": "Say hello in exactly 5 words."}],
        "max_tokens": 50
    }' 2>&1)

if echo "$RESPONSE" | grep -q "choices"; then
    echo -e "${GREEN}✓ Inference test passed${NC}"
    echo -e "  ${BLUE}Response:${NC} $(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['choices'][0]['message']['content'])" || echo "$RESPONSE")"
    PASS=$((PASS + 1))
else
    echo -e "${RED}✗ Inference test failed${NC}"
    echo -e "  ${YELLOW}Response:${NC} $RESPONSE"
    FAIL=$((FAIL + 1))
fi

# Test 3: opencode integration test
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Test 3: OpenCode Integration Test${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if command -v opencode &>/dev/null; then
    echo "OpenCode found. Configuring..."

    # Create temp opencode config
    OPENTMPDIR=$(mktemp -d)
    cat > "${OPENTMPDIR}/opencode.json" << EOF
{
    "provider": {
        "local": {
            "api_key": "none",
            "models": {
                "local-model": {
                    "endpoint": "${SERVER_URL}/v1/chat/completions"
                }
            }
        }
    },
    "agent": {
        "model": "local-model"
    }
}
EOF

    echo "Sending test prompt to local server..."
    RESULT=$(cd "$OPENTMPDIR" && opencode -p "What is the current temperature in Santiago, Chile? Reply with just the temperature number." 2>&1)

    if echo "$RESULT" | grep -qE '[0-9]+.*°|temperature|grados|[0-9]+ C'; then
        echo -e "${GREEN}✓ OpenCode integration test passed${NC}"
        echo -e "  ${BLUE}Response:${NC} $RESULT"
        PASS=$((PASS + 1))
    else
        echo -e "${RED}✗ OpenCode integration test failed${NC}"
        echo -e "  ${YELLOW}Response:${NC} $RESULT"
        FAIL=$((FAIL + 1))
    fi

    rm -rf "$OPENTMPDIR"
else
    echo -e "${YELLOW}⊘ OpenCode not installed. Skipping integration test.${NC}"
    echo "  Install with: brew install opencode (macOS) or see docs"
fi

# Summary
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Test Summary${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
TOTAL=$((PASS + FAIL))
echo -e "  ${GREEN}Passed: ${PASS}/${TOTAL}${NC}"
if [ "$FAIL" -gt 0 ]; then
    echo -e "  ${RED}Failed: ${FAIL}/${TOTAL}${NC}"
    exit 1
fi
