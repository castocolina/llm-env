import { padLeft, padRight, withThousands } from '../util/strings.js';

const section = (title, rows) => [title, ...rows.map(([k, v]) => `  ${padRight(k, 20)}${padLeft(v, 6)}`)];

export function formatReport(s) {
  const lines = [
    `events: ${s.events}`,
    `value sum: ${withThousands(s.valueSum)}`,
    `duplicates dropped: ${s.duplicates}`,
    ...section('by type', Object.entries(s.byType)),
    ...section('by user', Object.entries(s.byUser)),
    ...section('by day', Object.entries(s.days)),
    ...section('rejected', Object.entries(s.rejected)),
    ...section('hourly', s.hourly.map((b) => [b.hour, b.events])),
  ];
  if (s.busiest) lines.push(`busiest hour: ${s.busiest.hour} (${s.busiest.events})`);
  return lines.join('\n') + '\n';
}

export const formatJson = (s) => JSON.stringify(s, null, 2) + '\n';

// Fixed-width table: columns are [{ key, title, width }] and rows are plain objects.
export function formatTable(columns, rows) {
  const head = columns.map((c) => padRight(c.title, c.width)).join(' ');
  const rule = columns.map((c) => '-'.repeat(c.width)).join(' ');
  const body = rows.map((r) => columns.map((c) => padRight(r[c.key] ?? '', c.width)).join(' '));
  return [head, rule, ...body].join('\n') + '\n';
}

export function formatTop(top) {
  return formatTable(
    [{ key: 'user', title: 'user', width: 10 }, { key: 'events', title: 'events', width: 8 }],
    top,
  );
}

// "3 of 12 rows rejected (25.0%)", used in the report footer.
export function formatRejectionRate(rejectedCount, totalRows) {
  const pct = totalRows ? ((rejectedCount / totalRows) * 100).toFixed(1) : '0.0';
  return `${rejectedCount} of ${totalRows} rows rejected (${pct}%)`;
}
