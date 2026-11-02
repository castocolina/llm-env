import { invoiceTotals, lineTotal } from './invoice.js';
import { isZero } from './money.js';
import { describeRate } from './tax.js';

export function formatMoney(m) {
  const s = String(Math.abs(m.cents)).padStart(3, '0');
  return `${m.cents < 0 ? '-' : ''}$${s.slice(0, -2)}.${s.slice(-2)}`;
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
