import test from 'node:test';
import assert from 'node:assert/strict';
import { chunk } from '../src/lib/chunk.js';

test('chunk keeps a short last piece', () => {
  assert.deepEqual(chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]]);
});

test('chunk of an empty list is empty', () => {
  assert.deepEqual(chunk([], 3), []);
});

test('chunk rejects a size below 1', () => {
  assert.throws(() => chunk([1], 0), RangeError);
});
