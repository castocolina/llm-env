#!/usr/bin/env bash
# LLM Environment Setup - Download/compile only, does NOT start server
# Router mode: downloads both models, generates presets.ini
set -e

WORKSPACE_DIR="${HOME}/llm-workspace"
CACHE_DIR="${WORKSPACE_DIR}/.cache"
CHECKPOINT_DIR="${CACHE_DIR}/checkpoints"
CONFIG_FILE="${WORKSPACE_DIR}/.config"
PRESETS_FILE="${WORKSPACE_DIR}/presets.ini"
CONTAINER_NAME="llm-env"
FEDORA_VERSION="44"
CPP_REPO_URL="https://github.com/ggml-org/llama.cpp"

# Model definitions: ALIAS|URL|FILENAME|SIZE_BYTES
GEMMA4="gemma4|https://huggingface.co/bartowski/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q4_K_M.gguf|gemma-4-12B-it-Q4_K_M.gguf|7660000000"
ORNITH="ornith|https://huggingface.co/deepreinforce-ai/Ornith-1.0-9B-GGUF/resolve/main/ornith-1.0-9b-Q4_K_M.gguf|ornith-1.0-9b-Q4_K_M.gguf|5600000000"

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
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

# Parse model definition
parse_model() {
    local def="$1"
    IFS='|' read -r _ MODEL_URL MODEL_NAME MODEL_SIZE <<< "$def"
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
        echo -e "${GREEN}✓${NC} ${MODEL_NAME} already exists ($(human_readable_size "$MODEL_SIZE"))"
        return 0
    fi

    echo "Downloading ${MODEL_NAME}..."
    cd "${WORKSPACE_DIR}/models"
    download_file "${MODEL_URL}" "${MODEL_NAME}"
}

# Generate presets.ini
generate_presets() {
    mkdir -p "$(dirname "$PRESETS_FILE")"
    cat > "$PRESETS_FILE" << 'EOF'
# LLM Router Mode Presets
# Clients select model via "model" field in API requests.
# Both models fit in 16GB VRAM simultaneously.

[gemma4]
model = ~/llm-workspace/models/gemma-4-12B-it-Q4_K_M.gguf
n-gpu-layers = 99
ctx-size = 8192
jinja = true

[ornith]
model = ~/llm-workspace/models/ornith-1.0-9b-Q4_K_M.gguf
n-gpu-layers = 99
ctx-size = 8192
jinja = true
EOF
    echo -e "${GREEN}✓${NC} Presets saved to ${PRESETS_FILE}"
}

# Save config
save_config() {
    mkdir -p "$(dirname "$CONFIG_FILE")"
    cat > "$CONFIG_FILE" << EOF
MODELS=gemma4,ornith
SERVER_PORT=8000
SERVER_HOST=0.0.0.0
MODELS_MAX=2
EOF
    echo -e "${GREEN}✓${NC} Config saved to ${CONFIG_FILE}"
}

# Main execution
mkdir -p "${CHECKPOINT_DIR}" "${WORKSPACE_DIR}/models"

show_status "LLM Setup - Router Mode"
echo -e "${YELLOW}Models: Gemma4 12B + Ornith 9B${NC}"
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

# STEP 2: Download models
if ! is_checkpoint_done "models_downloaded"; then
    show_status "STEP 2: Downloading Models"
    download_model "$GEMMA4"
    download_model "$ORNITH"
    mark_checkpoint "models_downloaded"
else
    show_status "STEP 2: Models Already Downloaded (Skipped)"
fi

# STEP 3: Generate presets and config
show_status "STEP 3: Generating Config"
generate_presets
save_config

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

# STEP 5: Run test inference
if ! is_checkpoint_done "test_inference_run"; then
    show_status "STEP 5: Testing Inference"
    distrobox enter "${CONTAINER_NAME}" -- bash -c "
    set -e
    cd llama.cpp
    echo '  -> Testing Gemma4...'
    ./build/bin/llama-cli \
        -m ../models/gemma-4-12B-it-Q4_K_M.gguf \
        -ngl 99 \
        -t \$(nproc) \
        --jinja \
        -p 'Say hello in 5 words.' \
        -n 50 \
        --simple-io
    echo '  -> Testing Ornith...'
    ./build/bin/llama-cli \
        -m ../models/ornith-1.0-9b-Q4_K_M.gguf \
        -ngl 99 \
        -t \$(nproc) \
        --jinja \
        -p 'Say hello in 5 words.' \
        -n 50 \
        --simple-io
    " || exit 1
    mark_checkpoint "test_inference_run"
else
    show_status "STEP 5: Test Inference Already Completed (Skipped)"
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
echo "Router mode:"
echo -e "  ${BLUE}Gemma4:${NC} curl -d '{\"model\":\"gemma4\",...}' http://localhost:8000/v1/chat/completions"
echo -e "  ${BLUE}Ornith:${NC} curl -d '{\"model\":\"ornith\",...}' http://localhost:8000/v1/chat/completions"
echo ""
echo "Workspace: ${WORKSPACE_DIR}"
echo "Presets: ${PRESETS_FILE}"
