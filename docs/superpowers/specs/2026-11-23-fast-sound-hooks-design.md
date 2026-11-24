# Fast, sound git hooks and an offline, capped test suite

Date: 2026-11-23. Status: approved direction (owner), details decided here. Plan:
`docs/superpowers/plans/2026-11-23-fast-sound-hooks.md`.

## Problem

`.pre-commit-config.yaml` runs `bash tools/validate.sh` on every commit (`always_run`, about 11 s even for a docs-only
commit: shellcheck of every script 6.4 s, pyright 4.8 s, ruff 0.04 s, measured 2026-11-23) and the whole suite on every
push (`tools/test.sh` = `uv run pytest tests/ -n auto -v`: all 24 cores, 75-87 s wall, about 1,700 CPU-seconds, a
`-v` line per test). Tests can reach the network: run inside a loopback-only namespace, the onboard-local-model
`add-entry` tests fail with "Failed to fetch https://pypi.org/simple/pyyaml/" because their env builders
(`_qh_env` and siblings) pass `HOME=tmp_path` without `UV_CACHE_DIR`, so the real `uv run llmenv.py migrate-config`
starts from an empty cache and downloads pyyaml on every normal run. One test depends on host free disk, and nothing
checks that a run leaves no files, scratch dirs or Podman objects behind.

## Requirements

- R1 `make test` (`tools/test.sh` over `tests/`) is the only command whose purpose is the full suite; the pre-push
  full-suite hook goes away. A hook runs every test file only as a fallback (a hub at push, a `full:` file,
  `SELECT_ALL=1`, see D3), and at commit even a fallback deselects slow tests (D4). The hooks are a faster gate, not a
  substitute for the AGENTS.md rule to run `make test` after a `.py` edit.
- R2 Commit stage: shellcheck on staged `.sh` files (same globs as `tools/validate.sh`); ruff on staged `.py` files
  inside `validate.sh`'s ruff scope (`llmenv.py`, `pylib/`, `tests/`; the four tracked `.py` files outside it stay
  unlinted, as today); pyright (whole project) when a `.py`, `pyproject.toml` or `uv.lock` is
  added, modified, deleted or renamed in the index (D7); and the tests selected from the staged diff (depth 1).
- R3 Push stage: the full `tools/validate.sh`, then for a fast-forward of the checked-out branch the tests selected
  from the FROM..TO diff (depth 2), for a new branch the full suite (D8). Two no-op inputs pass without any check,
  even with a dirty index or worktree: no ref line, and a lone deletion line (all-zero local sha). Every other shape
  is refused.
- R4 CPU cap on every pytest run of the default suite (`make test` and both hooks):
  `workers = max(1, floor(nproc * pct / 100))`. `pct` comes from `LLM_ENV_TEST_CPU_PCT` (default 40) and must match
  `^[1-9][0-9]{0,2}$` with a value of 100 or less (no sign, no leading zero, so `010` is rejected, not read as octal);
  anything else exits 2. pytest runs under `nice -n 10` (the caller's niceness plus 10, capped at 19 by the kernel)
  and with no `-v`.
- R5 No test in the default suite downloads anything (images, model files, PyPI packages), enforced (D5); pyyaml
  resolves from the uv cache. Opt-in live suites are outside this contract and never run under `tools/test.sh`, even
  when the caller exported their switch (D5); their documented direct `uv run pytest <file> -q` commands stay.
- R6 A run fails when it leaves new entries anywhere under repo `tmp/` (nested ones included), in its own scratch dir
  (except pytest's temp root inside it), or new Podman containers, volumes or images; no process of the run outlives it.
- R7 Outcomes do not depend on host free disk: every test that reaches `fetch-gguf.sh`'s free-space check
  (`fetch-gguf.sh:64-67`, real `df` against size - `.part` + 1 GiB) sees a deterministic `df` stub, including the
  `_fetch` and local-file paths, not only `_qh_env`. One test pins the boundary: free = need - 1 fails, = need passes.
  These stubs land before any full-suite run (plan T2).
- R8 Shape-duplicate tests merge into `@pytest.mark.parametrize` with the same collected case count: the 32 groups
  (69 tests) the plan lists from a 2026-11-23 scan, so 37 fewer test definitions. A fake repeated within a file
  (`_reply` six times in `tests/test_cli.py`) becomes one helper; `_fake_login_ok` already has one copy in each of its
  two files, and merging those needs a shared module (optional, Non-goals).
- R9 Targets: doc-only commit < 5 s; typical commit < 15 s; typical push < 60 s. Representative cases, timed through
  the installed hooks on a scratch branch and recorded in the plan: a one-line `README.md` commit; a commit of one
  `pylib` module plus its test; a push of two such commits. Stated exceptions: a script or doc commit that selects the
  big shell test file, and a hub push (both in Limits).
- R10 The only bypasses are `git commit|push --no-verify` and, at commit, `SKIP=<hook-id>`, documented in `AGENTS.md`.
  Inherited pytest settings are not a bypass: the runner unsets `PYTEST_ADDOPTS` and sets `PYTEST_DEBUG_TEMPROOT` to
  its own scratch dir (D6), and the hooks pass pytest's 5 (nothing collected) through as a failure, because the
  always-run test is in every selection.

## Verified tool behaviour (pre-commit 4.6.2, read from its installed source)

- Commit stage: files are `git diff --staged --name-only --diff-filter=ACMRTUXB` (no deletions); unstaged changes are
  stashed. A hook with no matching files is skipped unless `always_run: true`.
- Filenames are chunked by `xargs.partition`, at most `max(4, ceil(n / jobs))` files and the platform length limit
  per call; `jobs` is 1 for a `require_serial` hook (split by length only), else `target_concurrency()`. Only
  shellcheck and ruff take filenames and they check each file on its own (D7), so chunking cannot change a verdict.
  The pyright and selection hooks use `pass_filenames: false` and read git themselves.
- Pre-push: pre-commit consumes git's ref lines on stdin and exports only the first eligible one
  (`PRE_COMMIT_FROM_REF`/`PRE_COMMIT_TO_REF`); its hooks never see the others, and the pushed commit is never checked
  out. So no pre-commit hook can refuse a multi-ref push, which is why D8 takes pre-push out of pre-commit.

## Decisions

- D1 Selector: `uv run llmenv.py select-tests --stage commit|push`, logic in `pylib/selection.py`. It takes no path
  arguments and trusts no caller's filenames: it reads every changed path from
  `git diff --name-status -z --no-renames`, of the index against `HEAD` at commit and of `FROM TO` at push (set by D8,
  or by G2). Every git path list in this design is read NUL-delimited (`-z`: status and path are separate fields), so
  no path is quoted or split on whitespace; a test pins a spaced and a non-ASCII deletion. `--no-renames` turns a rename
  into a deletion plus an addition, so both sides count, as do deletions. A test file is selected when it names a
  reached file. Names: the repo path, the basename, the dotted module, or an import line naming the module stem.
  Reached files: the changed paths (test files included), then `depth` hops of non-hub files that name a reached
  file, plus the CLI edge (D2). So `tests/test_capacity.py`, which imports `write_test_config` and `run` from
  `tests/test_cli.py`, is selected when `test_cli.py` changes; a test pins this edge. Also selected: changed test
  files; prefix owners for tests that glob a directory (the skill trees, fixtures named by f-strings); and always
  `tests/test_no_phoenix.py` (it scans the whole tree). Markdown files are leaves: no code reads a repo `.md` at
  runtime (checked 2026-11-23), so they select only their direct namers. Granularity is the test file. The commit
  selection hook is `always_run` and D8 always calls the selector, so a deletion-only change still selects.
  Soundness limit: the guarantee covers test files within `depth` hops (1 at commit, 2 at push); ownership is
  defined once, in G1. A fixture test pins this: chain `a <- b <- c <- e <- test_e` plus `test_a`
  naming `a`; push depth 2 reaches `b` and `c` but not `e`, so the selection for `a` holds `test_a`, not `test_e` (a
  `test_d` naming `c` would be selected). `make test` and G4 cover what lies beyond, and G3 measures the gap.
- D2 CLI edge: a `pylib` module reaches the files that call a `llmenv` subcommand whose handler in `llmenv.py` uses it
  (found from `llmenv.py`'s AST, plus the module's own `add_parser` names). A module that `llmenv.py` imports but no
  subcommand can be traced to counts as `llmenv.py` itself (a hub).
- D3 Hubs: `tests/conftest.py`, `pyproject.toml`, `uv.lock`, `tools/lib.sh`, `llmenv.py`, `pylib/config.py`,
  `Makefile`, `.pre-commit-config.yaml`, plus the selector's own inputs `pylib/selection.py`, `tools/test.sh`,
  `tools/test-changed.sh`, `tools/pre-push.sh` and `tests/selection-exempt.txt`. A file with no owner and no
  exemption fails G1 (which runs beside selection); a direct `select-tests` call maps it to the full suite. Per changed path, first
  match wins: (1) `SELECT_ALL=1`, a `full:` exemption (even when the file also has owners) or a hub at push: the full
  suite; (2) a hub at commit, root `tests/conftest.py` included: its direct namers only (the push of that commit runs
  the full suite); (3) any other changed or reached `conftest.py`: every test under its directory; (4) D1's naming.
  A test pins rules 1-3 with a root-conftest commit and an owned `full:` file.
- D4 Slow tests: a test whose call phase takes >= 5 s in `--durations=0` at the capped worker count gets
  `@pytest.mark.slow`. The commit stage deselects slow tests; push and `make test` run them. Owners are counted over
  the tests the push stage runs, so a slow marker never hides a file from the gate.
- D5 Offline: `tools/test.sh` runs pytest in network and PID namespaces that keep the caller's uid
  (`unshare -rn --pid --fork --kill-child --mount-proc` brings `lo` up, then a nested
  `unshare --user --map-user=<uid> --map-group=<gid>`), verified here (uid 1000 kept, mode-000 files unreadable,
  loopback works, pypi.org unreachable, warm `uv run llmenv.py` works, the chain nests, and with real `uv` as namespace
  PID 1 a group TERM reaches its child, which tears down, while a new-session grandchild dies with the chain).
  The runner first probes the same chain with `true`; a failed probe exits 69 with "network namespace unavailable".
  Then, inside the chain and before pytest starts, it runs `uv run --offline llmenv.py --help`; on failure (a cold
  cache: uv exits 1, "pyyaml was not found in the cache", verified) it exits 4 with "uv cannot resolve llmenv.py
  offline: run make dev-setup once with network", so the check never depends on collection or xdist. Otherwise its
  status is pytest's own (0-5), except as D6 says for a leak or the timeout (124); tests pin 3 and 5 passing through and the cold-cache 4
  with real `uv`. `tests/conftest.py` exports `UV_OFFLINE=1` and `HF_HUB_OFFLINE=1` at import time.
  `skill_helpers.run` passes `UV_CACHE_DIR`/`UV_OFFLINE` into explicit envs; `make dev-setup` warms the script
  environment once. The runner unsets the four opt-in switches
  (`LLM_ENV_UPDATE_PROOF`, `LLM_ENV_UPDATE_CHARACTERIZE`, `LLM_ENV_LIVE_VM`, and `LLM_ENV_MUTATION_CHECK` added by
  plan T13: every `skipif(os.environ...)` gate in `tests/`), so the default suite runs no real Podman
  (`ProofBackend`, `IsolatedBackend`) and no mutation run whatever the caller exported; a test exports all four and
  expects the skips.
- D6 Leak check in `tools/test.sh`, outside the namespace, once per run. Each run gets its own `TMPDIR`
  (`/tmp/pytest-of-$USER/llm-env-run.XXXXXX`, so `pylib/observe_fs.py`'s `pytest_roots` still sees it), printed as
  `run dir: <path>` on stderr and removed by a trap; `PYTEST_DEBUG_TEMPROOT` (which pytest prefers to `TMPDIR`) is
  `<run dir>/pytest`, which the runner creates (pytest makes only `pytest-of-<user>` inside it, without parents).
  Any other entry left in the run dir fails the run (no name-prefix exemption; the one exception is an empty file named
  `uv-<16 hex>.lock`, which uv 0.11 leaves in `TMPDIR` on every `uv run`), as do new entries
  anywhere under repo `tmp/` (a recursive before-and-after list of exact relative paths) and new Podman objects: a
  read-only before-and-after diff of container
  (`ps -a -q --no-trunc`), volume (`volume ls -q`) and image (`images -q --no-trunc`) IDs. An incomplete inventory
  fails the run with one `inventory: <what>` line and status 1: any `find` error under `tmp/` (only a missing `tmp/`
  is empty) or, with `podman` on PATH, a failing listing; without `podman` the Podman part is skipped with one line.
  A leak turns pytest's 0 or 5 into 1, keeps any other nonzero status, and lists the leaked paths or IDs. The INT and
  TERM traps are installed before the probe and the launch. On either, the runner sends TERM to the chain's process
  group, waits up to 5 s, sends KILL, and exits 130 or 143, removing its scratch dir. When the chain ends, the kernel
  kills every process left in its PID namespace (D5), including ones in another session such as
  `skill_helpers.run`'s; a test signals only the runner's PID while its stub has a `setsid` child, then finds no
  descendant alive and its printed run dir gone.
  Timeout (14d0e21, 6898907): `LLM_ENV_TEST_TIMEOUT_SECONDS` (default 1800; a positive integer without leading zeros,
  else exit 2 before the probe) bounds the chain from its launch, offline uv check included. On expiry the runner
  prints `timeout: test run exceeded <N> s`, stops the chain through the same stop path (TERM to the group, KILL after
  5 s, so `setsid` descendants are reaped), skips the post-run inventory and leak check, removes its run and inventory
  dirs, and exits 124. The probe, the Podman inventory snapshots, pyright and `validate.sh` are outside the bound. G2
  classifies the `timeout:` marker as a harness error.
- D7 Validate split: shellcheck (without `-x`) and ruff check each file on its own, so per-file runs at commit catch
  what the full run catches for those files. The pyright hook is `always_run` and reads the index itself
  (`git diff --staged --name-only -z --no-renames`, all statuses), because a filename-filtered hook never sees a
  staged deletion; it runs pyright on the whole project when any listed path is a `.py`, `pyproject.toml` or
  `uv.lock`, and tests pin a deletion-only and a rename-away commit of each (the `.py` one spaced and non-ASCII).
  The ruff hook's file pattern is R2's scope; a test pins that it accepts `pylib/x.py` and skips
  `scripts/system-one-cache-check.py`.
- D8 Two push shapes and two no-op inputs (no ref line, a lone deletion line); every other shape fails closed. Pre-push is a tracked script, `tools/pre-push.sh`, that
  `make dev-setup` links as `.git/hooks/pre-push`; pre-commit keeps only the commit stage (`pre-commit install
  --hook-type pre-commit`; the `test` pre-push entry leaves `.pre-commit-config.yaml`). Design call, not an owner
  decision: only that seat sees every ref line. The script is tracked with mode 100755 (git silently skips a
  non-executable hook). `make dev-setup` exits 1 with one line, before touching any hook, when `core.hooksPath` is
  set (unsupported; pre-commit refuses it too). Because a leftover pre-commit `pre-push` would pass every push, the
  commit-stage selection hook fails with "run make dev-setup" when `.git/hooks/pre-push` does not resolve to the
  script or is not executable; a real `git push` to a scratch bare remote proves git runs the linked script.
  The script accepts exactly one ref line whose local ref is the checked-out branch (or the word `HEAD`, which
  `git push origin HEAD` sends), whose local sha is `HEAD`, whose
  remote ref is that same `refs/heads/<branch>` (no tag or other destination), with a clean index and a clean
  worktree, each checked on its own (`git diff --cached --quiet`, `git diff --quiet`: a staged edit the worktree
  reverts passes `git diff HEAD`). A remote sha that is a local ancestor of `HEAD` (`git merge-base --is-ancestor`)
  is a fast-forward: FROM is that sha, which git took from the remote. An all-zero remote sha is a new branch: no
  base is trusted, so it runs the full suite (fail closed; 3 pushes in this repo's whole reflog). Empty ref input and
  a lone deletion line (all-zero local sha) exit 0 first, without any check, even with a dirty index or worktree (a
  test pins the dirty deletion). A dirty index or worktree exits 1 before any check with "push refused: the index or worktree is dirty: commit
  or stash your changes, then push again", without the `--no-verify` advice: `make test` tests the worktree, not the
  commit being pushed. Anything else (several refs, a tag or other destination, a rewind or force-push, another
  branch) exits 1 before any check, with one line:
  "push shape not supported by the hook: run make test, then git push --no-verify". Refusing beats running the full
  suite, because the full suite would still test only the checked-out tree and so could not vouch for a second ref, a
  rewound remote or another branch. An accepted push runs `tools/validate.sh`, then `tools/test-changed.sh --stage
  push` with FROM and TO exported (or `tools/test.sh` for a new branch), stopping at the first failure. Tests pin each
  refused shape (two refs, a tag destination, a rewind, another branch, staged and unstaged edits that cancel), one
  fast-forward, one new branch, and `core.hooksPath`.

## Rejected alternatives

- pytest-testmon: sees only Python lines run in-process; most tests drive bash in subprocesses.
- pytest-impacted / pytest-git-diff: Python import graph only; blind to scripts, dashboards, compose, docs.
- pytest-picked: selects only modified test files.
- conftest socket guard as the offline enforcement: blind to `bash`, `curl`, `uv`, `podman` and `node` children.
- HTTP proxy env as the guard: `node` fetch and `--noproxy` callers ignore it.
- Function-level selection: module-level helpers name most scripts in the big shell test file (4,573 of its lines),
  so it seldom narrows. The file split (deferred) is the better lever.

## Soundness guards

- G1 Ownership lint: every tracked file outside `tests/` (and every test helper) has an owner at commit depth (1), is
  a hub, or matches `tests/selection-exempt.txt`. This is the one definition of "owned": a file whose only owner is
  two hops away fails the lint (a test pins it) until it gains a depth-1 owner or a `full:` line, so at commit it
  selects the always-run test plus its direct namers, and push depth 2 is the backstop. A plain line (`<glob>  #
  <reason>`) waives the ownership requirement and nothing else: it does not suppress owners the file already has (a
  change still selects them at the stage's depth, so an unowned exempt file selects only the always-run test), and it
  may never match code (`.sh`/`.py`/`.mjs`/`.js` or an exec bit). A `full: <glob>  # <reason>` line accepts an
  executable file that no test can own (the `.test.mjs` files, plan O1); a change to it selects the full suite at
  both stages even when the file has owners (D3). Stale or reasonless lines fail. Opt-in files
  (`test_omniroute_update_proof.py`, `test_omniroute_characterize.py`) never count as owners. The lint runs in
  `tools/test-changed.sh` on every invocation, so on every commit and every fast-forward (range) push, over the whole
  tree: it starts in the background, select-tests and pytest run meanwhile, and the script then waits for it and prints
  its output after theirs (never gated or skipped). A lint failure fails the hook: the tests' status when that is
  nonzero, else 1. A new-branch push runs `tools/test.sh` directly, without the lint (D8). Tests pin that removing the last match of an exemption and removing a
  source's last owner each fail the commit and push hooks whether the tests pass or fail, and that a failed lint does
  not stop the tests. Its cost against R9 is measured by the plan; in parallel it adds only what it outlasts the tests
  by (a docs-only commit: about 7.5 s serial, 6.5-7 s parallel, 2026-11-24).
- G2 Mutation spot-check `tools/selection-mutation-check.sh`. It works in an allowlisted scratch copy of the index
  (`git checkout-index`), so the checker and the regression test being verified are in it before they are committed.
  First a clean control pass, which also runs the literal-table integrity test (every fault's target line exists).
  Then ten one-line faults across kinds (pylib via the CLI, `.sh`, dashboard JSON, compose key, a pinned doc
  sentence, Makefile, `tools/lib.sh`, `tests/conftest.py`, a skill script, a pylib render), each committed in a fresh
  copy with the integrity test deselected. A fault is caught only when the push-selected subset exits 1 with at least
  one failed test, no setup or teardown error and no harness marker: one rule, any runner marker on stderr (`leak:`,
  `inventory:`, `timeout:`, a namespace or offline-check failure; a failed post-run inventory exits 1 beside `1 failed`) or an
  xdist crash marker on stdout (`node down:`, `crashed while running`, ...; xdist reports the crashed worker's test as
  failed) makes it a harness error, pinned by one table-driven test. Any other outcome (2-5, or a failure mixed with
  any of these) is a harness error too: exit 2. If the subset passes, the full
  suite runs: still passing means the fault is invalid, failing means a selection hole (exit 1). Fault 1 is a `pylib`
  line that the module's direct-naming tests do not catch (so not the 512 MiB constant `tests/test_budget.py` pins)
  and that only a script test reached through `llmenv <subcommand>` catches. A regression test drops the CLI edge
  from the scratch copy's `pylib/selection.py` and expects exit 1 naming fault 1.
- G3 Replay: for the last 25 non-merge commits (paths from `git diff-tree -z`), a push selection that is not full
  contains every test file the oracle requires for each changed path: the test files under its directory for a
  non-root `conftest.py` (D3 rule 3, which deliberately drops its basename namers), else the test files that
  `git grep -z -l -w -F <basename>` finds (exit 1, no match, is the empty set; any other nonzero fails). Hubs and
  `full:` paths make the selection full (rule 1). The oracle shares no code with the selector. It also prints, as a
  measurement only, the test files depth 3 would add (D1's soundness gap).
- G4 Nightly full run (deferrable): a user timer runs `make test` daily and leaves a result line for `make status`.

## Host-dependent tests (found 2026-11-23)

The quant-headroom fetch test needs about 7.4 GB free in `/tmp` (fixed by R7). The `add-entry` tests download pyyaml
(fixed by D5). These skip when a tool is missing: node (onboard-local-model skill tests, `test_challenges.py`,
`test_podman_state_check.py`, two in `test_remote_setup.py`), curl (one skill test) and systemd-analyze
(`test_observe_stack.py`). One shell test skips as root (mode-000 read), which is why the namespace keeps the uid.
Real `yq`/`jq` are required with no skip. The opt-in live suites (D5's four switches) run only by direct
`uv run pytest <file> -q` with their switch set (README lists the two updater ones), outside R4-R5.

The isolated-store sweep test (`test_the_sweep_reaps_only_backend_roots_whose_owner_is_gone`, now in
`tests/test_omniroute_derive_candidate.py`) runs real Podman through `IsolatedBackend` whenever Podman is installed.
It moves into `tests/test_omniroute_characterize.py`, behind `LLM_ENV_UPDATE_CHARACTERIZE=1` (the existing opt-in
lane for that backend, README "How the updater itself is verified"), before any full-suite run in the owner's
session. The lane's run condition is the AGENTS.md Podman isolation rule, never the owner's session (a design call).
Open for the owner, predating this design and not changed here: `IsolatedBackend` does not isolate `--tmpdir` or
`XDG_RUNTIME_DIR`, so today the lane meets that rule only under a separate user.

## Limits (measured, stated rather than hidden)

Measured over 51 test files and 189 tracked non-test sources (excluding `docs/superpowers/`, handoffs and the
challenge and eval trees). Direct naming: 140 sources are named by 1-3 test files, 14 by 4-10, 4 by more than 10, and
31 by none. Selected test files at depth 1: median 3, p90 9, max 20; at depth 2: median 5, p90 17, max 28.

The big shell test file (268 CPU-s) is in most script selections, so those commits take about 30 s at 9 workers until
it is split, and so do edits to `AGENTS.md` or `QUICK_START.md`, which it names. A hub at push runs the full suite
(about 190 s at 9 workers), as does a new-branch push. Both hooks test the worktree, so an untracked file (for example
a module the commit deletes, left in place) can make them pass, as with the current hooks. Dynamic file names that no test spells out are invisible: G1 forces a prefix owner or the
full suite for them, and G4 is the backstop. Reach beyond push depth is not guaranteed (D1). A push of any shape but
D8's needs a manual `make test` and `--no-verify` (a dirty tree is committed or stashed first instead). A lone
deletion push passes even with a dirty index or worktree, by design (plan T8 item 1: deleting a remote branch tests
nothing; a test pins it). Deleting a
file together with the last test mention of it leaves "no owner", so that commit runs the full suite (about 2
minutes); selecting only the always-run test would be faster but less sound. A test that starts processes through `systemd-run --user` runs
them outside the namespace, so it is outside the offline guarantee until it stubs `systemd-run`. Inside a run,
`/proc` shows only the run's own processes. Checked 2026-11-23: tests and the scripts they run read `/proc` only for
their own processes or for host-wide files such as `/proc/meminfo`, which stay readable; the first full run under
the namespaces (plan T3) confirms it.
Direct `uv run pytest` gets the offline env but not the namespaces, the CPU cap or the leak check.
File names containing a newline are not supported by the newline-delimited hand-offs after the git reads: the selected
test paths (`jq -r` + `mapfile -t` in `tools/test-changed.sh`) and the repo-`tmp/` inventory in `tools/test.sh`; the
repo has no such names and the owner triaged this case out.

## Non-goals

No change to what `make validate` checks. No CI. No function-level selection. No coverage tooling or new
dependency. No proxy guard. No disk-space guard in the runner. Not running the `.test.mjs` files is an open decision,
not built here. Optional: split the big shell test file, and shared `tests/fakes/` for the eight embedded fake podman
copies and the two `_fake_login_ok` copies.
