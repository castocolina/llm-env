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
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pylib.budget import BudgetError, compute_budget, kv_cache_mib
from pylib.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    enabled_models,
    load_config,
    save_config,
    set_enabled_models,
    set_model_enabled,
    sync_models_max,
    validate_config,
)
from pylib.detect import DetectError, detect
from pylib.gguf import GgufError, kv_geometry, read_gguf_header, validate_gguf
from pylib.presets import write_presets

DEVICE_LINE_RE = re.compile(
    r"^\s*(?P<id>\S+):\s*(?P<name>.*?)\s*\((?P<total_mib>\d+)\s*MiB.*\)$",
    re.MULTILINE,
)


def emit(payload: dict[str, Any], code: int = 0) -> int:
    print(json.dumps(payload, indent=2))
    return code


def fail(message: str) -> int:
    return emit({"error": message}, 1)


def cmd_detect(args: argparse.Namespace) -> int:
    return emit(detect())


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
        header = read_gguf_header(path)
        geometry = kv_geometry(header["metadata"])
        costs.append(
            {
                "alias": model["alias"],
                "weights_mib": path.stat().st_size // (1024 * 1024),
                "kv_mib": kv_cache_mib(
                    geometry,
                    model["ctx_size"],
                    runtime["cache_type_k"],
                    runtime["cache_type_v"],
                ),
            }
        )
    return costs


def cmd_budget(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
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
        cache_type_k=runtime["cache_type_k"],
        cache_type_v=runtime["cache_type_v"],
    )
    result["compositor_on_this_gpu"] = compositor_used > 0
    result["models_max"] = cfg["runtime"]["models_max"]
    return emit(result, 0 if result["feasible"] else 1)


def cmd_presets(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    write_presets(cfg, args.models_dir, args.device, Path(args.output))
    return emit({"written": str(args.output), "models": cfg["runtime"]["models_max"]})


def cmd_models(args: argparse.Namespace) -> int:
    path = Path(args.config)
    cfg = load_config(path)

    if args.action == "list":
        cfg = sync_models_max(cfg)
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
        save_config(cfg, path)
        return emit({"models_max": cfg["runtime"]["models_max"]})

    alias = args.aliases[0]
    cfg = sync_models_max(set_model_enabled(cfg, alias, args.action == "enable"))
    save_config(cfg, path)
    return emit({"alias": alias, "models_max": cfg["runtime"]["models_max"]})


def cmd_validate_gguf(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    results, ok = [], True
    for model in enabled_models(cfg):
        valid, message = validate_gguf(Path(args.models_dir) / model["file"])
        ok = ok and valid
        results.append({"alias": model["alias"], "valid": valid, "message": message})
    return emit({"all_valid": ok, "results": results}, 0 if ok else 1)


def cmd_init(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.template))
    errors = validate_config(cfg)
    if errors:
        return fail("; ".join(errors))
    cfg = sync_models_max(cfg)
    save_config(cfg, Path(args.config))
    return emit({"written": str(args.config)})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llmenv")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("detect").set_defaults(func=cmd_detect)

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

    presets = sub.add_parser("presets")
    presets.add_argument("--config", default=argparse.SUPPRESS)
    presets.add_argument("--models-dir", required=True)
    presets.add_argument("--device", required=True)
    presets.add_argument("--output", required=True)
    presets.set_defaults(func=cmd_presets)

    models = sub.add_parser("models")
    models.add_argument("--config", default=argparse.SUPPRESS)
    models.add_argument("action", choices=["list", "enable", "disable", "select"])
    models.add_argument("aliases", nargs="*")
    models.set_defaults(func=cmd_models)

    gguf = sub.add_parser("validate-gguf")
    gguf.add_argument("--config", default=argparse.SUPPRESS)
    gguf.add_argument("--models-dir", required=True)
    gguf.set_defaults(func=cmd_validate_gguf)

    init = sub.add_parser("init")
    init.add_argument("--config", default=argparse.SUPPRESS)
    init.add_argument("--template", required=True)
    init.set_defaults(func=cmd_init)

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
    except (ConfigError, BudgetError, GgufError, DetectError) as exc:
        return fail(str(exc))
    except OSError as exc:
        return fail(f"filesystem error: {exc}")


if __name__ == "__main__":
    sys.exit(main())
