import test from 'node:test';
import assert from 'node:assert/strict';
import { movingAvg } from '../src/lib/window-avg.js';

test('movingAvg averages each window', () => {
  assert.deepEqual(movingAvg([1, 2, 3, 4], 2), [1.5, 2.5, 3.5]);
});

test('movingAvg of a short list is empty', () => {
  assert.deepEqual(movingAvg([1], 2), []);
});

test('movingAvg rejects a window below 1', () => {
  assert.throws(() => movingAvg([1], 0), RangeError);
});
