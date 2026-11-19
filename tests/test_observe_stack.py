"""Isolated Grafana + VictoriaMetrics observe stack: static config and script lifecycle."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OBSERVE = ROOT / "docker" / "observe"
COMPOSE = OBSERVE / "compose.yml"
DATASOURCE = OBSERVE / "provisioning" / "datasources" / "observe.yml"
DASHBOARD_PROVIDER = OBSERVE / "provisioning" / "dashboards" / "observe.yml"
DASHBOARD = OBSERVE / "dashboards" / "llm-env.json"
SCRIPTS = ROOT / "scripts"

SECRET = "llm-env-observe-grafana-admin"
PROJECT = "llm-env-observe"
VALID_PW = "a" * 48
INVALID_MSG = (
    "observe-stack: credential file is present but invalid (must be 48 hex chars); "
    "restore the original credential or reset the Grafana admin password per README "
    "(never delete the Grafana data volume); aborting before generation or start"
)
MISSING_MSG = (
    "observe-stack: Grafana admin secret exists but the credential file is missing; "
    "restore the original credential or reset the Grafana admin password per README "
    "(never delete the Grafana data volume); aborting before generation or start"
)

# Every family Task 1 declares (pylib/observe_metrics.py).
TASK1_FAMILIES = {
    "llm_env_source_up",
    "llm_env_container_running",
    "llm_env_container_memory_current_mib",
    "llm_env_container_memory_peak_mib",
    "llm_env_container_memory_cap_mib",
    "llm_env_container_memory_swap_mib",
    "llm_env_container_cpu_avg_cores",
    "llm_env_container_memory_psi_full_avg300",
    "llm_env_container_uptime_seconds",
    "llm_env_container_vram_mib",
    "llm_env_container_memory_events_max_total",
    "llm_env_container_oom_kills_total",
    "llm_env_omniroute_active_heavy",
    "llm_env_omniroute_waiting",
    "llm_env_omniroute_chat_sheds_total",
    "llm_env_omniroute_guard_rejects_total",
}


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def _json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _family(expr: str) -> str:
    match = re.fullmatch(r"last_over_time\(([a-z0-9_]+)\[90s\]\)", expr)
    assert match, expr
    return match.group(1)


@pytest.fixture(scope="module")
def compose() -> dict:
    return _yaml(COMPOSE)


# ---- Group 1: Compose structure ----


def test_compose_has_exactly_two_services_with_project_volumes(compose: dict) -> None:
    assert set(compose.get("services", {})) == {"victoriametrics", "grafana"}
    volumes = compose.get("volumes", {})
    assert set(volumes) == {"metrics", "grafana"}
    for volume in volumes.values():
        assert "external" not in volume and "name" not in volume  # for-project, not shared


def test_compose_services_bind_loopback_only(compose: dict) -> None:
    services = compose["services"]
    assert services["victoriametrics"]["ports"] == ["127.0.0.1:8428:8428"]
    assert services["grafana"]["ports"] == ["127.0.0.1:3000:3000"]


def test_victoriametrics_definition(compose: dict) -> None:
    svc = compose["services"]["victoriametrics"]
    assert svc["image"] == (
        "docker.io/victoriametrics/victoria-metrics:v1.156.0@"
        "sha256:da8a90ac0912f16589be2b96a6441a2fca4bca415c83783d972d92e3b5b66970"
    )
    assert svc["command"] == [
        "-storageDataPath=/storage",
        "-retentionPeriod=365d",
        "-storage.minFreeDiskSpaceBytes=100000000000",
        "-search.latencyOffset=0s",
    ]
    assert svc["volumes"] == ["metrics:/storage"]
    assert svc["mem_limit"] == "512m" and svc["cpus"] == 1.0
    assert svc["restart"] == "unless-stopped"


def test_grafana_definition(compose: dict) -> None:
    svc = compose["services"]["grafana"]
    assert svc["image"] == (
        "docker.io/grafana/grafana:13.3.1@"
        "sha256:0615ec4fbd4072004b7d86b46ded63013a74b7d6eb1119273dd89e8fa25c13cf"
    )
    assert svc["mem_limit"] == "512m" and svc["cpus"] == 1.0
    assert svc["restart"] == "unless-stopped"
    env = svc["environment"]
    assert env["GF_SECURITY_ADMIN_PASSWORD__FILE"] == f"/run/secrets/{SECRET}"
    assert env["GF_USERS_ALLOW_SIGN_UP"] == "false"
    assert env["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert env["GF_ANALYTICS_REPORTING_ENABLED"] == "false"


def test_grafana_mounts_exactly_three_provisioning_paths_read_only(compose: dict) -> None:
    volumes = compose["services"]["grafana"]["volumes"]
    assert volumes == [
        "grafana:/var/lib/grafana",
        "./provisioning/datasources:/etc/grafana/provisioning/datasources:ro,z",
        "./provisioning/dashboards:/etc/grafana/provisioning/dashboards:ro,z",
        "./dashboards:/etc/grafana/dashboards:ro,z",
    ]


def test_external_secret_long_syntax_with_quoted_uid_and_mode(compose: dict) -> None:
    assert compose["secrets"] == {SECRET: {"external": True}}
    grafana_secrets = compose["services"]["grafana"]["secrets"]
    assert grafana_secrets == [
        {"source": SECRET, "uid": "472", "mode": "0400", "target": SECRET}
    ]


def test_no_password_interpolation_or_bare_password_key(compose: dict) -> None:
    raw = COMPOSE.read_text()
    assert "${GF_SECURITY_ADMIN_PASSWORD" not in raw
    assert raw.count("GF_SECURITY_ADMIN_PASSWORD__FILE") == 1
    for svc in compose["services"].values():
        env = svc.get("environment", {})
        assert "GF_SECURITY_ADMIN_PASSWORD" not in env
    assert "GF_SECURITY_ADMIN_PASSWORD__FILE" not in " ".join(
        str(v) for v in compose["services"]["victoriametrics"].get("command", [])
    )


def test_no_socket_or_main_state_bind(compose: dict) -> None:
    mounts = [v for svc in compose["services"].values() for v in svc.get("volumes", [])]
    joined = " ".join(mounts)
    for forbidden in ("docker.sock", "/var/lib/containers", "/run/podman", "/run/user"):
        assert forbidden not in joined


# ---- Group 2: provisioning ----


def test_datasource_provisioning() -> None:
    doc = _yaml(DATASOURCE)
    ds = doc["datasources"][0]
    assert ds["uid"] == "llm-env-metrics"
    assert ds["name"] == "llm-env-metrics"
    assert ds["type"] == "prometheus"
    assert ds["access"] == "proxy"
    assert ds["url"] == "http://victoriametrics:8428"
    assert ds["jsonData"]["timeInterval"] == "60s"
    assert "interval" not in ds


def test_dashboard_provider_path_equals_the_dashboard_mount(compose: dict) -> None:
    doc = _yaml(DASHBOARD_PROVIDER)
    assert doc == {
        "apiVersion": 1,
        "providers": [
            {"name": "llm-env-observe", "type": "file", "options": {"path": "/etc/grafana/dashboards"}}
        ],
    }
    mounts = compose["services"]["grafana"]["volumes"]
    target = next(v.split(":")[1] for v in mounts if v.startswith("./dashboards:"))
    assert doc["providers"][0]["options"]["path"] == target


# ---- Group 3: dashboard ----


@pytest.fixture(scope="module")
def dashboard() -> dict:
    return _json(DASHBOARD)


def test_dashboard_identity_range_and_refresh(dashboard: dict) -> None:
    assert dashboard["uid"] == "llm-env-observe"
    assert dashboard["time"] == {"from": "now-6h", "to": "now"}
    assert dashboard["refresh"] == "60s"


def test_dashboard_panels_use_exact_families_gaps_and_datasource(dashboard: dict) -> None:
    panels = dashboard["panels"]
    assert len(panels) >= 7
    used: set[str] = set()
    for panel in panels:
        assert panel["type"] == "timeseries"
        assert panel["fieldConfig"]["defaults"]["unit"]  # explicit unit
        assert "spanNulls" in panel["fieldConfig"]["defaults"].get("custom", {})
        assert panel["fieldConfig"]["defaults"]["custom"]["spanNulls"] is False
        for target in panel["targets"]:
            assert target["datasource"]["uid"] == "llm-env-metrics"
            assert target["spanNulls"] is False
            family = _family(target["expr"])
            assert family in TASK1_FAMILIES  # no invented families
            used.add(family)
            assert "nullPointMode" not in target  # no zero fill
    assert used == {
        "llm_env_source_up",
        "llm_env_container_memory_current_mib",
        "llm_env_container_memory_peak_mib",
        "llm_env_container_memory_cap_mib",
        "llm_env_container_memory_events_max_total",
        "llm_env_container_oom_kills_total",
        "llm_env_container_cpu_avg_cores",
        "llm_env_container_vram_mib",
        "llm_env_omniroute_guard_rejects_total",
        "llm_env_omniroute_chat_sheds_total",
        "llm_env_omniroute_active_heavy",
        "llm_env_omniroute_waiting",
    }


def test_dashboard_panel_titles_and_signal_families(dashboard: dict) -> None:
    def families(fragment: str) -> set[str]:
        matches = [p for p in dashboard["panels"] if fragment in p["title"]]
        assert len(matches) == 1, fragment
        panel = matches[0]
        return {_family(t["expr"]) for t in panel["targets"]}

    assert families("Source status") == {"llm_env_source_up"}
    assert families("current / peak / cap") == {
        "llm_env_container_memory_current_mib",
        "llm_env_container_memory_peak_mib",
        "llm_env_container_memory_cap_mib",
    }
    assert families("OOM kills") == {
        "llm_env_container_memory_events_max_total", "llm_env_container_oom_kills_total"
    }
    assert families("VRAM") == {"llm_env_container_vram_mib"}
    assert families("guard rejects") == {
        "llm_env_omniroute_guard_rejects_total", "llm_env_omniroute_chat_sheds_total"
    }
    assert families("active heavy") == {
        "llm_env_omniroute_active_heavy", "llm_env_omniroute_waiting"
    }
    cpu = next(p for p in dashboard["panels"] if "lifetime average" in p["title"].lower())
    assert families("lifetime average") == {"llm_env_container_cpu_avg_cores"}
    assert "lifetime average" in cpu["title"].lower()
    for fragment in ("OOM kills", "guard rejects"):
        panel = next(p for p in dashboard["panels"] if fragment in p["title"])
        assert "cumulative" in panel["title"].lower()


# ---- Group 4: script stubs ----


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


_JOURNAL = '{ printf "%s" "${0##*/}"; for a in "$@"; do printf " <%s>" "$a"; done; printf "\\n"; } >> "$STUB_CALLS"\n'


def _world(
    tmp_path: Path,
    *,
    pw: str | None = None,
    pw_mode: int = 0o600,
    secret: bool = False,
    curl_rc: int = 0,
    systemctl_rc: int = 0,
    stub_pw: str = VALID_PW,
    openssl_rc: int = 0,
    mv_rc: int = 0,
) -> SimpleNamespace:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stub(bin_dir, "podman", _JOURNAL + '''\
if [ "$1" = secret ] && [ "$2" = inspect ]; then
  [ -e "$STUB_SECRET_MARKER" ] || exit 1
fi
if [ "$1" = secret ] && [ "$2" = create ]; then
  : > "$STUB_SECRET_MARKER"
fi
exit 0
''')
    _stub(bin_dir, "systemctl", _JOURNAL + 'exit "$STUB_SYSTEMCTL_RC"\n')
    _stub(bin_dir, "openssl", _JOURNAL + 'printf "%s\\n" "$STUB_PW"\nexit "$STUB_OPENSSL_RC"\n')
    if mv_rc:
        _stub(bin_dir, "mv", _JOURNAL + 'exit "$STUB_MV_RC"\n')
    _stub(bin_dir, "curl", _JOURNAL + 'exit "$STUB_CURL_RC"\n')
    _stub(bin_dir, "uv", _JOURNAL + "exit 0\n")
    _stub(bin_dir, "timeout", _JOURNAL + '''\
while [ $# -gt 0 ]; do
  case "$1" in --kill-after=*) shift ;; *) break ;; esac
done
shift
exec "$@"
''')

    home = tmp_path / "home"
    state = tmp_path / "state"
    calls = tmp_path / "calls"
    marker = tmp_path / "secret-marker"
    pw_file = state / "llm-env" / "observe" / "grafana-admin-password"
    if pw is not None:
        pw_file.parent.mkdir(parents=True, exist_ok=True)
        pw_file.write_text(pw)
        pw_file.chmod(pw_mode)
    if secret:
        marker.touch()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home),
        "XDG_STATE_HOME": str(state),
        "LLM_ENV_CONFIG": str(tmp_path / "models.yml"),
        "STUB_CALLS": str(calls),
        "STUB_SECRET_MARKER": str(marker),
        "STUB_PW": stub_pw,
        "STUB_CURL_RC": str(curl_rc),
        "STUB_SYSTEMCTL_RC": str(systemctl_rc),
        "STUB_OPENSSL_RC": str(openssl_rc),
        "STUB_MV_RC": str(mv_rc),
    }
    return SimpleNamespace(env=env, calls=calls, marker=marker, pw_file=pw_file,
                           home=home, state=state)


def _run(w: SimpleNamespace, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/bash", str(SCRIPTS / script), *args], cwd=ROOT, env=w.env,
        capture_output=True, text=True, timeout=60, check=False,
    )


def _calls(w: SimpleNamespace) -> str:
    return w.calls.read_text() if w.calls.exists() else ""


def _timer_unit(w: SimpleNamespace, suffix: str) -> str:
    path = w.home / ".config" / "systemd" / "user" / f"{PROJECT}.{suffix}"
    return path.read_text() if path.exists() else ""


def test_both_observe_scripts_source_tools_lib() -> None:
    for name in ("observe-stack.sh", "observe-publish.sh"):
        text = (SCRIPTS / name).read_text()
        assert "tools/lib.sh" in text
        assert "BASH_SOURCE" in text


def test_up_generates_secret_timer_and_secure_credential(tmp_path: Path) -> None:
    w = _world(tmp_path)
    r = _run(w, "observe-stack.sh", "up")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = _calls(w)
    assert f"podman <secret> <create> <--ignore> <{SECRET}> <{w.pw_file}>" in calls
    assert f"podman <compose> <--project-name> <{PROJECT}> <--file> <{ROOT / 'docker/observe/compose.yml'}> <up> <-d>" in calls
    assert "podman <compose>" in calls and "<down>" not in calls
    assert "podman-restart.service" not in calls
    assert "openssl <rand> <-hex> <24>" in calls
    assert w.pw_file.exists() and re.fullmatch(r"[0-9a-f]{48}", w.pw_file.read_text().strip())
    assert stat.S_IMODE(w.pw_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(w.pw_file.parent.stat().st_mode) == 0o700
    assert VALID_PW not in r.stdout + r.stderr + calls  # password never printed or in argv
    service = _timer_unit(w, "service")
    timer = _timer_unit(w, "timer")
    assert "Description=Collect llm-env resource and admission metrics" in service
    assert f"ExecStart=/usr/bin/bash {ROOT / 'scripts/observe-publish.sh'}" in service
    assert "After=network-online.target" in service and "Type=oneshot" in service
    assert "Description=Run the llm-env metrics collector every 60s" in timer
    for line in ("OnActiveSec=5s", "OnUnitActiveSec=60s", "AccuracySec=5s", "Persistent=true"):
        assert line in timer
    assert "OnCalendar" not in timer


def test_up_openssl_failure_leaves_no_temp_credential_or_start(tmp_path: Path) -> None:
    w = _world(tmp_path, openssl_rc=1)
    r = _run(w, "observe-stack.sh", "up")
    assert r.returncode != 0, r.stdout + r.stderr
    calls = _calls(w)
    assert "openssl <rand> <-hex> <24>" in calls
    assert "<create>" not in calls and "<up> <-d>" not in calls
    assert not w.pw_file.exists()  # no credential created
    assert not w.marker.exists()  # no secret created
    assert list(w.pw_file.parent.glob(".grafana-admin-password.*")) == []  # no temp residue


def test_up_mv_failure_leaves_no_temp_credential_or_start(tmp_path: Path) -> None:
    w = _world(tmp_path, mv_rc=1)
    r = _run(w, "observe-stack.sh", "up")
    assert r.returncode != 0, r.stdout + r.stderr
    calls = _calls(w)
    assert "<create>" not in calls and "<up> <-d>" not in calls
    assert not w.pw_file.exists()  # no credential created
    assert not w.marker.exists()  # no secret created
    assert list(w.pw_file.parent.glob(".grafana-admin-password.*")) == []  # no temp residue


def test_up_with_existing_secret_and_valid_file_reuses_both(tmp_path: Path) -> None:
    w = _world(tmp_path, pw=VALID_PW, secret=True)
    assert _run(w, "observe-stack.sh", "up").returncode == 0
    assert _run(w, "observe-stack.sh", "up").returncode == 0
    calls = _calls(w)
    assert "<create>" not in calls and "<secret> <inspect>" in calls
    assert "openssl" not in calls
    assert w.pw_file.read_text() == VALID_PW  # never overwritten
    assert calls.count("<up> <-d>") == 2


def test_up_with_valid_file_but_no_secret_creates_secret_from_file(tmp_path: Path) -> None:
    w = _world(tmp_path, pw=VALID_PW)
    assert _run(w, "observe-stack.sh", "up").returncode == 0
    calls = _calls(w)
    assert f"podman <secret> <create> <--ignore> <{SECRET}> <{w.pw_file}>" in calls
    assert "openssl" not in calls
    assert w.pw_file.read_text() == VALID_PW


def test_up_rejects_invalid_file_without_secret(tmp_path: Path) -> None:
    w = _world(tmp_path, pw="not-a-valid-password")
    r = _run(w, "observe-stack.sh", "up")
    assert r.returncode != 0 and INVALID_MSG in r.stdout + r.stderr
    calls = _calls(w)
    assert "<up>" not in calls and "openssl" not in calls and "<create>" not in calls
    assert w.pw_file.read_text() == "not-a-valid-password"  # unchanged
    assert not w.marker.exists()


def test_up_rejects_invalid_file_even_with_secret(tmp_path: Path) -> None:
    w = _world(tmp_path, pw="not-a-valid-password", secret=True)
    r = _run(w, "observe-stack.sh", "up")
    assert r.returncode != 0 and INVALID_MSG in r.stdout + r.stderr
    calls = _calls(w)
    assert "<up>" not in calls and "openssl" not in calls and "<create>" not in calls
    assert w.pw_file.read_text() == "not-a-valid-password"


def test_up_rejects_missing_file_beside_existing_secret(tmp_path: Path) -> None:
    w = _world(tmp_path, secret=True)
    r = _run(w, "observe-stack.sh", "up")
    assert r.returncode != 0 and MISSING_MSG in r.stdout + r.stderr
    calls = _calls(w)
    assert "<up>" not in calls and "openssl" not in calls and "<create>" not in calls
    assert not w.pw_file.exists()


def test_down_without_credential_stops_compose_and_keeps_secret_and_volumes(tmp_path: Path) -> None:
    w = _world(tmp_path)
    r = _run(w, "observe-stack.sh", "down")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = _calls(w)
    assert "systemctl <--user> <disable> <--now> <llm-env-observe.timer>" in calls
    assert "systemctl <--user> <stop> <llm-env-observe.service>" in calls
    assert "podman <compose>" in calls and "<stop>" in calls
    assert "<down>" not in calls and "<rm>" not in calls and "secret" not in calls
    assert not w.pw_file.exists()  # down never generates a credential


def test_down_still_stops_compose_when_systemctl_fails(tmp_path: Path) -> None:
    w = _world(tmp_path, systemctl_rc=1)
    r = _run(w, "observe-stack.sh", "down")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "<stop>" in _calls(w)


def test_status_without_credential_tolerates_inactive_timer(tmp_path: Path) -> None:
    w = _world(tmp_path, systemctl_rc=3)
    r = _run(w, "observe-stack.sh", "status")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = _calls(w)
    assert "podman <compose>" in calls and "<ps>" in calls
    assert "systemctl <--user> <status> <llm-env-observe.timer>" in calls
    assert "openssl" not in calls and "<create>" not in calls
    assert not w.pw_file.exists()


def test_publish_skips_quietly_when_backend_is_down(tmp_path: Path) -> None:
    w = _world(tmp_path, curl_rc=1)
    r = _run(w, "observe-publish.sh")
    assert r.returncode == 0
    assert r.stdout == "" and r.stderr == ""
    calls = _calls(w)
    assert "curl <--noproxy> <*> <--silent> <--fail> <--max-time> <2> <http://127.0.0.1:8428/health>" in calls
    assert "timeout" not in calls and "uv" not in calls


def test_publish_runs_bounded_collector_when_backend_is_up(tmp_path: Path) -> None:
    w = _world(tmp_path, curl_rc=0)
    r = _run(w, "observe-publish.sh")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout == ""  # health body never printed
    calls = _calls(w)
    assert "curl <--noproxy> <*> <--silent> <--fail> <--max-time> <2> <http://127.0.0.1:8428/health>" in calls
    assert "timeout <--kill-after=5s> <45s>" in calls
    assert (f"uv <run> <--project> <{ROOT}> <{ROOT / 'llmenv.py'}> "
            f"<--config> <{tmp_path / 'models.yml'}> <observe-metrics> <--push>") in calls
