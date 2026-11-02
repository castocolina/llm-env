import { isFiniteTs } from '../util/time.js';

export const DEFAULT_MAX_VALUE = 1_000_000;

// Returns the first reason a row is unusable, or null.
export function rejectionReason(row, { maxValue = DEFAULT_MAX_VALUE } = {}) {
  if (row.bad) return 'unparsable';
  if (!isFiniteTs(row.ts)) return 'bad-timestamp';
  if (!row.user) return 'missing-user';
  if (!row.type) return 'missing-type';
  if (!Number.isFinite(row.value) || row.value < 0) return 'bad-value';
  if (row.value > maxValue) return 'value-too-large';
  return null;
}

// -> { valid: [...], rejected: { reason: count } }
export function validateAll(rows, options = {}) {
  const valid = [];
  const rejected = {};
  for (const row of rows) {
    const reason = rejectionReason(row, options);
    if (reason) rejected[reason] = (rejected[reason] ?? 0) + 1;
    else valid.push(row);
  }
  return { valid, rejected };
}

export const REASONS = ['unparsable', 'bad-timestamp', 'missing-user', 'missing-type', 'bad-value', 'value-too-large'];

// Total number of rejected rows in a `rejected` tally.
export const rejectedTotal = (rejected) => Object.values(rejected).reduce((a, b) => a + b, 0);
