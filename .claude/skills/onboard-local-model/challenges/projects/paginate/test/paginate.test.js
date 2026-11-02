import test from 'node:test';
import assert from 'node:assert/strict';
import { paginate } from '../src/paginate.js';

const ten = Array.from({ length: 10 }, (_, i) => i + 1);
test('first page', () => assert.deepEqual(paginate(ten, 1, 4).items, [1, 2, 3, 4]));
test('last partial page', () => assert.deepEqual(paginate(ten, 3, 4).items, [9, 10]));
test('total pages rounds up', () => assert.equal(paginate(ten, 1, 4).totalPages, 3));
test('page past the end is empty', () => assert.deepEqual(paginate(ten, 4, 4).items, []));
