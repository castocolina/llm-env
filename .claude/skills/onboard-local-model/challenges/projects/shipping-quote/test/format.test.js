import test from 'node:test';
import assert from 'node:assert/strict';
import { quote } from '../src/quote.js';
import { formatCents, formatQuote } from '../src/format.js';

const light = { weight: 3, unit: 'lb', lengthCm: 10, widthCm: 10, heightCm: 10 };

test('formatCents shows dollars and cents', () => {
  assert.equal(formatCents(680), '$6.80');
  assert.equal(formatCents(5), '$0.05');
});
test('a standard quote reads one line per parcel', () =>
  assert.equal(formatQuote(quote([light], { zone: 'A' })), ['3 lb -> 1.5 kg: $6.80', 'Service: Standard', 'Total: $6.80'].join('\n')));
test('an express quote names the service and the total', () => {
  const text = formatQuote(quote([light], { zone: 'A', service: 'express' }));
  assert.ok(text.endsWith('Service: Express\nTotal: $10.88'), text);
});
