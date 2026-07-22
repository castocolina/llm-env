#!/usr/bin/env bash
# models.sh — Single source of truth for LLM environment
# Source this file from other scripts: source "$(dirname "$0")/models.sh"
# shellcheck disable=SC2034

# ── Paths ──────────────────────────────────────────────
WORKSPACE_DIR="${HOME}/llm-workspace"
CACHE_DIR="${WORKSPACE_DIR}/.cache"
CHECKPOINT_DIR="${CACHE_DIR}/checkpoints"
CONFIG_FILE="${WORKSPACE_DIR}/.config"
PRESETS_FILE="${WORKSPACE_DIR}/presets.ini"
CONTAINER_NAME="llm-env"
FEDORA_VERSION="44"
CPP_REPO_URL="https://github.com/ggml-org/llama.cpp"

# ── Configuration ──────────────────────────────────────
SETUP_TEST_TIMEOUT=60
SERVER_TEST_TIMEOUT=60
SERVER_PORT=8000
SERVER_HOST=0.0.0.0
MODELS_MAX=2

# ── Colors ─────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'
RED='\033[0;31m'; NC='\033[0m'

# ── Model Definitions ─────────────────────────────────
# Format: ALIAS|URL|FILENAME|SIZE_BYTES|DESCRIPTION
ALL_MODELS=(
  "gemma4|https://huggingface.co/bartowski/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q4_K_M.gguf|gemma-4-12B-it-Q4_K_M.gguf|7660000000|Gemma 4 12B"
  "ornith|https://huggingface.co/deepreinforce-ai/Ornith-1.0-9B-GGUF/resolve/main/ornith-1.0-9b-Q4_K_M.gguf|ornith-1.0-9b-Q4_K_M.gguf|5600000000|Ornith 1.0 9B"
)

# ── Test Prompts ───────────────────────────────────────
SETUP_TEST_PROMPTS=("Say hello in 5 words." "What is 2+2?" "Write a one-line Python hello.")
SERVER_TEST_PROMPTS=("What's the current weather in Tokyo?" "What time is it in New York right now?")

# ── Helper Functions ───────────────────────────────────

# Parse model definition into global variables
# Usage: parse_model "${ALL_MODELS[0]}"
# Sets: MODEL_ALIAS, MODEL_URL, MODEL_NAME, MODEL_SIZE, MODEL_DESC
parse_model() {
    local def="$1"
    IFS='|' read -r MODEL_ALIAS MODEL_URL MODEL_NAME MODEL_SIZE MODEL_DESC <<< "$def"
}

# Check if model file exists and is valid
# Usage: check_model "filename.gguf" "7660000000"
check_model() {
    local name="$1"
    local size="$2"
    if [ -f "${WORKSPACE_DIR}/models/${name}" ]; then
        local file_size
        file_size=$(stat -c%s "${WORKSPACE_DIR}/models/${name}" 2>/dev/null || echo "0")
        if [ "$file_size" -gt "$size" ]; then
            return 0
        fi
    fi
    return 1
}

# Download model if not present
# Usage: download_model "${ALL_MODELS[0]}"
download_model() {
    local def="$1"
    parse_model "$def"

    if check_model "$MODEL_NAME" "$MODEL_SIZE"; then
        echo -e "${GREEN}✓${NC} ${MODEL_DESC} already exists ($(human_readable_size "$MODEL_SIZE"))"
        return 0
    fi

    echo "Downloading ${MODEL_DESC}..."
    mkdir -p "${WORKSPACE_DIR}/models"
    cd "${WORKSPACE_DIR}/models" || return 1
    if command -v wget &>/dev/null; then
        wget --continue --progress=bar:force:noscroll "$MODEL_URL" -O "$MODEL_NAME"
    else
        curl -L -C - --progress-bar "$MODEL_URL" -o "$MODEL_NAME"
    fi
}

# Print section header
show_status() {
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# Convert bytes to human readable
human_readable_size() {
    local bytes="$1"
    if command -v numfmt &>/dev/null; then
        numfmt --to=iec-i --suffix=B "$bytes"
    else
        echo "${bytes} bytes"
    fi
}

# Mark checkpoint as done
mark_checkpoint() {
    mkdir -p "${CHECKPOINT_DIR}"
    touch "${CHECKPOINT_DIR}/$1"
    echo -e "${GREEN}✓${NC} Checkpoint saved: $1"
}

# Check if checkpoint exists
is_checkpoint_done() {
    [ -f "${CHECKPOINT_DIR}/$1" ]
}
