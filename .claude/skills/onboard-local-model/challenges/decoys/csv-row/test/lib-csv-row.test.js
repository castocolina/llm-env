import test from 'node:test';
import assert from 'node:assert/strict';
import { toCsvRow } from '../src/lib/csv-row.js';

test('toCsvRow joins plain values', () => {
  assert.equal(toCsvRow(['a', 1, null]), 'a,1,');
});

test('toCsvRow quotes commas and doubles quotes', () => {
  assert.equal(toCsvRow(['x,y', 'say "hi"']), '"x,y","say ""hi"""');
});
