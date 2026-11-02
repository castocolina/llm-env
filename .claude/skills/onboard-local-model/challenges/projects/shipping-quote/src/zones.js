export const ZONES = {
  A: { baseCents: 500, perKgCents: 120 },
  B: { baseCents: 800, perKgCents: 180 },
  C: { baseCents: 1500, perKgCents: 300 },
};

export function zoneRates(code) {
  const rates = ZONES[code];
  if (!rates) throw new RangeError(`unknown zone: ${code}`);
  return rates;
}
