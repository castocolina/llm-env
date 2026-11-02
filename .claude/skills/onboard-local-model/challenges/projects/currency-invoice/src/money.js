import { DEFAULT_CURRENCY, currencyInfo } from './currency.js';

// A money value is an integer count of minor units of its currency.
export function money(cents, currency = DEFAULT_CURRENCY) {
  if (!Number.isInteger(cents)) throw new TypeError('cents must be an integer');
  currencyInfo(currency);
  return { cents, currency };
}

function same(a, b) {
  if (a.currency !== b.currency) throw new RangeError(`currency mismatch: ${a.currency} vs ${b.currency}`);
  return a.currency;
}

export const add = (a, b) => money(a.cents + b.cents, same(a, b));
export const sub = (a, b) => money(a.cents - b.cents, same(a, b));
export const sum = (list, currency = DEFAULT_CURRENCY) => list.reduce(add, money(0, currency));
export const times = (m, qty) => money(m.cents * qty, m.currency);
export const isZero = (m) => m.cents === 0;

// `bps` basis points of an amount (725 = 7.25 %), rounded half up to the minor unit.
export function percent(m, bps) {
  if (m.cents < 0 || bps < 0) throw new RangeError('percent expects non-negative values');
  return money(Math.round((m.cents * bps) / 10000), m.currency);
}
