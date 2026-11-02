import { groupBy } from '../util/stats.js';

// Builds a Map from a key function once; the store keeps these in sync on add.
export function buildIndex(events, keyFn) {
  return groupBy(events, keyFn);
}

export function addToIndex(index, event, keyFn) {
  const k = keyFn(event);
  const bucket = index.get(k);
  if (bucket) bucket.push(event);
  else index.set(k, [event]);
}

export const indexSizes = (index) => Object.fromEntries([...index].map(([k, v]) => [k, v.length]));

// Keys of an index sorted by bucket size, largest first.
export function largestBuckets(index, n) {
  return [...index].sort((a, b) => b[1].length - a[1].length).slice(0, n).map(([k]) => k);
}

export const hasKey = (index, key) => index.has(key) && index.get(key).length > 0;
