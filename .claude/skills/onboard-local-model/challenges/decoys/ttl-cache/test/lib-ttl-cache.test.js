import test from 'node:test';
import assert from 'node:assert/strict';
import { TtlMap } from '../src/lib/ttl-cache.js';

test('TtlMap returns a value before it expires', () => {
  let t = 0;
  const m = new TtlMap(10, () => t);
  m.set('a', 1);
  t = 9;
  assert.equal(m.get('a'), 1);
});

test('TtlMap forgets a value once its time is up', () => {
  let t = 0;
  const m = new TtlMap(10, () => t);
  m.set('a', 1);
  t = 10;
  assert.equal(m.get('a'), undefined);
});
