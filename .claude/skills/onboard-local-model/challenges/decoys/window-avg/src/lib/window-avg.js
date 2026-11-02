// Average of each run of `n` neighbouring numbers; shorter input gives an empty list.
export function movingAvg(xs, n) {
  if (!(n >= 1)) throw new RangeError('n must be at least 1');
  const out = [];
  for (let i = 0; i + n <= xs.length; i++) {
    out.push(xs.slice(i, i + n).reduce((a, b) => a + b, 0) / n);
  }
  return out;
}
