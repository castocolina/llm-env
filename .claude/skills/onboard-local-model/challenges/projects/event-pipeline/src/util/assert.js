export function invariant(cond, message) {
  if (!cond) throw new Error(`invariant failed: ${message}`);
}

export const isNonNegativeInt = (n) => Number.isInteger(n) && n >= 0;

export function assertEvent(e) {
  invariant(e && typeof e === 'object', 'event is an object');
  invariant(typeof e.user === 'string' && e.user !== '', 'event has a user');
  invariant(typeof e.type === 'string' && e.type !== '', 'event has a type');
  invariant(Number.isFinite(e.ts), 'event has a numeric timestamp');
  return e;
}

// Throws with the name of the first failing field when `row` is not a usable raw row.
export function describeRow(row) {
  if (row === null || typeof row !== 'object') return 'row is not an object';
  for (const field of ['ts', 'user', 'type', 'value']) if (!(field in row)) return `missing field: ${field}`;
  return null;
}

export const assertAll = (items, check) => items.map((item) => check(item));
