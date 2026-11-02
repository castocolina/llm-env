import test from 'node:test';
import assert from 'node:assert/strict';
import { slug } from '../src/lib/slug.js';

test('slug joins words with hyphens', () => {
  assert.equal(slug('Hello, World!'), 'hello-world');
});

test('slug trims separators and handles empty input', () => {
  assert.equal(slug('  --Ab  '), 'ab');
  assert.equal(slug(''), '');
});
