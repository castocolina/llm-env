import { parseArgs } from './config.js';
import { readSources } from './ingest/source.js';
import { formatJson, formatReport } from './report/format.js';
import { buildSummary } from './report/summary.js';
import { EventStore } from './store/memory.js';
import { runTransforms } from './transform/pipeline.js';

// Reads the sources, runs every stage and returns the store, the summary object and its text form.
export function runPipeline(paths, options = {}) {
  const { events, rejected, duplicates } = runTransforms(readSources(paths), options);
  const store = new EventStore().addAll(events);
  const summary = buildSummary(store, { rejected, duplicates });
  return { store, summary, text: options.format === 'json' ? formatJson(summary) : formatReport(summary) };
}

// Command-line entry: returns the text to print.
export function main(argv) {
  const { paths, options } = parseArgs(argv);
  if (paths.length === 0) throw new Error('usage: run [--gap 30m] [--max-value N] [--json] FILE...');
  return runPipeline(paths, options).text;
}
