#!/usr/bin/env bash
# ==============================================================================
# LLM Environment Setup Script - Bazzite/Linux + macOS
# Features: Model selection, Docker-like cache/checkpoints, optimized compilation
# ==============================================================================
set -e

CONTAINER_NAME="llm-env"
FEDORA_VERSION="44"
CPP_REPO_URL="https://github.com/ggml-org/llama.cpp"
WORKSPACE_DIR="${HOME}/llm-workspace"
CACHE_DIR="${WORKSPACE_DIR}/.cache"
CHECKPOINT_DIR="${CACHE_DIR}/checkpoints"

# Color codes for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Cross-platform helpers
detect_os() {
    case "$(uname -s)" in
        Linux*)  echo "linux" ;;
        Darwin*) echo "macos" ;;
        *)       echo "unknown" ;;
    esac
}

get_file_size() {
    local file="$1"
    if [ "$(detect_os)" = "macos" ]; then
        stat -f%z "$file"
    else
        stat -c%s "$file"
    fi
}

get_cpu_cores() {
    if [ "$(detect_os)" = "macos" ]; then
        sysctl -n hw.ncpu
    else
        nproc
    fi
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

# Model presets: URL|FILENAME|DESCRIPTION|SIZE_BYTES
GEMMA4_26B="https://huggingface.co/ggml-org/gemma-4-26B-A4B-it-GGUF/resolve/main/gemma-4-26B-A4B-it-Q4_K_M.gguf|gemma-4-26B-A4B-it-Q4_K_M.gguf|Gemma 4 26B-A4B (MoE, best quality)|17616000000"
ORNITH_9B="https://huggingface.co/deepreinforce-ai/Ornith-1.0-9B-GGUF/resolve/main/ornith-1.0-9b-Q4_K_M.gguf|ornith-1.0-9b-Q4_K_M.gguf|Ornith 1.0 9B (coding specialist)|5900000000"

# Model selection
select_model() {
    IFS='|' read -r GEMMA_URL GEMMA_NAME _ _ <<< "$GEMMA4_26B"
    IFS='|' read -r ORNITH_URL ORNITH_NAME _ _ <<< "$ORNITH_9B"

    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}Select a model${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  1) Gemma 4 26B-A4B   ~17 GB  (MoE, best quality, needs 16GB+ VRAM)"
    echo "  2) Ornith 1.0 9B     ~6 GB   (coding specialist, fits 8GB+ VRAM)"
    echo ""

    # Detect VRAM for recommendation
    local vram_gb=0
    if command -v lspci &>/dev/null; then
        vram_gb=$(lspci -v 2>/dev/null | grep -i "memory" | head -1 | grep -oP '\d+ MB' | head -1 | awk '{printf "%d", $1/1024}' || echo "0")
    fi

    if [ "$vram_gb" -ge 16 ]; then
        echo -e "  ${GREEN}>> Recommended for your GPU: 1) Gemma 4 26B${NC}"
    elif [ "$vram_gb" -ge 8 ]; then
        echo -e "  ${GREEN}>> Recommended for your GPU: 2) Ornith 9B${NC}"
    fi
    echo ""

    local choice
    read -rp "  Enter choice [1-2]: " choice

    case "$choice" in
        1)
            MODEL_URL="$GEMMA_URL"
            MODEL_NAME="$GEMMA_NAME"
            JINJA_FLAG="--jinja"
            ;;
        2)
            MODEL_URL="$ORNITH_URL"
            MODEL_NAME="$ORNITH_NAME"
            JINJA_FLAG="--jinja"
            ;;
        *)
            echo -e "${YELLOW}Invalid choice. Defaulting to Ornith 9B.${NC}"
            MODEL_URL="$ORNITH_URL"
            MODEL_NAME="$ORNITH_NAME"
            JINJA_FLAG="--jinja"
            ;;
    esac
}

# Allow env var override (non-interactive)
if [ -n "${MODEL_URL:-}" ] && [ -n "${MODEL_NAME:-}" ]; then
    JINJA_FLAG="--jinja"
else
    select_model
fi

OS="$(detect_os)"

# Initialize cache and checkpoint system
mkdir -p "${CHECKPOINT_DIR}" "${WORKSPACE_DIR}/models"

# Checkpoint functions (Docker-like caching)
mark_checkpoint() {
    local checkpoint_name="$1"
    touch "${CHECKPOINT_DIR}/${checkpoint_name}"
    echo -e "${GREEN}✓${NC} Checkpoint saved: ${checkpoint_name}"
}

is_checkpoint_done() {
    [ -f "${CHECKPOINT_DIR}/$1" ]
}

show_status() {
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# Main execution
show_status "LLM Setup - ${OS}"
echo -e "${YELLOW}Model: ${MODEL_NAME}${NC}"
echo ""

# STEP 1: Create or verify Distrobox container (Linux only)
if [ "$OS" = "linux" ]; then
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
else
    show_status "STEP 1: macOS Detected (Using Native Build)"
fi

# STEP 2: Download model
if ! is_checkpoint_done "model_downloaded"; then
    show_status "STEP 2: Downloading ${MODEL_NAME}"

    if [ -f "${WORKSPACE_DIR}/models/${MODEL_NAME}" ]; then
        file_size=$(get_file_size "${WORKSPACE_DIR}/models/${MODEL_NAME}")
        if [ "$file_size" -lt 3000000000 ]; then
            echo -e "${YELLOW}Invalid/incomplete file detected. Removing...${NC}"
            rm "${WORKSPACE_DIR}/models/${MODEL_NAME}"
        else
            echo -e "${GREEN}✓${NC} Model file already exists ($(human_readable_size "$file_size"))"
            mark_checkpoint "model_downloaded"
            model_download_skipped=true
        fi
    fi

    if [ "$model_download_skipped" != "true" ]; then
        echo "Downloading ${MODEL_NAME} from Hugging Face..."
        echo "This may take several minutes depending on connection speed..."
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

    if [ "$OS" = "linux" ]; then
        CPU_CORES=$(get_cpu_cores)
        distrobox enter "${CONTAINER_NAME}" -- bash -c "
        set -e

        export WORK_DIR='${WORKSPACE_DIR}'
        export CPU_CORES=${CPU_CORES}

        echo '  -> Updating package repositories...'
        sudo dnf5 clean all 2>/dev/null || true
        sudo dnf5 makecache 2>/dev/null || true

        echo '  -> Installing build toolchain & Vulkan support...'
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

        cmake -B build \\
            -DGGML_VULKAN=ON \\
            -DCMAKE_BUILD_TYPE=Release \\
            -DCMAKE_C_FLAGS=\"\${CFLAGS}\" \\
            -DCMAKE_CXX_FLAGS=\"\${CXXFLAGS}\" \\
            -DBUILD_SHARED_LIBS=ON

        echo \"  -> Compiling (using \${CPU_CORES} cores)...\"
        cmake --build build --config Release -j\${CPU_CORES} --verbose

        echo '  -> Build successful! Binaries in: ./build/bin/'
        find build/bin/ -maxdepth 1 -type f \( -name '*llama*' -o -name '*server*' \) -exec ls -lh {} \; || true
        " || exit 1
    else
        # macOS: native build with Metal
        show_status "Installing macOS dependencies via Homebrew..."
        if ! command -v brew &>/dev/null; then
            echo "Homebrew not found. Install from https://brew.sh"
            exit 1
        fi
        brew install cmake git

        echo "  -> Cloning/updating llama.cpp repository..."
        if [ ! -d 'llama.cpp' ]; then
            git clone "${CPP_REPO_URL}" --depth=1
        else
            cd llama.cpp && git pull --rebase || true && cd ..
        fi

        cd llama.cpp

        echo "  -> Configuring CMake with Metal + optimization flags..."
        cmake -B build \
            -DGGML_METAL=ON \
            -DCMAKE_BUILD_TYPE=Release \
            -DBUILD_SHARED_LIBS=ON

        CPU_CORES=$(get_cpu_cores)
        echo "  -> Compiling (using ${CPU_CORES} cores)..."
        cmake --build build --config Release -j"${CPU_CORES}" --verbose

        echo "  -> Build successful! Binaries in: ./build/bin/"
        find build/bin/ -maxdepth 1 -type f \( -name '*llama*' -o -name '*server*' \) -exec ls -lh {} \; || true
    fi

    mark_checkpoint "llama_cpp_compiled"
else
    show_status "STEP 3: llama.cpp Already Compiled (Skipped)"
fi

# STEP 4: Run test inference
if ! is_checkpoint_done "test_inference_run"; then
    show_status "STEP 4: Testing Inference"

    if [ "$OS" = "linux" ]; then
        distrobox enter "${CONTAINER_NAME}" -- bash -c "
        set -e
        cd llama.cpp

        echo '  -> Running inference test with your LLM model...'
        echo '  -> Using GPU offload (all layers to VRAM)...'
        ./build/bin/llama-cli \\
            -m ../models/${MODEL_NAME} \\
            -ngl 99 \\
            -t \$(nproc) \\
            ${JINJA_FLAG} \\
            -p 'Write a short greeting message.' \\
            -n 100 \\
            --simple-io
        " || exit 1
    else
        cd llama.cpp
        echo "  -> Running inference test with your LLM model..."
        echo "  -> Using Metal GPU acceleration..."
        ./build/bin/llama-cli \
            -m "../models/${MODEL_NAME}" \
            -ngl 99 \
            -t "$(get_cpu_cores)" \
            "${JINJA_FLAG}" \
            -p 'Write a short greeting message.' \
            -n 100 \
            --simple-io
        cd "${WORKSPACE_DIR}"
    fi

    mark_checkpoint "test_inference_run"
else
    show_status "STEP 4: Test Inference Already Completed (Skipped)"
fi

# Final summary
show_status "Setup Complete!"
echo ""
echo -e "${GREEN}Your LLM environment is ready!${NC}"
echo ""
echo "Quick start commands:"
echo -e "  ${BLUE}Enter container:${NC} distrobox enter ${CONTAINER_NAME}"
echo -e "  ${BLUE}Run inference:${NC} cd ${WORKSPACE_DIR}/llama.cpp && ./build/bin/llama-cli -m ../models/${MODEL_NAME} -ngl 99 ${JINJA_FLAG} -p 'Your prompt here' -n 256"
echo -e "  ${BLUE}Start server:${NC} ./build/bin/llama-server -m ../models/${MODEL_NAME} -ngl 99 ${JINJA_FLAG} --host 0.0.0.0 --port 8000"
echo ""
echo "Workspace: ${WORKSPACE_DIR}"
echo "Model: ${WORKSPACE_DIR}/models/${MODEL_NAME}"
echo "Checkpoints: ${CHECKPOINT_DIR}"
echo ""
echo -e "${YELLOW}Tip:${NC} Run this script again to resume from the last checkpoint if interrupted!"
echo ""
