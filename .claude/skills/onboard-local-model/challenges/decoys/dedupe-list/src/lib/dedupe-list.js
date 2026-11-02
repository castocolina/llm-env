// Keeps the first item for each distinct keyFn(item), in input order.
export function dedupeBy(xs, keyFn) {
  const seen = new Set();
  return xs.filter((x) => {
    const k = keyFn(x);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}
