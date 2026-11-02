export const formatCents = (c) => `$${Math.floor(c / 100)}.${String(c % 100).padStart(2, '0')}`;

export function formatQuote(q) {
  const rows = q.lines.map((l) => `${l.parcel.weight} ${l.parcel.unit} -> ${l.billableKg} kg: ${formatCents(l.cents)}`);
  return [...rows, `Total: ${formatCents(q.totalCents)}`].join('\n');
}
