// Sources write timestamps three ways: ISO-8601 text, epoch seconds or epoch milliseconds.
// Anything below 1e11 cannot be a millisecond value for a date after 1973, so it is read as seconds.
const SECONDS_LIMIT = 1e11;

export function parseTs(raw) {
  if (raw === null || raw === undefined || raw === '') return NaN;
  const n = typeof raw === 'number' ? raw : /^-?\d+(\.\d+)?$/.test(String(raw).trim()) ? Number(raw) : NaN;
  if (Number.isFinite(n)) return n < SECONDS_LIMIT ? Math.round(n * 1000) : Math.round(n);
  return Date.parse(String(raw).trim());
}
