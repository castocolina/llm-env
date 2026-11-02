import { readFileSync } from 'node:fs';
import { parseLine } from './parse.js';
import { isError } from './levels.js';

export function errorReport(path) {
  const entries = readFileSync(path, 'utf8').split('\n').map(parseLine).filter(Boolean);
  const errors = entries.filter(isError);
  return { total: entries.length, errors: errors.length };
}
