import test from 'node:test';
import assert from 'node:assert/strict';
import { errorReport } from '../src/report.js';

test('counts error and fatal lines', () => {
  assert.deepEqual(errorReport(new URL('../data/app.log', import.meta.url).pathname), { total: 5, errors: 3 });
});
