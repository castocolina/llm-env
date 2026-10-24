import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.resources import (
    DEFAULT_POLICY,
    HOST_CPU_FLOOR,
    HOST_MEMORY_FLOOR_MIB,
    OMNIROUTE_CPU_MAX,
    OMNIROUTE_CPU_MIN,
    OMNIROUTE_MEMORY_MAX_MIB,
    OMNIROUTE_MEMORY_MIN_MIB,
    OVERRIDABLE_SERVICES,
    PHOENIX_CPUS_MAX,
    PHOENIX_CPUS_MIN,
    PHOENIX_MEMORY_MAX_MIB,
    PHOENIX_MEMORY_MIN_MIB,
    POLICY_KEYS,
    REMOTE_SETUP_CPU_MAX,
    REMOTE_SETUP_CPU_MIN,
    REMOTE_SETUP_MEMORY_MAX_MIB,
    REMOTE_SETUP_MEMORY_MIN_MIB,
    SYSTEM_ONE_CPUS,
    SYSTEM_ONE_CPUS_MIN,
    SYSTEM_ONE_MEMORY_MIB,
    SYSTEM_ONE_MEMORY_MIN_MIB,
    ResourceError,
    compute_resource_limits,
    effective_policy,
    format_live,
    live_resources,
    live_verdict,
    lowered_cap_warnings,
    read_live,
    summarize_policy,
    validate_overrides,
)

# The fixed floor consumed by OmniRoute + remote-setup's MIN reservations
# (what compute_resource_limits actually subtracts from the host before
# handing the remainder to llm_server) -- their MAX values are ceilings
# only, not pre-reserved from anything.
CPU_FLOOR = HOST_CPU_FLOOR + OMNIROUTE_CPU_MIN + REMOTE_SETUP_CPU_MIN
MEMORY_FLOOR_MIB = HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_MIN_MIB + REMOTE_SETUP_MEMORY_MIN_MIB


def _omniroute_expected():
    return {
        "cpus_min": OMNIROUTE_CPU_MIN,
        "cpus_max": OMNIROUTE_CPU_MAX,
        "memory_min_mib": OMNIROUTE_MEMORY_MIN_MIB,
        "memory_max_mib": OMNIROUTE_MEMORY_MAX_MIB,
    }


def _remote_setup_expected():
    return {
        "cpus_min": REMOTE_SETUP_CPU_MIN,
        "cpus_max": REMOTE_SETUP_CPU_MAX,
        "memory_min_mib": REMOTE_SETUP_MEMORY_MIN_MIB,
        "memory_max_mib": REMOTE_SETUP_MEMORY_MAX_MIB,
    }


def test_llm_server_gets_remainder_after_host_floor_and_omniroute():
    result = compute_resource_limits(host_cpu_count=8, host_memory_total_mib=32768)
    assert result["host_cpu_floor"] == HOST_CPU_FLOOR
    assert result["host_memory_floor_mib"] == HOST_MEMORY_FLOOR_MIB
    assert result["omniroute"] == _omniroute_expected()
    assert result["remote_setup"] == _remote_setup_expected()
    assert result["llm_server"]["cpus"] == 8 - CPU_FLOOR
    assert result["llm_server"]["memory_mib"] == 32768 - MEMORY_FLOOR_MIB


def test_insufficient_cpu_raises():
    with pytest.raises(ResourceError):
        compute_resource_limits(
            host_cpu_count=CPU_FLOOR,
            host_memory_total_mib=32768,
        )


def test_insufficient_memory_raises():
    with pytest.raises(ResourceError):
        compute_resource_limits(
            host_cpu_count=8,
            host_memory_total_mib=MEMORY_FLOOR_MIB,
        )


def test_exact_floor_plus_one_is_feasible():
    result = compute_resource_limits(
        host_cpu_count=CPU_FLOOR + 1,
        host_memory_total_mib=MEMORY_FLOOR_MIB + 1,
    )
    assert result["llm_server"]["cpus"] == 1
    assert result["llm_server"]["memory_mib"] == 1


def test_memory_ceiling_caps_llm_server_below_the_uncapped_remainder():
    uncapped = compute_resource_limits(host_cpu_count=8, host_memory_total_mib=32768)
    capped = compute_resource_limits(
        host_cpu_count=8, host_memory_total_mib=32768, memory_ceiling_pct=46
    )
    assert capped["llm_server"]["memory_mib"] < uncapped["llm_server"]["memory_mib"]
    assert capped["llm_server"]["memory_mib"] == round(32768 * 46 / 100)


def test_memory_ceiling_above_the_remainder_is_a_no_op():
    result = compute_resource_limits(
        host_cpu_count=8, host_memory_total_mib=32768, memory_ceiling_pct=100
    )
    assert result["llm_server"]["memory_mib"] == 32768 - MEMORY_FLOOR_MIB


def test_memory_ceiling_does_not_affect_the_insufficient_memory_floor_check():
    with pytest.raises(ResourceError):
        compute_resource_limits(
            host_cpu_count=8,
            host_memory_total_mib=MEMORY_FLOOR_MIB,
            memory_ceiling_pct=1,
        )


def test_memory_ceiling_floor_defaults_to_20_pct_when_unspecified():
    """Code-level safety net only — the real, user-facing floor lives in
    models.yml via migrate_config's 30% default (see the config tests
    below); this proves compute_resource_limits() itself never lets an
    unspecified floor disappear, even if a caller forgets to thread the
    config value through."""
    result = compute_resource_limits(
        host_cpu_count=8, host_memory_total_mib=32768, memory_ceiling_pct=0.001
    )
    assert result["llm_server"]["memory_mib"] == round(32768 * 20 / 100)


def test_memory_ceiling_floors_at_the_configured_value_instead_of_rounding_to_zero():
    """A tiny-but-valid pct (validate_config only requires 0 < pct <= 100)
    must not round down to a near-zero cap — pylib/compose.py omits
    `mem_limit` entirely when memory_mib is falsy, which would silently
    uncap the container. Unfloored, 32768 * 0.001 / 100 rounds to 0;
    floored at the configured 30%, the ceiling becomes exactly
    round(32768 * 30 / 100), not some other small percentage-only value.
    A percentage-of-total floor can never exceed the total, unlike the old
    fixed-MiB floor which could on a small enough host."""
    result = compute_resource_limits(
        host_cpu_count=8,
        host_memory_total_mib=32768,
        memory_ceiling_pct=0.001,
        memory_ceiling_floor_pct=30,
    )
    assert result["llm_server"]["memory_mib"] == round(32768 * 30 / 100)


def test_cpu_ceiling_caps_llm_server_below_the_uncapped_remainder():
    uncapped = compute_resource_limits(host_cpu_count=24, host_memory_total_mib=32768)
    capped = compute_resource_limits(
        host_cpu_count=24, host_memory_total_mib=32768, cpu_ceiling_pct=60
    )
    assert capped["llm_server"]["cpus"] < uncapped["llm_server"]["cpus"]
    assert capped["llm_server"]["cpus"] == 14  # round(24 * 0.60) == 14.4 -> 14


def test_cpu_ceiling_above_the_remainder_is_a_no_op():
    result = compute_resource_limits(
        host_cpu_count=8, host_memory_total_mib=32768, cpu_ceiling_pct=100
    )
    assert result["llm_server"]["cpus"] == 8 - CPU_FLOOR


def test_cpu_ceiling_floor_defaults_to_20_pct_when_unspecified():
    result = compute_resource_limits(
        host_cpu_count=24, host_memory_total_mib=32768, cpu_ceiling_pct=0.001
    )
    assert result["llm_server"]["cpus"] == round(24 * 20 / 100)


def test_cpu_ceiling_floors_at_the_configured_value_instead_of_rounding_to_zero():
    result = compute_resource_limits(
        host_cpu_count=24,
        host_memory_total_mib=32768,
        cpu_ceiling_pct=0.001,
        cpu_ceiling_floor_pct=60,
    )
    assert result["llm_server"]["cpus"] == round(24 * 60 / 100)


def test_cpu_ceiling_never_rounds_down_to_zero_on_a_small_host():
    """Unlike the memory ceiling (MiB values are always large enough that a
    valid 0-100% pct can never round to 0), CPU core counts are small whole
    numbers -- on a host with only 1 usable core after the fixed floor, both
    a tiny cpu_ceiling_pct AND a tiny cpu_ceiling_floor_pct can independently
    round to 0 (round(4 * 1 / 100) == 0), which compose.py then treats as
    "no cpus: limit at all" (fully uncapped), not "cap at 0 cores"."""
    result = compute_resource_limits(
        host_cpu_count=CPU_FLOOR + 1,  # exactly 1 usable core after the fixed floor
        host_memory_total_mib=32768,
        cpu_ceiling_pct=1,
        cpu_ceiling_floor_pct=1,
    )
    assert result["llm_server"]["cpus"] == 1


@pytest.mark.parametrize("cpu_ceiling_pct", [1, 5, 10, 19])
def test_cpu_ceiling_never_rounds_down_to_zero_across_tiny_percentages(cpu_ceiling_pct):
    result = compute_resource_limits(
        host_cpu_count=6,
        host_memory_total_mib=32768,
        cpu_ceiling_pct=cpu_ceiling_pct,
        cpu_ceiling_floor_pct=1,
    )
    assert result["llm_server"]["cpus"] >= 1


def test_llm_server_limits_are_zeroed_when_disabled():
    result = compute_resource_limits(
        host_cpu_count=8, host_memory_total_mib=32768, llm_server_enabled=False
    )
    assert result["llm_server"] == {"cpus": 0, "memory_mib": 0}
    assert result["omniroute"] == _omniroute_expected()
    assert result["remote_setup"] == _remote_setup_expected()


def test_disabled_llm_server_only_requires_the_host_and_omniroute_floor():
    """Exactly CPU_FLOOR CPUs (no spare core for llm-server) must be
    feasible when llm_server_enabled=False -- today's enabled-path floor
    check raises at this exact count."""
    result = compute_resource_limits(
        host_cpu_count=CPU_FLOOR,
        host_memory_total_mib=MEMORY_FLOOR_MIB,
        llm_server_enabled=False,
    )
    assert result["llm_server"] == {"cpus": 0, "memory_mib": 0}


def test_disabled_llm_server_still_raises_below_the_host_and_omniroute_floor():
    with pytest.raises(ResourceError):
        compute_resource_limits(
            host_cpu_count=CPU_FLOOR - 1,
            host_memory_total_mib=32768,
            llm_server_enabled=False,
        )
    with pytest.raises(ResourceError):
        compute_resource_limits(
            host_cpu_count=8,
            host_memory_total_mib=MEMORY_FLOOR_MIB - 1,
            llm_server_enabled=False,
        )


def test_llm_server_enabled_defaults_to_true():
    enabled_default = compute_resource_limits(host_cpu_count=8, host_memory_total_mib=32768)
    enabled_explicit = compute_resource_limits(
        host_cpu_count=8, host_memory_total_mib=32768, llm_server_enabled=True
    )
    assert enabled_default == enabled_explicit


def test_system_one_reservation_shrinks_llm_server_remainder():
    base = compute_resource_limits(16, 32768)
    with_one = compute_resource_limits(16, 32768, system_one_enabled=True)
    memory_floor_mib = (
        HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_MIN_MIB + REMOTE_SETUP_MEMORY_MIN_MIB
    )
    # Caps stay on the service; the host floor subtracts the reservation, same as OmniRoute's min.
    assert with_one["system_one"] == DEFAULT_POLICY["system_one"]
    assert with_one["llm_server"]["memory_mib"] == base["llm_server"]["memory_mib"] - SYSTEM_ONE_MEMORY_MIN_MIB - PHOENIX_MEMORY_MIN_MIB
    assert with_one["llm_server"]["cpus"] == base["llm_server"]["cpus"] - SYSTEM_ONE_CPUS_MIN - PHOENIX_CPUS_MIN
    assert with_one["llm_server"]["memory_mib"] <= 32768 - memory_floor_mib - 2560


def test_system_one_host_floor_uses_the_reservation_not_the_cap():
    cpu = HOST_CPU_FLOOR + OMNIROUTE_CPU_MIN + REMOTE_SETUP_CPU_MIN + 1
    memory = (
        HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_MIN_MIB + REMOTE_SETUP_MEMORY_MIN_MIB
        + SYSTEM_ONE_MEMORY_MIN_MIB + PHOENIX_MEMORY_MIN_MIB
    )
    compute_resource_limits(cpu + 1, memory + 1, system_one_enabled=True)
    with pytest.raises(ResourceError, match=r"System One's budgeted minimum \(1 CPU-share unit, 2560 MiB\)"):
        compute_resource_limits(cpu, memory + 1, system_one_enabled=True)


def test_system_one_reservation_absent_when_disabled():
    assert compute_resource_limits(16, 32768)["system_one"] == dict.fromkeys(POLICY_KEYS, 0)


def test_system_one_resource_error_names_its_reservation():
    message = r"System One's budgeted minimum \(1 CPU-share unit, 2560 MiB\)"
    cpu_floor = HOST_CPU_FLOOR + OMNIROUTE_CPU_MIN + REMOTE_SETUP_CPU_MIN + 1
    memory_floor = (
        HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_MIN_MIB + REMOTE_SETUP_MEMORY_MIN_MIB + 2560
    )
    # (cpus, memory_mib, llm_server_enabled) — one call per ResourceError branch
    for cpus, memory, llm in (
        (cpu_floor, 32768, True),
        (16, memory_floor, True),
        (cpu_floor - 1, 32768, False),
        (16, memory_floor - 1, False),
    ):
        with pytest.raises(ResourceError, match=message):
            compute_resource_limits(cpus, memory, system_one_enabled=True, llm_server_enabled=llm)


def test_default_policy_matches_the_module_constants():
    assert DEFAULT_POLICY["omniroute"] == _omniroute_expected()
    assert DEFAULT_POLICY["remote_setup"] == _remote_setup_expected()
    assert DEFAULT_POLICY["system_one"] == {
        "cpus_min": SYSTEM_ONE_CPUS_MIN,
        "cpus_max": SYSTEM_ONE_CPUS,
        "memory_min_mib": SYSTEM_ONE_MEMORY_MIN_MIB,
        "memory_max_mib": SYSTEM_ONE_MEMORY_MIB,
    }
    assert DEFAULT_POLICY["phoenix"]["cpus_max"] == PHOENIX_CPUS_MAX
    assert DEFAULT_POLICY["phoenix"]["memory_max_mib"] == PHOENIX_MEMORY_MAX_MIB
    assert set(OVERRIDABLE_SERVICES) == set(DEFAULT_POLICY)
    assert all(set(policy) == set(POLICY_KEYS) for policy in DEFAULT_POLICY.values())


def test_shipped_defaults_are_the_benchmark_derived_policy():
    # docs/superpowers/research/2026-10-24-benchmark-derived-sizing.md. Update deliberately with the doc.
    assert DEFAULT_POLICY == {
        "omniroute": {"cpus_min": 1, "cpus_max": 2, "memory_min_mib": 768, "memory_max_mib": 2048},
        "remote_setup": {"cpus_min": 0.25, "cpus_max": 0.5, "memory_min_mib": 32, "memory_max_mib": 64},
        # Explicit exception to the sizing rule (peak clipped by its own cap); see pylib/resources.py.
        "system_one": {"cpus_min": 1, "cpus_max": 2, "memory_min_mib": 2560, "memory_max_mib": 5120},
        # Live peak 804 MiB 2 h after a restart: 804 * 1.2 = 965 -> 1024.
        "phoenix": {"cpus_min": 0.25, "cpus_max": 1, "memory_min_mib": 192, "memory_max_mib": 1024},
    }


def test_baseline_hard_memory_caps_sum_to_8256_mib():
    assert sum(p["memory_max_mib"] for p in DEFAULT_POLICY.values()) == 2048 + 64 + 5120 + 1024 == 8256


def test_effective_policy_without_overrides_is_the_default_and_a_copy():
    policy = effective_policy("omniroute", None)
    assert policy == DEFAULT_POLICY["omniroute"]
    policy["memory_max_mib"] = 1
    assert DEFAULT_POLICY["omniroute"]["memory_max_mib"] == OMNIROUTE_MEMORY_MAX_MIB


def test_effective_policy_merges_only_the_overridden_keys():
    policy = effective_policy("omniroute", {"omniroute": {"memory_max_mib": 4096}})
    assert policy == {**DEFAULT_POLICY["omniroute"], "memory_max_mib": 4096}
    other = effective_policy("remote_setup", {"omniroute": {"memory_max_mib": 4096}})
    assert other == DEFAULT_POLICY["remote_setup"]


def test_valid_overrides_produce_no_errors():
    assert validate_overrides(None) == []
    assert validate_overrides({}) == []
    assert validate_overrides({"omniroute": {"memory_max_mib": 4096, "cpus_max": 2.5}}) == []
    # Lowering a cap below the default is allowed (it is warned about, not refused).
    assert validate_overrides({"omniroute": {"memory_max_mib": 1536}}) == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ([], "resources.overrides must be a mapping"),
        ({"llm_server": {"memory_max_mib": 1}}, "resources.overrides.llm_server is not an overridable service"),
        ({"omniroute": []}, "resources.overrides.omniroute must be a mapping"),
        ({"omniroute": {"memory_max": 2048}}, "resources.overrides.omniroute.memory_max is not an overridable field"),
        ({"omniroute": {"memory_max_mib": 0}}, "resources.overrides.omniroute.memory_max_mib must be a positive integer"),
        ({"omniroute": {"memory_max_mib": -1}}, "resources.overrides.omniroute.memory_max_mib must be a positive integer"),
        ({"omniroute": {"memory_max_mib": True}}, "resources.overrides.omniroute.memory_max_mib must be a positive integer"),
        ({"omniroute": {"memory_max_mib": "2048"}}, "resources.overrides.omniroute.memory_max_mib must be a positive integer"),
        ({"omniroute": {"memory_max_mib": 2048.5}}, "resources.overrides.omniroute.memory_max_mib must be a positive integer"),
        ({"omniroute": {"cpus_max": 0}}, "resources.overrides.omniroute.cpus_max must be a positive number"),
        ({"omniroute": {"cpus_max": float("inf")}}, "resources.overrides.omniroute.cpus_max must be a positive number"),
        ({"omniroute": {"memory_max_mib": 512}}, "effective memory_min_mib (768) must not exceed effective memory_max_mib (512)"),
        ({"omniroute": {"cpus_min": 8}}, "effective cpus_min (8) must not exceed effective cpus_max (2)"),
        # The retired evidence-gate reference is no longer a field (migrate_config drops it on load).
        ({"omniroute": {"memory_max_evidence": {}}}, "memory_max_evidence is not an overridable field"),
    ],
)
def test_invalid_overrides_are_reported(overrides, fragment):
    errors = validate_overrides(overrides)
    assert any(fragment in error for error in errors), errors


def test_overrides_apply_and_are_reported():
    result = compute_resource_limits(
        16, 32768, llm_server_enabled=False,
        overrides={"omniroute": {"memory_max_mib": 4096}},
    )
    assert result["omniroute"]["memory_max_mib"] == 4096
    assert result["omniroute"]["memory_min_mib"] == OMNIROUTE_MEMORY_MIN_MIB
    assert result["remote_setup"] == _remote_setup_expected()
    assert result["overrides_applied"] == {"omniroute": ["memory_max_mib"]}


def test_override_of_a_disabled_service_is_inert():
    base = compute_resource_limits(16, 32768)
    result = compute_resource_limits(
        16, 32768, overrides={"system_one": {"memory_max_mib": 5120, "memory_min_mib": 3000}}
    )
    assert result["system_one"] == dict.fromkeys(POLICY_KEYS, 0)
    assert result["phoenix"] == dict.fromkeys(POLICY_KEYS, 0)
    assert result["overrides_applied"] == {}
    assert result["totals"] == base["totals"]
    assert result["llm_server"] == base["llm_server"]


def test_raised_minimum_shrinks_the_llm_server_remainder():
    base = compute_resource_limits(16, 32768, memory_ceiling_pct=100)
    raised = compute_resource_limits(
        16, 32768, memory_ceiling_pct=100,
        overrides={"omniroute": {"memory_min_mib": 2048}},
    )
    assert raised["llm_server"]["memory_mib"] == (
        base["llm_server"]["memory_mib"] - (2048 - OMNIROUTE_MEMORY_MIN_MIB)
    )


def test_raised_minimum_on_a_too_small_host_raises_resource_error():
    floor = HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_MAX_MIB + REMOTE_SETUP_MEMORY_MIN_MIB
    with pytest.raises(ResourceError, match=f"at least {floor} MiB"):
        compute_resource_limits(
            16, floor - 1, llm_server_enabled=False,
            overrides={"omniroute": {"memory_min_mib": OMNIROUTE_MEMORY_MAX_MIB}},
        )


def test_invalid_overrides_raise_resource_error():
    with pytest.raises(ResourceError, match=r"resources\.overrides\.omniroute\.memory_max_mib"):
        compute_resource_limits(16, 32768, overrides={"omniroute": {"memory_max_mib": 0}})


def test_totals_report_hard_caps_and_soft_reservations():
    result = compute_resource_limits(
        16, 65536, llm_server_enabled=False, system_one_enabled=True
    )
    assert result["totals"]["hard_memory_cap_mib"] == 8256
    assert result["totals"]["soft_memory_reservation_mib"] == (
        OMNIROUTE_MEMORY_MIN_MIB + REMOTE_SETUP_MEMORY_MIN_MIB
        + SYSTEM_ONE_MEMORY_MIN_MIB + PHOENIX_MEMORY_MIN_MIB
    )


def test_totals_include_the_llm_server_cap_when_enabled():
    result = compute_resource_limits(16, 32768)
    assert result["totals"]["hard_memory_cap_mib"] == (
        result["llm_server"]["memory_mib"]
        + OMNIROUTE_MEMORY_MAX_MIB + REMOTE_SETUP_MEMORY_MAX_MIB
    )


def test_summarize_policy_names_totals_and_overrides():
    plain = summarize_policy(compute_resource_limits(16, 32768, llm_server_enabled=False))
    assert len(plain) == 1
    assert "hard memory caps total" in plain[0] and "not allocated RAM" in plain[0]
    overridden = summarize_policy(
        compute_resource_limits(
            16, 32768, llm_server_enabled=False,
            overrides={"omniroute": {"memory_max_mib": 4096}},
        )
    )
    assert overridden[1] == "resource overrides applied: omniroute=memory_max_mib"


def test_a_cap_below_the_default_is_warned_about_with_the_benchmark_target():
    lowered = compute_resource_limits(
        16, 32768, llm_server_enabled=False, overrides={"omniroute": {"memory_max_mib": 1536}}
    )
    warnings = [line for line in summarize_policy(lowered) if line.startswith("WARNING")]
    assert len(warnings) == 1
    assert "omniroute memory_max_mib 1536 MiB" in warnings[0] and "default 2048 MiB" in warnings[0]
    assert "make resources-benchmark" in warnings[0]
    raised = compute_resource_limits(
        16, 32768, llm_server_enabled=False, overrides={"omniroute": {"memory_max_mib": 4096}}
    )
    assert not any(line.startswith("WARNING") for line in summarize_policy(raised))


def test_lowered_cap_warnings_skip_disabled_services():
    # A disabled service carries an all-zero policy: nothing to warn about.
    assert lowered_cap_warnings({"system_one": dict.fromkeys(POLICY_KEYS, 0)}) == []
    assert len(lowered_cap_warnings({"phoenix": {**DEFAULT_POLICY["phoenix"], "memory_max_mib": 512}})) == 1


# --- live cgroup counters -------------------------------------------------

MIB = 1024 * 1024


def _cgroup(path, *, current=100, peak=200, cap=1000, events="high 0\nmax 0\noom 0\noom_kill 0",
            psi_some=0.0, psi_full=0.0, cap_text=None, usage_usec=10_000_000):
    path.mkdir(parents=True, exist_ok=True)
    files = {
        "memory.current": f"{current * MIB}", "memory.peak": f"{peak * MIB}",
        "memory.max": cap_text or f"{cap * MIB}", "memory.swap.current": f"{3 * MIB}",
        "memory.swap.peak": f"{5 * MIB}", "memory.events": events,
        "memory.pressure": f"some avg10=0.00 avg60=0.00 avg300={psi_some:.2f} total=1\n"
                           f"full avg10=0.00 avg60=0.00 avg300={psi_full:.2f} total=1",
        "cpu.stat": f"usage_usec {usage_usec}\nuser_usec 1",
    }
    for name, text in files.items():
        (path / name).write_text(text + "\n")
    return path


def _live(cgroup, cap_mib=1000):
    return read_live(cgroup, 0, cap_mib, now=100)


def test_live_ok(tmp_path):
    row = _live(_cgroup(tmp_path / "c"))
    assert row["verdict"] == "OK"
    assert (row["current_mib"], row["peak_mib"], row["cap_mib"]) == (100, 200, 1000)
    assert row["peak_over_cap"] == pytest.approx(0.2)
    assert (row["swap_mib"], row["swap_peak_mib"]) == (3, 5)
    assert row["cpu_avg_cores"] == pytest.approx(0.1)  # 10 s of CPU over 100 s
    assert row["events"] == {"high": 0, "max": 0, "oom": 0, "oom_kill": 0}


@pytest.mark.parametrize("kwargs", [
    {"peak": 850}, {"events": "high 0\nmax 2\noom 0\noom_kill 0"}, {"psi_some": 10.0},
])
def test_live_watch(tmp_path, kwargs):
    assert _live(_cgroup(tmp_path / "c", **kwargs))["verdict"] == "WATCH"


@pytest.mark.parametrize("kwargs", [
    {"peak": 920}, {"events": "high 0\nmax 0\noom 1\noom_kill 1"}, {"psi_full": 5.0},
])
def test_live_alert(tmp_path, kwargs):
    assert _live(_cgroup(tmp_path / "c", **kwargs))["verdict"] == "ALERT"


def test_live_pending_restart_when_cap_differs(tmp_path):
    row = _live(_cgroup(tmp_path / "c", cap=2048), cap_mib=1000)
    assert row["restart_pending"] and row["verdict"] == "WATCH"
    assert "restart pending" in "\n".join(format_live({"omniroute": {"state": "running", **row}})[0])


def test_live_missing_files_are_none_not_a_crash(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    row = _live(empty)
    assert row["current_mib"] is None and row["cpu_avg_cores"] is None and row["verdict"] == "OK"
    assert "n/a" in "\n".join(line for table in format_live({"omniroute": {"state": "running", **row}}) for line in table)


def test_live_unlimited_cap_has_no_ratios(tmp_path):
    row = _live(_cgroup(tmp_path / "c", cap_text="max"))
    assert row["cap_mib"] is None and row["peak_over_cap"] is None and row["restart_pending"]


def test_live_resources_stopped_container_and_disabled_service(tmp_path):
    cg = _cgroup(tmp_path / "c")
    limits = {s: dict(p) for s, p in DEFAULT_POLICY.items()}
    limits["phoenix"] = dict.fromkeys(POLICY_KEYS, 0)  # disabled: omitted
    found = {"omniroute": (cg, 1, 0.0), "remote-setup": (None, 2, 0.0)}
    live = live_resources(limits, locate=lambda name: found.get(name), now=100)
    assert set(live) == {"omniroute", "remote_setup", "system_one"}
    assert live["omniroute"]["state"] == "running" and live["omniroute"]["cap_mib"] == 1000
    assert live["remote_setup"]["verdict"] == "n/a"  # running, cgroup not found
    assert live["system_one"] == {"state": "not running"}
    memory, activity = format_live(live)
    assert memory[0].startswith("service\t") and any("not running" in line for line in memory)
    assert activity[0].startswith("service\t") and "verdict" not in activity[0]
    assert {len(line.split("\t")) for line in memory} == {10} and {len(line.split("\t")) for line in activity} == {6}
    assert live_verdict({"events": {"oom_kill": None}}) == "OK"
