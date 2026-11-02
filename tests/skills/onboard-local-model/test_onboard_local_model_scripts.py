"""Smoke and behaviour tests for the onboard-local-model skill scripts.

Everything here is offline: no GPU, no containers, no network (hf-scout reads HF_FIXTURE_DIR).
"""

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml
from skill_helpers import NODE_DIR, NODE_SKIP_REASON, ROOT, SCRIPTS, SKILL
from skill_helpers import out as _out
from skill_helpers import run as _run
from skill_helpers import scratch as _scratch

sys.path.insert(0, str(ROOT))
from pylib.agent_runner import DEFAULT_RUNTIME_SECONDS

RUNNER_CAP = int(DEFAULT_RUNTIME_SECONDS)

SCRIPT_NAMES = ["latency-profile", "preflight", "hf-scout", "bench-fit", "effort-run", "score", "gpu-sample", "system-one-check", "wait-gpu-release",
                "tune-sweep", "tune-pick", "tune-apply", "quant-headroom", "gen-challenge", "compare-runs", "resolve-target", "rank-candidates",
                "fetch-gguf", "new-alias", "live-entry"]


NEEDS_NODE = pytest.mark.skipif(NODE_DIR is None, reason=NODE_SKIP_REASON)


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_script_help_exits_zero_and_prints_usage(name: str) -> None:
    result = _run("bash", SCRIPTS / f"{name}.sh", "--help")
    assert result.returncode == 0
    first_line = result.stdout.splitlines()[0]
    assert first_line.startswith(f"{name}.sh"), first_line
    assert "set -euo pipefail" not in result.stdout


def _result(effort: str, seconds: int, pass_: int, temp: int, cpu: int, api_error: bool = False) -> str:
    return json.dumps(
        {
            "task": "lru-cache", "effort": effort, "model": "m", "api_error": api_error, "seconds": seconds,
            "pass": pass_, "total": 3, "gpu": {"peak_temp_c": temp, "avg_cpu_pct": cpu, "peak_vram_mib": 9000},
        }
    )


def test_score_weights_correctness_over_speed_and_load(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    # low: 70 + 15*0.9 + 10 + 5 = 98.5; high: 70/3 + 0 + 0 + 0 = 23.3
    results.write_text(_result("low", 60, 3, 60, 0) + "\n" + _result("high", 600, 1, 85, 100) + "\n")
    out = json.loads(_run("bash", SCRIPTS / "score.sh", results).stdout)
    by_effort = {row["effort"]: row for row in out["by_effort"]}
    assert by_effort["low"]["score"] == 98.5
    assert by_effort["high"]["score"] == 23.3
    assert by_effort["low"]["solved"] == "1/1" and by_effort["high"]["solved"] == "0/1"
    assert out["overall"] == 60.9 and out["invalid_runs"] == 0


def test_score_excludes_api_error_runs_instead_of_scoring_them_zero(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    results.write_text(_result("low", 5, 1, 40, 1, api_error=True) + "\n")
    out = json.loads(_run("bash", SCRIPTS / "score.sh", results).stdout)
    assert out["overall"] is None and out["invalid_runs"] == 1


def test_gpu_sample_summary_reports_peaks_and_means(tmp_path: Path) -> None:
    csv = tmp_path / "gpu.csv"
    csv.write_text(
        "epoch,vram_mib,temp_c,sclk_mhz,busy_pct,cpu_pct\n"
        "100,1000,40,2000,50,10\n"
        "102,9000,70,2400,90,30\n"
        "104,8000,65,2200,70,20\n"
    )
    out = json.loads(_run("bash", SCRIPTS / "gpu-sample.sh", "summary", csv).stdout)
    assert out == {
        "samples": 3, "seconds": 4, "first_vram_mib": 1000, "peak_vram_mib": 9000, "peak_temp_c": 70,
        "avg_sclk_mhz": 2200, "avg_gpu_busy_pct": 70, "avg_cpu_pct": 20,
        "peak_sclk_mhz": 2400, "peak_gpu_busy_pct": 90, "peak_cpu_pct": 30, "avg_vram_mib": 6000,
    }


def test_gpu_sample_start_stops_within_a_second_and_leaves_no_sleep(tmp_path: Path) -> None:
    csv = _out(tmp_path, "gpu.csv")
    proc = subprocess.Popen(  # pyright: ignore[reportCallIssue]
        ["bash", SCRIPTS / "gpu-sample.sh", "start", "0000:00:00.0", csv, "7.31"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while (not csv.is_file() or len(csv.read_text().splitlines()) < 2) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert csv.is_file() and len(csv.read_text().splitlines()) >= 2, "gpu-sample wrote no row"
        sent = time.monotonic()
        proc.terminate()
        proc.wait(timeout=5)
        assert time.monotonic() - sent <= 1.0, "gpu-sample waited for its sleep before it stopped"
        args = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, check=True).stdout.splitlines()
        assert "sleep 7.31" not in [line.strip() for line in args], "gpu-sample left its sleep behind"
    finally:
        try:  # the whole session: a red run leaves the 7.31 s sleep running, and it must die with the sampler
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def _repo(id_: str, params: float, ctx: int, arch: str, downloads: int = 100) -> dict[str, object]:
    return {
        "id": id_, "downloads": downloads, "likes": 10, "lastModified": "2026-10-01T00:00:00.000Z",
        "gguf": {"total": int(params * 1e9), "context_length": ctx, "architecture": arch},
        "baseModels": {"models": [{"id": "lab/base"}]}, "tags": ["gguf"],
    }


def _tree(*files: tuple[str, int]) -> list[dict[str, object]]:
    return [{"path": name, "size": mib * 1048576} for name, mib in files]


def test_hf_scout_classifies_fit_and_filters_noise(tmp_path: Path) -> None:
    models = [
        _repo("lab/dense-8b-GGUF", 8, 262144, "qwen35"),
        _repo("lab/moe-35B-A3B-GGUF", 35, 262144, "qwen35moe"),
        _repo("lab/dense-27b-GGUF", 27, 262144, "qwen35"),
        _repo("lab/dense-8b-uncensored-GGUF", 8, 262144, "qwen35"),
        _repo("lab/short-ctx-GGUF", 8, 32768, "qwen35"),
        _repo("lab/draft-only-35B-A3B-GGUF", 35, 262144, "qwen35moe"),
    ]
    (tmp_path / "models.json").write_text(json.dumps(models))
    trees = {
        "lab__dense-8b-GGUF": _tree(("m-Q4_K_M.gguf", 5000), ("m-Q8_0.gguf", 8500)),
        "lab__moe-35B-A3B-GGUF": _tree(("m-Q4_K_M.gguf", 20700), ("m-Q8_0.gguf", 36000)),
        "lab__dense-27b-GGUF": _tree(("m-Q4_K_M.gguf", 16500)),
        "lab__draft-only-35B-A3B-GGUF": _tree(("m-draft.gguf", 1400)),
    }
    for name, tree in trees.items():
        (tmp_path / f"tree-{name}.json").write_text(json.dumps(tree))

    result = _run(
        "bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000",
        env={"HF_FIXTURE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    rows = {row["id"]: row for row in json.loads(result.stdout)}

    assert set(rows) == {
        "lab/dense-8b-GGUF", "lab/moe-35B-A3B-GGUF", "lab/dense-27b-GGUF", "lab/draft-only-35B-A3B-GGUF",
    }, "modded and short-context repos must be dropped"
    assert rows["lab/dense-8b-GGUF"]["fit"] == "vram"
    assert rows["lab/dense-8b-GGUF"]["pick"]["quant"] == "Q8_0", "largest quant that leaves KV headroom"
    assert rows["lab/moe-35B-A3B-GGUF"]["fit"] == "offload"
    assert rows["lab/dense-27b-GGUF"]["fit"] == "no", "dense models are never offload candidates"
    assert rows["lab/draft-only-35B-A3B-GGUF"]["pick"] is None, "a 0.3 bits-per-weight file is a draft head"


def test_hf_scout_include_modded_keeps_modified_variants(tmp_path: Path) -> None:
    (tmp_path / "models.json").write_text(json.dumps([_repo("lab/x-abliterated-GGUF", 8, 262144, "qwen35")]))
    (tmp_path / "tree-lab__x-abliterated-GGUF.json").write_text(json.dumps(_tree(("m-Q4_K_M.gguf", 5000))))
    env = {"HF_FIXTURE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    base = ("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000")
    assert json.loads(_run(*base, env=env).stdout) == []
    assert len(json.loads(_run(*base, "--include-modded", env=env).stdout)) == 1


def test_hf_scout_reads_gguf_files_in_sub_folders(tmp_path: Path) -> None:
    (tmp_path / "models.json").write_text(json.dumps([_repo("lab/dense-8b-GGUF", 8, 262144, "qwen35")]))
    recursive = [
        {"path": "Q8_0", "type": "directory"},
        {"path": "Q8_0/m-Q8_0-00001-of-00002.gguf", "size": 4250 * 1048576},
        {"path": "Q8_0/m-Q8_0-00002-of-00002.gguf", "size": 4250 * 1048576},
        {"path": "m-Q4_K_M.gguf", "size": 5000 * 1048576},
    ]
    flat = [{"path": "Q8_0", "type": "directory"}, {"path": "m-Q4_K_M.gguf", "size": 5000 * 1048576}]
    (tmp_path / "tree-recursive.json").write_text(json.dumps(recursive))
    (tmp_path / "tree-flat.json").write_text(json.dumps(flat))
    (tmp_path / "search.json").write_text(json.dumps([_repo("lab/dense-8b-GGUF", 8, 262144, "qwen35")]))
    calls = tmp_path / "calls"
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    (stub / "curl").write_text(f"""#!/usr/bin/env bash
echo "$*" >> {calls}
url=""; out=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift ;; https://*) url="$1" ;; esac; shift; done
case "$url" in
    *"tree/main?recursive=true") cat {tmp_path}/tree-recursive.json > "$out" ;;
    *"/tree/main") cat {tmp_path}/tree-flat.json > "$out" ;;
    *) cat {tmp_path}/search.json ;;
esac
""")
    (stub / "curl").chmod(0o755)
    result = _run(
        "bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000",
        env={"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)[0]
    assert "Q8_0:8500" in row["quants"], "split shards in a sub-folder must be summed into one quant"
    assert row["pick"]["quant"] == "Q8_0"
    assert "tree/main?recursive=true" in calls.read_text()


def test_score_correctness_only_ignores_speed_and_load(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    results.write_text(_result("low", 590, 2, 85, 100) + "\n")
    out = json.loads(_run("bash", SCRIPTS / "score.sh", results, "--correctness-only").stdout)
    assert out["overall"] == 66.7


def test_score_drops_the_thermal_term_when_no_sensor_reads_zero(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    # temp 0 means "no sensor": (70 + 15 * 0.9 + 5) * 100 / 90 = 98.3, not 98.5 with free thermal marks
    results.write_text(_result("low", 60, 3, 0, 0) + "\n")
    out = json.loads(_run("bash", SCRIPTS / "score.sh", results).stdout)
    assert out["overall"] == 98.3


@pytest.mark.parametrize(
    "script",
    ["bench-fit.sh", "effort-run.sh", "score.sh", "tune-sweep.sh", "tune-pick.sh", "tune-apply.sh", "gen-challenge.sh", "compare-runs.sh", "resolve-target.sh", "rank-candidates.sh",
     "fetch-gguf.sh", "new-alias.sh"],
)
def test_missing_required_arguments_exit_two(script: str) -> None:
    assert _run("bash", SCRIPTS / script).returncode == 2


def test_hf_scout_rejects_unknown_flags() -> None:
    result = _run("bash", SCRIPTS / "hf-scout.sh", "--nope")
    assert result.returncode == 2 and "unknown flag" in result.stderr


def test_bench_fit_refuses_while_llm_server_holds_the_gpu(tmp_path: Path) -> None:
    """A stubbed podman that lists llm-server must stop bench-fit with exit 3 before it touches the GPU."""
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    (stub / "podman").write_text(f"#!/usr/bin/env bash\n{RUNNING_LLM_SERVER}")
    (stub / "podman").chmod(0o755)
    models = tmp_path / "models"
    models.mkdir()
    (models / "m.gguf").write_bytes(b"x")
    cfg = tmp_path / "models.yml"
    cfg.write_text("gpu: {image: x, pci_address: '0000:00:00.0', device_name: d}\nruntime: {cache_type_k: q5_1, cache_type_v: q5_1, ubatch_size: 512}\n")
    result = _run(
        "bash", SCRIPTS / "bench-fit.sh", "--alias", "m", "--out", _out(tmp_path, "out"),
        env={"PATH": f"{stub}:/usr/bin:/bin", "LLM_ENV_CONFIG": str(cfg), "LLM_ENV_MODELS_DIR": str(models), "HOME": str(tmp_path)},
    )
    assert result.returncode == 3, result.stderr
    assert "make stop-llama" in result.stderr


# podman stub body answering `podman inspect --format {{.State.Running}} llm-server` (container_running) with true
RUNNING_LLM_SERVER = 'case "$*" in *inspect*llm-server*) echo true ;; esac\n'
PRESETS_INI = "[*]\\nflash-attn = off\\ncache-type-k = q8_0\\ncache-type-v = q8_0\\nubatch-size = 1024\\n[m]\\nn-gpu-layers = 99\\n"
BENCH_JSON = '[{"n_prompt":512,"n_gen":0,"n_depth":0,"avg_ts":100},{"n_prompt":0,"n_gen":128,"n_depth":0,"avg_ts":10}]'


def _bench_fit(tmp_path: Path, system_one: str, device: str = "d") -> tuple[subprocess.CompletedProcess[str], Path, dict[str, object]]:
    """Run bench-fit.sh against stubbed podman/uv/curl; the podman and curl stubs log to <tmp>/calls and <tmp>/bench-argv."""
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    calls = tmp_path / "calls"
    argv = tmp_path / "bench-argv"
    bodies = {
        "podman": (
            'case "$*" in\n'
            "*--list-devices*) echo 'Vulkan0: AMD' ;;\n"
            f'*" bench "*) sleep 0.5; echo BENCH >>"{calls}"; echo "$*" >>"{argv}"; echo \'{BENCH_JSON}\' ;;\n'
            "*inspect*system-one*) echo true ;;\n"
            "esac\n"
        ),
        "uv": (
            'case "$*" in\n'
            "*resolve-device*) echo '{\"device\":\"Vulkan0\"}' ;;\n"
            'presets*|*" presets "*) p=; for a in "$@"; do [ "$p" = --output ] && printf \'%b\' \''
            f'{PRESETS_INI}\' >"$a"; p="$a"; done ;;\n'
            "*resources*) echo '{\"llm_server\":{\"cpus\":6,\"memory_mib\":12000}}' ;;\n"
            "esac\n"
        ),
        "curl": f'echo "curl $*" >>"{calls}"\n',
    }
    for name, body in bodies.items():
        (stub / name).write_text(f"#!/usr/bin/env bash\n{body}")
        (stub / name).chmod(0o755)
    models = tmp_path / "models"
    models.mkdir(exist_ok=True)
    (models / "m.gguf").write_bytes(b"x")
    cfg = tmp_path / "models.yml"
    cfg.write_text(
        f"gpu: {{image: x, pci_address: '0000:00:00.0', device_name: '{device}'}}\n"
        f"system_one: {system_one}\nmodels: [{{alias: m, file: m.gguf}}]\n"
    )
    result = _run(
        "bash", SCRIPTS / "bench-fit.sh", "--alias", "m", "--out", _out(tmp_path, "out"), "--threads", "6", "--depths", "0",
        env={"PATH": f"{stub}:/usr/bin:/bin:{Path(shutil.which('yq') or '/usr/bin/yq').parent}", "LLM_ENV_CONFIG": str(cfg),
             "LLM_ENV_MODELS_DIR": str(models), "HOME": str(tmp_path)},
    )
    return result, calls, json.loads(result.stdout) if result.returncode == 0 else {}


def test_bench_fit_takes_flash_attn_from_presets_threads_and_production_limits(tmp_path: Path) -> None:
    result, calls, out = _bench_fit(tmp_path, "{enabled: false}")
    assert result.returncode == 0, result.stderr
    argv = (tmp_path / "bench-argv").read_text()
    for token in ("-fa 0", "-t 6", "-ub 1024", "--cpus 6", "--memory 12000m"):
        assert token in argv, (token, argv)
    assert out["flags"] == {
        "n_gpu_layers": 99, "n_cpu_moe": None, "cache_type_k": "q8_0", "cache_type_v": "q8_0",
        "ubatch_size": 1024, "flash_attn": False, "threads": 6,
    }
    assert out["file"] == "m.gguf" and out["limits"] == {"cpus": 6, "memory_mib": 12000}
    assert out["tps"][0] == {"depth": 0, "pp_tps": 100, "tg_tps": 10}  # pyright: ignore[reportIndexIssue]
    assert out["system_one_warm"] is False and "curl" not in calls.read_text()


def test_bench_fit_names_an_empty_device_before_any_container(tmp_path: Path) -> None:
    result, calls, _ = _bench_fit(tmp_path, "{enabled: false}", device="")
    assert result.returncode == 2 and "gpu.device_name is empty" in result.stderr and "then make restart" in result.stderr
    assert not (_out(tmp_path, "out") / "devices.txt").exists() and not calls.exists()


def test_bench_fit_keeps_system_one_warm_while_benchmarking(tmp_path: Path) -> None:
    result, calls, out = _bench_fit(tmp_path, "{enabled: true, port: 8010}")
    assert result.returncode == 0, result.stderr
    lines = calls.read_text().splitlines()
    warm = next(i for i, line in enumerate(lines) if "127.0.0.1:8010/v1/systemone" in line)
    assert warm < lines.index("BENCH")
    assert out["system_one_warm"] is True


def test_system_one_check_counts_releases_behind_and_newest_torch_per_index(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("ARG TORCH_VERSION=2.14.1\nARG TORCH_INDEX=rocm7.2\nARG LAYA_VERSION=0.4.2\n")
    releases = {v: [{"upload_time": f"2026-10-{i + 10}T00:00:00", "yanked": v == "0.4.4"}] for i, v in enumerate(["0.4.1", "0.4.2", "0.4.3", "0.4.4", "0.4.10"])}
    (tmp_path / "pypi.json").write_text(json.dumps({"info": {"requires_python": ">=3.10", "project_urls": {"Homepage": "https://x"}}, "releases": releases}))
    wheels = "".join(
        f'<a href="torch-{v}%2B{tag}-cp311-cp311-manylinux_2_28_x86_64.whl">w</a>\n'
        for v, tag in [("2.14.0", "rocm7.2"), ("2.14.1", "rocm7.2"), ("2.15.0", "rocm7.14"), ("2.14.1", "cpu")]
    )
    (tmp_path / "torch-index.html").write_text(wheels)
    (tmp_path / "hf.json").write_text(json.dumps({"id": "org/laya", "lastModified": "2026-10-03", "sha": "abc"}))
    result = _run(
        "bash", SCRIPTS / "system-one-check.sh",
        env={"SO_FIXTURE_DIR": str(tmp_path), "SO_DOCKERFILE": str(tmp_path / "Dockerfile"), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["pinned"] == {"laya": "0.4.2", "torch": "2.14.1", "torch_index": "rocm7.2"}
    assert out["latest"]["laya"] == "0.4.10" and out["latest"]["releases_behind"] == 2, "yanked 0.4.4 must not count"
    assert out["torch_latest_per_rocm_index"] == {"rocm7.2": "2.14.1", "rocm7.14": "2.15.0"}
    assert out["github"]["since_pin"] is None and out["pin_watch"] == [], "offline fixtures skip gh"


def _row(
    task: str, effort: str, pass_: int, total: int, api_error: bool = False, layer: int | None = None,
    tool_calls: int | None = None, tool_errors: int | None = None, **extra: object,
) -> str:
    row: dict[str, object] = {
        "task": task, "effort": effort, "model": "m", "api_error": api_error, "seconds": 60, "pass": pass_, "total": total,
        "gpu": {"peak_temp_c": 60, "avg_cpu_pct": 0, "peak_vram_mib": 1},
    }
    if layer is not None:
        row["layer"] = layer
    if tool_calls is not None:
        row["tool_calls"] = tool_calls
    if tool_errors is not None:
        row["tool_errors"] = tool_errors
    return json.dumps(row | extra)


def _score(tmp_path: Path, rows: list[str]) -> dict[str, object]:
    results = tmp_path / "results.jsonl"
    results.write_text("\n".join(rows) + "\n")
    return json.loads(_run("bash", SCRIPTS / "score.sh", results, "--correctness-only").stdout)


def test_score_ladder_stops_at_the_first_unsolved_layer_and_reports_the_cheapest_tier(tmp_path: Path) -> None:
    out = _score(
        tmp_path,
        [
            _row("l1-a", "low", 4, 4, layer=1), _row("l1-a", "high", 4, 4, layer=1),
            _row("l2-b", "low", 1, 3, layer=2), _row("l2-b", "high", 3, 3, layer=2), _row("l2-b", "medium", 3, 3, layer=2),
            _row("l3-c", "high", 0, 1, layer=3), _row("l4-d", "high", 16, 16, layer=4),  # L3 unsolved: L4 does not count
        ],
    )
    assert out["ladder"] == 2
    assert out["ladder_at"] == {"1": "low", "2": "medium", "3": None, "4": "high"}
    assert out["by_layer"] == [
        {"layer": 1, "score": 100, "solved": "2/2"}, {"layer": 2, "score": 77.8, "solved": "2/3"},
        {"layer": 3, "score": 0, "solved": "0/1"}, {"layer": 4, "score": 100, "solved": "1/1"},
    ]


def test_score_ladder_derives_layers_from_task_names(tmp_path: Path) -> None:
    out = _score(tmp_path, [_row("l2-b", "low", 3, 3)])  # no `layer` field: taken from the task name
    assert out["ladder"] == 2 and out["ladder_at"] == {"2": "low"}
    legacy = _score(tmp_path, [_row("lru-cache", "low", 3, 3)])  # no layer at all: no layer rows
    assert legacy["by_layer"] == [] and legacy["ladder"] == 0 and legacy["overall"] == 100


def test_score_ladder_counts_from_the_lowest_layer_run(tmp_path: Path) -> None:
    out = _score(tmp_path, [_row(f"l{n}-s1", "medium", 3, 3, layer=n) for n in (2, 3, 4, 5)])
    assert out["ladder"] == 5 and out["ladder_at"] == {"2": "medium", "3": "medium", "4": "medium", "5": "medium"}
    assert _score(tmp_path, [_row("l2-s1", "low", 3, 3, layer=2), _row("l4-s1", "low", 3, 3, layer=4)])["ladder"] == 2
    assert _score(tmp_path, [_row("l2-s1", "low", 0, 3, layer=2), _row("l3-s1", "low", 3, 3, layer=3)])["ladder"] == 0
    down = [_row("l1-s1", "medium", 0, 3, api_error=True)] + [_row(f"l{n}-s1", "medium", 3, 3) for n in (2, 3, 4, 5)]
    out = _score(tmp_path, down)  # L1 ran but every row is an API error: the lowest layer run has no tier
    assert out["ladder"] == 0 and out["ladder_at"] == {"1": None, "2": "medium", "3": "medium", "4": "medium", "5": "medium"}


def test_compare_runs_reports_the_ladder_of_a_partial_layer_run(tmp_path: Path) -> None:
    rows = [_row(f"l{n}-s1", "medium", 3, 3, layer=n) for n in (2, 3, 4, 5)]
    out = json.loads(_compare(_lane(tmp_path, "a.jsonl", rows), _lane(tmp_path, "b.jsonl", rows)).stdout)
    assert [r["ladder"] for r in out["runs"]] == [5, 5] and out["delta_vs_first"][1]["ladder"] == 0


def test_score_ladder_ignores_api_error_runs(tmp_path: Path) -> None:
    out = _score(tmp_path, [_row("l1-a", "low", 4, 4, layer=1), _row("l2-b", "low", 3, 3, api_error=True, layer=2)])
    assert out["ladder"] == 1 and out["invalid_runs"] == 1 and out["ladder_at"] == {"1": "low", "2": None}


def test_score_reports_pass_rate_and_tool_use_for_layers_three_to_five(tmp_path: Path) -> None:
    out = _score(
        tmp_path,
        [
            _row("l1-a", "low", 4, 4, layer=1, tool_calls=5, tool_errors=0),
            _row("l3-c", "high", 1, 1, layer=3, tool_calls=4, tool_errors=1),
            _row("l4-d", "high", 0, 2, layer=4, tool_calls=6, tool_errors=2),
            _row("l5-e", "high", 1, 1, api_error=True, layer=5, tool_calls=9, tool_errors=9),
        ],
    )
    assert out["pass_rate"] == 0.667
    assert out["tool_use"] == {"layers": [3, 4, 5], "calls": 10, "errors": 3, "error_rate": 0.3}


def test_score_tool_use_is_null_when_rows_lack_tool_counts(tmp_path: Path) -> None:
    out = _score(tmp_path, [_row("l3-c", "high", 1, 1, layer=3), _row("l4-d", "high", 2, 2, layer=4)])
    assert out["tool_use"] == {"layers": [3, 4, 5], "calls": None, "errors": None, "error_rate": None}
    assert out["pass_rate"] == 1.0
    low = _score(tmp_path, [_row("l1-a", "low", 4, 4, layer=1)])
    tool_use = low["tool_use"]
    assert isinstance(tool_use, dict) and tool_use["calls"] == 0 and tool_use["errors"] == 0 and tool_use["error_rate"] is None


# What the stubbed agent does besides recording tool events; each branch is switched on by an env variable of the test.
AGENT_MISBEHAVIOUR = r"""
[ -z "${TAMPER:-}" ] || echo tampered >>../../challenges/l1-s7/challenge/prompt.md
if [ -n "${OUTSIDE:-}" ]; then
  for f in .claude/skills/onboard-local-model/challenges/planted.js .claude/skills/onboard-local-model/scripts/planted.sh tools/lib.sh; do
    mkdir -p "$(dirname "$ONBOARD_GUARD_ROOT/$f")"; echo x >"$ONBOARD_GUARD_ROOT/$f"
  done
  rm -f "$ONBOARD_GUARD_ROOT/.claude/skills/onboard-local-model/scripts/guarded.sh"
fi
if [ -n "${CONCURRENT:-}" ]; then
  for f in owner-file.txt tests/skills/x.py .claude/skills/onboard-local-model/SKILL.md .claude/skills/onboard-local-model/references/x.md; do
    mkdir -p "$(dirname "$ONBOARD_GUARD_ROOT/$f")"; echo x >"$ONBOARD_GUARD_ROOT/$f"
  done
  rm -f "$ONBOARD_GUARD_ROOT/owner.txt"
fi
if [ -n "${BENIGN:-}" ]; then
  for f in .codegraph/codegraph.db-shm .claude/settings.local.json .claude/worktrees/w/x .pi/agent/x .superpowers/x .playwright-mcp/x tmp/x cache/x; do
    mkdir -p "$(dirname "$ONBOARD_GUARD_ROOT/$f")"; echo x >"$ONBOARD_GUARD_ROOT/$f"
  done
fi
[ -z "${HANG:-}" ] || for f in src/*.js; do echo 'for (;;) {}' >>"$f"; done
[ -z "${EXIT0:-}" ] || sed -i '1i process.exit(0);' src/*.js
[ -z "${PEEK:-}" ] || printf '%s\n' '{"type":"tool_execution_start","args":{"command":"cat ../../challenges/l1-s7/manifest.json"}}' >>"$t"
[ -z "${PEEK_LIB:-}" ] || printf '%s\n' '{"type":"tool_execution_start","args":{"command":"cat ../../../../.claude/skills/onboard-local-model/challenges/projects/paginate/src/paginate.js"}}' >>"$t"
"""


def _stub_bin(tmp_path: Path) -> Path:
    """yq answers the PCI lookup, uv (behind lib.sh's llmenv) plays the agent, sleep returns at once."""
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    scripts = {
        "yq": "echo 0000:00:00.0\n",
        "uv": (
            f"case \"$*\" in *agent-runtime-cap*) echo '{{\"runtime_seconds\": {RUNNER_CAP}}}'; exit 0 ;; esac\n"
            "echo agent > src/agent_added.js\n"
            'case "$*" in *run-agent-bounded*) printf "%s\\n" "$*" >>"$STUB_ARGV"; t=; p=\n'
            '[ -z "${STUB_ENV:-}" ] || printf "PI_CODING_AGENT_DIR=%s\\n" "$PI_CODING_AGENT_DIR" >>"$STUB_ENV"\n'
            'for a in "$@"; do [ "$p" = --transcript ] && t="$a"; p="$a"; done\n'
            "printf '%s\\n' '{\"type\":\"tool_execution_end\",\"isError\":false}' "
            "'{\"type\":\"tool_execution_end\",\"isError\":true}' noise >>\"$t\"\n"
            f"{AGENT_MISBEHAVIOUR}"
            "[ \"${STUB_OUTCOME:-completed}\" = none ] || printf '{\"outcome\":\"%s\"}\\n' \"${STUB_OUTCOME:-completed}\" ;; esac\n"
        ),
        "sleep": "exec /bin/sleep 0.05\n",
    }
    for name, body in scripts.items():
        (stub / name).write_text(f"#!/usr/bin/env bash\n{body}")
        (stub / name).chmod(0o755)
    return stub


def _guard_root(tmp_path: Path) -> Path:
    """A throwaway git work tree standing in for the owner's repository (ONBOARD_GUARD_ROOT), prepared idempotently."""
    guard = tmp_path / "guard"
    guard.mkdir(exist_ok=True)
    if not (guard / ".git").exists():
        _run("git", "-C", guard, "init", "-q")
    (guard / ".gitignore").write_text("tmp/\ncache/\n")
    (guard / "owner.txt").write_text("owner\n")
    (guard / ".claude/skills/onboard-local-model/scripts").mkdir(parents=True, exist_ok=True)
    (guard / ".claude/skills/onboard-local-model/scripts/guarded.sh").write_text("g\n")
    return guard


def _effort_run(
    tmp_path: Path, *extra: str, outcome: str = "completed", env: Mapping[str, str] | None = None, timeout: float | None = None,
    out_name: str = "out",
) -> subprocess.CompletedProcess[str]:
    path = ":".join(p for p in (str(_stub_bin(tmp_path)), NODE_DIR, "/usr/bin", "/bin") if p)
    full_env = {
        "PATH": path, "HOME": str(tmp_path / "home"), "ONBOARD_GUARD_ROOT": str(_guard_root(tmp_path)),
        "LLM_ENV_CONFIG": str(tmp_path / "models.yml"), "STUB_ARGV": str(tmp_path / "argv.txt"), "STUB_OUTCOME": outcome, **(env or {}),
    }
    return _run(
        "bash", SCRIPTS / "effort-run.sh", "--model", "p/m", "--out", _out(tmp_path, out_name), "--efforts", "low", *extra,
        env=full_env, stdin=subprocess.DEVNULL, timeout=timeout,
    )


SEEDS_ALL: dict[str, list[object]] = {"1": [1], "2": [1], "3": [1], "4": [1], "5": [1]}  # seed 1 of every layer passes the validity gate (test_challenges.py)


@NEEDS_NODE
def test_effort_run_defaults_to_every_layer_and_isolates_each_run_in_a_git_repo(tmp_path: Path) -> None:
    result = _effort_run(tmp_path, "--seeds", str(_seeds_file(tmp_path, SEEDS_ALL)))
    assert result.returncode == 0, result.stderr
    rows = _rows(tmp_path)
    assert [r["layer"] for r in rows] == [1, 2, 3, 4, 5]
    assert all(isinstance(r["total"], int) and r["total"] > 0 and r["outside_writes"] == [] for r in rows), "node reached, and the run's own writes never flag"
    out = _out(tmp_path, "out")
    run = out / "l1-s1-low"
    assert _run("git", "-C", run / "work", "rev-list", "--count", "HEAD").stdout.strip() == "1"
    diff = (run / "agent.diff").read_text()
    assert re.findall(r"^diff --git a/(\S+)", diff, re.MULTILINE) == ["src/agent_added.js"], "new files appear in the diff, untouched ones do not"
    assert not (out / "challenges" / "l1-s1" / "challenge" / "src" / "agent_added.js").exists()


@NEEDS_NODE
def test_effort_run_layers_flag_filters_the_task_list(tmp_path: Path) -> None:
    seeds = _seeds_file(tmp_path, {"2": [1], "3": [1]})
    assert _effort_run(tmp_path, "--layers", "2,3", "--seeds", str(seeds)).returncode == 0
    assert [r["layer"] for r in _rows(tmp_path)] == [2, 3]


@NEEDS_NODE
def test_effort_run_counts_tool_calls_and_errors_from_the_transcript(tmp_path: Path) -> None:
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(_seeds_file(tmp_path, {"1": [7]})))
    assert result.returncode == 0, result.stderr
    row = _rows(tmp_path)[0]
    assert row["tool_calls"] == 2 and row["tool_errors"] == 1


@NEEDS_NODE
def test_effort_run_defaults_to_the_runner_cap_and_refuses_a_larger_timeout(tmp_path: Path) -> None:
    seeds = str(_seeds_file(tmp_path, {"1": [7]}))
    assert _effort_run(tmp_path, "--layers", "1", "--seeds", seeds, out_name="a").returncode == 0
    assert f"--runtime-seconds {RUNNER_CAP} " in (tmp_path / "argv.txt").read_text()
    bad = _effort_run(tmp_path, "--layers", "1", "--seeds", seeds, "--timeout", str(RUNNER_CAP + 1))
    assert bad.returncode == 2 and str(RUNNER_CAP) in bad.stderr and "--timeout" in bad.stderr
    assert _effort_run(tmp_path, "--layers", "1", "--seeds", seeds, "--timeout", "120", out_name="c").returncode == 0
    assert "--runtime-seconds 120 " in (tmp_path / "argv.txt").read_text()


@NEEDS_NODE
def test_effort_run_defaults_to_the_router_profile(tmp_path: Path) -> None:
    log = tmp_path / "env.txt"
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(_seeds_file(tmp_path, {"1": [1]})), env={"STUB_ENV": str(log)})
    assert result.returncode == 0, result.stderr
    assert set(log.read_text().splitlines()) == {f"PI_CODING_AGENT_DIR={tmp_path / 'home' / '.pi' / 'agent'}"}


@NEEDS_NODE
@pytest.mark.parametrize(("outcome", "expected"), [("completed", "completed"), ("boundary-failure", "boundary-failure"), ("none", "launch-failure")])
def test_effort_run_records_the_bounded_outcome(tmp_path: Path, outcome: str, expected: str) -> None:
    seeds = _seeds_file(tmp_path, {"1": [7]})
    assert _effort_run(tmp_path, "--layers", "1", "--seeds", str(seeds), outcome=outcome).returncode == 0
    assert _rows(tmp_path)[0]["bounded_outcome"] == expected


def _gen_id() -> str:
    assert NODE_DIR is not None
    env = {"PATH": f"{NODE_DIR}:/usr/bin:/bin", "HOME": str(Path.home())}
    return _run("bash", SCRIPTS / "gen-challenge.sh", "--generator-id", env=env).stdout.strip()


def _seeds_file(tmp_path: Path, layers: Mapping[str, list[object]], generator: str | None = None) -> Path:
    path = tmp_path / "seeds-in.json"
    path.write_text(json.dumps({"schema": 1, "generator": generator or _gen_id(), "per_layer": 1, "layers": layers}))
    return path


def _rows(tmp_path: Path, out_name: str = "out") -> list[dict[str, object]]:
    return _jsonl(_out(tmp_path, out_name) / "results.jsonl")


@NEEDS_NODE
def test_effort_run_scores_a_hanging_solution_as_failed(tmp_path: Path) -> None:
    seeds = _seeds_file(tmp_path, {"1": [7]})
    result = _effort_run(
        tmp_path, "--layers", "1", "--seeds", str(seeds), env={"HANG": "1", "ONBOARD_GEN_TEST_TIMEOUT_MS": "3000"}, timeout=300
    )
    assert result.returncode == 0, result.stderr
    (row,) = _rows(tmp_path)
    assert isinstance(row["total"], int) and isinstance(row["pass"], int)
    assert 0 < row["total"] and row["pass"] < row["total"]
    leftover = [line for line in _run("ps", "-eo", "args").stdout.splitlines() if str(_out(tmp_path, "out")) in line]
    assert leftover == []


@NEEDS_NODE
def test_effort_run_never_scores_a_solution_that_exits_at_load_as_solved(tmp_path: Path) -> None:
    seeds = _seeds_file(tmp_path, {"1": [7]})
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(seeds), env={"EXIT0": "1"})
    assert result.returncode == 0, result.stderr
    (row,) = _rows(tmp_path)
    manifest = json.loads((_out(tmp_path, "out") / "challenges" / "l1-s7" / "manifest.json").read_text())
    assert manifest["tests_total"] == 13 and row["tests_total"] == 13
    assert isinstance(row["pass"], int) and isinstance(row["total"], int)
    assert row["total"] == 13 and row["pass"] < row["total"]


@NEEDS_NODE
def test_effort_run_runs_generated_challenges_from_a_seeds_file(tmp_path: Path) -> None:
    seeds = _seeds_file(tmp_path, {"1": [7]})
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(seeds))
    assert result.returncode == 0, result.stderr
    assert result.stderr.splitlines()[0] == f"effort-run: 1 challenges x 1 efforts x {RUNNER_CAP} s upper bound"
    (row,) = _rows(tmp_path)
    out = _out(tmp_path, "out")
    manifest = json.loads((out / "challenges" / "l1-s7" / "manifest.json").read_text())
    assert row["task"] == "l1-s7" and row["layer"] == 1 and row["seed"] == 7 and row["generator"] == _gen_id()
    assert row["project"] == manifest["project"] and row["operators"] == [o["op"] for o in manifest["operators"]]
    assert row["timeout_s"] == RUNNER_CAP and row["leak_suspect"] is False and row["outside_writes"] == []
    assert isinstance(row["pass"], int) and isinstance(row["total"], int) and row["pass"] < row["total"]
    assert json.loads((out / "seeds.json").read_text()) == json.loads(seeds.read_text())
    assert _run("git", "-C", out / "l1-s7-low" / "work", "rev-list", "--count", "HEAD").stdout.strip() == "1"
    assert not list(out.rglob("reference"))


@NEEDS_NODE
def test_effort_run_draws_fresh_seeds_per_layer(tmp_path: Path) -> None:
    result = _effort_run(tmp_path, "--layers", "1,2", "--per-layer", "2")
    assert result.returncode == 0, result.stderr
    drawn = json.loads((_out(tmp_path, "out") / "seeds.json").read_text())["layers"]
    assert sorted(drawn) == ["1", "2"]
    for seeds in drawn.values():
        assert len(set(seeds)) == 2 and all(isinstance(x, int) and 0 <= x < 2**32 for x in seeds)
    assert [r["layer"] for r in _rows(tmp_path)] == [1, 1, 2, 2]


@NEEDS_NODE
def test_effort_run_refuses_seeds_from_another_generator(tmp_path: Path) -> None:
    seeds = _seeds_file(tmp_path, {"1": [7]}, generator="g-000000000000")
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(seeds))
    assert result.returncode == 2 and "generator" in result.stderr
    assert not (_out(tmp_path, "out") / "challenges").exists()


@NEEDS_NODE
@pytest.mark.parametrize(
    "layers", [{"2": [7]}, {"1": [7, 7]}, {"1": []}, {"1": [-1]}, {"1": [2**32]}, {"1": ["7"]}],
    ids=["layer-missing", "duplicate", "empty", "negative", "too-large", "not-a-number"],
)
def test_effort_run_rejects_an_invalid_seeds_file(tmp_path: Path, layers: dict[str, list[object]]) -> None:
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(_seeds_file(tmp_path, layers)))
    assert result.returncode == 2 and "seeds file" in result.stderr
    assert not (_out(tmp_path, "out") / "challenges").exists()


def test_effort_run_rejects_mixed_seed_options(tmp_path: Path) -> None:
    seeds = tmp_path / "s.json"
    seeds.write_text("{}")
    for extra in (("--per-layer", "2", "--seeds", str(seeds)), ("--tasks", "l1-x"), ("--challenges", "bundled")):
        result = _effort_run(tmp_path, *extra)
        assert result.returncode == 2, (extra, result.stderr)
        assert extra[0] == "--per-layer" or "unknown flag" in result.stderr, (extra, result.stderr)
    assert not (_out(tmp_path, "out") / "challenges").exists()


@pytest.mark.parametrize("value", ["hgih", "low/../x", "low,low", ",low", ""])
def test_effort_run_rejects_unknown_or_repeated_efforts(tmp_path: Path, value: str) -> None:
    result = _effort_run(tmp_path, "--efforts", value)
    assert result.returncode == 2 and "--efforts" in result.stderr, (value, result.stderr)
    assert not _out(tmp_path, "out").exists()


@pytest.mark.parametrize("flag", ["--model", "--out", "--efforts", "--layers", "--timeout", "--per-layer", "--seeds"])
def test_effort_run_rejects_a_value_flag_given_last_without_a_value(tmp_path: Path, flag: str) -> None:
    result = _run("bash", SCRIPTS / "effort-run.sh", flag, stdin=subprocess.DEVNULL)
    assert result.returncode == 2 and flag in result.stderr, (flag, result.returncode, result.stderr)


@NEEDS_NODE
def test_effort_run_aborts_when_the_pristine_challenge_changes(tmp_path: Path) -> None:
    seeds = _seeds_file(tmp_path, {"1": [7]})
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(seeds), env={"TAMPER": "1"})
    assert result.returncode == 1 and "was modified" in result.stderr
    assert [r["leak_suspect"] for r in _rows(tmp_path)] == [True]


@NEEDS_NODE
def test_effort_run_flags_writes_outside_the_run(tmp_path: Path) -> None:
    seeds = _seeds_file(tmp_path, {"1": [7]})
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(seeds), env={"OUTSIDE": "1"})
    assert result.returncode == 0, result.stderr
    (row,) = _rows(tmp_path)
    assert row["outside_writes"] == [
        ".claude/skills/onboard-local-model/challenges/planted.js", ".claude/skills/onboard-local-model/scripts/guarded.sh",
        ".claude/skills/onboard-local-model/scripts/planted.sh", "tools/lib.sh",
    ]
    assert row["other_writes"] == [] and row["leak_suspect"] is True


@NEEDS_NODE
def test_effort_run_ignores_tool_state_and_ignored_paths(tmp_path: Path) -> None:
    seeds = _seeds_file(tmp_path, {"1": [7]})
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(seeds), env={"BENIGN": "1"})
    assert result.returncode == 0, result.stderr
    (row,) = _rows(tmp_path)
    assert row["outside_writes"] == [] and row["other_writes"] == [] and row["leak_suspect"] is False


@NEEDS_NODE
def test_effort_run_ignores_a_tracked_file_deleted_before_the_run(tmp_path: Path) -> None:
    guard = _guard_root(tmp_path)
    tracked = guard / ".claude/skills/onboard-local-model/scripts/tracked.sh"
    tracked.write_text("t\n")
    _run("git", "-C", guard, "add", tracked)
    _run("git", "-C", guard, "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "-q", "-m", "tracked")
    tracked.unlink()
    seeds = _seeds_file(tmp_path, {"1": [7]})
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(seeds))
    assert result.returncode == 0, result.stderr
    (row,) = _rows(tmp_path)
    assert row["outside_writes"] == [] and row["leak_suspect"] is False


@NEEDS_NODE
def test_effort_run_ignores_concurrent_edits_outside_the_generator_and_scorer(tmp_path: Path) -> None:
    seeds = _seeds_file(tmp_path, {"1": [7]})
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(seeds), env={"CONCURRENT": "1"})
    assert result.returncode == 0, result.stderr
    (row,) = _rows(tmp_path)
    assert row["outside_writes"] == [] and row["leak_suspect"] is False
    assert row["other_writes"] == [
        ".claude/skills/onboard-local-model/SKILL.md", ".claude/skills/onboard-local-model/references/x.md",
        "owner-file.txt", "owner.txt", "tests/skills/x.py",
    ]


@NEEDS_NODE
def test_effort_run_requires_a_git_guard_root(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    result = _effort_run(tmp_path, "--layers", "1", "--per-layer", "1", env={"ONBOARD_GUARD_ROOT": str(plain)})
    assert result.returncode == 2 and "git work tree" in result.stderr
    assert not (_out(tmp_path, "out") / "challenges").exists()


@NEEDS_NODE
def test_effort_run_flags_a_transcript_that_reads_the_manifest(tmp_path: Path) -> None:
    seeds = _seeds_file(tmp_path, {"1": [7]})
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(seeds), env={"PEEK": "1"})
    assert result.returncode == 0, result.stderr
    assert [r["leak_suspect"] for r in _rows(tmp_path)] == [True]


@NEEDS_NODE
def test_effort_run_flags_a_transcript_that_reads_the_generator_library_by_a_relative_path(tmp_path: Path) -> None:
    seeds = _seeds_file(tmp_path, {"1": [7]})
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(seeds), env={"PEEK_LIB": "1"})
    assert result.returncode == 0, result.stderr
    assert [r["leak_suspect"] for r in _rows(tmp_path)] == [True]


@NEEDS_NODE
def test_effort_run_refuses_an_out_that_already_holds_a_generated_run(tmp_path: Path) -> None:
    seeds = _seeds_file(tmp_path, {"1": [7]})
    assert _effort_run(tmp_path, "--layers", "1", "--seeds", str(seeds)).returncode == 0
    out = _out(tmp_path, "out")
    before = ((out / "results.jsonl").read_text(), (out / "seeds.json").read_text())
    result = _effort_run(tmp_path, "--layers", "1", "--per-layer", "1")
    assert result.returncode == 2 and "fresh --out" in result.stderr and "--seeds" in result.stderr
    assert ((out / "results.jsonl").read_text(), (out / "seeds.json").read_text()) == before


def test_score_excludes_boundary_and_launch_failures_as_invalid_runs(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    rows = [json.loads(_result("low", 60, 3, 60, 0)) | {"bounded_outcome": o} for o in ("completed", "boundary-failure", "launch-failure", "timeout")]
    results.write_text("".join(json.dumps(r) + "\n" for r in rows) + _result("low", 60, 3, 60, 0) + "\n")
    out = json.loads(_run("bash", SCRIPTS / "score.sh", results).stdout)
    assert out["invalid_runs"] == 2
    assert out["by_effort"][0]["solved"] == "3/3"


def test_score_excludes_leak_suspect_runs(tmp_path: Path) -> None:
    out = _score(tmp_path, [_row("l1-a", "low", 1, 1), _row("l2-b", "low", 1, 1, leak_suspect=True)])
    assert out["ladder"] == 1 and out["leak_suspect_runs"] == 1 and out["invalid_runs"] == 0
    both = _score(tmp_path, [_row("l1-a", "low", 1, 1), _row("l2-b", "low", 1, 1, api_error=True, leak_suspect=True)])
    assert both["invalid_runs"] == 1 and both["leak_suspect_runs"] == 0


def test_score_ladder_needs_a_majority_of_a_layers_challenges_at_one_effort(tmp_path: Path) -> None:
    out = _score(
        tmp_path,
        [
            _row("l1-s1", "low", 1, 1), _row("l1-s2", "low", 1, 1), _row("l1-s3", "low", 0, 1), _row("l1-s3", "high", 1, 1),
            _row("l2-s4", "low", 1, 1), _row("l2-s4", "high", 0, 1), _row("l2-s5", "low", 0, 1), _row("l2-s5", "high", 1, 1),
        ],
    )
    assert out["ladder_at"] == {"1": "low", "2": None} and out["ladder"] == 1


def test_score_reports_the_challenge_set_and_operator_solve_rates(tmp_path: Path) -> None:
    gen = {"generator": "g-abc", "timeout_s": 900}
    out = _score(
        tmp_path,
        [
            _row("l1-s1", "low", 1, 1, operators=["ceil-floor"], **gen), _row("l1-s1", "high", 0, 1, operators=["ceil-floor"], **gen),
            _row("l2-s2", "low", 1, 1, operators=["stub"], **gen),
        ],
    )
    assert out["challenge_set"] == {"generators": ["g-abc"], "tasks": ["l1-s1", "l2-s2"], "efforts": ["low", "high"], "timeouts": [900]}
    assert out["by_operator"] == [{"op": "ceil-floor", "solved": "1/2"}, {"op": "stub", "solved": "1/1"}]
    old = _score(tmp_path, [_row("l1-a", "low", 1, 1)])  # a results file from before Task 7: missing keys are null
    assert old["challenge_set"] == {"generators": [None], "tasks": ["l1-a"], "efforts": ["low"], "timeouts": [None]}
    assert old["by_operator"] == []


def _lane(tmp_path: Path, name: str, rows: list[str]) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(rows) + "\n")
    return path


def _compare(*files: Path, flag: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    return _run("bash", SCRIPTS / "compare-runs.sh", *flag, *files)


def test_compare_runs_reports_deltas_on_one_challenge_set(tmp_path: Path) -> None:
    kw = {"generator": "g-abc", "timeout_s": 900}
    first = _lane(tmp_path, "a.jsonl", [_row("l1-s7", "low", 1, 1, model="q4", **kw), _row("l2-s8", "low", 1, 1, model="q4", **kw)])
    second = _lane(tmp_path, "b.jsonl", [_row("l1-s7", "low", 1, 1, model="q5", **kw), _row("l2-s8", "low", 0, 1, model="q5", **kw)])
    result = _compare(first, second, flag=("--correctness-only",))
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["runs"][1]["models"] == ["q5"] and out["runs"][0]["file"] == str(first)
    assert out["delta_vs_first"][0]["overall"] == 0
    assert out["delta_vs_first"][1] == {"file": str(second), "ladder": -1, "overall": -50, "pass_rate": -0.5}
    assert out["warnings"] == [] and out["challenge_set"]["tasks"] == ["l1-s7", "l2-s8"]


def test_replace_rule_reads_a_correctness_only_compare(tmp_path: Path) -> None:
    kw = {"generator": "g-abc", "timeout_s": 300}
    hot = {"seconds": 200, "gpu": {"peak_temp_c": 80, "avg_cpu_pct": 50, "peak_vram_mib": 1}}
    cool = {"seconds": 60, "gpu": {"peak_temp_c": 65, "avg_cpu_pct": 20, "peak_vram_mib": 1}}
    base = _lane(tmp_path, "a.jsonl", [_row(t, "low", 1, 2, model="a", **kw, **hot) for t in ("l1-s7", "l2-s8")])
    cand = _lane(tmp_path, "b.jsonl", [_row(t, "low", 1, 2, model="b", **kw, **cool) for t in ("l1-s7", "l2-s8")])
    full = json.loads(_compare(base, cand).stdout)["delta_vs_first"][1]
    only = json.loads(_compare(base, cand, flag=("--correctness-only",)).stdout)["delta_vs_first"][1]
    assert full["overall"] > 10 and only["overall"] == 0  # equal quality: only the correctness-only compare keeps the baseline
    rule = _section(_md(REFS / "scoring.md"), "(6) **the one replace rule**", "## Head-to-head")
    assert "`compare-runs.sh --correctness-only`" in rule
    assert "`bash $SK/scripts/compare-runs.sh --correctness-only` with the baseline first" in _md(REFS / "onboard-flow.md")
    assert "`bash $SK/scripts/compare-runs.sh --correctness-only <a-results> <results.jsonl>`" in _md(REFS / "tuning.md")
    assert "runs one `compare-runs.sh --correctness-only` with the baseline first" in _md(REFS / "selection-criteria.md")
    assert "The lanes are compared with compare-runs.sh --correctness-only" in _asserts("quant-head-to-head")


def test_compare_runs_refuses_different_challenge_sets(tmp_path: Path) -> None:
    first = _lane(tmp_path, "a.jsonl", [_row("l1-s7", "low", 1, 1)])
    second = _lane(tmp_path, "b.jsonl", [_row("l1-s8", "low", 1, 1)])
    result = _compare(first, second)
    assert result.returncode == 3
    out = json.loads(result.stdout)
    assert out["error"].startswith("not a head-to-head") and len(out["challenge_sets"]) == 2


def test_compare_runs_warns_about_invalid_lanes(tmp_path: Path) -> None:
    first = _lane(tmp_path, "a.jsonl", [_row("l1-s7", "low", 1, 1)])
    second = _lane(tmp_path, "b.jsonl", [_row("l1-s7", "low", 1, 1), _row("l1-s7", "low", 0, 1, api_error=True)])
    result = _compare(first, second)
    assert result.returncode == 0
    warnings = json.loads(result.stdout)["warnings"]
    assert len(warnings) == 1 and str(second) in warnings[0] and "same seeds" in warnings[0]


def test_compare_runs_fails_when_score_fails_on_a_file(tmp_path: Path) -> None:
    good = _lane(tmp_path, "a.jsonl", [_row("l1-s7", "low", 1, 1)])
    bad = _lane(tmp_path, "b.jsonl", [json.dumps({"task": "l1-s7", "effort": "low", "pass": 1, "total": 1})])  # no seconds/gpu
    result = _compare(good, bad)
    assert result.returncode != 0 and result.stdout.strip() == "" and str(bad) in result.stderr


def test_compare_runs_needs_two_files_and_known_flags(tmp_path: Path) -> None:
    one = _lane(tmp_path, "a.jsonl", [_row("l1-s7", "low", 1, 1)])
    assert _compare(one).returncode == 2
    assert _compare(one, one, flag=("--nope",)).returncode == 2
    assert _compare(one, tmp_path / "missing.jsonl").returncode == 2


SKILL_MD = SKILL / "SKILL.md"


def _md(path: Path) -> str:
    return " ".join(path.read_text().split())


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_skill_gates_announce_and_proceed_when_permissions_are_bypassed() -> None:
    gates = _section(_md(SKILL_MD), "## Gates", "## NEVER")
    for phrase in ("permissions bypassed", "announce the facts, then proceed", "deletes user data",
                   "any change of provider, harness or model after a failure", "a yes to one never covers the next"):
        assert phrase in gates, phrase


def test_freeing_the_gpu_waits_for_every_llama_server_holder_to_leave() -> None:
    gates = _section(_md(SKILL_MD), "## Gates", "## NEVER")
    assert "has `services.llm_server_running` true (the container check" in gates
    for name in ("bench-fit.sh", "tune-sweep.sh", "quant-headroom.sh"):
        assert "if container_running llm-server; then" in (SCRIPTS / name).read_text(), name
    assert "llm_server_running: $llm_up" in (SCRIPTS / "preflight.sh").read_text()
    assert "`bash $SK/scripts/wait-gpu-release.sh` (it returns once no `llama-server` holder is left)" in gates
    assert "G-T1 to G-T3, G-Q0 to G-Q3, G-R1, G-R2 and G6, each asked in the file that names it" in gates
    pre = json.loads((FIX / "preflight-served.json").read_text())
    assert [h["vram_mib"] for h in pre["gpu"]["holders"] if h["comm"] == "llama-server"] == [12, 7450]
    assert all("router 12 + model 7450" in _asserts(i) for i in ("served-alias-argument", "tune-capacity-card"))
    evals = (SKILL / "evals" / "evals.json").read_text()
    assert "--below-mib" not in evals and "U - H" not in evals and "H = 0" not in evals


ISOLATION_SENTENCE = ("Benchmark challenges run only on generated challenges (`gen-challenge.sh`), copied into isolated "
                      "directories under `./tmp/`, and never in or against the owner's project.")
REFS = SKILL / "references"
CHALLENGES_MD = REFS / "challenges.md"


def test_skill_states_that_challenges_run_only_in_isolated_dirs_under_tmp() -> None:
    assert ISOLATION_SENTENCE in _md(SKILL_MD)
    for path in (REFS / "tuning.md", REFS / "scoring.md", CHALLENGES_MD):
        assert ISOLATION_SENTENCE not in _md(path), path.name
        assert "run only on the bundled tasks" not in _md(path), path.name
        assert "--challenges bundled" not in _md(path), path.name


def test_skill_prose_has_no_long_shell_logic() -> None:
    """Shell logic (loops, waits, chains of more than two commands) lives in scripts/, not in prose."""
    for path in [SKILL_MD, *sorted((SKILL / "references").glob("*.md"))]:
        prose = re.sub(r"```.*?```", "", path.read_text(), flags=re.DOTALL)
        for span in re.findall(r"`([^`]+)`", prose):
            assert not re.search(r"; do\b|; then\b|\bwhile |\buntil ", span), (path.name, span)
            assert len(re.findall(r"&&|\|\||;", span)) <= 2, (path.name, span)


def test_skill_docs_name_only_existing_scripts() -> None:
    tokens = {t for p in [SKILL_MD, *sorted(REFS.glob("*.md")), *sorted((SKILL / "evals").glob("*.json"))]
              for t in re.findall(r"(?:[\w.-]+/)*[\w-]+\.sh\b", p.read_text())}
    assert "effort-run.sh" in tokens, "the scan found nothing: the test would pass vacuously"
    missing = sorted(t for t in tokens if not (SCRIPTS / Path(t).name).is_file() and not (ROOT / t).is_file())
    assert not missing, missing


def test_skill_description_fits_the_spec_limit() -> None:
    meta = yaml.safe_load(SKILL_MD.read_text().split("---")[1])
    assert meta["name"] == "onboard-local-model"
    assert 0 < len(meta["description"]) <= 1024


# Every route the description promises has a positive; every Not-for boundary has a near-miss negative.
TRIGGER_ROUTES = {"alias": r"models\.yml", "hf": r"huggingface\.co", "gguf": r"\.gguf", "quant": r"\bq[4-6]\b",
                  "challenge": r"ladder challenge", "depth": r"tok/s", "card": r"capacity card", "anchor": r"haiku|luna|composer",
                  "runtime": r"llama\.cpp image", "system one": r"laya is", "scout": r"quepa|fits"}
TRIGGER_BOUNDARIES = {"omniroute": r"omniroute", "combo": r"\bcombo\b", "memory limits": r"memory cap", "gpu-status": r"gpu-status",
                      "laya judgment": r"ask laya", "cloud": r"openrouter", "sampling": r"temperature", "fine-tuning": r"fine-tune",
                      "quantizing": r"quantize", "challenge": r"challenge"}


def test_trigger_queries_are_balanced_and_cover_routes_and_boundaries() -> None:
    rows = json.loads((SKILL / "evals" / "trigger-queries.json").read_text())
    pos = [r["query"].lower() for r in rows if r["should_trigger"]]
    neg = [r["query"].lower() for r in rows if not r["should_trigger"]]
    assert 8 <= len(pos) <= 10 and 8 <= len(neg) <= 10 and len(set(pos + neg)) == len(rows)
    assert not [k for k, rx in TRIGGER_ROUTES.items() if not any(re.search(rx, q) for q in pos)]
    assert not [k for k, rx in TRIGGER_BOUNDARIES.items() if not any(re.search(rx, q) for q in neg)]


EVAL_SETUP = re.compile(r"Eval setup|^onboard-local-model|fixture|DRY RUN|LLM_ENV_|HF_FIXTURE_DIR|Premise")
EVAL_KEYS = {"id", "prompt", "setup", "files", "expected_output", "assertions"}


def test_eval_prompts_read_as_user_turns() -> None:
    """The prompt is the user's message alone; fixture paths, env vars and the read-only rule live in setup."""
    data = json.loads((SKILL / "evals" / "evals.json").read_text())
    assert "setup" in data["harness"]
    assert "A run without setup is unsafe and not a valid run: it would act on the live host. Use the fixtures and the DRY RUN rule only." in data["harness"]
    for e in data["evals"]:
        assert set(e) <= EVAL_KEYS and not EVAL_SETUP.search(e["prompt"]), e["id"]
        assert bool(e.get("setup")) == bool(e.get("files")), e["id"]


def test_eval_gateway_host_fixtures_match_their_assertions() -> None:
    cfg = yaml.safe_load((FIX / "models-gateway.yml").read_text())
    pre = json.loads((FIX / "preflight-gateway.json").read_text())
    assert cfg["llm_server"]["enabled"] is False and pre["services"]["llm_server_enabled"] is False
    assert all(s in _asserts("gateway-only-host") for s in ("llm_server.enabled false", "5 x 1 x 3 x 300 s", "5 x 1 x 2 x 300 s"))
    assert cfg["gpu"]["pci_address"] == pre["gpu"]["pci"] and cfg["gpu"]["device_name"] == ""
    assert "gpu.device_name is empty" in _asserts("gateway-only-host")
    ev = next(e for e in json.loads((SKILL / "evals" / "evals.json").read_text())["evals"] if e["id"] == "gateway-only-host")
    assert "throughput works through a throwaway container" not in " ".join([ev["expected_output"], *ev["assertions"]])


def test_onboard_flow_lists_its_sections_and_the_quant_steps_name_their_inputs() -> None:
    flow = _md(REFS / "onboard-flow.md")
    assert "Sections: Candidate by kind (Alias for a new entry, Alias, Hugging Face H1-H5, Scout pick); Steps" in flow
    assert "quantization: Q4_K_M # pick.quant of the scout row (a local GGUF: L5)" in flow
    tuning = _md(REFS / "tuning.md")
    assert "--vram-available-mib <N> --quants <list>`" in _section(tuning, "- Q1.", "- For each candidate")
    q3 = _section(tuning, "- Q3.", "- Q4.")
    assert "A declined G-Q2: report the file Q2 fetched (its path and size) and ask whether to delete it" in q3


def test_eval_tune_enabled_no_gpu_config_fixture_matches_its_assertions(tmp_path: Path) -> None:
    fixture = FIX / "models-enabled-no-gpu.yml"
    cfg = yaml.safe_load(fixture.read_text())
    assert cfg["llm_server"]["enabled"] is True and cfg["gpu"]["pci_address"] == "" and cfg["gpu"]["device_name"] == ""
    result = _run("bash", SCRIPTS / "preflight.sh", env=_preflight_env(tmp_path, None) | {"LLM_ENV_CONFIG": str(fixture)})
    assert (result.returncode, result.stdout) == (2, "") and "gpu.pci_address is empty" in result.stderr
    text = _asserts("tune-enabled-no-gpu-config")
    assert all(s in text for s in ("exits 2", "gpu.pci_address is empty", "Gate G-T1", "LLM_ENV_ASSUME_YES=1 make setup",
                                   "T2 (make llama-enable) is not run", "T1 runs again"))


def test_onboarding_and_tuning_branch_on_a_host_without_gpu_config() -> None:
    flow = _md(REFS / "onboard-flow.md")
    step1 = _section(flow, "1. **Preflight (read-only).**", "2. **Baseline.**")
    assert all(s in step1 for s in ("`gpu.pci_address is empty`", "and stop; nothing changed", ".gpu.device_name", "the No-device order",
                                    "as a declined G3 does"))
    order = _section(flow, "**No-device order**", "1. **Preflight (read-only).**")
    marks = [order.find(m) for m in ("steps 1-4", "7a.1-7a.4 (Gate G3)", "L=~/.config/llm-env/models.yml yq -i '.gpu = load(strenv(L)).gpu' $S",
                                      "6.2-6.5", "7a.5-7a.7", "7b, 7c, 8, 9")]
    assert -1 not in marks and marks == sorted(marks), marks
    assert "still works in a throwaway container" not in flow
    scout = _section(_md(REFS / "selection-criteria.md"), "## Scout and rank", "## 1.")
    assert "`gpu.pci_address is empty`" in scout and "report its message" in scout
    tuning = _md(REFS / "tuning.md")
    t1 = _section(tuning, "- T1.", "- T2.")
    assert "`gpu.pci_address is empty`" in t1 and "with `llm_server.enabled` true, run `LLM_ENV_ASSUME_YES=1 make setup`" in t1
    assert "LLM_ENV_ASSUME_YES=1 make setup" in _section(tuning, "- T2.", "- T3.")
    assert "then `make restart`) and stop; nothing changed" in step1
    assert "the report names AGENTS.md's last step of the switch, `make restart`" in _section(tuning, "- T2.", "- T3.")
    step9 = _section(flow, "9. **Keep or revert", "## Entry template")
    assert "A host that was gateway-only before G3: the report names AGENTS.md's last step of the switch, `make restart`" in step9


JEV_RULE ="follow its own SKILL.md for how to call it"
JEV_FORBIDDEN = re.compile(r"JEV_BACKEND|JEV_ALLOW_REMOTE|GSD_PATH_JEV|consent|typesafe|--probe|ai-kit"
                           r"|laya backend|remote backend|jev client|call per item|clients? time ?outs?", re.IGNORECASE)
JEV_NAMED_IN = {"references/scoring.md", "references/selection-criteria.md", "evals/evals.json"}  # the files that ask it
# Allowed matches inside the scan, as {"<relative path>": {"<lowercased match>"}}: none. Outside the scan by design:
# scripts/print-endpoints.sh (the user-facing jev env line) and the "jev-1" request bodies that call System One's own API.
JEV_ALLOWLIST: dict[str, set[str]] = {}


def test_skill_and_touched_docs_treat_jev_as_a_black_box() -> None:
    """jev-fast-decision is asked, never described: no backend, consent, probe, env, call-rule or timeout knowledge; no ai-kit."""
    skill_files = [SKILL_MD, *sorted(REFS.glob("*.md")), *sorted((SKILL / "evals").rglob("*.json")), *sorted(SCRIPTS.glob("*.sh"))]
    docs = [ROOT / "README.md", ROOT / ".agents" / "architecture.md", ROOT / "pylib" / "capacity.py"]
    offenders = []
    for path in skill_files + docs:
        rel = str(path.relative_to(SKILL if path in skill_files else ROOT))
        text = _md(path)
        hits = {m.lower() for m in JEV_FORBIDDEN.findall(text)} - JEV_ALLOWLIST.get(rel, set())
        if "jev-fast-decision" in text and rel not in JEV_NAMED_IN:
            hits.add("jev-fast-decision")
        if hits:
            offenders.append((rel, sorted(hits)))
    offenders += [(f"references/{n}.md", ["rule missing"]) for n in ("scoring", "selection-criteria") if JEV_RULE not in _md(REFS / f"{n}.md")]
    assert not offenders, offenders


REF_NAMES = ("anchors", "challenges", "fit-math", "local-gguf", "onboard-flow", "runtimes", "scoring", "selection-criteria", "served-alias",
             "system-one", "tuning")


# One level deep: SKILL.md alone says when to load a reference. A reference names another only by its path, and only these pairs:
# files its SKILL.md route row loads, plus onboard-flow -> selection-criteria (the research subagent's brief, handed over by path).
CITES = {"onboard-flow": {"fit-math", "local-gguf", "scoring", "selection-criteria"}, "local-gguf": {"onboard-flow"},
         "served-alias": {"scoring"}, "tuning": {"scoring"}, "anchors": {"scoring"}, "challenges": {"scoring"}}
BARE_REF = re.compile(rf"(?<!references/)\b(?:{'|'.join(map(re.escape, REF_NAMES))})\.md\b")  # evals write the path without backticks
NICKNAMES = ("effort-ladder reference", "effort-ladder brief", "Local GGUF reference", "Local GGUF route", "anchors reference",
             "research brief", "fit math loaded", "the runtime route", "the quant route", "section 7 brief", "the reference table",
             "its own reference")


def test_references_cite_each_other_only_by_path_and_only_on_their_route() -> None:
    assert sorted(p.stem for p in REFS.glob("*.md")) == sorted(REF_NAMES)
    for path in sorted(REFS.glob("*.md")):
        text = path.read_text()
        named = {n for n in REF_NAMES if n != path.stem and re.search(rf"\b{re.escape(n)}\.md\b", text)}
        assert named == CITES.get(path.stem, set()), (path.name, sorted(named))
        assert not BARE_REF.findall(text), (path.name, BARE_REF.findall(text))


def test_skill_files_name_other_files_by_path_not_by_nickname() -> None:
    files = [SKILL_MD, *sorted(REFS.glob("*.md")), SKILL / "evals" / "evals.json", SKILL / "evals" / "files" / "anchors" / "route-check.txt"]
    assert not [(p.name, n) for p in files for n in NICKNAMES if n in _md(p)]
    assert not BARE_REF.findall((SKILL / "evals" / "evals.json").read_text())


def test_fit_math_has_its_own_file_and_the_onboard_rows_skip_the_ranking_sections() -> None:
    fit, sel = (REFS / "fit-math.md").read_text(), (REFS / "selection-criteria.md").read_text()
    assert re.findall(r"^## (\d)\. ", fit, re.MULTILINE) == ["1", "2", "3"] and "pick.mib + 2048" not in fit
    assert re.findall(r"^## (\d)\. ", sel, re.MULTILINE) == ["1", "2", "3", "4"] and "Fit math" not in sel
    assert "`pick.mib + 2048 - <rule 1 budget>`" in _section(_md(REFS / "selection-criteria.md"), "## Scout and rank", "## 1.")
    text = _md(SKILL_MD)
    assert "`references/fit-math.md` at step 4" in text and "section 7" not in text and "sections 1-3" not in text
    assert "| `references/challenges.md` | `references/scoring.md` to calibrate |" in text
    flow = _md(REFS / "onboard-flow.md")
    assert "`references/selection-criteria.md`, section 4, Research brief" in _section(flow, "H4. ", "H5. ")
    assert "a `pick.bpw` under 3.5 or a `ctx` under 131072" in _section(flow, "H3. ", "H4. ")
    assert "1,117 output tokens" in _md(REFS / "scoring.md")


# A rule that lives in one file: copies drift (rounds 1-7 lost points to them). Phrases match after whitespace folding.
STATED_ONCE = {"5 x 3 x 2 x T": "references/scoring.md", "not a head-to-head": "references/scoring.md",
               "delta > 10": "references/scoring.md", "`ram.available_mib` (say so)": "SKILL.md",
               "follow each matching row in table order": "SKILL.md", "G5 and G-Q3 count as a no": "SKILL.md",
               "rewrites `gpu.backend`": "SKILL.md", "**Gate G-R1** before `make start`": "references/scoring.md",
               "says the stack is not running": "references/scoring.md",
               "kills a foreground run at 600 s": "SKILL.md"}
RETIRED_COPIES = ("may run inline", "skipped at Q2 (none measured)", "No other session may write the repo", "A live benchmark needs no other writer on the repo", "5 x 3 x 2 x 300", "x 300 s", "re-scouted with", "the onboard flow's step 2",
                  "a quant switch is a change of model", "under bypass, announce the facts and proceed",
                  "rule 1 (a live entry", "rule-1 candidate", "Read only your kind's subsection",
                  "largest fitting quant (H1's call", "`SKILL.md` Gotchas", "the `vram_mib` of the `.gpu.holders[]` entry",
                  "`make start` when the stack is down", "L3 at 4.4", "(another quant of that model)",
                  "per the research brief loaded at this step", "and paths are in `SKILL.md`", "and so are `$SK` and paths",
                  "Reasoning models spend output tokens", "Remember that it was stopped, for R6", "when llm-server served before G2",
                  "G2 has not stopped it", "the prior state (serving, or gateway-only)", "(step 9; no `make stop-llama`)",
                  "enabled but stopped: `make start-llama` after P0", "Flag `services.llm_server_running`",
                  "**Steps 1-9** in this order", "with L6 live entry (7a.2)", "at 4.4 (run before G1), G1, 7a.2 and 9",
                  "(the enabled alias: the re-measure)", "A mismatch on a file that was already there",
                  "curl -fL --continue-at - -o <models_dir>",
                  "--below-mib", "U - H", "U and H", "each run of characters outside",
                  "a `.gpu.holders[]` entry whose `comm` is", "reads its holders from that read",
                  "on the holders of the last R1 preflight",
                  "7a resumes at 7a.5", "from 7a.5 when item 8", "when step 1 moved Gate G3",
                  "screens with the same flag presets", "`budget` reports 0 MiB of RAM for llm-server",
                  "`make clean` keeps them; `make prune` deletes them", "or a failed step after G2:", "G2 stopped nothing",
                  "CLI (every `--out` must resolve", "| `drop-minus-one` |", "R5 G3, interference",
                  "record whether `my-coding-lite` exists", "only when Q1 recorded", "`my-coding-lite`, when it exists, is unavailable")


def test_shared_rules_are_stated_once() -> None:
    files = {"SKILL.md": SKILL_MD, **{f"references/{p.name}": p for p in sorted(REFS.glob("*.md"))}}
    for phrase, owner in STATED_ONCE.items():
        assert sorted(rel for rel, p in files.items() if phrase in _md(p)) == [owner], phrase
    assert not [(rel, c) for rel, p in files.items() for c in RETIRED_COPIES if c in _md(p)]


SKILL_MD_MAX_BYTES = 11_000  # lowered from the 14,500 target of 2026-10-31-judge-round-1-fixes; raise only with a reason


def test_skill_md_is_a_router_that_loads_every_reference() -> None:
    text = SKILL_MD.read_text()
    assert len(text.encode()) <= SKILL_MD_MAX_BYTES
    for name in REF_NAMES:
        assert f"references/{name}.md" in text, name
    assert set(re.findall(r"\b([a-z][a-z-]*)\.md\b", text)) <= set(REF_NAMES)


DESCRIPTION_ROUTES = ("models.yml alias", "Hugging Face", ".gguf", "re-measure", "got slower", "quants", "capacity card", "Haiku",
                      "llama.cpp image", "Laya", "ladder challenge", "Not for OmniRoute")


def test_skill_md_routes_near_misses_runs_long_commands_detached_and_names_the_global_kv_key() -> None:
    text = _md(SKILL_MD)
    assert "a near-miss alias" in text and '"no, search it" after a near-miss' in text
    rule2 = _section(text, "2. **Benchmark runs.**", "3. **Like for like.**")
    assert all(s in rule2 for s in ("Run each `bench-fit.sh` and download (`fetch-gguf.sh`, `quant-headroom.sh fetch`) detached",
                                    "the shell tool kills a foreground run at 600 s", "exits 143 or is lost, once its PID is gone"))
    assert "single" not in rule2
    assert "the model argument wins (a tune or quant request on the enabled alias takes that row)" in text
    assert "the tune row, references/tuning.md, not the re-measure" in _asserts("tune-named-enabled-alias")
    assert all(s in rule2 for s in ("`tune-sweep.sh`, `effort-run.sh` and `latency-profile.sh` run goes to one subagent", "own new `--out`"))
    gotchas = text.split("## Gotchas", 1)[1]
    assert "`runtime.cache_type_k/v`" in gotchas and "make benchmark" not in gotchas and "--no-recreate" not in text
    assert "NEVER run `make benchmark`: a Vulkan failure rewrites `gpu.backend`" in _section(text, "## NEVER", "## Gotchas")
    assert "NEVER leave a bench container" not in text
    assert "overrides another row's Do NOT load cell" not in text and "also serves authoring" not in text
    meta = yaml.safe_load(SKILL_MD.read_text().split("---")[1])
    assert not [w for w in DESCRIPTION_ROUTES if w not in meta["description"]]
    assert all(w in meta["description"] for w in ("temperature/top_p", "LoRA fine-tuning", "converting safetensors to GGUF", "except my-coding-lite"))
    assert not [w for w in ("sampling", "quantizing", "other combos") if w in meta["description"]]


def test_references_keep_host_specific_evidence_and_give_every_flag_a_destination() -> None:
    ev = _section(_md(REFS / "selection-criteria.md"), "## 1. Is it really agentic?", "## 2.")
    assert "Rank evidence from strongest to weakest" not in ev and "gguf.chat_template" in ev and "agentic_evidence" in ev
    assert "Set expectations honestly" not in _md(REFS / "anchors.md")
    flags = _section(_md(REFS / "runtimes.md"), "## Flags worth an A/B", "## Other runtimes")
    assert "tune-sweep.sh" in flags and "measure with `bench-fit.sh`" not in flags and "no models.yml key" in flags
    assert "it wraps `challenges/gen.mjs`" not in _md(CHALLENGES_MD)
    assert re.search(r"^## Published figures \(retrieved 2026-10-30 from secondary coverage; re-check before quoting\)$",
                     (REFS / "anchors.md").read_text(), re.MULTILINE)
    runtimes = (REFS / "runtimes.md").read_text()
    assert "## Last seen on this host (2026-10-30; re-check)" in runtimes and "These are last-seen values" not in runtimes


def test_route_table_names_existing_sections_and_steps() -> None:
    text = SKILL_MD.read_text()
    rows = "\n".join(line for line in text.splitlines() if line.startswith("| ") and "references/" in line)
    pairs = re.findall(r"`references/([a-z-]+)\.md`, ([A-Z][A-Za-z ]+?)(?: \(| \|)", rows)
    assert len(pairs) >= 6, pairs  # Alias, Hugging Face, Steps, Find the repo, Scout and rank, Standalone quant comparison
    def heading(section: str) -> str:
        return rf"^#+ {re.escape(section)}(?: \([^)]*\))?[ \t]*$"

    assert not re.search(heading("Alias"), "### Alias for a new entry", re.MULTILINE)
    for name, section in pairs:
        assert re.search(heading(section), (REFS / f"{name}.md").read_text(), re.MULTILINE), (name, section)
    flow = (REFS / "onboard-flow.md").read_text()
    for step in sorted(set(re.findall(r"at step (\d)", text))):
        assert re.search(rf"^{step}\. \*\*", flow, re.MULTILINE), step
    served = (REFS / "served-alias.md").read_text()
    for step in sorted(set(re.findall(r"at R(\d)", text))):
        assert re.search(rf"^R{step}\. \*\*", served, re.MULTILINE), step
    assert re.search(r"^### Scout pick", flow, re.MULTILINE) and "| `references/challenges.md` |" in rows
    assert "| `references/served-alias.md` |" in rows and "`references/local-gguf.md`, then `references/onboard-flow.md`, Steps" in rows


def test_onboard_step_4_selects_the_candidate_before_validating_it() -> None:
    flow = _md(REFS / "onboard-flow.md")
    step4 = _section(flow, "4. **Fit and configure.**", "5. **Baseline lane")
    marks = [step4.find(m) for m in ("models select <alias>", "validate-gguf --models-dir <models_dir>", "budget --models-dir <models_dir>")]
    assert -1 not in marks and marks == sorted(marks), marks
    assert "<dir>" not in flow and re.search(r"^H5\. ", (REFS / "onboard-flow.md").read_text(), re.MULTILINE)
    h5 = _section(flow, "H5. ", "### Scout pick")
    assert "add nothing" in h5 and "(step 2)" not in h5 and "step 4's" not in h5
    assert all(s in _asserts("hf-repo-argument") for s in ("models select foo-9b", "before validate-gguf"))
    assert "4.4 ran first" not in step4


def test_onboard_step_4_writes_the_settings_into_the_scratch_candidate() -> None:
    flow = _md(REFS / "onboard-flow.md")
    step4 = _section(flow, "4. **Fit and configure.**", "5. **Baseline lane")
    marks = [step4.find(m) for m in ("yq -i '(.models[] | select(.alias == \"<alias>\")) |=", "**Gate G1**", "models select <alias>")]
    assert -1 not in marks and marks == sorted(marks), marks
    assert "`runtime.cache_type_k/v`" in step4 and "KV `q5_1` unless" not in step4 and "del(.n_cpu_moe)" in step4
    assert "from `$S`" in _section(flow, "**7a. Apply and serve", "**7b.")
    h2 = _section(flow, "H2. ", "H3. ")
    assert all(s in h2 for s in ("`pick.split` true", "with `--single-file`", "When its `fit` is `vram`", "largest fitting single-file quant",
                                 "offer none"))
    assert "the fallback (H2)" in _section(flow, "H3. ", "H4. ")
    assert "--out tmp/model-scout/<run>/bench-baseline" in _section(flow, "6. **Screen throughput.**", "7. **Apply")
    assert "path and size" in _section(flow, "9. **Keep or revert", "## Entry template")
    assert "never touches a file it did not download" in step4
    assert all(s in _asserts("hf-repo-argument") for s in ("yq -i", "runtime.cache_type_k/v"))


def test_g1_downloads_through_fetch_gguf_and_can_resume() -> None:
    flow = _md(REFS / "onboard-flow.md")
    g1 = _section(_section(flow, "4. **Fit and configure.**", "5. **Baseline lane"), "**Gate G1**", "4. Select and check")
    assert "`bash $SK/scripts/fetch-gguf.sh --url <url> --dest <models_dir>/<entry file> --size <size_bytes>`" in g1
    assert all(s in g1 for s in ("`--sha256 <pick.sha256>`", "`<entry file>.part`", "size only")) and "curl" not in g1
    assert "left a file or a `.part` in the models directory" in _section(flow, "9. **Keep or revert", "## Entry template")
    assert "fetch-gguf.sh" in _section(_md(REFS / "tuning.md"), "- Q2.", "- Q3.")


def test_hugging_face_route_sends_the_baselines_own_file_to_the_re_measure() -> None:
    flow = _md(REFS / "onboard-flow.md")
    h2 = _section(flow, "H2. ", "H3. ")
    assert all(s in h2 for s in ("is the `file` of the baseline (step 2)", "`pick.bytes` equal to its `size_bytes`",
                                 "`SKILL.md`'s enabled-alias row", "before H3 and H4", "With another size", "report both sizes and stop"))
    assert h2.index("the `file` of the baseline") < h2.index('`fit: "unknown"`')


def test_alias_reuse_needs_the_same_size() -> None:
    raw = (REFS / "onboard-flow.md").read_text()
    a1 = " ".join(raw.split("- A1. ", 1)[1].split("- A2. ", 1)[0].split())
    assert all(s in a1 for s in ("that `file` and `size_bytes`: its alias", "report both sizes it prints and stop before G1"))
    assert "`<bytes>` is `pick.bytes`" in " ".join(raw.split("### Alias for a new entry", 1)[1].split("- A1. ", 1)[0].split())
    flow = _md(REFS / "onboard-flow.md")
    assert "Under A1 (a live entry other than the baseline has this `file` and size)" in _section(flow, "H5. ", "### Scout pick")
    assert "(A1 checked its size)" in _section(flow, "**7a. Apply and serve", "**7b.")
    l1 = _section(_md(REFS / "local-gguf.md"), "L1. ", "L2. ")
    assert "its file is not in the models directory" in l1 and "before any copy" in l1
    assert all(s in l1 for s in ("On A1, L2 is `bash $SK/scripts/resolve-target.sh <A1 alias> --scratch-config $S`",
                                 "`<alias>` is the A1 alias from then on", "4.2 writes nothing, no L5",
                                 "L4's copy is its G1, never the entry's `url`"))


def test_routes_have_progress_checklists() -> None:
    flow = _md(REFS / "onboard-flow.md")
    steps = _section(flow, "## Steps", "## Entry template")
    assert "served alias" not in steps.lower() and "Progress checklist" in steps
    for name in ("served-alias",):
        assert "Progress checklist" in _md(REFS / f"{name}.md"), name
    assert "Run order (copy it into your task list" in _md(REFS / "local-gguf.md")
    raw = (REFS / "onboard-flow.md").read_text().split("## Steps", 1)[1].split("\n1. **Preflight", 1)[0]
    assert re.findall(r"^- (\d) ", raw, re.MULTILINE) == [str(n) for n in range(1, 10)] and "hand-back" in raw
    alias = re.split(r"^### Alias\n", (REFS / "onboard-flow.md").read_text(), flags=re.MULTILINE)[1].split("### Hugging Face")[0]
    assert "is a re-measure (`SKILL.md` Route)" in " ".join(alias.split())
    tuning = _md(REFS / "tuning.md")
    assert "Progress checklist" in _section(tuning, "### Standalone quant comparison", "- Q1.")
    assert "`ps` by PID" in _section(tuning, "- T9.", "## Interference gate")


def test_served_alias_reference_covers_a_stopped_server_and_a_declined_g3() -> None:
    served = _md(REFS / "served-alias.md")
    assert all(f"R{n}. " in served for n in range(1, 7)) and "R7. " not in served
    r1 = _section(served, "R1. ", "R2. ")
    assert all(s in r1 for s in ("`services.llm_server_enabled` false", "`services.llm_server_running` false", "Serving the lane's model",
                                 "then R1 again"))
    assert "A no to G-R2: llm-server stays stopped" in _section(served, "R5. ", "R6. **Report**")
    assert "`make stop-llama` hands it back" in served.split("R6. **Report**", 1)[1] and "G5 has nothing to revert" in served
    assert "--out tmp/model-scout/<run>/effort-served" in served and "--out tmp/model-scout/<run>/bench-served" in served
    assert all(s in r1 for s in ("A no to G-R1", "capacity show"))
    assert "`make start-llama` only when llm-server served before R1" in _section(served, "R4. ", "R5. ")
    assert "was stopped before R1 and runs now" in served.split("R6. **Report**", 1)[1]
    assert "G3's facts also say that this step runs `make start`" in _section(_md(REFS / "onboard-flow.md"), "**7a. Apply and serve", "**7b.")
    flow = _md(REFS / "onboard-flow.md")
    assert "Served alias re-measure" not in flow and "R1. " not in flow


def test_one_rule_serves_a_stopped_llm_server_and_each_route_hands_it_back() -> None:
    rule = _section(_md(REFS / "scoring.md"), "## Serving the lane's model", "## Running a lane")
    assert all(s in rule for s in ("`gpu.pci_address is empty`", "`services.llm_server_enabled` false", "no local lane",
                                   "before any lane or G4", "`make llama-enable MODEL=<alias>`"))
    for name in ("anchors", "challenges"):
        assert "enabled but stopped: serve it per" not in _md(REFS / f"{name}.md"), name
    assert all(s in rule for s in ("`services.llm_server_running` false", "make start-llama", "says the stack is not running",
                                   "**Gate G-R1** before `make start`", "A no to G-R1", "`make stop-llama` last",
                                   "a kept change included", "fails its budget check"))
    cite = "`references/scoring.md`, Serving the lane's model"
    for name in ("anchors", "challenges"):  # every route that runs a local lane serves and hands back
        assert cite in _md(REFS / f"{name}.md") and "`make stop-llama` after" in _md(REFS / f"{name}.md"), name
    flow = _md(REFS / "onboard-flow.md")
    step1 = _section(flow, "1. **Preflight (read-only).**", "2. **Baseline.**")
    assert cite in step1 and "A no to G-R1 ends the run here" in step1
    step9 = _section(flow, "9. **Keep or revert", "## Entry template")
    assert "A no to G5, a declined G3, a 6.4 reject, or a failed step once G2 or G3 has run" in step9
    assert "Hand-back: the first step-1 read found llm-server enabled but stopped and it runs now (a yes to G5 included): `make stop-llama`" in step9
    assert "After that bullet (a restore or nothing to restore), when the first step-1 read found llm-server serving: `make start-llama`" in step9
    assert "a failure while neither G2 nor G3 has run" in step9
    assert "a failed step once G2 or G3 has run (the No-device order runs G3 without G2)" in step9
    assert all(s in _section(flow, "**7a. Apply and serve", "**7b.") for s in ("When the last step-1 read found the stack down",
                                                                         "never after a yes to G-R1, which started it"))
    r6 = _md(REFS / "served-alias.md").split("R6. **Report**", 1)[1]
    assert "Only llm-server is restored" not in r6 and cite in r6
    assert cite in _section(_md(REFS / "served-alias.md"), "R1. ", "R2. ")
    tuning = _md(REFS / "tuning.md")
    t1 = _section(tuning, "- T1.", "- T2.")
    assert cite in t1 and "the Hand-back bullet of Quant headroom stops it again" in t1
    assert "make stop-llama" not in _section(tuning, "- T9.", "## Interference gate")
    assert "when llm-server was serving at P0 (or T1), then the Hand-back bullet" in _section(tuning, "- Q1.", "- For each candidate")
    hand_back = tuning.split("- G-Q3 yes", 1)[1]
    assert "when T1 or P0 found llm-server stopped, `make stop-llama`" in hand_back
    assert all(s in hand_back for s in ("a P0 stop or a declined G-Q0", "a failed P1 gate", "a declined G-T2", "Q1's no-fit verdict",
                                        "every candidate skipped at Q2 or Q3"))
    assert "A declined G-Q1 or a `fetch` exit 1: report it and skip this candidate" in _section(tuning, "- Q2.", "- Q3.")
    assert ("Every candidate skipped at Q2 or Q3 (none measured): no G-Q3; `make start-llama` when llm-server was serving at P0 (or T1), "
            "then the Hand-back bullet") in tuning
    anchors = _md(REFS / "anchors.md")
    item1 = _section(anchors, "1. **Route check**", "2. **Gate G4**")
    assert item1.index("`bash $SK/scripts/preflight.sh` comes first") < item1.index("pi auth check")
    assert "preflight.sh` first" not in _section(anchors, "3. The first local lane", "4. `bash $SK/scripts/compare-runs.sh")
    standalone = _section(tuning, "### Standalone quant comparison", "Ask yourself first:")
    assert cite in standalone and "the Hand-back bullet stops it again" in standalone
    assert "a `preflight.sh` read before P0 shows (`services.llm_server_running` false)" in standalone
    assert "G-Q3; hand-back; leftover check" in _section(tuning, "Progress checklist", "Before Q1")
    latency = _section(tuning, "## Latency profile and model report", "## Subagent brief")
    assert "One subagent (`SKILL.md` rule 2)" in latency and "in the background, polled" not in latency
    text = _md(SKILL_MD)
    assert "(`alias`, `enabled: true`) on a host with llm-server enabled (preflight `services.llm_server_enabled`): re-measure" in text
    assert all(f"`references/scoring.md` {s}" in text for s in ("at step 1", "at R1", "after P0", "at T1"))
    assert not [s for s in ("`references/scoring.md` at step 5", "at R2", "at T8", "before T8 or Q8") if s in text]


def test_a_kept_switch_moves_my_coding_lite_and_a_revert_restores_the_router_profile() -> None:
    assert 'bash "${REPO_DIR}/scripts/omniroute-local-combo.sh"' in (ROOT / "scripts" / "start.sh").read_text()  # start-llama ensures it
    assert '_local_combo_skip("interference gate not passed; run make model-interference")' in (ROOT / "llmenv.py").read_text()
    step9 = _section(_md(REFS / "onboard-flow.md"), "9. **Keep or revert", "## Entry template")
    keep = _section(step9, "- Keep:", "- A declined G1")
    assert keep.index("`make local-combo`") < keep.index("`make setup-local-llm-agents`")
    assert "7a.6's `make start-llama` skipped `my-coding-lite`" in keep and "a `skipped` line: report its reason" in keep
    after = _section(step9, "- After that bullet", "- Profile:")
    assert "setup-local-llm-agents" not in after and "stops there" not in after
    profile = _section(step9, "- Profile:", "- Hand-back:")
    assert "not kept and 7a.7 ran, whatever the first step-1 read found: `make setup-local-llm-agents`" in profile
    tuning = _md(REFS / "tuning.md")
    assert "`make start-llama` creates or updates `my-coding-lite` for the served alias" in tuning
    assert "make local-combo" not in _section(tuning, "- Q11.", "- A failed Q4-Q6") + _section(tuning, "- G-Q3 yes:", "- Hand-back,")
    evals = (SKILL / "evals" / "evals.json").read_text()
    assert "Q1 recorded" not in evals and "the Q1 record" not in evals and "the combo is not created" not in evals


def test_eval_onboard_keep_fixtures_match_their_assertions() -> None:
    pre = json.loads((FIX / "preflight-served.json").read_text())
    assert pre["services"]["llm_server_running"] is True and pre["runtime"]["enabled_models"] == ["gemma4"]
    text = _asserts("onboard-keep")
    assert text.index("make local-combo") < text.index("make setup-local-llm-agents")
    assert all(s in text for s in ("llama-cpp/gemma4", "llama-cpp/foo-9b", "no capacity card", "no make stop-llama", "models.yml.example"))


def test_local_gguf_reference_owns_the_name_check_copy_and_live_entry() -> None:
    local = _md(REFS / "local-gguf.md")
    assert all(f"L{n}. " in local for n in range(1, 7)) and "L7. " not in local
    order = _section(local, "Run order", "L1. ")
    marks = [order.find(m) for m in ("L1 name check", "L2 scratch config", "Steps 1 to 4.2", "L3 fit", "L4 copy",
                                      "Step 5 (", "L5 live entry", "Step 6 (", "Steps 7a to 8", "Step 9 with L6")]
    assert -1 not in marks and marks == sorted(marks), marks
    assert "the No-device order of `references/onboard-flow.md` from 7a.1 on" in local
    assert not [s for s in ("in place of", "replaces Steps", "L items replace") if s in local]
    l1 = _section(local, "L1. ", "L2. ")
    assert all(s in l1 for s in ("`in_models_dir` true", "never copy over it", "the owner named that alias"))
    assert "`new-alias.sh` (A1, with this file's byte size) decides now" in l1
    assert l1.index("A live entry has this `file`") < l1.index("`in_models_dir` true")  # gemma4-v2-Q4_K_M.gguf in the models dir routes to gemma4's row
    l3 = _section(local, "L3. ", "L4. ")
    assert all(s in l3 for s in ("Fit, before any copy", "right after Steps 4.2", "nothing is copied"))
    assert "asked only after L3 found the fit feasible" in _section(local, "L4. ", "L5. ")
    assert "Never move or delete the original" in _section(local, "L4. ", "L5. ")
    l5 = _section(local, "L5. ", "L6. ")
    assert all(s in l5 for s in ("decided before G3", "file://<original path>", "G-Q3", "`pick.sha256` equals its `sha256sum`",
                                 "`https://huggingface.co/<repo>/resolve/<sha>/<pick.file>`"))
    assert not re.search(r"^### Local GGUF", (REFS / "onboard-flow.md").read_text(), re.MULTILINE)


def test_onboard_flow_names_alias_items_a1_to_a3() -> None:
    raw = (REFS / "onboard-flow.md").read_text()
    new_entry = raw.split("### Alias for a new entry", 1)[1].split("\n### ", 1)[0]
    assert re.findall(r"^- (A\d)\. ", new_entry, re.MULTILINE) == ["A1", "A2", "A3"]
    assert "`<that alias>-<quant lowercase>`" in " ".join(new_entry.split())
    assert "`bash $SK/scripts/new-alias.sh --file <entry file> --size <bytes> --quant <quant>`" in " ".join(new_entry.split())
    flow = " ".join(raw.split())
    assert "Under A1" in _section(flow, "H5. ", "### Scout pick") and "An Alias-row or A1 candidate" in flow
    assert "A local GGUF runs these Steps in the run order of `references/local-gguf.md`." in flow and "L items replace" not in flow


def _alias(tmp_path: Path, *args: str, models: list[dict] | None = None) -> subprocess.CompletedProcess[str]:
    cfg = tmp_path / "models.yml"
    cfg.write_text(yaml.safe_dump({"models": models or []}))
    return _run("bash", SCRIPTS / "new-alias.sh", *args, env={**os.environ, "LLM_ENV_CONFIG": str(cfg)})


def test_new_alias_names_the_eval_candidates_from_the_fixture_config() -> None:
    def alias(*args: str) -> dict:
        result = _run("bash", SCRIPTS / "new-alias.sh", *args, env={**os.environ, **FIX_ENV})
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    assert alias("--file", "Qwen3.8-Coder-9B-Q5_K_M.gguf", "--size", "6710886400", "--quant", "Q5_K_M") == {"alias": "qwen3-8-coder-9b", "rule": "A2"}
    assert alias("--file", "sub/gemma4-v2-Q5_K_M.gguf", "--size", "1", "--quant", "Q5_K_M") == {"alias": "gemma4-q5_k_m", "rule": "A2"}
    assert alias("--file", "gemma4-v2-Q4_K_M.gguf", "--size", "7381381664", "--quant", "Q4_K_M") == {"alias": "gemma4", "rule": "A1"}
    assert alias("--file", "Foo-9B-Q5_K_M.gguf", "--size", "1", "--quant", "Q5_K_M") == {"alias": "foo-9b", "rule": "A2"}


def test_new_alias_stops_on_the_same_file_name_with_another_size() -> None:
    result = _run("bash", SCRIPTS / "new-alias.sh", "--file", "gemma4-v2-Q4_K_M.gguf", "--size", "5", "--quant", "Q4_K_M",
                  env={**os.environ, **FIX_ENV})
    assert (result.returncode, result.stdout) == (1, "") and "7381381664" in result.stderr and "gemma4" in result.stderr


def test_new_alias_appends_a_counter_for_a_taken_name_and_works_without_a_quant(tmp_path: Path) -> None:
    taken = [{"alias": "mymodel", "file": "other.gguf", "quantization": "Q8_0", "size_bytes": 1},
             {"alias": "mymodel-2", "file": "other2.gguf", "quantization": "Q8_0", "size_bytes": 1}]
    assert json.loads(_alias(tmp_path, "--file", "/home/x/MyModel.gguf", "--size", "9", models=taken).stdout) == {"alias": "mymodel-3", "rule": "A3"}
    assert json.loads(_alias(tmp_path, "--file", "MyModel.gguf", "--size", "9").stdout) == {"alias": "mymodel", "rule": "A2"}


def test_new_alias_needs_a_token_for_the_sibling_quant_name(tmp_path: Path) -> None:
    live = [{"alias": "mymodel", "file": "MyModel-Q8_0.gguf", "quantization": "Q8_0", "size_bytes": 1}]
    assert json.loads(_alias(tmp_path, "--file", "MyModel.gguf", "--size", "9", models=live).stdout) == {"alias": "mymodel-2", "rule": "A3"}
    no_file = [{"alias": "remote", "quantization": "Q4_K_M"}]  # an entry without a file never breaks the sibling check
    assert json.loads(_alias(tmp_path, "--file", "X-Q5_K_M.gguf", "--size", "1", "--quant", "Q5_K_M", models=no_file).stdout) == {"alias": "x", "rule": "A2"}


def test_new_alias_caps_the_name_at_60_characters(tmp_path: Path) -> None:
    out = json.loads(_alias(tmp_path, "--file", "Ab_" * 30 + "Q4_K_M.gguf", "--size", "1", "--quant", "Q4_K_M").stdout)
    assert out == {"alias": ("ab-" * 20)[:-1], "rule": "A2"}  # 59 characters: the cut at 60 ends on a '-'


@pytest.mark.parametrize("args", [["--file", "m.gguf", "--size", "x"], ["--file", "m.gguf", "--size", "1", "--quant", "Q4 K"], ["--size", "1"]])
def test_new_alias_rejects_bad_arguments(tmp_path: Path, args: list[str]) -> None:
    assert _alias(tmp_path, *args).returncode == 2


ASK_BLOCKS = {"onboard-flow": 1, "served-alias": 1, "local-gguf": 1, "tuning": 2, "selection-criteria": 1, "anchors": 1}


def test_every_flow_opens_with_questions_to_ask_first() -> None:
    """Thinking guidance, not more steps: 3-4 one-line questions after each "Ask yourself first:" line."""
    for name, count in ASK_BLOCKS.items():
        lines = (REFS / f"{name}.md").read_text().splitlines()
        starts = [i for i, line in enumerate(lines) if line == "Ask yourself first:"]
        assert len(starts) == count, name
        for i in starts:
            block = []
            for line in lines[i + 1:]:
                if not line.startswith("- "):
                    break
                block.append(line)
            assert 3 <= len(block) <= 4 and all("?" in line for line in block), (name, block)


def test_only_bench_fit_mounts_the_models_directory() -> None:
    """bench-fit.sh mounts $MODELS_DIR with :z (an SELinux relabel of the whole tree); no other script may."""
    mounts = [p.name for p in sorted(SCRIPTS.glob("*.sh")) if re.search(r"-v\s+\"?\$MODELS_DIR:/models:ro,z", p.read_text())]
    assert mounts == ["bench-fit.sh"]


def test_every_benchmark_out_dir_is_named_once() -> None:
    """Two lanes never share an --out: effort-run.sh refuses a used one and bench-fit.sh overwrites its side files."""
    # DIR is gen-challenge.sh's usage placeholder and tmp/lib-check its generator check, neither a benchmark lane.
    outs = [o for p in [SKILL_MD, *sorted(REFS.glob("*.md"))] for o in re.findall(r"--out ([^\s`)]+)", p.read_text())
            if o not in ("DIR", "tmp/lib-check")]
    assert "<out>" in outs and len(outs) == len(set(outs)), sorted({o for o in outs if outs.count(o) > 1})
    run_def = next(line for line in SKILL_MD.read_text().splitlines() if "`<run>` is" in line)
    assert "`-2`" in run_def
    assert all(any(t in o for t in ("<run>", "<stamp>", "<out>")) or o.startswith("QR") for o in outs), outs
    tuning = _md(REFS / "tuning.md")
    assert "--out QR/base/effort" in tuning and "`QR` is `tmp/model-tune/quant-<stamp>`" in tuning
    assert "`<YYYYMMDD>-scout`" in _md(REFS / "selection-criteria.md") and "`<YYYYMMDD>-anchors`" in _md(REFS / "anchors.md")


LS, PY = "llama-server", "python3"


def _fake_preflight(tmp_path: Path, *states: tuple[int | None, list[str] | None]) -> Path:
    """One state per run (the last one repeats): used_mib and the comm of each GPU holder; None leaves the holders key out."""
    lines = tmp_path / "states.jsonl"
    lines.write_text("".join(json.dumps({"gpu": {"used_mib": used} | ({} if comms is None else {
        "holders": [{"pid": n, "comm": c, "vram_mib": 100} for n, c in enumerate(comms)]})}) + "\n" for used, comms in states))
    script = tmp_path / "preflight.sh"
    script.write_text(f'#!/usr/bin/env bash\nhead -n1 {lines}\n[ "$(wc -l < {lines})" -le 1 ] || sed -i 1d {lines}\n')
    script.chmod(0o755)
    return script


def _wait(tmp_path: Path, *states: tuple[int | None, list[str] | None], extra: tuple[str, ...] = (),
          timeout: str = "30") -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "ONBOARD_PREFLIGHT": str(_fake_preflight(tmp_path, *states))}
    return _run("bash", SCRIPTS / "wait-gpu-release.sh", *extra, "--interval", "0", "--timeout", timeout, env=env)


def test_wait_gpu_release_returns_once_no_llama_server_holds_the_gpu_even_when_system_one_reloads(tmp_path: Path) -> None:
    # llm-server stops while a Laya request reloads System One: used VRAM rises, and the GPU is still released
    result = _wait(tmp_path, (13662, [LS, LS, PY]), (6300, [LS, PY]), (10000, [PY]))
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["released"] is True and out["used_mib"] == 10000


def test_wait_gpu_release_times_out_with_the_gpu_holders(tmp_path: Path) -> None:
    result = _wait(tmp_path, (9000, [LS, LS, PY]), timeout="0")
    assert result.returncode == 1
    out = json.loads(result.stdout)
    assert out["released"] is False and [h["comm"] for h in out["holders"]] == [LS, LS, PY]


def test_wait_gpu_release_rejects_unknown_flags_and_the_retired_target(tmp_path: Path) -> None:
    assert _run("bash", SCRIPTS / "wait-gpu-release.sh", "--nope").returncode == 2
    assert _wait(tmp_path, (1200, [PY]), extra=("--below-mib", "2000"), timeout="0").returncode == 2


@pytest.mark.parametrize("args", [["--timeout"], ["--timeout", "-1"], ["--timeout", "x"], ["--interval", "1.5"]])
def test_wait_gpu_release_rejects_missing_or_non_numeric_values(args: list[str]) -> None:
    result = _run("bash", SCRIPTS / "wait-gpu-release.sh", *args)
    assert result.returncode == 2 and result.stderr.strip()


@pytest.mark.parametrize("used", [None, 1200])
def test_wait_gpu_release_treats_a_missing_holder_list_as_unreadable(tmp_path: Path, used: int | None) -> None:
    result = _wait(tmp_path, (used, None), timeout="0")
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"released": False, "used_mib": used, "holders": []}


TUNE_UV = r"""cfg=; p=
for a in "$@"; do [ "$p" = --config ] && cfg="$a"; p="$a"; done
case "$*" in
*" models select "*) echo '{}' ;;
*" budget "*)
    if [ -n "${BUDGET_ERROR:-}" ]; then echo "{\"error\": \"$BUDGET_ERROR\"}"; exit 1; fi
    ctx="$(yq '.models[] | select(.alias == "m") | .ctx_size' "$cfg")"
    f=false; { [ "$ctx" = 131072 ] || [ -n "${ALL_FIT:-}" ]; } && f=true
    echo "{\"vram_feasible\": $f, \"ram_feasible\": true, \"feasible\": $f, \"available_mib\": 8321}"
    [ "$f" = true ] ;;
esac
"""
TUNE_BENCH = r"""out=; depths=; p=
for a in "$@"; do [ "$p" = --out ] && out="$a"; [ "$p" = --depths ] && depths="$a"; p="$a"; done
echo "$*" >>"$CALLS"
jq -n --arg d "$depths" '{gpu: {model_vram_mib: 9000}, tps: ($d | split(",") | map(tonumber | {depth: ., pp_tps: 1000, tg_tps: 50}))}' >"$out/bench-m.json"
"""


def _tune_env(tmp_path: Path, podman: str = "") -> dict[str, str]:
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    for name, body in {"uv": TUNE_UV, "podman": podman}.items():
        (stub / name).write_text(f"#!/usr/bin/env bash\n{body}")
        (stub / name).chmod(0o755)
    bench = tmp_path / "bench-stub.sh"
    bench.write_text(f"#!/usr/bin/env bash\n{TUNE_BENCH}")
    cfg = tmp_path / "models.yml"
    cfg.write_text("server: {api_key: live-secret}\nomniroute: {initial_password: live-pass}\nruntime: {cache_type_k: q5_1}\n"
                   "models: [{alias: m, file: m.gguf, ctx_size: 131072}]\n")
    tools = {Path(shutil.which(t) or "/usr/bin/" + t).parent for t in ("yq", "jq")}
    return {"PATH": f"{stub}:/usr/bin:/bin:{':'.join(map(str, tools))}", "HOME": str(tmp_path), "LLM_ENV_CONFIG": str(cfg),
            "LLM_ENV_MODELS_DIR": str(tmp_path), "TUNE_BENCH_CMD": str(bench), "CALLS": str(tmp_path / "calls")}


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_tune_sweep_skips_infeasible_fits_and_commits_each_run(tmp_path: Path) -> None:
    out = _out(tmp_path, "sweep")
    result = _run("bash", SCRIPTS / "tune-sweep.sh", "--alias", "m", "--out", out, "--ctx", "131072,262144", "--kv", "q5_1",
                  "--ubatch", "512,1024", env=_tune_env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("upper bound: 4 runs x 3600s"), result.stdout
    fits = _jsonl(out / "fit.jsonl")
    assert len(fits) == 2 and fits[1]["ctx"] == 262144 and fits[1]["vram_feasible"] is False
    rows = _jsonl(out / "results.jsonl")
    assert len(rows) == 3 and not any("c262144" in str(r["id"]) for r in rows)
    assert all(r["vram_available_mib"] == 8321 and r["model_vram_mib"] == 9000 and r["status"] == "ok" for r in rows)
    depths = [line.split("--depths ")[1].split()[0] for line in (tmp_path / "calls").read_text().splitlines()]
    assert sorted(depths) == ["0,32768", "0,32768", "0,32768,130560"]
    assert [r["stage"] for r in rows] == ["throughput", "throughput", "verify"]
    assert (rows[2]["ref_depth"], rows[2]["full_depth"]) == (130560, 130560) and rows[0]["ref_depth"] is None
    assert int(_run("git", "-C", out, "rev-list", "--count", "HEAD").stdout) >= 4
    assert "redacted" in (out / "base.yml").read_text()
    assert not any(secret in p.read_text() for p in out.rglob("*") if p.is_file() and ".git" not in p.parts
                   for secret in ("live-secret", "live-pass"))
    history = _run("git", "-C", out, "log", "-p", "--all").stdout
    assert history and "live-secret" not in history and "live-pass" not in history
    picked = json.loads(result.stdout[result.stdout.index("{"):])  # the stub's 9000 MiB model exceeds the 8321 MiB budget
    assert picked["pick"] is None and picked["rejected"] == [{"id": rows[2]["id"], "reason": "model VRAM over budget"}]


@pytest.mark.parametrize(("ctx", "env_extra", "bound", "stages"), [
    ("131072,196608", {"ALL_FIT": "1"}, 3, [("throughput", 131072), ("verify", 131072), ("verify", 196608)]),
    ("262144,131072", {}, 3, [("throughput", 131072), ("verify", 131072)]),  # the first feasible ctx runs the pass
])
def test_tune_sweep_benchmarks_each_kv_ngl_moe_once_across_context_sizes(
    tmp_path: Path, ctx: str, env_extra: dict[str, str], bound: int, stages: list[tuple[str, int]]
) -> None:
    out = _out(tmp_path, "sweep")
    result = _run("bash", SCRIPTS / "tune-sweep.sh", "--alias", "m", "--out", out, "--ctx", ctx, "--kv", "q5_1", "--ubatch", "512",
                  env=_tune_env(tmp_path) | env_extra)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"upper bound: {bound} runs x 3600s"), result.stdout
    rows = _jsonl(out / "results.jsonl")
    assert [(r["stage"], r["ctx"]) for r in rows] == stages
    assert all(r["ubatch"] == 512 and r["fa"] == "on" for r in rows)


def test_tune_sweep_refuses_while_llm_server_runs(tmp_path: Path) -> None:
    result = _run("bash", SCRIPTS / "tune-sweep.sh", "--alias", "m", "--out", _out(tmp_path, "sweep"), env=_tune_env(tmp_path, RUNNING_LLM_SERVER))
    assert result.returncode == 3 and "make stop-llama" in result.stderr


def test_tune_sweep_stops_on_a_budget_error(tmp_path: Path) -> None:
    env = _tune_env(tmp_path) | {"BUDGET_ERROR": "configured GPU 0000:03:00.0 not present; detected: []"}
    result = _run("bash", SCRIPTS / "tune-sweep.sh", "--alias", "m", "--out", _out(tmp_path, "sweep"), env=env)
    assert result.returncode == 1 and "not present" in result.stderr
    assert not (tmp_path / "calls").exists()


def test_tune_sweep_rejects_a_non_empty_out_dir(tmp_path: Path) -> None:
    (_out(tmp_path, "sweep")).mkdir()
    (_out(tmp_path, "sweep") / "x").write_text("x")
    assert _run("bash", SCRIPTS / "tune-sweep.sh", "--alias", "m", "--out", _out(tmp_path, "sweep")).returncode == 2


OUT_SCRIPTS = {
    "bench-fit.sh": ("--alias", "m"),
    "tune-sweep.sh": ("--alias", "m"),
    "effort-run.sh": ("--model", "p/m"),
}


@pytest.mark.parametrize("script", OUT_SCRIPTS)
def test_benchmark_scripts_refuse_an_out_outside_the_repo_tmp(script: str, tmp_path: Path) -> None:
    link = _out(tmp_path, "link")
    link.symlink_to(tmp_path / "elsewhere")
    escape = _scratch(tmp_path) / ".." / ".." / "escaped-out"
    for out in (tmp_path / "outside", escape, link / "sub", ROOT / "tmp", ROOT):
        result = _run("bash", SCRIPTS / script, *OUT_SCRIPTS[script], "--out", out, env=_tune_env(tmp_path, RUNNING_LLM_SERVER))
        assert result.returncode == 2, (out, result.stderr)
        assert "isolated temp directories under ./tmp/" in result.stderr and "never in or against the owner's project" in result.stderr
    assert not (tmp_path / "outside").exists() and not (tmp_path / "elsewhere").exists() and not (ROOT / "escaped-out").exists()


@pytest.mark.parametrize("script", OUT_SCRIPTS)
def test_benchmark_scripts_accept_an_out_under_the_repo_tmp(script: str, tmp_path: Path) -> None:
    """Past the --out guard: bench-fit and tune-sweep reach their llm-server refusal (3), effort-run its --timeout check (2)."""
    extra = ("--timeout", str(RUNNER_CAP + 1)) if script == "effort-run.sh" else ()
    env = _tune_env(tmp_path, RUNNING_LLM_SERVER)
    result = _run("bash", SCRIPTS / script, *OUT_SCRIPTS[script], "--out", _out(tmp_path, "run"), *extra, env=env, stdin=subprocess.DEVNULL)
    assert result.returncode == (2 if script == "effort-run.sh" else 3), result.stderr
    assert "isolated temp directories" not in result.stderr


def _verify_row(id_: str, ctx: int, points: Mapping[int, tuple[float, float]], vram: int | None = 7000, status: str = "ok") -> str:
    return json.dumps({
        "id": id_, "stage": "verify", "ctx": ctx, "status": status, "vram_available_mib": 8321, "model_vram_mib": vram,
        "ref_depth": min(131072, ctx - 512), "full_depth": ctx - 512,
        "tps": [{"depth": d, "pp_tps": pp, "tg_tps": tg} for d, (tg, pp) in points.items()],
    })


def test_tune_pick_ranks_context_first_then_decode_at_reference_depth(tmp_path: Path) -> None:
    full = {0: (90, 3000), 32768: (60, 1500)}
    results = tmp_path / "results.jsonl"
    results.write_text("\n".join([
        _verify_row("A", 262144, full | {131072: (25, 900), 261632: (15, 400)}),
        _verify_row("B", 131072, full | {130560: (80, 2000)}),
        _verify_row("C", 262144, full | {131072: (25, 900)}),
        _verify_row("D", 196608, full | {131072: (30, 900), 196096: (20, 500)}, vram=9000),
        _verify_row("E", 196608, full | {131072: (10, 900), 196096: (9, 500)}),
        _verify_row("F", 131072, {}, status="failed"),
        json.dumps({"id": "G", "stage": "verify", "ctx": 131072, "status": "ok", "vram_available_mib": 8321, "model_vram_mib": 7000,
                    "tps": [{"depth": 130560, "pp_tps": 2000, "tg_tps": 80}]}),
        json.dumps({"id": "T", "stage": "throughput", "ctx": 131072, "status": "ok", "tps": []}),
    ]) + "\n")
    out = json.loads(_run("bash", SCRIPTS / "tune-pick.sh", results).stdout)
    assert out["pick"]["id"] == "A"
    assert [r["id"] for r in out["ranked"]] == ["A", "B"]
    assert out["ranked"][0]["ref_depth"] == 131072 and out["ranked"][1]["ref_depth"] == 130560 and out["ranked"][1]["tg_at_ref"] == 80
    assert out["rejected"] == [
        {"id": "C", "reason": "not verified to full depth"}, {"id": "D", "reason": "model VRAM over budget"},
        {"id": "E", "reason": "tg at reference depth below 20"}, {"id": "F", "reason": "failed"},
        {"id": "G", "reason": "no recorded depths (a results.jsonl older than tune-pick: re-run the sweep)"},
    ]


def _apply(tmp_path: Path, row: dict[str, object]) -> tuple[subprocess.CompletedProcess[str], Path, str]:
    config = json.loads(json.dumps(QH_CONFIG))  # a deep copy: shared sub-dicts would dump as YAML anchors
    config["runtime"] = {"models_max": 1, "parallel_slots": 1, "ubatch_size": 512, "flash_attn": False, "cache_type_k": "q5_1", "cache_type_v": "q5_1"}
    config["models"].append(dict(config["models"][0], alias="o", enabled=False, file="o.gguf", ctx_size=8192))
    cfg = tmp_path / "models.yml"
    cfg.write_text(yaml.safe_dump(config))
    cfg.chmod(0o644)
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(row) + "\n")
    tools = {Path(shutil.which(t) or "/usr/bin/" + t).parent for t in ("uv", "yq", "jq")}
    env = {"PATH": f"/usr/bin:/bin:{':'.join(map(str, tools))}", "HOME": str(tmp_path), "LLM_ENV_CONFIG": str(cfg)}
    original = cfg.read_text()
    return _run("bash", SCRIPTS / "tune-apply.sh", "--alias", "m", "--results", results, "--id", str(row["id"]), env=env), cfg, original


def test_tune_apply_backs_up_and_writes_only_the_tuned_keys(tmp_path: Path) -> None:
    row = {"id": "r", "stage": "verify", "status": "ok", "kv": "q8_0", "ctx": 196608, "ubatch": 1024, "fa": "on", "ngl": None, "moe": 28,
           "vram_available_mib": 8321}
    result, cfg, original = _apply(tmp_path, row)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    backup = Path(lines[0].removeprefix("backup: "))
    assert backup.read_text() == original and backup.stat().st_mode & 0o777 == 0o600
    assert "vram_available_mib: 8321" in lines and lines[-3:] == ["make stop-llama", "make start-llama", "make model-interference"]
    before, after = yaml.safe_load(original), yaml.safe_load(cfg.read_text())  # migrate-config normalizes, so compare the touched keys only
    assert {k: after["runtime"][k] for k in ("cache_type_k", "cache_type_v", "ubatch_size", "flash_attn")} == {
        "cache_type_k": "q8_0", "cache_type_v": "q8_0", "ubatch_size": 1024, "flash_attn": True}
    m, o = after["models"]
    assert (m["ctx_size"], m["n_cpu_moe"], m["n_gpu_layers"]) == (196608, 28, 99)
    assert o == before["models"][1] and after["server"] == before["server"]
    assert cfg.stat().st_mode & 0o777 == 0o600


def test_tune_apply_leaves_the_live_config_when_the_result_fails_validation(tmp_path: Path) -> None:
    row = {"id": "r", "stage": "verify", "status": "ok", "kv": "q8_0", "ctx": 4096, "ubatch": 1024, "fa": "on", "vram_available_mib": 8321}
    result, cfg, original = _apply(tmp_path, row)
    assert result.returncode == 1 and "client_max_output_tokens must not exceed ctx_size" in result.stderr
    assert cfg.read_text() == original
    assert len(list(tmp_path.glob("models.yml.pre-tune-*.bak"))) == 1 and not list(tmp_path.glob(".models.yml.*"))


def test_tune_apply_refuses_a_throughput_row(tmp_path: Path) -> None:
    row = {"id": "r", "stage": "throughput", "status": "ok", "kv": "q8_0", "ctx": 196608, "ubatch": 1024, "fa": "on", "vram_available_mib": 1}
    result, cfg, original = _apply(tmp_path, row)
    assert result.returncode != 0 and cfg.read_text() == original
    assert not list(tmp_path.glob("models.yml.*"))


QH_SHA = "0123456789abcdef0123456789abcdef01234567"
QH_URL = f"https://huggingface.co/o/r/resolve/{QH_SHA}/m-Q4_K_M.gguf"
QH_TOTAL = 9197093888
MIB = 1048576
QH_FILES = {"m-Q4_K_M.gguf": 5512 * MIB, "m-Q5_K_M.gguf": 6334 * MIB, "m-Q6_K.gguf": 7208 * MIB}
QH_UV = r"""echo "$*" >>"$CALLS"
case "$*" in
*" migrate-config"*) exec "$REAL_UV" "$@" ;;
*" capacity show --alias m-q5_k_m"*) echo '{"alias": "m-q5_k_m", "depth": {"model_vram_mib": 7754}}' ;;
*" capacity show "*) echo '{"alias": "m"}' ;;
*" budget "*) [ -z "${QH_BUDGET_SLEEP:-}" ] || { touch "$CALLS.budget"; sleep "$QH_BUDGET_SLEEP"; }; if [ -n "${QH_BUDGET:-}" ]; then echo "$QH_BUDGET"; else echo '{"available_mib":8321,"feasible":false}'; fi; exit 1 ;;
*" capacity headroom "*) if [ -n "${QH_HEADROOM:-}" ]; then echo "$QH_HEADROOM"; else echo '{"quants": {"verdict": null, "candidates": [{"quant": "Q5_K_M", "fits": true, "reason": "fits: 599 MiB VRAM free", "delta_mib": 822, "predicted_vram_mib": 7722}]}}'; fi ;;
*" capacity compare "*) echo '{"quants": {"candidates": [{"quant": "Q5_K_M", "measured": {"alias": "m-q5_k_m", "ref_depth": 130560, "delta": {"tg_at_ref": -8, "pp_at_ref": -100, "model_vram_mib": 800, "highest_layer": 1, "overall_score": null}}}]}}' ;;
esac
"""
QH_CURL = r"""out=; p=
for a in "$@"; do [ "$p" = -o ] && out="$a"; p="$a"; done
echo "curl $*" >>"$CALLS"
printf 'known-bytes' >"$out"
"""
QH_CONFIG = {
    "version": 1,
    "server": {"host": "0.0.0.0", "port": 8000, "api_key": "k", "mdns_name": "llm", "sleep_idle_seconds": 300},
    "gpu": {"pci_address": "0000:03:00.0", "device_name": "d", "backend": "vulkan", "image": "i", "vram_total_mib": 16304,
            "reserve_mode": "auto", "reserve_floor_mib": 1024},
    "runtime": {"models_max": 1, "parallel_slots": 1, "ubatch_size": 512, "flash_attn": True, "cache_type_k": "q8_0", "cache_type_v": "q8_0"},
    "models": [{"alias": "m", "label": "M", "parameters": "9B", "quantization": "Q4_K_M", "enabled": True, "file": "m-Q4_K_M.gguf",
                "url": QH_URL, "size_bytes": 5600000000, "vram_budget": "10%", "ctx_size": 131072, "client_max_output_tokens": 8192,
                "n_gpu_layers": 99, "sampling": {"temperature": 0.6}, "benchmark": {"vulkan": {"pp_tps": 1, "tg_tps": 2, "measured_at": "x"}}}],
}
LIVE_CANDIDATE = dict(QH_CONFIG["models"][0], alias="candidate", label="Foo 9B", file="Foo-9B-Q4_K_M.gguf",  # type: ignore[index]
                      url="file:///home/u/Downloads/Foo-9B-Q4_K_M.gguf", size_bytes=5000000000)


def _live(root: Path, *args: str, scratch: list[dict[str, object]] | None = None) -> tuple[subprocess.CompletedProcess[str], Path, str]:
    root.mkdir(parents=True, exist_ok=True)
    cfg = root / "models.yml"
    cfg.write_text(yaml.safe_dump(json.loads(json.dumps(QH_CONFIG))))
    s = root / "scratch.yml"
    s.write_text(yaml.safe_dump(json.loads(json.dumps(QH_CONFIG | {"models": scratch or [LIVE_CANDIDATE]}))))
    tools = {Path(shutil.which(t) or "/usr/bin/" + t).parent for t in ("uv", "yq", "jq")}
    env = {"PATH": f"/usr/bin:/bin:{':'.join(map(str, tools))}", "HOME": str(root), "LLM_ENV_CONFIG": str(cfg)}
    original = cfg.read_text()
    argv = [str(s) if a == "<S>" else a for a in args]
    return _run("bash", SCRIPTS / "live-entry.sh", *argv, env=env), cfg, original


def test_live_entry_add_copies_the_scratch_entry_disabled_after_a_backup(tmp_path: Path) -> None:
    result, cfg, original = _live(tmp_path, "add", "--from", "<S>", "--alias", "candidate", "--as", "foo-9b")
    assert result.returncode == 0, result.stderr
    backup = Path(result.stdout.splitlines()[0].removeprefix("backup: "))
    assert ".pre-entry-" in backup.name and backup.read_text() == original and backup.stat().st_mode & 0o777 == 0o600
    m, new = yaml.safe_load(cfg.read_text())["models"]
    assert (m["alias"], m["enabled"]) == ("m", True)
    assert (new["alias"], new["enabled"], "benchmark" in new) == ("foo-9b", False, False)
    assert (new["file"], new["url"], new["size_bytes"]) == ("Foo-9B-Q4_K_M.gguf", LIVE_CANDIDATE["url"], 5000000000)
    assert result.stdout.splitlines()[-1] == "added: foo-9b" and cfg.stat().st_mode & 0o777 == 0o600


def test_live_entry_add_refuses_and_leaves_the_live_file(tmp_path: Path) -> None:
    cases = [
        (("add", "--from", "<S>", "--alias", "nope", "--as", "x"), None, 1, "no entry nope"),
        (("add", "--from", "<S>", "--alias", "candidate", "--as", "m"), None, 1, "already"),
        (("add", "--from", "<S>", "--alias", "candidate"), None, 2, "candidate"),
        (("add", "--from", "<S>", "--alias", "candidate", "--as", "x"), [dict(LIVE_CANDIDATE, file="m-Q4_K_M.gguf")], 1, "already the file of m"),
        (("add", "--from", "<S>", "--alias", "candidate", "--as", "x"), [dict(LIVE_CANDIDATE, ctx_size=4096)], 1,
         "client_max_output_tokens must not exceed ctx_size"),
    ]
    for i, (args, scratch, code, message) in enumerate(cases):
        result, cfg, original = _live(tmp_path / str(i), *args, scratch=scratch)
        assert (result.returncode, message in result.stderr, cfg.read_text() == original) == (code, True, True), (args, result.stderr)
        assert not list(cfg.parent.glob(".models.yml.*")), args


def test_live_entry_set_url_pins_a_resolve_url_of_the_same_file(tmp_path: Path) -> None:
    url = f"https://huggingface.co/o/r/resolve/{QH_SHA}/Q4/m-Q4_K_M.gguf"
    result, cfg, _ = _live(tmp_path / "ok", "set-url", "--alias", "m", "--url", url)
    assert result.returncode == 0, result.stderr
    assert ".pre-url-" in result.stdout and yaml.safe_load(cfg.read_text())["models"][0]["url"] == url
    for i, (alias, bad, code) in enumerate([("m", "https://huggingface.co/o/r/resolve/main/m-Q4_K_M.gguf", 2),
                                            ("m", f"https://huggingface.co/o/r/resolve/{QH_SHA}/other.gguf", 2),
                                            ("m", "file:///x/m-Q4_K_M.gguf", 2), ("nope", url, 1)]):
        result, cfg, original = _live(tmp_path / str(i), "set-url", "--alias", alias, "--url", bad)
        assert result.returncode == code and cfg.read_text() == original, (alias, bad, result.stderr)


def _qh_tree(files: Mapping[str, int] | None = None) -> list[dict[str, object]]:
    return [{"type": "file", "path": f, "size": n, "lfs": {"oid": hashlib.sha256(f.encode()).hexdigest(), "size": n}}
            for f, n in (files or QH_FILES).items()]


def _qh_env(tmp_path: Path, *, podman: str = "", config: Mapping[str, object] | None = None, tree: list[dict[str, object]] | None = None,
            sha: str = QH_SHA, curl: bool = False) -> dict[str, str]:
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    bodies = {"uv": QH_UV, "podman": podman, **({"curl": QH_CURL} if curl else {})}
    for name, body in bodies.items():
        (stub / name).write_text(f"#!/usr/bin/env bash\n{body}")
        (stub / name).chmod(0o755)
    cfg = tmp_path / "cfg" / "models.yml"
    cfg.parent.mkdir(exist_ok=True)
    cfg.write_text(yaml.safe_dump(config or QH_CONFIG))
    fixtures = tmp_path / "hf"
    fixtures.mkdir(exist_ok=True)
    (fixtures / "model-o__r.json").write_text(json.dumps({"sha": sha, "gguf": {"total": QH_TOTAL}}))
    (fixtures / f"tree-o__r@{sha}.json").write_text(json.dumps(tree if tree is not None else _qh_tree()))
    (tmp_path / "models").mkdir(exist_ok=True)
    tools = {Path(shutil.which(t) or "/usr/bin/" + t).parent for t in ("yq", "jq")}
    return {"PATH": f"{stub}:/usr/bin:/bin:{':'.join(map(str, tools))}", "HOME": str(tmp_path), "LLM_ENV_CONFIG": str(cfg),
            "LLM_ENV_MODELS_DIR": str(tmp_path / "models"), "LLM_ENV_SCOUT_OUT": str(tmp_path / "out"), "HF_FIXTURE_DIR": str(fixtures),
            "CALLS": str(tmp_path / "calls"), "REAL_UV": shutil.which("uv") or "uv"}


def _qh(env: Mapping[str, str], *args: str | Path) -> subprocess.CompletedProcess[str]:
    return _run("bash", SCRIPTS / "quant-headroom.sh", *args, env=dict(env))


def _calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "calls"
    return path.read_text().splitlines() if path.exists() else []


def _candidates(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    (file,) = (tmp_path / "out" / "model-tune").glob("quant-*/candidates.json")
    assert re.fullmatch(r"quant-[0-9]{8}T[0-9]{6}-[0-9]+", file.parent.name), file.parent.name
    return file, json.loads(file.read_text())


@pytest.fixture
def real_model_tune_untouched():
    real = ROOT / "tmp" / "model-tune"
    before = sorted(p.name for p in real.iterdir()) if real.exists() else None
    yield
    assert (sorted(p.name for p in real.iterdir()) if real.exists() else None) == before


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_check_reads_the_tree_at_the_pinned_sha(tmp_path: Path) -> None:
    result = _qh(_qh_env(tmp_path), "check", "--alias", "m", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    file, data = _candidates(tmp_path)
    oid = lambda f: hashlib.sha256(f.encode()).hexdigest()
    assert data["sha"] == QH_SHA and data["repo"] == "o/r" and data["params_total"] == QH_TOTAL
    assert data["current"] == {"quant": "Q4_K_M", "file": "m-Q4_K_M.gguf", "size_bytes": 5512 * MIB, "sha256": oid("m-Q4_K_M.gguf"),
                               "params_total": QH_TOTAL}
    assert data["candidates"] == [
        {"quant": q, "file": f"m-{q}.gguf", "size_bytes": QH_FILES[f"m-{q}.gguf"], "sha256": oid(f"m-{q}.gguf"), "params_total": QH_TOTAL}
        for q in ("Q5_K_M", "Q6_K")
    ]
    assert f"capacity headroom --alias m --candidates {file} --vram-available-mib 8321" in "\n".join(_calls(tmp_path))
    assert "Q5_K_M: fits" in result.stdout and "verdict: none" in result.stdout
    assert not any(" budget " in c and str(tmp_path / "cfg" / "models.yml") in c for c in _calls(tmp_path))


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_check_takes_the_verdict_from_the_budget_gate_and_shows_the_formula_and_measurement(tmp_path: Path) -> None:
    over = {"available_mib": 8321, "required_mib": 8383, "vram_feasible": False, "ram_feasible": True, "feasible": False}
    headroom = {"quants": {"verdict": "no headroom for Q5_K_M at ctx 131072, free MiB 1421", "candidates": [
        {"quant": "Q5_K_M", "fits": False, "reason": "over the budget gate: required 8383 MiB, available 8321 MiB (formula predicted 7897 MiB)",
         "delta_mib": 822, "predicted_vram_mib": 7897, "budget_required_mib": 8383, "budget_available_mib": 8321}]}}
    env = _qh_env(tmp_path) | {"QH_BUDGET": json.dumps(over), "QH_HEADROOM": json.dumps(headroom)}
    (tmp_path / "models" / "m-Q4_K_M.gguf").write_bytes(b"GGUF-header")  # the not-yet-downloaded candidates borrow this header
    result = _qh(env, "check", "--alias", "m", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    _, data = _candidates(tmp_path)
    budget = {"required_mib": 8383, "available_mib": 8321, "vram_feasible": False, "ram_feasible": True}
    assert [c["budget"] for c in data["candidates"]] == [budget, budget]  # type: ignore[index]
    scratch = (tmp_path / "out" / "model-tune").glob("quant-*/budget-Q5_K_M/models.yml")
    entry = yaml.safe_load(next(scratch).read_text())["models"][0]
    assert (entry["file"], entry["size_bytes"], entry["quantization"], entry["ctx_size"]) == ("m-Q5_K_M.gguf", 6334 * MIB, "Q5_K_M", 131072)
    assert not list((tmp_path / "out" / "model-tune").glob("quant-*/budget-*/models")), "the sparse stand-ins are scratch"
    assert str(tmp_path / "cfg" / "models.yml") in " ".join(c for c in _calls(tmp_path) if "capacity headroom" in c)
    line = next(ln for ln in result.stdout.splitlines() if ln.startswith("Q5_K_M:"))
    assert line.startswith("Q5_K_M: does not fit (over the budget gate: required 8383 MiB, available 8321 MiB")
    assert "formula predicted 7897 MiB" in line and "measured 7754 MiB" in line
    assert "verdict: no headroom for Q5_K_M" in result.stdout
    assert "fits" not in line.split("(")[0]


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_check_marks_sharded_and_missing_quants(tmp_path: Path) -> None:
    files = {"m-Q4_K_M.gguf": 5512 * MIB, "Q5_K_M/m-Q5_K_M-00001-of-00002.gguf": MIB, "Q5_K_M/m-Q5_K_M-00002-of-00002.gguf": MIB}
    result = _qh(_qh_env(tmp_path, tree=_qh_tree(files)), "check", "--alias", "m", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    assert _candidates(tmp_path)[1]["candidates"] == [
        {"quant": "Q5_K_M", "reason": "sharded, not supported"}, {"quant": "Q6_K", "reason": "not in repo"}]


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_check_marks_a_quant_token_missing_from_the_file_name(tmp_path: Path) -> None:
    entry = dict(QH_CONFIG["models"][0], file="model.gguf", url=f"https://huggingface.co/o/r/resolve/{QH_SHA}/model.gguf")  # type: ignore[index]
    files = {"model.gguf": 5512 * MIB, "m-Q5_K_M.gguf": 6000 * MIB}
    env = _qh_env(tmp_path, config=dict(QH_CONFIG, models=[entry]), tree=_qh_tree(files))
    result = _qh(env, "check", "--alias", "m", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    assert _candidates(tmp_path)[1]["candidates"] == [
        {"quant": q, "reason": "quant token not in file name"} for q in ("Q5_K_M", "Q6_K")]


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_check_resolves_an_unpinned_entry_only_when_the_file_matches(tmp_path: Path) -> None:
    entry = dict(QH_CONFIG["models"][0], url="https://huggingface.co/o/r/resolve/main/m-Q4_K_M.gguf", size_bytes=5512 * MIB)  # type: ignore[index]
    result = _qh(_qh_env(tmp_path, config=QH_CONFIG | {"models": [entry]}), "check", "--alias", "m", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    assert f"resolved main to {QH_SHA}" in result.stdout and _candidates(tmp_path)[1]["sha"] == QH_SHA
    other = tmp_path / "other"
    other.mkdir()
    result = _qh(_qh_env(other, config=QH_CONFIG | {"models": [dict(entry, size_bytes=5600000000)]}), "check", "--alias", "m",
                 "--vram-available-mib", "8321")
    assert result.returncode != 0 and "pin the entry url first" in result.stderr
    assert not (other / "calls").exists() or not any("capacity headroom" in c for c in _calls(other))


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_check_does_not_read_the_budget_while_llm_server_runs(tmp_path: Path) -> None:
    env = _qh_env(tmp_path, podman=RUNNING_LLM_SERVER)
    result = _qh(env, "check", "--alias", "m")
    assert result.returncode != 0 and "--vram-available-mib" in result.stderr
    live_budget = lambda c: " budget " in c and str(tmp_path / "cfg" / "models.yml") in c
    assert not any(live_budget(c) for c in _calls(tmp_path))
    result = _qh(env, "check", "--alias", "m", "--vram-available-mib", "8000")
    assert result.returncode == 0, result.stderr
    assert not any(live_budget(c) for c in _calls(tmp_path))
    assert any("--vram-available-mib 8000" in c for c in _calls(tmp_path))


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_check_reads_the_budget_ignoring_its_exit_status(tmp_path: Path) -> None:
    result = _qh(_qh_env(tmp_path), "check", "--alias", "m")
    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)
    assert any(" budget --models-dir " in c for c in calls) and any("--vram-available-mib 8321" in c for c in calls)


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_check_removes_its_stand_ins_when_interrupted(tmp_path: Path) -> None:
    def interrupted_check(root: Path, *, sleep: str, whole_group: bool) -> None:
        root.mkdir()
        env = _qh_env(root) | {"QH_BUDGET_SLEEP": sleep}
        (root / "models" / "m-Q4_K_M.gguf").write_bytes(b"GGUF-header")
        cmd = ["bash", str(SCRIPTS / "quant-headroom.sh"), "check", "--alias", "m", "--quants", "Q5_K_M,Q6_K", "--vram-available-mib", "8321"]
        proc = subprocess.Popen(cmd, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tune = root / "out" / "model-tune"
        try:
            deadline = time.monotonic() + 30
            while not Path(env["CALLS"] + ".budget").exists():
                assert proc.poll() is None and time.monotonic() < deadline
                time.sleep(0.1)
            assert list(tune.glob("quant-*/budget-Q5_K_M/models/m-Q5_K_M.gguf"))
            (os.killpg if whole_group else os.kill)(proc.pid, signal.SIGTERM)
            assert proc.wait(timeout=10) != 0
            # the budget subshell and its stalled stub may outlive the main shell: wait for the whole group
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    os.killpg(proc.pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("the check's process group is still alive")
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        assert not list(tune.glob("quant-*/budget-*/models"))

    interrupted_check(tmp_path / "group", sleep="30", whole_group=True)
    # SIGTERM to the main shell only: the budget loop still runs the next quant after the EXIT trap fired
    interrupted_check(tmp_path / "main-only", sleep="2", whole_group=False)


def test_quant_headroom_fetch_verifies_sha256_and_removes_a_mismatch(tmp_path: Path) -> None:
    good = hashlib.sha256(b"known-bytes").hexdigest()
    env = _qh_env(tmp_path, tree=_qh_tree() + [{"type": "file", "path": "m-Q8_0.gguf", "size": 11, "lfs": {"oid": good}}], curl=True)
    result = _qh(env, "fetch", "--alias", "m", "--quant", "Q8_0")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "models" / "m-Q8_0.gguf").read_bytes() == b"known-bytes"
    assert any(f"-o {tmp_path}/models/m-Q8_0.gguf.part https://huggingface.co/o/r/resolve/{QH_SHA}/m-Q8_0.gguf" in c for c in _calls(tmp_path))
    assert any("--continue-at -" in c for c in _calls(tmp_path))
    bad = tmp_path / "bad"
    bad.mkdir()
    result = _qh(_qh_env(bad, curl=True), "fetch", "--alias", "m", "--quant", "Q5_K_M")
    assert result.returncode == 1 and "mismatch" in result.stderr
    assert not (bad / "models" / "m-Q5_K_M.gguf").exists() and not (bad / "models" / "m-Q5_K_M.gguf.part").exists()


def test_quant_headroom_fetch_skips_a_present_verified_file(tmp_path: Path) -> None:
    good = hashlib.sha256(b"known-bytes").hexdigest()
    env = _qh_env(tmp_path, tree=_qh_tree() + [{"type": "file", "path": "m-Q8_0.gguf", "size": 11, "lfs": {"oid": good}}], curl=True)
    (tmp_path / "models" / "m-Q8_0.gguf").write_bytes(b"known-bytes")
    result = _qh(env, "fetch", "--alias", "m", "--quant", "Q8_0")
    assert result.returncode == 0 and "already present" in result.stdout
    assert not any(c.startswith("curl") for c in _calls(tmp_path))


def test_quant_headroom_fetch_never_resumes_onto_or_deletes_a_file_it_did_not_download(tmp_path: Path) -> None:
    good = hashlib.sha256(b"known-bytes").hexdigest()
    env = _qh_env(tmp_path, tree=_qh_tree() + [{"type": "file", "path": "m-Q8_0.gguf", "size": 11, "lfs": {"oid": good}}], curl=True)
    dest, part = tmp_path / "models" / "m-Q8_0.gguf", tmp_path / "models" / "m-Q8_0.gguf.part"
    dest.write_bytes(b"owner")  # the owner's own, smaller file under the candidate's name
    result = _qh(env, "fetch", "--alias", "m", "--quant", "Q8_0")
    assert result.returncode == 1 and str(dest) in result.stderr and "never" in result.stderr
    assert dest.read_bytes() == b"owner" and not any(c.startswith("curl") for c in _calls(tmp_path))
    dest.unlink()
    part.write_bytes(b"known")  # an interrupted fetch resumes into its own .part
    result = _qh(env, "fetch", "--alias", "m", "--quant", "Q8_0")
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == b"known-bytes" and not part.exists()
    assert any("--continue-at -" in c and f"-o {part} " in c for c in _calls(tmp_path))


def test_quant_headroom_fetch_refuses_without_free_disk_and_for_unavailable_quants(tmp_path: Path) -> None:
    huge = {"m-Q4_K_M.gguf": MIB, "m-Q5_K_M.gguf": 1 << 60}
    env = _qh_env(tmp_path, tree=_qh_tree(huge), curl=True)
    result = _qh(env, "fetch", "--alias", "m", "--quant", "Q5_K_M")
    assert result.returncode == 1 and "free disk" in result.stderr
    result = _qh(env, "fetch", "--alias", "m", "--quant", "Q6_K")
    assert result.returncode == 1 and "not in repo" in result.stderr
    assert not any(c.startswith("curl") for c in _calls(tmp_path))


KNOWN = b"known-bytes"
FETCH_URL = f"https://huggingface.co/o/r/resolve/{QH_SHA}/m.gguf"
FETCH_CURL = r"""out=; p=
for a in "$@"; do [ "$p" = -o ] && out="$a"; p="$a"; done
echo "curl $* part=$(stat -c %s "$out" 2>/dev/null || echo none)" >>"$CALLS"
case " $* " in *" -K - "*) cat >>"$CALLS.stdin" ;; esac
tr '\0' ' ' <"/proc/$PPID/cmdline" >>"$CALLS.parent"; echo >>"$CALLS.parent"
n="$(cat "$CALLS.n" 2>/dev/null || echo 0)"; echo $((n + 1)) >"$CALLS.n"
case "${FETCH_MODE:-full}:$n" in
drop:0) printf 'known' >>"$out"; printf 206; exit 18 ;;
stall:*) printf 206; exit 28 ;;
norange:*) printf 200; exit 33 ;;
http503:0|http429:0|http40[134]:*) printf '%s' "${FETCH_MODE#http}"; exit 22 ;;
esac
printf 'known-bytes' | tail -c +$(( $(stat -c %s "$out" 2>/dev/null || echo 0) + 1 )) >>"$out"
printf 200
"""
HF_TOKEN_VALUE = "hf_r17NotARealToken0123456789abcdef"


def _fetch(tmp_path: Path, *, size: int = len(KNOWN), sha: str | None = hashlib.sha256(KNOWN).hexdigest(),
           mode: str = "full", url: str = FETCH_URL, env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess[str], Path]:
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    for name, body in (("curl", FETCH_CURL), ("sleep", 'echo "sleep $*" >>"$CALLS.sleep"\n')):
        (stub / name).write_text(f"#!/usr/bin/env bash\n{body}")
        (stub / name).chmod(0o755)
    dest = tmp_path / "models" / "m.gguf"
    args = ["--url", url, "--dest", dest, "--size", str(size), *(["--sha256", sha] if sha else [])]
    run_env = {"PATH": f"{stub}:/usr/bin:/bin", "CALLS": str(tmp_path / "calls"), "FETCH_MODE": mode, **(env or {})}
    return _run("bash", SCRIPTS / "fetch-gguf.sh", *args, env=run_env), dest


def _sleeps(tmp_path: Path) -> list[str]:
    path = tmp_path / "calls.sleep"
    return path.read_text().splitlines() if path.exists() else []


def test_fetch_gguf_downloads_into_a_part_file_and_renames_it_once_verified(tmp_path: Path) -> None:
    result, dest = _fetch(tmp_path)
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == KNOWN and not dest.with_name("m.gguf.part").exists() and "sha256 verified" in result.stdout
    assert any(f"--continue-at - -o {dest}.part {FETCH_URL}" in c for c in _calls(tmp_path))


def test_fetch_gguf_resumes_an_interrupted_download(tmp_path: Path) -> None:
    part = tmp_path / "models" / "m.gguf.part"
    part.parent.mkdir()
    part.write_bytes(b"known")  # what a killed or exhausted earlier run leaves
    result, dest = _fetch(tmp_path)
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == KNOWN and not part.exists() and len(_calls(tmp_path)) == 1 and "part=5" in _calls(tmp_path)[0]


def test_fetch_gguf_reuses_a_complete_part_and_restarts_an_oversized_one(tmp_path: Path) -> None:
    part = tmp_path / "models" / "m.gguf.part"
    part.parent.mkdir()
    part.write_bytes(KNOWN)
    result, dest = _fetch(tmp_path)
    assert result.returncode == 0 and dest.read_bytes() == KNOWN and _calls(tmp_path) == []
    dest.unlink()
    part.write_bytes(KNOWN + b"-of-another-revision")
    result, _ = _fetch(tmp_path)
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == KNOWN and "part=none" in _calls(tmp_path)[-1]


@pytest.mark.parametrize(("size", "sha", "message"), [(12, hashlib.sha256(KNOWN).hexdigest(), "size mismatch"),
                                                      (len(KNOWN), "0" * 64, "sha256 mismatch")])
def test_fetch_gguf_removes_only_its_part_on_a_mismatch(tmp_path: Path, size: int, sha: str, message: str) -> None:
    result, dest = _fetch(tmp_path, size=size, sha=sha)
    assert result.returncode == 1 and message in result.stderr
    assert not dest.exists() and not dest.with_name("m.gguf.part").exists()


def test_fetch_gguf_never_touches_a_file_already_at_the_destination(tmp_path: Path) -> None:
    dest = tmp_path / "models" / "m.gguf"
    dest.parent.mkdir()
    dest.write_bytes(KNOWN)
    result, _ = _fetch(tmp_path)
    assert result.returncode == 0 and "already present" in result.stdout
    dest.write_bytes(b"owner")
    result, _ = _fetch(tmp_path)
    assert result.returncode == 1 and str(dest) in result.stderr and "never" in result.stderr
    assert dest.read_bytes() == b"owner" and not dest.with_name("m.gguf.part").exists() and _calls(tmp_path) == []


def test_fetch_gguf_checks_the_size_alone_without_a_sha256(tmp_path: Path) -> None:
    result, _ = _fetch(tmp_path, sha=None)
    assert result.returncode == 0 and "size verified" in result.stdout and "sha256" not in result.stdout


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not installed")
def test_fetch_gguf_copies_a_local_file_and_resumes_it(tmp_path: Path) -> None:  # real curl on a file:// url: no network
    src = tmp_path / "Down loads" / "Bar#1 %x.gguf"
    src.parent.mkdir()
    data = os.urandom(300_000)
    src.write_bytes(data)
    dest = tmp_path / "models" / "Bar-7B-Q4_K_M.gguf"
    dest.parent.mkdir()
    dest.with_name(dest.name + ".part").write_bytes(data[:100_000])  # an interrupted earlier copy
    path = f"/usr/bin:/bin:{Path(shutil.which('jq') or '/usr/bin/jq').parent}"  # --from encodes the path with jq
    result = _run("bash", SCRIPTS / "fetch-gguf.sh", "--from", src, "--dest", dest, "--size", str(len(data)), env={"PATH": path})
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == data and src.read_bytes() == data and not dest.with_name(dest.name + ".part").exists()
    assert "size verified" in result.stdout


def test_fetch_gguf_from_needs_one_regular_file(tmp_path: Path) -> None:
    dest = tmp_path / "models" / "m.gguf"
    cases = (
        (("--from", tmp_path / "missing.gguf"), "not a regular file"),
        (("--from", tmp_path), "not a regular file"),
        (("--from", tmp_path / "x", "--url", FETCH_URL), "exclude each other"),
    )
    for args, message in cases:
        result = _run("bash", SCRIPTS / "fetch-gguf.sh", *args, "--dest", dest, "--size", "1", env={"PATH": "/usr/bin:/bin"})
        assert result.returncode == 2 and not dest.parent.exists(), (args, result.stderr)
        assert message in result.stderr, (args, result.stderr)


def test_fetch_gguf_needs_free_disk_only_for_what_the_part_lacks(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    result, _ = _fetch(tmp_path, size=1 << 60)
    assert result.returncode == 1 and "free disk" in result.stderr and _calls(tmp_path) == []
    size = shutil.disk_usage(tmp_path / "models").free + 512 * MIB  # size + 1024 MiB never fits; the remainder does
    with (tmp_path / "models" / "m.gguf.part").open("wb") as handle:
        handle.truncate(size - 1)  # sparse: no disk used
    result, _ = _fetch(tmp_path, size=size)
    assert "free disk" not in result.stderr and len(_calls(tmp_path)) == 1
    assert result.returncode == 1 and "size mismatch" in result.stderr  # the stub has nothing to append


def test_fetch_gguf_rejects_a_non_integer_size() -> None:
    assert _run("bash", SCRIPTS / "fetch-gguf.sh", "--url", "u", "--dest", "d", "--size", "x").returncode == 2


@pytest.mark.parametrize("mode", ["drop", "http503", "http429"])
def test_fetch_gguf_retries_a_transient_failure_from_its_part(tmp_path: Path, mode: str) -> None:
    result, dest = _fetch(tmp_path, mode=mode)
    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)
    assert dest.read_bytes() == KNOWN and len(calls) == 2 and _sleeps(tmp_path) == ["sleep 5"]
    assert all("--speed-limit 1024 --speed-time 120" in c for c in calls)
    assert ("part=5" if mode == "drop" else "part=none") in calls[1]


def test_fetch_gguf_gives_up_after_four_stalled_attempts_and_keeps_the_part(tmp_path: Path) -> None:
    part = tmp_path / "models" / "m.gguf.part"
    part.parent.mkdir()
    part.write_bytes(b"known")
    result, dest = _fetch(tmp_path, mode="stall")
    assert result.returncode == 1 and "after 4 attempts" in result.stderr and "re-run the same command to resume" in result.stderr
    assert len(_calls(tmp_path)) == 4 and len(_sleeps(tmp_path)) == 3 and part.read_bytes() == b"known" and not dest.exists()


def test_fetch_gguf_stops_at_once_when_the_server_cannot_resume(tmp_path: Path) -> None:
    part = tmp_path / "models" / "m.gguf.part"
    part.parent.mkdir()
    part.write_bytes(b"known")
    result, dest = _fetch(tmp_path, mode="norange")
    assert result.returncode == 1 and "cannot resume" in result.stderr and "remove it" in result.stderr
    assert len(_calls(tmp_path)) == 1 and _sleeps(tmp_path) == [] and part.read_bytes() == b"known" and not dest.exists()


@pytest.mark.parametrize(("code", "token", "state", "url"), [
    ("401", None, "HF_TOKEN was not set", FETCH_URL),
    ("403", HF_TOKEN_VALUE, "HF_TOKEN was sent and refused", FETCH_URL),
    ("401", HF_TOKEN_VALUE, "HF_TOKEN was not sent: only https://huggingface.co/ urls get it", "https://hf.co/o/r/resolve/main/m.gguf")])
def test_fetch_gguf_stops_at_once_on_a_gated_repo(tmp_path: Path, code: str, token: str | None, state: str, url: str) -> None:
    result, _ = _fetch(tmp_path, mode=f"http{code}", url=url, env={"HF_TOKEN": token} if token else None)
    assert result.returncode == 1 and f"HTTP {code}" in result.stderr and "gated or private repo" in result.stderr
    assert state in result.stderr and "to resume" not in result.stderr
    assert len(_calls(tmp_path)) == 1 and _sleeps(tmp_path) == [] and HF_TOKEN_VALUE not in result.stdout + result.stderr


def test_fetch_gguf_stops_at_once_on_another_client_error(tmp_path: Path) -> None:
    result, _ = _fetch(tmp_path, mode="http404")
    assert result.returncode == 1 and "HTTP 404" in result.stderr and "gated" not in result.stderr
    assert len(_calls(tmp_path)) == 1 and _sleeps(tmp_path) == []


def test_fetch_gguf_sends_hf_token_only_to_hugging_face_through_curl_stdin(tmp_path: Path) -> None:
    result, _ = _fetch(tmp_path, env={"HF_TOKEN": HF_TOKEN_VALUE})
    assert result.returncode == 0, result.stderr
    (call,) = _calls(tmp_path)
    assert " -K - " in f" {call} " and HF_TOKEN_VALUE not in call and "--location-trusted" not in call
    assert (tmp_path / "calls.stdin").read_text() == f'header = "Authorization: Bearer {HF_TOKEN_VALUE}"\n'
    assert HF_TOKEN_VALUE not in (tmp_path / "calls.parent").read_text() + result.stdout + result.stderr
    for name, url, env in (("other-host", "https://example.com/m.gguf", {"HF_TOKEN": HF_TOKEN_VALUE}),
                           ("hf-co", "https://hf.co/lab/x/resolve/main/m.gguf", {"HF_TOKEN": HF_TOKEN_VALUE}),
                           ("no-token", FETCH_URL, None)):
        root = tmp_path / name
        root.mkdir()
        result, _ = _fetch(root, url=url, env=env)
        assert result.returncode == 0 and "-K" not in _calls(root)[0] and not (root / "calls.stdin").exists(), name


def test_fetch_gguf_refuses_a_malformed_hf_token_before_any_request(tmp_path: Path) -> None:
    result, dest = _fetch(tmp_path, env={"HF_TOKEN": 'hf_x"\nurl = "https://example.com/'})
    assert result.returncode == 2 and "HF_TOKEN" in result.stderr and "hf_x" not in result.stdout + result.stderr
    assert _calls(tmp_path) == [] and not dest.exists()


def _add_entry(tmp_path: Path, quant: str = "Q5_K_M") -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    env = _qh_env(tmp_path)
    return _qh(env, "add-entry", "--alias", "m", "--quant", quant), env


def test_quant_headroom_add_entry_backs_up_and_appends_a_disabled_copy(tmp_path: Path) -> None:
    env = _qh_env(tmp_path)
    cfg = Path(env["LLM_ENV_CONFIG"])
    original = cfg.read_bytes()
    result = _qh(env, "add-entry", "--alias", "m", "--quant", "Q5_K_M")
    assert result.returncode == 0, result.stderr
    models = {m["alias"]: m for m in yaml.safe_load(cfg.read_text())["models"]}
    old, new = models["m"], models["m-q5_k_m"]
    assert all(new[k] == old[k] for k in ("ctx_size", "n_gpu_layers", "sampling"))
    assert new["url"] == f"https://huggingface.co/o/r/resolve/{QH_SHA}/m-Q5_K_M.gguf" and new["file"] == "m-Q5_K_M.gguf"
    assert new["enabled"] is False and "benchmark" not in new and new["quantization"] == "Q5_K_M"
    assert new["size_bytes"] == 6334 * MIB and new["label"] == "M (Q5_K_M)" and old == QH_CONFIG["models"][0]  # type: ignore[index]
    (backup,) = cfg.parent.glob("models.yml.pre-quant-*.bak")
    assert backup.read_bytes() == original and backup.stat().st_mode & 0o777 == 0o600
    assert cfg.stat().st_mode & 0o777 == 0o600 and sorted(p.name for p in cfg.parent.iterdir()) == sorted(["models.yml", backup.name])
    assert "alias: m-q5_k_m" in result.stdout and f"backup: {backup}" in result.stdout


def test_quant_headroom_add_entry_refuses_an_existing_alias(tmp_path: Path) -> None:
    result, env = _add_entry(tmp_path)
    assert result.returncode == 0, result.stderr
    cfg = Path(env["LLM_ENV_CONFIG"])
    after_first = cfg.read_bytes()
    second = _qh(env, "add-entry", "--alias", "m", "--quant", "Q5_K_M")
    assert second.returncode != 0 and "already exists" in second.stderr
    assert cfg.read_bytes() == after_first
    assert len(list(cfg.parent.glob("models.yml.pre-quant-*.bak"))) == 1


def test_quant_headroom_add_entry_leaves_the_live_config_when_validation_fails(tmp_path: Path) -> None:
    bad = dict(QH_CONFIG["models"][0], sampling={"nope": 1})  # type: ignore[index]
    env = _qh_env(tmp_path, config=QH_CONFIG | {"models": [bad]})
    cfg = Path(env["LLM_ENV_CONFIG"])
    original = cfg.read_bytes()
    result = _qh(env, "add-entry", "--alias", "m", "--quant", "Q5_K_M")
    assert result.returncode == 1 and "sampling" in result.stderr
    assert cfg.read_bytes() == original
    assert sorted(p.name for p in cfg.parent.iterdir() if not p.name.endswith(".bak")) == ["models.yml"]


def test_quant_headroom_record_calls_capacity_compare(tmp_path: Path) -> None:
    result = _qh(_qh_env(tmp_path), "record", "--alias", "m", "--candidate", "m-q5_k_m")
    assert result.returncode == 0, result.stderr
    assert any(c.endswith("capacity compare --alias m --candidate m-q5_k_m") for c in _calls(tmp_path))
    assert result.stdout.strip() == "Q5_K_M: tg@130560 -8, pp@130560 -100, VRAM +800 MiB, ladder +1, score n/a"


def test_quant_headroom_rejects_bad_usage(tmp_path: Path) -> None:
    env = _qh_env(tmp_path)
    assert _qh(env).returncode == 2
    assert _qh(env, "bogus").returncode == 2
    assert _qh(env, "check").returncode == 2
    assert _qh(env, "check", "--alias", "m", "--quants", "Q5;rm").returncode == 2
    assert _qh(env, "fetch", "--alias", "m").returncode == 2
    assert not _calls(tmp_path)


QH_SUB_URL = f"https://huggingface.co/o/r/resolve/{QH_SHA}/Q4_K_M/m-Q4_K_M.gguf"


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_maps_an_entry_url_in_a_sub_folder(tmp_path: Path) -> None:
    good = hashlib.sha256(b"known-bytes").hexdigest()
    tree = _qh_tree({"Q4_K_M/m-Q4_K_M.gguf": 5512 * MIB, "Q5_K_M/m-Q5_K_M.gguf": 6334 * MIB}) \
        + [{"type": "file", "path": "Q8_0/m-Q8_0.gguf", "size": 11, "lfs": {"oid": good}}]
    entry = dict(QH_CONFIG["models"][0], url=QH_SUB_URL)  # type: ignore[index]
    env = _qh_env(tmp_path, config=QH_CONFIG | {"models": [entry]}, tree=tree, curl=True)
    result = _qh(env, "check", "--alias", "m", "--quants", "Q5_K_M", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    (q5,) = json.loads(_candidates(tmp_path)[0].read_text())["candidates"]  # json.loads, not [1]: pyright types that as object
    assert (q5["file"], q5["path"], q5["size_bytes"]) == ("m-Q5_K_M.gguf", "Q5_K_M/m-Q5_K_M.gguf", 6334 * MIB)
    assert _qh(env, "fetch", "--alias", "m", "--quant", "Q8_0").returncode == 0
    assert (tmp_path / "models" / "m-Q8_0.gguf").read_bytes() == b"known-bytes"
    assert any(f"resolve/{QH_SHA}/Q8_0/m-Q8_0.gguf" in c for c in _calls(tmp_path))
    assert _qh(env, "add-entry", "--alias", "m", "--quant", "Q5_K_M").returncode == 0
    new = next(m for m in yaml.safe_load(Path(env["LLM_ENV_CONFIG"]).read_text())["models"] if m["alias"] == "m-q5_k_m")
    assert (new["file"], new["url"]) == ("m-Q5_K_M.gguf", f"https://huggingface.co/o/r/resolve/{QH_SHA}/Q5_K_M/m-Q5_K_M.gguf")


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_never_maps_a_candidate_onto_the_current_file_name(tmp_path: Path) -> None:
    entry = dict(QH_CONFIG["models"][0], url=f"https://huggingface.co/o/r/resolve/{QH_SHA}/Q4_K_M/model.gguf", file="model.gguf")  # type: ignore[index]
    env = _qh_env(tmp_path, config=QH_CONFIG | {"models": [entry]}, tree=_qh_tree({"Q4_K_M/model.gguf": 5512 * MIB, "Q5_K_M/model.gguf": 6334 * MIB}))
    result = _qh(env, "check", "--alias", "m", "--quants", "Q5_K_M", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    assert _candidates(tmp_path)[1]["candidates"] == [{"quant": "Q5_K_M", "reason": "quant token not in file name"}]
    other = tmp_path / "other"
    other.mkdir()
    bad = dict(QH_CONFIG["models"][0], url=f"https://huggingface.co/o/r/resolve/{QH_SHA}/sub/other.gguf")  # type: ignore[index]
    result = _qh(_qh_env(other, config=QH_CONFIG | {"models": [bad]}), "check", "--alias", "m", "--vram-available-mib", "8321")
    assert result.returncode == 1 and "does not end in its file" in result.stderr


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_takes_ud_quant_tokens(tmp_path: Path) -> None:
    entry = dict(QH_CONFIG["models"][0], quantization="UD-Q4_K_XL", file="m-UD-Q4_K_XL.gguf",  # type: ignore[index]
                 url=f"https://huggingface.co/o/r/resolve/{QH_SHA}/m-UD-Q4_K_XL.gguf")
    files = {"m-UD-Q4_K_XL.gguf": 5600 * MIB, "m-UD-Q5_K_XL.gguf": 6400 * MIB, "m-Q5_K_M.gguf": 6334 * MIB}
    env = _qh_env(tmp_path, config=QH_CONFIG | {"models": [entry]}, tree=_qh_tree(files))
    result = _qh(env, "check", "--alias", "m", "--quants", "UD-Q5_K_XL,Q5_K_M", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    rows = json.loads(_candidates(tmp_path)[0].read_text())["candidates"]
    assert [(r["quant"], r.get("file")) for r in rows] == [("UD-Q5_K_XL", "m-UD-Q5_K_XL.gguf"), ("Q5_K_M", "m-Q5_K_M.gguf")]
    assert _qh(env, "add-entry", "--alias", "m", "--quant", "UD-Q5_K_XL").returncode == 0
    new = next(m for m in yaml.safe_load(Path(env["LLM_ENV_CONFIG"]).read_text())["models"] if m["alias"] == "m-ud-q5_k_xl")
    assert (new["file"], new["quantization"]) == ("m-UD-Q5_K_XL.gguf", "UD-Q5_K_XL")
    for bad in ("-rf", "Q4.K", "Q5;rm"):
        assert _qh(env, "check", "--alias", "m", "--quants", bad).returncode == 2, bad


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_matches_lower_case_quant_files(tmp_path: Path) -> None:
    good = hashlib.sha256(b"known-bytes").hexdigest()
    entry = dict(QH_CONFIG["models"][0], file="m-q4_k_m.gguf", url=f"https://huggingface.co/o/r/resolve/{QH_SHA}/m-q4_k_m.gguf")  # type: ignore[index]
    tree = _qh_tree({"m-q4_k_m.gguf": 5512 * MIB}) + [{"type": "file", "path": "m-q5_k_m.gguf", "size": 11, "lfs": {"oid": good}}]
    env = _qh_env(tmp_path, config=QH_CONFIG | {"models": [entry]}, tree=tree, curl=True)
    result = _qh(env, "check", "--alias", "m", "--quants", "Q5_K_M", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    (q5,) = json.loads(_candidates(tmp_path)[0].read_text())["candidates"]
    assert (q5["file"], q5["size_bytes"], "path" in q5) == ("m-q5_k_m.gguf", 11, False)
    assert _qh(env, "fetch", "--alias", "m", "--quant", "Q5_K_M").returncode == 0
    assert (tmp_path / "models" / "m-q5_k_m.gguf").read_bytes() == b"known-bytes"
    assert any(f"resolve/{QH_SHA}/m-q5_k_m.gguf" in c for c in _calls(tmp_path))


GGUF_BYTES = b"GGUF" + bytes(60)


def _rt_env(tmp_path: Path) -> dict[str, str]:
    """Hermetic config and models dir for resolve-target.sh; the real uv/yq/jq stay on PATH (scratch mode runs llmenv)."""
    cfg = tmp_path / "cfg" / "models.yml"
    cfg.parent.mkdir(exist_ok=True)
    models = json.loads(json.dumps(QH_CONFIG["models"] * 2))  # type: ignore[index]  # a copy: shared sub-dicts would dump as YAML anchors
    models[1].update(alias="lab/x", enabled=False, file="x-Q4_K_M.gguf")
    cfg.write_text(yaml.safe_dump(QH_CONFIG | {"models": models}))
    (tmp_path / "models").mkdir(exist_ok=True)
    return {**os.environ, "LLM_ENV_CONFIG": str(cfg), "LLM_ENV_MODELS_DIR": str(tmp_path / "models")}


def _rt(tmp_path: Path, *args: str | Path) -> subprocess.CompletedProcess[str]:
    return _run("bash", SCRIPTS / "resolve-target.sh", *args, env=_rt_env(tmp_path), cwd=tmp_path)


def _rt_json(tmp_path: Path, *args: str | Path) -> dict[str, object]:
    result = _rt(tmp_path, *args)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_resolve_target_reads_an_alias_before_a_repo_id(tmp_path: Path) -> None:
    assert _rt_json(tmp_path, "m") == {"kind": "alias", "alias": "m", "file": "m-Q4_K_M.gguf", "enabled": True, "present": False}
    (tmp_path / "models" / "x-Q4_K_M.gguf").write_bytes(GGUF_BYTES)
    assert _rt_json(tmp_path, "lab/x") == {"kind": "alias", "alias": "lab/x", "file": "x-Q4_K_M.gguf", "enabled": False, "present": True}
    assert _rt_json(tmp_path, "org/other-GGUF") == {"kind": "hf_repo", "repo": "org/other-GGUF", "quant": None}


def test_resolve_target_parses_hugging_face_urls(tmp_path: Path) -> None:
    rev = "0123456789abcdef0123456789abcdef01234567"
    want = {"kind": "hf_file", "repo": "o/r", "revision": "main", "file": "sub/m-Q5_K_M.gguf", "quant": "Q5_K_M"}
    assert _rt_json(tmp_path, "https://huggingface.co/o/r/resolve/main/sub/m-Q5_K_M.gguf?download=true") == want
    assert _rt_json(tmp_path, f"https://huggingface.co/o/r/blob/{rev}/sub/m-Q5_K_M.gguf") == want | {"revision": rev}
    pr = {"kind": "hf_file", "repo": "o/r", "revision": "refs%2Fpr%2F1", "file": "m-Q5_K_M.gguf", "quant": "Q5_K_M"}
    assert _rt_json(tmp_path, "https://huggingface.co/o/r/resolve/refs/pr/1/m-Q5_K_M.gguf") == pr
    assert _rt_json(tmp_path, "https://huggingface.co/o/r/blob/refs%2Fpr%2F1/m-Q5_K_M.gguf") == pr  # the web UI's encoded form
    assert _rt_json(tmp_path, "https://huggingface.co/o/r/resolve/refs/pr/12/sub/m-Q5_K_M.gguf") == pr | {"revision": "refs%2Fpr%2F12", "file": "sub/m-Q5_K_M.gguf"}
    assert _rt_json(tmp_path, "https://hf.co/o/r") == {"kind": "hf_repo", "repo": "o/r", "quant": None}
    assert _rt_json(tmp_path, "huggingface.co/o/r") == {"kind": "hf_repo", "repo": "o/r", "quant": None}
    assert _rt_json(tmp_path, "HF.co/o/r/resolve/main/sub/m-Q5_K_M.gguf?download=true") == want
    assert _rt_json(tmp_path, "https://huggingface.co/o/r/tree/main") == {"kind": "hf_repo", "repo": "o/r", "quant": None}
    result = _rt(tmp_path, "https://example.com/o/r")
    assert result.returncode == 2 and "Hugging Face" in result.stderr
    for doc in ("https://huggingface.co/o/r/resolve/main/README.md", "https://huggingface.co/o/r/blob/main/sub/config.json"):
        result = _rt(tmp_path, doc)
        assert (result.returncode, result.stdout) == (2, "") and "not a .gguf file URL" in result.stderr, doc
    for other in ("https://huggingface.co/datasets/o/r", "https://huggingface.co/datasets/o/r/resolve/main/x-Q4_K_M.gguf",
                  "https://huggingface.co/spaces/o/r", "https://huggingface.co/docs/hub/gguf", "https://hf.co/api/models/o/r"):
        result = _rt(tmp_path, other)
        assert result.returncode == 2 and "not a Hugging Face model URL" in result.stderr and result.stdout == "", other


def test_resolve_target_splits_a_quant_suffix_off_a_repo(tmp_path: Path) -> None:
    assert _rt_json(tmp_path, "org/other-GGUF:Q4_K_M") == {"kind": "hf_repo", "repo": "org/other-GGUF", "quant": "Q4_K_M"}
    assert _rt_json(tmp_path, "hf.co/o/r:q4_k_m") == {"kind": "hf_repo", "repo": "o/r", "quant": "q4_k_m"}
    assert _rt_json(tmp_path, "https://huggingface.co/o/r:UD-Q4_K_XL") == {"kind": "hf_repo", "repo": "o/r", "quant": "UD-Q4_K_XL"}
    assert _rt_json(tmp_path, "hf.co/o/r:latest") == {"kind": "hf_repo", "repo": "o/r", "quant": None}
    alias = _rt(tmp_path, "lab/x:Q4_K_M")
    assert (alias.returncode, alias.stdout) == (2, "") and "only selects a quant of a Hugging Face repo" in alias.stderr
    assert "kind alias" in alias.stderr


def test_resolve_target_keeps_an_existing_path_that_looks_like_a_scheme_less_url(tmp_path: Path) -> None:
    (tmp_path / "hf.co").mkdir()
    (tmp_path / "hf.co" / "x-Q4_K_M.gguf").write_bytes(GGUF_BYTES)
    assert _rt_json(tmp_path, "hf.co/x-Q4_K_M.gguf")["kind"] == "gguf_file"


def test_resolve_target_expands_a_quoted_tilde_before_classifying(tmp_path: Path) -> None:
    gguf = tmp_path / "Downloads" / "Bar-7B-Q4_K_M.gguf"
    gguf.parent.mkdir()
    gguf.write_bytes(GGUF_BYTES)
    env = _rt_env(tmp_path) | {"HOME": str(tmp_path)}

    def rt(model: str) -> subprocess.CompletedProcess[str]:
        return _run("bash", SCRIPTS / "resolve-target.sh", model, env=env, cwd=tmp_path)

    found = rt("~/Downloads/Bar-7B-Q4_K_M.gguf")  # a quoted path reaches the script with its ~ unexpanded
    assert found.returncode == 0, found.stderr
    assert (json.loads(found.stdout)["kind"], Path(json.loads(found.stdout)["path"])) == ("gguf_file", gguf.resolve())
    near = rt("~/Downloads/Bar-7B-Q4_K_S.gguf")
    assert near.returncode == 2 and f"did you mean {gguf}?" in near.stderr
    bare = rt("~")
    assert (bare.returncode, bare.stdout) == (2, "") and "not a .gguf file" in bare.stderr


def test_resolve_target_checks_a_local_gguf_file(tmp_path: Path) -> None:
    outside = tmp_path / "x-IQ4_XS.gguf"
    outside.write_bytes(GGUF_BYTES)
    assert _rt_json(tmp_path, outside) == {"kind": "gguf_file", "path": str(outside), "size_bytes": len(GGUF_BYTES), "in_models_dir": False, "quant": "IQ4_XS"}
    inside = tmp_path / "models" / "plain.GGUF"
    inside.write_bytes(GGUF_BYTES)
    assert _rt_json(tmp_path, inside) == {"kind": "gguf_file", "path": str(inside), "size_bytes": len(GGUF_BYTES), "in_models_dir": True, "quant": None}
    (tmp_path / "bad.gguf").write_bytes(b"PK" + bytes(60))
    result = _rt(tmp_path, tmp_path / "bad.gguf")
    assert result.returncode == 2 and "not a GGUF file" in result.stderr
    for missing in ("nope.gguf", "Downloads/Foo-Q4_K_M.gguf"):
        result = _rt(tmp_path, missing)
        assert result.returncode == 2 and "not found" in result.stderr, missing
        assert result.stdout == ""
    nested = tmp_path / "models" / "sub" / "y-Q4_K_M.gguf"
    nested.parent.mkdir()
    nested.write_bytes(GGUF_BYTES)
    assert _rt_json(tmp_path, nested)["in_models_dir"] is False  # llama-server finds a model only at <models_dir>/<file>


def test_resolve_target_writes_a_scratch_config(tmp_path: Path) -> None:
    required = re.findall(r'"(\w+)"', re.search(r"REQUIRED_MODEL_KEYS = \((.*?)\)", (ROOT / "pylib" / "config.py").read_text(), re.DOTALL)[1])  # type: ignore[index]
    assert "alias" in required and "n_gpu_layers" in required
    gguf = tmp_path / "x-IQ4_XS.gguf"
    gguf.write_bytes(GGUF_BYTES)
    live = Path(_rt_env(tmp_path)["LLM_ENV_CONFIG"])
    before = live.read_bytes()
    scratch = _out(tmp_path, "s") / "scratch.yml"
    data = _rt_json(tmp_path, gguf, "--scratch-config", scratch)
    assert data["kind"] == "gguf_file" and data["scratch_config"] == str(scratch)
    cfg = yaml.safe_load(scratch.read_text())
    assert cfg["server"]["api_key"] == "redacted"
    (entry,) = [m for m in cfg["models"] if m["enabled"]]
    assert entry["alias"] == "candidate" and entry["file"] == "x-IQ4_XS.gguf" and entry["quantization"] == "IQ4_XS"
    assert entry["url"] == f"file://{gguf}" and entry["size_bytes"] == len(GGUF_BYTES)
    assert all(key in entry for key in required)
    assert live.read_bytes() == before

    by_alias = _out(tmp_path, "s") / "alias.yml"
    assert _rt_json(tmp_path, "m", "--scratch-config", by_alias)["scratch_config"] == str(by_alias)
    assert [m["alias"] for m in yaml.safe_load(by_alias.read_text())["models"] if m["enabled"]] == ["m"]
    assert live.read_bytes() == before

    hf = _rt(tmp_path, "org/other-GGUF", "--scratch-config", _out(tmp_path, "s") / "hf.yml")
    assert hf.returncode == 2 and not (_out(tmp_path, "s") / "hf.yml").exists()
    outside = _rt(tmp_path, "m", "--scratch-config", tmp_path / "outside.yml")
    assert outside.returncode == 2 and "isolated temp directories under ./tmp/" in outside.stderr and not (tmp_path / "outside.yml").exists()
    assert live.read_bytes() == before


def test_resolve_target_refuses_a_scratch_alias_that_already_exists(tmp_path: Path) -> None:
    env = _rt_env(tmp_path)
    cfg = yaml.safe_load(Path(env["LLM_ENV_CONFIG"]).read_text())
    cfg["models"].append(json.loads(json.dumps(dict(cfg["models"][0], alias="candidate", enabled=False))))
    Path(env["LLM_ENV_CONFIG"]).write_text(yaml.safe_dump(cfg))
    gguf = tmp_path / "x-IQ4_XS.gguf"
    gguf.write_bytes(GGUF_BYTES)
    scratch = _out(tmp_path, "s") / "scratch.yml"
    result = _run("bash", SCRIPTS / "resolve-target.sh", gguf, "--scratch-config", scratch, env=env)
    assert result.returncode == 2 and "candidate" in result.stderr and not scratch.exists()


def test_resolve_target_scratch_config_on_a_gateway_only_config(tmp_path: Path) -> None:
    live = SKILL / "evals" / "files" / "models-gateway.yml"
    before = live.read_bytes()
    env = {**os.environ, "LLM_ENV_CONFIG": str(live), "LLM_ENV_MODELS_DIR": str(SKILL / "evals" / "files" / "models")}
    scratch = _out(tmp_path, "s") / "gateway.yml"
    result = _run("bash", SCRIPTS / "resolve-target.sh", "ornith", "--scratch-config", scratch, env=env)
    assert result.returncode == 0, result.stderr
    cfg = yaml.safe_load(scratch.read_text())
    assert cfg["llm_server"]["enabled"] is True and [m["alias"] for m in cfg["models"] if m["enabled"]] == ["ornith"]
    assert live.read_bytes() == before


@pytest.mark.parametrize(
    ("model", "code", "hint"),
    [("Qwen3.8-27B", 3, None), ("Qwen 3.8 27B", 3, None), ("nope.gguf", 2, None), ("Missing-7B.GGUF", 2, None),
     ("https://example.com/o/r", 2, None), ("https://huggingface.co/datasets/o/r", 2, None),
     ("evals/files/Foo-9B-Q4_K_M.ggu", 2, "evals/files/Foo-9B-Q4_K_M.gguf"),
     ("evals/files/Foo-9B-Q4_K_S.gguf", 2, "evals/files/Foo-9B-Q4_K_M.gguf"),
     ("{tmp}/evals/files/Foo-9B-Q4_K_M", 2, "{tmp}/evals/files/Foo-9B-Q4_K_M.gguf"),
     ("evals/files/", 2, "evals/files/Foo-9B-Q4_K_M.gguf"),
     ("evals/Foo-9B-Q4_K_M", 2, None), ("Foo-9B-Q4_K_M.ggu", 2, None), ("./Foo-9B", 2, None),
     ("~/no-such-dir-r8/Foo-9B", 2, None), ("~nobody-r11/Foo-9B-Q4_K_M.gguf", 2, None), ("link/Foo-9B-Q4_K_M.ggu", 2, "link/Foo-9B-Q4_K_M.gguf"),
     ("link", 2, "link/Foo-9B-Q4_K_M.gguf"), ("link/", 2, "link/Foo-9B-Q4_K_M.gguf")],
)
def test_resolve_target_exits_3_only_for_a_bare_model_name(tmp_path: Path, model: str, code: int, hint: str | None) -> None:
    gguf = tmp_path / "evals" / "files" / "Foo-9B-Q4_K_M.gguf"
    gguf.parent.mkdir(parents=True)
    gguf.write_bytes(GGUF_BYTES)
    (tmp_path / "link").symlink_to(gguf.parent)
    result = _rt(tmp_path, model.format(tmp=tmp_path))
    assert (result.returncode, result.stdout) == (code, ""), result.stderr
    assert ("search it as a model name" in result.stderr) == (code == 3)
    if hint:
        assert f"did you mean {hint.format(tmp=tmp_path)}?" in result.stderr
    if code == 2 and not model.lower().endswith(".gguf") and "://" not in model:
        assert "not a .gguf file" in result.stderr and "https://huggingface.co/" in result.stderr


def test_resolve_target_hints_a_bare_file_name_for_an_argument_without_a_directory(tmp_path: Path) -> None:
    (tmp_path / "Foo.gguf").write_bytes(GGUF_BYTES)
    result = _rt(tmp_path, "Foo.ggu")
    assert (result.returncode, result.stdout) == (2, "") and "(did you mean Foo.gguf?)" in result.stderr, result.stderr


def test_resolve_target_hints_a_gguf_name_within_two_edits(tmp_path: Path) -> None:
    d = tmp_path / "dl"
    d.mkdir()
    for name in ("Foo-9B-Q4_K_M.gguf", "Zed-1B-Q8_0.gguf"):
        (d / name).write_bytes(GGUF_BYTES)
    early = _rt(tmp_path, d / "Fo-9B-Q4_K_M.gguf")  # the first characters differ: the 3-character prefix rule finds nothing
    assert early.returncode == 2 and f"did you mean {d}/Foo-9B-Q4_K_M.gguf?" in early.stderr, early.stderr
    far = _rt(tmp_path, d / "Fxx-9C-Q4_K_M.gguf")  # 3 edits
    assert far.returncode == 2 and "did you mean" not in far.stderr
    assert f"did you mean {d}/Foo-9B-Q4_K_M.gguf?" in _rt(tmp_path, d / "Foo-9B-Q4_K_S.gguf").stderr  # the prefix rule still wins


def test_resolve_target_takes_a_file_url_as_its_path(tmp_path: Path) -> None:
    gguf = tmp_path / "Bar-7B-Q4_K_M.gguf"
    gguf.write_bytes(GGUF_BYTES)
    out = _rt_json(tmp_path, f"file://{gguf}")
    assert (out["kind"], out["path"]) == ("gguf_file", str(gguf.resolve()))
    for missing in (f"{tmp_path}/Gone-Q4_K_M.gguf", f"{tmp_path}/My%20Model-Q4_K_M.gguf"):  # percent-escapes stay encoded
        result = _rt(tmp_path, f"file://{missing}")
        assert result.returncode == 2 and f"{missing}: not found" in result.stderr and "file://" not in result.stderr


def test_resolve_target_rejects_anything_else(tmp_path: Path) -> None:
    result = _rt(tmp_path, "hello world")
    assert result.returncode == 3
    for word in ("alias", "Hugging Face", ".gguf"):
        assert word in result.stderr, word


def test_eval_input_files_exist_and_the_gguf_fixture_resolves(tmp_path: Path) -> None:
    evals = json.loads((SKILL / "evals" / "evals.json").read_text())["evals"]
    listed = {(e["id"], f) for e in evals for f in e.get("files", [])}
    assert ("named-gguf-argument", "evals/files/Foo-9B-Q4_K_M.gguf") in listed
    for eval_id, name in listed:
        assert (SKILL / name).is_file(), name
        prompt = next(e["prompt"] + " " + e.get("setup", "") for e in evals if e["id"] == eval_id)
        assert name in prompt or (Path(name).parent.name != "files" and f"{Path(name).parent}/" in prompt), eval_id
    fixture = SKILL / "evals" / "files" / "Foo-9B-Q4_K_M.gguf"
    assert fixture.read_bytes() == GGUF_BYTES
    out = _rt_json(tmp_path, fixture)
    assert (out["kind"], out["quant"], out["size_bytes"]) == ("gguf_file", "Q4_K_M", len(GGUF_BYTES))


FIX = SKILL / "evals" / "files"


def _asserts(eval_id: str) -> str:
    evals = json.loads((SKILL / "evals" / "evals.json").read_text())["evals"]
    return " ".join(next(e for e in evals if e["id"] == eval_id)["assertions"])


FIX_ENV = {"LLM_ENV_CONFIG": str(FIX / "models.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
SERVED_DEVICE = "AMD Radeon RX 9070 XT (RADV GFX1201)"


@pytest.mark.parametrize(("model", "code", "names"), [("Gemma4", 2, "gemma4"), ("gemma", 2, "gemma4"), ("GEMMA-4", 2, "gemma4"),
                                                      ("Ornith-35B", 2, "ornith-35b"), ("ornith-3", 2, "ornith-35b"),
                                                      ("orn", 2, "ornith or ornith-35b"), ("gema4", 2, "gemma4"), ("orinth-35b", 2, "ornith-35b"),
                                                      ("ornth", 2, "ornith"), ("gemma3", 3, None), ("gemma2", 3, None), ("gemma7", 3, None),
                                                      ("gemma-2b", 3, None), ("gemma-3n", 3, None), ("gemma-4-9b", 3, None),
                                                      ("ornith-2", 3, None), ("ornith-7b", 3, None), ("ornith-25b", 3, None),
                                                      ("gemma-4-27b-it", 3, None), ("Qwen3.8-27B", 3, None), ("Zeta-Coder-7B", 3, None)])
def test_resolve_target_names_a_near_miss_alias_instead_of_a_search(model: str, code: int, names: str | None) -> None:
    result = _run("bash", SCRIPTS / "resolve-target.sh", model, env={**os.environ, **FIX_ENV}, cwd=ROOT)
    assert (result.returncode, result.stdout) == (code, ""), result.stderr
    if names:
        assert f"no models.yml alias of that name; did you mean alias {names}?" in result.stderr
        assert "search it as a model name" not in result.stderr


def test_resolve_target_hints_the_models_dir_for_a_bare_file_name() -> None:
    want = f"did you mean {FIX / 'models' / 'gemma4-v2-Q4_K_M.gguf'}?"
    for arg in ("gemma4-v2-Q4_K_M.gguf", "gemma4-v2-Q4_K_M"):
        result = _run("bash", SCRIPTS / "resolve-target.sh", arg, env={**os.environ, **FIX_ENV}, cwd=ROOT)
        assert (result.returncode, result.stdout) == (2, "") and want in result.stderr, (arg, result.stderr)


def test_resolve_target_candidate_drops_the_baselines_own_settings(tmp_path: Path) -> None:
    env = _rt_env(tmp_path)
    cfg = yaml.safe_load(Path(env["LLM_ENV_CONFIG"]).read_text())
    cfg["models"][0]["n_cpu_moe"] = 28
    Path(env["LLM_ENV_CONFIG"]).write_text(yaml.safe_dump(cfg))
    gguf = tmp_path / "plain.gguf"
    gguf.write_bytes(GGUF_BYTES)
    scratch = _out(tmp_path, "s") / "scratch.yml"
    result = _run("bash", SCRIPTS / "resolve-target.sh", gguf, "--scratch-config", scratch, env=env)
    assert result.returncode == 0, result.stderr
    (entry,) = [m for m in yaml.safe_load(scratch.read_text())["models"] if m["alias"] == "candidate"]
    assert not {"n_cpu_moe", "sampling", "benchmark"} & set(entry)
    assert (entry["quantization"], entry["parameters"]) == ("unknown", "unknown")
    assert all(entry[k] == cfg["models"][0][k] for k in ("ctx_size", "client_max_output_tokens", "n_gpu_layers", "vram_budget"))


@pytest.mark.parametrize(("eval_id", "alias"), [("alias-typo", "gemma4"), ("alias-edit-typo", "ornith-35b")])
def test_eval_alias_typo_fixtures_match_their_assertions(eval_id: str, alias: str) -> None:
    want = f"no models.yml alias of that name; did you mean alias {alias}?"
    assert all(s in _asserts(eval_id) for s in ("exits 2", want, "hf-scout.sh --query is not run"))
    find = _section(_md(REFS / "selection-criteria.md"), "## Find the repo", "## Scout and rank")
    assert "near-miss alias" in find and "no, search it" in find


QWEN_SHA = "9c" * 20


def test_eval_latest_fit_fixtures_match_their_assertions() -> None:
    pre = json.loads((FIX / "preflight-served.json").read_text())
    b = pre["budget"]
    assert (b["llmenv_budget"]["vram_available_mib"], b["model_vram_budget_mib"], b["llmenv_budget"]["vram_required_mib"],
            b["ram_offload_budget_mib"], pre["ram"]["swap_used_mib"]) == (8321, 10369, 7300, 12000, 3100)
    assert pre["budget"]["llmenv_budget"]["vram_feasible"] is True
    assert [m["alias"] for m in yaml.safe_load((FIX / "models.yml").read_text())["models"] if m["enabled"]] == ["gemma4"]
    text = _asserts("latest-fit")
    assert all(s in text for s in ("gemma4", "8321", "10369", "7300", "12000", "3100", "lab-a/Coder-9B-GGUF 23",
                                   "lab-d/MoE-30B-A3B-GGUF 20", "lab-c/Coder-27B-IQ2-GGUF", "(gemma)"))


def test_eval_onboard_plan_fixtures_match_their_assertions() -> None:
    hf = {"HF_FIXTURE_DIR": str(FIX / "hf-qwen"), "PATH": "/usr/bin:/bin"}
    flags = ("--json", "--budget-mib", "8321", "--ram-mib", "12000")
    rows = {r["id"]: r for r in json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--query", "Qwen3.8-27B", *flags, env=hf).stdout)}
    assert {k: (r["fit"], r["pick"]["quant"], r["pick"]["mib"]) for k, r in rows.items()} == {
        "qwen/Qwen3.8-27B-GGUF": ("no", "Q4_K_M", 16500), "qwen/Qwen3.8-Coder-9B-GGUF": ("vram", "Q5_K_M", 6400)}
    (row,) = json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--repo", "qwen/Qwen3.8-Coder-9B-GGUF", *flags, env=hf).stdout)
    assert (row["sha"], row["pick"]["file"], row["pick"]["bytes"]) == (QWEN_SHA, "Qwen3.8-Coder-9B-Q5_K_M.gguf", 6710886400)
    text = _asserts("onboard-plan-with-server-running")
    assert all(s in text for s in ("exits 3", "fit no", "16500", "Qwen3.8-Coder-9B-Q5_K_M.gguf", "6710886400", "qwen3-8-coder-9b",
                                   "gemma4 baseline lane", "wait-gpu-release.sh"))


def test_eval_anchor_fixture_matches_its_assertions() -> None:
    out = dict(re.findall(r"^\$ (.+)\n(.+)$", (FIX / "anchors" / "route-check.txt").read_text(), re.MULTILINE))
    pi = "PI_CODING_AGENT_DIR=$HOME/.pi/agent pi auth check --model "
    assert out[pi + "opencode/claude-haiku-4-5"].startswith("auth ok") and "401" in out[pi + "openai/gpt-5.6-luna"]
    assert out["cursor-agent models"] == "Error: Authentication required"
    head = (FIX / "anchors" / "route-check.txt").read_text().splitlines()[0]
    assert head.startswith("# ") and "2026-11-01" in head and "2026-10-30" in head
    text = _asserts("anchor-comparison")
    assert all(s in text for s in ("Authentication required", "401", "opencode/claude-haiku-4-5", "never switches", "published numbers only"))


def test_eval_runtime_fixture_matches_its_assertions() -> None:
    saved = (FIX / "runtime" / "images.txt").read_text()
    vulkan, rocm = re.findall(r"^version: (\d+) ", saved, re.MULTILINE)
    upstream = re.findall(r"^b(\d+)$", saved, re.MULTILINE)[0]
    assert (vulkan, rocm, upstream) == ("10871", "10143", "12290")
    assert "| 10454 |" in (REFS / "runtimes.md").read_text()  # the last-seen table differs from this run's output
    assert all(s in _asserts("runtime-image-review") for s in ("10871", "10143", "1419", "not the last-seen 10454"))


def test_eval_enabled_alias_stopped_fixture_matches_its_assertions() -> None:
    served = json.loads((FIX / "preflight-served.json").read_text())
    stopped = json.loads((FIX / "preflight-stopped.json").read_text())
    assert (stopped["services"]["llm_server_enabled"], stopped["services"]["llm_server_running"]) == (True, False)
    assert not [h for h in stopped["gpu"]["holders"] if h["comm"] == "llama-server"]
    assert stopped["gpu"]["used_mib"] == served["gpu"]["used_mib"] - 7462
    rt = json.loads(_run("bash", SCRIPTS / "resolve-target.sh", "gemma4", env={**os.environ, **FIX_ENV}).stdout)
    assert {k: rt[k] for k in ("kind", "enabled", "present")} == {"kind": "alias", "enabled": True, "present": True}
    text = _asserts("enabled-alias-stopped")
    assert all(s in text for s in ("llm_server_running false", "make start-llama", "effort-served", "130560", "make stop-llama",
                                   "llm_server_running true"))


def test_eval_onboard_baseline_server_stopped_fixtures_match_their_assertions() -> None:
    stopped = json.loads((FIX / "preflight-stopped.json").read_text())
    s = stopped["services"]
    assert (s["llm_server_enabled"], s["llm_server_running"], s["omniroute_running"], s["system_one_running"]) == (True, False, True, True)
    assert not [h for h in stopped["gpu"]["holders"] if h["comm"] == "llama-server"]
    (row,) = json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--repo", "lab/Foo-9B-GGUF", "--json", "--budget-mib", "8321", "--ram-mib",
                             "12000", env={"HF_FIXTURE_DIR": str(FIX / "hf"), "PATH": "/usr/bin:/bin"}).stdout)
    assert (row["fit"], row["pick"]["file"], row["pick"]["mib"]) == ("vram", "Foo-9B-Q5_K_M.gguf", 6500)
    text = _asserts("onboard-baseline-server-stopped")
    assert all(t in text for t in ("make start-llama", "reads preflight again", "effort-baseline", "after make start-llama",
                                   "make stop-llama", "a yes to G5"))


def test_eval_gateway_enabled_alias_fixtures_match_their_assertions() -> None:
    base = yaml.safe_load((FIX / "models-gateway.yml").read_text())
    next(m for m in base["models"] if m["alias"] == "gemma4")["enabled"] = True
    base["gpu"]["device_name"] = SERVED_DEVICE
    cfg = FIX / "models-gateway-enabled.yml"
    assert yaml.safe_load(cfg.read_text()) == base and base["llm_server"]["enabled"] is False
    env = {**os.environ, "LLM_ENV_CONFIG": str(cfg), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    rt = json.loads(_run("bash", SCRIPTS / "resolve-target.sh", "gemma4", env=env).stdout)
    assert (rt["kind"], rt["enabled"], rt["present"]) == ("alias", True, True)
    pre = json.loads((FIX / "preflight-gateway-enabled.json").read_text())
    b = pre["budget"]
    assert pre["services"]["llm_server_enabled"] is False and pre["services"]["llm_server_running"] is False
    assert pre["runtime"]["enabled_models"] == ["gemma4"]
    assert (b["llmenv_budget"]["vram_available_mib"], b["llmenv_budget"]["ram_available_mib"]) == (8321, 0)
    assert min(b["llmenv_budget"]["vram_available_mib"], b["model_vram_budget_mib"]) == 8321
    assert not [h for h in pre["gpu"]["holders"] if h["comm"] == "llama-server"]
    text = _asserts("gateway-enabled-alias")
    assert all(t in text for t in ("other-alias row", "not a re-measure", "8321", "14500", "llm_server_running false", "make llama-enable MODEL=gemma4"))


def test_eval_local_gguf_a1_fixtures_match_their_assertions(tmp_path: Path) -> None:
    base = yaml.safe_load((FIX / "models.yml").read_text())
    next(m for m in base["models"] if m["alias"] == "ornith-35b")["size_bytes"] = len(GGUF_BYTES)
    cfg = FIX / "models-a1-local.yml"
    assert yaml.safe_load(cfg.read_text()) == base
    gguf = FIX / "home" / "Downloads" / "ornith-1.0-35b-Q4_K_M.gguf"
    assert gguf.read_bytes() == GGUF_BYTES and not (FIX / "models" / gguf.name).exists()
    env = {**os.environ, "LLM_ENV_CONFIG": str(cfg), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    rt = json.loads(_run("bash", SCRIPTS / "resolve-target.sh", gguf, env=env).stdout)
    assert (rt["kind"], rt["size_bytes"], rt["in_models_dir"]) == ("gguf_file", len(GGUF_BYTES), False)
    na = _run("bash", SCRIPTS / "new-alias.sh", "--file", gguf.name, "--size", str(len(GGUF_BYTES)), "--quant", "Q4_K_M", env=env)
    assert json.loads(na.stdout) == {"alias": "ornith-35b", "rule": "A1"}
    s = _out(tmp_path, "a1-scratch.yml")
    assert _run("bash", SCRIPTS / "resolve-target.sh", "ornith-35b", "--scratch-config", s, env=env).returncode == 0
    (entry,) = [m for m in yaml.safe_load(s.read_text())["models"] if m["enabled"]]
    assert (entry["alias"], entry["n_cpu_moe"], entry["ctx_size"], entry["vram_budget"]) == ("ornith-35b", 28, 262144, "85%")
    text = _asserts("local-gguf-a1-moe")
    assert all(t in text for t in ("rule A1", "resolve-target.sh ornith-35b --scratch-config", "n_cpu_moe 28", "ctx_size 262144",
                                   "no entry candidate", "fetch-gguf.sh --from", "make llama-enable MODEL=ornith-35b"))


def test_eval_alias_fixtures_match_their_assertions() -> None:
    env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}

    def rt(model: str) -> subprocess.CompletedProcess[str]:
        return _run("bash", SCRIPTS / "resolve-target.sh", model, env=env)

    assert {k: json.loads(rt("gemma4").stdout)[k] for k in ("kind", "enabled", "present")} == {"kind": "alias", "enabled": True, "present": True}
    assert {k: json.loads(rt("ornith-35b").stdout)[k] for k in ("enabled", "present")} == {"enabled": False, "present": False}
    assert rt("Qwen3.8-27B").returncode == 3 and "exits 3" in _asserts("onboard-plan-with-server-running")
    pre = json.loads((FIX / "preflight-served.json").read_text())
    budget = min(pre["budget"]["llmenv_budget"]["vram_available_mib"], pre["budget"]["model_vram_budget_mib"])
    assert budget == 8321
    assert "8321" in _asserts("served-alias-argument")
    assert "present false" in _asserts("disabled-alias-argument")
    gemma = next(m for m in yaml.safe_load((FIX / "models.yml").read_text())["models"] if m["alias"] == "gemma4")
    assert gemma["ctx_size"] == 131072 and "130560" in _asserts("served-alias-argument")
    assert yaml.safe_load((FIX / "models.yml").read_text())["gpu"]["device_name"] == SERVED_DEVICE


def test_eval_scout_fixtures_match_their_assertions() -> None:
    (row,) = json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--repo", "lab/Foo-9B-GGUF", "--json", "--budget-mib", "8321", "--ram-mib", "12000",
                             env={"HF_FIXTURE_DIR": str(FIX / "hf"), "PATH": "/usr/bin:/bin"}).stdout)
    assert (row["fit"], row["pick"]["file"]) == ("vram", "Foo-9B-Q5_K_M.gguf") and "Foo-9B-Q5_K_M.gguf" in _asserts("hf-repo-argument")
    assert row["pick"]["mib"] == 6500 and all(s in _asserts("hf-repo-argument") for s in ("6500", "182000"))
    scouts = ("--scout", FIX / "scout-coder.json", "--scout", FIX / "scout-agent.json")
    short = json.loads(_run("bash", SCRIPTS / "rank-candidates.sh", *scouts).stdout)
    ranked = json.loads(_run("bash", SCRIPTS / "rank-candidates.sh", *scouts, "--evidence", FIX / "evidence").stdout)
    assert [r["id"] for r in short["shortlist"]] == ["lab-a/Coder-9B-GGUF", "lab-b/Agent-14B-GGUF", "lab-d/MoE-30B-A3B-GGUF"]
    assert [(g["id"], g["gate"]) for g in short["gates_failed"]] == [("lab-c/Coder-27B-IQ2-GGUF", "bpw"), ("lab-e/Short-8B-GGUF", "ctx"), ("lab-f/Dense-32B-GGUF", "fit")]
    assert [(r["id"], r["score"]) for r in ranked["ranked"]] == [("lab-a/Coder-9B-GGUF", 23), ("lab-d/MoE-30B-A3B-GGUF", 20)]
    assert ranked["disagreements"] == [{"id": "lab-a/Coder-9B-GGUF", "criterion": "agentic_evidence", "level": 2, "jev": 0}]
    text = _asserts("no-argument-ranking")
    assert all(s in text for s in ("lab-b/Agent-14B-GGUF", "lab-c/Coder-27B-IQ2-GGUF", "lab-e/Short-8B-GGUF", "lab-f/Dense-32B-GGUF", "23", "20", "3100",
                                   "its own SKILL.md"))
    # the default cap is pinned by test_rank_candidates_shortlists_six_rows_by_default
    assert all(s in _asserts("latest-fit") for s in ("at most 6", "at most three are recommended to go on"))


def test_eval_hf_repo_quant_fixture_matches_its_assertions() -> None:
    rt = json.loads(_run("bash", SCRIPTS / "resolve-target.sh", "lab/Foo-9B-GGUF:Q4_K_M", env={**os.environ, **FIX_ENV}).stdout)
    assert rt == {"kind": "hf_repo", "repo": "lab/Foo-9B-GGUF", "quant": "Q4_K_M"}
    (row,) = json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--repo", "lab/Foo-9B-GGUF", "--quant", "Q4_K_M", "--json", "--budget-mib",
                             "8321", "--ram-mib", "12000", env={"HF_FIXTURE_DIR": str(FIX / "hf"), "PATH": "/usr/bin:/bin"}).stdout)
    assert (row["fit"], row["pick"]["file"], row["pick"]["mib"]) == ("vram", "Foo-9B-Q4_K_M.gguf", 5600)
    text = _asserts("hf-repo-quant")
    assert all(s in text for s in ("kind hf_repo", "quant Q4_K_M", "--quant Q4_K_M", "Foo-9B-Q4_K_M.gguf (5600 MiB)", "182000",
                                   "not a bare-name search"))


FOO_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
FOO_OID = "c0ffee00" * 8


def test_eval_hf_file_fixtures_match_their_assertions() -> None:
    url = "https://huggingface.co/lab/Foo-9B-GGUF/resolve/v2/Foo-9B-Q4_K_M.gguf"
    env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    rt = json.loads(_run("bash", SCRIPTS / "resolve-target.sh", url, env=env).stdout)
    assert (rt["kind"], rt["revision"], rt["file"]) == ("hf_file", "v2", "Foo-9B-Q4_K_M.gguf")
    (row,) = json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--repo", "lab/Foo-9B-GGUF", "--revision", "v2", "--file", "Foo-9B-Q4_K_M.gguf",
                             "--json", "--budget-mib", "8321", "--ram-mib", "12000",
                             env={"HF_FIXTURE_DIR": str(FIX / "hf"), "PATH": "/usr/bin:/bin"}).stdout)
    assert (row["sha"], row["pick"]["mib"], row["pick"]["bytes"], row["pick"]["sha256"]) == (FOO_SHA, 5500, 5767168000, FOO_OID)
    text = _asserts("hf-file-pinned-revision")
    assert all(s in text for s in (f"resolve/{FOO_SHA}/Foo-9B-Q4_K_M.gguf", "5767168000", FOO_OID,
                                   "--revision v2 --file Foo-9B-Q4_K_M.gguf", "Foo-9B-Q5_K_M.gguf", "tree/main"))
    models = yaml.safe_load((FIX / "models.yml").read_text())["models"]
    assert "foo-9b" not in {m["alias"] for m in models} and "Foo-9B-Q4_K_M.gguf" not in {m["file"] for m in models}
    assert "The entry alias is foo-9b" in text


YUX = "yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF"
YUX_SHA = "190a31365a6b80a692349be34ccdac730cad4fe4"
YUX_Q5 = f"https://huggingface.co/{YUX}/resolve/main/Q5_K_M/gemma4-v2-Q5_K_M.gguf"


def _yux_scout(budget: str, ram: str, *extra: str) -> dict:
    (row,) = json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--repo", YUX, "--revision", "main", "--json", "--budget-mib", budget,
                             "--ram-mib", ram, *extra, env={"HF_FIXTURE_DIR": str(FIX / "hf"), "PATH": "/usr/bin:/bin"}).stdout)
    return row


def test_eval_hf_subfolder_file_fixtures_match_their_assertions() -> None:
    slug = YUX.replace("/", "__")
    model = json.loads((FIX / "hf" / f"model-{slug}.json").read_text())
    assert (model["id"], model["gguf"]["context_length"]) == (YUX, 262144)
    assert json.loads((FIX / "hf" / f"model-{slug}@main.json").read_text()) == model
    assert (FIX / "hf" / f"tree-{slug}@main.json").read_text() == (FIX / "hf" / f"tree-{slug}@{YUX_SHA}.json").read_text()
    env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models-gateway.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    rt = json.loads(_run("bash", SCRIPTS / "resolve-target.sh", YUX_Q5, env=env).stdout)
    assert (rt["kind"], rt["revision"], rt["file"]) == ("hf_file", "main", "Q5_K_M/gemma4-v2-Q5_K_M.gguf")
    row = _yux_scout("10369", "14500", "--file", "Q5_K_M/gemma4-v2-Q5_K_M.gguf")
    assert (row["sha"], row["fit"], row["pick"]["bytes"], row["pick"]["sha256"]) == (YUX_SHA, "vram", 8547884576, "5" * 64)
    text = _asserts("hf-subfolder-file")
    assert all(s in text for s in (f"resolve/{YUX_SHA}/Q5_K_M/gemma4-v2-Q5_K_M.gguf", "file gemma4-v2-Q5_K_M.gguf", "8547884576",
                                   "gemma4-q5_k_m", "~/llm-workspace/models/gemma4-v2-Q5_K_M.gguf", "--budget-mib 10369 --ram-mib 14500"))


def test_eval_hf_subfolder_no_fit_fixtures_match_their_assertions() -> None:
    named = _yux_scout("8321", "12000", "--file", "Q5_K_M/gemma4-v2-Q5_K_M.gguf")
    single = _yux_scout("8321", "12000", "--single-file")
    plain = _yux_scout("8321", "12000")
    assert (named["fit"], named["pick"]["mib"], single["fit"]) == ("no", 8151, "no")
    assert (plain["pick"]["file"], plain["pick"]["mib"], plain["pick"]["split"]) == ("Q6_K/gemma4-v2-Q6_K-00001-of-00002.gguf", 9346, True)
    text = _asserts("hf-subfolder-no-fit")
    assert all(s in text for s in ("fit no", "--single-file", "offers none", "Q6_K/gemma4-v2-Q6_K-00001-of-00002.gguf", "9346", "before G1"))


def test_eval_hf_file_is_the_served_file_fixtures_match_their_assertions() -> None:
    gemma = next(m for m in yaml.safe_load((FIX / "models.yml").read_text())["models"] if m["alias"] == "gemma4")
    row = _yux_scout("8321", "12000", "--file", "Q4_K_M/gemma4-v2-Q4_K_M.gguf")
    assert (Path(row["pick"]["file"]).name, row["pick"]["bytes"], row["fit"]) == (gemma["file"], gemma["size_bytes"], "no")
    assert gemma["enabled"] is True and json.loads((FIX / "preflight-served.json").read_text())["budget"]["llmenv_budget"]["feasible"] is True
    url = f"https://huggingface.co/{YUX}/resolve/main/Q4_K_M/gemma4-v2-Q4_K_M.gguf"
    rt = json.loads(_run("bash", SCRIPTS / "resolve-target.sh", url, env={**os.environ, **FIX_ENV}).stdout)
    assert (rt["kind"], rt["revision"], rt["file"]) == ("hf_file", "main", "Q4_K_M/gemma4-v2-Q4_K_M.gguf")
    text = _asserts("hf-file-is-the-served-file")
    assert all(t in text for t in ("7381381664", "enabled-alias row", "references/served-alias.md", "gemma4-2", "feasible true"))


def test_eval_quoted_tilde_fixture_matches_its_assertions() -> None:
    home = FIX / "home"
    assert (home / "Downloads" / "Bar-7B-Q4_K_M.gguf").read_bytes() == GGUF_BYTES
    result = _run("bash", SCRIPTS / "resolve-target.sh", "~/Downloads/Bar-7B-Q4_K_M.gguf", env={**os.environ, **FIX_ENV, "HOME": str(home)}, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    rt = json.loads(result.stdout)
    assert (rt["kind"], rt["quant"], rt["in_models_dir"]) == ("gguf_file", "Q4_K_M", False)
    assert all(s in _asserts("quoted-tilde-gguf") for s in ("kind gguf_file", "in_models_dir false", "never offered", "own directory"))
    assert "only after a feasible fit" in _asserts("named-gguf-argument")


def test_eval_gateway_hf_fixtures_match_their_assertions(tmp_path: Path) -> None:
    pre = json.loads((FIX / "preflight-gateway.json").read_text())
    assert pre["budget"]["llmenv_budget"] is None and pre["budget"]["ram_offload_budget_mib"] == 0
    assert pre["services"]["llm_server_enabled"] is False
    budget = pre["budget"]["model_vram_budget_mib"]
    ram = pre["ram"]["available_mib"]
    (row,) = json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--repo", "lab/Foo-9B-GGUF", "--json", "--budget-mib", str(budget), "--ram-mib", str(ram),
                             env={"HF_FIXTURE_DIR": str(FIX / "hf"), "PATH": "/usr/bin:/bin"}).stdout)
    assert (budget, ram, row["pick"]["file"]) == (10369, 14500, "Foo-9B-Q5_K_M.gguf")
    cfg = yaml.safe_load((FIX / "models-gateway.yml").read_text())
    assert cfg["llm_server"]["enabled"] is False and not [m for m in cfg["models"] if m["enabled"]]
    scratch = _out(tmp_path, "s") / "gateway.yml"
    env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models-gateway.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    result = _run("bash", SCRIPTS / "resolve-target.sh", "gemma4", "--scratch-config", scratch, env=env)
    assert result.returncode == 0, result.stderr
    s = yaml.safe_load(scratch.read_text())
    assert s["llm_server"]["enabled"] is True and [m["alias"] for m in s["models"] if m["enabled"]] == ["gemma4"]
    text = _asserts("hf-repo-gateway-only")
    assert all(x in text for x in ("10369", "--ram-mib 14500", "resolve-target.sh gemma4 --scratch-config", "LLM_ENV_ASSUME_YES=1 make setup", "no baseline"))
    assert "before step 6" in text


def test_eval_gguf_alias_collision_fixture_matches_its_assertions() -> None:
    fixture = FIX / "Ornith-Q5_K_M.gguf"
    assert fixture.read_bytes() == GGUF_BYTES
    env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    result = _run("bash", SCRIPTS / "resolve-target.sh", fixture, env=env)
    assert result.returncode == 0, result.stderr
    rt = json.loads(result.stdout)
    assert (rt["kind"], rt["quant"]) == ("gguf_file", "Q5_K_M")
    by_alias = {m["alias"]: m["file"] for m in yaml.safe_load((FIX / "models.yml").read_text())["models"]}
    assert by_alias["ornith"] == "ornith-1.0-9b-Q4_K_M.gguf" and fixture.name not in by_alias.values() and "ornith-2" not in by_alias
    assert all(s in _asserts("gguf-alias-collision") for s in ("ornith-2", "Q5_K_M", "ornith-1.0-9b-Q4_K_M.gguf"))


def test_eval_system_one_fixtures_match_their_assertions() -> None:
    env = {"SO_FIXTURE_DIR": str(FIX / "system-one"), "SO_DOCKERFILE": str(FIX / "system-one" / "Dockerfile"), "PATH": "/usr/bin:/bin"}
    out = json.loads(_run("bash", SCRIPTS / "system-one-check.sh", env=env).stdout)
    assert (out["pinned"]["laya"], out["latest"]["laya"], out["latest"]["releases_behind"]) == ("0.4.2", "0.5.0", 2)
    assert out["torch_latest_per_rocm_index"]["rocm7.14"] == "2.15.0"
    assert all(s in _asserts("system-one-check") for s in ("0.4.2", "0.5.0", "2 releases behind", "rocm7.14"))


def test_eval_bare_name_fixture_matches_its_assertions() -> None:
    env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    assert _run("bash", SCRIPTS / "resolve-target.sh", "Zeta-Coder-7B", env=env).returncode == 3
    out = _run("bash", SCRIPTS / "hf-scout.sh", "--query", "Zeta-Coder-7B", "--json", "--budget-mib", "8321", "--ram-mib", "12000",
               env={"HF_FIXTURE_DIR": str(FIX / "hf-empty"), "PATH": "/usr/bin:/bin"})
    assert out.returncode == 0 and json.loads(out.stdout) == []
    assert all(s in _asserts("bare-name-no-repo") for s in ("exits 3", "--budget-mib 8321 --ram-mib 12000", "[]", "stops"))


def test_eval_missing_gguf_fixture_matches_its_assertions() -> None:
    path = ".claude/skills/onboard-local-model/evals/files/Missing-7B-Q4_K_M.gguf"
    assert not (ROOT / path).exists()
    env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    result = _run("bash", SCRIPTS / "resolve-target.sh", path, env=env, cwd=ROOT)
    assert (result.returncode, result.stdout) == (2, "") and "not found" in result.stderr
    assert all(s in _asserts("missing-gguf-path") for s in ("exits 2", "not found", "hf-scout.sh --query is not run"))


def test_eval_mistyped_path_fixture_matches_its_assertions() -> None:
    path = ".claude/skills/onboard-local-model/evals/files/Foo-9B-Q4_K_M.ggu"
    env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    result = _run("bash", SCRIPTS / "resolve-target.sh", path, env=env, cwd=ROOT)
    want = f"not a .gguf file (did you mean {path}f?)"
    assert (result.returncode, result.stdout) == (2, "") and want in result.stderr
    assert all(s in _asserts("mistyped-gguf-path") for s in ("exits 2", want, "hf-scout.sh --query is not run"))


def test_eval_early_typo_fixture_matches_its_assertions() -> None:
    arg = ".claude/skills/onboard-local-model/evals/files/Fo-9B-Q4_K_M.gguf"
    result = _run("bash", SCRIPTS / "resolve-target.sh", arg, env={**os.environ, **FIX_ENV}, cwd=ROOT)
    hint = "did you mean .claude/skills/onboard-local-model/evals/files/Foo-9B-Q4_K_M.gguf?"
    assert result.returncode == 2 and "not found" in result.stderr and hint in result.stderr
    assert hint in _asserts("early-typo-gguf-path")


def test_eval_quant_switch_fixtures_match_their_assertions() -> None:
    out = json.loads(_run("bash", SCRIPTS / "compare-runs.sh", "--correctness-only", FIX / "quant" / "q4.jsonl", FIX / "quant" / "q5.jsonl").stdout)
    delta = out["delta_vs_first"][1]
    assert (delta["ladder"], delta["overall"]) == (1, 22.2) and out["warnings"] == []
    text = _asserts("quant-switch-yes")
    assert all(s in text for s in ("ladder delta 1", "overall delta 22.2", "compare-runs.sh --correctness-only",
                                   "make llama-enable MODEL=gemma4-q5_k_m", "moves my-coding-lite to gemma4-q5_k_m",
                                   "no separate make local-combo", "make llama-enable MODEL=gemma4,"))


def test_eval_quant_fixtures_match_their_assertions() -> None:
    show = _run("uv", "run", "llmenv.py", "--config", FIX / "models.yml", "capacity", "show", "--alias", "gemma4", cwd=ROOT)
    assert show.returncode == 1 and "no capacity card for gemma4" in show.stdout
    gemma = next(m for m in yaml.safe_load((FIX / "models.yml").read_text())["models"] if m["alias"] == "gemma4")
    depths = f"0,32768,{min(131072, gemma['ctx_size'] - 512)}"
    text = _asserts("quant-head-to-head")
    assert depths == "0,32768,130560"
    assert all(s in text for s in ("P0", "no ladder lane has run", "no capacity card", depths, "without --vram-available-mib",
                                   "make start-llama", "5 x 3 x 2 x 300 s"))
    assert re.fullmatch(r"https://huggingface\.co/[^/]+/[^/]+/resolve/[0-9a-f]{40}/.+", gemma["url"])


def test_quant_comparison_checks_headroom_before_the_ladder_lane() -> None:
    tuning = _md(REFS / "tuning.md")
    text = _section(tuning, "### Standalone quant comparison", "- Q1.")
    marks = [text.find(m) for m in ("P0. The entry url", "P1. A passed interference gate", "P2. A current depth section",
                                     "After Q1, only when a candidate has `fits: true`", "P3. `<seeds>`")]
    assert -1 not in marks and marks == sorted(marks), marks
    assert "<dir>" not in tuning and "P3 did it" not in tuning and "(P2 did it; else Gate G-T2)" in tuning and "G-Q0" in text


def test_quant_headroom_check_refuses_an_entry_without_a_hugging_face_url(tmp_path: Path) -> None:
    """Coverage: the message P0 quotes (quant-headroom.sh is unchanged)."""
    cfg = json.loads(json.dumps(QH_CONFIG))
    cfg["models"][0]["url"] = "file:///x/m-Q4_K_M.gguf"
    result = _qh(_qh_env(tmp_path, config=cfg), "check", "--alias", "m", "--vram-available-mib", "8000")
    assert result.returncode == 1 and "entry url is not a Hugging Face resolve url: file:///x/m-Q4_K_M.gguf" in result.stderr
    assert not list((tmp_path / "out").glob("model-tune/quant-*/candidates.json"))


def test_eval_local_url_fixture_matches_its_assertions() -> None:
    url = "file:///var/home/bazzite/Downloads/gemma4-v2-Q4_K_M.gguf"
    base = yaml.safe_load((FIX / "models.yml").read_text())
    next(m for m in base["models"] if m["alias"] == "gemma4")["url"] = url
    assert yaml.safe_load((FIX / "models-local-url.yml").read_text()) == base
    assert all(s in _asserts("quant-room-local-entry") for s in (url, "entry url is not a Hugging Face resolve url", "G-Q0"))


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_eval_subfolder_quant_fixture_matches_its_assertions(tmp_path: Path) -> None:
    base = yaml.safe_load((FIX / "models.yml").read_text())
    gemma = next(m for m in base["models"] if m["alias"] == "gemma4")
    gemma["url"] = gemma["url"].replace("/gemma4-v2-Q4_K_M.gguf", "/Q4_K_M/gemma4-v2-Q4_K_M.gguf")
    sub = yaml.safe_load((FIX / "models-subfolder.yml").read_text())
    assert sub == base
    env = _qh_env(tmp_path, config=sub) | {"HF_FIXTURE_DIR": str(FIX / "hf")}
    result = _qh(env, "check", "--alias", "gemma4", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    rows = {c["quant"]: c for c in json.loads(_candidates(tmp_path)[0].read_text())["candidates"]}
    assert (rows["Q5_K_M"]["path"], rows["Q5_K_M"]["file"], rows["Q5_K_M"]["size_bytes"]) == (
        "Q5_K_M/gemma4-v2-Q5_K_M.gguf", "gemma4-v2-Q5_K_M.gguf", 8547884576)
    assert rows["Q6_K"] == {"quant": "Q6_K", "reason": "sharded, not supported"}
    assert all(s in _asserts("quant-room-subfolder-entry") for s in (gemma["url"], "Q5_K_M/gemma4-v2-Q5_K_M.gguf", "8547884576",
                                                                       "sharded, not supported", "G-Q0 is not asked"))


def test_eval_second_lane_fixture_matches_its_assertions() -> None:
    flow = _md(REFS / "onboard-flow.md")
    assert "--out tmp/model-scout/<run>/effort-baseline" in flow and "--out tmp/model-scout/<run>/effort --efforts low,high --seeds" in flow
    assert "--out <out> <flags> > <out>.log" in _md(REFS / "scoring.md")
    text = _asserts("second-lane-out-dir")
    assert all(s in text for s in ("--out tmp/model-scout/20261031-foo-9b/effort --efforts low,high --seeds tmp/model-scout/20261031-foo-9b/effort-baseline/seeds.json",
                                   "exits 2", "tmp/model-scout/20261031-foo-9b/effort.log"))


def test_eval_tune_fixtures_match_their_assertions() -> None:
    cfg = yaml.safe_load((FIX / "models.yml").read_text())
    assert cfg["llm_server"]["enabled"] is True
    assert all(s in _asserts("tune-capacity-card") for s in ("wait-gpu-release.sh", "G-T1 (make llama-enable) is not asked"))


def test_hf_scout_repo_screens_one_named_repo(tmp_path: Path) -> None:
    (tmp_path / "model-lab__x-abliterated-GGUF.json").write_text(json.dumps(_repo("lab/x-abliterated-GGUF", 8, 32768, "qwen35")))
    (tmp_path / "tree-lab__x-abliterated-GGUF.json").write_text(json.dumps(_tree(("m-Q4_K_M.gguf", 5000))))
    env = {"HF_FIXTURE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    base = ("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000")
    result = _run(*base, "--repo", "lab/x-abliterated-GGUF", env=env)
    assert result.returncode == 0, result.stderr
    (row,) = json.loads(result.stdout)
    assert row["id"] == "lab/x-abliterated-GGUF" and row["ctx"] == 32768 and row["fit"] == "vram" and row["pick"]["quant"] == "Q4_K_M"
    assert _run(*base, "--repo", "lab/x", "--query", "y", env=env).returncode == 2
    assert _run(*base, "--repo", "not a repo", env=env).returncode == 2


REV_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


def _lfs_tree(*files: tuple[str, int, str | None]) -> list[dict[str, object]]:
    return [{"path": n, "size": mib * 1048576, **({"lfs": {"oid": oid, "size": mib * 1048576}} if oid else {})} for n, mib, oid in files]


def test_hf_scout_reads_a_named_file_at_a_pinned_revision(tmp_path: Path) -> None:
    (tmp_path / "model-lab__x@v2.json").write_text(json.dumps(_repo("lab/x", 8, 262144, "qwen35") | {"sha": REV_SHA}))
    (tmp_path / "tree-lab__x@v2.json").write_text(json.dumps(_lfs_tree(("m-Q4_K_M.gguf", 5000, "4" * 64), ("m-Q8_0.gguf", 8500, "8" * 64))))
    (tmp_path / "model-lab__x.json").write_text(json.dumps(_repo("lab/x", 8, 262144, "qwen35") | {"sha": "f" * 40}))
    (tmp_path / "tree-lab__x.json").write_text(json.dumps(_lfs_tree(("m-Q4_K_M.gguf", 6000, "0" * 64))))
    env = {"HF_FIXTURE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    base = ("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000", "--repo", "lab/x")
    result = _run(*base, "--revision", "v2", "--file", "m-Q4_K_M.gguf", env=env)
    assert result.returncode == 0, result.stderr
    (row,) = json.loads(result.stdout)
    assert row["sha"] == REV_SHA, "the commit the revision resolved to, never the branch name"
    assert row["fit"] == "vram"
    assert row["pick"] == {"file": "m-Q4_K_M.gguf", "quant": "Q4_K_M", "mib": 5000, "bpw": 5.2, "bytes": 5000 * 1048576, "sha256": "4" * 64, "split": False}, \
        "the named file wins although Q8_0 (8500 + 1536 <= 10369) is the scout's choice"
    (main,) = json.loads(_run(*base, env=env).stdout)
    assert (main["sha"], main["pick"]["mib"]) == ("f" * 40, 6000), "no --revision reads the main fixtures"
    absent = _run(*base, "--revision", "v2", "--file", "m-Q5_K_M.gguf", env=env)
    assert absent.returncode == 2 and "m-Q5_K_M.gguf" in absent.stderr and "v2" in absent.stderr
    missing = _run(*base, "--revision", "nope", env=env)
    assert missing.returncode == 2 and "nope" in missing.stderr
    for flag in (("--revision", "v2"), ("--file", "m-Q4_K_M.gguf")):
        assert _run("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "1", "--ram-mib", "1", *flag, env=env).returncode == 2, flag


@pytest.mark.parametrize("revision", ["..", "../../evil", "v2?x=1", "a/b", "v 2"])
def test_hf_scout_rejects_an_unsafe_revision(tmp_path: Path, revision: str) -> None:
    result = _run("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000", "--repo", "lab/x", "--revision", revision,
                  env={"HF_FIXTURE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert result.returncode == 2 and "--revision" in result.stderr and not result.stdout


def test_hf_scout_named_split_file_sums_bytes_and_has_no_single_checksum(tmp_path: Path) -> None:
    (tmp_path / "model-lab__x.json").write_text(json.dumps(_repo("lab/x", 8, 262144, "qwen35")))  # no "sha" in the answer
    shards = (("Q8_0/m-Q8_0-00001-of-00002.gguf", 4250, "1" * 64), ("Q8_0/m-Q8_0-00002-of-00002.gguf", 4250, "2" * 64))
    (tmp_path / "tree-lab__x.json").write_text(json.dumps(_lfs_tree(*shards)))
    result = _run("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000", "--repo", "lab/x",
                  "--file", "Q8_0/m-Q8_0-00001-of-00002.gguf", env={"HF_FIXTURE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert result.returncode == 0, result.stderr
    (row,) = json.loads(result.stdout)
    assert row["sha"] is None, "an answer without sha is null, never main"
    assert (row["pick"]["bytes"], row["pick"]["sha256"]) == (8500 * 1048576, None)


def test_hf_scout_file_takes_a_bare_name_in_a_sub_folder(tmp_path: Path) -> None:
    (tmp_path / "model-lab__x.json").write_text(json.dumps(_repo("lab/x", 8, 262144, "qwen35")))
    (tmp_path / "tree-lab__x.json").write_text(json.dumps(_lfs_tree(("Q4_K_M/m-Q4_K_M.gguf", 5000, "4" * 64), ("m-Q5_K_M.gguf", 6000, "5" * 64),
                                                                    ("old/m-Q5_K_M.gguf", 6000, "6" * 64))))
    base = ("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000", "--repo", "lab/x", "--file")
    pick = lambda f: json.loads(_run(*base, f, env={"HF_FIXTURE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}).stdout)[0]["pick"]
    assert (pick("m-Q4_K_M.gguf")["file"], pick("m-Q4_K_M.gguf")["sha256"]) == ("Q4_K_M/m-Q4_K_M.gguf", "4" * 64)
    assert pick("m-Q5_K_M.gguf")["file"] == "m-Q5_K_M.gguf", "an exact path wins over a sub-folder copy"


def test_hf_scout_single_file_skips_split_quants_and_marks_split(tmp_path: Path) -> None:
    for repo, tree in (("lab__x", (("m-Q4_K_M.gguf", 5000, "4" * 64), ("Q8_0/m-Q8_0-00001-of-00002.gguf", 3000, "1" * 64),
                                   ("Q8_0/m-Q8_0-00002-of-00002.gguf", 3000, "2" * 64))),
                       ("lab__y", (("m-Q6_K-00001-of-00002.gguf", 2000, "1" * 64), ("m-Q6_K-00002-of-00002.gguf", 2000, "2" * 64)))):
        (tmp_path / f"model-{repo}.json").write_text(json.dumps(_repo(repo.replace("__", "/"), 8, 262144, "qwen35")))
        (tmp_path / f"tree-{repo}.json").write_text(json.dumps(_lfs_tree(*tree)))
    env = {"HF_FIXTURE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}

    def scout(repo: str, *extra: str) -> subprocess.CompletedProcess[str]:
        return _run("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000", "--repo", repo, *extra, env=env)

    (plain,) = json.loads(scout("lab/x").stdout)
    assert (plain["fit"], plain["pick"]["file"], plain["pick"]["split"]) == ("vram", "Q8_0/m-Q8_0-00001-of-00002.gguf", True)
    (single,) = json.loads(scout("lab/x", "--single-file").stdout)
    assert (single["fit"], single["pick"]["file"], single["pick"]["split"], single["quants"]) == ("vram", "m-Q4_K_M.gguf", False, ["Q4_K_M:5000"])
    none = scout("lab/y", "--single-file")
    assert none.returncode == 0 and [(r["fit"], r["pick"], r["quants"]) for r in json.loads(none.stdout)] == [("no", None, [])]
    both = scout("lab/x", "--single-file", "--file", "m-Q4_K_M.gguf")
    assert both.returncode == 2 and "--single-file" in both.stderr and not both.stdout


def test_hf_scout_quant_picks_that_quant_whatever_the_fit() -> None:
    env = {"HF_FIXTURE_DIR": str(FIX / "hf"), "PATH": "/usr/bin:/bin"}
    base = ("--repo", "lab/Foo-9B-GGUF", "--json", "--budget-mib", "8321", "--ram-mib", "12000")
    scout = lambda *a: _run("bash", SCRIPTS / "hf-scout.sh", *a, env=env)
    (row,) = json.loads(scout(*base, "--quant", "q4_k_m").stdout)
    assert (row["pick"]["file"], row["pick"]["mib"], row["quants"]) == ("Foo-9B-Q4_K_M.gguf", 5600, ["Q4_K_M:5600"])
    missing = scout(*base, "--quant", "Q3_K_S")
    assert missing.returncode == 2 and "no quant Q3_K_S in lab/Foo-9B-GGUF" in missing.stderr
    for extra in (("--file", "Foo-9B-Q4_K_M.gguf"), ("--single-file",)):
        assert scout(*base, "--quant", "Q4_K_M", *extra).returncode == 2, extra
    assert scout("--query", "x", "--quant", "Q4_K_M", "--budget-mib", "1", "--ram-mib", "1").returncode == 2


def test_quant_p0_pins_the_repo_path_and_names_hf_scout_exit_2() -> None:
    tuning = _md(REFS / "tuning.md")
    p0 = _section(tuning, "P0. The entry url", "- P1.")
    assert "`<pick.file>` keeps its sub-folder" in p0 and "Exit 2 (no file of that name" in p0 and "a top-level file" not in p0
    assert '`fit: "unknown"`' in p0 and "report a read failure" in p0
    assert "G-Q3 still runs" in tuning and "sub-folder" in _section(tuning, "## Quant headroom", "### Standalone")


def test_hf_scout_requests_the_revision_and_the_tree_at_its_commit(tmp_path: Path) -> None:
    calls = tmp_path / "calls"
    (tmp_path / "model.json").write_text(json.dumps(_repo("lab/x", 8, 262144, "qwen35") | {"sha": REV_SHA}))
    (tmp_path / "tree.json").write_text(json.dumps(_lfs_tree(("m-Q4_K_M.gguf", 5000, "4" * 64))))
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "curl").write_text(f"""#!/usr/bin/env bash
echo "$*" >> {calls}
url=""; out=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift ;; https://*) url="$1" ;; esac; shift; done
case "$url" in
    *"/tree/{REV_SHA}?recursive=true") cat {tmp_path}/tree.json > "$out" ;;
    *"/revision/"*) cat {tmp_path}/model.json ;;
    *) exit 22 ;;
esac
""")
    (stub / "curl").chmod(0o755)
    result = _run("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000", "--repo", "lab/x",
                  "--revision", "refs%2Fpr%2F3", env={"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    text = calls.read_text()
    assert "/api/models/lab/x/revision/refs%2Fpr%2F3" in text and "expand[]=sha" in text
    assert f"/api/models/lab/x/tree/{REV_SHA}?recursive=true" in text, "the tree is read at the resolved commit"
    assert json.loads(result.stdout)[0]["pick"]["sha256"] == "4" * 64


def _hf_net(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """hf-scout.sh in network mode against a curl stub that logs argv, stdin (with -K -) and its parent's argv."""
    calls = tmp_path / "calls"
    (tmp_path / "model.json").write_text(json.dumps(_repo("lab/x", 8, 262144, "qwen35") | {"sha": REV_SHA}))
    (tmp_path / "tree.json").write_text(json.dumps(_lfs_tree(("m-Q4_K_M.gguf", 5000, "4" * 64))))
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "curl").write_text(f"""#!/usr/bin/env bash
echo "$*" >> {calls}
case " $* " in *" -K - "*) cat >> {calls}.stdin ;; esac
tr '\\0' ' ' < /proc/$PPID/cmdline >> {calls}.parent; echo >> {calls}.parent
url=""; out=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift ;; https://*) url="$1" ;; esac; shift; done
case "$url" in
    *"/tree/{REV_SHA}?recursive=true") cat {tmp_path}/tree.json > "$out" ;;
    *"/revision/"*) cat {tmp_path}/model.json ;;
    *) exit 22 ;;
esac
""")
    (stub / "curl").chmod(0o755)
    return _run("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000", "--repo", "lab/x",
                env={"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(tmp_path), **env})


def test_hf_scout_sends_hf_token_only_through_curl_stdin(tmp_path: Path) -> None:
    result = _hf_net(tmp_path, HF_TOKEN=HF_TOKEN_VALUE)
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    assert len(calls) == 2 and all(" -K - " in f" {c} " and HF_TOKEN_VALUE not in c for c in calls)
    assert (tmp_path / "calls.stdin").read_text() == f'header = "Authorization: Bearer {HF_TOKEN_VALUE}"\n' * 2
    assert HF_TOKEN_VALUE not in (tmp_path / "calls.parent").read_text() + result.stdout + result.stderr
    plain = tmp_path / "plain"
    plain.mkdir()
    result = _hf_net(plain)
    assert result.returncode == 0 and "-K" not in (plain / "calls").read_text() and not (plain / "calls.stdin").exists()


def test_hf_scout_refuses_a_malformed_hf_token_before_any_request(tmp_path: Path) -> None:
    result = _hf_net(tmp_path, HF_TOKEN='hf_x"\nurl = "https://example.com/')
    assert result.returncode == 2 and "HF_TOKEN" in result.stderr and "hf_x" not in result.stdout + result.stderr
    assert not (tmp_path / "calls").exists()


@pytest.mark.parametrize(
    ("budget", "extra"),
    [('{"feasible": true, "available_mib": 9500, "required_mib": 1, "ram_available_mib": 1024, "ram_feasible": true}', ()),
     (None, ("--ram-mib", "0"))],
    ids=["llmenv-larger", "llmenv-error"],
)
def test_hf_scout_defaults_to_the_smaller_preflight_budget(tmp_path: Path, budget: str | None, extra: tuple[str, ...]) -> None:
    fixtures = tmp_path / "hf"
    fixtures.mkdir()
    (fixtures / "models.json").write_text("[]")
    env = _preflight_env(tmp_path, budget, gpu={"vram_budget_ceiling_mib": 9000}) | {"HF_FIXTURE_DIR": str(fixtures)}
    result = _run("bash", SCRIPTS / "hf-scout.sh", *extra, env=env)
    assert result.returncode == 0, result.stderr
    assert "VRAM 9000 MiB" in result.stdout.splitlines()[0], "rule 1: the smaller of the repo verdict and ceiling minus reserve"


def _cand(id_: str, fit: str = "vram", ctx: int = 262144, updated: str = "2026-10-01", bpw: float = 4.8, mib: int = 6000, downloads: int = 100) -> dict:
    return {"id": id_, "params_b": 8, "ctx": ctx, "fit": fit, "pick": {"quant": "Q4_K_M", "mib": mib, "bpw": bpw}, "updated": updated, "downloads": downloads}


def _rank(tmp_path: Path, files: list[list[dict]], *extra: str | Path) -> subprocess.CompletedProcess[str]:
    args: list[str | Path] = []
    for n, rows in enumerate(files):
        path = tmp_path / f"scout{n}.json"
        path.write_text(json.dumps(rows))
        args += ["--scout", path]
    return _run("bash", SCRIPTS / "rank-candidates.sh", *args, *extra)


def _card(id_: str, levels: tuple[float, ...] = (3, 3, 3, 3, 3), **over: object) -> dict:
    card: dict = {
        "id": id_,
        "gates": {"tool_template": True, "arch_supported": True, "no_red_flags": True},
        "levels": dict(zip(("agentic_evidence", "speed", "context", "lab", "recency"), levels, strict=True)),
        "jev": {},
        "sources": [],
        "notes": "",
    }
    card.update(over)
    return card


def _cards(tmp_path: Path, *cards: dict) -> Path:
    d = tmp_path / "cards"
    d.mkdir()
    for n, card in enumerate(cards, 1):
        (d / f"{n}.json").write_text(json.dumps(card))
    return d


def test_rank_candidates_gates_and_orders_the_scout_rows(tmp_path: Path) -> None:
    first = [_cand("A", updated="2026-10-01"), _cand("B", ctx=131072, updated="2026-10-20"), _cand("D", fit="no")]
    second = [_cand("A", ctx=1), _cand("C", fit="offload"), _cand("E", bpw=3.0), _cand("F", ctx=32768)]
    result = _rank(tmp_path, [first, second])
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert [r["id"] for r in out["shortlist"]] == ["A", "B", "C"]
    assert [r["rank"] for r in out["shortlist"]] == [1, 2, 3]
    assert out["shortlist"][0]["pick"]["quant"] == "Q4_K_M"
    assert out["gates_failed"] == [{"id": "D", "gate": "fit"}, {"id": "E", "gate": "bpw"}, {"id": "F", "gate": "ctx"}]
    top = json.loads(_rank(tmp_path, [first, second], "--top", "2").stdout)
    assert [r["id"] for r in top["shortlist"]] == ["A", "B"]


def test_rank_candidates_breaks_ties_by_updated_then_downloads(tmp_path: Path) -> None:
    rows = [_cand("old", updated="2026-09-01", downloads=9), _cand("new", updated="2026-10-01", downloads=1), _cand("newpop", updated="2026-10-01", downloads=5)]
    out = json.loads(_rank(tmp_path, [rows]).stdout)
    assert [r["id"] for r in out["shortlist"]] == ["newpop", "new", "old"]


def test_rank_candidates_shortlists_six_rows_by_default(tmp_path: Path) -> None:
    out = json.loads(_rank(tmp_path, [[_cand(f"r{n}", downloads=n) for n in range(7)]]).stdout)
    assert [r["id"] for r in out["shortlist"]] == ["r6", "r5", "r4", "r3", "r2", "r1"]


def test_rank_candidates_scores_evidence_and_flags_jev_disagreements(tmp_path: Path) -> None:
    rows = [_cand("A", mib=7000), _cand("B", mib=5000), _cand("C"), _cand("D")]
    cards = _cards(
        tmp_path,
        _card("A", jev={"agentic_evidence": {"level": 1, "needs_fallback": False}, "lab": {"level": 2, "needs_fallback": False}}),
        _card("B", jev={"speed": {"level": None, "needs_fallback": True}}),
        _card("C", gates={"tool_template": True, "arch_supported": False, "no_red_flags": True}),
        _card("D", (3, 0, 0, 0, 0), gates={"tool_template": None, "arch_supported": True, "no_red_flags": True}),
    )
    result = _rank(tmp_path, [rows], "--evidence", cards)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert [(r["id"], r["score"]) for r in out["ranked"]] == [("B", 27), ("A", 27), ("D", 9)]
    assert out["ranked"][2]["unverified"] == ["tool_template"] and out["ranked"][0]["unverified"] == []
    assert [e["id"] for e in out["excluded"]] == ["C"] and "arch_supported" in out["excluded"][0]["reason"]
    assert out["disagreements"] == [
        {"id": "A", "criterion": "agentic_evidence", "level": 3, "jev": 1},
        {"id": "B", "criterion": "speed", "level": 3, "jev": None},
    ]


def test_rank_candidates_never_ranks_a_card_for_a_scout_gated_row(tmp_path: Path) -> None:
    rows = [_cand("a/ok"), _cand("b/lowbit", fit="vram-lowbit", bpw=2.1, mib=3000)]
    jev = {"speed": {"level": None, "needs_fallback": True}}
    cards = _cards(tmp_path, _card("a/ok"), _card("b/lowbit", jev=jev))
    out = json.loads(_rank(tmp_path, [rows], "--evidence", cards).stdout)
    assert [r["id"] for r in out["ranked"]] == ["a/ok"]
    assert out["excluded"] == [{"id": "b/lowbit", "reason": "scout gate: fit"}]
    assert out["disagreements"] == []


@pytest.mark.parametrize(
    "card",
    [
        _card("A", (4, 3, 3, 3, 3)),
        _card("A", (3, 3, -1, 3, 3)),
        _card("A", (3, 3, 1.5, 3, 3)),
        _card("A", gates={"tool_template": True, "arch_supported": "yes", "no_red_flags": True}),
        _card("A", gates={"tool_template": True}),
        _card("A", jev={"speed": 3}),
        _card("ghost"),
        {"levels": {}},
        [],
    ],
    ids=["level-4", "negative", "fraction", "gate-string", "gate-missing", "jev-malformed", "unknown-id", "no-id", "not-an-object"],
)
def test_rank_candidates_rejects_bad_cards(tmp_path: Path, card: object) -> None:
    result = _rank(tmp_path, [[_cand("A")]], "--evidence", _cards(tmp_path, card))  # type: ignore[arg-type]
    assert result.returncode == 2 and result.stderr and not result.stdout


def test_rank_candidates_rejects_duplicate_cards_malformed_json_and_bad_flags(tmp_path: Path) -> None:
    rows = [[_cand("A")]]
    assert _rank(tmp_path, rows, "--evidence", _cards(tmp_path, _card("A"), _card("A"))).returncode == 2
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "1.json").write_text("{nope")
    assert _rank(tmp_path, rows, "--evidence", tmp_path / "bad").returncode == 2
    assert _rank(tmp_path, rows, "--top", "0").returncode == 2
    assert _rank(tmp_path, rows, "--nope").returncode == 2
    (tmp_path / "notrows.json").write_text('{"id": "A"}')
    assert _run("bash", SCRIPTS / "rank-candidates.sh", "--scout", tmp_path / "notrows.json").returncode == 2
    assert _run("bash", SCRIPTS / "rank-candidates.sh", "--scout", tmp_path / "missing.json").returncode == 2


_PREFLIGHT_STUB_UV = """#!/bin/bash
for a in "$@"; do
    case "$a" in
    detect) echo '{"gpus": [{"pci_address": "0000:00:00.0", "vram_total_mib": 16304, "vram_used_mib": 1000, "render_node": "renderD128"}]}'; exit 0 ;;
    processes-on-render-node) if [ -n "${PROCESSES_JSON:-}" ]; then echo "$PROCESSES_JSON"; exit 0; fi; echo '{"processes": []}'; exit 0 ;;
    budget) if [ -n "${BUDGET_JSON:-}" ]; then echo "$BUDGET_JSON"; exit 0; fi; echo '{"error": "x"}'; exit 1 ;;
    resources) if [ -n "${RESOURCES_JSON:-}" ]; then echo "$RESOURCES_JSON"; exit 0; fi; echo '{"error": "y"}'; exit 1 ;;
    esac
done
exit 1
"""


def _preflight_env(tmp_path: Path, budget: str | None, resources: str | None = None, gpu: dict[str, object] | None = None) -> dict[str, str]:
    cfg = {
        "gpu": {"pci_address": "0000:00:00.0", "backend": "vulkan", "image": "i"} | (gpu or {}),
        "resources": {"llm_server": {"memory_ceiling_pct": 60}},
        "llm_server": {"enabled": True}, "system_one": {"enabled": False}, "models": [{"alias": "m", "enabled": True}],
    }
    (tmp_path / "models.yml").write_text(yaml.safe_dump(cfg))
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "podman").write_text("#!/bin/bash\nexit 0\n")
    (stubs / "uv").write_text(_PREFLIGHT_STUB_UV)
    for stub in stubs.iterdir():
        stub.chmod(0o755)
    path = ":".join([str(stubs), *{str(Path(p).parent) for p in (shutil.which("yq"), shutil.which("jq")) if p}, "/usr/bin:/bin"])
    env = {"PATH": path, "HOME": str(tmp_path), "LLM_ENV_CONFIG": str(tmp_path / "models.yml"), "LLM_ENV_MODELS_DIR": str(tmp_path / "models")}
    return env | {k: v for k, v in (("BUDGET_JSON", budget), ("RESOURCES_JSON", resources)) if v}


@pytest.mark.parametrize(
    ("budget", "resources", "expected"),
    [
        ('{"feasible": true, "available_mib": 9000, "required_mib": 8000, "ram_available_mib": 1024, "ram_feasible": true}', None, (1024, "llmenv budget")),
        (None, '{"llm_server": {"memory_mib": 900}}', (900, "llmenv resources")),
        (None, None, (None, None)),
    ],
    ids=["budget", "resources-fallback", "neither"],
)
def test_preflight_takes_the_ram_budget_from_llmenv(
    tmp_path: Path, budget: str | None, resources: str | None, expected: tuple[int | None, str | None]
) -> None:
    # 1024 and 900 assume the host has more MemAvailable; the old formula gave 60 % of MemTotal.
    result = _run("bash", SCRIPTS / "preflight.sh", env=_preflight_env(tmp_path, budget, resources))
    assert result.returncode == 0, result.stderr
    got = json.loads(result.stdout)["budget"]
    assert (got["ram_offload_budget_mib"], got["ram_budget_source"]) == expected


def test_preflight_reports_no_llmenv_budget_when_llmenv_budget_cannot_answer(tmp_path: Path) -> None:
    result = _run("bash", SCRIPTS / "preflight.sh", env=_preflight_env(tmp_path, None, '{"llm_server": {"memory_mib": 900}}'))
    assert result.returncode == 0, result.stderr
    budget = json.loads(result.stdout)["budget"]
    assert budget["llmenv_budget"] is None and budget["ram_budget_source"] == "llmenv resources"


@pytest.mark.parametrize(("ceiling", "expected"), [(0, 16304), (15000, 15000)])
def test_preflight_treats_a_zero_ceiling_as_no_cap(tmp_path: Path, ceiling: int, expected: int) -> None:
    result = _run("bash", SCRIPTS / "preflight.sh", env=_preflight_env(tmp_path, None, None, gpu={"vram_budget_ceiling_mib": ceiling}))
    assert result.returncode == 0, result.stderr
    budget = json.loads(result.stdout)["budget"]
    assert budget["vram_ceiling_mib"] == expected and budget["model_vram_budget_mib"] == expected  # System One off: no reserve


def test_preflight_names_an_empty_pci_address(tmp_path: Path) -> None:
    result = _run("bash", SCRIPTS / "preflight.sh", env=_preflight_env(tmp_path, None, None, gpu={"pci_address": ""}))
    assert result.returncode == 2 and "gpu.pci_address is empty" in result.stderr and "make setup" in result.stderr
    assert "then make restart" in result.stderr


def test_preflight_passes_vram_feasible_through(tmp_path: Path) -> None:
    verdict = '{"feasible": false, "vram_feasible": true, "available_mib": 9000, "required_mib": 8000, "ram_available_mib": 0, "ram_feasible": false}'
    budget = json.loads(_run("bash", SCRIPTS / "preflight.sh", env=_preflight_env(tmp_path, verdict)).stdout)["budget"]["llmenv_budget"]
    assert (budget["feasible"], budget["vram_feasible"], budget["ram_feasible"]) == (False, True, False)


def _verdict(available: int, reserve: int, compositor: bool) -> str:
    return json.dumps({"feasible": False, "vram_feasible": False, "available_mib": available, "required_mib": 5000,
                       "shortfall_mib": max(0, 5000 - available), "reserve_mib": reserve, "compositor_on_this_gpu": compositor,
                       "ram_available_mib": 9000, "ram_feasible": True, "ram_required_mib": 0, "remedies": ["move the compositor"]})


LLAMA_HOLDERS = [{"pid": 2, "comm": "llama-server", "vram_mib": 12}, {"pid": 3, "comm": "llama-server", "vram_mib": 9288}]


@pytest.mark.parametrize(("verdict", "procs", "expected"), [
    (_verdict(-3840, 14000, True), LLAMA_HOLDERS, (9300, 5460, 0, True, True, [])),
    (_verdict(-3840, 9800, True), LLAMA_HOLDERS, (8776, 4936, 64, False, False, ["move the compositor"])),  # capped at reserve - floor
    (_verdict(-3840, 14000, True), [{"pid": 4, "comm": "kwin_wayland", "vram_mib": 500}], (0, -3840, 8840, False, False, ["move the compositor"])),
    (_verdict(1000, 1024, False), LLAMA_HOLDERS, (0, 1000, 4000, False, False, ["move the compositor"])),
], ids=["compositor-gpu", "floor-cap", "llm-server-stopped", "compositor-elsewhere"])
def test_preflight_adds_back_llama_server_vram_on_the_compositor_gpu(
    tmp_path: Path, verdict: str, procs: list[dict[str, object]], expected: tuple[object, ...]
) -> None:
    env = _preflight_env(tmp_path, verdict, gpu={"reserve_floor_mib": 1024}) | {"PROCESSES_JSON": json.dumps({"processes": procs})}
    result = _run("bash", SCRIPTS / "preflight.sh", env=env)
    assert result.returncode == 0, result.stderr
    b = json.loads(result.stdout)["budget"]["llmenv_budget"]
    got = (b["llama_server_added_back_mib"], b["vram_available_mib"], b["vram_shortfall_mib"], b["vram_feasible"], b["feasible"], b["remedies"])
    assert got == expected


def test_hf_scout_refuses_a_budget_that_is_not_a_number(tmp_path: Path) -> None:
    result = _run(
        "bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "null",
        env={"HF_FIXTURE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 2 and "--ram-mib" in result.stderr
