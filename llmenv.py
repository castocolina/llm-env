#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""llm-env control CLI.

Emits JSON on stdout so bash callers can consume output with jq.

Exit codes: 0 success, 1 handled error, 2 usage error.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pylib.agent_runner import (
    DEFAULT_GRACE_SECONDS,
    DEFAULT_RUNTIME_SECONDS,
    DEFAULT_STREAM_LIMIT_BYTES,
    RunLimits,
    run_bounded_agent,
)
from pylib.budget import BudgetError, compute_budget, kv_cache_components_mib
from pylib.compose import write_compose
from pylib.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    enabled_models,
    load_config,
    migrate_config,
    require_valid_config,
    save_config,
    set_enabled_models,
    set_model_enabled,
    sync_models_max,
)
from pylib.detect import DetectError, detect, host_resources, processes_on_render_node
from pylib.dotenv import read_env_file
from pylib.gguf import (
    GgufError,
    kv_geometry,
    moe_expert_offload_mib,
    read_gguf_header,
    validate_gguf,
)
from pylib.omniroute import OmniRouteError, provision
from pylib.presets import write_presets
from pylib.remote_setup import ensure_api_key
from pylib.resources import ResourceError, compute_resource_limits
from pylib.transcript import classify_transcript

DEVICE_LINE_RE = re.compile(
    r"^\s*(?P<id>\S+):\s*(?P<name>.*?)\s*\((?P<total_mib>\d+)\s*MiB.*\)$",
    re.MULTILINE,
)


def emit(payload: dict[str, Any], code: int = 0) -> int:
    print(json.dumps(payload, indent=2))
    return code


def fail(message: str) -> int:
    return emit({"error": message}, 1)


def finite_positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "must be a finite positive number"
        ) from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return parsed


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def cmd_detect(args: argparse.Namespace) -> int:
    return emit(detect())


def cmd_processes_on_render_node(args: argparse.Namespace) -> int:
    return emit({"processes": processes_on_render_node(args.render_node)})


def parse_device_listing(text: str) -> list[dict[str, int | str]]:
    return [
        {
            "id": match["id"],
            "name": match["name"].strip(),
            "total_mib": int(match["total_mib"]),
        }
        for match in DEVICE_LINE_RE.finditer(text)
    ]


def _read_device_listing(args: argparse.Namespace) -> str:
    if args.listing_file:
        return Path(args.listing_file).read_text(encoding="utf-8")
    if not args.list_command:
        raise ConfigError("provide --listing-file or --list-command to list devices")
    try:
        result = subprocess.run(
            args.list_command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConfigError("timed out running the device listing command") from exc
    if result.returncode:
        detail = result.stderr.strip()
        message = f"device listing command failed with exit status {result.returncode}"
        if detail:
            message += f": {detail}"
        raise ConfigError(message)
    return result.stdout


def cmd_list_devices(args: argparse.Namespace) -> int:
    return emit({"devices": parse_device_listing(_read_device_listing(args))})


def cmd_resolve_device(args: argparse.Namespace) -> int:
    devices = parse_device_listing(_read_device_listing(args))
    matches = [device for device in devices if device["name"] == args.device_name.strip()]
    if len(matches) == 1:
        device = matches[0]
        return emit({"device": device["id"], "name": device["name"]})
    if len(matches) > 1:
        ids = [device["id"] for device in matches]
        return fail(
            f"multiple devices match {args.device_name!r}: {ids}. "
            "Choose a unique device name."
        )

    available = [f"{d['id']}: {d['name']}" for d in devices]
    return fail(
        f"device {args.device_name!r} not found. Available: {available or 'none'}"
    )


def _model_costs(cfg: dict[str, Any], models_dir: Path) -> list[dict[str, Any]]:
    runtime = cfg["runtime"]
    costs = []
    for model in enabled_models(cfg):
        path = models_dir / model["file"]
        metadata = read_gguf_header(path)["metadata"]
        geometry = kv_geometry(metadata)
        weights_mib = math.ceil(path.stat().st_size / (1024 * 1024))
        ram_weights_mib = 0
        n_cpu_moe = model.get("n_cpu_moe")
        # `is not None`, not truthiness: n_cpu_moe: 0 is a valid, meaningful
        # config value (explicitly "no CPU offload"), and must still route
        # through moe_expert_offload_mib so a stray n_cpu_moe on a dense
        # model (which has no routed experts at all) is still rejected,
        # rather than silently skipped because 0 is falsy.
        if n_cpu_moe is not None:
            ram_weights_mib = moe_expert_offload_mib(path, metadata, n_cpu_moe)
            weights_mib -= ram_weights_mib
        costs.append(
            {
                "alias": model["alias"],
                "weights_mib": weights_mib,
                "ram_weights_mib": ram_weights_mib,
                **kv_cache_components_mib(
                    geometry,
                    model["ctx_size"],
                    runtime["ubatch_size"],
                    runtime["cache_type_k"],
                    runtime["cache_type_v"],
                ),
            }
        )
    return costs


def cmd_budget(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    facts = detect()
    pci = cfg["gpu"]["pci_address"]
    if not pci:
        return fail(
            "gpu.pci_address is not set. Run 'make setup' to choose a GPU, or copy "
            "a pci_address from 'llmenv.py detect' into the config."
        )
    gpu = next((g for g in facts["gpus"] if g["pci_address"] == pci), None)
    if gpu is None:
        detected = [g["pci_address"] for g in facts["gpus"]]
        return fail(f"configured GPU {pci} not present; detected: {detected}")

    compositor_used = (
        gpu["vram_used_mib"]
        if facts["compositor_render_node"] == gpu["render_node"]
        else 0
    )
    runtime = cfg["runtime"]
    result = compute_budget(
        vram_total_mib=gpu["vram_total_mib"],
        compositor_used_mib=compositor_used,
        reserve_floor_mib=cfg["gpu"]["reserve_floor_mib"],
        model_costs=_model_costs(cfg, Path(args.models_dir)),
        models_max=runtime["models_max"],
        cache_type_k=runtime["cache_type_k"],
        cache_type_v=runtime["cache_type_v"],
        # `or None` folds both "key absent" (None from .get()) and a
        # configured `0` into the same "uncapped" sentinel compute_budget()
        # already understands, instead of letting a literal 0 through.
        vram_budget_ceiling_mib=cfg["gpu"].get("vram_budget_ceiling_mib") or None,
    )
    result["compositor_on_this_gpu"] = compositor_used > 0
    result["models_max"] = cfg["runtime"]["models_max"]
    # compute_budget()'s own feasible/shortfall_mib/remedies describe only
    # the VRAM check above. Preserve that verdict under its own name before
    # the RAM cross-check below can flip the combined "feasible" -- a
    # RAM-only failure must never be reported through the VRAM check's
    # fields (shortfall_mib/available_mib/required_mib stay VRAM-only, and
    # would otherwise misleadingly read "short by 0 MiB").
    result["vram_feasible"] = result["feasible"]

    # The RAM check below must cover every enabled model that could become
    # concurrently resident, not just compute_budget()'s "resident_models"
    # (its top models_max models ranked by VRAM cost) -- a model cheap in
    # VRAM but expensive in CPU-offloaded RAM can lose that ranking to a big
    # dense model and still be an enabled, selectable model. Rank
    # `result["models"]` (every enabled model's cost) by ram_weights_mib
    # instead and sum the top models_max of them: runtime.models_max can
    # legally be greater than 1 (nothing in the schema forbids it, even
    # though this plan's Global Constraint keeps the shipped default at 1),
    # in which case that many models can be concurrently resident and their
    # RAM needs add up. At models_max == 1 this reduces to exactly the
    # single largest ram_weights_mib.
    models_max = cfg["runtime"]["models_max"]
    ram_ranked = sorted(
        result.get("models", []),
        key=lambda model: model.get("ram_weights_mib", 0),
        reverse=True,
    )
    ram_resident = ram_ranked[:models_max]
    ram_required_mib = sum(model.get("ram_weights_mib", 0) for model in ram_resident)
    ram_required_aliases = [
        model["alias"] for model in ram_resident if model.get("ram_weights_mib", 0) > 0
    ]
    result["ram_required_mib"] = ram_required_mib

    llm_server_resources = cfg.get("resources", {}).get("llm_server", {})
    host = host_resources()
    ram_available_mib = None
    resource_floor_error = None
    try:
        resource_limits = compute_resource_limits(
            host["cpu_count"],
            host["memory_total_mib"],
            llm_server_resources.get("memory_ceiling_pct", 46),
            llm_server_resources.get("memory_ceiling_floor_pct", 30),
            llm_server_resources.get("cpu_ceiling_pct", 60),
            llm_server_resources.get("cpu_ceiling_floor_pct", 20),
        )
        ram_available_mib = resource_limits["llm_server"]["memory_mib"]
    except ResourceError as exc:
        # `make setup` (Step 3 below) and `make start` (Step 5d below) both
        # call `llmenv resources` directly and die loudly on this exact
        # error before ever reaching this command in their normal
        # pipelines -- but `llmenv budget` is a standalone command a caller
        # can invoke directly (e.g. a bare CLI check, or a future script
        # that never calls `resources`). On a host that can't even reserve
        # the fixed CPU/RAM floors, silently skipping the RAM check here
        # would report "feasible": true for a host that can never actually
        # run llm-server at all, which is exactly the kind of "silently
        # correct an infeasible config" this plan's Global Constraints
        # forbid. Surface it as its own explicit RAM failure instead.
        resource_floor_error = str(exc)
    result["ram_available_mib"] = ram_available_mib

    ram_feasible = True
    ram_shortfall_mib = None
    if resource_floor_error is not None:
        ram_feasible = False
        result.setdefault("remedies", [])
        result["remedies"].append(
            f"host cannot reserve the fixed CPU/RAM floors for llm-server "
            f"at all: {resource_floor_error}; this is fatal regardless of "
            "n_cpu_moe or memory_ceiling_pct"
        )
    elif ram_required_mib and ram_available_mib is not None and ram_required_mib > ram_available_mib:
        ram_feasible = False
        ram_shortfall_mib = ram_required_mib - ram_available_mib
        result.setdefault("remedies", [])
        absolute_max_limits = compute_resource_limits(
            host["cpu_count"],
            host["memory_total_mib"],
            memory_ceiling_pct=100,
            memory_ceiling_floor_pct=llm_server_resources.get("memory_ceiling_floor_pct", 30),
            cpu_ceiling_pct=llm_server_resources.get("cpu_ceiling_pct", 60),
            cpu_ceiling_floor_pct=llm_server_resources.get("cpu_ceiling_floor_pct", 20),
        )
        absolute_max_mib = absolute_max_limits["llm_server"]["memory_mib"]
        # Built without ever putting a possessive "'s" directly after a
        # closing quote mark (i.e. never "'alias''s") -- that construction
        # reads as a doubled-quote typo. "of model 'alias'" / "of models
        # 'a', 'b'" instead.
        models_desc = (
            f"model '{ram_required_aliases[0]}'"
            if len(ram_required_aliases) == 1
            else "models " + ", ".join(f"'{alias}'" for alias in ram_required_aliases)
        )
        if ram_required_mib > absolute_max_mib:
            result["remedies"].append(
                f"no resources.llm_server.memory_ceiling_pct value can fit "
                f"the CPU-offloaded MoE experts of {models_desc} on this "
                f"host ({ram_required_mib} MiB needed, {absolute_max_mib} MiB "
                "is the most the host can ever provide llm-server after its "
                "fixed floor and OmniRoute's reservation); reduce "
                "n_cpu_moe, use a smaller quantization, add RAM, or lower "
                "runtime.models_max"
            )
        else:
            needed_pct = math.ceil(ram_required_mib / host["memory_total_mib"] * 100)
            result["remedies"].append(
                f"resources.llm_server.memory_ceiling_pct is too low for "
                f"the CPU-offloaded MoE experts of {models_desc} "
                f"({ram_required_mib} MiB needed, {ram_available_mib} MiB "
                f"available); raise it to at least {needed_pct}%"
            )
    result["ram_shortfall_mib"] = ram_shortfall_mib
    result["ram_feasible"] = ram_feasible
    result["feasible"] = result["vram_feasible"] and ram_feasible
    return emit(result, 0 if result["feasible"] else 1)


def cmd_resources(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    host = host_resources()
    llm_server_resources = cfg["resources"]["llm_server"]
    llm_server_enabled = cfg.get("llm_server", {}).get("enabled", True)
    limits = compute_resource_limits(
        host["cpu_count"],
        host["memory_total_mib"],
        llm_server_resources["memory_ceiling_pct"],
        llm_server_resources["memory_ceiling_floor_pct"],
        llm_server_resources["cpu_ceiling_pct"],
        llm_server_resources["cpu_ceiling_floor_pct"],
        llm_server_enabled=llm_server_enabled,
    )
    return emit({"host": host, **limits})


def cmd_omniroute(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    omniroute_cfg = cfg.get("omniroute") or {}
    initial_password = omniroute_cfg.get("initial_password")
    port = omniroute_cfg.get("port")
    if not initial_password or not port:
        return fail(
            "omniroute.initial_password and omniroute.port must be set; run "
            "'make start' after 'make setup' to generate them"
        )
    base_url = f"http://127.0.0.1:{port}"
    if args.action == "provision":
        result = provision(
            base_url, initial_password, cfg["server"]["port"], cfg["server"]["api_key"]
        )
        return emit(result)
    # args.action == "issue-key"
    cache_path = Path(args.config).parent / "omniroute-api-key.json"
    api_key = ensure_api_key(
        base_url, initial_password, cache_path, key_name="llm-env-local-agents"
    )
    return emit({"api_key": api_key})


def cmd_presets(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    write_presets(cfg, args.models_dir, args.device, Path(args.output))
    return emit({"written": str(args.output), "models": cfg["runtime"]["models_max"]})


def cmd_render_compose(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    env_vars = read_env_file(Path(args.env_file)) if args.env_file else {}
    write_compose(
        cfg,
        models_dir=args.models_dir,
        presets_path=args.presets_path,
        repo_root=args.repo_root,
        omni_router_master_key=env_vars.get("OMNI_ROUTER_MASTER_KEY", ""),
        path=Path(args.output),
    )
    return emit({"written": str(args.output)})


def cmd_models(args: argparse.Namespace) -> int:
    path = Path(args.config)
    cfg = require_valid_config(load_config(path))

    if args.action == "list":
        return emit(
            {
                "models_max": cfg["runtime"]["models_max"],
                "models": [
                    {
                        "alias": m["alias"],
                        "enabled": bool(m["enabled"]),
                        "file": m["file"],
                    }
                    for m in cfg["models"]
                ],
            }
        )

    if args.action == "select":
        cfg = set_enabled_models(cfg, args.aliases)
        require_valid_config(cfg)
        save_config(cfg, path)
        return emit({"models_max": cfg["runtime"]["models_max"]})

    alias = args.aliases[0]
    cfg = sync_models_max(set_model_enabled(cfg, alias, args.action == "enable"))
    require_valid_config(cfg)
    save_config(cfg, path)
    return emit({"alias": alias, "models_max": cfg["runtime"]["models_max"]})


def cmd_validate_gguf(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    results, ok = [], True
    for model in enabled_models(cfg):
        valid, message = validate_gguf(Path(args.models_dir) / model["file"])
        ok = ok and valid
        results.append({"alias": model["alias"], "valid": valid, "message": message})
    return emit({"all_valid": ok, "results": results}, 0 if ok else 1)


def cmd_classify_transcript(args: argparse.Namespace) -> int:
    excerpt = classify_transcript(args.client, Path(args.transcript))
    return emit({"excerpt": excerpt})


def cmd_init(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.template)))
    cfg = sync_models_max(cfg)
    save_config(cfg, Path(args.config))
    return emit({"written": str(args.config)})


def cmd_migrate_config(args: argparse.Namespace) -> int:
    path = Path(args.config)
    original = load_config(path, migrate=False)
    cfg = require_valid_config(migrate_config(copy.deepcopy(original)))
    written = cfg != original
    if written:
        save_config(cfg, path)
    return emit({"written": written, "path": str(path)})


def cmd_run_agent_bounded(args: argparse.Namespace) -> int:
    command = args.agent_command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        args.command_parser.error("a remainder command is required")

    result = run_bounded_agent(
        command,
        args.transcript,
        args.stderr,
        limits=RunLimits(
            runtime_seconds=args.runtime_seconds,
            grace_seconds=args.grace_seconds,
            stream_limit_bytes=args.stream_limit_bytes,
        ),
    )
    return emit(result.to_dict())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llmenv")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("detect").set_defaults(func=cmd_detect)

    processes_parser = sub.add_parser("processes-on-render-node")
    processes_parser.add_argument("--render-node", required=True)
    processes_parser.set_defaults(func=cmd_processes_on_render_node)

    list_devices = sub.add_parser("list-devices")
    list_devices.add_argument("--listing-file")
    list_devices.add_argument("--list-command", default="")
    list_devices.set_defaults(func=cmd_list_devices)

    resolve = sub.add_parser("resolve-device")
    resolve.add_argument("--device-name", required=True)
    resolve.add_argument("--listing-file")
    resolve.add_argument("--list-command", default="")
    resolve.set_defaults(func=cmd_resolve_device)

    budget = sub.add_parser("budget")
    budget.add_argument("--config", default=argparse.SUPPRESS)
    budget.add_argument("--models-dir", required=True)
    budget.set_defaults(func=cmd_budget)

    resources_parser = sub.add_parser("resources")
    resources_parser.add_argument("--config", default=argparse.SUPPRESS)
    resources_parser.set_defaults(func=cmd_resources)

    omniroute_parser = sub.add_parser("omniroute")
    omniroute_parser.add_argument("--config", default=argparse.SUPPRESS)
    omniroute_parser.add_argument("action", choices=["provision", "issue-key"])
    omniroute_parser.set_defaults(func=cmd_omniroute)

    presets = sub.add_parser("presets")
    presets.add_argument("--config", default=argparse.SUPPRESS)
    presets.add_argument("--models-dir", required=True)
    presets.add_argument("--device", required=True)
    presets.add_argument("--output", required=True)
    presets.set_defaults(func=cmd_presets)

    render_compose = sub.add_parser("render-compose")
    render_compose.add_argument("--config", default=argparse.SUPPRESS)
    render_compose.add_argument("--models-dir", required=True)
    render_compose.add_argument("--presets-path", required=True)
    render_compose.add_argument("--repo-root", required=True)
    render_compose.add_argument("--env-file", default=None)
    render_compose.add_argument("--output", required=True)
    render_compose.set_defaults(func=cmd_render_compose)

    models = sub.add_parser("models")
    models.add_argument("--config", default=argparse.SUPPRESS)
    models.add_argument("action", choices=["list", "enable", "disable", "select"])
    models.add_argument("aliases", nargs="*")
    models.set_defaults(func=cmd_models)

    gguf = sub.add_parser("validate-gguf")
    gguf.add_argument("--config", default=argparse.SUPPRESS)
    gguf.add_argument("--models-dir", required=True)
    gguf.set_defaults(func=cmd_validate_gguf)

    classify_transcript_parser = sub.add_parser("classify-transcript")
    classify_transcript_parser.add_argument("--client", required=True, choices=["pi", "opencode"])
    classify_transcript_parser.add_argument("--transcript", required=True)
    classify_transcript_parser.set_defaults(func=cmd_classify_transcript)

    init = sub.add_parser("init")
    init.add_argument("--config", default=argparse.SUPPRESS)
    init.add_argument("--template", required=True)
    init.set_defaults(func=cmd_init)

    migrate = sub.add_parser("migrate-config")
    migrate.add_argument("--config", default=argparse.SUPPRESS)
    migrate.set_defaults(func=cmd_migrate_config)

    run_agent = sub.add_parser("run-agent-bounded")
    run_agent.add_argument("--transcript", type=Path, required=True)
    run_agent.add_argument("--stderr", type=Path, required=True)
    run_agent.add_argument(
        "--runtime-seconds",
        type=finite_positive_float,
        default=DEFAULT_RUNTIME_SECONDS,
    )
    run_agent.add_argument(
        "--grace-seconds",
        type=finite_positive_float,
        default=DEFAULT_GRACE_SECONDS,
    )
    run_agent.add_argument(
        "--stream-limit-bytes",
        type=positive_int,
        default=DEFAULT_STREAM_LIMIT_BYTES,
    )
    run_agent.add_argument("agent_command", nargs=argparse.REMAINDER)
    run_agent.set_defaults(func=cmd_run_agent_bounded, command_parser=run_agent)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "models" and args.action != "list" and not args.aliases:
        print(json.dumps({"error": "at least one alias is required"}, indent=2))
        return 2
    if args.command == "models" and args.action in ("enable", "disable") and len(args.aliases) != 1:
        print(json.dumps({"error": "exactly one alias is required for enable/disable"}, indent=2))
        return 2
    try:
        return args.func(args)
    except (ConfigError, BudgetError, GgufError, DetectError, ResourceError, OmniRouteError) as exc:
        return fail(str(exc))
    except OSError as exc:
        return fail(f"filesystem error: {exc}")


if __name__ == "__main__":
    sys.exit(main())
