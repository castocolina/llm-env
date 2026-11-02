import test from 'node:test';
import assert from 'node:assert/strict';
import { parseNdjson } from '../src/ingest/ndjson.js';
import { parseTs } from '../src/ingest/timestamps.js';

const T = Date.parse('2026-10-30T10:00:00Z');

test('parseTs reads ISO text, epoch seconds and epoch milliseconds', () => {
  assert.equal(parseTs('2026-10-30T10:00:00Z'), T);
  assert.equal(parseTs(T / 1000), T);
  assert.equal(parseTs(String(T)), T);
  assert.ok(Number.isNaN(parseTs('yesterday')));
});

test('parseNdjson skips blank lines, keeps bad ones as bad rows and honours aliases', () => {
  const rows = parseNdjson('\n{"ts":1793354400,"user":"a","type":"x","value":1}\nnope\n{"timestamp":"2026-10-30T10:00:00Z","user":"b","kind":"y","value":2}\n');
  assert.equal(rows.length, 3);
  assert.deepEqual(rows[0], { ts: T, user: 'a', type: 'x', value: 1 });
  assert.equal(rows[1].bad, true);
  assert.deepEqual(rows[2], { ts: T, user: 'b', type: 'y', value: 2 });
});
