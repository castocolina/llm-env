# Setup and Runtime Lifecycle Design

## Goal

Make `make setup` prepare a local model environment without implying that a server is running. Keep credentials, LAN exposure, and service lifecycle in runtime commands. Add a pre-start GPU inference smoke test for every enabled model.

## Scope

This change covers:

- A confirmed host-prerequisite installation flow
- Numbered GPU and model selection in `make setup`
- Removal of OpenHermes from the template and generated configuration
- Vulkan image preparation and device-name resolution during setup
- Offline model inference in `make check-setup`
- API-key creation in `make start` and a `make key-reset` target
- LAN exposure after a server becomes healthy
- Boot enablement that does not inspect VRAM while a model is loaded
- Documentation and automated tests for the new lifecycle

This change does not add Ollama model import, partial CPU/GPU offload, dynamic model loading, or macOS support.

## Host Prerequisites

`make prerequisites` delegates to a shell script that examines the host before setup changes user configuration.

It reports three groups:

- Required runtime tools: `bash`, `make`, `uv`, `jq`, Mike Farah `yq`, `podman`, `curl`, `systemctl`, and `ip`
- Development tools: `git`, `shellcheck`, and the tooling that `make validate` and `make test` invoke
- Optional LAN tools: `firewall-cmd` and `avahi-publish`

For each missing tool, it prints the tool name, why the project needs it, and the package that supplies it on the supported host platform. It does not install anything until the user confirms a single prompt that lists every proposed host change. If installation requires an image deployment or reboot, it states that before confirmation and reports the required next action after installation.

The installer must verify that an installed `yq` is Mike Farah v4, because the scripts use its syntax. A similarly named incompatible package does not satisfy the prerequisite. `uv` continues to manage Python and Python dependencies; users do not manually create a project virtual environment.

`make setup` runs the detector first and stops with a clear instruction to run `make prerequisites` when a required runtime tool is missing. It never attempts an implicit host-package installation.

## Configuration

`models.yml.example` remains the committed source template. `make setup` copies it to `~/.config/llm-env/models.yml` when the generated configuration is absent.

The template contains only Gemma4 and Ornith. Each model definition includes its display label, parameter count, quantization, URL, filename, size, and runtime settings. Setup removes the retired OpenHermes entry if it exists in a generated configuration. Model definitions remain in YAML; scripts do not contain model-specific URLs, filenames, labels, or menus.

The configuration persists the selected GPU PCI address and the resolved llama.cpp device name. It never persists a Vulkan index. Runtime commands resolve the device name again against the current `--list-devices` output.

## Setup Workflow

`make setup` has six steps:

1. Create or load the generated configuration.
2. Detect GPUs. Display each GPU with a numbered choice, a colour-distinguished `cardN`, PCI address, VRAM, render node, and connected displays. Select the GPU with the most VRAM by default. The user enters a number, not a PCI address. Setup stores the selected PCI address.
3. Display Gemma4 and Ornith as numbered rows with parameter count, quantization, and download size. The user enters a comma-separated set of model numbers. Setup replaces the enabled set with that selection and synchronizes `runtime.models_max`.
4. Download missing enabled GGUF files and validate their headers.
5. Pull `ghcr.io/ggml-org/llama.cpp:server-vulkan`, list its devices, and resolve the selected GPU to a llama.cpp device name. Setup matches the selected GPU's measured VRAM total to the listed device totals. One exact match records its name. Zero or multiple matches present a numbered llama.cpp-device choice; setup stops if no selection is made. It never guesses or persists an index.
6. Calculate and report the full-GPU VRAM budget.

Setup does not create or rotate an API key, alter firewall rules, publish mDNS, start a service, or print API usage examples.

## Offline Setup Check

`make check-setup` keeps its static tool, configuration, GPU, image, GGUF, and budget checks. After those checks, it runs one disposable GPU-pinned container per enabled model:

```bash
podman run --rm --device /dev/dri \
  -v "$MODELS_DIR:/models:ro,z" \
  --entrypoint /app/llama "$IMAGE" cli \
  -m "/models/$FILE" --device "$RESOLVED_DEVICE" \
  --n-gpu-layers "$N_GPU_LAYERS" -p "Reply with exactly: ready"
```

The command uses a bounded timeout. A successful process with non-empty model output passes. The check does not test a live fact, such as current weather, because local models do not have web access. It does not bind a port, create a service, require an API key, or require mDNS.

The image is Vulkan before benchmark. A failed Vulkan benchmark configures CPU fallback for the persistent service and exits nonzero.

## Runtime Workflow

`make benchmark` measures Vulkan throughput. It selects the Vulkan image on success; on failure, it configures CPU fallback and exits nonzero.

`make start` performs these actions in order:

1. Require a valid configuration and enabled models.
2. Create a random API key only when the configured key is empty. It writes the configuration with mode `0600` and never prints the secret.
3. Check the full-GPU VRAM budget.
4. Resolve the saved llama.cpp device name and generate presets.
5. Render and start the Quadlet service.
6. Wait for the local health endpoint.
7. Run LAN exposure: ask for firewall consent only when the port is not already open, then enable the mDNS publisher. Print local, LAN-IP, and `.local` examples only after health succeeds.

`make key-reset` delegates to a shell script. It creates a new API key, protects the configuration file, and restarts an active service. When the service is inactive, it only updates the configuration.

`.local` depends on mDNS support on the client machine. Clients without mDNS use the printed LAN IP address.

## Boot Lifecycle

`make enable-boot` updates `server.start_at_boot` and renders the Quadlet `[Install]` section without starting the server or checking the live VRAM budget. `make start` and `make enable-boot` share a render-only unit helper. This prevents a running model's own VRAM allocation from being mistaken for compositor usage.

## Tests

Add or update tests for:

- Prerequisite grouping, incompatible `yq` detection, and no installation without confirmation
- GPU number-to-PCI selection and unambiguous device-name resolution
- Comma-separated model-number selection, enabled-set replacement, and OpenHermes removal
- Setup without API-key or network changes
- `check-setup` disposable inference command construction for every enabled model
- `make start` creating a missing key without printing it
- `make key-reset` restarting only an active service
- `make enable-boot` using the render-only path rather than `start.sh`

Run `make validate` and `make test` after implementation. The end-to-end sequence is:

```bash
make setup
make check-setup
make benchmark
make start
make check-server
make stop
```
