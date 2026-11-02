// Mutation operators for the challenge generator. apply(snippet) changes the first match and returns the new
// snippet, or null when the pattern is absent. hints are matched case-insensitively as whole words or phrases.
const first = (text, re, replace) => {
  const m = re.exec(text);
  return m ? text.slice(0, m.index) + replace(m) + text.slice(m.index + m[0].length) : null;
};
const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const flip = (a, b) => (text) => first(text, new RegExp(`${escape(a)}|${escape(b)}`), (m) => (m[0] === a ? b : a));

export const OPERATORS = {
  "drop-minus-one": {
    kind: "mutate",
    hints: ["off-by-one", "off by one", "minus one"],
    apply: (s) => first(s, / - 1(?!\w)/, () => ""),
  },
  "ceil-floor": {
    kind: "mutate",
    hints: ["ceil", "floor", "round up", "round down", "rounding"],
    apply: flip("Math.ceil", "Math.floor"),
  },
  "swap-sub": {
    kind: "mutate",
    hints: ["descending", "reverse", "reversed"],
    apply: (s) => first(s, /\b([A-Za-z_$][\w$]*) - ([A-Za-z_$][\w$]*)\b/, (m) => `${m[2]} - ${m[1]}`),
  },
  "lt-le": {
    kind: "mutate",
    hints: ["inclusive", "exclusive", "boundary"],
    apply: (s) => first(s, /(?<!<)<(?![<=])/, () => "<=") ?? first(s, /<=/, () => "<"),
  },
  "swap-args": {
    kind: "mutate",
    hints: ["argument order", "swapped"],
    apply: (s) => first(s, /([A-Za-z_$][\w$.]*)\(([^(),]+), ([^(),]+)\)/, (m) => `${m[1]}(${m[3]}, ${m[2]})`),
  },
  "max-min": {
    kind: "mutate",
    hints: ["maximum", "minimum", "larger of", "smaller of"],
    apply: flip("Math.max", "Math.min"),
  },
  "plus-minus": {
    kind: "mutate",
    hints: ["sign", "subtract"],
    apply: (s) => first(s, / \+ /, () => " - "),
  },
  negate: {
    kind: "mutate",
    hints: ["negate", "negated", "negation", "invert", "inverted"],
    apply: (s) => `!(${s})`,
  },
  "drop-or-fallback": {
    kind: "mutate",
    hints: ["tie", "tiebreak", "tie-break", "fallback"],
    apply: (s) => first(s, / \|\| .*$/s, () => ""),
  },
  stub: { kind: "stub", hints: [], apply: (source, name) => stubOut(source, name) },
  "remove-feature": { kind: "feature", hints: [], apply: (files, variant, feature) => removeFeature(files, variant, feature) },
  "case-fold": {
    kind: "mutate",
    hints: ["uppercase", "lowercase", "upper case", "lower case", "case-insensitive"],
    apply: (s) => first(s, /\.to(Lower|Upper)Case\(\)/, (m) => (m[1] === "Lower" ? ".toUpperCase()" : ".toLowerCase()")),
  },
};

// kind "stub": apply(source, name) empties the exported function or class `name` (the header line stays, the body
// runs to the next line that is exactly "}") and returns the new source, or null when there is no such declaration.
const stubOut = (source, name) => {
  const header = new RegExp(`^export (function|class) ${escape(name)}\\b.*\\{$`, "m").exec(source);
  const end = header ? source.indexOf("\n}\n", header.index) : -1;
  if (end < 0) return null;
  const body = header[1] === "class" ? "  // TODO" : "  throw new Error('not implemented');";
  return `${source.slice(0, header.index + header[0].length)}\n${body}${source.slice(end)}`;
};

// kind "feature": apply(files, variant, { replace, delete }) returns a copy of the reference file set with the
// `replace` files taken from the variant set and the `delete` files dropped (all paths already renamed).
const removeFeature = (files, variant, feature) => {
  const out = { ...files };
  for (const rel of feature.replace) out[rel] = variant[rel];
  for (const rel of feature.delete) delete out[rel];
  return out;
};

// The hint words of `ops` that occur in `text` (whole words or phrases, any case).
export function hintHits(text, ops) {
  const hits = [];
  for (const op of ops) {
    for (const hint of OPERATORS[op]?.hints ?? []) if (new RegExp(`\\b${escape(hint)}\\b`, "i").test(text)) hits.push(hint);
  }
  return hits;
}
