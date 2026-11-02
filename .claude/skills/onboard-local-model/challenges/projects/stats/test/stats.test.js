import test from 'node:test';
import assert from 'node:assert/strict';
import { mean, median, percentile, histogram, countInRange } from '../src/stats.js';

test('mean of four values', () => assert.equal(mean([1, 2, 3, 6]), 3));
test('median of an even count averages the middle pair', () => assert.equal(median([5, 1, 4, 2]), 3));
test('median of an odd count', () => assert.equal(median([9, 1, 5]), 5));
test('median leaves its input unchanged', () => {
  const xs = [5, 1, 4, 2];
  median(xs);
  assert.deepEqual(xs, [5, 1, 4, 2]);
});
test('percentile picks the smallest value for a low rank', () => assert.equal(percentile([1, 2, 3, 4, 5], 20), 1));
test('percentile uses the nearest rank', () => assert.equal(percentile([10, 20, 30, 40], 90), 40));
test('histogram buckets by width, negatives included', () =>
  assert.deepEqual(histogram([-3, 2, 7, 12], 5), { '-5': 1, '0': 1, '5': 1, '10': 1 }));
test('countInRange includes lo and excludes hi', () => assert.equal(countInRange([1, 5, 10], 1, 10), 2));
