import { percent } from './money.js';

export function taxOn(subtotal, bps) {
  return percent(subtotal, bps);
}

// Tax rates are quoted in basis points: 725 -> "7.25%".
export function describeRate(bps) {
  return `${(bps / 100).toFixed(2)}%`;
}
