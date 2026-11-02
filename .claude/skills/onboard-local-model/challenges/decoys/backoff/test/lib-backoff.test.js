import test from 'node:test';
import assert from 'node:assert/strict';
import { backoffMs } from '../src/lib/backoff.js';

test('backoffMs doubles with each attempt', () => {
  assert.deepEqual([0, 1, 2, 3].map((n) => backoffMs(n, 100, 10000)), [100, 200, 400, 800]);
});

test('backoffMs stops at the cap', () => {
  assert.equal(backoffMs(10, 100, 1000), 1000);
});
