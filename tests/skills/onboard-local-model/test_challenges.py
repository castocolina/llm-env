"""Tests for the seeded benchmark challenge generator (challenges/, scripts/gen-challenge.sh).

Offline: node only, no GPU, no containers, no network.
"""

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest
from skill_helpers import NODE, NODE_DIR, NODE_SKIP_REASON, ROOT, SCRIPTS, SKILL
from skill_helpers import node_env as _node_env
from skill_helpers import out as _out
from skill_helpers import run as _run

CHALLENGES = SKILL / "challenges"
GEN = SCRIPTS / "gen-challenge.sh"
SHARED_END = "Do not edit the tests or data; reply with one sentence naming the root cause or the files you changed."
LEAK_DENYLIST = re.compile(r"solution|reference|mutant|mutation|answer|fix|bug|defect|manifest|seed|oracle|variant|decoy|twin|hint|stub", re.IGNORECASE)


pytestmark = pytest.mark.skipif(NODE_DIR is None, reason=NODE_SKIP_REASON)


def _node(*args: str | Path) -> subprocess.CompletedProcess[str]:
    """Run the real node binary (never the shim) with NODE_DIR first on PATH."""
    assert NODE is not None  # guarded by pytestmark
    return _run(NODE, *args, env=_node_env())


def _gen_raw(*args: str | Path) -> subprocess.CompletedProcess[str]:
    return _run("bash", GEN, *args, env=_node_env(), stdin=subprocess.DEVNULL)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--layer", "6", "--seed", "1", "--out", "{OUT}"],
        ["--layer", "1", "--seed", "-1", "--out", "{OUT}"],
        ["--layer", "1", "--seed", "4294967296", "--out", "{OUT}"],
        ["--layer", "1", "--seed", "x", "--out", "{OUT}"],
        ["--layer", "1", "--seed", "1"],  # no --out: 2, not require_tmp_out's 1
        ["--check-library"],
        ["--verify"],
        ["--score"],
        ["--from", "transcript.jsonl"],  # there is no input-file flag
    ],
)
def test_gen_challenge_rejects_bad_usage(argv: list[str], tmp_path: Path) -> None:
    out = _out(tmp_path, "gen")
    result = _gen_raw(*[a.replace("{OUT}", str(out)) for a in argv])
    assert result.returncode == 2 and result.stderr.strip(), (argv, result.stdout, result.stderr)


def test_gen_challenge_rejects_a_non_empty_out(tmp_path: Path) -> None:
    out = _out(tmp_path, "gen")
    out.mkdir()
    (out / "keep.txt").write_text("x")
    result = _gen_raw("--layer", "1", "--seed", "1", "--out", out)
    assert result.returncode == 2 and result.stderr.strip()
    assert (out / "keep.txt").read_text() == "x"


def test_gen_challenge_refuses_an_out_outside_the_repo_tmp(tmp_path: Path) -> None:
    for out in (tmp_path / "outside", ROOT / "tmp"):
        result = _gen_raw("--layer", "1", "--seed", "1", "--out", out)
        assert result.returncode == 2, (out, result.stderr)
        assert "isolated temp directories under ./tmp/" in result.stderr
    assert not (tmp_path / "outside").exists()


def test_generator_id_depends_only_on_the_library(tmp_path: Path) -> None:
    result = _gen_raw("--generator-id")
    assert result.returncode == 0, result.stderr
    gid = result.stdout.strip()
    assert re.fullmatch(r"g-[0-9a-f]{12}", gid), gid

    copy = tmp_path / "challenges"
    shutil.copytree(CHALLENGES, copy)
    assert _node(copy / "gen.mjs", "--generator-id").stdout.strip() == gid
    with (copy / "lib" / "rng.mjs").open("a") as handle:
        handle.write("\n")
    changed = _node(copy / "gen.mjs", "--generator-id")
    assert changed.returncode == 0 and re.fullmatch(r"g-[0-9a-f]{12}", changed.stdout.strip())
    assert changed.stdout.strip() != gid


RNG_PROGRAM = """
const { rng } = await import(process.argv[1]);
const five = (seed, label) => { const r = rng(seed, label); return Array.from({ length: 5 }, () => r.int(0, 1000000)); };
const dice = rng(3, "dice");
const rolls = Array.from({ length: 1000 }, () => dice.int(1, 6));
console.log(JSON.stringify({ a: five(7, "a"), a2: five(7, "a"), b: five(7, "b"), s8: five(8, "a"), rolls }));
"""


def test_rng_streams_are_deterministic_and_labelled() -> None:
    runs = [_node("--input-type=module", "-e", RNG_PROGRAM, (CHALLENGES / "lib" / "rng.mjs").as_uri()) for _ in range(2)]
    assert all(r.returncode == 0 for r in runs), [r.stderr for r in runs]
    assert runs[0].stdout == runs[1].stdout
    data = json.loads(runs[0].stdout)
    assert data["a"] == data["a2"] and data["b"] != data["a"] and data["s8"] != data["a"]
    assert set(data["rolls"]) == {1, 2, 3, 4, 5, 6}


def test_generator_sources_parse() -> None:
    sources = sorted(CHALLENGES.rglob("*.mjs"))
    assert sources, "challenges/ holds no *.mjs yet"
    for source in sources:
        result = _node("--check", source)
        assert result.returncode == 0, (source, result.stderr)


def test_wrapper_works_through_a_symlinked_skill_path(tmp_path: Path) -> None:
    """node resolves its own module path but not argv[1]: a symlinked invocation must still run main."""
    link = tmp_path / "sk"
    link.symlink_to(SKILL)
    result = _run("bash", link / "scripts" / "gen-challenge.sh", "--generator-id", env=_node_env(), stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r"g-[0-9a-f]{12}", result.stdout.strip()), (result.stdout, result.stderr)


def _gen(
    tmp_path: Path, layer: int, seed: int, *extra: str, name: str | None = None, env: dict[str, str] | None = None, cwd: Path | None = None
) -> tuple[subprocess.CompletedProcess[str], Path]:
    out = _out(tmp_path, name or f"l{layer}-s{seed}")
    result = _run(
        "bash", GEN, "--layer", str(layer), "--seed", str(seed), "--out", out, *extra,
        env=_node_env(**(env or {})), cwd=cwd, stdin=subprocess.DEVNULL,
    )
    return result, out


def _manifest(out: Path) -> dict:
    return json.loads((out / "manifest.json").read_text())


def _verify(directory: Path, **env: str) -> dict:
    """Score `directory` with gen-challenge.sh --score (the real node first on PATH)."""
    result = _run("bash", GEN, "--score", directory, env=_node_env(**env), stdin=subprocess.DEVNULL, timeout=180)
    return json.loads(result.stdout)


def _overlay_reference(out: Path, tmp_path: Path) -> Path:
    dest = tmp_path / f"overlay-{out.name}"
    shutil.copytree(out / "challenge", dest, ignore=shutil.ignore_patterns(".git"))
    shutil.rmtree(dest / "src")
    shutil.copytree(out / "reference" / "src", dest / "src")
    return dest


def _ps_lines_with(text: str) -> list[str]:
    return [line for line in _run("ps", "-eo", "args").stdout.splitlines() if text in line]


def test_score_counts_a_cancelled_test_as_failed(tmp_path: Path) -> None:
    result, out = _gen(tmp_path, 1, 3, "--with-reference")
    assert result.returncode == 0, result.stderr
    d = _overlay_reference(out, tmp_path)
    base = _verify(d)
    assert base["pass"] == base["total"] > 0
    (d / "test" / "zz-hang.test.js").write_text(
        'import test from "node:test";\n'
        'test("ok", () => {});\n'
        'test("hang", async () => { await new Promise((r) => setTimeout(r, 1e9)); });\n'
    )
    started = time.monotonic()
    r = _verify(d, ONBOARD_GEN_TEST_TIMEOUT_MS="4000")
    assert time.monotonic() - started < 30
    assert r["timed_out"] == []
    assert r["total"] == base["total"] + 2 and r["pass"] == base["pass"] + 1


def test_score_kills_a_busy_loop_and_counts_the_file_as_failed(tmp_path: Path) -> None:
    result, out = _gen(tmp_path, 1, 3, "--with-reference")
    assert result.returncode == 0, result.stderr
    d = _overlay_reference(out, tmp_path)
    with (d / _manifest(out)["operators"][0]["file"]).open("a") as handle:
        handle.write("\nfor (;;) {}\n")
    r = _verify(d, ONBOARD_GEN_TEST_TIMEOUT_MS="2000")
    assert len(r["timed_out"]) >= 1 and r["pass"] < r["total"]
    assert _ps_lines_with(str(d)) == []


def test_score_ignores_counts_a_solution_prints_itself(tmp_path: Path) -> None:
    result, out = _gen(tmp_path, 1, 3)
    assert result.returncode == 0, result.stderr
    shipped = _verify(out / "challenge")
    assert 0 < shipped["total"] and shipped["pass"] < shipped["total"]
    with (out / "challenge" / _manifest(out)["operators"][0]["file"]).open("a") as handle:
        handle.write('\nconsole.log("pass 100");\nconsole.log("fail 0");\n')
    r = _verify(out / "challenge")
    assert r["pass"] == shipped["pass"] and r["total"] == shipped["total"], (r, shipped)


def test_score_gives_a_hung_file_no_printed_pass_count(tmp_path: Path) -> None:
    result, out = _gen(tmp_path, 1, 3, "--with-reference")
    assert result.returncode == 0, result.stderr
    d = _overlay_reference(out, tmp_path)
    with (d / _manifest(out)["operators"][0]["file"]).open("a") as handle:
        handle.write('\nconsole.log("pass 100");\nfor (;;) {}\n')
    r = _verify(d, ONBOARD_GEN_TEST_TIMEOUT_MS="2000")
    assert len(r["timed_out"]) >= 1 and r["pass"] < r["total"] and r["pass"] < 100, r
    assert _ps_lines_with(str(d)) == []


def _library_copy(tmp_path: Path) -> Path:
    copy = tmp_path / "library"
    shutil.copytree(CHALLENGES, copy)
    return copy


def _check(
    gen_mjs: Path, out: Path, layer: int | None = None, env: dict[str, str] | None = None, timeout: float = 300
) -> tuple[subprocess.CompletedProcess[str], dict]:
    assert NODE is not None  # guarded by pytestmark
    layer_args = [] if layer is None else ["--layer", str(layer)]
    result = _run(
        NODE, gen_mjs, "--check-library", "--out", out, *layer_args,
        env=_node_env(**(env or {})), stdin=subprocess.DEVNULL, timeout=timeout,
    )
    return result, json.loads(result.stdout) if result.stdout.strip() else {}


def _tree(root: Path) -> dict[str, str]:
    """relpath -> sha256 of every file under root, without .git."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def test_l1_challenge_is_deterministic(tmp_path: Path) -> None:
    first, out_a = _gen(tmp_path, 1, 11, name="a")
    second, out_b = _gen(
        tmp_path, 1, 11, name="b", cwd=tmp_path, env={"HOME": str(tmp_path), "TZ": "Asia/Tokyo", "LANG": "C"}
    )
    assert first.returncode == 0 and second.returncode == 0, (first.stderr, second.stderr)
    assert _manifest(out_a) == _manifest(out_b)
    files = _tree(out_a / "challenge")
    assert files == _tree(out_b / "challenge") == _manifest(out_a)["files"]


def test_l1_challenge_fails_as_shipped_and_passes_with_the_reference(tmp_path: Path) -> None:
    result, out = _gen(tmp_path, 1, 3, "--with-reference")
    assert result.returncode == 0, result.stderr
    manifest = _manifest(out)
    shipped = _verify(out / "challenge")
    assert 0 < shipped["total"] and shipped["pass"] < shipped["total"]
    fixed = _verify(_overlay_reference(out, tmp_path))
    assert fixed["pass"] == fixed["total"]
    assert sum(f["failed"] for f in manifest["expected_failing"]) == shipped["total"] - shipped["pass"]
    validity = manifest["validity"]
    assert all(validity[k] is True for k in ("fails_as_shipped", "passes_with_reference", "leak_free", "self_contained"))
    assert manifest["layer"] == 1 and validity["difficulty"]["site_depth"] == 0


@pytest.mark.parametrize("layer", [1, 2, 3, 4, 5])
def test_seeds_one_to_three_pass_the_validity_gate(layer: int, tmp_path: Path) -> None:
    """The offline regression set: generation itself runs the gate, so exit 0 plus the manifest proves it."""
    for seed in (1, 2, 3):
        result, out = _gen(tmp_path, layer, seed)
        assert result.returncode == 0, (layer, seed, result.stderr)
        validity = _manifest(out)["validity"]
        assert all(validity[k] is True for k in ("fails_as_shipped", "passes_with_reference", "leak_free", "self_contained")), (layer, seed)
        shutil.rmtree(out)


def test_l1_challenge_layout_and_prompt_do_not_leak(tmp_path: Path) -> None:
    result, out = _gen(tmp_path, 1, 5)
    assert result.returncode == 0, result.stderr
    challenge = out / "challenge"
    top = {p.name for p in challenge.iterdir()}
    assert {"prompt.md", "package.json", "src", "test"} <= top <= {"prompt.md", "package.json", "src", "test", "data", ".git"}
    count = _run("git", "-C", challenge, "rev-list", "--count", "HEAD")
    assert count.stdout.strip() == "1", count.stderr
    for rel in _tree(challenge):
        parts = rel.split("/")
        assert not LEAK_DENYLIST.search(rel), rel
        assert parts[-1] not in {"project.json", "fixtures.mjs"} and "variants" not in parts, rel
    manifest = _manifest(out)
    prompt = (challenge / "prompt.md").read_text()
    assert prompt.startswith("Work only inside this directory.") and prompt.endswith(SHARED_END)
    op = manifest["operators"][0]
    assert op["op"] not in prompt and op["site"] not in prompt
    for path in [*(challenge.rglob("*")), out / "manifest.json"]:
        if path.is_file() and ".git" not in path.relative_to(out).parts:
            text = path.read_text()
            assert str(ROOT) not in text and "/home/" not in text, path


def test_l1_seeds_vary_projects_operators_and_names(tmp_path: Path) -> None:
    manifests = []
    for seed in range(1, 13):
        result, out = _gen(tmp_path, 1, seed)
        assert result.returncode == 0, (seed, result.stderr)
        manifests.append(_manifest(out))
    assert len({m["project"] for m in manifests}) >= 2
    assert len({m["operators"][0]["op"] for m in manifests}) >= 2
    assert any(m["renames"] for m in manifests)
    assert len({json.dumps(m["files"], sort_keys=True) for m in manifests}) == 12


L1_PAIRS = {
    ("paginate", "start-offset", "drop-minus-one"),
    ("paginate", "page-count", "ceil-floor"),
    ("stats", "ascending-order", "swap-sub"),
    ("stats", "median-pair", "drop-minus-one"),
    ("stats", "percentile-rank", "ceil-floor"),
    ("stats", "bucket-start", "ceil-floor"),
    ("stats", "range-upper", "lt-le"),
}
L2_PAIRS = {
    ("paginate", "paginate-fn", "stub"),
    ("stats", "median-fn", "stub"),
    ("stats", "percentile-fn", "stub"),
    ("stats", "histogram-fn", "stub"),
    ("stats", "count-in-range-fn", "stub"),
    ("lru-cache", "lru-class", "stub"),
}
L3_PAIRS = {
    ("log-parser", "level-case", "case-fold"),
    ("log-parser", "error-check", "negate"),
    ("currency-invoice", "tax-base", "swap-args"),
    ("shipping-quote", "round-step", "ceil-floor"),
    ("shipping-quote", "billable-max", "max-min"),
    ("shipping-quote", "zone-cost", "plus-minus"),
}
L4_PAIRS = {
    ("currency-invoice", "multi-currency", "remove-feature"),
    ("currency-invoice", "discount", "remove-feature"),
    ("shipping-quote", "weight-units", "remove-feature"),
    ("shipping-quote", "service-levels", "remove-feature"),
}
L5_PAIRS = {
    ("event-pipeline", "compare-tiebreak", "drop-or-fallback"),
    ("event-pipeline", "trim-lower", "case-fold"),
    ("shipping-quote", "round-step", "ceil-floor"),
}
L5_FIND = {  # the find text of each twin site (no identifier in them is renamed)
    "compare-tiebreak": "a.ts - b.ts || compareKeys(eventKey(a), eventKey(b))",
    "trim-lower": "String(s ?? '').trim().toLowerCase()",
}
LIBRARY_PAIRS = {1: L1_PAIRS, 2: L2_PAIRS, 3: L3_PAIRS, 4: L4_PAIRS, 5: L5_PAIRS}
STUB_NAMES = {
    "paginate-fn": "paginate", "median-fn": "median", "percentile-fn": "percentile", "histogram-fn": "histogram",
    "count-in-range-fn": "countInRange", "lru-class": "LRUCache",
}


@pytest.mark.parametrize("layer", [1, 2, 3, 4, 5])
def test_library_check_passes(layer: int, tmp_path: Path) -> None:
    out = _out(tmp_path, "check")
    result = _run("bash", GEN, "--check-library", "--layer", str(layer), "--out", out, env=_node_env(), stdin=subprocess.DEVNULL)
    report = json.loads(result.stdout)
    assert result.returncode == 0, (result.stderr, report)
    assert report["lint"] == []
    assert all(row["valid"] for row in report["rows"]), report["rows"]
    assert {(r["project"], r["site"], r["op"]) for r in report["rows"] if r["layer"] == layer} >= LIBRARY_PAIRS[layer]
    if layer == 4:  # a feature named in its own task statement is not a leak
        assert any(r["site"] == "discount" and r["valid"] for r in report["rows"]), report["rows"]


def _add_site(copy: Path, project: str, site: dict) -> None:
    path = copy / "projects" / project / "project.json"
    data = json.loads(path.read_text())
    data["sites"].append(site)
    path.write_text(json.dumps(data, indent=2) + "\n")


def test_library_check_reports_a_surviving_mutant(tmp_path: Path) -> None:
    copy = _library_copy(tmp_path)
    with (copy / "projects" / "paginate" / "src" / "paginate.js").open("a") as handle:
        handle.write("export const isShortList = (n) => n < 10;\n")
    _add_site(copy, "paginate", {"id": "short-list", "layers": [1], "file": "src/paginate.js", "find": "n < 10", "ops": ["lt-le"]})
    result, report = _check(copy / "gen.mjs", _out(tmp_path, "check"), layer=1)
    assert result.returncode == 1, result.stderr
    rows = [r for r in report["rows"] if r["site"] == "short-list"]
    assert len(rows) == 1 and rows[0]["valid"] is False and "no test fails" in rows[0]["reason"], rows


def test_library_check_reports_a_hanging_mutant(tmp_path: Path) -> None:
    copy = _library_copy(tmp_path)
    project = copy / "projects" / "paginate"
    with (project / "src" / "paginate.js").open("a") as handle:
        handle.write("export function countUp(n) { let i = 0; for (; i < n; i = i + 1); return i; }\n")
    with (project / "test" / "paginate.test.js").open("a") as handle:
        handle.write("import { countUp } from '../src/paginate.js';\ntest('count up', () => assert.equal(countUp(3), 3));\n")
    _add_site(copy, "paginate", {"id": "count-up", "layers": [1], "file": "src/paginate.js", "find": "i = i + 1", "ops": ["plus-minus"]})
    out = _out(tmp_path, "check")
    result, report = _check(copy / "gen.mjs", out, layer=1, env={"ONBOARD_GEN_TEST_TIMEOUT_MS": "5000"}, timeout=60)
    assert result.returncode == 1, result.stderr
    rows = [r for r in report["rows"] if r["site"] == "count-up"]
    assert len(rows) == 1 and rows[0]["valid"] is False and "timed out" in rows[0]["reason"], rows
    leftovers = [line for line in _run("ps", "-eo", "args").stdout.splitlines() if str(out) in line]
    assert leftovers == []
    assert not (out / ".gate").exists()


def test_library_check_rejects_a_shallow_l3_site(tmp_path: Path) -> None:
    """paginate's start-offset sits in a file its test imports directly, so as a layer 3 site it has depth 0."""
    copy = _library_copy(tmp_path)
    path = copy / "projects" / "paginate" / "project.json"
    data = json.loads(path.read_text())
    next(s for s in data["sites"] if s["id"] == "start-offset")["layers"].append(3)
    path.write_text(json.dumps(data, indent=2) + "\n")
    result, report = _check(copy / "gen.mjs", _out(tmp_path, "check"), layer=3)
    assert result.returncode == 1, result.stderr
    bad = [r for r in report["rows"] if not r["valid"]]
    assert [(r["project"], r["site"]) for r in bad] == [("paginate", "start-offset")], bad
    assert "depth" in bad[0]["reason"]
    assert any("start-offset" in entry and "comment" in entry for entry in report["lint"]), report["lint"]


def test_l3_cause_is_away_from_the_failing_tests(tmp_path: Path) -> None:
    projects = set()
    for seed in range(1, 9):
        result, out = _gen(tmp_path, 3, seed, "--with-reference")
        assert result.returncode == 0, (seed, result.stderr)
        manifest = _manifest(out)
        assert manifest["layer"] == 3 and manifest["validity"]["difficulty"]["site_depth"] >= 1, manifest["validity"]
        projects.add(manifest["project"])
        prompt = (out / "challenge" / "prompt.md").read_text()
        assert Path(manifest["operators"][0]["file"]).name not in prompt, (seed, prompt)
        assert prompt.startswith("Work only inside this directory.") and prompt.endswith(SHARED_END)
        assert "Several modules under `src/` are involved" in prompt
        shipped = _verify(out / "challenge")
        assert 0 < shipped["total"] and shipped["pass"] < shipped["total"], seed
        fixed = _verify(_overlay_reference(out, tmp_path))
        assert fixed["pass"] == fixed["total"] > 0, seed
        shutil.rmtree(out)
    assert len(projects) >= 2, projects


def test_library_check_reports_a_leaky_summary(tmp_path: Path) -> None:
    copy = _library_copy(tmp_path)
    path = copy / "projects" / "stats" / "project.json"
    data = json.loads(path.read_text())
    data["summary"] = "This module rounds up with ceil."
    path.write_text(json.dumps(data, indent=2) + "\n")
    result, report = _check(copy / "gen.mjs", _out(tmp_path, "check"), layer=1)
    assert result.returncode == 1, result.stderr
    assert any("stats" in entry and "hint" in entry for entry in report["lint"]), report["lint"]


def test_library_check_reports_a_rename_collision(tmp_path: Path) -> None:
    copy = _library_copy(tmp_path)
    path = copy / "projects" / "paginate" / "project.json"
    data = json.loads(path.read_text())
    data["renames"]["perPage"].append("items")
    path.write_text(json.dumps(data, indent=2) + "\n")
    result, report = _check(copy / "gen.mjs", _out(tmp_path, "check"), layer=1)
    assert result.returncode == 1, result.stderr
    assert any("paginate" in entry and "rename" in entry for entry in report["lint"]), report["lint"]


def test_verify_detects_a_modified_challenge(tmp_path: Path) -> None:
    result, out = _gen(tmp_path, 1, 2)
    assert result.returncode == 0, result.stderr
    clean = _run("bash", GEN, "--verify", out, env=_node_env(), stdin=subprocess.DEVNULL)
    assert clean.returncode == 0 and json.loads(clean.stdout) == {"verified": True, "changed": []}, clean.stderr
    victim = min((out / "challenge" / "test").iterdir())
    with victim.open("a") as handle:
        handle.write("// tampered\n")
    changed = _run("bash", GEN, "--verify", out, env=_node_env(), stdin=subprocess.DEVNULL)
    assert changed.returncode == 1
    report = json.loads(changed.stdout)
    assert report["verified"] is False and report["changed"] == [f"test/{victim.name}"]


def test_l2_stub_ships_the_spec_and_is_solvable(tmp_path: Path) -> None:
    projects = set()
    for seed in range(1, 9):
        result, out = _gen(tmp_path, 2, seed, "--with-reference")
        assert result.returncode == 0, (seed, result.stderr)
        manifest = _manifest(out)
        (entry,) = manifest["operators"]
        assert entry["op"] == "stub" and manifest["layer"] == 2, entry
        projects.add(manifest["project"])
        site = (out / "challenge" / entry["file"]).read_text()
        assert "throw new Error('not implemented')" in site or "// TODO" in site, (seed, site)
        prompt = (out / "challenge" / "prompt.md").read_text()
        canonical = STUB_NAMES[entry["site"]]
        name = manifest["renames"].get(canonical, canonical)
        assert "Spec:" in prompt and f"`{name}" in prompt, (seed, prompt)
        assert prompt.startswith("Work only inside this directory.") and prompt.endswith(SHARED_END)
        shipped = _verify(out / "challenge")
        assert 0 < shipped["total"] and shipped["pass"] < shipped["total"], seed
        fixed = _verify(_overlay_reference(out, tmp_path))
        assert fixed["pass"] == fixed["total"] > 0, seed
        shutil.rmtree(out)
    assert len(projects) >= 2, projects


def _hanging_library(tmp_path: Path) -> Path:
    copy = _library_copy(tmp_path)
    project = copy / "projects" / "paginate"
    with (project / "src" / "paginate.js").open("a") as handle:
        handle.write("export function countUp(n) { let i = 0; for (; i < n; i = i + 1); return i; }\n")
    with (project / "test" / "paginate.test.js").open("a") as handle:
        handle.write("import { countUp } from '../src/paginate.js';\ntest('count up', () => assert.equal(countUp(3), 3));\n")
    _add_site(copy, "paginate", {"id": "count-up", "layers": [1], "file": "src/paginate.js", "find": "i = i + 1", "ops": ["plus-minus"]})
    return copy


def test_sigterm_leaves_no_orphaned_test_process(tmp_path: Path) -> None:
    assert NODE is not None  # guarded by pytestmark
    copy = _hanging_library(tmp_path)
    out = _out(tmp_path, "check")

    def holders() -> dict[int, str]:
        rows = (line.split(None, 1) for line in _run("ps", "-eo", "pid,args").stdout.splitlines()[1:])
        return {int(pid): args for pid, args in rows if str(out) in args and "--check-library" not in args}

    proc = subprocess.Popen(
        [NODE, str(copy / "gen.mjs"), "--check-library", "--layer", "1", "--out", str(out)],
        env=_node_env(ONBOARD_GEN_TEST_TIMEOUT_MS="600000"), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # A normal test run lasts well under a second; the hanging mutant's processes persist.
        stable, last = 0, set[int]()
        for _ in range(150):
            now = set(holders())
            stable, last = (stable + 1 if now and now == last else 0), now
            if stable >= 8:
                break
            time.sleep(0.2)
        assert stable >= 8, "the hanging mutant never showed up"
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=30)
        for _ in range(50):
            if not holders():
                break
            time.sleep(0.1)
        assert holders() == {}
        assert not out.exists(), "the scratch (it holds the reference) and the out dir this run created must be gone"
    finally:
        for pid in set(holders()):
            os.kill(pid, signal.SIGKILL)
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_l4_feature_removal_changes_several_modules(tmp_path: Path) -> None:
    projects = set()
    for seed in range(1, 9):
        result, out = _gen(tmp_path, 4, seed, "--with-reference")
        assert result.returncode == 0, (seed, result.stderr)
        manifest = _manifest(out)
        assert manifest["layer"] == 4 and manifest["operators"][0]["op"] == "remove-feature", manifest["operators"]
        assert manifest["operators"][0]["file"] is None
        assert manifest["validity"]["difficulty"]["changed_src_files"] >= 2, manifest["validity"]
        projects.add(manifest["project"])
        for sub in ("test", "data"):
            assert _tree(out / "challenge" / sub) == _tree(out / "reference" / sub), (seed, sub)
        prompt = (out / "challenge" / "prompt.md").read_text()
        assert prompt.startswith("Work only inside this directory.") and prompt.endswith(SHARED_END)
        assert "Existing callers must keep working" in prompt
        shipped = _verify(out / "challenge")
        assert 0 < shipped["total"] and shipped["pass"] < shipped["total"], seed
        fixed = _verify(_overlay_reference(out, tmp_path))
        assert fixed["pass"] == fixed["total"] > 0, seed
        shutil.rmtree(out)
    assert projects == {"currency-invoice", "shipping-quote"}, projects


def _feature(copy: Path, project: str, feature: str, fn) -> None:
    path = copy / "projects" / project / "project.json"
    data = json.loads(path.read_text())
    fn(next(f for f in data["features"] if f["id"] == feature))
    path.write_text(json.dumps(data, indent=2) + "\n")


def test_library_check_rejects_a_variant_outside_src(tmp_path: Path) -> None:
    copy = _library_copy(tmp_path)
    extra = copy / "projects" / "currency-invoice" / "variants" / "discount" / "test" / "extra.test.js"
    extra.parent.mkdir(parents=True)
    extra.write_text("export {};\n")
    _feature(copy, "currency-invoice", "discount", lambda f: f["replace"].append("test/extra.test.js"))
    result, report = _check(copy / "gen.mjs", _out(tmp_path, "check"), layer=4)
    assert result.returncode == 1, result.stderr
    assert any("discount" in entry and "outside src" in entry for entry in report["lint"]), report["lint"]


@pytest.mark.parametrize(
    "appended",
    [" `const discount = percent(subtotal, invoice.discountBps);`", " See `variants/discount/src/invoice.js`."],
    ids=["reference-line", "denylisted-path"],
)
def test_library_check_rejects_a_feature_prompt_that_leaks_the_solution(appended: str, tmp_path: Path) -> None:
    copy = _library_copy(tmp_path)
    _feature(copy, "currency-invoice", "discount", lambda f: f.update(prompt=f["prompt"] + appended))
    result, report = _check(copy / "gen.mjs", _out(tmp_path, "check"), layer=4)
    assert result.returncode == 1, result.stderr
    rows = [r for r in report["rows"] if r["site"] == "discount"]
    assert len(rows) == 1 and rows[0]["valid"] is False and "leak" in rows[0]["reason"], rows
    assert all(r["valid"] for r in report["rows"] if r["site"] != "discount"), report["rows"]


def test_l5_challenge_is_repo_scale_with_decoys(tmp_path: Path) -> None:
    projects, twins = set(), 0
    for seed in range(1, 7):
        result, out = _gen(tmp_path, 5, seed, "--with-reference")
        assert result.returncode == 0, (seed, result.stderr)
        manifest = _manifest(out)
        difficulty = manifest["validity"]["difficulty"]
        assert manifest["layer"] == 5 and difficulty["src_files"] >= 14 and difficulty["site_depth"] >= 2, difficulty
        assert len(manifest["decoys"]) >= 4, manifest["decoys"]
        for name in manifest["decoys"]:
            assert (out / "challenge" / "src" / "lib" / f"{name}.js").is_file(), name
            assert (out / "challenge" / "test" / f"lib-{name}.test.js").is_file(), name
        projects.add(manifest["project"])
        prompt = (out / "challenge" / "prompt.md").read_text()
        assert Path(manifest["operators"][0]["file"]).name not in prompt, (seed, prompt)
        assert prompt.startswith("Work only inside this directory.") and prompt.endswith(SHARED_END)
        assert "The project has many modules" in prompt
        if manifest["twin"] is not None:
            twins += 1
            both = [(out / side / manifest["twin"]).read_bytes() for side in ("challenge", "reference")]
            assert both[0] == both[1]
            name = Path(manifest["twin"]).stem
            for src in (out / "challenge" / "src").rglob("*.js"):
                assert f"/{name}.js" not in src.read_text(), (seed, src)
            assert L5_FIND[manifest["operators"][0]["site"]] not in both[0].decode(), seed
        shipped = _verify(out / "challenge")
        assert 0 < shipped["total"] and shipped["pass"] < shipped["total"], seed
        fixed = _verify(_overlay_reference(out, tmp_path))
        assert fixed["pass"] == fixed["total"] > 0, seed
        shutil.rmtree(out)
    assert "event-pipeline" in projects, projects
    assert twins >= 1


def test_library_check_rejects_a_shallow_l5_site(tmp_path: Path) -> None:
    """paginate's page-count is imported directly by its test, so as a layer 5 site it has depth 0 (and the project is small)."""
    copy = _library_copy(tmp_path)
    path = copy / "projects" / "paginate" / "project.json"
    data = json.loads(path.read_text())
    next(s for s in data["sites"] if s["id"] == "page-count")["layers"].append(5)
    path.write_text(json.dumps(data, indent=2) + "\n")
    result, report = _check(copy / "gen.mjs", _out(tmp_path, "check"), layer=5)
    assert result.returncode == 1, result.stderr
    bad = [r for r in report["rows"] if not r["valid"]]
    assert [(r["project"], r["site"]) for r in bad] == [("paginate", "page-count")], bad
    assert "layer 5 needs at least 2" in bad[0]["reason"], bad


def test_library_check_lints_a_decoy_that_is_too_long(tmp_path: Path) -> None:
    copy = _library_copy(tmp_path)
    module = copy / "decoys" / "slug" / "src" / "lib" / "slug.js"
    module.write_text(module.read_text() + "// padding\n" * 20)
    result, report = _check(copy / "gen.mjs", _out(tmp_path, "check"), layer=5)
    assert result.returncode == 1, result.stderr
    assert any("decoy slug" in entry and "25 lines" in entry for entry in report["lint"]), report["lint"]


def test_only_known_library_test_files_ship_a_single_test() -> None:
    # effort-run.sh's one-test shortcut: a new one-test file widens it; give it a second test in the same library edit
    files = [*CHALLENGES.glob("projects/*/test/*.test.js"), *CHALLENGES.glob("decoys/*/test/*.test.js")]
    single = sorted(str(p.relative_to(CHALLENGES)) for p in files if len(re.findall(r"^\s*test\(", p.read_text(), re.MULTILINE)) < 2)
    assert single == ["projects/log-parser/test/report.test.js"]
