import http.server
import json
import subprocess
import sys
import threading
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO / "llmenv.py"), *args],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )


def write_test_config(tmp_path: Path) -> Path:
    config = tmp_path / "models.yml"
    config.write_text(
        "version: 1\n"
        "server: {host: 0.0.0.0, port: 8000, api_key: k, mdns_name: llm,"
        " sleep_idle_seconds: 300}\n"
        "gpu: {pci_address: '0000:03:00.0', device_name: d, backend: vulkan,"
        " image: i, vram_total_mib: 16304, reserve_mode: auto, reserve_floor_mib: 1024}\n"
        "runtime: {models_max: 1, parallel_slots: 1, ubatch_size: 512,"
        " flash_attn: true, cache_type_k: q8_0, cache_type_v: q8_0}\n"
        "models:\n"
        "  - {alias: a, label: A, parameters: 1B, quantization: Q4_K_M, enabled: true,"
        " file: a.gguf, url: u, size_bytes: 1, vram_budget: 10%, ctx_size: 4096,"
        " client_max_output_tokens: 4096, n_gpu_layers: 99}\n"
        "  - {alias: b, label: B, parameters: 1B, quantization: Q4_K_M, enabled: false,"
        " file: b.gguf, url: u, size_bytes: 1, vram_budget: 10%, ctx_size: 4096,"
        " client_max_output_tokens: 4096, n_gpu_layers: 99}\n"
    )
    return config


def enabled_aliases(config: Path) -> list[str]:
    import yaml

    return [
        model["alias"]
        for model in yaml.safe_load(config.read_text())["models"]
        if model["enabled"]
    ]


def test_detect_emits_json_with_gpus_key():
    result = run("detect")
    assert result.returncode == 0, result.stderr
    assert "gpus" in json.loads(result.stdout)


def test_cmd_processes_on_render_node_returns_a_processes_list() -> None:
    result = run("processes-on-render-node", "--render-node", "renderD128")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert isinstance(payload["processes"], list)


def test_cmd_processes_on_render_node_requires_render_node() -> None:
    result = run("processes-on-render-node")
    assert result.returncode == 2


def test_resolve_device_matches_pci_from_device_listing(tmp_path):
    listing = tmp_path / "devices.txt"
    listing.write_text(
        "Available devices:\n"
        "  Vulkan0: AMD Radeon RX 9070 XT (RADV GFX1201) (16304 MiB, 16304 MiB free)\n"
        "  Vulkan1: AMD Radeon Graphics (RADV RAPHAEL_MENDOCINO) (512 MiB, 512 MiB free)\n"
    )
    result = run(
        "resolve-device",
        "--device-name",
        "AMD Radeon RX 9070 XT (RADV GFX1201)",
        "--listing-file",
        str(listing),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["device"] == "Vulkan0"


def test_resolve_device_rejects_duplicate_device_names(tmp_path):
    listing = tmp_path / "devices.txt"
    listing.write_text(
        "  Vulkan0: Shared GPU (16304 MiB)\n"
        "  Vulkan1: Shared GPU (16304 MiB)\n"
    )
    result = run(
        "resolve-device",
        "--device-name",
        "Shared GPU",
        "--listing-file",
        str(listing),
    )
    assert result.returncode == 1
    assert "multiple devices match" in json.loads(result.stdout)["error"]


def test_resolve_device_reports_error_when_absent(tmp_path):
    listing = tmp_path / "devices.txt"
    listing.write_text("Available devices:\n  Vulkan0: Some Other GPU (1024 MiB)\n")
    result = run(
        "resolve-device",
        "--device-name",
        "AMD Radeon RX 9070 XT (RADV GFX1201)",
        "--listing-file",
        str(listing),
    )
    assert result.returncode == 1
    assert "error" in json.loads(result.stdout)


def test_models_list_reports_enabled_flags(tmp_path):
    config = write_test_config(tmp_path)
    result = run("models", "list", "--config", str(config))
    assert result.returncode == 0, result.stderr
    models = {m["alias"]: m["enabled"] for m in json.loads(result.stdout)["models"]}
    assert models == {"a": True, "b": False}


def test_models_enable_preserves_models_max(tmp_path):
    config = write_test_config(tmp_path)
    result = run("models", "enable", "b", "--config", str(config))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["models_max"] == 1
    assert enabled_aliases(config) == ["a", "b"]


def test_models_rejects_disabling_final_model_without_writing(tmp_path):
    config = write_test_config(tmp_path)
    before = config.read_text()
    result = run("models", "disable", "a", "--config", str(config))
    assert result.returncode == 1
    assert "final enabled model" in json.loads(result.stdout)["error"]
    assert config.read_text() == before


def test_models_select_replaces_enabled_set(tmp_path):
    config = write_test_config(tmp_path)
    result = run("models", "select", "b", "--config", str(config))
    assert result.returncode == 0
    assert json.loads(result.stdout)["models_max"] == 1
    assert enabled_aliases(config) == ["b"]


def test_models_select_accepts_multiple_aliases(tmp_path):
    config = write_test_config(tmp_path)
    result = run("models", "select", "a", "b", "--config", str(config))
    assert result.returncode == 0
    assert json.loads(result.stdout)["models_max"] == 1
    assert enabled_aliases(config) == ["a", "b"]


def test_models_select_persists_reverse_request_order(tmp_path):
    config = write_test_config(tmp_path)

    result = run("models", "select", "b", "a", "--config", str(config))

    assert result.returncode == 0, result.stderr
    assert [model["alias"] for model in yaml.safe_load(config.read_text())["models"]] == [
        "b",
        "a",
    ]
    assert enabled_aliases(config) == ["b", "a"]


def test_models_select_rejects_duplicate_alias_without_writing(tmp_path):
    config = write_test_config(tmp_path)
    before = config.read_bytes()

    result = run("models", "select", "b", "b", "--config", str(config))

    assert result.returncode == 1
    assert "duplicate model alias: b" in json.loads(result.stdout)["error"]
    assert config.read_bytes() == before


def test_models_select_reports_unknown_alias(tmp_path):
    config = write_test_config(tmp_path)
    before = config.read_bytes()
    result = run("models", "select", "missing", "--config", str(config))
    assert result.returncode == 1
    assert "unknown model alias: missing" in json.loads(result.stdout)["error"]
    assert config.read_bytes() == before


def test_list_devices_reports_failed_listing_command():
    result = run("list-devices", "--list-command", "exit 7")
    assert result.returncode == 1
    assert "device listing command failed with exit status 7" in json.loads(result.stdout)[
        "error"
    ]


def test_list_devices_requires_listing_source():
    result = run("list-devices")
    assert result.returncode == 1
    assert "provide --listing-file or --list-command" in json.loads(result.stdout)["error"]


def test_list_devices_parses_vulkan_rows(tmp_path):
    listing = tmp_path / "devices.txt"
    listing.write_text(
        "  Vulkan0: GPU A (16304 MiB, 16000 MiB free)\n"
        "  Vulkan1: GPU B (512 MiB, 500 MiB free)\n"
    )
    result = run("list-devices", "--listing-file", str(listing))
    assert json.loads(result.stdout)["devices"] == [
        {"id": "Vulkan0", "name": "GPU A", "total_mib": 16304},
        {"id": "Vulkan1", "name": "GPU B", "total_mib": 512},
    ]


def test_unknown_subcommand_is_usage_error():
    assert run("nonsense").returncode == 2


def test_run_agent_bounded_forwards_lower_limits_and_remainder(
    tmp_path: Path, monkeypatch, capsys
):
    import llmenv
    from pylib.agent_runner import BoundedRunResult, RunLimits

    transcript = tmp_path / "transcript.jsonl"
    stderr = tmp_path / "agent.stderr"
    captured = {}
    runner_result = BoundedRunResult(
        outcome="completed",
        exit_status=0,
        transcript_bytes=12,
        stderr_bytes=3,
        cleanup_proved=True,
    )

    def fake_run(command, transcript_path, stderr_path, *, limits):
        captured.update(
            command=command,
            transcript_path=transcript_path,
            stderr_path=stderr_path,
            limits=limits,
        )
        return runner_result

    monkeypatch.setattr(llmenv, "run_bounded_agent", fake_run, raising=False)

    status = llmenv.main(
        [
            "run-agent-bounded",
            "--transcript",
            str(transcript),
            "--stderr",
            str(stderr),
            "--runtime-seconds",
            "1.25",
            "--grace-seconds",
            "0.5",
            "--stream-limit-bytes",
            "1024",
            "--",
            "agent-client",
            "--model",
            "local",
        ]
    )

    streams = capsys.readouterr()
    assert status == 0
    assert streams.err == ""
    assert captured == {
        "command": ["agent-client", "--model", "local"],
        "transcript_path": transcript,
        "stderr_path": stderr,
        "limits": RunLimits(
            runtime_seconds=1.25,
            grace_seconds=0.5,
            stream_limit_bytes=1024,
        ),
    }


def test_run_agent_bounded_emits_exact_json_and_succeeds_for_client_failure(
    tmp_path: Path, monkeypatch, capsys
):
    import llmenv
    from pylib.agent_runner import BoundedRunResult

    runner_result = BoundedRunResult(
        outcome="completed",
        exit_status=7,
        transcript_bytes=23,
        stderr_bytes=11,
        cleanup_proved=True,
    )
    monkeypatch.setattr(
        llmenv,
        "run_bounded_agent",
        lambda *args, **kwargs: runner_result,
        raising=False,
    )

    status = llmenv.main(
        [
            "run-agent-bounded",
            "--transcript",
            str(tmp_path / "transcript.jsonl"),
            "--stderr",
            str(tmp_path / "agent.stderr"),
            "--",
            "agent-client",
        ]
    )

    streams = capsys.readouterr()
    assert status == 0
    assert streams.out == json.dumps(runner_result.to_dict(), indent=2) + "\n"
    assert streams.err == ""
    assert set(json.loads(streams.out)) == {
        "schema",
        "outcome",
        "exit_status",
        "transcript_bytes",
        "stderr_bytes",
        "cleanup_proved",
    }


def test_run_agent_bounded_boundary_result_still_succeeds(
    tmp_path: Path, monkeypatch, capsys
):
    import llmenv
    from pylib.agent_runner import BoundedRunResult

    runner_result = BoundedRunResult(
        outcome="boundary-failure",
        exit_status=None,
        transcript_bytes=0,
        stderr_bytes=0,
        cleanup_proved=False,
    )
    monkeypatch.setattr(
        llmenv,
        "run_bounded_agent",
        lambda *args, **kwargs: runner_result,
        raising=False,
    )

    status = llmenv.main(
        [
            "run-agent-bounded",
            "--transcript",
            str(tmp_path / "transcript.jsonl"),
            "--stderr",
            str(tmp_path / "agent.stderr"),
            "agent-client",
        ]
    )

    streams = capsys.readouterr()
    assert status == 0
    assert json.loads(streams.out) == runner_result.to_dict()
    assert streams.err == ""


@pytest.mark.parametrize("remainder", [(), ("--",)])
def test_run_agent_bounded_rejects_missing_remainder_command(
    tmp_path: Path, monkeypatch, capsys, remainder: tuple[str, ...]
):
    import llmenv

    monkeypatch.setattr(
        llmenv,
        "run_bounded_agent",
        lambda *args, **kwargs: pytest.fail("runner must not be called"),
        raising=False,
    )

    with pytest.raises(SystemExit) as exc_info:
        llmenv.main(
            [
                "run-agent-bounded",
                "--transcript",
                str(tmp_path / "transcript.jsonl"),
                "--stderr",
                str(tmp_path / "agent.stderr"),
                *remainder,
            ]
        )

    streams = capsys.readouterr()
    assert exc_info.value.code == 2
    assert streams.out == ""
    assert "a remainder command is required" in streams.err


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--runtime-seconds", "not-a-number"),
        ("--runtime-seconds", "nan"),
        ("--runtime-seconds", "inf"),
        ("--runtime-seconds", "0"),
        ("--runtime-seconds", "-1"),
        ("--grace-seconds", "not-a-number"),
        ("--grace-seconds", "nan"),
        ("--grace-seconds", "inf"),
        ("--grace-seconds", "0"),
        ("--grace-seconds", "-1"),
        ("--stream-limit-bytes", "not-an-integer"),
        ("--stream-limit-bytes", "1.5"),
        ("--stream-limit-bytes", "0"),
        ("--stream-limit-bytes", "-1"),
    ],
)
def test_run_agent_bounded_rejects_invalid_limits(
    tmp_path: Path, monkeypatch, capsys, option: str, value: str
):
    import llmenv

    monkeypatch.setattr(
        llmenv,
        "run_bounded_agent",
        lambda *args, **kwargs: pytest.fail("runner must not be called"),
        raising=False,
    )

    with pytest.raises(SystemExit) as exc_info:
        llmenv.main(
            [
                "run-agent-bounded",
                "--transcript",
                str(tmp_path / "transcript.jsonl"),
                "--stderr",
                str(tmp_path / "agent.stderr"),
                option,
                value,
                "--",
                "agent-client",
            ]
        )

    streams = capsys.readouterr()
    assert exc_info.value.code == 2
    assert streams.out == ""
    assert f"argument {option}" in streams.err


def test_run_agent_bounded_forwards_production_defaults(
    tmp_path: Path, monkeypatch, capsys
):
    import llmenv
    from pylib.agent_runner import (
        DEFAULT_GRACE_SECONDS,
        DEFAULT_RUNTIME_SECONDS,
        DEFAULT_STREAM_LIMIT_BYTES,
        BoundedRunResult,
        RunLimits,
    )

    captured = {}

    def fake_run(command, transcript_path, stderr_path, *, limits):
        captured["limits"] = limits
        return BoundedRunResult(
            outcome="completed",
            exit_status=0,
            transcript_bytes=0,
            stderr_bytes=0,
            cleanup_proved=True,
        )

    monkeypatch.setattr(llmenv, "run_bounded_agent", fake_run, raising=False)

    status = llmenv.main(
        [
            "run-agent-bounded",
            "--transcript",
            str(tmp_path / "transcript.jsonl"),
            "--stderr",
            str(tmp_path / "agent.stderr"),
            "--",
            "agent-client",
        ]
    )

    assert status == 0
    assert capsys.readouterr().err == ""
    assert captured["limits"] == RunLimits(
        runtime_seconds=DEFAULT_RUNTIME_SECONDS,
        grace_seconds=DEFAULT_GRACE_SECONDS,
        stream_limit_bytes=DEFAULT_STREAM_LIMIT_BYTES,
    )


def test_config_flag_works_before_the_subcommand(tmp_path):
    config = tmp_path / "models.yml"
    result = run("--config", str(config), "init", "--template", "models.yml.example")
    assert result.returncode == 0, result.stderr
    assert config.exists()


def test_config_flag_works_after_the_subcommand(tmp_path):
    config = tmp_path / "models.yml"
    result = run("init", "--config", str(config), "--template", "models.yml.example")
    assert result.returncode == 0, result.stderr
    assert config.exists()


def test_default_config_uses_128k_q5_1_runtime(tmp_path):
    config = tmp_path / "models.yml"
    result = run("init", "--config", str(config), "--template", "models.yml.example")

    assert result.returncode == 0, result.stderr
    parsed = yaml.safe_load(config.read_text())
    assert parsed["runtime"]["models_max"] == 1
    assert parsed["runtime"]["cache_type_k"] == "q5_1"
    assert parsed["runtime"]["cache_type_v"] == "q5_1"
    ctx_size_by_alias = {model["alias"]: model["ctx_size"] for model in parsed["models"]}
    output_tokens_by_alias = {
        model["alias"]: model["client_max_output_tokens"] for model in parsed["models"]
    }
    assert ctx_size_by_alias == {
        "gemma4": 131072,
        "ornith": 262144,
        "ornith-35b": 262144,
    }
    assert output_tokens_by_alias == {
        "gemma4": 8192,
        "ornith": 8192,
        "ornith-35b": 8192,
    }


def test_default_config_uses_agentic_gemma_q4(tmp_path):
    config = tmp_path / "models.yml"
    result = run("init", "--config", str(config), "--template", "models.yml.example")

    assert result.returncode == 0, result.stderr
    parsed = yaml.safe_load(config.read_text())
    gemma = next(model for model in parsed["models"] if model["alias"] == "gemma4")
    assert {
        "label": gemma["label"],
        "file": gemma["file"],
        "url": gemma["url"],
        "size_bytes": gemma["size_bytes"],
        "parameters": gemma["parameters"],
        "quantization": gemma["quantization"],
        "ctx_size": gemma["ctx_size"],
        "client_max_output_tokens": gemma["client_max_output_tokens"],
        "n_gpu_layers": gemma["n_gpu_layers"],
    } == {
        "label": "Gemma 4 12B Agentic v2",
        "file": "gemma4-v2-Q4_K_M.gguf",
        "url": "https://huggingface.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF/resolve/190a31365a6b80a692349be34ccdac730cad4fe4/gemma4-v2-Q4_K_M.gguf",
        "size_bytes": 7381381664,
        "parameters": "12B",
        "quantization": "Q4_K_M",
        "ctx_size": 131072,
        "client_max_output_tokens": 8192,
        "n_gpu_layers": 99,
    }


def test_budget_reports_actionable_error_when_pci_address_unset(tmp_path):
    config = tmp_path / "models.yml"
    assert (
        run(
            "init", "--config", str(config), "--template", "models.yml.example"
        ).returncode
        == 0
    )
    result = run("budget", "--config", str(config), "--models-dir", str(tmp_path))
    assert result.returncode == 1
    message = json.loads(result.stdout)["error"]
    assert "gpu.pci_address is not set" in message
    assert "  " not in message


def test_malformed_yaml_reports_location_without_source_text(tmp_path: Path):
    config = tmp_path / "models.yml"
    secret = "fixture-yaml-parser-secret"
    config.write_text(f"server:\n  api_key: {secret}: exposed\n")

    result = run("models", "list", "--config", str(config))

    assert result.returncode == 1
    error = json.loads(result.stdout)["error"]
    assert str(config) in error
    assert "mapping values are not allowed here" in error
    assert "line 2" in error
    assert "column" in error
    assert secret not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "command",
    [
        ("budget", "--models-dir", "."),
        ("presets", "--models-dir", "/models", "--device", "all", "--output", "presets.ini"),
        ("models", "list"),
        ("models", "select", "b"),
        ("models", "enable", "b"),
        ("models", "disable", "a"),
        ("validate-gguf", "--models-dir", "."),
    ],
)
def test_operational_commands_reject_invalid_concurrency_before_output_or_save(
    tmp_path: Path, command: tuple[str, ...]
):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    parsed["runtime"]["parallel_slots"] = 2
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))
    before = config.read_bytes()
    output = tmp_path / "presets.ini"
    resolved = tuple(str(output) if value == "presets.ini" else value for value in command)

    result = run(*resolved, "--config", str(config))

    assert result.returncode == 1
    assert "runtime.parallel_slots must be 1" in json.loads(result.stdout)["error"]
    assert config.read_bytes() == before
    assert not output.exists()


def test_presets_rejects_invalid_sampling_before_output(tmp_path: Path):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    parsed["models"][0]["sampling"] = {"temperature": -1}
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))
    before = config.read_bytes()
    output = tmp_path / "presets.ini"

    result = run(
        "presets",
        "--models-dir",
        "/models",
        "--device",
        "all",
        "--output",
        str(output),
        "--config",
        str(config),
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["error"] == (
        "model a sampling.temperature must be a finite non-negative number"
    )
    assert "Traceback" not in result.stdout + result.stderr
    assert config.read_bytes() == before
    assert not output.exists()


def test_migrate_config_writes_once_without_exposing_secrets(tmp_path: Path):
    fixture = REPO / "tests/fixtures/models-v1-pre-feature.yml"
    config = tmp_path / "models.yml"
    config.write_bytes(fixture.read_bytes())
    secret = "fixture-private-migration-key"

    first = run("migrate-config", "--config", str(config))

    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["written"] is True
    assert secret not in first.stdout + first.stderr
    migrated = yaml.safe_load(config.read_text())
    assert migrated["runtime"]["parallel_slots"] == 1
    assert migrated["runtime"]["ubatch_size"] == 512
    assert [model["client_max_output_tokens"] for model in migrated["models"]] == [
        8192,
        4096,
    ]
    assert migrated["server"]["api_key"] == secret
    assert migrated["custom_top_level"] == {"retained": True}
    assert oct(config.stat().st_mode & 0o777) == "0o600"

    stat_before = config.stat()
    second = run("migrate-config", "--config", str(config))

    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["written"] is False
    assert secret not in second.stdout + second.stderr
    stat_after = config.stat()
    assert (stat_after.st_ino, stat_after.st_mtime_ns) == (
        stat_before.st_ino,
        stat_before.st_mtime_ns,
    )


def test_migrate_config_rejects_unsafe_default_without_rewriting(tmp_path: Path):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    parsed["models"][0].pop("client_max_output_tokens")
    parsed["models"][0]["ctx_size"] = True
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))
    before = config.read_bytes()

    result = run("migrate-config", "--config", str(config))

    assert result.returncode == 1
    assert "ctx_size must be a positive integer" in json.loads(result.stdout)["error"]
    assert config.read_bytes() == before


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("alias", ["a"], "model at index 0 alias must be a non-empty string"),
        ("alias", {"name": "a"}, "model at index 0 alias must be a non-empty string"),
        ("enabled", "true", "model a enabled must be a Boolean"),
        ("enabled", 1, "model a enabled must be a Boolean"),
    ],
)
@pytest.mark.parametrize(
    "command",
    [
        ("migrate-config",),
        ("budget", "--models-dir", "."),
        ("presets", "--models-dir", "/models", "--device", "all", "--output", "OUTPUT"),
        ("models", "list"),
        ("models", "select", "b"),
        ("models", "enable", "b"),
        ("models", "disable", "a"),
        ("validate-gguf", "--models-dir", "."),
    ],
)
def test_config_commands_report_malformed_model_records_without_traceback(
    tmp_path: Path,
    field: str,
    value: object,
    expected: str,
    command: tuple[str, ...],
):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    parsed["server"]["api_key"] = "fixture-malformed-record-secret"
    parsed["models"][0][field] = value
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))
    before = config.read_bytes()
    output = tmp_path / "presets.ini"
    resolved = tuple(str(output) if value == "OUTPUT" else value for value in command)

    result = run(*resolved, "--config", str(config))

    assert result.returncode == 1
    assert expected in json.loads(result.stdout)["error"]
    assert "Traceback" not in result.stdout + result.stderr
    assert "fixture-malformed-record-secret" not in result.stdout + result.stderr
    assert config.read_bytes() == before
    assert not output.exists()


def test_model_costs_round_weights_up_and_report_cache_components(
    tmp_path, monkeypatch
):
    import llmenv

    model_path = tmp_path / "gemma.gguf"
    model_path.write_bytes(b"x" * (1024 * 1024 + 1))
    cfg = {
        "runtime": {
            "ubatch_size": 512,
            "cache_type_k": "f16",
            "cache_type_v": "f16",
        },
        "models": [
            {
                "alias": "gemma",
                "enabled": True,
                "file": model_path.name,
                "ctx_size": 131072,
            }
        ],
    }
    metadata = {
        "general.architecture": "gemma4",
        "gemma4.block_count": 1,
        "gemma4.attention.head_count_kv": 2,
        "gemma4.attention.key_length": 128,
        "gemma4.attention.value_length": 128,
        "gemma4.attention.sliding_window": 257,
        "gemma4.attention.sliding_window_pattern": [True],
        "gemma4.attention.key_length_swa": 64,
        "gemma4.attention.value_length_swa": 96,
    }
    monkeypatch.setattr(
        llmenv, "read_gguf_header", lambda path: {"metadata": metadata}
    )

    assert llmenv._model_costs(cfg, tmp_path) == [
        {
            "alias": "gemma",
            "weights_mib": 2,
            "ram_weights_mib": 0,
            "full_kv_mib": 0,
            "swa_kv_mib": 1,
            "kv_mib": 1,
        }
    ]


def test_model_costs_splits_weights_for_moe_offload(tmp_path, monkeypatch):
    import llmenv

    model_path = tmp_path / "moe.gguf"
    model_path.write_bytes(b"x" * (10 * 1024 * 1024))  # 10 MiB total file
    cfg = {
        "runtime": {"ubatch_size": 512, "cache_type_k": "f16", "cache_type_v": "f16"},
        "models": [
            {
                "alias": "moe",
                "enabled": True,
                "file": model_path.name,
                "ctx_size": 4096,
                "n_cpu_moe": 2,
            }
        ],
    }
    metadata = {
        "general.architecture": "qwen35moe",
        "qwen35moe.block_count": 1,
        "qwen35moe.expert_count": 8,
        "qwen35moe.attention.head_count_kv": 1,
        "qwen35moe.attention.key_length": 1,
        "qwen35moe.attention.value_length": 1,
        "qwen35moe.full_attention_interval": 1,
    }
    monkeypatch.setattr(llmenv, "read_gguf_header", lambda path: {"metadata": metadata})
    monkeypatch.setattr(
        llmenv, "moe_expert_offload_mib", lambda path, metadata, n: 3
    )

    result = llmenv._model_costs(cfg, tmp_path)
    assert result[0]["ram_weights_mib"] == 3
    assert result[0]["weights_mib"] == 10 - 3


def test_model_costs_calls_moe_offload_even_when_n_cpu_moe_is_zero(tmp_path, monkeypatch):
    """A model with n_cpu_moe explicitly set to 0 must still route through
    moe_expert_offload_mib -- that function validates the model actually has
    routed experts, which is how a dense model with a stray n_cpu_moe: 0
    gets caught, rather than silently ignored because 0 is falsy."""
    import llmenv

    model_path = tmp_path / "moe.gguf"
    model_path.write_bytes(b"x" * (10 * 1024 * 1024))
    cfg = {
        "runtime": {"ubatch_size": 512, "cache_type_k": "f16", "cache_type_v": "f16"},
        "models": [
            {
                "alias": "moe",
                "enabled": True,
                "file": model_path.name,
                "ctx_size": 4096,
                "n_cpu_moe": 0,
            }
        ],
    }
    metadata = {
        "general.architecture": "qwen35moe",
        "qwen35moe.block_count": 1,
        "qwen35moe.expert_count": 8,
        "qwen35moe.attention.head_count_kv": 1,
        "qwen35moe.attention.key_length": 1,
        "qwen35moe.attention.value_length": 1,
        "qwen35moe.full_attention_interval": 1,
    }
    monkeypatch.setattr(llmenv, "read_gguf_header", lambda path: {"metadata": metadata})
    calls = []
    monkeypatch.setattr(
        llmenv,
        "moe_expert_offload_mib",
        lambda path, metadata, n: calls.append(n) or 0,
    )

    result = llmenv._model_costs(cfg, tmp_path)
    assert calls == [0]
    assert result[0]["ram_weights_mib"] == 0


def test_model_costs_rejects_n_cpu_moe_zero_on_a_non_moe_model(tmp_path, monkeypatch):
    import llmenv

    model_path = tmp_path / "dense.gguf"
    model_path.write_bytes(b"x" * 1024)
    cfg = {
        "runtime": {"ubatch_size": 512, "cache_type_k": "f16", "cache_type_v": "f16"},
        "models": [
            {
                "alias": "dense",
                "enabled": True,
                "file": model_path.name,
                "ctx_size": 4096,
                "n_cpu_moe": 0,
            }
        ],
    }
    metadata = {
        "general.architecture": "llama",
        "llama.block_count": 1,
        "llama.attention.head_count_kv": 1,
        "llama.attention.key_length": 1,
        "llama.attention.value_length": 1,
    }
    monkeypatch.setattr(llmenv, "read_gguf_header", lambda path: {"metadata": metadata})

    with pytest.raises(llmenv.GgufError, match="no routed experts"):
        llmenv._model_costs(cfg, tmp_path)


def test_cmd_budget_reports_infeasible_with_an_achievable_remedy_percentage(
    tmp_path, monkeypatch, capsys
):
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    cfg["resources"] = {
        "llm_server": {
            "memory_ceiling_pct": 10,
            "memory_ceiling_floor_pct": 10,
            "cpu_ceiling_pct": 60,
            "cpu_ceiling_floor_pct": 20,
        }
    }
    facts = {
        "compositor_render_node": "/dev/dri/renderD128",
        "gpus": [
            {
                "pci_address": "0000:03:00.0",
                "render_node": "/dev/dri/renderD129",
                "vram_total_mib": 16304,
                "vram_used_mib": 200,
            }
        ],
    }
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "detect", lambda: facts)
    monkeypatch.setattr(
        llmenv, "host_resources", lambda: {"cpu_count": 24, "memory_total_mib": 32768}
    )
    monkeypatch.setattr(
        llmenv,
        "_model_costs",
        lambda config, path: [
            {"alias": "a", "weights_mib": 100, "ram_weights_mib": 10000, "kv_mib": 0}
        ],
    )

    result = llmenv.cmd_budget(
        cast(Namespace, SimpleNamespace(config="cfg", models_dir="models"))
    )
    output = json.loads(capsys.readouterr().out)

    assert result == 1
    assert output["feasible"] is False
    assert output["ram_required_mib"] == 10000
    assert output["vram_feasible"] is True
    assert output["ram_feasible"] is False
    assert output["ram_shortfall_mib"] == 10000 - output["ram_available_mib"]
    assert any(
        "raise it to at least 31%" in remedy for remedy in output["remedies"]
    )


def test_cmd_budget_reports_infeasible_and_impossible_when_no_pct_could_ever_fit(
    tmp_path, monkeypatch, capsys
):
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    cfg["resources"] = {
        "llm_server": {
            "memory_ceiling_pct": 10,
            "memory_ceiling_floor_pct": 10,
            "cpu_ceiling_pct": 60,
            "cpu_ceiling_floor_pct": 20,
        }
    }
    facts = {
        "compositor_render_node": "/dev/dri/renderD128",
        "gpus": [
            {
                "pci_address": "0000:03:00.0",
                "render_node": "/dev/dri/renderD129",
                "vram_total_mib": 16304,
                "vram_used_mib": 200,
            }
        ],
    }
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "detect", lambda: facts)
    monkeypatch.setattr(
        llmenv, "host_resources", lambda: {"cpu_count": 24, "memory_total_mib": 32768}
    )
    monkeypatch.setattr(
        llmenv,
        "_model_costs",
        lambda config, path: [
            {"alias": "a", "weights_mib": 100, "ram_weights_mib": 100000, "kv_mib": 0}
        ],
    )

    result = llmenv.cmd_budget(
        cast(Namespace, SimpleNamespace(config="cfg", models_dir="models"))
    )
    output = json.loads(capsys.readouterr().out)

    assert result == 1
    assert output["feasible"] is False
    assert output["vram_feasible"] is True
    assert output["ram_feasible"] is False
    assert output["ram_required_mib"] == 100000
    remedy = next(r for r in output["remedies"] if "memory_ceiling_pct" in r and "cpu_ceiling_pct" not in r)
    assert "%" not in remedy or all(
        int(token.rstrip("%")) <= 100
        for token in remedy.split()
        if token.rstrip("%,.").isdigit() and token.endswith("%")
    )
    assert "no resources.llm_server.memory_ceiling_pct value can fit" in remedy


def test_cmd_budget_catches_ram_infeasibility_from_a_model_the_vram_budget_would_not_pick(
    tmp_path, monkeypatch, capsys
):
    """resident_models (compute_budget's VRAM-ranked top models_max models)
    is the wrong set to sum/max RAM need over: a model that's cheap in VRAM
    (small weights_mib) but expensive in offloaded RAM (large
    ram_weights_mib) can lose the VRAM ranking to a big dense model yet
    still be an enabled model a user could select and run. The RAM check
    must cover every enabled model (compute_budget's full "models" list),
    not just the VRAM-ranked resident subset."""
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    cfg["runtime"]["models_max"] = 1
    cfg["resources"] = {
        "llm_server": {
            "memory_ceiling_pct": 10,
            "memory_ceiling_floor_pct": 10,
            "cpu_ceiling_pct": 60,
            "cpu_ceiling_floor_pct": 20,
        }
    }
    facts = {
        "compositor_render_node": "/dev/dri/renderD128",
        "gpus": [
            {
                "pci_address": "0000:03:00.0",
                "render_node": "/dev/dri/renderD129",
                "vram_total_mib": 16304,
                "vram_used_mib": 200,
            }
        ],
    }
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "detect", lambda: facts)
    monkeypatch.setattr(
        llmenv, "host_resources", lambda: {"cpu_count": 24, "memory_total_mib": 32768}
    )
    monkeypatch.setattr(
        llmenv,
        "_model_costs",
        lambda config, path: [
            {"alias": "dense", "weights_mib": 1000, "ram_weights_mib": 0, "kv_mib": 100},
            {"alias": "moe", "weights_mib": 50, "ram_weights_mib": 100000, "kv_mib": 10},
        ],
    )

    result = llmenv.cmd_budget(
        cast(Namespace, SimpleNamespace(config="cfg", models_dir="models"))
    )
    output = json.loads(capsys.readouterr().out)

    assert result == 1
    assert output["feasible"] is False
    assert output["vram_feasible"] is True
    assert output["ram_feasible"] is False
    assert output["ram_required_mib"] == 100000
    assert any("moe" in remedy for remedy in output["remedies"])


def test_cmd_budget_reports_infeasible_when_the_host_cant_reserve_the_fixed_floors(
    tmp_path, monkeypatch, capsys
):
    """A standalone `llmenv budget` call must report failed fixed floors."""
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    cfg["runtime"]["models_max"] = 1
    cfg["resources"] = {"llm_server": {}}
    facts = {
        "compositor_render_node": "/dev/dri/renderD128",
        "gpus": [
            {
                "pci_address": "0000:03:00.0",
                "render_node": "/dev/dri/renderD129",
                "vram_total_mib": 16304,
                "vram_used_mib": 200,
            }
        ],
    }
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "detect", lambda: facts)
    monkeypatch.setattr(
        llmenv, "host_resources", lambda: {"cpu_count": 3, "memory_total_mib": 32768}
    )
    monkeypatch.setattr(
        llmenv,
        "_model_costs",
        lambda config, path: [
            {"alias": "dense", "weights_mib": 50, "ram_weights_mib": 0, "kv_mib": 10},
        ],
    )

    result = llmenv.cmd_budget(
        cast(Namespace, SimpleNamespace(config="cfg", models_dir="models"))
    )
    output = json.loads(capsys.readouterr().out)

    assert result == 1
    assert output["feasible"] is False
    assert output["ram_feasible"] is False
    assert output["ram_available_mib"] is None
    assert any(
        "cannot reserve the fixed" in remedy for remedy in output["remedies"]
    )


def test_cmd_budget_system_one_lowers_ram_available_mib(tmp_path, monkeypatch, capsys):
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    cfg.setdefault("resources", {}).setdefault("llm_server", {})["memory_ceiling_pct"] = 100
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(
        llmenv,
        "detect",
        lambda: {
            "compositor_render_node": "renderD128",
            "gpus": [
                {
                    "pci_address": "0000:03:00.0",
                    "render_node": "renderD129",
                    "vram_total_mib": 16304,
                    "vram_used_mib": 0,
                }
            ],
        },
    )
    monkeypatch.setattr(
        llmenv, "host_resources", lambda: {"cpu_count": 16, "memory_total_mib": 32768}
    )
    monkeypatch.setattr(
        llmenv,
        "_model_costs",
        lambda c, p: [{"alias": "a", "weights_mib": 100, "ram_weights_mib": 0, "kv_mib": 0}],
    )
    ns = cast(Namespace, SimpleNamespace(config="cfg", models_dir="m"))
    llmenv.cmd_budget(ns)
    off = json.loads(capsys.readouterr().out)["ram_available_mib"]
    cfg["system_one"] = {
        "enabled": True,
        "port": 8010,
        "image": "localhost/llm-env-system-one:rocm",
        "models": "english",
        "vram_reserve_mib": 4096,
    }
    llmenv.cmd_budget(ns)
    assert json.loads(capsys.readouterr().out)["ram_available_mib"] < off


def test_cmd_budget_sums_ram_across_concurrently_resident_models(
    tmp_path, monkeypatch, capsys
):
    """Top RAM-ranked models can be concurrently resident and must be summed."""
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    cfg["runtime"]["models_max"] = 2
    cfg["models"][1]["enabled"] = True
    cfg["resources"] = {
        "llm_server": {
            "memory_ceiling_pct": 10,
            "memory_ceiling_floor_pct": 10,
            "cpu_ceiling_pct": 60,
            "cpu_ceiling_floor_pct": 20,
        }
    }
    facts = {
        "compositor_render_node": "/dev/dri/renderD128",
        "gpus": [
            {
                "pci_address": "0000:03:00.0",
                "render_node": "/dev/dri/renderD129",
                "vram_total_mib": 16304,
                "vram_used_mib": 200,
            }
        ],
    }
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "detect", lambda: facts)
    monkeypatch.setattr(
        llmenv, "host_resources", lambda: {"cpu_count": 24, "memory_total_mib": 32768}
    )
    monkeypatch.setattr(
        llmenv,
        "_model_costs",
        lambda config, path: [
            {"alias": "moe-a", "weights_mib": 50, "ram_weights_mib": 2000, "kv_mib": 10},
            {"alias": "moe-b", "weights_mib": 50, "ram_weights_mib": 2000, "kv_mib": 10},
        ],
    )

    result = llmenv.cmd_budget(
        cast(Namespace, SimpleNamespace(config="cfg", models_dir="models"))
    )
    output = json.loads(capsys.readouterr().out)

    assert result == 1
    assert output["feasible"] is False
    assert output["ram_feasible"] is False
    assert output["ram_required_mib"] == 4000
    assert any(
        "moe-a" in remedy and "moe-b" in remedy for remedy in output["remedies"]
    )


def test_resources_cpu_ceiling_caps_llm_server_cpus(tmp_path: Path):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    llm_server_resources = parsed.setdefault("resources", {}).setdefault("llm_server", {})
    llm_server_resources["cpu_ceiling_pct"] = 10
    llm_server_resources["cpu_ceiling_floor_pct"] = 1
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))

    result = run("resources", "--config", str(config))
    payload = json.loads(result.stdout)
    if result.returncode == 0:
        host_cpu_count = payload["host"]["cpu_count"]
        uncapped = (
            host_cpu_count
            - payload["host_cpu_floor"]
            - payload["omniroute"]["cpus_min"]
            - payload["remote_setup"]["cpus_min"]
        )
        expected_ceiling = max(
            1, round(host_cpu_count * 1 / 100), round(host_cpu_count * 10 / 100)
        )
        expected_cpus = min(uncapped, expected_ceiling)
        assert payload["llm_server"]["cpus"] == expected_cpus
        if expected_ceiling < uncapped:
            assert payload["llm_server"]["cpus"] < uncapped
    else:
        assert result.returncode == 1
        assert "error" in payload


def test_cmd_budget_passes_configured_models_max(tmp_path, monkeypatch):
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    facts = {
        "compositor_render_node": "/dev/dri/renderD128",
        "gpus": [
            {
                "pci_address": "0000:03:00.0",
                "render_node": "/dev/dri/renderD129",
                "vram_total_mib": 16304,
                "vram_used_mib": 200,
            }
        ],
    }
    captured = {}

    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "detect", lambda: facts)
    monkeypatch.setattr(llmenv, "_model_costs", lambda config, path: [{"alias": "a"}])

    def fake_compute_budget(**kwargs):
        captured.update(kwargs)
        return {"feasible": True}

    monkeypatch.setattr(llmenv, "compute_budget", fake_compute_budget)

    result = llmenv.cmd_budget(
        cast(Namespace, SimpleNamespace(config="cfg", models_dir="models"))
    )

    assert result == 0
    assert captured["models_max"] == 1


def test_budget_respects_the_configured_vram_ceiling(tmp_path, monkeypatch):
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    cfg["gpu"]["vram_budget_ceiling_mib"] = 100
    facts = {
        "compositor_render_node": "/dev/dri/renderD128",
        "gpus": [
            {
                "pci_address": "0000:03:00.0",
                "render_node": "/dev/dri/renderD129",
                "vram_total_mib": 16304,
                "vram_used_mib": 200,
            }
        ],
    }
    captured = {}

    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "detect", lambda: facts)
    monkeypatch.setattr(llmenv, "_model_costs", lambda config, path: [{"alias": "a"}])

    def fake_compute_budget(**kwargs):
        captured.update(kwargs)
        return {"feasible": True, "available_mib": 100}

    monkeypatch.setattr(llmenv, "compute_budget", fake_compute_budget)

    result = llmenv.cmd_budget(
        cast(Namespace, SimpleNamespace(config="cfg", models_dir="models"))
    )

    assert result == 0
    assert captured["vram_budget_ceiling_mib"] == 100


def test_budget_treats_a_configured_zero_vram_ceiling_as_uncapped(tmp_path, monkeypatch):
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    cfg["gpu"]["vram_budget_ceiling_mib"] = 0
    facts = {
        "compositor_render_node": "/dev/dri/renderD128",
        "gpus": [
            {
                "pci_address": "0000:03:00.0",
                "render_node": "/dev/dri/renderD129",
                "vram_total_mib": 16304,
                "vram_used_mib": 200,
            }
        ],
    }
    captured = {}

    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "detect", lambda: facts)
    monkeypatch.setattr(llmenv, "_model_costs", lambda config, path: [{"alias": "a"}])

    def fake_compute_budget(**kwargs):
        captured.update(kwargs)
        return {"feasible": True, "available_mib": 100}

    monkeypatch.setattr(llmenv, "compute_budget", fake_compute_budget)

    result = llmenv.cmd_budget(
        cast(Namespace, SimpleNamespace(config="cfg", models_dir="models"))
    )

    assert result == 0
    # A configured 0 must reach compute_budget() as None (uncapped), never
    # as a literal 0 — passing 0 straight through would make
    # compute_budget()'s min(vram_total_mib, 0) == 0, producing a negative
    # available_mib instead of the documented "no cap" behavior.
    assert captured["vram_budget_ceiling_mib"] is None


def test_classify_transcript_emits_an_excerpt(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"error": "boom"}) + "\n")
    result = run(
        "classify-transcript",
        "--client", "pi",
        "--transcript", str(transcript),
    )
    assert result.returncode == 0, result.stderr
    assert "boom" in json.loads(result.stdout)["excerpt"]


def test_resources_emits_host_and_llm_server_limits(tmp_path: Path):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    llm_server_resources = parsed.setdefault("resources", {}).setdefault("llm_server", {})
    llm_server_resources["memory_ceiling_pct"] = 10
    llm_server_resources["memory_ceiling_floor_pct"] = 1
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))

    result = run("resources", "--config", str(config))
    payload = json.loads(result.stdout)
    if result.returncode == 0:
        assert payload["host"]["cpu_count"] > 0
        assert payload["llm_server"]["cpus"] >= 0
        host_total_mib = payload["host"]["memory_total_mib"]
        # floor pinned to 1% of host total (below the 10% ceiling pct) so
        # this test proves memory_ceiling_pct reached
        # compute_resource_limits() and isn't masked by the floor.
        expected_ceiling_mib = max(
            round(host_total_mib * 1 / 100), round(host_total_mib * 10 / 100)
        )
        assert payload["llm_server"]["memory_mib"] <= expected_ceiling_mib
        # A pct this low always caps below the uncapped remainder on any
        # host that clears the floor checks, so this also proves the
        # config value reached compute_resource_limits() rather than the
        # implementation silently defaulting to pct=100.
        assert payload["llm_server"]["memory_mib"] == expected_ceiling_mib
    else:
        assert result.returncode == 1
        assert "error" in payload


def test_phoenix_resource_policy_is_charged_only_with_system_one():
    from pylib.resources import (
        PHOENIX_CPUS_MAX,
        PHOENIX_CPUS_MIN,
        PHOENIX_MEMORY_MAX_MIB,
        PHOENIX_MEMORY_MIN_MIB,
        compute_resource_limits,
    )

    policy = {
        "cpus_min": PHOENIX_CPUS_MIN,
        "cpus_max": PHOENIX_CPUS_MAX,
        "memory_min_mib": PHOENIX_MEMORY_MIN_MIB,
        "memory_max_mib": PHOENIX_MEMORY_MAX_MIB,
    }
    enabled = compute_resource_limits(16, 65536, system_one_enabled=True)
    disabled = compute_resource_limits(16, 65536, system_one_enabled=False)
    # llm-server-disabled (OmniRoute gateway) mode still charges Phoenix
    # whenever System One is enabled.
    gateway = compute_resource_limits(
        16, 65536, llm_server_enabled=False, system_one_enabled=True
    )

    assert enabled["phoenix"] == policy
    assert gateway["phoenix"] == policy
    assert disabled["phoenix"] == {
        "cpus_min": 0,
        "cpus_max": 0,
        "memory_min_mib": 0,
        "memory_max_mib": 0,
    }
    # Phoenix's soft reservation (together with System One's) shrinks the
    # llm-server remainder of the same 16-CPU/64-GiB host.
    from pylib.resources import (
        HOST_CPU_FLOOR,
        HOST_MEMORY_FLOOR_MIB,
        SYSTEM_ONE_CPUS_MIN,
        SYSTEM_ONE_MEMORY_MIN_MIB,
    )

    expected_cpu_floor = (
        HOST_CPU_FLOOR + 1 + 0.25 + SYSTEM_ONE_CPUS_MIN + PHOENIX_CPUS_MIN
    )
    expected_memory_floor = (
        HOST_MEMORY_FLOOR_MIB + 768 + 32 + SYSTEM_ONE_MEMORY_MIN_MIB + PHOENIX_MEMORY_MIN_MIB
    )
    assert enabled["llm_server"]["cpus"] == 16 - expected_cpu_floor
    assert enabled["llm_server"]["memory_mib"] == 65536 - expected_memory_floor


def test_phoenix_reservation_can_make_a_small_host_infeasible():
    from pylib.resources import ResourceError, compute_resource_limits

    # 4 CPUs / 6 GiB RAM: enough for the host floor + OmniRoute +
    # remote-setup + System One, but not once the Phoenix reservation is
    # added -- the error must name Phoenix, not just "the floor".
    with pytest.raises(ResourceError, match="Phoenix"):
        compute_resource_limits(4, 6144, llm_server_enabled=False, system_one_enabled=True)


def test_migrate_config_drops_the_generated_resources_mirror(tmp_path: Path):
    """`resources.<service>` was a generated copy nothing read; a stale one (even an invalid one) is dropped."""
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    parsed.setdefault("resources", {})["phoenix"] = {"cpus_min": 4, "cpus_max": 1}
    parsed["resources"]["omniroute"] = {"cpus_min": 1, "cpus_max": 4, "memory_min_mib": 1024, "memory_max_mib": 3072}
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))
    result = run("migrate-config", "--config", str(config))
    assert result.returncode == 0, result.stdout + result.stderr
    resources = yaml.safe_load(config.read_text())["resources"]
    assert "phoenix" not in resources and "omniroute" not in resources
    assert "llm_server" in resources


def test_resources_rejects_a_phoenix_override_with_min_above_max(tmp_path: Path):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    parsed.setdefault("resources", {})["overrides"] = {"phoenix": {"cpus_min": 4}}
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))

    result = run("resources", "--config", str(config))
    assert result.returncode == 1
    assert "resources.overrides.phoenix" in result.stdout + result.stderr


def test_resources_emits_phoenix_policy_when_system_one_enabled(tmp_path: Path):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    parsed["system_one"] = {
        "enabled": True,
        "port": 8010,
        "image": "localhost/llm-env-system-one:rocm",
        "models": "english",
        "vram_reserve_mib": 4096,
    }
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))

    result = run("resources", "--config", str(config))
    payload = json.loads(result.stdout)
    if result.returncode != 0:
        # A genuinely tiny host is allowed to refuse the reservation.
        assert "error" in payload
        return
    assert payload["phoenix"]["memory_min_mib"] == 192
    assert payload["phoenix"]["memory_max_mib"] == 1024
    assert payload["phoenix"]["cpus_min"] == 0.25
    assert payload["phoenix"]["cpus_max"] == 1


def test_resources_respects_the_configured_memory_ceiling_floor(tmp_path: Path):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    llm_server_resources = parsed.setdefault("resources", {}).setdefault("llm_server", {})
    # A pct tiny enough that, unfloored, it would round to far below the
    # configured floor -- this only passes if the floor is actually read
    # from config rather than defaulting to the 20% code-level fallback.
    llm_server_resources["memory_ceiling_pct"] = 0.001
    llm_server_resources["memory_ceiling_floor_pct"] = 30
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))

    result = run("resources", "--config", str(config))
    payload = json.loads(result.stdout)
    if result.returncode == 0:
        # Computed relative to the real host's own reported total (like the
        # neighboring test above), not a bare fixed MiB value -- on a
        # small-but-valid CI host, host_total_mib - memory_floor_mib can be
        # below the floor's derived MiB value, in which case min() keeps the
        # ordinary remainder, not the floor.
        host_total_mib = payload["host"]["memory_total_mib"]
        memory_floor_mib = (
            payload["host_memory_floor_mib"]
            + payload["omniroute"]["memory_min_mib"]
            + payload["remote_setup"]["memory_min_mib"]
        )
        floored_ceiling_mib = round(host_total_mib * 30 / 100)
        expected_ceiling_mib = min(host_total_mib - memory_floor_mib, floored_ceiling_mib)
        assert payload["llm_server"]["memory_mib"] == expected_ceiling_mib
    else:
        assert result.returncode == 1
        assert "error" in payload


class _RecordingProviderHandler(http.server.BaseHTTPRequestHandler):
    received: ClassVar[list[tuple]] = []

    def _reply(self, payload, status=200, *, set_cookie=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie is not None:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.received.append(("GET", self.path, self.headers.get("Cookie")))
        self._reply({"connections": []})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        if self.path == "/api/auth/login":
            self.received.append(("POST", self.path, body))
            self._reply({"success": True}, set_cookie="auth_token=session-token; Path=/; HttpOnly")
            return
        self.received.append(("POST", self.path, body))
        self._reply({"connection": {"id": "created-id"}})

    def log_message(self, format_string, *args):
        pass


def _write_omniroute_test_config(
    tmp_path: Path, *, omniroute_port: int, initial_password: str
) -> Path:
    config = tmp_path / "models.yml"
    config.write_text(
        "version: 1\n"
        "server: {host: 0.0.0.0, port: 8000, api_key: routerkey, mdns_name: llm,"
        " sleep_idle_seconds: 300}\n"
        "gpu: {pci_address: '0000:03:00.0', device_name: d, backend: vulkan,"
        " image: i, vram_total_mib: 16304, reserve_mode: auto, reserve_floor_mib: 1024}\n"
        "runtime: {models_max: 1, parallel_slots: 1, ubatch_size: 512,"
        " flash_attn: true, cache_type_k: q8_0, cache_type_v: q8_0}\n"
        f"omniroute: {{image: i, port: {omniroute_port}, initial_password: {initial_password}}}\n"
        "models:\n"
        "  - {alias: a, label: A, parameters: 1B, quantization: Q4_K_M, enabled: true,"
        " file: a.gguf, url: u, size_bytes: 1, vram_budget: 10%, ctx_size: 4096,"
        " client_max_output_tokens: 4096, n_gpu_layers: 99}\n"
    )
    return config


def test_omniroute_provision_creates_a_connection_via_the_admin_api(tmp_path):
    _RecordingProviderHandler.received = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = _write_omniroute_test_config(
            tmp_path, omniroute_port=server.server_address[1], initial_password="dashboard-pw"
        )
        result = run("--config", str(config), "omniroute", "provision")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"action": "created", "id": "created-id"}
        assert _RecordingProviderHandler.received[0] == (
            "POST",
            "/api/auth/login",
            {"password": "dashboard-pw"},
        )
        assert _RecordingProviderHandler.received[1] == (
            "GET",
            "/api/providers",
            "auth_token=session-token",
        )
        method, path, body = _RecordingProviderHandler.received[2]
        assert method == "POST"
        assert path == "/api/providers"
        assert body["providerSpecificData"]["baseUrl"] == "http://llm-server:8000/v1"
        assert body["apiKey"] == "routerkey"
    finally:
        server.shutdown()
        thread.join()


def test_omniroute_provision_fails_cleanly_without_a_dashboard_password(tmp_path):
    config = _write_omniroute_test_config(tmp_path, omniroute_port=20128, initial_password='""')
    result = run("--config", str(config), "omniroute", "provision")
    assert result.returncode == 1
    assert "initial_password" in json.loads(result.stdout)["error"]


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


class _RecordingCodexImportHandler(http.server.BaseHTTPRequestHandler):
    received: ClassVar[list[tuple[str, str, dict[str, Any]]]] = []

    def _reply(self, payload, *, set_cookie=None):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie is not None:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.received.append(("GET", self.path, {}))
        if self.path == "/api/providers":
            self._reply({"connections": []})
            return
        self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        if self.path == "/api/auth/login":
            self.received.append(("POST", self.path, body))
            self._reply({"success": True}, set_cookie="auth_token=session-token; Path=/; HttpOnly")
            return
        self.received.append(("POST", self.path, body))
        self._reply({"created": True, "connection": {"id": "codex-id", "name": body["name"]}})

    def log_message(self, format_string, *args):
        pass


def _cli_id_token_with_email(email: str) -> str:
    import base64

    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


def test_omniroute_import_codex_posts_the_local_auth_file(tmp_path):
    _RecordingCodexImportHandler.received = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingCodexImportHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = _write_omniroute_test_config(
            tmp_path, omniroute_port=server.server_address[1], initial_password="dashboard-pw"
        )
        id_token = _cli_id_token_with_email("castocolina.cl@gmail.com")
        auth_path = tmp_path / "codex-auth.json"
        auth_path.write_text(
            json.dumps({"auth_mode": "chatgpt", "tokens": {"id_token": id_token}})
        )
        result = run(
            "--config", str(config),
            "omniroute", "import-codex",
            "--auth-path", str(auth_path),
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            "action": "created",
            "id": "codex-id",
            "name": "cco-cl",
            "email": "castocolina.cl@gmail.com",
        }
        # login POST, providers GET, then import POST
        method, path, body = _RecordingCodexImportHandler.received[2]
        assert method == "POST"
        assert path == "/api/providers/codex-auth/import"
        assert body["name"] == "cco-cl"
        assert body["overwriteExisting"] is True
        assert body["source"] == {
            "kind": "json",
            "json": {"auth_mode": "chatgpt", "tokens": {"id_token": id_token}},
        }
    finally:
        server.shutdown()
        thread.join()


def test_omniroute_import_codex_fails_cleanly_when_auth_file_is_missing(tmp_path):
    config = _write_omniroute_test_config(
        tmp_path, omniroute_port=20128, initial_password="dashboard-pw"
    )
    result = run(
        "--config", str(config),
        "omniroute", "import-codex",
        "--auth-path", str(tmp_path / "missing.json"),
    )
    assert result.returncode != 0
    assert "no Codex session" in json.loads(result.stdout)["error"]


class _RecordingFixContextHandler(http.server.BaseHTTPRequestHandler):
    received: ClassVar[list[tuple[str, str, object]]] = []

    def _reply(self, payload, *, set_cookie=None):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie is not None:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        self.received.append(("POST", self.path, body))
        self._reply({"success": True}, set_cookie="auth_token=session-token; Path=/; HttpOnly")

    def do_GET(self):
        self.received.append(("GET", self.path, None))
        self._reply(
            {
                "object": "list",
                "data": [
                    {
                        "id": "cx/gpt-5.6-sol-high",
                        "root": "gpt-5.6-sol-high",
                        "owned_by": "codex",
                        "context_length": 272000,
                    },
                    {
                        "id": "cx/gpt-5.4",
                        "root": "gpt-5.4",
                        "owned_by": "codex",
                        "context_length": 400000,
                    },
                ],
            }
        )

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        self.received.append(("PUT", self.path, body))
        self._reply({"ok": True})

    def log_message(self, format_string, *args):
        pass


def test_omniroute_fix_codex_context_corrects_the_gpt_5_6_family(tmp_path):
    _RecordingFixContextHandler.received = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingFixContextHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = _write_omniroute_test_config(
            tmp_path, omniroute_port=server.server_address[1], initial_password="dashboard-pw"
        )
        result = run("--config", str(config), "omniroute", "fix-codex-context")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            "fixed": [
                {
                    "model": "gpt-5.6-sol-high",
                    "family": "gpt-5.6-sol",
                    "context_window": 1_050_000,
                }
            ]
        }
        puts = [r for r in _RecordingFixContextHandler.received if r[0] == "PUT"]
        assert len(puts) == 1
        assert puts[0][1] == "/api/provider-models"
        assert puts[0][2] == {
            "provider": "codex",
            "modelId": "gpt-5.6-sol-high",
            "contextWindowOverride": 1_050_000,
        }
    finally:
        server.shutdown()
        thread.join()


class _RecordingComboContextHandler(http.server.BaseHTTPRequestHandler):
    received: ClassVar[list[tuple[str, str, object]]] = []

    def _reply(self, payload, *, set_cookie=None):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie is not None:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        self.received.append(("POST", self.path, body))
        self._reply({"success": True}, set_cookie="auth_token=session-token; Path=/; HttpOnly")

    def do_GET(self):
        self.received.append(("GET", self.path, None))
        if self.path == "/v1/models":
            self._reply(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "cx/gpt-5.6-sol-high",
                            "root": "gpt-5.6-sol-high",
                            "owned_by": "codex",
                            "context_length": 272000,
                            "max_output_tokens": 128000,
                        },
                        {
                            "id": "opencode-go/kimi-k2.7-code",
                            "root": "kimi-k2.7-code",
                            "owned_by": "opencode-go",
                            "context_length": 262144,
                            "max_output_tokens": 262144,
                        },
                    ],
                }
            )
            return
        assert self.path == "/api/combos"
        self._reply(
            {
                "combos": [
                    {
                        "name": "my-coding",
                        "models": [
                            {
                                "kind": "model",
                                "providerId": "codex",
                                "model": "codex/gpt-5.6-sol-high",
                            },
                            {
                                "kind": "model",
                                "providerId": "grok-cli",
                                "model": "grok-cli/grok-composer-2.5-fast",
                            },
                            {
                                "kind": "model",
                                "providerId": "opencode-go",
                                "model": "opencode-go/kimi-k2.7-code",
                            },
                        ],
                    }
                ]
            }
        )

    def log_message(self, format_string, *args):
        pass


def test_omniroute_combo_context_reports_the_true_minimum(tmp_path):
    _RecordingComboContextHandler.received = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingComboContextHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = _write_omniroute_test_config(
            tmp_path, omniroute_port=server.server_address[1], initial_password="dashboard-pw"
        )
        result = run("--config", str(config), "omniroute", "combo-context")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            "combos": [
                {
                    "combo": "my-coding",
                    "members": [
                        {
                            "provider": "codex",
                            "model": "gpt-5.6-sol-high",
                            "context_window": 1_050_000,
                            "max_output_tokens": 128000,
                        },
                        {
                            "provider": "grok-cli",
                            "model": "grok-composer-2.5-fast",
                            "context_window": 200_000,
                            "max_output_tokens": None,
                        },
                        {
                            "provider": "opencode-go",
                            "model": "kimi-k2.7-code",
                            "context_window": 262144,
                            "max_output_tokens": 262144,
                        },
                    ],
                    "min_context_window": 200_000,
                    "min_max_output_tokens": 128000,
                    "limiting_member": "grok-cli/grok-composer-2.5-fast",
                }
            ]
        }
    finally:
        server.shutdown()
        thread.join()


def test_omniroute_combo_context_dies_clearly_for_unknown_combo(tmp_path):
    _RecordingComboContextHandler.received = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingComboContextHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = _write_omniroute_test_config(
            tmp_path, omniroute_port=server.server_address[1], initial_password="dashboard-pw"
        )
        result = run(
            "--config", str(config), "omniroute", "combo-context", "--combo", "not-a-combo"
        )
        assert result.returncode != 0
        assert "not-a-combo" in json.loads(result.stdout)["error"]
    finally:
        server.shutdown()
        thread.join()


class _RecordingComboBackupRestoreHandler(http.server.BaseHTTPRequestHandler):
    received: ClassVar[list[tuple[str, str, dict[str, Any] | None]]] = []
    combos: ClassVar[list[dict]] = []
    connections: ClassVar[list[dict]] = []

    def _reply(self, payload, *, status=200, set_cookie=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie is not None:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        self.received.append(("POST", self.path, body))
        if self.path == "/api/auth/login":
            self._reply({"success": True}, set_cookie="auth_token=session-token; Path=/; HttpOnly")
            return
        assert self.path == "/api/combos"
        self._reply({**body, "id": f"id-{body['name']}"}, status=201)

    def do_GET(self):
        self.received.append(("GET", self.path, None))
        if self.path == "/api/providers":
            self._reply({"connections": self.connections})
            return
        assert self.path == "/api/combos"
        self._reply({"combos": self.combos, "total": len(self.combos)})

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        self.received.append(("PUT", self.path, body))
        self._reply({**body, "id": self.path.rsplit("/", 1)[-1]})

    def log_message(self, format_string, *args):
        pass


def test_omniroute_backup_combos_writes_a_file(tmp_path):
    _RecordingComboBackupRestoreHandler.received = []
    _RecordingComboBackupRestoreHandler.combos = [
        {
            "name": "my-planning",
            "id": "id-my-planning",
            "strategy": "priority",
            "models": [{"kind": "model", "providerId": "grok-cli", "model": "grok-cli/grok-4.6"}],
            "isHidden": False,
            "computed_context_length": 500000,
        }
    ]
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), _RecordingComboBackupRestoreHandler
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = _write_omniroute_test_config(
            tmp_path, omniroute_port=server.server_address[1], initial_password="dashboard-pw"
        )
        output = tmp_path / "backup.json"
        result = run(
            "--config", str(config), "omniroute", "backup-combos", "--output", str(output)
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"path": str(output), "count": 1}
        backup = json.loads(output.read_text())
        assert backup["combos"] == [
            {
                "name": "my-planning",
                "strategy": "priority",
                "models": [
                    {"kind": "model", "providerId": "grok-cli", "model": "grok-cli/grok-4.6"}
                ],
            }
        ]
    finally:
        server.shutdown()
        thread.join()


def test_omniroute_restore_combos_creates_and_reports(tmp_path):
    _RecordingComboBackupRestoreHandler.received = []
    _RecordingComboBackupRestoreHandler.combos = []
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), _RecordingComboBackupRestoreHandler
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = _write_omniroute_test_config(
            tmp_path, omniroute_port=server.server_address[1], initial_password="dashboard-pw"
        )
        backup_file = tmp_path / "backup.json"
        backup_file.write_text(
            json.dumps({"combos": [{"name": "my-planning", "strategy": "priority"}]})
        )
        result = run(
            "--config", str(config), "omniroute", "restore-combos", "--input", str(backup_file)
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            "restored": [{"combo": "my-planning", "action": "created"}]
        }
        posts = [r for r in _RecordingComboBackupRestoreHandler.received if r[0] == "POST"]
        assert posts[-1][2] == {"name": "my-planning", "strategy": "priority"}
    finally:
        server.shutdown()
        thread.join()


def test_omniroute_backup_connections_writes_metadata_only(tmp_path):
    _RecordingComboBackupRestoreHandler.received = []
    _RecordingComboBackupRestoreHandler.combos = []
    _RecordingComboBackupRestoreHandler.connections = [
        {"id": "conn-1", "provider": "codex", "name": "cco-cl", "authType": "oauth"},
    ]
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), _RecordingComboBackupRestoreHandler
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = _write_omniroute_test_config(
            tmp_path, omniroute_port=server.server_address[1], initial_password="dashboard-pw"
        )
        output = tmp_path / "connections-backup.json"
        result = run(
            "--config", str(config), "omniroute", "backup-connections", "--output", str(output)
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"path": str(output), "count": 1}
        backup = json.loads(output.read_text())
        assert backup["connections"] == [{"provider": "codex", "name": "cco-cl", "id": "conn-1"}]
    finally:
        server.shutdown()
        thread.join()


def test_omniroute_restore_combos_remaps_connection_id_by_live_provider_and_label(tmp_path):
    _RecordingComboBackupRestoreHandler.received = []
    _RecordingComboBackupRestoreHandler.combos = []
    _RecordingComboBackupRestoreHandler.connections = [
        {"id": "conn-live", "provider": "codex", "name": "cco-cl"},
    ]
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), _RecordingComboBackupRestoreHandler
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = _write_omniroute_test_config(
            tmp_path, omniroute_port=server.server_address[1], initial_password="dashboard-pw"
        )
        backup_file = tmp_path / "backup.json"
        backup_file.write_text(
            json.dumps(
                {
                    "combos": [
                        {
                            "name": "my-planning",
                            "strategy": "priority",
                            "models": [
                                {
                                    "kind": "model",
                                    "providerId": "codex",
                                    "model": "codex/gpt-5.6-sol-high",
                                    "label": "cco-cl",
                                    "connectionId": "conn-stale",
                                }
                            ],
                        }
                    ]
                }
            )
        )
        result = run(
            "--config", str(config), "omniroute", "restore-combos", "--input", str(backup_file)
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            "restored": [{"combo": "my-planning", "action": "created"}]
        }
        posts = [r for r in _RecordingComboBackupRestoreHandler.received if r[0] == "POST"]
        post_body = posts[-1][2]
        assert post_body is not None
        assert post_body["models"][0]["connectionId"] == "conn-live"
    finally:
        server.shutdown()
        thread.join()


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
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["written"] == str(output)
    assert output.exists()
    assert "llm-server" in output.read_text()


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


def test_resources_zeroes_llm_server_when_disabled(tmp_path: Path):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    parsed["llm_server"] = {"enabled": False}
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))

    result = run("resources", "--config", str(config))
    payload = json.loads(result.stdout)
    if result.returncode == 0:
        assert payload["llm_server"] == {"cpus": 0, "memory_mib": 0}
    else:
        assert result.returncode == 1
        assert "error" in payload


# ---------------------------------------------------------------------------
# Phoenix trial CLI contracts
# ---------------------------------------------------------------------------


def test_phoenix_trial_fixture_check_emits_valid_summary():
    result = run(
        "phoenix-trial",
        "fixture-check",
        "--manifest",
        "docker/phoenix-trial/fixtures/laya-synthetic.json",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["status"] == "valid"
    assert payload["fixture_version"] == "laya-synthetic-v1"
    assert payload["summary"]["spans"] == 13
    assert payload["summary"]["median_ms"] == 65


def test_phoenix_trial_fixture_check_rejects_malformed_manifest(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"schema_version": 2}')
    result = run("phoenix-trial", "fixture-check", "--manifest", str(manifest))
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert set(payload) == {"error"}


def test_phoenix_trial_prepare_writes_durable_state(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = run(
        "phoenix-trial",
        "prepare",
        "--manifest",
        "docker/phoenix-trial/fixtures/laya-synthetic.json",
        "--state-dir",
        str(state_dir),
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "prepared"
    assert receipt["project"] == "laya-phoenix-trial"
    run_id = receipt["run_id"]
    assert len(run_id) == 32 and run_id.islower()
    expected_path = Path(receipt["expected_path"])
    assert expected_path.is_file()
    assert (expected_path.parent / "prepared.json").is_file()
    # Deterministic re-derivation: same explicit run id + state fails (exclusive).
    again = run(
        "phoenix-trial",
        "prepare",
        "--manifest",
        "docker/phoenix-trial/fixtures/laya-synthetic.json",
        "--state-dir",
        str(state_dir),
        "--run-id",
        run_id,
    )
    assert again.returncode == 1


def test_phoenix_trial_prepare_rejects_bad_run_id(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = run(
        "phoenix-trial",
        "prepare",
        "--manifest",
        "docker/phoenix-trial/fixtures/laya-synthetic.json",
        "--state-dir",
        str(state_dir),
        "--run-id",
        "NOTHEX",
    )
    assert result.returncode == 1
    assert set(json.loads(result.stdout)) == {"error"}


def test_phoenix_trial_mark_attempt_roundtrip(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    prepared = json.loads(
        run(
            "phoenix-trial",
            "prepare",
            "--manifest",
            "docker/phoenix-trial/fixtures/laya-synthetic.json",
            "--state-dir",
            str(state_dir),
        ).stdout
    )
    result = run(
        "phoenix-trial",
        "mark-attempt",
        "--expected",
        prepared["expected_path"],
    )
    assert result.returncode == 0, result.stderr
    attempt = json.loads(result.stdout)
    assert attempt["run_id"] == prepared["run_id"]
    assert attempt["post_may_have_started"] is True
    second = run(
        "phoenix-trial",
        "mark-attempt",
        "--expected",
        prepared["expected_path"],
    )
    assert second.returncode == 1


def test_phoenix_trial_missing_arguments_exit_2():
    result = run("phoenix-trial")
    assert result.returncode == 2
    result = run("phoenix-trial", "prepare")
    assert result.returncode == 2


def test_phoenix_trial_verify_refuses_symlink_expected(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    prepared = json.loads(
        run(
            "phoenix-trial",
            "prepare",
            "--manifest",
            "docker/phoenix-trial/fixtures/laya-synthetic.json",
            "--state-dir",
            str(state_dir),
        ).stdout
    )
    link = tmp_path / "expected-link.json"
    link.symlink_to(prepared["expected_path"])
    result = run(
        "phoenix-trial",
        "verify",
        "--base-url",
        "https://invalid.example",
        "--run-id",
        prepared["run_id"],
        "--expected",
        str(link),
    )
    assert result.returncode == 1
    assert "symlink" in json.loads(result.stdout)["error"]


def test_phoenix_trial_volume_bytes_cli_counts_files(tmp_path):
    volume = tmp_path / "volume"
    volume.mkdir()
    (volume / "sqlite.db").write_bytes(b"12345")
    result = run("phoenix-trial", "volume-bytes", "--path", str(volume))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"bytes": 5}


def test_phoenix_trial_local_cli_actions_never_open_http(tmp_path, monkeypatch, capsys):
    import urllib.request

    import llmenv

    def forbidden(*args, **kwargs):
        raise AssertionError("local Phoenix trial action attempted HTTP")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    manifest = "docker/phoenix-trial/fixtures/laya-synthetic.json"
    state = tmp_path / "state"
    state.mkdir()

    assert llmenv.main(["phoenix-trial", "fixture-check", "--manifest", manifest]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "valid"
    assert llmenv.main(
        ["phoenix-trial", "prepare", "--manifest", manifest, "--state-dir", str(state)]
    ) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert llmenv.main(
        ["phoenix-trial", "mark-attempt", "--expected", prepared["expected_path"]]
    ) == 0
    attempt = json.loads(capsys.readouterr().out)
    assert attempt["run_id"] == prepared["run_id"]


def test_omniroute_update_resolve_is_registered():
    import llmenv
    args = llmenv.build_parser().parse_args(["omniroute-update", "resolve", "--reference", "x/y:latest",
                                              "--os", "linux", "--arch", "amd64", "--json", "r.json"])
    assert args.func.__name__ == "cmd_resolve" and args.variant == ""


def test_preflight_cli_exit_codes_and_files(tmp_path, capsys):
    from omniroute_fakes import OMNI, PASSWORD, RS, inspect_doc, preflight_world

    from pylib.omniroute_cli import cmd_preflight
    from pylib.omniroute_update import UpdateError
    _cfg, cp, comp, prov, _env, run_backend = preflight_world(tmp_path)
    out = tmp_path / "out"
    out.mkdir(mode=0o700)
    args = Namespace(config=cp, compose=comp, out_dir=str(out), recheck=False, budget=60, service="omniroute")
    assert cmd_preflight(args, run=run_backend, env={"PATH": "/usr/bin", "HOME": "/h"},
                         which=lambda name: prov) == 0
    output = capsys.readouterr().out
    assert "container_id=" in output and "image_id=sha256:" in output and PASSWORD not in output
    for name in ("snapshot.json", "fingerprint.txt", "effective.json", "target.json", "deployment.json"):
        assert (out / name).stat().st_mode & 0o777 == 0o600
    _, _, _, _, _, dependent_run = preflight_world(tmp_path, extra_containers={RS: inspect_doc(RS, deps=[OMNI])})
    assert cmd_preflight(args, run=dependent_run, env={"PATH": "/usr/bin", "HOME": "/h"},
                         which=lambda name: prov) == 75
    assert capsys.readouterr().out == "deferred: ownership\n"
    def failing(argv, timeout=None):
        raise UpdateError("failed")
    # Ruling (Task 2): the plan prose said "returns 1" but its binding reason table maps command
    # failure to Reason.io, a Deferral -> exit 75, matching recheck's UpdateError->io conversion.
    assert cmd_preflight(args, run=failing, env={"PATH": "/usr/bin", "HOME": "/h"},
                         which=lambda name: prov) == 75
    assert capsys.readouterr().out == "deferred: io\n"
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    bad_args = Namespace(**{**vars(args), "out_dir": str(unsafe)})
    assert cmd_preflight(bad_args, run=run_backend, env={"PATH": "/usr/bin", "HOME": "/h"},
                         which=lambda name: prov) == 75
    assert capsys.readouterr().out == "deferred: lock\n"
    assert cmd_preflight(args, run=run_backend,
                         env={"PATH": "/usr/bin", "HOME": "/h", "CONTAINER_HOST": "ssh://x"},
                         which=lambda name: prov) == 75
    assert capsys.readouterr().out == "deferred: input\n"
    recheck_args = Namespace(**{**vars(args), "recheck": True})
    assert cmd_preflight(recheck_args, run=run_backend, env={}, which=lambda name: prov) == 0
    assert capsys.readouterr().out == ""
    _, _, _, _, _, drift_run = preflight_world(tmp_path, extra_containers={RS: inspect_doc(RS)})
    assert cmd_preflight(recheck_args, run=drift_run, env={}, which=lambda name: prov) == 75
    assert capsys.readouterr().out == "deferred: ownership\n"


def test_preflight_recheck_budget_discards_a_slow_success(tmp_path, capsys):
    import time

    from omniroute_fakes import preflight_world

    from pylib.omniroute_cli import cmd_preflight
    from pylib.omniroute_update import preflight, write_preflight_artifacts
    cfg, cp, comp, prov, env, run = preflight_world(tmp_path)
    dep, snap, fp, effective = preflight(cfg, cp, comp, env, run, provider=prov)
    out = tmp_path / "out"; out.mkdir(mode=0o700)
    write_preflight_artifacts(str(out), dep, snap, fp, effective, snap.docs[dep.container_id])
    def slow(argv, timeout=None):
        if argv[:2] == ["podman", "info"]:
            time.sleep(1.2)
        return run(argv, timeout=timeout)
    args = Namespace(config=cp, compose=comp, out_dir=str(out), recheck=True, budget=1, service="omniroute")
    assert cmd_preflight(args, run=slow, env={}, which=lambda name: prov) == 75
    assert capsys.readouterr().out == "deferred: io\n"


def test_overrides_and_deadline_and_preflight_are_registered():
    import llmenv
    p = llmenv.build_parser()
    a = p.parse_args(["omniroute-update", "overrides", "--idle", "60", "--max-wait", "600"])
    assert a.func.__name__ == "cmd_overrides" and a.idle == "60" and a.max_wait == "600"
    b = p.parse_args(["omniroute-update", "deadline", "--file", "f", "--init", "10"])
    assert b.func.__name__ == "cmd_deadline" and b.init == 10
    c = p.parse_args(["omniroute-update", "preflight", "--config", "m.yml", "--compose", "d.yml",
                      "--out-dir", "o", "--recheck", "--budget", "30", "--service", "omniroute"])
    assert c.func.__name__ == "cmd_preflight" and c.recheck and c.budget == 30 and c.service == "omniroute"


def test_lock_path_cli_prints_only_the_path(tmp_path, capsys):
    from pylib.omniroute_cli import cmd_lock_path
    from pylib.omniroute_observer import lock_path
    (tmp_path / "g").mkdir()
    (tmp_path / "r").mkdir()
    doc = json.dumps({"store": {"graphRoot": str(tmp_path / "g"), "runRoot": str(tmp_path / "r")}}).encode()
    args = Namespace(container="omniroute")
    assert cmd_lock_path(args, run=lambda argv, timeout=None: doc) == 0
    out = capsys.readouterr()
    assert out.out == f"{lock_path(lambda argv, timeout=None: doc)}\n" and out.err == ""
    assert cmd_lock_path(args, run=lambda argv, timeout=None: (_ for _ in ()).throw(
        __import__("pylib.omniroute_update", fromlist=["UpdateError"]).UpdateError("boom"))) == 75
    assert capsys.readouterr().err.startswith("deferred: io")
    assert capsys.readouterr().out == ""


def _task6_world(tmp_path):
    import yaml as _yaml
    from omniroute_fakes import NET, omni

    from pylib.omniroute_update import write_preflight_artifacts
    svc = {"image": "docker.io/diegosouzapw/omniroute:latest", "container_name": "omniroute",
           "ports": ["0.0.0.0:20128:20128"], "stop_grace_period": "40s",
           "environment": {"PORT": "20128", "INITIAL_PASSWORD": "pa$$s", "REQUIRE_API_KEY": "true"},
           "volumes": ["omniroute-data:/app/data"], "depends_on": {"x": {}}}
    eff = {"services": {"omniroute": svc}, "networks": {"default": {}}, "volumes": {"omniroute-data": {}}}
    tgt = omni(env=["INITIAL_PASSWORD=pa$s", "PORT=20128", "REQUIRE_API_KEY=true", "NODE_ENV=production"])
    tgt["Mounts"] = [{"Type": "volume", "Name": "llm-env_omniroute-data"}]
    tgt["NetworkSettings"]["Networks"] = {"llm-env_default": {"NetworkID": NET}}
    cfg = {"omniroute": {"port": 20128, "image": "docker.io/diegosouzapw/omniroute:latest",
                         "initial_password": "pa$s"}}
    cp = tmp_path / "models.yml"; cp.write_text(_yaml.safe_dump(cfg))
    comp = tmp_path / "docker-compose.yml"; comp.write_text("services:\n  omniroute: {}\n")
    prov = tmp_path / "podman-compose"; prov.write_bytes(b"#!/bin/sh\n"); prov.chmod(0o755)
    out = tmp_path / "art"; out.mkdir(mode=0o700)
    write_preflight_artifacts(str(out), type("D", (), {"__iter__": lambda s: iter([])})(), None, None, None, None) \
        if False else None
    # build the artifact files directly (deployment.json needs only project/provider for the CLI)
    import json as _json
    dep = {"project": "llm-env", "provider": str(prov)}
    (out / "deployment.json").write_text(_json.dumps(dep))
    (out / "effective.json").write_text(_json.dumps(eff))
    (out / "target.json").write_text(_json.dumps([tgt]))
    image = "docker.io/diegosouzapw/omniroute@sha256:" + "9" * 64
    return str(cp), str(comp), str(out), str(prov), eff, tgt, image


def _provider_run(mutate=lambda doc: doc):
    import yaml as _yaml
    def run(argv, timeout=None):
        assert argv[-1] == "config"
        from pylib.omniroute_update import _unescape
        doc = _unescape(json.loads(Path(argv[argv.index("-f") + 1]).read_text()))
        return _yaml.safe_dump(mutate(doc)).encode()
    return run


def test_verify_staged_cli(tmp_path, capsys):
    import hashlib

    from pylib.omniroute_cli import cmd_verify_staged
    m = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "config": {"digest": "sha256:" + "c" * 64,
                               "mediaType": "application/vnd.oci.image.config.v1+json", "size": 1},
                    "layers": []}).encode()
    _digest = "sha256:" + hashlib.sha256(m).hexdigest()
    resolved = tmp_path / "r.json"
    args = Namespace(reference="docker.io/x/y:latest", os="linux", arch="amd64", variant="", json=str(resolved))
    from pylib.omniroute_cli import cmd_resolve
    assert cmd_resolve(args, run=lambda a, timeout=None: m) == 0
    capsys.readouterr()  # resolve prints its key=value lines; verify-staged itself prints nothing
    good = tmp_path / "i.json"
    good.write_text(json.dumps([{"Id": "c" * 64, "Os": "linux", "Architecture": "amd64", "Variant": ""}]))
    va = Namespace(resolved=str(resolved), inspect=str(good))
    assert cmd_verify_staged(va) == 0
    assert capsys.readouterr().out == ""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"Id": "d" * 64, "Os": "linux", "Architecture": "amd64", "Variant": ""}]))
    assert cmd_verify_staged(Namespace(resolved=str(resolved), inspect=str(bad))) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err


def test_projection_cli_writes_private_file_and_verifies(tmp_path, capsys):
    from pylib.omniroute_cli import cmd_projection
    _, _, out, _, _, _, image = _task6_world(tmp_path)
    projection = tmp_path / "projection.yml"
    args = Namespace(dir=out, service="omniroute", image=image, out=str(projection))
    assert cmd_projection(args, run=_provider_run(), env={"PATH": "/usr/bin", "HOME": "/h"}) == 0
    assert projection.stat().st_mode & 0o777 == 0o600
    assert capsys.readouterr().out == ""
    # a provider that mutates the ports makes the round trip disagree -> 75 ownership
    def mutate(doc):
        doc["services"]["omniroute"]["ports"] = ["0.0.0.0:9999:9999"]
        return doc
    assert cmd_projection(args, run=_provider_run(mutate), env={"PATH": "/usr/bin", "HOME": "/h"}) == 75
    assert capsys.readouterr().out == "deferred: ownership\n"


def test_verify_commit_cli(tmp_path, capsys):
    from omniroute_fakes import NEW, OMNI, SIB, fake_backend, inspect_doc, omni

    from pylib.omniroute_cli import cmd_verify_commit
    from pylib.omniroute_update import take_snapshot, write_private
    snap = take_snapshot(fake_backend({OMNI: omni(), SIB: inspect_doc(SIB, name="sib")}))
    out = tmp_path / "art"; out.mkdir(mode=0o700)
    write_private(str(out / "snapshot.json"), snap.to_json())
    write_private(str(out / "target.json"), json.dumps([omni()]))
    new_doc = inspect_doc(NEW, name="omniroute", image="2" * 64)
    new_doc["Mounts"] = omni()["Mounts"]
    state = {"i": 0}
    def run(argv, timeout=None):
        if argv[:2] == ["podman", "info"]:
            state["i"] += 1
        containers = {OMNI: omni(), SIB: inspect_doc(SIB, name="sib")} if state["i"] <= 1 \
            else {NEW: new_doc, SIB: inspect_doc(SIB, name="sib")}
        return fake_backend(containers)(argv, timeout=timeout)
    args = Namespace(dir=str(out), service="omniroute", old_id=OMNI, config_id="sha256:" + "2" * 64,
                     timeout=5, expect_no_deps=False, password_file=None)
    assert cmd_verify_commit(args, run=run, env={"PATH": "/usr/bin", "HOME": "/h"}) == 0
    assert capsys.readouterr().out.startswith("new_id=")
    args_bad = Namespace(**{**vars(args), "config_id": "sha256:" + "3" * 64})
    assert cmd_verify_commit(args_bad, run=run, env={"PATH": "/usr/bin", "HOME": "/h"}) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err


def _resources_cfg(tmp_path, **overrides):
    from pylib.config import migrate_config

    cfg = migrate_config(yaml.safe_load(write_test_config(tmp_path).read_text()))
    cfg["resources"]["llm_server"]["memory_ceiling_pct"] = 100
    if overrides:
        cfg["resources"]["overrides"] = overrides
    return cfg


def test_cmd_resources_applies_overrides_and_prints_a_summary(tmp_path, monkeypatch, capsys):
    import llmenv

    cfg = _resources_cfg(tmp_path, omniroute={"memory_max_mib": 4096})
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(
        llmenv, "host_resources", lambda: {"cpu_count": 16, "memory_total_mib": 32768}
    )

    assert llmenv.cmd_resources(cast(Namespace, SimpleNamespace(config="cfg"))) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["omniroute"]["memory_max_mib"] == 4096
    assert payload["overrides_applied"] == {"omniroute": ["memory_max_mib"]}
    assert any(
        "resource overrides applied: omniroute=memory_max_mib" in line
        for line in payload["summary"]
    )


def test_cmd_budget_ram_check_sees_a_raised_minimum(tmp_path, monkeypatch, capsys):
    import llmenv

    facts = {
        "compositor_render_node": "/dev/dri/renderD128",
        "gpus": [{
            "pci_address": "0000:03:00.0", "render_node": "/dev/dri/renderD129",
            "vram_total_mib": 16304, "vram_used_mib": 200,
        }],
    }
    available = []
    for extra in ({}, {"omniroute": {"memory_min_mib": 2048}}):
        cfg = _resources_cfg(tmp_path, **extra)
        monkeypatch.setattr(llmenv, "load_config", lambda path, cfg=cfg: cfg)
        monkeypatch.setattr(llmenv, "detect", lambda: facts)
        monkeypatch.setattr(
            llmenv, "host_resources", lambda: {"cpu_count": 24, "memory_total_mib": 32768}
        )
        monkeypatch.setattr(
            llmenv, "_model_costs",
            lambda config, path: [{"alias": "a", "weights_mib": 100, "ram_weights_mib": 0, "kv_mib": 10}],
        )
        llmenv.cmd_budget(cast(Namespace, SimpleNamespace(config="cfg", models_dir="models")))
        available.append(json.loads(capsys.readouterr().out)["ram_available_mib"])

    assert available[0] - available[1] == 2048 - 768


def _render_args(tmp_path):
    return cast(Namespace, SimpleNamespace(
        config="cfg", models_dir="/m", presets_path="/p", repo_root="/r",
        env_file=None, output=str(tmp_path / "compose.yml"), system_one_rocr_index="",
    ))


def _render_with(tmp_path, monkeypatch, **overrides):
    import llmenv

    cfg = _resources_cfg(tmp_path, **overrides)
    # Stale generated mirror: must not reach the rendered file.
    cfg["resources"]["omniroute"] = {"cpus_min": 1, "cpus_max": 4, "memory_min_mib": 512, "memory_max_mib": 999}
    cfg["resources"]["remote_setup"] = {"cpus_min": 1, "cpus_max": 1, "memory_min_mib": 16, "memory_max_mib": 17}
    cfg["resources"]["llm_server"].update({"cpus": 1, "memory_mib": 1})
    host = {"cpu_count": 16, "memory_total_mib": 32768}
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "host_resources", lambda: host)
    assert llmenv.cmd_render_compose(_render_args(tmp_path)) == 0
    services = yaml.safe_load((tmp_path / "compose.yml").read_text())["services"]
    return services, llmenv.limits_for(cfg, host)


def test_render_compose_follows_overrides_not_the_stale_mirror(tmp_path, monkeypatch):
    services, limits = _render_with(tmp_path, monkeypatch, omniroute={"memory_max_mib": 4096})
    assert services["omniroute"]["mem_limit"] == "4096m"
    assert services["remote-setup"]["mem_limit"] == "64m"
    assert services["llm-server"]["mem_limit"] == f"{limits['llm_server']['memory_mib']}m"


def test_render_compose_after_clear_restores_defaults_despite_the_stale_mirror(tmp_path, monkeypatch):
    services, limits = _render_with(tmp_path, monkeypatch)
    assert services["omniroute"]["mem_limit"] == "2048m"
    assert services["remote-setup"]["mem_limit"] == "64m"
    assert limits["llm_server"]["memory_mib"] > 1
    assert services["llm-server"]["mem_limit"] == f"{limits['llm_server']['memory_mib']}m"


def test_effective_policy_command_returns_the_merged_policy(tmp_path, monkeypatch, capsys):
    import llmenv

    cfg = _resources_cfg(tmp_path, phoenix={"cpus_max": 1.5, "memory_max_mib": 1536})
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    code = llmenv.cmd_effective_policy(cast(Namespace, SimpleNamespace(config="cfg", service="phoenix")))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload == {"cpus_min": 0.25, "cpus_max": 1.5, "memory_min_mib": 192, "memory_max_mib": 1536}


def test_effective_policy_command_is_wired(tmp_path):
    config = write_test_config(tmp_path)
    shown = run("effective-policy", "--config", str(config), "--service", "omniroute")
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["memory_max_mib"] == 2048
    assert run("effective-policy", "--config", str(config), "--service", "nginx").returncode == 2


def test_render_compose_on_a_host_below_the_resource_floor_fails_with_a_clear_error(
    tmp_path, monkeypatch, capsys
):
    """render-compose now reads the host (limits_for); ResourceError/DetectError must surface, not crash."""
    import llmenv

    cfg = _resources_cfg(tmp_path)
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "host_resources", lambda: {"cpu_count": 1, "memory_total_mib": 512})
    output = tmp_path / "compose.yml"
    code = llmenv.main([
        "--config", "cfg", "render-compose", "--models-dir", "/m",
        "--presets-path", "/p", "--repo-root", "/r", "--output", str(output),
    ])
    assert code == 1  # `main` converts ResourceError into {"error": ...}, exit 1
    assert "host has" in json.loads(capsys.readouterr().out)["error"]
    assert not output.exists()


def _override_call(config, action, **fields):
    import llmenv

    args = {
        "config": str(config), "action": action, "service": None,
        "cpus_min": None, "cpus_max": None, "memory_min_mib": None, "memory_max_mib": None,
    }
    args.update(fields)
    return llmenv.cmd_resource_override(cast(Namespace, SimpleNamespace(**args)))


def _saved_overrides(config):
    return yaml.safe_load(config.read_text()).get("resources", {}).get("overrides")


def _out(capsys):
    return json.loads(capsys.readouterr().out)


def test_override_lowering_a_cap_is_saved_with_a_benchmark_warning(tmp_path, capsys):
    from pylib.resources import DEFAULT_POLICY

    config = write_test_config(tmp_path)
    lowered = DEFAULT_POLICY["omniroute"]["memory_max_mib"] - 512
    assert _override_call(config, "set", service="omniroute", memory_max_mib=lowered) == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert _saved_overrides(config) == {"omniroute": {"memory_max_mib": lowered}}
    assert out["effective"]["memory_max_mib"] == lowered
    assert len(out["warnings"]) == 1 and "make resources-benchmark" in out["warnings"][0]
    assert "make resources-benchmark" in captured.err  # visible, not only in the JSON


def test_override_raising_a_cap_has_no_warning(tmp_path, capsys):
    config = write_test_config(tmp_path)
    assert _override_call(config, "set", service="omniroute", memory_max_mib=4096) == 0
    assert _saved_overrides(config) == {"omniroute": {"memory_max_mib": 4096}}
    out = _out(capsys)
    assert out["warnings"] == []
    apply = out["apply"]
    assert "make restart" in apply
    # render-unit.sh is "next start", not "apply now"; the disruption must be spelled out.
    assert "does not touch running containers" in apply
    assert "interrupted" in apply and "new cgroup lifetime" in apply


def test_override_check_validates_and_writes_nothing(tmp_path, capsys):
    config = write_test_config(tmp_path)
    assert _override_call(config, "check", service="phoenix", memory_max_mib=512) == 0
    out = _out(capsys)
    assert out["effective"]["memory_max_mib"] == 512 and len(out["warnings"]) == 1
    assert _override_call(config, "check", service="phoenix", memory_max_mib=64) == 1  # below memory_min_mib 192
    assert "effective memory_min_mib" in _out(capsys)["error"]
    assert _saved_overrides(config) is None


def test_override_set_rejects_an_invalid_merge(tmp_path, capsys):
    config = write_test_config(tmp_path)
    assert _override_call(config, "set", service="omniroute", cpus_min=9) == 1
    assert "effective cpus_min" in _out(capsys)["error"]
    assert _saved_overrides(config) is None


def test_override_set_requires_a_service_and_a_field(tmp_path, capsys):
    config = write_test_config(tmp_path)
    assert _override_call(config, "set", memory_max_mib=4096) == 1
    assert _override_call(config, "set", service="omniroute") == 1


def test_override_set_merges_with_the_existing_override(tmp_path, capsys):
    config = write_test_config(tmp_path)
    assert _override_call(config, "set", service="omniroute", memory_max_mib=4096) == 0
    assert _override_call(config, "set", service="omniroute", cpus_max=3) == 0
    assert _saved_overrides(config) == {"omniroute": {"memory_max_mib": 4096, "cpus_max": 3}}


def test_override_clear_repairs_a_hand_broken_override(tmp_path, capsys):
    import llmenv

    config = write_test_config(tmp_path)
    cfg = yaml.safe_load(config.read_text())
    cfg.setdefault("resources", {})["overrides"] = {"omniroute": {"memory_max_mib": 0}}
    config.write_text(yaml.safe_dump(cfg))
    with pytest.raises(llmenv.ConfigError):
        llmenv.require_valid_config(llmenv.load_config(config))
    assert _override_call(config, "clear", service="omniroute") == 0
    assert _saved_overrides(config) is None


def test_override_clear_drops_one_service_or_everything(tmp_path, capsys):
    config = write_test_config(tmp_path)
    _override_call(config, "set", service="omniroute", memory_max_mib=4096)
    _override_call(config, "set", service="remote_setup", memory_max_mib=128)
    assert _override_call(config, "clear", service="omniroute") == 0
    assert _saved_overrides(config) == {"remote_setup": {"memory_max_mib": 128}}
    assert _override_call(config, "clear") == 0
    assert "overrides" not in yaml.safe_load(config.read_text())["resources"]


def test_override_show_lists_overrides_and_defaults(tmp_path, capsys):
    from pylib.resources import DEFAULT_POLICY

    config = write_test_config(tmp_path)
    _override_call(config, "set", service="omniroute", memory_max_mib=4096)
    capsys.readouterr()
    assert _override_call(config, "show") == 0
    out = _out(capsys)
    assert out["overrides"] == {"omniroute": {"memory_max_mib": 4096}}
    assert out["defaults"] == DEFAULT_POLICY
    assert out["defaults"]["omniroute"]["memory_max_mib"] == 2048


def test_resource_override_subcommand_is_wired_and_the_gate_is_gone(tmp_path):
    config = write_test_config(tmp_path)
    shown = run("resource-override", "show", "--config", str(config))
    assert shown.returncode == 0, shown.stderr
    assert "defaults" in json.loads(shown.stdout)
    base = ["resource-override", "set", "--config", str(config), "--service", "omniroute"]
    assert run(*base, "--memory-max-mib", "0").returncode == 2
    for retired in (["--evidence", "x.json"], ["--max-peak-ratio", "0.8"], ["--min-observation-hours", "24"]):
        assert run(*base, "--memory-max-mib", "1536", *retired).returncode == 2, retired
    assert run("resource-evidence", "--service", "omniroute").returncode == 2
