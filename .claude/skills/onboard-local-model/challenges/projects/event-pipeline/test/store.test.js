import test from 'node:test';
import assert from 'node:assert/strict';
import { EventStore } from '../src/store/memory.js';
import { fromSnapshot, toSnapshot } from '../src/store/snapshot.js';

const ev = (ts, user, type = 'view', value = 1) => ({ ts, user, type, value });
const filled = () => new EventStore().addAll([ev(10, 'a'), ev(20, 'b', 'login'), ev(30, 'a', 'login', 5)]);

test('the store indexes by user and by type and answers range queries', () => {
  const s = filled();
  assert.equal(s.size, 3);
  assert.deepEqual(s.userCounts(), { a: 2, b: 1 });
  assert.deepEqual(s.typeCounts(), { view: 1, login: 2 });
  assert.deepEqual(s.range(10, 30).map((e) => e.ts), [10, 20]);
});

test('snapshots round-trip and detect corruption', () => {
  const text = toSnapshot(filled());
  assert.deepEqual(fromSnapshot(text).all(), filled().all());
  assert.throws(() => fromSnapshot(text.replace('"value\\":5', '"value\\":6')), /checksum/);
});
