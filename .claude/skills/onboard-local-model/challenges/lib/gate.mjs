// gate.mjs — the validity gate's measuring tools (V1-V6): test runs, import graph, site depth, leak and self-containment checks.
//
// Decision 16, confirmed once with `ps -eo pid,pgid,args` while a hanging-mutant test ran (2026-10-31): the per-file
// child is `node --test --test-reporter=tap --test-timeout=<half the file limit> --test-force-exit <absolute test file>` and carries its own process group (pgid == pid, the
// runner's pgid differs); its test subprocess shares that group, so kill(-pid, SIGKILL) ends both.
import { spawn } from "node:child_process";
import { posix, resolve } from "node:path";
import { hintHits } from "./operators.mjs";
import { readTree } from "./instantiate.mjs";

export const DENYLIST = /solution|reference|mutant|mutation|answer|fix|bug|defect|manifest|seed|oracle|variant|decoy|twin|hint|stub/i;
const TOP_LEVEL = new Set(["prompt.md", "package.json", "src", "test", "data"]);
const BARE_OK = new Set(["node:test", "node:assert", "node:assert/strict", "node:fs", "node:path", "node:url"]);
const IMPORT = /\b(?:import|export)\b[^'"`;]*?\bfrom\s*['"]([^'"]+)['"]|\bimport\s*['"]([^'"]+)['"]|\bimport\(\s*['"]([^'"]+)['"]\s*\)/g;
const timeoutDefault = () => Number(process.env.ONBOARD_GEN_TEST_TIMEOUT_MS) || 20000;
const specs = (text) => [...text.matchAll(IMPORT)].map((m) => m[1] ?? m[2] ?? m[3]);

// Pids of the spawned per-file test runs, each its own process group: killed with the generator, so a SIGTERMed or
// crashed run leaves no orphan busy loop behind.
const live = new Set();
// Callbacks a signal must run before the process exits (scratch removal): a signal skips the callers' `finally`.
export const cleanups = new Set();
let hooked = false;
function killLive() {
  for (const pid of live) {
    try { process.kill(-pid, "SIGKILL"); } catch { /* already gone */ }
  }
  live.clear();
}
function hookExit() {
  if (hooked) return;
  hooked = true;
  process.on("exit", killLive);
  for (const [signal, code] of [["SIGINT", 130], ["SIGTERM", 143]]) process.on(signal, () => {
    killLive();
    for (const undo of cleanups) {
      try { undo(); } catch { /* best effort */ }
    }
    process.exit(code);
  });
}

function runOne(dir, file, timeoutMs) {
  return new Promise((done) => {
    const abs = resolve(dir);
    const child = spawn(process.execPath, [
      "--test", "--test-reporter=tap", `--test-timeout=${Math.max(1, Math.floor(timeoutMs / 2))}`, "--test-force-exit", `${abs}/${file}`,
    ], {
      cwd: abs,
      detached: true,
      stdio: ["ignore", "pipe", "ignore"],
    });
    hookExit();
    if (child.pid !== undefined) live.add(child.pid);
    let text = "";
    let timedOut = false;
    child.stdout.on("data", (d) => (text += d));
    const timer = setTimeout(() => {
      timedOut = true;
      try { process.kill(-child.pid, "SIGKILL"); } catch { /* already gone */ }
    }, timeoutMs);
    const finish = () => {
      clearTimeout(timer);
      if (child.pid !== undefined) live.delete(child.pid);
      // The runner prints its summary last, but node echoes whatever a test file prints as a `# ...` line before it: take the
      // last match of stdout only. A killed run has no summary, so a timed-out file keeps no pass. A test cancelled by
      // --test-timeout is in neither # pass nor # fail.
      const count = (name) => Number([...text.matchAll(new RegExp(`^# ${name} (\\d+)`, "gm"))].at(-1)?.[1] ?? 0);
      done({ file, pass: timedOut ? 0 : count("pass"), fail: count("fail") + count("cancelled"), timedOut });
    };
    child.on("close", finish);
    child.on("error", finish);
  });
}

// One run per test file: [{ file, pass, fail, timedOut }], files sorted.
export async function runTests(dir, { timeoutMs = timeoutDefault() } = {}) {
  const files = Object.keys(readTree(`${dir}/test`)).map((f) => `test/${f}`).filter((f) => f.endsWith(".test.js"));
  const results = [];
  for (const file of files) results.push(await runOne(dir, file, timeoutMs));
  return results;
}

// The agent's score: every test file under the same limits as the validity gate; a hung file adds one failure.
export async function score(dir, { timeoutMs = timeoutDefault() } = {}) {
  const results = await runTests(dir, { timeoutMs });
  const pass = results.reduce((n, r) => n + r.pass, 0);
  const fail = results.reduce((n, r) => n + r.fail + (r.timedOut ? 1 : 0), 0);
  return { pass, total: pass + fail, timed_out: results.filter((r) => r.timedOut).map((r) => r.file).sort() };
}

// Static relative imports between .js files, plus test/cases.test.js -> the entry named in data/cases.json.
export function importGraph(dir) {
  const files = readTree(dir);
  const graph = new Map();
  for (const [rel, text] of Object.entries(files)) {
    if (!rel.endsWith(".js") || rel.startsWith(".git/")) continue;
    const edges = new Set();
    for (const spec of specs(text)) {
      if (spec.startsWith(".")) edges.add(posix.normalize(posix.join(posix.dirname(rel), spec)));
    }
    if (rel === "test/cases.test.js" && files["data/cases.json"]) edges.add(JSON.parse(files["data/cases.json"]).entry);
    graph.set(rel, edges);
  }
  return graph;
}

// Shortest distance from any failing test file to siteFile, minus 1 (a directly imported file has depth 0); null when unreachable.
export function siteDepth(graph, failingFiles, siteFile) {
  const seen = new Set(failingFiles);
  let frontier = [...failingFiles];
  for (let distance = 1; frontier.length > 0; distance++) {
    const next = [];
    for (const file of frontier) {
      for (const to of graph.get(file) ?? []) {
        if (to === siteFile) return distance - 1;
        if (!seen.has(to)) { seen.add(to); next.push(to); }
      }
    }
    frontier = next;
  }
  return null;
}

// V4: the layer's invariant; returns the rejection reason, or null when it holds. Layers 1-3 use the site depth of a
// mutation or stub site, layer 4 the number of changed src files of a feature removal, layer 5 (repo scale) the depth
// plus the size of src/ and the number of decoys.
export function depthProblem(layer, depth, changedSrcFiles = 0, srcFiles = 0, decoys = 0) {
  if (layer <= 2 && depth !== 0) return `site depth ${depth}, layer ${layer} needs 0`;
  if (layer === 3 && !(depth >= 1)) return `site depth ${depth}, layer 3 needs at least 1`;
  if (layer === 4 && !(changedSrcFiles >= 2)) return `changed src files ${changedSrcFiles}, layer 4 needs at least 2`;
  if (layer === 5) {
    if (!(depth >= 2)) return `site depth ${depth}, layer 5 needs at least 2`;
    if (!(srcFiles >= 14)) return `src files ${srcFiles}, layer 5 needs at least 14`;
    if (!(decoys >= 4)) return `decoys ${decoys}, layer 5 needs at least 4`;
  }
  return null;
}

// V5. challenge and reference are { relpath: text } sets (the challenge includes prompt.md); mutation is
// { op, id, find, mutated, file, layer, feature, twin } (twin = the twin file's text or null) for a mutation, stub or feature challenge (find and mutated are null for a
// stub or feature; the op's hint words come from OPERATORS), else null. A feature's own statement is the task, not a leak:
// for `feature: true` only the reference-line, denylisted-path and operator-id rules apply.

export function leaks({ challenge, reference, prompt, mutation = null }) {
  const out = [];
  for (const rel of Object.keys(challenge)) {
    if (!TOP_LEVEL.has(rel.split("/")[0])) out.push(`top-level entry ${rel.split("/")[0]}`);
    if (DENYLIST.test(rel)) out.push(`path ${rel} matches the leak denylist`);
  }
  const shipped = new Set(
    Object.entries(challenge).filter(([rel]) => rel !== "prompt.md").flatMap(([, t]) => t.split("\n").map((l) => l.trim())),
  );
  for (const [rel, text] of Object.entries(reference)) {
    if (!rel.startsWith("src/")) continue;
    for (const line of text.split("\n").map((l) => l.trim())) {
      if (line.length >= 20 && !shipped.has(line) && prompt.includes(line)) out.push(`prompt quotes the reference line ${JSON.stringify(line)}`);
    }
  }
  for (const [, span] of prompt.matchAll(/`([^`]+)`/g)) {
    if ((span.includes("/") || /\.(js|json)$/.test(span)) && span.split("/").some((seg) => DENYLIST.test(seg))) out.push(`prompt names the path ${span}`);
  }
  if (mutation) out.push(...(prompt.includes(mutation.op) ? [`prompt contains the operator id ${mutation.op}`] : []));
  if (mutation && !mutation.feature) {
    if (prompt.includes(mutation.id)) out.push(`prompt contains the site id ${mutation.id}`);
    for (const text of [mutation.find, mutation.mutated]) if (text && prompt.includes(text)) out.push(`prompt contains ${JSON.stringify(text)}`);
    if (mutation.layer >= 3 && prompt.includes(posix.basename(mutation.file))) out.push(`prompt names the cause file ${posix.basename(mutation.file)}`);
    if (mutation.twin && mutation.find && mutation.twin.includes(mutation.find)) out.push("the twin file contains the find text");
    for (const hint of hintHits(prompt, [mutation.op])) out.push(`prompt contains the hint word ${hint}`);
  }
  return out;
}

// V6. Every relative import resolves inside dir, bare imports come only from the allowed built-ins, and no file
// names a host path or the generator's own directory.
export function selfContained(dir, genDir) {
  const out = [];
  const files = readTree(dir);
  for (const [rel, text] of Object.entries(files)) {
    if (rel.startsWith(".git/")) continue;
    if (/\.m?js$/.test(rel)) {
      for (const spec of specs(text)) {
        if (spec.startsWith(".")) {
          const target = posix.normalize(posix.join(posix.dirname(rel), spec));
          if (target.startsWith("..") || !(target in files)) out.push(`${rel} imports ${spec}, which does not resolve inside the challenge`);
        } else if (!BARE_OK.has(spec)) out.push(`${rel} imports ${spec}`);
      }
    }
    for (const bad of ["/home/", "/var/home/", "/root/", "/tmp/", genDir].filter(Boolean)) if (text.includes(bad)) out.push(`${rel} contains ${bad}`);
  }
  return out;
}
