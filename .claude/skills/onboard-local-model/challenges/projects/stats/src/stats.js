export const ascending = (a, b) => a - b;

export const mean = (xs) => xs.reduce((sum, x) => sum + x, 0) / xs.length;

export function median(xs) {
  if (xs.length === 0) return 0;
  const sorted = [...xs].sort(ascending);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

export function percentile(xs, p) {
  if (xs.length === 0) return 0;
  const sorted = [...xs].sort(ascending);
  const rank = Math.max(1, Math.ceil((p / 100) * sorted.length));
  return sorted[rank - 1];
}

export function histogram(xs, width) {
  const counts = {};
  for (const x of xs) {
    const start = Math.floor(x / width) * width;
    counts[start] = (counts[start] ?? 0) + 1;
  }
  return counts;
}

export function countInRange(xs, lo, hi) {
  return xs.filter((x) => x >= lo && x < hi).length;
}
