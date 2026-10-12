# System One (Laya) Provisioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Execution order (paired plan):** Decision-skill Tasks 5–8 start once System One is enabled and healthy on port 8010 (`system_one.enabled: true`). Plan B needs System One enabled and healthy on 8010. Migration may keep a custom `system_one.port`; both plans assume 8010. Task 10 (live E2E) stops with a clear message when the port is not 8010; its regression gate (idle CPU < 5% of one core, decision p50 ≤ 20 ms) must pass on that container. Decision-skill Task 6 copies decision-skill Task 5’s `notes/eval-results.json`. Decision-skill Tasks 0–4 do not need Laya.

**Goal:** Add an opt-in, ROCm-backed local System One server (Laya, Jev-compatible `POST /v1/systemone`) to the llm-env compose stack, gated by `system_one.enabled` exactly the way `llm_server.enabled` gates llama.cpp.

**Architecture:** Compose service `system-one`, rendered by `pylib/compose.py` only when `system_one.enabled` is true. Laya ships no image (upstream compose is NVIDIA/CPU only), so `scripts/system-one-build.sh` builds `docker/system-one/Dockerfile` during `make setup` and via `make system-one-build`. Setup also prefetches weights; `make start` only starts what setup prepared. setup, start, check-setup, check-server, print-endpoints, gpu-status, clean, resources, and the VRAM budget all read the flag.

**Tech Stack:** bash + `yq`/`jq`/`podman compose` (orchestration), Python 3 via `uv run llmenv.py` (rendering, budgeting), PyTorch 2.14.1 from the `rocm7.2` index (HIP 7.2.53211, ROCR 1.18), `laya[serve]==0.4.2`, pytest.

**Spec:** No separate spec. Design facts measured live on this host on 2026-10-10 (RX 9070 XT, 16 GiB; Bazzite; podman rootless):

| Measured | Value |
|---|---|
| Image size | rocm7.14 image 18.2 GB. rocm7.2 image 16.2 GB, of which ~7 GB are per-GPU-arch kernel files (paths containing `gfx…` under `site-packages/torch` and `triton`: hipblaslt/library, rocblas/library, hipsparselt, miopen db, aotriton.images). Deleting every such file whose path does not contain `gfx1201`, `gfx120x`, or `gfx12-generic` removed 6646 MB; inference stayed identical (same probabilities, 12.2 ms p50, 0% idle, no CPU fallback). Expected image ~9.5 GB. |
| Runtime libs missing from `python:3.11-slim-trixie` | `libatomic1` (hard ImportError), `libnuma1`/`libdrm2` (warnings) |
| GPU pin (VERIFIED LIVE 2026-10-11) | `--device /dev/kfd --device /dev/dri/renderD128` → torch `No CUDA GPUs are available`. Working pin (2026-10-11 live `podman run`, corroborated by the run comment in `docker/laya-rocm/Dockerfile`: `--device /dev/kfd --device /dev/dri --group-add keep-groups --security-opt label=disable`): `/dev/kfd` and `/dev/dri:/dev/dri` plus `ROCR_VISIBLE_DEVICES=<index>`. llm-server only maps `/dev/dri:/dev/dri` (`pylib/compose.py:80`) and has no `/dev/kfd`. Index = position of the KFD node whose `properties` has `drm_render_minor <minor>`, among `/sys/class/kfd/kfd/topology/nodes/` with `gpu_id` != 0, in node-number order. This host: renderD128 → 0 (RX 9070 XT), renderD129 → 1 (iGPU). Computed in pylib (`rocr_visible_index`), not shell. |
| Health | `GET /health` → `{"status":"ok","device":"cuda",...}` (ROCm torch reports HIP as `cuda`) |
| Latency (warm, 1 question) | ~12 ms; 10 questions/request ~19 ms; 8 concurrent ~84 req/s |
| VRAM, all 3 checkpoints | ~5.8 GiB (2026-10-10). `LAYA_MODELS=english` on the rocm7.2 image: ~2992 MiB (2026-10-11, RX 9070 XT, `ROCR_VISIBLE_DEVICES=0`). `english,multilingual`: ~4.26 GB steady (2026-10-12, same GPU; dGPU ~4442 MiB used, baseline ~183 MiB). Default is both; `vram_reserve_mib` default is 5120. `english` alone saves ~200 MiB but non-English and short/accented English inputs are routed to `multilingual` and return HTTP 500 under `HF_HUB_OFFLINE=1`. |
| RAM (RSS) | ~2.39 GB steady (2026-10-12); memory.peak 3155 MiB under load. Idle CPU returns to 0% immediately. |
| Idle CPU | Not Laya. torch `2.14.1+rocm7.14` bundles ROCR 1.21; `rocr::core::Runtime::AsyncEventsLoop` busy-spins one core after the first GPU kernel (gdb backtrace on the busy thread; `import torch` / `cuda.init()` = 0%, first GPU `x*2` = 99%). None of `HSA_ENABLE_INTERRUPT=0/1`, `HSA_ENABLE_SDMA=0`, `AMD_DIRECT_DISPATCH=0`, `GPU_MAX_HW_QUEUES=1`, `HSA_ENABLE_SCRATCH_ASYNC_RECLAIM=0`, `HIP_HOST_COHERENT=0`, `HIP_FORCE_DEV_KERNARG=1` helps. Upstream: https://github.com/ROCm/rocm-systems/issues/12674 , https://github.com/ROCm/TheRock/issues/7051 (fixed on rocm-systems main by PR #7898, not in any 7.14 release). Fix, measured 2026-10-11: `torch==2.14.1` from `https://download.pytorch.org/whl/rocm7.2` (HIP 7.2.53211, ROCR 1.18) — idle 0% (three 10 s samples after inference), decision p50 12.3 ms / max 15 ms (20 requests), same probabilities as rocm7.14. Laya CPU mode was 108 ms p50 at 0% idle — not chosen; fallback only if a future ROCm regresses. Regression gate is Task 10 (no env-var hunting). |
| Decisions (english, one request each) | Shape `{"model":"jev-1","state":{...},"questions":{"<id>":{...}}}`. choice billing p 0.98 (charged twice); choice technical p 0.95 (settings crash); noul 0.77 (delete production database); noul 0.18 (rename foo→bar); score argmax `"2"` p 0.99 (site down). Bodies are Task 8. Never batch questions — Laya reads the whole state per question. |
| Env var that limits preloaded checkpoints | `LAYA_MODELS` (comma list). `LAYA_MODEL` does **not** limit preload |
| Response `model` field | `laya-rl-agent` |

## Global Constraints

- `system_one.enabled` defaults to `false`; every shell read is `yq -r '.system_one.enabled'` + string compare — never `// true`/`// false` (yq's `//` treats `false` as falsy; see `scripts/start.sh:16-20`).
- Image tag `localhost/llm-env-system-one:rocm`; Dockerfile pins `torch==2.14.1` from `https://download.pytorch.org/whl/rocm7.2` (`ARG TORCH_INDEX=rocm7.2`) and `laya[serve]==0.4.2` plus `huggingface_hub==2.2.0` (the weights check imports it). `ARG GPU_ARCH` has no default; the pip-install `RUN` starts with `test -n "${GPU_ARCH}"`. `scripts/system-one-build.sh` passes `--label llm-env.gpu-arch=<arch>` and `--build-arg GPU_ARCH=<arch>` from KFD `gfx_target_version` via `gfx_arch`; if the arch cannot be read the build dies (no default). The image then deletes per-arch kernel files under `site-packages/torch` and `triton` whose path does not contain that arch, its family dir (last character replaced by `x`), or `gfx<major>-generic`.
- AMD ROCm only. `make setup` refuses to enable System One when `/dev/kfd` is absent.
- Host port binds `127.0.0.1` only (Laya runs without auth here). Default host port `8010`; container port `8000`.
- `LAYA_MODELS=english,multilingual` by default (`system_one.models`). An existing explicit `models` value is preserved. Default `vram_reserve_mib` is 5120; `migrate_config` only fills a missing key.
- Makefile target bodies ≤ 3 lines and delegate to a `.sh` (AGENTS.md).
- Host Python only as `uv run llmenv.py <subcommand>` (`llmenv()` in `tools/lib.sh` is that). Ad-hoc `uv run python3 -c` or a Python heredoc in a shell step is not allowed; Python a step needs is an `llmenv.py` subcommand or one that already exists. `python3` inside the System One image is the image entrypoint, not this rule. All output in English.
- After any `.sh` edit: `make validate`. Every task that edits a `.py` file runs `make validate && make test` before its commit. A targeted `uv run pytest` is only the red step.

## Review Focus

1. **`make start` with System One enabled but `make setup` never run for it** (image missing, cache volume missing, or weights incomplete for configured `system_one.models`) → clear `die` pointing at `make setup`, never a silent multi-minute download or a healthcheck timeout. Pinned in Task 7.
2. **Host without `/dev/kfd`** (NVIDIA/Intel/no GPU) answering "y" — setup must die with a clear message before writing `enabled: true`. Pinned in Task 6.
3. **`llm_server.enabled: false` + `system_one.enabled: true`** — GPU detection must still run (the render node feeds `rocr_visible_index`), llama.cpp steps stay skipped, OmniRoute still has no `depends_on`. `make gpu-status` must not say “no GPU is in use” while System One is enabled. Pinned in Tasks 3, 6, and 7.
4. **Two ROCm-visible GPUs** — whole `/dev/dri` without `ROCR_VISIBLE_DEVICES` can land Laya on the iGPU. Devices are `/dev/kfd` and `/dev/dri:/dev/dri`; the index is `rocr_visible_index` (Task 3). A single render node does not start torch.
5. **`make clean`** must also remove the locally built image and the `llm-env-system-one-cache` volume (~0.8 GB Laya weights; cheap to re-download on the next `make setup`, unlike llama.cpp model files kept by `make prune`). Pinned in Task 9.
6. **rocm7.2 pin and regression gate** — `TORCH_INDEX=rocm7.2` (not 7.14). The build passes the detected `GPU_ARCH` or dies. Task 10, after one decision request: wait 10 s, then `measure_idle system-one` < 5% of one core over 10 s, and decision p50 over 20 requests ≤ 20 ms. On failure stop and report; do not hunt env vars.

## Load follow-up (2026-10-12)

Live load on the RX 9070 XT with `english,multilingual` loaded. Caps stay 2 CPUs and 4608 MiB. Reservations are `SYSTEM_ONE_CPUS_MIN = 1` and `SYSTEM_ONE_MEMORY_MIN_MIB = 2560` (`cpu_shares` / `mem_reservation` via `_apply_resource_limits`). The host floor subtracts those mins, the same way OmniRoute's min is counted. `LAYA_MAX_CONCURRENT=64`; beyond 64 requests in flight Laya returns HTTP 503 and `Retry-After: 1` (no queue). `make system-one-benchmark` runs idle, sequential 10/100/1000, concurrent 1000×8 and 2000×16, one phase at 2× `LAYA_MAX_CONCURRENT` (non-200 count), then cooldown. Port and render node come from config/detect. A timestamped copy of the table is written into the same diagnostic directory `scripts/benchmark.sh` uses (`prepare_diagnostic_dir benchmark`).

---

### Task 1: Config schema for `system_one`

**Files:**
- Modify: `models.yml.example` (after the `llm_server:` block, ~line 35-37)
- Modify: `pylib/config.py` (`migrate_config` ~line 172; `validate_config` end of the `llm_server` block ~line 393)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: config keys `system_one.enabled: bool`, `system_one.port: int`, `system_one.image: str`, `system_one.models: str`, `system_one.vram_reserve_mib: int`. Constants `DEFAULT_SYSTEM_ONE_IMAGE`, `DEFAULT_SYSTEM_ONE_PORT`, `LAYA_MODEL_ALIASES` / `canonical_laya_model_name()` in `pylib/config.py` (comma-separated `system_one.models` tokens must each resolve to `english`, `multilingual`, or `typed-decisions`; reject empty segments and duplicates).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py — migrate_config and validate_config are already imported (lines 11-21); do not import them again.

@pytest.fixture
def valid_cfg(): return make_cfg(system_one={"enabled": False, "port": 8010, "image": "localhost/llm-env-system-one:rocm", "models": "english", "vram_reserve_mib": 4096})

def test_migrate_config_adds_disabled_system_one_defaults():
    cfg = migrate_config({})
    assert cfg["system_one"] == {
        "enabled": False,
        "port": 8010,
        "image": "localhost/llm-env-system-one:rocm",
        "models": "english,multilingual",
        "vram_reserve_mib": 5120,
    }

def test_migrate_config_keeps_existing_system_one_values():
    cfg = migrate_config({"system_one": {"enabled": True, "port": 9000}})
    assert cfg["system_one"]["enabled"] is True
    assert cfg["system_one"]["port"] == 9000

def test_validate_config_rejects_bad_system_one(valid_cfg):
    valid_cfg["system_one"] = {"enabled": "yes", "port": 0, "image": "", "models": "", "vram_reserve_mib": -1}
    errors = validate_config(valid_cfg)
    assert "system_one.enabled must be a Boolean" in errors
    assert "system_one.port must be a positive integer" in errors
    assert "system_one.image must be a non-empty string" in errors
    assert "system_one.models must be a non-empty string" in errors
    assert "system_one.vram_reserve_mib must be a positive integer" in errors


def test_validate_config_rejects_unknown_or_malformed_system_one_models(valid_cfg):
    valid_cfg["system_one"]["models"] = "english,not-a-model"
    errors = validate_config(valid_cfg)
    assert any("system_one.models" in e and "not-a-model" in e for e in errors)

    valid_cfg["system_one"]["models"] = "english,,multilingual"
    errors = validate_config(valid_cfg)
    assert any("empty" in e.lower() for e in errors)

    valid_cfg["system_one"]["models"] = "english,english"
    errors = validate_config(valid_cfg)
    assert any("more than once" in e for e in errors)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py -k system_one -v`
Expected: FAIL (`KeyError: 'system_one'` / missing error strings).

- [ ] **Step 3: Implement**

In `pylib/config.py` next to the other defaults:

```python
DEFAULT_SYSTEM_ONE_IMAGE = "localhost/llm-env-system-one:rocm"
DEFAULT_SYSTEM_ONE_PORT = 8010
# Mirrors laya.router canonical names (english | multilingual | typed-decisions).
LAYA_MODEL_ALIASES = {
    "en": "english", "laya": "english", "default": "english",
    "multi": "multilingual", "ml": "multilingual", "laya-multilingual": "multilingual",
    "typed": "typed-decisions", "typed_decisions": "typed-decisions",
    "laya-typed-decisions": "typed-decisions", "decisions": "typed-decisions",
}


def canonical_laya_model_name(token: str) -> str:
    key = token.strip().lower()
    if key in LAYA_MODEL_ALIASES:
        return LAYA_MODEL_ALIASES[key]
    if key in ("english", "multilingual", "typed-decisions"):
        return key
    raise ValueError(token)
```

In `migrate_config`, after the `llm_server` block:

```python
    system_one = cfg.setdefault("system_one", {})
    if isinstance(system_one, dict):
        system_one.setdefault("enabled", False)
        system_one.setdefault("port", DEFAULT_SYSTEM_ONE_PORT)
        system_one.setdefault("image", DEFAULT_SYSTEM_ONE_IMAGE)
        # Measured 2026-10-12 (RX 9070 XT, rocm7.2): both ~3170 MiB; english alone saves ~200 MiB.
        system_one.setdefault("models", "english,multilingual")
        system_one.setdefault("vram_reserve_mib", 5120)
```

In `validate_config`, after the `llm_server` block:

```python
    if "system_one" in cfg:
        system_one = cfg["system_one"]
        if not isinstance(system_one, dict):
            errors.append("section system_one must be a mapping")
        else:
            if not isinstance(system_one.get("enabled", False), bool):
                errors.append("system_one.enabled must be a Boolean")
            if not _positive_int(system_one.get("port", DEFAULT_SYSTEM_ONE_PORT)):
                errors.append("system_one.port must be a positive integer")
            for key in ("image", "models"):
                value = system_one.get(key, "x")
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"system_one.{key} must be a non-empty string")
            if not _positive_int(system_one.get("vram_reserve_mib", 5120)):
                errors.append("system_one.vram_reserve_mib must be a positive integer")
            models_raw = system_one.get("models", "english")
            if isinstance(models_raw, str):
                parts = [p.strip() for p in models_raw.split(",")]
                if not parts or any(not p for p in parts):
                    errors.append("system_one.models must be a comma-separated list with no empty members")
                else:
                    seen: set[str] = set()
                    for part in parts:
                        try:
                            canonical = canonical_laya_model_name(part)
                        except ValueError:
                            errors.append(f"system_one.models contains unknown checkpoint name: {part}")
                            continue
                        if canonical in seen:
                            errors.append(f"system_one.models lists {canonical} more than once")
                        seen.add(canonical)
```

In `models.yml.example`, after `llm_server:`:

```yaml
# Opt-in local System One decision server (Laya, Jev-compatible
# POST /v1/systemone) on the AMD GPU via ROCm. Set during `make setup`
# (Step 1) or via LLM_ENV_SYSTEM_ONE=1. Loopback-only, no auth.
system_one:
  enabled: false
  port: 8010
  image: localhost/llm-env-system-one:rocm
  models: english,multilingual  # LAYA_MODELS: english | multilingual | typed-decisions (comma list). Both fit in vram_reserve_mib. english alone saves ~200 MiB but non-English and short English inputs return HTTP 500.
  vram_reserve_mib: 5120   # subtracted from llm-server's VRAM budget when enabled
```

- [ ] **Step 4: Run tests**

Run: `make validate && make test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add models.yml.example pylib/config.py tests/test_config.py
git commit -m "feat(config): add opt-in system_one section"
```

---

### Task 2: Image definition, build script, Makefile target

**Files:**
- Create: `docker/system-one/Dockerfile` (move from `docker/laya-rocm/Dockerfile`, untracked)
- Create: `scripts/system-one-build.sh`
- Modify: `Makefile` (`.PHONY` + new target), `scripts/help.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Produces: `bash scripts/system-one-build.sh` — builds `system_one.image` from `${REPO_DIR}/docker/system-one` with `--build-arg GPU_ARCH=<arch>`; exit 0 on success. Arch comes from `llmenv gfx-arch` (Task 3). If pci, render node, or arch is missing, the script dies. Used by Task 6 (`make setup`) and `make system-one-build`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shell.py
def test_system_one_build_builds_the_configured_image_from_the_repo_context(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(
        tmp_path, "scripts/system-one-build.sh", system_one_enabled=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"podman build --label llm-env.gpu-arch=gfx1201 --build-arg GPU_ARCH=gfx1201 -t localhost/llm-env-system-one:rocm {ROOT}/docker/system-one" in calls.read_text()
    dockerfile = (ROOT / "docker/system-one/Dockerfile").read_text()
    assert "ARG TORCH_INDEX=rocm7.2" in dockerfile and "rocm7.14" not in dockerfile


def test_makefile_exposes_system_one_build() -> None:
    assert "system-one-build:" in (ROOT / "Makefile").read_text()
```

Extend `run_lifecycle_script` (tests/test_shell.py ~line 4496) with a keyword `system_one_enabled: bool = False` that appends to the written config:

```python
        + "system_one:\n"
        f"  enabled: {str(system_one_enabled).lower()}\n"
        "  port: 8010\n"
        "  image: localhost/llm-env-system-one:rocm\n"
        "  models: english\n"
        "  vram_reserve_mib: 4096\n"
```

and adds `"SYSTEM_ONE_ENABLED": "true" if system_one_enabled else "false"` to `environment`. When `system_one_enabled`, the written `gpu.pci_address` is `"0000:03:00.0"` (quoted; a bare `0000:03:00.0` is the sexagesimal float 180.0) instead of `''`. In the `uv` stub, before the `migrate-config` case:

```bash
case "$*" in
  *' detect'*) printf '%s\n' '{"gpus":[{"card":"card1","pci_address":"0000:03:00.0","vram_total_mib":16304,"vram_used_mib":0,"render_node":"renderD128","vendor":"0x1002","connected_outputs":[]}]}' ;;
  *' gfx-arch '*) printf '%s\n' 'gfx1201' ;;
esac
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_shell.py -k system_one_build -v`
Expected: FAIL (script missing).

- [ ] **Step 3: Implement**

`git mv` is not possible (untracked); move the file:

```bash
mkdir -p docker/system-one && mv docker/laya-rocm/Dockerfile docker/system-one/Dockerfile && rmdir docker/laya-rocm
```

Final `docker/system-one/Dockerfile`:

```dockerfile
# Laya (Jev-compatible System One server) on AMD ROCm. Upstream's compose
# covers only NVIDIA/CPU, so llm-env builds this image locally:
# scripts/system-one-build.sh (make system-one-build / make setup).
FROM python:3.11-slim-trixie
ARG TORCH_VERSION=2.14.1
# shortcut: ROCR 1.21 AsyncEventsLoop busy-spins one core after the first GPU kernel (https://github.com/ROCm/rocm-systems/issues/12674, https://github.com/ROCm/TheRock/issues/7051); move back to the newest index once a release carries rocm-systems PR #7898; re-run the idle check.
ARG TORCH_INDEX=rocm7.2
ARG LAYA_VERSION=0.4.2
ARG GPU_ARCH
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false \
    TORCH_DISABLE_NATIVE_JIT=1 LAYA_DEVICE=cuda LAYA_PRELOAD=1 \
    HF_HOME=/home/laya/.cache/huggingface
# Drop per-arch kernels for every arch except GPU_ARCH, its family dir (last char → x), and gfx<major>-generic.
# Same RUN as the install: a later RUN would leave the deleted files in the lower layer.
RUN test -n "${GPU_ARCH}" \
    && pip install "torch==${TORCH_VERSION}" --index-url https://download.pytorch.org/whl/${TORCH_INDEX} \
    && pip install "laya[serve]==${LAYA_VERSION}" "huggingface_hub==2.2.0" \
    && family="${GPU_ARCH%?}x" && major="${GPU_ARCH#gfx}" && major="${major%??}" && generic="gfx${major}-generic" \
    && site=/usr/local/lib/python3.11/site-packages \
    && find "$site/torch" "$site/triton" -type f -path '*gfx*' \
         ! -path "*${GPU_ARCH}*" ! -path "*${family}*" ! -path "*${generic}*" -delete \
    && find "$site/torch" "$site/triton" -type d -empty -delete
# libatomic1: torch ROCm ImportError without it; libnuma1/libdrm2: rocSHMEM/HIP warnings.
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 libdrm2 libnuma1 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd -g 10001 laya && useradd -u 10001 -g laya -m laya \
    && mkdir -p "$HF_HOME" && chown -R laya:laya /home/laya
USER laya
EXPOSE 8000
CMD ["laya-serve"]
```

`scripts/system-one-build.sh`:

```bash
#!/usr/bin/env bash
# system-one-build.sh — build the local ROCm Laya image (no upstream image exists).
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd podman yq
[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
migrate_config_file || die "configuration migration failed"
image="$(yq -r '.system_one.image' "$CONFIG_PATH")"; [ -n "$image" ] && [ "$image" != null ] || die "system_one.image is empty; run 'make setup'"
pci="$(yq -r '.gpu.pci_address' "$CONFIG_PATH")"; [ -n "$pci" ] && [ "$pci" != null ] || die "system-one build: gpu.pci_address is empty; run 'make setup' (no GPU arch default)"
render="$(llmenv detect | jq -r --arg p "$pci" '.gpus[] | select(.pci_address == $p) | .render_node')"; [ -n "$render" ] && [ "$render" != null ] || die "system-one build: no render node for ${pci}; cannot determine GPU arch"
arch="$(llmenv gfx-arch --render-node "$render")" && [ -n "$arch" ] || die "system-one build: cannot read gfx arch for ${render} (KFD gfx_target_version); refusing to guess"
log_step "Building ${image} for ${arch} (large ROCm image; first build takes several minutes)"
podman build --label "llm-env.gpu-arch=${arch}" --build-arg "GPU_ARCH=${arch}" -t "$image" "${REPO_DIR}/docker/system-one"
log_info "built ${image}"
```

`Makefile`: add `system-one-build` to `.PHONY`, and:

```make
system-one-build:
	@bash tools/run-target.sh system-one-build -- bash scripts/system-one-build.sh
```

`scripts/help.sh` (after `make benchmark`):

```bash
_help_line "system-one-build" "Build the local System One (Laya) GPU image"
```

- [ ] **Step 4: Run tests**

Run: `make validate && make test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docker/system-one scripts/system-one-build.sh Makefile scripts/help.sh tests/test_shell.py
git commit -m "feat(system-one): local ROCm Laya image and build target"
```

---

### Task 3: Compose service, pinned to the configured GPU

**Files:**
- Modify: `pylib/compose.py` (`render_compose`), `pylib/detect.py` (`rocr_visible_index`)
- Modify: `llmenv.py` (`rocr-index`; `cmd_render_compose` ~line 436; parser ~line 604)
- Modify: `setup/render-unit.sh` (render-compose call ~line 67)
- Test: `tests/test_compose.py`, `tests/test_detect.py`

**Interfaces:**
- Consumes: Task 1 config keys.
- Produces: `rocr_visible_index(render_minor: int, nodes_root: Path) -> int`; `gfx_arch(render_node: str, kfd_root: Path) -> str` (`gfx_target_version` N = major*10000 + minor*100 + step → `gfx<major><minor><step>`, minor/step one hex digit; 120001 → `gfx1201`); `render_compose(..., system_one_rocr_index="")`; `llmenv rocr-index --render-node renderD128` prints the index; `llmenv gfx-arch --render-node renderD128` prints the arch; `llmenv render-compose --system-one-rocr-index 0`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_detect.py — import rocr_visible_index, gfx_arch; fake KFD tree; this host minor 128 → 0, 129 → 1; 120001 → gfx1201
def test_rocr_visible_index_orders_gpu_nodes(tmp_path):
    nodes = tmp_path / "nodes"
    for name, gpu_id, minor, gfx in (("0", "0", "0", "0"), ("1", "23609", "128", "120001"), ("2", "58620", "129", "100306")):
        (nodes / name).mkdir(parents=True)
        (nodes / name / "gpu_id").write_text(gpu_id + "\n")
        (nodes / name / "properties").write_text(f"drm_render_minor {minor}\ngfx_target_version {gfx}\n")
    assert rocr_visible_index(128, nodes) == 0 and rocr_visible_index(129, nodes) == 1
    assert gfx_arch("renderD128", nodes) == "gfx1201" and gfx_arch("renderD129", nodes) == "gfx1036"

def test_rocr_visible_index_missing_drm_render_minor_raises(tmp_path):
    node = tmp_path / "nodes" / "1"; node.mkdir(parents=True); (node / "gpu_id").write_text("1\n"); (node / "properties").write_text("gpu_id 1\n")
    with pytest.raises(ValueError, match="drm_render_minor"): rocr_visible_index(128, node.parent)

def test_gfx_arch_missing_version_raises(tmp_path):
    node = tmp_path / "nodes" / "1"; node.mkdir(parents=True); (node / "gpu_id").write_text("1\n"); (node / "properties").write_text("drm_render_minor 128\n")
    with pytest.raises(ValueError, match="gfx_target_version"): gfx_arch("renderD128", node.parent)

# tests/test_compose.py
SYSTEM_ONE = {"enabled": True, "port": 8010, "image": "localhost/llm-env-system-one:rocm",
              "models": "english", "vram_reserve_mib": 4096}


def system_one_document(cfg):
    text = render_compose(
        cfg,
        models_dir="/m", presets_path="/p", repo_root="/r",
        system_one_rocr_index="0",
    )
    return yaml.safe_load(text)


def test_system_one_service_absent_by_default():
    _, document = compose_dict()
    assert "system-one" not in document["services"]


def test_system_one_service_rendered_when_enabled():
    document = system_one_document({**CFG, "system_one": SYSTEM_ONE})
    service = document["services"]["system-one"]
    assert service["image"] == "localhost/llm-env-system-one:rocm"
    assert service["container_name"] == "system-one"
    assert service["devices"] == ["/dev/kfd", "/dev/dri:/dev/dri"]
    assert service["environment"]["ROCR_VISIBLE_DEVICES"] == "0"
    assert service["group_add"] == ["keep-groups"]
    assert service["security_opt"] == ["label=disable"]
    assert service["ports"] == ["127.0.0.1:8010:8000"]
    assert service["environment"]["LAYA_MODELS"] == "english"
    assert "system-one-cache:/home/laya/.cache/huggingface" in service["volumes"]
    assert document["volumes"]["system-one-cache"] == {"name": "llm-env-system-one-cache"}
    assert service["healthcheck"]["start_period"] == "120s"


def test_system_one_is_independent_of_llm_server():
    cfg = {**CFG, "llm_server": {"enabled": False}, "system_one": SYSTEM_ONE}
    document = system_one_document(cfg)
    assert "llm-server" not in document["services"]
    assert "system-one" in document["services"]
    assert "depends_on" not in document["services"]["omniroute"]


def test_system_one_requires_a_rocr_index():
    import pytest
    with pytest.raises(ValueError, match="ROCR_VISIBLE_DEVICES"):
        render_compose({**CFG, "system_one": SYSTEM_ONE}, models_dir="/m",
                       presets_path="/p", repo_root="/r")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_compose.py tests/test_detect.py -k "system_one or rocr_visible_index or gfx_arch" -v`
Expected: FAIL (`unexpected keyword argument 'system_one_rocr_index'`; `rocr_visible_index` / `gfx_arch` missing).

- [ ] **Step 3: Implement** `rocr_visible_index` in `pylib/detect.py` (`properties` field `drm_render_minor`):

```python
def rocr_visible_index(render_minor: int, nodes_root: Path) -> int:
    gpus: list[int] = []
    nodes = sorted((p for p in Path(nodes_root).iterdir() if p.name.isdigit()), key=lambda p: int(p.name))
    for node in nodes:
        if (node / "gpu_id").read_text().strip() == "0":
            continue
        minor = next((int(b) for a, b in (ln.split() for ln in (node / "properties").read_text().splitlines()) if a == "drm_render_minor"), None)
        if minor is None: raise ValueError(f"no KFD GPU node with drm_render_minor (node {node.name})")
        gpus.append(minor)
    if render_minor not in gpus:
        raise ValueError(f"no KFD GPU node with drm_render_minor {render_minor}")
    return gpus.index(render_minor)
```

`gfx_arch` beside it (`kfd_root` is that same nodes directory; minor/step above 15 is an error):

```python
def gfx_arch(render_node: str, kfd_root: Path) -> str:
    want = str(int(str(render_node).removeprefix("renderD")))
    version = None
    for node in sorted((p for p in Path(kfd_root).iterdir() if p.name.isdigit()), key=lambda p: int(p.name)):
        if (node / "gpu_id").read_text().strip() == "0": continue
        fields = {a: b for a, b in (ln.split() for ln in (node / "properties").read_text().splitlines())}
        if fields.get("drm_render_minor") != want: continue
        raw = fields.get("gfx_target_version")
        if raw is None: raise ValueError(f"no gfx_target_version on KFD node {node.name}")
        version = int(raw); break
    if version is None: raise ValueError(f"no KFD GPU node with drm_render_minor {want}")
    major, rem = divmod(version, 10000); minor, step = divmod(rem, 100)
    # shortcut: minor/step are one hex digit (0–15); reject wider KFD values
    if minor > 15 or step > 15: raise ValueError(f"gfx_target_version {version} minor/step need more than one hex digit")
    return f"gfx{major}{minor:x}{step:x}"
```

`pylib/compose.py` — add `system_one_rocr_index: str = ""` to `render_compose` and, before `services: dict[str, Any] = {}`:

```python
    system_one = cfg.get("system_one", {})
    system_one_service: dict[str, Any] | None = None
    if system_one.get("enabled", False):
        if system_one_rocr_index == "":
            raise ValueError("system_one is enabled but ROCR_VISIBLE_DEVICES was not resolved")
        system_one_service = {
            "image": _dollar_escape(system_one["image"]),
            "container_name": "system-one",
            "devices": ["/dev/kfd", "/dev/dri:/dev/dri"],
            "group_add": ["keep-groups"],
            "security_opt": ["label=disable"],
            "volumes": ["system-one-cache:/home/laya/.cache/huggingface"],
            "ports": [f"127.0.0.1:{system_one['port']}:8000"],
            "environment": {
                "LAYA_MODELS": _dollar_escape(system_one["models"]),
                "LAYA_MAX_LOADED": str(len([part for part in system_one["models"].split(",") if part.strip()])),
                "LAYA_REVISION": "reviewed",
                "HF_HUB_OFFLINE": "1",
                "ROCR_VISIBLE_DEVICES": system_one_rocr_index,
            },
            "healthcheck": {
                # slim image has no curl
                "test": ["CMD", "python3", "-c",
                         "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"],
                "interval": "10s",
                "timeout": "5s",
                "retries": 5,
                # weights prefetched in make setup; start_period covers load-to-GPU only
                "start_period": "120s",
            },
            "restart": "unless-stopped",
        }
```

and after the existing llm-server / omniroute / remote-setup population (an addition, not a replacement of that block):

```python
    if system_one_service is not None:
        services["system-one"] = system_one_service
    volumes: dict[str, Any] = {"omniroute-data": {}, "remote-setup-data": {}}
    if system_one_service is not None:
        volumes["system-one-cache"] = {"name": "llm-env-system-one-cache"}
    document = {"services": services, "volumes": volumes}
    return HEADER_COMMENT + yaml.safe_dump(document, sort_keys=False, default_flow_style=False)
```

```python
# Replace llmenv.py:48 with this ruff-formatted import. Do not add a second import.
from pylib.detect import (
    DetectError,
    detect,
    gfx_arch,
    host_resources,
    processes_on_render_node,
    rocr_visible_index,
)
def cmd_rocr_index(args: argparse.Namespace) -> int:
    try: index = rocr_visible_index(int(args.render_node.removeprefix("renderD")), Path("/sys/class/kfd/kfd/topology/nodes"))
    except (ValueError, OSError) as error: print(str(error), file=sys.stderr); return 1  # OSError: missing KFD topology
    print(index); return 0
def cmd_gfx_arch(args: argparse.Namespace) -> int:
    try: arch = gfx_arch(args.render_node, Path("/sys/class/kfd/kfd/topology/nodes"))
    except (ValueError, OSError) as error: print(str(error), file=sys.stderr); return 1
    print(arch); return 0
# llmenv.py build_parser after detect (line 539); render_compose is not defined yet
rocr = sub.add_parser("rocr-index"); rocr.add_argument("--render-node", required=True); rocr.set_defaults(func=cmd_rocr_index)
gfx = sub.add_parser("gfx-arch"); gfx.add_argument("--render-node", required=True); gfx.set_defaults(func=cmd_gfx_arch)
# llmenv.py build_parser after the render-compose parser (lines 603-610)
render_compose.add_argument("--system-one-rocr-index", default="")
# pylib/compose.py write_compose — keep the rest of this function
def write_compose(cfg: dict[str, Any], *, models_dir: str, presets_path: str, repo_root: str, omni_router_master_key: str = "", path: Path, system_one_rocr_index: str = "") -> None:
    content = render_compose(cfg, models_dir=models_dir, presets_path=presets_path, repo_root=repo_root, omni_router_master_key=omni_router_master_key, system_one_rocr_index=system_one_rocr_index)
# llmenv.py cmd_render_compose, not inside write_compose
try: write_compose(cfg, models_dir=args.models_dir, presets_path=args.presets_path, repo_root=args.repo_root, omni_router_master_key=env_vars.get("OMNI_ROUTER_MASTER_KEY", ""), path=Path(args.output), system_one_rocr_index=args.system_one_rocr_index)
except ValueError as error: return fail(str(error))
return emit({"written": str(args.output)})
```

`setup/render-unit.sh` — shell does not read sysfs. Before the render-compose call:

```bash
system_one_enabled="$(yq -r '.system_one.enabled' "$CONFIG_PATH")"
system_one_rocr_index=""
if [ "$system_one_enabled" = "true" ]; then
    pci="$(yq -r '.gpu.pci_address' "$CONFIG_PATH")"
    render="$(llmenv detect | jq -r --arg p "$pci" '.gpus[] | select(.pci_address == $p) | .render_node')" || die "System One: GPU detection failed; cannot resolve a render node for ${pci}"
    [ -n "$render" ] || die "system_one: configured GPU ${pci} has no render node; run 'make setup'"
    system_one_rocr_index="$(llmenv rocr-index --render-node "$render")" || die "System One: cannot map ${render} to a ROCm device; see message above"
fi
```

and append `--system-one-rocr-index "$system_one_rocr_index"` to the render-compose invocation.

- [ ] **Step 4: Run tests**

Run: `make validate && make test`
Expected: PASS (existing compose tests unchanged: no `system_one` in `CFG` → service absent).

- [ ] **Step 5: Commit**

```bash
git add pylib/compose.py pylib/detect.py llmenv.py setup/render-unit.sh tests/test_compose.py tests/test_detect.py
git commit -m "feat(compose): render pinned system-one service when enabled"
```

---

### Task 4: RAM/CPU reservation

**Files:**
- Modify: `pylib/resources.py` (constants + `compute_resource_limits`)
- Modify: `llmenv.py` (`cmd_resources` ~line 366)
- Modify: `pylib/compose.py` (apply limits to `system-one`)
- Test: `tests/test_resources.py`, `tests/test_compose.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `SYSTEM_ONE_CPUS = 2`, `SYSTEM_ONE_MEMORY_MIB = 4608` in `pylib/resources.py`; `compute_resource_limits(..., system_one_enabled: bool = False)`; result key `"system_one": {"cpus": int, "memory_mib": int}` (zeros when disabled).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_resources.py — add only SYSTEM_ONE_CPUS and SYSTEM_ONE_MEMORY_MIB to the existing `from pylib.resources import (...)`, after ResourceError. Do not add a second import.

def test_system_one_reservation_shrinks_llm_server_remainder():
    base = compute_resource_limits(16, 32768)
    with_one = compute_resource_limits(16, 32768, system_one_enabled=True)
    memory_floor_mib = (
        HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_MIN_MIB + REMOTE_SETUP_MEMORY_MIN_MIB
    )
    assert with_one["system_one"] == {"cpus": SYSTEM_ONE_CPUS, "memory_mib": SYSTEM_ONE_MEMORY_MIB}
    assert with_one["llm_server"]["memory_mib"] == base["llm_server"]["memory_mib"] - SYSTEM_ONE_MEMORY_MIB
    assert with_one["llm_server"]["memory_mib"] <= 32768 - memory_floor_mib - SYSTEM_ONE_MEMORY_MIB

def test_system_one_reservation_absent_when_disabled():
    assert compute_resource_limits(16, 32768)["system_one"] == {"cpus": 0, "memory_mib": 0}


def test_system_one_resource_error_names_its_reservation():
    message = r"System One's reservation \(2 CPUs, 4608 MiB\)"
    # (cpus, memory_mib, llm_server_enabled) — one call per ResourceError branch
    for cpus, memory, llm in ((6, 32768, True), (16, 9760, True), (5, 32768, False), (16, 9759, False)):
        with pytest.raises(ResourceError, match=message):
            compute_resource_limits(cpus, memory, system_one_enabled=True, llm_server_enabled=llm)
```

```python
# tests/test_compose.py
def test_system_one_service_has_fixed_limits():
    document = system_one_document({**CFG, "system_one": SYSTEM_ONE})
    service = document["services"]["system-one"]
    assert service["cpus"] == 2
    assert service["mem_limit"] == "4608m"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_resources.py tests/test_compose.py -k system_one -v`
Expected: FAIL (ImportError / missing keys).

- [ ] **Step 3: Implement**

`pylib/resources.py`:

```python
SYSTEM_ONE_CPUS = 2  # 2 cores is plenty at ~12 ms/request; RSS was ~3.5 GB
SYSTEM_ONE_MEMORY_MIB = 4608

def _floor_words(system_one_enabled: bool) -> str:
    words = "the host floor and OmniRoute's/remote-setup's fixed allocations"
    if system_one_enabled:
        words += f" and System One's reservation ({SYSTEM_ONE_CPUS} CPUs, {SYSTEM_ONE_MEMORY_MIB} MiB)"
    return words
```

In all four `ResourceError` strings, the floor phrase becomes an f-string call `_floor_words(system_one_enabled)` (so `{_floor_words(system_one_enabled)}` interpolates). System One off keeps today's wording.

In `compute_resource_limits` add `system_one_enabled: bool = False`. Right after the floors are computed, add System One when enabled, and add this key to the returned dict. System One's RAM reservation lowers llm-server RAM only when the remainder is the binding limit; when the percentage ceiling binds, llm-server is unchanged (the host still fits).

```python
    if system_one_enabled:
        cpu_floor += SYSTEM_ONE_CPUS
        memory_floor_mib += SYSTEM_ONE_MEMORY_MIB
    # in the returned dict:
    "system_one": {"cpus": SYSTEM_ONE_CPUS if system_one_enabled else 0,
                   "memory_mib": SYSTEM_ONE_MEMORY_MIB if system_one_enabled else 0},
```

Both `cmd_budget` `compute_resource_limits` calls (`llmenv.py` lines 282 and 320) pass `system_one_enabled=cfg.get("system_one", {}).get("enabled", False)` and `llm_server_enabled=cfg.get("llm_server", {}).get("enabled", True)`. `cmd_resources` already passes `llm_server_enabled`; pass `system_one_enabled` there too.

```python
def test_cmd_budget_system_one_lowers_ram_available_mib(tmp_path, monkeypatch, capsys):
    import llmenv
    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    cfg.setdefault("resources", {}).setdefault("llm_server", {})["memory_ceiling_pct"] = 100
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg); monkeypatch.setattr(llmenv, "detect", lambda: {"compositor_render_node": "renderD128", "gpus": [{"pci_address": "0000:03:00.0", "render_node": "renderD129", "vram_total_mib": 16304, "vram_used_mib": 0}]}); monkeypatch.setattr(llmenv, "host_resources", lambda: {"cpu_count": 16, "memory_total_mib": 32768}); monkeypatch.setattr(llmenv, "_model_costs", lambda c, p: [{"alias": "a", "weights_mib": 100, "ram_weights_mib": 0, "kv_mib": 0}])
    ns = cast(Namespace, SimpleNamespace(config="cfg", models_dir="m")); llmenv.cmd_budget(ns); off = json.loads(capsys.readouterr().out)["ram_available_mib"]; cfg["system_one"] = {"enabled": True, "port": 8010, "image": "localhost/llm-env-system-one:rocm", "models": "english", "vram_reserve_mib": 4096}; llmenv.cmd_budget(ns); assert json.loads(capsys.readouterr().out)["ram_available_mib"] < off
```

`pylib/compose.py` (inside the enabled branch of Task 3): `"cpus": SYSTEM_ONE_CPUS, "mem_limit": f"{SYSTEM_ONE_MEMORY_MIB}m"`, imported from `pylib.resources`.

- [ ] **Step 4: Run tests**

Run: `make validate && make test`
Expected: PASS (existing `test_resources.py` cases default `system_one_enabled=False` → unchanged).

- [ ] **Step 5: Commit**

```bash
git add pylib/resources.py llmenv.py pylib/compose.py tests/test_resources.py tests/test_compose.py tests/test_cli.py
git commit -m "feat(resources): reserve RAM/CPU for system-one when enabled"
```

---

### Task 5: VRAM budget reserves System One's share

**Files:**
- Modify: `pylib/budget.py` (`compute_budget` ~line 103)
- Modify: `llmenv.py` (`cmd_budget` ~line 228)
- Test: `tests/test_budget.py`

**Interfaces:**
- Produces: `compute_budget(..., extra_reserved_mib: int = 0)`; result key `"extra_reserved_mib"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_budget.py
def test_extra_reserved_mib_reduces_available():
    costs = [{"alias": "m", "weights_mib": 1000, "kv_mib": 100}]
    base = compute_budget(vram_total_mib=16384, compositor_used_mib=0, reserve_floor_mib=1024, model_costs=costs, models_max=1)
    reserved = compute_budget(vram_total_mib=16384, compositor_used_mib=0, reserve_floor_mib=1024, model_costs=costs, models_max=1, extra_reserved_mib=4096)
    assert reserved["available_mib"] == base["available_mib"] - 4096
    assert reserved["extra_reserved_mib"] == 4096
```

(Match `model_costs` keys to whatever `_model_costs` emits; copy one entry from an existing `test_budget.py` case.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_budget.py -k extra_reserved -v`
Expected: FAIL (unexpected keyword).

- [ ] **Step 3: Implement**

`pylib/budget.py`:

```python
    extra_reserved_mib: int = 0,
) -> dict[str, Any]:
    ...
    # Not folded into `reserve`: that is max(compositor, floor), which would swallow it.
    available = ceiling_mib - reserve - extra_reserved_mib - SPIKE_HEADROOM_MIB
```

and add `"extra_reserved_mib": extra_reserved_mib,` to the result dict.

`llmenv.py` `cmd_budget`:

```python
    system_one = cfg.get("system_one", {})
    result = compute_budget(
        ...,
        extra_reserved_mib=system_one["vram_reserve_mib"] if system_one.get("enabled") else 0,
    )
```

- [ ] **Step 4: Run tests**

Run: `make validate && make test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pylib/budget.py llmenv.py tests/test_budget.py
git commit -m "feat(budget): reserve system-one VRAM from llm-server's budget"
```

---

### Task 6: `make setup` asks, guards, GPU detection, image build, and weight prefetch

**Files:**
- Create: `scripts/system-one-cache-check.py` (shared prefetch + verify manifest for `setup` / `start` / `check-setup`)
- Modify: `pylib/detect.py` (`list_gpus`: add `vendor` from `device/vendor` sysfs for ROCm guard)
- Modify: `setup/setup.sh` (Step 1 ~line 36-58; the `if [ "$llm_server_enabled" = "true" ]` block split ~line 60/101; new System One provisioning after GPU detection)
- Modify: `AGENTS.md` (env var list)
- Test: `tests/test_shell.py`, `tests/test_detect.py`

**Interfaces:**
- Produces: env var `LLM_ENV_SYSTEM_ONE=1|0` (unattended default); persisted `system_one.enabled`. When enabled, after GPU detection: builds `system_one.image` via `scripts/system-one-build.sh` if `podman image exists` fails; prefetches weights for `system_one.models` into volume `llm-env-system-one-cache` (compose key `system-one-cache`).

- [ ] **Step 1: Write the failing tests**

Extend `run_setup_no_gpu` (`tests/test_shell.py:682`):

- Keywords: `system_one_env: str | None = None` → sets `LLM_ENV_SYSTEM_ONE` when not `None`; `kfd_path: str | None = None` → sets `LLM_ENV_KFD_PATH` (defaults to `/dev/kfd` in `setup.sh`); `env_overrides: dict[str, str] | None = None` → merged into the subprocess `environment` after the base dict (later keys win). Append a complete `system_one` block to the helper's written `models.yml` (`tests/test_shell.py` ~716: `gpu` / `runtime` / `models` only today; the `uv` stub does not migrate): `enabled: false`, `port: 8010`, `image: localhost/llm-env-system-one:rocm`, `models: english`, `vram_reserve_mib: 4096`.
- Install a recording `bash` stub on `PATH` (same bytes as `run_lifecycle_script`'s `bash` stub at `tests/test_shell.py` ~4573: log `$*` to `$CALLS`, then `exec /usr/bin/bash "$@"`) so `bash "${REPO_DIR}/scripts/system-one-build.sh"` is observable. If `SYSTEM_ONE_BUILD_FAIL=1` and `$*` contains `system-one-build.sh`, log and `exit 1` without `exec`. Invoke setup itself as `/usr/bin/bash` so the stub does not wrap the script under test.
- Replace the `podman` `_mock_command` entry with a stub that always logs `podman $*` to `$CALLS`, and exits 1 on `image exists` when `PODMAN_IMAGE_MISSING=1` (otherwise exits 0).
- `uv` stub: add this arm **before** the existing `*' resources')` arm:

```bash
*' detect'*)
  vendor="${DETECT_GPU_VENDOR:-0x1002}"
  printf '%s\n' "{\"gpus\":[{\"card\":\"card1\",\"pci_address\":\"0000:03:00.0\",\"vram_total_mib\":16304,\"vram_used_mib\":0,\"render_node\":\"renderD129\",\"vendor\":\"${vendor}\",\"connected_outputs\":[]}]}"
  ;;
*' gfx-arch '*) printf '%s\n' 'gfx1201' ;;
```

- **DRI root:** always `dri = tmp_path / "dri"`; `dri.mkdir()`; `(dri / "renderD129").touch()`; set `LLM_ENV_DRI_PATH` to `str(dri)`. `setup.sh` uses `DRI_PATH="${LLM_ENV_DRI_PATH:-/dev/dri}"` and tests `-r "${DRI_PATH}/${render_node}"`. The fixture must not stat the host `/dev/dri`.
- **Input:** the new System One prompt is the second `ask` (after llm-server, before the GPU number). Every interactive `setup.sh` stdin must answer it or it consumes the next selection line (at EOF `ask` returns its default; verified by running `setup.sh` with empty stdin).
  - `run_setup_no_gpu`: `input="\n\n"` when `system_one_env` is `None` (llm-server default, System One default `n`). When it is `"1"`, `input="n\ny\n1\n"` (llm-server `n`, System One `y`, GPU number `1`).
  - `run_setup_with_numbered_selection` (~line 571): change `input="\n" + selection` to `input="\nn\n" + selection`. The blank keeps the llm-server default `Y`; `n` declines System One so every existing `selection` (`"1\n1\n1\n"`, `"1\n1\n2\n"`, `"1\n3\n"`, `"1\n1,2\n2\n"`) still drives GPU, model, and Vulkan.
  - `test_setup_fails_when_the_budget_is_infeasible` (`tests/test_shell.py:855`) inlines `input="1\n1\n1\n"`. Change it to `input="1\nn\n1\n1\n"`: `1` is not `n`/`N`, so llm-server stays enabled; `n` declines System One; the next two `1`s stay the GPU and model answers. Vulkan still auto-matches the one stub device.
  - `test_setup_rerun_keeps_llm_server_disabled_without_no_gpu_override` (~line 786) sets `LLM_ENV_ASSUME_YES=1`. `ask` returns the default and does not read stdin. No input change; System One’s default is `n`.
  - `test_setup_stops_for_missing_prerequisites_before_mutating_config` (~line 538) dies in the prerequisites check, before either prompt. No input change.

Then:

```python
def test_setup_persists_system_one_disabled_by_default(tmp_path: pathlib.Path) -> None:
    result, _calls, config = run_setup_no_gpu(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert yq_value(config, ".system_one.enabled") == "false"


def test_setup_refuses_system_one_without_kfd(tmp_path: pathlib.Path) -> None:
    result, _calls, config = run_setup_no_gpu(
        tmp_path, system_one_env="1", kfd_path=str(tmp_path / "no-kfd")
    )
    assert result.returncode != 0
    assert "requires an AMD GPU with ROCm (/dev/kfd)" in result.stderr
    assert yq_value(config, ".system_one.enabled") == "false"


def test_setup_refuses_system_one_when_selected_gpu_not_rocm(tmp_path: pathlib.Path) -> None:
    kfd = tmp_path / "kfd"
    kfd.touch()
    result, _calls, config = run_setup_no_gpu(
        tmp_path,
        system_one_env="1",
        kfd_path=str(kfd),
        env_overrides={"DETECT_GPU_VENDOR": "0x10de"},
    )
    assert result.returncode != 0
    assert "AMD ROCm GPU" in result.stderr
    assert yq_value(config, ".system_one.enabled") == "false"


def test_setup_system_one_runs_gpu_detection_even_without_llm_server(tmp_path: pathlib.Path) -> None:
    kfd = tmp_path / "kfd"
    kfd.touch()
    result, calls, config = run_setup_no_gpu(tmp_path, system_one_env="1", kfd_path=str(kfd))
    # Detection is stubbed; the assertion is that setup reached the detect path.
    assert "detect" in (calls.read_text() if calls.exists() else "")
    assert "Step 3/8  Selecting models" in result.stdout
    assert "(skipped -- llm-server disabled)" in result.stdout
    assert (yq_value(config, ".gpu.vram_total_mib"), yq_value(config, ".gpu.vram_budget_ceiling_mib")) == ("16304", "15489")


def test_setup_builds_system_one_image_when_missing(tmp_path: pathlib.Path) -> None:
    kfd = tmp_path / "kfd"
    kfd.touch()
    result, calls, _config = run_setup_no_gpu(
        tmp_path, system_one_env="1", kfd_path=str(kfd),
        env_overrides={"PODMAN_IMAGE_MISSING": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "system-one-build.sh" in calls.read_text() and "podman build --label llm-env.gpu-arch=gfx1201 --build-arg GPU_ARCH=gfx1201 -t localhost/llm-env-system-one:rocm" in calls.read_text()


def test_setup_leaves_system_one_disabled_when_build_fails(tmp_path: pathlib.Path) -> None:
    kfd = tmp_path / "kfd"
    kfd.touch()
    result, _calls, config = run_setup_no_gpu(
        tmp_path, system_one_env="1", kfd_path=str(kfd),
        env_overrides={"PODMAN_IMAGE_MISSING": "1", "SYSTEM_ONE_BUILD_FAIL": "1"},
    )
    assert result.returncode != 0
    assert yq_value(config, ".system_one.enabled") == "false"


def test_setup_skips_system_one_build_when_image_present(tmp_path: pathlib.Path) -> None:
    kfd = tmp_path / "kfd"
    kfd.touch()
    result, calls, _config = run_setup_no_gpu(tmp_path, system_one_env="1", kfd_path=str(kfd))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/system-one-build.sh" not in calls.read_text()


def test_setup_prefetches_system_one_weights_when_enabled(tmp_path: pathlib.Path) -> None:
    kfd = tmp_path / "kfd"
    kfd.touch()
    _result, calls, _config = run_setup_no_gpu(tmp_path, system_one_env="1", kfd_path=str(kfd))
    recorded = calls.read_text()
    assert "llm-env-system-one-cache:/home/laya/.cache/huggingface" in recorded
    assert "podman run" in recorded and "LAYA_MODELS=english" in recorded and "localhost/llm-env-system-one:rocm" in recorded
    assert "system-one-cache-check.py:ro,z" in recorded
    assert yq_value(_config, ".system_one.enabled") == "true"


def test_setup_skips_system_one_build_and_prefetch_when_disabled(tmp_path: pathlib.Path) -> None:
    _result, calls, _config = run_setup_no_gpu(tmp_path)
    recorded = calls.read_text()
    assert "scripts/system-one-build.sh" not in recorded
    assert "llm-env-system-one-cache" not in recorded
```

In `tests/test_detect.py` (`list_gpus` is already imported; `build_drm` writes no `vendor` file, so card0 is the missing-file case):

```python
def test_list_gpus_reads_device_vendor_and_missing_vendor_file(tmp_path):
    drm = build_drm(tmp_path)
    (drm / "card1" / "device").resolve().joinpath("vendor").write_text("0x1002\n")
    gpus = {g["card"]: g for g in list_gpus(drm)}
    assert gpus["card1"]["vendor"] == "0x1002"
    assert gpus["card0"]["vendor"] == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_shell.py tests/test_detect.py -k "(setup and system_one) or reads_device_vendor" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`pylib/detect.py` — in each GPU dict from `list_gpus`, add `"vendor": (device / "vendor").read_text().strip()` when that file exists, else `""` (AMD ROCm GPUs report `0x1002`).

`scripts/system-one-cache-check.py` — stdlib + `huggingface_hub` + `laya` (runs inside the System One image). One manifest for every checkpoint:

```python
#!/usr/bin/env python3
"""Prefetch or verify the snapshots laya 0.4.2 Agent.__init__ downloads."""
from __future__ import annotations

import os
import sys

from huggingface_hub import snapshot_download
from laya.revisions import resolve_revision
from laya.router import DEFAULT_MODELS, normalise_name

# Bundle repo convaiinnovations/laya (DEFAULT_MODELS; subfolder None |
# multilingual | typed-decisions). allow_patterns match Agent.__init__.
# LAYA_REVISION=reviewed → 55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851.
# Unused: laya-multilingual e4e9ddf21a7b1903b7acffd8814ad4307bf63a67,
# laya-typed-decisions 1a793eb568e6718f15941d08f85432581df534e3.


def _configured_names() -> list[str]:
    raw = os.environ.get("LAYA_MODELS", "").strip()
    try:
        names = [normalise_name(part) for part in raw.split(",") if part.strip()]
    except ValueError as error:
        sys.exit(str(error))
    if not names:
        sys.exit("LAYA_MODELS empty")
    return names

def _download(name: str, *, local_only: bool) -> None:
    repo, sub = DEFAULT_MODELS[name]
    revision = resolve_revision(repo)
    prefix = f"{sub}/" if sub else ""
    kw: dict = {
        "token": os.environ.get("HF_TOKEN") or None,
        "allow_patterns": [prefix + artifact for artifact in (
            "rl_agent_config.json", "model.safetensors", "tokenizer/*", "encoder/*",
        )],
    }
    if revision:
        kw["revision"] = revision
    if local_only:
        kw["local_files_only"] = True
    snapshot_download(repo, **kw)

def prefetch() -> None:
    for name in _configured_names():
        _download(name, local_only=False)
    print("prefetch ok")

def verify() -> None:
    for name in _configured_names():
        _download(name, local_only=True)
    print("weights ok")

if __name__ == "__main__":
    {"prefetch": prefetch, "verify": verify}[sys.argv[1]]()
```

`setup/setup.sh` — near `NO_GPU=`:

```bash
SYSTEM_ONE="${LLM_ENV_SYSTEM_ONE:-}"
KFD_PATH="${LLM_ENV_KFD_PATH:-/dev/kfd}"
```

`system_one.enabled: true` is written only after the image build and the weight prefetch both succeed. KFD, vendor, render-node, build, and prefetch failures write `false` and then `die`.

After `log_info "llm-server (local GPU inference): ${llm_server_enabled}"`, replace the `if [ "$llm_server_enabled" = "true" ]; then` … `else` … `fi` that wraps Steps 2–7 (`setup/setup.sh` lines 60–217) with this layout. Delete each `true #` line after pasting the range it names.

```bash
# Same persisted-default rule as the llm-server prompt above.
system_one_default="n"
[ "$(yq -r '.system_one.enabled' "$CONFIG_PATH" 2>/dev/null)" = "true" ] && system_one_default="y"
[ "$SYSTEM_ONE" = "1" ] && system_one_default="y"
[ "$SYSTEM_ONE" = "0" ] && system_one_default="n"
system_one_answer="$(ask "Enable local System One decisions (Laya on ROCm)? [y/N]: " "$system_one_default")"
case "$system_one_answer" in
    [yY]*) system_one_enabled=true ;;
    *) system_one_enabled=false ;;
esac
DRI_PATH="${LLM_ENV_DRI_PATH:-/dev/dri}"
if [ "$system_one_enabled" != "true" ]; then
    SYSTEM_ONE_ENABLED=false yq -i '.system_one.enabled = (strenv(SYSTEM_ONE_ENABLED) == "true")' "$CONFIG_PATH"
elif [ ! -r "$KFD_PATH" ]; then
    SYSTEM_ONE_ENABLED=false yq -i '.system_one.enabled = (strenv(SYSTEM_ONE_ENABLED) == "true")' "$CONFIG_PATH"
    die "System One requires an AMD GPU with ROCm (/dev/kfd); not found at ${KFD_PATH}"
fi
log_info "system-one (local Laya decisions): ${system_one_enabled}"

if [ "$llm_server_enabled" = "true" ] || [ "$system_one_enabled" = "true" ]; then
    true # REPLACE THIS LINE with the existing Step 2 body, through log_info "selected ${pci} with ..."
    # Step 2's vram_total and vram_budget_ceiling_mib. Step 6 rewrites the same values when llm-server is on (setup.sh:170-177). System-One-only (Step 6 skipped) keeps this write.
    PCI_ADDRESS="$pci" VRAM_TOTAL_MIB="$vram_total" VRAM_BUDGET_CEILING_MIB="$vram_budget_ceiling_mib" yq -i '.gpu.pci_address = strenv(PCI_ADDRESS) | .gpu.vram_total_mib = (strenv(VRAM_TOTAL_MIB) | tonumber) | .gpu.vram_budget_ceiling_mib = (strenv(VRAM_BUDGET_CEILING_MIB) | tonumber)' "$CONFIG_PATH"
    if [ "$system_one_enabled" = "true" ]; then
        gpu_vendor="$(llmenv detect | jq -r --arg p "$pci" '.gpus[] | select(.pci_address == $p) | .vendor // empty')"
        if [ -z "$gpu_vendor" ]; then
            gpu_vendor="$(cat "/sys/bus/pci/devices/${pci}/vendor" 2>/dev/null || echo missing)"
        fi
        if [ "$gpu_vendor" != "0x1002" ]; then
            SYSTEM_ONE_ENABLED=false yq -i '.system_one.enabled = (strenv(SYSTEM_ONE_ENABLED) == "true")' "$CONFIG_PATH"
            die "System One requires an AMD ROCm GPU at ${pci}; vendor is ${gpu_vendor}"
        fi
        system_one_render="$(llmenv detect | jq -r --arg p "$pci" '.gpus[] | select(.pci_address == $p) | .render_node')"
        if [ -z "$system_one_render" ] || [ ! -r "${DRI_PATH}/${system_one_render}" ]; then
            SYSTEM_ONE_ENABLED=false yq -i '.system_one.enabled = (strenv(SYSTEM_ONE_ENABLED) == "true")' "$CONFIG_PATH"
            die "System One: configured GPU ${pci} has no readable render node (${DRI_PATH}/...); re-run make setup"
        fi
        system_one_off() { SYSTEM_ONE_ENABLED=false yq -i '.system_one.enabled = (strenv(SYSTEM_ONE_ENABLED) == "true")' "$CONFIG_PATH"; die "$1"; }
        log_step "System One image and weights"
        system_one_image="$(yq -r '.system_one.image' "$CONFIG_PATH")"
        system_one_models="$(yq -r '.system_one.models' "$CONFIG_PATH")"
        system_one_arch="$(llmenv gfx-arch --render-node "$system_one_render")" || system_one_off "System One: cannot read gfx arch for ${system_one_render}"
        system_one_image_arch=""
        if podman image exists "$system_one_image"; then
            system_one_image_arch="$(podman image inspect --format '{{index .Labels "llm-env.gpu-arch"}}' "$system_one_image" 2>/dev/null || true)"
        fi
        if [ "$system_one_image_arch" != "$system_one_arch" ]; then
            bash "${REPO_DIR}/scripts/system-one-build.sh" || system_one_off "System One image build failed"
        fi
        log_step "Prefetching Laya weights (${system_one_models}) into llm-env-system-one-cache"
        # :z relabels the script bind for Fedora SELinux.
        podman run --rm \
            -e LAYA_REVISION=reviewed \
            -e "LAYA_MODELS=${system_one_models}" \
            -v llm-env-system-one-cache:/home/laya/.cache/huggingface \
            -v "${REPO_DIR}/scripts/system-one-cache-check.py:/opt/system-one-cache-check.py:ro,z" \
            --entrypoint python3 "$system_one_image" /opt/system-one-cache-check.py prefetch \
            || system_one_off "System One weight prefetch failed"
        SYSTEM_ONE_ENABLED=true yq -i '.system_one.enabled = (strenv(SYSTEM_ONE_ENABLED) == "true")' "$CONFIG_PATH"
    fi
    if [ "$llm_server_enabled" = "true" ]; then
        true # paste setup/setup.sh:103-208. Starts at `log_step "Step 3/8  Selecting models"`. Ends at the `fi` closing `if llmenv --config "$CONFIG_PATH" budget` (the line after `die "budget infeasible for models_max=..."`). Not the outer `else` at line 210.
    else
        log_step "Step 3/8  Selecting models"; log_info "(skipped -- llm-server disabled)"
        log_step "Step 4/8  Downloading models"; log_info "(skipped -- llm-server disabled)"
        log_step "Step 5/8  Validating model files"; log_info "(skipped -- llm-server disabled)"
        log_step "Step 6/8  Preparing Vulkan"; log_info "(skipped -- llm-server disabled)"
        log_step "Step 7/8  Checking the VRAM budget"; log_info "(skipped -- llm-server disabled)"
    fi
else
    log_step "Step 2/8  Detecting GPUs"; log_info "(skipped -- llm-server disabled)"
    log_step "Step 3/8  Selecting models"; log_info "(skipped -- llm-server disabled)"
    log_step "Step 4/8  Downloading models"; log_info "(skipped -- llm-server disabled)"
    log_step "Step 5/8  Validating model files"; log_info "(skipped -- llm-server disabled)"
    log_step "Step 6/8  Preparing Vulkan"; log_info "(skipped -- llm-server disabled)"
    log_step "Step 7/8  Checking the VRAM budget"; log_info "(skipped -- llm-server disabled)"
fi
```

Step 8 stays after this `fi`, as it does today.

`AGENTS.md` env var list:

```markdown
- `LLM_ENV_SYSTEM_ONE=1|0` — seed `make setup`'s System One prompt for
  unattended runs. Persisted as `system_one.enabled` in `models.yml`.
  Requires `/dev/kfd` (AMD ROCm).
```

- [ ] **Step 4: Run tests**

Run: `make validate && make test`
Expected: PASS, including pre-existing setup tests. Their stdin is the list in Step 1 (System One answer inserted before GPU/model lines; `LLM_ENV_ASSUME_YES=1` and the prerequisites-failure test unchanged).

- [ ] **Step 5: Commit**

```bash
git add scripts/system-one-cache-check.py pylib/detect.py setup/setup.sh AGENTS.md tests/test_shell.py tests/test_detect.py
git commit -m "feat(setup): opt-in System One prompt, image build, and weight prefetch"
```

---

### Task 7: `make start` requires prepared image and weights, waits for health; endpoints printed

**Files:**
- Modify: `scripts/start.sh` (before `bash "${REPO_DIR}/setup/render-unit.sh"`; after llm-server health wait)
- Modify: `scripts/print-endpoints.sh`
- Modify: `scripts/gpu-status.sh` (early exit; drop cmdline `laya-serve` from offenders), `pylib/detect.py` (`cmdline` on each process)
- Modify: `tools/lib.sh` (one constant)
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `wait_for_health` (`tools/lib.sh:324`). Does **not** call `scripts/system-one-build.sh` (setup owns build + prefetch).
- Produces: `LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS` (default 120; validated like `LLM_ENV_HEALTH_TIMEOUT_SECONDS` in `tools/lib.sh`; load from local cache, not download).

`run_lifecycle_script` already has `system_one_enabled` (Task 2), `env_overrides`, the `bash` stub, the quoted `gpu.pci_address` when System One is enabled, and `uv` arms for `detect` and `gfx-arch` — do not re-add them. Keep `gpu.backend: cpu` (render-unit reads it only to skip device resolution; Task 3's system-one block never reads it). Add these arms only: podman after its printf, then `exit 0` (a failed `[` must not become the stub's status); a `rocr-index` arm on `uv` before the migrate-config case; curl after its printf, then `exit 0`. `wait_for_health` (`tools/lib.sh:327`) curls `/health`.

```python
"case \"$*\" in *'image exists'*) [ \"${PODMAN_IMAGE_MISSING:-}\" = 1 ] && exit 1 ;; *'volume exists llm-env-system-one-cache'*) [ \"${PODMAN_VOLUME_MISSING:-}\" = 1 ] && exit 1 ;; *'system-one-cache-check.py verify'*) [ \"${PODMAN_WEIGHTS_INCOMPLETE:-}\" = 1 ] && exit 1 ;; esac\nexit 0\n"
"case \"$*\" in *' rocr-index '*) printf '0\\n' ;; esac\n"
"case \"$*\" in *'/health'*) [ \"${CURL_HEALTH_FAIL:-}\" = 1 ] && exit 1 ;; esac\nexit 0\n"
```

- [ ] **Step 1: Write the failing tests**

```python
def test_start_dies_when_system_one_image_missing(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(
        tmp_path, "scripts/start.sh", system_one_enabled=True,
        env_overrides={"PODMAN_IMAGE_MISSING": "1"},
    )
    assert result.returncode != 0
    assert "system-one image missing; run 'make setup'" in result.stderr
    assert "scripts/system-one-build.sh" not in calls.read_text()


def test_start_dies_when_system_one_weights_cache_missing(tmp_path: pathlib.Path) -> None:
    result, _config, _calls = run_lifecycle_script(
        tmp_path, "scripts/start.sh", system_one_enabled=True,
        env_overrides={"PODMAN_VOLUME_MISSING": "1"},
    )
    assert result.returncode != 0
    assert "system-one weights" in result.stderr.lower() or "make setup" in result.stderr


def test_start_dies_when_system_one_weights_incomplete(tmp_path: pathlib.Path) -> None:
    result, _config, _calls = run_lifecycle_script(
        tmp_path, "scripts/start.sh", system_one_enabled=True,
        env_overrides={"PODMAN_WEIGHTS_INCOMPLETE": "1"},
    )
    assert result.returncode != 0
    assert "weights" in result.stderr.lower()


def test_start_waits_for_system_one_health_when_image_present(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(
        tmp_path, "scripts/start.sh", system_one_enabled=True,
        env_overrides={"LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS": "1"},
    )
    recorded = calls.read_text()
    assert "system-one-build.sh" not in recorded
    assert "podman image exists localhost/llm-env-system-one:rocm" in recorded
    assert "Waiting for System One" in result.stdout + result.stderr
    assert "curl" in recorded and "/health" in recorded
    assert result.returncode == 0, result.stdout + result.stderr


def test_start_exits_when_system_one_health_never_succeeds(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(
        tmp_path, "scripts/start.sh", system_one_enabled=True, llm_server_enabled=False,
        env_overrides={"LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS": "1", "CURL_HEALTH_FAIL": "1"},
    )
    text = result.stdout + result.stderr
    assert result.returncode != 0
    assert "System One did not become healthy within 1s" in text
    assert "podman compose -f" in text and "logs system-one" in text
    assert "/health" in calls.read_text()


def test_start_skips_system_one_when_disabled(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(tmp_path, "scripts/start.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "llm-env-system-one" not in calls.read_text()
    assert "System One" not in result.stdout


def _print_endpoints(tmp_path: pathlib.Path, system_one_yaml: str):
    real = {name: shutil.which(name) for name in ("yq", "jq", "ip")}
    assert all(real.values())
    commands = tmp_path / "bin"
    commands.mkdir(parents=True)
    for name, path in real.items():
        stub = commands / name
        stub.write_text(f"#!/usr/bin/bash\nexec {path} \"$@\"\n")
        stub.chmod(0o755)
    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text("server:\n  port: 8000\n  mdns_name: llm\n  api_key: fixture-api-key\nllm_server:\n  enabled: false\nomniroute:\n  port: 20128\n  initial_password: fixture-omniroute-password\nremote_setup:\n  port: 20130\nmodels:\n  - alias: a\n    enabled: true\n" + system_one_yaml)
    return subprocess.run(["/usr/bin/bash", "scripts/print-endpoints.sh"], cwd=ROOT, env=os.environ | {"HOME": str(home), "LLM_ENV_CONFIG": str(config), "PATH": f"{commands}:/usr/bin:/bin"}, stdin=subprocess.DEVNULL, text=True, capture_output=True, check=False)


def test_print_endpoints_system_one_follows_the_flag(tmp_path: pathlib.Path) -> None:
    enabled = "system_one:\n  enabled: true\n  port: 8010\n"
    on = _print_endpoints(tmp_path / "on", enabled)
    off = _print_endpoints(tmp_path / "off", enabled.replace("true", "false"))
    assert on.returncode == 0, on.stderr
    assert "http://127.0.0.1:8010/v1/systemone" in on.stdout
    assert "GSD_PATH_JEV=1 JEV_BACKEND=laya JEV_LAYA_URL=http://127.0.0.1:8010 GSD_PATH_JEV_TIMEOUT_SECONDS=20" in on.stdout
    assert off.returncode == 0, off.stderr
    text = off.stdout + off.stderr
    assert "v1/systemone" not in text
    assert "JEV_BACKEND=laya" not in text and "GSD_PATH_JEV" not in text
    assert "JEV_LAYA_URL" not in text
```

```python
def test_gpu_status_reports_the_gpu_when_only_system_one_is_enabled(tmp_path: pathlib.Path) -> None:
    result, _, _ = run_gpu_status_with_stubs(
        tmp_path,
        config_text=(
            "llm_server:\n  enabled: false\n"
            "system_one:\n  enabled: true\n"
            "gpu:\n  pci_address: \"0000:03:00.0\"\n  vram_budget_ceiling_mib: 15565\n"
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no GPU is in use" not in result.stdout + result.stderr
    assert "0000:03:00.0" in result.stdout


def test_gpu_status_does_not_offer_to_migrate_system_one(tmp_path: pathlib.Path) -> None:
    result, _, _ = run_gpu_status_with_stubs(tmp_path, config_text="llm_server:\n  enabled: false\nsystem_one:\n  enabled: true\ngpu:\n  pci_address: \"0000:03:00.0\"\n  vram_budget_ceiling_mib: 15565\n", processes_json='{"processes":[{"pid":9,"comm":"python","exe":"python","cmdline":"laya-serve","vram_mib":4000}]}')
    assert result.returncode == 0 and "no other processes using the dGPU" in result.stdout and "Move these" not in result.stdout
```

`run_gpu_status_with_stubs` (~line 1338) already stubs `detect` with pci `0000:03:00.0`. `MIGRATE_CONFIG_PYSTUB` rewrites the file and keeps `system_one`. Do not change `test_gpu_status_exits_cleanly_when_llm_server_disabled`: a config with no `system_one` key makes `yq` print `null`, which is not `true`, so that test still exits on the old message.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_shell.py -k "system_one and (start or print_endpoints or gpu_status)" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`tools/lib.sh` after the validation at lines 231–232 (`die` is defined at line 226; do not insert at the line 17 assignment):

```bash
# Weights are on disk after make setup; this bounds load-to-GPU only.
LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS="${LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS:-120}"
[[ "$LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] \
    || die "LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS must be a positive integer, got: ${LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS}"
```

`scripts/start.sh` after `llm_server_enabled=...`:

```bash
system_one_enabled="$(yq -r '.system_one.enabled' "$CONFIG_PATH")"
if [ "$system_one_enabled" = "true" ]; then
    system_one_image="$(yq -r '.system_one.image' "$CONFIG_PATH")"
    system_one_models="$(yq -r '.system_one.models' "$CONFIG_PATH")"
    podman image exists "$system_one_image" || die "system-one image missing; run 'make setup'"
    podman volume exists llm-env-system-one-cache \
        || die "system-one weights cache missing; run 'make setup'"
    if ! podman run --rm \
            -e LAYA_REVISION=reviewed -e "LAYA_MODELS=${system_one_models}" \
            -v llm-env-system-one-cache:/home/laya/.cache/huggingface \
            -v "${REPO_DIR}/scripts/system-one-cache-check.py:/opt/system-one-cache-check.py:ro,z" \
            --entrypoint python3 "$system_one_image" /opt/system-one-cache-check.py verify; then
        die "system-one weights incomplete for configured models; run 'make setup'"
    fi
fi
```

(`require_cmd` line gains `podman`.) After the llm-server health block:

```bash
if [ "$system_one_enabled" = "true" ]; then
    log_step "Waiting for System One"
    system_one_port="$(yq -r '.system_one.port' "$CONFIG_PATH")"
    if ! LLM_ENV_HEALTH_TIMEOUT_SECONDS="$LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS" \
            wait_for_health "$system_one_port"; then
        log_error "System One did not become healthy within ${LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS}s"
        echo "  Logs: podman compose -f ${COMPOSE_FILE} logs system-one"
        exit 1
    fi
    log_info "System One is ready"
    # First decision after start pays GPU warm-up (~1.6s); a failure must not fail start.
    warmup_body='{"model":"jev-1","state":{"ticket":"I was charged twice this month, please refund me"},"questions":{"team":{"type":"choice","instructions":"Which team should handle this ticket?","criteria":{"billing":"charges, invoices, refunds","technical":"bugs, crashes, outages","other":"everything else"}}}}'
    if ! curl -fsS --max-time 10 -o /dev/null \
            -H "Content-Type: application/json" \
            --data-raw "$warmup_body" \
            "http://127.0.0.1:${system_one_port}/v1/systemone" 2>/dev/null; then
        log_warn "System One warm-up request failed"
    fi
fi
```

`scripts/print-endpoints.sh`, after the OmniRoute block:

```bash
if [ "$(yq -r '.system_one.enabled' "$CONFIG_PATH")" = "true" ]; then
    system_one_port="$(yq -r '.system_one.port' "$CONFIG_PATH")"
    echo
    echo "  ${BOLD}System One (Laya)${NC}"
    echo "  Local:   http://127.0.0.1:${system_one_port}/v1/systemone  (loopback only, no auth)"
    echo "  Skill:   GSD_PATH_JEV=1 JEV_BACKEND=laya JEV_LAYA_URL=http://127.0.0.1:${system_one_port} GSD_PATH_JEV_TIMEOUT_SECONDS=20"
fi
```

`scripts/gpu-status.sh`, replace the llm-server-only early exit (`scripts/gpu-status.sh` ~27–31):

```bash
llm_server_enabled="$(yq -r '.llm_server.enabled' "$CONFIG_PATH")"
system_one_enabled="$(yq -r '.system_one.enabled' "$CONFIG_PATH")"
if [ "$llm_server_enabled" != "true" ] && [ "$system_one_enabled" != "true" ]; then
    log_info "llm-server is disabled (llm_server.enabled: false); no GPU is in use"
    exit 0
fi
```

When only System One is on, fall through to the existing diagnostic. On the jq filter (`scripts/gpu-status.sh` ~75), also drop a process when `(.cmdline // "") | contains("laya-serve")` (System One’s own workload; do not offer to migrate it). `processes_on_render_node` adds `cmdline` (NUL bytes in `/proc/<pid>/cmdline` become spaces, `""` if missing). Add that key to every equality assertion in `tests/test_detect.py`.

- [ ] **Step 4: Run tests**

Run: `make validate && make test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/start.sh scripts/print-endpoints.sh scripts/gpu-status.sh pylib/detect.py tools/lib.sh tests/test_shell.py tests/test_detect.py
git commit -m "feat(start): require setup-prepared system-one image and health gate"
```

---

### Task 8: `check-setup` and `check-server` sections

**Files:**
- Modify: `scripts/check-setup.sh` (`record_section_skip` ~line 83; new section before `log_step "Compose file"`)
- Modify: `scripts/check-server.sh` (new section before the final `Results`)
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `record_command`, `record_section_skip`, `request_record`, `request_failed` (existing helpers).

- [ ] **Step 1: Write the failing tests**

```python
def test_check_setup_skips_system_one_when_disabled(tmp_path: pathlib.Path) -> None:
    result, _calls, _ = run_check_setup_with_stubs(tmp_path)
    assert "Verdict: SKIP reason=system-one is disabled" in result.stdout + result.stderr


def test_check_setup_checks_kfd_and_image_when_system_one_enabled(tmp_path: pathlib.Path) -> None:
    result, calls, _ = run_check_setup_with_stubs(
        tmp_path, system_one_enabled=True, llm_server_enabled=False,
    )
    recorded = calls.read_text()
    assert "podman image exists localhost/llm-env-system-one:rocm" in recorded
    assert "identity=ROCm device" in result.stdout + result.stderr


def test_check_setup_checks_system_one_weights_cache_when_enabled(tmp_path: pathlib.Path) -> None:
    _result, calls, _ = run_check_setup_with_stubs(
        tmp_path, system_one_enabled=True, llm_server_enabled=False,
    )
    recorded = calls.read_text()
    assert "podman volume exists llm-env-system-one-cache" in recorded
    assert "system-one-cache-check.py:ro,z" in recorded
    assert "verify" in recorded


def test_check_setup_fails_system_one_when_rocr_index_mismatches(tmp_path: pathlib.Path) -> None:
    result, _calls, _ = run_check_setup_with_stubs(
        tmp_path,
        system_one_enabled=True,
        llm_server_enabled=False,
        rocr_index="1",
    )
    assert result.returncode != 0
    assert "rocr" in (result.stdout + result.stderr).lower()


def test_check_setup_skips_model_files_when_llm_server_disabled_and_gguf_absent(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, models_dir = run_check_setup_with_stubs(
        tmp_path, llm_server_enabled=False, real_gguf=True,
    )
    assert not (models_dir / "first.gguf").exists()
    assert not (models_dir / "second.gguf").exists()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "validate-gguf" not in calls.read_text()
    assert result.stderr.count("Verdict: SKIP reason=llm-server is disabled") == 6
```

`test_check_setup_skips_gpu_and_model_sections_when_llm_server_disabled` (`tests/test_shell.py` ~4476) currently requires `Verdict: PASS identity=GGUF validation` and exactly 5 `SKIP reason=llm-server is disabled`, and its docstring says Model files still run. Change that test: drop the GGUF PASS assertion, expect the skip count to be 6, and say Compose file still runs while Model files is skipped. The helper's `uv` stub today answers `validate-gguf` with `{"results":[]}` and exit 0, so a missing GGUF cannot fail the section. Add keyword `real_gguf: bool = False`. Set `REAL_UV` to `shutil.which("uv")` and `REAL_GGUF` to `"1"` when `real_gguf` else `"0"` (the helper does not set `REAL_UV` today). In the `uv` stub, replace the `*' validate-gguf'*)` arm with:

```bash
*' validate-gguf'*)
  if [ "$REAL_GGUF" = 1 ]; then exec "$REAL_UV" "$@"; fi
  printf '%s\n' '{"results":[]}' ;;
```

`run_check_setup_with_stubs` never creates `tmp_path / "models" / "first.gguf"` or `second.gguf`. Its written config (`tests/test_shell.py` ~4178–4182) has no `gpu.backend`, `vram_total_mib`, or `reserve_mode`; `migrate_config` does not default them and `validate_config` rejects them (`pylib/config.py:256-260`), so real `llmenv validate-gguf` dies in `require_valid_config` (`llmenv.py:483`) with exit 1 and a config-validation error (including `gpu.backend must be one of ('vulkan', 'cpu')`) before `validate_gguf` runs — the message is never `not found:`. If the Model files section still runs, this test's returncode is nonzero.

Extend `run_check_setup_with_stubs` (`tests/test_shell.py` ~4063) with `system_one_enabled: bool = False` (and `real_gguf` above). Its `detected_gpu` object already has `pci_address` and optional `render_node` (default `"renderD128"`); add `"vendor": "0x1002"` on that object. When `system_one_enabled` is true:

- Append to the written config: `system_one.enabled: true`, `port: 8010`, `image: localhost/llm-env-system-one:rocm`, `models: english`, `vram_reserve_mib: 4096`.
- `kfd = tmp_path / "kfd"`; `kfd.touch()`; set `LLM_ENV_KFD_PATH` to that path.
- Keyword `rocr_index: str = "0"` sets env `ROCR_INDEX`. In the `uv` stub, before `detect`: `*' rocr-index '*) printf '%s\n' "$ROCR_INDEX" ;;`
- Write `$HOME/.config/llm-env/docker-compose.yml` (`COMPOSE_FILE`; `HOME` is already `tmp_path / "home"`) with `services.system-one.devices` `[/dev/kfd, /dev/dri:/dev/dri]` and `environment.ROCR_VISIBLE_DEVICES: "0"`. `rocr_index="1"` disagrees with that pin.

`run_check_server(..., system_one_enabled: bool = False, system_one_wrong_choice: bool = False)` appends `system_one.enabled` / `port: 8010` and sets `HEALTH_BODY` to `{"status":"ok","device":"cuda"}` when enabled (else `{"status":"ok"}`) and `SYSTEM_ONE_WRONG_CHOICE` to `1` or `0`. Define `laya_answer` after `write_response`; these arms replace `*/health` (before `*) exit 64`):

```bash
laya_answer() { # $1 = "\"<id>\":{...}" — usage/routing envelope is the appendix
  write_response "{\"model\":\"laya-rl-agent\",\"answers\":{$1},\"usage\":{\"input_tokens\":120,\"output_tokens\":0,\"state_tokens\":13,\"state_tokens_dropped\":0,\"truncated\":false,\"truncated_questions\":[]},\"routing\":{\"model\":\"english\",\"repo\":\"convaiinnovations/laya\",\"reason\":\"English Latin text\"}}"
}
# case "$url" arms:
  */v1/systemone)
    choice=billing; [ "${SYSTEM_ONE_WRONG_CHOICE:-0}" = 1 ] && choice=other
    case "$data" in
      *charged\ twice*) laya_answer "\"team\":{\"type\":\"choice\",\"choice\":\"${choice}\",\"probabilities\":{\"billing\":0.98,\"technical\":0.01,\"other\":0.01},\"answer_confidence\":0.98,\"action\":{\"act_probability\":1.0}}" ;;
      *crashes\ every\ time*) laya_answer "\"team\":{\"type\":\"choice\",\"choice\":\"technical\",\"probabilities\":{\"billing\":0.02,\"technical\":0.95,\"other\":0.03},\"answer_confidence\":0.95,\"action\":{\"act_probability\":1.0}}" ;;
      *production\ database*) laya_answer "\"destructive\":{\"type\":\"noul\",\"noul\":0.77,\"action\":{\"act_probability\":1.0}}" ;;
      *foo\ to\ bar*) laya_answer "\"destructive\":{\"type\":\"noul\",\"noul\":0.18,\"action\":{\"act_probability\":1.0}}" ;;
      *whole\ site\ is\ down*) laya_answer "\"urgency\":{\"type\":\"score\",\"legend\":{\"0\":\"not urgent\",\"1\":\"soon\",\"2\":\"blocking\"},\"probabilities\":{\"0\":0.0,\"1\":0.01,\"2\":0.99},\"action\":{\"act_probability\":1.0}}" ;;
      *) write_response '{"error":"unmatched systemone case"}'; printf '500'; exit 0 ;;
    esac
    printf '200' ;;
  */health) write_response "$HEALTH_BODY"; printf '200' ;;
```

```python
def test_check_server_system_one_passes_when_stubbed(tmp_path: pathlib.Path) -> None:
    result, _calls = run_check_server(tmp_path, {"gemma4": "ok", "ornith": "ok"}, system_one_enabled=True, llm_server_enabled=False)
    text = result.stdout + result.stderr
    for identity in ("system-one health", "system-one choice billing", "system-one choice technical", "system-one noul destructive", "system-one noul safe", "system-one score urgent"):
        assert f"Verdict: PASS identity={identity}" in text


def test_check_server_system_one_fails_when_choice_is_wrong(tmp_path: pathlib.Path) -> None:
    result, _calls = run_check_server(tmp_path, {"gemma4": "ok", "ornith": "ok"}, system_one_enabled=True, llm_server_enabled=False, system_one_wrong_choice=True)
    text = result.stdout + result.stderr
    assert result.returncode != 0 and "Verdict: FAIL stage=answer identity=system-one choice billing" in text
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_shell.py -k "check and system_one" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`record_section_skip` gets an optional 3rd parameter for the command text: `log_command "${3:-not run: llm-server is disabled}"`. The system-one disabled branch is the one-line form, not that helper: one `log_warn` and no Identity:/Command:/Expectation: blocks, so `test_check_setup_prints_concise_pass_rows_by_default` stays green on a default run.

`scripts/check-setup.sh` runs `log_step "Model files"` and `validate-gguf` unconditionally (`scripts/check-setup.sh` ~233–237), after `llm_server_enabled` is already set. Wrap that block:

```bash
log_step "Model files"
if [ "$llm_server_enabled" != "false" ]; then
    record_command "GGUF validation" \
        "uv run ${REPO_DIR}/llmenv.py --config ${CONFIG_PATH} validate-gguf --models-dir ${MODELS_DIR}" "" \
        "exit status: 0" "" 0 "Command stdout" "Command stderr" \
        uv run "${REPO_DIR}/llmenv.py" --config "$CONFIG_PATH" validate-gguf --models-dir "$MODELS_DIR" || true
else
    record_section_skip "GGUF validation" "llm-server is disabled"
fi
```

`scripts/check-setup.sh`, before `log_step "Compose file"`:

`check-setup.sh` has no `bad` helper. A failing check goes through `record_command`, which increments `FAIL` on a non-zero status (`scripts/check-setup.sh` ~53–57). The `jq` predicate and the ROCR comparison are the commands `record_command` runs (a function), not a pipe around `record_command`.

```bash
system_one_amd_gpu() {
    uv run "${REPO_DIR}/llmenv.py" detect | jq -e --arg p "$1" \
        '.gpus[] | select(.pci_address == $p and .vendor == "0x1002" and .render_node != "")'
}

system_one_rocr_matches() {
    local detected expected
    detected="$(uv run "${REPO_DIR}/llmenv.py" detect | jq -r --arg p "$1" '.gpus[] | select(.pci_address == $p) | .render_node')"
    expected="$(uv run "${REPO_DIR}/llmenv.py" rocr-index --render-node "$detected")"
    [ -n "$detected" ] && [ "$expected" = "$(yq -r '.services["system-one"].environment.ROCR_VISIBLE_DEVICES' "$COMPOSE_FILE")" ]
}

system_one_enabled="$(yq -r '.system_one.enabled' "$CONFIG_PATH" 2>/dev/null)"
log_step "System One"
if [ "$system_one_enabled" = "true" ]; then
    system_one_image="$(yq -r '.system_one.image' "$CONFIG_PATH")"
    kfd_path="${LLM_ENV_KFD_PATH:-/dev/kfd}"
    pci="$(yq -r '.gpu.pci_address' "$CONFIG_PATH")"
    record_command "ROCm device" "test -r ${kfd_path}" "" \
        "exit status: 0" "" 0 "Command stdout" "Command stderr" test -r "$kfd_path" || true
    record_command "ROCm GPU" \
        "uv run ${REPO_DIR}/llmenv.py detect | jq -e --arg p ${pci} '.gpus[] | select(.pci_address == \$p and .vendor == \"0x1002\" and .render_node != \"\")'" \
        "" "exit status: 0" "" 0 "Command stdout" "Command stderr" \
        system_one_amd_gpu "$pci" || true
    if [ ! -f "$COMPOSE_FILE" ]; then
        record_section_skip "system-one ROCR index" "compose not rendered yet (run make start)" "not run: compose not rendered yet (run make start)"
    elif [ "$(yq -r '.services["system-one"].environment.ROCR_VISIBLE_DEVICES' "$COMPOSE_FILE")" = "null" ]; then
        record_section_skip "system-one ROCR index" "compose not rendered with system-one yet (run make start)" "not run: compose not rendered with system-one yet (run make start)"
    else
        record_command "system-one ROCR index" \
            "llmenv rocr-index for the detected render node equals ROCR_VISIBLE_DEVICES in ${COMPOSE_FILE}" \
            "" "exit status: 0" "" 0 "Command stdout" "Command stderr" \
            system_one_rocr_matches "$pci" || true
    fi
    record_command "system-one image" "podman image exists ${system_one_image}" "" \
        "exit status: 0" "" 0 "Command stdout" "Command stderr" \
        podman image exists "$system_one_image" || true
    record_command "system-one weights cache" "podman volume exists llm-env-system-one-cache" "" \
        "exit status: 0" "" 0 "Command stdout" "Command stderr" \
        podman volume exists llm-env-system-one-cache || true
    system_one_models="$(yq -r '.system_one.models' "$CONFIG_PATH")"
    record_command "system-one weights present" \
        "podman run --rm -e LAYA_REVISION=reviewed -e LAYA_MODELS=${system_one_models} -v llm-env-system-one-cache:/home/laya/.cache/huggingface -v ${REPO_DIR}/scripts/system-one-cache-check.py:/opt/system-one-cache-check.py:ro,z --entrypoint python3 ${system_one_image} /opt/system-one-cache-check.py verify" \
        "" "exit status: 0" "" 0 "Command stdout" "Command stderr" \
        podman run --rm \
            -e LAYA_REVISION=reviewed -e "LAYA_MODELS=${system_one_models}" \
            -v llm-env-system-one-cache:/home/laya/.cache/huggingface \
            -v "${REPO_DIR}/scripts/system-one-cache-check.py:/opt/system-one-cache-check.py:ro,z" \
            --entrypoint python3 "$system_one_image" /opt/system-one-cache-check.py verify || true
else
    log_warn "Verdict: SKIP reason=system-one is disabled"
fi
```

`scripts/check-server.sh`, before the final `echo; log_step "Results..."`:

```bash
if [ "$(yq -r '.system_one.enabled' "$CONFIG_PATH" 2>/dev/null)" = "true" ]; then
    system_one_base="http://127.0.0.1:$(yq -r '.system_one.port' "$CONFIG_PATH")"
    log_step "System One"
    request_record "system-one health" \
        "curl --silent --show-error --max-time 10 ${system_one_base}/health" \
        "" "HTTP status: 200 and device=cuda" -- \
        curl --silent --show-error --max-time 10 "${system_one_base}/health"
    log_block "Expectation" "$REQUEST_EXPECTATION"
    if request_failed 200 "system-one health"; then
        :
    elif [ "$(jq -r '.device' < "$REQUEST_BODY_FILE")" != cuda ]; then
        close_diagnostic_capture 1
        bad "Verdict: FAIL stage=device identity=system-one health reason=not on GPU"
    else
        close_diagnostic_capture 0
        ok "Verdict: PASS identity=system-one health on GPU"
    fi
    system_one_decision() { # $1 identity, $2 body, $3 jq -e predicate, $4 expectation. One request; never batch questions.
        request_record "$1" "curl --silent --show-error --max-time 10 -H 'Content-Type: application/json' --data-raw '${2}' ${system_one_base}/v1/systemone" "$2" "$4" -- \
            curl --silent --show-error --max-time 10 -H "Content-Type: application/json" --data-raw "$2" "${system_one_base}/v1/systemone"
        log_block "Expectation" "$REQUEST_EXPECTATION"
        if request_failed 200 "$1"; then :
        elif ! jq -e "$3" < "$REQUEST_BODY_FILE" >/dev/null; then
            close_diagnostic_capture 1
            bad "Verdict: FAIL stage=answer identity=${1} expected=${4}"
        else close_diagnostic_capture 0; ok "Verdict: PASS identity=${1}"; fi
    }
    choice_q='"team":{"type":"choice","instructions":"Which team should handle this ticket?","criteria":{"billing":"charges, invoices, refunds","technical":"bugs, crashes, outages","other":"everything else"}}'
    noul_q='"destructive":{"type":"noul","instructions":"Is this action destructive or irreversible?"}'
    system_one_decision "system-one choice billing" "{\"model\":\"jev-1\",\"state\":{\"ticket\":\"I was charged twice this month, please refund me\"},\"questions\":{${choice_q}}}" '.answers.team.choice == "billing"' "HTTP 200 and answers.team.choice == billing (measured p 0.98)"
    system_one_decision "system-one choice technical" "{\"model\":\"jev-1\",\"state\":{\"ticket\":\"The app crashes every time I open the settings page\"},\"questions\":{${choice_q}}}" '.answers.team.choice == "technical"' "HTTP 200 and answers.team.choice == technical (measured p 0.95)"
    system_one_decision "system-one noul destructive" "{\"model\":\"jev-1\",\"state\":{\"command\":\"Delete the production database now and skip the backup\"},\"questions\":{${noul_q}}}" '.answers.destructive.noul > 0.5' "HTTP 200 and answers.destructive.noul > 0.5 (measured 0.77)"
    system_one_decision "system-one noul safe" "{\"model\":\"jev-1\",\"state\":{\"command\":\"Rename the variable foo to bar in utils.py\"},\"questions\":{${noul_q}}}" '.answers.destructive.noul < 0.5' "HTTP 200 and answers.destructive.noul < 0.5 (measured 0.18)"
    system_one_decision "system-one score urgent" '{"model":"jev-1","state":{"ticket":"Our whole site is down and no customer can pay right now"},"questions":{"urgency":{"type":"score","instructions":"How urgent is this?","criteria":["not urgent","soon","blocking"]}}}' '(.answers.urgency.probabilities | to_entries | max_by(.value) | .key) == "2"' 'HTTP 200 and argmax of answers.urgency.probabilities is "2" (measured 0.99)'
else
    log_step "System One"
    log_warn "Verdict: SKIP identity=system-one reason=system-one is disabled"
fi
```

(Confirm `request_record` exposes the response body as `REQUEST_BODY_FILE` — it is used that way in the OmniRoute section; if the variable name differs, use the one that section uses.)

- [ ] **Step 4: Run tests**

Run: `make validate && make test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/check-setup.sh scripts/check-server.sh scripts/system-one-cache-check.py tests/test_shell.py
git commit -m "feat(checks): offline and online System One checks"
```

---

### Task 9: `make clean` and documentation

**Files:**
- Modify: `scripts/clean.sh`
- Modify: `.agents/architecture.md` (Files table + Container Lifecycle paragraph), `AGENTS.md` (commands list), `docker-compose.yml.example` (annotated `system-one` service)
- Test: `tests/test_shell.py`

- [ ] **Step 1: Write the failing test**

```python
def test_clean_removes_the_system_one_image(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(
        tmp_path, "scripts/clean.sh", system_one_enabled=True, llm_server_enabled=False,  # daemon-reload stub exits 66 unless LLM_SERVER_ENABLED=false (tests/test_shell.py:4632)
        env_overrides={"LLM_ENV_ASSUME_YES": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text()
    assert "podman rmi -f localhost/llm-env-system-one:rocm" in recorded
    assert "podman volume rm -f llm-env-system-one-cache" in recorded
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_shell.py -k clean_removes_the_system_one -v` → FAIL.

- [ ] **Step 3: Implement** — in `scripts/clean.sh`, read `configured_system_one_image="$(yq -r '.system_one.image // ""' "$CONFIG_PATH")"` alongside the other images (before `rm -f "$CONFIG_PATH"`), list it in the "This removes" text and mention the Laya weights volume (`llm-env-system-one-cache`, ~0.8 GB — removed by `make clean` and re-downloaded on the next `make setup`; unlike llama.cpp model files, which `make clean` keeps and `make prune` deletes). After the other `podman rmi` calls:

```bash
if [ -n "$configured_system_one_image" ] && [ "$configured_system_one_image" != null ]; then
    podman rmi -f "$configured_system_one_image" 2>/dev/null || true
fi
podman volume rm -f llm-env-system-one-cache 2>/dev/null || true
```

(`make setup` may create the volume even if the stack never started.)

Docs, all of these (a Files-table row plus one paragraph leaves the rest of `.agents/architecture.md` describing three services and two named volumes):

- **Files table:** rows for `scripts/system-one-build.sh`, `scripts/system-one-cache-check.py`, and `docker/system-one/Dockerfile`.
- **Service count** (the "compose file also runs `omniroute`" paragraph and "A third compose service, `remote-setup`"): when both flags are true the stack is four services — `llm-server`, `omniroute`, `remote-setup`, `system-one`. `system-one` has no `depends_on`. OmniRoute does not proxy `/v1/systemone`.
- **`llm_server.enabled: false` paragraph:** GPU detection is skipped only when `system_one.enabled` is also false. When System One is on, detection still runs (render node → `rocr_visible_index`). llama.cpp model/Vulkan/VRAM steps stay skipped. `check-setup` skips the Model files GGUF section in that mode.
- **Topology diagram:** fourth box `system-one`, published only at `127.0.0.1:8010` (not on the LAN arrows). Named volume `system-one-cache` → `/home/laya/.cache/huggingface` (podman name `llm-env-system-one-cache`).
- **"What survives what" table:** columns for the `system-one` container and `llm-env-system-one-cache`. The cache is kept across stop, crash, and reboot, same as `omniroute-data` and `remote-setup-data`. `make clean` destroys it; the next `make setup` re-downloads the weights. Bind-mounted models/presets stay untouched.
- **Persistence paragraph** after that table: name `llm-env-system-one-cache` as the third named volume, with the same survive/destroy rule.
- **`docker-compose.yml.example`:** annotated `system-one` service (image `localhost/llm-env-system-one:rocm`, `127.0.0.1:8010:8000`, devices `/dev/kfd` and `/dev/dri:/dev/dri`, `ROCR_VISIBLE_DEVICES`, `security_opt: [label=disable]`, volume `system-one-cache:/home/laya/.cache/huggingface`, `LAYA_MODELS` / `LAYA_REVISION=reviewed`, healthcheck, `cpus` / `mem_limit`) and `volumes.system-one-cache: {name: llm-env-system-one-cache}`. Comment that `make clean` deletes this named volume, unlike the model bind mounts.
- `make system-one-build` in the AGENTS.md commands list.

- [ ] **Step 4: Run tests** — `make validate && make test` → PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/clean.sh .agents/architecture.md AGENTS.md docker-compose.yml.example tests/test_shell.py
git commit -m "docs: document system-one lifecycle; clean removes its image"
```

---

### Task 10: Live end-to-end on this host

No code. Stops when `system_one.port` is not 8010. Leaves System One enabled and healthy on 8010. Plan B needs System One enabled and healthy on 8010. Step 9 is the idle-CPU and latency regression gate on that container; on failure stop and report (no env-var hunting).

- [ ] **Step 1: Remove the research leftovers, then Step 2 builds the real image.** Each removal tolerates an already-absent artifact: `podman rm -f laya 2>/dev/null || true`; `podman rmi -f localhost/laya-rocm 2>/dev/null || true`; `podman rmi -f localhost/laya-rocm72-test 2>/dev/null || true`; `podman volume rm -f laya-cache 2>/dev/null || true`. Then assert all four are gone (`podman container exists laya`, `podman image exists localhost/laya-rocm`, `podman image exists localhost/laya-rocm72-test`, and `podman volume exists laya-cache` each exit nonzero).
- [ ] **Step 2: Setup** — `LLM_ENV_SYSTEM_ONE=1 make setup` → `system_one.enabled: true`; observe image build (if missing) and weight prefetch into `llm-env-system-one-cache` (setup sub-steps).
- [ ] **Step 3: Start** — `make start` → "System One is ready" within 120 s (no weight download); `make status` shows the System One endpoint.
- [ ] **Step 4: Checks** — `make check-setup` and `make check-server` → System One verdicts PASS.
- [ ] **Step 5: VRAM delta** — delta on the configured GPU ≤ `system_one.vram_reserve_mib`. Same fields setup Step 2 reads (`vram_used_mib` from `uv run llmenv.py detect`). `systemctl --user stop llm-server.service` does not isolate System One: that unit owns the whole compose stack, so a restart still starts llama.cpp and the delta is not System One's. Run the gate with `llm_server.enabled: false` and `system_one.enabled: true` so only System One allocates on the GPU. The EXIT trap restores the backup and restarts, including when a later command exits nonzero. Install it immediately after the backup.

```bash
# Run with bash, not sourced: the trap exits this process.
set -euo pipefail
config="${LLM_ENV_CONFIG:-${HOME}/.config/llm-env/models.yml}"
backup="${config}.vram-gate.bak"
cp "$config" "$backup"
restore_and_restart() {
    local rc=$?
    trap - EXIT
    cp "$backup" "$config" 2>/dev/null || true
    rm -f "$backup"
    make restart || true
    exit "$rc"
}
trap restore_and_restart EXIT
pci="$(yq -r '.gpu.pci_address' "$config")"
reserve="$(yq -r '.system_one.vram_reserve_mib' "$config")"
port="$(yq -r '.system_one.port' "$config")"
LLM_SERVER_ENABLED=false SYSTEM_ONE_ENABLED=true yq -i \
    '.llm_server.enabled = (strenv(LLM_SERVER_ENABLED) == "true") | .system_one.enabled = (strenv(SYSTEM_ONE_ENABLED) == "true")' \
    "$config"
make stop
before="$(uv run llmenv.py detect | jq -r --arg p "$pci" '.gpus[] | select(.pci_address == $p) | .vram_used_mib')"
make start
status=0
curl --silent --show-error --fail --max-time 10 \
    -H 'Content-Type: application/json' \
    --data-raw '{"model":"jev-1","state":{"ticket":"I was charged twice this month, please refund me"},"questions":{"team":{"type":"choice","instructions":"Which team should handle this ticket?","criteria":{"billing":"charges, invoices, refunds","support":"bugs, crashes, how-to"}}}}' \
    "http://127.0.0.1:${port}/v1/systemone" >/dev/null || status=1
access="$(podman logs system-one | rg 'POST /v1/systemone' | head -1 || true)"; echo "system-one access: ${access}"; [ -n "$access" ] || status=1
after="$(uv run llmenv.py detect | jq -r --arg p "$pci" '.gpus[] | select(.pci_address == $p) | .vram_used_mib')"
delta=$((after - before))
echo "system-one VRAM delta: ${delta} MiB (reserve ${reserve} MiB)"
[ "$delta" -le "$reserve" ] || status=1
[ "$status" -eq 0 ]
```
- [ ] **Step 6: Only english loaded** — `make stop && make start` again; `/health` lists only `english` loaded.
- [ ] **Step 7: Disabled mode** — flip `system_one.enabled: false`, `make restart` → no `system-one` container; `make check-server` shows SKIP.
- [ ] **Step 8: Leave System One enabled and healthy** — Read `system_one.port` and stop if it is not 8010. Set `system_one.enabled: true` and leave every other key as Step 5 restored it. `make restart`, then health on that port must succeed. This restart clears the Step 5 access log. Plan B needs System One enabled and healthy on 8010.

```bash
config="${LLM_ENV_CONFIG:-${HOME}/.config/llm-env/models.yml}"
port="$(yq -r '.system_one.port' "$config")"; [ "$port" = 8010 ] || { echo "system_one.port is ${port}, not 8010; paired plan assumes 8010"; exit 1; }
SYSTEM_ONE_ENABLED=true yq -i '.system_one.enabled = (strenv(SYSTEM_ONE_ENABLED) == "true")' "$config"
make restart
curl --silent --show-error --fail --max-time 10 "http://127.0.0.1:${port}/health"
```

- [ ] **Step 9: Regression gate (idle CPU and latency)** — After Step 8's restart: one decision POST, `sleep 10`, then `measure_idle system-one` < 5% of one core. Then 20 of the same POST; p50 ≤ 0.020 s. On failure print the numbers and stop. Do not set HSA/HIP/AMD env vars.

```bash
measure_idle() {  # usage: measure_idle <container>
  local id f a b
  id="$(podman inspect "$1" --format '{{.Id}}')"
  f="$(fd -p "libpod-${id}.*/cpu\.stat" /sys/fs/cgroup | head -1)"
  a="$(awk '/usage_usec/{print $2}' "$f")"; sleep 10; b="$(awk '/usage_usec/{print $2}' "$f")"
  echo "$1 idle: $(( (b - a) / 100000 ))% of one core"
}
config="${LLM_ENV_CONFIG:-${HOME}/.config/llm-env/models.yml}"; port="$(yq -r '.system_one.port' "$config")"
body='{"model":"jev-1","state":{"ticket":"I was charged twice this month, please refund me"},"questions":{"team":{"type":"choice","instructions":"Which team should handle this ticket?","criteria":{"billing":"charges, invoices, refunds","technical":"bugs, crashes, outages","other":"everything else"}}}}'
post() { curl --silent --show-error --output /dev/null --write-out '%{time_total}\n' --max-time 10 -H 'Content-Type: application/json' --data-raw "$body" "http://127.0.0.1:${port}/v1/systemone"; }
post >/dev/null
sleep 10
idle_line="$(measure_idle system-one)"; echo "$idle_line"
pct="${idle_line##*idle: }"; pct="${pct%%% of one core}"
[ "$pct" -lt 5 ] || { echo "idle CPU ${pct}% >= 5; stop and report. Do not hunt env vars."; exit 1; }
times="$(for _ in $(seq 1 20); do post; done | sort -n)"
p50="$(printf '%s\n' "$times" | awk 'NR==10{a=$1} NR==11{printf "%.6f", (a+$1)/2; exit}')"
printf '%s\n' "$times"; echo "p50 ${p50}s"
awk -v p="$p50" 'BEGIN{exit !(p+0 <= 0.020)}' || { echo "decision p50 ${p50}s > 0.020; stop and report. Do not hunt env vars."; exit 1; }
```

---
## Appendix: Laya responses for the check-server stub

Same envelope as the 2026-10-10 capture: `model` `laya-rl-agent`, `usage` `{"input_tokens":120,"output_tokens":0,"state_tokens":13,"state_tokens_dropped":0,"truncated":false,"truncated_questions":[]}`, `routing` `{"model":"english","repo":"convaiinnovations/laya","reason":"English Latin text"}`. One `answers` entry per request. Winner probabilities are the 2026-10-11 measurements; other masses are stub fillers so the object sums to 1. The curl stub's `laya_answer` argument is `"<id>":<object>` for these objects:

1. `team` (charged twice): `{"type":"choice","choice":"billing","probabilities":{"billing":0.98,"technical":0.01,"other":0.01},"answer_confidence":0.98,"action":{"act_probability":1.0}}`
2. `team` (settings crash): `{"type":"choice","choice":"technical","probabilities":{"billing":0.02,"technical":0.95,"other":0.03},"answer_confidence":0.95,"action":{"act_probability":1.0}}`
3. `destructive` (delete database): `{"type":"noul","noul":0.77,"action":{"act_probability":1.0}}`
4. `destructive` (rename foo to bar): `{"type":"noul","noul":0.18,"action":{"act_probability":1.0}}`
5. `urgency` (site down): `{"type":"score","legend":{"0":"not urgent","1":"soon","2":"blocking"},"probabilities":{"0":0.0,"1":0.01,"2":0.99},"action":{"act_probability":1.0}}`

Health:

```json
{"status":"ok","loaded":["english"],"device":"cuda","device_is_preference":false,"checkpoint_devices":{"english":"cuda"},"cpu_fallbacks":{"english":{"count":0,"last_reason":null}}}
```
