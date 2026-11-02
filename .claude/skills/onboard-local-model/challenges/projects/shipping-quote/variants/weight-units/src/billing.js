import { roundUpTo } from './round.js';
import { volumetricKg } from './volume.js';

export function billableKg(parcel) {
  return roundUpTo(Math.max(parcel.weightKg, volumetricKg(parcel)), 0.5);
}
