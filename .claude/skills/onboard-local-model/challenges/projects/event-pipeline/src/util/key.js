// Identity and ordering helpers shared by the transform and store stages.

// Two events are the same event when user, type and timestamp agree (the sources carry no ids).
export function eventKey(e) {
  return `${e.user}|${e.type}|${e.ts}`;
}

export function compareEvents(a, b) {
  return a.ts - b.ts || compareKeys(eventKey(a), eventKey(b));
}

export function compareKeys(a, b) {
  return a < b ? -1 : a > b ? 1 : 0;
}

// Sorts a copy of `events` by identity key, for diffing two runs of the pipeline.
export const sortedByKey = (events) => events.slice().sort((a, b) => compareKeys(eventKey(a), eventKey(b)));

// True when both lists hold the same events regardless of order and repeats.
export function sameEventSet(a, b) {
  const keys = (xs) => [...new Set(xs.map(eventKey))].sort(compareKeys);
  const ka = keys(a);
  const kb = keys(b);
  return ka.length === kb.length && ka.every((k, i) => k === kb[i]);
}
