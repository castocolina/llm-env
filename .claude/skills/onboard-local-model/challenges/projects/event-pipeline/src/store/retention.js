import { EventStore } from './memory.js';

// New store holding only the events at or after `cutoff` (the original is never modified).
export function keepSince(store, cutoff) {
  return new EventStore().addAll(store.all().filter((e) => e.ts >= cutoff));
}

// Merges stores in order; events are not deduplicated here, run the transform stages for that.
export function mergeStores(...stores) {
  return new EventStore().addAll(stores.flatMap((s) => s.all()));
}

// Splits a store into one store per UTC day, keyed "YYYY-MM-DD".
export function partitionByDay(store) {
  const parts = new Map();
  for (const e of store) {
    const day = new Date(e.ts).toISOString().slice(0, 10);
    if (!parts.has(day)) parts.set(day, new EventStore());
    parts.get(day).add(e);
  }
  return Object.fromEntries([...parts].sort(([a], [b]) => (a < b ? -1 : 1)));
}
