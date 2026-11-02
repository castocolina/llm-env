"""One-page plain-text account of a capacity card: what the model does, in numbers. Unknown values read `not measured`."""

from __future__ import annotations

NM = "not measured"
STATIC_UNMEASURED = (
    "answer quality at long context (no recall or needle test)",
    "several simultaneous requests (the server runs one slot; not tested)",
    "prompt-cache hit speed (the profile runs with the cache off)",
    "power draw",
    "thermals beyond the profiled run lengths",
)


def _n(value, unit: str = "", digits: int = 0) -> str:
    return NM if value is None else f"{value:,.{digits}f}{unit}"


def _section(card: dict, name: str) -> dict | None:
    section = card.get(name)
    return section if isinstance(section, dict) and name not in (card.get("stale_sections") or []) else None


def _peak(runs: list[dict], group: str, key: str):
    values = [r[group][key] for r in runs if (r.get(group) or {}).get(key) is not None]
    return max(values) if values else None


def _avg(runs: list[dict], group: str, key: str):
    """Mean of the per-run averages, weighted by each run's sample count."""
    pairs = [(r[group][key], r[group].get("samples") or 1) for r in runs if (r.get(group) or {}).get(key) is not None]
    return sum(v * w for v, w in pairs) / sum(w for _, w in pairs) if pairs else None


def _window(card: dict, config: dict, profile: dict | None, usable: dict) -> list[str]:
    total, cap = config["ctx_size"], config.get("client_max_output_tokens")
    lines = [
        f"- Total context window: {total:,} tokens. The input prompt and the generated output share this one window.",
        f"- Client output cap: {_n(cap)} tokens per reply." if cap else f"- Client output cap: {NM} (none set in models.yml).",
    ]
    if cap:
        lines.append(f"- So the largest prompt that still leaves a full reply is {total:,} - {cap:,} = {total - cap:,} tokens.")
    if usable.get("context_tokens") is None:
        lines.append(f"- Card verdict: not verified ({usable.get('basis')}).")
    else:
        lines.append(f"- Card verdict: usable context {usable['context_tokens']:,} tokens, usable reply {usable['max_output_tokens']:,} tokens ({usable['basis']}).")
    if profile is not None:
        n_ctx = profile.get("n_ctx")
        lines.append(f"- llama-server reports its own window as {_n(n_ctx)} tokens" + (" (matches)." if n_ctx == total else "."))
        cap_run = next((r for r in profile["runs"] if r.get("kind") == "cap"), None)
        if cap_run is None:
            lines.append(f"- Full-window request (prompt + full {_n(cap)}-token reply): {NM}.")
        elif cap_run.get("error"):
            lines.append(f"- Full-window request FAILED: {cap_run['error']}")
        else:
            used = cap_run["prompt_n"] + cap_run["predicted_n"]
            lines.append(f"- Full-window request: a {cap_run['prompt_n']:,}-token prompt plus {cap_run['predicted_n']:,} generated tokens "
                         f"= {used:,} of {total:,} tokens ({total - used:,} spare) completed without error.")
    return lines


def _speed(profile: dict | None, depth: dict | None, runner_cap: float) -> list[str]:
    lines = []
    if profile is None:
        lines.append(f"Real requests (latency profile): {NM}; run latency-profile.sh and make model-capacity PROFILE=<json>.")
    else:
        lines += [
            (f"Real requests, prompt cache off, {profile.get('output_tokens')} generated tokens unless noted "
            f"(measured {profile.get('measured_at')}):"),
            "  prompt tokens | time to first token | input tok/s | output tok/s | output tokens | total",
        ]
        for r in profile["runs"]:
            if r.get("error"):
                lines.append(f"  {r['label']:>13} | FAILED: {r['error']}")
                continue
            ttft = r.get("ttft_s")
            slow = f"  (first token after the {runner_cap:g} s runner cap)" if ttft is not None and ttft > runner_cap else ""
            lines.append(
                f"  {r['prompt_n']:>13,} | {_n(ttft, ' s', 1):>19} | {_n(r.get('input_tps')):>11} | "
                f"{_n(r.get('output_tps'), digits=1):>12} | {r['predicted_n']:>13,} | {_n(r.get('wall_s'), ' s', 1)}{slow}"
            )
        lines += [f"  skipped: {s['label']} ({s['reason']})" for s in profile.get("skipped") or []]
    if depth is None:
        lines.append(f"Depth sweep (llama-bench): {NM}.")
    else:
        lines += ["Depth sweep (llama-bench, speed with the prompt already that deep):", "  depth | input tok/s | output tok/s"]
        lines += [f"  {p['depth']:>6,} | {_n(p.get('pp_tps')):>11} | {_n(p.get('tg_tps'), digits=1):>12}" for p in depth["points"]]
    return lines


def _hardware(card: dict, profile: dict | None) -> list[str]:
    if profile is None or not [r for r in profile["runs"] if not r.get("error")]:
        return [f"GPU, CPU, RAM and swap during requests: {NM}."]
    runs, idle = [r for r in profile["runs"] if not r.get("error")], profile.get("idle") or {}
    total = ((card.get("interference") or {}).get("vram") or {}).get("total_mib")
    host_total = profile.get("host_mem_total_mib")
    avail = [r["host"]["mem_avail_min_mib"] for r in runs if r["host"].get("mem_avail_min_mib") is not None]
    lines = [
        f"VRAM, whole card: peak {_n(_peak(runs, 'gpu', 'peak_vram_mib'), ' MiB')}" + (f" of {total:,} MiB" if total else "")
        + f", average {_n(_avg(runs, 'gpu', 'avg_vram_mib'), ' MiB')}, idle with the model loaded {_n((idle.get('gpu') or {}).get('peak_vram_mib'), ' MiB')}.",
        (f"VRAM, model only (the llama-server process): peak {_n(_peak(runs, 'host', 'proc_vram_peak_mib'), ' MiB')}, "
        f"average {_n(_avg(runs, 'host', 'proc_vram_avg_mib'), ' MiB')}, idle {_n((idle.get('host') or {}).get('proc_vram_peak_mib'), ' MiB')}."),
        (f"GPU busy: peak {_n(_peak(runs, 'gpu', 'peak_busy_pct'), ' %')}, average {_n(_avg(runs, 'gpu', 'avg_busy_pct'), ' %')}. "
        f"Temperature peak {_n(_peak(runs, 'gpu', 'peak_temp_c'), ' C')}. "
        f"Clock peak {_n(_peak(runs, 'gpu', 'peak_sclk_mhz'), ' MHz')}, average {_n(_avg(runs, 'gpu', 'avg_sclk_mhz'), ' MHz')}."),
        (f"CPU: host peak {_n(_peak(runs, 'gpu', 'peak_cpu_pct'), ' %')}, average {_n(_avg(runs, 'gpu', 'avg_cpu_pct'), ' %')} (all cores, includes other host work); "
        f"llm-server container peak {_n(_peak(runs, 'host', 'cg_cpu_peak_cores'), ' cores', 2)}, average {_n(_avg(runs, 'host', 'cg_cpu_avg_cores'), ' cores', 2)}."),
        f"RAM: host available at its lowest {_n(min(avail) if avail else None, ' MiB')}"
        + (f" of {host_total:,} MiB" if host_total else "")
        + f"; llm-server memory.current peak {_n(_peak(runs, 'host', 'cg_mem_peak_mib'), ' MiB')} (includes file cache), anonymous {_n(_peak(runs, 'host', 'cg_anon_peak_mib'), ' MiB')}.",
        f"Swap: used at most {_n(_peak(runs, 'host', 'swap_used_max_mib'), ' MiB')} (idle {_n((idle.get('host') or {}).get('swap_used_max_mib'), ' MiB')}).",
    ]
    return lines


def _impact(inter: dict | None) -> list[str]:
    if inter is None:
        return [f"Impact on Laya (System One) and OmniRoute: {NM}; run make model-interference."]

    def pair(block: dict | None) -> str:
        if not block:
            return NM
        before, during = block.get("before") or {}, block.get("during") or {}
        return (f"p50 {_n(before.get('p50_ms'), ' ms', 1)} -> {_n(during.get('p50_ms'), ' ms', 1)}, "
                f"p95 {_n(before.get('p95_ms'), ' ms', 1)} -> {_n(during.get('p95_ms'), ' ms', 1)} (idle -> while the model is under load), "
                f"non-200 answers {_n(during.get('non200'))}")

    lines = [f"Gate: {'passed' if inter.get('passed') else 'FAILED'}" + ("" if inter.get("passed") else f" ({'; '.join(inter.get('failures') or [])})") + ".",
             f"Laya (System One): {pair(inter.get('system_one'))}.", f"OmniRoute health probe: {pair(inter.get('omniroute'))}."]
    if inter.get("regression_warning"):
        lines.append(f"Note: {inter['regression_warning']}.")
    vram = inter.get("vram") or {}
    if vram:
        lines.append(f"Whole-card VRAM during that load: peak {_n(vram.get('peak_used_mib'), ' MiB')} of {_n(vram.get('total_mib'), ' MiB')}, "
                     f"lowest headroom {_n(vram.get('headroom_min_mib'), ' MiB')}.")
    return lines


def _ladder(ladder: dict | None, runner_cap: float) -> list[str]:
    if ladder is None:
        return [f"Agent ladder: {NM}; run effort-run.sh and score.sh, then make model-capacity SCORE=<json>."]
    rate = ladder.get("tool_error_rate")
    lowest = ladder.get("lowest_effort") or {}
    efforts = ", ".join(f"layer {k}: {v or 'not passed'}" for k, v in sorted(lowest.items()))
    layers = ", ".join(sorted(lowest, key=int)) or NM
    highest = "none" if ladder.get("highest_layer") == 0 else _n(ladder.get("highest_layer"))
    return [
        (f"Highest layer passed: {highest} (layers run: {layers}); pass rate {_n(None if ladder.get('pass_rate') is None else ladder['pass_rate'] * 100, ' %', 1)}; "
        f"overall score {_n(ladder.get('overall_score'), digits=1)}; invalid runs {_n(ladder.get('invalid_runs'))}."),
        (f"Tool calls {_n(ladder.get('tool_calls'))}, tool errors {_n(ladder.get('tool_errors'))} "
        f"(tool-error rate {_n(None if rate is None else rate * 100, ' %', 1)})."),
        f"Lowest effort that passed, per layer: {efforts or NM}.",
        f"The runner stops every ladder run after {runner_cap:g} s (a prompt whose first token comes later than that cannot finish inside one run).",
    ]


def _unmeasured(card: dict, profile: dict | None) -> list[str]:
    items = [f"{label} ({why})" for label, section, why in (
        ("real-request speeds and host peaks", profile, "latency-profile.sh"), ("depth sweep", _section(card, "depth"), "make model-capacity DEPTH="),
        ("agent ladder", _section(card, "ladder"), "make model-capacity SCORE="), ("Laya/OmniRoute impact", _section(card, "interference"), "make model-interference"),
    ) if section is None]
    if profile is not None:
        runs = [r for r in profile["runs"] if not r.get("error")]
        items += [f"{label} (no reading)" for label, group, key in (
            ("GPU busy %", "gpu", "peak_busy_pct"), ("GPU temperature", "gpu", "peak_temp_c"), ("GPU clock", "gpu", "peak_sclk_mhz"),
            ("llm-server CPU cores", "host", "cg_cpu_peak_cores"), ("model-only VRAM", "host", "proc_vram_peak_mib"),
        ) if _peak(runs, group, key) is None]
    return [*items, *STATIC_UNMEASURED]


def render(card: dict, runner_cap_seconds: float) -> str:
    config, stale = card["config"], card.get("stale_sections") or []
    profile, depth = _section(card, "profile"), _section(card, "depth")
    weights = ((_section(card, "quants") or {}).get("current") or {})
    quant = f"{config.get('quantization') or NM}" + (f", weights {weights['weights_mib']:,} MiB at {weights['bpw']} bits/weight" if weights.get("weights_mib") and weights.get("bpw") else "")
    head = [
        f"# {card['alias']} ({quant})",
        (f"File {config['file']}; KV cache {config.get('cache_type_k')}/{config.get('cache_type_v')}, flash attention {config.get('flash_attn')}, "
        f"ubatch {config.get('ubatch_size')}, GPU layers {config.get('n_gpu_layers')}."),
        f"Card written {card['written_at']}, config fingerprint {card['fingerprint']}." + (f" Stale sections left out: {', '.join(stale)}." if stale else ""),
    ]
    blocks = [
        ("The context window, in numbers", _window(card, config, profile, card["usable"])),
        ("Speed", _speed(profile, depth, runner_cap_seconds)),
        ("Hardware load (peaks during real requests)", _hardware(card, profile)),
        ("Impact on Laya (System One) and OmniRoute", _impact(_section(card, "interference"))),
        ("Agent ladder", _ladder(_section(card, "ladder"), runner_cap_seconds)),
        ("Not measured", [f"- {item}" for item in _unmeasured(card, profile)]),
    ]
    return "\n".join([*head, *(line for title, body in blocks for line in ("", f"## {title}", *body))]) + "\n"
