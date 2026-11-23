"""Isolated Grafana + VictoriaMetrics observe stack: static config and script lifecycle."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from itertools import pairwise
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

# Grafana 13.3.1's optional plugin preinstall / analytics update checks drove a
# disposable 1 GiB probe to 1051295744 bytes (757 MB file cache); with these
# disabled the peak stayed at 284037120 bytes (~1.7 MB cache) within the 512 MiB cap.
GRAFANA_QUIET_ENV = {
    "GF_PLUGINS_PREINSTALL_DISABLED": "true",
    "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
    "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES": "false",
}

# Every family Task 1 declares (pylib/observe_metrics.py).
TASK1_FAMILIES = {
    "llm_env_source_up",
    "llm_env_container_running",
    "llm_env_container_memory_current_mib",
    "llm_env_container_memory_peak_mib",
    "llm_env_container_memory_cap_mib",
    "llm_env_container_memory_anon_mib",
    "llm_env_container_memory_file_mib",
    "llm_env_container_memory_swap_mib",
    "llm_env_container_cpu_avg_cores",
    "llm_env_container_cpu_throttled_periods_total",
    "llm_env_container_cpu_throttled_seconds_total",
    "llm_env_container_memory_psi_full_avg300",
    "llm_env_container_uptime_seconds",
    "llm_env_container_vram_mib",
    "llm_env_container_memory_events_max_total",
    "llm_env_container_oom_kills_total",
    "llm_env_omniroute_active_heavy",
    "llm_env_omniroute_waiting",
    "llm_env_omniroute_chat_sheds_total",
    "llm_env_omniroute_guard_rejects_total",
    "llm_env_native_http_requests_total",
    "llm_env_native_http_requests_all_total",
    "llm_env_native_http_request_errors_total",
    "llm_env_native_http_request_duration_seconds_sum",
    "llm_env_native_http_request_duration_seconds_count",
}


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def _json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _family(expr: str) -> str:
    if "llm_env_native_http_requests_all_total" in expr:
        return "llm_env_native_http_requests_all_total"
    if "llm_env_native_http_request_errors_total" in expr:
        return "llm_env_native_http_request_errors_total"
    if "llm_env_native_http_request_duration_seconds_count" in expr:
        return "llm_env_native_http_request_duration_seconds_count"
    if "llm_env_native_http_request_duration_seconds_sum" in expr:
        return "llm_env_native_http_request_duration_seconds_sum"
    if "llm_env_native_http_request_duration_seconds_sum" in expr:
        return "llm_env_native_http_request_duration_seconds_sum"
    patterns = (
        r"last_over_time\((llm_env_[a-z0-9_]+)\[90s\]\)",
        r"last_over_time\((llm_env_[a-z0-9_]+)\[3m\]\)",
        r"last_over_time\((llm_env_[a-z0-9_]+)\[3m\]\) and on\(service\) \(last_over_time\(llm_env_container_running\[3m\]\) == 1\)",
        r"\(last_over_time\((llm_env_[a-z0-9_]+)\[3m\]\) - last_over_time\(llm_env_container_memory_peak_mib\[3m\]\)\) and on\(service\) \(last_over_time\(llm_env_container_running\[3m\]\) == 1\)",
        r"60 \* sum by \(service\) \(rate\((llm_env_[a-z0-9_]+)\[5m\]\)\)",
        r"quantile_over_time\(0\.95, (llm_env_[a-z0-9_]+)\[6h\]\)",
        r"last_over_time\((llm_env_[a-z0-9_]+)\[90s\]\) - last_over_time\(llm_env_container_memory_peak_mib\[90s\]\)",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, expr)
        if match:
            return match.group(1)
    fs_match = re.search(r"(llm_env_fs_[a-z0-9_]+)", expr)
    if fs_match:
        return fs_match.group(1)
    native_match = re.search(r"(llm_env_native_http_[a-z0-9_]+)", expr)
    if native_match:
        return native_match.group(1)
    remote_match = re.search(r"(llm_env_remote_setup_[a-z0-9_]+)", expr)
    if remote_match:
        return remote_match.group(1)
    raise AssertionError(f"unexpected query shape: {expr}")


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
    assert svc["mem_limit"] == "128m" and svc["cpus"] == 1.0
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


def test_grafana_disables_background_plugin_installs_and_update_checks(compose: dict) -> None:
    env = compose["services"]["grafana"]["environment"]
    for key, value in GRAFANA_QUIET_ENV.items():
        assert env.get(key) == value  # string value, not a YAML bool
    vm_env = compose["services"]["victoriametrics"].get("environment", {})
    for key in GRAFANA_QUIET_ENV:
        assert key not in vm_env  # Grafana-only, never on VictoriaMetrics


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
        if panel["type"] == "table":
            assert panel["id"] == 10 and panel["title"] == "Container inventory"
            assert panel["datasource"]["uid"] == "llm-env-metrics"
            assert {target["refId"] for target in panel["targets"]} == {"A", "B", "C", "D", "E"}
            assert any(t["id"] == "merge" for t in panel["transformations"])
            assert panel["fieldConfig"]["overrides"]
            for target in panel["targets"]:
                assert target["datasource"]["uid"] == "llm-env-metrics"
                family = _family(target["expr"])
                assert family in TASK1_FAMILIES or family in {
                    "llm_env_container_uptime_seconds"
                }
                used.add(family)
            continue
        if panel["type"] == "row":
            continue
        if panel["type"] == "stat":
            assert panel["datasource"]["uid"] == "llm-env-metrics"
            assert panel["targets"][0]["datasource"]["uid"] == "llm-env-metrics"
            assert set(re.findall(r"llm_env_[a-z0-9_]+", panel["targets"][0]["expr"])) <= {"llm_env_fs_store_bytes", "llm_env_fs_used_ratio", "llm_env_remote_setup_requests_total"}
            if "llm_env_fs_store_bytes" in panel["targets"][0]["expr"]:
                used.add("llm_env_fs_store_bytes")
            continue
        if panel["type"] == "text":
            assert panel["id"] == 26 and panel["datasource"]["uid"] == "llm-env-metrics"
            assert panel["targets"] == [] and panel["options"]["content"]
            continue
        if panel["type"] == "bargauge":
            assert panel["title"] == "Largest repo tmp entries"
            used.add("llm_env_fs_repo_tmp_top_bytes")
            continue
        assert panel["type"] in {"timeseries", "state-timeline"}
        assert panel["fieldConfig"]["defaults"]["unit"]  # explicit unit
        if panel["type"] == "timeseries":
            assert "spanNulls" in panel["fieldConfig"]["defaults"].get("custom", {})
            assert panel["fieldConfig"]["defaults"]["custom"]["spanNulls"] is False
        legend = panel["options"]["legend"]
        if panel["type"] == "state-timeline":
            assert legend["displayMode"] in {"list", "table"}
        else:
            assert legend["displayMode"] == "table" and legend["placement"] == "right"
        assert {"lastNotNull", "median", "max"} <= set(legend["calcs"])
        assert legend["sortBy"] == "Max" and legend["sortDesc"] is True
        for target in panel["targets"]:
            assert target["datasource"]["uid"] == "llm-env-metrics"
            assert target["spanNulls"] is False
            family = _family(target["expr"])
            assert family in TASK1_FAMILIES or family.startswith(("llm_env_fs_", "llm_env_remote_setup_", "llm_env_native_http_"))  # fixed families of the fs, remote-setup and native HTTP sources
            used.add(family)
            if panel["id"] == 49:
                used.add("llm_env_native_http_request_duration_seconds_sum")
            assert "nullPointMode" not in target  # no zero fill
    assert used == {
        "llm_env_source_up",
        "llm_env_container_memory_current_mib",
        "llm_env_container_memory_peak_mib",
        "llm_env_container_memory_cap_mib",
        "llm_env_container_memory_anon_mib",
        "llm_env_container_memory_file_mib",
        "llm_env_container_memory_events_max_total",
        "llm_env_container_oom_kills_total",
        "llm_env_container_cpu_avg_cores",
        "llm_env_container_cpu_throttled_seconds_total",
        "llm_env_container_vram_mib",
        "llm_env_omniroute_guard_rejects_total",
        "llm_env_omniroute_chat_sheds_total",
        "llm_env_omniroute_active_heavy",
        "llm_env_omniroute_waiting",
        "llm_env_container_running",
        "llm_env_container_uptime_seconds",
        "llm_env_container_memory_swap_mib",
        "llm_env_container_memory_psi_full_avg300",
        "llm_env_fs_used_ratio", "llm_env_fs_bytes", "llm_env_fs_repo_tmp_top_bytes",
        "llm_env_fs_store_bytes", "llm_env_fs_store_unreadable_entries",
        "llm_env_fs_scan_age_seconds", "llm_env_fs_scan_truncated",
        "llm_env_remote_setup_requests_total", "llm_env_remote_setup_request_duration_ms_sum",
        "llm_env_native_http_requests_total", "llm_env_native_http_requests_all_total",
        "llm_env_native_http_request_errors_total", "llm_env_native_http_request_duration_seconds_sum",
        "llm_env_native_http_request_duration_seconds_count",
    }
    inventory = next(p for p in panels if p["id"] == 10)
    names = inventory["transformations"][-1]["options"]["renameByName"]
    assert names["service"] == "Service"
    overrides = {o["matcher"]["options"]: o["properties"] for o in inventory["fieldConfig"]["overrides"]}
    assert overrides["Uptime"][0] == {"id": "unit", "value": "s"}
    assert any(o["matcher"].get("options") == "Memory current|Memory cap|Headroom" and {"id": "unit", "value": "mbytes"} in o["properties"] for o in inventory["fieldConfig"]["overrides"])
    assert any(o["matcher"].get("options") == "State" and "Stopped" in str(o) and "Running" in str(o) and "red" in str(o) and "green" in str(o) for o in inventory["fieldConfig"]["overrides"])


def test_family_rejects_unexpected_expression_shape() -> None:
    with pytest.raises(AssertionError):
        _family("max(llm_env_container_memory_swap_mib[90s])")


def test_laya_dashboard_uses_only_emitted_families_with_descriptions() -> None:
    from pylib.observe_jev import HELP as JEV_HELP
    from pylib.observe_usage import _HELP as USAGE_HELP

    dashboard = _json(OBSERVE / "dashboards" / "laya.json")
    assert dashboard["uid"] == "llm-env-laya"
    assert dashboard["title"] == "Laya: requests, question types, confidence"
    assert dashboard["time"] == {"from": "now-7d", "to": "now"}
    assert dashboard["refresh"] == "60s"
    ids = [panel["id"] for panel in dashboard["panels"]]
    assert len(ids) == len(set(ids))
    assert any(panel["id"] == 1 and panel["title"] == "Jev log source up" and
               panel["targets"][0]["expr"] == 'min(llm_env_source_up{source="jev_log"})'
               for panel in dashboard["panels"])
    assert {"uid": dashboard["uid"], "title": dashboard["title"], "time": dashboard["time"],
            "refresh": dashboard["refresh"]} == {
                "uid": "llm-env-laya", "title": "Laya: requests, question types, confidence",
                "time": {"from": "now-7d", "to": "now"}, "refresh": "60s"}
    emitted = set(JEV_HELP) | set(USAGE_HELP) | {"llm_env_jev_top_confidence_bucket", "llm_env_jev_top_confidence_sum",
            "llm_env_jev_top_confidence_count", "llm_env_laya_duration_ms_bucket", "llm_env_laya_duration_ms_count",
            "llm_env_laya_confidence_bucket", "llm_env_laya_confidence_count", "llm_env_source_up",
            "llm_env_laya_request_duration_ms", "llm_env_laya_request_questions",
            "llm_env_laya_request_confidence_mean", "llm_env_laya_request_answers"}
    allowed_labels = {"backend": {"laya", "typesafe"}, "type": {"noul", "choice", "score"},
                      "status": {"2xx", "3xx", "4xx", "5xx"},
                      "endpoint": {"/v1/systemone", "/v1/systemone/batch"}}
    for panel in dashboard["panels"]:
        assert panel.get("description", "").strip(), panel["title"]
        for target in panel.get("targets", []):
            expr = target["expr"]
            names = set(re.findall(r"\bllm_env_[a-z0-9_]+", expr))
            assert names <= emitted, (panel["title"], names - emitted)
            for label, values in allowed_labels.items():
                for value in re.findall(rf'{label}="([^"]+)"', expr):
                    assert value in values
            if "histogram_quantile" in expr:
                assert "increase(" in expr and "[$__range]" in expr and (" > 0)" in expr or " >= 20)" in expr)
                assert not re.search(r"rate\([^)]*\[[0-9]+[smhd]", expr)
    about = next(p for p in dashboard["panels"] if p["type"] == "text")
    content = about["options"]["content"]
    assert "1,082" in content and "1,542" in content and "not stored" in content



def test_laya_decision_shares_are_split_by_backend() -> None:
    dashboard = _json(OBSERVE / "dashboards" / "laya.json")
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    for backend in ("laya", "typesafe"):
        panel = panels[f"Decision share by type: {backend}"]
        assert panel["fieldConfig"]["defaults"]["custom"]["stacking"]["mode"] == "percent"
        expressions = [target["expr"] for target in panel["targets"]]
        assert len(expressions) == 1
        assert expressions[0].count(f'backend="{backend}"') == 3
        assert 'and on (backend) (sum by (backend)' in expressions[0]
        assert panel["targets"][0]["legendFormat"] == "{{type}}"
        assert panel["options"]["legend"]["sortBy"] == "Max"
        assert panel["options"]["legend"]["calcs"] == ["lastNotNull", "median", "max"]
        assert "no data" in panel["description"].lower()
    assert {backend for backend in ("laya", "typesafe")
            if f'backend="{backend}"' in next(p for p in panels.values() if p["title"] == f"Decision share by type: {backend}")["targets"][0]["expr"]} == {"laya", "typesafe"}


def test_filesystem_store_growth_and_budget_panels() -> None:
    dashboard = _json(DASHBOARD)
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    budget = panels["VictoriaMetrics store vs 2 GB budget"]
    assert budget["targets"][0]["expr"] == 'llm_env_fs_store_bytes{store="victoriametrics"} / 2000000000'
    assert "VictoriaMetrics has no size-cap flag" in budget["description"]
    defaults = budget["fieldConfig"]["defaults"]
    assert defaults["unit"] == "percentunit"
    assert defaults["thresholds"]["steps"] == [
        {"color": "green", "value": None}, {"color": "orange", "value": 0.7}, {"color": "red", "value": 0.9}]
    assert budget["options"]["colorMode"] in {"value", "background", "background_solid"}
    growth = panels["Store growth per day"]
    assert growth["targets"][0]["expr"] == "deriv(llm_env_fs_store_bytes[$__range]) * 86400"
    assert "2026-11-21" in growth["description"] and "day" in growth["description"].lower()
    assert growth["targets"][0]["legendFormat"] == "{{store}}"
    assert growth["options"]["legend"]["sortBy"] == "Max"
    for mount in ("var_home", "tmp"):
        panel = panels[f"Days until /{'var/home' if mount == 'var_home' else 'tmp'} reaches 85 % used"]
        expr = panel["targets"][0]["expr"]
        assert "deriv(" in expr and f'mount="{mount}"' in expr
        assert "and on()" in expr and "deriv(" in expr and "clamp_max" in expr
        assert panel["fieldConfig"]["defaults"]["unit"] == "d"
        assert "No data" in panel["fieldConfig"]["defaults"]["noValue"]  # the key Grafana reads
        assert "No data" in panel["options"]["noValue"]
        assert "clamp_min(" in expr and "0 days" in panel["description"]
        assert "linear projection" in panel["description"].lower() and "short history" in panel["description"].lower()
        assert panel["fieldConfig"]["defaults"]["thresholds"]["steps"] == [
            {"color": "red", "value": None}, {"color": "orange", "value": 14}, {"color": "green", "value": 60}]


def test_laya_dashboard_has_request_table_and_duration_panel() -> None:
    dashboard = _json(OBSERVE / "dashboards" / "laya.json")
    row = next(p for p in dashboard["panels"] if p["type"] == "row" and p["title"] == "REQUESTS (per request)")
    table = next(p for p in dashboard["panels"] if p.get("title") == "Notable Laya requests (errors, batches, slow, truncated)")
    timeline = next(p for p in dashboard["panels"] if p.get("title") == "Laya request duration (notable requests)")
    assert row["gridPos"]["y"] == 120
    assert table["type"] == "table" and timeline["type"] == "timeseries"
    assert all(target["datasource"]["uid"] == "llm-env-metrics" for panel in (table, timeline) for target in panel["targets"])
    assert all(target.get("instant") and target.get("format") == "table" for target in table["targets"])
    assert [t["id"] for t in table["transformations"]] == ["merge", "organize"]  # one row per request: labels are identical across the queries
    renames = table["transformations"][1]["options"]["renameByName"]
    assert {renames[k] for k in ("Value #A", "Value #B", "Value #C")} == {"Duration (ms)", "Questions", "Mean confidence"}
    assert "ts" in renames and "Time" in table["transformations"][1]["options"]["excludeByName"]  # no collision with the native Time column
    assert [t["refId"] for t in table["targets"]] == ["A", "B", "C"]
    assert "Plain successes are counted in the per-hour panels" in table["description"]
    assert "at most 100" in table["description"] and "not the time picker" in table["description"]
    assert "endpoint=" not in timeline["targets"][0]["expr"]  # batch requests are notable too: no endpoint filter
    assert "spanNulls" in timeline["fieldConfig"]["defaults"]["custom"]


def test_laya_dashboard_has_all_requested_sections_and_histogram_semantics() -> None:
    dashboard = _json(OBSERVE / "dashboards" / "laya.json")
    titles = {p["title"] for p in dashboard["panels"]}
    assert {"Jev log source up", "Laya file source up", "Jev state source up", "Requests per hour by endpoint and status class", "Request duration p50 / p90 / p99", "Answers by type", "Decisions per hour by backend",
            "Laya confidence quantiles by type (p50 / p90)", "Abstention rate by backend and type",
            "Top caller abstention share by backend"} <= titles
    quantiles = next(p for p in dashboard["panels"] if "quantiles by type" in p["title"])
    quantile_targets = [t for t in quantiles["targets"] if "histogram_quantile" in t["expr"]]
    sample_targets = [t for t in quantiles["targets"] if "top_confidence_count" in t["expr"] and "histogram_quantile" not in t["expr"]]
    assert len(quantile_targets) == 6 and len(sample_targets) == 3
    assert all("_bucket" in t["expr"] and 'le' in t["expr"] for t in quantile_targets)
    assert all("increase(" in t["expr"] and "[$__range]" in t["expr"] for t in quantiles["targets"])
    assert all("and on(type)" in t["expr"] for t in quantile_targets)


def test_laya_sparse_statistics_use_selected_range_and_count_guards() -> None:
    dashboard = _json(OBSERVE / "dashboards" / "laya.json")
    for panel in dashboard["panels"]:
        title = panel["title"].lower()
        if (any(word in title for word in ("quantile", "confidence", "rate", "share")) or
                any(token in target["expr"] for target in panel.get("targets", [])
                    for token in ("histogram_quantile", "_sum", "_count"))):
            for target in panel.get("targets", []):
                expr = target["expr"]
                if "histogram_quantile" in expr or "top_confidence_sum" in expr or "/" in expr:
                    windows = re.findall(r"(?:rate|increase)\([^\]]*\[([^]]+)\]\)", expr)
                    assert windows and all(window == "$__range" for window in windows), (panel["title"], windows, expr)
                    assert " > 0)" in expr or " >= 20)" in expr, (panel["title"], expr)
    duration = next(p for p in dashboard["panels"] if p["title"] == "Request duration p50 / p90 / p99")
    assert all("[$__range]" in target["expr"] for target in duration["targets"])
    p50 = next(p for p in dashboard["panels"] if "p50 confidence comparison" in p["title"].lower())
    assert ">= 20" in p50["targets"][0]["expr"]


def test_laya_shorter_than_range_windows_are_documented() -> None:
    dashboard = _json(OBSERVE / "dashboards" / "laya.json")
    short_window_exceptions = {6, 7, 8}  # Jev history panels retain their established 15m rates.
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            expr = target["expr"]
            if re.search(r"(?:rate|increase)\([^)]*\[(?:[0-9]+[smhd])", expr):
                assert panel["id"] in short_window_exceptions, (panel["title"], expr)
                assert "15m" in expr, (panel["title"], expr)


def test_laya_panels_do_not_overlap_and_belong_to_one_ordered_section() -> None:
    panels = _json(OBSERVE / "dashboards" / "laya.json")["panels"]
    rows = sorted((p for p in panels if p["type"] == "row"), key=lambda p: p["gridPos"]["y"])
    assert len(rows) == 7
    rectangles = [(p, p["gridPos"]) for p in panels]
    for index, (left, a) in enumerate(rectangles):
        for right, b in rectangles[index + 1:]:
            assert (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"] or
                    a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"]), (left["id"], right["id"])
    tops = {g["y"] for _, g in rectangles}
    bottoms = {g["y"] + g["h"] for _, g in rectangles}
    for panel, g in rectangles:  # no empty bands: at most the one-row spacing before a row header
        assert g["y"] == 0 or any(g["y"] - 1 <= bottom <= g["y"] for bottom in bottoms), ("gap above", panel["id"])
        assert g["y"] + g["h"] == max(bottoms) or any(g["y"] + g["h"] <= top <= g["y"] + g["h"] + 1 for top in tops), ("gap below", panel["id"])
    for panel in panels:
        if panel["type"] == "row":
            continue
        preceding = [row for row in rows if row["gridPos"]["y"] < panel["gridPos"]["y"]]
        following = [row for row in rows if row["gridPos"]["y"] >= panel["gridPos"]["y"]]
        assert preceding and panel["gridPos"]["y"] + panel["gridPos"]["h"] <= (following[0]["gridPos"]["y"] if following else float("inf")), panel["title"]


def test_laya_count_targets_use_count_units_and_stat_panels_have_thresholds() -> None:
    dashboard = _json(OBSERVE / "dashboards" / "laya.json")
    neutral_panels = {}  # No stat-like panel needs neutral thresholds.
    for panel in dashboard["panels"]:
        if panel["type"] in {"stat", "bargauge", "gauge"}:
            steps = panel.get("fieldConfig", {}).get("defaults", {}).get("thresholds", {}).get("steps", [])
            assert panel["id"] in neutral_panels or len(steps) >= 2, panel["title"]
            if panel["id"] not in neutral_panels:
                assert steps[0].get("value") is None, panel["title"]
        if "answered share" in (panel.get("title", "") + " " + panel.get("description", "")).lower():
            steps = panel.get("fieldConfig", {}).get("defaults", {}).get("thresholds", {}).get("steps", [])
            assert not any(step.get("value") in {0.1, 0.25} and step.get("color") in {"yellow", "red"} for step in steps), panel["title"]
    freshness = next(p for p in dashboard["panels"] if p["id"] == 3)
    assert "idle periods are normal" in freshness["description"].lower()
    assert freshness["fieldConfig"]["defaults"]["thresholds"]["steps"] == [
        {"color": "green", "value": None}, {"color": "yellow", "value": 600}, {"color": "red", "value": 3600}
    ]
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            if target["refId"] in {"G", "H", "I"} and "top_confidence_count" in target["expr"]:
                ref_overrides = [o for o in panel["fieldConfig"].get("overrides", [])
                                 if o.get("matcher", {}).get("options") == target["refId"]]
                assert any({"id": "unit", "value": "short"} in o.get("properties", []) for o in ref_overrides), target["refId"]
    expressions = [target["expr"] for panel in dashboard["panels"] for target in panel.get("targets", [])]
    assert len(expressions) == len(set(expressions)), "duplicate target expressions"
    err = next(p for p in dashboard["panels"] if p["id"] == 15)
    assert "or on (backend) (0 * sum by (backend) (increase(llm_env_jev_decisions_total[$__range])))" in err["targets"][0]["expr"]


@pytest.mark.skipif(os.environ.get("LLM_ENV_LIVE_VM") != "1", reason="requires opt-in live VictoriaMetrics")
def test_laya_dashboard_targets_return_data_from_live_victoriametrics() -> None:
    import time
    import urllib.parse
    import urllib.request

    dashboard = _json(OBSERVE / "dashboards" / "laya.json")
    empty_by_design = {
        (10, "E", "7d"): "Laya score has no observations in the last 7 days",
        (10, "F", "7d"): "Laya score has no observations in the last 7 days",
        (10, "C", "30m"): "No Laya noul confidence observations in the last 30 minutes",
        (10, "D", "30m"): "No Laya noul confidence observations in the last 30 minutes",
        (10, "E", "30m"): "Laya score has no observations in the last 30 minutes",
        (10, "F", "30m"): "Laya score has no observations in the last 30 minutes",
        (11, "A", "30m"): "No confidence series reaches the 20-sample display threshold",
        (14, "A", "30m"): "No type reaches the 20-answered-decision display threshold",
        (18, "A", "30m"): "No confidence series reaches the 20-sample display threshold",
        (20, "A", "30m"): "No caller reaches the 20-answered-decision display threshold",
    }
    now = time.time()
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            for label, duration, step in (("7d", 604800, 3600), ("30m", 1800, 60), ("30d", 2592000, 3600)):
                expr = target["expr"].replace("$__range", f"{duration}s").replace("$__interval", f"{step}s")
                if target.get("instant"):
                    path = "/api/v1/query?" + urllib.parse.urlencode({"query": expr, "time": now})
                else:
                    path = "/api/v1/query_range?" + urllib.parse.urlencode({
                        "query": expr, "start": now - duration, "end": now, "step": step,
                    })
                with urllib.request.urlopen("http://127.0.0.1:8428" + path, timeout=30) as response:
                    body = json.load(response)
                assert body["status"] == "success", (panel["id"], target["refId"], label)
                result = body["data"]["result"]
                allowed_reason = empty_by_design.get((panel["id"], target["refId"], label))
                assert result or allowed_reason, (panel["id"], target["refId"], label, expr)
                assert not allowed_reason or not result, (panel["id"], target["refId"], label, allowed_reason)
                assert all(float(value) == float(value) for series in result
                           for value in ([series["value"][1]] if "value" in series else
                                         [point[1] for point in series.get("values", [])]))


def test_usage_gap_queries_require_omniroute_usage_source() -> None:
    dashboard = _json(OBSERVE / "dashboards" / "omniroute.json")
    for title in ("Usage gaps (1h)", "Skipped usage rows (1h)"):
        panel = next(p for p in dashboard["panels"] if p["title"] == title)
        assert 'llm_env_source_up{source="omniroute_usage"}' in panel["targets"][0]["expr"]


def test_collector_health_row_exists() -> None:
    dashboard = _json(OBSERVE / "dashboards" / "omniroute.json")
    assert any(p["type"] == "row" and p["title"] == "Collector health" for p in dashboard["panels"])


def test_filesystem_thresholds_are_visible_and_descriptions_are_accurate(dashboard: dict) -> None:
    for panel in dashboard["panels"]:
        config = panel.get("fieldConfig", {})
        defaults = config.get("defaults", {})
        if panel.get("type") == "stat" and defaults.get("unit") == "percentunit" and defaults.get("thresholds", {}).get("steps"):
            assert panel.get("options", {}).get("colorMode") not in {None, "none"}, panel["title"]
        defaults = config.get("defaults", {})
        default_steps = list(defaults.get("thresholds", {}).get("steps", []))
        # A missing thresholdsStyle means "off": the lines are only drawn when a mode is set explicitly.
        default_style = defaults.get("custom", {}).get("thresholdsStyle", {}).get("mode", "off")
        if panel.get("type") == "timeseries" and any(step.get("value") is not None for step in default_steps):
            assert default_style != "off", panel["title"]
        for override in config.get("overrides", []):
            props = {prop["id"]: prop["value"] for prop in override.get("properties", [])}
            override_steps = props.get("thresholds", {}).get("steps", [])
            if panel.get("type") == "timeseries" and any(step.get("value") is not None for step in override_steps):
                style = props.get("custom.thresholdsStyle", {}).get("mode", default_style)
                assert style != "off", (panel["title"], override["matcher"])
        if "2 GB" in panel.get("description", ""):
            steps = default_steps + [step for override in config.get("overrides", [])
                                     for prop in override.get("properties", []) if prop.get("id") == "thresholds"
                                     for step in prop.get("value", {}).get("steps", [])]
            assert (any(step.get("value") == 2_000_000_000 for step in steps) or
                    (panel["type"] == "stat" and panel["targets"][0]["expr"].endswith("/ 2000000000") and
                     any(step.get("value") == 0.9 for step in steps))), panel["title"]


def test_remote_setup_dashboard_panels_and_source_status(dashboard: dict) -> None:
    panels = dashboard["panels"]
    row = next(p for p in panels if p.get("type") == "row" and p["title"] == "Remote setup (installer)")
    group_ids = {41, 42, 43, 44, 45, 46, 47, 48, 49}
    assert row["gridPos"]["y"] == max(p["gridPos"]["y"] + p["gridPos"]["h"]
                                      for p in panels if p["id"] not in group_ids)
    source_query = next(p for p in panels if p["title"] == "Source status (up)")["targets"][0]["expr"]
    assert source_query == "last_over_time(llm_env_source_up[90s])"  # unfiltered: new sources appear automatically
    by_title = {p["title"]: p for p in panels}
    count = by_title["remote-setup requests (current run)"]
    assert count["targets"][0]["expr"] == 'sum(llm_env_remote_setup_requests_total{route!="healthcheck"})'  # the 5 s health probe is not traffic
    assert count["options"]["noValue"] == "No data: remote-setup has not published counters (restart it onto the new code)"
    rate = by_title["remote-setup request rate by class"]
    assert rate["targets"][0]["expr"] == 'sum by (class) (rate(llm_env_remote_setup_requests_total{route!="healthcheck"}[5m])) * 60'
    latency = by_title["remote-setup mean latency (ms)"]["targets"][0]["expr"]
    assert "and on (route)" in latency and "> 0" in latency
    assert latency.count('route!="healthcheck"') == 3  # sum, count and the guard all exclude the probe
    errors = by_title["remote-setup 5xx and error (current run)"]
    assert errors["targets"][0]["expr"] == 'sum(llm_env_remote_setup_requests_total{class=~"5xx|error"})'
    assert errors["fieldConfig"]["defaults"]["thresholds"]["steps"][-1] == {"color": "red", "value": 1}
    for panel in (count, rate, by_title["remote-setup mean latency (ms)"], errors):
        assert "after a container restart the previous run's numbers stay until the first new request" in panel["description"]
        assert "Routes are only /setup.sh, /config, other and healthcheck" in panel["description"]
        assert all(t["datasource"]["uid"] == "llm-env-metrics" for t in panel["targets"])


def test_native_http_panels_have_expected_queries_and_final_layout(dashboard: dict) -> None:
    panels = dashboard["panels"]
    row = next(p for p in panels if p.get("title") == "HTTP services (native counters)")
    assert row["type"] == "row"
    requests = next(p for p in panels if p.get("title") == "HTTP requests per minute by service")
    assert requests["targets"][0]["expr"] == "sum by (service) (rate(llm_env_native_http_requests_total[5m]) or rate(llm_env_native_http_requests_all_total[5m])) * 60"
    layout = next(p for p in panels if p.get("title") == "Mean Grafana request latency (ms)")
    assert "and on (service)" in layout["targets"][0]["expr"] and "> 0)" in layout["targets"][0]["expr"]
    assert "only times its own /metrics endpoint" in layout["description"]  # why VictoriaMetrics has no latency
    errors = next(p for p in panels if p.get("title") == "HTTP errors per minute by service")
    assert all("* 60" in t["expr"] for t in errors["targets"])  # same unit on every line: per minute
    assert all("the collector's scrape of each /metrics endpoint" in p["description"] and "aggregated across all paths" in p["description"] and "start at zero" in p["description"] for p in panels if p.get("id") in {47, 48, 49})
    assert not any("health check" in p["description"] for p in panels if p.get("id") in {47, 48, 49})  # compose has none for these services
    assert row["gridPos"]["y"] <= max(p["gridPos"]["y"] + p["gridPos"]["h"] for p in panels if p.get("id") not in {46, 47, 48, 49}) + 1
    assert min(p["gridPos"]["y"] for p in panels if p.get("id") in {47, 48, 49}) == row["gridPos"]["y"] + 1


def test_dashboard_panel_titles_and_signal_families(dashboard: dict) -> None:
    def families(fragment: str) -> set[str]:
        matches = [p for p in dashboard["panels"] if fragment in p["title"]]
        assert len(matches) == 1, fragment
        panel = matches[0]
        return {_family(t["expr"]) for t in panel["targets"]}

    assert families("Source status") == {"llm_env_source_up"}
    filesystem = next(p for p in dashboard["panels"] if p["title"] == "Filesystem used ratio")
    assert filesystem["fieldConfig"]["defaults"]["custom"]["thresholdsStyle"]["mode"] == "line+area"
    assert any(p.get("type") == "row" and p["title"] == "Filesystem" for p in dashboard["panels"])
    assert filesystem["fieldConfig"]["defaults"]["thresholds"]["steps"][-2:] == [
        {"color": "orange", "value": 0.7}, {"color": "red", "value": 0.85}
    ]
    assert next(p for p in dashboard["panels"] if p["title"] == "Largest repo tmp entries")["type"] == "bargauge"
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


def test_inventory_units_and_state_mappings() -> None:
    dashboard = _json(OBSERVE / "dashboards" / "llm-env.json")
    inventory = next(p for p in dashboard["panels"] if p["id"] == 10)
    first = min(dashboard["panels"], key=lambda p: p["gridPos"]["y"])
    assert first["id"] == 2
    assert first["title"] == "Container memory: current / peak / cap (MiB)"
    assert next(p for p in dashboard["panels"] if p["title"] == "Source status (up)")["type"] == "state-timeline"
    assert next(p for p in dashboard["panels"] if p["id"] == 1)["description"]
    assert next(p for p in dashboard["panels"] if p["id"] == 2)["description"]
    overrides = inventory["fieldConfig"]["overrides"]
    assert any(o["matcher"].get("options") == "Uptime" and {"id": "unit", "value": "s"} in o["properties"] for o in overrides)
    assert any(o["matcher"].get("options") == "Memory current|Memory cap|Headroom" and {"id": "unit", "value": "mbytes"} in o["properties"] for o in overrides)
    state = next(o for o in overrides if o["matcher"].get("options") == "State")
    assert "Running" in str(state) and "Stopped" in str(state) and "green" in str(state) and "red" in str(state)
    assert inventory["transformations"][-1]["options"]["renameByName"]["service"] == "Service"


def test_llama_dashboard_container_label_is_emitted():
    from pylib.observe_llama import EMITTED_FAMILIES
    from pylib.resources import LIVE_CONTAINERS

    dashboard = _json(OBSERVE / "dashboards" / "llama.json")
    expressions = [target["expr"] for panel in dashboard["panels"] for target in panel.get("targets", [])]
    assert LIVE_CONTAINERS["llm_server"] == "llm-server"
    assert all('service="llm_server"' in expr for expr in expressions
               if "llm_env_container_" in expr)
    mixed = next(p for p in dashboard["panels"] if p["id"] == 9)
    assert {o["matcher"]["options"] for o in mixed["fieldConfig"]["overrides"]} >= {"A", "B", "C"}
    assert all(o["properties"][0]["value"] in {"left", "right"} for o in mixed["fieldConfig"]["overrides"])
    emitted = EMITTED_FAMILIES
    assert all(family in emitted for family in (
        "llm_env_container_cpu_avg_cores", "llm_env_container_memory_current_mib",
        "llm_env_container_memory_peak_mib"))


OVERLAP_MITIGATIONS = {
    ("llm-env-observe", 1): "state-timeline",
    ("llm-env-observe", 2): "log-cap",
    ("llm-env-observe", 8): "symlog",
    ("llm-env-observe", 11): "symlog",
    ("llm-env-observe", 12): "symlog",
    ("llm-env-llama", 5): "second-axis",
    ("llm-env-llama", 8): "second-axis",
    ("llm-env-llama", 9): "second-axis",
    ("llm-env-omniroute", 8): "symlog",
}


def test_overlap_mitigations_are_present() -> None:
    dashboard_dir = OBSERVE / "dashboards"
    by_uid = {"llm-env-observe": "llm-env.json", "llm-env-llama": "llama.json",
              "llm-env-omniroute": "omniroute.json", "llm-env-laya": "laya.json"}
    for (uid, panel_id), mitigation in OVERLAP_MITIGATIONS.items():
        panel = next(p for p in _json(dashboard_dir / by_uid[uid])["panels"] if p["id"] == panel_id)
        custom = panel.get("fieldConfig", {}).get("defaults", {}).get("custom", {})
        if mitigation in {"log", "symlog", "log-cap"}:
            expected_scale = "log" if mitigation == "log-cap" else mitigation
            scale = custom.get("scaleDistribution", {})
            assert scale.get("type") == expected_scale, (uid, panel_id)
            if expected_scale == "log":
                assert scale.get("log") == 10
            else:
                assert scale.get("log") == 10 and scale.get("linearThreshold") == 1
            if mitigation == "log-cap":
                cap = next(o for o in panel["fieldConfig"]["overrides"] if o["matcher"]["options"] == "C")
                props = {prop["id"]: prop["value"] for prop in cap["properties"]}
                assert props["custom.lineStyle"] == {"fill": "dash", "dash": [10, 10]}
                assert props["color"]["mode"] == "fixed"
            if mitigation == "symlog":
                assert scale.get("linearThreshold") == 1
        elif mitigation == "second-axis":
            assert panel["type"] == "timeseries"
            expected = {5: "B", 8: "E"}.get(panel_id)
            if panel_id == 9:
                assert {o["matcher"]["options"]: o["properties"][0]["value"]
                        for o in panel["fieldConfig"]["overrides"]} == {"A": "left", "B": "right", "C": "right"}
            else:
                override = next(o for o in panel["fieldConfig"]["overrides"]
                                if o["matcher"].get("options") == expected)
                props = {prop["id"]: prop["value"] for prop in override["properties"]}
                assert props["custom.axisPlacement"] == "right"
                assert props["custom.axisSoftMin"] == 0 and props["custom.axisSoftMax"] > 0
                assert props["unit"]
        elif mitigation == "percent-stack":
            assert custom.get("stacking", {}).get("mode") == "percent"
        else:
            assert panel["type"] == mitigation
            defaults = panel["fieldConfig"]["defaults"]
            mappings = {str(value): spec for entry in defaults["mappings"] for value, spec in entry["options"].items()}
            assert {"0", "1"} <= mappings.keys()
            assert mappings["0"]["text"] == "down" and mappings["1"]["text"] == "up"
            assert mappings["0"]["color"] == "red" and mappings["1"]["color"] == "green"
            steps = defaults["thresholds"]["steps"]
            assert steps[0]["color"] == "red" and {s.get("value") for s in steps} >= {None, 1}


def test_overlap_panel_descriptions_restore_caveats() -> None:
    dashboards = {name: _json(OBSERVE / "dashboards" / name) for name in
                  ("llm-env.json", "llama.json", "omniroute.json", "laya.json")}
    for name, panel_id in (("llm-env.json", 1), ("llm-env.json", 2), ("llm-env.json", 8),
                           ("llm-env.json", 11), ("llm-env.json", 12), ("llama.json", 5),
                           ("llama.json", 8), ("llama.json", 9), ("omniroute.json", 8),
                           ):
        panel = next(p for p in dashboards[name]["panels"] if p["id"] == panel_id)
        assert panel.get("description", "").strip(), (name, panel_id)
    assert "treat dependent readings as stale" in next(p for p in dashboards["llm-env.json"]["panels"] if p["id"] == 1)["description"]
    assert "not llama.cpp VRAM metrics" in next(p for p in dashboards["llama.json"]["panels"] if p["id"] == 9)["description"]
    for name, panel_id, word in (("llm-env.json", 2, "logarithmic"), ("llm-env.json", 8, "symmetric log"),
                                 ("llm-env.json", 11, "symmetric log"), ("llm-env.json", 12, "symmetric log"),
                                 ("llama.json", 5, "separate right axis"), ("llama.json", 8, "separate right axis"),
                                 ("omniroute.json", 8, "symmetric log")):
        assert word in next(p for p in dashboards[name]["panels"] if p["id"] == panel_id)["description"].lower()


def test_descriptions_claim_only_what_the_panel_does() -> None:
    """A description that names a display (log scale, stacking, right axis) must match the panel's real settings."""
    import re

    for name in ("llm-env.json", "llama.json", "omniroute.json", "laya.json"):
        for panel in _json(OBSERVE / "dashboards" / name)["panels"]:
            if panel.get("type") != "timeseries":
                continue
            text = panel.get("description", "").lower()
            custom = panel.get("fieldConfig", {}).get("defaults", {}).get("custom", {})
            scale = custom.get("scaleDistribution", {}).get("type", "linear")
            stacking = custom.get("stacking", {}).get("mode", "none")
            where = (name, panel["id"], panel.get("title"))
            if "symmetric log" in text:
                assert scale == "symlog", where
            elif "logarithmic" in text:
                assert scale == "log", where
            if re.search(r"(?<!not )stacked (?:to|by|as)\b", text):
                assert stacking != "none", where
            if "separate right axis" in text:
                placements = [prop["value"] for override in panel.get("fieldConfig", {}).get("overrides", [])
                              for prop in override["properties"] if prop["id"] == "custom.axisPlacement"]
                assert "right" in placements, where


def test_llama_memory_axis_is_right_and_legend_labels_are_grouped() -> None:
    for name in ("llm-env.json", "laya.json", "llama.json", "omniroute.json"):
        for panel in _json(OBSERVE / "dashboards" / name)["panels"]:
            for target in panel.get("targets", []):
                expr = target.get("expr", "")
                clauses = re.findall(r"\bby\s*\(([^)]*)\)", expr)
                if not clauses:
                    continue
                labels = {label.strip() for clause in clauses for label in clause.split(",")}
                used = set(re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", target.get("legendFormat", "")))
                assert used <= labels, (name, panel.get("id"), target.get("refId"), used - labels)
    llama = _json(OBSERVE / "dashboards" / "llama.json")
    memory_panel = next(p for p in llama["panels"] if p["id"] == 9)
    sides = {o["matcher"]["options"]: o["properties"][0]["value"] for o in memory_panel["fieldConfig"]["overrides"]}
    assert sides["B"] == sides["C"] == "right"


def test_all_dashboard_timeseries_legends_and_text_panels() -> None:
    dashboard_dir = OBSERVE / "dashboards"
    for path in dashboard_dir.glob("*.json"):
        panels = _json(path)
        for panel in panels["panels"]:
            if panel["type"] == "timeseries":
                legend = panel["options"]["legend"]
                assert legend["displayMode"] == "table" and legend["placement"] == "right"
                assert {"lastNotNull", "median", "max"} <= set(legend["calcs"])
                assert legend["sortBy"] == "Max" and legend["sortDesc"] is True
            if panel["type"] in {"timeseries", "barchart"}:
                legend = panel["options"]["legend"]
                assert {"lastNotNull", "median", "max"} <= set(legend["calcs"])
                assert legend["sortBy"] == "Max" and legend["sortDesc"] is True
            if panel["type"] == "text":
                assert panel.get("datasource") is None or panel["datasource"]["uid"] == "llm-env-metrics"
                assert panel.get("targets", []) == []
                assert panel.get("options", {}).get("content")


def test_all_dashboard_panel_legends_and_grid_layout() -> None:
    dashboard_dir = OBSERVE / "dashboards"
    chart_types = {"timeseries", "barchart", "bargauge", "piechart", "state-timeline"}
    for path in dashboard_dir.glob("*.json"):
        panels = _json(path)["panels"]
        ids = [panel["id"] for panel in panels]
        assert len(ids) == len(set(ids)), path.name
        for panel in panels:
            if panel["type"] == "row":
                assert panel["gridPos"]["x"] == 0 and panel["gridPos"]["w"] == 24
            if panel["type"] in chart_types:
                for target in panel.get("targets", []):
                    legend = target.get("legendFormat", "")
                    assert legend.strip(), (path.name, panel["title"], target["refId"])
                    assert "{{ __name__ }}" not in legend
                    grouped = re.findall(r"sum by\s*\(([^)]*)\)", target["expr"])
                    for clause in grouped:
                        for label in (name.strip() for name in clause.split(",")):
                            if label and label != "le" and not (panel["title"].startswith("Decision share by type:") and label == "backend"):
                                assert f"{{{{{label}}}}}" in legend, (panel["title"], label, legend)
            if panel["type"] in {"table", "stat"}:
                targets = panel.get("targets", [])
                multi_series = len(targets) > 1 or any(
                    any(label.strip() != "le" for clause in re.findall(r"\bby\s*\(([^)]*)\)", t.get("expr", ""))
                        for label in clause.split(",")) for t in targets
                )
                if panel["type"] == "table" and all(t.get("format") == "table" for t in targets):
                    multi_series = False
                reduced_to_one = (panel["type"] == "stat" and
                                  all(target.get("instant") for target in targets) and
                                  bool(panel.get("options", {}).get("reduceOptions", {}).get("calcs")))
                if multi_series and not reduced_to_one:
                    for target in targets:
                        assert target.get("legendFormat", "").strip(), (path.name, panel["title"])
        bands: dict[int, list[dict]] = {}
        for panel in panels:
            if panel["type"] != "row":
                bands.setdefault(panel["gridPos"]["y"], []).append(panel)
        for y, band in bands.items():
            positions = sorted((p["gridPos"]["x"], p["gridPos"]["w"], p["gridPos"]["h"]) for p in band)
            assert len({h for _, _, h in positions}) == 1, (path.name, y, positions)
            x = 0
            for start, width, _ in positions:
                assert start == x, (path.name, y, positions)
                x += width
            assert x == 24, (path.name, y, positions)
        rectangles = [(p, p["gridPos"]) for p in panels]
        fs_bands = sorted({(p["gridPos"]["y"], p["gridPos"]["h"]) for p in panels
                           if p.get("title", "").startswith("Filesystem") or p.get("title") in {"Largest repo tmp entries", "Source status (up)", "VictoriaMetrics store vs 2 GB budget", "Store growth per day", "Days until /var/home reaches 85 % used", "Days until /tmp reaches 85 % used"}})
        for (prior_y, prior_h), (next_y, _) in pairwise(fs_bands):
            assert next_y == prior_y + prior_h
        for index, (left, a) in enumerate(rectangles):
            for right, b in rectangles[index + 1:]:
                assert (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"] or
                        a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"]), (left["id"], right["id"])


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


# ---- Boot unit (docs/superpowers/plans/2026-11-23-tracked-host-profile.md) ----

BOOT_UNIT = "llm-env-observe-stack.service"


def _boot_unit(w: SimpleNamespace) -> str:
    path = w.home / ".config" / "systemd" / "user" / BOOT_UNIT
    return path.read_text() if path.exists() else ""


def test_install_units_enables_a_generated_boot_unit_without_starting_anything(tmp_path: Path) -> None:
    w = _world(tmp_path)
    r = _run(w, "observe-stack.sh", "install-units")
    assert r.returncode == 0, r.stdout + r.stderr
    unit = _boot_unit(w)
    assert f'ExecStart=/usr/bin/bash "{ROOT / "scripts/observe-stack.sh"}" start' in unit
    for line in ("Type=oneshot", "RemainAfterExit=yes", "[Install]", "WantedBy=default.target",
                 "Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin"):
        assert line in unit
    unit_path = w.home / ".config" / "systemd" / "user" / BOOT_UNIT
    assert stat.S_IMODE(unit_path.stat().st_mode) == 0o600
    calls = _calls(w)
    assert f"systemctl <--user> <enable> <{BOOT_UNIT}>" in calls  # enabled for boot, never --now
    assert f"<--now> <{BOOT_UNIT}>" not in calls
    assert "podman" not in calls and "podman-restart.service" not in calls  # no container is touched
    assert "<enable> <--now> <llm-env-observe.timer>" in calls


def test_boot_unit_parses_with_systemd_analyze(tmp_path: Path) -> None:
    analyze = shutil.which("systemd-analyze")
    if analyze is None:
        pytest.skip("systemd-analyze not installed")
    w = _world(tmp_path)
    assert _run(w, "observe-stack.sh", "install-units").returncode == 0
    unit = w.home / ".config" / "systemd" / "user" / BOOT_UNIT
    r = subprocess.run([analyze, "verify", str(unit)], capture_output=True, text=True, timeout=60, check=False,
                       env={**os.environ, "SYSTEMD_LOG_LEVEL": "warning"})
    assert r.returncode == 0, r.stdout + r.stderr


def test_start_brings_existing_containers_up_without_recreating(tmp_path: Path) -> None:
    w = _world(tmp_path, pw=VALID_PW, secret=True)
    r = _run(w, "observe-stack.sh", "start")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = _calls(w)
    assert "<up> <-d> <--no-recreate>" in calls
    assert "systemctl" not in calls  # the boot entry never writes or enables units


def test_up_installs_the_boot_unit_and_down_disables_it(tmp_path: Path) -> None:
    w = _world(tmp_path, pw=VALID_PW, secret=True)
    assert _run(w, "observe-stack.sh", "up").returncode == 0
    assert "WantedBy=default.target" in _boot_unit(w)
    assert _run(w, "observe-stack.sh", "down").returncode == 0
    assert f"systemctl <--user> <disable> <{BOOT_UNIT}>" in _calls(w)
