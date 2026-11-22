import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, rm, symlink, readFile, writeFile, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parseArgs, validateArgs, planCells, estimate, formatPlan, loadOmniroute, loadDirect, makeFiller, makeNeedles, plantNeedles, buildMessages, makeCellPrompt, classifyAnswer, calibrateDirect, calibrateOmniroute, loadCtxDirect, redact, runCell, scoreRecall, runLadder, main } from '../../../.claude/skills/onboard-local-model/scripts/context-ladder.mjs';

const timeout = { timeout: 2000 };
const valid = (...args) => parseArgs(['--out', 'tmp/run', ...args]);

test('parses defaults and bounded flags', timeout, () => {
  const args = valid();
  assert.deepEqual(args.fills, [0, 25, 50, 75, 90]);
  assert.equal(args.keepAnswers, false);
  assert.equal(args.seed, 1);
  assert.equal(valid('--seed', '24').seed, 24);
  assert.throws(() => valid('--seed', '2147483648'), /--seed/);
  assert.throws(() => valid('--runs', '10'), /--runs/);
  assert.equal(valid('--keep-answers').keepAnswers, true);
  assert.equal(args.via, 'omniroute');
  assert.equal(args.needles, 3);
  assert.equal(args.ctx, undefined);
  assert.equal(valid('--ctx', '8192', '--pp-tps', '500.5').ppTps, 500.5);
  assert.equal(valid('--chars-per-token', '1.5').charsPerToken, 1.5);
  assert.equal(valid('--chars-per-token', '8').charsPerToken, 8);
  for (const value of ['1.49', '8.01', 'NaN']) assert.throws(() => valid('--chars-per-token', value), /--chars-per-token/);
});

test('rejects unknown, duplicate, missing and invalid-valued flags by name', timeout, () => {
  for (const [argv, flag] of [[['--wat'], '--wat'], [['--runs'], '--runs'], [['--runs','1','--runs','2'], '--runs'], [['--needles','x'], '--needles'], [['--fills','0,0'], '--fills'], [['--via','no'], '--via'], [['--max-tokens','2'], '--max-tokens'], [['--ctx','0'], '--ctx']]) {
    assert.throws(() => parseArgs(['--out','tmp/x',...argv]), new RegExp(flag));
  }
  assert.throws(() => parseArgs([]), /--out/);
});

test('validates new in-tmp output and rejects existing, escaping, and symlinked parents', timeout, async () => {
  const root = await mkdtemp(join(tmpdir(), 'cl13-validation-'));
  try {
    await mkdir(join(root, 'tmp'));
    const args = parseArgs(['--out','tmp/new']);
    assert.deepEqual(validateArgs(args, { cwd: root }), []);
    await mkdir(join(root, 'tmp', 'exists'));
    assert.throws(() => validateArgs(parseArgs(['--out','tmp/exists']), { cwd: root }), /--out.*already exists/);
    assert.throws(() => validateArgs(parseArgs(['--out','tmp/../escape']), { cwd: root }), /--out.*under tmp/);
    await mkdir(join(root, 'outside'));
    await symlink(join(root, 'outside'), join(root, 'tmp', 'link'));
    assert.throws(() => validateArgs(parseArgs(['--out','tmp/link/new']), { cwd: root }), /--out.*symlink/);
    assert.deepEqual(validateArgs(parseArgs(['--via','omniroute','--alias','x','--out','tmp/a']), { cwd: root }), ['warning: --alias ignored with --via omniroute']);
    assert.deepEqual(validateArgs(parseArgs(['--via','direct','--model','x','--out','tmp/a']), { cwd: root }), ['warning: --model ignored with --via direct']);
    const both = parseArgs(['--via','direct','--model','x','--alias','y','--out','tmp/a']);
    assert.deepEqual(validateArgs(both, { cwd: root }), ['warning: --model ignored with --via direct']);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('plans evenly spread needles for K=1, 2, 3, and 5', timeout, () => {
  const depths = k => planCells(valid('--fills','50','--needles',String(k)), 1000)[0].needleDepths;
  assert.deepEqual(depths(1), [50]);
  assert.deepEqual(depths(2), [25,75]);
  assert.deepEqual(depths(3), [10,50,90]);
  assert.deepEqual(depths(5), [0,25,50,75,100]);
  const cells = planCells(valid('--fills','0,50','--runs','2'), 1000);
  assert.deepEqual(cells.map(c => [c.fillPct,c.run,c.targetTokens]), [[0,1,0],[50,1,488],[0,2,0],[50,2,488]]);
  const bounded = planCells(valid('--fills','100','--max-tokens','256'), 1024)[0];
  assert.deepEqual([bounded.targetTokens, bounded.clamped, bounded.skipped], [512, true, false]);
  const impossible = planCells(valid('--fills', '100', '--max-tokens', '4096'), 1024)[0];
  assert.deepEqual([impossible.targetTokens, impossible.clamped, impossible.skipped], [0, false, true]);
});

test('estimates prefill and decode; formats assumed context, bound, and over-budget warning', timeout, () => {
  const args = valid('--fills','50','--max-tokens','100','--pp-tps','10','--tg-tps','5','--timeout','60','--max-seconds','60');
  const cells = planCells(args, 1000);
  assert.equal(estimate(cells, args), 72);
  const plan = formatPlan(args, cells, estimate(cells, args), { ctxAssumed: true });
  assert.match(plan, /ctx assumed/);
  assert.match(plan, /context-ladder: 1 cells, upper bound 60 s \(timeout x cells\)/);
  assert.match(plan, /estimated total 72\.0 s/);
  assert.match(plan, /warning: estimate exceeds --max-seconds/);
  const overArgs = valid('--fills','50','--max-tokens','4096','--tg-tps','1');
  const over = formatPlan(overArgs, cells, 5000, { ctxAssumed: true });
  assert.match(over, /warning: estimate exceeds --max-seconds/);
});

test('dry-run exits zero, prints bound, and does not create output or its parent', timeout, async () => {
  const root = await mkdtemp(join(tmpdir(), 'cl7-dry-'));
  try {
    const out = join(root, 'tmp', 'not-created', 'run');
    const relative = `${out.slice(root.length + 1)}`;
    const child = spawn(process.execPath, [new URL('../../../.claude/skills/onboard-local-model/scripts/context-ladder.mjs', import.meta.url).pathname, '--out', relative, '--fills','0,50','--ctx','1024','--dry-run'], { cwd: root, stdio: ['ignore','pipe','pipe'] });
    let stdout = '', stderr = '';
    child.stdout.setEncoding('utf8').on('data', chunk => stdout += chunk);
    child.stderr.setEncoding('utf8').on('data', chunk => stderr += chunk);
    const code = await new Promise(resolve => child.on('close', resolve));
    assert.equal(code, 0, stderr);
    assert.match(stderr, /context-ladder: 2 cells, upper bound 1200 s/);
    assert.match(stdout, /fill %/);
    await assert.rejects(import('node:fs/promises').then(fs => fs.access(out)));
    await assert.rejects(import('node:fs/promises').then(fs => fs.access(join(root, 'tmp', 'not-created'))));
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('loads targets without exposing secrets and rejects missing providers or yq', timeout, () => {
  const home = '/home/test';
  const omni = loadOmniroute({ home, realpath: path => path, readFile: path => path.endsWith('models.json') ? JSON.stringify({ providers: { 'router-env': { baseUrl: 'http://router', apiKey: '!cat /home/test/key' } } }) : 'SECRETKEY-12345\n' });
  assert.equal(omni.secret, 'SECRETKEY-12345');
  for (const apiKey of ['!echo x', '!cat /home/test/../outside']) assert.throws(() => loadOmniroute({ home, realpath: path => path, readFile: () => JSON.stringify({ providers: { 'router-env': { baseUrl: 'http://router', apiKey } } }) }), error => error.exitCode === 2 && /unsupported apiKey command/.test(error.message));
  assert.throws(() => loadOmniroute({ home, realpath: path => path, readFile: () => JSON.stringify({ providers: { 'router-env': { baseUrl: 'http://router', apiKey: '!cat /outside/key' } } }) }), error => error.exitCode === 2);
  assert.throws(() => loadOmniroute({ home, realpath: path => path, readFile: path => path.endsWith('models.json') ? JSON.stringify({ providers: { 'router-env': { baseUrl: 'http://router', apiKey: '!cat /home/test/missing' } } }) : (() => { throw new Error('SECRET_FILE_CONTENT'); })() }), error => error.exitCode === 2 && !/SECRET_FILE_CONTENT/.test(error.message));
  process.env.CL11_TEST_KEY = 'ENV-SECRET';
  assert.equal(loadOmniroute({ home, realpath: path => path, readFile: () => JSON.stringify({ providers: { 'router-env': { baseUrl: 'http://router', apiKey: 'CL11_TEST_KEY' } } }) }).secret, 'ENV-SECRET');
  delete process.env.CL11_TEST_KEY;
  assert.equal(loadOmniroute({ home, realpath: path => path, readFile: () => JSON.stringify({ providers: { 'router-env': { baseUrl: 'http://router', apiKey: 'literal-key' } } }) }).secret, 'literal-key');
  assert.equal(omni.baseUrl, 'http://router'); assert.equal(JSON.stringify(omni).includes('SECRETKEY-12345'), false);
  assert.throws(() => loadOmniroute({ home, readFile: () => '{"providers":{}}' }), e => e.exitCode === 2 && /make setup-local-llm-agents/.test(e.message));
  const exec = (_bin, args) => ({ '.server.api_key': 'SECRETKEY-12345', '.llm_server.port': '9000', '.models[] | select(.enabled == true) | .alias': 'a', '.models[] | select(.alias == "a") | .ctx_size': '8192' }[args[1]] ?? '');
  const direct = loadDirect({ home, exec }); assert.equal(direct.baseUrl, 'http://127.0.0.1:9000/v1'); assert.equal(direct.ctx, 8192); assert.equal(JSON.stringify(direct).includes('SECRETKEY-12345'), false);
  assert.throws(() => loadDirect({ home, exec: () => { const error = new Error('missing'); error.code = 'ENOENT'; throw error; } }), e => e.exitCode === 2);
  assert.throws(() => loadDirect({ home, exec: (_bin, args) => args[1] === '.server.api_key' ? 'key' : '' }), e => e.exitCode === 2);
  assert.throws(() => loadDirect({ home, exec: (_bin, args) => args[1] === '.server.api_key' ? 'key' : args[1] === '.models[] | select(.enabled == true) | .alias' ? '' : '8000' }), e => e.exitCode === 2);
});

test('direct loader reports missing yq and no enabled model as usage errors', timeout, () => {
  const home = '/home/test';
  assert.throws(() => loadDirect({ home, exec: () => { const error = new Error('missing'); error.code = 'ENOENT'; throw error; } }), error => error.exitCode === 2 && /yq/.test(error.message));
  assert.throws(() => loadDirect({ home, exec: (_bin, args) => args[1] === '.server.api_key' ? 'key' : args[1] === '.llm_server.port' ? 'null' : '' }), error => error.exitCode === 2 && /enabled alias/.test(error.message));
});

test('streams a 200K-token-sized filler without unbounded RSS growth', timeout, () => {
  const before = process.memoryUsage().rss;
  const filler = makeFiller(2026, 750000);
  assert.equal(filler.length, 750000);
  assert.ok(process.memoryUsage().rss - before < 300 * 1024 * 1024);
});

test('filler and unique fact needles are deterministic and placed on lines', timeout, () => {
  assert.equal(makeFiller(42, 200), makeFiller(42, 200)); assert.notEqual(makeFiller(42, 200), makeFiller(43, 200));
  const needles = makeNeedles(42, 3); assert.deepEqual(needles, makeNeedles(42, 3));
  assert.equal(new Set(needles.map(x => x.match(/is (.+)\.$/)[1])).size, 3);
  for (const k of [1,2,3]) { const ns = makeNeedles(7, k); const planted = plantNeedles(makeFiller(7, 500), ns, k === 1 ? [50] : k === 2 ? [25,75] : [10,50,90]); for (const n of ns) assert.ok(planted.split('\n').includes(n)); }
  const messages = buildMessages({ filler: '', needles, question: 'recall' }); for (const n of needles) assert.ok(messages[1].content.includes(n.match(/module ([\w-]+)/)[1]));
  assert.match(messages[1].content, /each module ID, return its exact value on the same line/i);
});

test('direct tokenizer calibration measures once and OmniRoute makes no sizing probe', timeout, async () => {
  let requests = 0;
  const direct = await calibrateDirect({ baseUrl: 'http://fake/v1', alias: 'router-model', secret: 'SECRETKEY-12345', text: makeFiller(4, 370), fetchFn: async (url, options) => { requests++; assert.equal(url, 'http://fake/tokenize'); assert.equal(JSON.parse(options.body).model, 'router-model'); assert.equal(options.headers.Authorization, 'Bearer SECRETKEY-12345'); return { json: async () => ({ tokens: Array(Math.round(JSON.parse(options.body).content.length / 3.7 + (requests % 2 ? 1 : -1))).fill(1) }) }; } });
  assert.equal(requests, 1); assert.ok(Math.abs(direct.chars - 370) <= 1);
  const probe = await calibrateOmniroute();
  assert.equal(probe.method, 'usage'); assert.equal(probe.requests, 0); assert.equal(probe.charsPerToken, 3.4);
  assert.equal(await loadCtxDirect({ baseUrl: 'http://fake/v1', alias: 'router-model', secret: 'x', fetchFn: async url => { assert.equal(url, 'http://fake/props?model=router-model'); return { json: async () => ({ default_generation_settings: { n_ctx: 4096 } }) }; } }), 4096);
  const fallback = loadDirect({ home: '/home/test', exec: (_bin, args) => ({ '.server.api_key': 'key', '.llm_server.port': 'null', '.models[] | select(.enabled == true) | .alias': 'first', '.models[] | select(.alias == "first") | .ctx_size': '' }[args[1]] ?? '') });
  assert.equal(fallback.baseUrl, 'http://127.0.0.1:8000/v1');
  assert.equal(await loadCtxDirect({ baseUrl: 'http://fake/v1', secret: 'x', fetchFn: async () => ({ ok: false }) }), undefined);
});

test('direct calibration measures the actual generated filler, not a proxy string', timeout, async () => {
  let measuredText = '';
  const result = await calibrateDirect({ baseUrl: 'http://fake/v1', alias: 'router-model', secret: 'x', text: makeFiller(1, 1000), fetchFn: async (_url, options) => { measuredText = JSON.parse(options.body).content; return { ok: true, json: async () => ({ tokens: Array(Math.round(JSON.parse(options.body).content.length / 2)).fill(1) }) }; } });
  assert.notEqual(measuredText, 'x'.repeat(measuredText.length));
  assert.equal(measuredText.length, result.text.length);
  assert.equal(result.tokens, 500);
});

test('direct calibration measures the received prompt to within three percent', timeout, async () => {
  const result = await calibrateDirect({ baseUrl: 'http://fake/v1', alias: 'x', text: makeFiller(8, 1200), fetchFn: async (_url, options) => ({ json: async () => ({ tokens: Array(Math.round(JSON.parse(options.body).content.length / 3.7)).fill(1) }) }) });
  assert.equal(result.tokens, Math.round(1200 / 3.7));
});

test('direct calibration never exceeds twelve tokenizer requests', timeout, async () => {
  let requests = 0;
  const result = await calibrateDirect({ baseUrl: 'http://fake/v1', secret: 'SECRETKEY-12345', targetTokens: 100, fetchFn: async () => { requests++; return { ok: true, json: async () => ({ tokens: Array(1000).fill(1) }) }; } });
  assert.ok(requests <= 12); assert.equal(result.requests, requests);
});

test('scores strict and core module-value pairs on the same answer line', timeout, () => {
  const needles = ['The deploy token for module zx-1 is token-1869.', 'The deploy token for module zx-2 is blue-7006.'];
  assert.deepEqual(scoreRecall('zx-1: 1869\nzx-2: blue-7006', needles), { hits: 1, correct: false, hits_core: 2, correct_core: true });
  assert.deepEqual(scoreRecall('zx-1: 9999\nzx-2: blue-7006', needles), { hits: 1, correct: false, hits_core: 1, correct_core: false });
  assert.deepEqual(scoreRecall('zx-other: 1869\nzx-2: blue-7006', needles), { hits: 1, correct: false, hits_core: 1, correct_core: false });
});

test('plans 200K context percentages and prefill estimates', timeout, () => {
  const args = valid('--ctx', '204800', '--fills', '0,25,50,75,90,95', '--max-tokens', '256', '--timeout', '600', '--pp-tps', '580');
  const cells = planCells(args, 204800);
  assert.deepEqual(cells.map(cell => cell.targetTokens), [0, 51200, 102400, 153600, 184320, 194560]);
  const plan = formatPlan(args, cells, estimate(cells, args));
  assert.match(plan, /194560/);
  assert.match(plan, /% of ctx/);
  assert.match(formatPlan({ ...args, timeout: 30 }, cells, estimate(cells, args)), /per-request timeout/);
});

test('records redacted HTTP JSON and non-JSON errors without storing request bodies', timeout, async () => {
  const secret = 'SECRETKEY-12345';
  const cell = { fillPct: 50, targetTokens: 500, messages: [{ role: 'user', content: 'synthetic request text' }] };
  for (const [body, type, message] of [[JSON.stringify({ error: { type: 'exceed_context_size', message: `bad ${secret}` } }), 'exceed_context_size', 'bad [REDACTED]'], ['server unhappy', 'non_json', 'server unhappy']]) {
    const row = await runCell({ fetchFn: async () => ({ ok: false, status: 400, text: async () => body }), target: { baseUrl: 'http://fake/v1', secret }, cell, args: { via: 'direct', alias: 'local', think: 'off', timeout: 5, maxTokens: 10 } });
    assert.equal(row.error_type, type);
    assert.equal(row.error_message, message);
    assert.doesNotMatch(JSON.stringify(row), /synthetic request text|SECRETKEY-12345/);
  }
});

test('streams and measures a cell, and redacts literal, encoded and base64 secrets', timeout, async () => {
  const secret = 'SECRETKEY-12345';
  const chunks = ['data: {"choices":[{"delta":{"reasoning_content":"hmm"}}]}', 'data: {"choices":[{"delta":{"content":"module zx-1 is amber-heron-1"}}]}', 'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":12,"completion_tokens":4}}', 'data: [DONE]'].map(x => `${x}\r\n\r\n`);
  const row = await runCell({ fetchFn: async (_url, options) => { assert.equal(options.headers.Authorization, `Bearer ${secret}`); return { ok: true, status: 200, body: (async function* () { for (const c of chunks) yield Buffer.from(c); })() }; }, target: { baseUrl: 'http://fake/v1', secret }, cell: { fillPct: 0, targetTokens: 0, messages: [] }, args: { via: 'direct', model: 'x', think: 'off', timeout: 5, maxTokens: 10 }, now: (() => { let n = 0; return () => n += 100; })() });
  assert.equal(row.status, 'ok', JSON.stringify(row)); assert.equal(row.ttft_ms, 100); assert.equal(row.completion_tokens, 4); assert.equal(row.prompt_tokens, 12); assert.equal(row.reasoning_tokens_hint, 1);
  const options = JSON.parse((await (async () => { let value; await runCell({ fetchFn: async (_url, opts) => { value = opts; return { ok: false, status: 500 }; }, target: { baseUrl: 'http://fake/v1' }, cell: { fillPct: 0, targetTokens: 0, messages: [] }, args: { via: 'omniroute', model: 'selected-id', think: 'off', timeout: 5, maxTokens: 2 } }); return value; })()).body);
  assert.equal(options.model, 'selected-id'); assert.equal(options.stream, true); assert.equal(options.stream_options.include_usage, true); assert.deepEqual(options.chat_template_kwargs, { enable_thinking: false }); assert.equal(options.cache_prompt, false);
  const directOpts = await runCell({ fetchFn: async (_url, opts) => ({ ok: false, status: 500, opts }), target: { baseUrl: 'http://fake/v1' }, cell: { fillPct: 0, targetTokens: 0, messages: [] }, args: { via: 'direct', alias: 'local', think: 'on', timeout: 5, maxTokens: 2 } });
  assert.equal(directOpts.status, 'http_500');
  for (const leak of [secret, encodeURIComponent(secret), Buffer.from(secret).toString('base64')]) assert.equal(redact(`x ${leak}`, secret).includes(leak), false);
  assert.equal(JSON.stringify(row).includes(secret), false); assert.equal(JSON.stringify(row).includes('module zx-1'), false);
  await assert.rejects(calibrateDirect({ baseUrl: 'http://fake/v1', secret, targetTokens: 1, fetchFn: async () => { throw new Error(`failed ${encodeURIComponent(secret)} ${Buffer.from(secret).toString('base64')}`); } }), error => ![secret, encodeURIComponent(secret), Buffer.from(secret).toString('base64')].some(value => error.message.includes(value)));
});

test('keeps answers only when opted in, truncates each cell, and protects the answer file', timeout, async () => {
  const root = await mkdtemp(join(tmpdir(), 'cl12-answers-'));
  try {
    const args = { fills: [0], runs: 1, maxSeconds: 600, minTg: 1, via: 'direct', alias: 'x', think: 'off', timeout: 1, maxTokens: 8, ppTps: 10, tgTps: 10, keepAnswers: false };
    const cell = { fillPct: 0, run: 1, targetTokens: 0, messages: [] };
    const answerTarget = { baseUrl: 'http://fake/v1', secret: 'SECRETKEY-12345' };
    const fetchFn = async () => ({ ok: true, body: (async function* () { yield Buffer.from(`data: ${JSON.stringify({ choices: [{ delta: { content: 'a'.repeat(2100) } }] })}\n\n`); yield Buffer.from(`data: ${JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 1, completion_tokens: 1 } })}\n\n`); yield Buffer.from('data: [DONE]\n\n'); })() });
    const off = join(root, 'off');
    await runLadder({ out: off, cells: [cell], args, target: answerTarget, needles: [], fetchFn });
    await assert.rejects(readFile(join(off, 'answers.jsonl')));
    assert.doesNotMatch(await readFile(join(off, 'results.jsonl'), 'utf8'), /a{20}|SECRETKEY-12345/);
    const on = join(root, 'on');
    await runLadder({ out: on, cells: [cell], args: { ...args, keepAnswers: true }, target: answerTarget, needles: [], fetchFn });
    const answers = (await readFile(join(on, 'answers.jsonl'), 'utf8')).trim().split('\\n').map(JSON.parse);
    assert.deepEqual(answers, [{ fill_pct: 0, run: 1, answer: 'a'.repeat(2000) }]);
    assert.equal((await stat(join(on, 'answers.jsonl'))).mode & 0o777, 0o600);
    assert.doesNotMatch(await readFile(join(on, 'answers.jsonl'), 'utf8'), /SECRETKEY-12345/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('appends each result and writes summary despite cell failures and budget stop', timeout, async () => {
  const root = await mkdtemp(join(tmpdir(), 'cl9-ladder-'));
  try {
    const args = { fills: [0, 50, 70], runs: 1, maxSeconds: 600, minTg: 1, via: 'omniroute', model: 'x', think: 'off', timeout: 1, maxTokens: 8, ppTps: 10, tgTps: 10 };
    const cells = planCells(args, 1000);
    let n = 0;
    const result = await runLadder({ out: join(root, 'out'), cells, args, target: { baseUrl: 'http://fake/v1', secret: 'SECRETKEY-12345' }, needles: ['The deploy token for module zx-1 is amber-heron-1.'], fetchFn: async () => { n++; return { ok: false, status: 500 }; } });
    assert.equal(n, 3, JSON.stringify(result)); assert.equal(result.rows.length, 3); assert.equal(result.summary.n, 3);
    const { readFile, stat } = await import('node:fs/promises');
    assert.equal((await readFile(join(root, 'out', 'results.jsonl'), 'utf8')).trim().split('\n').length, 3);
    assert.equal((await stat(join(root, 'out'))).mode & 0o777, 0o700);
    assert.equal((await stat(join(root, 'out', 'results.jsonl'))).mode & 0o777, 0o600);
    assert.equal(JSON.stringify(result).includes('SECRETKEY-12345'), false);
    assert.ok((await readFile(join(root, 'out', 'summary.json'), 'utf8')).includes('"n": 3'));
    const timeoutOut = join(root, 'timeouts');
    const timeoutResult = await runLadder({ out: timeoutOut, cells, args, target: { baseUrl: 'http://fake/v1', secret: 'x' }, needles: ['The deploy token for module zx-1 is amber-heron-1.'], fetchFn: async () => { throw new DOMException('timeout', 'AbortError'); } });
    assert.equal(timeoutResult.rows.length, 2); assert.equal(timeoutResult.stop_reason, 'consecutive_timeouts');
    const budgetOut = join(root, 'budget');
    const budgetResult = await runLadder({ out: budgetOut, cells, args: { ...args, maxSeconds: 1 }, target: { baseUrl: 'http://fake/v1' }, needles: [], fetchFn: async () => { throw new Error('must not fetch'); } });
    assert.equal(budgetResult.rows.length, 0); assert.equal(budgetResult.stop_reason, 'max_seconds');
    assert.ok((await readFile(join(budgetOut, 'summary.json'), 'utf8')).includes('max_seconds'));
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('main runs the injected target through props, calibration, prompt construction and cells', timeout, async () => {
  const root = await mkdtemp(join(process.cwd(), 'tmp', 'cl9-main-'));
  try {
    const out = join(root, 'out'); let calls = 0;
    const fakeFetch = async (url, options) => {
      calls++;
      if (url.includes('/props?')) return { json: async () => ({ default_generation_settings: { n_ctx: 1024 } }) };
      if (url.endsWith('/tokenize')) return { ok: true, json: async () => ({ tokens: Array(Math.max(1, Math.floor(JSON.parse(options.body).content.length / 3.7))).fill(1) }) };
      return { ok: false, status: 401 };
    };
    const previousExitCode = process.exitCode;
    await main(['--via','direct','--alias','fake','--fills','0','--ctx','1024','--out',out.slice(process.cwd().length + 1)], { target: { baseUrl: 'http://fake/v1', secret: 'SECRETKEY-12345' }, fetchFn: fakeFetch, signal: new AbortController().signal });
    const exitCode = process.exitCode; process.exitCode = previousExitCode;
    assert.equal(exitCode, 1); assert.ok(calls >= 2); assert.ok((await readFile(join(out, 'results.jsonl'), 'utf8')).includes('http_401'));
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('calibration prints one observed ratio and does not run the ladder', timeout, async () => {
  const root = await mkdtemp(join(process.cwd(), 'tmp', 'cl16-calibrate-'));
  const originalLog = console.log; let output = '', requests = 0;
  console.log = value => { output += `${value}\n`; };
  try {
    const previous = process.exitCode;
    await main(['--via','direct','--alias','fake','--calibrate','--out',join(root, 'out').slice(process.cwd().length + 1)], {
      target: { baseUrl: 'http://fake/v1', secret: 'x', alias: 'fake' },
      fetchFn: async (url, options) => {
        if (url.includes('/props?')) return { json: async () => ({ default_generation_settings: { n_ctx: 1024 } }) };
        requests++; assert.equal(url, 'http://fake/tokenize'); assert.equal(JSON.parse(options.body).content.length, 100000);
        return { json: async () => ({ tokens: 25000 }) };
      },
      signal: new AbortController().signal,
    });
    process.exitCode = previous;
    assert.equal(requests, 1); assert.equal(output, 'chars_per_token: 4\n');
    await assert.rejects(readFile(join(root, 'out')));
  } finally { console.log = originalLog; await rm(root, { recursive: true, force: true }); }
});

test('skipped-over-context cells are recorded without sending a request', timeout, async () => {
  const root = await mkdtemp(join(tmpdir(), 'cl16-clamp-'));
  try {
    const args = { fills: [100], runs: 1, maxSeconds: 600, minTg: 1, via: 'omniroute', model: 'x', think: 'off', timeout: 1, maxTokens: 256, ppTps: 10, tgTps: 10 };
    const cells = [{ fillPct: 100, run: 1, seed: 1, targetTokens: 0, skipped: true, needleDepths: [10] }];
    const result = await runLadder({ out: join(root, 'out'), cells, args, target: { baseUrl: 'http://fake/v1' }, needles: makeNeedles(1, 1), fetchFn: async () => assert.fail('must not send skipped cell') });
    assert.equal(result.rows[0].status, 'skipped_over_ctx');
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('filler sizing rounds target tokens times the selected character ratio', timeout, () => {
  for (const charsPerToken of [1.5, 3.4]) {
    const prompt = makeCellPrompt({ seed: 20, targetTokens: 101, charsPerToken });
    assert.equal(prompt.filler.length, Math.round(101 * charsPerToken));
  }
});

test('answer example is not a planted fact for any supported seed', timeout, () => {
  for (let seed = -100; seed <= 1000; seed++) {
    const prompt = makeCellPrompt({ seed, targetTokens: 100, needles: 3 });
    assert.match(prompt.text, /zx-0000-x: sample-0000/);
    assert.ok(prompt.needles.every(fact => !fact.includes('zx-0000-x') && !fact.includes('sample-0000')));
  }
});

test('main sizes filler by target tokens identically across routes at large context', timeout, async () => {
  const root = await mkdtemp(join(process.cwd(), 'tmp', 'cl18-sizing-'));
  try {
    for (const ratio of [3.4, 4.0]) {
      const execute = async (via, suffix) => {
        const out = join(root, `${suffix}-${ratio}`);
        const previous = process.exitCode;
        await main(['--via', via, '--fills', '10,25', '--ctx', '200000', '--chars-per-token', String(ratio), '--out', out.slice(process.cwd().length + 1)], {
          target: { baseUrl: 'http://fake/v1' }, signal: new AbortController().signal,
          fetchFn: async (url, options = {}) => {
            if (url.includes('/props?')) return { json: async () => ({ default_generation_settings: { n_ctx: 200000 } }) };
            if (url.endsWith('/tokenize')) return { json: async () => ({ tokens: 12 }) };
            return { ok: true, body: (async function* () { yield Buffer.from('data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'); yield Buffer.from('data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":12,"completion_tokens":3}}\n\n'); })() };
          },
        });
        process.exitCode = previous;
        return (await readFile(join(out, 'results.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
      };
      const direct = await execute('direct', 'direct'), omni = await execute('omniroute', 'omni');
      for (const rows of [direct, omni]) assert.deepEqual(rows.map(row => row.filler_chars), rows.map(row => Math.round(row.target_tokens * ratio)));
      assert.deepEqual(direct.map(row => row.filler_chars), omni.map(row => row.filler_chars));
      assert.deepEqual(direct.map(row => row.prompt_sha256_prefix), omni.map(row => row.prompt_sha256_prefix));
    }
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('main produces identical seeded prompts and request options across routes and invocations', timeout, async () => {
  const root = await mkdtemp(join(process.cwd(), 'tmp', 'cl15-routes-'));
  try {
    const execute = async (via, suffix) => {
      const requests = [];
      const fetchFn = async (url, options = {}) => {
        const body = options.body ? JSON.parse(options.body) : null;
        requests.push({ url, body });
        if (url.includes('/props?')) return { json: async () => ({ default_generation_settings: { n_ctx: 1024 } }) };
        if (url.endsWith('/tokenize')) return { json: async () => ({ tokens: Math.ceil(body.content.length / 2.9) }) };
        if (body && !body.stream) return { ok: true, json: async () => ({ usage: { prompt_tokens: Math.ceil(body.messages.at(-1).content.length / 4.1) } }) };
        const content = body.messages[1].content;
        return { ok: true, body: (async function* () { yield Buffer.from(`data: ${JSON.stringify({ choices: [{ delta: { content: 'ok' } }] })}\n\n`); yield Buffer.from(`data: ${JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: Math.max(100000, Math.ceil(content.length / 4.1)), completion_tokens: 2 } })}\n\n`); })() };
      };
      const out = join(root, suffix);
      const previous = process.exitCode;
      await main(['--via', via, ...(via === 'direct' ? ['--alias','fake'] : []), '--fills','0,25,50','--ctx','1024','--max-tokens','16','--seed','7','--out',out], { target: { baseUrl: 'http://fake/v1', secret: 'x', alias: 'fake' }, fetchFn, signal: new AbortController().signal });
      process.exitCode = previous;
      return { rows: (await readFile(join(out, 'results.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse), summary: JSON.parse(await readFile(join(out, 'summary.json'), 'utf8')), requests };
    };
    const direct = await execute('direct', 'direct'), omni = await execute('omniroute', 'omni');
    assert.deepEqual(direct.rows.map(row => [row.prompt_sha256_prefix, row.needles]), omni.rows.map(row => [row.prompt_sha256_prefix, row.needles]));
    assert.ok(direct.rows.slice(1).every(row => Number.isFinite(row.size_error_pct)));
    assert.ok(omni.rows.slice(1).every(row => Number.isFinite(row.size_error_pct)));
    assert.ok(direct.rows.slice(1).some(row => row.size_off));
    assert.ok(omni.rows.slice(1).some(row => row.size_off));
    const directMeasured = direct.requests.filter(r => r.url.endsWith('/tokenize')).map(r => r.body.content);
    const directPrompts = direct.requests.filter(r => r.body?.stream).map(r => r.body.messages[1].content);
    assert.deepEqual(directMeasured, directPrompts);
    assert.equal(typeof direct.summary.chars_per_token_observed, 'number');
    assert.equal(typeof omni.summary.chars_per_token_observed, 'number');
    assert.ok(direct.requests.filter(r => r.body?.stream).every(r => r.body.cache_prompt === false));
    assert.ok(omni.requests.filter(r => r.body?.stream).every(r => r.body.cache_prompt === false));
    assert.ok(omni.rows.every(row => row.ttft_suspect === true));
    assert.ok(omni.summary.fills.every(fill => fill.ttft_suspect_cells > 0));
    const output = console.log; let table = ''; console.log = line => { table += `${line}\n`; };
    try { await runLadder({ out: join(root, 'instant'), cells: planCells(parseArgs(['--out','tmp/instant','--fills','0']), 1024).map(cell => ({ ...cell, messages: [{ role: 'system', content: '' }, { role: 'user', content: 'instant' }], needles: [], filler: '' })), args: { ...parseArgs(['--out','tmp/instant','--fills','0']), ctx: 1024, ctxAssumed: false }, target: { baseUrl: 'http://fake/v1' }, needles: [], fetchFn: async () => ({ ok: true, body: (async function* () { yield Buffer.from('data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'); yield Buffer.from('data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":100000,"completion_tokens":1}}\n\n'); })() }), now: () => 100 }); } finally { console.log = output; }
    assert.match(table, /\* first token implausibly fast: likely a server prompt-cache hit, not a cold prefill/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('main records per-run seeds and deterministic unique prompt hashes', timeout, async () => {
  const root = await mkdtemp(join(process.cwd(), 'tmp', 'cl17-seeds-'));
  try {
    const execute = async suffix => {
      const out = join(root, suffix);
      const previous = process.exitCode;
      await main(['--fills','0,50','--runs','3','--seed','7','--ctx','1024','--out',out.slice(process.cwd().length + 1)], {
        target: { baseUrl: 'http://fake/v1' }, signal: new AbortController().signal,
        fetchFn: async () => ({ ok: true, body: (async function* () { yield Buffer.from(`data: ${JSON.stringify({ choices: [{ delta: { content: 'ok' } }] })}\n\n`); yield Buffer.from(`data: ${JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 12, completion_tokens: 3 } })}\n\n`); })() }),
      });
      process.exitCode = previous;
      return (await readFile(join(out, 'results.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    };
    const first = await execute('first'), second = await execute('second');
    for (const fill of [0, 50]) {
      const rows = first.filter(row => row.fill_pct === fill);
      assert.deepEqual(rows.map(row => row.seed), [7, 8, 9]);
      assert.equal(new Set(rows.map(row => row.prompt_sha256_prefix)).size, 3);
      assert.deepEqual(rows.map(row => row.prompt_sha256_prefix), second.filter(row => row.fill_pct === fill).map(row => row.prompt_sha256_prefix));
    }
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('classifies answer outcomes and counts filler-number distractors', timeout, () => {
  const needles = ['The deploy token for module zx-1 is amber-2907.', 'The deploy token for module zx-2 is blue-7006.'];
  const filler = 'function distractor() { return 92 + 12 + 70; }';
  assert.equal(classifyAnswer('zx-1: amber-2907\nzx-2: blue-7006', needles, filler).answer_kind, 'exact');
  assert.equal(classifyAnswer('zx-1: 2907\nzx-2: 7006', needles, filler).answer_kind, 'format_drift');
  assert.equal(classifyAnswer('zx-1: amber-2907\nzx-2: wrong', needles, filler).answer_kind, 'partial');
  assert.equal(classifyAnswer('zx-1: 92\nzx-2: 12', needles, filler).answer_kind, 'wrong_values');
  assert.equal(classifyAnswer('not sure', needles, filler).answer_kind, 'no_ids');
  assert.equal(classifyAnswer('', needles, filler).answer_kind, 'empty');
  assert.equal(classifyAnswer('zx-1: 92\\nzx-2: 12', needles, filler).answer_values_in_filler, 2);
});

test('run aggregation reports k/R, medians, strict ceiling and interleaves fill-major rounds', timeout, async () => {
  const root = await mkdtemp(join(tmpdir(), 'cl14-aggregate-'));
  try {
    const args = { fills: [0, 50, 75], runs: 3, seed: 1, needles: 1, maxSeconds: 600, minTg: 20, via: 'direct', alias: 'x', think: 'off', timeout: 1, maxTokens: 8, ppTps: 10, tgTps: 10, ctx: 1000 };
    const cells = planCells(args, 1000);
    assert.deepEqual(cells.map(c => [c.fillPct, c.run]), [[0,1],[50,1],[75,1],[0,2],[50,2],[75,2],[0,3],[50,3],[75,3]]);
    const seen = [];
    let response = 0;
    const fetchFn = async () => {
      const n = ++response; seen.push(n);
      const answer = n === 5 ? 'zx-1: 9999' : 'zx-1: amber-2907';
      return { ok: true, body: (async function* () { yield Buffer.from(`data: ${JSON.stringify({ choices: [{ delta: { content: answer } }] })}\n\n`); yield Buffer.from(`data: ${JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 10 + n, completion_tokens: 3 } })}\n\n`); yield Buffer.from('data: [DONE]\n\n'); })() };
    };
    let time = 0;
    const result = await runLadder({ out: join(root, 'out'), cells, args, target: { baseUrl: 'http://fake/v1' }, needles: ['The deploy token for module zx-1 is amber-2907.'], fetchFn, now: () => time += 100 });
    const fifty = result.summary.fills.find(row => row.fill_pct === 50);
    assert.equal(fifty.runs_correct, '2/3'); assert.equal(fifty.recall_rate, 2/3);
    assert.equal(fifty.prompt_tokens, 15);
    assert.equal(result.summary.ceiling_fill, 75);
    assert.equal(result.rows[4].answer_kind, 'wrong_values');
    assert.equal(result.summary.seed, 1);
    assert.equal(result.rows[0].decode_tps, 30);
    assert.equal(result.rows[0].ttft_suspect, false);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('CLI returns code 2 for invalid arguments and runs request execution', timeout, async () => {
  const script = new URL('../../../.claude/skills/onboard-local-model/scripts/context-ladder.mjs', import.meta.url).pathname;
  const run = (args, env = process.env) => new Promise(resolve => {
    const child = spawn(process.execPath, [script, ...args], { cwd: process.cwd(), env, stdio: ['ignore','pipe','pipe'] });
    let stderr = '', stdout = ''; child.stderr.setEncoding('utf8').on('data', chunk => stderr += chunk); child.stdout.setEncoding('utf8').on('data', chunk => stdout += chunk);
    child.on('close', code => resolve({ code, stderr, stdout }));
  });
  const invalid = await run(['--out','tmp/cl7-invalid','--unknown']);
  assert.equal(invalid.code, 2);
  assert.match(invalid.stderr, /--unknown/);
  const home = await mkdtemp(join(tmpdir(), 'cl7-home-'));
  try {
    await mkdir(join(home, '.pi/agent'), { recursive: true });
    await import('node:fs/promises').then(fs => fs.writeFile(join(home, '.pi/agent/models.json'), JSON.stringify({ providers: { 'router-env': { baseUrl: 'http://127.0.0.1:1/v1', apiKey: 'SECRETKEY-12345' } } })));
    const dry = await run(['--out','tmp/cl7-real','--ctx','1024','--fills','0','--dry-run'], { ...process.env, HOME: home });
    assert.equal(dry.code, 0);
    assert.match(dry.stdout, /fill %/);
  } finally { await rm(home, { recursive: true, force: true }); }
});
