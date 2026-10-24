"""Disposable, opt-in OmniRoute characterization (LLM_ENV_UPDATE_CHARACTERIZE=1).

Runs the product harness (`pylib.omniroute_characterize.collect`, the one `make update-omniroute` uses for an
unprofiled running image) and writes reviewable evidence, candidate and, when complete, the promoted profile entry
under docs/superpowers/research/. It never edits pylib/omniroute_activity_profiles.json: shipping an entry is a
reviewed commit (`tests/test_omniroute_derive_candidate.py` then re-derives it from the committed evidence).

Image under test: `LLM_ENV_UPDATE_CHARACTERIZE_IMAGE` (default docker.io/diegosouzapw/omniroute:latest);
it must already be in the host image store (`podman pull <image>` once).
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time

import pytest

from pylib.omniroute_characterize import (
    BackendUnavailable,
    collect,
    derive_candidate,
    promote_candidate,
)

pytestmark = pytest.mark.skipif(os.environ.get("LLM_ENV_UPDATE_CHARACTERIZE") != "1",
                                reason="opt-in: LLM_ENV_UPDATE_CHARACTERIZE=1 (manual-review step)")

REPO = pathlib.Path(__file__).resolve().parents[1]
OMNIROUTE_IMAGE = os.environ.get("LLM_ENV_UPDATE_CHARACTERIZE_IMAGE", "docker.io/diegosouzapw/omniroute:latest")
RESEARCH = REPO / "docs/superpowers/research"
DATE = time.strftime("%Y-%m-%d")


def test_characterize_and_derive_candidate():
    try:
        evidence = collect(OMNIROUTE_IMAGE, log=print)
    except BackendUnavailable as exc:
        pytest.skip(str(exc))
    RESEARCH.mkdir(parents=True, exist_ok=True)
    config12 = evidence["config_id"].removeprefix("sha256:")[:12]
    ev_path = RESEARCH / f"{DATE}-omniroute-characterization-{config12}.json"
    ev_path.write_text(json.dumps(evidence, indent=2, sort_keys=True))
    ev_path.with_suffix(".json.sha256").write_text(hashlib.sha256(ev_path.read_bytes()).hexdigest() + "\n")

    candidate = derive_candidate(evidence)
    cand_path = RESEARCH / f"{DATE}-omniroute-candidate-{config12}.json"
    cand_path.write_text(json.dumps(candidate, indent=2, sort_keys=True))
    promoted = None
    if not candidate["incomplete"]:
        try:
            entry = promote_candidate(candidate, config_id=evidence["config_id"],
                                      evidence_name=str(ev_path.relative_to(REPO)),
                                      evidence_sha256=hashlib.sha256(ev_path.read_bytes()).hexdigest(),
                                      entrypoint=evidence["entrypoint"])
            promoted = RESEARCH / f"{DATE}-omniroute-profile-{config12}.json"
            promoted.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n")
        except ValueError as exc:
            candidate["incomplete"].append(f"promotion: {exc}")
    print(json.dumps({"evidence": str(ev_path), "candidate": str(cand_path), "profile_entry": str(promoted),
                      "incomplete": candidate["incomplete"], "scenarios": evidence["scenarios"]}, indent=2))
    assert len(evidence["scenarios"]) == 10
