import { busiestHour, hourlyBuckets } from './windows.js';
import { byDay, topUsers, totalsOf, valueStats } from './totals.js';

// Everything the text report prints, as plain data.
export function buildSummary(store, { rejected = {}, duplicates = 0 } = {}) {
  const hourly = hourlyBuckets(store.all());
  return {
    ...totalsOf(store),
    duplicates,
    rejected,
    hourly,
    busiest: busiestHour(hourly),
    top: topUsers(store, 3),
    days: byDay(store),
    stats: valueStats(store),
  };
}
