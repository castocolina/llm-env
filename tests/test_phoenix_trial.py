"""Phoenix trial: isolated server configuration tests (Task 1).

Pure configuration tests: no engine, port or network probes.
"""

import copy
import json
import os
import re
import stat
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

COMPOSE_PATH = REPO / "docker" / "phoenix-trial" / "compose.yml"
README_PATH = REPO / "docker" / "phoenix-trial" / "README.md"

PHOENIX_PIN = (
    "docker.io/arizephoenix/phoenix:20.23.0"
    "@sha256:c542e7de1dc1fdd0f938de337d44ebefa7266e91391eed6e7978523d7f6e1f2f"
)


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_compose_has_only_loopback_http_port(compose: dict) -> None:
    service = compose["services"]["phoenix"]
    assert service["ports"] == ["127.0.0.1:${PHOENIX_TRIAL_PORT:?}:6006"]
    assert service["volumes"] == ["data:/mnt/data"]
    assert compose["networks"]["trial"]["internal"] is True
    assert compose["volumes"]["data"]["external"] is True
    assert set(compose["services"]) == {"phoenix", "sender"}
    assert "healthcheck" not in service
    sender = compose["services"]["sender"]
    assert sender["profiles"] == ["tools"]
    assert "ports" not in sender and "volumes" not in sender
    assert sender["environment"] == {"UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never"}
    assert sender["networks"] == ["trial"]


def test_trial_has_no_production_access(compose: dict) -> None:
    service = compose["services"]["phoenix"]
    for forbidden in (
        "privileged",
        "devices",
        "network_mode",
        "cap_add",
        "pid",
        "ipc",
        "restart",
        "container_name",
        "user",
        "entrypoint",
        "command",
        "working_dir",
    ):
        assert forbidden not in service, f"phoenix service must not set {forbidden}"
    for mount in service["volumes"]:
        assert ":" not in mount or mount.startswith("data:"), f"bind mount: {mount}"
    assert service["networks"] == ["trial"]
    assert set(compose["networks"]) == {"trial"}
    assert compose["networks"]["trial"].get("driver") != "host"
    # No credential passthrough: environment values come only from validated
    # trial variables or literal safe policy values.
    for value in service["environment"].values():
        assert not str(value).startswith("${") or "PHOENIX_TRIAL" in str(value), value


def test_compose_pins_image_and_policy_environment(compose: dict) -> None:
    service = compose["services"]["phoenix"]
    assert service["image"] == PHOENIX_PIN
    assert service["environment"] == {
        "PHOENIX_HOST": "0.0.0.0",
        "PHOENIX_PORT": "6006",
        "PHOENIX_WORKING_DIR": "/mnt/data",
        "PHOENIX_DEFAULT_RETENTION_POLICY_DAYS": "${PHOENIX_TRIAL_RETENTION_DAYS:?}",
        "PHOENIX_ALLOW_EXTERNAL_RESOURCES": "false",
        "PHOENIX_TELEMETRY_ENABLED": "false",
        "PHOENIX_ALLOWED_PROVIDERS": "NONE",
        "PHOENIX_ALLOWED_SANDBOX_PROVIDERS": "NONE",
        "PHOENIX_ENABLE_MCP_SERVER": "false",
        "PHOENIX_ENABLE_PROMETHEUS": "false",
        "PHOENIX_ENABLE_AUTH": "false",
    }


def test_compose_labels_owned_resources(compose: dict) -> None:
    labels = {
        "llm-env.scope": "phoenix-trial",
        "llm-env.project": "${PHOENIX_TRIAL_PROJECT:?}",
    }
    assert compose["services"]["phoenix"]["labels"] == labels
    assert compose["networks"]["trial"]["labels"] == labels


def test_compose_variables_are_validated_trial_only(compose: dict) -> None:
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    import re

    for variable in sorted(set(re.findall(r"\$\{([A-Z_]+):\?", text))):
        assert variable.startswith("PHOENIX_TRIAL_"), variable


def test_sender_image_is_curated_pinned_and_offline() -> None:
    dockerfile = (REPO / "docker" / "phoenix-trial" / "Dockerfile.sender").read_text()
    assert "ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim@sha256:aba5f865793af9275ceaa06d30935dfc6db8b81969c9fb099b0ebca6396b189e" in dockerfile
    assert "COPY ." not in dockerfile
    assert "UV_OFFLINE=1 uv run llmenv.py phoenix-trial fixture-check" in dockerfile
    assert 'ENTRYPOINT ["uv", "run", "llmenv.py", "phoenix-trial"]' in dockerfile
    copy_lines = "\n".join(line for line in dockerfile.splitlines() if line.startswith("COPY "))
    for source in ("llmenv.py", "pyproject.toml", "uv.lock", "pylib/phoenix_trial.py", "docker/phoenix-trial/fixtures/laya-synthetic.json"):
        assert source in copy_lines


def test_agents_preserves_plans_and_names_worktree_root() -> None:
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "Retain all plans, including dormant and superseded plans" in agents
    assert "`.claude/worktrees/`" in agents


def test_trial_readme_documents_boundary_and_pins() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    assert PHOENIX_PIN in readme
    for needle in (
        "llm-env-phoenix-trial",
        "127.0.0.1",
        "internal: true",
        "verify",
        "stop",
        "recovery",
        "laya-phoenix-trial",
    ):
        assert needle in readme, needle


# ---------------------------------------------------------------------------
# Task 2: fixture contracts and durable pre-POST preparation
# ---------------------------------------------------------------------------

from pylib import phoenix_trial
from pylib.phoenix_trial import (
    Manifest,
    PhoenixTrialError,
    build_fixture_spans,
    load_manifest,
    make_expected,
    mark_attempt,
    summarize_spans,
    validate_expected,
    write_prepared_run,
)

FIXTURE_PATH = REPO / "docker" / "phoenix-trial" / "fixtures" / "laya-synthetic.json"
RUN_ID = "12345678123442348123123456789abc"
BASE_TIME = datetime(2026, 10, 18, 16, tzinfo=UTC)

EXPECTED_SUMMARY = {
    "spans": 13,
    "requests": 12,
    "ok_requests": 9,
    "error_requests": 3,
    "error_rate": 0.25,
    "median_ms": 65,
    "requested_items": 15,
    "requested_questions": 18,
    "completed_answers": 15,
    "batch_requests": 2,
    "recorded_input_tokens": 900,
    "recorded_output_tokens": 18,
    "usage_unknown_requests": 3,
}

ROOT_TABLE = [
    ("r01", 10, "choice", 1, 1, "OK", 200),
    ("r02", 20, "score", 3, 2, "OK", 200),
    ("r03", 30, "noul", 1, 1, "OK", 200),
    ("r04", 40, "choice", 1, 1, "OK", 200),
    ("r05", 50, "score", 1, 1, "OK", 200),
    ("r06", 60, "noul", 2, 1, "OK", 200),
    ("r07", 70, "choice", 1, 1, "OK", 200),
    ("r08", 80, "score", 1, 1, "OK", 200),
    ("r09", 90, "noul", 1, 1, "OK", 200),
    ("r10", 100, "choice", 1, 1, "ERROR", 422),
    ("r11", 200, "score", 1, 1, "ERROR", 500),
    ("r12", 500, "noul", 1, 1, "ERROR", 503),
]


@pytest.fixture()
def manifest() -> Manifest:
    return load_manifest(FIXTURE_PATH)


@pytest.fixture()
def expected(manifest: Manifest) -> phoenix_trial.ExpectedRun:
    return make_expected(manifest, run_id=RUN_ID, base_time=BASE_TIME)


def test_flatten_attributes_rejects_empty_nested_dict() -> None:
    with pytest.raises(PhoenixTrialError, match="empty nested attribute"):
        phoenix_trial._flatten_attributes({"empty": {}})


def test_result_json_rejects_duplicate_object_keys(expected) -> None:
    span = next(span for span in expected["spans"] if span["name"] == "synthetic.r01")
    span["attributes"]["laya.result"] = '[{"item":1,"item":1,"question":1,"value":"fixture-a"}]'
    with pytest.raises(PhoenixTrialError, match="duplicate JSON key"):
        validate_expected(expected)


def test_fixture_and_root_summary(manifest, expected):
    spans = expected["spans"]
    summary = summarize_spans(spans, run_id=expected["run_id"])
    assert summary == manifest["expected_summary"] == expected["summary"]
    assert (summary["spans"], summary["requests"], summary["error_rate"]) == (
        13,
        12,
        0.25,
    )
    assert (
        summary["median_ms"],
        summary["recorded_input_tokens"],
        summary["recorded_output_tokens"],
    ) == (65, 900, 18)
    assert (
        summary["requested_items"],
        summary["requested_questions"],
        summary["completed_answers"],
    ) == (15, 18, 15)
    assert (summary["batch_requests"], summary["usage_unknown_requests"]) == (2, 3)
    assert spans == build_fixture_spans(
        manifest, run_id=expected["run_id"], base_time=BASE_TIME
    )


def test_fixture_pins_full_table_and_child_containment(manifest, expected):
    spans = {span["name"]: span for span in expected["spans"]}
    assert set(spans) == {f"synthetic.{k}" for k, *_ in ROOT_TABLE} | {"synthetic.c01"}
    base = BASE_TIME
    for index, (key, duration_ms, req_type, items, qpi, status, http_status) in enumerate(
        ROOT_TABLE
    ):
        span = spans[f"synthetic.{key}"]
        end = base - timedelta(seconds=index * 180)
        start = end - timedelta(milliseconds=duration_ms)
        assert span["start_time"] == phoenix_trial.canonical_time(start)
        assert span["end_time"] == phoenix_trial.canonical_time(end)
        assert span["span_kind"] == "LLM"
        assert span["parent_id"] is None
        assert span["status_code"] == status
        assert span["status_message"] == ("" if status == "OK" else f"synthetic HTTP {http_status}")
        attributes = span["attributes"]
        assert attributes["laya.run_id"] == RUN_ID
        assert attributes["llm.model_name"] == "synthetic-laya"
        assert attributes["laya.request_type"] == req_type
        assert attributes["laya.http_status"] == http_status
        assert attributes["laya.requested_items"] == items
        assert attributes["laya.requested_questions"] == items * qpi
        assert attributes["laya.completed_answers"] == (
            items * qpi if status == "OK" else 0
        )
        assert attributes["laya.batch"] == (items > 1)
        if status == "OK":
            assert attributes["llm.token_count.prompt"] == 100
            assert attributes["llm.token_count.completion"] == 2
            assert attributes["laya.result_type"] == req_type
            result = json.loads(attributes["laya.result"])
            assert result == [
                {"item": i, "question": q, "value": _fixture_value(req_type)}
                for i in range(1, items + 1)
                for q in range(1, qpi + 1)
            ]
            for probability in (
                "laya.confidence",
                "laya.x_jev_confidence",
                "laya.answer_confidence",
                "laya.noul_probability",
            ):
                if probability in attributes:
                    assert 0 <= attributes[probability] <= 1
        else:
            for absent in (
                "llm.token_count.prompt",
                "llm.token_count.completion",
                "laya.result_type",
                "laya.result",
                "laya.confidence",
                "laya.x_jev_confidence",
                "laya.answer_confidence",
                "laya.noul_probability",
            ):
                assert absent not in attributes
        metadata = json.loads(attributes["metadata"])
        assert metadata == {
            "data_source": "synthetic",
            "fixture_version": "laya-synthetic-v1",
            "run_id": RUN_ID,
        }

    child = spans["synthetic.c01"]
    root = spans["synthetic.r01"]
    assert child["span_kind"] == "CHAIN"
    assert child["context"]["trace_id"] == root["context"]["trace_id"]
    assert child["parent_id"] == root["context"]["span_id"]
    assert child["end_time"] == phoenix_trial.canonical_time(
        BASE_TIME - timedelta(milliseconds=1)
    )
    assert child["start_time"] == phoenix_trial.canonical_time(
        BASE_TIME - timedelta(milliseconds=1 + 5)
    )
    assert child["status_code"] == "OK"
    assert set(child["attributes"]) == {"laya.run_id", "metadata"}

    # Identity derivation: deterministic, unique, nonzero.
    trace_ids = [s["context"]["trace_id"] for s in expected["spans"][:12]]
    span_ids = [s["context"]["span_id"] for s in expected["spans"]]
    assert len(set(trace_ids)) == len(trace_ids)
    # The child intentionally shares its root's trace ID; span IDs remain unique.
    assert len(set(span_ids)) == len(span_ids)
    assert all(re.fullmatch(r"[0-9a-f]{32}", t) for t in trace_ids)
    assert all(re.fullmatch(r"[0-9a-f]{16}", s) for s in span_ids)


def _fixture_value(request_type: str):
    return {"choice": "fixture-a", "score": 0.5, "noul": 0.25}[request_type]


def test_new_run_ids_are_independent(manifest):
    other = make_expected(
        manifest,
        run_id="aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa",
        base_time=BASE_TIME + timedelta(seconds=180),
    )
    first = make_expected(manifest, run_id=RUN_ID, base_time=BASE_TIME)
    first_ids = {(s["context"]["trace_id"], s["context"]["span_id"]) for s in first["spans"]}
    other_ids = {(s["context"]["trace_id"], s["context"]["span_id"]) for s in other["spans"]}
    assert first_ids.isdisjoint(other_ids)
    assert first["base_time"] != other["base_time"]


def test_make_expected_rejects_bad_run_id_and_naive_time(manifest):
    with pytest.raises(PhoenixTrialError):
        make_expected(manifest, run_id="not-hex", base_time=BASE_TIME)
    with pytest.raises(PhoenixTrialError):
        make_expected(manifest, run_id=RUN_ID.upper(), base_time=BASE_TIME)
    with pytest.raises(PhoenixTrialError):
        make_expected(manifest, run_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", base_time=BASE_TIME)
    with pytest.raises(PhoenixTrialError):
        make_expected(manifest, run_id="aaaaaaaaaaaa4aaa0aaaaaaaaaaaaaaa", base_time=BASE_TIME)
    with pytest.raises(PhoenixTrialError):
        make_expected(
            manifest, run_id=RUN_ID, base_time=datetime(2026, 10, 18, 16)  # noqa: DTZ001
        )  # naive


def _altered_expected(expected, mutate):
    altered = copy.deepcopy(expected)
    mutate(altered)
    return altered


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.update(schema_version=2),
        lambda e: e.update(schema_version="1"),
        lambda e: e.update(schema_version=True),
        lambda e: e.update(extra_key=1),
        lambda e: e.pop("summary"),
        lambda e: e.update(run_id="z" * 32),
        lambda e: e.update(run_id="0" * 32),
        lambda e: e.update(base_time="2026-10-18T16:00:00Z"),  # non-millisecond
        lambda e: e.update(base_time="2026-10-18 16:00:00.000Z"),
        lambda e: e.update(base_time="2026-10-18T17:00:00.000+01:00"),  # non-UTC
        lambda e: e.update(spans=e["spans"][:-1]),
        lambda e: e["spans"].append(copy.deepcopy(e["spans"][0])),
        lambda e: e["spans"][0].update(name="synthetic.r99"),
        lambda e: e["spans"][0].update(context={"trace_id": "0" * 32, "span_id": "1" * 16}),
        lambda e: e["spans"][0]["context"].update(span_id=e["spans"][1]["context"]["span_id"]),
        lambda e: e["spans"][0].update(parent_id="f" * 16),
        lambda e: e["spans"][12].update(parent_id="e" * 16),  # child of nothing
        lambda e: e["spans"][12].update(parent_id=e["spans"][2]["context"]["span_id"]),
        lambda e: e["spans"][12]["attributes"].update(**{"llm.token_count.prompt": 100}),
        lambda e: e["spans"][9]["attributes"].update(**{"llm.token_count.prompt": 100}),
        lambda e: e["spans"][0]["attributes"].update(**{"laya.confidence": None}),
        lambda e: e["spans"][0]["attributes"].update(**{"laya.confidence": float("nan")}),
        lambda e: e["spans"][0]["attributes"].update(**{"laya.confidence": 1.5}),
        lambda e: e["spans"][0]["attributes"].update(**{"laya.requested_items": True}),
        lambda e: e["spans"][0]["attributes"].update(**{"laya.requested_items": -1}),
        lambda e: e["spans"][0]["attributes"].update(**{"laya.mystery": 1}),
        lambda e: e["spans"][0]["attributes"].pop("metadata"),
        lambda e: e["spans"][0].update(events=[{"name": "x"}]),
        lambda e: e["spans"][0].update(status_code="UNSET"),
        lambda e: e["spans"][0].update(start_time="2026-10-18T15:59:59.9999Z"),
        lambda e: e.update(summary={**e["summary"], "median_ms": 64}),
    ],
)
def test_validate_expected_rejects_malformed(expected, mutate):
    altered = _altered_expected(expected, mutate)
    with pytest.raises(PhoenixTrialError):
        validate_expected(altered)


def test_load_manifest_rejects_malformed_json(tmp_path):
    cases = [
        "[1,2,3]",
        '{"schema_version": 1, "schema_version": 1}',
        '{"schema_version": 1}',
        '{"schema_version": "1"}',
        '{"schema_version": true}',
    ]
    for index, text in enumerate(cases):
        path = tmp_path / f"manifest-{index}.json"
        path.write_text(text)
        with pytest.raises(PhoenixTrialError):
            load_manifest(path)


def test_load_manifest_rejects_nan(tmp_path):
    path = tmp_path / "manifest-nan.json"
    text = FIXTURE_PATH.read_text().replace('"error_rate": 0.25', '"error_rate": NaN')
    path.write_text(text)
    with pytest.raises(PhoenixTrialError):
        load_manifest(path)


def test_summarize_rejects_malformed_records(expected):
    altered = copy.deepcopy(expected["spans"])
    altered[0]["attributes"]["laya.completed_answers"] = "15"
    with pytest.raises(PhoenixTrialError):
        summarize_spans(altered, run_id=RUN_ID)
    altered = copy.deepcopy(expected["spans"])
    altered[3]["attributes"]["llm.token_count.prompt"] = True  # bool is not int
    with pytest.raises(PhoenixTrialError):
        summarize_spans(altered, run_id=RUN_ID)
    altered = copy.deepcopy(expected["spans"])
    altered[9]["attributes"]["llm.token_count.prompt"] = 5  # ERROR usage
    with pytest.raises(PhoenixTrialError):
        summarize_spans(altered, run_id=RUN_ID)


def test_prepare_is_durable_before_any_send(tmp_path, monkeypatch, expected):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    fsynced_files: list[str] = []
    fsynced_dirs: list[str] = []
    original_fsync = os.fsync

    def spy(fd):
        original_fsync(fd)
        path = os.readlink(f"/proc/self/fd/{fd}") if os.path.exists("/proc/self/fd") else ""
        if os.path.isdir(path) if path else False:
            fsynced_dirs.append(path)
        else:
            fsynced_files.append(path)

    monkeypatch.setattr(os, "fsync", spy)
    receipt = write_prepared_run(state_dir, expected)

    run_dir = state_dir / "runs" / expected["run_id"]
    expected_path = run_dir / "expected.json"
    prepared_path = run_dir / "prepared.json"
    assert receipt["schema_version"] == 1
    assert receipt["status"] == "prepared"
    assert receipt["run_id"] == expected["run_id"]
    assert receipt["expected_path"] == str(expected_path)
    assert expected_path.is_file()
    assert prepared_path.is_file()
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((state_dir / "runs").stat().st_mode) == 0o700
    assert stat.S_IMODE(expected_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(prepared_path.stat().st_mode) == 0o600
    # File and directory entries were fsynced before return.
    assert str(expected_path) in fsynced_files
    assert str(prepared_path) in fsynced_files
    assert str(run_dir) in fsynced_dirs
    # No partial state on second write: the run dir already exists.
    with pytest.raises(PhoenixTrialError):
        write_prepared_run(state_dir, expected)
    # Receipt round-trips the artifact.
    assert validate_expected(json.loads(expected_path.read_text()))["run_id"] == RUN_ID


def test_prepare_rejects_symlink_state(tmp_path, expected):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(PhoenixTrialError):
        write_prepared_run(link, expected)


def test_prepare_fails_when_fsync_fails(tmp_path, monkeypatch, expected):
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    def broken(fd):
        raise OSError("injected fsync failure")

    monkeypatch.setattr(os, "fsync", broken)
    with pytest.raises(PhoenixTrialError):
        write_prepared_run(state_dir, expected)


def test_mark_attempt_is_exclusive(tmp_path, expected):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    receipt = write_prepared_run(state_dir, expected)
    attempt = mark_attempt(Path(receipt["expected_path"]))
    assert attempt == {
        "schema_version": 1,
        "run_id": RUN_ID,
        "expected_path": receipt["expected_path"],
        "post_may_have_started": True,
    }
    attempt_path = Path(receipt["expected_path"]).with_name("attempt.json")
    assert attempt_path.is_file()
    assert stat.S_IMODE(attempt_path.stat().st_mode) == 0o600
    with pytest.raises(PhoenixTrialError):
        mark_attempt(Path(receipt["expected_path"]))


def test_mark_attempt_rejects_symlink_and_invalid_artifact(tmp_path, expected):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    receipt = write_prepared_run(state_dir, expected)
    link = tmp_path / "expected-link.json"
    link.symlink_to(receipt["expected_path"])
    with pytest.raises(PhoenixTrialError):
        mark_attempt(link)
    bogus = tmp_path / "bogus.json"
    bogus.write_text('{"schema_version": 1}')
    with pytest.raises(PhoenixTrialError):
        mark_attempt(bogus)


def test_compare_run_checks_exact_semantics_not_just_totals(expected):
    compare = phoenix_trial.compare_run
    assert compare(copy.deepcopy(expected["spans"]), expected=expected) == expected["summary"]
    mutations = [
        lambda spans: spans[0]["context"].update(span_id="f" * 16),
        lambda spans: spans[12].update(parent_id="f" * 16),
        lambda spans: spans[0]["attributes"].update(**{"laya.confidence": 0.1}),
        lambda spans: spans[0]["attributes"].update(
            **{"laya.result": '[{"item":1,"question":1,"value":"fixture-b"}]'}
        ),
    ]
    for mutate in mutations:
        changed = copy.deepcopy(expected["spans"])
        mutate(changed)
        assert summarize_spans(changed, run_id=expected["run_id"]) == expected["summary"]
        with pytest.raises(PhoenixTrialError):
            compare(changed, expected=expected)


def test_canonical_span_normalizes_pinned_get_shape(expected):
    span = copy.deepcopy(expected["spans"][0])
    attributes = dict(span["attributes"])
    attributes["openinference.span.kind"] = span["span_kind"]
    get_span = {
        **span,
        "id": "U3Bhbjox",
        "span_kind": span.pop("span_kind"),
        "attributes": {
            "llm": {
                "model_name": attributes.pop("llm.model_name"),
                "token_count": {
                    "prompt": attributes.pop("llm.token_count.prompt"),
                    "completion": attributes.pop("llm.token_count.completion"),
                },
            },
            **{key: value for key, value in attributes.items() if not key.startswith("llm.")},
        },
    }
    assert phoenix_trial.canonical_span(get_span) == expected["spans"][0]


def test_seed_response_lost_is_one_post_and_get_only_recovery(tmp_path, expected, monkeypatch):
    import io
    import urllib.error

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    prepared = write_prepared_run(state_dir, expected)
    expected_path = Path(prepared["expected_path"])
    attempt = mark_attempt(expected_path)
    before = {
        path.name: path.read_bytes()
        for path in (expected_path, expected_path.with_name("attempt.json"))
    }
    calls = []

    class Response:
        status = 200

        def __init__(self, value):
            self.buffer = io.BytesIO(json.dumps(value).encode())

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return self.buffer.read()

    class Opener:
        def open(self, request, timeout):
            calls.append((request.get_method(), request.full_url, request.data, timeout))
            if request.get_method() == "POST":
                raise urllib.error.URLError(TimeoutError("response lost"))
            return Response({"data": [{**span, "id": "U3Bhbjox"} for span in expected["spans"]], "next_cursor": None})

    monkeypatch.setattr(phoenix_trial, "build_opener", lambda *handlers: Opener())
    with pytest.raises(PhoenixTrialError, match=r"Phoenix POST outcome is uncertain \(URLError\)"):
        phoenix_trial.seed_spans("http://127.0.0.1:6006", expected, timeout_seconds=2)
    assert [call[0] for call in calls] == ["POST"]
    assert {path.name: path.read_bytes() for path in (expected_path, expected_path.with_name("attempt.json"))} == before
    assert attempt["post_may_have_started"] is True

    receipt = phoenix_trial.verify_run(
        "http://127.0.0.1:6006", run_id=expected["run_id"], expected=expected, timeout_seconds=2
    )
    assert receipt["status"] == "verified"
    assert [call[0] for call in calls] == ["POST", "GET"]
    assert calls[1][2] is None
    assert {path.name: path.read_bytes() for path in (expected_path, expected_path.with_name("attempt.json"))} == before


def test_seed_posts_once_and_returns_verified_receipt(expected, monkeypatch):
    import io

    calls = []

    class Response:
        def __init__(self, status, value):
            self.status = status
            self.buffer = io.BytesIO(json.dumps(value).encode())

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return self.buffer.read()

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            if request.get_method() == "POST":
                return Response(202, {"total_received": 13, "total_queued": 13})
            return Response(
                200,
                {"data": [{**span, "id": f"server-{i}"} for i, span in enumerate(expected["spans"])], "next_cursor": None},
            )

    monkeypatch.setattr(phoenix_trial, "build_opener", lambda *handlers: Opener())
    receipt = phoenix_trial.seed_spans(
        "http://phoenix:6006", expected, timeout_seconds=2
    )
    assert receipt["status"] == "verified"
    assert [request.get_method() for request in calls] == ["POST", "GET"]
    assert calls[0].full_url == "http://phoenix:6006/v1/projects/laya-phoenix-trial/spans"
    assert len(json.loads(calls[0].data)["data"]) == 13


def test_seed_rejects_non_integer_acceptance_count_without_retry(expected, monkeypatch):
    import io

    calls = []

    class Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return io.BytesIO(b'{"total_received":13.0,"total_queued":13}').read()

    class Opener:
        def open(self, request, timeout):
            calls.append(request.get_method())
            return Response()

    monkeypatch.setattr(phoenix_trial, "build_opener", lambda *handlers: Opener())
    with pytest.raises(PhoenixTrialError):
        phoenix_trial.seed_spans("http://phoenix:6006", expected, timeout_seconds=2)
    assert calls == ["POST"]


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1:6006",
        "http://user:pass@127.0.0.1:6006",
        "http://example.com:6006",
        "http://127.0.0.1:6006/api",
        "http://127.0.0.1:6006/",
        "http://127.0.0.1:6006?token=x",
        "http://127.0.0.1",
        "http://127.0.0.1:70000",
        "http://phoenix",
    ],
)
def test_rest_rejects_unsafe_base_urls_before_opening_network(base_url, expected, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unsafe URL must fail before creating transport")

    monkeypatch.setattr(phoenix_trial, "build_opener", forbidden)
    with pytest.raises(PhoenixTrialError):
        phoenix_trial.verify_run(
            base_url, run_id=expected["run_id"], expected=expected, timeout_seconds=1
        )


def test_verify_rejects_aggregate_preserving_readback_change(tmp_path, expected, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    prepared = write_prepared_run(state, expected)
    path = Path(prepared["expected_path"])
    before = path.read_bytes()
    altered = copy.deepcopy(expected["spans"])
    altered[0]["attributes"]["laya.confidence"] = 0.1
    assert summarize_spans(altered, run_id=expected["run_id"]) == expected["summary"]
    methods = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({"data": altered, "next_cursor": None}).encode()

    class Opener:
        def open(self, request, timeout):
            methods.append(request.get_method())
            return Response()

    monkeypatch.setattr(phoenix_trial, "build_opener", lambda *handlers: Opener())
    with pytest.raises(PhoenixTrialError):
        phoenix_trial.verify_run(
            "http://127.0.0.1:6006", run_id=expected["run_id"], expected=expected, timeout_seconds=2
        )
    assert methods == ["GET"]
    assert path.read_bytes() == before


def test_verify_waits_for_eventual_full_readback(expected, monkeypatch):
    import io

    class Clock:
        value = 0.0

        def now(self):
            return self.value

        def sleep(self, duration):
            self.value += duration

    clock = Clock()
    responses = [
        {"data": [], "next_cursor": None},
        {"data": [{**span, "id": f"span-{i}"} for i, span in enumerate(expected["spans"])], "next_cursor": None},
    ]
    calls = []

    class Response:
        status = 200

        def __init__(self, value):
            self.buffer = io.BytesIO(json.dumps(value).encode())

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return self.buffer.read()

    class Opener:
        def open(self, request, timeout):
            calls.append(request.get_method())
            return Response(responses.pop(0))

    monkeypatch.setattr(phoenix_trial, "build_opener", lambda *handlers: Opener())
    monkeypatch.setattr(phoenix_trial.time, "monotonic", clock.now)
    monkeypatch.setattr(phoenix_trial.time, "sleep", clock.sleep)
    receipt = phoenix_trial.verify_run(
        "http://127.0.0.1:6006", run_id=expected["run_id"], expected=expected, timeout_seconds=1
    )
    assert receipt["status"] == "verified"
    assert calls == ["GET", "GET"]


def test_volume_bytes_counts_logical_files_and_rejects_symlinks(tmp_path):
    root = tmp_path / "volume"
    root.mkdir()
    (root / "a").write_bytes(b"123")
    nested = root / "nested"
    nested.mkdir()
    (nested / "b").write_bytes(b"4567")
    assert phoenix_trial.volume_bytes(root) == 7
    (nested / "link").symlink_to(root / "a")
    with pytest.raises(PhoenixTrialError):
        phoenix_trial.volume_bytes(root)


def test_prepare_fixture_check_and_mark_attempt_do_no_http(
    tmp_path, manifest, expected, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("prepare/fixture-check/mark-attempt must not use HTTP")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    receipt = write_prepared_run(state_dir, expected)
    mark_attempt(Path(receipt["expected_path"]))


def _fake_response(status: int, payload: object):
    import io

    class Response:
        def __init__(self):
            self.status = status
            self.buffer = io.BytesIO(json.dumps(payload).encode())

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return self.buffer.read()

    return Response()


def test_seed_deadline_after_accepted_post_preserves_recovery_artifacts(tmp_path, expected, monkeypatch):
    import io

    state = tmp_path / "state"
    state.mkdir()
    prepared = write_prepared_run(state, expected)
    path = Path(prepared["expected_path"])
    mark_attempt(path)
    attempt_path = path.with_name("attempt.json")
    before = (path.read_bytes(), attempt_path.read_bytes())

    class Clock:
        value = 0.0

        def now(self):
            return self.value

    clock = Clock()

    class Accepted:
        status = 202

        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self):
            clock.value = 1.0
            return io.BytesIO(b'{"total_received":13,"total_queued":13}').read()

    class Opener:
        def open(self, request, timeout):
            assert request.get_method() == "POST"
            return Accepted()

    monkeypatch.setattr(phoenix_trial, "build_opener", lambda *handlers: Opener())
    monkeypatch.setattr(phoenix_trial.time, "monotonic", clock.now)
    with pytest.raises(PhoenixTrialError, match="exhausted the verification deadline"):
        phoenix_trial.seed_spans("http://127.0.0.1:6006", expected, timeout_seconds=1)
    assert (path.read_bytes(), attempt_path.read_bytes()) == before


def test_page_walk_round_trips_escaped_cursor(expected, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    pages = [expected["spans"][:7], expected["spans"][7:]]
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(request.full_url)
            page = len(requests) - 1
            cursor = "next /+?&" if page == 0 else None
            return _fake_response(200, {"data": pages[page], "next_cursor": cursor})

    monkeypatch.setattr(phoenix_trial.time, "monotonic", lambda: 0.0)
    result = phoenix_trial._page_walk(Opener(), "http://127.0.0.1:6006/spans", 5.0)
    assert len(result) == len(expected["spans"])
    assert parse_qs(urlsplit(requests[1]).query)["cursor"] == ["next /+?&"]


def test_page_walk_rejects_repeated_cursor(expected, monkeypatch):
    class Opener:
        def open(self, request, timeout):
            return _fake_response(200, {"data": [], "next_cursor": "repeat"})

    monkeypatch.setattr(phoenix_trial.time, "monotonic", lambda: 0.0)
    with pytest.raises(PhoenixTrialError, match="repeated"):
        phoenix_trial._page_walk(Opener(), "http://127.0.0.1:6006/spans", 5.0)


def test_page_walk_enforces_twenty_page_cap(expected, monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            return _fake_response(200, {"data": [], "next_cursor": f"cursor-{len(calls)}"})

    monkeypatch.setattr(phoenix_trial.time, "monotonic", lambda: 0.0)
    with pytest.raises(PhoenixTrialError, match="20-page limit"):
        phoenix_trial._page_walk(Opener(), "http://127.0.0.1:6006/spans", 5.0)
    assert len(calls) == 20


def test_verify_retries_bounded_503_and_connection_errors(expected, monkeypatch):
    import urllib.error

    class Clock:
        value = 0.0
        def now(self): return self.value
        def sleep(self, duration): self.value += duration

    clock, calls = Clock(), []

    class Opener:
        def open(self, request, timeout):
            calls.append(request.get_method())
            if len(calls) == 1:
                raise urllib.error.URLError("offline")
            if len(calls) == 2:
                return _fake_response(503, {})
            records = [{**span, "id": f"server-{index}"} for index, span in enumerate(expected["spans"])]
            return _fake_response(200, {"data": records, "next_cursor": None})

    monkeypatch.setattr(phoenix_trial, "build_opener", lambda *handlers: Opener())
    monkeypatch.setattr(phoenix_trial.time, "monotonic", clock.now)
    monkeypatch.setattr(phoenix_trial.time, "sleep", clock.sleep)
    receipt = phoenix_trial.verify_run("http://127.0.0.1:6006", run_id=expected["run_id"], expected=expected, timeout_seconds=2)
    assert receipt["status"] == "verified"
    assert calls == ["GET", "GET", "GET"]
    assert clock.value <= 2


def test_verify_retries_initial_project_not_found(expected, monkeypatch):
    class Clock:
        value = 0.0
        def now(self): return self.value
        def sleep(self, duration): self.value += duration
    clock = Clock()
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request.get_method())
            if len(calls) == 1: return _fake_response(404, {})
            records = [{**span, "id": f"server-{index}"} for index, span in enumerate(expected["spans"])]
            return _fake_response(200, {"data": records, "next_cursor": None})

    monkeypatch.setattr(phoenix_trial, "build_opener", lambda *handlers: Opener())
    monkeypatch.setattr(phoenix_trial.time, "monotonic", clock.now)
    monkeypatch.setattr(phoenix_trial.time, "sleep", clock.sleep)
    assert phoenix_trial.verify_run("http://127.0.0.1:6006", run_id=expected["run_id"], expected=expected, timeout_seconds=2)["status"] == "verified"
    assert calls == ["GET", "GET"]


def test_verify_readback_timeout_preserves_prepared_and_attempt_artifacts(tmp_path, expected, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    prepared = write_prepared_run(state, expected)
    path = Path(prepared["expected_path"])
    mark_attempt(path)
    attempt = path.with_name("attempt.json")
    before = (path.read_bytes(), attempt.read_bytes())

    class Clock:
        value = 0.0
        def now(self): return self.value
        def sleep(self, duration): self.value += duration
    clock, calls = Clock(), []

    class Opener:
        def open(self, request, timeout):
            calls.append(request.get_method())
            return _fake_response(200, {"data": [], "next_cursor": None})

    monkeypatch.setattr(phoenix_trial, "build_opener", lambda *handlers: Opener())
    monkeypatch.setattr(phoenix_trial.time, "monotonic", clock.now)
    monkeypatch.setattr(phoenix_trial.time, "sleep", clock.sleep)
    with pytest.raises(PhoenixTrialError, match="readback deadline expired"):
        phoenix_trial.verify_run("http://127.0.0.1:6006", run_id=expected["run_id"], expected=expected, timeout_seconds=1)
    assert calls and set(calls) == {"GET"}
    assert clock.value == 1
    assert (path.read_bytes(), attempt.read_bytes()) == before


def test_seed_rejects_non_202_without_retry(expected, monkeypatch):
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(request.get_method())
            return _fake_response(200, {})
    monkeypatch.setattr(phoenix_trial, "build_opener", lambda *handlers: Opener())
    with pytest.raises(PhoenixTrialError, match="HTTP 200"):
        phoenix_trial.seed_spans("http://127.0.0.1:6006", expected, timeout_seconds=2)
    assert calls == ["POST"]


@pytest.mark.parametrize("received,queued", [(12, 13), (13, 12), (-1, 13)])
def test_seed_rejects_wrong_integer_acceptance_counts_without_retry(expected, monkeypatch, received, queued):
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(request.get_method())
            return _fake_response(202, {"total_received": received, "total_queued": queued})
    monkeypatch.setattr(phoenix_trial, "build_opener", lambda *handlers: Opener())
    with pytest.raises(PhoenixTrialError, match="acceptance counts"):
        phoenix_trial.seed_spans("http://127.0.0.1:6006", expected, timeout_seconds=2)
    assert calls == ["POST"]


@pytest.mark.parametrize("malformed", [b"{", b"[]"])
def test_open_json_rejects_malformed_or_nonobject_get_json(expected, monkeypatch, malformed):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self): return malformed
    class Opener:
        def open(self, request, timeout): return Response()
    monkeypatch.setattr(phoenix_trial, "build_opener", lambda *handlers: Opener())
    with pytest.raises(PhoenixTrialError, match="malformed JSON|must be an object"):
        phoenix_trial.verify_run("http://127.0.0.1:6006", run_id=expected["run_id"], expected=expected, timeout_seconds=1)


def test_reject_redirect_handler_fails_closed():
    import io
    from http.client import HTTPMessage
    from urllib.request import Request
    with pytest.raises(PhoenixTrialError, match="redirects are not allowed"):
        phoenix_trial._RejectRedirects().redirect_request(
            Request("http://127.0.0.1:6006/spans"), io.BytesIO(), 302, "Found", HTTPMessage(),
            "http://127.0.0.1:8010/",
        )


@pytest.mark.parametrize("extra_kind", ["duplicate", "excess-same-run"])
def test_verify_rejects_duplicate_or_excess_same_run_records(expected, monkeypatch, extra_kind):
    import copy
    records = [{**span, "id": f"server-{index}"} for index, span in enumerate(expected["spans"])]
    if extra_kind == "duplicate":
        records.append({**records[0], "id": "server-duplicate"})
    else:
        extra = copy.deepcopy(records[0])
        extra["id"] = "server-excess"
        extra["context"]["span_id"] = "f" * 16
        records.append(extra)

    class Opener:
        def open(self, request, timeout):
            return _fake_response(200, {"data": records, "next_cursor": None})

    monkeypatch.setattr(phoenix_trial, "build_opener", lambda *handlers: Opener())
    with pytest.raises(PhoenixTrialError, match="duplicate selected|identities do not match"):
        phoenix_trial.verify_run(
            "http://127.0.0.1:6006", run_id=expected["run_id"], expected=expected, timeout_seconds=1
        )
