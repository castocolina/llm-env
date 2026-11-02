import test from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { runPipeline } from '../src/run.js';

const data = (name) => fileURLToPath(new URL(`../data/${name}`, import.meta.url));
const sources = [data('events-a.ndjson'), data('events-b.csv')];

test('rejected rows are counted by reason across both sources', () => {
  const { summary } = runPipeline(sources);
  assert.deepEqual(summary.rejected, {
    unparsable: 3, 'missing-user': 1, 'bad-value': 2, 'bad-timestamp': 2, 'value-too-large': 2,
  });
});

test('end to end: every distinct event is counted once', () => {
  const { summary } = runPipeline(sources);
  assert.equal(summary.events, 171);
  assert.equal(summary.duplicates, 36);
  assert.equal(summary.valueSum, 55884);
});

test('end to end: per-type and per-user totals', () => {
  const { summary } = runPipeline(sources);
  assert.deepEqual(summary.byType, { login: 38, purchase: 27, view: 106 });
  assert.deepEqual(summary.byUser, { ana: 19, ben: 23, cy: 22, dee: 22, eli: 20, fay: 26, gus: 19, hal: 20 });
});

test('end to end: hourly and daily buckets add up to the event total', () => {
  const { summary } = runPipeline(sources);
  assert.equal(summary.hourly.reduce((n, b) => n + b.events, 0), summary.events);
  assert.deepEqual(summary.days, { '2026-10-30': 142, '2026-10-31': 29 });
  assert.deepEqual(summary.busiest, { hour: '2026-10-30T08:00Z', events: 52 });
});
