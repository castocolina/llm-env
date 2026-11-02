export const SERVICES = {
  standard: { label: 'Standard', factorPct: 100 },
  express: { label: 'Express', factorPct: 160 },
};

export function serviceInfo(name) {
  const info = SERVICES[name];
  if (!info) throw new RangeError(`unknown service: ${name}`);
  return info;
}
