export const sum = (xs) => xs.reduce((a, b) => a + b, 0);

export const mean = (xs) => (xs.length ? sum(xs) / xs.length : 0);

export function median(xs) {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

// Population standard deviation.
export function stddev(xs) {
  if (xs.length < 2) return 0;
  const m = mean(xs);
  return Math.sqrt(mean(xs.map((x) => (x - m) ** 2)));
}

// Nearest-rank percentile, p in (0, 100].
export function percentile(xs, p) {
  if (!xs.length) return 0;
  const sorted = [...xs].sort((a, b) => a - b);
  const rank = Math.ceil((p / 100) * sorted.length);
  return sorted[Math.max(0, rank - 1)];
}

export function groupBy(xs, keyFn) {
  const out = new Map();
  for (const x of xs) {
    const k = keyFn(x);
    if (!out.has(k)) out.set(k, []);
    out.get(k).push(x);
  }
  return out;
}

export function countBy(xs, keyFn) {
  const out = {};
  for (const x of xs) {
    const k = keyFn(x);
    out[k] = (out[k] ?? 0) + 1;
  }
  return out;
}

// Fixed-width histogram: [{ from, to, count }] covering [min, max] of the values.
export function histogram(xs, bins) {
  if (!xs.length || bins < 1) return [];
  const lo = Math.min(...xs);
  const hi = Math.max(...xs);
  const width = (hi - lo) / bins || 1;
  const out = Array.from({ length: bins }, (_, i) => ({ from: lo + i * width, to: lo + (i + 1) * width, count: 0 }));
  for (const x of xs) out[Math.min(bins - 1, Math.floor((x - lo) / width))].count++;
  return out;
}

export const sortedEntries = (obj) => Object.entries(obj).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));

// Simple moving average over a window of `n` points; the first n-1 outputs use the points available so far.
export function movingAverage(xs, n) {
  return xs.map((_, i) => mean(xs.slice(Math.max(0, i - n + 1), i + 1)));
}

// How many standard deviations each value is from the mean (all zeros when there is no spread).
export function zScores(xs) {
  const m = mean(xs);
  const sd = stddev(xs);
  return xs.map((x) => (sd === 0 ? 0 : (x - m) / sd));
}

export const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));

export const round1 = (x) => Math.round(x * 10) / 10;

// Cumulative sums: [1, 2, 3] -> [1, 3, 6]
export function cumulative(xs) {
  let acc = 0;
  return xs.map((x) => (acc += x));
}
