import test from 'node:test';
import assert from 'node:assert/strict';
import { eventKey } from '../src/util/key.js';
import { HOUR_MS, floorTo, isoDay, isoHour, parseDuration } from '../src/util/time.js';

test('eventKey is the same for the same user, type and timestamp', () => {
  const a = { ts: 5, user: 'ana', type: 'view', value: 1 };
  assert.equal(eventKey(a), eventKey({ ...a, value: 99 }));
  assert.notEqual(eventKey(a), eventKey({ ...a, user: 'ben' }));
});

test('time helpers floor to windows and parse durations', () => {
  const ts = Date.parse('2026-10-30T10:45:10Z');
  assert.equal(floorTo(ts, HOUR_MS), Date.parse('2026-10-30T10:00:00Z'));
  assert.equal(isoHour(ts), '2026-10-30T10:00Z');
  assert.equal(isoDay(ts), '2026-10-30');
  assert.equal(parseDuration('15m'), 900000);
  assert.throws(() => parseDuration('soon'), RangeError);
});
