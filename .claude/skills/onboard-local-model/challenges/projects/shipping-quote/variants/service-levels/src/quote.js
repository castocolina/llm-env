import { billableKg } from './billing.js';
import { parcelCents } from './rates.js';

export function quote(parcels, { zone }) {
  if (parcels.length === 0) throw new RangeError('nothing to quote');
  const lines = parcels.map((parcel) => ({ parcel, billableKg: billableKg(parcel), cents: parcelCents(parcel, zone) }));
  return { zone, lines, totalCents: lines.reduce((sum, line) => sum + line.cents, 0) };
}
