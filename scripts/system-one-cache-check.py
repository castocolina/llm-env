#!/usr/bin/env python3
"""Prefetch or verify the snapshots laya 0.4.2 Agent.__init__ downloads."""
from __future__ import annotations

import os
import sys

from huggingface_hub import snapshot_download
from laya.revisions import resolve_revision
from laya.router import DEFAULT_MODELS, normalise_name

# Bundle repo convaiinnovations/laya (DEFAULT_MODELS; subfolder None |
# multilingual | typed-decisions). allow_patterns match Agent.__init__.
# LAYA_REVISION=reviewed → 55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851.
# Unused: laya-multilingual e4e9ddf21a7b1903b7acffd8814ad4307bf63a67,
# laya-typed-decisions 1a793eb568e6718f15941d08f85432581df534e3.


def _configured_names() -> list[str]:
    raw = os.environ.get("LAYA_MODELS", "").strip()
    try:
        names = [normalise_name(part) for part in raw.split(",") if part.strip()]
    except ValueError as error:
        sys.exit(str(error))
    if not names:
        sys.exit("LAYA_MODELS empty")
    return names

def _download(name: str, *, local_only: bool) -> None:
    repo, sub = DEFAULT_MODELS[name]
    revision = resolve_revision(repo)
    prefix = f"{sub}/" if sub else ""
    kw: dict = {
        "token": os.environ.get("HF_TOKEN") or None,
        "allow_patterns": [prefix + artifact for artifact in (
            "rl_agent_config.json", "model.safetensors", "tokenizer/*", "encoder/*",
        )],
    }
    if revision:
        kw["revision"] = revision
    if local_only:
        kw["local_files_only"] = True
    snapshot_download(repo, **kw)

def prefetch() -> None:
    for name in _configured_names():
        _download(name, local_only=False)
    print("prefetch ok")

def verify() -> None:
    for name in _configured_names():
        _download(name, local_only=True)
    print("weights ok")

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("prefetch", "verify"):
        print(f"usage: {sys.argv[0]} prefetch|verify", file=sys.stderr)
        sys.exit(2)
    {"prefetch": prefetch, "verify": verify}[sys.argv[1]]()
