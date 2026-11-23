"""Tracked host profile: schema, export/apply/check, setup precedence, .env, prerequisites and firewall.

Plan: docs/superpowers/plans/2026-11-23-tracked-host-profile.md. Fakes only: no podman, sudo or network.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pylib.config import ConfigError, load_config
from pylib.profile import apply_profile, export_profile, validate_profile

API_KEY = "SENTINEL-API-KEY-7f3a"
DASH_PW = "SENTINEL-DASHBOARD-PW-91c2"
MASTER_KEY = "SENTINEL-MASTER-KEY-55e0"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(REPO / "llmenv.py"), *args],
                          capture_output=True, text=True, cwd=REPO, check=False)


def write_config(tmp_path: Path, **changes: Any) -> Path:
    """The shipped template plus sentinel secrets and generated values, mode 0600."""
    cfg = yaml.safe_load((REPO / "models.yml.example").read_text())
    cfg["server"]["api_key"] = API_KEY
    cfg["omniroute"]["initial_password"] = DASH_PW
    cfg["gpu"] |= {"pci_address": "0000:03:00.0", "device_name": "Fixture GPU", "vram_total_mib": 16304}
    cfg.setdefault("resources", {}).setdefault("llm_server", {}).update(cpus=14, memory_mib=18730)
    for path, value in changes.items():
        section, key = path.split("__")
        cfg[section][key] = value
    config = tmp_path / "models.yml"
    config.write_text(yaml.safe_dump(cfg, sort_keys=False))
    config.chmod(0o600)
    (tmp_path / ".env").write_text(f"OMNI_ROUTER_MASTER_KEY={MASTER_KEY}\n")
    return config


def export(tmp_path: Path, config: Path) -> tuple[subprocess.CompletedProcess[str], Path]:
    profile = tmp_path / "llm-env.profile.yml"
    result = run("--config", str(config), "profile", "export", "--profile", str(profile),
                 "--env-file", str(tmp_path / ".env"))
    return result, profile


# ---- schema ----

def test_unknown_keys_are_errors_at_every_level() -> None:
    errors = validate_profile({"version": 1, "gpu": {}, "server": {"api_key": "x"},
                               "network": {"firewall": {"zone": "home"}},
                               "models": [{"alias": "m", "benchmark": {}}]})
    assert "unknown key: gpu" in errors
    assert "unknown key: server.api_key" in errors
    assert "unknown key: network.firewall.zone" in errors
    assert "unknown key: models[m].benchmark" in errors


def test_types_and_cidrs_are_validated() -> None:
    errors = validate_profile({"version": 2, "boot": {"enable": "yes"}, "observe": {"enabled": 1},
                               "network": {"firewall": {"manage": "true", "allowed_cidrs": ["10.0.0.0/8", "lan", "10.0.0.1"]}},
                               "llm_server": {"models": []}, "server": {"port": 0}})
    assert "version must be 1" in errors
    assert "boot.enable must be a Boolean" in errors
    assert "observe.enabled must be a Boolean" in errors
    assert "network.firewall.manage must be a Boolean" in errors
    assert "network.firewall.allowed_cidrs: not a CIDR: 'lan'" in errors
    assert "network.firewall.allowed_cidrs: not a CIDR: '10.0.0.1'" in errors
    assert "llm_server.models must be a non-empty list of distinct model aliases" in errors
    assert "server.port must be a positive integer" in errors


def test_values_go_through_the_config_validators(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path))
    with pytest.raises(ConfigError, match="system_one.listen must be loopback or lan"):
        apply_profile(cfg, {"version": 1, "system_one": {"listen": "wan"}})
    with pytest.raises(ConfigError, match="memory_max_mib"):
        apply_profile(cfg, {"version": 1, "resources": {"overrides": {"omniroute": {"memory_max_mib": 0}}}})
    with pytest.raises(ConfigError, match="unknown model alias: nope"):
        apply_profile(cfg, {"version": 1, "llm_server": {"models": ["nope"]}})


def test_cli_reports_an_unknown_key_without_writing(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    before = config.read_bytes()
    profile = tmp_path / "p.yml"
    profile.write_text("version: 1\nsurprise: true\n")
    result = run("--config", str(config), "profile", "apply", "--profile", str(profile))
    assert result.returncode == 1
    assert "unknown key: surprise" in json.loads(result.stdout)["error"]
    assert config.read_bytes() == before


# ---- export ----

def test_export_contains_no_secret_and_round_trips_without_drift(tmp_path: Path) -> None:
    config = write_config(tmp_path, system_one__listen="lan", server__start_at_boot=True)
    result, profile = export(tmp_path, config)
    assert result.returncode == 0, result.stdout + result.stderr
    text = profile.read_text()
    for secret in (API_KEY, DASH_PW, MASTER_KEY, "0000:03:00.0", "Fixture GPU", "18730"):
        assert secret not in text and secret not in result.stdout + result.stderr
    data = yaml.safe_load(text)
    assert validate_profile(data) == []
    assert data["system_one"]["listen"] == "lan" and data["boot"] == {"enable": True}
    assert data["observe"] == {"enabled": False}
    assert data["network"] == {"firewall": {"manage": False, "allowed_cidrs": []}}
    assert stat.S_IMODE(profile.stat().st_mode) == 0o644
    check = run("--config", str(config), "profile", "check", "--profile", str(profile))
    assert check.returncode == 0 and json.loads(check.stdout)["drift"] is False


def test_export_keeps_profile_only_sections_and_refuses_secret_values(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    _, profile = export(tmp_path, config)
    data = yaml.safe_load(profile.read_text())
    data["observe"] = {"enabled": True}
    data["network"]["firewall"] = {"manage": True, "allowed_cidrs": ["192.168.1.0/24"]}
    profile.write_text(yaml.safe_dump(data))
    result, _ = export(tmp_path, config)
    assert result.returncode == 0
    again = yaml.safe_load(profile.read_text())
    assert again["observe"] == {"enabled": True}
    assert again["network"]["firewall"]["allowed_cidrs"] == ["192.168.1.0/24"]
    cfg = load_config(config)
    cfg["server"]["mdns_name"] = API_KEY  # a secret value inside an allowlisted key
    with pytest.raises(ConfigError, match="secret"):
        export_profile(cfg)


# ---- apply / check ----

def test_apply_is_idempotent_and_backs_up_once(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    profile = tmp_path / "p.yml"
    profile.write_text("version: 1\nsystem_one:\n  listen: lan\nomniroute:\n  port: 20999\n")
    first = run("--config", str(config), "profile", "apply", "--profile", str(profile))
    assert first.returncode == 0, first.stdout
    payload = json.loads(first.stdout)
    assert payload["changed"] is True
    assert {d["path"] for d in payload["differences"]} == {"system_one.listen", "omniroute.port"}
    backup = Path(payload["backup"])
    assert backup.exists() and stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    applied = config.read_bytes()
    second = run("--config", str(config), "profile", "apply", "--profile", str(profile))
    assert json.loads(second.stdout) == {"changed": False, "backup": None, "differences": []}
    assert config.read_bytes() == applied
    assert sorted(p.name for p in tmp_path.glob("models.yml.profile-*.bak")) == [backup.name]


def test_apply_leaves_generated_secrets_untouched(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    _, profile = export(tmp_path, config)
    data = yaml.safe_load(profile.read_text())
    data["server"]["port"] = 8111
    profile.write_text(yaml.safe_dump(data))
    result = run("--config", str(config), "profile", "apply", "--profile", str(profile))
    assert result.returncode == 0
    assert API_KEY not in result.stdout and DASH_PW not in result.stdout
    cfg = yaml.safe_load(config.read_text())
    assert cfg["server"]["port"] == 8111
    assert cfg["server"]["api_key"] == API_KEY
    assert cfg["omniroute"]["initial_password"] == DASH_PW
    assert cfg["gpu"]["pci_address"] == "0000:03:00.0"
    assert cfg["resources"]["llm_server"]["cpus"] == 14


def test_check_detects_drift_read_only_and_clears_after_apply(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    profile = tmp_path / "p.yml"
    profile.write_text("version: 1\nruntime:\n  cache_type_k: q8_0\nboot:\n  enable: true\n")
    before = config.read_bytes()
    drift = run("--config", str(config), "profile", "check", "--profile", str(profile))
    assert drift.returncode == 1
    report = json.loads(drift.stdout)
    assert report["drift"] is True
    assert {"path": "server.start_at_boot", "live": False, "profile": True} in report["differences"]
    assert config.read_bytes() == before
    assert run("--config", str(config), "profile", "apply", "--profile", str(profile)).returncode == 0
    assert run("--config", str(config), "profile", "check", "--profile", str(profile)).returncode == 0


def test_owner_profile_reproduces_its_models_on_a_fresh_template(tmp_path: Path) -> None:
    """A new host starts from models.yml.example, which lacks the owner's tuned model."""
    owner = yaml.safe_load((REPO / "llm-env.profile.yml").read_text())
    assert validate_profile(owner) == []
    fresh = load_config(REPO / "models.yml.example")
    merged = apply_profile(fresh, owner)
    enabled = [m["alias"] for m in merged["models"] if m["enabled"]]
    assert enabled == owner["llm_server"]["models"]
    assert apply_profile(merged, owner) == merged
    example = yaml.safe_load((REPO / "llm-env.profile.example.yml").read_text())
    assert validate_profile(example) == []
    apply_profile(fresh, example)


def test_tracked_profiles_hold_no_secret_keys() -> None:
    for name in ("llm-env.profile.yml", "llm-env.profile.example.yml"):
        data = yaml.safe_load((REPO / name).read_text())
        assert "api_key" not in data.get("server", {}) and "initial_password" not in data.get("omniroute", {})


# ---- setup precedence and .env ----

def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/bash\n" + body)
    path.chmod(0o755)


def run_setup(tmp_path: Path, profile_text: str | None, env: dict[str, str], answers: str = "") -> subprocess.CompletedProcess[str]:
    """Gateway-only setup (no GPU steps) against real llmenv, fake podman/curl, private HOME/.env."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("podman", "curl", "ip"):
        _stub(bin_dir, name, "exit 0\n")
    config = write_config(tmp_path)
    (tmp_path / ".env").unlink()
    uv_dir = str(Path(shutil.which("uv") or "uv").parent)
    environment = {k: v for k, v in os.environ.items() if not k.startswith("LLM_ENV_")} | {
        "PATH": f"{bin_dir}:{uv_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config), "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "LLM_ENV_ENV_FILE": str(tmp_path / ".env"), "LLM_ENV_PROFILE": str(tmp_path / "p.yml"),
    } | env
    if profile_text is not None:
        (tmp_path / "p.yml").write_text(profile_text)
    return subprocess.run(["/usr/bin/bash", "setup/setup.sh"], cwd=REPO, env=environment, input=answers,
                          text=True, capture_output=True, timeout=300, check=False)


def test_setup_applies_the_profile_and_it_wins_only_for_keys_it_names(tmp_path: Path) -> None:
    # The profile names llm_server.enabled (false) and the remote-setup port; LLM_ENV_NO_GPU=0 asks for
    # GPU inference, but the profile wins. system_one.enabled is not named, so LLM_ENV_SYSTEM_ONE=0 decides.
    result = run_setup(tmp_path, "version: 1\nllm_server:\n  enabled: false\nremote_setup:\n  port: 20777\n",
                       {"LLM_ENV_ASSUME_YES": "1", "LLM_ENV_NO_GPU": "0", "LLM_ENV_SYSTEM_ONE": "0"})
    assert result.returncode == 0, result.stdout + result.stderr
    cfg = yaml.safe_load((tmp_path / "models.yml").read_text())
    assert cfg["llm_server"]["enabled"] is False
    assert cfg["remote_setup"]["port"] == 20777
    assert cfg["system_one"]["enabled"] is False
    output = result.stdout + result.stderr
    assert "Enable local GPU inference" not in output  # never prompted for a profile key
    assert "Enable local System One decisions" in output  # still prompted (seeded) for an unnamed key
    assert "applied profile" in output
    env_file = tmp_path / ".env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    key = env_file.read_text().strip().removeprefix("OMNI_ROUTER_MASTER_KEY=")
    assert len(key) == 64 and key not in output


def test_setup_without_a_profile_keeps_todays_prompts(tmp_path: Path) -> None:
    result = run_setup(tmp_path, None, {"LLM_ENV_ASSUME_YES": "1", "LLM_ENV_NO_GPU": "1", "LLM_ENV_SYSTEM_ONE": "0"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Enable local GPU inference" in result.stdout + result.stderr
    assert "applied profile" not in result.stdout + result.stderr


def ensure_master_key(env_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["/usr/bin/bash", "-c", 'source tools/lib.sh; ensure_master_key'], cwd=REPO,
                          env=os.environ | {"LLM_ENV_ENV_FILE": str(env_file)},
                          text=True, capture_output=True, check=False)


def test_master_key_is_created_once_and_never_overwritten(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    first = ensure_master_key(env_file)
    assert first.returncode == 0, first.stderr
    content = env_file.read_text()
    key = content.strip().removeprefix("OMNI_ROUTER_MASTER_KEY=")
    assert len(key) == 64 and int(key, 16) >= 0
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert key not in first.stdout + first.stderr
    assert ensure_master_key(env_file).returncode == 0
    assert env_file.read_text() == content  # idempotent: the existing key is kept


def test_master_key_fills_an_empty_line_keeps_other_lines_and_tightens_mode(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nOTHER=1\nOMNI_ROUTER_MASTER_KEY=\n")
    env_file.chmod(0o644)
    assert ensure_master_key(env_file).returncode == 0
    lines = env_file.read_text().splitlines()
    assert lines[:2] == ["# comment", "OTHER=1"] and len(lines) == 3
    assert len(lines[2].removeprefix("OMNI_ROUTER_MASTER_KEY=")) == 64
    kept = tmp_path / "kept.env"
    kept.write_text(f"OMNI_ROUTER_MASTER_KEY={MASTER_KEY}\n")
    kept.chmod(0o644)
    assert ensure_master_key(kept).returncode == 0
    assert kept.read_text() == f"OMNI_ROUTER_MASTER_KEY={MASTER_KEY}\n"
    assert stat.S_IMODE(kept.stat().st_mode) == 0o600


# ---- check-setup ----

def test_check_setup_skips_the_compose_check_before_the_first_render(tmp_path: Path) -> None:
    result, calls_file, _ = __import__("test_shell").run_check_setup_with_stubs(
        tmp_path, llm_server_enabled=False, compose_rendered=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Verdict: SKIP reason=compose not rendered yet" in result.stderr
    assert "compose" not in calls_file.read_text().replace("docker-compose", "")


@pytest.mark.parametrize("drift", [False, True])
def test_check_setup_runs_the_profile_drift_check(tmp_path: Path, drift: bool) -> None:
    result, calls_file, _ = __import__("test_shell").run_check_setup_with_stubs(
        tmp_path, llm_server_enabled=False, profile_drift=drift)
    assert f"profile check --profile {tmp_path / 'llm-env.profile.yml'}" in calls_file.read_text()
    if drift:
        assert result.returncode == 1 and "Verdict: FAIL stage=command exit reason=profile exited 1" in result.stderr
    else:
        assert result.returncode == 0 and "Verdict: PASS identity=profile drift" in result.stdout


# ---- prerequisites on dnf Fedora ----

def test_prerequisites_install_with_dnf_on_a_package_based_fedora(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    for name in ("uv", "jq", "podman", "curl", "ip", "git", "shellcheck", "node", "firewall-cmd", "avahi-publish", "skopeo"):
        _stub(bin_dir, name, "exit 0\n")
    _stub(bin_dir, "dirname", 'case $1 in */*) printf "%s\\n" "${1%/*}" ;; *) printf ".\\n" ;; esac\n')
    _stub(bin_dir, "yq", "printf '%s\\n' 'yq (https://github.com/mikefarah/yq/) version v4.45.1'\n")
    _stub(bin_dir, "sudo", 'exec "$@"\n')
    for tool in ("dnf", "rpm-ostree"):
        _stub(bin_dir, tool, f'printf "{tool} %s\\n" "$*" >> "$CALLS"\n')
    environment = os.environ | {"PATH": str(bin_dir), "CALLS": str(calls),
                                "LLM_ENV_OSTREE_BOOTED": str(tmp_path / "absent")}
    check = subprocess.run(["/usr/bin/bash", "setup/prerequisites.sh", "--check"], cwd=REPO, env=environment,
                           text=True, capture_output=True, check=False)
    assert check.returncode == 1  # openssl is a runtime requirement
    assert "missing    openssl" in check.stdout and "installed  skopeo" in check.stdout
    result = subprocess.run(["/usr/bin/bash", "setup/prerequisites.sh"], cwd=REPO, env=environment,
                            input="yes\n", text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "sudo dnf install -y openssl" in result.stdout
    assert calls.read_text() == "dnf install -y openssl\n"  # no rpm-ostree, no apply-live


# ---- firewall ----

FAKE_FIREWALL = r'''{ printf "firewall-cmd"; printf " %s" "$@"; printf "\n"; } >> "$CALLS"
file="$FW_STATE/runtime"; args=()
for a in "$@"; do if [ "$a" = --permanent ]; then file="$FW_STATE/permanent"; else args+=("$a"); fi; done
touch "$file"; a="${args[0]}"
case "$a" in
  --state) [ "${FW_RUNNING:-1}" = 1 ] ;;
  --query-port=*) [ "${FW_RANGE_OPEN:-0}" = 1 ] || grep -qxF "port ${a#*=}" "$file" ;;
  --query-rich-rule=*) grep -qxF "rich ${a#*=}" "$file" ;;
  --add-port=*) grep -qxF "port ${a#*=}" "$file" || echo "port ${a#*=}" >> "$file" ;;
  --add-rich-rule=*) grep -qxF "rich ${a#*=}" "$file" || echo "rich ${a#*=}" >> "$file" ;;
  *) exit 2 ;;
esac
'''


def firewall_world(tmp_path: Path, *, manage: bool = True, cidrs: list[str] | None = None,
                   listen: str = "lan", sudo_ok: bool = True) -> dict[str, Any]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    state = tmp_path / "fw"
    state.mkdir(exist_ok=True)
    _stub(bin_dir, "firewall-cmd", FAKE_FIREWALL)
    _stub(bin_dir, "sudo", '{ printf "sudo"; printf " %s" "$@"; printf "\\n"; } >> "$CALLS"\n'
          '[ "$1" = -n ] && shift\n[ "$1" = true ] && exit "$SUDO_RC"\nexec "$@"\n')
    config = write_config(tmp_path, system_one__enabled=True, system_one__listen=listen)
    profile = tmp_path / "p.yml"
    profile.write_text(yaml.safe_dump({"version": 1, "network": {"firewall": {"manage": manage, "allowed_cidrs": cidrs or []}}}))
    env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(tmp_path / "calls"),
                        "FW_STATE": str(state), "SUDO_RC": "0" if sudo_ok else "1",
                        "LLM_ENV_CONFIG": str(config), "LLM_ENV_PROFILE": str(profile)}
    return {"env": env, "calls": tmp_path / "calls", "state": state}


def firewall(world: dict[str, Any], *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["/usr/bin/bash", "scripts/firewall.sh", *args], cwd=REPO, env=world["env"] | env,
                          stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=60, check=False)


def rules(world: dict[str, Any], scope: str) -> list[str]:
    path = world["state"] / scope
    return path.read_text().splitlines() if path.exists() else []


def calls(world: dict[str, Any]) -> str:
    return world["calls"].read_text() if world["calls"].exists() else ""


def test_firewall_adds_exactly_the_enabled_lan_ports_and_is_idempotent(tmp_path: Path) -> None:
    world = firewall_world(tmp_path)
    first = firewall(world)
    assert first.returncode == 0, first.stdout + first.stderr
    expected = ["port 8000/tcp", "port 20128/tcp", "port 20130/tcp", "port 8010/tcp"]
    assert rules(world, "runtime") == expected and rules(world, "permanent") == expected
    assert "run: sudo -n firewall-cmd --add-port=8000/tcp" in first.stdout
    assert "run: sudo -n firewall-cmd --permanent --add-port=8010/tcp" in first.stdout
    assert first.stdout.count("run: ") == 8
    second = firewall(world)
    assert second.returncode == 0 and "run: " not in second.stdout and "nothing to do" in second.stdout
    assert "--remove" not in calls(world)


def test_firewall_skips_a_loopback_laya_and_does_nothing_when_not_managed(tmp_path: Path) -> None:
    world = firewall_world(tmp_path, listen="loopback")
    assert firewall(world).returncode == 0
    assert "port 8010/tcp" not in rules(world, "runtime")
    off = firewall_world(tmp_path / "off", manage=False)
    result = firewall(off)
    assert result.returncode == 0 and "nothing to do" in result.stdout
    assert "--add" not in calls(off) and "--query" not in calls(off)


def test_firewall_cidrs_use_rich_rules_and_never_remove_an_open_port(tmp_path: Path) -> None:
    world = firewall_world(tmp_path, cidrs=["192.168.1.0/24", "fd00::/8"])
    result = firewall(world, FW_RANGE_OPEN="1")
    assert result.returncode == 0, result.stdout + result.stderr
    runtime = rules(world, "runtime")
    assert not any(rule.startswith("port ") for rule in runtime)  # no open port next to the limit
    assert 'rich rule family="ipv4" source address="192.168.1.0/24" port port="20128" protocol="tcp" accept' in runtime
    assert 'rich rule family="ipv6" source address="fd00::/8" port port="8010" protocol="tcp" accept' in runtime
    assert len(runtime) == 8 and rules(world, "permanent") == runtime
    assert "open to every source" in result.stderr
    assert "--remove" not in calls(world)


def test_firewall_without_usable_sudo_prints_the_commands_and_fails_clearly(tmp_path: Path) -> None:
    world = firewall_world(tmp_path, sudo_ok=False)
    result = firewall(world)
    assert result.returncode == 1
    assert "needs: sudo -n firewall-cmd --add-port=20128/tcp" in result.stdout
    assert "sudo needs a password and this run has no terminal" in result.stderr
    assert rules(world, "runtime") == [] and rules(world, "permanent") == []


def test_firewall_dry_run_only_prints(tmp_path: Path) -> None:
    world = firewall_world(tmp_path, sudo_ok=False)
    result = firewall(world, "--dry-run")
    assert result.returncode == 0
    assert result.stdout.count("would run: sudo -n firewall-cmd") == 8
    assert rules(world, "runtime") == [] and "sudo" not in calls(world)
