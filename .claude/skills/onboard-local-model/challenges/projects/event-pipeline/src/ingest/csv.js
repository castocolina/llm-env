import { isBlank, splitCsvLine, stripBom, trimLower } from '../util/strings.js';
import { parseTs } from './timestamps.js';

const ALIASES = { timestamp: 'ts', time: 'ts', kind: 'type', event: 'type', amount: 'value' };

// First non-blank line is the header; columns are matched by name (aliases allowed) so their order does not matter.
// A row with the wrong number of fields becomes { bad: true } for the validation stage to count.
export function parseCsv(text) {
  const lines = stripBom(text).split('\n').map((l) => l.replace(/\r$/, '')).filter((l) => !isBlank(l));
  if (lines.length === 0) return [];
  const header = splitCsvLine(lines[0]).map((h) => ALIASES[trimLower(h)] ?? trimLower(h));
  const [iTs, iUser, iType, iValue] = ['ts', 'user', 'type', 'value'].map((name) => header.indexOf(name));
  if ([iTs, iUser, iType, iValue].includes(-1)) throw new Error('csv header must name ts, user, type and value');
  return lines.slice(1).map((line) => {
    const f = splitCsvLine(line);
    if (f.length !== header.length) return { bad: true, raw: line };
    return { ts: parseTs(f[iTs]), user: f[iUser], type: f[iType], value: f[iValue] };
  });
}
