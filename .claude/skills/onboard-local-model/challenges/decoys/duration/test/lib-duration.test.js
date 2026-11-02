import test from 'node:test';
import assert from 'node:assert/strict';
import { formatDuration } from '../src/lib/duration.js';

test('formatDuration spells hours, minutes and seconds', () => {
  assert.equal(formatDuration(3725000), '1h 2m 5s');
});

test('formatDuration skips zero parts', () => {
  assert.equal(formatDuration(3600000), '1h');
  assert.equal(formatDuration(0), '0s');
});
