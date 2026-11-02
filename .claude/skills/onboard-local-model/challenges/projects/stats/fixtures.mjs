// Seeded cases for the stats project: 8-12 calls over arrays of 5-15 integers in [-20, 60].
export const entry = "src/stats.js";

export function fixtures(rng) {
  const cases = [];
  const count = rng.int(8, 12);
  for (let i = 0; i < count; i++) {
    const xs = Array.from({ length: rng.int(5, 15) }, () => rng.int(-20, 60));
    const kind = rng.int(0, 4);
    let call = "median";
    let args = [xs];
    if (kind === 1) { call = "percentile"; args = [xs, rng.pick([10, 20, 50, 90, 100])]; }
    else if (kind === 2) { call = "histogram"; args = [xs, rng.pick([5, 10])]; }
    else if (kind === 3) { const lo = rng.int(-20, 30); call = "countInRange"; args = [xs, lo, lo + rng.int(5, 30)]; }
    else if (kind === 4) { call = "mean"; }
    cases.push({ name: `case ${i + 1}: ${call} of ${xs.length} values`, call, args });
  }
  return { cases };
}
