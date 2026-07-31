"""VRAM budget arithmetic.

Geometry and model sizes are derived from GGUF metadata. Runtime and spike
allowances are fixed constants defined here and documented in the design spec.
"""

from __future__ import annotations

import math
import re
from typing import Any

# Fixed allowance for each loaded model's runtime allocations.
RUNTIME_OVERHEAD_MIB = 512
# Fixed allowance for compositor, browser, and game VRAM spikes.
SPIKE_HEADROOM_MIB = 1024

MIB = 1024 * 1024
MIB_PER_GB = 1024

# Approximate bytes per element for KV cache storage types.
# q8_0 stores 32 int8 values plus one f16 scale => 34/32 bytes per element.
BYTES_PER_ELEMENT: dict[str, float] = {
    "f32": 4.0,
    "f16": 2.0,
    "bf16": 2.0,
    "q8_0": 34.0 / 32.0,
    "q5_1": 24.0 / 32.0,
    "q5_0": 22.0 / 32.0,
    "q4_1": 20.0 / 32.0,
    "q4_0": 18.0 / 32.0,
}

BUDGET_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(%|GB|MiB)\s*$", re.IGNORECASE)


class BudgetError(Exception):
    """Raised when a budget value or cache type cannot be interpreted."""


def parse_vram_budget(value: str, total_mib: int) -> int:
    match = BUDGET_RE.match(str(value))
    if not match:
        raise BudgetError(
            f"cannot parse vram_budget {value!r}; expected '55%', '7.5GB', or '512MiB'"
        )
    amount, unit = float(match.group(1)), match.group(2).lower()
    if unit == "%":
        return int(total_mib * amount / 100)
    if unit == "gb":
        return int(amount * MIB_PER_GB)
    return int(amount)


def pad256(value: int) -> int:
    return ((value + 255) // 256) * 256


def kv_cache_components_mib(
    geometry: dict[str, Any],
    ctx_size: int,
    ubatch_size: int,
    cache_type_k: str,
    cache_type_v: str,
) -> dict[str, int]:
    for name in (cache_type_k, cache_type_v):
        if name not in BYTES_PER_ELEMENT:
            raise BudgetError(
                f"unknown cache type {name!r}; known: {sorted(BYTES_PER_ELEMENT)}"
            )

    sliding_window = geometry.get("sliding_window")
    full_bytes = 0.0
    swa_bytes = 0.0
    for layer_geometry in geometry["layers"]:
        kind = layer_geometry["kind"]
        if kind == "recurrent":
            continue
        if kind == "full":
            cells = ctx_size
        elif kind == "swa" and isinstance(sliding_window, int):
            cells = pad256(min(ctx_size, sliding_window + ubatch_size))
        else:
            raise BudgetError(f"invalid layer geometry: {layer_geometry!r}")
        layer_bytes = cells * layer_geometry["head_count_kv"] * (
            layer_geometry["key_length"] * BYTES_PER_ELEMENT[cache_type_k]
            + layer_geometry["value_length"] * BYTES_PER_ELEMENT[cache_type_v]
        )
        if kind == "full":
            full_bytes += layer_bytes
        else:
            swa_bytes += layer_bytes

    full_mib = math.ceil(full_bytes / MIB)
    swa_mib = math.ceil(swa_bytes / MIB)
    return {
        "full_kv_mib": full_mib,
        "swa_kv_mib": swa_mib,
        "kv_mib": full_mib + swa_mib,
    }


def compute_budget(
    vram_total_mib: int,
    compositor_used_mib: int,
    reserve_floor_mib: int,
    model_costs: list[dict[str, Any]],
    models_max: int,
    cache_type_k: str = "f16",
    cache_type_v: str = "f16",
) -> dict[str, Any]:
    if models_max < 1 or models_max > len(model_costs):
        raise BudgetError("models_max must select at least one and no more than all models")
    reserve = max(compositor_used_mib, reserve_floor_mib)
    available = vram_total_mib - reserve - SPIKE_HEADROOM_MIB
    models = []
    for cost in model_costs:
        required = cost["weights_mib"] + cost["kv_mib"] + RUNTIME_OVERHEAD_MIB
        models.append(
            cost
            | {
                "runtime_overhead_mib": RUNTIME_OVERHEAD_MIB,
                "required_mib": required,
            }
        )
    resident_models = sorted(
        models, key=lambda model: model["required_mib"], reverse=True
    )[:models_max]
    required = sum(model["required_mib"] for model in resident_models)
    shortfall = max(0, required - available)
    feasible = shortfall == 0

    remedies: list[str] = []
    if not feasible:
        already_quantized = (
            BYTES_PER_ELEMENT.get(cache_type_k, 2.0) <= BYTES_PER_ELEMENT["q8_0"]
            and BYTES_PER_ELEMENT.get(cache_type_v, 2.0) <= BYTES_PER_ELEMENT["q8_0"]
        )
        if not already_quantized:
            remedies.append(
                "set runtime.cache_type_k and cache_type_v to q8_0 "
                "(roughly halves KV cache size)"
            )
        remedies.append(
            "reduce ctx_size explicitly (KV cache scales with context)"
        )
        remedies.append(
            "enable runtime.flash_attn explicitly to reduce attention scratch memory"
        )
        if len(models) > models_max:
            largest = resident_models[0]
            remedies.append(
                f"disable model '{largest['alias']}' or choose a smaller model "
                f"({largest['required_mib']} MiB required)"
            )
        reclaimable = compositor_used_mib - reserve_floor_mib
        if reclaimable > 0:
            remedies.append(
                f"move the compositor to the iGPU with "
                f"KWIN_DRM_DEVICES=/dev/dri/card0 to reclaim ~{reclaimable} MiB "
                "(this blanks displays attached to the dGPU)"
            )

    return {
        "vram_total_mib": vram_total_mib,
        "reserve_mib": reserve,
        "spike_headroom_mib": SPIKE_HEADROOM_MIB,
        "runtime_overhead_mib_per_model": RUNTIME_OVERHEAD_MIB,
        "available_mib": available,
        "required_mib": required,
        "shortfall_mib": shortfall,
        "feasible": feasible,
        "models": models,
        "resident_models": resident_models,
        "remedies": remedies,
        "cache_type_k": cache_type_k,
        "cache_type_v": cache_type_v,
    }
