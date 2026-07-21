# LLM Environment - Separate Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace monolithic setup-dev.sh with four separate scripts (setup, start, stop, test) plus config file management for model selection and server lifecycle.

**Architecture:** Linux-only server scripts in root directory. Config file at `~/llm-workspace/.config` stores model selection and server settings. Checkpoint system preserved for idempotent setup. macOS connects as client via direct IP.

**Tech Stack:** Bash, distrobox, llama.cpp (Vulkan), huggingface-cli, shellcheck

## Global Constraints

- All script output in English regardless of input language
- All scripts use `set -e`
- After editing any `.sh` file, run `make validate` (shellcheck) before committing
- Research before implementation - no assumptions about platform behavior
- Scripts live in project root directory
- Config file location: `~/llm-workspace/.config`
- Checkpoint directory: `~/llm-workspace/.cache/checkpoints/`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `setup.sh` | Create | Download/compile (idempotent, checkpointed) |
| `start.sh` | Create | Launch server, wait for health, print connection info |
| `stop.sh` | Create | Kill server process, clean up PID file |
| `test.sh` | Create | curl test + opencode integration test |
| `Makefile` | Modify | Add new targets, remove old setup-dev |
| `setup-dev.sh` | Delete | Replaced by setup.sh |
| `README.md` | Modify | Update for new scripts |
| `QUICK_START.md` | Modify | Update for new scripts |

---

### Task 1: Create setup.sh

**Files:**
- Create: `setup.sh`

**Interfaces:**
- Consumes: `MODEL_ALIAS` env var (optional, overrides interactive prompt)
- Produces: `~/llm-workspace/.config` (config file)

- [ ] **Step 1: Create setup.sh with header and config system**

```bash
#!/usr/bin/env bash
# LLM Environment Setup - Download/compile only, does NOT start server
set -e

WORKSPACE_DIR="${HOME}/llm-workspace"
CACHE_DIR="${WORKSPACE_DIR}/.cache"
CHECKPOINT_DIR="${CACHE_DIR}/checkpoints"
CONFIG_FILE="${WORKSPACE_DIR}/.config"
CONTAINER_NAME="llm-env"
FEDORA_VERSION="44"
CPP_REPO_URL="https://github.com/ggml-org/llama.cpp"

# Model presets: ALIAS|URL|FILENAME|SIZE_BYTES|DESCRIPTION
GEMMA4="gemma4|https://huggingface.co/bartowski/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q4_K_M.gguf|gemma-4-12B-it-Q4_K_M.gguf|7660000000|Gemma 4 12B (dense, multimodal, best for 16GB VRAM)"
ORNITH="ornith|https://huggingface.co/deepreinforce-ai/Ornith-1.0-9B-GGUF/resolve/main/ornith-1.0-9b-Q4_K_M.gguf|ornith-1.0-9b-Q4_K_M.gguf|5600000000|Ornith 1.0 9B (coding specialist, 69.4% SWE-bench)"

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
```

- [ ] **Step 2: Add helper functions**

```bash
# Checkpoint functions
mark_checkpoint() {
    touch "${CHECKPOINT_DIR}/$1"
    echo -e "${GREEN}✓${NC} Checkpoint saved: $1"
}

is_checkpoint_done() {
    [ -f "${CHECKPOINT_DIR}/$1" ]
}

show_status() {
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

human_readable_size() {
    local bytes="$1"
    if command -v numfmt &>/dev/null; then
        numfmt --to=iec-i --suffix=B "$bytes"
    else
        echo "${bytes} bytes"
    fi
}

download_file() {
    local url="$1"
    local output="$2"
    if command -v wget &>/dev/null; then
        wget --continue --progress=bar:force:noscroll "$url" -O "$output"
    else
        curl -L -C - --progress-bar "$url" -o "$output"
    fi
}
```

- [ ] **Step 3: Add model selection and config save**

```bash
select_model() {
    IFS='|' read -r _ GEMMA_URL GEMMA_NAME _ GEMMA_DESC <<< "$GEMMA4"
    IFS='|' read -r _ ORNITH_URL ORNITH_NAME _ ORNITH_DESC <<< "$ORNITH"

    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}Select a model${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  1) Gemma 4 12B     ~7.6 GB  (dense, multimodal, best for 16GB VRAM)"
    echo "  2) Ornith 1.0 9B   ~5.6 GB  (coding specialist, fits 8GB+ VRAM)"
    echo ""

    local vram_gb=0
    if command -v lspci &>/dev/null; then
        vram_gb=$(lspci -v 2>/dev/null | grep -i "memory" | head -1 | grep -oP '\d+ MB' | head -1 | awk '{printf "%d", $1/1024}' || echo "0")
    fi

    if [ "$vram_gb" -ge 16 ]; then
        echo -e "  ${GREEN}>> Recommended for your GPU: 1) Gemma 4 12B${NC}"
    elif [ "$vram_gb" -ge 8 ]; then
        echo -e "  ${GREEN}>> Recommended for your GPU: 2) Ornith 9B${NC}"
    fi
    echo ""

    local choice
    read -rp "  Enter choice [1-2]: " choice

    case "$choice" in
        1) MODEL_ALIAS="gemma4"; MODEL_URL="$GEMMA_URL"; MODEL_NAME="$GEMMA_NAME" ;;
        2) MODEL_ALIAS="ornith"; MODEL_URL="$ORNITH_URL"; MODEL_NAME="$ORNITH_NAME" ;;
        *) echo -e "${YELLOW}Invalid choice. Defaulting to Gemma 4 12B.${NC}"; MODEL_ALIAS="gemma4"; MODEL_URL="$GEMMA_URL"; MODEL_NAME="$GEMMA_NAME" ;;
    esac
}

save_config() {
    mkdir -p "$(dirname "$CONFIG_FILE")"
    cat > "$CONFIG_FILE" << EOF
MODEL_ALIAS=${MODEL_ALIAS}
MODEL_NAME=${MODEL_NAME}
MODEL_URL=${MODEL_URL}
SERVER_PORT=8000
SERVER_HOST=0.0.0.0
EOF
    echo -e "${GREEN}✓${NC} Config saved to ${CONFIG_FILE}"
}
```

- [ ] **Step 4: Add main execution flow (steps 1-4)**

```bash
# Main execution
mkdir -p "${CHECKPOINT_DIR}" "${WORKSPACE_DIR}/models"

# Model selection
if [ -n "${MODEL_ALIAS:-}" ]; then
    case "$MODEL_ALIAS" in
        gemma4) IFS='|' read -r _ MODEL_URL MODEL_NAME _ _ <<< "$GEMMA4" ;;
        ornith) IFS='|' read -r _ MODEL_URL MODEL_NAME _ _ <<< "$ORNITH" ;;
        *) echo "Unknown model: $MODEL_ALIAS"; exit 1 ;;
    esac
else
    select_model
fi
save_config

show_status "LLM Setup"
echo -e "${YELLOW}Model: ${MODEL_NAME}${NC}"
echo ""

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

# STEP 2: Download model
if ! is_checkpoint_done "model_downloaded"; then
    show_status "STEP 2: Downloading ${MODEL_NAME}"
    if [ -f "${WORKSPACE_DIR}/models/${MODEL_NAME}" ]; then
        file_size=$(stat -c%s "${WORKSPACE_DIR}/models/${MODEL_NAME}" 2>/dev/null || echo "0")
        if [ "$file_size" -gt 3000000000 ]; then
            echo -e "${GREEN}✓${NC} Model file already exists ($(human_readable_size "$file_size"))"
            mark_checkpoint "model_downloaded"
            model_download_skipped=true
        else
            echo -e "${YELLOW}Invalid/incomplete file detected. Removing...${NC}"
            rm "${WORKSPACE_DIR}/models/${MODEL_NAME}"
        fi
    fi
    if [ "$model_download_skipped" != "true" ]; then
        echo "Downloading ${MODEL_NAME} from Hugging Face..."
        cd "${WORKSPACE_DIR}/models"
        download_file "${MODEL_URL}" "${MODEL_NAME}"
        mark_checkpoint "model_downloaded"
    fi
else
    show_status "STEP 2: Model Already Downloaded (Skipped)"
fi
```

- [ ] **Step 5: Add build llama.cpp step**

```bash
cd "${WORKSPACE_DIR}"

# STEP 3: Build llama.cpp
if ! is_checkpoint_done "llama_cpp_compiled"; then
    show_status "STEP 3: Building llama.cpp"
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
    show_status "STEP 3: llama.cpp Already Compiled (Skipped)"
fi
```

- [ ] **Step 6: Add test inference step and summary**

```bash
# STEP 4: Run test inference
if ! is_checkpoint_done "test_inference_run"; then
    show_status "STEP 4: Testing Inference"
    distrobox enter "${CONTAINER_NAME}" -- bash -c "
    set -e
    cd llama.cpp
    echo '  -> Running inference test...'
    ./build/bin/llama-cli \
        -m ../models/${MODEL_NAME} \
        -ngl 99 \
        -t \$(nproc) \
        --jinja \
        -p 'Write a short greeting message.' \
        -n 100 \
        --simple-io
    " || exit 1
    mark_checkpoint "test_inference_run"
else
    show_status "STEP 4: Test Inference Already Completed (Skipped)"
fi

# Summary
show_status "Setup Complete!"
echo ""
echo -e "${GREEN}Your LLM environment is ready!${NC}"
echo ""
echo "Quick start commands:"
echo -e "  ${BLUE}Start server:${NC} ./start.sh"
echo -e "  ${BLUE}Stop server:${NC}  ./stop.sh"
echo -e "  ${BLUE}Test server:${NC}  ./test.sh"
echo -e "  ${BLUE}Enter container:${NC} distrobox enter ${CONTAINER_NAME}"
echo ""
echo "Workspace: ${WORKSPACE_DIR}"
echo "Config: ${CONFIG_FILE}"
echo "Model: ${WORKSPACE_DIR}/models/${MODEL_NAME}"
```

- [ ] **Step 7: Make executable and run shellcheck**

```bash
chmod +x setup.sh
make validate
```

- [ ] **Step 8: Commit**

```bash
git add setup.sh
git commit -m "feat: add setup.sh with config file and 12B model presets"
```

---

### Task 2: Create start.sh

**Files:**
- Create: `start.sh`

**Interfaces:**
- Consumes: `~/llm-workspace/.config` (from setup.sh)
- Produces: `~/llm-workspace/.config/server.pid`

- [ ] **Step 1: Create start.sh with full implementation**

```bash
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
```

- [ ] **Step 2: Make executable and run shellcheck**

```bash
chmod +x start.sh
make validate
```

- [ ] **Step 3: Commit**

```bash
git add start.sh
git commit -m "feat: add start.sh for server lifecycle management"
```

---

### Task 3: Create stop.sh

**Files:**
- Create: `stop.sh`

**Interfaces:**
- Consumes: `~/llm-workspace/.config/server.pid`

- [ ] **Step 1: Create stop.sh**

```bash
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
```

- [ ] **Step 2: Make executable and run shellcheck**

```bash
chmod +x stop.sh
make validate
```

- [ ] **Step 3: Commit**

```bash
git add stop.sh
git commit -m "feat: add stop.sh for server shutdown"
```

---

### Task 4: Create test.sh

**Files:**
- Create: `test.sh`

**Interfaces:**
- Consumes: `~/llm-workspace/.config` (for server URL)
- Produces: test results (pass/fail)

- [ ] **Step 1: Create test.sh**

```bash
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
    echo -e "  ${BLUE}Response:${NC} $(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['choices'][0]['message']['content'])" 2>/dev/null || echo "$RESPONSE")"
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
    TMPDIR=$(mktemp -d)
    cat > "${TMPDIR}/opencode.json" << EOF
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
    
    echo "Sending test prompt (requires internet access)..."
    RESULT=$(cd "$TMPDIR" && opencode -p "What is the current temperature in Santiago, Chile? Reply with just the temperature number." 2>&1)
    
    if echo "$RESULT" | grep -qE '[0-9]+.*°|temperature|grados|[0-9]+ C'; then
        echo -e "${GREEN}✓ OpenCode integration test passed${NC}"
        echo -e "  ${BLUE}Response:${NC} $RESULT"
        PASS=$((PASS + 1))
    else
        echo -e "${RED}✗ OpenCode integration test failed${NC}"
        echo -e "  ${YELLOW}Response:${NC} $RESULT"
        FAIL=$((FAIL + 1))
    fi
    
    rm -rf "$TMPDIR"
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
if [ $FAIL -gt 0 ]; then
    echo -e "  ${RED}Failed: ${FAIL}/${TOTAL}${NC}"
    exit 1
fi
```

- [ ] **Step 2: Make executable and run shellcheck**

```bash
chmod +x test.sh
make validate
```

- [ ] **Step 3: Commit**

```bash
git add test.sh
git commit -m "feat: add test.sh for server and agent integration testing"
```

---

### Task 5: Update Makefile

**Files:**
- Modify: `Makefile`

- [ ] **Step 1: Replace Makefile content**

```makefile
.PHONY: help setup start stop test shell clean cache-status validate

CONTAINER_NAME = llm-env
WORKSPACE = $(HOME)/llm-workspace
CHECKPOINTS = $(WORKSPACE)/.cache/checkpoints

help:
	@echo "╔════════════════════════════════════════════════════════════╗"
	@echo "║           LLM Environment - Available Commands             ║"
	@echo "╠════════════════════════════════════════════════════════════╣"
	@echo "║ make setup        Download/compile LLM environment         ║"
	@echo "║ make start        Start LLM server                        ║"
	@echo "║ make stop         Stop LLM server                         ║"
	@echo "║ make test         Run server and agent tests              ║"
	@echo "║ make shell        Enter distrobox container               ║"
	@echo "║ make cache-status Show build cache/checkpoints status     ║"
	@echo "║ make clean-cache  Clear all checkpoints (rebuild next)    ║"
	@echo "║ make clean        Remove container & workspace            ║"
	@echo "║ make validate     Run shellcheck on all .sh files         ║"
	@echo "╚════════════════════════════════════════════════════════════╝"

setup:
	@echo "Starting LLM environment setup..."
	@bash setup.sh

start:
	@bash start.sh

stop:
	@bash stop.sh

test:
	@bash test.sh

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
		echo "  Models: $$(ls -lh $(WORKSPACE)/models 2>/dev/null | tail -n +2 | awk '{print $$9, "(" $$5 ")"}')"; \
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

- [ ] **Step 2: Run shellcheck validation**

```bash
make validate
```

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "refactor: update Makefile for new script structure"
```

---

### Task 6: Remove old setup-dev.sh

**Files:**
- Delete: `setup-dev.sh`

- [ ] **Step 1: Remove old script**

```bash
rm setup-dev.sh
```

- [ ] **Step 2: Commit**

```bash
git add -A setup-dev.sh
git commit -m "chore: remove old setup-dev.sh replaced by setup.sh"
```

---

### Task 7: Update documentation

**Files:**
- Modify: `README.md`
- Modify: `QUICK_START.md`

- [ ] **Step 1: Update README.md**

Replace content with:

```markdown
# LLM Environment

Automated setup for llama.cpp on Bazzite (Linux) with GPU acceleration.

## Quick Start

```bash
make setup      # Download/compile (idempotent, uses checkpoints)
make start      # Start server, print connection info
make test       # Verify server and agent integration
make stop       # Stop server
```

## Model Selection

| Model | Size | Best For |
|-------|------|----------|
| Gemma 4 12B Q4_K_M | ~7.6 GB | Best for 16GB VRAM, multimodal |
| Ornith 1.0 9B Q4_K_M | ~5.6 GB | Coding specialist (69.4% SWE-bench) |

Select model during `make setup`. Config saved to `~/llm-workspace/.config`.

## Remote Access (macOS Client)

Connect directly to Linux IP (no SSH tunnel needed):

```bash
http://<linux-ip>:8000/docs
```

Configure OpenCode to use local server:
```json
{
    "provider": {
        "local": {
            "api_key": "none",
            "models": {
                "local-model": {
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
| `make setup` | Download/compile environment (idempotent) |
| `make start` | Start LLM server |
| `make stop` | Stop LLM server |
| `make test` | Run server and agent tests |
| `make shell` | Enter distrobox container |
| `make cache-status` | Show checkpoint status |
| `make clean-cache` | Clear all checkpoints |
| `make clean` | Remove container & workspace |
| `make validate` | Run shellcheck on all scripts |

## Hardware Requirements

- **GPU**: AMD 9070 XT 16GB VRAM (or equivalent)
- **RAM**: 32GB DDR5
- **Storage**: 2TB NVMe SSD
- **OS**: Bazzite (Fedora-based) with distrobox

## Architecture

- **Linux only** for server (macOS connects as client)
- **Distrobox** container for isolated build environment
- **Vulkan** GPU acceleration for llama.cpp
- **Checkpoint system** for idempotent setup
```

- [ ] **Step 2: Update QUICK_START.md**

Replace content with:

```markdown
# Quick Start

## First-Time Setup

```bash
make setup
```

This will:
1. Prompt you to select a model (Gemma 4 12B or Ornith 1.0 9B)
2. Create a distrobox container
3. Download the selected model
4. Compile llama.cpp with Vulkan support
5. Run a basic inference test

## Start Server

```bash
make start
```

The script will print:
- Local URL: `http://localhost:8000/docs`
- Network URL: `http://<linux-ip>:8000/docs`
- OpenCode configuration

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
                   "local-model": {
                       "endpoint": "http://<linux-ip>:8000/v1/chat/completions"
                   }
               }
           }
       }
   }
   ```

## Test Everything

```bash
make test
```

Tests:
1. Server health check
2. curl inference test
3. OpenCode integration test (requires internet)

## Stop Server

```bash
make stop
```

## Switch Models

Edit `~/llm-workspace/.config`:
```
MODEL_ALIAS=gemma4
MODEL_NAME=gemma-4-12B-it-Q4_K_M.gguf
MODEL_URL=https://huggingface.co/bartowski/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q4_K_M.gguf
SERVER_PORT=8000
SERVER_HOST=0.0.0.0
```

Then restart: `make stop && make start`

## Troubleshooting

- **Server won't start**: Check logs at `~/llm-workspace/.config/server.log`
- **Connection refused**: Ensure server is running with `make start`
- **Out of memory**: Only one model fits in 16GB VRAM at a time
- **Build failed**: Run `make clean-cache && make setup` to rebuild from scratch
```

- [ ] **Step 3: Commit**

```bash
git add README.md QUICK_START.md
git commit -m "docs: update README and QUICK_START for new script structure"
```

---

### Task 8: Final validation

- [ ] **Step 1: Run full validation**

```bash
make validate
```

- [ ] **Step 2: Verify all scripts are executable**

```bash
ls -l *.sh
```

- [ ] **Step 3: Test dry run (optional, requires distrobox)**

```bash
# Only if distrobox container exists
distrobox enter llm-env -- echo "Container works"
```

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-21-llm-env-separate-scripts.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
