import { trimLower } from '../util/strings.js';

// Spelling variants seen in the field that mean the same event type.
const TYPE_ALIASES = { 'log-in': 'login', signin: 'login', 'sign-in': 'login', buy: 'purchase', pageview: 'view' };

export const canonicalType = (t) => TYPE_ALIASES[trimLower(t)] ?? trimLower(t);

// Users and types are compared case-insensitively; values arrive as numbers or numeric text.
export function normalizeEvent(row) {
  if (row.bad) return row;
  const value = typeof row.value === 'number' ? row.value : Number(String(row.value ?? '').trim());
  return { ts: row.ts, user: trimLower(row.user), type: canonicalType(row.type), value };
}

export const normalizeAll = (rows) => rows.map(normalizeEvent);

// Collapses internal whitespace so "ana  smith" and "ana smith" are the same user.
export const squeeze = (s) => String(s ?? '').trim().replace(/\s+/g, ' ');
