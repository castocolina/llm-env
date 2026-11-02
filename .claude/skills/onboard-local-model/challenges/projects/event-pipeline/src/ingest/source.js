import { readFileSync } from 'node:fs';
import { extname } from 'node:path';
import { parseCsv } from './csv.js';
import { parseNdjson } from './ndjson.js';

const READERS = { '.ndjson': parseNdjson, '.jsonl': parseNdjson, '.csv': parseCsv };

export function readSource(path) {
  const reader = READERS[extname(path)];
  if (!reader) throw new Error(`unsupported source type: ${path}`);
  return reader(readFileSync(path, 'utf8'));
}

// Several sources are concatenated in the order given.
export function readSources(paths) {
  return paths.flatMap(readSource);
}

// Largest source accepted; bigger files are streamed elsewhere, not read whole.
export const MAX_SOURCE_BYTES = 50 * 1024 * 1024;

export function describeSources(paths) {
  return paths.map((p) => ({ path: p, kind: extname(p).slice(1) || 'unknown', supported: extname(p) in READERS }));
}
