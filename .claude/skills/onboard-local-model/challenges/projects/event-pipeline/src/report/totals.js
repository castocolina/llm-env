import { groupBy, mean, median, percentile, sortedEntries, sum } from '../util/stats.js';

// Counts and value sums straight from the store's indexes.
export function totalsOf(store) {
  const events = store.all();
  return {
    events: store.size,
    valueSum: sum(events.map((e) => e.value)),
    byType: Object.fromEntries(sortedEntries(store.typeCounts())),
    byUser: Object.fromEntries(sortedEntries(store.userCounts())),
  };
}

export function topUsers(store, n) {
  return sortedEntries(store.userCounts())
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([user, events]) => ({ user, events }));
}

// Value statistics per event type: { type: { count, mean, median, p90 } }.
export function valueStats(store) {
  const out = {};
  for (const [type, evs] of groupBy(store.all(), (e) => e.type)) {
    const values = evs.map((e) => e.value);
    out[type] = { count: values.length, mean: mean(values), median: median(values), p90: percentile(values, 90) };
  }
  return out;
}

export function byDay(store) {
  const out = {};
  for (const [day, evs] of groupBy(store.all(), (e) => e.day)) out[day] = evs.length;
  return Object.fromEntries(sortedEntries(out));
}

// Share of each type in the event total, rounded to one decimal place.
export function typeShares(store) {
  const out = {};
  for (const [type, n] of Object.entries(store.typeCounts())) out[type] = store.size ? Math.round((n / store.size) * 1000) / 10 : 0;
  return out;
}

// Per-user activity: first and last event time plus the number of distinct sessions.
export function userActivity(store) {
  const out = {};
  for (const e of store) {
    const a = out[e.user] ?? { first: e.ts, last: e.ts, sessions: new Set() };
    a.first = Math.min(a.first, e.ts);
    a.last = Math.max(a.last, e.ts);
    if (e.session) a.sessions.add(e.session);
    out[e.user] = a;
  }
  return Object.fromEntries(
    sortedEntries(out).map(([user, a]) => [user, { first: a.first, last: a.last, sessions: a.sessions.size }]),
  );
}
