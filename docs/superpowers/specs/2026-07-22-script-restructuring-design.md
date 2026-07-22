# LLM Environment Script Restructuring Design

## Overview

Restructure the LLM environment scripts into focused, single-responsibility components with a shared library for model definitions. All behavior derives from the model definitions at the top of `models.sh` — nothing is hardcoded.

## Architecture

```
models.sh      ← Single source of truth (definitions + helpers + config)
setup.sh       ← Download models, build llama.cpp, validate
setup-test.sh  ← Inference test based on downloaded models
start.sh       ← Launch server based on presets.ini
server-test.sh ← Live test forcing internet access
stop.sh        ← Server shutdown (unchanged)
Makefile       ← Updated targets
```

**Workflow:**
```
make all  →  setup → setup-test → start → server-test
```

## `models.sh` — Shared Library

### Structure

```bash
#!/usr/bin/env bash
# models.sh — Single source of truth for LLM environment

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

# ── Functions ──────────────────────────────────────────
# parse_model()     — Sets: MODEL_ALIAS, MODEL_URL, MODEL_NAME, MODEL_SIZE, MODEL_DESC
# check_model()     — Verify file exists and size matches
# download_model()  — Download if not present
# show_status()     — Print section header
# human_readable_size()
# mark_checkpoint()
# is_checkpoint_done()
```

### Key Design Decisions

1. **ALL_MODELS array**: Single line per model, pipe-delimited. Adding a new model = adding one line.
2. **Configurable timeouts**: `SETUP_TEST_TIMEOUT` and `SERVER_TEST_TIMEOUT` at top of file.
3. **Test prompts as arrays**: Easy to add/remove without touching logic.

## `setup.sh` — Download & Build

### Responsibilities
1. Source `models.sh`
2. Show dynamic menu from `ALL_MODELS`
3. Create distrobox container (if needed)
4. Download selected models
5. Build llama.cpp with Vulkan support (if needed)
6. **Validate**: Verify llama.cpp sees the downloaded models match selection
7. Write `presets.ini` with ONLY selected models
8. Write `.config` with selected model aliases

### Validation Step

After build, verify llama.cpp can see the downloaded models:

```bash
# Ask llama.cpp what it can load
LLAMA_OUTPUT=$(distrobox enter "$CONTAINER_NAME" -- bash -c "
  cd '$WORKSPACE_DIR/llama.cpp'
  ./build/bin/llama-cli --list-models 2>&1
")

# Check each selected model appears in output
for model_def in "${SELECTED_MODELS_ARRAY[@]}"; do
    parse_model "$model_def"
    if echo "$LLAMA_OUTPUT" | grep -q "$MODEL_NAME"; then
        echo "✓ $MODEL_DESC found by llama.cpp"
    else
        echo "✗ $MODEL_DESC NOT found by llama.cpp"
        exit 1
    fi
done
```

**Fallback**: If `--list-models` isn't available, check file existence + size in `~/llm-workspace/models/`.

### Presets Generation

Dynamic from model definitions:

```bash
generate_presets() {
    local models="$1"
    cat > "$PRESETS_FILE" << 'EOF'
# LLM Router Mode Presets
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
}
```

## `setup-test.sh` — Inference Test

### Responsibilities
1. Source `models.sh`
2. Discover what models are downloaded (from filesystem)
3. Match against model definitions
4. Run test prompts with 20s timeout each
5. Print results inline with countdown
6. Return exit code for Makefile chaining

### Flow

```bash
#!/usr/bin/env bash
source "$(dirname "$0")/models.sh"

# Discover downloaded models
DOWNLOADED=$(ls "$WORKSPACE_DIR/models/"*.gguf 2>/dev/null | xargs -I{} basename {})

TEST_PASS=0; TEST_FAIL=0

for model_def in "${ALL_MODELS[@]}"; do
    parse_model "$model_def"
    if echo "$DOWNLOADED" | grep -q "$MODEL_NAME"; then
        for prompt in "${SETUP_TEST_PROMPTS[@]}"; do
            # Run with timeout, show countdown
            OUTPUT=$(timeout $SETUP_TEST_TIMEOUT distrobox enter "$CONTAINER_NAME" -- bash -c "
                cd '$WORKSPACE_DIR/llama.cpp'
                ./build/bin/llama-cli \
                    -m '../models/$MODEL_NAME' \
                    -ngl 99 -t \$(nproc) --jinja \
                    -n 50 -no-cnv --simple-io \
                    -p '$prompt' 2>/dev/null
            " 2>&1)
            
            if [ $? -eq 0 ] && echo "$OUTPUT" | grep -qE '[a-zA-Z0-9]'; then
                echo "  ✓ ${prompt}: $(echo "$OUTPUT" | tail -1)"
                TEST_PASS=$((TEST_PASS + 1))
            else
                echo "  ✗ ${prompt}: timeout or empty"
                TEST_FAIL=$((TEST_FAIL + 1))
            fi
        done
    fi
done

echo "Passed: $TEST_PASS/$((TEST_PASS + TEST_FAIL))"
[ $TEST_FAIL -eq 0 ] && exit 0 || exit 1
```

### Countdown Pattern

```bash
echo -n "  Testing: ${prompt} "
# Print [20s], [19s], ... [1s] on same line, overwriting
# When done, print result on same line
```

## `start.sh` — Launch Server

### Responsibilities
1. Source `models.sh` (for config paths)
2. Read `presets.ini` (what setup wrote)
3. Launch `llama-server --models-preset presets.ini`
4. Wait for health check, print connection info

### Key Change

Uses `--models-preset presets.ini` instead of single model `-m` flag. Server discovers available models from the presets file.

## `server-test.sh` — Live Agent Test

### Responsibilities
1. Source `models.sh` (for config paths)
2. Read `presets.ini` to discover available models
3. For each model, send prompts that force internet access
4. Verify response contains real-time data
5. Print results inline, return success/failure

### Flow

```bash
#!/usr/bin/env bash
source "$(dirname "$0")/models.sh"

# Check server is running
if ! curl -s "http://localhost:${SERVER_PORT}/health" > /dev/null 2>&1; then
    echo "✗ Server not running. Start with: ./start.sh"
    exit 1
fi

# Discover models from presets.ini
AVAILABLE_MODELS=$(grep -E '^\[' "$PRESETS_FILE" | tr -d '[]')

TEST_PASS=0; TEST_FAIL=0

for model in $AVAILABLE_MODELS; do
    for prompt in "${SERVER_TEST_PROMPTS[@]}"; do
        RESPONSE=$(timeout $SERVER_TEST_TIMEOUT curl -s "http://localhost:${SERVER_PORT}/v1/chat/completions" \
            -H "Content-Type: application/json" \
            -d "{
                \"model\": \"$model\",
                \"messages\": [{\"role\": \"user\", \"content\": \"$prompt\"}],
                \"max_tokens\": 100
            }" 2>&1)
        
        if [ $? -eq 0 ] && echo "$RESPONSE" | grep -q "choices"; then
            CONTENT=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['choices'][0]['message']['content'])" 2>/dev/null)
            
            # Check for real-time indicators
            if echo "$CONTENT" | grep -qiE "(weather|temperature|degrees|forecast|time|clock|hour|am|pm|[0-9]+°|[0-9]+:[0-9]+)"; then
                echo "  ✓ ${prompt}: $(echo "$CONTENT" | head -c 80)..."
                TEST_PASS=$((TEST_PASS + 1))
            else
                echo "  ✗ ${prompt}: Model didn't access internet"
                TEST_FAIL=$((TEST_FAIL + 1))
            fi
        else
            echo "  ✗ ${prompt}: Request failed"
            TEST_FAIL=$((TEST_FAIL + 1))
        fi
    done
done

echo "Passed: $TEST_PASS/$((TEST_PASS + TEST_FAIL))"
[ $TEST_FAIL -eq 0 ] && exit 0 || exit 1
```

### Real-Time Detection

Checks response for indicators of internet access:
- Weather terms: weather, temperature, degrees, forecast
- Time terms: time, clock, hour, am, pm
- Patterns: numbers with °, time format HH:MM

## Makefile Updates

```makefile
all: setup setup-test start server-test
	@echo "Full setup complete!"

setup:
	@bash setup.sh

setup-test:
	@bash setup-test.sh

start:
	@bash start.sh

server-test:
	@bash server-test.sh

stop:
	@bash stop.sh
```

## Files to Create/Modify

| File | Action |
|------|--------|
| `models.sh` | **Create** — shared library |
| `setup.sh` | **Rewrite** — dynamic from models.sh |
| `setup-test.sh` | **Create** — inference test |
| `start.sh` | **Rewrite** — use presets.ini |
| `server-test.sh` | **Create** — live agent test |
| `test.sh` | **Delete** — replaced by server-test.sh |
| `stop.sh` | **Keep** — no changes |
| `Makefile` | **Update** — new targets |
| `README.md` | **Update** — new workflow |
| `QUICK_START.md` | **Update** — new workflow |
