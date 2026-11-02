import test from 'node:test';
import assert from 'node:assert/strict';
import { money } from '../src/money.js';
import { convert } from '../src/convert.js';

const rates = { USD: 1, EUR: 0.92, JPY: 151.3, CHF: 1.005 };

test('USD to EUR', () => assert.deepEqual(convert(money(10000), 'EUR', rates), money(9200, 'EUR')));
test('USD to JPY drops the minor digits', () => assert.deepEqual(convert(money(1234), 'JPY', rates), money(1867, 'JPY')));
test('EUR to JPY goes through the rate table', () => assert.deepEqual(convert(money(10000, 'EUR'), 'JPY', rates), money(16446, 'JPY')));
test('half a minor unit rounds up (1.00 USD at 1.005 is 1.01 CHF)', () =>
  assert.deepEqual(convert(money(100), 'CHF', rates), money(101, 'CHF')));
test('a missing rate is an error', () => assert.throws(() => convert(money(100), 'EUR', { USD: 1 }), RangeError));
