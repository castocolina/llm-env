// Turns a library project into a reference file set: copy, rename identifiers, add seeded fixture cases.
// A file set is a plain object { relpath: utf8 text }; nothing here touches the clock or the environment.
import { mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const byLength = (a, b) => b.length - a.length || (a < b ? -1 : 1);
const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function readTree(dir, prefix = "") {
  const files = {};
  for (const name of readdirSync(dir).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))) {
    const rel = prefix === "" ? name : `${prefix}/${name}`;
    if (statSync(join(dir, name)).isDirectory()) Object.assign(files, readTree(join(dir, name), rel));
    else files[rel] = readFileSync(join(dir, name), "utf8");
  }
  return files;
}

export function writeTree(dir, files) {
  for (const [rel, text] of Object.entries(files)) {
    mkdirSync(dirname(join(dir, rel)), { recursive: true });
    writeFileSync(join(dir, rel), text);
  }
}

// The files whose words count when a name must not collide: everything but the project's own metadata.
export const codeFiles = (files) => Object.fromEntries(Object.entries(files).filter(([rel]) => rel !== "project.json" && rel !== "fixtures.mjs"));

export const hasWord = (files, word) => Object.values(files).some((text) => new RegExp(`\\b${escape(word)}\\b`).test(text));

// The library files a challenge starts from: everything but the project's own metadata and variants.
export function copyProject(files) {
  return Object.fromEntries(
    Object.entries(files).filter(([rel]) => rel !== "project.json" && rel !== "fixtures.mjs" && !rel.startsWith("variants/")),
  );
}

// The decoy pool: { name: { "src/lib/<name>.js": text, "test/lib-<name>.test.js": text } }, names sorted.
export function readDecoys(dir) {
  return Object.fromEntries(readdirSync(dir).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0)).map((name) => [name, readTree(join(dir, name))]));
}

// The twin of a site: the exported declaration of `source` (the mutated file) that holds character `index`, renamed
// to `name`; an `export function` through the next line that is exactly `}`, or a single-line `export const`. Null otherwise.
export function twinOf(source, index, name) {
  const lines = source.split("\n");
  const site = source.slice(0, index).split("\n").length - 1;
  let at = site;
  while (at >= 0 && !/^export (?:function|const) /.test(lines[at])) at--;
  if (at < 0) return null;
  const decl = /^export (function|const) ([A-Za-z_$][\w$]*)/.exec(lines[at]);
  let end = at;
  if (decl[1] === "function") {
    while (end < lines.length && lines[end] !== "}") end++;
    if (end === lines.length) return null;
  } else if (at !== site) return null;
  return renameText(`${lines.slice(at, end + 1).join("\n")}\n`, { [decl[2]]: name });
}

// One chosen candidate per renamed identifier; a candidate that already occurs as a word in the project is skipped.
export function chooseRenames(project, files, r) {
  const chosen = {};
  for (const name of Object.keys(project.renames ?? {}).sort()) {
    const free = project.renames[name].filter((c) => !hasWord(files, c));
    if (free.length > 0) chosen[name] = r.pick(free);
  }
  return chosen;
}

export function renameText(text, renames) {
  const names = Object.keys(renames).sort(byLength);
  if (names.length === 0) return text;
  return text.replace(new RegExp(`\\b(${names.map(escape).join("|")})\\b`, "g"), (w) => renames[w]);
}

// Only inside backtick spans, so prose such as "nearest-rank percentile" stays English.
export const renameProse = (text, renames) => text.replace(/`[^`]*`/g, (span) => renameText(span, renames));

// Path segments whose stem (the part before the first ".") is a renamed identifier.
export const renamePath = (rel, renames) =>
  rel.split("/").map((seg) => {
    const dot = seg.indexOf(".");
    const stem = dot < 0 ? seg : seg.slice(0, dot);
    return Object.hasOwn(renames, stem) ? renames[stem] + seg.slice(stem.length) : seg;
  }).join("/");

export const renameFiles = (files, renames) =>
  Object.fromEntries(Object.entries(files).map(([rel, text]) => [renamePath(rel, renames), renameText(text, renames)]));

// Seeded fixture cases (fixtures.mjs). expected comes from the renamed reference, imported from refDir, so the
// reference has to be on disk first; returns { entry, files, cases } (JSON round trip checked by the caller).
let importCount = 0;
export async function runFixtures(projectDir, renames, r, refDir) {
  const mod = await import(pathToFileURL(join(projectDir, "fixtures.mjs")).href);
  const { files = {}, cases } = mod.fixtures(r);
  const entry = renamePath(mod.entry, renames);
  writeTree(refDir, files); // fixture data files must exist before the reference reads them
  // shortcut: only the entry URL is cache-busted, so a module it imports stays cached across calls; library entries import nothing yet, revisit when one does.
  const ref = await import(`${pathToFileURL(join(refDir, entry)).href}?v=${importCount++}`);
  const out = cases.map((c) => {
    const call = renameText(c.call, renames);
    const args = c.args.map((a) => (typeof a === "string" && a.startsWith("@") ? `@${renamePath(a.slice(1), renames)}` : a));
    const real = args.map((a) => (typeof a === "string" && a.startsWith("@") ? join(refDir, a.slice(1)) : structuredClone(a)));
    return { name: c.name, call, args, expected: ref[call](...real) };
  });
  return { entry, files, cases: out };
}

export const casesTemplate = () => readFileSync(join(HERE, "cases.test.template"), "utf8");
