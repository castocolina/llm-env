import { currencyInfo } from './currency.js';
import { invoiceTotals, lineTotal } from './invoice.js';
import { isZero } from './money.js';
import { describeRate } from './tax.js';

export function formatMoney(m) {
  const { digits, symbol } = currencyInfo(m.currency);
  const s = String(Math.abs(m.cents)).padStart(digits + 1, '0');
  const body = digits ? `${s.slice(0, -digits)}.${s.slice(-digits)}` : s;
  return `${m.cents < 0 ? '-' : ''}${symbol}${body}`;
}

export function formatInvoice(invoice) {
  const t = invoiceTotals(invoice);
  const rows = invoice.lines.map(
    (l) => `${l.description} x${l.qty} @ ${formatMoney(l.unit)} = ${formatMoney(lineTotal(l))}`,
  );
  const discount = isZero(t.discount) ? [] : [`Discount: -${formatMoney(t.discount)}`];
  const tax = invoice.taxBps ? `Tax (${describeRate(invoice.taxBps)}): ${formatMoney(t.tax)}` : `Tax: ${formatMoney(t.tax)}`;
  return [...rows, `Subtotal: ${formatMoney(t.subtotal)}`, ...discount, tax, `Total: ${formatMoney(t.total)}`].join('\n');
}
