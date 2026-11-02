import test from 'node:test';
import assert from 'node:assert/strict';
import { LRUCache } from '../src/lru.js';

test('get and set', () => {
  const c = new LRUCache(2);
  c.set('a', 1);
  assert.equal(c.get('a'), 1);
  assert.equal(c.get('b'), undefined);
});
test('evicts least recently used', () => {
  const c = new LRUCache(2);
  c.set('a', 1); c.set('b', 2); c.get('a'); c.set('c', 3);
  assert.equal(c.get('b'), undefined);
  assert.equal(c.get('a'), 1);
  assert.equal(c.size, 2);
});
test('update refreshes recency and does not grow', () => {
  const c = new LRUCache(2);
  c.set('a', 1); c.set('b', 2); c.set('a', 10); c.set('c', 3);
  assert.equal(c.get('a'), 10);
  assert.equal(c.get('b'), undefined);
  assert.equal(c.size, 2);
});
