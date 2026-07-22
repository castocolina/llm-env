#!/usr/bin/env bash
# server-test.sh — Live test forcing internet access
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/models.sh"

show_status "Server Test — Live Agent Check"

# Check server is running
if ! curl -s "http://localhost:${SERVER_PORT}/health" > /dev/null 2>&1; then
    echo -e "${RED}✗ Server not running at http://localhost:${SERVER_PORT}${NC}"
    echo "  Start with: ./start.sh"
    exit 1
fi
echo -e "${GREEN}✓ Server is running${NC}"

# Discover models from presets.ini
if [ ! -f "$PRESETS_FILE" ]; then
    echo -e "${RED}✗ Presets file not found: ${PRESETS_FILE}${NC}"
    exit 1
fi

AVAILABLE_MODELS=$(grep -E '^\[' "$PRESETS_FILE" | tr -d '[]' || true)
if [ -z "$AVAILABLE_MODELS" ]; then
    echo -e "${RED}✗ No models found in presets.ini${NC}"
    exit 1
fi

TEST_PASS=0
TEST_FAIL=0

for model in $AVAILABLE_MODELS; do
    echo -e "\n${BLUE}Model: ${model}${NC}"

    for prompt in "${SERVER_TEST_PROMPTS[@]}"; do
        echo -n "  ${prompt} "

        RESPONSE=$(timeout "${SERVER_TEST_TIMEOUT}" curl -s \
            "http://localhost:${SERVER_PORT}/v1/chat/completions" \
            -H "Content-Type: application/json" \
            -d "{
                \"model\": \"${model}\",
                \"messages\": [{\"role\": \"user\", \"content\": \"${prompt}\"}],
                \"max_tokens\": 100
            }" 2>&1) || {
            echo -e "\r  ${prompt} ${RED}✗ timeout (${SERVER_TEST_TIMEOUT}s)${NC}    "
            TEST_FAIL=$((TEST_FAIL + 1))
            continue
        }

        if echo "$RESPONSE" | grep -q "choices"; then
            CONTENT=$(echo "$RESPONSE" | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin)['choices'][0]['message']['content'])
except:
    print('')
" 2>/dev/null)

            # Check for real-time data indicators
            if echo "$CONTENT" | grep -qiE "(weather|temperature|degrees|forecast|time|clock|hour|am|pm|[0-9]+°|[0-9]+:[0-9]+|[0-9]+ (AM|PM))"; then
                SHORT=$(echo "$CONTENT" | head -c 80)
                echo -e "\r  ${prompt} ${GREEN}✓${NC} ${SHORT}...    "
                TEST_PASS=$((TEST_PASS + 1))
            else
                echo -e "\r  ${prompt} ${RED}✗ no internet access detected${NC}    "
                TEST_FAIL=$((TEST_FAIL + 1))
            fi
        else
            echo -e "\r  ${prompt} ${RED}✗ request failed${NC}    "
            TEST_FAIL=$((TEST_FAIL + 1))
        fi
    done
done

echo ""
show_status "Results"
echo -e "  ${GREEN}Passed: ${TEST_PASS}/${TEST_PASS + TEST_FAIL}${NC}"
if [ $TEST_FAIL -gt 0 ]; then
    echo -e "  ${RED}Failed: ${TEST_FAIL}/${TEST_PASS + TEST_FAIL}${NC}"
    exit 1
fi
