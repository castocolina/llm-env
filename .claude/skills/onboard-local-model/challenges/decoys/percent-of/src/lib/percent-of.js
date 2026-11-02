// part as a percentage of whole, rounded to `digits` decimals; an empty whole gives 0.
export function percentOf(part, whole, digits = 1) {
  if (whole === 0) return 0;
  const scale = 10 ** digits;
  return Math.round((part / whole) * 100 * scale) / scale;
}
