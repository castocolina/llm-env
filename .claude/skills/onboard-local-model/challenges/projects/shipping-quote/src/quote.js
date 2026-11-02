import { billableKg } from './billing.js';
import { parcelCents } from './rates.js';

export function quote(parcels, { zone, service = 'standard' }) {
  if (parcels.length === 0) throw new RangeError('nothing to quote');
  const lines = parcels.map((parcel) => ({ parcel, billableKg: billableKg(parcel), cents: parcelCents(parcel, zone, service) }));
  return { zone, service, lines, totalCents: lines.reduce((sum, line) => sum + line.cents, 0) };
}
