"""Host Prometheus metrics exposition: renderer, health fetch, collector, publisher and CLI."""

import json
import math
import subprocess
import sys
import threading
import time
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _running_row(**overrides):
    """A read_live-shaped running row with every flat key present."""
    row = {
        "state": "running",
        "uptime_s": 100,
        "current_mib": 512,
        "peak_mib": 768,
        "cap_mib": 1024,
        "swap_mib": 0,
        "cpu_avg_cores": 0.25,
        "psi_full_avg300": 0.0,
        "vram_mib": 1234,
        "events": {"high": 0, "max": 2, "oom": 0, "oom_kill": 3},
    }
    row.update(overrides)
    return row


# ---- Group 1: renderer ----

def test_render_emits_running_and_stopped_and_skips_unknown():
    from pylib.observe_metrics import render_metrics

    live = {
        "omniroute": _running_row(),
        "remote_setup": {"state": "not running"},
        # system_one unknown -> filtered out by the collector, absent from live
    }
    text = render_metrics(live, None)

    assert 'llm_env_container_running{service="omniroute"} 1' in text
    assert 'llm_env_container_running{service="remote_setup"} 0' in text
    assert 'llm_env_container_memory_current_mib{service="omniroute"} 512' in text
    # confirmed-stopped row emits only running=0
    assert 'llm_env_container_memory_current_mib{service="remote_setup"}' not in text
    assert 'llm_env_container_oom_kills_total{service="remote_setup"}' not in text
    # unknown row absent
    assert 'service="system_one"' not in text
    assert 'service="phoenix"' not in text
    assert 'llm_env_source_up{source="containers"} 1' in text
    assert 'llm_env_source_up{source="omniroute"} 0' in text


def test_render_reads_flat_psi_key_and_ignores_other_nested_mappings():
    from pylib.observe_metrics import render_metrics

    row = _running_row()
    del row["psi_full_avg300"]
    row["psi"] = {"full_avg300": 99.0}  # wrong shape must not be traversed
    text = render_metrics({"omniroute": row}, None)

    assert "llm_env_container_memory_psi_full_avg300" not in text
    assert 'llm_env_container_memory_events_max_total{service="omniroute"} 2' in text
    assert 'llm_env_container_oom_kills_total{service="omniroute"} 3' in text


def test_render_omits_bad_values_but_keeps_real_zero():
    from pylib.observe_metrics import render_metrics

    row = _running_row(
        current_mib=0,
        peak_mib=None,
        cap_mib=True,            # bool is not a number
        swap_mib=math.nan,       # nonfinite
        cpu_avg_cores="0.5",     # non-number
        vram_mib=math.inf,       # nonfinite
        events={"max": 0, "oom_kill": False, "high": 4},
    )
    del row["psi_full_avg300"]   # missing
    text = render_metrics({"omniroute": row}, None)

    assert 'llm_env_container_memory_current_mib{service="omniroute"} 0' in text
    assert 'llm_env_container_memory_events_max_total{service="omniroute"} 0' in text
    for name in (
        "llm_env_container_memory_peak_mib",
        "llm_env_container_memory_cap_mib",
        "llm_env_container_memory_swap_mib",
        "llm_env_container_cpu_avg_cores",
        "llm_env_container_memory_psi_full_avg300",
        "llm_env_container_vram_mib",
        "llm_env_container_oom_kills_total",
    ):
        assert f'{name}{{service="omniroute"}}' not in text


def test_render_labels_are_fixed_and_health_uses_omniroute_service():
    from pylib.observe_metrics import render_metrics

    health = {
        "chatAdmission": {"activeHeavy": 3, "waiting": 2, "shedTotal": 9},
        "adaptiveAdmission": {"pressureGuardRejectCount": 5},
    }
    text = render_metrics({"omniroute": _running_row()}, health)

    assert 'llm_env_omniroute_active_heavy{service="omniroute"} 3' in text
    assert 'llm_env_omniroute_waiting{service="omniroute"} 2' in text
    assert 'llm_env_omniroute_chat_sheds_total{service="omniroute"} 9' in text
    assert 'llm_env_omniroute_guard_rejects_total{service="omniroute"} 5' in text
    assert 'llm_env_source_up{source="containers"} 1' in text
    assert 'llm_env_source_up{source="omniroute"} 1' in text
    assert "llm_env_source_up{service=" not in text


def test_render_marks_containers_down_when_incomplete_but_keeps_health():
    from pylib.observe_metrics import render_metrics

    health = {"chatAdmission": {"activeHeavy": 1}}
    text = render_metrics({"omniroute": _running_row()}, health, containers_incomplete=True)

    assert 'llm_env_source_up{source="containers"} 0' in text
    assert 'llm_env_source_up{source="omniroute"} 1' in text
    assert 'llm_env_container_running{service="omniroute"} 1' in text
    assert 'llm_env_omniroute_active_heavy{service="omniroute"} 1' in text


def test_render_with_none_live_is_still_valid_exposition():
    from pylib.observe_metrics import render_metrics

    text = render_metrics(None, None)

    assert 'llm_env_source_up{source="containers"} 0' in text
    assert 'llm_env_source_up{source="omniroute"} 0' in text
    assert "llm_env_container_" not in text
    assert "# TYPE llm_env_omniroute_active_heavy" not in text
    assert text.endswith("\n")


def test_render_has_type_per_present_family_and_lifetime_help():
    from pylib.observe_metrics import render_metrics

    text = render_metrics({"omniroute": _running_row()}, None)
    families = (
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
    )
    for name in families:
        assert f"# TYPE {name} " in text

    help_lines = [line for line in text.splitlines()
                  if line.startswith("# HELP llm_env_container_cpu_avg_cores")]
    assert help_lines
    assert "lifetime average" in help_lines[0].lower()


# ---- Group 2: health fetch ----

def _health_body(ts_offset_ms=-1000, **changes):
    doc = {
        "timestamp": int(time.time() * 1000) + ts_offset_ms,
        "chatAdmission": {"activeHeavy": 3, "waiting": 2, "shedTotal": 9},
        "adaptiveAdmission": {"pressureGuardRejectCount": 5},
    }
    doc.update(changes)
    return json.dumps(doc).encode()


def test_read_health_primes_then_reads_and_maps_four_fields(monkeypatch, capsys):
    import pylib.observe_metrics as om
    from pylib.omniroute_activity import PRIME_GAP_S

    calls = []
    sleeps = []
    monkeypatch.setattr(om, "bounded_login", lambda base, pw, deadline: (calls.append(("login", base)), "TOKEN")[1])
    bodies = iter([b"primed-discarded", _health_body()])
    monkeypatch.setattr(om, "bounded_get", lambda base, path, token, deadline: (calls.append((path, token)), next(bodies))[1])

    doc = om.read_omniroute_health(9123, "SUPERSECRET-PW", clock=lambda: 100.0, sleep=sleeps.append)

    assert calls[0] == ("login", "http://127.0.0.1:9123")
    assert [c[0] for c in calls[1:]] == ["/api/monitoring/health", "/api/monitoring/health"]
    assert all(c[1] == "TOKEN" for c in calls[1:])
    assert sleeps == [PRIME_GAP_S]
    text = om.render_metrics(None, doc)
    assert 'llm_env_omniroute_active_heavy{service="omniroute"} 3' in text
    assert 'llm_env_omniroute_waiting{service="omniroute"} 2' in text
    assert 'llm_env_omniroute_chat_sheds_total{service="omniroute"} 9' in text
    assert 'llm_env_omniroute_guard_rejects_total{service="omniroute"} 5' in text
    captured = capsys.readouterr()
    assert "SUPERSECRET-PW" not in captured.out + captured.err
    assert "TOKEN" not in captured.out + captured.err


def test_read_health_stale_after_retry_returns_none(monkeypatch):
    import pylib.observe_metrics as om
    from pylib.omniroute_activity import PRIME_GAP_S

    monkeypatch.setattr(om, "bounded_login", lambda base, pw, deadline: "TOKEN")
    gets = []
    stale = _health_body(ts_offset_ms=-100000)
    monkeypatch.setattr(om, "bounded_get",
                        lambda base, path, token, deadline: (gets.append(path), stale)[1])
    sleeps = []

    assert om.read_omniroute_health(1, "pw", clock=lambda: 100.0, sleep=sleeps.append) is None
    assert len(gets) == 3  # prime + second + one retry
    assert sleeps == [PRIME_GAP_S, 1]


def test_read_health_recovers_on_one_retry(monkeypatch):
    import pylib.observe_metrics as om
    from pylib.omniroute_activity import PRIME_GAP_S

    monkeypatch.setattr(om, "bounded_login", lambda base, pw, deadline: "TOKEN")
    bodies = iter([b"primed", _health_body(ts_offset_ms=-100000), _health_body(ts_offset_ms=-1000)])
    monkeypatch.setattr(om, "bounded_get", lambda base, path, token, deadline: next(bodies))
    sleeps = []

    doc = om.read_omniroute_health(1, "pw", clock=lambda: 100.0, sleep=sleeps.append)
    assert doc is not None
    assert doc["chatAdmission"]["activeHeavy"] == 3
    assert sleeps == [PRIME_GAP_S, 1]


@pytest.mark.parametrize("body", [
    {"timestamp_bogus": 1, "chatAdmission": {"activeHeavy": 1}},
    {"timestamp": 0, "chatAdmission": {"waiting": "two"}},
    {"timestamp": 0, "chatAdmission": {"shedTotal": True}, "adaptiveAdmission": {"pressureGuardRejectCount": None}},
])
def test_read_health_all_fields_missing_or_nonnumeric_returns_none(monkeypatch, body):
    import pylib.observe_metrics as om

    monkeypatch.setattr(om, "bounded_login", lambda base, pw, deadline: "TOKEN")
    monkeypatch.setattr(om, "bounded_get", lambda base, path, token, deadline: json.dumps(body).encode())
    assert om.read_omniroute_health(1, "pw", clock=lambda: 100.0, sleep=lambda s: None) is None


@pytest.mark.parametrize("stamp", [
    "not-a-timestamp",
    None,
    "2030-01-01T00:00:00+02:00",  # non-UTC RFC 3339 is not accepted
])
def test_read_health_missing_or_unparseable_timestamp_returns_none(monkeypatch, stamp):
    import pylib.observe_metrics as om

    monkeypatch.setattr(om, "bounded_login", lambda base, pw, deadline: "TOKEN")
    body = {"timestamp": stamp, "chatAdmission": {"activeHeavy": 1}}
    monkeypatch.setattr(om, "bounded_get", lambda base, path, token, deadline: json.dumps(body).encode())
    assert om.read_omniroute_health(1, "pw", clock=lambda: 100.0, sleep=lambda s: None) is None


def test_read_health_deferral_is_secret_free(monkeypatch, capsys):
    import pylib.observe_metrics as om
    from pylib.omniroute_activity import Deferral, Reason

    def login(base, pw, deadline):
        raise Deferral(Reason.auth, "password=SUPERSECRET-PW")

    monkeypatch.setattr(om, "bounded_login", login)
    assert om.read_omniroute_health(1, "SUPERSECRET-PW", clock=lambda: 100.0, sleep=lambda s: None) is None
    captured = capsys.readouterr()
    assert "SUPERSECRET-PW" not in captured.out + captured.err


# ---- Group 3: collector ----

def _install_collector(monkeypatch, *, states, proc_text=None, health=None, live_impl=None):
    """Stub only the slow/external levels: podman, cgroup reads, live_resources and health."""
    import pylib.observe_metrics as om
    from pylib.resources import LIVE_CONTAINERS

    calls = {"run": [], "found": {}}

    def fake_run(argv, **kwargs):
        calls["run"].append((argv, kwargs))
        value = states.get(argv[-1], "false 0 0\n")
        if isinstance(value, BaseException):
            raise value
        return SimpleNamespace(stdout=value)

    monkeypatch.setattr(om, "subprocess", SimpleNamespace(
        run=fake_run,
        CalledProcessError=subprocess.CalledProcessError,
        TimeoutExpired=subprocess.TimeoutExpired,
    ))

    def default_live(limits, pci, *, locate, now):
        rows = {}
        for svc, container in LIVE_CONTAINERS.items():
            found = locate(container)
            calls["found"][container] = found
            if found is None:
                rows[svc] = {"state": "not running"}
            else:
                rows[svc] = {"state": "running", "current_mib": 7, "uptime_s": 10}
        return rows

    monkeypatch.setattr(om, "live_resources", live_impl or default_live)
    monkeypatch.setattr(om, "read_omniroute_health", lambda *a, **k: health)

    if proc_text is not None:
        class FakeProcPath:
            def __init__(self, path):
                self.path = path

            def read_text(self):
                if self.path not in proc_text:
                    raise OSError("no such process")
                return proc_text[self.path]

        monkeypatch.setattr(om, "Path", FakeProcPath)
    return calls


def test_collect_locator_true_runs_with_type_container_and_cgroup(monkeypatch):
    import pylib.observe_metrics as om

    proc = {"/proc/4242/cgroup": "12:freezer:/foo\n0::/machine.slice/libpod-abc.scope\n"}
    calls = _install_collector(monkeypatch, states={"omniroute": "true 4242 1700000000\n"}, proc_text=proc)

    text = om.collect_metrics({}, None, port=1, password="pw")

    argv, kwargs = calls["run"][0]
    assert argv == [
        "podman", "inspect", "--type", "container", "-f",
        "{{.State.Running}} {{.State.Pid}} {{.State.StartedAt.Unix}}", "omniroute",
    ]
    assert kwargs == {"capture_output": True, "text": True, "check": True, "timeout": 5}
    found = calls["found"]["omniroute"]
    assert found[1:] == (4242, 1700000000.0)
    assert found[0] == om.CGROUP_ROOT / "machine.slice/libpod-abc.scope"
    assert 'llm_env_container_running{service="omniroute"} 1' in text
    assert 'llm_env_source_up{source="containers"} 1' in text


def test_collect_locator_false_is_confirmed_stopped_not_incomplete(monkeypatch):
    import pylib.observe_metrics as om

    _install_collector(monkeypatch, states={"omniroute": "false 0 0\n"})

    text = om.collect_metrics({}, None, port=1, password="pw")

    assert 'llm_env_container_running{service="omniroute"} 0' in text
    assert 'llm_env_source_up{source="containers"} 1' in text  # not incomplete
    assert 'llm_env_container_memory_current_mib{service="omniroute"}' not in text


@pytest.mark.parametrize("value,proc_text", [
    ("", None),
    ("true 4242", None),                 # too few tokens
    ("TRUE 4242 1700", None),            # wrong running token
    ("true 4242 1700 extra", None),      # extra tokens
    (subprocess.TimeoutExpired("podman", 5), None),
    ("true 4242 1700", {}),              # cgroup read failure
])
def test_collect_unknown_rows_filtered_while_peer_emits(monkeypatch, value, proc_text):
    import pylib.observe_metrics as om

    _install_collector(monkeypatch, states={"omniroute": value}, proc_text=proc_text)

    text = om.collect_metrics({}, None, port=1, password="pw")

    assert 'llm_env_container_running{service="omniroute"}' not in text
    assert 'llm_env_source_up{source="containers"} 0' in text
    assert 'llm_env_container_running{service="remote_setup"} 0' in text  # peer still complete


@pytest.mark.parametrize("hung", ["containers", "omniroute"])
def test_collect_ignores_hung_worker_and_keeps_peer(monkeypatch, hung):
    import pylib.observe_metrics as om

    release = threading.Event()
    health = {"chatAdmission": {"activeHeavy": 9}}
    live_impl = None
    if hung == "containers":
        def blocked_live(limits, pci, *, locate, now):
            release.wait(10)
            return {"omniroute": {"state": "running", "current_mib": 424242}}
        live_impl = blocked_live

    _install_collector(monkeypatch, states={}, health=health, live_impl=live_impl)
    if hung == "omniroute":
        def blocked_health(*a, **k):
            release.wait(10)
            return health
        monkeypatch.setattr(om, "read_omniroute_health", blocked_health)

    try:
        text = om.collect_metrics({}, None, port=1, password="pw", deadline_s=0.05)
    finally:
        release.set()

    if hung == "containers":
        assert 'llm_env_source_up{source="containers"} 0' in text
        assert 'llm_env_source_up{source="omniroute"} 1' in text
        assert 'llm_env_omniroute_active_heavy{service="omniroute"} 9' in text
        assert "424242" not in text  # hung snapshot ignored even if it later returns
        assert 'llm_env_container_running{service="omniroute"}' not in text
    else:
        assert 'llm_env_source_up{source="containers"} 1' in text
        assert 'llm_env_source_up{source="omniroute"} 0' in text
        assert 'llm_env_container_running{service="omniroute"} 0' in text  # peer still complete
        assert 'llm_env_omniroute_active_heavy' not in text


# ---- Group 4: publisher and CLI ----

class _FakeConn:
    def __init__(self, status, body=b""):
        self.status, self.body = status, body
        self.closed = False
        self.request_args = self.request_kwargs = None

    def request(self, *args, **kwargs):
        self.request_args, self.request_kwargs = args, kwargs

    def getresponse(self):
        return SimpleNamespace(status=self.status, read=lambda: self.body)

    def close(self):
        self.closed = True


def _install_http(monkeypatch, conn):
    import pylib.observe_metrics as om

    built = {}

    def make_conn(*args, **kwargs):
        built["args"], built["kwargs"] = args, kwargs
        return conn

    monkeypatch.setattr(om.http.client, "HTTPConnection", make_conn)
    return built


def test_publish_posts_fixed_payload_and_accepts_204(monkeypatch, capsys):
    import pylib.observe_metrics as om

    conn = _FakeConn(204)
    built = _install_http(monkeypatch, conn)
    text = 'llm_env_source_up{source="containers"} 0\n'

    om.publish_metrics(text)

    assert built == {"args": ("127.0.0.1", 8428), "kwargs": {"timeout": 5}}
    assert conn.request_args == ("POST", "/api/v1/import/prometheus")
    assert conn.request_kwargs == {
        "body": text,
        "headers": {"Content-Type": "text/plain; version=0.0.4"},
    }
    assert conn.closed
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("status,accepted", [(200, True), (204, True), (299, True), (300, False), (500, False)])
def test_publish_accepts_only_2xx(monkeypatch, status, accepted):
    import pylib.observe_metrics as om

    conn = _FakeConn(status)
    _install_http(monkeypatch, conn)
    if accepted:
        om.publish_metrics("payload\n")
    else:
        with pytest.raises(OSError):
            om.publish_metrics("payload\n")
    assert conn.closed


def test_publish_never_logs_response_body(monkeypatch, capsys):
    import pylib.observe_metrics as om

    conn = _FakeConn(500, body=b"SENSITIVE BODY")
    _install_http(monkeypatch, conn)
    with pytest.raises(OSError):
        om.publish_metrics("payload\n")
    captured = capsys.readouterr()
    assert "SENSITIVE BODY" not in captured.out + captured.err


def _cfg():
    return {
        "gpu": {"pci_address": "0000:03:00.0"},
        "omniroute": {"port": 8080, "initial_password": "SUPERSECRET"},
    }


def _install_cli(monkeypatch, *, fail_at=None, collect_text=None):
    import llmenv

    seen = {}
    cfg = _cfg()
    monkeypatch.setattr(llmenv, "load_config", lambda p: (seen.__setitem__("config_path", str(p)), cfg)[1])
    monkeypatch.setattr(llmenv, "require_valid_config", lambda c: c)
    monkeypatch.setattr(llmenv, "host_resources", lambda: {"cpu_count": 1, "memory_total_mib": 1024})
    monkeypatch.setattr(llmenv, "limits_for", lambda c, h: {"limits": True})

    if fail_at == "collect":
        def failing_collect(*a, **k):
            raise ValueError("password=SUPERSECRET")
        collect_stub = failing_collect
    else:
        def ok_collect(limits, pci, *, port, password):
            seen.update(limits=limits, pci=pci, port=port, password=password)
            return collect_text or 'llm_env_source_up{source="containers"} 0\nllm_env_source_up{source="omniroute"} 0\n'
        collect_stub = ok_collect
    monkeypatch.setattr(llmenv, "collect_metrics", collect_stub)

    if fail_at == "publish":
        def failing_publish(text):
            raise ValueError("password=SUPERSECRET")
        publish_stub = failing_publish
    else:
        def ok_publish(text):
            seen["published"] = text
        publish_stub = ok_publish
    monkeypatch.setattr(llmenv, "publish_metrics", publish_stub)
    return seen


def test_cli_default_prints_status_only_exposition_and_exits_zero(monkeypatch, capsys):
    import llmenv

    seen = _install_cli(monkeypatch)
    assert llmenv.cmd_observe_metrics(cast(Namespace, SimpleNamespace(config="cfg.yml", push=False))) == 0
    out = capsys.readouterr()
    assert out.out == 'llm_env_source_up{source="containers"} 0\nllm_env_source_up{source="omniroute"} 0\n'
    assert out.err == ""
    assert "published" not in seen
    assert seen == {
        "config_path": "cfg.yml", "limits": {"limits": True}, "pci": "0000:03:00.0",
        "port": 8080, "password": "SUPERSECRET",
    }


def test_cli_push_publishes_same_text_silently(monkeypatch, capsys):
    import llmenv

    seen = _install_cli(monkeypatch)
    assert llmenv.cmd_observe_metrics(cast(Namespace, SimpleNamespace(config="cfg.yml", push=True))) == 0
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
    assert seen["published"] == 'llm_env_source_up{source="containers"} 0\nllm_env_source_up{source="omniroute"} 0\n'


@pytest.mark.parametrize("push,fail_at", [
    (False, "load"), (False, "collect"),
    (True, "load"), (True, "collect"), (True, "publish"),
])
def test_cli_errors_are_sanitized_in_both_modes(monkeypatch, capsys, push, fail_at):
    import llmenv

    _install_cli(monkeypatch, fail_at=fail_at)
    if fail_at == "load":
        def bad_load(path):
            raise ValueError("password=SUPERSECRET")
        monkeypatch.setattr(llmenv, "load_config", bad_load)

    assert llmenv.cmd_observe_metrics(cast(Namespace, SimpleNamespace(config="cfg.yml", push=push))) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.strip() == "observe-metrics: collection or publish failed"
    assert "SUPERSECRET" not in out.out + out.err


def test_cli_parser_registers_push_and_keeps_top_level_config(monkeypatch):
    import llmenv

    seen = {}
    monkeypatch.setattr(
        llmenv, "cmd_observe_metrics",
        lambda args: seen.update(push=args.push, config=args.config) or 0,
    )
    assert llmenv.main(["--config", "top.yml", "observe-metrics", "--push"]) == 0
    assert seen == {"push": True, "config": "top.yml"}


def test_cli_argparse_usage_error_is_not_wrapped(capsys):
    import llmenv

    with pytest.raises(SystemExit) as exc:
        llmenv.main(["observe-metrics", "--bogus"])
    assert exc.value.code == 2
