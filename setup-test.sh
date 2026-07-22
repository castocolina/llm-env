#!/usr/bin/env bash
# setup-test.sh — Inference test based on downloaded models
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/models.sh"

mkdir -p "${CHECKPOINT_DIR}"

show_status "Setup Test — Inference Check"

# Discover downloaded models
DOWNLOADED=""
if [ -d "${WORKSPACE_DIR}/models" ]; then
    DOWNLOADED=$(find "${WORKSPACE_DIR}/models" -name "*.gguf" -exec basename {} \; 2>/dev/null || true)
fi

if [ -z "$DOWNLOADED" ]; then
    echo -e "${RED}No models found in ${WORKSPACE_DIR}/models/${NC}"
    echo "Run ./setup.sh first to download models."
    exit 1
fi

TEST_PASS=0
TEST_FAIL=0

for model_def in "${ALL_MODELS[@]}"; do
    parse_model "$model_def"

    if echo "$DOWNLOADED" | grep -q "$MODEL_NAME"; then
        echo -e "\n${BLUE}Model: ${MODEL_DESC}${NC}"

        for prompt in "${SETUP_TEST_PROMPTS[@]}"; do
            echo -n "  ${prompt} "

            # Run inference with timeout
            OUTPUT=$(timeout "${SETUP_TEST_TIMEOUT}" distrobox enter "${CONTAINER_NAME}" -- bash -c "
                cd '${WORKSPACE_DIR}/llama.cpp'
                ./build/bin/llama-cli \
                    -m '../models/${MODEL_NAME}' \
                    -ngl 99 \
                    -t \$(nproc) \
                    --jinja \
                    -n 50 \
                    -no-cnv \
                    --simple-io \
                    -p '${prompt}'
            " 2>&1) || {
                echo -e "\r  ${prompt} ${RED}✗ timeout (${SETUP_TEST_TIMEOUT}s)${NC}    "
                TEST_FAIL=$((TEST_FAIL + 1))
                continue
            }

            if [ -n "$OUTPUT" ] && echo "$OUTPUT" | grep -qE '[a-zA-Z0-9]'; then
                response=$(echo "$OUTPUT" | tail -1)
                echo -e "\r  ${prompt} ${GREEN}✓${NC} ${response}    "
                TEST_PASS=$((TEST_PASS + 1))
            else
                echo -e "\r  ${prompt} ${RED}✗ empty response${NC}    "
                TEST_FAIL=$((TEST_FAIL + 1))
            fi
        done
    fi
done

echo ""
show_status "Results"
echo -e "  ${GREEN}Passed: ${TEST_PASS}/${TEST_PASS + TEST_FAIL}${NC}"
if [ $TEST_FAIL -gt 0 ]; then
    echo -e "  ${RED}Failed: ${TEST_FAIL}/${TEST_PASS + TEST_FAIL}${NC}"
    exit 1
fi
