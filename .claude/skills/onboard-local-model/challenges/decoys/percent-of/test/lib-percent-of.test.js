import test from 'node:test';
import assert from 'node:assert/strict';
import { percentOf } from '../src/lib/percent-of.js';

test('percentOf rounds to one digit by default', () => {
  assert.equal(percentOf(1, 3), 33.3);
});

test('percentOf honours the digits argument', () => {
  assert.equal(percentOf(1, 3, 0), 33);
  assert.equal(percentOf(1, 8, 2), 12.5);
});

test('percentOf of an empty whole is 0', () => {
  assert.equal(percentOf(5, 0), 0);
});
