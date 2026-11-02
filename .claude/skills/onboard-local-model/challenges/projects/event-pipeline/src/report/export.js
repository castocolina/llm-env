import { csvField } from '../util/strings.js';

// CSV of the clean events, one row per event, header first.
export function eventsToCsv(events) {
  const rows = events.map((e) => [new Date(e.ts).toISOString(), e.user, e.type, e.value, e.session].map(csvField).join(','));
  return ['ts,user,type,value,session', ...rows].join('\n') + '\n';
}

// One line of JSON per event, for piping into other tools.
export const eventsToNdjson = (events) => events.map((e) => JSON.stringify({ ts: e.ts, user: e.user, type: e.type, value: e.value })).join('\n') + '\n';
