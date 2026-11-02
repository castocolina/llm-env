import { dedupeSorted } from './dedupe.js';
import { enrichAll } from './enrich.js';
import { normalizeAll } from './normalize.js';
import { sortEvents } from './order.js';
import { sessionize } from './sessions.js';
import { validateAll } from './validate.js';

// raw rows -> { events, rejected, duplicates }
export function runTransforms(rows, { maxValue, gapMs } = {}) {
  const { valid, rejected } = validateAll(normalizeAll(rows), { maxValue });
  const sorted = sortEvents(valid);
  const events = sessionize(enrichAll(dedupeSorted(sorted)), gapMs);
  return { events, rejected, duplicates: sorted.length - events.length };
}
