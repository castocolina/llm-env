import { billableKg } from './billing.js';
import { zoneRates } from './zones.js';

export function parcelCents(parcel, zone) {
  const { baseCents, perKgCents } = zoneRates(zone);
  return Math.round(baseCents + perKgCents * billableKg(parcel));
}
