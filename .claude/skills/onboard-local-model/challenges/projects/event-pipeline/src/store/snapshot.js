import { EventStore } from './memory.js';

const VERSION = 1;

// Cheap order-sensitive checksum (FNV-1a, 32 bit) so a truncated snapshot is noticed on restore.
export function checksum(text) {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193) >>> 0;
  return h.toString(16).padStart(8, '0');
}

// Stable JSON form of a store: events in insertion order, fields in a fixed order.
export function toSnapshot(store) {
  const events = store.all().map(({ ts, user, type, value }) => ({ ts, user, type, value }));
  const body = JSON.stringify(events);
  return JSON.stringify({ version: VERSION, checksum: checksum(body), events: body });
}

export function fromSnapshot(text) {
  const snap = JSON.parse(text);
  if (snap.version !== VERSION) throw new Error(`unsupported snapshot version: ${snap.version}`);
  if (checksum(snap.events) !== snap.checksum) throw new Error('snapshot checksum mismatch');
  return new EventStore().addAll(JSON.parse(snap.events));
}
