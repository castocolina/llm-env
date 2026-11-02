import test from 'node:test';
import assert from 'node:assert/strict';
import { money } from '../src/money.js';
import { createInvoice, invoiceTotals } from '../src/invoice.js';
import { formatInvoice } from '../src/format.js';

const rates = { USD: 1, EUR: 0.92, JPY: 151.3 };
const mixed = () =>
  createInvoice(
    [
      { description: 'Widget', unit: money(1000), qty: 2 },
      { description: 'Gadget', unit: money(1500, 'JPY'), qty: 1 },
    ],
    { taxBps: 1000, currency: 'EUR', rates },
  );

test('single-currency USD invoices keep their old totals', () => {
  const inv = createInvoice([{ description: 'A', unit: money(1999), qty: 2 }, { description: 'B', unit: money(500), qty: 1 }], { taxBps: 725 });
  const t = invoiceTotals(inv);
  assert.deepEqual([t.subtotal.cents, t.tax.cents, t.total.cents], [4498, 326, 4824]);
});
test('an all-EUR invoice totals in EUR without a rate table', () => {
  const t = invoiceTotals(createInvoice([{ description: 'A', unit: money(1000, 'EUR'), qty: 3 }], { taxBps: 2000 }));
  assert.deepEqual(t.total, money(3600, 'EUR'));
  assert.equal(t.tax.currency, 'EUR');
});
test('a mixed invoice converts each line, then sums, then taxes', () => {
  const t = invoiceTotals(mixed());
  assert.deepEqual([t.subtotal, t.tax, t.total], [money(2752, 'EUR'), money(275, 'EUR'), money(3027, 'EUR')]);
  assert.deepEqual(t.discount, money(0, 'EUR'));
});
test('a discount comes off before tax and stays in the invoice currency', () => {
  const t = invoiceTotals(createInvoice([{ description: 'A', unit: money(2000, 'EUR'), qty: 1 }], { taxBps: 1000, discountBps: 500 }));
  assert.deepEqual([t.discount, t.tax, t.total], [money(100, 'EUR'), money(190, 'EUR'), money(2090, 'EUR')]);
});
test('a mixed invoice without rates is an error', () =>
  assert.throws(() => invoiceTotals(createInvoice([{ description: 'A', unit: money(1000), qty: 1 }, { description: 'B', unit: money(1000, 'EUR'), qty: 1 }])), RangeError));
test('the text formatter shows line currencies and settlement totals', () =>
  assert.equal(
    formatInvoice(mixed()),
    ['Widget x2 @ $10.00 = $20.00', 'Gadget x1 @ ¥1500 = ¥1500', 'Subtotal: €27.52', 'Tax (10.00%): €2.75', 'Total: €30.27'].join('\n'),
  ));
