#!/usr/bin/env bash
# LLM Environment Setup - Download/compile only, does NOT start server
# Interactive model selection, non-blocking tests with timeout
set -e

WORKSPACE_DIR="${HOME}/llm-workspace"
CACHE_DIR="${WORKSPACE_DIR}/.cache"
CHECKPOINT_DIR="${CACHE_DIR}/checkpoints"
CONFIG_FILE="${WORKSPACE_DIR}/.config"
PRESETS_FILE="${WORKSPACE_DIR}/presets.ini"
CONTAINER_NAME="llm-env"
FEDORA_VERSION="44"
CPP_REPO_URL="https://github.com/ggml-org/llama.cpp"

# Model definitions: ALIAS|URL|FILENAME|SIZE_BYTES|DESCRIPTION
GEMMA4="gemma4|https://huggingface.co/bartowski/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q4_K_M.gguf|gemma-4-12B-it-Q4_K_M.gguf|7660000000|Gemma 4 12B"
ORNITH="ornith|https://huggingface.co/deepreinforce-ai/Ornith-1.0-9B-GGUF/resolve/main/ornith-1.0-9b-Q4_K_M.gguf|ornith-1.0-9b-Q4_K_M.gguf|5600000000|Ornith 1.0 9B"

# All model definitions for iteration
ALL_MODELS=("$GEMMA4" "$ORNITH")

# Test prompts (multiple to verify model works)
TEST_PROMPTS=("Say hello in 5 words." "What is 2+2? Reply with just the number." "Write a one-line Python hello world.")

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

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

# Parse model definition into global variables
# Usage: parse_model "$GEMMA4"
# Sets: MODEL_ALIAS, MODEL_URL, MODEL_NAME, MODEL_SIZE, MODEL_DESC
parse_model() {
    local def="$1"
    IFS='|' read -r MODEL_ALIAS MODEL_URL MODEL_NAME MODEL_SIZE MODEL_DESC <<< "$def"
}

# Check if model file exists and is valid
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
download_model() {
    local def="$1"
    parse_model "$def"

    if check_model "$MODEL_NAME" "$MODEL_SIZE"; then
        echo -e "${GREEN}✓${NC} ${MODEL_DESC} already exists ($(human_readable_size "$MODEL_SIZE"))"
        return 0
    fi

    echo "Downloading ${MODEL_DESC}..."
    cd "${WORKSPACE_DIR}/models"
    download_file "${MODEL_URL}" "${MODEL_NAME}"
}

# Generate presets.ini based on selected models
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

# Save config
save_config() {
    local models="$1"
    mkdir -p "$(dirname "$CONFIG_FILE")"
    cat > "$CONFIG_FILE" << EOF
MODELS=${models}
SERVER_PORT=8000
SERVER_HOST=0.0.0.0
MODELS_MAX=2
EOF
    echo -e "${GREEN}✓${NC} Config saved to ${CONFIG_FILE}"
}

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

# Test a single model with multiple prompts and timeout
test_model() {
    local def="$1"
    parse_model "$def"

    echo -e "  ${BLUE}Testing ${MODEL_DESC}...${NC}"

    local all_passed=true
    for prompt in "${TEST_PROMPTS[@]}"; do
        echo -e "    Prompt: ${YELLOW}${prompt}${NC}"
        local output
        output=$(timeout 60 distrobox enter "${CONTAINER_NAME}" -- bash -c "
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
            echo -e "    ${RED}✗ Timeout or error${NC}"
            all_passed=false
            continue
        }

        if [ -n "$output" ] && echo "$output" | grep -qE '[a-zA-Z0-9]'; then
            local response
            response=$(echo "$output" | tail -1)
            echo -e "    ${GREEN}✓ Response:${NC} ${response}"
        else
            echo -e "    ${RED}✗ Empty response${NC}"
            all_passed=false
        fi
    done

    if $all_passed; then
        echo -e "  ${GREEN}✓ ${MODEL_DESC} passed all tests${NC}"
        return 0
    else
        echo -e "  ${YELLOW}⚠ ${MODEL_DESC} had some failures${NC}"
        return 1
    fi
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

cd "${WORKSPACE_DIR}"

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

# STEP 5: Run test inference (dynamic, non-blocking, with timeout)
if ! is_checkpoint_done "test_inference_run"; then
    show_status "STEP 5: Testing Inference"

    TEST_PASS=0
    TEST_FAIL=0

    for model_def in "${ALL_MODELS[@]}"; do
        parse_model "$model_def"
        if echo "$SELECTED_MODELS" | grep -q "$MODEL_ALIAS"; then
            if test_model "$model_def"; then
                TEST_PASS=$((TEST_PASS + 1))
            else
                TEST_FAIL=$((TEST_FAIL + 1))
            fi
        fi
    done

    if [ $TEST_FAIL -eq 0 ]; then
        mark_checkpoint "test_inference_run"
    else
        echo -e "${YELLOW}Some tests failed. Checkpoint not saved. Re-run to retry.${NC}"
    fi
else
    show_status "STEP 5: Test Inference Already Completed (Skipped)"
fi

# Summary
show_status "Setup Complete!"
echo ""
echo -e "${GREEN}Your LLM environment is ready!${NC}"
echo -e "${GREEN}Selected models: ${SELECTED_MODELS}${NC}"
echo ""
echo "Quick start commands:"
echo -e "  ${BLUE}Start server:${NC} ./start.sh"
echo -e "  ${BLUE}Stop server:${NC}  ./stop.sh"
echo -e "  ${BLUE}Test server:${NC}  ./test.sh"
echo -e "  ${BLUE}Enter container:${NC} distrobox enter ${CONTAINER_NAME}"
echo ""
echo "Router mode:"
for model_def in "${ALL_MODELS[@]}"; do
    parse_model "$model_def"
    echo -e "  ${BLUE}${MODEL_ALIAS}:${NC} curl -d '{\"model\":\"${MODEL_ALIAS}\",...}' http://localhost:8000/v1/chat/completions"
done
echo ""
echo "Workspace: ${WORKSPACE_DIR}"
echo "Presets: ${PRESETS_FILE}"
