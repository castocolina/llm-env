// A money value is an integer count of minor units (cents).
export function money(cents) {
  if (!Number.isInteger(cents)) throw new TypeError('cents must be an integer');
  return { cents };
}

export const add = (a, b) => money(a.cents + b.cents);
export const sub = (a, b) => money(a.cents - b.cents);
export const sum = (list) => list.reduce(add, money(0));
export const times = (m, qty) => money(m.cents * qty);
export const isZero = (m) => m.cents === 0;

// `bps` basis points of an amount (725 = 7.25 %), rounded half up to the minor unit.
export function percent(m, bps) {
  if (m.cents < 0 || bps < 0) throw new RangeError('percent expects non-negative values');
  return money(Math.round((m.cents * bps) / 10000));
}
