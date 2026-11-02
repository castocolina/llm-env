import { isBlank, stripBom } from '../util/strings.js';
import { parseTs } from './timestamps.js';

// One JSON object per line; blank lines are skipped, unparsable lines become { bad: true } rows
// so the validation stage can count them instead of the reader hiding them.
// `timestamp` is accepted as an alias of `ts`, `kind` as an alias of `type`.
export function parseNdjson(text) {
  const rows = [];
  for (const line of stripBom(text).split('\n')) {
    if (isBlank(line)) continue;
    try {
      const o = JSON.parse(line);
      if (o === null || typeof o !== 'object' || Array.isArray(o)) throw new TypeError('not an object');
      rows.push({ ts: parseTs(o.ts ?? o.timestamp), user: o.user, type: o.type ?? o.kind, value: o.value });
    } catch {
      rows.push({ bad: true, raw: line });
    }
  }
  return rows;
}
