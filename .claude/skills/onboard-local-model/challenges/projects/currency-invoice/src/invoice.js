import { DEFAULT_CURRENCY } from './currency.js';
import { convert } from './convert.js';
import { add, percent, sub, sum, times } from './money.js';
import { taxOn } from './tax.js';

const assertLine = (line) => {
  if (!Number.isInteger(line.qty) || line.qty < 1) throw new RangeError(`bad quantity for ${line.description}`);
};

// lines: [{ description, unit: money, qty }]; options: { taxBps = 0, discountBps = 0, currency, rates }
export function createInvoice(
  lines,
  { taxBps = 0, discountBps = 0, currency = lines[0]?.unit.currency ?? DEFAULT_CURRENCY, rates } = {},
) {
  lines.forEach(assertLine);
  return { lines, taxBps, discountBps, currency, rates };
}

export const lineTotal = (line) => times(line.unit, line.qty);

export function invoiceTotals(invoice) {
  const { currency, rates } = invoice;
  const subtotal = sum(invoice.lines.map((l) => convert(lineTotal(l), currency, rates)), currency);
  const discount = percent(subtotal, invoice.discountBps);
  const tax = taxOn(subtotal, discount, invoice.taxBps);
  return { subtotal, discount, tax, total: add(sub(subtotal, discount), tax) };
}
