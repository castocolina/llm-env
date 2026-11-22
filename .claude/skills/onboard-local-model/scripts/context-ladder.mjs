#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { existsSync, realpathSync, readFileSync, writeFileSync, chmodSync } from 'node:fs';
import { execFileSync, execFile } from 'node:child_process';
import { resolve, relative, dirname, join, isAbsolute, sep } from 'node:path';
import { pathToFileURL } from 'node:url';

export const DEFAULT_CHARS_PER_TOKEN = 3.4;
const defaults = { via: 'omniroute', model: 'my-local-worker', alias: '', fills: [0, 25, 50, 75, 90], needles: 3, runs: 1, seed: 1, maxTokens: 256, think: 'off', out: '', maxSeconds: 1800, timeout: 600, dryRun: false, idleCheck: false, calibrate: false, charsPerToken: DEFAULT_CHARS_PER_TOKEN, minTg: 20, ppTps: 580, tgTps: 70 };
const valueFlags = new Map(Object.entries({ '--via': 'via', '--model': 'model', '--alias': 'alias', '--fills': 'fills', '--needles': 'needles', '--runs': 'runs', '--seed': 'seed', '--max-tokens': 'maxTokens', '--think': 'think', '--out': 'out', '--max-seconds': 'maxSeconds', '--timeout': 'timeout', '--ctx': 'ctx', '--pp-tps': 'ppTps', '--tg-tps': 'tgTps', '--min-tg': 'minTg', '--chars-per-token': 'charsPerToken' }));
const fail = (flag, message) => { throw new Error(`${flag}: ${message}`); };

export function parseArgs(argv) {
  const args = { ...defaults };
  const seen = new Set();
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === '--dry-run' || flag === '--idle-check' || flag === '--keep-answers' || flag === '--calibrate') {
      if (seen.has(flag)) fail(flag, 'duplicate flag');
      seen.add(flag);
      args[flag === '--dry-run' ? 'dryRun' : flag === '--idle-check' ? 'idleCheck' : flag === '--calibrate' ? 'calibrate' : 'keepAnswers'] = true;
      continue;
    }
    const key = valueFlags.get(flag);
    if (!key) fail(flag || 'argument', 'unknown flag');
    if (seen.has(flag)) fail(flag, 'duplicate flag');
    seen.add(flag);
    const value = argv[++i];
    if (value === undefined || value.startsWith('--')) fail(flag, 'value required');
    args[key] = value;
  }
  for (const key of ['needles', 'runs', 'seed', 'maxTokens', 'maxSeconds', 'timeout', 'minTg', 'ppTps', 'tgTps', 'charsPerToken']) {
    if (args[key] === defaults[key] && !seen.has(({ maxTokens: '--max-tokens', maxSeconds: '--max-seconds', minTg: '--min-tg', ppTps: '--pp-tps', tgTps: '--tg-tps', charsPerToken: '--chars-per-token' })[key] ?? `--${key}`)) continue;
    const flag = ({ maxTokens: '--max-tokens', maxSeconds: '--max-seconds', minTg: '--min-tg', ppTps: '--pp-tps', tgTps: '--tg-tps', charsPerToken: '--chars-per-token' })[key] ?? `--${key}`;
    if (!(key === 'seed' ? /^-?\d+$/.test(String(args[key])) : /^\d+(?:\.\d+)?$/.test(String(args[key])))) fail(flag, 'must be numeric');
    args[key] = Number(args[key]);
  }
  for (const [key, min, max, flag] of [['needles', 1, 8, '--needles'], ['runs', 1, 9, '--runs'], ['seed', -2147483648, 2147483647, '--seed'], ['maxTokens', 16, 4096, '--max-tokens'], ['maxSeconds', 60, 14400, '--max-seconds'], ['timeout', 10, 3600, '--timeout'], ['minTg', Number.MIN_VALUE, Infinity, '--min-tg']]) {
    if (!Number.isInteger(args[key]) || args[key] < min || args[key] > max) fail(flag, `must be between ${min} and ${max}`);
  }
  for (const key of ['ppTps', 'tgTps']) if (!Number.isFinite(args[key]) || args[key] <= 0) fail(key === 'ppTps' ? '--pp-tps' : '--tg-tps', 'must be positive');
  if (!Number.isFinite(args.charsPerToken) || args.charsPerToken < 1.5 || args.charsPerToken > 8) fail('--chars-per-token', 'must be between 1.5 and 8');
  if (args.ctx !== undefined) {
    if (!/^\d+$/.test(String(args.ctx)) || !Number.isInteger(Number(args.ctx)) || Number(args.ctx) < 1024 || Number(args.ctx) > 2097152) fail('--ctx', 'must be an integer between 1024 and 2097152');
    args.ctx = Number(args.ctx);
  }
  if (!Number.isFinite(Number(args.charsPerToken)) || Number(args.charsPerToken) < 1.5 || Number(args.charsPerToken) > 8) fail('--chars-per-token', 'must be between 1.5 and 8');
  args.charsPerToken = Number(args.charsPerToken);
  args.modelExplicit = seen.has('--model');
  if (!['omniroute', 'direct'].includes(args.via)) fail('--via', 'must be omniroute or direct');
  if (!['on', 'off'].includes(args.think)) fail('--think', 'must be on or off');
  if (!args.out) fail('--out', 'is required');
  args.keepAnswers ??= false;
  const fills = String(args.fills).split(',');
  if (fills.length > 8 || fills.some(x => !/^\d+$/.test(x) || Number(x) > 100) || fills.some((x, i) => i && Number(x) <= Number(fills[i - 1]))) fail('--fills', 'must be up to 8 strictly increasing integer percentages from 0 to 100');
  args.fills = fills.map(Number);
  return args;
}

export function validateArgs(args, { cwd = process.cwd() } = {}) {
  if (!args.out) fail('--out', 'is required');
  const root = resolve(cwd, 'tmp');
  const out = resolve(cwd, args.out);
  const rel = relative(root, out);
  if (!rel || rel === '..' || rel.startsWith(`..${sep}`) || isAbsolute(rel)) fail('--out', 'must resolve to a new path under tmp/');
  if (existsSync(out)) fail('--out', 'already exists');
  let ancestor = dirname(out);
  while (!existsSync(ancestor)) {
    const next = dirname(ancestor);
    if (next === ancestor) fail('--out', 'has no existing parent under tmp/');
    ancestor = next;
  }
  let realCwd, realAncestor;
  try { realCwd = realpathSync(cwd); realAncestor = realpathSync(ancestor); }
  catch { fail('--out', 'parent path cannot be resolved'); }
  const realRoot = existsSync(root) ? realpathSync(root) : join(realCwd, 'tmp');
  const ancestorRel = relative(realRoot, realAncestor);
  if ((existsSync(root) && realRoot !== join(realCwd, 'tmp')) || (realAncestor !== ancestor && ancestor !== realCwd) || (existsSync(root) && (ancestorRel === '..' || ancestorRel.startsWith(`..${sep}`) || isAbsolute(ancestorRel)))) fail('--out', 'parent path contains a symlink or escapes tmp/');
  if (existsSync(root) && realRoot !== join(realCwd, 'tmp')) fail('--out', 'tmp parent path contains a symlink');
  const warnings = [];
  if (args.via === 'omniroute' && args.alias) warnings.push('warning: --alias ignored with --via omniroute');
  if (args.via === 'direct' && args.modelExplicit) warnings.push('warning: --model ignored with --via direct');
  return warnings;
}

function needleDepths(count) {
  if (count === 1) return [50];
  if (count === 2) return [25, 75];
  if (count === 3) return [10, 50, 90];
  return Array.from({ length: count }, (_, i) => Math.round(i * 100 / (count - 1)));
}

export function planCells(args, ctx) {
  const actualCtx = ctx ?? args.ctx ?? 131072;
  const usable = Math.max(0, actualCtx - (args.maxTokens ?? 256) - 256);
  return Array.from({ length: args.runs }, (_, runIndex) => args.fills.map(fillPct => {
    const target = Math.floor(actualCtx * fillPct / 100);
    return { fillPct, run: runIndex + 1, seed: (args.seed ?? 1) + runIndex, targetTokens: Math.min(target, usable), clamped: target > usable && usable > 0, skipped: target > 0 && usable === 0, needleDepths: needleDepths(args.needles) };
  })).flat();
}

export function estimate(cells, hints) {
  return cells.reduce((total, cell) => total + cell.targetTokens / hints.ppTps + hints.maxTokens / hints.tgTps + 2, 0);
}

export function formatPlan(args, cells, estimatedSeconds, { ctxAssumed = false, ctx = args.ctx ?? 131072 } = {}) {
  const rows = cells.map(cell => `${cell.fillPct}% | ${cell.targetTokens} | ${cell.needleDepths.join(',')} | ${(cell.targetTokens / args.ppTps + args.maxTokens / args.tgTps + 2).toFixed(1)} s${cell.clamped ? ' | clamped' : ''}${cell.targetTokens / args.ppTps > args.timeout ? ' | warning: prefill exceeds per-request timeout' : ''}`);
  const bound = `context-ladder: ${cells.length} cells, upper bound ${args.timeout * cells.length} s (timeout x cells)`;
  return `${ctxAssumed ? 'ctx assumed (131072)\n' : ''}fill % | target tokens | needle depths % | estimated seconds | % of ctx\n${rows.map((row, i) => `${row} | ${(cells[i].targetTokens / ctx * 100).toFixed(1)}%`).join('\n')}\n${bound}\nestimated total ${estimatedSeconds.toFixed(1)} s${estimatedSeconds > args.maxSeconds ? '\nwarning: estimate exceeds --max-seconds' : ''}`;
}

const usageError = message => Object.assign(new Error(message), { exitCode: 2 });
export const redact = (text, secret) => {
  let safe = String(text);
  if (secret) for (const value of new Set([secret, encodeURIComponent(secret), Buffer.from(secret).toString('base64')])) safe = safe.split(value).join('[REDACTED]');
  return safe;
};
const target = (baseUrl, apiKey) => { const result = { baseUrl }; Object.defineProperty(result, 'secret', { value: apiKey, enumerable: false }); return result; };

export function loadOmniroute({ home = process.env.HOME, readFile = readFileSync, realpath = realpathSync, env = process.env } = {}) {
  try {
    const provider = JSON.parse(readFile(join(home, '.pi/agent/models.json'), 'utf8')).providers?.['router-env'];
    if (!provider?.baseUrl || !provider.apiKey) throw usageError('run make setup-local-llm-agents');
    let apiKey = provider.apiKey;
    if (apiKey.startsWith('!')) {
      const match = apiKey.match(/^!cat (?:"([^"]+)"|'([^']+)'|(\S+))$/);
      const file = match?.[1] ?? match?.[2] ?? match?.[3];
      if (!file || !isAbsolute(file) || file.split(sep).includes('..')) throw usageError("unsupported apiKey command (only '!cat <file under HOME>' is supported)");
      try {
        const homePath = realpath(home), keyPath = realpath(file);
        const rel = relative(homePath, keyPath);
        if (!rel || rel === '..' || rel.startsWith(`..${sep}`) || isAbsolute(rel)) throw new Error('path outside HOME');
        apiKey = readFile(keyPath, 'utf8').trimEnd();
      } catch { throw usageError(`cannot read apiKey file under HOME: ${file}`); }
    } else if (/^[A-Z][A-Z0-9_]*$/.test(apiKey) && env[apiKey] !== undefined) apiKey = env[apiKey];
    if (!apiKey) throw usageError('apiKey is empty');
    return target(provider.baseUrl, apiKey);
  } catch (error) { if (error.exitCode === 2) throw error; throw usageError('run make setup-local-llm-agents'); }
}
export function loadDirect({ home = process.env.HOME, alias, exec = execFileSync } = {}) {
  const file = join(home, '.config/llm-env/models.yml');
  const yq = expr => exec('yq', ['-r', expr, file], { encoding: 'utf8' }).trim();
  try {
    const key = yq('.server.api_key'); let port = yq('.llm_server.port');
    if (!port || port === 'null') port = '8000';
    const selected = alias || yq('.models[] | select(.enabled == true) | .alias');
    if (!key || !selected || selected === 'null') throw usageError('missing API key or enabled alias');
    const ctx = yq(`.models[] | select(.alias == ${JSON.stringify(selected)}) | .ctx_size`);
    const result = target(`http://127.0.0.1:${port}/v1`, key);
    Object.defineProperty(result, 'ctx', { value: Number(ctx) || undefined, enumerable: false });
    Object.defineProperty(result, 'alias', { value: selected, enumerable: false }); return result;
  } catch (error) { if (error.exitCode === 2) throw error; throw usageError(redact(error.code === 'ENOENT' ? 'yq is required' : 'cannot read direct target configuration', error.secret)); }
}
const words = ['cache','route','worker','queue','index','state','token','record','value','module'];
function rng(seed) { let n = Number(seed) >>> 0; return () => { n = (n * 1664525 + 1013904223) >>> 0; return n / 4294967296; }; }
export function makeFiller(seed, chars) {
  const random = rng(seed), buffer = Buffer.allocUnsafe(chars); let offset = 0;
  while (offset < chars) { const name = words[Math.floor(random() * words.length)]; const chunk = Buffer.from(`// ${name} module keeps state\nfunction ${name}Worker(value) {\n  return value + ${Math.floor(random() * 97)};\n}\n`); offset += chunk.copy(buffer, offset, 0, Math.min(chunk.length, chars - offset)); }
  return buffer.toString();
}
export function makeNeedles(seed, k) {
  const random = rng(seed), used = new Set();
  return Array.from({ length: k }, (_, i) => { const module = `zx-${Math.floor(random() * 9000 + 1000)}-${i}`; let value;
    do { value = `${words[Math.floor(random() * words.length)]}-${Math.floor(random() * 9000 + 1000)}`; } while (used.has(value));
    used.add(value); return `The deploy token for module ${module} is ${value}.`; });
}
export function plantNeedles(filler, needles, depths) { const lines = filler.split('\n'); for (let i = 0; i < needles.length; i++) lines.splice(Math.floor((depths[i] ?? 50) / 100 * lines.length), 0, needles[i]); return lines.join('\n'); }
export function makeCellPrompt({ seed, run = 1, fillPct = 0, needles: count = 3, targetTokens = 0, fillerChars, charsPerToken = DEFAULT_CHARS_PER_TOKEN }) {
  const promptSeed = seed + run - 1;
  const filler = makeFiller(promptSeed, fillerChars ?? Math.max(0, Math.round(targetTokens * charsPerToken)));
  const facts = makeNeedles(promptSeed, count);
  const question = `Recall all planted module facts for seed ${promptSeed} at ${fillPct}% fill (target ${targetTokens} tokens). Answer with one line per module id, the id then a colon then the full value exactly as written in the text, for example: zx-0000-x: sample-0000`;
  const messages = buildMessages({ filler: plantNeedles(filler, facts, needleDepths(count)), needles: facts, question });
  const text = messages[1].content;
  return { filler, needles: facts, messages, text, sha256: createHash('sha256').update(text).digest('hex').slice(0, 12) };
}
export function buildMessages({ filler, needles, question = '' }) {
  const ids = needles.map(text => text.match(/module ([\w-]+)/)[1]);
  return [{ role: 'system', content: 'You are a precise recall assistant.' }, { role: 'user', content: `${filler}\n${question}\nFor each module ID, return its exact value on the same line. Include both the module ID and value, one pair per line; do not return values alone. IDs: ${ids.join(', ')}` }];
}
export async function calibrateDirect({ fetchFn = fetch, baseUrl, alias, secret, text = '' }) {
  try {
    const response = await fetchFn(`${baseUrl.replace(/\/v1\/?$/, '')}/tokenize`, { method: 'POST', headers: { Authorization: `Bearer ${secret}`, 'content-type': 'application/json' }, body: JSON.stringify({ model: alias, content: text }) });
    if (response.ok === false) throw new Error(redact(`tokenize HTTP ${response.status}`, secret));
    const data = await response.json(), tokens = Array.isArray(data.tokens) ? data.tokens.length : data.tokens;
    return { chars: text.length, text, tokens, method: 'tokenize', requests: 1 };
  } catch (error) { throw new Error(redact(error.message, secret)); }
}
export async function calibrateOmniroute() {
  return { chars: 0, method: 'usage', requests: 0, charsPerToken: DEFAULT_CHARS_PER_TOKEN };
}
export async function loadCtxDirect({ fetchFn = fetch, baseUrl, alias, secret }) {
  try {
    const response = await fetchFn(`${baseUrl.replace(/\/v1\/?$/, '')}/props?model=${encodeURIComponent(alias)}`, { headers: { Authorization: `Bearer ${secret}` } });
    if (response.ok === false) return undefined;
    return (await response.json()).default_generation_settings.n_ctx;
  } catch (error) { throw new Error(redact(error.message, secret)); }
}

export function scoreRecall(answer, needles) {
  const lines = String(answer).toLocaleLowerCase().split(/\r?\n/);
  const matches = needles.map(needle => {
    const match = needle.match(/module ([\w-]+) is (.+)\.$/i);
    const normalize = value => value.toLocaleLowerCase().replace(/\s+/g, ' ').trim();
    return match && lines.some(line => normalize(line).includes(normalize(match[1])) && normalize(line).includes(normalize(match[2]))) ? { module: match[1], value: match[2] } : null;
  });
  const hits = matches.filter(Boolean).length;
  const core = matches.filter((match, index) => {
    const full = needles[index].match(/module ([\w-]+) is (.+)\.$/i);
    const number = full?.[2].match(/\d+/)?.[0];
    return number && lines.some(line => {
      const id = full[1].toLocaleLowerCase();
      const at = line.indexOf(id);
      return at >= 0 && !/[\w-]/.test(line[at - 1] ?? '') && !/[\w-]/.test(line[at + id.length] ?? '') && line.slice(at + id.length).match(/\d+/)?.[0] === number;
    });
  }).length;
  return { hits, correct: hits === needles.length, hits_core: core, correct_core: core === needles.length };
}

export function classifyAnswer(answer, needles, filler = '') {
  const text = String(answer).trim();
  const score = scoreRecall(text, needles);
  const idsPresent = needles.filter(needle => text.toLowerCase().includes(needle.match(/module ([\w-]+)/i)[1].toLowerCase())).length;
  const fillerNumbers = new Set([...String(filler).matchAll(/\d+/g)].map(match => match[0]));
  const answerValues = text.split(/\r?\n/).map(line => line.split(/:\s*/).slice(1).join(': ')).filter(Boolean).flatMap(value => value.match(/\d+/g) ?? []);
  const answer_kind = !text ? 'empty' : score.correct ? 'exact' : score.correct_core ? 'format_drift' : score.hits > 0 ? 'partial' : idsPresent === needles.length ? 'wrong_values' : idsPresent === 0 ? 'no_ids' : 'partial';
  return { ...score, answer_kind, answer_values_in_filler: answerValues.filter(number => fillerNumbers.has(number)).length };
}

export async function runCell({ fetchFn = fetch, target, cell, args, now = Date.now, signal }) {
  const started = now(), controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), args.timeout * 1000);
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  let answer = '', contentDeltas = 0, reasoningDeltas = 0, firstContent;
  const row = { fill_pct: cell.fillPct, run: cell.run, seed: cell.seed ?? args.seed + cell.run - 1, target_tokens: cell.targetTokens, prompt_sha256_prefix: cell.messages?.[1]?.content ? createHash('sha256').update(cell.messages[1].content).digest('hex').slice(0, 12) : null, via: args.via, model: args.via === 'direct' ? args.alias : args.model, think: args.think, status: 'bad_stream' };
  try {
    const headers = { 'content-type': 'application/json' };
    if (target.secret) headers.Authorization = `Bearer ${target.secret}`;
    const direct = args.via === 'direct';
    const body = { model: direct ? args.alias : args.model, messages: cell.messages, stream: true, stream_options: { include_usage: true }, max_tokens: args.maxTokens };
    body.cache_prompt = false;
    if (args.think === 'off') body.chat_template_kwargs = { enable_thinking: false };
    const response = await fetchFn(`${target.baseUrl}/chat/completions`, { method: 'POST', headers, body: JSON.stringify(body), signal: controller.signal });
    if (!response.ok) {
      let errorType = 'non_json', errorMessage = '';
      let responseText = '';
      try {
        responseText = typeof response.text === 'function' ? await response.text() : '';
        const parsed = JSON.parse(responseText);
        errorType = String(parsed?.error?.type ?? 'unknown').slice(0, 160);
        errorMessage = String(parsed?.error?.message ?? '').slice(0, 160);
      } catch {
        errorMessage = responseText.slice(0, 80);
      }
      return { ...row, status: `http_${response.status}`, ...(typeof response.text === 'function' ? { error_type: redact(errorType, target.secret), error_message: redact(errorMessage, target.secret) } : {}), total_s: (now() - started) / 1000 };
    }
    if (!response.body) throw new Error('missing response stream');
    const decoder = new TextDecoder(); let buffer = '', usage = {};
    for await (const chunk of response.body) {
      buffer += decoder.decode(chunk, { stream: true }).replace(/\r\n/g, '\n');
      let split;
      while ((split = buffer.indexOf('\n\n')) >= 0) {
        const event = buffer.slice(0, split); buffer = buffer.slice(split + 2);
        const data = event.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n');
        if (!data || data === '[DONE]') continue;
        const item = JSON.parse(data); if (item.usage) usage = item.usage;
        for (const choice of item.choices ?? []) {
          const delta = choice.delta ?? {};
          if (delta.reasoning_content) reasoningDeltas++;
          if (typeof delta.content === 'string' && delta.content.length) { if (firstContent === undefined) firstContent = now(); contentDeltas++; answer += delta.content; }
        }
      }
    }
    if (buffer.trim().startsWith('data:')) {
      const data = buffer.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n');
      if (data && data !== '[DONE]') {
        const item = JSON.parse(data); if (item.usage) usage = item.usage;
        for (const choice of item.choices ?? []) {
          const delta = choice.delta ?? {};
          if (delta.reasoning_content) reasoningDeltas++;
          if (typeof delta.content === 'string' && delta.content.length) { if (firstContent === undefined) firstContent = now(); contentDeltas++; answer += delta.content; }
        }
      }
    }
    if (firstContent === undefined) throw new Error('stream ended without content');
    const ended = now(), ttft = firstContent - started, completion = usage.completion_tokens ?? contentDeltas, prompt = usage.prompt_tokens;
    const measuredPrompt = cell.measuredPromptTokens ?? prompt;
    const sizeError = Number.isFinite(measuredPrompt) && row.target_tokens > 0 ? (measuredPrompt - row.target_tokens) / row.target_tokens * 100 : null;
    const prefill = prompt ? (ttft > 0 ? prompt / (ttft / 1000) : Infinity) : null;
    const { answer: scoredAnswer, ...safeRow } = { ...row, status: 'ok', prompt_tokens: measuredPrompt, completion_tokens: completion, size_error_pct: sizeError, size_off: Number.isFinite(sizeError) && Math.abs(sizeError) > 15, ttft_ms: ttft, prefill_tps: prefill, ttft_suspect: prefill > 20000, decode_tps: completion / Math.max((ended - firstContent) / 1000, 0.001), total_s: (ended - started) / 1000, reasoning_tokens_hint: reasoningDeltas, answer };
    Object.defineProperty(safeRow, 'answer', { value: scoredAnswer, enumerable: false });
    return safeRow;
  } catch (error) {
    const interrupted = signal?.aborted;
    const timedOut = !interrupted && (controller.signal.aborted || error.name === 'AbortError');
    return { ...row, status: interrupted ? 'interrupted' : timedOut ? 'timeout' : error instanceof TypeError ? 'network' : 'bad_stream', error: redact(error.message, target.secret).slice(0, 300), total_s: (now() - started) / 1000 };
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
}

async function checkIdle() {
  const samples = [];
  let reason;
  try {
    const cgroup = await new Promise((resolve, reject) => execFile('podman', ['inspect', '--format', '{{.State.CgroupPath}}', 'llm-server'], { encoding: 'utf8' }, (error, stdout) => error ? reject(error) : resolve(stdout.trim())));
    const path = join(process.env.CONTEXT_LADDER_CGROUP_ROOT ?? '/sys/fs/cgroup', cgroup.replace(/^\/+/, ''), 'cpu.stat');
    const usage = () => {
      const value = readFileSync(path, 'utf8').match(/^usage_usec\s+(\d+)$/m)?.[1];
      if (!value) throw new Error('unreadable CPU counter');
      return Number(value);
    };
    let previous = usage(), previousTime = Date.now();
    for (let i = 0; i < 2; i++) {
      await new Promise(resolve => setTimeout(resolve, Number(process.env.CONTEXT_LADDER_IDLE_INTERVAL_MS ?? 5000)));
      const current = usage(), now = Date.now();
      samples.push((current - previous) / ((now - previousTime) * 10000));
      previous = current; previousTime = now;
    }
  } catch (error) { reason = error.code === 'ENOENT' ? (error.syscall === 'spawn' ? 'podman unavailable' : 'cgroup CPU counter unavailable') : 'cgroup CPU counter unavailable'; }
  return { idle_cpu_pct: samples, idle: reason ? null : samples.every(value => value < 20), ...(reason ? { idle_reason: reason } : {}) };
}

export async function runLadder({ out, cells, args, target, needles, fetchFn = fetch, now = Date.now, signal }) {
  const { mkdirSync, openSync, writeSync, closeSync, chmodSync } = await import('node:fs');
  mkdirSync(out, { mode: 0o700, recursive: true }); chmodSync(out, 0o700);
  const path = join(out, 'results.jsonl'), fd = openSync(path, 'wx', 0o600); chmodSync(path, 0o600);
  const answersFd = args.keepAnswers ? openSync(join(out, 'answers.jsonl'), 'wx', 0o600) : null;
  if (answersFd !== null) chmodSync(join(out, 'answers.jsonl'), 0o600);
  const start = now(), rows = []; let stopped = false, stop_reason = null, timeouts = 0;
  const finish = async () => {
    const observedRatios = rows.map(row => row.prompt_tokens > 0 && row.filler_chars > 0 ? row.filler_chars / row.prompt_tokens : null).filter(Number.isFinite).sort((a,b) => a-b);
    const charsPerTokenObserved = observedRatios.length ? observedRatios.length % 2 ? observedRatios[(observedRatios.length - 1) / 2] : (observedRatios[observedRatios.length / 2 - 1] + observedRatios[observedRatios.length / 2]) / 2 : null;
    const fills = [...new Set(rows.map(row => row.fill_pct))].map(fill => {
      const group = rows.filter(row => row.fill_pct === fill), speedGroup = group.filter(row => !row.ttft_suspect), median = (key, source = group) => { const values = source.map(x => x[key]).filter(Number.isFinite).sort((a,b) => a-b); return values.length ? (values.length % 2 ? (values[(values.length - 1) / 2]) : (values[values.length / 2 - 1] + values[values.length / 2]) / 2) : null; };
      const rounded = (value, digits = 1) => value === null ? null : Math.round(value * 10 ** digits) / 10 ** digits;
      const strict = group.filter(x => x.correct).length, core = group.filter(x => x.correct_core).length;
      return { fill_pct: fill, n: group.length, runs_correct: `${strict}/${args.runs}`, runs_correct_core: `${core}/${args.runs}`, hits_by_run: group.map(row => ({ run: row.run, seed: row.seed, hits: row.hits, hits_core: row.hits_core, correct: row.correct, correct_core: row.correct_core })), ttft_ms: rounded(median('ttft_ms', speedGroup), 0), prefill_tps: rounded(median('prefill_tps', speedGroup)), decode_tps: rounded(median('decode_tps', speedGroup)), ttft_suspect_cells: group.filter(row => row.ttft_suspect).length, prompt_tokens: median('prompt_tokens'), ctx_pct: median('prompt_tokens') === null || !args.ctx ? null : median('prompt_tokens') / args.ctx * 100, recall_rate: strict / args.runs, recall_core: core / args.runs };
    });
    const drops = fills.filter(x => x.recall_rate < 1);
    const ceiling = fills.filter(x => x.decode_tps !== null && x.decode_tps >= args.minTg && x.runs_correct === `${args.runs}/${args.runs}` && x.ttft_suspect_cells < x.n);
    const summary = { fills, chars_per_token_observed: charsPerTokenObserved, first_recall_drop_fill: drops[0]?.fill_pct ?? null, ceiling_fill: ceiling.at(-1)?.fill_pct ?? null, n: rows.length, totals: { prompt_tokens: rows.reduce((a,x) => a + (x.prompt_tokens || 0), 0), completion_tokens: rows.reduce((a,x) => a + (x.completion_tokens || 0), 0) }, elapsed_s: (now() - start) / 1000, stopped, stop_reason, via: args.via, model: args.via === 'direct' ? args.alias : args.model, think: args.think, seed: args.seed, runs: args.runs, ctx: args.ctx, ctx_assumed: args.ctxAssumed ?? false };
    if (args.idleCheck) Object.assign(summary, await checkIdle());
    writeFileSync(join(out, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`, { mode: 0o600 });
    return summary;
  };
  try {
    for (const cell of cells) {
      if (cell.skipped) {
        const skippedNeedles = makeNeedles(cell.seed ?? args.seed + cell.run - 1, args.needles);
        const skippedPrompt = cell.messages?.[1]?.content ?? buildMessages({ filler: '', needles: skippedNeedles })[1].content;
        const row = { fill_pct: cell.fillPct, run: cell.run, seed: cell.seed ?? args.seed + cell.run - 1, target_tokens: cell.targetTokens, prompt_tokens: cell.measuredPromptTokens, prompt_chars: cell.promptChars ?? skippedPrompt.length, prompt_sha256_prefix: createHash('sha256').update(skippedPrompt).digest('hex').slice(0, 12), clamped: true, via: args.via, model: args.via === 'direct' ? args.alias : args.model, think: args.think, status: 'skipped_over_ctx', hits: 0, correct: false, hits_core: 0, correct_core: false, answer_kind: 'empty', answer_values_in_filler: 0, needles: skippedNeedles.map(n => n.match(/module ([\w-]+)/)[1]) };
        writeSync(fd, `${JSON.stringify(row)}\n`); rows.push(row); continue;
      }
      if (signal?.aborted) { stopped = true; stop_reason = signal.reason || 'signal'; break; }
      const estimateSeconds = cell.targetTokens / args.ppTps + args.maxTokens / args.tgTps + 2;
      if ((now() - start) / 1000 + estimateSeconds > args.maxSeconds) { stopped = true; stop_reason = 'max_seconds'; break; }
      const measured = await runCell({ fetchFn, target, cell, args, now, signal });
      const answer = Object.getOwnPropertyDescriptor(measured, 'answer')?.value ?? '';
      const cellNeedles = cell.needles ?? needles;
      const classification = measured.status === 'ok' ? classifyAnswer(answer, cellNeedles, cell.filler ?? '') : { ...scoreRecall('', cellNeedles), answer_kind: 'empty', answer_values_in_filler: 0 };
      if (measured.status === 'ok' && Number.isFinite(measured.prompt_tokens) && cell.targetTokens > 0) {
        measured.size_error_pct = (measured.prompt_tokens - cell.targetTokens) / cell.targetTokens * 100;
        measured.size_off = Math.abs(measured.size_error_pct) > 15;
      }
      const row = { ...measured, prompt_chars: cell.messages?.[1]?.content?.length ?? cell.promptChars, filler_chars: cell.filler?.length ?? 0, seed: cell.seed ?? args.seed + cell.run - 1, prompt_sha256_prefix: cell.messages?.[1]?.content ? createHash('sha256').update(cell.messages[1].content).digest('hex').slice(0, 12) : null, ...(cell.clamped ? { clamped: true } : {}), ...classification, needles: cellNeedles.map(n => n.match(/module ([\w-]+)/)[1]) };
      delete row.answer;
      if (answersFd !== null && measured.status === 'ok') writeSync(answersFd, `${JSON.stringify({ fill_pct: cell.fillPct, run: cell.run, answer: (Object.getOwnPropertyDescriptor(measured, 'answer')?.value ?? '').slice(0, 2000) })}\n`);
      writeSync(fd, `${JSON.stringify(row)}\n`); rows.push(row);
      timeouts = row.status === 'timeout' ? timeouts + 1 : 0;
      if (timeouts >= 2) { stopped = true; stop_reason = 'consecutive_timeouts'; break; }
    }
  } finally { closeSync(fd); if (answersFd !== null) closeSync(answersFd); }
  const summary = await finish();
  console.log('fill | prompt tokens | ctx % | ttft s | decode t/s | strict k/R | core k/R');
  for (const fill of summary.fills) console.log(`${fill.fill_pct}% | ${fill.prompt_tokens ?? '-'} | ${fill.ctx_pct === null ? '-' : `${fill.ctx_pct.toFixed(1)}%`} | ${fill.ttft_ms === null ? '-' : `${(fill.ttft_ms / 1000).toFixed(3)}${fill.ttft_suspect_cells ? '*' : ''}`} | ${fill.decode_tps === null ? '-' : fill.decode_tps.toFixed(1)} | ${fill.runs_correct} | ${fill.runs_correct_core}`);
  if (summary.fills.some(fill => fill.ttft_suspect_cells)) console.log('* first token implausibly fast: likely a server prompt-cache hit, not a cold prefill');
  if (args.idleCheck) console.log(`idle: ${summary.idle === null ? 'unknown' : summary.idle} | CPU ${summary.idle_cpu_pct.join(',')}%${summary.idle_reason ? ` | ${summary.idle_reason}` : ''}`);
  return { rows, summary, stopped, stop_reason };
}

export async function main(argv = process.argv.slice(2), deps = {}) {
  let args;
  try { args = parseArgs(argv); }
  catch (error) { console.error(`context-ladder: ${error.message}`); process.exitCode = 2; return; }
  let warnings;
  try { warnings = validateArgs(args); }
  catch (error) { console.error(`context-ladder: ${error.message}`); process.exitCode = 2; return; }
  let ctx = args.ctx, assumed = ctx === undefined, loaded;
  const fetchFn = deps.fetchFn ?? fetch;
  const signalController = new AbortController();
  const onInterrupt = signal => { signalController.abort(signal); };
  const sigint = () => onInterrupt('SIGINT'), sigterm = () => onInterrupt('SIGTERM');
  if (!deps.signal) { process.once('SIGINT', sigint); process.once('SIGTERM', sigterm); }
  try {
    if (!args.dryRun) {
      loaded = deps.target ?? (args.via === 'direct' ? loadDirect({ alias: args.alias }) : loadOmniroute());
      const actual = args.via === 'direct' ? await loadCtxDirect({ fetchFn, baseUrl: loaded.baseUrl, alias: loaded.alias, secret: loaded.secret }) : undefined;
      ctx = actual || loaded.ctx || ctx || 131072; assumed = !actual && !loaded.ctx && args.ctx === undefined;
      if (args.via === 'direct') args.alias ||= loaded.alias;
      if (args.calibrate) {
        if (args.via !== 'direct') throw usageError('--calibrate requires --via direct');
        const sample = makeFiller(1, 100000);
        const measured = await calibrateDirect({ fetchFn, baseUrl: loaded.baseUrl, alias: args.alias, secret: loaded.secret, text: sample });
        const ratio = measured.tokens ? measured.chars / measured.tokens : null;
        console.log(`chars_per_token: ${ratio ?? 'unknown'}`); process.exitCode = 0; return;
      }
    }
  } catch (error) { console.error(`context-ladder: ${redact(error.message, loaded?.secret)}`); process.exitCode = error.exitCode || 2; return; }
  args.ctx = ctx; args.ctxAssumed = assumed;
  const cells = planCells(args, ctx);
  const needles = makeNeedles(args.seed, args.needles);
  try {
    if (!args.dryRun) for (const cell of cells) {
      const cellSeed = args.seed + cell.run - 1;
      const usable = Math.max(0, ctx - args.maxTokens - 256);
      const estimatedFillerChars = Math.max(0, Math.round(cell.targetTokens * args.charsPerToken));
      const fillerChars = Math.min(estimatedFillerChars, Math.floor(usable * args.charsPerToken));
      if (fillerChars < estimatedFillerChars) cell.clamped = true;
      const prompt = makeCellPrompt({ seed: cellSeed, run: 1, fillPct: cell.fillPct, needles: args.needles, targetTokens: cell.targetTokens, fillerChars });
      const synthetic = prompt.filler;
      cell.seed = cellSeed; cell.promptChars = synthetic.length;
      cell.messages = prompt.messages; cell.filler = prompt.filler; cell.needles = prompt.needles;
      if (usable === 0 && cell.targetTokens > 0) { cell.clamped = true; cell.skipped = true; }
      if (args.via === 'direct' && !cell.skipped) {
        const measured = args.via === 'direct'
          ? await calibrateDirect({ fetchFn, baseUrl: loaded.baseUrl, alias: args.alias, secret: loaded.secret, text: cell.messages[1].content })
          : null;
        if (measured) cell.measuredPromptTokens = measured.tokens;
      }
    }
  } catch (error) { console.error(`context-ladder: ${redact(error.message, loaded?.secret)}`); process.exitCode = 1; return; }
  const seconds = estimate(cells, args);
  const plan = formatPlan(args, cells, seconds, { ctxAssumed: assumed });
  const bound = plan.split('\n').find(line => line.startsWith('context-ladder:'));
  console.error(bound);
  for (const warning of warnings) console.error(`context-ladder: ${warning}`);
  console.log(plan.replace(`${bound}\n`, ''));
  console.log(bound);
  try {
    if (!args.dryRun) {
      const result = await runLadder({ out: resolve(args.out), cells, args, target: loaded, needles, fetchFn, signal: deps.signal ?? signalController.signal });
      process.exitCode = result.stop_reason === 'SIGINT' ? 130 : result.stop_reason === 'SIGTERM' ? 143 : result.rows.length && result.rows.every(row => row.status !== 'ok') ? 1 : 0;
    }
  } catch (error) {
    console.error(`context-ladder: ${redact(error.message, loaded?.secret ?? deps.target?.secret)}`);
    process.exitCode = 1;
  } finally {
    if (!deps.signal) { process.removeListener('SIGINT', sigint); process.removeListener('SIGTERM', sigterm); }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();
