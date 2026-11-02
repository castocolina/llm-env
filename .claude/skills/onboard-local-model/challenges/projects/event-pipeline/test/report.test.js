import test from 'node:test';
import assert from 'node:assert/strict';
import { formatReport } from '../src/report/format.js';
import { buildSummary } from '../src/report/summary.js';
import { hourlyBuckets } from '../src/report/windows.js';
import { EventStore } from '../src/store/memory.js';
import { enrichAll } from '../src/transform/enrich.js';

const H = 3600000;
const T0 = Date.parse('2026-10-30T10:00:00Z');
const store = () =>
  new EventStore().addAll(
    enrichAll([
      { ts: T0, user: 'a', type: 'view', value: 1 },
      { ts: T0 + 10, user: 'b', type: 'view', value: 3 },
      { ts: T0 + 2 * H, user: 'a', type: 'buy', value: 10 },
    ]),
  );

test('hourlyBuckets keeps empty hours between the first and last event', () => {
  const b = hourlyBuckets(store().all());
  assert.deepEqual(b.map((x) => x.events), [2, 0, 1]);
  assert.equal(b[1].hour, '2026-10-30T11:00Z');
});

test('the summary and its text form agree with the store', () => {
  const s = buildSummary(store(), { duplicates: 2 });
  assert.deepEqual([s.events, s.valueSum, s.duplicates, s.busiest.events], [3, 14, 2, 2]);
  const text = formatReport(s);
  assert.match(text, /^events: 3$/m);
  assert.match(text, /busiest hour: 2026-10-30T10:00Z \(2\)/);
});
