import test from 'node:test';
import assert from 'node:assert/strict';
import { Ring } from '../src/lib/ring-buffer.js';

test('Ring keeps everything below its capacity', () => {
  assert.deepEqual(new Ring(3).push(1).push(2).toArray(), [1, 2]);
});

test('Ring drops the oldest item when full', () => {
  const r = new Ring(2);
  [1, 2, 3].forEach((n) => r.push(n));
  assert.deepEqual(r.toArray(), [2, 3]);
});
