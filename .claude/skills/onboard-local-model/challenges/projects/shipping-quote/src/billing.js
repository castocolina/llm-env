import { roundUpTo } from './round.js';
import { toKg } from './units.js';
import { volumetricKg } from './volume.js';

export function billableKg(parcel) {
  const actualKg = toKg(parcel.weight, parcel.unit);
  return roundUpTo(Math.max(actualKg, volumetricKg(parcel)), 0.5);
}
