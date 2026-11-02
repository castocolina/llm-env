import copy
import json
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import write_test_config

from pylib import capacity
from pylib.capacity import CapacityError
from pylib.config import load_config

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "pylib" / "capacity_card.schema.json").read_text())
NOW = datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC)

QWEN = {
    "alias": "qwen", "label": "Qwen", "parameters": "1B", "quantization": "Q4_K_M", "enabled": True,
    "file": "qwen.gguf", "url": "u", "size_bytes": 1, "vram_budget": "10%", "ctx_size": 131072,
    "client_max_output_tokens": 8192, "n_gpu_layers": 99,
}

BENCH_FIT_OUTPUT = {
    "alias": "qwen",
    "file": "qwen.gguf",
    "n_cpu_moe": None,
    "flags": {
        "n_gpu_layers": 99, "n_cpu_moe": None, "cache_type_k": "q8_0", "cache_type_v": "q8_0",
        "ubatch_size": 512, "flash_attn": True, "threads": None,
    },
    "limits": {"cpus": None, "memory_mib": None},
    "system_one_warm": True,
    "gpu": {
        "samples": 64, "seconds": 127, "first_vram_mib": 711, "peak_vram_mib": 8759, "peak_temp_c": 41,
        "avg_sclk_mhz": 1481, "avg_gpu_busy_pct": 56, "avg_cpu_pct": 14, "model_vram_mib": 8048,
    },
    "tps": [
        {"depth": 32768, "pp_tps": 257.290166, "tg_tps": 32.242981},
        {"depth": 0, "pp_tps": 389.040026, "tg_tps": 37.498983},
    ],
}

REPORT = {
    "schema": 1, "passed": True, "failures": [], "notes": [], "system_one": None, "omniroute": None,
    "events_changed": [], "vram": {"total_mib": 16304, "peak_used_mib": 9000, "headroom_min_mib": 7304},
    "host": {"mem_available_min_mib": 20000, "swap_delta_mib": 0},
    "llm_server": {"memory_peak_mib": 6000, "memory_cap_mib": 12000},
}


def bench(points, **overrides):
    out = copy.deepcopy(BENCH_FIT_OUTPUT)
    out["tps"] = [{"depth": d, "pp_tps": pp, "tg_tps": tg} for d, pp, tg in points]
    out.update(overrides)
    return out


def edit_config(path: Path, **changes) -> None:
    data = yaml.safe_load(path.read_text())
    next(m for m in data["models"] if m["alias"] == "qwen").update(changes)
    path.write_text(yaml.safe_dump(data))


@pytest.fixture
def env(tmp_path):
    config = write_test_config(tmp_path)
    data = yaml.safe_load(config.read_text())
    data["models"][0]["enabled"] = False
    data["models"].append(dict(QWEN))
    data["models"].append(dict(QWEN, alias="Qwen3", file="q3.gguf", enabled=True))
    data["llm_server"] = {"enabled": True}
    config.write_text(yaml.safe_dump(data))
    return config


def cfg_of(config: Path) -> dict:
    return load_config(config)


def read(config) -> dict:
    card = capacity.read_card(config, cfg_of(config), "qwen")
    assert card is not None
    return card


def write(config, **kw):
    kw.setdefault("now", NOW)
    return capacity.write_card(config, cfg_of(config), "qwen", **kw)


def test_card_path_rejects_unknown_or_unsafe_alias(env):
    cfg = cfg_of(env)
    for bad in ("../x", "Qwen", "", "qwen\n", "ghost"):
        with pytest.raises(CapacityError):
            capacity.card_path(env, cfg, bad)
    assert capacity.card_path(env, cfg, "qwen") == env.parent / "capacity" / "qwen.json"


def test_write_card_from_config_only_keeps_measurements_null(env):
    card = write(env)
    for key in ("depth", "ladder", "interference", "ram", "quants", "benchmark"):
        assert card[key] is None, key
    assert card["usable"] == {"context_tokens": None, "max_output_tokens": None, "basis": "no current depth sweep"}
    assert card["config"]["n_cpu_moe"] is None
    bm = {"pp_tps": 1.5, "tg_tps": 2.5, "measured_at": "2026-10-30T00:00:00+00:00"}
    edit_config(env, benchmark={"vulkan": bm})
    assert write(env)["benchmark"] == bm


def test_write_card_maps_a_real_bench_fit_output(env):
    card = write(env, depth=BENCH_FIT_OUTPUT, sources={"depth": "bench.json"})
    depth = card["depth"]
    assert depth["points"] == [
        {"depth": 0, "pp_tps": 389.040026, "tg_tps": 37.498983},
        {"depth": 32768, "pp_tps": 257.290166, "tg_tps": 32.242981},
    ]
    assert depth["model_vram_mib"] == 8048
    assert depth["peak_temp_c"] == 41
    assert depth["avg_sclk_mhz"] == 1481
    assert depth["limits"] == {"cpus": None, "memory_mib": None}
    assert depth["source"] == "bench.json"
    assert card["usable"]["context_tokens"] == 33280
    assert card["usable"]["max_output_tokens"] == 8192


def test_write_card_merges_depth_and_computes_usable_context(env):
    card = write(env, depth=bench([(0, 900, 60), (32768, 700, 50), (130560, 300, 30)]))
    assert card["usable"]["context_tokens"] == 131072
    assert card["usable"]["max_output_tokens"] == 8192
    assert card["usable"]["basis"] == "verified to depth 130560 (interference: not measured)"


def test_points_without_tg_do_not_count(env):
    card = write(env, depth=bench([(0, 900, 60), (32768, 700, 50), (130560, 300, None)]))
    assert card["usable"]["context_tokens"] == 33280


def test_write_card_refuses_a_file_that_is_not_bench_fit_output(env):
    with pytest.raises(CapacityError, match=r"bench-fit\.sh"):
        write(env, depth={"points": [], "model_vram_mib": 1})
    old = {k: v for k, v in BENCH_FIT_OUTPUT.items() if k != "flags"}
    with pytest.raises(CapacityError, match="no flags"):
        write(env, depth=old)


def test_write_card_refuses_depth_measured_with_other_flags(env):
    other = copy.deepcopy(BENCH_FIT_OUTPUT)
    other["flags"]["ubatch_size"] = 1024
    with pytest.raises(CapacityError, match="ubatch_size"):
        write(env, depth=other)
    with pytest.raises(CapacityError, match="file"):
        write(env, depth=dict(BENCH_FIT_OUTPUT, file="other.gguf"))


def test_threads_are_not_compared(env):
    other = copy.deepcopy(BENCH_FIT_OUTPUT)
    other["flags"]["threads"] = 8
    write(env, depth=other)


def test_n_cpu_moe_zero_matches_a_bench_without_offload(env):
    edit_config(env, n_cpu_moe=0)
    card = write(env, depth=BENCH_FIT_OUTPUT)
    assert card["config"]["n_cpu_moe"] is None


def test_sections_go_stale_when_config_changes(env):
    write(env, depth=bench([(0, 900, 60), (130560, 300, 30)]))
    edit_config(env, ctx_size=262144)
    card = capacity.read_card(env, cfg_of(env), "qwen")
    assert card is not None
    assert card["stale_sections"] == ["depth"]
    assert card["usable"]["context_tokens"] is None
    assert card["usable"]["basis"] == "no current depth sweep"


def test_failed_interference_blocks_usable_context(env):
    write(env, depth=bench([(0, 900, 60), (130560, 300, 30)]))
    failed = dict(REPORT, passed=False, failures=["x"])
    card = write(env, interference=failed)
    assert card["usable"] == {"context_tokens": None, "max_output_tokens": None, "basis": "interference gate failed"}
    assert capacity.local_limits(env, cfg_of(env)) == {}
    assert card["ram"]["llm_server_peak_mib"] == 6000
    assert card["ram"]["llm_server_cap_mib"] == 12000
    assert card["ram"]["host_available_min_mib"] == 20000
    assert card["ram"]["swap_delta_mib"] == 0
    assert capacity.interference_passed(read(env)) is False
    note = "system_one latency not measured (System One disabled)"
    card = write(env, interference=dict(REPORT, notes=[note]))
    assert card["interference"]["notes"] == [note]
    assert card["usable"]["basis"] == "verified to depth 130560 (interference: passed)"
    assert capacity.interference_passed(read(env)) is True


def test_write_card_merges_score_ladder_and_tool_use(env):
    score = {
        "ladder": 3, "ladder_at": {"1": "low", "2": "low", "3": "high"}, "pass_rate": 0.667, "overall": 71.5,
        "tool_use": {"layers": [3, 4, 5], "calls": 10, "errors": 3, "error_rate": 0.3}, "invalid_runs": 0,
    }
    ladder = write(env, score=score)["ladder"]
    assert ladder["highest_layer"] == 3
    assert ladder["lowest_effort"] == {"1": "low", "2": "low", "3": "high"}
    assert ladder["pass_rate"] == 0.667
    assert ladder["overall_score"] == 71.5
    assert ladder["tool_calls"] == 10
    assert ladder["tool_errors"] == 3
    assert ladder["tool_error_rate"] == 0.3
    assert ladder["invalid_runs"] == 0


def test_sections_not_given_are_kept(env):
    write(env, depth=BENCH_FIT_OUTPUT)
    card = write(env, interference=REPORT)
    assert card["depth"] is not None
    assert card["interference"]["passed"] is True


def test_card_keys_match_the_schema(env):
    write(env, depth=BENCH_FIT_OUTPUT, interference=REPORT)
    card = json.loads(capacity.card_path(env, cfg_of(env), "qwen").read_text())
    assert set(card) == set(SCHEMA["properties"]) - {"stale_sections"}
    assert set(SCHEMA["required"]) <= set(card)
    assert card["quants"] is None


def test_card_file_and_directory_are_private(env):
    write(env)
    path = capacity.card_path(env, cfg_of(env), "qwen")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert [p.name for p in path.parent.iterdir()] == ["qwen.json"]


def test_current_usable_without_a_card(env):
    assert capacity.current_usable(env, cfg_of(env), "qwen") == {
        "context_tokens": None, "max_output_tokens": None, "basis": "no capacity card",
    }


def test_local_limits_use_only_current_verified_cards(env):
    assert capacity.local_limits(env, cfg_of(env)) == {}
    write(env, depth=BENCH_FIT_OUTPUT)
    assert capacity.local_limits(env, cfg_of(env)) == {"qwen": (33280, 8192)}
    edit_config(env, ctx_size=262144)
    assert capacity.local_limits(env, cfg_of(env)) == {}
    edit_config(env, ctx_size=131072)
    data = yaml.safe_load(env.read_text())
    data["llm_server"]["enabled"] = False
    env.write_text(yaml.safe_dump(data))
    assert capacity.local_limits(env, cfg_of(env)) == {}


def test_write_card_returns_the_read_view_and_interference_passed_is_staleness_safe(env):
    card = write(env, depth=bench([(0, 900, 60), (130560, 300, 30)]), interference=REPORT)
    assert card["stale_sections"] == []
    assert capacity.interference_passed(card) is True
    assert "stale_sections" not in json.loads(capacity.card_path(env, cfg_of(env), "qwen").read_text())
    edit_config(env, ctx_size=262144)
    stale = write(env, score=SCORE)
    assert stale["stale_sections"] == ["depth", "interference", "ram"]
    assert capacity.interference_passed(stale) is None
    assert capacity.interference_passed(read(env)) is None
    raw = json.loads(capacity.card_path(env, cfg_of(env), "qwen").read_text())
    assert capacity.interference_passed(raw) is None  # not checked against the config: not current


def test_usable_follows_the_current_config_after_a_revert(env):
    write(env, depth=bench([(0, 900, 60), (130560, 300, 30)]))
    edit_config(env, ctx_size=65536, client_max_output_tokens=1000)
    write(env, score=SCORE)  # the stored config is now B and the depth section is stale
    assert read(env)["usable"]["context_tokens"] is None
    edit_config(env, ctx_size=131072, client_max_output_tokens=8192)
    usable = read(env)["usable"]
    assert (usable["context_tokens"], usable["max_output_tokens"]) == (131072, 8192)


SCORE = {
    "ladder": 3, "ladder_at": {"1": "low"}, "pass_rate": 0.5, "overall": 50.0,
    "tool_use": {"calls": 1, "errors": 0, "error_rate": 0.0}, "invalid_runs": 0,
}


def test_interference_input_must_be_a_gate_report(env):
    for bad in (BENCH_FIT_OUTPUT, dict(REPORT, passed="yes"), {}):
        with pytest.raises(CapacityError, match="interference"):
            write(env, interference=bad, sources={"interference": "x.json"})
    assert capacity.read_card(env, cfg_of(env), "qwen") is None


def test_score_input_must_be_a_score_output(env):
    with pytest.raises(CapacityError, match=r"score\.sh"):
        write(env, score=BENCH_FIT_OUTPUT)
    with pytest.raises(CapacityError, match="tool_use"):
        write(env, score={k: v for k, v in SCORE.items() if k != "tool_use"})


def test_depth_point_without_depth_is_a_capacity_error(env):
    with pytest.raises(CapacityError, match="depth"):
        write(env, depth=dict(BENCH_FIT_OUTPUT, tps=[{"tg_tps": 1.0}]))
    with pytest.raises(CapacityError, match="depth"):
        write(env, depth=dict(BENCH_FIT_OUTPUT, tps=["x"]))


@pytest.mark.parametrize("points", [[{"tg_tps": 1.0}], ["x"], 5])
def test_local_limits_skip_a_corrupt_card(env, points):
    write(env, depth=BENCH_FIT_OUTPUT)
    path = capacity.card_path(env, cfg_of(env), "qwen")
    card = json.loads(path.read_text())
    card["depth"]["points"] = points
    path.write_text(json.dumps(card))
    assert capacity.local_limits(env, cfg_of(env)) == {}
    path.write_bytes(b"\xff\xfe")
    assert capacity.local_limits(env, cfg_of(env)) == {}


def test_model_capacity_script_is_executable():
    root = Path(__file__).resolve().parents[1]
    assert (root / "scripts" / "model-capacity.sh").stat().st_mode & stat.S_IXUSR
