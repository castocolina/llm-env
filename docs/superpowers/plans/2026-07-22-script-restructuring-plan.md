# Script Restructuring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure LLM environment scripts into focused components with a shared library, where all behavior derives from model definitions at the top of `models.sh`.

**Architecture:** Create `models.sh` as single source of truth. Each script sources it and uses its functions/arrays. Tests discover models dynamically from filesystem/presets, not hardcoded.

**Tech Stack:** Bash, distrobox, llama.cpp, curl

## Global Constraints

- All output in English regardless of input language
- After editing any `.sh` file, run `make validate` (shellcheck) before committing
- Timeouts configurable via variables at top of `models.sh`
- `-p` flag must be last in llama-cli calls to avoid parsing issues
- Use `-no-cnv` flag to disable interactive mode in llama-cli

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `models.sh` | Create | Single source of truth: paths, config, model definitions, functions |
| `setup.sh` | Rewrite | Dynamic menu, download, build, validate, write presets |
| `setup-test.sh` | Create | Inference test based on downloaded models |
| `start.sh` | Rewrite | Launch server from presets.ini |
| `server-test.sh` | Create | Live test forcing internet access |
| `test.sh` | Delete | Replaced by server-test.sh |
| `Makefile` | Update | New targets: setup-test, server-test |
| `README.md` | Update | New workflow |
| `QUICK_START.md` | Update | New workflow |

---

### Task 1: Create `models.sh` — Shared Library

**Files:**
- Create: `models.sh`

**Interfaces:**
- Produces: `ALL_MODELS` array, `SETUP_TEST_PROMPTS`, `SERVER_TEST_PROMPTS`, `parse_model()`, `check_model()`, `download_model()`, `show_status()`, `human_readable_size()`, `mark_checkpoint()`, `is_checkpoint_done()`, all path/config variables

- [ ] **Step 1: Create models.sh with paths, config, colors, model definitions**

```bash
#!/usr/bin/env bash
# models.sh — Single source of truth for LLM environment
# Source this file from other scripts: source "$(dirname "$0")/models.sh"

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
SETUP_TEST_TIMEOUT=20
SERVER_TEST_TIMEOUT=20
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
    cd "${WORKSPACE_DIR}/models"
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
```

- [ ] **Step 2: Run shellcheck on models.sh**

Run: `shellcheck -s bash models.sh`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add models.sh
git commit -m "feat: add models.sh shared library

Single source of truth for model definitions, paths, config,
and helper functions. All scripts will source this file."
```

---

### Task 2: Rewrite `setup.sh` — Dynamic from models.sh

**Files:**
- Rewrite: `setup.sh`

**Interfaces:**
- Consumes: `models.sh` (all functions and arrays)
- Produces: `presets.ini`, `.config`, downloaded models, compiled llama.cpp

- [ ] **Step 1: Create new setup.sh that sources models.sh**

```bash
#!/usr/bin/env bash
# setup.sh — Download models, build llama.cpp, validate
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/models.sh"

# Interactive model selection
select_models() {
    echo ""
    echo -e "${YELLOW}Select models to download:${NC}"
    echo ""

    local i=1
    for model_def in "${ALL_MODELS[@]}"; do
        parse_model "$model_def"
        echo "  ${i}) ${MODEL_DESC}    $(human_readable_size "$MODEL_SIZE")"
        i=$((i + 1))
    done
    echo "  ${i}) Both          ~13.2 GB (recommended for 16GB VRAM)"
    echo ""

    while true; do
        read -rp "  Enter choice [1-${i}] (default: ${i}): " choice
        choice=${choice:-$i}

        case "$choice" in
            1) SELECTED_MODELS="gemma4"; break ;;
            2) SELECTED_MODELS="ornith"; break ;;
            3) SELECTED_MODELS="gemma4,ornith"; break ;;
            *) echo -e "${RED}Invalid choice. Please enter 1-${i}.${NC}" ;;
        esac
    done

    echo ""
    echo -e "${GREEN}Selected: ${SELECTED_MODELS}${NC}"
    echo ""
}

# Generate presets.ini from selected models
generate_presets() {
    local models="$1"
    mkdir -p "$(dirname "$PRESETS_FILE")"

    cat > "$PRESETS_FILE" << 'EOF'
# LLM Router Mode Presets
# Clients select model via "model" field in API requests.
EOF

    for model_def in "${ALL_MODELS[@]}"; do
        parse_model "$model_def"
        if echo "$models" | grep -q "$MODEL_ALIAS"; then
            cat >> "$PRESETS_FILE" << EOF

[$MODEL_ALIAS]
model = ~/llm-workspace/models/$MODEL_NAME
n-gpu-layers = 99
ctx-size = 8192
jinja = true
EOF
        fi
    done

    echo -e "${GREEN}✓${NC} Presets saved to ${PRESETS_FILE}"
}

# Save config file
save_config() {
    local models="$1"
    mkdir -p "$(dirname "$CONFIG_FILE")"
    cat > "$CONFIG_FILE" << EOF
MODELS=${models}
SERVER_PORT=${SERVER_PORT}
SERVER_HOST=${SERVER_HOST}
MODELS_MAX=${MODELS_MAX}
EOF
    echo -e "${GREEN}✓${NC} Config saved to ${CONFIG_FILE}"
}

# Validate llama.cpp can see downloaded models
validate_models() {
    show_status "Validating Models"

    # Try --list-models first, fallback to file check
    LLAMA_OUTPUT=""
    if [ -d "${WORKSPACE_DIR}/llama.cpp" ]; then
        LLAMA_OUTPUT=$(distrobox enter "${CONTAINER_NAME}" -- bash -c "
            cd '${WORKSPACE_DIR}/llama.cpp'
            ./build/bin/llama-cli --list-models 2>&1 || true
        " 2>&1) || true
    fi

    for model_def in "${ALL_MODELS[@]}"; do
        parse_model "$model_def"
        if echo "$SELECTED_MODELS" | grep -q "$MODEL_ALIAS"; then
            # Check via llama-cli output or file existence
            if (echo "$LLAMA_OUTPUT" | grep -q "$MODEL_NAME") || \
               check_model "$MODEL_NAME" "$MODEL_SIZE"; then
                echo -e "  ${GREEN}✓${NC} ${MODEL_DESC} verified"
            else
                echo -e "  ${RED}✗${NC} ${MODEL_DESC} NOT found"
                return 1
            fi
        fi
    done
}

# Main execution
mkdir -p "${CHECKPOINT_DIR}" "${WORKSPACE_DIR}/models"

show_status "LLM Setup"

# Model selection (always ask)
select_models

# STEP 1: Create distrobox container
if ! is_checkpoint_done "container_created"; then
    show_status "STEP 1: Creating Distrobox Container"
    if ! distrobox list | grep -q "${CONTAINER_NAME}"; then
        echo "Creating container: ${CONTAINER_NAME}..."
        distrobox create -n "${CONTAINER_NAME}" --image "fedora:${FEDORA_VERSION}" --no-entry
    else
        echo "Container already exists, verifying..."
    fi
    mark_checkpoint "container_created"
else
    show_status "STEP 1: Container Already Created (Skipped)"
fi

# STEP 2: Download selected models
if ! is_checkpoint_done "models_downloaded"; then
    show_status "STEP 2: Downloading Models"
    for model_def in "${ALL_MODELS[@]}"; do
        parse_model "$model_def"
        if echo "$SELECTED_MODELS" | grep -q "$MODEL_ALIAS"; then
            download_model "$model_def"
        fi
    done
    mark_checkpoint "models_downloaded"
else
    show_status "STEP 2: Models Already Downloaded (Skipped)"
fi

# STEP 3: Generate presets and config
show_status "STEP 3: Generating Config"
generate_presets "$SELECTED_MODELS"
save_config "$SELECTED_MODELS"

# STEP 4: Build llama.cpp
if ! is_checkpoint_done "llama_cpp_compiled"; then
    show_status "STEP 4: Building llama.cpp"
    CPU_CORES=$(nproc)
    distrobox enter "${CONTAINER_NAME}" -- bash -c "
    set -e
    export WORK_DIR='${WORKSPACE_DIR}'
    export CPU_CORES=${CPU_CORES}

    echo '  -> Installing build toolchain & Vulkan support...'
    sudo dnf5 clean all 2>/dev/null || true
    sudo dnf5 makecache 2>/dev/null || true
    sudo dnf5 install -y \
        gcc gcc-c++ git cmake make \
        vulkan-loader-devel vulkan-headers \
        spirv-headers-devel glslc \
        mesa-vulkan-drivers \
        python3-pip python3-devel 2>/dev/null || true
    sudo dnf5 install -y openblas-devel 2>/dev/null || true

    echo '  -> Cloning/updating llama.cpp repository...'
    if [ ! -d 'llama.cpp' ]; then
        git clone '${CPP_REPO_URL}' --depth=1
    else
        cd llama.cpp && git pull --rebase || true && cd ..
    fi

    cd llama.cpp
    echo '  -> Configuring CMake with Vulkan + optimization flags...'
    export CFLAGS='-O3 -march=native -mtune=native -flto'
    export CXXFLAGS='-O3 -march=native -mtune=native -flto'
    cmake -B build \
        -DGGML_VULKAN=ON \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_C_FLAGS=\"\${CFLAGS}\" \
        -DCMAKE_CXX_FLAGS=\"\${CXXFLAGS}\" \
        -DBUILD_SHARED_LIBS=ON

    echo \"  -> Compiling (using \${CPU_CORES} cores)...\"
    cmake --build build --config Release -j\${CPU_CORES} --verbose
    echo '  -> Build successful!'
    " || exit 1
    mark_checkpoint "llama_cpp_compiled"
else
    show_status "STEP 4: llama.cpp Already Compiled (Skipped)"
fi

# STEP 5: Validate models
if ! is_checkpoint_done "models_validated"; then
    validate_models
    mark_checkpoint "models_validated"
else
    show_status "STEP 5: Models Already Validated (Skipped)"
fi

# Summary
show_status "Setup Complete!"
echo ""
echo -e "${GREEN}Your LLM environment is ready!${NC}"
echo -e "${GREEN}Selected models: ${SELECTED_MODELS}${NC}"
echo ""
echo "Quick start commands:"
echo -e "  ${BLUE}Test inference:${NC} ./setup-test.sh"
echo -e "  ${BLUE}Start server:${NC}   ./start.sh"
echo -e "  ${BLUE}Stop server:${NC}    ./stop.sh"
echo -e "  ${BLUE}Test server:${NC}    ./server-test.sh"
echo -e "  ${BLUE}Enter container:${NC} distrobox enter ${CONTAINER_NAME}"
echo ""
echo "Workspace: ${WORKSPACE_DIR}"
echo "Presets: ${PRESETS_FILE}"
```

- [ ] **Step 2: Run shellcheck on setup.sh**

Run: `shellcheck -s bash setup.sh`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add setup.sh
git commit -m "refactor: rewrite setup.sh to source models.sh

Dynamic menu, presets, validation all derive from ALL_MODELS array.
Added validate_models step to verify llama.cpp sees downloaded files."
```

---

### Task 3: Create `setup-test.sh` — Inference Test

**Files:**
- Create: `setup-test.sh`

**Interfaces:**
- Consumes: `models.sh` (ALL_MODELS, SETUP_TEST_PROMPTS, SETUP_TEST_TIMEOUT, parse_model, show_status)
- Produces: Exit code 0 (pass) or 1 (fail)

- [ ] **Step 1: Create setup-test.sh**

```bash
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
    DOWNLOADED=$(ls "${WORKSPACE_DIR}/models/"*.gguf 2>/dev/null | xargs -I{} basename {} || true)
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
            OUTPUT=$(timeout ${SETUP_TEST_TIMEOUT} distrobox enter "${CONTAINER_NAME}" -- bash -c "
                cd '${WORKSPACE_DIR}/llama.cpp'
                ./build/bin/llama-cli \
                    -m '../models/${MODEL_NAME}' \
                    -ngl 99 \
                    -t \$(nproc) \
                    --jinja \
                    -n 50 \
                    -no-cnv \
                    --simple-io \
                    -p '${prompt}' 2>/dev/null
            " 2>&1) || {
                echo -e "\r  ${prompt} ${RED}✗ timeout (${SETUP_TEST_TIMEOUT}s)${NC}    "
                TEST_FAIL=$((TEST_FAIL + 1))
                continue
            }

            if [ -n "$OUTPUT" ] && echo "$OUTPUT" | grep -qE '[a-zA-Z0-9]'; then
                local response
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
```

- [ ] **Step 2: Run shellcheck on setup-test.sh**

Run: `shellcheck -s bash setup-test.sh`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add setup-test.sh
git commit -m "feat: add setup-test.sh for inference testing

Tests each downloaded model with multiple prompts.
20s timeout per prompt, inline results, exit code for chaining."
```

---

### Task 4: Rewrite `start.sh` — Use presets.ini

**Files:**
- Rewrite: `start.sh`

**Interfaces:**
- Consumes: `models.sh` (paths, config variables)
- Produces: Running llama-server process, PID file

- [ ] **Step 1: Create new start.sh**

```bash
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
```

- [ ] **Step 2: Run shellcheck on start.sh**

Run: `shellcheck -s bash start.sh`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add start.sh
git commit -m "refactor: rewrite start.sh to use presets.ini

Reads presets.ini to discover models. Launches router mode server.
Sources models.sh for all config values."
```

---

### Task 5: Create `server-test.sh` — Live Agent Test

**Files:**
- Create: `server-test.sh`

**Interfaces:**
- Consumes: `models.sh` (paths, SERVER_TEST_PROMPTS, SERVER_TEST_TIMEOUT, SERVER_PORT, PRESETS_FILE)
- Produces: Exit code 0 (pass) or 1 (fail)

- [ ] **Step 1: Create server-test.sh**

```bash
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

        RESPONSE=$(timeout ${SERVER_TEST_TIMEOUT} curl -s \
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
```

- [ ] **Step 2: Run shellcheck on server-test.sh**

Run: `shellcheck -s bash server-test.sh`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add server-test.sh
git commit -m "feat: add server-test.sh for live agent testing

Discovers models from presets.ini. Sends prompts that force
internet access (weather, time). Verifies real-time data in response."
```

---

### Task 6: Delete `test.sh` and Update Makefile

**Files:**
- Delete: `test.sh`
- Modify: `Makefile`

**Interfaces:**
- Consumes: None
- Produces: Updated Makefile with new targets

- [ ] **Step 1: Delete test.sh**

Run: `rm test.sh`

- [ ] **Step 2: Update Makefile**

```makefile
.PHONY: help all setup setup-test start stop server-test shell clean-cache clean validate

CONTAINER_NAME = llm-env
WORKSPACE = $(HOME)/llm-workspace
CHECKPOINTS = $(WORKSPACE)/.cache/checkpoints

help:
	@echo "╔════════════════════════════════════════════════════════════╗"
	@echo "║           LLM Environment - Available Commands             ║"
	@echo "╠════════════════════════════════════════════════════════════╣"
	@echo "║ make all          Full setup + test + start + server-test  ║"
	@echo "║ make setup        Download/compile LLM environment         ║"
	@echo "║ make setup-test   Test inference on downloaded models      ║"
	@echo "║ make start        Start LLM server                        ║"
	@echo "║ make stop         Stop LLM server                         ║"
	@echo "║ make server-test  Live agent test (forces internet)       ║"
	@echo "║ make shell        Enter distrobox container               ║"
	@echo "║ make cache-status Show build cache/checkpoints status     ║"
	@echo "║ make clean-cache  Clear all checkpoints (rebuild next)    ║"
	@echo "║ make clean        Remove container & workspace            ║"
	@echo "║ make validate     Run shellcheck on all .sh files         ║"
	@echo "╚════════════════════════════════════════════════════════════╝"

all: setup setup-test start server-test
	@echo "Full setup complete!"

setup:
	@echo "Starting LLM environment setup..."
	@bash setup.sh

setup-test:
	@bash setup-test.sh

start:
	@bash start.sh

stop:
	@bash stop.sh

server-test:
	@bash server-test.sh

shell:
	@if distrobox list | grep -q "$(CONTAINER_NAME)"; then \
		distrobox enter $(CONTAINER_NAME); \
	else \
		echo "Container $(CONTAINER_NAME) not found. Run 'make setup' first."; \
		exit 1; \
	fi

cache-status:
	@echo "Build Cache Status:"
	@if [ -d "$(CHECKPOINTS)" ]; then \
		echo "  Checkpoint directory exists"; \
		ls -1 "$(CHECKPOINTS)" 2>/dev/null | sed 's/^/    /'; \
	else \
		echo "  No checkpoints yet"; \
	fi
	@echo ""
	@echo "Workspace: $(WORKSPACE)"
	@if [ -d "$(WORKSPACE)/models" ]; then \
		echo "  Models:"; \
		ls -lh $(WORKSPACE)/models 2>/dev/null | tail -n +2 | awk '{print "    " $$9, "(" $$5 ")"}'; \
	fi

clean-cache:
	@echo "Clearing all build checkpoints..."
	@rm -rf "$(CHECKPOINTS)"
	@echo "Checkpoints cleared. Next run will rebuild from scratch."

clean:
	@echo "This will remove the entire LLM environment!"
	@echo "  Container: $(CONTAINER_NAME)"
	@echo "  Workspace: $(WORKSPACE)"
	@read -p "Are you sure? (yes/no) " confirm && [ "$$confirm" = "yes" ] || exit 1
	@distrobox rm -f $(CONTAINER_NAME) 2>/dev/null || true
	@rm -rf $(WORKSPACE)
	@echo "Cleanup complete."

validate:
	@command -v shellcheck >/dev/null 2>&1 || { echo "shellcheck not found. Install with: brew install shellcheck (macOS) or sudo dnf install ShellCheck (Fedora)"; exit 1; }
	@echo "Running shellcheck on all .sh files..."
	@shellcheck -s bash *.sh
	@echo "All shell scripts pass shellcheck."
```

- [ ] **Step 3: Run shellcheck on all scripts**

Run: `make validate`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add Makefile
git rm test.sh
git commit -m "refactor: update Makefile, remove old test.sh

New targets: setup-test, server-test.
Workflow: setup → setup-test → start → server-test"
```

---

### Task 7: Update Documentation

**Files:**
- Modify: `README.md`
- Modify: `QUICK_START.md`

**Interfaces:**
- Consumes: None
- Produces: Updated docs reflecting new workflow

- [ ] **Step 1: Update README.md**

Replace the entire content with:

```markdown
# LLM Environment

Automated setup for llama.cpp on Bazzite (Linux) with GPU acceleration.

## Quick Start

```bash
make all          # Full setup + test + start + server-test
```

Or step by step:

```bash
make setup        # Download/compile (interactive model selection)
make setup-test   # Test inference on downloaded models
make start        # Start server, print connection info
make server-test  # Live agent test (forces internet access)
make stop         # Stop server
```

## Models (Router Mode)

Both models are served simultaneously from a single server:

| Alias | Model | Size | Best For |
|-------|-------|------|----------|
| `gemma4` | Gemma 4 12B Q4_K_M | ~7.6 GB | Multimodal, general tasks |
| `ornith` | Ornith 1.0 9B Q4_K_M | ~5.6 GB | Coding specialist (69.4% SWE-bench) |

Total VRAM usage: ~13.2 GB (fits in 16GB). Models are loaded on-demand with LRU eviction.

Select model via `model` field in API requests.

## Remote Access (macOS Client)

Connect directly to Linux IP (no SSH tunnel needed):

```bash
http://<linux-ip>:8000/docs
```

### Usage

```bash
# Use Gemma4 (general tasks)
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gemma4", "messages": [{"role": "user", "content": "Hello"}]}'

# Use Ornith (coding tasks)
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "ornith", "messages": [{"role": "user", "content": "Write a Python function"}]}'
```

### OpenCode Configuration

```json
{
    "provider": {
        "local": {
            "api_key": "none",
            "models": {
                "gemma4": {
                    "endpoint": "http://<linux-ip>:8000/v1/chat/completions"
                },
                "ornith": {
                    "endpoint": "http://<linux-ip>:8000/v1/chat/completions"
                }
            }
        }
    }
}
```

## Available Commands

| Command | Description |
|---------|-------------|
| `make all` | Full setup + test + start + server-test |
| `make setup` | Download models, build llama.cpp |
| `make setup-test` | Test inference on downloaded models |
| `make start` | Start LLM server |
| `make stop` | Stop LLM server |
| `make server-test` | Live agent test (forces internet) |
| `make shell` | Enter distrobox container |
| `make cache-status` | Show build checkpoint status |
| `make clean-cache` | Clear all checkpoints |
| `make clean` | Remove container & workspace |
| `make validate` | Run shellcheck on all .sh files |

## Architecture

```
models.sh      ← Single source of truth (model definitions + helpers)
setup.sh       ← Download models, build llama.cpp, validate
setup-test.sh  ← Inference test based on downloaded models
start.sh       ← Launch server based on presets.ini
server-test.sh ← Live test forcing internet access
stop.sh        ← Server shutdown
```

## Workspace

- Models: `~/llm-workspace/models/`
- Config: `~/llm-workspace/.config`
- Presets: `~/llm-workspace/presets.ini`
- Logs: `~/llm-workspace/.config/server.log`
```

- [ ] **Step 2: Update QUICK_START.md**

Replace the entire content with:

```markdown
# Quick Start

## First-Time Setup

```bash
make all
```

This will:
1. Prompt you to select models (Gemma4, Ornith, or Both)
2. Create a distrobox container
3. Download selected models
4. Compile llama.cpp with Vulkan support
5. Validate models are accessible
6. Test inference on downloaded models
7. Start the server
8. Run live agent test

## Step by Step

```bash
make setup        # Interactive setup with model selection
make setup-test   # Test inference (non-blocking, 20s timeout)
make start        # Start server, print connection info
make server-test  # Live test forcing internet access
make stop         # Stop server
```

## Connect from macOS

1. Ensure both machines are on the same network
2. Open browser on macOS: `http://<linux-ip>:8000/docs`
3. Or configure OpenCode:
   ```json
   {
       "provider": {
           "local": {
               "api_key": "none",
               "models": {
                   "gemma4": {
                       "endpoint": "http://<linux-ip>:8000/v1/chat/completions"
                   },
                   "ornith": {
                       "endpoint": "http://<linux-ip>:8000/v1/chat/completions"
                   }
               }
           }
       }
   }
   ```

## Troubleshooting

- **Server won't start**: Check logs at `~/llm-workspace/.config/server.log`
- **Connection refused**: Ensure server is running with `make start`
- **Inference timeout**: Models may need more time to load, increase `SETUP_TEST_TIMEOUT` in models.sh
- **Build failed**: Run `make clean-cache && make setup` to rebuild from scratch
```

- [ ] **Step 3: Commit**

```bash
git add README.md QUICK_START.md
git commit -m "docs: update README and QUICK_START for new workflow

Document models.sh, setup-test.sh, server-test.sh workflow.
Add troubleshooting for new timeout configuration."
```

---

### Task 8: Final Validation

**Files:**
- None (validation only)

**Interfaces:**
- Consumes: None
- Produces: Verified working system

- [ ] **Step 1: Run shellcheck on all scripts**

Run: `make validate`
Expected: PASS

- [ ] **Step 2: Verify all scripts exist and are executable**

Run: `ls -la *.sh models.sh`
Expected: All scripts exist with execute permission

- [ ] **Step 3: Verify Makefile targets work**

Run: `make help`
Expected: Shows all available commands

- [ ] **Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: final validation fixes"
```

(Only if fixes were needed)
