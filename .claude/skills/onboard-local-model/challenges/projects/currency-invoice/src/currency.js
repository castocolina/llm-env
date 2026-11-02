// Minor-unit digits and display symbol per currency code.
export const DEFAULT_CURRENCY = 'USD';
export const CURRENCIES = {
  USD: { digits: 2, symbol: '$' },
  EUR: { digits: 2, symbol: '€' },
  CHF: { digits: 2, symbol: 'CHF ' },
  JPY: { digits: 0, symbol: '¥' },
};

export function currencyInfo(code) {
  const info = CURRENCIES[code];
  if (!info) throw new RangeError(`unknown currency: ${code}`);
  return info;
}
