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
