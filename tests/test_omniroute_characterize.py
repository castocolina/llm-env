"""Disposable, opt-in OmniRoute characterization (LLM_ENV_UPDATE_CHARACTERIZE=1).

Runs the product harness (`pylib.omniroute_characterize.collect`, the one `make update-omniroute` uses for an
unprofiled running image) and writes the gzipped evidence, the candidate and, when complete, the promoted profile entry
under ./tmp/omniroute-characterization/ (gitignored: recorded series are logs, not docs). It never edits
pylib/omniroute_activity_profiles.json. Shipping an entry is a reviewed commit: copy the `.json.gz` into
tests/fixtures/omniroute-evidence/ (the entry already names that path) and append the entry;
`tests/test_omniroute_derive_candidate.py` then re-derives it from that fixture on every `make test`.

Image under test: `LLM_ENV_UPDATE_CHARACTERIZE_IMAGE` (default docker.io/diegosouzapw/omniroute:latest);
it must already be in the host image store (`podman pull <image>` once).
"""
from __future__ import annotations

import gzip
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
OUT = REPO / "tmp/omniroute-characterization"
FIXTURES = "tests/fixtures/omniroute-evidence"
DATE = time.strftime("%Y-%m-%d")


def test_characterize_and_derive_candidate():
    try:
        evidence = collect(OMNIROUTE_IMAGE, log=print)
    except BackendUnavailable as exc:
        pytest.skip(str(exc))
    OUT.mkdir(parents=True, exist_ok=True)
    config12 = evidence["config_id"].removeprefix("sha256:")[:12]
    raw = json.dumps(evidence, indent=2, sort_keys=True).encode()
    ev_path = OUT / f"{DATE}-omniroute-characterization-{config12}.json.gz"
    ev_path.write_bytes(gzip.compress(raw, mtime=0))

    candidate = derive_candidate(json.loads(raw))  # exactly what the shipped fixture will hold
    cand_path = OUT / f"{DATE}-omniroute-candidate-{config12}.json"
    cand_path.write_text(json.dumps(candidate, indent=2, sort_keys=True))
    promoted = None
    if not candidate["incomplete"]:
        try:
            entry = promote_candidate(candidate, config_id=evidence["config_id"],
                                      evidence_name=f"{FIXTURES}/{ev_path.name}",
                                      evidence_sha256=hashlib.sha256(raw).hexdigest(),  # of the plain bytes
                                      entrypoint=evidence["entrypoint"])
            promoted = OUT / f"{DATE}-omniroute-profile-{config12}.json"
            promoted.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n")
        except ValueError as exc:
            candidate["incomplete"].append(f"promotion: {exc}")
    print(json.dumps({"evidence": str(ev_path), "candidate": str(cand_path), "profile_entry": str(promoted),
                      "incomplete": candidate["incomplete"], "scenarios": evidence["scenarios"]}, indent=2))
    assert len(evidence["scenarios"]) == 10
