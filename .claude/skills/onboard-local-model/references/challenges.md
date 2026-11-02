# Generated challenges

Loaded only to author, debug or calibrate the challenge library, or to investigate a `leak_suspect` row; a normal ladder run
does not need it.

## Generator

`bash $SK/scripts/gen-challenge.sh` is the only entry point (node ESM, no dependencies, no clock, no environment-dependent output). One challenge is a library project plus a seed. Run it per `bash $SK/scripts/gen-challenge.sh --help`; open the files below only when authoring or debugging the library.

| Path under `$SK/challenges/` | Holds |
|---|---|
| `gen.mjs` | the CLI: generate, `--check-library`, `--verify`, `--generator-id` |
| `lib/rng.mjs`, `lib/operators.mjs`, `lib/instantiate.mjs`, `lib/gate.mjs`, `lib/prompts.mjs` | seeded draws, defect operators, copy and rename, the validity gate, prompts |
| `lib/cases.test.template` | copied into a challenge as `test/cases.test.js` (fixture-driven projects) |
| `projects/<id>/` | one correct reference project: `project.json`, optional `fixtures.mjs`, optional `variants/<feature>/src/...`, plus `package.json`, `src/`, `test/` |
| `decoys/<name>/` | one decoy module and its test (layer 5 only) |

The command takes no input file: challenges come only from the library plus a seed. Seeds are unsigned 32-bit integers; every random
decision (`project`, `item`, `op`, `renames`, `fixtures`, `decoys`, `twin`) draws from a generator seeded by the attempt, the label and
the seed, so the same seed gives byte-identical output. `effort-run.sh` draws fresh seeds from `/dev/urandom` unless `--seeds` is given
and writes them to `seeds.json`: `{schema, generator, per_layer, layers: {"1": [seed, ...], ...}}`. A seed set from another generator id is refused.

What a seed changes: the project, the site or feature, the operator, identifier renames (always applied, so a model cannot recall a
published fix by name), seeded fixture cases (`paginate`, `stats`, `log-parser`) and, at layer 5, the decoy selection and an optional
twin decoy. A manifest holds ids, counts and hashes (`generator`, `id` = `l<layer>-s<seed>`, `project`, `operators`, `renames`, `decoys`,
`twin`, `prompt_sha256`, `expected_failing`, `files`, `reference_src`, `validity`), never source text.

Prompts start with `Work only inside this directory.` and end with the shared sentence (`Do not edit the tests or data;
reply with one sentence naming the root cause or the files you changed.`). L1 names the cause file, L2 gives a spec for one stub, L3
and L5 never name the file that holds the cause, L4 states the feature.

## Library

Seven correct reference projects (several serve more than one layer) and a pool of ten decoys.

| Project | Layers | Defects | Notes |
|---|---|---|---|
| `paginate` | 1, 2 | `start-offset`, `page-count`; stub `paginate` | seeded fixtures |
| `stats` | 1, 2 | five one-line sites (order, median pair, percentile rank, bucket start, range bound); four stubs | seeded fixtures |
| `lru-cache` | 2 | stub class `LRUCache` | |
| `log-parser` | 3 | `level-case`, `error-check`, both away from the failing tests | seeded data file `data/run.log` (the shipped reference test reads `data/app.log`) |
| `currency-invoice` | 3, 4 | `tax-base`; features `multi-currency`, `discount` | feature prompts are the task statement |
| `shipping-quote` | 3, 4, 5 | `round-step`, `billable-max`, `zone-cost`; features `weight-units`, `service-levels` | |
| `event-pipeline` | 5 | `compare-tiebreak`, `trim-lower` (both measured deep) | repo scale, twin decoy |

Decoys (`slug`, `chunk`, `backoff`, `ttl-cache`, `csv-row`, `duration`, `percent-of`, `ring-buffer`, `dedupe-list`, `window-avg`): each at
most 25 lines with 2-3 passing tests. A layer 5 challenge adds enough of them to reach at least 14 `src/` files.

## Operators

An operator changes the first match of a site's `find` text (or turns a stub or a feature into the defect). Prompts never contain the
operator id nor any hint word of the operator in use.

Ids, their change and the hint words kept out of prompts: `challenges/lib/operators.mjs` (`OPERATORS`).

## Validity gate

Every attempt is built and checked in `DIR/.gate/` (removed in a `finally`). A challenge is released only when:

- V1 the reference passes every test file (pass equals total, more than 0, no timeout);
- V2 the challenge fails at least one test file, no timeout;
- V3 the file set is the reference's (minus feature deletions), files outside `src/` are byte-identical, and the differing `src/` files
  are exactly the site file (mutation, stub) or the feature's replaced and deleted files;
- V4 the layer invariant holds (below);
- V5 the challenge is leak-free (below);
- V6 the challenge is self-contained (below);
- V7 every fixture `expected` value survives a JSON round trip.

V1, V3, V5 and V6 failures are library errors (exit 1, no retry). V2, V4 and V7 failures move to the next attempt, at most 8, then exit 1.

Layer invariants (`site_depth` = shortest import distance from a failing test file to the site file, minus 1, so a directly imported
file has depth 0): L1 and L2 depth 0; L3 depth at least 1; L4 at least 2 changed `src/` files; L5 depth at least 2, at least 14 `src/`
files and at least 4 decoys. The manifest records `validity.difficulty` = `{site_depth, changed_src_files, src_files}`; a feature
challenge (L4) has `site_depth` null, because no single site file exists, so never read it as a number.

Leak rules (V5). The challenge's top level holds only `prompt.md`, `package.json`, `src/`, `test/` and optional `data/`, with no
copied metadata. No path segment may match the denylist `solution|reference|mutant|mutation|answer|fix|bug|defect|manifest|seed|oracle|variant|decoy|twin|hint|stub`.
The match is a case-insensitive substring match on the whole relative path, not a word match: `prefix`, `suffix` and `debug` trip it.
The same list screens rename candidates and decoy names. The prompt contains no reference-only source line (20 or more characters, in
the reference and in no challenge file), no path-like backtick span naming a generator file (`variants/...`, `reference/...`,
`manifest.json`) and not the operator id. A mutation or stub prompt also omits the site id, the renamed `find` text and its mutated form,
the operator's hint words, and (L3, L5) the cause file's basename. A feature prompt (L4) may name the feature and the files it
changes, because that is the task statement.

Self-contained (V6): every relative import resolves inside the challenge, bare imports come only from `node:test`, `node:assert`,
`node:assert/strict`, `node:fs`, `node:path` and `node:url`, and no file contains `/home/`, `/var/home/`, `/root/`, `/tmp/` or the
generator's own directory.

Timeouts: each test file runs as its own process group with a timeout of `ONBOARD_GEN_TEST_TIMEOUT_MS` (default 20000). A timeout in
the reference is a library error, in the challenge an invalid attempt, and the whole process group is killed. Inside a file every test
also gets `--test-timeout` of half that limit and the run uses `--test-force-exit`, so a hanging async test is cancelled; the runner
counts a cancelled test as failed (`# fail` + `# cancelled`), reads only the last summary line of the child's stdout (a solution that prints `pass 100` cannot spoof it) and gives a timed-out file no passes. `gen-challenge.sh --score DIR` scores the agent's solution with the same
runner and limits (a timed-out file adds one failure and is listed in `timed_out`). The variable only
affects the failure path, never the bytes of a valid challenge. The agent's own limit is `effort-run.sh --timeout`, which defaults to
the bounded runner cap (300 s today, `DEFAULT_RUNTIME_SECONDS` in `pylib/agent_runner.py`; a larger value exits 2).

## Difficulty

Calibration procedure (after any library change or when the ladder looks flat):

1. `PI_CODING_AGENT_DIR=$HOME/.pi/agent bash $SK/scripts/effort-run.sh --model router-env/llama-cpp/<baseline> --out tmp/model-scout/<stamp>/calibration --per-layer 5 --efforts low,high`
   on the baseline, on the router profile (upper bound 5 x 5 x 2 x T; `references/scoring.md`, Running a lane) and, under G4, the same `--seeds` (`tmp/model-scout/<stamp>/calibration/seeds.json`)
   on one anchor with `--out tmp/model-scout/<stamp>/calibration-<anchor>` (`<anchor>`: its model id with `/` as `-`).
   The baseline lane needs llm-server serving it: `bash $SK/scripts/preflight.sh` first, then
   `references/scoring.md`, Serving the lane's model (a gateway-only host stops there), and `make stop-llama` after that lane when that rule started it.
2. Expect the solve rate per layer to be non-increasing from L1 to L5 for each model.
3. Read `by_operator` from `score.sh`: an operator whose solve rate sits 20 points or more off its layer's mean moves layer, by editing
   its site's `layers` in `project.json`.
4. Every library edit changes the generator id, so the calibration seeds are retired with it.

## Anti-contamination

- The reference solution is never placed next to a challenge or in the run directory; the library itself lives in the skill directory, which the
  `leak_suspect` tripwire watches. `leak_suspect` rows never count, and a library edit retires every older
  seed set (the generator id changes).

The leak flags are a tripwire, not a sandbox. The agent runs as the owner, so nothing here stops a read or a write.

- What is guarded: before each run `effort-run.sh` checks the pristine challenge against its manifest hashes, and again after the
  agent (a change aborts the run with exit 1 and flags the row). A row is `leak_suspect` when its transcript names the skill
  directory, the run's `challenges` directory or `manifest.json`, or when a guarded file changed during the run (`outside_writes`
  lists up to 20 paths, relative to the guard root). The guard root is `ONBOARD_GUARD_ROOT`, default the repo; it must be a git work
  tree. Guarded files are the git-visible files (tracked, plus untracked and not ignored) under
  `.claude/skills/onboard-local-model/scripts/` and `challenges/` and `tools/lib.sh`: what generates and scores a challenge.
- What is not detected: reads or writes of sibling run directories under the out directory, reads that never name the paths above,
  writes elsewhere in the guard root that git does not list (ignored files), and anything outside the guard root. Git-visible writes
  elsewhere in the guard root never flag a row; `other_writes` records them.
- A live benchmark needs no other session editing the guarded paths: such an edit flags the row. Run with
  no other writer, and re-run a flagged lane on the same seeds into `<out>-r2` (the re-run folder of `references/scoring.md`, Running a lane): pass `--seeds <the seeds.json>`, because the
  generator refuses a non-empty challenge directory and a stale `challenges/` would survive in the old out directory.

## Authoring a project

Add `challenges/projects/<id>/` with `project.json` (`id`, `summary`, `renames`, `sites`, `features`), `package.json` (exactly
`{"type":"module","engines":{"node":">=20"}}`), `src/` and `test/`. Then run `bash $SK/scripts/gen-challenge.sh --check-library --out tmp/lib-check`
and fix every `lint` entry and invalid row; the check also proves every site by mutation. The lint enforces:

- only the allowed top-level entries; `test/` holds only `*.test.js`; no `src/` name that `node --test` discovers as a test;
- every path passes the leak denylist, and no core project has `src/lib/` (that is the decoy directory);
- every `find` text occurs exactly once and every listed operator changes it; every `stub` is found by the stub rule;
- no rename candidate already occurs as a word in its project; the `summary` holds no hint word of an operator the project uses;
- a site of layer 3 or higher has no `//` comment on its line or the two lines above;
- feature variants exist and lie under `src/`; twin names and files collide with nothing.

A new project's tests must fail on every declared defect (a mutant that survives, or that hangs, is reported and never ships).
