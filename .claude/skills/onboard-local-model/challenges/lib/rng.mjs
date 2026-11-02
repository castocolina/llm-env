// Seeded, labelled random streams for the challenge generator (Decision 4). Pure: no clock, no environment.
export function fnv1a32(text) {
  let h = 0x811c9dc5;
  for (const byte of Buffer.from(text, "utf8")) {
    h ^= byte;
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

function mulberry32(seed) {
  let a = seed | 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// One stream per (seed, label); callers fold the attempt into the label ("a<attempt>/<label>").
export function rng(seed, label) {
  const next = mulberry32((fnv1a32(label) ^ seed) >>> 0);
  const int = (lo, hi) => lo + Math.floor(next() * (hi - lo + 1));
  const pick = (arr) => arr[int(0, arr.length - 1)];
  const shuffle = (arr) => {
    const out = [...arr];
    for (let i = out.length - 1; i > 0; i--) {
      const j = int(0, i);
      [out[i], out[j]] = [out[j], out[i]];
    }
    return out;
  };
  const sample = (arr, k) => shuffle(arr).slice(0, k);
  return { next, int, pick, shuffle, sample };
}
