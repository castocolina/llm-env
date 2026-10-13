#!/usr/bin/env node
// Spike: patch OmniRoute's compiled Next.js server chunks so combos can use extra
// routing strategies loaded from OMNIROUTE_STRATEGIES_DIR (<name>.mjs, default export
// { order(targets, deps) -> targets }). Exit 0 = patched (or already patched),
// exit 2 = an anchor was not found -> upstream shape changed, do NOT start patched.
// shortcut: anchors target the pre-registry build (v3.8.52 image of 2026-10-07); the
// strategyRegistry refactor (#16206) needs a different dispatch anchor.
import { readdirSync, readFileSync, writeFileSync, statSync } from "node:fs";
import { join } from "node:path";

const root = process.argv[2] || "/app/.build/next/server";
const dir = process.env.OMNIROUTE_STRATEGIES_DIR || "/opt/omni-strategies";
const names = readdirSync(dir)
  .filter((f) => /^[a-z0-9][a-z0-9-]{1,40}\.mjs$/.test(f))
  .map((f) => f.slice(0, -4));
if (names.length === 0) throw new Error(`no strategies in ${dir}`);

const MARK = "/*omni-custom-strategies*/";
const LIST_RE = /\["priority","weighted","round-robin"(?:,"[a-z0-9-]+")*\]/g;
const DISPATCH_RE = /async function (\w+)\((\w+),(\w+),(\w+)\)\{let\{combo:(\w+),config:/g;
const extra = names.map((n) => JSON.stringify(n)).join(",");
const hook = (s, b, d) =>
  `${MARK}if(${JSON.stringify(names)}.includes(${s}))try{const S=globalThis.__omniStrategies??=new Map();` +
  `if(!S.has(${s}))S.set(${s},(await import(${JSON.stringify("file://" + dir + "/")}+${s}+".mjs")).default);` +
  `return{orderedTargets:await S.get(${s}).order([...${b}],${d}),quotaShareRelease:null}}` +
  `catch(e){${d}.log?.warn?.("COMBO","custom strategy "+${s}+" failed, using declared order: "+e?.message);` +
  `return{orderedTargets:${b},quotaShareRelease:null}}`;

let lists = 0, dispatch = 0, files = 0, already = 0;
const walk = (p) => {
  for (const e of readdirSync(p)) {
    const f = join(p, e);
    if (statSync(f).isDirectory()) { walk(f); continue; }
    if (!f.endsWith(".js")) continue;
    let s = readFileSync(f, "utf8");
    if (s.includes(MARK)) { already++; continue; }
    const before = s;
    s = s.replace(LIST_RE, (m) => (lists++, `${m.slice(0, -1)},${extra}]`));
    s = s.replace(DISPATCH_RE, (m, fn, a, b, d, combo) => (dispatch++,
      `async function ${fn}(${a},${b},${d}){${hook(a, b, d)};let{combo:${combo},config:`));
    if (s !== before) { writeFileSync(f, s.replace(/^/, MARK)); files++; }
  }
};
walk(root);
console.log(JSON.stringify({ names, lists, dispatch, files, already }));
if (already === 0 && (lists === 0 || dispatch === 0)) process.exit(2);
