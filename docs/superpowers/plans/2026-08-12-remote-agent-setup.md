# Remote Agent Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve a one-command installer (`curl http://<host>:<port>/setup.sh | bash`) that configures Pi/OpenCode on a remote LAN machine to talk to OmniRoute, gated by a master key (`OMNI_ROUTER_MASTER_KEY` in `.env`) that never leaks the OmniRoute dashboard password — only a scoped API key. Task 5 extends the same never-leak-the-dashboard-password posture to the *local* machine: `make setup-local-llm-agents` is repointed from `llm-server` directly to OmniRoute, via its own distinct scoped key.

**Architecture:** A third `podman-compose` service, `remote-setup`, running `pylib/remote_setup.py` (stdlib-only, reuses `pylib/omniroute.py`'s session-login helpers) via a stock `python:3.13-alpine` image with the whole `pylib/` directory bind-mounted read-only. Exactly two HTTP routes, matching the design's two-endpoint contract: `GET /setup.sh` (public, Host-header-aware script generator) and `GET /config` (master-key-gated, idempotently issues/reuses a scoped OmniRoute API key). `setup/update-opencode-config.mjs` is also bind-mounted read-only (a single extra file mount, not a route) so `render_setup_script()` can read its content at script-render time and embed it verbatim into the `/setup.sh` response via a quoted bash heredoc — the remote machine gets the updater's code as part of the single `/setup.sh` fetch, never a second HTTP round-trip. OmniRoute's own port binding changes from `127.0.0.1` to `0.0.0.0` so it's genuinely LAN-reachable.

**Tech Stack:** Python stdlib only (`http.server`, `hmac`, `json`, `urllib.request` via the reused `pylib.omniroute` helpers — no new dependency; the `remote-setup` container runs `python:3.13-alpine`, matching the design's Architecture section), pytest, bash (generated installer script, tested via `tests/test_shell.py`-style subprocess execution against the actual `pylib/remote_setup.py` server).

## Global Constraints

- No new Python dependency anywhere in this plan — `pylib/remote_setup.py` uses only the standard library plus `pylib.omniroute`'s existing helpers.
- OmniRoute's `/api/keys`-issued keys are used for the remote credential — **never** the dashboard password. Verified live: such a key is accepted by `POST /v1/chat/completions` and rejected by `GET /api/providers` (`403 AUTH_001 "Invalid management token"`).
- `GET /api/keys` never re-reveals a previously created key's raw value (only `keyPreview`, last 4 chars) — the raw value is returned once, at `POST /api/keys` creation time. This is why a persistent cache file (not a re-fetch) is required for idempotent reuse.
- Per `docs/superpowers/specs/2026-08-12-remote-agent-setup-design.md`: `.env` (repo root, already gitignored) is the **only** place `OMNI_ROUTER_MASTER_KEY` lives — never written into `models.yml` or persisted anywhere by `migrate_config`. A missing `.env` is not a hard error anywhere in this plan; it degrades to a `503` on `/config` (see Task 3).
- `remote_setup` has no `enabled` toggle — it's always part of the compose stack, matching `omniroute`'s own unconditional inclusion (see design doc).
- OmniRoute's admin API for key issuance is reached via the compose-internal DNS name (`http://omniroute:{port}`), never through the newly-public `0.0.0.0` binding — that binding is only for the remote machine's own traffic.
- The generated installer script reuses `setup/setup-local-llm-agents.sh`'s own OpenCode target-file detection algorithm (`setup/setup-local-llm-agents.sh:149-231`), not a simplified single-file version: every candidate (`config.json`/`opencode.json`/`opencode.jsonc`) that already contains the `local-llm-env` provider is targeted (a machine may have more than one to update); if none does, fall back to the highest-priority *existing* candidate, checked in reverse order (`opencode.jsonc`, then `opencode.json`, then `config.json`), first hit wins; only when none of the three exist at all does it default to creating `opencode.jsonc` fresh. This preserves an existing remote machine's own OpenCode config filename(s) instead of creating a second, out-of-sync `opencode.jsonc` beside them. See Task 3's `SETUP_SCRIPT_TEMPLATE` for the exact detection loop. Every target (both Pi files — `~/.pi/agent/models.json`, `~/.pi/agent/settings.json` — and every matched OpenCode target) is staged to a private temp file first and only `mv -f`'d into place after every staging succeeds, so a mid-run failure (a bad jq transform, a `node` failure) never leaves some targets updated and others untouched — matching `setup-local-llm-agents.sh`'s own "stage everything, then move everything" ordering (see its `prepare_staged_file`/`stage_opencode` functions and the `mv -f --` block at its end).
- `setup-local-llm-agents.sh`'s `$XDG_STATE_HOME/opencode/model.json` (OpenCode's own recent/favorite/variant model-cycling state) handling is **explicitly out of scope** for the generated remote script — it is per-installation runtime UI state, not provider/model configuration, and replicating its version-pinned (`opencode --version` == `1.18.10`) creation path would make the remote installer hard-fail on any remote machine running a different OpenCode version, for a file OmniRoute connectivity does not depend on. OpenCode regenerates it on its own on first use. See the design doc's "Explicitly out of scope" section.

---

## Task 1: `.env.example`, `pylib/dotenv.py`, and `models.yml` schema for `remote_setup`

**Files:**
- Create: `.env.example`
- Create: `pylib/dotenv.py`
- Create: `tests/test_dotenv.py`
- Modify: `pylib/config.py`
- Modify: `tests/test_config.py`
- Modify: `models.yml.example`

**Interfaces:**
- Produces: `read_env_file(path: Path) -> dict[str, str]` in `pylib/dotenv.py` — parses simple `KEY=VALUE` lines, skipping blank lines and lines starting with `#`; returns `{}` if the file doesn't exist.
- Produces: `DEFAULT_REMOTE_SETUP_IMAGE = "docker.io/library/python:3.13-alpine"`, `DEFAULT_REMOTE_SETUP_PORT = 20130` constants in `pylib/config.py`.
- Changes: `migrate_config()` now also defaults `cfg["remote_setup"]["image"]`/`cfg["remote_setup"]["port"]`.
- Changes: `validate_config()` now also validates `remote_setup.{image,port}` (same rules as `omniroute.{image,port}`).
- Consumes: nothing from other tasks (foundational).

- [ ] **Step 1: Write the failing tests for `read_env_file`**

Create `tests/test_dotenv.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.dotenv import read_env_file


def test_read_env_file_parses_simple_assignments(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OMNI_ROUTER_MASTER_KEY=abc123\nOTHER=value\n")
    assert read_env_file(env_file) == {"OMNI_ROUTER_MASTER_KEY": "abc123", "OTHER": "value"}


def test_read_env_file_skips_blank_lines_and_comments(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("\n# a comment\nKEY=value\n  \n# another\n")
    assert read_env_file(env_file) == {"KEY": "value"}


def test_read_env_file_returns_empty_dict_when_file_missing(tmp_path):
    assert read_env_file(tmp_path / "does-not-exist.env") == {}


def test_read_env_file_strips_surrounding_whitespace(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("  KEY = value with spaces  \n")
    assert read_env_file(env_file) == {"KEY": "value with spaces"}
```

The final `tests/test_dotenv.py` has exactly these four test functions:
`test_read_env_file_parses_simple_assignments`,
`test_read_env_file_skips_blank_lines_and_comments`,
`test_read_env_file_returns_empty_dict_when_file_missing`,
`test_read_env_file_strips_surrounding_whitespace`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dotenv.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pylib.dotenv'`

- [ ] **Step 3: Implement `pylib/dotenv.py`**

```python
"""Minimal .env-style file reader.

Deliberately not python-dotenv -- this repo's one Python dependency is
pyyaml (see pylib/omniroute.py's own module docstring for the same
stdlib-only constraint), and the format needed here is a single flat
KEY=VALUE mapping, no interpolation/export/multiline support required.
"""

from __future__ import annotations

from pathlib import Path


def read_env_file(path: Path) -> dict[str, str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    result: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        result[key.strip()] = value.strip()
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dotenv.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Write the failing config schema tests**

Add to `tests/test_config.py`, near the existing
`test_migrate_config_adds_default_omniroute_section`:

```python
def test_migrate_config_adds_default_remote_setup_section():
    cfg = make_cfg()
    cfg.pop("remote_setup", None)
    migrated = migrate_config(cfg)
    assert migrated["remote_setup"] == {
        "image": "docker.io/library/python:3.13-alpine",
        "port": 20130,
    }


def test_migrate_config_preserves_existing_remote_setup_values():
    cfg = make_cfg(remote_setup={"image": "custom/image:tag", "port": 30000})
    migrated = migrate_config(cfg)
    assert migrated["remote_setup"] == {"image": "custom/image:tag", "port": 30000}


def test_config_accepts_valid_remote_setup_section():
    cfg = make_cfg(remote_setup={"image": "docker.io/library/python:3.13-alpine", "port": 20130})
    assert validate_config(cfg) == []


def test_config_without_remote_setup_key_has_no_errors():
    cfg = make_cfg()
    cfg.pop("remote_setup", None)
    assert validate_config(cfg) == []


def test_config_rejects_non_mapping_remote_setup_section():
    cfg = make_cfg(remote_setup=[])
    errors = validate_config(cfg)
    assert any(error == "section remote_setup must be a mapping" for error in errors)


@pytest.mark.parametrize(
    "field,value,expected_error",
    [
        ("image", "", "remote_setup.image must be a non-empty string"),
        ("image", 5, "remote_setup.image must be a non-empty string"),
        ("port", 0, "remote_setup.port must be a positive integer"),
        ("port", "20130", "remote_setup.port must be a positive integer"),
    ],
)
def test_config_rejects_invalid_remote_setup_values(field, value, expected_error):
    remote_setup = {"image": "docker.io/library/python:3.13-alpine", "port": 20130}
    remote_setup[field] = value
    cfg = make_cfg(remote_setup=remote_setup)
    assert expected_error in validate_config(cfg)
```

- [ ] **Step 6: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k remote_setup -v`
Expected: FAIL — `migrate_config`/`validate_config` don't know about
`remote_setup` yet.

- [ ] **Step 7: Add the migration defaults and validation**

In `pylib/config.py`, add two new constants right after the existing
`DEFAULT_OMNIROUTE_PORT = 20128` line:

```python
DEFAULT_REMOTE_SETUP_IMAGE = "docker.io/library/python:3.13-alpine"
DEFAULT_REMOTE_SETUP_PORT = 20130
```

In `migrate_config()`, right after the existing block that sets
`omniroute.setdefault(...)` (the three-line block ending
`omniroute.setdefault("initial_password", "")`, immediately before the
`gpu = cfg.get("gpu")` line), add:

```python
    remote_setup = cfg.setdefault("remote_setup", {})
    if isinstance(remote_setup, dict):
        remote_setup.setdefault("image", DEFAULT_REMOTE_SETUP_IMAGE)
        remote_setup.setdefault("port", DEFAULT_REMOTE_SETUP_PORT)
```

In `validate_config()`, right after the existing
`if "omniroute" in cfg:` block (ends with the
`initial_password must be a string` check), add:

```python
    if "remote_setup" in cfg:
        remote_setup = cfg["remote_setup"]
        if not isinstance(remote_setup, dict):
            errors.append("section remote_setup must be a mapping")
        else:
            rs_image = remote_setup.get("image")
            if not (isinstance(rs_image, str) and rs_image.strip()):
                errors.append("remote_setup.image must be a non-empty string")
            if not _positive_int(remote_setup.get("port")):
                errors.append("remote_setup.port must be a positive integer")
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 9: Update `models.yml.example` and create `.env.example`**

In `models.yml.example`, right after the existing `omniroute:` block
(ends with `initial_password: ""`), add:

```yaml
remote_setup:
  image: docker.io/library/python:3.13-alpine
  port: 20130
```

Create `.env.example` at the repo root:

```
# Master key required to fetch OmniRoute credentials through the
# remote-setup service's /config endpoint (see:
# docs/superpowers/specs/2026-08-12-remote-agent-setup-design.md).
# Generate a random value, e.g.:
#   openssl rand -hex 32
OMNI_ROUTER_MASTER_KEY=
```

- [ ] **Step 10: Run the full config/dotenv test files**

Run: `uv run pytest tests/test_config.py tests/test_dotenv.py -v`
Expected: PASS (all tests)

- [ ] **Step 11: Run the full project gate**

This task edits `.py` files (`pylib/dotenv.py`, `pylib/config.py`), so per
this repo's own convention the full gate — not just this task's own test
files — runs before committing.

Run: `make validate && make test`
Expected: all checks and tests pass (this also re-runs the full test suite,
not just `tests/test_config.py`/`tests/test_dotenv.py`, catching any
unrelated regression before it gets committed).

- [ ] **Step 12: Commit**

```bash
git add .env.example pylib/dotenv.py tests/test_dotenv.py pylib/config.py tests/test_config.py models.yml.example
git commit -m "feat(config): add remote_setup section and .env master-key convention"
```

---

## Task 2: `pylib/compose.py` — the `remote-setup` service and OmniRoute's public binding

**Files:**
- Modify: `pylib/compose.py`
- Modify: `tests/test_compose.py`
- Modify: `llmenv.py:389-397` (`cmd_render_compose`, the `render-compose` argparse subcommand)
- Modify: `tests/test_cli.py` (render-compose tests, if any reference the old signature — see Step 9)
- Modify: `setup/render-unit.sh`

**Interfaces:**
- Consumes (from Task 1): `pylib.dotenv.read_env_file`.
- Changes: `render_compose(cfg, *, models_dir, presets_path, repo_root, omni_router_master_key="")` — two new keyword params. `write_compose(cfg, *, models_dir, presets_path, repo_root, omni_router_master_key="", path)` mirrors it.
- Produces: rendered compose document gains a `services.remote-setup` entry and a top-level `volumes.remote-setup-data: {}` entry; `services.omniroute.ports` changes from `127.0.0.1:{port}:{port}` to `0.0.0.0:{port}:{port}`.
- Produces (for Task 3 to consume at container runtime via env vars): `remote-setup`'s `environment` carries `OMNI_ROUTER_MASTER_KEY`, `REMOTE_SETUP_PORT`, `OMNIROUTE_INTERNAL_URL` (`http://omniroute:{omniroute_port}`), `OMNIROUTE_PORT`, `OMNIROUTE_DASHBOARD_PASSWORD`, `MODELS_JSON` (a JSON array string, one object per enabled model: `{"alias", "ctx_size", "client_max_output_tokens"}`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_compose.py`, update the `compose_dict()` helper (used by
every existing test in the file) to pass the two new required arguments:

```python
def compose_dict(cfg=CFG):
    text = render_compose(
        cfg,
        models_dir="/home/user/llm-workspace/models",
        presets_path="/home/user/.config/llm-env/presets.ini",
        repo_root="/home/user/llm-env",
    )
    return text, yaml.safe_load(text)
```

Update the existing `CFG` dict at the top of the file to add a
`remote_setup` section, right after the existing `"omniroute": {...}` entry:

```python
    "remote_setup": {
        "image": "docker.io/library/python:3.13-alpine",
        "port": 20130,
    },
```

Update the existing `test_omniroute_service_uses_configured_image_and_port`
test — OmniRoute's port binding is changing:

```python
def test_omniroute_service_uses_configured_image_and_port():
    _, document = compose_dict()
    omniroute = document["services"]["omniroute"]
    assert omniroute["image"] == "docker.io/diegosouzapw/omniroute:latest"
    assert omniroute["ports"] == ["0.0.0.0:20128:20128"]
```

The existing `test_omniroute_service_mounts_a_named_data_volume` test asserts
the compose document's top-level `volumes:` is *exactly*
`{"omniroute-data": {}}` — that's now wrong on two counts: the new
`remote-setup-data` volume joins it, and the assertion shouldn't own the
top-level `volumes:` shape at all now that a second service also owns an
entry in it. Change it to:

```python
def test_omniroute_service_mounts_a_named_data_volume():
    _, document = compose_dict()
    assert document["services"]["omniroute"]["volumes"] == ["omniroute-data:/app/data"]
    assert document["volumes"]["omniroute-data"] == {}
```

The existing `test_write_compose_creates_parent_directories` and
`test_write_compose_sets_restrictive_file_mode` tests call `write_compose()`
directly, without `repo_root` — that argument is becoming required (Step 7
below), so both calls raise `TypeError` unless updated. Change:

```python
def test_write_compose_creates_parent_directories(tmp_path):
    target = tmp_path / "nested" / "docker-compose.yml"
    write_compose(CFG, models_dir="/models", presets_path="/presets.ini", path=target)
    assert target.exists()
    assert "llm-server" in yaml.safe_load(target.read_text())["services"]


def test_write_compose_sets_restrictive_file_mode(tmp_path):
    target = tmp_path / "docker-compose.yml"
    write_compose(CFG, models_dir="/models", presets_path="/presets.ini", path=target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
```

to:

```python
def test_write_compose_creates_parent_directories(tmp_path):
    target = tmp_path / "nested" / "docker-compose.yml"
    write_compose(
        CFG, models_dir="/models", presets_path="/presets.ini", repo_root="/repo", path=target
    )
    assert target.exists()
    assert "llm-server" in yaml.safe_load(target.read_text())["services"]


def test_write_compose_sets_restrictive_file_mode(tmp_path):
    target = tmp_path / "docker-compose.yml"
    write_compose(
        CFG, models_dir="/models", presets_path="/presets.ini", repo_root="/repo", path=target
    )
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
```

These three (`test_write_compose_creates_parent_directories`,
`test_write_compose_sets_restrictive_file_mode`, and
`test_omniroute_service_mounts_a_named_data_volume`, all fixed above) plus
`test_omniroute_service_uses_configured_image_and_port` (already fixed
above) are the only existing tests in `tests/test_compose.py` that either
assert an exact `volumes`/`ports` shape or call
`render_compose()`/`write_compose()` directly outside `compose_dict()`.
Verified by reading the file's full pre-change content: it has exactly 22
test functions total. The other 18 all go through `compose_dict()`, which
Step 1 above already updated to pass `repo_root` — no further changes
needed for those 18.

Add new tests, after the existing OmniRoute-related tests in the file:

```python
def test_remote_setup_service_uses_configured_image_and_port():
    _, document = compose_dict()
    remote_setup = document["services"]["remote-setup"]
    assert remote_setup["image"] == "docker.io/library/python:3.13-alpine"
    assert remote_setup["ports"] == ["0.0.0.0:20130:20130"]


def test_remote_setup_service_mounts_pylib_and_a_named_data_volume():
    _, document = compose_dict()
    volumes = document["services"]["remote-setup"]["volumes"]
    assert "/home/user/llm-env/pylib:/app/pylib:ro,z" in volumes
    assert (
        "/home/user/llm-env/setup/update-opencode-config.mjs"
        ":/app/setup/update-opencode-config.mjs:ro,z" in volumes
    )
    assert "remote-setup-data:/app/data" in volumes
    assert document["volumes"]["remote-setup-data"] == {}


def test_remote_setup_service_command_runs_the_module():
    _, document = compose_dict()
    assert document["services"]["remote-setup"]["command"] == [
        "python3", "-m", "pylib.remote_setup",
    ]


def test_remote_setup_service_working_dir_is_app():
    _, document = compose_dict()
    assert document["services"]["remote-setup"]["working_dir"] == "/app"


def test_remote_setup_environment_carries_omniroute_and_master_key():
    text = render_compose(
        CFG,
        models_dir="/models",
        presets_path="/presets.ini",
        repo_root="/repo",
        omni_router_master_key="test-master-key",
    )
    document = yaml.safe_load(text)
    env = document["services"]["remote-setup"]["environment"]
    assert env["OMNI_ROUTER_MASTER_KEY"] == "test-master-key"
    assert env["REMOTE_SETUP_PORT"] == "20130"
    assert env["OMNIROUTE_INTERNAL_URL"] == "http://omniroute:20128"
    assert env["OMNIROUTE_PORT"] == "20128"
    assert env["OMNIROUTE_DASHBOARD_PASSWORD"] == "test-password"


def test_remote_setup_environment_defaults_master_key_to_empty_string():
    _, document = compose_dict()
    assert document["services"]["remote-setup"]["environment"]["OMNI_ROUTER_MASTER_KEY"] == ""


def test_remote_setup_models_json_lists_only_enabled_models_alias_ctx_and_output_tokens():
    cfg = {
        **CFG,
        "models": [
            {"alias": "a", "enabled": True, "ctx_size": 8192, "client_max_output_tokens": 4096},
            {"alias": "b", "enabled": False, "ctx_size": 8192, "client_max_output_tokens": 4096},
        ],
    }
    _, document = compose_dict(cfg)
    import json as _json
    models_json = _json.loads(document["services"]["remote-setup"]["environment"]["MODELS_JSON"])
    assert models_json == [{"alias": "a", "ctx_size": 8192, "client_max_output_tokens": 4096}]


def test_remote_setup_service_depends_on_omniroute_healthy():
    _, document = compose_dict()
    assert document["services"]["remote-setup"]["depends_on"] == {
        "omniroute": {"condition": "service_healthy"},
    }


def test_remote_setup_service_has_a_healthcheck():
    _, document = compose_dict()
    healthcheck = document["services"]["remote-setup"]["healthcheck"]
    test = healthcheck["test"]
    assert test[:3] == ["CMD", "python3", "-c"]
    assert "/setup.sh" in test[3]
    assert "127.0.0.1:20130" in test[3]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_compose.py -v`
Expected: FAIL — `render_compose()` doesn't accept `repo_root`/
`omni_router_master_key` yet, and there's no `remote-setup` service.

- [ ] **Step 3: Add `import json` and update `render_compose()`'s signature**

In `pylib/compose.py`, add `import json` to the imports (after `from typing
import Any`, before `import yaml`). Change the `render_compose` signature:

```python
def render_compose(
    cfg: dict[str, Any],
    *,
    models_dir: str,
    presets_path: str,
    repo_root: str,
    omni_router_master_key: str = "",
) -> str:
```

- [ ] **Step 4: Change OmniRoute's port binding**

In `render_compose()`, change:

```python
        "ports": [f"127.0.0.1:{omniroute_port}:{omniroute_port}"],
```

to:

```python
        "ports": [f"0.0.0.0:{omniroute_port}:{omniroute_port}"],
```

(inside the `omniroute_service` dict construction).

- [ ] **Step 5: Build the `remote_setup_service` dict**

In `render_compose()`, right after the `omniroute_memory_mib` block (right
before the `document = {...}` line), add:

```python
    remote_setup_cfg = cfg.get("remote_setup", {})
    remote_setup_port = remote_setup_cfg.get("port", 20130)
    enabled_models = [
        {
            "alias": model["alias"],
            "ctx_size": model["ctx_size"],
            "client_max_output_tokens": model["client_max_output_tokens"],
        }
        for model in cfg.get("models", [])
        if model.get("enabled")
    ]
    remote_setup_service: dict[str, Any] = {
        "image": _dollar_escape(
            remote_setup_cfg.get("image", "docker.io/library/python:3.13-alpine")
        ),
        "container_name": "remote-setup",
        "working_dir": "/app",
        "volumes": [
            f"{_dollar_escape(repo_root)}/pylib:/app/pylib:ro,z",
            # update-opencode-config.mjs is NOT served over HTTP (the design
            # defines exactly two public routes, /setup.sh and /config) --
            # it's mounted here so pylib/remote_setup.py can read its
            # content at script-render time and embed it verbatim into the
            # generated /setup.sh response via a bash heredoc. Mounted
            # explicitly since only pylib/ is otherwise in view.
            f"{_dollar_escape(repo_root)}/setup/update-opencode-config.mjs:/app/setup/update-opencode-config.mjs:ro,z",
            "remote-setup-data:/app/data",
        ],
        "ports": [f"0.0.0.0:{remote_setup_port}:{remote_setup_port}"],
        "command": ["python3", "-m", "pylib.remote_setup"],
        "environment": {
            "OMNI_ROUTER_MASTER_KEY": _dollar_escape(omni_router_master_key),
            "REMOTE_SETUP_PORT": str(remote_setup_port),
            "OMNIROUTE_INTERNAL_URL": f"http://omniroute:{omniroute_port}",
            "OMNIROUTE_PORT": str(omniroute_port),
            "OMNIROUTE_DASHBOARD_PASSWORD": _dollar_escape(
                omniroute_cfg.get("initial_password", "")
            ),
            "MODELS_JSON": _dollar_escape(json.dumps(enabled_models)),
        },
        "depends_on": {"omniroute": {"condition": "service_healthy"}},
        "restart": "unless-stopped",
        "healthcheck": {
            "test": [
                "CMD",
                "python3",
                "-c",
                (
                    "import urllib.request,sys; "
                    "urllib.request.urlopen("
                    f"'http://127.0.0.1:{remote_setup_port}/setup.sh', timeout=3"
                    ")"
                ),
            ],
            "interval": "10s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "10s",
        },
    }
```

`/setup.sh` is the public route (no master key required), so the
healthcheck can hit it directly with stdlib `urllib.request` — no `curl`
binary exists in `python:3.13-alpine`, and adding one would mean a custom
image build, which the design explicitly rules out (see "Explicitly out
of scope"). A non-2xx response raises `urllib.error.HTTPError`, which
`python3 -c` propagates as a non-zero exit — exactly what `CMD` needs.

- [ ] **Step 6: Wire the new service and volume into the compose document**

Change:

```python
    document = {
        "services": {"llm-server": service, "omniroute": omniroute_service},
        "volumes": {"omniroute-data": {}},
    }
```

to:

```python
    document = {
        "services": {
            "llm-server": service,
            "omniroute": omniroute_service,
            "remote-setup": remote_setup_service,
        },
        "volumes": {"omniroute-data": {}, "remote-setup-data": {}},
    }
```

- [ ] **Step 7: Update `write_compose()`**

```python
def write_compose(
    cfg: dict[str, Any],
    *,
    models_dir: str,
    presets_path: str,
    repo_root: str,
    omni_router_master_key: str = "",
    path: Path,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_compose(
            cfg,
            models_dir=models_dir,
            presets_path=presets_path,
            repo_root=repo_root,
            omni_router_master_key=omni_router_master_key,
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_compose.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 9: Wire `llmenv.py`'s `render-compose` CLI**

In `llmenv.py`, find `cmd_render_compose` (around line 389):

```python
def cmd_render_compose(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    write_compose(
        cfg,
        models_dir=args.models_dir,
        presets_path=args.presets_path,
        path=Path(args.output),
    )
    return emit({"written": str(args.output)})
```

Replace it with:

```python
def cmd_render_compose(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    env_vars = read_env_file(Path(args.env_file)) if args.env_file else {}
    write_compose(
        cfg,
        models_dir=args.models_dir,
        presets_path=args.presets_path,
        repo_root=args.repo_root,
        omni_router_master_key=env_vars.get("OMNI_ROUTER_MASTER_KEY", ""),
        path=Path(args.output),
    )
    return emit({"written": str(args.output)})
```

Add the import, alongside the existing `from pylib.compose import
write_compose` line:

```python
from pylib.dotenv import read_env_file
```

Find the `render_compose` argparse subparser (around line 527):

```python
    render_compose = sub.add_parser("render-compose")
    render_compose.add_argument("--config", default=argparse.SUPPRESS)
    render_compose.add_argument("--models-dir", required=True)
    render_compose.add_argument("--presets-path", required=True)
    render_compose.add_argument("--output", required=True)
    render_compose.set_defaults(func=cmd_render_compose)
```

Replace with:

```python
    render_compose = sub.add_parser("render-compose")
    render_compose.add_argument("--config", default=argparse.SUPPRESS)
    render_compose.add_argument("--models-dir", required=True)
    render_compose.add_argument("--presets-path", required=True)
    render_compose.add_argument("--repo-root", required=True)
    render_compose.add_argument("--env-file", default=None)
    render_compose.add_argument("--output", required=True)
    render_compose.set_defaults(func=cmd_render_compose)
```

- [ ] **Step 10: Update `setup/render-unit.sh`'s call site**

In `setup/render-unit.sh`, find:

```bash
log_step "Rendering the compose file"
llmenv --config "$CONFIG_PATH" render-compose \
    --models-dir "$MODELS_DIR" --presets-path "$presets_path" --output "$COMPOSE_FILE" >/dev/null
```

Replace with:

```bash
log_step "Rendering the compose file"
llmenv --config "$CONFIG_PATH" render-compose \
    --models-dir "$MODELS_DIR" --presets-path "$presets_path" \
    --repo-root "$REPO_DIR" --env-file "${REPO_DIR}/.env" \
    --output "$COMPOSE_FILE" >/dev/null
```

- [ ] **Step 11: Find and update any other `render-compose`/`write_compose`/`render_compose` call sites**

Run: `grep -rn "render-compose\|write_compose(\|render_compose(" --include='*.py' --include='*.sh' .`
For each result outside `pylib/compose.py`, `llmenv.py`, `setup/render-unit.sh`,
`tests/test_compose.py` (already handled above), apply the same two new
arguments (`--repo-root`/`repo_root=`, and `--env-file`/
`omni_router_master_key=` if the call site is CLI-level and has a
`.env`-like path available, else omit `--env-file` — it's optional and
defaults to no master key).

- [ ] **Step 12: Update the existing `render-compose` CLI test and run the CLI/compose suites**

`--repo-root` is now `required=True` on the `render-compose` subparser, so
the existing `test_render_compose_writes_a_compose_file` test in
`tests/test_cli.py` (around line 1480) will fail without it. In
`tests/test_cli.py`, change:

```python
def test_render_compose_writes_a_compose_file(tmp_path):
    config = write_test_config(tmp_path)
    output = tmp_path / "docker-compose.yml"
    result = run(
        "--config", str(config),
        "render-compose",
        "--models-dir", "/models",
        "--presets-path", "/presets.ini",
        "--output", str(output),
    )
```

to:

```python
def test_render_compose_writes_a_compose_file(tmp_path):
    config = write_test_config(tmp_path)
    output = tmp_path / "docker-compose.yml"
    result = run(
        "--config", str(config),
        "render-compose",
        "--models-dir", "/models",
        "--presets-path", "/presets.ini",
        "--repo-root", str(tmp_path),
        "--output", str(output),
    )
```

Add two new tests to `tests/test_cli.py`, right after
`test_render_compose_writes_a_compose_file`, that actually invoke the CLI's
`--env-file` flag end to end (every test above this point either calls
`render_compose()`/`write_compose()` directly or omits `--env-file`
entirely, so none of them prove the CLI's `read_env_file()` wiring in
`cmd_render_compose()` — added in Step 9 above — actually works):

```python
def test_render_compose_reads_the_master_key_from_env_file(tmp_path):
    config = write_test_config(tmp_path)
    output = tmp_path / "docker-compose.yml"
    env_file = tmp_path / ".env"
    env_file.write_text("OMNI_ROUTER_MASTER_KEY=test-master-key-from-env\n")
    result = run(
        "--config", str(config),
        "render-compose",
        "--models-dir", "/models",
        "--presets-path", "/presets.ini",
        "--repo-root", str(tmp_path),
        "--env-file", str(env_file),
        "--output", str(output),
    )
    assert result.returncode == 0, result.stderr
    document = yaml.safe_load(output.read_text())
    env = document["services"]["remote-setup"]["environment"]
    assert env["OMNI_ROUTER_MASTER_KEY"] == "test-master-key-from-env"


def test_render_compose_defaults_to_empty_master_key_when_env_file_missing(tmp_path):
    config = write_test_config(tmp_path)
    output = tmp_path / "docker-compose.yml"
    missing_env_file = tmp_path / "does-not-exist.env"
    result = run(
        "--config", str(config),
        "render-compose",
        "--models-dir", "/models",
        "--presets-path", "/presets.ini",
        "--repo-root", str(tmp_path),
        "--env-file", str(missing_env_file),
        "--output", str(output),
    )
    assert result.returncode == 0, result.stderr
    document = yaml.safe_load(output.read_text())
    env = document["services"]["remote-setup"]["environment"]
    assert env["OMNI_ROUTER_MASTER_KEY"] == ""
```

`tests/test_cli.py` already imports `yaml` at module level (used by other
compose-related tests in the file), so no new import is needed.

Run: `uv run pytest tests/test_cli.py tests/test_compose.py -v`
Expected: PASS (all tests), including the two new `--env-file` tests above.

- [ ] **Step 13: Run the full project gate**

This task edits `.py` files (`pylib/compose.py`, `llmenv.py`) and a `.sh`
file (`setup/render-unit.sh`), so the full gate runs before committing, not
just this task's own test files.

Run: `make validate && make test`
Expected: all checks and tests pass.

- [ ] **Step 14: Commit**

```bash
git add pylib/compose.py tests/test_compose.py llmenv.py setup/render-unit.sh tests/test_cli.py
git commit -m "feat(compose): add the remote-setup service and expose OmniRoute on the LAN"
```

---

## Task 3: `pylib/remote_setup.py` — the HTTP server

**Files:**
- Create: `pylib/remote_setup.py`
- Create: `tests/test_remote_setup.py`

**Interfaces:**
- Consumes (from Task 2, at container runtime via `os.environ`):
  `OMNI_ROUTER_MASTER_KEY`, `REMOTE_SETUP_PORT`, `OMNIROUTE_INTERNAL_URL`,
  `OMNIROUTE_PORT`, `OMNIROUTE_DASHBOARD_PASSWORD`, `MODELS_JSON`.
- Consumes: `pylib.omniroute._login`, `pylib.omniroute._request`,
  `pylib.omniroute.OmniRouteError` (reused directly rather than
  reimplemented — both modules ship in the same `pylib/` mount).
- Produces (module-level, for tests and for `main()`):
  `parse_bearer_token(header_value)`, `master_key_matches(provided,
  expected)`, `read_cached_key(cache_path)`, `write_cached_key(cache_path,
  key_id, key_value)` (atomic: writes a temp file in `cache_path`'s
  directory, then `os.replace()`s it into place), `ensure_api_key(base_url,
  dashboard_password, cache_path)` (guarded by a module-level
  `threading.Lock()` so concurrent callers create at most one key),
  `host_without_port(host_header)` (handles bracketed IPv6 literals),
  `validate_host_header(host_header) -> bool`, `build_config_response(*,
  host, omniroute_port, api_key, models)`, `render_setup_script(host)`
  (reads and embeds `setup/update-opencode-config.mjs`'s content from
  `UPDATE_OPENCODE_CONFIG_PATH`), `RemoteSetupHandler` (an
  `http.server.BaseHTTPRequestHandler` subclass whose `do_GET` catches any
  unexpected exception and turns it into a JSON 500), `main()`.
- Note: there is no `GET /update-opencode-config.mjs` route (the design
  defines exactly two public endpoints) — `render_setup_script()` embeds
  that file's content directly into the `/setup.sh` response instead.

- [ ] **Step 1: Write the failing tests for the pure functions**

Create `tests/test_remote_setup.py`:

```python
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pylib.remote_setup as remote_setup_module
from pylib.remote_setup import (
    build_config_response,
    host_without_port,
    master_key_matches,
    parse_bearer_token,
    read_cached_key,
    render_setup_script,
    validate_host_header,
    write_cached_key,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _use_real_opencode_updater(monkeypatch):
    """render_setup_script() reads UPDATE_OPENCODE_CONFIG_PATH from disk to
    embed it into the generated script. In the running container that path
    is /app/setup/update-opencode-config.mjs (see pylib/compose.py's
    remote-setup volume mount); on the test host it's this repo's real
    setup/update-opencode-config.mjs -- point the module at it so tests
    exercise the real embed rather than a container path that doesn't
    exist outside the container.
    """
    monkeypatch.setattr(
        remote_setup_module,
        "UPDATE_OPENCODE_CONFIG_PATH",
        REPO_ROOT / "setup" / "update-opencode-config.mjs",
    )


def test_parse_bearer_token_extracts_the_token():
    assert parse_bearer_token("Bearer abc123") == "abc123"


def test_parse_bearer_token_returns_none_for_missing_header():
    assert parse_bearer_token(None) is None


def test_parse_bearer_token_returns_none_for_non_bearer_scheme():
    assert parse_bearer_token("Basic abc123") is None


def test_parse_bearer_token_returns_none_for_empty_token():
    assert parse_bearer_token("Bearer ") is None


def test_master_key_matches_true_for_equal_strings():
    assert master_key_matches("secret", "secret") is True


def test_master_key_matches_false_for_different_strings():
    assert master_key_matches("wrong", "secret") is False


def test_read_cached_key_returns_none_when_file_missing(tmp_path):
    assert read_cached_key(tmp_path / "missing.json") is None


def test_read_cached_key_returns_none_for_malformed_json(tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text("not json")
    assert read_cached_key(cache) is None


def test_read_cached_key_returns_none_when_fields_missing(tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"id": "abc"}))
    assert read_cached_key(cache) is None


def test_write_then_read_cached_key_round_trips(tmp_path):
    cache = tmp_path / "nested" / "cache.json"
    write_cached_key(cache, "key-id", "sk-value")
    assert read_cached_key(cache) == {"id": "key-id", "key": "sk-value"}


def test_host_without_port_strips_a_trailing_port():
    assert host_without_port("192.0.2.1:20130") == "192.0.2.1"


def test_host_without_port_leaves_a_bare_host_unchanged():
    assert host_without_port("192.0.2.1") == "192.0.2.1"


def test_host_without_port_handles_a_bracketed_ipv6_literal_with_port():
    assert host_without_port("[::1]:20130") == "[::1]"


def test_host_without_port_handles_a_bracketed_ipv6_literal_without_port():
    assert host_without_port("[::1]") == "[::1]"


def test_validate_host_header_accepts_a_hostname_with_port():
    assert validate_host_header("192.0.2.1:20130") is True


def test_validate_host_header_accepts_a_bracketed_ipv6_literal():
    assert validate_host_header("[::1]:20130") is True


def test_validate_host_header_accepts_an_mdns_name():
    assert validate_host_header("my-host.local:20130") is True


def test_validate_host_header_rejects_an_empty_header():
    assert validate_host_header("") is False


def test_validate_host_header_rejects_shell_metacharacters():
    assert validate_host_header("evil`touch pwned`:20130") is False
    assert validate_host_header("evil$(touch pwned):20130") is False
    assert validate_host_header("evil; rm -rf /:20130") is False


def test_build_config_response_shape():
    response = build_config_response(
        host="192.0.2.1",
        omniroute_port="20128",
        api_key="sk-test",
        models=[{"alias": "a", "ctx_size": 8192, "client_max_output_tokens": 4096}],
    )
    assert response == {
        "omniroute_base_url": "http://192.0.2.1:20128",
        "api_key": "sk-test",
        "models": [{"alias": "a", "ctx_size": 8192, "client_max_output_tokens": 4096}],
    }


def test_render_setup_script_embeds_the_host(monkeypatch):
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert 'LLM_ENV_HOST="192.0.2.1:20130"' in script
    assert script.startswith("#!/usr/bin/env bash")


def test_render_setup_script_prompts_for_the_master_key_from_the_tty(monkeypatch):
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert "OMNI_ROUTER_MASTER_KEY:" in script
    assert "read -r -s -p" in script
    # Under `curl ... | bash`, stdin is the script pipe, not the terminal --
    # reading from /dev/tty explicitly is required for the prompt to
    # actually reach the user instead of silently reading EOF.
    assert "/dev/tty" in script


def test_render_setup_script_does_not_use_curl_dash_f(monkeypatch):
    # -f (--fail) swallows the response body on a non-2xx status, which is
    # exactly the body the script needs to read `.error` from. Match only
    # curl invocations -- the template's own `rm -f --` / `mv -f --` lines
    # (unrelated uses of -f) legitimately contain " -f " as a substring,
    # so a blanket substring check would false-positive on those.
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    for line in script.splitlines():
        if line.strip().startswith("curl"):
            assert " -f" not in line, line
    assert "curl -fsS" not in script


def test_render_setup_script_captures_the_http_status_and_checks_it(monkeypatch):
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert "%{http_code}" in script
    assert 'if [ "$http_status" != "200" ]' in script


def test_render_setup_script_never_passes_the_master_key_as_a_curl_argument(monkeypatch):
    # A bearer token passed via `curl -H "Authorization: Bearer $x"` is
    # visible to every other local user on the remote machine through
    # `ps`/`/proc/<pid>/cmdline` for as long as curl runs -- the script
    # must instead use a private, mode-0600 curl config file passed via
    # -K, mirroring scripts/check-server.sh's own auth_conf pattern.
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert '-H "Authorization: Bearer' not in script
    assert 'header = "Authorization: Bearer %s"' in script
    assert "-K \"$auth_conf\"" in script
    assert 'chmod 600 "$auth_conf"' in script


def test_render_setup_script_sends_omniroute_routed_model_ids(monkeypatch):
    # Requests going TO OmniRoute must use "llama-cpp/<alias>", matching
    # this repo's own established, live-verified routing convention (see
    # scripts/check-server.sh's OmniRoute completion check).
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert 'id: "llama-cpp/\\(.alias)"' in script
    assert '.["llama-cpp/\\($model.alias)"]' in script


def test_render_setup_script_embeds_the_opencode_updater_via_heredoc(monkeypatch):
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert "<<'OPENCODE_UPDATER_EOF'" in script
    assert "OPENCODE_UPDATER_EOF" in script
    # A real function from setup/update-opencode-config.mjs, proving the
    # file's actual content was embedded rather than a second HTTP fetch.
    assert "function replaceProvider(text, provider)" in script
    assert "http://${LLM_ENV_HOST}/update-opencode-config.mjs" not in script


def test_render_setup_script_uses_staged_files_with_restrictive_permissions(monkeypatch):
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert "chmod 700 \"$workdir\"" in script
    assert "chmod 600" in script
    assert "trap cleanup EXIT" in script


def test_render_setup_script_detects_an_existing_opencode_candidate_file(monkeypatch):
    # Mirrors setup-local-llm-agents.sh's own opencode_candidates detection
    # (setup/setup-local-llm-agents.sh:149-231): every candidate already
    # containing the "local-llm-env" provider is targeted; only when none
    # do does it fall back to the highest-priority *existing* file
    # (reverse order: opencode.jsonc, then opencode.json, then
    # config.json), and only when none exist at all does it default to
    # creating opencode.jsonc fresh.
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert (
        'opencode_candidates=(\n'
        '    "${opencode_dir}/config.json"\n'
        '    "${opencode_dir}/opencode.json"\n'
        '    "${opencode_dir}/opencode.jsonc"\n'
        ')'
    ) in script
    assert 'node "$updater" --contains-provider "$candidate"' in script
    assert 'for candidate in "${opencode_candidates[2]}" "${opencode_candidates[1]}" "${opencode_candidates[0]}"; do' in script
    assert 'opencode_targets+=("${opencode_candidates[2]}")' in script


def test_render_setup_script_validates_opencode_candidates_are_regular_files_and_checks_status(
    monkeypatch,
):
    # Full fidelity to setup/setup-local-llm-agents.sh:193-211: a
    # candidate that exists but isn't a regular file must die loud (not
    # be silently skipped), and the updater's exit status must be
    # discriminated 0 (contains)/1 (does not contain)/anything else
    # (real validation error) rather than treating every non-zero exit as
    # "does not contain".
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert '[ -f "$candidate" ] ||' in script
    assert 'contains_status=0' in script
    assert 'contains_status=$?' in script
    assert 'case "$contains_status" in' in script


def test_render_setup_script_stages_pi_and_opencode_before_moving_either_into_place(monkeypatch):
    # A failure partway through building the targets (Pi's models.json,
    # Pi's settings.json, every detected/created OpenCode config) must
    # never leave some of them updated and others untouched -- so every
    # staging step (the jq/node calls that write into the *_staged temp
    # files) must appear in the script BEFORE the first `mv -f --` that
    # moves any of them into place.
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    last_staging_step = script.index(
        'node "$updater" --replace-provider "$opencode_source" "$provider_file" "$staged"'
    )
    first_move = script.index('mv -f -- "$pi_staged" "$pi_path"')
    assert last_staging_step < first_move
    assert script.index('mv -f -- "$pi_settings_staged" "$pi_settings_path"') > first_move
    assert script.index(
        'mv -f -- "${opencode_staged[$index]}" "${opencode_targets[$index]}"'
    ) > first_move
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_remote_setup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pylib.remote_setup'`

- [ ] **Step 3: Implement the pure functions**

Create `pylib/remote_setup.py`:

```python
"""HTTP server for the remote-setup service.

Gates OmniRoute credentials behind OMNI_ROUTER_MASTER_KEY and serves a
self-contained installer script that configures Pi/OpenCode on a remote
machine to talk to OmniRoute. Stdlib-only, run as
`python3 -m pylib.remote_setup` inside the `remote-setup` compose service
-- see docs/superpowers/specs/2026-08-12-remote-agent-setup-design.md.

Reuses pylib.omniroute's session-login/request helpers rather than
reimplementing OmniRoute's dashboard-session auth a second time -- both
modules ship together in the same read-only pylib/ bind mount.
"""

from __future__ import annotations

import hmac
import http.server
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from pylib.omniroute import OmniRouteError, _login, _request

KEY_NAME = "llm-env-remote-agents"
CACHE_PATH = Path("/app/data/api-key.json")

# Mounted read-only by pylib/compose.py's remote-setup service (see Task
# 2) alongside pylib/ itself. NOT served over HTTP -- the design defines
# exactly two public routes (/setup.sh, /config); render_setup_script()
# below reads this file's content and embeds it into the /setup.sh
# response via a bash heredoc instead of serving it as its own route.
UPDATE_OPENCODE_CONFIG_PATH = Path("/app/setup/update-opencode-config.mjs")

# Authority characters valid in an HTTP Host header we're willing to
# interpolate into a generated shell script or a JSON response: DNS
# labels, dots, a port-separator colon, and the brackets a literal IPv6
# address uses. Rejects anything else (backticks, `$`, `;`, spaces, ...)
# so a hostile Host header can't inject shell/JSON content.
_HOST_HEADER_RE = re.compile(r"^[A-Za-z0-9.:\[\]-]+$")

# render_setup_script() builds the final script via plain string
# replacement (not str.format()) specifically so the embedded
# update-opencode-config.mjs source -- which is full of literal `{`/`}`
# JS syntax -- never has to be escaped for a format mini-language it was
# never written for.
_HOST_PLACEHOLDER = "@@LLM_ENV_HOST@@"
_UPDATER_JS_PLACEHOLDER = "@@OPENCODE_UPDATER_JS@@"

SETUP_SCRIPT_TEMPLATE = r'''#!/usr/bin/env bash
set -euo pipefail
umask 077

LLM_ENV_HOST="@@LLM_ENV_HOST@@"

for cmd in curl jq node mktemp; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "remote-setup: missing required command: $cmd" >&2
        exit 1
    }
done

workdir="$(mktemp -d)" || {
    echo "remote-setup: could not create private configuration workspace" >&2
    exit 1
}
chmod 700 "$workdir" || {
    rm -rf -- "$workdir"
    echo "remote-setup: could not secure private configuration workspace" >&2
    exit 1
}
staged_files=()
cleanup() {
    local status=$?
    local path
    for path in "${staged_files[@]}"; do
        [ -n "$path" ] && rm -f -- "$path"
    done
    rm -rf -- "$workdir"
    exit "$status"
}
trap cleanup EXIT

# `curl ... | bash` leaves stdin attached to the script pipe, not the
# terminal -- `read` must go to /dev/tty explicitly or the prompt below
# silently reads EOF instead of actually asking the user.
read -r -s -p "OMNI_ROUTER_MASTER_KEY: " master_key < /dev/tty
echo

# A bearer token on the curl command line would be visible to every other
# local user via `ps`/`/proc/<pid>/cmdline` for as long as curl runs --
# mirrors scripts/check-server.sh's own auth_conf pattern (a private,
# mode-0600 curl config file passed via -K, never -H on the command line).
auth_conf="${workdir}/auth.conf"
printf 'header = "Authorization: Bearer %s"\n' "$master_key" >"$auth_conf"
chmod 600 "$auth_conf"

response_file="${workdir}/config-response.json"
http_status="$(curl -sS -K "$auth_conf" -o "$response_file" -w '%{http_code}' \
    "http://${LLM_ENV_HOST}/config")" || {
    echo "remote-setup: could not reach http://${LLM_ENV_HOST}/config" >&2
    exit 1
}
if [ "$http_status" != "200" ]; then
    error="$(jq -r '.error // "request failed"' "$response_file" 2>/dev/null || echo "request failed")"
    echo "remote-setup: ${error} (HTTP ${http_status})" >&2
    exit 1
fi

config_json="$(cat "$response_file")"
base_url="$(printf '%s' "$config_json" | jq -r '.omniroute_base_url')/v1"
api_key="$(printf '%s' "$config_json" | jq -r '.api_key')"
models_json="$(printf '%s' "$config_json" | jq -c '.models')"

api_key_file="${workdir}/api-key"
printf '%s' "$api_key" >"$api_key_file"
chmod 600 "$api_key_file"

pi_dir="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
pi_path="${pi_dir}/models.json"
pi_settings_path="${pi_dir}/settings.json"
mkdir -p "$pi_dir"
chmod 700 "$pi_dir"

# Same candidate-file detection setup-local-llm-agents.sh uses
# (setup/setup-local-llm-agents.sh:149-231): every candidate that already
# contains the "local-llm-env" provider is updated (a machine can have more
# than one OpenCode config file); if none contains it yet, fall back to the
# highest-priority *existing* candidate (checked in reverse,
# opencode.jsonc -> opencode.json -> config.json, first hit wins); if none
# exists at all, default to creating opencode.jsonc fresh. Preserves an
# existing remote machine's own OpenCode config filename(s) instead of
# always replacing them with a second, out-of-sync opencode.jsonc.
opencode_dir="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
mkdir -p "$opencode_dir"
chmod 700 "$opencode_dir"
opencode_candidates=(
    "${opencode_dir}/config.json"
    "${opencode_dir}/opencode.json"
    "${opencode_dir}/opencode.jsonc"
)

# The updater's own source is embedded here (not fetched over HTTP -- the
# design defines only /setup.sh and /config as public routes). A quoted
# heredoc delimiter means the shell does no expansion inside the JS body,
# so its own `$`/backtick usage is inert.
updater="${workdir}/update-opencode-config.mjs"
cat >"$updater" <<'OPENCODE_UPDATER_EOF'
@@OPENCODE_UPDATER_JS@@
OPENCODE_UPDATER_EOF

opencode_targets=()
opencode_sources=()
for candidate in "${opencode_candidates[@]}"; do
    [ -e "$candidate" ] || continue
    [ -f "$candidate" ] || {
        echo "remote-setup: OpenCode configuration is not a regular file: ${candidate}" >&2
        exit 1
    }
    if node "$updater" --contains-provider "$candidate"; then
        contains_status=0
    else
        contains_status=$?
    fi
    case "$contains_status" in
        0)
            opencode_targets+=("$candidate")
            opencode_sources+=("$candidate")
            ;;
        1) ;;
        *)
            echo "remote-setup: could not validate OpenCode configuration: ${candidate}" >&2
            exit 1
            ;;
    esac
done
if [ "${#opencode_targets[@]}" -eq 0 ]; then
    for candidate in "${opencode_candidates[2]}" "${opencode_candidates[1]}" "${opencode_candidates[0]}"; do
        if [ -e "$candidate" ]; then
            opencode_targets+=("$candidate")
            opencode_sources+=("$candidate")
            break
        fi
    done
fi
if [ "${#opencode_targets[@]}" -eq 0 ]; then
    printf '{}\n' >"${workdir}/empty-opencode.jsonc"
    opencode_targets+=("${opencode_candidates[2]}")
    opencode_sources+=("${workdir}/empty-opencode.jsonc")
fi

# OpenCode's own recent/favorite/variant model-cycling state
# ($XDG_STATE_HOME/opencode/model.json) -- out of scope for this installer.
# It is per-installation runtime UI state, not provider/model
# configuration: OpenCode regenerates it on its own on first use, and
# replicating setup-local-llm-agents.sh's version-pinned
# (`opencode --version` == 1.18.10) creation path here would make the
# remote installer hard-fail on any machine running a different OpenCode
# version, for a file that isn't required for OmniRoute connectivity to
# work. See docs/superpowers/specs/2026-08-12-remote-agent-setup-design.md,
# "Explicitly out of scope".

# Model ids sent TO OmniRoute must be "llama-cpp/<alias>" -- OmniRoute
# routes on the provider slug, not the connection's own name (confirmed
# live; see scripts/check-server.sh's own OmniRoute completion check,
# which uses the identical "llama-cpp/${alias}" convention).
pi_provider_json="$(jq -n --arg base_url "$base_url" --rawfile api_key "$api_key_file" \
    --argjson models "$models_json" \
    '{baseUrl: $base_url, api: "openai-completions", apiKey: ($api_key | rtrimstr("\n")),
      compat: {supportsDeveloperRole: false, supportsReasoningEffort: false},
      models: [$models[] | {id: "llama-cpp/\(.alias)", contextWindow: .ctx_size, maxTokens: .client_max_output_tokens}]}')"

pi_source="$pi_path"
if [ ! -e "$pi_path" ]; then
    printf '{}\n' >"${workdir}/empty-pi.json"
    pi_source="${workdir}/empty-pi.json"
fi
pi_staged="$(mktemp "${pi_dir}/.models.json.XXXXXX")"
chmod 600 "$pi_staged"
staged_files+=("$pi_staged")
jq --argjson provider "$pi_provider_json" \
   '.providers = ((.providers // {}) + {"local-llm-env": $provider})' \
   "$pi_source" >"$pi_staged" || {
    echo "remote-setup: could not update Pi configuration" >&2
    exit 1
}

pi_settings_source="$pi_settings_path"
if [ ! -e "$pi_settings_path" ]; then
    printf '{}\n' >"${workdir}/empty-pi-settings.json"
    pi_settings_source="${workdir}/empty-pi-settings.json"
fi
pi_settings_staged="$(mktemp "${pi_dir}/.settings.json.XXXXXX")"
chmod 600 "$pi_settings_staged"
staged_files+=("$pi_settings_staged")
jq --argjson models "$models_json" \
   '.enabledModels = [$models[] | "local-llm-env/llama-cpp/\(.alias)"]' \
   "$pi_settings_source" >"$pi_settings_staged" || {
    echo "remote-setup: could not update Pi settings" >&2
    exit 1
}

opencode_provider_json="$(jq -n --arg base_url "$base_url" --rawfile api_key "$api_key_file" \
    --argjson models "$models_json" \
    '{npm: "@ai-sdk/openai-compatible", name: "local-llm-env",
      options: {baseURL: $base_url, apiKey: ($api_key | rtrimstr("\n"))},
      models: (reduce $models[] as $model ({};
          .["llama-cpp/\($model.alias)"] = {name: $model.alias,
              limit: {context: $model.ctx_size, output: $model.client_max_output_tokens}}))}')"
provider_file="${workdir}/opencode-provider.json"
printf '%s' "$opencode_provider_json" >"$provider_file"
chmod 600 "$provider_file"

opencode_staged=()
for index in "${!opencode_targets[@]}"; do
    opencode_target="${opencode_targets[$index]}"
    opencode_source="${opencode_sources[$index]}"
    staged="$(mktemp "${opencode_dir}/.$(basename "$opencode_target").XXXXXX")"
    chmod 600 "$staged"
    staged_files+=("$staged")
    opencode_staged+=("$staged")
    node "$updater" --replace-provider "$opencode_source" "$provider_file" "$staged" || {
        echo "remote-setup: could not update OpenCode configuration: ${opencode_target}" >&2
        exit 1
    }
done

# All targets -- Pi's models.json, Pi's settings.json, and every detected/
# created OpenCode config -- are now staged in full. Only past this point
# does anything get moved into place, so a failure in any of the jq/node
# steps above (each already an explicit `exit 1` on error) leaves every
# existing file on disk completely untouched, never a partial mix of "Pi
# updated, OpenCode not" or vice versa.
mv -f -- "$pi_staged" "$pi_path"
staged_files=("${staged_files[@]:1}")
mv -f -- "$pi_settings_staged" "$pi_settings_path"
staged_files=("${staged_files[@]:1}")
for index in "${!opencode_targets[@]}"; do
    mv -f -- "${opencode_staged[$index]}" "${opencode_targets[$index]}"
    staged_files=("${staged_files[@]:1}")
done
echo "Pi configured: ${pi_path}"
for opencode_target in "${opencode_targets[@]}"; do
    echo "OpenCode configured: ${opencode_target}"
done
echo "Done. Model(s): $(printf '%s' "$models_json" | jq -r '[.[].alias] | join(", ")')"
'''


def parse_bearer_token(header_value: str | None) -> str | None:
    if not header_value or not header_value.startswith("Bearer "):
        return None
    token = header_value[len("Bearer "):]
    return token or None


def master_key_matches(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided, expected)


def read_cached_key(cache_path: Path) -> dict[str, str] | None:
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if (
        isinstance(data, dict)
        and isinstance(data.get("id"), str)
        and isinstance(data.get("key"), str)
    ):
        return {"id": data["id"], "key": data["key"]}
    return None


def write_cached_key(cache_path: Path, key_id: str, key_value: str) -> None:
    """Atomically replace the cache file so a crash mid-write can never
    leave a truncated/corrupt cache: write to a private temp file in the
    same directory, then os.replace() it into place (same-filesystem
    rename is atomic on POSIX)."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(cache_path.parent), prefix=".api-key-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": key_id, "key": key_value}))
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, cache_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


# Guards the read-check-create-write sequence in ensure_api_key() below.
# ThreadingHTTPServer dispatches each request on its own thread, so two
# /config requests arriving close together (e.g. two remote machines
# curling /setup.sh | bash at nearly the same time on a cold cache) could
# otherwise both observe "no cached key" and each mint a new OmniRoute
# API key.
_key_issuance_lock = threading.Lock()


def ensure_api_key(
    base_url: str,
    dashboard_password: str,
    cache_path: Path,
    key_name: str = KEY_NAME,
) -> str:
    """Reused as-is by Task 5's `llmenv.py omniroute issue-key` (a
    different key_name/cache_path -- "llm-env-local-agents" -- so a local
    key is never confused with the shared remote-installer key in
    OmniRoute's own dashboard listing)."""
    with _key_issuance_lock:
        session_token = _login(base_url, dashboard_password)
        cached = read_cached_key(cache_path)
        if cached is not None:
            listing = _request("GET", f"{base_url}/api/keys", session_token)
            keys = listing.get("keys", []) if isinstance(listing, dict) else []
            if any(isinstance(k, dict) and k.get("id") == cached["id"] for k in keys):
                return cached["key"]
        created = _request(
            "POST", f"{base_url}/api/keys", session_token, {"name": key_name}
        )
        if (
            not isinstance(created, dict)
            or not isinstance(created.get("id"), str)
            or not isinstance(created.get("key"), str)
        ):
            raise OmniRouteError("key creation returned no usable id/key")
        write_cached_key(cache_path, created["id"], created["key"])
        return created["key"]


def host_without_port(host_header: str) -> str:
    """Strip a trailing :port. A bracketed IPv6 literal (e.g. "[::1]:20130")
    has colons INSIDE the brackets that must survive -- only a colon after
    the closing bracket (or, for a bare non-bracketed host, the first
    colon) separates the port."""
    if host_header.startswith("["):
        end = host_header.find("]")
        if end != -1:
            return host_header[: end + 1]
        return host_header
    return host_header.split(":")[0]


def validate_host_header(host_header: str) -> bool:
    """Reject a Host header before it's interpolated into either the
    generated shell script or the JSON /config response -- an attacker
    who controls the Host header (trivial over plain HTTP) could
    otherwise inject shell syntax into /setup.sh's output."""
    return bool(host_header) and bool(_HOST_HEADER_RE.match(host_header))


def build_config_response(
    *, host: str, omniroute_port: str, api_key: str, models: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "omniroute_base_url": f"http://{host}:{omniroute_port}",
        "api_key": api_key,
        "models": models,
    }


def render_setup_script(host: str) -> str:
    updater_source = UPDATE_OPENCODE_CONFIG_PATH.read_text(encoding="utf-8")
    script = SETUP_SCRIPT_TEMPLATE.replace(_HOST_PLACEHOLDER, host)
    return script.replace(_UPDATER_JS_PLACEHOLDER, updater_source)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_remote_setup.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Write the failing tests for the HTTP handler**

Add to `tests/test_remote_setup.py`:

```python
import http.client
import http.server
import threading

import pylib.remote_setup as remote_setup_module
from pylib.remote_setup import RemoteSetupHandler


class _FakeOmniRouteHandler(http.server.BaseHTTPRequestHandler):
    """Stands in for OmniRoute's admin API during the integration test."""

    keys_created = 0
    last_key_name = None

    def _reply(self, payload, status=200, set_cookie=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        if self.path == "/api/auth/login":
            self._reply({"success": True}, set_cookie="auth_token=tok; Path=/; HttpOnly")
        elif self.path == "/api/keys":
            _FakeOmniRouteHandler.keys_created += 1
            _FakeOmniRouteHandler.last_key_name = body.get("name")
            self._reply({"id": "key-id", "key": "sk-live-value", "name": body.get("name")}, 201)

    def do_GET(self):
        if self.path == "/api/keys":
            self._reply({"keys": [{"id": "key-id", "keyPreview": "abcd"}]})

    def log_message(self, format_string, *args):
        pass


def _start_server(handler_class):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_setup_sh_is_served_without_auth_and_embeds_the_request_host(tmp_path, monkeypatch):
    _use_real_opencode_updater(monkeypatch)
    monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
    monkeypatch.setenv("MODELS_JSON", "[]")
    server, thread = _start_server(RemoteSetupHandler)
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/setup.sh")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert f'LLM_ENV_HOST="127.0.0.1:{port}"' in body
    finally:
        server.shutdown()
        thread.join()


def test_setup_sh_rejects_an_invalid_host_header(monkeypatch):
    monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
    monkeypatch.setenv("MODELS_JSON", "[]")
    server, thread = _start_server(RemoteSetupHandler)
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.putrequest("GET", "/setup.sh", skip_host=True)
        conn.putheader("Host", "evil`touch pwned`")
        conn.endheaders()
        response = conn.getresponse()
        payload = json.loads(response.read())
        assert response.status == 400
        assert "error" in payload
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.parametrize(
    "path",
    ["/update-opencode-config.mjs", "/nonexistent", "/setup", "/config.json"],
)
def test_unknown_paths_404(path, monkeypatch):
    # Exactly two public routes exist -- /setup.sh and /config. In
    # particular, /update-opencode-config.mjs is deliberately NOT a route
    # (its content is embedded into /setup.sh's response via heredoc
    # instead, see render_setup_script()) -- a client trying to fetch it
    # directly must get a plain 404, not a 200 or a 500.
    monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
    monkeypatch.setenv("MODELS_JSON", "[]")
    server, thread = _start_server(RemoteSetupHandler)
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", path)
        response = conn.getresponse()
        payload = json.loads(response.read())
        assert response.status == 404
        assert "error" in payload
    finally:
        server.shutdown()
        thread.join()


def test_config_rejects_an_invalid_host_header(monkeypatch):
    monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
    server, thread = _start_server(RemoteSetupHandler)
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.putrequest("GET", "/config", skip_host=True)
        conn.putheader("Host", "evil$(id)")
        conn.putheader("Authorization", "Bearer test-key")
        conn.endheaders()
        response = conn.getresponse()
        payload = json.loads(response.read())
        assert response.status == 400
        assert "error" in payload
    finally:
        server.shutdown()
        thread.join()


def test_config_rejects_missing_bearer_token(monkeypatch):
    monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
    server, thread = _start_server(RemoteSetupHandler)
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/config")
        response = conn.getresponse()
        assert response.status == 401
    finally:
        server.shutdown()
        thread.join()


def test_config_rejects_wrong_bearer_token(monkeypatch):
    monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
    server, thread = _start_server(RemoteSetupHandler)
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/config", headers={"Authorization": "Bearer wrong"})
        response = conn.getresponse()
        assert response.status == 401
    finally:
        server.shutdown()
        thread.join()


def test_config_returns_503_when_master_key_unset(monkeypatch):
    monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "")
    server, thread = _start_server(RemoteSetupHandler)
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/config", headers={"Authorization": "Bearer anything"})
        response = conn.getresponse()
        assert response.status == 503
    finally:
        server.shutdown()
        thread.join()


def test_ensure_api_key_uses_the_given_key_name(tmp_path, monkeypatch):
    # Task 5 (setup-local-llm-agents.sh -> OmniRoute) reuses ensure_api_key
    # with a distinct key_name ("llm-env-local-agents") so a locally-issued
    # key never shows up in OmniRoute's dashboard under the shared
    # remote-installer key's name ("llm-env-remote-agents").
    _FakeOmniRouteHandler.keys_created = 0
    _FakeOmniRouteHandler.last_key_name = None
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteHandler)
    try:
        fake_port = fake_omniroute.server_address[1]
        cache_path = tmp_path / "api-key.json"
        key = remote_setup_module.ensure_api_key(
            f"http://127.0.0.1:{fake_port}",
            "dashboard-pw",
            cache_path,
            key_name="llm-env-local-agents",
        )
        assert key == "sk-live-value"
        assert _FakeOmniRouteHandler.last_key_name == "llm-env-local-agents"
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


def test_config_returns_the_scoped_key_and_omniroute_address(tmp_path, monkeypatch):
    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteHandler)
    try:
        fake_port = fake_omniroute.server_address[1]
        cache_path = tmp_path / "api-key.json"
        monkeypatch.setattr(remote_setup_module, "CACHE_PATH", cache_path)
        monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
        monkeypatch.setenv("OMNIROUTE_INTERNAL_URL", f"http://127.0.0.1:{fake_port}")
        monkeypatch.setenv("OMNIROUTE_DASHBOARD_PASSWORD", "dashboard-pw")
        monkeypatch.setenv("OMNIROUTE_PORT", "20128")
        monkeypatch.setenv(
            "MODELS_JSON",
            json.dumps([{"alias": "a", "ctx_size": 8192, "client_max_output_tokens": 4096}]),
        )

        server, thread = _start_server(RemoteSetupHandler)
        try:
            port = server.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request(
                "GET", "/config", headers={"Authorization": "Bearer test-key"}
            )
            response = conn.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["api_key"] == "sk-live-value"
            assert payload["omniroute_base_url"] == "http://127.0.0.1:20128"
            assert payload["models"] == [
                {"alias": "a", "ctx_size": 8192, "client_max_output_tokens": 4096}
            ]
            assert _FakeOmniRouteHandler.keys_created == 1

            # Second call reuses the cached key -- no new key created.
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request(
                "GET", "/config", headers={"Authorization": "Bearer test-key"}
            )
            response = conn.getresponse()
            payload = json.loads(response.read())
            assert payload["api_key"] == "sk-live-value"
            assert _FakeOmniRouteHandler.keys_created == 1
        finally:
            server.shutdown()
            thread.join()
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


def test_config_returns_json_500_for_an_unexpected_error(tmp_path, monkeypatch):
    # A malformed MODELS_JSON env var isn't an OmniRouteError -- it's a
    # plain JSONDecodeError raised deep inside _handle_config, after
    # ensure_api_key() has already succeeded. The handler must still
    # produce a JSON 500, not crash the request thread / hang the socket.
    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteHandler)
    try:
        fake_port = fake_omniroute.server_address[1]
        cache_path = tmp_path / "api-key.json"
        monkeypatch.setattr(remote_setup_module, "CACHE_PATH", cache_path)
        monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
        monkeypatch.setenv("OMNIROUTE_INTERNAL_URL", f"http://127.0.0.1:{fake_port}")
        monkeypatch.setenv("OMNIROUTE_DASHBOARD_PASSWORD", "dashboard-pw")
        monkeypatch.setenv("OMNIROUTE_PORT", "20128")
        monkeypatch.setenv("MODELS_JSON", "not valid json")

        server, thread = _start_server(RemoteSetupHandler)
        try:
            port = server.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request(
                "GET", "/config", headers={"Authorization": "Bearer test-key"}
            )
            response = conn.getresponse()
            payload = json.loads(response.read())
            assert response.status == 500
            assert "error" in payload
        finally:
            server.shutdown()
            thread.join()
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


def test_ensure_api_key_concurrent_calls_create_only_one_key(tmp_path, monkeypatch):
    # Two /config requests arriving close together on a cold cache must
    # not each mint their own OmniRoute API key -- ensure_api_key()'s
    # module-level lock serializes the read-check-create-write sequence.
    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteHandler)
    try:
        fake_port = fake_omniroute.server_address[1]
        base_url = f"http://127.0.0.1:{fake_port}"
        cache_path = tmp_path / "api-key.json"

        results = []
        errors = []

        def _call():
            try:
                results.append(
                    remote_setup_module.ensure_api_key(base_url, "dashboard-pw", cache_path)
                )
            except Exception as exc:  # pragma: no cover -- failure path
                errors.append(exc)

        threads = [threading.Thread(target=_call) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors
        assert len(results) == 8
        assert all(value == "sk-live-value" for value in results)
        assert _FakeOmniRouteHandler.keys_created == 1
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


def test_ensure_api_key_reissues_after_dashboard_revocation(tmp_path, monkeypatch):
    # _FakeOmniRouteHandler.do_GET always lists exactly one key, "key-id"
    # (see its fixed do_GET body above) -- a cache seeded with a DIFFERENT
    # id therefore can never appear in that listing, which is exactly what
    # a dashboard-side revocation of the previously cached key looks like
    # from ensure_api_key()'s point of view: cache present, but the id it
    # names is gone from GET /api/keys. ensure_api_key() must not blindly
    # trust the cache file -- it must notice the id is missing, mint a
    # replacement, and overwrite the cache with the new value.
    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteHandler)
    try:
        fake_port = fake_omniroute.server_address[1]
        base_url = f"http://127.0.0.1:{fake_port}"
        cache_path = tmp_path / "api-key.json"
        write_cached_key(cache_path, "revoked-key-id", "sk-old-revoked-value")

        result = remote_setup_module.ensure_api_key(base_url, "dashboard-pw", cache_path)

        assert result == "sk-live-value"
        assert _FakeOmniRouteHandler.keys_created == 1
        assert read_cached_key(cache_path) == {"id": "key-id", "key": "sk-live-value"}
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run pytest tests/test_remote_setup.py -v`
Expected: FAIL — `RemoteSetupHandler` doesn't exist yet.

- [ ] **Step 7: Implement `RemoteSetupHandler` and `main()`**

Append to `pylib/remote_setup.py`:

```python
class RemoteSetupHandler(http.server.BaseHTTPRequestHandler):
    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_text(self, status: int, content_type: str, body_text: str) -> None:
        body = body_text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Any unexpected exception below (a malformed env var, a missing
        # mounted file, ...) must still produce a JSON response -- letting
        # it escape do_GET would abort the request without ever writing a
        # response, leaving the client's connection to hang/reset instead
        # of getting a clear error.
        try:
            self._route()
        except Exception as exc:
            self._write_json(500, {"error": f"internal error: {exc}"})

    def _route(self):
        # Exactly two public routes, per the design: /setup.sh and
        # /config. update-opencode-config.mjs is embedded into /setup.sh's
        # response instead of being served at its own path (see
        # render_setup_script() / UPDATE_OPENCODE_CONFIG_PATH above).
        if self.path == "/setup.sh":
            host = self.headers.get("Host", "")
            if not validate_host_header(host):
                self._write_json(400, {"error": "invalid or missing Host header"})
                return
            self._write_text(200, "text/x-shellscript", render_setup_script(host))
            return
        if self.path == "/config":
            self._handle_config()
            return
        self._write_json(404, {"error": "not found"})

    def _handle_config(self):
        expected = os.environ.get("OMNI_ROUTER_MASTER_KEY", "")
        if not expected:
            self._write_json(
                503,
                {"error": "remote setup not configured -- set OMNI_ROUTER_MASTER_KEY in .env and restart"},
            )
            return
        token = parse_bearer_token(self.headers.get("Authorization"))
        if token is None or not master_key_matches(token, expected):
            self._write_json(401, {"error": "invalid or missing master key"})
            return

        host_header = self.headers.get("Host", "")
        if not validate_host_header(host_header):
            self._write_json(400, {"error": "invalid or missing Host header"})
            return

        base_url = os.environ.get("OMNIROUTE_INTERNAL_URL", "")
        dashboard_password = os.environ.get("OMNIROUTE_DASHBOARD_PASSWORD", "")
        try:
            api_key = ensure_api_key(base_url, dashboard_password, CACHE_PATH)
        except OmniRouteError as exc:
            self._write_json(502, {"error": f"could not reach OmniRoute: {exc}"})
            return

        host = host_without_port(host_header)
        omniroute_port = os.environ.get("OMNIROUTE_PORT", "")
        models = json.loads(os.environ.get("MODELS_JSON", "[]"))
        response = build_config_response(
            host=host, omniroute_port=omniroute_port, api_key=api_key, models=models
        )
        self._write_json(200, response)

    def log_message(self, format_string, *args):
        pass


def main() -> None:
    port = int(os.environ.get("REMOTE_SETUP_PORT", "20130"))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), RemoteSetupHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_remote_setup.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 9: Write and run the real end-to-end installer test**

Every test so far either inspects `render_setup_script()`'s text or drives
`RemoteSetupHandler` in-process — none of them actually run the generated
bash script, so a broken `/dev/tty` read or a shell syntax error in the
generated script would slip through undetected. Add one test that fetches
the real `/setup.sh` body over HTTP and executes it with `bash` inside a
pty (a pty is required because the script deliberately reads from
`/dev/tty`, not stdin — see Step 3), against an isolated `$HOME` and the
same fake-OmniRoute test double already used above.

Add to `tests/test_remote_setup.py`:

```python
import os
import pty
import shutil

import pytest


def test_setup_sh_executed_end_to_end_configures_pi_and_opencode(tmp_path, monkeypatch):
    """Runs the ACTUAL generated /setup.sh via bash, in an isolated $HOME,
    typing the master key into a pty -- the tests above only inspect the
    script's text or drive the handler in-process; this is the one test
    that proves the script is genuinely executable end to end, including
    its /dev/tty prompt, its jq transforms, and its OpenCode update."""
    for cmd in ("bash", "curl", "jq", "node"):
        if shutil.which(cmd) is None:
            pytest.skip(f"{cmd} not available on this host")

    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteHandler)
    try:
        fake_port = fake_omniroute.server_address[1]
        cache_path = tmp_path / "api-key.json"
        monkeypatch.setattr(remote_setup_module, "CACHE_PATH", cache_path)
        _use_real_opencode_updater(monkeypatch)
        monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-master-key")
        monkeypatch.setenv("OMNIROUTE_INTERNAL_URL", f"http://127.0.0.1:{fake_port}")
        monkeypatch.setenv("OMNIROUTE_DASHBOARD_PASSWORD", "dashboard-pw")
        monkeypatch.setenv("OMNIROUTE_PORT", "20128")
        monkeypatch.setenv(
            "MODELS_JSON",
            json.dumps([{"alias": "a", "ctx_size": 8192, "client_max_output_tokens": 4096}]),
        )

        server, thread = _start_server(RemoteSetupHandler)
        try:
            port = server.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/setup.sh")
            script_body = conn.getresponse().read()
            script_path = tmp_path / "setup.sh"
            script_path.write_bytes(script_body)

            home_dir = tmp_path / "home"
            home_dir.mkdir()
            child_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home_dir)}

            pid, master_fd = pty.fork()
            if pid == 0:
                os.execvpe("bash", ["bash", str(script_path)], child_env)

            os.write(master_fd, b"test-master-key\n")
            output = b""
            while True:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
            _, status = os.waitpid(pid, 0)
            exit_code = os.WEXITSTATUS(status)
            assert exit_code == 0, output.decode(errors="replace")

            pi_models = json.loads((home_dir / ".pi" / "agent" / "models.json").read_text())
            assert pi_models["providers"]["local-llm-env"]["models"][0]["id"] == "llama-cpp/a"
            pi_settings = json.loads((home_dir / ".pi" / "agent" / "settings.json").read_text())
            assert pi_settings["enabledModels"] == ["local-llm-env/llama-cpp/a"]

            opencode_config = json.loads(
                (home_dir / ".config" / "opencode" / "opencode.jsonc").read_text()
            )
            assert "llama-cpp/a" in opencode_config["provider"]["local-llm-env"]["models"]

            pi_models_path = home_dir / ".pi" / "agent" / "models.json"
            assert (pi_models_path.stat().st_mode & 0o777) == 0o600
        finally:
            server.shutdown()
            thread.join()
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()
```

Run: `uv run pytest tests/test_remote_setup.py -v -k executed_end_to_end`
Expected: PASS. If `bash`/`curl`/`jq`/`node` are unavailable in this
environment, the test SKIPs rather than failing — the same posture
`setup/setup-local-llm-agents.sh`'s own `require_cmd` dependencies get in
this repo's test suite.

- [ ] **Step 10: Run the full test suite**

Run: `uv run pytest tests/test_remote_setup.py tests/test_compose.py tests/test_config.py tests/test_dotenv.py -v`
Expected: PASS (all tests)

- [ ] **Step 11: Run the full project gate**

This task creates a new `.py` file (`pylib/remote_setup.py`), so the full
gate runs before committing, not just this task's own test files.

Run: `make validate && make test`
Expected: all checks and tests pass.

- [ ] **Step 12: Commit**

```bash
git add pylib/remote_setup.py tests/test_remote_setup.py
git commit -m "feat(remote-setup): serve the master-key-gated installer and config endpoints"
```

---

## Task 4: Documentation and the full project gate

**Files:**
- Modify: `setup/network.sh`
- Modify: `.agents/architecture.md`
- Modify: `docker-compose.yml.example`
- Modify: `scripts/clean.sh`
- Modify: `scripts/show-secrets.sh`
- Modify: `README.md` (only if it references OmniRoute's `127.0.0.1`-only binding — see Step 5)

**Interfaces:**
- Consumes (from Task 2): `remote_setup.port` in `models.yml`.
- No new interfaces produced — this task is documentation plus the final gate.

- [ ] **Step 1: Update `setup/network.sh`**

The old `log_warn` about OmniRoute being loopback-only becomes false once
this ships. It's replaced with a *different* warning: `firewall-cmd`
above only offers to open `llm-server`'s own port, so the two new/changed
`0.0.0.0` bindings (OmniRoute, `remote-setup`) still need a manual
firewall rule on a firewalld-enabled host — this stays a printed warning,
not an automatic change, per the design's explicit "no automatic
firewalling" scope decision.

Replace the entire body of `setup/network.sh` (everything after the
`source ".../tools/lib.sh"` line) with:

```bash

require_cmd ip jq yq

load_server_config
# shellcheck disable=SC2153 # PORT is set by load_server_config() in ../tools/lib.sh.
port="$PORT"
mdns="$(yq -r '.server.mdns_name' "$CONFIG_PATH")"
omniroute_port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"
remote_setup_port="$(yq -r '.remote_setup.port' "$CONFIG_PATH")"

if command -v firewall-cmd >/dev/null 2>&1; then
    if firewall-cmd --query-port="${port}/tcp" >/dev/null 2>&1; then
        log_info "firewall port ${port}/tcp already open"
    elif [ -t 0 ]; then
        read -rp "  Open firewall port ${port}/tcp for LAN access? (yes/no) " open_port
        if [ "$open_port" = "yes" ]; then
            sudo firewall-cmd --permanent --add-port="${port}/tcp" >/dev/null
            sudo firewall-cmd --reload >/dev/null
            log_info "opened firewall port ${port}/tcp"
        fi
    else
        log_info "firewall port ${port}/tcp remains closed; LAN access is disabled"
    fi
    log_warn "firewalld rules for OmniRoute (port ${omniroute_port}/tcp) and the remote-setup installer (port ${remote_setup_port}/tcp) are not opened automatically -- if this host has firewalld enabled and you want either reachable from other machines on the LAN, run: sudo firewall-cmd --permanent --add-port=${omniroute_port}/tcp --add-port=${remote_setup_port}/tcp && sudo firewall-cmd --reload"
fi

if command -v avahi-publish >/dev/null 2>&1; then
    log_info "mDNS is managed by ${UNIT_NAME}-mdns.service"
fi

ip="$(ip -4 -json addr show scope global 2>/dev/null \
      | jq -r '[.[].addr_info[].local] | first // "unknown"')"
api_key="$(yq -r '.server.api_key' "$CONFIG_PATH")"
omniroute_password="$(yq -r '.omniroute.initial_password' "$CONFIG_PATH")"
echo
echo "  ${BOLD}llm-server${NC}"
echo "  Local:   http://127.0.0.1:${port}/v1"
echo "  Network: http://${ip}:${port}/v1"
echo "  mDNS:    http://${mdns}.local:${port}/v1"
log_warn "some browsers (e.g. Firefox with DNS-over-HTTPS on) fail to resolve .local mDNS names and report \"Server Not Found\" even though the server is up -- use the Local/Network address above instead, or disable DNS-over-HTTPS"
echo "  API key: ${BOLD}${api_key}${NC}"
echo
echo "  ${BOLD}OmniRoute${NC}"
echo "  Local:   http://127.0.0.1:${omniroute_port}"
echo "  Network: http://${ip}:${omniroute_port}"
echo "  mDNS:    http://${mdns}.local:${omniroute_port}"
echo "  Password: ${BOLD}${omniroute_password}${NC}"
echo
echo "  ${BOLD}Remote agent setup${NC}"
echo "  On another machine on this network, run:"
echo "  curl http://${ip}:${remote_setup_port}/setup.sh | bash"
echo "  (it will prompt for the OMNI_ROUTER_MASTER_KEY from this repo's .env)"
echo
echo "  Models:"
yq -r '.models[] | select(.enabled) | "    - " + .alias' "$CONFIG_PATH"
```

- [ ] **Step 2: Write a failing test for `setup/network.sh`'s new output, then implement/run it**

No existing test in `tests/test_shell.py` invokes `setup/network.sh` at
all (confirmed by reading the file in full — no test name contains
"network", and no test references `setup/network.sh` or the exact
`log_warn`/`echo` strings it prints) — `pytest tests/test_shell.py -k
network -v` would select **zero** tests and vacuously "pass" without
exercising Step 1's changes at all. Add a real test that runs the script
directly, the same "direct subprocess against real `yq`/`jq`/`ip`" pattern
`scripts/clean.sh`'s tests already use (see
`test_cleanup_removes_the_configured_gpu_image_not_a_hardcoded_one`,
`tests/test_shell.py` ~line 2454) — `firewall-cmd`/`avahi-publish` are not
present in the test environment, so those two `command -v` guards in
`setup/network.sh` are skipped automatically, and only `ip`/`jq`/`yq` (all
three genuinely installed, verified: `which ip jq yq` all resolve) need to
be on `PATH`.

The test stubs `firewall-cmd` explicitly (rather than relying on the
ambient host's own `firewall-cmd`, which may or may not be installed on a
given CI runner) so the `command -v firewall-cmd` branch — the one that
actually prints the new `log_warn` — deterministically executes, and so
`--query-port` deterministically reports "not open" without needing an
interactive `read -rp` prompt (`stdin=subprocess.DEVNULL` makes `[ -t 0 ]`
false, so the script's own non-interactive fallback branch runs instead of
blocking on a prompt).

Add to `tests/test_shell.py`:

```python
def test_network_sh_prints_the_firewall_warning_and_the_remote_setup_one_liner(
    tmp_path: pathlib.Path,
) -> None:
    real_yq = shutil.which("yq")
    real_jq = shutil.which("jq")
    real_ip = shutil.which("ip")
    assert real_yq is not None
    assert real_jq is not None
    assert real_ip is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    for name, real in (("yq", real_yq), ("jq", real_jq), ("ip", real_ip)):
        stub = commands / name
        stub.write_text(f"#!/usr/bin/bash\nexec {real} \"$@\"\n")
        stub.chmod(0o755)
    firewall_cmd = commands / "firewall-cmd"
    firewall_cmd.write_text("#!/usr/bin/bash\nexit 1\n")
    firewall_cmd.chmod(0o755)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "server:\n"
        "  port: 8000\n"
        "  mdns_name: llm\n"
        "  api_key: fixture-api-key\n"
        "omniroute:\n"
        "  port: 20128\n"
        "  initial_password: fixture-omniroute-password\n"
        "remote_setup:\n"
        "  port: 20130\n"
        "models:\n"
        "  - alias: a\n"
        "    enabled: true\n"
    )

    result = subprocess.run(
        ["/usr/bin/bash", "setup/network.sh"],
        cwd=ROOT,
        env=os.environ
        | {
            "HOME": str(home),
            "LLM_ENV_CONFIG": str(config),
            "PATH": f"{commands}:/usr/bin:/bin",
        },
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert (
        "firewalld rules for OmniRoute (port 20128/tcp) and the remote-setup "
        "installer (port 20130/tcp) are not opened automatically" in combined
    )
    assert "sudo firewall-cmd --permanent --add-port=20128/tcp --add-port=20130/tcp" in combined
    assert "the OmniRoute container only binds 127.0.0.1" not in combined
    assert "curl http://" in result.stdout
    assert ":20130/setup.sh | bash" in result.stdout
```

This is a **regression test**, not a TDD-first test: Step 1 (the
`setup/network.sh` rewrite) already ran before this step, so there is no
separate "write it failing, then implement" cycle here — the test is
written against the already-changed script to lock its new output in
place for future changes.

Run: `uv run pytest tests/test_shell.py -k network_sh -v`
Expected: PASS. If it fails, the actual printed text doesn't match Step
1's body exactly — fix whichever of the two (test or `setup/network.sh`)
has the typo.

- [ ] **Step 3: Update `docker-compose.yml.example`**

This file is a static reference (not read by any script — see its own
header comment) showing the shape of what `render_compose()` actually
generates. It's now stale in two ways: `omniroute`'s `ports:` still shows
`127.0.0.1:20128:20128`, and there's no `remote-setup` service at all.

In `docker-compose.yml.example`, change:

```yaml
    ports:
    - 127.0.0.1:20128:20128
```

(inside the `omniroute:` service) to:

```yaml
    ports:
    - 0.0.0.0:20128:20128
```

Then, immediately after the `omniroute:` service block and before the
top-level `volumes:` key, add:

```yaml
  remote-setup:
    image: docker.io/library/python:3.13-alpine
    container_name: remote-setup
    working_dir: /app
    volumes:
    # Bind mounts, read-only: this repo's own pylib/ and the single
    # update-opencode-config.mjs file the generated /setup.sh embeds --
    # see pylib/remote_setup.py's render_setup_script(). Same "no host
    # content lost on container recreation" property as llm-server's
    # bind mounts above.
    - /home/YOU/llm-env/pylib:/app/pylib:ro,z
    - /home/YOU/llm-env/setup/update-opencode-config.mjs:/app/setup/update-opencode-config.mjs:ro,z
    # Named volume, podman-managed: the cached scoped OmniRoute API key
    # (/app/data/api-key.json). Survives container kill, `podman compose
    # down` (no -v), and host reboot the same way omniroute-data does --
    # only destroyed by `podman compose down -v` or `make clean` (which
    # warns you first; see scripts/clean.sh).
    - remote-setup-data:/app/data
    ports:
    - 0.0.0.0:20130:20130
    command:
    - python3
    - -m
    - pylib.remote_setup
    environment:
      OMNI_ROUTER_MASTER_KEY: ''
      REMOTE_SETUP_PORT: '20130'
      OMNIROUTE_INTERNAL_URL: http://omniroute:20128
      OMNIROUTE_PORT: '20128'
      OMNIROUTE_DASHBOARD_PASSWORD: ''
      MODELS_JSON: '[]'
    depends_on:
      omniroute:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test:
      - CMD
      - python3
      - -c
      - import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:20130/setup.sh', timeout=3)
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
```

Finally, change the top-level:

```yaml
volumes:
  omniroute-data: {}
```

to:

```yaml
volumes:
  omniroute-data: {}
  remote-setup-data: {}
```

- [ ] **Step 4: Update `scripts/clean.sh` for the volume warning AND the new configurable `remote_setup.image`**

Two independent gaps: (1) `make clean` runs `podman compose down -v`, which
destroys `remote-setup-data` (the cached scoped OmniRoute API key) exactly
like it already destroys `omniroute-data` — but the printed warning before
that prompt doesn't mention it. (2) `remote_setup.image` is a third
configurable container image (alongside `gpu.image` and
`omniroute.image`), but `scripts/clean.sh`'s existing image-discovery/
prompt/removal logic — read in full: it discovers `configured_image` (from
`gpu.image`) and `configured_omniroute_image` (from `omniroute.image`) the
same way, appends each to the `images_to_remove` message, and conditionally
`podman rmi -f`s each when the operator opts in — has no equivalent for
`remote_setup.image`, so opting into image removal would silently leave it
behind.

In `scripts/clean.sh`, change:

```bash
configured_omniroute_image=""
if [ -f "$CONFIG_PATH" ]; then
    configured_omniroute_image="$(yq -r '.omniroute.image // ""' "$CONFIG_PATH")" \
        || die "could not read omniroute.image from ${CONFIG_PATH}; config may be corrupt"
fi
if [ -n "$configured_omniroute_image" ] && [ "$configured_omniroute_image" != null ]; then
    images_to_remove="${images_to_remove}, ${configured_omniroute_image}"
else
    configured_omniroute_image=""
    images_to_remove="${images_to_remove}, and the configured omniroute.image if present"
fi

echo "This removes:"
echo "  compose stack   ${COMPOSE_FILE}"
echo "  compose volumes (including omniroute-data; OmniRoute's stored connections/password)"
```

to:

```bash
configured_omniroute_image=""
if [ -f "$CONFIG_PATH" ]; then
    configured_omniroute_image="$(yq -r '.omniroute.image // ""' "$CONFIG_PATH")" \
        || die "could not read omniroute.image from ${CONFIG_PATH}; config may be corrupt"
fi
if [ -n "$configured_omniroute_image" ] && [ "$configured_omniroute_image" != null ]; then
    images_to_remove="${images_to_remove}, ${configured_omniroute_image}"
else
    configured_omniroute_image=""
    images_to_remove="${images_to_remove}, and the configured omniroute.image if present"
fi

configured_remote_setup_image=""
if [ -f "$CONFIG_PATH" ]; then
    configured_remote_setup_image="$(yq -r '.remote_setup.image // ""' "$CONFIG_PATH")" \
        || die "could not read remote_setup.image from ${CONFIG_PATH}; config may be corrupt"
fi
if [ -n "$configured_remote_setup_image" ] && [ "$configured_remote_setup_image" != null ]; then
    images_to_remove="${images_to_remove}, ${configured_remote_setup_image}"
else
    configured_remote_setup_image=""
    images_to_remove="${images_to_remove}, and the configured remote_setup.image if present"
fi

echo "This removes:"
echo "  compose stack   ${COMPOSE_FILE}"
echo "  compose volumes (including omniroute-data and remote-setup-data; OmniRoute's stored connections/password and the cached remote-setup API key)"
```

Then, in the same file, change the image-removal block:

```bash
    if [ -n "$configured_omniroute_image" ]; then
        podman rmi -f "$configured_omniroute_image" 2>/dev/null || true
    fi
else
    log_info "container image(s) kept"
fi
```

to:

```bash
    if [ -n "$configured_omniroute_image" ]; then
        podman rmi -f "$configured_omniroute_image" 2>/dev/null || true
    fi
    if [ -n "$configured_remote_setup_image" ]; then
        podman rmi -f "$configured_remote_setup_image" 2>/dev/null || true
    fi
else
    log_info "container image(s) kept"
fi
```

- [ ] **Step 5: Write a test for `scripts/clean.sh`'s `remote_setup.image` handling and run it**

There is no existing test covering `omniroute.image` removal in
`scripts/clean.sh` to mirror exactly, so this models the closest existing
one instead: `test_cleanup_removes_the_configured_gpu_image_not_a_hardcoded_one`
(`tests/test_shell.py`, ~line 2454) — same `commands`/`calls`/`podman`-stub/
`REAL_YQ` scaffolding, same `LLM_ENV_ASSUME_YES`/`LLM_ENV_REMOVE_IMAGES`
env vars.

Add to `tests/test_shell.py`, right after
`test_cleanup_removes_the_configured_gpu_image_not_a_hardcoded_one`:

```python
def test_cleanup_removes_the_configured_remote_setup_image(
    tmp_path: pathlib.Path,
) -> None:
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_command(commands, "systemctl")
    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)
    calls = tmp_path / "calls"
    podman = commands / "podman"
    podman.write_text("#!/usr/bin/bash\nprintf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n")
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "remote_setup:\n  image: example.invalid/custom-remote-setup:pinned\n  port: 20130\n"
    )

    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_ASSUME_YES": "1",
        "LLM_ENV_REMOVE_IMAGES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "example.invalid/custom-remote-setup:pinned" in calls.read_text()


def test_cleanup_banner_mentions_remote_setup_data(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_command(commands, "systemctl")
    _mock_command(commands, "podman")
    real_yq = shutil.which("yq")
    assert real_yq is not None
    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text("version: 1\n")

    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=os.environ
        | {
            "HOME": str(home),
            "LLM_ENV_CONFIG": str(config),
            "LLM_ENV_ASSUME_YES": "1",
            "PATH": f"{commands}:/usr/bin:/bin",
            "REAL_YQ": real_yq,
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "remote-setup-data" in result.stdout
```

`_mock_command()` is an existing helper already used by other tests in this
file (see `test_cleanup_removes_the_configured_remote_setup_image` above
and the pre-existing `test_cleanup_removes_the_configured_gpu_image_not_a_hardcoded_one`)
that writes a no-op executable stub for the given command name — no new
helper is needed here.

Run: `uv run pytest tests/test_shell.py -k cleanup -v`
Expected: PASS, including both new tests.

- [ ] **Step 6: Check `README.md` for the same stale claim**

Run: `grep -n "127.0.0.1" README.md`
If any line describes OmniRoute as loopback-only or not LAN-reachable,
update it to describe the current behavior (LAN-reachable at
`0.0.0.0:{omniroute.port}`, following `.agents/architecture.md`'s wording
from Step 7 below). If no such line exists, no change needed here.

- [ ] **Step 6b: Update `scripts/show-secrets.sh`**

Its header comment currently reads: *"These only ever protect requests to
localhost/the compose network, never a remote endpoint, so there is no
reason to keep them out of stdout."* That premise was already wrong even
before this feature: `pylib/compose.py`'s `llm-server` service publishes
`"ports": [f"{server['port']}:{server['port']}"]` (`pylib/compose.py:48`)
— no `127.0.0.1:` prefix, so it's LAN-reachable today, same as
`omniroute` now is. The new `OMNI_ROUTER_MASTER_KEY` (in `.env`, not
`models.yml`) is also a secret this command should surface, since it's
the only thing gating the `/config` endpoint that hands out real
OmniRoute API keys to any machine on the LAN.

Replace `scripts/show-secrets.sh` in full:

```bash
#!/usr/bin/env bash
# show-secrets.sh — print the locally-generated credentials.
#
# llm-server and OmniRoute both publish LAN-reachable ports (no
# 127.0.0.1-only binding on either), and OMNI_ROUTER_MASTER_KEY gates an
# endpoint that hands out real OmniRoute API keys to any LAN machine that
# has it -- these are not localhost-only secrets. Still printed here since
# this command is meant to be run interactively by the machine's own
# owner, not exposed remotely.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd yq

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

printf 'llm-server API key:           %s\n' "$(yq -r '.server.api_key // "(not set)"' "$CONFIG_PATH")"
printf 'OmniRoute dashboard password: %s\n' "$(yq -r '.omniroute.initial_password // "(not set)"' "$CONFIG_PATH")"

env_file="${LLM_ENV_ENV_FILE:-${REPO_DIR}/.env}"
master_key="(not set)"
if [ -f "$env_file" ]; then
    line="$(grep -m1 '^OMNI_ROUTER_MASTER_KEY=' "$env_file" || true)"
    [ -n "$line" ] && master_key="${line#OMNI_ROUTER_MASTER_KEY=}"
fi
printf 'OMNI_ROUTER_MASTER_KEY:        %s\n' "$master_key"
```

`LLM_ENV_ENV_FILE` follows the same override convention `tools/lib.sh`
already uses for `CONFIG_PATH` (`"${LLM_ENV_CONFIG:-${HOME}/.config/llm-env/models.yml}"`,
`tools/lib.sh:7`) — defaulting to the real `.env` at the repo root, but
overridable so the regression test below never has to write to or read
the operator's actual `.env`.

- [ ] **Step 6c: Add a regression test for the new line**

Add to `tests/test_shell.py`:

```python
def test_show_secrets_prints_the_master_key(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    config = home / ".config" / "llm-env" / "models.yml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "server:\n  api_key: sk-test\n"
        "omniroute:\n  initial_password: dash-test\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("OMNI_ROUTER_MASTER_KEY=master-test\n", encoding="utf-8")
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/show-secrets.sh"],
        cwd=ROOT,
        env=os.environ
        | {"HOME": str(home), "LLM_ENV_CONFIG": str(config), "LLM_ENV_ENV_FILE": str(env_file)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "master-test" in result.stdout
```

This never reads or writes `ROOT/.env` — the real, gitignored file that
may hold the operator's actual `OMNI_ROUTER_MASTER_KEY` on a machine
where this test suite runs. It only exercises the new
`LLM_ENV_ENV_FILE` override.

This is a **regression test**, not a TDD-first test: `scripts/show-secrets.sh`
already exists and is being modified in place, so there is no
"write the test, watch it fail, then implement" cycle here — Step 6b's
replacement is written already. Run it to confirm the new script behaves
as intended.

Run: `uv run pytest tests/test_shell.py -k show_secrets -v`
Expected: PASS.

- [ ] **Step 7: Document the new service in `.agents/architecture.md`**

Appending one descriptive paragraph is not enough — three other parts of
this file describe the *current* two-service topology and OmniRoute's
loopback-only binding as fact, and would be left stale by that alone: the
file-responsibility table, the ASCII topology diagram, and the
"what survives a restart" lifecycle table. Apply all four edits below (the
three concrete diffs first, the descriptive paragraph last).

**7a. File-responsibility table.** In `.agents/architecture.md`'s `## Files`
table, change:

```markdown
| `pylib/omniroute.py` | Idempotent OmniRoute provider-connection provisioning via its admin API |
```

to:

```markdown
| `pylib/omniroute.py` | Idempotent OmniRoute provider-connection provisioning via its admin API |
| `pylib/remote_setup.py` | HTTP server for the master-key-gated remote-machine installer (`/setup.sh`, `/config`) |
```

And change:

```markdown
| `pylib/config.py` | Schema, enable/disable, `models_max` validation and clamping |
```

to:

```markdown
| `pylib/config.py` | Schema, enable/disable, `models_max` validation and clamping |
| `pylib/dotenv.py` | Minimal `.env`-style `KEY=VALUE` file reader (no `python-dotenv` dependency) |
```

**7b. Topology diagram.** In the `### Topology and what survives a restart`
section, change the fenced ASCII diagram from:

```
                 podman-compose default network (compose project network)
                 ┌──────────────────────────────────────────────────┐
                 │                                                  │
 host:8000 ─────▶│  llm-server  ◀───DNS "llm-server"────  omniroute │◀──── host:127.0.0.1:20128
 (LLAMA_ARG_PORT) │  (llama.cpp)                          (router)  │      (dashboard + /v1/*)
                 │      ▲                                    ▲     │
                 └──────┼────────────────────────────────────┼─────┘
                        │                                    │
              bind mount (ro)                        named volume
       ~/llm-workspace/models  :/models              omniroute-data:/app/data
       ~/.config/llm-env/presets.ini                 (dashboard password, provider
       (host directories -- not podman-managed)        connections, everything you
                                                        configure in the dashboard)
```

to:

```
                 podman-compose default network (compose project network)
                 ┌────────────────────────────────────────────────────────────────────┐
                 │                                                                    │
 host:8000 ─────▶│  llm-server  ◀──DNS "llm-server"──  omniroute  ◀─DNS "omniroute"──  remote-setup │
 (LLAMA_ARG_PORT) │  (llama.cpp)                        (router)                       (installer)   │
                 │      ▲                                    ▲                              ▲        │
                 └──────┼────────────────────────────────────┼──────────────────────────────┼────────┘
                        │                                    │                              │
              bind mount (ro)                        named volume                    bind mount (ro):
       ~/llm-workspace/models  :/models              omniroute-data:/app/data         pylib/, setup/update-
       ~/.config/llm-env/presets.ini                 (dashboard password, provider    opencode-config.mjs
       (host directories -- not podman-managed)        connections, everything you    named volume:
                                                        configure in the dashboard)    remote-setup-data:/app/data
                                                                                        (cached scoped API key)

 omniroute is also published at host:0.0.0.0:20128 (dashboard + /v1/*), and
 remote-setup at host:0.0.0.0:20130 (/setup.sh, /config) -- both genuinely
 LAN-reachable now, not loopback-only.
```

**7c. Lifecycle table.** Change:

```markdown
| Event | `llm-server`/`omniroute` containers | `omniroute-data` volume | bind-mounted models/presets |
| --- | --- | --- | --- |
| `podman kill`/crash, then `restart:` policy relaunches | recreated | untouched | untouched (host files) |
| `make stop` / `systemctl --user stop llm-server.service` (`podman compose down`, no `-v`) | removed, volume detached | **kept** | untouched |
| `make start` (`podman compose up -d` again) | recreated | reattached, same data | untouched |
| host reboot (`start_at_boot` unit, or manual `make start` after) | recreated | **kept** | untouched |
| `make clean` (`podman compose down -v`) | removed | **destroyed** — explicitly warned before confirming | untouched (models are never deleted) |
```

to:

```markdown
| Event | `llm-server`/`omniroute`/`remote-setup` containers | `omniroute-data` volume | `remote-setup-data` volume | bind-mounted models/presets |
| --- | --- | --- | --- | --- |
| `podman kill`/crash, then `restart:` policy relaunches | recreated | untouched | untouched | untouched (host files) |
| `make stop` / `systemctl --user stop llm-server.service` (`podman compose down`, no `-v`) | removed, volumes detached | **kept** | **kept** | untouched |
| `make start` (`podman compose up -d` again) | recreated | reattached, same data | reattached, same data | untouched |
| host reboot (`start_at_boot` unit, or manual `make start` after) | recreated | **kept** | **kept** | untouched |
| `make clean` (`podman compose down -v`) | removed | **destroyed** — explicitly warned before confirming | **destroyed** — explicitly warned before confirming | untouched (models are never deleted) |
```

Immediately after that table, change:

```markdown
In short: anything you configure by hand in the OmniRoute dashboard (extra
provider connections, settings) lives in the `omniroute-data` named volume
and survives every normal lifecycle operation — stopping, starting,
rebooting the host, or the container crashing and being restarted. It is
only lost if you run `make clean`, or manually `podman volume rm
omniroute-data` / `podman compose down -v`.
```

to:

```markdown
In short: anything you configure by hand in the OmniRoute dashboard (extra
provider connections, settings) lives in the `omniroute-data` named volume,
and the cached scoped API key `remote-setup` hands to remote machines lives
in the `remote-setup-data` named volume — both survive every normal
lifecycle operation — stopping, starting, rebooting the host, or a
container crashing and being restarted. Either is only lost if you run
`make clean`, or manually `podman volume rm omniroute-data
remote-setup-data` / `podman compose down -v`.
```

**7d. Descriptive paragraph.** Read `.agents/architecture.md`'s existing OmniRoute-related prose (the
paragraph inside `### Topology and what survives a restart` describing
`pylib/omniroute.py` and the provisioning flow — the exact wording may
have shifted from the omniroute-profiles plan landing first; locate it by
searching for `pylib/omniroute.py` in the file). Immediately after that
paragraph (still inside the same `### Topology and what survives a
restart` section), add:

```markdown
A third compose service, `remote-setup`, serves a one-command installer
for configuring Pi/OpenCode on another machine on the LAN against
OmniRoute: `curl http://<host>:<remote_setup.port>/setup.sh | bash`. It
runs `pylib/remote_setup.py` (stdlib-only, reuses `pylib/omniroute.py`'s
session-login helpers) under a stock `python:3.13-alpine` image with the
whole `pylib/` directory bind-mounted read-only -- no custom image build.
Exactly two public HTTP routes. `GET /setup.sh` is public and needs no
secret -- the actual credential gate is `/config`; `setup/update-opencode-
config.mjs` is bind-mounted into the container alongside `pylib/` and its
content is read and embedded directly into `/setup.sh`'s response via a
bash heredoc, never served as its own route, so the design's two-endpoint
contract holds and the remote script never needs a second HTTP
round-trip. `GET /config` requires
`Authorization: Bearer <OMNI_ROUTER_MASTER_KEY>` (a value the operator
sets in this repo's gitignored `.env`, never in `models.yml`) and, once
authorized, hands back a *scoped* OmniRoute API key from `POST
/api/keys` -- never the dashboard password (`POST /api/keys`-issued keys
are accepted by `/v1/chat/completions` but rejected by `/api/providers`,
verified live). Because OmniRoute never re-reveals a created key's raw
value after the fact, that key is cached in the `remote-setup-data`
volume (`/app/data/api-key.json`) and reused on subsequent `/config`
calls rather than minting a new one every time -- self-healing if the
cached key was later revoked from the dashboard (checked against
`GET /api/keys` on every call). This also means OmniRoute's own port
binding changed from `127.0.0.1` to `0.0.0.0` -- it is now genuinely
LAN-reachable, not just printed as if it were (see
`docs/superpowers/specs/2026-08-12-remote-agent-setup-design.md`).
```

- [ ] **Step 8: Run the project gate**

This task edits `.sh`/`.md`/`.yml.example` files (`setup/network.sh`,
`.agents/architecture.md`, `docker-compose.yml.example`,
`scripts/clean.sh`, `scripts/show-secrets.sh`, `README.md`) **and**
`tests/test_shell.py`, which is a `.py` file — so per this repo's
convention the gate before committing is the full `make validate && make
test`, not `make validate` alone.

Run: `make validate && make test`
Expected: all checks and tests pass. Before running the full suite,
additionally run the three shell-test slices this task added/touched, so a
failure is easy to localize:

Run: `uv run pytest tests/test_shell.py -k "network_sh or cleanup or show_secrets" -v`
Expected: PASS (all tests, including the two new `remote-setup`-related
`cleanup` tests from Step 5, the new `network_sh` test from Step 2, and the
new `show_secrets` test from Step 6c).

- [ ] **Step 9: Commit**

```bash
git add setup/network.sh .agents/architecture.md docker-compose.yml.example scripts/clean.sh scripts/show-secrets.sh README.md tests/test_shell.py
git commit -m "docs(remote-setup): document the installer, OmniRoute's LAN binding, and firewall/cleanup implications"
```

---

## Task 5: Point `setup/setup-local-llm-agents.sh` at OmniRoute

`setup/setup-local-llm-agents.sh` (`make setup-local-llm-agents`) currently
points Pi/OpenCode at `llm-server` directly
(`base_url="http://127.0.0.1:${port}/v1"` using `.server.port`/
`.server.api_key`). Now that OmniRoute sits in front of `llm-server` and
this repo already never hands out its dashboard password as a client
credential (Tasks 1-4 above), the *local* machine's own Pi/OpenCode should
go through OmniRoute too, for the same reasons: a revocable, scoped
credential instead of the raw server key, and a single point (OmniRoute's
combos/connections) for the owner to observe and manage every client that
talks to these models — local and remote alike.

**Files:**
- Modify: `llmenv.py`
- Modify: `tests/test_cli.py`
- Modify: `setup/setup-local-llm-agents.sh`
- Modify: `tests/test_shell.py`

**Interfaces:**
- Consumes (from Task 3): `ensure_api_key(base_url, dashboard_password,
  cache_path, key_name=KEY_NAME) -> str` from `pylib.remote_setup` (already
  parameterized by `key_name` in Task 3 Step 3 specifically so this task
  can call it with a distinct name/cache path).
- Produces: `llmenv.py omniroute issue-key` — a new CLI action, JSON on
  stdout: `{"api_key": "<key>"}`.

- [ ] **Step 1: Write the failing test for the new CLI action**

Add to `tests/test_cli.py` (mirroring this file's existing pattern for
`cmd_omniroute`'s `provision` action — find it by searching for
`"omniroute", "provision"` in the file, and reuse the same
`monkeypatch.setattr` style used there for stubbing network-touching
functions). Reuse the existing `_write_omniroute_test_config()` helper
(`tests/test_cli.py:1420-1438`) rather than a hand-rolled fixture — it
already produces a config that passes `validate_config()` in full
(`version`, `server`, `gpu`, `runtime`, `models`, plus `omniroute`):

```python
def test_omniroute_issue_key_prints_the_key(tmp_path, monkeypatch, capsys):
    import llmenv

    config = _write_omniroute_test_config(
        tmp_path, omniroute_port=20128, initial_password="dash-pw"
    )
    monkeypatch.setattr(
        llmenv,
        "ensure_api_key",
        lambda base_url, dashboard_password, cache_path, key_name: (
            "sk-local" if (base_url, dashboard_password, key_name)
            == ("http://127.0.0.1:20128", "dash-pw", "llm-env-local-agents")
            else "wrong-args"
        ),
    )
    rc = llmenv.main(["omniroute", "issue-key", "--config", str(config)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"api_key": "sk-local"}


def test_omniroute_issue_key_fails_when_unconfigured(tmp_path):
    import llmenv

    # A schema-valid config with omniroute.initial_password left blank --
    # exercises cmd_omniroute's own "not configured" guard, the same case
    # test_omniroute_provision_fails_cleanly_without_a_dashboard_password
    # already covers for the "provision" action.
    config = _write_omniroute_test_config(tmp_path, omniroute_port=20128, initial_password='""')
    rc = llmenv.main(["omniroute", "issue-key", "--config", str(config)])
    assert rc != 0
```

Each test does its own `import llmenv`, matching this file's existing
convention (see e.g. `test_run_agent_bounded_forwards_lower_limits_and_remainder`).

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py -k omniroute_issue_key -v`
Expected: FAIL — `argparse` rejects the unknown action `issue-key` (the
`omniroute` subparser currently only accepts `choices=["provision"]`).

- [ ] **Step 3: Add the `issue-key` action to `llmenv.py`**

`llmenv.py:56` currently reads:

```python
from pylib.omniroute import OmniRouteError, provision
```

Change to also import the reused key-issuance helper:

```python
from pylib.omniroute import OmniRouteError, provision
from pylib.remote_setup import ensure_api_key
```

Replace `cmd_omniroute` (`llmenv.py:368-380`):

```python
def cmd_omniroute(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    omniroute_cfg = cfg.get("omniroute") or {}
    initial_password = omniroute_cfg.get("initial_password")
    port = omniroute_cfg.get("port")
    if not initial_password or not port:
        return fail(
            "omniroute.initial_password and omniroute.port must be set; run "
            "'make start' after 'make setup' to generate them"
        )
    base_url = f"http://127.0.0.1:{port}"
    if args.action == "provision":
        result = provision(
            base_url, initial_password, cfg["server"]["port"], cfg["server"]["api_key"]
        )
        return emit(result)
    # args.action == "issue-key"
    cache_path = Path(args.config).parent / "omniroute-api-key.json"
    api_key = ensure_api_key(
        base_url, initial_password, cache_path, key_name="llm-env-local-agents"
    )
    return emit({"api_key": api_key})
```

`llmenv.py:515-518` currently reads:

```python
    omniroute_parser = sub.add_parser("omniroute")
    omniroute_parser.add_argument("--config", default=argparse.SUPPRESS)
    omniroute_parser.add_argument("action", choices=["provision"])
    omniroute_parser.set_defaults(func=cmd_omniroute)
```

Change the `choices` list:

```python
    omniroute_parser = sub.add_parser("omniroute")
    omniroute_parser.add_argument("--config", default=argparse.SUPPRESS)
    omniroute_parser.add_argument("action", choices=["provision", "issue-key"])
    omniroute_parser.set_defaults(func=cmd_omniroute)
```

The cache lives at `<config-dir>/omniroute-api-key.json` (next to
`models.yml`, e.g. `~/.config/llm-env/omniroute-api-key.json`) — a local
file on the same machine, not the `remote-setup-data` volume Task 3 uses
inside the container; there is no container here.

- [ ] **Step 4: Run the full project gate**

This step touches `.py` files (`llmenv.py`, `tests/test_cli.py`), so per
this repo's convention (see Task 4 Step 8's identical reasoning) the gate
before committing is the full `make validate && make test`, not just the
scoped test slice. Run the scoped slice first to localize any failure
quickly, then the full gate:

Run: `uv run pytest tests/test_cli.py -k omniroute_issue_key -v`
Expected: PASS.

Run: `make validate && make test`
Expected: all checks and tests pass.

- [ ] **Step 5: Commit**

```bash
git add llmenv.py tests/test_cli.py
git commit -m "feat(cli): add 'llmenv omniroute issue-key' for local-agent OmniRoute credentials"
```

- [ ] **Step 6: Point the script's credential/base-url sourcing at OmniRoute**

In `setup/setup-local-llm-agents.sh:9`, `require_cmd` currently lists
`uv curl jq yq node cmp` — `uv` is already required (it's how `llmenv()`,
defined in `tools/lib.sh`, invokes `llmenv.py`), so no change there.

Replace lines 13-14:

```bash
port="$(yq -r '.server.port // ""' "$CONFIG_PATH")"
api_key="$(yq -r '.server.api_key // ""' "$CONFIG_PATH")"
```

with:

```bash
omniroute_port="$(yq -r '.omniroute.port // ""' "$CONFIG_PATH")"
[ -n "$omniroute_port" ] || die "omniroute.port is not set; run 'make setup' first"
key_response="$(llmenv omniroute issue-key --config "$CONFIG_PATH")" \
    || die "could not obtain an OmniRoute API key; run 'make start' first"
api_key="$(jq -r '.api_key // ""' <<<"$key_response")"
[ -n "$api_key" ] || die "OmniRoute did not return a usable API key"
```

Replace line 21-22 (the port-shape check, now against `omniroute_port`):

```bash
jq -ne --arg port "$port" '$port | test("^[1-9][0-9]{0,4}$") and (tonumber <= 65535)' >/dev/null \
    || die "server port must be an integer from 1 to 65535"
```

with:

```bash
jq -ne --arg port "$omniroute_port" '$port | test("^[1-9][0-9]{0,4}$") and (tonumber <= 65535)' >/dev/null \
    || die "omniroute port must be an integer from 1 to 65535"
```

Line 23 (`[ -n "$api_key" ] || die "server API key is empty; run 'make start' first"`)
is now redundant with the new check right after `key_response` above —
delete it.

Replace line 35:

```bash
base_url="http://127.0.0.1:${port}/v1"
```

with:

```bash
base_url="http://127.0.0.1:${omniroute_port}/v1"
```

The health check at line 261 currently reads `"${base_url%/v1}/health"`.
Since `base_url` now points at OmniRoute (which has no `/health` route),
this must instead be an explicit `llm-server` health probe sourced from
`.server.port`, unrelated to `base_url`:

```bash
curl -fsS --max-time 5 -o /dev/null "${base_url%/v1}/health" \
    || die "local server is not healthy; run 'make start' and retry"
```

becomes:

```bash
server_port="$(yq -r '.server.port // ""' "$CONFIG_PATH")"
curl -fsS --max-time 5 -o /dev/null "http://127.0.0.1:${server_port}/health" \
    || die "local server is not healthy; run 'make start' and retry"
```

Note on ordering: `llmenv omniroute issue-key` (a network call to
OmniRoute, added above at line ~13) now runs *before* this health check
(line 261, unchanged position — it stays where the original script put
it, right before the staging/write steps begin, as a last chance to bail
before touching any real files). This is intentional, not an oversight:
`ensure_api_key` is idempotent (it reuses the cached key on every call
after the first, per Task 3), so an early, cheap OmniRoute round-trip
doesn't waste anything even on a run that later aborts at the health
check; and OmniRoute and `llm-server` come up together as part of the
same compose stack, so if OmniRoute answered, `llm-server` being down is
the more likely failure this check is guarding against, not the reverse.

- [ ] **Step 7: Prefix model ids with `llama-cpp/` for both clients**

Model ids sent to OmniRoute must be `llama-cpp/<alias>`, not the bare
alias (OmniRoute routes on the provider slug, not the connection's own
name — the same live-verified convention Task 3 already applies to the
remote script; see `scripts/check-server.sh:300-316`'s own comment).

In the Pi-provider `jq -n` call (`setup/setup-local-llm-agents.sh:56-70`),
change:

```bash
        models: [$models[] | {
            id: .alias,
            contextWindow: .ctx_size,
            maxTokens: .client_max_output_tokens
        }]
```

to:

```bash
        models: [$models[] | {
            id: "llama-cpp/\(.alias)",
            contextWindow: .ctx_size,
            maxTokens: .client_max_output_tokens
        }]
```

In the OpenCode-provider `jq -n` call (`setup/setup-local-llm-agents.sh:73-89`),
change:

```bash
        models: (reduce $models[] as $model ({};
            .[$model.alias] = {
                name: $model.alias,
                limit: {
                    context: $model.ctx_size,
                    output: $model.client_max_output_tokens
                }
            }))
```

to:

```bash
        models: (reduce $models[] as $model ({};
            .["llama-cpp/\($model.alias)"] = {
                name: $model.alias,
                limit: {
                    context: $model.ctx_size,
                    output: $model.client_max_output_tokens
                }
            }))
```

And in `stage_pi_settings()` (`setup/setup-local-llm-agents.sh:129-136`),
change:

```bash
          .enabledModels = [$models[0][] | "local-llm-env/\(.alias)"]
```

to:

```bash
          .enabledModels = [$models[0][] | "local-llm-env/llama-cpp/\(.alias)"]
```

**Model state.** `setup/setup-local-llm-agents.sh` (unlike the remote
installer) already maintains OpenCode's `$XDG_STATE_HOME/opencode/model.json`
(recent/favorite/variant), via `update-opencode-config.mjs --update-model-state
"$opencode_state_source" "$models_file" "$opencode_state_staged"` at
`setup/setup-local-llm-agents.sh:308-310` — this is squarely in scope
here (unlike the remote script's deliberately-out-of-scope decision,
which is about not requiring a specific pinned `opencode` version on a
*remote* machine, not about model ids). `updateModelState()`
(`setup/update-opencode-config.mjs:266-293`) sets each favorite's
`modelID` directly from the model record's `.alias` field, so once the
provider's model keys become `llama-cpp/<alias>` (this step), the
favorites written from the bare `$models_file` would point at
`modelID`s the provider no longer declares. Build a second, prefixed
copy of the models file just for this one call — `$models_file` itself
stays bare-alias, since the Pi/OpenCode provider `jq` transforms above
already do their own `"llama-cpp/\(.alias)"` prefixing from the bare
source.

Add, right after `models_file` is written (`setup/setup-local-llm-agents.sh:53-54`,
`printf '%s\n' "$models_json" >"$models_file"` / `chmod 600 "$models_file"`):

```bash
models_state_file="${workdir}/models-state.json"
jq '[.[] | .alias = "llama-cpp/\(.alias)"]' "$models_file" >"$models_state_file" \
    || die "could not build prefixed model ids for OpenCode model state"
chmod 600 "$models_state_file" || die "could not secure prefixed model ids"
```

Then change the `--update-model-state` call site
(`setup/setup-local-llm-agents.sh:308-310`) from:

```bash
node "${REPO_DIR}/setup/update-opencode-config.mjs" \
    --update-model-state "$opencode_state_source" "$models_file" "$opencode_state_staged" \
    || die "could not update OpenCode model state"
```

to:

```bash
node "${REPO_DIR}/setup/update-opencode-config.mjs" \
    --update-model-state "$opencode_state_source" "$models_state_file" "$opencode_state_staged" \
    || die "could not update OpenCode model state"
```

and the idempotency-check call right after it
(`setup/setup-local-llm-agents.sh:311-314`) the same way — its second
`--update-model-state` argument changes from `"$models_file"` to
`"$models_state_file"` too (both calls must use the same models input,
or the idempotency `cmp` a few lines later would trivially fail).

- [ ] **Step 8: Remove the local API key cache in `make clean`**

Step 3 creates `<config-dir>/omniroute-api-key.json` (e.g.
`~/.config/llm-env/omniroute-api-key.json`, next to `models.yml`) — a
plain host file, not inside any podman volume, so `make clean`'s
`podman compose down -v` (which already destroys `remote-setup-data`,
handled in Task 4 Step 4) never touches it. Without an explicit removal,
this credential would outlive every other artifact `make clean` claims to
remove.

In `scripts/clean.sh`, change:

```bash
rm -f "$CONFIG_PATH" "$COMPOSE_FILE" "${HOME}/.config/llm-env/presets.ini"
```

to:

```bash
rm -f "$CONFIG_PATH" "$COMPOSE_FILE" "${HOME}/.config/llm-env/presets.ini" \
    "$(dirname "$CONFIG_PATH")/omniroute-api-key.json"
```

And in the same file, change the earlier "This removes:" listing (already
updated once by Task 4 Step 4) from:

```bash
echo "  config          ${CONFIG_PATH}"
```

to:

```bash
echo "  config          ${CONFIG_PATH}"
echo "  local OmniRoute API key cache  $(dirname "$CONFIG_PATH")/omniroute-api-key.json"
```

Add a regression test to `tests/test_shell.py` (mirroring the existing
`test_cleanup_removes_the_configured_remote_setup_image`-style fixture
already used by Task 4 Step 5 — reuse `_mock_command()` and the same
`env=os.environ | {...}` pattern):

```python
def test_cleanup_removes_the_local_omniroute_api_key_cache(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".config" / "llm-env" / "models.yml"
    config.parent.mkdir(parents=True)
    config.write_text("gpu:\n  image: i\n", encoding="utf-8")
    cache = config.parent / "omniroute-api-key.json"
    cache.write_text('{"id": "x", "key": "y"}\n', encoding="utf-8")
    commands = tmp_path / "bin"
    commands.mkdir()
    real_yq = shutil.which("yq") or "yq"
    for name in ("podman", "systemctl"):
        command = commands / name
        command.write_text("#!/usr/bin/bash\nexit 0\n")
        command.chmod(0o755)
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=os.environ
        | {
            "HOME": str(home),
            "LLM_ENV_CONFIG": str(config),
            "LLM_ENV_ASSUME_YES": "1",
            "PATH": f"{commands}:/usr/bin:/bin",
            "REAL_YQ": real_yq,
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not cache.exists()
```

Run: `uv run pytest tests/test_shell.py -k cleanup -v`
Expected: PASS, including the new test.

- [ ] **Step 9: Extend the existing integration-test fixture for the new
  OmniRoute credential call**

`tests/test_shell.py`'s `run_setup_local_llm_agents()` helper
(`tests/test_shell.py:3906-4027`) stubs `curl`/`mktemp`/`opencode`/`mv` as
fake executables on `PATH` and drives the real script end to end. It needs
one more stub — a fake `uv` — since the script now calls `llmenv` (which
`tools/lib.sh` defines as `uv run "${REPO_DIR}/llmenv.py" "$@"`) to obtain
the OmniRoute key, and `VALID_AGENT_SETUP_CONFIG` needs an `omniroute:`
block.

Add to `VALID_AGENT_SETUP_CONFIG` (`tests/test_shell.py:22-82`), after the
`server:` block:

```yaml
omniroute:
  port: 20128
  initial_password: fixture-dashboard-password
```

Add a fake `uv` stub in `run_setup_local_llm_agents()`, alongside the
existing `curl`/`mktemp`/`opencode`/`mv` stubs (right before the
`if failing_command is not None:` block at `tests/test_shell.py:4000`).
Unlike those four, this stub must NOT reject every call it doesn't
recognize: `setup/setup-local-llm-agents.sh:11` already calls
`migrate_config_file` before any of this task's new code runs, which
itself shells out to `uv run "${REPO_DIR}/llmenv.py" migrate-config ...`
(`tools/lib.sh:264-272`) — so the stub must delegate every invocation
*other than* `omniroute issue-key` to the real `uv`, exactly like the
existing `REAL_UV`/`exec "$REAL_UV" "$@"` pattern this file already uses
in `run_render_unit_with_legacy_rocm_config()` (`tests/test_shell.py:2758-2762`):

```python
    real_uv = shutil.which("uv")
    assert real_uv is not None
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "printf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        "  *'omniroute issue-key'*) printf '{\"api_key\": \"%s\"}\\n' \"$OMNIROUTE_API_KEY\" ;;\n"
        "  *) exec \"$REAL_UV\" \"$@\" ;;\n"
        "esac\n"
    )
    uv.chmod(0o755)
```

And add `"REAL_UV": real_uv` to the subprocess `env=os.environ | {...}`
block (`tests/test_shell.py:4007-4022`), alongside the existing
`"REAL_YQ": shutil.which("yq") or "yq"` entry.

Add a matching `omniroute_api_key: str = "fixture-omniroute-api-key"`
keyword parameter to `run_setup_local_llm_agents()`'s signature (next to
`opencode_version`), and pass it through to the subprocess environment
(next to `"OPENCODE_VERSION": opencode_version` in the `env=os.environ |
{...}` block at `tests/test_shell.py:4007-4022`) as
`"OMNIROUTE_API_KEY": omniroute_api_key`.

Update the `curl` stub's hardcoded health-check URL
(`tests/test_shell.py:3961`, `[ "${!#}" = 'http://127.0.0.1:18123/health' ]`)
— this stays exactly as-is: `server.port` is still `18123` in
`VALID_AGENT_SETUP_CONFIG` and Step 6 above keeps the health check pointed
at `llm-server` directly via `server_port`, so no change needed here.

**Remove the now-obsolete empty-`server.api_key` case.**
`test_setup_local_llm_agents_rejects_invalid_inputs_without_replacing_files`'s
`@pytest.mark.parametrize` list (`tests/test_shell.py:4563-4581`) has a
first case that replaces `api_key: fixture-local-api-key` with
`api_key: ''` and expects `result.returncode != 0`. After Step 6 the
script no longer reads `.server.api_key` at all (the empty-key check was
deleted along with it), so that case's premise is gone — an empty
`server.api_key` no longer makes this script fail, and this exact
fixture's stubbed `uv` (Step 8) always returns a fixed key regardless of
config content, so there's no way to re-target this parametrize case at
an equivalent OmniRoute-side failure without a fake `uv` that actually
inspects the config (out of scope for this task). Delete just that first
tuple from the parametrize list:

```python
        (
            VALID_AGENT_SETUP_CONFIG.replace(
                "api_key: fixture-local-api-key", "api_key: ''"
            ),
            0,
            None,
        ),
```

The remaining four cases (`port: 0`, `enabled: false`, `health_exit: 1`,
and the malformed `pi_text`) are untouched and still exercise real
negative-input paths.

- [ ] **Step 10: Update the fixture's downstream assertions**

Every existing test that calls `run_setup_local_llm_agents(...)` and then
asserts on model ids, `enabledModels`, or OpenCode's model-state
`modelID`s needs some subset of five mechanical substitutions. Tests that
call `run_opencode_config_editor(...)` instead (e.g. around
`tests/test_shell.py:3683-3885`) are a *different* fixture — they test
`update-opencode-config.mjs` directly against hand-rolled synthetic
provider JSON, unrelated to this task's alias-prefixing change — do
**not** touch those.

1. `"baseUrl": "http://127.0.0.1:18123/v1"` / `"baseURL": "http://127.0.0.1:18123/v1"`
   → `"http://127.0.0.1:20128/v1"` (OmniRoute's fixture port from Step 8).
2. `"apiKey": "fixture-local-api-key"` → `"apiKey": "fixture-omniroute-api-key"`
   (Step 8's new default), and the corresponding
   `assert "fixture-local-api-key" not in result.stdout + result.stderr`
   lines become `assert "fixture-omniroute-api-key" not in result.stdout +
   result.stderr`.
3. Model ids `"gemma4"` / `"ornith"` inside the Pi provider's `"models"`/`"id"`
   fields (`[{"id": "gemma4", ...}, {"id": "ornith", ...}]`) and the
   OpenCode provider's model-map keys (`{"gemma4": {...}, "ornith": {...}}`)
   → `"llama-cpp/gemma4"` / `"llama-cpp/ornith"`. The `"name"` field inside
   each model entry (e.g. `"name": "gemma4"`) stays the bare alias — only
   the *id*/map-key changes, matching Task 3's identical
   `id`-vs-`name` distinction in `SETUP_SCRIPT_TEMPLATE`.
4. `"local-llm-env/gemma4"` / `"local-llm-env/ornith"` in `enabledModels`
   arrays → `"local-llm-env/llama-cpp/gemma4"` /
   `"local-llm-env/llama-cpp/ornith"` (Step 7's `stage_pi_settings` change).
5. `{"providerID": "local-llm-env", "modelID": "gemma4"}` /
   `{"providerID": "local-llm-env", "modelID": "ornith"}` in OpenCode
   model-state (`favorite`) assertions → `"modelID": "llama-cpp/gemma4"` /
   `"modelID": "llama-cpp/ornith"` (Step 7's `models_state_file` change).
   `"providerID"` is unaffected — it's always `"local-llm-env"`.

Run `grep -n '"id": "gemma4"\|"id": "ornith"\|modelID.*"gemma4"\|modelID.*"ornith"\|"gemma4": {\|"ornith": {\|local-llm-env/gemma4\|local-llm-env/ornith' tests/test_shell.py`
to find every occurrence, then check each hit's enclosing test: if it's
inside a `run_setup_local_llm_agents(...)`-based test, apply the matching
rule above; if it's inside a `run_opencode_config_editor(...)`-based test,
skip it. The confirmed `run_setup_local_llm_agents`-based hits, beyond
`test_setup_local_llm_agents_creates_private_provider_files` (rewritten
below) are:

- `test_reverse_setup_selection_drives_client_model_order`
  (`tests/test_shell.py:1056-1107`) — rewritten below (rules 3, 4, 5 all
  apply; this one also reorders models via `models select`, so its
  expected list order matters, not just the prefixing).
- `test_setup_creates_missing_opencode_state_only_for_compatible_1_18_10`
  (`:4030-4046`) and `test_setup_existing_state_does_not_require_version_or_path_compatibility`
  (`:4081-4097`) — rule 5 only (`modelID` values in a `state_path.read_text()`
  assertion; no other part of these two tests touches model ids).
- `test_setup_stages_every_target_before_first_replacement`
  (`:4118-4145`) — rules 4 and 5 (`enabledModels` and `modelID` both
  appear in this test's staged-content assertions).
- `test_setup_local_llm_agents_creates_private_provider_files`
  (`:4177-4235`) — rules 1, 2, 3 — rewritten below.
- `test_setup_local_llm_agents_updates_every_global_file_that_defines_the_provider`
  (`:4260-4291`) — rule 3 only — rewritten below.
- `test_setup_local_llm_agents_sets_exact_pi_cycle` (`:4457-4471`) — rule
  4 only — rewritten below.

`test_setup_local_llm_agents_creates_private_provider_files`
(`tests/test_shell.py:4177-4235`) becomes:

```python
def test_setup_local_llm_agents_creates_private_provider_files(
    tmp_path: pathlib.Path,
) -> None:
    pi_path = tmp_path / "home/.pi/agent/models.json"
    assert not pi_path.parent.exists()
    result, calls, pi_path, settings_path, opencode_paths, state_path = (
        run_setup_local_llm_agents(tmp_path)
    )
    config_json, opencode_json, opencode_jsonc = opencode_paths

    assert result.returncode == 0, result.stderr
    assert json.loads(pi_path.read_text())["providers"]["local-llm-env"] == {
        "baseUrl": "http://127.0.0.1:20128/v1",
        "api": "openai-completions",
        "apiKey": "fixture-omniroute-api-key",
        "compat": {
            "supportsDeveloperRole": False,
            "supportsReasoningEffort": False,
        },
        "models": [
            {"id": "llama-cpp/gemma4", "contextWindow": 131072, "maxTokens": 8192},
            {"id": "llama-cpp/ornith", "contextWindow": 131072, "maxTokens": 8192},
        ],
    }
    assert not config_json.exists()
    assert not opencode_json.exists()
    assert json.loads(opencode_jsonc.read_text())["provider"]["local-llm-env"] == {
        "npm": "@ai-sdk/openai-compatible",
        "name": "local-llm-env",
        "options": {
            "baseURL": "http://127.0.0.1:20128/v1",
            "apiKey": "fixture-omniroute-api-key",
        },
        "models": {
            "llama-cpp/gemma4": {
                "name": "gemma4",
                "limit": {"context": 131072, "output": 8192},
            },
            "llama-cpp/ornith": {
                "name": "ornith",
                "limit": {"context": 131072, "output": 8192},
            },
        },
    }
    assert stat.S_IMODE(pi_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(opencode_jsonc.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    calls_made = calls.read_text().splitlines()
    health_index = next(
        index for index, call in enumerate(calls_made) if call.startswith("curl ")
    )
    first_target_stage = next(
        index
        for index, call in enumerate(calls_made)
        if call.startswith("mktemp ") and ".XXXXXX" in call
    )
    assert health_index < first_target_stage
    assert "fixture-omniroute-api-key" not in result.stdout + result.stderr
```

`test_reverse_setup_selection_drives_client_model_order`
(`tests/test_shell.py:1089-1107`, the part after `run_setup_local_llm_agents`
is called) becomes:

```python
    assert result.returncode == 0, result.stderr
    pi_provider = json.loads(pi_path.read_text())["providers"]["local-llm-env"]
    assert [model["id"] for model in pi_provider["models"]] == [
        "llama-cpp/ornith",
        "llama-cpp/gemma4",
    ]
    assert json.loads(settings_path.read_text())["enabledModels"] == [
        "local-llm-env/llama-cpp/ornith",
        "local-llm-env/llama-cpp/gemma4",
    ]
    opencode_provider = json.loads(opencode_paths[2].read_text())["provider"][
        "local-llm-env"
    ]
    assert list(opencode_provider["models"]) == ["llama-cpp/ornith", "llama-cpp/gemma4"]
    assert json.loads(state_path.read_text())["favorite"][:2] == [
        {"providerID": "local-llm-env", "modelID": "llama-cpp/ornith"},
        {"providerID": "local-llm-env", "modelID": "llama-cpp/gemma4"},
    ]
```

`test_setup_local_llm_agents_updates_every_global_file_that_defines_the_provider`
(`tests/test_shell.py:4277-4287`, the `assert provider["models"] == {...}`
block) becomes:

```python
        assert provider["models"] == {
            "llama-cpp/gemma4": {
                "name": "gemma4",
                "limit": {"context": 131072, "output": 8192},
            },
            "llama-cpp/ornith": {
                "name": "ornith",
                "limit": {"context": 131072, "output": 8192},
            },
        }
```

`test_setup_local_llm_agents_sets_exact_pi_cycle`
(`tests/test_shell.py:4466-4469`) becomes:

```python
    assert settings["enabledModels"] == [
        "local-llm-env/llama-cpp/gemma4",
        "local-llm-env/llama-cpp/ornith",
    ]
```

For `test_setup_creates_missing_opencode_state_only_for_compatible_1_18_10`,
`test_setup_existing_state_does_not_require_version_or_path_compatibility`,
and `test_setup_stages_every_target_before_first_replacement`, apply rule
5 (and rule 4 for the last one) to each `"modelID": "gemma4"` /
`"modelID": "ornith"` / `"local-llm-env/gemma4"` / `"local-llm-env/ornith"`
literal found by the Step 9 grep above, inside those three tests
specifically — same substitution, no structural change to any of them.

- [ ] **Step 11: Run the tests**

Run: `uv run pytest tests/test_shell.py -k setup_local_llm_agents -v`
Expected: PASS, including the updated
`test_setup_local_llm_agents_creates_private_provider_files` and every
other test touched by Step 9's substitutions.

- [ ] **Step 12: Update `.agents/architecture.md`**

In the paragraph added by Task 4 Step 7 documenting `remote-setup`, add
one sentence noting that `make setup-local-llm-agents` now goes through
the identical OmniRoute-credential path locally (via `llmenv omniroute
issue-key`, a distinct `llm-env-local-agents` key, never the dashboard
password), so a reader comparing the two doesn't need to guess whether
they diverge.

- [ ] **Step 13: Run the full project gate**

Run: `make validate && make test`
Expected: all checks and tests pass.

- [ ] **Step 14: Commit**

```bash
git add setup/setup-local-llm-agents.sh scripts/clean.sh tests/test_shell.py .agents/architecture.md
git commit -m "feat(setup-local-llm-agents): route Pi/OpenCode through OmniRoute instead of llm-server directly"
```
