"""VRAM budget arithmetic.

Every quantity here is measured or derived from GGUF metadata. The single
tunable, SPIKE_HEADROOM_MIB, is a constant defined once and documented in the
design spec.
"""

from __future__ import annotations

import re
from typing import Any

# Fixed allowance for compositor, browser, and game VRAM spikes.
SPIKE_HEADROOM_MIB = 1024

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


def _total_kv_heads(geometry: dict[str, Any]) -> int:
    """Total KV heads summed across all layers.

    head_count_kv is an int when every layer is identical, or one entry per
    layer when a model varies it (Gemma alternates local and global attention).
    """
    head_count_kv = geometry["head_count_kv"]
    block_count = geometry["block_count"]

    if isinstance(head_count_kv, list):
        if len(head_count_kv) != block_count:
            raise BudgetError(
                f"head_count_kv has {len(head_count_kv)} entries but the model "
                f"has {block_count} blocks"
            )
        return sum(head_count_kv)
    return block_count * head_count_kv


def kv_cache_mib(
    geometry: dict[str, Any],
    ctx_size: int,
    cache_type_k: str,
    cache_type_v: str,
) -> int:
    for name in (cache_type_k, cache_type_v):
        if name not in BYTES_PER_ELEMENT:
            raise BudgetError(
                f"unknown cache type {name!r}; known: {sorted(BYTES_PER_ELEMENT)}"
            )

    total_kv_heads = _total_kv_heads(geometry)
    elements_k = ctx_size * total_kv_heads * geometry["key_length"]
    elements_v = ctx_size * total_kv_heads * geometry["value_length"]

    total_bytes = (
        elements_k * BYTES_PER_ELEMENT[cache_type_k]
        + elements_v * BYTES_PER_ELEMENT[cache_type_v]
    )
    return int(total_bytes / (1024 * 1024))


def compute_budget(
    vram_total_mib: int,
    compositor_used_mib: int,
    reserve_floor_mib: int,
    model_costs: list[dict[str, Any]],
    cache_type_k: str = "f16",
    cache_type_v: str = "f16",
) -> dict[str, Any]:
    reserve = max(compositor_used_mib, reserve_floor_mib)
    available = vram_total_mib - reserve - SPIKE_HEADROOM_MIB
    required = sum(m["weights_mib"] + m["kv_mib"] for m in model_costs)
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
            "reduce ctx_size for one or more models (KV cache scales linearly)"
        )
        remedies.append("enable runtime.flash_attn to reduce attention scratch memory")
        if len(model_costs) > 1:
            largest = max(model_costs, key=lambda m: m["weights_mib"] + m["kv_mib"])
            remedies.append(
                f"disable a model, e.g. '{largest['alias']}' "
                f"({largest['weights_mib'] + largest['kv_mib']} MiB)"
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
        "available_mib": available,
        "required_mib": required,
        "shortfall_mib": shortfall,
        "feasible": feasible,
        "models": model_costs,
        "remedies": remedies,
        "cache_type_k": cache_type_k,
        "cache_type_v": cache_type_v,
    }
