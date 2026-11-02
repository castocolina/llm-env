import test from 'node:test';
import assert from 'node:assert/strict';
import { dedupeBy } from '../src/lib/dedupe-list.js';

test('dedupeBy keeps the first of each key', () => {
  const xs = [{ id: 1, v: 'a' }, { id: 2, v: 'b' }, { id: 1, v: 'c' }];
  assert.deepEqual(dedupeBy(xs, (x) => x.id).map((x) => x.v), ['a', 'b']);
});

test('dedupeBy of an empty list is empty', () => {
  assert.deepEqual(dedupeBy([], (x) => x), []);
});
