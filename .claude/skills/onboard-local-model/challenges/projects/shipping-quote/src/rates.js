import { billableKg } from './billing.js';
import { serviceInfo } from './services.js';
import { zoneRates } from './zones.js';

export function parcelCents(parcel, zone, service) {
  const { baseCents, perKgCents } = zoneRates(zone);
  const { factorPct } = serviceInfo(service);
  return Math.round(((baseCents + perKgCents * billableKg(parcel)) * factorPct) / 100);
}
