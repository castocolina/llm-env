export const KG_PER_UNIT = { kg: 1, g: 0.001, lb: 0.45359237 };

export function toKg(weight, unit) {
  const factor = KG_PER_UNIT[unit];
  if (factor === undefined) throw new RangeError(`unknown unit: ${unit}`);
  return weight * factor;
}
