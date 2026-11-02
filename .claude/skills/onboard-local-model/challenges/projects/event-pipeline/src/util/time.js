export const SECOND_MS = 1000;
export const MINUTE_MS = 60 * SECOND_MS;
export const HOUR_MS = 60 * MINUTE_MS;
export const DAY_MS = 24 * HOUR_MS;

// Start of the `size`-millisecond window that contains `ts` (windows are aligned to the epoch).
export function floorTo(ts, size) {
  return ts - (ts % size);
}

export function isoHour(ts) {
  return new Date(floorTo(ts, HOUR_MS)).toISOString().slice(0, 13) + ':00Z';
}

export function isoDay(ts) {
  return new Date(floorTo(ts, DAY_MS)).toISOString().slice(0, 10);
}

export function isFiniteTs(ts) {
  return Number.isFinite(ts) && ts > 0;
}

// "90s" | "15m" | "2h" | "1d" -> milliseconds; throws on anything else.
export function parseDuration(text) {
  const m = /^(\d+)\s*(s|m|h|d)$/.exec(String(text).trim());
  if (!m) throw new RangeError(`bad duration: ${text}`);
  const unit = { s: SECOND_MS, m: MINUTE_MS, h: HOUR_MS, d: DAY_MS }[m[2]];
  return Number(m[1]) * unit;
}

export function formatDuration(ms) {
  if (ms % DAY_MS === 0) return `${ms / DAY_MS}d`;
  if (ms % HOUR_MS === 0) return `${ms / HOUR_MS}h`;
  if (ms % MINUTE_MS === 0) return `${ms / MINUTE_MS}m`;
  return `${Math.round(ms / SECOND_MS)}s`;
}
