import { MINUTE_MS } from '../util/time.js';

export const DEFAULT_GAP_MS = 30 * MINUTE_MS;

// Splits each user's time-ordered events into sessions: a gap longer than `gapMs` starts a new one.
// Returns events tagged with `session` ("<user>#<n>", n counts from 1 per user).
export function sessionize(events, gapMs = DEFAULT_GAP_MS) {
  const last = new Map();
  const counter = new Map();
  return events.map((e) => {
    const prev = last.get(e.user);
    if (prev === undefined || e.ts - prev > gapMs) counter.set(e.user, (counter.get(e.user) ?? 0) + 1);
    last.set(e.user, e.ts);
    return { ...e, session: `${e.user}#${counter.get(e.user)}` };
  });
}

export function sessionLengths(events) {
  const bounds = new Map();
  for (const e of events) {
    const b = bounds.get(e.session) ?? { first: e.ts, last: e.ts };
    b.last = Math.max(b.last, e.ts);
    bounds.set(e.session, b);
  }
  return Object.fromEntries([...bounds].map(([s, b]) => [s, b.last - b.first]));
}

export const sessionCount = (events) => new Set(events.map((e) => e.session)).size;

// Sessions that never left the first event (length 0 ms), usually bots or probes.
export const singleEventSessions = (events) =>
  Object.entries(sessionLengths(events)).filter(([, len]) => len === 0).map(([s]) => s);
