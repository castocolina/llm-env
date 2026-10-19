# OmniRoute Smart Routing P1: Spikes and Derived Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a derived OmniRoute image whose compiled server is patched to load extra routing strategies from a directory, run the PRD's Phase 1 spikes S1–S5 against a throwaway container, and record go/no-go with measured values in a spike report and a PRD v2.1.

**Architecture:**
- **Patcher:** a manifest-driven Node patcher edits the compiled Next.js chunks of a digest-pinned OmniRoute image at `podman build` time.
  - Every patch site has an exact expected match count. Any mismatch exits 2 and fails the build.
  - Anchors are string literals and code shapes. Minified identifiers are only ever captured through regex groups, never written as literals.
- **llm-env integration:** the derived image is built and switched in through llm-env's own config (`smart_routing:` in `models.yml`), the same way System One's image is.
- **Spikes:** they run on a side-by-side test container on `127.0.0.1:29128` with no volumes, against a local mock OpenAI-compatible upstream. The production `omniroute` container is never touched.

**Tech Stack:** Bash (tools/lib.sh), Python 3 (pylib, pytest), Node 24 ES modules (`.mjs`, `node --test`, no npm dependencies), podman, OmniRoute `docker.io/diegosouzapw/omniroute@sha256:3d387b3d392d686817daa4e74ececda5b4b4b2992e1d7550cca41f79bb4389a3` This is the manifest-list digest of the `:next` image the production `omniroute` container runs on this host: image id `2a7a68e89fe5`, built 2026-10-07, package 3.8.52, Node v26.10.0, user `node`, `node:sqlite` available, pre-strategy-registry. It was resolved on 2026-10-13 with `podman image inspect --format '{{.Digest}}' docker.io/diegosouzapw/omniroute:next` and checked against `podman inspect --format '{{.Image}}' omniroute`. Task 5 Step 1 re-checks it and records it in the spike report.

**Spec:** `docs/prds/omniroute-smart-routing-v2.0-prd.md`, especially sections "Architecture", "Interface design", and "Execution Phases → Phase 1". Read the evidence it cites as needed:
- `docs/prds/omniroute-smart-routing-research/custom-strategy-spike.md`
- `docs/prds/omniroute-smart-routing-research/strategies-fusion.md`
- `docs/prds/omniroute-smart-routing-research/spike/patch-strategies.mjs`

## Global Constraints

- Never stop, restart, exec writes into, or reconfigure the production `omniroute` container. Never edit `~/.config/llm-env/*` from this plan, with one exception: `make setup` writes `smart_routing` defaults through migration and creates `omniroute-smart.env` (mode 0600, never overwritten) when smart routing is enabled, and that happens only when the operator runs it.
- Throwaway containers are named `omniroute-smart-spike*` and bind only `127.0.0.1:29128` (or `29129` for the S1 unpatched-base comparison).
  - They mount no named volumes and no host data. The only mounts allowed are read-only binds of the repo directories `omniroute-smart/spike` and `omniroute-smart/strategies`.
  - Each one is removed at the end of its task, success or failure.
- Secrets are never printed, logged, or written to repo files. Test passwords for the throwaway container are generated per run and kept only in shell variables.
- `.mjs` files use Node built-ins only (no `package.json`, no npm). Type hints are JSDoc. They are checked with `node --check` in `tools/validate.sh` and tested with `node --test` from `tools/test.sh`.
- Patch anchors are string literals and code shapes. A minified identifier may be captured by a regex group but never appears as a literal in a pattern. A site that cannot be anchored this way is an S1 no-go.
- Every patch site declares an exact `expected` match count. The patcher writes nothing unless every site matches exactly, and on any mismatch it exits 2 with a JSON report.
- Never fall back to declared order silently. A strategy module that is missing or throws makes the request fail.
- Commit after each task. Never push.
- Follow ponytail rules: the smallest complete change, no speculative options, and the existing repo patterns (`scripts/system-one-build.sh`, `pylib/config.py` migrate/validate, `pylib/compose.py`).

## Review Focus

1. **Re-patching an already-patched tree with a different patch version** must exit 2 ("stale patch"), not skip silently or double-apply. Pinned in Task 3.
2. **A partial anchor match** (e.g. dispatch matches 1 of 2 expected) must write no file at all and exit 2. Pinned in Task 3.
3. **A strategy module that is missing or throws at request time** must make the dispatch throw, never return the declared order. Pinned in Task 4.
4. **`smart_routing.enabled: true` with a missing `~/.config/llm-env/omniroute-smart.env`** must not break `make start`. Setup creates the file empty with mode 0600, and compose references it only when it exists. Pinned in Tasks 7 and 8.
5. **A base-digest or patch-version change** must trigger a rebuild in `make setup` (label mismatch), not reuse a stale derived image. Pinned in Task 8.

---

## File Structure

| Path | Responsibility |
|---|---|
| `omniroute-smart/patch/patcher.mjs` | Generic engine: walk a tree, apply the site manifest in memory, assert exact counts before writing anything, write the changed files, stamp the version last |
| `omniroute-smart/patch/sites.mjs` | Site manifest: id, anchor regex, expected count, and replacement for sites 1–6 |
| `omniroute-smart/patch/runtime.mjs` | Runtime helpers that patched code calls through `globalThis.__omniSmart`: module loading, context registry, plugin env filter |
| `omniroute-smart/patch/patch-omniroute.mjs` | CLI used by the Dockerfile: `node patch-omniroute.mjs <server-root>` |
| `omniroute-smart/patch/test/*.test.mjs` + `fixtures/` | Unit tests against synthetic minified fixtures |
| `omniroute-smart/shared/classifier-questions.mjs` | The PRD's five System One questions (English) and the `buildState()` truncation rule, reused by P3 |
| `omniroute-smart/strategies/spike-order.mjs` | Canary strategy for the spikes (reverse order, prelude 503 on a header, observe counter) |
| `omniroute-smart/spike/mock-upstream.mjs` | Mock OpenAI-compatible upstream with scripted per-model behaviour |
| `omniroute-smart/spike/probe.mjs` | Loopback probe run inside the throwaway container (combos, chat, plugins) |
| `omniroute-smart/spike/laya-batch-compare.mjs` | Laya latency and batched-vs-separate comparison |
| `omniroute-smart/spike/plugins/smart-catalog-probe/` | Minimal plugin that reports the env key **names** it received and runs a 60 s timer |
| `docker/omniroute-smart/Dockerfile` | Derived image: FROM pinned digest, copy modules, run patcher |
| `scripts/omniroute-smart-build.sh` | Build the derived image with labels (base digest, patch version) |
| `pylib/config.py`, `tests/test_config.py` | `smart_routing` section: migrate defaults and validate |
| `pylib/compose.py`, `tests/test_compose.py` | Image swap, `OMNIROUTE_STRATEGIES_DIR`, `env_file` |
| `setup/setup.sh`, `tests/test_shell.py` | Build on label mismatch; create `omniroute-smart.env` with mode 0600 |
| `scripts/check-server.sh`, `tests/test_shell.py` | `smart-routing` health rows |
| `tools/validate.sh`, `tools/test.sh` | `node --check` and `node --test` |
| `docs/prds/omniroute-smart-routing-research/spike-report-p1.md` | Measured results, go/no-go |
| `docs/prds/omniroute-smart-routing-v2.1-prd.md` | PRD with measured values (git mv from v2.0) |

**Interfaces this plan produces for P2–P4.** P2–P4 must read the spike report for what was actually reachable.

- **Strategy module contract.** Each file in `OMNIROUTE_STRATEGIES_DIR/<name>.mjs` has a default export:

  ```js
  /** @typedef {{modelStr:string, provider:string, providerId?:string, connectionId?:string}} Target */
  export default {
    name: "<name>",
    /** Optional; runs before ordering/fusion. ctx = {strategy, body, combo, config, log, headers, store}.
     *  Return {response: Response} to answer directly, or null to continue to order(). */
    async prelude(ctx) {},
    /** Required; returns the targets in the order the combo loop should try them. */
    async order(targets, deps) {},
    /** Optional; receives every upstream attempt outcome. */
    observe(outcome) {}, // outcome: {store, target, status, latencyMs, error?, retryAfterMs?, quotaResetAt?}
  };
  ```

  The `{targets, lane}` return named in the PRD is dropped. `order()` covers steering, and the per-request store carries the prelude's decision to it.
- **Per-request store.** `globalThis.__omniSmart.als` is an AsyncLocalStorage. Site 3 enters `{body, headers, strategy}` before calling `prelude`. `order()` and `observe()` read it with `als.getStore()`, and modules may add fields to it (P3 adds `store.smart`).
- **Context registry.** Patched code fills `globalThis.__omniSmart.ctx`, and modules read it. Keys are filled when their site exists; Task 5 records which ones are reachable, with argument shapes:
  - `getQuotaCache(connectionId)`: an upstream `QuotaCacheEntry` `{quotas, modelQuotas, fetchedAt, …}`. Upstream anchors its state on `globalThis`; record the global key if reading it directly avoids a patch site.
  - `getQuotaFetcher(provider)`
  - `getUsageFetcher(provider)` or `getUsageForProvider(connection)`, whichever is reachable
  - `getCircuitBreaker(provider)`
  - `isModelLocked(provider, connectionId, model)`
  - `getPricingForModel(provider, model)`
  - `getPluginConfig(name)`
  - `listProviderConnections()`: connections with `apiKey` readable (lazily decrypted upstream)
  - `handleFusionChat({body, models, handleSingleModel, log, comboName, judgeModel, tuning, perTargetAdmission})` (research shape; the exact shape is the one Task 5 records in `spike-report-p1.md`)
  - `handleSingleModel(body, modelStr, target?)`
  - `checkComboResponse(combo, response) → {fallback: boolean, reason?}` (or the upstream shape): OmniRoute's **own** check that the combo loop runs on a target's answer for the combo's forbidden-substring and HTTP-200 error-text rules (the one that makes the loop fall back to the next target). P4 applies it to every fusion upstream call so those rules are inherited, never reimplemented. Not needed if S3 finds that `handleSingleModel` already applies them.
- **Shared classifier contract.** `omniroute-smart/shared/classifier-questions.mjs` exports:
  - `QUESTIONS`: the five-question object;
  - `buildState(body, maxChars = 4000)`: returns `{text, has_tools}`;
  - `deriveConfidence(answer)`: returns a number in [0,1].

---

### Task 1: JS checks in validate and test

**Files:**
- Modify: `tools/validate.sh`
- Modify: `tools/test.sh`
- Create: `omniroute-smart/patch/test/smoke.test.mjs`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `tools/validate.sh` runs `node --check` on every `omniroute-smart/**/*.mjs`;
  - `tools/test.sh` runs `node --test 'omniroute-smart/**/*.test.mjs'` after pytest.

- [ ] **Step 1: Write a smoke test that will run once wired**

```js
// omniroute-smart/patch/test/smoke.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";

test("node test runner is wired into tools/test.sh", () => {
  assert.equal(1 + 1, 2);
});
```

- [ ] **Step 2: Wire the checks**

`tools/validate.sh`: add the line below after the shellcheck line.

```bash
find ./omniroute-smart -name '*.mjs' -print0 | xargs -0 -r -n1 node --check
```

`tools/test.sh`: replace the last line with these two.

```bash
uv run pytest tests/ -n auto -v
node --test 'omniroute-smart/**/*.test.mjs'
```

The quoted glob is required. Node 24's `node --test <directory>` fails with `Could not find` (verified on this host), and the glob also keeps fixture `.js` files under `test/` from being run as tests.

- [ ] **Step 3: Run both**

Run: `bash tools/validate.sh && bash tools/test.sh 2>&1 | tail -8`
Expected:
- `All checks passed.`
- pytest passes;
- node's reporter prints `ℹ pass 1` and `ℹ fail 0`.

- [ ] **Step 4: Commit**

```bash
git add tools/validate.sh tools/test.sh omniroute-smart/patch/test/smoke.test.mjs
git commit -m "build: check and test omniroute-smart .mjs modules"
```

---

### Task 2: `smart_routing` config section

**Files:**
- Modify: `pylib/config.py`, next to the `system_one` migrate (lines ~194–203) and validate (lines ~424–445) blocks
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `migrate_config`, `validate_config`, `ConfigError` (existing).
- Produces: `cfg["smart_routing"]` with these keys:
  - `enabled: bool = False`
  - `base_image: str = "docker.io/diegosouzapw/omniroute@sha256:3d387b3d392d686817daa4e74ececda5b4b4b2992e1d7550cca41f79bb4389a3"`
  - `image: str = "localhost/llm-env-omniroute-smart:latest"`

  Constants: `DEFAULT_SMART_ROUTING_BASE_IMAGE`, `DEFAULT_SMART_ROUTING_IMAGE`. P2/P3 add more keys to this section.

- [ ] **Step 1: Write the failing tests**

```python
def test_migrate_config_adds_disabled_smart_routing_defaults():
    cfg = migrate_config({})
    assert cfg["smart_routing"] == {
        "enabled": False,
        "base_image": "docker.io/diegosouzapw/omniroute@sha256:3d387b3d392d686817daa4e74ececda5b4b4b2992e1d7550cca41f79bb4389a3",
        "image": "localhost/llm-env-omniroute-smart:latest",
    }


def test_migrate_config_keeps_an_explicit_smart_routing_base_image():
    cfg = migrate_config({"smart_routing": {"base_image": "docker.io/x/omniroute@sha256:" + "a" * 64}})
    assert cfg["smart_routing"]["base_image"].endswith("a" * 64)


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ({"enabled": "yes"}, "smart_routing.enabled must be a Boolean"),
        ({"base_image": "docker.io/diegosouzapw/omniroute:next"}, "smart_routing.base_image must be pinned by digest"),
        ({"image": ""}, "smart_routing.image must be a non-empty string"),
    ],
)
# valid_cfg is the existing pytest fixture defined near tests/test_config.py:1105 (`rg -n "def valid_cfg" tests/test_config.py`).
def test_validate_config_rejects_bad_smart_routing(valid_cfg, section, message):
    cfg = copy.deepcopy(valid_cfg)
    cfg["smart_routing"] = {**migrate_config({})["smart_routing"], **section}
    errors = validate_config(cfg)  # returns a list; require_valid_config is the raising wrapper
    assert any(message in e for e in errors), errors
```

- [ ] **Step 2: Run and see them fail**

Run: `uv run pytest tests/test_config.py -k smart_routing -v`
Expected: FAIL with `KeyError: 'smart_routing'` and `DID NOT RAISE`.

- [ ] **Step 3: Implement**

In `pylib/config.py`, next to `DEFAULT_SYSTEM_ONE_IMAGE`:

```python
DEFAULT_SMART_ROUTING_BASE_IMAGE = (
    "docker.io/diegosouzapw/omniroute@sha256:"
    "3d387b3d392d686817daa4e74ececda5b4b4b2992e1d7550cca41f79bb4389a3"
)
DEFAULT_SMART_ROUTING_IMAGE = "localhost/llm-env-omniroute-smart:latest"
```

In `migrate_config`, after the `system_one` block:

```python
    smart_routing = cfg.setdefault("smart_routing", {})
    if isinstance(smart_routing, dict):
        smart_routing.setdefault("enabled", False)
        # Pinned by digest: the patch anchors match this exact compiled build.
        smart_routing.setdefault("base_image", DEFAULT_SMART_ROUTING_BASE_IMAGE)
        smart_routing.setdefault("image", DEFAULT_SMART_ROUTING_IMAGE)
```

In `validate_config`, after the `system_one` block:

```python
    if "smart_routing" in cfg:
        smart_routing = cfg["smart_routing"]
        if not isinstance(smart_routing, dict):
            errors.append("section smart_routing must be a mapping")
        else:
            if not isinstance(smart_routing.get("enabled", False), bool):
                errors.append("smart_routing.enabled must be a Boolean")
            base_image = smart_routing.get("base_image", DEFAULT_SMART_ROUTING_BASE_IMAGE)
            if not isinstance(base_image, str) or not re.search(r"@sha256:[0-9a-f]{64}$", base_image):
                errors.append("smart_routing.base_image must be pinned by digest (…@sha256:<64 hex>)")
            image = smart_routing.get("image", DEFAULT_SMART_ROUTING_IMAGE)
            if not isinstance(image, str) or not image.strip():
                errors.append("smart_routing.image must be a non-empty string")
```

`re` is already imported (`pylib/config.py:7`).

- [ ] **Step 4: Run the tests and the full config suite**

Run: `uv run pytest tests/test_config.py -v`
Expected: all PASS. Existing tests that compare the whole migrated dict may need the new section added. Update those expectations, never the behaviour.

- [ ] **Step 5: Commit**

```bash
git add pylib/config.py tests/test_config.py
git commit -m "feat(config): smart_routing section with digest-pinned base image"
```

---

### Task 3: Patcher engine (exact counts, atomic, versioned)

**Files:**
- Create: `omniroute-smart/patch/patcher.mjs`
- Test: `omniroute-smart/patch/test/patcher.test.mjs`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `applyPatch(root: string, sites: Site[], version: string) → {status: "patched"|"already", counts: Record<string,number>, files: number}`. It throws `PatchError` with `.report = {expected, counts}` on any mismatch or a stale stamp.
  - `Site = {id: string, re: RegExp /*global*/, expected: number, replace: (match: string, ...groups: string[]) => string}`.
  - Stamp file: `<root>/.omniroute-smart-patch.json` = `{version, counts}`.

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/patch/test/patcher.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, readFileSync, mkdirSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { applyPatch, PatchError } from "../patcher.mjs";

const site = (expected) => ({ id: "s", re: /ANCHOR\((\w)\)/g, expected, replace: (m, v) => `HOOK(${v})` });
const tree = (files) => {
  const root = mkdtempSync(join(tmpdir(), "patcher-"));
  for (const [rel, text] of Object.entries(files)) {
    mkdirSync(join(root, rel, ".."), { recursive: true });
    writeFileSync(join(root, rel), text);
  }
  return root;
};

test("patches every match when counts are exact and stamps the version", () => {
  const root = tree({ "a.js": "x ANCHOR(q) y", "sub/b.js": "ANCHOR(z)", "c.txt": "ANCHOR(t)" });
  const r = applyPatch(root, [site(2)], "p1");
  assert.equal(r.status, "patched");
  assert.deepEqual(r.counts, { s: 2 });
  assert.equal(readFileSync(join(root, "a.js"), "utf8"), "x HOOK(q) y");
  assert.equal(readFileSync(join(root, "c.txt"), "utf8"), "ANCHOR(t)"); // only .js files
  assert.deepEqual(JSON.parse(readFileSync(join(root, ".omniroute-smart-patch.json"), "utf8")).version, "p1");
});

test("a partial match writes nothing and throws with the report", () => {
  const root = tree({ "a.js": "ANCHOR(q)" });
  assert.throws(() => applyPatch(root, [site(2)], "p1"), (e) => e instanceof PatchError && e.report.counts.s === 1);
  assert.equal(readFileSync(join(root, "a.js"), "utf8"), "ANCHOR(q)");
  assert.equal(existsSync(join(root, ".omniroute-smart-patch.json")), false);
});

test("re-running the same version is a no-op", () => {
  const root = tree({ "a.js": "ANCHOR(q)" });
  applyPatch(root, [site(1)], "p1");
  assert.equal(applyPatch(root, [site(1)], "p1").status, "already");
});

test("a tree stamped by another version is rejected", () => {
  const root = tree({ "a.js": "ANCHOR(q)" });
  applyPatch(root, [site(1)], "p1");
  assert.throws(() => applyPatch(root, [site(1)], "p2"), /stale patch/);
});
```

- [ ] **Step 2: Run and see them fail**

Run: `node --test omniroute-smart/patch/test/patcher.test.mjs`
Expected: FAIL with `Cannot find module '../patcher.mjs'`.

- [ ] **Step 3: Implement**

```js
// omniroute-smart/patch/patcher.mjs
// Applies a site manifest to compiled OmniRoute chunks. All-or-nothing: counts are
// computed in memory, and files are written only when every site matches exactly.
import { readdirSync, readFileSync, writeFileSync, statSync, existsSync } from "node:fs";
import { join } from "node:path";

export class PatchError extends Error {
  constructor(message, report) { super(message); this.report = report; }
}

const STAMP = ".omniroute-smart-patch.json";

const jsFiles = (dir, out = []) => {
  for (const e of readdirSync(dir)) {
    const f = join(dir, e);
    if (statSync(f).isDirectory()) jsFiles(f, out);
    else if (f.endsWith(".js")) out.push(f);
  }
  return out;
};

/** @param {string} root @param {{id:string,re:RegExp,expected:number,replace:Function}[]} sites @param {string} version */
export function applyPatch(root, sites, version) {
  const stampPath = join(root, STAMP);
  if (existsSync(stampPath)) {
    const stamp = JSON.parse(readFileSync(stampPath, "utf8"));
    if (stamp.version === version) return { status: "already", counts: stamp.counts, files: 0 };
    throw new PatchError(`stale patch: tree stamped ${stamp.version}, wanted ${version}`, { stamp });
  }
  const counts = Object.fromEntries(sites.map((s) => [s.id, 0]));
  const changed = new Map();
  for (const f of jsFiles(root)) {
    const before = readFileSync(f, "utf8");
    let text = before;
    for (const s of sites) text = text.replace(s.re, (...args) => (counts[s.id]++, s.replace(...args)));
    if (text !== before) changed.set(f, text);
  }
  const expected = Object.fromEntries(sites.map((s) => [s.id, s.expected]));
  if (sites.some((s) => counts[s.id] !== s.expected)) {
    throw new PatchError("anchor count mismatch", { expected, counts });
  }
  // shortcut: files are written one by one, stamp last; a crash mid-write leaves a half-patched tree. It runs only inside a
  // `podman build` RUN step, where a failure discards the layer. Upgrade to temp-file + rename if it is ever run in place.
  for (const [f, text] of changed) writeFileSync(f, text);
  writeFileSync(stampPath, JSON.stringify({ version, counts }));
  return { status: "patched", counts, files: changed.size };
}
```

- [ ] **Step 4: Run the tests**

Run: `node --test omniroute-smart/patch/test/patcher.test.mjs`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add omniroute-smart/patch/patcher.mjs omniroute-smart/patch/test/patcher.test.mjs
git commit -m "feat(omniroute-smart): all-or-nothing patcher with exact counts and version stamp"
```

---

### Task 4: Sites 1–2 (strategy list + ordering hook, fail-closed), runtime loader, CLI

**Files:**
- Create: `omniroute-smart/patch/runtime.mjs`, `omniroute-smart/patch/sites.mjs`, `omniroute-smart/patch/patch-omniroute.mjs`
- Create: `omniroute-smart/patch/test/fixtures/pre-registry/server/chunks/combo.js`, `.../dashboard.js`
- Test: `omniroute-smart/patch/test/sites.test.mjs`

**Interfaces:**
- Consumes: `applyPatch`, `PatchError` (Task 3).
- Produces:
  - `buildSites({names, runtimePath}) → Site[]`;
  - `PATCH_VERSION` (string, bumped whenever any site changes);
  - `runtime.mjs` exports `loadStrategy(name)` (throws `SmartStrategyUnavailable`), `installRuntime(dir)`, and `pluginEnv(pluginName, env)`.

  Patched code reaches the runtime only through `globalThis.__omniSmart`, which `installRuntime` creates.

- [ ] **Step 1: Write the fixtures**

These are synthetic, minified-shaped copies of the two anchors the spike proved. Their shapes are from `custom-strategy-spike.md` §1 and `spike/patch-strategies.mjs`.

```js
// omniroute-smart/patch/test/fixtures/pre-registry/server/chunks/combo.js
const L=["priority","weighted","round-robin","fill-first","fusion"];
async function q(a,b,d){let{combo:c,config:g}=d;return{orderedTargets:b,quotaShareRelease:null}}
async function r(a,b,d){let{combo:c,config:g}=d;return{orderedTargets:b,quotaShareRelease:null}}
export { L, q, r };
```

```js
// omniroute-smart/patch/test/fixtures/pre-registry/server/chunks/dashboard.js
export const D=["priority","weighted","round-robin","fill-first","fusion"];
```

- [ ] **Step 2: Write the failing tests**

```js
// omniroute-smart/patch/test/sites.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { cpSync, mkdtempSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { applyPatch } from "../patcher.mjs";
import { buildSites, PATCH_VERSION } from "../sites.mjs";

const here = fileURLToPath(new URL(".", import.meta.url));
const runtimePath = fileURLToPath(new URL("../runtime.mjs", import.meta.url));
const setup = (modules) => {
  const root = mkdtempSync(join(tmpdir(), "sites-"));
  cpSync(join(here, "fixtures/pre-registry"), root, { recursive: true });
  const dir = mkdtempSync(join(tmpdir(), "strategies-"));
  for (const [n, src] of Object.entries(modules)) writeFileSync(join(dir, `${n}.mjs`), src);
  delete globalThis.__omniSmart; // the runtime caches the dir and loaded modules process-wide
  return { root, dir };
};
// Sites 3-6 are only enforced once Task 5 adds them; here we test sites 1-2.
// The fixture holds 2 list literals and 2 dispatchers; production counts (48/2) stay in the manifest.
const firstTwo = (names) => buildSites({ names, runtimePath })
  .filter((s) => s.id === "strategy-list" || s.id === "ordering-hook")
  .map((s) => ({ ...s, expected: 2 }));
// Unique `?v=` query per import: patched chunks are rewritten across tests, so a fresh specifier avoids Node's ESM cache.
let imports = 0;
const load = (f) => import(`${f}?v=${++imports}`);

test("adds custom names to every strategy list literal", () => {
  const { root } = setup({});
  applyPatch(join(root, "server"), firstTwo(["smart"]), PATCH_VERSION);
  assert.match(readFileSync(join(root, "server/chunks/dashboard.js"), "utf8"), /"fusion","smart"\]/);
});

test("ordering hook calls the module's order() for a custom strategy", async () => {
  const { root, dir } = setup({ smart: 'export default {name:"smart", order(t){ return [...t].reverse(); }}' });
  applyPatch(join(root, "server"), firstTwo(["smart"]), PATCH_VERSION);
  process.env.OMNIROUTE_STRATEGIES_DIR = dir;
  const m = await load(join(root, "server/chunks/combo.js"));
  const out = await m.q("smart", [1, 2, 3], { combo: {}, config: {}, log: {} });
  assert.deepEqual(out.orderedTargets, [3, 2, 1]);
});

test("built-in strategies are untouched by the hook", async () => {
  const { root, dir } = setup({ smart: 'export default {name:"smart", order(t){ return [...t].reverse(); }}' });
  applyPatch(join(root, "server"), firstTwo(["smart"]), PATCH_VERSION);
  process.env.OMNIROUTE_STRATEGIES_DIR = dir;
  const m = await load(join(root, "server/chunks/combo.js"));
  assert.deepEqual((await m.q("priority", [1, 2], { combo: {}, config: {}, log: {} })).orderedTargets, [1, 2]);
});

test("a throwing or missing module fails the dispatch instead of using declared order", async () => {
  const { root, dir } = setup({ smart: 'export default {name:"smart", order(){ throw new Error("boom"); }}' });
  applyPatch(join(root, "server"), firstTwo(["smart", "ghost"]), PATCH_VERSION);
  process.env.OMNIROUTE_STRATEGIES_DIR = dir;
  const m = await load(join(root, "server/chunks/combo.js"));
  await assert.rejects(m.q("smart", [1, 2], { combo: {}, config: {}, log: {} }), /SmartStrategyUnavailable|boom/);
  await assert.rejects(m.q("ghost", [1, 2], { combo: {}, config: {}, log: {} }), /SmartStrategyUnavailable/);
});
```

- [ ] **Step 3: Run and see them fail**

Run: `node --test omniroute-smart/patch/test/sites.test.mjs`
Expected: FAIL with `Cannot find module '../sites.mjs'`.

- [ ] **Step 4: Implement the runtime, the sites, and the CLI**

```js
// omniroute-smart/patch/runtime.mjs
// Loaded by patched OmniRoute code via globalThis.__omniSmart. Never falls back silently.
import { AsyncLocalStorage } from "node:async_hooks";
import { randomUUID } from "node:crypto";
import { pathToFileURL } from "node:url";
import { join } from "node:path";

export class SmartStrategyUnavailable extends Error {
  constructor(name, cause) { super(`SmartStrategyUnavailable: ${name}: ${cause?.message ?? cause}`); this.code = "SMART_STRATEGY_UNAVAILABLE"; }
}

/** @param {string} dir */
export function installRuntime(dir) {
  const g = (globalThis.__omniSmart ??= { modules: new Map(), ctx: {} });
  g.modules ??= new Map();
  g.ctx ??= {}; // context sites may have registered keys before the runtime was installed
  // Per-request store shared by prelude → order → observe (site 3 enters it; P3 depends on it).
  g.als ??= new AsyncLocalStorage();
  g.dir = dir;
  // Site 3 turns a failed module load into the PRD error body (never a silent fallback, never a bare 500).
  g.unavailable = (name, cause) => new Response(JSON.stringify({ error: { type: "smart_routing_unavailable", reason: "strategy_unavailable",
    detail: `strategy ${name} failed to load: ${String(cause?.message ?? cause).slice(0, 200)}`, request_id: randomUUID() } }),
    { status: 503, headers: { "content-type": "application/json" } });
  g.loadStrategy = async (name) => {
    if (!g.modules.has(name)) {
      try { g.modules.set(name, (await import(pathToFileURL(join(dir, `${name}.mjs`)).href)).default); }
      catch (e) { throw new SmartStrategyUnavailable(name, e); }
    }
    return g.modules.get(name);
  };
  return g;
}

/** Env vars scoped to one plugin: OMNIROUTE_PLUGIN_<NAME>_* only. */
export function pluginEnv(pluginName, env = process.env) {
  const prefix = `OMNIROUTE_PLUGIN_${String(pluginName).toUpperCase().replace(/[^A-Z0-9]/g, "_")}_`;
  return Object.fromEntries(Object.entries(env).filter(([k]) => k.startsWith(prefix)));
}
```

```js
// omniroute-smart/patch/sites.mjs
// Site manifest for the pre-registry OmniRoute build (base digest in models.yml smart_routing.base_image).
// Anchors are literals/shapes; minified identifiers are only captured by groups.
export const PATCH_VERSION = "p1.0";

const ensureRuntime = (runtimePath) =>
  `(globalThis.__omniSmart?.loadStrategy?globalThis.__omniSmart:(await import(${JSON.stringify("file://" + runtimePath)})).installRuntime(process.env.OMNIROUTE_STRATEGIES_DIR||"/opt/omniroute-smart/strategies"))`;

/** @param {{names:string[], runtimePath:string}} opts */
export function buildSites({ names, runtimePath }) {
  const extra = names.map((n) => JSON.stringify(n)).join(",");
  return [
    {
      id: "strategy-list",
      // Measured by the 2026-10-13 spike: 48 occurrences in 47 files.
      re: /\["priority","weighted","round-robin"(?:,"[a-z0-9-]+")*\]/g,
      expected: 48,
      replace: (m) => `${m.slice(0, -1)},${extra}]`,
    },
    {
      id: "ordering-hook",
      re: /async function (\w+)\((\w+),(\w+),(\w+)\)\{let\{combo:(\w+),config:/g,
      expected: 2,
      replace: (m, fn, s, b, d, combo) =>
        `async function ${fn}(${s},${b},${d}){if(${JSON.stringify(names)}.includes(${s})){` +
        `const __t=performance.now(),__R=await ${ensureRuntime(runtimePath)},__m=await __R.loadStrategy(${s});` +
        `const __o=await __m.order([...${b}],${d}),__st=__R.als?.getStore?.();` +
        // the whole injected path (runtime, load, order) is added to the request's hook time, for the S1 p95
        `if(__st)__st.hookMs=(__st.hookMs??0)+performance.now()-__t;` +
        `return{orderedTargets:__o,quotaShareRelease:null}}` +
        `let{combo:${combo},config:`,
    },
  ];
}
```

```js
// omniroute-smart/patch/patch-omniroute.mjs
// Usage: node patch-omniroute.mjs <server-root>   (exit 0 patched/already, 2 mismatch)
import { readdirSync } from "node:fs";
import { applyPatch, PatchError } from "./patcher.mjs";
import { buildSites, PATCH_VERSION } from "./sites.mjs";

const root = process.argv[2] || "/app/.build/next/server";
const dir = process.env.OMNIROUTE_STRATEGIES_DIR || "/opt/omniroute-smart/strategies";
const names = readdirSync(dir).filter((f) => /^[a-z0-9][a-z0-9-]{1,40}\.mjs$/.test(f)).map((f) => f.slice(0, -4));
if (names.length === 0) { console.error(`no strategies in ${dir}`); process.exit(2); }
try {
  const r = applyPatch(root, buildSites({ names, runtimePath: new URL("./runtime.mjs", import.meta.url).pathname }), PATCH_VERSION);
  console.log(JSON.stringify({ version: PATCH_VERSION, names, ...r }));
} catch (e) {
  console.error(JSON.stringify({ error: e.message, report: e instanceof PatchError ? e.report : undefined }));
  process.exit(2);
}
```

The tests override `expected` because the fixture has 2 lists, not 48. That is fine: the production count stays in the manifest. `setup()` deletes `globalThis.__omniSmart`, so each test loads its own strategy dir.

- [ ] **Step 5: Run the tests**

Run: `node --test 'omniroute-smart/patch/test/**/*.test.mjs'`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add omniroute-smart/patch
git commit -m "feat(omniroute-smart): strategy-list and fail-closed ordering hook sites"
```

---

### Task 5: S1a — discover anchors for sites 3–6 in the pinned image

This is an investigation task with a code deliverable. Executor: one subagent, with read-only access to the image.

**Files:**
- Modify: `omniroute-smart/patch/sites.mjs` (add sites 3–6 or record why not), `omniroute-smart/patch/runtime.mjs` (context registration helpers)
- Create: `omniroute-smart/patch/test/fixtures/pre-registry-ext/server/chunks/{prelude,context,single,loader}.js` copied from the real anchors, minimised to the matched statement. They live in their own root so Task 4's tests, which copy `fixtures/pre-registry`, keep counting only sites 1–2.
- Test: `omniroute-smart/patch/test/sites.test.mjs` (one test per new site)
- Create: `docs/prds/omniroute-smart-routing-research/spike-report-p1.md` (section "S1a anchors")

**Interfaces:**
- Consumes: the base image from `smart_routing.base_image`.
- Produces:
  - sites `prelude-hook`, `context-*`, `attempt-observer` and `plugin-env`, each with an exact `expected`;
  - `globalThis.__omniSmart.ctx` keys from the Interfaces table above, each one marked reachable or unreachable in the report.

- [ ] **Step 1: Extract the compiled server tree from the pinned image (read-only)**

```bash
REPO="$(git rev-parse --show-toplevel)"   # run Steps 1-4 in one shell from the repo; Step 2 cd's away
W="$(mktemp -d)"; img="$(yq -r '.smart_routing.base_image // "docker.io/diegosouzapw/omniroute@sha256:3d387b3d392d686817daa4e74ececda5b4b4b2992e1d7550cca41f79bb4389a3"' ~/.config/llm-env/models.yml)"
prod="$(podman image inspect --format '{{json .RepoDigests}}' "$(podman inspect --format '{{.Image}}' omniroute)")"
[[ "$prod" == *"${img#*@}"* ]] || echo "WARN: pinned digest is not the production image ($prod); record this in the report before continuing"
podman image exists "$img" || podman pull "$img"
cid="$(podman create --name omniroute-smart-spike-extract "$img")"; podman cp "$cid:/app/.build/next/server" "$W/server"; podman rm "$cid"
echo "$W"
```

Expected:
- no WARN line. The pinned digest is one of the production image's `RepoDigests`;
- `$W/server/chunks/*.js` exists.

Record `$W` and the digest check in the report. Nothing is written to the repo. `podman inspect` here only reads the production container.

- [ ] **Step 2: Locate each anchor by literal, then read its shape**

```bash
cd "$W/server"
rg -l 'handleFusionChat|All fusion panel models failed' -g '*.js' | head        # site 3: fusion/prelude branch
rg -o '"fusion"[!=]==\w+.{0,200}' -g '*.js' | head -5
rg -l 'HALF_OPEN' -g '*.js' | head                                                  # context: circuit breaker module
rg -l 'get-current-period-usage' -g '*.js'                                         # context: usage fetchers (cursor/claude)
rg -o '"PATH","HOME","USER","LANG","LC_ALL","NODE_ENV".{0,300}' -g '*.js' | head -3  # site 6: plugin env allowlist
rg -o '.{0,200}loader\.cleanup.{0,100}' -g '*.js' | head -3                         # site 6: spawn scope with manifest.name
```

For each target, open the matching chunk around the hit (`rg -n` plus `sed -n`) and write down:
- the shortest literal-anchored regex that matches exactly the intended statement;
- its exact count across the tree (`rg -c` summed);
- the identifiers the hook needs, which must come from capture groups.

Targets:
- **Site 3 `prelude-hook`:** the point in combo dispatch before the fusion/pipeline branch, where the strategy name, request body and combo are in scope. It runs only when `S` is one of the custom names. The hook is written *in the dispatch function's own body* (not inside a helper), so the store it enters covers the rest of this request: the ordering step and every per-target call.

  ```
  const __t=performance.now();const __g=await RT;const __st={body,headers,strategy:S};__g.als.enterWith(__st);
  const __p=await __g.loadStrategy(S).then(m=>m.prelude?.({strategy:S,body,combo,config,log,headers,store:__st}),e=>({response:__g.unavailable(S,e)}));
  __st.hookMs=(__st.hookMs??0)+performance.now()-__t;
  if(__p?.response) return __p.response;
  ```

  - A module that fails to load answers the PRD's `503 strategy_unavailable` body through `__g.unavailable` (runtime.mjs), instead of throwing into OmniRoute's generic 500 handler. Errors inside `prelude` itself are the strategy's job (P3 catches them).

  - `headers` are the client request headers, captured by a group when a request or headers object is in scope at the site; otherwise pass `headers: undefined`.
  - The canary triggers on the **body**, not headers, so the spike never depends on `headers` being reachable.
- **Site 4 `context-checkComboResponse`:** find where the combo loop decides that a 2xx answer must still fall back because it contains a forbidden substring or HTTP-200 error text. Start from the combo config field names those rules use (read them from a combo the user already has: `P get-combo` on the spike container after creating one with such a rule in S1, or from the dashboard combo editor bundle: `rg -o '.{0,120}(orbidden|rrorText|rrorPattern).{0,120}' -g '*.js' | head`). Capture that check function the same way as the other context keys, anchored on a string literal inside it. If the check is inline in the loop (no function to capture), record the exact statement and whether `handleSingleModel` runs it (then the key is unnecessary); if neither, record it as unreachable: P4 then keeps every smart request on the single lane, so the rules always apply.
- **Site 4 `context-*`:** inside each defining module, right after the function definition, a statement such as `(globalThis.__omniSmart??={modules:new Map(),ctx:{}}).ctx.getCircuitBreaker=<captured fn>;`. Anchor on a string literal inside the function body, such as `"HALF_OPEN"`. Repeat for every key in the Interfaces list. A key with no literal-anchored definition is recorded as unreachable. Do not anchor on mangled names.
- **Site 5 `attempt-observer`:** the per-target call the combo loop receives (`handleSingleModelWithTimeout` upstream). Wrap the captured callee so each call reports `{store, target, status, latencyMs, error, retryAfterMs, quotaResetAt}` to `module.observe?.()` of the active custom strategy, then returns or rethrows the original result unchanged. The wrapper adds the time of its own work (building the outcome and the `observe` call, not the wrapped upstream call) to `store.hookMs`, like sites 2 and 3, so the S1 hook p95 covers the whole injected path.
  - `store` is `globalThis.__omniSmart.als.getStore()`.
  - `status` is the Response status, or 0 for a thrown error or timeout.
  - `retryAfterMs` comes from a `Retry-After` header, and `quotaResetAt` from the error body's reset field when the error class exposes one. Otherwise both are `undefined`.
  - The active strategy is `store.strategy`. Calls with no store, such as a non-custom combo, are passed through untouched.
- **Site 6 `plugin-env`:** at the child spawn, where both the filtered env and the plugin's name are in scope. Plugins can spawn before any strategy has loaded the runtime, so the merge is inlined and does not depend on `globalThis.__omniSmart`:

  ```
  ...Object.fromEntries(Object.entries(process.env).filter(([k])=>k.startsWith("OMNIROUTE_PLUGIN_"+String(<captured name>).toUpperCase().replace(/[^A-Z0-9]/g,"_")+"_")))
  ```

  If the name is not in scope there, record it as a no-go for S5. `runtime.mjs`'s `pluginEnv` stays as the tested reference implementation of the same rule, and the site-6 fixture test asserts the inlined expression gives the same result.

- [ ] **Step 3: Add each found site to `sites.mjs` with its measured `expected`, plus a fixture test**

For each site, add a minimised fixture file containing exactly the matched statement, wrapped in a callable export, and a test following the Task 4 pattern:
- the prelude returning `{response}` short-circuits;
- a custom strategy name whose module file is missing makes the site-3 fixture return a `503` whose JSON `error.reason` is `strategy_unavailable` (no throw);
- after the prelude, `globalThis.__omniSmart.als.getStore()` read inside the patched ordering call (site 2) and inside a wrapped per-target call (site 5) is the same object the prelude saw (`store.body === body`);
- two concurrent dispatches each see their own store;
- after a dispatch through the site-2 and site-3 fixtures (and site 5 when found), `store.hookMs` is a finite number > 0;
- `ctx.getCircuitBreaker` is the captured function;
- the observer receives one outcome per call and passes results and errors through unchanged;
- the plugin env includes only `OMNIROUTE_PLUGIN_<NAME>_*` keys for that plugin's name.

Bump `PATCH_VERSION` to `"p1.1"`.

- [ ] **Step 4: Apply the full manifest to the extracted tree**

```bash
cd "$REPO"   # Step 2 cd'ed into $W/server; the paths below are repo-relative
mkdir -p "$W/strategies" && cp omniroute-smart/strategies/*.mjs "$W/strategies/" 2>/dev/null || echo 'export default {name:"probe",order(t){return t}}' > "$W/strategies/probe.mjs"
OMNIROUTE_STRATEGIES_DIR="$W/strategies" node omniroute-smart/patch/patch-omniroute.mjs "$W/server"
```

Expected: exit 0 and JSON with `status:"patched"`, where every count equals its `expected`. On exit 2, fix the regex or the count. The no-go criteria below decide whether a fix is allowed.

- [ ] **Step 5: Record in `spike-report-p1.md`, section "S1a anchors", one row per site:** id, anchor literal, regex, expected count, captured groups, and for context keys whether each is reachable. If any of these holds, write **NO-GO** and stop the plan:
  - site 3 cannot return a `Response`;
  - any anchor needs a literal minified identifier.

  If site 5 (attempt observer) has no anchor, that is not a NO-GO by the PRD's criteria, but it removes `observe()` (failure EWMA and the per-attempt decision log): record it as a P3 impact item and continue.

- [ ] **Step 6: Run the tests and commit**

```bash
node --test 'omniroute-smart/patch/test/**/*.test.mjs' && bash tools/validate.sh
git add omniroute-smart/patch docs/prds/omniroute-smart-routing-research/spike-report-p1.md
git commit -m "feat(omniroute-smart): prelude, context, observer and plugin-env sites (S1a)"
```

---

### Task 6: Shared classifier questions and canary strategy

**Files:**
- Create: `omniroute-smart/shared/classifier-questions.mjs`, `omniroute-smart/strategies/spike-order.mjs`
- Test: `omniroute-smart/shared/test/classifier-questions.test.mjs`

**Interfaces:**
- Consumes: nothing.
- Produces: `QUESTIONS`, `buildState(body, maxChars)`, `deriveConfidence(answer)` (used by P3), and the canary strategy `spike-order`.

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/shared/test/classifier-questions.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { QUESTIONS, buildState, deriveConfidence } from "../classifier-questions.mjs";

test("five questions with the PRD types", () => {
  assert.deepEqual(Object.fromEntries(Object.entries(QUESTIONS).map(([k, q]) => [k, q.type])),
    { task_kind: "choice", domain: "choice", scope: "score", complexity: "score", second_opinion: "noul" });
});

test("the last user turn wins over a long system prompt", () => {
  const body = { messages: [{ role: "system", content: "S".repeat(10000) }, { role: "user", content: "rename foo to bar" }], tools: [] };
  const s = buildState(body, 4000);
  assert.ok(s.text.includes("rename foo to bar"));
  assert.ok(s.text.length <= 4000);
  assert.equal(s.has_tools, false);
});

test("a huge user turn keeps head and tail within 3/4 of the budget", () => {
  const body = { messages: [{ role: "user", content: "HEAD" + "x".repeat(20000) + "TAIL" }] };
  const s = buildState(body, 4000);
  assert.ok(s.text.startsWith("HEAD") && s.text.includes("TAIL") && s.text.length <= 4000);
});

test("confidence by type", () => {
  assert.equal(deriveConfidence({ type: "choice", answer_confidence: 0.9 }), 0.9);
  assert.equal(deriveConfidence({ type: "score", probabilities: { 0: 0.1, 1: 0.7, 2: 0.2 } }), 0.7);
  assert.ok(Math.abs(deriveConfidence({ type: "noul", noul: 0.9 }) - 0.8) < 1e-9);
  assert.ok(Math.abs(deriveConfidence({ type: "noul", noul: 0.6 }) - 0.2) < 1e-9);
});
```

- [ ] **Step 2: Run and see them fail**

Run: `node --test 'omniroute-smart/shared/test/**/*.test.mjs'`
Expected: FAIL with `Cannot find module`.

- [ ] **Step 3: Implement**

```js
// omniroute-smart/shared/classifier-questions.mjs
// System One questions (PRD "System One classification"). English instructions and concrete criteria.
export const QUESTIONS = {
  task_kind: { type: "choice", instructions: "What kind of work does this request ask for?",
    criteria: { plan: "design or plan work before any code changes: specs, task breakdowns, architecture",
      review_audit: "review, audit, critique or verify existing code, plans or documents",
      execute_edit: "write or change code or files following given instructions",
      mechanical: "move, rename, format, or other edits that need no reasoning" } },
  domain: { type: "choice", instructions: "Which part of the stack does this request mainly touch?",
    criteria: { frontend: "UI, components, CSS, browser code", backend: "servers, APIs, databases, CLIs, business logic",
      fullstack: "both UI and server code", infra_docs: "infrastructure, CI, containers, configuration or documentation" } },
  scope: { type: "score", instructions: "How much material must be read or changed?", criteria: ["small", "medium", "large"] },
  complexity: { type: "score", instructions: "How hard is the reasoning required?", criteria: ["low", "medium", "high"] },
  second_opinion: { type: "noul", instructions: "Would a second independent expert opinion materially improve the answer?" },
};

const text = (c) => (typeof c === "string" ? c : Array.isArray(c) ? c.map((p) => p?.text ?? "").join("\n") : "");
const squeeze = (s, max) => (s.length <= max ? s : s.slice(0, Math.floor(max / 2) - 2) + "\n…\n" + s.slice(s.length - (Math.ceil(max / 2) - 2)));

/** @param {{messages?:{role:string,content:any}[], tools?:any[]}} body */
export function buildState(body, maxChars = 4000) {
  const msgs = body.messages ?? [];
  const user = squeeze(text([...msgs].reverse().find((m) => m.role === "user")?.content), Math.floor((maxChars * 3) / 4));
  const system = text(msgs.find((m) => m.role === "system")?.content).slice(0, Math.max(0, maxChars - user.length - 2));
  return { text: system ? `${user}\n\n${system}` : user, has_tools: Array.isArray(body.tools) && body.tools.length > 0 };
}

export function deriveConfidence(a) {
  if (a.type === "choice") return a.answer_confidence ?? a.confidence ?? 0;
  if (a.type === "score") return Math.max(...Object.values(a.probabilities ?? { 0: 0 }));
  if (a.type === "noul") return Math.abs((a.noul ?? 0.5) - 0.5) * 2;
  return 0;
}
```

```js
// omniroute-smart/strategies/spike-order.mjs
// Canary for P1 spikes only: reverse order; a last user message starting "SPIKE:<cmd>" drives the prelude
// (body, not headers: the body is always in the prelude ctx); counts observed attempts.
export const observed = [];
const seen = { orderSawStore: null, observeSawStore: null }; // per-request store visibility (P3 depends on both)
const sampled = []; // one store per ordered request; the patched sites 3, 2 and (if found) 5 add their whole hook time to store.hookMs
const json = (body, status = 200) => ({ response: new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }) });
const storeOf = () => globalThis.__omniSmart?.als?.getStore?.();
export default {
  name: "spike-order",
  async prelude(ctx) {
    const last = [...(ctx.body?.messages ?? [])].reverse().find((m) => m.role === "user")?.content;
    const cmd = typeof last === "string" && last.startsWith("SPIKE:") ? last.slice(6).trim() : null;
    if (cmd === "503") return json({ error: { type: "spike" } }, 503);
    if (cmd === "ctx") {
      const sorted = sampled.map((st) => st.hookMs).filter((x) => x != null).sort((a, b) => a - b);
      return json({ ctxKeys: Object.keys(globalThis.__omniSmart?.ctx ?? {}).sort(), observed: observed.length, ...seen,
        hookP95Ms: sorted.length ? sorted[Math.floor(sorted.length * 0.95)] : null, hookSamples: sorted.length,
        typesafeKeyPresent: typeof process.env.TYPESAFE_API_KEY === "string" }); // presence only, never the value
    }
    // Task 10 Step 3 adds `SPIKE:lane` and the fusion lane here (calls ctx.handleFusionChat via globalThis.__omniSmart.ctx).
    if (ctx.store) ctx.store.canary = true;
    return null;
  },
  async order(targets) {
    const st = storeOf();
    // keep the store at order(), which always runs (site 2), so the gate works even when site 5 is unreachable;
    // site 2 and site 5 add their time to this same object afterwards, so it is read only at SPIKE:ctx
    if (st && sampled.length < 1000) sampled.push(st);
    seen.orderSawStore = st?.canary === true;
    return [...targets].reverse();
  },
  observe(o) {
    seen.observeSawStore = o.store?.canary === true;
    observed.push(o);
  },
};
```

- [ ] **Step 4: Run the tests and validate**

Run: `node --test 'omniroute-smart/**/*.test.mjs' && bash tools/validate.sh`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add omniroute-smart/shared omniroute-smart/strategies
git commit -m "feat(omniroute-smart): shared System One questions and spike canary strategy"
```

---

### Task 7: Derived image and build script

**Files:**
- Create: `docker/omniroute-smart/Dockerfile`, `scripts/omniroute-smart-build.sh`
- Modify: `Makefile` (target `omniroute-smart-build`), `scripts/help.sh` (one `_help_line`)
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `smart_routing.base_image` and `smart_routing.image` (Task 2); `omniroute-smart/patch/*` (Tasks 3–5); `omniroute-smart/strategies/*`.
- Produces:
  - image `smart_routing.image`, with labels `llm-env.omniroute-smart.base=<base_image>` and `llm-env.omniroute-smart.patch=<PATCH_VERSION>`;
  - `make omniroute-smart-build`.

- [ ] **Step 1: Write the failing tests**

They follow `test_system_one_build_builds_the_configured_image_from_the_repo_context` (`tests/test_shell.py` ~L5152). Extend `run_lifecycle_script`'s generated config with an optional `smart_routing_enabled: bool = False` parameter that writes the Task 2 defaults with `enabled` set, the same way `system_one_enabled` is handled.

```python
def test_omniroute_smart_build_builds_from_the_pinned_base(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(tmp_path, "scripts/omniroute-smart-build.sh", smart_routing_enabled=True)
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text()
    assert "--build-arg BASE_IMAGE=docker.io/diegosouzapw/omniroute@sha256:3d387b3d" in recorded
    assert "--label llm-env.omniroute-smart.patch=" in recorded
    assert re.search(r"--label llm-env\.omniroute-smart\.tree=[0-9a-f]{16}", recorded)
    assert f"-f {ROOT}/docker/omniroute-smart/Dockerfile" in recorded
    assert recorded.rstrip().endswith(str(ROOT))  # repo root is the build context


def test_omniroute_smart_dockerfile_patches_at_build_time() -> None:
    dockerfile = (ROOT / "docker/omniroute-smart/Dockerfile").read_text()
    assert "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}" in dockerfile
    assert "node /opt/omniroute-smart/patch/patch-omniroute.mjs /app/.build/next/server" in dockerfile
    assert "ENV OMNIROUTE_STRATEGIES_DIR=/opt/omniroute-smart/strategies" in dockerfile


def test_makefile_exposes_omniroute_smart_build() -> None:
    assert "omniroute-smart-build:" in (ROOT / "Makefile").read_text()
```

**Harness change, needed by Tasks 7, 8 and 12.** `run_lifecycle_script` (tests/test_shell.py, around L4930) puts only its stub dir, `/usr/bin` and `/bin` on `PATH` (around L5123), and node lives under `~/.volta`. Add a `node` stub that records and delegates, following the `REAL_YQ`/`REAL_UV` pattern:
- stub body: `printf 'node %s\n' "$*" >> "$CALLS"; exec "$REAL_NODE" "$@"`;
- env entry: `"REAL_NODE": shutil.which("node") or "node"`.

Make the change once and reuse it in every later task.

- [ ] **Step 2: Run and see them fail**

Run: `uv run pytest tests/test_shell.py -k omniroute_smart -v`
Expected: FAIL (the script and Dockerfile are missing).

- [ ] **Step 3: Implement**

```dockerfile
# docker/omniroute-smart/Dockerfile
# Derived OmniRoute image: the compiled server is patched to load extra combo strategies.
# A changed upstream shape makes the patcher exit 2, which fails this build (never a silently wrong image).
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG PATCH_VERSION
USER root
COPY omniroute-smart/patch /opt/omniroute-smart/patch
COPY omniroute-smart/shared /opt/omniroute-smart/shared
COPY omniroute-smart/strategies /opt/omniroute-smart/strategies
ENV OMNIROUTE_STRATEGIES_DIR=/opt/omniroute-smart/strategies
RUN test -n "${PATCH_VERSION}" \
    && node /opt/omniroute-smart/patch/patch-omniroute.mjs /app/.build/next/server \
    && rm -rf /opt/omniroute-smart/patch/test /opt/omniroute-smart/shared/test
# The pinned base runs as `node` (verified 2026-10-13); restore it.
USER node
```

Re-check the user on the pinned base with `podman image inspect --format '{{.Config.User}}' "$img"`. If it isn't `node`, use that value in the last line.

```bash
#!/usr/bin/env bash
# omniroute-smart-build.sh — build the derived OmniRoute image (patched strategy loader).
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd podman yq uv node
[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
migrate_config_file || die "configuration migration failed"
base="$(yq -r '.smart_routing.base_image' "$CONFIG_PATH")"; [[ "$base" == *@sha256:* ]] || die "smart_routing.base_image must be pinned by digest"
image="$(yq -r '.smart_routing.image' "$CONFIG_PATH")"; [ -n "$image" ] && [ "$image" != null ] || die "smart_routing.image is empty"
patch_version="$(omniroute_smart_patch_version)"
tree="$(omniroute_smart_tree_hash)"
log_step "Building ${image} from ${base} (patch ${patch_version}, tree ${tree})"
podman build --label "llm-env.omniroute-smart.base=${base}" --label "llm-env.omniroute-smart.patch=${patch_version}" \
    --label "llm-env.omniroute-smart.tree=${tree}" \
    --build-arg "BASE_IMAGE=${base}" --build-arg "PATCH_VERSION=${patch_version}" \
    -t "$image" -f "${REPO_DIR}/docker/omniroute-smart/Dockerfile" "${REPO_DIR}"
log_info "built ${image}"
```

Add two helpers to `tools/lib.sh`, next to `system_one_process_vram`. The build script, setup (Task 8) and check-server (Task 12) all use them.

```bash
# PATCH_VERSION from the patch manifest (the single source of truth).
omniroute_smart_patch_version() {
    node --input-type=module -e "import {PATCH_VERSION} from '${REPO_DIR}/omniroute-smart/patch/sites.mjs'; console.log(PATCH_VERSION)"
}

# Content hash of everything baked into the derived image, so a strategy/plugin/patch edit triggers a rebuild.
omniroute_smart_tree_hash() {
    (cd "$REPO_DIR" && find omniroute-smart docker/omniroute-smart -type f -not -path '*/test/*' -not -path 'omniroute-smart/spike/*' -print0 \
        | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16)
}
```

In the `Makefile`, next to `system-one-build`, in the same `tools/run-target.sh` style, and add `omniroute-smart-build` to `.PHONY`:

```make
omniroute-smart-build:
	@bash tools/run-target.sh omniroute-smart-build -- bash scripts/omniroute-smart-build.sh
```

Add `_help_line omniroute-smart-build "Build the derived OmniRoute image (smart routing)"` to `scripts/help.sh`, next to `system-one-build`. The build context is the repo root, so add a `.containerignore` that keeps secrets and bulk out of it: `.env` (it holds the live `OMNI_ROUTER_MASTER_KEY` that `setup/render-unit.sh` passes as `--env-file`), `tmp/` (dev keys), `.git/`, `.venv/`, `.codegraph/`, `node_modules/`, `.pytest_cache/`, `.ruff_cache/` and `__pycache__/`. If the file already exists, add the missing entries. Pin it with one test in `tests/test_shell.py`:

```python
def test_containerignore_keeps_secrets_out_of_the_build_context() -> None:
    entries = (ROOT / ".containerignore").read_text(encoding="utf-8").split()
    assert {".env", "tmp/", ".git/"} <= set(entries)
```

- [ ] **Step 4: Run the tests, then a real build**

Run: `uv run pytest tests/test_shell.py -k omniroute_smart -v`
Expected: PASS.

Run: `TMPCFG=$(mktemp) && yq '.smart_routing.enabled = true' ~/.config/llm-env/models.yml > "$TMPCFG" && LLM_ENV_CONFIG="$TMPCFG" bash scripts/omniroute-smart-build.sh; rm -f "$TMPCFG"`
(`tools/lib.sh:7` honours `LLM_ENV_CONFIG`. Never edit the real file.)
Expected: the build succeeds, the patcher prints `status:"patched"` with every count equal to its expected value, and `podman image inspect localhost/llm-env-omniroute-smart:latest --format '{{index .Labels "llm-env.omniroute-smart.patch"}}'` prints `p1.1`.

- [ ] **Step 5: Commit**

```bash
git add docker/omniroute-smart scripts/omniroute-smart-build.sh Makefile scripts/help.sh .containerignore tests/test_shell.py
git commit -m "feat(omniroute-smart): derived image build with patch-time anchor assertions"
```

---

### Task 8: Compose and setup integration

**Files:**
- Modify:
  - `pylib/compose.py`: `render_compose` around L61, the omniroute service around L122–170, and `write_compose` around L318;
  - `llmenv.py`: `cmd_render_compose`, around L482;
  - `setup/setup.sh`: after the GPU/System One gate closes (the `fi` right before `log_step "Step 8/8  Computing resource limits"`, around L286), so it also runs in no-GPU and llm-server-disabled setups; the compose side renders `omniroute` regardless of `llm_server_enabled` too.
- Test: `tests/test_compose.py`, `tests/test_cli.py`, `tests/test_shell.py`

**Interfaces:**
- Consumes: `cfg["smart_routing"]` (Task 2), the image labels (Task 7).
- Produces:
  - with `enabled`, the omniroute service uses `smart_routing.image` and sets `OMNIROUTE_STRATEGIES_DIR`;
  - `env_file: [<config dir>/omniroute-smart.env]` is added only when that file exists at render time;
  - setup builds on a label mismatch and creates the env file with mode 0600 if it's missing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_compose.py
SMART = {"enabled": True, "base_image": "docker.io/diegosouzapw/omniroute@sha256:" + "a" * 64, "image": "localhost/llm-env-omniroute-smart:latest"}


def smart_compose(env_file):
    text = render_compose(
        {**CFG, "smart_routing": SMART},
        models_dir="/home/user/llm-workspace/models",
        presets_path="/home/user/.config/llm-env/presets.ini",
        repo_root="/home/user/llm-env",
        smart_env_file=env_file,
    )
    return yaml.safe_load(text)["services"]["omniroute"]


def test_omniroute_uses_the_derived_image_when_smart_routing_enabled(tmp_path):
    env_file = tmp_path / "omniroute-smart.env"
    env_file.write_text("")
    service = smart_compose(env_file)
    assert service["image"] == "localhost/llm-env-omniroute-smart:latest"
    assert service["environment"]["OMNIROUTE_STRATEGIES_DIR"] == "/opt/omniroute-smart/strategies"
    assert service["env_file"] == [str(env_file)]


def test_omniroute_skips_env_file_when_missing(tmp_path):
    assert "env_file" not in smart_compose(tmp_path / "absent.env")


def test_omniroute_unchanged_when_smart_routing_disabled():
    _, document = compose_dict()
    assert "OMNIROUTE_STRATEGIES_DIR" not in document["services"]["omniroute"]["environment"]
```

Thread the env file through the live render path. The live compose file is written by `setup/render-unit.sh:75` → `llmenv render-compose` → `cmd_render_compose` (`llmenv.py:482`) → `write_compose` (`pylib/compose.py:318`) → `render_compose`.
- Add the keyword-only parameter `smart_env_file: str | Path | None = None` to both `render_compose` and `write_compose`. `write_compose` passes it through.
- In `cmd_render_compose`, pass `smart_env_file=Path(args.config).parent / "omniroute-smart.env"`.

Add a pass-through test in `tests/test_cli.py`, following its existing `render-compose` tests (`rg -n "render-compose" tests/test_cli.py | head`). Run `render-compose` with a temp config that has `smart_routing.enabled: true` and an existing empty `omniroute-smart.env` next to the config. Assert that the written YAML's `services.omniroute.env_file` equals `[<that path>]`.

`render-unit.sh` runs from `scripts/start.sh:130`, after `make setup` has created the env file (Step 3), so the file exists on the first render.

```python
# tests/test_shell.py
def test_setup_creates_private_smart_env_and_builds_on_label_mismatch(tmp_path: pathlib.Path) -> None:
    result, calls, config = run_setup_no_gpu(tmp_path, smart_routing_enabled=True)
    assert result.returncode == 0, result.stdout + result.stderr
    env_file = config.parent / "omniroute-smart.env"
    assert env_file.exists() and stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "omniroute-smart-build.sh" in calls.read_text() or "podman build" in calls.read_text()
```

Drive `setup/setup.sh` through the existing `run_setup_no_gpu` harness (tests/test_shell.py, around L682), which already runs setup non-interactively with stubbed commands. No existing test drives setup through `run_lifecycle_script`.
- Add a `smart_routing_enabled: bool = False` keyword that writes the Task 2 `smart_routing` defaults with `enabled` set into its config.
- Add the Task 7 `node` stub.
- Keep its `podman` stub's defaults: `image exists` exits 0 and every `image inspect` label query prints `gfx1201` (`tests/test_shell.py` around L743), which is a stale base/patch/tree label. So the test pins the **label-mismatch** rebuild, not just the missing-image path. Also assert that `calls` records the three label reads (`llm-env.omniroute-smart.base`, `.patch`, `.tree`).

`run_setup_no_gpu` returns `(result, calls, config)`.

- [ ] **Step 2: Run and see them fail**

Run: `uv run pytest tests/test_compose.py tests/test_shell.py -k "smart" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `pylib/compose.py`, after `omniroute_service` is built:

```python
    smart = cfg.get("smart_routing", {})
    if smart.get("enabled"):
        omniroute_service["image"] = _dollar_escape(smart.get("image", DEFAULT_SMART_ROUTING_IMAGE))
        omniroute_service["environment"]["OMNIROUTE_STRATEGIES_DIR"] = "/opt/omniroute-smart/strategies"
        # Keys reach the container env via podman; the patched loader scopes them per plugin.
        if smart_env_file is not None and Path(smart_env_file).exists():
            omniroute_service["env_file"] = [str(smart_env_file)]
```

Add `from pylib.config import DEFAULT_SMART_ROUTING_IMAGE` to `pylib/compose.py`'s imports (it imports only from `pylib.resources` today); if that creates an import cycle, move the two `DEFAULT_SMART_ROUTING_*` constants into a module both can import.

In `setup/setup.sh`, right after the `fi` that closes the GPU/System One gate and before `log_step "Step 8/8  Computing resource limits"`:

```bash
if [ "$(yq -r '.smart_routing.enabled' "$CONFIG_PATH")" = "true" ]; then
    require_cmd node find sha256sum   # the label helpers need them; node is only a development prerequisite otherwise
    smart_env="$(dirname "$CONFIG_PATH")/omniroute-smart.env"
    if [ ! -f "$smart_env" ]; then (umask 077; : > "$smart_env"); fi
    chmod 600 "$smart_env"
    smart_image="$(yq -r '.smart_routing.image' "$CONFIG_PATH")"
    smart_base="$(yq -r '.smart_routing.base_image' "$CONFIG_PATH")"
    smart_patch="$(omniroute_smart_patch_version)"
    smart_tree="$(omniroute_smart_tree_hash)"
    have_base=""; have_patch=""; have_tree=""
    if podman image exists "$smart_image"; then
        have_base="$(podman image inspect --format '{{index .Labels "llm-env.omniroute-smart.base"}}' "$smart_image" 2>/dev/null || true)"
        have_patch="$(podman image inspect --format '{{index .Labels "llm-env.omniroute-smart.patch"}}' "$smart_image" 2>/dev/null || true)"
        have_tree="$(podman image inspect --format '{{index .Labels "llm-env.omniroute-smart.tree"}}' "$smart_image" 2>/dev/null || true)"
    fi
    if [ "$have_base" != "$smart_base" ] || [ "$have_patch" != "$smart_patch" ] || [ "$have_tree" != "$smart_tree" ]; then
        bash "${REPO_DIR}/scripts/omniroute-smart-build.sh" || die "OmniRoute smart image build failed (patch anchors may have moved; see the build output)"
    fi
fi
```

- [ ] **Step 4: Run the tests and the full suite**

Run: `uv run pytest tests/test_compose.py tests/test_shell.py -k smart -v && bash tools/validate.sh && bash tools/test.sh`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pylib/compose.py setup/setup.sh tests/test_compose.py tests/test_shell.py
git commit -m "feat(omniroute-smart): compose image swap, scoped env_file, setup rebuild on label mismatch"
```

---

### Task 9: Spike harness (mock upstream, probe, Laya compare, probe plugin)

**Files:**
- Create: `omniroute-smart/spike/mock-upstream.mjs`, `omniroute-smart/spike/probe.mjs`, `omniroute-smart/spike/laya-batch-compare.mjs`
- Create: `omniroute-smart/spike/plugins/smart-catalog-probe/plugin.json`, `.../index.js`, and the same pair under `omniroute-smart/spike/plugins/other-probe/`
- Test: `omniroute-smart/spike/test/mock-upstream.test.mjs`

**Interfaces:**
- Consumes: the classifier `QUESTIONS` (Task 6).
- Produces:
  - **Mock upstream:** `mock-upstream.mjs --port N` serves `POST /v1/chat/completions` and `GET /v1/models`. Its behaviour depends on the request's `model`:

    | Model contains | Response |
    |---|---|
    | `fail` | 503 |
    | `slow` | after 3 s |
    | anything else | 200 with content `"mock:<model>"` |

    Each request is logged to stdout as JSON `{model, status}`.
  - **`probe.mjs <cmd> [json]`:** runs inside the container on loopback and prints JSON. Commands: `create-combo`, `get-combo`, `put-combo`, `chat`, `plugins`, `install`, `activate`, `providers`, `list-providers`, `models`, `plugin-config`, `put-plugin-config`.

- [ ] **Step 1: Write the failing mock test**

```js
// omniroute-smart/spike/test/mock-upstream.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { startMock } from "../mock-upstream.mjs";

test("mock answers by model name", async () => {
  const srv = await startMock(0);
  const url = `http://127.0.0.1:${srv.address().port}/v1/chat/completions`;
  const ok = await fetch(url, { method: "POST", body: JSON.stringify({ model: "m-ok", messages: [] }) });
  assert.equal((await ok.json()).choices[0].message.content, "mock:m-ok");
  const bad = await fetch(url, { method: "POST", body: JSON.stringify({ model: "m-fail", messages: [] }) });
  assert.equal(bad.status, 503);
  srv.close();
});
```

- [ ] **Step 2: Implement the harness**

```js
// omniroute-smart/spike/mock-upstream.mjs
// OpenAI-compatible mock for P1 spikes. Behaviour by model name: *fail* → 503, *slow* → 3 s delay, else 200.
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { realpathSync } from "node:fs";
import { pathToFileURL } from "node:url";

// a short hash of the last user message: lets S3 check which calls carried a given text without logging it
const textHash = (body) => {
  const last = [...(body.messages ?? [])].reverse().find((m) => m.role === "user")?.content;
  return typeof last === "string" ? createHash("sha256").update(last).digest("hex").slice(0, 12) : null;
};

export function startMock(port) {
  const srv = createServer(async (req, res) => {
    let raw = ""; for await (const c of req) raw += c;
    if (req.url.endsWith("/models")) { res.end(JSON.stringify({ data: ["m-ok", "m-fail", "m-slow", "m-judge"].map((id) => ({ id })) })); return; }
    const body = raw ? JSON.parse(raw) : {};
    const { model = "" } = body;
    const status = model.includes("fail") ? 503 : 200;
    if (model.includes("slow")) await new Promise((r) => setTimeout(r, 3000));
    console.log(JSON.stringify({ model, status, text: textHash(body) }));
    res.writeHead(status, { "content-type": "application/json" });
    res.end(JSON.stringify(status === 200
      ? { id: "x", object: "chat.completion", model, choices: [{ index: 0, message: { role: "assistant", content: `mock:${model}` }, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } }
      : { error: { message: "mock failure" } }));
  });
  return new Promise((r) => srv.listen(port, "0.0.0.0", () => r(srv)));
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  const port = Number(process.argv[process.argv.indexOf("--port") + 1] || 18080);
  startMock(port).then(() => console.error(`mock upstream on :${port}`));
}
```

```js
// omniroute-smart/spike/probe.mjs
// Runs INSIDE the throwaway container: loopback calls to OmniRoute's API. Prints JSON only.
// Auth: dashboard login with SPIKE_PASSWORD (generated per run), cookie reused.
const B = `http://127.0.0.1:${process.env.PORT || 20128}`;
const [cmd, arg] = process.argv.slice(2);
const body = arg ? JSON.parse(arg) : {};
const login = await fetch(`${B}/api/auth/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ password: process.env.SPIKE_PASSWORD }) });
const cookie = (login.headers.get("set-cookie") || "").split(";")[0];
const call = async (method, path, payload, extra = {}) => {
  const r = await fetch(`${B}${path}`, { method, headers: { "content-type": "application/json", cookie, ...extra }, body: payload ? JSON.stringify(payload) : undefined });
  const t = await r.text(); let j; try { j = JSON.parse(t); } catch { j = t.slice(0, 500); }
  return { status: r.status, body: j };
};
const out = {
  "create-combo": () => call("POST", "/api/combos", body),
  "get-combo": () => call("GET", `/api/combos/${body.id}`),
  "put-combo": () => call("PUT", `/api/combos/${body.id}`, body.combo),
  providers: () => call("POST", "/api/providers", body),
  "list-providers": () => call("GET", "/api/providers"),
  models: () => call("GET", "/v1/models"),
  "plugin-config": () => call("GET", `/api/plugins/${body.name}/config`),
  // Save route: confirm the method in the pinned image (rg -n 'export async function (PUT|POST|PATCH)' in the plugins/[name]/config route chunk) and record it in S5.
  "put-plugin-config": () => call("PUT", `/api/plugins/${body.name}/config`, { config: body.config }),
  plugins: () => call("GET", "/api/plugins"),
  install: () => call("POST", "/api/plugins", body),
  activate: () => call("POST", `/api/plugins/${body.name}/activate`),
  chat: () => call("POST", "/v1/chat/completions", body.request, body.headers || {}),
}[cmd];
console.log(JSON.stringify(await out()));
```

```js
// omniroute-smart/spike/laya-batch-compare.mjs
// Compares the five questions batched vs one-per-request vs parallel on 20 inputs; prints latency p50/p95 and flips.
import { QUESTIONS } from "../shared/classifier-questions.mjs";
const URL_ = process.argv[2] || "http://127.0.0.1:8010/v1/systemone";
const INPUTS = [
  "Rename the variable foo to bar in utils.py", "Plan the migration of our auth service to OAuth2 with a rollout strategy",
  "Review this pull request for security issues in the payment API", "Fix the CSS so the navbar stays fixed on mobile",
  "Write a Dockerfile for the Python worker", "Audit the database schema for missing indexes",
  "Move all helpers from lib/ to src/utils/", "Design the architecture for a multi-tenant billing system",
  "Add a unit test for the date parser edge cases", "Implement pagination in the /orders endpoint",
  "Format all JSON files with two-space indentation", "Review the deployment plan for the Kubernetes upgrade",
  "Build a React form with validation for user signup", "Explain why this SQL query is slow and fix it",
  "Plan the refactor of the monolith into three services", "Update the README with the new make targets",
  "Audit the frontend for accessibility problems", "Rewrite the retry loop to use exponential backoff",
  "Rename the CI job from build to compile", "Plan and review a data retention policy implementation",
];
const post = async (questions, text) => { const t0 = performance.now();
  const r = await fetch(URL_, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ model: "jev-1", state: { request: text }, questions }) });
  return { ms: performance.now() - t0, json: await r.json() }; };
const pick = (a) => a.choice ?? a.noul ?? Object.entries(a.probabilities ?? {}).sort((x, y) => y[1] - x[1])[0]?.[0];
const pct = (xs, p) => [...xs].sort((a, b) => a - b)[Math.min(xs.length - 1, Math.floor(xs.length * p))];
const batched = [], separate = [], parallel = []; let flips = 0;
for (const text of INPUTS) {
  const b = await post(QUESTIONS, text); batched.push(b.ms);
  let sepMs = 0; const sep = {};
  for (const [k, q] of Object.entries(QUESTIONS)) { const s = await post({ [k]: q }, text); sepMs += s.ms; sep[k] = s.json.answers?.[k] ?? s.json[k]; }
  separate.push(sepMs);
  const t0 = performance.now(); await Promise.all(Object.entries(QUESTIONS).map(([k, q]) => post({ [k]: q }, text))); parallel.push(performance.now() - t0);
  for (const k of Object.keys(QUESTIONS)) { const bv = (b.json.answers ?? b.json)[k]; if (bv && sep[k] && String(pick(bv)) !== String(pick(sep[k]))) flips++; }
}
console.log(JSON.stringify({ inputs: INPUTS.length, flips,
  batched_ms: { p50: pct(batched, 0.5), p95: pct(batched, 0.95) },
  separate_sum_ms: { p50: pct(separate, 0.5), p95: pct(separate, 0.95) },
  parallel_wall_ms: { p50: pct(parallel, 0.5), p95: pct(parallel, 0.95) } }));
```

```json
{ "name": "smart-catalog-probe", "version": "0.1.0", "main": "index.js",
  "description": "P1 spike: reports which OMNIROUTE_PLUGIN_SMART_CATALOG_PROBE_* key names it received and runs a 60s timer",
  "requires": { "permissions": ["env", "file-write"] }, "hooks": { "onActivate": true }, "enabledByDefault": false }
```

```js
// omniroute-smart/spike/plugins/smart-catalog-probe/index.js
// Writes key NAMES only (never values) and a tick counter to /tmp/<plugin dir name>.json. The same file is the
// entry of other-probe/, so S5 can check env isolation in both directions.
const { writeFileSync } = require("node:fs");
const { basename } = require("node:path");
let ticks = 0;
const report = () => writeFileSync(`/tmp/${basename(__dirname)}.json`, JSON.stringify({
  keys: Object.keys(process.env).filter((k) => k.startsWith("OMNIROUTE_PLUGIN_")).sort(), ticks, at: new Date().toISOString() }));
module.exports = { onActivate() { report(); setInterval(() => { ticks++; report(); }, 60_000); } };
```

`omniroute-smart/spike/plugins/other-probe/` holds an identical `index.js` (copy it) and a `plugin.json` that differs only in `"name": "other-probe"` and its description.

The loader fires a lifecycle hook only when the manifest declares it (`hooks.onActivate: true`, upstream `src/lib/plugins/manager.ts`), and it loads the entry with `import()` and uses `plugin.default || plugin` (`loader.ts:161-162`), so `module.exports` works. Before writing `plugin.json`, confirm these manifest keys (`requires.permissions`, `hooks.onActivate`, `enabledByDefault`) and the loader behaviour against the image's own `docs/frameworks/PLUGIN_MARKETPLACE.md` (from the Task 5 extracted tree, or `podman run --rm --entrypoint cat <image> /app/docs/frameworks/PLUGIN_MARKETPLACE.md`). If the pinned image differs, adapt and record the shape in the report.

- [ ] **Step 3: Run the tests and validate**

Run: `node --test 'omniroute-smart/**/*.test.mjs' && bash tools/validate.sh`
Expected: PASS. `probe.mjs` and `laya-batch-compare.mjs` only need to pass `node --check` here; they run in Task 10.

- [ ] **Step 4: Commit**

```bash
git add omniroute-smart/spike
git commit -m "test(omniroute-smart): spike harness (mock upstream, probe, Laya compare, probe plugin)"
```

---

### Task 10: Run spikes S1, S3, S4, S5 on a throwaway container

This is an execution task. One subagent runs it. It needs Playwright MCP tools for S4. Every result goes into `spike-report-p1.md`.

**Files:**
- Modify: `docs/prds/omniroute-smart-routing-research/spike-report-p1.md`

**Interfaces:**
- Consumes: the derived image (Task 7) and the harness (Task 9).
- Produces: report sections S1 runtime, S3, S4 and S5, each with PASS/FAIL against the PRD's criteria and the measured numbers.

- [ ] **Step 1: Start the mock and a throwaway container (never the production one)**

The container is started with `podman compose` and an `env_file:`, the same way production will get its keys (PRD S5). The only mounts allowed are read-only binds of the repo's `omniroute-smart/spike` and `omniroute-smart/strategies` (see Global Constraints). The strategies bind lets later steps change `spike-order.mjs` and then `podman restart` the container instead of rebuilding. The patched strategy *names* were fixed at build time, and the file name doesn't change.

Run Steps 1–6 from the repo root (the binds use `$PWD`) in **one** persistent shell session (one terminal, or one script the executor writes to the scratchpad and sources step by step), so `SPK`, `MOCK_PID`, `P`, `chat`, `wait_health` and the cleanup trap survive between steps. If the session dies, run the Step 6 cleanup by hand first, and check that `omniroute-smart/strategies/spike-order.mjs` exists (restore it from `spike-order.mjs.bak` if not).

```bash
SPK="$(mktemp -d)"; chmod 700 "$SPK"
# cleanup first, so a failure at any later point leaves nothing behind (incl. the step-11 rename of the canary)
cleanup() {
  podman compose -f "$SPK/compose.yml" down >/dev/null 2>&1; podman rm -f omniroute-smart-spike omniroute-smart-spike-base >/dev/null 2>&1
  [ -n "${MOCK_PID:-}" ] && kill "$MOCK_PID" 2>/dev/null
  [ -f omniroute-smart/strategies/spike-order.mjs.bak ] && mv omniroute-smart/strategies/spike-order.mjs.bak omniroute-smart/strategies/spike-order.mjs
  rm -rf "$SPK"
}
trap cleanup EXIT
node omniroute-smart/spike/mock-upstream.mjs --port 18080 > "$SPK/mock.log" 2>&1 &
MOCK_PID=$!
SPIKE_PASSWORD="$(openssl rand -hex 16)"
( umask 077; printf '%s\n' "INITIAL_PASSWORD=$SPIKE_PASSWORD" \
    OMNIROUTE_PLUGIN_SMART_CATALOG_PROBE_TEST=present OMNIROUTE_PLUGIN_OTHER_PROBE_TEST=present OMNIROUTE_PLUGIN_OTHER_SECRET=present TYPESAFE_API_KEY=present > "$SPK/spike.env" )
cat > "$SPK/compose.yml" <<EOF
services:
  omniroute-smart-spike:
    image: localhost/llm-env-omniroute-smart:latest
    container_name: omniroute-smart-spike
    ports: ["127.0.0.1:29128:20128"]
    environment: { PORT: "20128", OMNIROUTE_ALLOW_PRIVATE_PROVIDER_URLS: "true" }
    env_file: [ "$SPK/spike.env" ]
    volumes:
      - "$PWD/omniroute-smart/spike:/opt/spike:ro,z"
      - "$PWD/omniroute-smart/strategies:/opt/omniroute-smart/strategies:ro,z"
EOF
podman compose -f "$SPK/compose.yml" up -d
wait_health() {
    for _ in $(seq 60); do
        curl -sf http://127.0.0.1:29128/api/health >/dev/null 2>&1 || curl -sf http://127.0.0.1:29128/ >/dev/null 2>&1 && return 0
        sleep 2
    done
    podman logs --tail 100 omniroute-smart-spike; echo "spike container not healthy after 120 s" >&2; return 1
}
wait_health || exit 1
P() { podman exec -e SPIKE_PASSWORD="$SPIKE_PASSWORD" omniroute-smart-spike node /opt/spike/probe.mjs "$@"; }
chat() { P chat "{\"request\":{\"model\":\"$1\",\"messages\":[{\"role\":\"user\",\"content\":\"$2\"}]}}"; }
podman exec omniroute-smart-spike sh -c 'test "$TYPESAFE_API_KEY" = present && test "$OMNIROUTE_PLUGIN_SMART_CATALOG_PROBE_TEST" = present' && echo "env_file delivered"
```

Expected:
- the container is healthy (use the health path `healthcheck.mjs` probes if `/api/health` differs);
- `env_file delivered` is printed. That is the S5 "env_file puts the keys in the container env" check; record it.

The strategy-process read of `TYPESAFE_API_KEY` is checked in S1 step 8: `SPIKE:ctx` reports `typesafeKeyPresent` (presence only). Record it in the S5 section.

- [ ] **Step 2: S1 runtime checks**

1. **Provider connections.** Create two connections to the mock with the llama-cpp provider shape from `pylib/omniroute.py:build_payload`:
   - `P providers '{"provider":"llama-cpp","name":"mock-a","apiKey":"x","priority":1,"testStatus":"active","providerSpecificData":{"baseUrl":"http://host.containers.internal:18080/v1"}}'`
   - the same for `mock-b`.
2. **Combo creation.** Create `spike-combo`:
   - strategy `spike-order`, members `llama-cpp/m-fail` then `llama-cpp/m-ok`, so the reversed order tries `m-ok` first. Use the model-id format `P models '{}'` (`GET /v1/models`) shows for the new connections. Expect **201**.
   - a combo with `strategy:"bogus"`: expect **400**.
3. **Ordering.** Chat through `spike-combo`. Expect 200 with `mock:m-ok`. The mock log shows `m-ok` first.
4. **Fallback.** Swap members so the reversed order tries `m-fail` first. Chat: the mock log shows `m-fail` 503, then `m-ok` 200, and the client gets 200.
5. **Prelude.** `chat spike-combo "SPIKE:503"`. Expect 503 with `{"error":{"type":"spike"}}` and **no** mock request logged.
6. **Built-ins.** A `priority` combo with the same members picks the same first target as on an unpatched image. Compare directly against a second throwaway container from the **base** image:
   - `podman run -d --name omniroute-smart-spike-base -p 127.0.0.1:29129:20128 -e PORT=20128 -e OMNIROUTE_ALLOW_PRIVATE_PROVIDER_URLS=true --env-file "$SPK/spike.env" -v "$PWD/omniroute-smart/spike:/opt/spike:ro,z" "$(yq -r '.smart_routing.base_image // "docker.io/diegosouzapw/omniroute@sha256:3d387b3d392d686817daa4e74ececda5b4b4b2992e1d7550cca41f79bb4389a3"' ~/.config/llm-env/models.yml)"` (the same pinned default as Task 5: the operator's config is not migrated by this plan), then wait for it like `wait_health` (port 29129);
   - create the same two connections and a `priority` combo with the same members on both containers, chat once on each, and compare the first model each one sent to the mock (mock log order);
   - `podman rm -f omniroute-smart-spike-base` right after, and add it to the trap and Step 6's cleanup. Never call production.
7. **Hook overhead.** Send 200 chats on `spike-combo` (to `m-ok`), then `chat spike-combo "SPIKE:ctx"`: the canary reports `hookP95Ms`, the p95 of the per-request in-process time spent in the whole injected path: site 3 (runtime, strategy load, prelude), site 2 (runtime, load, order) and site 5's wrapper work, measured with `performance.now()` by the patched code itself, so HTTP and upstream variance do not count. It must be < 1 ms. Also record, informational only, the end-to-end p95 of 50 chats with `spike-order` vs `priority`.
8. **Context keys and store.** `chat spike-combo "SPIKE:ctx"` returns `ctxKeys`, `observed`, `orderSawStore` and `observeSawStore`, read from the server's own globals by the canary.
   - Record the reachable keys, including the Cursor/Claude usage fetchers, and compare them with Task 5's report.
   - **S1 PASS requires** `orderSawStore === true`: the per-request store entered at site 3 is visible at site 2. Without it, S1 is a no-go (P3's fail-closed design needs it).
   - `observeSawStore` must be `true` when Task 5 found site 5. If Task 5 recorded site 5 as unreachable, record `observeSawStore: n/a` and the P3 impact item (no `observe()`: no failure EWMA, no per-attempt decision log). `false` with site 5 present is an S1 defect to fix before GO. `hookP95Ms` then covers sites 3 and 2 only.
9. **Usage history.** `podman exec omniroute-smart-spike sh -c 'ls /app/data'`, then copy the SQLite file out (it is the throwaway volume's) and run `sqlite3 <file> 'PRAGMA table_info(usage_history)'` on the host. Report whether `status` and `combo_strategy` exist and which column holds the time, with its storage type. Then check the values, not just the columns: `sqlite3 <file> "SELECT <model column>, status, combo_strategy, <time column> FROM usage_history ORDER BY rowid DESC LIMIT 6"` must show the step-4 fallback request as two rows, `m-fail` with a 503 (or the error class OmniRoute stores) and `m-ok` with a 200, both with `combo_strategy` `spike-order`. Report exactly what each row holds; P5's 7-day baseline relies on this (a failed attempt not recorded as its own row means the baseline must come from elsewhere).
10. **Laya.** `node omniroute-smart/spike/laya-batch-compare.mjs http://127.0.0.1:8010/v1/systemone` against the live System One. This is read-only use, already allowed for host clients. Record the JSON.
11. **Missing strategy module.** On the host, `mv omniroute-smart/strategies/spike-order.mjs omniroute-smart/strategies/spike-order.mjs.bak`, then `chat spike-combo "hello"` without restarting. Record the client status and body, and whether the server log shows the load error.
    - Then `podman restart omniroute-smart-spike && wait_health` and repeat the chat. Record status and body again, and whether `POST /api/combos` with `strategy:"spike-order"` is now rejected.
    - Restore: `mv omniroute-smart/strategies/spike-order.mjs.bak omniroute-smart/strategies/spike-order.mjs`, `podman restart omniroute-smart-spike && wait_health`, and confirm step 3 passes again.
    - Expected before the restart: 200, because `runtime.mjs` caches loaded modules in `g.modules`. Record it; it is not a defect.
    - Expected after the restart: `503` with `error.reason` = `strategy_unavailable` (site 3's `__g.unavailable`), never a silent fallback to another strategy. Anything else is an S1 defect: fix site 3 and re-run, or record it in the Task 13 impact list for P3/P4.

- [ ] **Step 3: S3 fusion through the injected `handleFusionChat`**

The PRD criterion is that **the same text** goes single or panel + judge depending on conditions, so the lane is a condition, not part of the text. Extend `spike-order.mjs` with a module-level `let lane = "single"` and a command `SPIKE:lane <single|fusion|fusion-one-fail|fusion-all-fail>` that sets it and answers `{lane}`. For any non-command chat, when `lane` starts with `fusion` and the body carries no `tools`, the prelude calls `globalThis.__omniSmart.ctx.handleFusionChat({body, models, judgeModel: "llama-cpp/m-judge", tuning: {panelHardTimeoutMs: 10000, minPanel: 2, stragglerGraceMs: 2000}, ...})` with `models` = `[m-ok, m-slow]` (`fusion`), `[m-ok, m-fail]` (`fusion-one-fail`) or `[m-fail, m-fail]` (`fusion-all-fail`), using the argument shape Task 5 recorded, and returns its response; otherwise it returns `null` (single lane through the combo loop).

Then, with one fixed text `T="Compare two caching strategies for this API."` and its hash `H="$(printf '%s' "$T" | sha256sum | cut -c1-12)"` (the mock logs that hash of each call's last user message as `text`), note the mock log line count before each send and read only the new lines after it. "Carrying `T`" below means the line's `text` equals `$H`:
1. `SPIKE:lane single`, chat `T`: exactly one upstream chat call, for a combo member, carrying `T`.
2. `SPIKE:lane fusion`, chat `T`: two panel calls (`m-ok`, `m-slow`) carrying `T`, then one judge call (whichever model upstream picks; record it), and a 200.
3. Still `fusion`, chat `T` with a `tools` array: exactly one call, as in 1 (tools force single).
4. `SPIKE:lane fusion-one-fail`, then `fusion-all-fail`, chat `T` each time: the paths below.
5. `SPIKE:lane single` at the end.

The repo's `omniroute-smart/strategies` is bind-mounted, so edit the file, `podman restart omniroute-smart-spike`, and run `wait_health`. Commit this canary extension with the report. Record:
- the panel and judge requests in the mock log;
- that a tool-bearing body goes single;
- the one-survivor path, with one member `m-fail`: direct answer vs judge;
- the zero-survivor path, with both `m-fail`: status and body;
- latency;
- whether `handleSingleModel` itself applies the combo's forbidden-substring / HTTP-200 error-text rules (read its body in the extracted `$W` tree from Task 5), and, if not, whether `ctx.checkComboResponse` is reachable: check it on a real answer: give a copy of `spike-combo` one forbidden-substring rule in the field shape an existing coding combo uses, with the substring `mock:m-ok` (the mock's answer text for `m-ok`), and (a) chat on it normally: the combo loop must fall back past `m-ok` (mock log shows the next target), which proves the rule works on this image; (b) have the canary's `SPIKE:lane fusion` path call `ctx.checkComboResponse(combo, <the m-ok panel answer>)` and return the result, and record `{fallback, reason}` (expected: fallback true). P4 inherits the rules through whichever of the two exists; if neither, P4 keeps fusion off.

- [ ] **Step 4: S4 dashboard editor (Playwright MCP)**

Log in at `http://127.0.0.1:29128` with `SPIKE_PASSWORD`. Open the combo editor for `spike-combo`, change nothing, and save. Then `P get-combo '{"id":"<id>"}'`. **PASS** if the strategy is still `spike-order` and the config is unchanged. Otherwise record what the UI did; the PRD fallback is to edit smart combos through the API only. Take one screenshot of the editor's strategy field for the report.

If no browser tool (Playwright MCP or Claude in Chrome) is available in the executor's session, do not skip S4 silently:
- record "S4 deferred: no browser tool" in the report;
- leave the container down after Step 6 as usual;
- list S4 as an open item in Task 13, to be run by the user (or a session with a browser tool) against a fresh spike container before P5 seeds any smart combo. Until then, P5 treats smart combos as API-only.

- [ ] **Step 5: S5 plugin env and timer**

```bash
podman exec omniroute-smart-spike sh -c 'mkdir -p /tmp/p && cp -r /opt/spike/plugins/smart-catalog-probe /opt/spike/plugins/other-probe /tmp/p/'
P install '{"path":"/tmp/p/smart-catalog-probe"}'; P install '{"path":"/tmp/p/other-probe"}'
P activate '{"name":"smart-catalog-probe"}'; P activate '{"name":"other-probe"}'
sleep 190; podman exec omniroute-smart-spike cat /tmp/smart-catalog-probe.json /tmp/other-probe.json
```

**PASS** when all of these hold:
- isolation in both directions: `smart-catalog-probe`'s `keys` is exactly `["OMNIROUTE_PLUGIN_SMART_CATALOG_PROBE_TEST"]` (no `OTHER_*`), and `other-probe`'s is exactly `["OMNIROUTE_PLUGIN_OTHER_PROBE_TEST"]` (no `SMART_CATALOG_*`);
- `ticks ≥ 3`;
- after `podman restart omniroute-smart-spike` and the activate call again, ticks resume.

Also record these facts, which P2 depends on:
- **Activate on an active plugin.** Before the restart, call `P activate '{"name":"smart-catalog-probe"}'` a second time on the already-active plugin. Record the HTTP status, and whether `ticks` resets (child restarted) or keeps counting.
- **Loopback trust.** Run `podman exec omniroute-smart-spike node -e "fetch('http://127.0.0.1:20128/api/plugins/smart-catalog-probe/config').then(r=>console.log(r.status))"`. A 200 means loopback callers need no management key; a 401 or 403 means the key is required. That process is a non-plugin loopback caller, the same as the plugin child.
- **Export shape.** Record the plugin export shape the loader accepted.
- **Plugin config save route.** Find the method the pinned image exports for `/api/plugins/[name]/config` besides GET, then run `P put-plugin-config '{"name":"smart-catalog-probe","config":{}}'` (adjusting the method in `probe.mjs` if it is not PUT). Record the method, the body shape and the status; P2–P4 use this command to change plugin settings.

The full 24 h timer check is moved to P5 monitoring. Record that in the report.

- [ ] **Step 6: Clean up and verify**

```bash
cleanup; trap - EXIT; podman ps -a --format '{{.Names}}' | rg -c omniroute-smart-spike || echo "0 spike containers"
```

Expected: `0 spike containers`. The production `omniroute` container's `podman inspect --format '{{.State.StartedAt}}' omniroute` is unchanged from before the task. Record both timestamps.

- [ ] **Step 7: Commit the report**

```bash
git add docs/prds/omniroute-smart-routing-research/spike-report-p1.md omniroute-smart/strategies/spike-order.mjs
git commit -m "docs(spike): P1 S1/S3/S4/S5 results"
```

---

### Task 11: S2 — rebuild against the newest `:next`

**Files:**
- Modify: `docs/prds/omniroute-smart-routing-research/spike-report-p1.md` (section S2)
- Modify (only on exit 2): `omniroute-smart/patch/sites.mjs`, `omniroute-smart/patch/patch-omniroute.mjs` (layout probe dispatch), `omniroute-smart/patch/test/*`

**Interfaces:**
- Consumes: the Task 7 build script with an overridden base.
- Produces: an S2 verdict, plus the anchor rewrite if one was needed (a new `PATCH_VERSION` and a separate site set selected by base digest).

- [ ] **Step 1: Resolve the newest digest without changing any config**

```bash
podman pull docker.io/diegosouzapw/omniroute:next
NEW="docker.io/diegosouzapw/omniroute@$(podman image inspect --format '{{.Digest}}' docker.io/diegosouzapw/omniroute:next)"; echo "$NEW"
```

- [ ] **Step 2: Try the build against it in a temporary config copy**

Use the same temporary-config method as Task 7 Step 4, with `.smart_routing.base_image = "$NEW"` and `.smart_routing.image = "localhost/llm-env-omniroute-smart:s2"`.

Expected: either PASS (built unchanged), or exit 2 with the JSON count report. If the digest equals the pinned one, record "no newer `:next`; S2 deferred to the first newer digest". Then stop here.

- [ ] **Step 3: On exit 2, rewrite the anchors (time box: 2 h)**

Repeat Task 5 Steps 1–4 against `$NEW`. The registry refactor (#16206) is expected to move the `ordering-hook` anchor into `applyStrategyOrdering`. Add the new site set as `buildSitesRegistry()` in `sites.mjs`, selected by a `layout` probe: the presence of a registry literal in the tree. Do not use a digest list. Add fixture tests. Bump `PATCH_VERSION`.

PASS if the rewrite lands within the time box. Otherwise S2 FAIL: keep the pinned digest, and the upstream PR becomes a prerequisite for moving off it. Record which.

- [ ] **Step 4: Clean up and commit**

```bash
podman rmi -f localhost/llm-env-omniroute-smart:s2 2>/dev/null || true
git add omniroute-smart/patch docs/prds/omniroute-smart-routing-research/spike-report-p1.md
git commit -m "spike(omniroute-smart): S2 rebuild against newest :next"
```

---

### Task 12: `check-server` rows for smart routing

**Files:**
- Modify: `scripts/check-server.sh` (new section after System One)
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `smart_routing.*` and the image labels.
- Produces these rows, each a PASS or FAIL when `smart_routing.enabled` (P2/P3 add plugin, scorecard and System One rows):
  - `smart-routing image is the derived build`: the running `omniroute` container's image labels match: `llm-env.omniroute-smart.patch` equals `PATCH_VERSION`, `llm-env.omniroute-smart.base` equals the config, and `llm-env.omniroute-smart.tree` equals `omniroute_smart_tree_hash` (Task 7 helpers in `tools/lib.sh`, which check-server already sources);
  - `smart-routing env file is private`: `omniroute-smart.env` has mode 600.

  When disabled, there is one SKIP row.

- [ ] **Step 1: Write the failing tests** (pattern: the existing System One check-server tests with a stubbed `podman inspect`)

```python
def test_check_server_reports_smart_routing_rows_when_enabled(tmp_path: pathlib.Path) -> None:
    result, _ = run_check_server(tmp_path, {}, llm_server_enabled=False, smart_routing_enabled=True)
    output = result.stdout + result.stderr
    assert "PASS identity=smart-routing image is the derived build" in output
    assert "PASS identity=smart-routing env file is private" in output


def test_check_server_fails_smart_routing_on_stale_tree_label(tmp_path: pathlib.Path) -> None:
    result, _ = run_check_server(tmp_path, {}, llm_server_enabled=False, smart_routing_enabled=True,
                                 smart_tree_label="0000000000000000")
    assert "FAIL identity=smart-routing image is the derived build" in result.stdout + result.stderr


def test_check_server_fails_when_the_running_container_uses_an_older_image(tmp_path: pathlib.Path) -> None:
    # the tag was rebuilt, but the container still runs the old image ID: labels must be read from that ID
    result, _ = run_check_server(tmp_path, {}, llm_server_enabled=False, smart_routing_enabled=True,
                                 smart_running_image="sha256:old")
    assert "FAIL identity=smart-routing image is the derived build" in result.stdout + result.stderr


def test_check_server_skips_smart_routing_when_disabled(tmp_path: pathlib.Path) -> None:
    result, _ = run_check_server(tmp_path, {}, llm_server_enabled=False)
    assert "SKIP identity=smart-routing" in result.stdout + result.stderr
```

Extend `run_check_server` (tests/test_shell.py, around L7482) with keywords, defaults keeping every existing caller unchanged:
- `smart_routing_enabled: bool = False`: appends the Task 2 `smart_routing` block with `enabled: true` to the config it writes, and creates `omniroute-smart.env` next to it with mode 600;
- `smart_running_image: str = "sha256:current"`: exported to the stub as `STUB_RUNNING_IMAGE`;
- `smart_tree_label: str | None = None`: when `None`, the stub answers with the real tree hash, computed once in the test with `subprocess.run(["bash", "-c", "source tools/lib.sh; omniroute_smart_tree_hash"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()`;
- add the Task 7 `node` stub to its stub dir.

Make its stubbed `podman` answer `podman inspect --format '{{.Image}}' omniroute` (match on `{{.Image}}` in `$*`) with `$STUB_RUNNING_IMAGE` (default `sha256:current`), since the existing stub exits 1 for any call other than its PID query; then answer the three label queries separately, but only when `$*` contains `sha256:current` (any other image ID gets the stale value `0000000000000000` for every label):
- `$*` contains `llm-env.omniroute-smart.patch`: print the real `PATCH_VERSION` (passed in through a `STUB_SMART_PATCH_LABEL` env var the helper sets to `subprocess.run(["bash", "-c", "source tools/lib.sh; omniroute_smart_patch_version"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()`);
- `llm-env.omniroute-smart.base`: print the Task 2 default `base_image`;
- `llm-env.omniroute-smart.tree`: print `smart_tree_label` or the real hash.

The disabled default means the existing check-server tests see one extra SKIP row and nothing else; check that none of them asserts an exact line count.

- [ ] **Step 2: Implement** (using the existing `ok`/`bad`/skip helpers and verdict format)

```bash
log_step "Smart routing"
if [ "$(yq -r '.smart_routing.enabled' "$CONFIG_PATH")" != "true" ]; then
    log_warn "Verdict: SKIP identity=smart-routing reason=smart_routing.enabled is false"
else
    require_cmd node find sha256sum stat  # only needed when smart routing is enabled
    want_patch="$(omniroute_smart_patch_version)"
    want_tree="$(omniroute_smart_tree_hash)"
    want_base="$(yq -r '.smart_routing.base_image' "$CONFIG_PATH")"
    img="$(podman inspect --format '{{.Image}}' omniroute 2>/dev/null || true)"   # the running container's image ID, not the tag (a rebuilt tag may not be running yet)
    label() { podman image inspect --format "{{index .Labels \"llm-env.omniroute-smart.$1\"}}" "$img" 2>/dev/null || true; }
    got_patch="$(label patch)"; got_base="$(label base)"; got_tree="$(label tree)"
    if [ "$got_patch" = "$want_patch" ] && [ "$got_base" = "$want_base" ] && [ "$got_tree" = "$want_tree" ]; then
        ok "Verdict: PASS identity=smart-routing image is the derived build (${want_patch}, tree ${want_tree})"
    else
        bad "Verdict: FAIL identity=smart-routing image is the derived build (want ${want_patch}/${want_tree}@${want_base}, got ${got_patch:-none}/${got_tree:-none}@${got_base:-none}); run make setup && make restart"
    fi
    env_file="$(dirname "$CONFIG_PATH")/omniroute-smart.env"
    if [ "$(stat -c %a "$env_file" 2>/dev/null)" = "600" ]; then ok "Verdict: PASS identity=smart-routing env file is private"
    else bad "Verdict: FAIL identity=smart-routing env file is private (${env_file} must exist with mode 600)"; fi
fi
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_shell.py -k smart_routing -v && bash tools/validate.sh`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/check-server.sh tests/test_shell.py
git commit -m "feat(check-server): smart routing image and env-file rows"
```

---

### Task 13: Go/no-go, PRD v2.1, and the P2–P4 adjustment list

**Files:**
- Modify: `docs/prds/omniroute-smart-routing-research/spike-report-p1.md` (sections "Verdict" and "Impact on P2–P4")
- Rename and modify: `docs/prds/omniroute-smart-routing-v2.0-prd.md` → `docs/prds/omniroute-smart-routing-v2.1-prd.md` (via `git mv`)

**Interfaces:**
- Consumes: every report section from Tasks 5, 10 and 11.
- Produces:
  - a GO or NO-GO verdict line, which the orchestrator rail reads;
  - PRD v2.1;
  - an "Impact on P2–P4" list that names, per later plan, each task that must change and why.

- [ ] **Step 1: Write the verdict section in the report**

Fill a table with one row per criterion and the columns criterion / result / evidence. The criteria are the PRD's Phase 1 pass criteria for S1–S5 (PRD "Execution Phases → Phase 1") and the S1a anchor rules. End with exactly one line:
- `VERDICT: GO`, or
- `VERDICT: NO-GO (<criterion>) → fallback: <PRD fallback>`

- [ ] **Step 2: Create PRD v2.1 with measured values**

```bash
git mv docs/prds/omniroute-smart-routing-v2.0-prd.md docs/prds/omniroute-smart-routing-v2.1-prd.md
```

Edit it as follows. Every edit must cite the report section it comes from:
- the patch-site list and counts in "What the patch changes";
- "Laya classification" p95 (batched, and parallel fallback) and the batching decision;
- the S3 one-survivor and zero-survivor behaviour;
- the S4 result (UI or API-only editing);
- the S5 result (env scoping, timer), noting the 24 h check is moved to P5;
- the context keys reachable;
- the usage-history columns;
- the S2 result;
- "Module contract": `prelude(ctx)` (ctx includes `body` and `headers` when reachable), `order(targets, deps)`, and `observe(outcome)`, matching this plan's Interfaces;
- "Data storage": the `omniroute-smart.env` mode check lives in `make check-server` (Task 12), not `make check-setup`.

Change "Document Version" to 2.1, and add a clarification-summary row `14 | Phase 1 measured: …`.

- [ ] **Step 3: Write "Impact on P2–P4" in the report**

P2–P5 are written and committed before P1 runs:
- `docs/superpowers/plans/2026-10-13-omniroute-smart-p2-smart-catalog-plugin.md`
- `…-p3-*.md`, `…-p4-*.md`, `…-p5-*.md` (check with `ls docs/superpowers/plans/2026-10-13-omniroute-smart-p*.md`).

For each plan, list every task whose assumptions changed (always state, for P4, how the fusion lane inherits OmniRoute's forbidden-substring / HTTP-200 error-text rules: `handleSingleModel` applies them, `ctx.checkComboResponse`, or neither → fusion stays off): a context key it relied on, a latency budget, fusion behaviour, the editing route, env scoping, loopback trust, activate-on-active behaviour, or the plugin export shape. Give the exact edit as plan file + task + old text → new text.

Do **not** edit those plans in this task. The orchestrator applies the list before starting each plan. If a plan file is missing, write its entries in the report anyway, under the plan's name.

- [ ] **Step 4: Validate and commit**

```bash
bash tools/validate.sh && bash tools/test.sh
git add docs/prds docs/superpowers/plans
git commit -m "docs(prd): v2.1 with Phase 1 measurements and go/no-go"
```

Expected: everything passes; the report ends with the `VERDICT:` line.

---

## Self-Review Notes (author)

**Spec coverage (PRD Phase 1):**

| PRD item | Task |
|---|---|
| S1 | Tasks 5, 7, 10 |
| S2 | Task 11 |
| S3 | Task 10, Step 3 |
| S4 | Task 10, Step 4 |
| S5 | Task 10, Step 5 |
| Derived image in `make setup` | Tasks 7, 8 |
| Health checks | Task 12 |
| Spike report and PRD v2.1 | Task 13 |
| Usage-history status check | Task 10, Step 2.9 |
| Laya batched vs separate | Task 10, Step 2.10 |

**Deliberately not in P1:**
- the `smart` strategy (P3);
- the `smart-catalog` plugin (P2);
- `make smart-probe` (P2);
- plugin, scorecard and System One check rows (P2/P3).

**Known spike uncertainty:** Task 5's exact regexes cannot be written before reading the pinned image. The task gives the discovery commands, the anchor rules, the hook contract per site, and the stop criteria instead.
