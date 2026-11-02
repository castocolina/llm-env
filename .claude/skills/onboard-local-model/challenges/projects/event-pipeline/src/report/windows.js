import { DAY_MS, HOUR_MS, floorTo, isoHour } from '../util/time.js';

// Hourly buckets over [first event, last event], empty hours included so charts have no gaps.
export function hourlyBuckets(events) {
  if (events.length === 0) return [];
  const counts = new Map();
  for (const e of events) counts.set(floorTo(e.ts, HOUR_MS), (counts.get(floorTo(e.ts, HOUR_MS)) ?? 0) + 1);
  const first = Math.min(...counts.keys());
  const last = Math.max(...counts.keys());
  const out = [];
  for (let t = first; t <= last; t += HOUR_MS) out.push({ hour: isoHour(t), events: counts.get(t) ?? 0 });
  return out;
}

export const busiestHour = (buckets) => buckets.reduce((best, b) => (b.events > (best?.events ?? -1) ? b : best), null);

// Largest number of events inside any window of `size` ms that starts at an event (events sorted by ts).
export function peakInWindow(events, size) {
  let best = 0;
  let lo = 0;
  for (let hi = 0; hi < events.length; hi++) {
    while (events[hi].ts - events[lo].ts >= size) lo++;
    best = Math.max(best, hi - lo + 1);
  }
  return best;
}

export const dailyWindow = (events, day) => events.filter((e) => floorTo(e.ts, DAY_MS) === Date.parse(`${day}T00:00:00Z`));

// Events per UTC day, keyed "YYYY-MM-DD", for events that have no `day` field yet.
export function dailyCounts(events) {
  const out = {};
  for (const e of events) {
    const day = new Date(floorTo(e.ts, DAY_MS)).toISOString().slice(0, 10);
    out[day] = (out[day] ?? 0) + 1;
  }
  return out;
}
