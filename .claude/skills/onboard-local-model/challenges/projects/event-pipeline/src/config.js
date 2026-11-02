import { DEFAULT_MAX_VALUE } from './transform/validate.js';
import { DEFAULT_GAP_MS } from './transform/sessions.js';
import { parseDuration } from './util/time.js';

export const DEFAULTS = { maxValue: DEFAULT_MAX_VALUE, gapMs: DEFAULT_GAP_MS, format: 'text' };

// argv like ['--gap', '15m', '--max-value', '5000', '--json', 'a.csv', 'b.ndjson'] -> { paths, options }
export function parseArgs(argv) {
  const options = { ...DEFAULTS };
  const paths = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--gap') options.gapMs = parseDuration(argv[++i]);
    else if (a === '--max-value') options.maxValue = Number(argv[++i]);
    else if (a === '--json') options.format = 'json';
    else if (a.startsWith('--')) throw new Error(`unknown option: ${a}`);
    else paths.push(a);
  }
  return { paths, options };
}
