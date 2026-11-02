import test from 'node:test';
import assert from 'node:assert/strict';
import { money, add, sum } from '../src/money.js';
import { formatMoney } from '../src/format.js';

test('money defaults to USD', () => assert.deepEqual(money(1999), { cents: 1999, currency: 'USD' }));
test('add keeps working for same-currency values', () => assert.equal(add(money(150), money(275)).cents, 425));
test('add refuses to mix currencies', () => assert.throws(() => add(money(1, 'USD'), money(1, 'EUR')), RangeError));
test('sum of nothing is zero in the requested currency', () =>
  assert.deepEqual(sum([], 'EUR'), { cents: 0, currency: 'EUR' }));
test('formatMoney uses the currency symbol and minor-unit digits', () => {
  assert.equal(formatMoney(money(1999)), '$19.99');
  assert.equal(formatMoney(money(1234, 'EUR')), '€12.34');
  assert.equal(formatMoney(money(1867, 'JPY')), '¥1867');
  assert.equal(formatMoney(money(-5)), '-$0.05');
});
