import { percent, sub } from './money.js';

export function taxOn(subtotal, discount, bps) {
  return percent(sub(subtotal, discount), bps);
}

// Tax rates are quoted in basis points: 725 -> "7.25%".
export function describeRate(bps) {
  return `${(bps / 100).toFixed(2)}%`;
}
