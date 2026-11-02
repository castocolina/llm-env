import test from 'node:test';
import assert from 'node:assert/strict';
import { quote } from '../src/quote.js';

const light = { weight: 3, unit: 'lb', lengthCm: 10, widthCm: 10, heightCm: 10 };
const bulky = { weight: 500, unit: 'g', lengthCm: 50, widthCm: 40, heightCm: 30 };
const dense = { weight: 2, unit: 'kg', lengthCm: 10, widthCm: 10, heightCm: 10 };

test('a light parcel is billed on its weight, rounded up to half a kilo', () => {
  const q = quote([light], { zone: 'A' });
  assert.deepEqual([q.lines[0].billableKg, q.lines[0].cents, q.totalCents], [1.5, 680, 680]);
});
test('a bulky parcel is billed on its volume', () => {
  const q = quote([bulky], { zone: 'B' });
  assert.deepEqual([q.lines[0].billableKg, q.lines[0].cents], [12, 2960]);
});
test('a dense parcel in the far zone', () => {
  const q = quote([dense], { zone: 'C' });
  assert.deepEqual([q.lines[0].billableKg, q.lines[0].cents], [2, 2100]);
});
test('express costs 160 percent', () => assert.equal(quote([light], { zone: 'A', service: 'express' }).totalCents, 1088));
test('the total adds the parcels', () => assert.equal(quote([light, bulky], { zone: 'A' }).totalCents, 680 + 500 + 120 * 12));
test('unknown zone, unit and service are errors', () => {
  assert.throws(() => quote([light], { zone: 'Z' }), RangeError);
  assert.throws(() => quote([{ ...light, unit: 'oz' }], { zone: 'A' }), RangeError);
  assert.throws(() => quote([light], { zone: 'A', service: 'drone' }), RangeError);
});
test('no parcels is an error', () => assert.throws(() => quote([], { zone: 'A' }), RangeError));
