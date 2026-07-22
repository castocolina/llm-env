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

select_model() {
    IFS='|' read -r _ GEMMA_URL GEMMA_NAME _ _ <<< "$GEMMA4"
    IFS='|' read -r _ ORNITH_URL ORNITH_NAME _ _ <<< "$ORNITH"

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
        vram_gb=$(lspci -v 2>/dev/null | grep -i "memory" | head -1 | grep -oP '\d+ MB' | head -1 | awk '{printf "%d", $1/1024}' || true)
    fi
    vram_gb=${vram_gb:-0}

    if [ "$vram_gb" -ge 16 ]; then
        echo -e "  ${GREEN}>> Recommended for your GPU: 1) Gemma 4 12B${NC}"
    elif [ "$vram_gb" -ge 8 ]; then
        echo -e "  ${GREEN}>> Recommended for your GPU: 2) Ornith 9B${NC}"
    fi
    echo ""

    local choice
    read -rp "  Enter choice [1-2] (default: 1, timeout 10s): " choice

    # Default to gemma4 if no input
    choice=${choice:-1}

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
