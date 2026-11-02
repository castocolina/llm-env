// gen.mjs — seeded benchmark challenge generator (CLI). Run through scripts/gen-challenge.sh, never directly.
// Generator code is deterministic: no clock, no output-changing environment, no absolute path in an output.
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, rmSync, rmdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { isDeepStrictEqual } from "node:util";
import { DENYLIST, cleanups, depthProblem, importGraph, leaks, runTests, score, selfContained, siteDepth } from "./lib/gate.mjs";
import {
  casesTemplate, chooseRenames, codeFiles, copyProject, hasWord, readDecoys, readTree, renameFiles, renamePath, renameProse, renameText, runFixtures, twinOf, writeTree,
} from "./lib/instantiate.mjs";
import { OPERATORS, hintHits } from "./lib/operators.mjs";
import { buildPrompt } from "./lib/prompts.mjs";
import { rng } from "./lib/rng.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));

function listFiles(dir, prefix = "") {
  const out = [];
  for (const name of readdirSync(dir)) {
    const rel = prefix === "" ? name : `${prefix}/${name}`;
    if (statSync(join(dir, name)).isDirectory()) out.push(...listFiles(join(dir, name), rel));
    else out.push(rel);
  }
  return out;
}

// Decision 5: "g-" plus 12 hex digits of sha256 over the sorted challenges/ tree (<relative path>\0<content>\0 per file).
export function generatorId(root = HERE) {
  const hash = createHash("sha256");
  for (const rel of listFiles(root).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))) {
    hash.update(`${rel}\0`).update(readFileSync(join(root, rel))).update("\0");
  }
  return `g-${hash.digest("hex").slice(0, 12)}`;
}


const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const sha = (text) => createHash("sha256").update(text).digest("hex");
const sortedObject = (obj) => Object.fromEntries(Object.entries(obj).sort(([a], [b]) => cmp(a, b)));
const MAX_ATTEMPTS = 8;

// A failed gate check. library = V1/V3/V5/V6 (the library is wrong, no retry); otherwise the attempt is just invalid.
class Reject extends Error {
  constructor(reason, library = false) {
    super(reason);
    this.library = library;
  }
}

function loadProjects(root = HERE) {
  const dir = join(root, "projects");
  return readdirSync(dir)
    .sort(cmp)
    .filter((name) => statSync(join(dir, name)).isDirectory())
    .map((id) => {
      const files = readTree(join(dir, id));
      return { id, dir: join(dir, id), files, project: JSON.parse(files["project.json"]) };
    });
}

const isFeature = (item) => item.prompt !== undefined;
const opsOf = (item) => (isFeature(item) ? ["remove-feature"] : item.stub !== undefined ? ["stub"] : item.ops ?? []);
const allItems = (p) => [...p.project.sites, ...(p.project.features ?? [])];
const itemsFor = (p, layer) => allItems(p).filter((s) => s.layers.includes(layer) && opsOf(s).length > 0);
const occurrences = (text, find) => text.split(find).length - 1;

// One attempt, V1-V7 in order. Throws Reject; `gate` is a scratch directory this call fills (the caller removes it).
async function build({ layer, seed, attempt, force, gate }) {
  const projects = loadProjects();
  let p, site, op;
  if (force) {
    p = projects.find((q) => q.id === force.project);
    site = p && allItems(p).find((s) => s.id === force.item);
    op = force.op;
    if (!site) throw new Reject(`no site ${force.project}/${force.item}`, true);
  } else {
    const pool = projects.filter((q) => itemsFor(q, layer).length > 0);
    if (pool.length === 0) throw new Reject(`no library projects for layer ${layer}`, true);
    p = rng(seed, `a${attempt}/project`).pick(pool);
    site = rng(seed, `a${attempt}/item`).pick(itemsFor(p, layer));
    op = rng(seed, `a${attempt}/op`).pick(opsOf(site));
  }
  const { project } = p;
  const renames = chooseRenames(project, codeFiles(p.files), rng(seed, `a${attempt}/renames`));
  const feature = isFeature(site);
  const siteFile = feature ? null : renamePath(site.file, renames);
  const isStub = site.stub !== undefined;
  const stubName = isStub ? renameText(site.stub, renames) : null;
  const find = isStub || feature ? null : renameText(site.find, renames);
  const ref = renameFiles(copyProject(p.files), renames);
  const refDir = join(gate, "reference");
  const chalDir = join(gate, "challenge");

  if (p.files["fixtures.mjs"]) {
    writeTree(refDir, ref);
    const fx = await runFixtures(p.dir, renames, rng(seed, `a${attempt}/fixtures`), refDir);
    for (const c of fx.cases) {
      if (c.expected === undefined || !isDeepStrictEqual(JSON.parse(JSON.stringify(c.expected)), c.expected)) {
        throw new Reject(`fixture ${c.name}: expected does not survive a JSON round trip`);
      }
    }
    Object.assign(ref, fx.files, {
      "data/cases.json": `${JSON.stringify({ entry: fx.entry, cases: fx.cases }, null, 2)}\n`,
      "test/cases.test.js": casesTemplate(),
    });
  }

  // L5 only: k = min(10, max(rng.int(4, 8), 14 - core src files)) decoys, copied after the renames and never renamed.
  const decoys = [];
  if (layer === 5) {
    const pool = readDecoys(join(HERE, "decoys"));
    const core = Object.keys(ref).filter((rel) => rel.startsWith("src/")).length;
    const k = Math.min(10, Math.max(rng(seed, `a${attempt}/decoys`).int(4, 8), 14 - core));
    for (const name of rng(seed, `a${attempt}/decoys`).sample(Object.keys(pool), k).sort(cmp)) {
      for (const [rel, text] of Object.entries(pool[name])) {
        if (rel in ref) throw new Reject(`decoy ${name}: ${rel} already exists in ${p.id}`, true);
        ref[rel] = text;
      }
      decoys.push(name);
    }
  }

  let mutated = null;
  let twin = null; // { file, text }: a never-imported copy of the mutated function under another name
  let chal, expectedDiff;
  if (feature) {
    // The reference is the full project; the challenge swaps in the variant files and drops the deleted ones.
    const prefix = `variants/${site.id}/`;
    const variant = renameFiles(Object.fromEntries(Object.entries(p.files).filter(([rel]) => rel.startsWith(prefix)).map(([rel, text]) => [rel.slice(prefix.length), text])), renames);
    const replace = (site.replace ?? []).map((rel) => renamePath(rel, renames));
    const del = (site.delete ?? []).map((rel) => renamePath(rel, renames));
    const missing = [...replace.filter((rel) => !(rel in variant)).map((rel) => `variant ${rel}`), ...del.filter((rel) => !(rel in ref)).map((rel) => `delete ${rel}`)];
    if (missing.length > 0) throw new Reject(`feature ${p.id}/${site.id}: missing ${missing.join(", ")}`, true);
    chal = OPERATORS[op].apply(ref, variant, { replace, delete: del });
    expectedDiff = [...replace, ...del].sort(cmp);
  } else {
    const source = ref[siteFile];
    if (source === undefined) throw new Reject(`site file ${siteFile} is missing`, true);
    let changed;
    if (isStub) {
      changed = OPERATORS[op]?.apply(source, stubName) ?? null;
      if (changed === null || changed === source) throw new Reject(`${p.id}/${site.id}: no exported ${stubName} to stub in ${siteFile}`, true);
    } else {
      if (occurrences(source, find) !== 1) throw new Reject(`${p.id}/${site.id}: find text occurs ${occurrences(source, find)} times in ${siteFile}`, true);
      mutated = OPERATORS[op]?.apply(find) ?? null;
      if (mutated === null || mutated === find) throw new Reject(`${p.id}/${site.id}: operator ${op} does not change the find text`, true);
      changed = source.replace(find, () => mutated);
    }
    chal = { ...ref, [siteFile]: changed };
    expectedDiff = [siteFile];
    if (layer === 5 && site.twin !== undefined) {
      const r = rng(seed, `a${attempt}/twin`);
      if (force?.twin ?? r.int(0, 1) === 1) {
        const name = r.pick(site.twin.names);
        const file = `src/lib/${r.pick(site.twin.files)}.js`;
        const text = twinOf(changed, source.indexOf(find), name);
        if (text === null) throw new Reject(`${p.id}/${site.id}: no exported declaration around the site to copy as a twin`, true);
        if (file in ref) throw new Reject(`twin file ${file} already exists in ${p.id}`, true);
        twin = { file, text };
        ref[file] = text;
        chal[file] = text;
      }
    }
  }

  // V3: same file set, only the site file (or the feature's replace and delete files) differs.
  const differing = Object.keys(ref).filter((rel) => ref[rel] !== chal[rel]).sort(cmp);
  if (differing.join("\n") !== expectedDiff.join("\n")) throw new Reject(`differing files ${differing.join(", ")}, expected ${expectedDiff.join(", ")}`, true);

  writeTree(refDir, ref);
  writeTree(chalDir, chal);
  const refResults = await runTests(refDir);
  const refTotal = refResults.reduce((n, r) => n + r.pass + r.fail, 0);
  const refTimedOut = refResults.filter((r) => r.timedOut).map((r) => r.file);
  if (refTimedOut.length > 0) throw new Reject(`reference timed out in ${refTimedOut.join(", ")}`, true);
  if (refTotal === 0 || refResults.some((r) => r.fail > 0)) throw new Reject(`reference does not pass every test (${JSON.stringify(refResults)})`, true);

  const chalResults = await runTests(chalDir);
  const chalTimedOut = chalResults.filter((r) => r.timedOut).map((r) => r.file);
  if (chalTimedOut.length > 0) throw new Reject(`challenge timed out in ${chalTimedOut.join(", ")}`);
  const failing = chalResults.filter((r) => r.fail > 0).map((r) => r.file);
  if (failing.length === 0) throw new Reject(`no test fails in the challenge (the ${op} mutant of ${p.id}/${site.id} survives)`);

  const graph = importGraph(chalDir);
  const depth = feature ? null : siteDepth(graph, failing, siteFile);
  const changedSrc = differing.filter((rel) => rel.startsWith("src/")).length;
  const srcCount = Object.keys(chal).filter((rel) => rel.startsWith("src/")).length;
  const depthReason = depthProblem(layer, depth, changedSrc, srcCount, decoys.length);
  if (depthReason !== null) throw new Reject(depthReason);
  const imported = [...new Set(failing.flatMap((f) => [...(graph.get(f) ?? [])]))].filter((rel) => rel.startsWith("src/"));

  const prompt = buildPrompt({
    layer, summary: renameProse(project.summary, renames), symptoms: failing, siteFile, name: stubName, spec: isStub ? renameProse(site.spec, renames) : null, imported,
    feature: feature ? renameProse(site.prompt, renames) : null,
  });
  const challenge = { ...chal, "prompt.md": prompt };
  writeFileSync(join(chalDir, "prompt.md"), prompt);
  const leakList = leaks({ challenge, reference: ref, prompt, mutation: { op, id: site.id, find, mutated, file: siteFile, layer, feature, twin: twin?.text ?? null } });
  if (leakList.length > 0) throw new Reject(`leak: ${leakList.join("; ")}`, true);
  const openList = selfContained(chalDir, HERE);
  if (openList.length > 0) throw new Reject(`not self-contained: ${openList.join("; ")}`, true);

  const srcFiles = Object.keys(challenge).filter((rel) => rel.startsWith("src/"));
  const manifest = {
    schema: 1,
    generator: generatorId(),
    id: `l${layer}-s${seed}`,
    layer,
    seed,
    attempt,
    project: p.id,
    operators: [{ op, site: site.id, file: siteFile }], // a feature has file null
    renames: sortedObject(renames),
    decoys,
    twin: twin?.file ?? null,
    prompt_sha256: sha(prompt),
    expected_failing: chalResults
      .filter((r) => r.fail > 0)
      .sort((a, b) => cmp(a.file, b.file))
      .map((r) => ({ file: r.file, failed: r.fail, total: r.pass + r.fail })),
    tests_total: chalResults.reduce((n, r) => n + r.pass + r.fail, 0),
    files: sortedObject(Object.fromEntries(Object.entries(challenge).map(([rel, text]) => [rel, sha(text)]))),
    reference_src: sortedObject(Object.fromEntries(Object.entries(ref).filter(([rel]) => rel.startsWith("src/")).map(([rel, text]) => [rel, sha(text)]))),
    validity: {
      fails_as_shipped: true,
      passes_with_reference: true,
      leak_free: true,
      self_contained: true,
      difficulty: { site_depth: depth, changed_src_files: changedSrc, src_files: srcFiles.length },
    },
  };
  return { manifest, challenge, reference: ref };
}

async function attemptOnce(args) {
  try {
    return { ok: true, built: await build(args) };
  } catch (error) {
    if (error instanceof Reject) return { ok: false, library: error.library, reason: error.message };
    throw error;
  }
}

// Removes the scratch tree, and the out directory too when this run created it and nothing was written to it.
function removeScratch(root, out) {
  rmSync(root, { recursive: true, force: true });
  if (out !== null) {
    try { rmdirSync(out); } catch { /* holds the challenge */ }
  }
}

// The Generator contract: up to 8 deterministic attempts (Decision 3); a library error stops at once.
export async function buildChallenge({ layer, seed, out: outArg, withReference = false, force = null }) {
  const out = resolve(outArg);
  const created = !existsSync(out);
  mkdirSync(out, { recursive: true });
  const scratch = join(out, ".gate");
  const reasons = [];
  const undo = () => removeScratch(scratch, created ? out : null);
  cleanups.add(undo);
  try {
    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      const result = await attemptOnce({ layer, seed, attempt, force, gate: join(scratch, `a${attempt}`) });
      if (result.ok) {
        const { manifest, challenge, reference } = result.built;
        writeTree(join(out, "challenge"), challenge);
        if (withReference) writeTree(join(out, "reference"), reference);
        writeFileSync(join(out, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
        return manifest;
      }
      if (result.library) throw new Error(`library error: ${result.reason}`);
      reasons.push(`attempt ${attempt}: ${result.reason}`);
    }
    throw new Error(`no valid challenge for layer ${layer} seed ${seed} after ${MAX_ATTEMPTS} attempts: ${reasons.join(" | ")}`);
  } finally {
    cleanups.delete(undo);
    undo();
  }
}

const ALLOWED_TOP = new Set(["project.json", "fixtures.mjs", "variants", "package.json", "src", "test", "data"]);
const DISCOVERY = /(\.test|-test|_test)\.[cm]?js$|^test-.*\.[cm]?js$|^test\.[cm]?js$/;
const PACKAGE_JSON = { type: "module", engines: { node: ">=20" } };

function lintProject({ id, files, project }) {
  const out = [];
  const add = (message) => out.push(`${id}: ${message}`);
  const code = codeFiles(files);
  const ops = new Set(project.sites.flatMap((s) => s.ops ?? []));
  for (const top of new Set(Object.keys(files).map((rel) => rel.split("/")[0]))) if (!ALLOWED_TOP.has(top)) add(`unexpected top-level entry ${top}`);
  if (!isDeepStrictEqual(JSON.parse(files["package.json"] ?? "null"), PACKAGE_JSON)) add(`package.json must be exactly ${JSON.stringify(PACKAGE_JSON)}`);
  for (const rel of Object.keys(code)) {
    const shipped = rel.startsWith("variants/") ? rel.split("/").slice(2).join("/") : rel;
    if (DENYLIST.test(shipped)) add(`path ${shipped} matches the leak denylist`);
    if (rel.startsWith("test/") && !rel.endsWith(".test.js")) add(`test/ holds ${rel}, only *.test.js is allowed`);
    if (rel.startsWith("src/")) {
      const parts = rel.split("/");
      if (DISCOVERY.test(parts.at(-1)) || parts.slice(1, -1).includes("test")) add(`${rel} matches a node --test discovery pattern`);
      if (rel.startsWith("src/lib/")) add(`${rel}: a core project has no src/lib/ (decoys live there)`);
    }
  }
  for (const site of project.sites) {
    const text = files[site.file];
    if (text === undefined) { add(`site ${site.id}: file ${site.file} is missing`); continue; }
    if (site.stub !== undefined) {
      if (typeof site.spec !== "string" || site.spec === "") add(`site ${site.id}: a stub site needs a spec`);
      if (!site.layers.every((l) => l === 2)) add(`site ${site.id}: a stub site belongs to layer 2 only`);
      if (OPERATORS.stub.apply(text, site.stub) === null) add(`site ${site.id}: no exported ${site.stub} to stub in ${site.file}`);
    }
    if (site.find !== undefined) {
      if (occurrences(text, site.find) !== 1) add(`site ${site.id}: find text occurs ${occurrences(text, site.find)} times`);
      for (const op of site.ops ?? []) {
        const changed = OPERATORS[op]?.apply(site.find) ?? null;
        if (changed === null || changed === site.find) add(`site ${site.id}: operator ${op} does not change the find text`);
      }
      if (Math.max(...site.layers) >= 3) {
        const line = text.slice(0, Math.max(text.indexOf(site.find), 0)).split("\n").length - 1;
        if (text.split("\n").slice(Math.max(line - 2, 0), line + 1).some((l) => l.includes("//"))) add(`site ${site.id}: a // comment sits on or just above the site`);
      }
    }
    for (const name of site.twin?.names ?? []) if (hasWord(code, name)) add(`site ${site.id}: twin name ${name} already occurs as a word`);
    for (const file of site.twin?.files ?? []) if (`src/lib/${file}.js` in files) add(`site ${site.id}: twin file src/lib/${file}.js already exists`);
  }
  for (const feature of project.features ?? []) {
    if (typeof feature.prompt !== "string" || feature.prompt === "") add(`feature ${feature.id}: a feature needs a prompt`);
    if (!feature.layers.every((l) => l === 4)) add(`feature ${feature.id}: a feature belongs to layer 4 only`);
    if ((feature.replace ?? []).length + (feature.delete ?? []).length < 2) add(`feature ${feature.id}: layer 4 needs at least 2 changed src files`);
    for (const rel of feature.replace ?? []) {
      if (!rel.startsWith("src/") || !(`variants/${feature.id}/${rel}` in files)) add(`feature ${feature.id}: variant ${rel} is missing or outside src/`);
    }
    for (const rel of feature.delete ?? []) if (!rel.startsWith("src/") || !(rel in files)) add(`feature ${feature.id}: delete ${rel} is missing or outside src/`);
  }
  for (const [name, candidates] of Object.entries(project.renames ?? {})) {
    for (const c of candidates) {
      if (hasWord(code, c)) add(`rename candidate ${c} for ${name} already occurs as a word`);
      if (DENYLIST.test(c)) add(`rename candidate ${c} for ${name} matches the leak denylist`);
    }
  }
  for (const hint of hintHits(project.summary, ops)) add(`summary contains the hint word "${hint}" of an operator the project uses`);
  return out;
}

// A decoy is exactly src/lib/<name>.js (at most 25 lines) plus test/lib-<name>.test.js with 2-3 tests.
function lintDecoys(root = HERE) {
  const out = [];
  for (const [name, files] of Object.entries(readDecoys(join(root, "decoys")))) {
    const module = `src/lib/${name}.js`;
    const tests = `test/lib-${name}.test.js`;
    if (Object.keys(files).sort(cmp).join("\n") !== [module, tests].join("\n")) out.push(`decoy ${name}: must hold exactly ${module} and ${tests}`);
    if ((files[module] ?? "").trimEnd().split("\n").length > 25) out.push(`decoy ${name}: ${module} is longer than 25 lines`);
    const count = (files[tests]?.match(/^test\(/gm) ?? []).length;
    if (count < 2 || count > 3) out.push(`decoy ${name}: ${count} tests, needs 2 or 3`);
    if (DENYLIST.test(name)) out.push(`decoy ${name}: name matches the leak denylist`);
  }
  return out;
}

// Every (project, site, op) of the library through one attempt at seed 1: a mutant no test catches, a hang or a leak is a row with valid false.
export async function checkLibrary({ layer = null, out: outArg }) {
  const projects = loadProjects();
  const lint = [...projects.flatMap(lintProject), ...lintDecoys()];
  const rows = [];
  const out = resolve(outArg);
  const created = !existsSync(out);
  mkdirSync(out, { recursive: true });
  const scratch = join(out, ".gate");
  const undo = () => removeScratch(scratch, created ? out : null);
  cleanups.add(undo);
  try {
    let n = 0;
    for (const p of projects) {
      for (const site of allItems(p)) {
        for (const siteLayer of site.layers) {
          if (layer !== null && siteLayer !== layer) continue;
          for (const op of opsOf(site)) {
            const force = { project: p.id, item: site.id, op, twin: true }; // a twin site is always checked with its twin
            const r = await attemptOnce({ layer: siteLayer, seed: 1, attempt: 0, force, gate: join(scratch, String(n++)) });
            rows.push({ layer: siteLayer, project: p.id, site: site.id, op, valid: r.ok, reason: r.ok ? "" : r.reason });
          }
        }
      }
    }
  } finally {
    cleanups.delete(undo);
    undo();
  }
  return { generator: generatorId(), rows, lint };
}

// The one definition of "pristine": DIR/challenge (without .git) against DIR/manifest.json's files.
export function verify(dir) {
  const manifest = JSON.parse(readFileSync(join(dir, "manifest.json"), "utf8"));
  const actual = {};
  for (const rel of listFiles(join(dir, "challenge"))) {
    if (rel !== ".git" && !rel.startsWith(".git/")) actual[rel] = sha(readFileSync(join(dir, "challenge", rel), "utf8"));
  }
  const changed = [...new Set([...Object.keys(manifest.files), ...Object.keys(actual)])].filter((rel) => manifest.files[rel] !== actual[rel]).sort(cmp);
  return { verified: changed.length === 0, changed };
}

function usage(message) {
  process.stderr.write(`gen.mjs: ${message}\n`);
  process.exit(2);
}

function parse(argv) {
  const opts = { mode: "generate", withReference: false };
  const value = (i, flag) => (i + 1 < argv.length ? argv[i + 1] : usage(`${flag} needs a value`));
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === "--generator-id") opts.mode = "id";
    else if (flag === "--check-library") opts.mode = "check";
    else if (flag === "--verify") { opts.mode = "verify"; opts.dir = value(i++, flag); }
    else if (flag === "--score") { opts.mode = "score"; opts.dir = value(i++, flag); }
    else if (flag === "--layer") opts.layer = Number(value(i++, flag));
    else if (flag === "--seed") opts.seed = Number(value(i++, flag));
    else if (flag === "--out") opts.out = value(i++, flag);
    else if (flag === "--with-reference") opts.withReference = true;
    else usage(`unknown flag: ${flag}`);
  }
  return opts;
}

async function main(argv) {
  const opts = parse(argv);
  try {
    if (opts.mode === "id") {
      process.stdout.write(`${generatorId()}\n`);
    } else if (opts.mode === "verify") {
      const result = verify(opts.dir);
      process.stdout.write(`${JSON.stringify(result)}\n`);
      if (!result.verified) process.exitCode = 1;
    } else if (opts.mode === "score") {
      process.stdout.write(`${JSON.stringify(await score(opts.dir))}\n`);
    } else if (opts.mode === "check") {
      if (opts.out === undefined) usage("--check-library requires --out");
      const report = await checkLibrary({ layer: opts.layer ?? null, out: opts.out });
      process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
      if (report.lint.length > 0 || report.rows.some((row) => !row.valid)) process.exitCode = 1;
    } else {
      if (opts.layer === undefined || opts.seed === undefined || opts.out === undefined) usage("--layer, --seed and --out are required");
      await buildChallenge({ layer: opts.layer, seed: opts.seed, out: opts.out, withReference: opts.withReference });
    }
  } catch (error) {
    process.stderr.write(`gen.mjs: ${error.message}\n`);
    process.exitCode = 1;
  }
}

// argv[1] keeps a symlinked path while import.meta.url is the real one, so compare real paths.
if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) main(process.argv.slice(2));
