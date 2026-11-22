"""Bounded llama.cpp router metrics collector.

Counter resets are passed through unchanged: llama.cpp restarts child processes on unload and
sleep wake, and Prometheus rate() handles those resets. A reachable router with no loaded model
is healthy and reports status only; model-scoped endpoints can autoload or wake a child. The
status family is one-hot; a separate model_state gauge uses stable display codes.
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request

SOURCE = 'llm_env_source_up{source="llama"}'
ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}\Z")
# Source name -> exported name and source TYPE from the pinned b12004 image.
FAMILIES = {
    "prompt_tokens_total": ("prompt_tokens_total", "counter"),
    "prompt_tokens_cached_total": ("prompt_tokens_cached_total", "counter"),
    "prompt_seconds_total": ("prompt_seconds_total", "counter"),
    "tokens_predicted_total": ("tokens_predicted_total", "counter"),
    "tokens_predicted_seconds_total": ("tokens_predicted_seconds_total", "counter"),
    "n_decode_total": ("n_decode_total", "counter"),
    "n_tokens_max": ("n_tokens_max", "gauge"),
    "spec_decode_num_draft_tokens_total": ("spec_decode_num_draft_tokens_total", "counter"),
    "spec_decode_num_accepted_tokens_total": ("spec_decode_num_accepted_tokens_total", "counter"),
    "spec_decode_num_drafts_total": ("spec_decode_num_drafts_total", "counter"),
    "prompt_tokens_seconds": ("prompt_tokens_seconds", "gauge"),
    "predicted_tokens_seconds": ("predicted_tokens_seconds", "gauge"),
    "requests_processing": ("requests_processing", "gauge"),
    "requests_deferred": ("requests_deferred", "gauge"),
    "n_busy_slots_per_decode": ("n_busy_slots_per_decode", "gauge"),
}
EMITTED_FAMILIES = {"llm_env_source_up", "llm_env_llama_dropped_models_total",
                    "llm_env_llama_unknown_families_total", "llm_env_llama_models_loaded",
                    "llm_env_llama_model_status", "llm_env_llama_model_state",
                    "llm_env_llama_slot_context_tokens",
                    "llm_env_llama_slot_processing", "llm_env_llama_dropped_samples_total",
                    "llm_env_container_cpu_avg_cores", "llm_env_container_memory_current_mib",
                    "llm_env_container_memory_peak_mib"} | {
                        "llm_env_llama_" + out for out, _ in FAMILIES.values()}


def _valid_alias(value: object) -> bool:
    return isinstance(value, str) and bool(ALIAS.fullmatch(value))


def _parse_metrics(alias: str, exposition: str) -> tuple[str, int, int]:
    types: dict[str, str] = {}
    values: dict[str, str] = {}
    unknown_names: set[str] = set()
    bad_families: set[str] = set()
    dropped = 0
    number_re = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
    for line in exposition.splitlines():
        if not line or line.startswith("# HELP "):
            continue
        typ = re.fullmatch(r"# TYPE llamacpp:([A-Za-z0-9_:]+) (counter|gauge|untyped)", line)
        if typ:
            family, kind = typ.groups()
            if family not in FAMILIES:
                unknown_names.add(family)
            elif family in types and types[family] != kind:
                bad_families.add(family)
            else:
                types[family] = kind
            continue
        if line.startswith("#"):
            continue
        match = re.fullmatch(r"llamacpp:([A-Za-z0-9_:]+)(\{[^}]*\})? ([^ ]+)", line)
        if not match:
            dropped += 1
            continue
        family, labels, value = match.groups()
        if family not in FAMILIES:
            unknown_names.add(family)
            continue
        if labels or not number_re.fullmatch(value):
            dropped += 1
            bad_families.add(family)
            continue
        try:
            if not math.isfinite(float(value)):
                raise ValueError
        except ValueError:
            dropped += 1
            bad_families.add(family)
            continue
        if family in values:
            dropped += 1
            bad_families.add(family)
        else:
            values[family] = value
    out = []
    for family, value in values.items():
        output_name, kind = FAMILIES[family]
        source_kind = "counter" if family == "n_tokens_max" else kind
        if family in bad_families or types.get(family) != source_kind:
            dropped += 1
            continue
        name = "llm_env_llama_" + output_name
        out += [f"# HELP {name} llama.cpp {family.replace('_', ' ')}.",
                f"# TYPE {name} {kind}", f'{name}{{model="{alias}"}} {value}']
    return "\n".join(out) + ("\n" if out else ""), len(unknown_names), dropped


def collect_llama(*, base_url: str, api_key: str, deadline_s: float = 3.0) -> str:
    """Read models first; only scrape metrics and slots for aliases positively marked loaded."""
    deadline = time.monotonic() + max(0.0, deadline_s)
    dropped_models = unknown_families = dropped_samples = 0

    def get(path: str) -> bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        request = urllib.request.Request(base_url.rstrip("/") + path,
                                         headers={"Authorization": f"Bearer {api_key}"}, method="GET")
        with urllib.request.urlopen(request, timeout=min(3.0, remaining)) as response:
            chunks = bytearray()
            response.fp.raw._sock.settimeout(min(3.0, max(0.001, deadline - time.monotonic())))
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                chunk = response.read1(min(65536, 2_000_001 - len(chunks)))
                if not chunk:
                    return bytes(chunks)
                chunks.extend(chunk)
                if len(chunks) > 2_000_000:
                    raise ValueError

    try:
        doc = json.loads(get("/models"))
        entries = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(entries, list) or not entries:
            raise ValueError
    except Exception:  # noqa: BLE001 -- network, auth and malformed data fail closed without leaking response/key
        return SOURCE + " 0\n"

    statuses: list[tuple[str, str]] = []
    loaded: list[str] = []
    seen_aliases: set[str] = set()
    dropped_model_count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            dropped_models += 1
            continue
        alias, status_obj = entry.get("id"), entry.get("status")
        status = status_obj.get("value") if isinstance(status_obj, dict) else None
        if not isinstance(alias, str) or not _valid_alias(alias):
            dropped_models += 1
            dropped_model_count += 1
            continue
        if alias in seen_aliases:
            dropped_models += 1
            continue
        seen_aliases.add(alias)
        state = status if isinstance(status, str) and status in {"loaded", "unloaded", "loading", "sleeping"} else "other"
        statuses.append((alias, state))
        if status == "loaded":
            loaded.append(alias)

    lines = [SOURCE + " 1",
             "# HELP llm_env_llama_model_status Current listed model status.",
             "# TYPE llm_env_llama_model_status gauge"]
    declared_metrics: set[str] = {"llm_env_llama_model_status", "llm_env_llama_model_state"}
    declared_metric_lines: set[tuple[str, str]] = set()
    state_values = {"unloaded": 0, "loading": 1, "sleeping": 2, "loaded": 3, "other": 4}
    lines += ["# HELP llm_env_llama_model_state Model state code for Grafana display.",
              "# TYPE llm_env_llama_model_state gauge"]
    lines += [f'llm_env_llama_model_status{{model="{a}",status="{s}"}} 1' for a, s in statuses]
    lines += [f'llm_env_llama_model_state{{model="{a}"}} {state_values[s]}' for a, s in statuses]
    lines += ["# HELP llm_env_llama_models_loaded Number of listed aliases currently loaded.",
              "# TYPE llm_env_llama_models_loaded gauge", f"llm_env_llama_models_loaded {len(loaded)}"]
    for alias in loaded:
        try:
            metrics_path = "/metrics?" + urllib.parse.urlencode({"model": alias})
            exposition = get(metrics_path).decode("utf-8", "strict")
            frag, unknown, dropped = _parse_metrics(alias, exposition)
            # Each parser result reports unknown names once per alias; deduplicate across this run.
            unknown_families = max(unknown_families, unknown)
            for metric_line in frag.splitlines():
                if metric_line.startswith(("# TYPE ", "# HELP ")):
                    metric_name = metric_line.split()[2]
                    declaration = (metric_line.split()[1], metric_name)
                    if declaration in declared_metric_lines:
                        continue
                    declared_metric_lines.add(declaration)
                lines.append(metric_line)
            dropped_samples += dropped
            slots_path = "/slots?" + urllib.parse.urlencode({"model": alias})
            slots = json.loads(get(slots_path))
            if isinstance(slots, list):
                seen_slots: set[str] = set()
                for index, slot in enumerate(slots):
                    if not isinstance(slot, dict):
                        continue
                    slot_id = str(slot.get("id", index))
                    if slot_id in seen_slots:
                        continue
                    seen_slots.add(slot_id)
                    ctx = slot.get("n_ctx")
                    if isinstance(ctx, (int, float)) and not isinstance(ctx, bool) and math.isfinite(ctx):
                        name = "llm_env_llama_slot_context_tokens"
                        if name not in declared_metrics:
                            lines += [f"# HELP {name} Loaded slot context size in tokens.", f"# TYPE {name} gauge"]
                            declared_metrics.add(name)
                        lines.append(f'{name}{{model="{alias}"}} {ctx}')
                    processing = slot.get("is_processing")
                    if isinstance(processing, bool):
                        name = "llm_env_llama_slot_processing"
                        if name not in declared_metrics:
                            lines += [f"# HELP {name} Whether a loaded slot is processing.", f"# TYPE {name} gauge"]
                            declared_metrics.add(name)
                        lines.append(f'{name}{{model="{alias}"}} {int(processing)}')
        except Exception:  # noqa: BLE001 -- outage fails closed without including error/body/key
            return SOURCE + " 0\n"
    if dropped_model_count and not statuses:
        return SOURCE + " 0\n"
    for name, description in (("dropped_models_total", "Invalid or duplicate model aliases dropped."),
                              ("dropped_samples_total", "Invalid, duplicate, or conflicting samples dropped."),
                              ("unknown_families_total", "Unknown llama.cpp families ignored.")):
        full = "llm_env_llama_" + name
        lines += [f"# HELP {full} {description}", f"# TYPE {full} counter"]
    lines += [f"llm_env_llama_dropped_models_total {dropped_models}",
              f"llm_env_llama_dropped_samples_total {dropped_samples}",
              f"llm_env_llama_unknown_families_total {unknown_families}"]
    # Malformed fragments are isolated again by the combined collector's existing fragment guard.
    return "\n".join(lines) + "\n"
