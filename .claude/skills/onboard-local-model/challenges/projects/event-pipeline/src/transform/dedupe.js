import { eventKey } from '../util/key.js';

// Drops repeated deliveries of the same event. Input must be sorted (see order.js) so that
// events with the same key sit next to each other; comparing with the previous kept event is enough.
export function dedupeSorted(events) {
  const out = [];
  let prevKey = null;
  for (const e of events) {
    const k = eventKey(e);
    if (k === prevKey) continue;
    out.push(e);
    prevKey = k;
  }
  return out;
}
