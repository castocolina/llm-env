import test from 'node:test';
import assert from 'node:assert/strict';
import { dedupeSorted } from '../src/transform/dedupe.js';
import { runTransforms } from '../src/transform/pipeline.js';
import { validateAll } from '../src/transform/validate.js';

const ev = (ts, user, type = 'view', value = 1) => ({ ts, user, type, value });

test('validateAll counts the reasons rows are rejected', () => {
  const { valid, rejected } = validateAll([ev(1000, 'a'), ev(0, 'a'), ev(1000, ''), ev(1000, 'a', 'view', -1), { bad: true }]);
  assert.equal(valid.length, 1);
  assert.deepEqual(rejected, { 'bad-timestamp': 1, 'missing-user': 1, 'bad-value': 1, unparsable: 1 });
});

test('dedupeSorted drops neighbouring repeats of the same event', () => {
  const out = dedupeSorted([ev(1, 'a'), ev(1, 'a'), ev(2, 'a'), ev(2, 'b'), ev(2, 'b')]);
  assert.deepEqual(out.map((e) => `${e.ts}${e.user}`), ['1a', '2a', '2b']);
});

test('runTransforms reports what it rejected and dropped', () => {
  const { events, rejected, duplicates } = runTransforms([ev(1000, 'a'), ev(1000, 'a'), ev(2000, 'b'), ev(0, 'c')]);
  assert.equal(events.length, 2);
  assert.equal(duplicates, 1);
  assert.deepEqual(rejected, { 'bad-timestamp': 1 });
});
