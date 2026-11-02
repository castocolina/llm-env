import { currencyInfo } from './currency.js';
import { money } from './money.js';

const ppm = (rate) => BigInt(Math.round(rate * 1e6)); // rates are exact to 6 decimals
const pow10 = (n) => 10n ** BigInt(n);

// shortcut: non-negative amounts only, negative cents would need half-away-from-zero.
export function convert(m, to, rates) {
  if (m.currency === to) return m;
  const rf = rates?.[m.currency];
  const rt = rates?.[to];
  if (!rf || !rt) throw new RangeError(`no rate for ${m.currency} -> ${to}`);
  const num = BigInt(m.cents) * ppm(rt) * pow10(currencyInfo(to).digits);
  const den = ppm(rf) * pow10(currencyInfo(m.currency).digits);
  return money(Number((2n * num + den) / (2n * den)), to); // integer half-up, no float multiplication
}
