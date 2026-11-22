import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, rm, readFile, writeFile, chmod, stat } from 'node:fs/promises';
import { writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = resolve(fileURLToPath(new URL('../../..', import.meta.url)));
const skill = join(repo, '.claude/skills/onboard-local-model');
const fixture = join(repo, 'tests/skills/onboard-local-model/fixtures/fake-openai-server.mjs');
const cli = join(skill, 'scripts/context-ladder.sh');
const timeout = { timeout: 85000 };

async function child(command, args, options = {}) {
  const process = spawn(command, args, { cwd: repo, stdio: ['ignore', 'pipe', 'pipe'], ...options });
  let stdout = '', stderr = '';
  process.stdout.setEncoding('utf8').on('data', chunk => stdout += chunk);
  process.stderr.setEncoding('utf8').on('data', chunk => stderr += chunk);
  const closed = new Promise((resolve, reject) => { process.once('error', reject); process.once('close', (code, signal) => resolve({ code, signal, stdout, stderr })); });
  return { process, closed, output: () => ({ stdout, stderr }) };
}

async function setup(t, serverArgs = []) {
  const root = await mkdtemp(join(repo, 'tmp/cl10-process-'));
  const home = join(root, 'home');
  const children = [];
  await mkdir(join(home, '.pi/agent'), { recursive: true });
  const server = await child(process.execPath, [fixture, ...serverArgs]);
  const readline = await import('node:readline');
  t.after(async () => {
    for (const process of [server.process, ...children]) if (process.exitCode === null && process.signalCode === null) process.kill('SIGKILL');
    await Promise.allSettled([server.closed]);
    await rm(root, { recursive: true, force: true });
  });
  const ready = new Promise((resolve, reject) => {
    const lines = readline.createInterface({ input: server.process.stdout });
    lines.once('line', line => { lines.close(); resolve(JSON.parse(line)); });
    server.process.once('close', code => reject(new Error(`fake server exited ${code}`)));
  });
  const { port } = await ready;
  const keyFile = join(home, '.config/llm-env/key');
  await mkdir(join(home, '.config/llm-env'), { recursive: true });
  await writeFile(keyFile, 'SECRETKEY-12345\n', { mode: 0o600 });
  await writeFile(join(home, '.pi/agent/models.json'), JSON.stringify({ providers: { 'router-env': { baseUrl: `http://127.0.0.1:${port}/v1`, apiKey: `!cat ${keyFile}` } } }));
  const run = async (out, extra = [], env = {}) => {
    const folder = join(root, `out-${out.split('/').at(-1)}`);
    outputPaths.set(out, folder);
    const selectedFills = extra.includes('--fills') ? [] : ['--fills', '0,90'];
    const defaults = ['--needles', '2', '--max-seconds', '60', '--ctx', '8192'];
    const overrides = new Set(extra.filter((value, index) => index % 2 === 0));
    const proc = await child('bash', [cli, '--out', folder.slice(repo.length + 1), ...selectedFills, ...defaults.filter((value, index) => index % 2 === 0 ? !overrides.has(value) : !overrides.has(defaults[index - 1])), ...extra], { env: { ...process.env, HOME: home, ...env } });
    children.push(proc.process);
    return proc;
  };
  return { root, home, port, server, children, run, output(out) { return outputPaths.get(out); } };
}

const outputPaths = new Map();
async function outputs(out) {
  const folder = outputPaths.get(out) ?? resolve(repo, out);
  return { folder, rows: (await readFile(join(folder, 'results.jsonl'), 'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse), summary: JSON.parse(await readFile(join(folder, 'summary.json'), 'utf8')) };
}

test('real CLI and separate HTTP child write private rows and detect deepest recall loss', timeout, async t => {
  const ctx = await setup(t, ['--required-key', 'SECRETKEY-12345', '--drop-deepest', 'true', '--drop-after', '90', '--reasoning', 'true', '--split', 'true']);
  const proc = await ctx.run('tmp/cl10-process-normal');
  const result = await proc.closed;
  assert.equal(result.code, 0, result.stderr);
  const { folder, rows, summary } = await outputs('tmp/cl10-process-normal');
  assert.equal(rows.length, 2);
  assert.equal(summary.first_recall_drop_fill, 90);
  assert.match(result.stdout, /0% \|/);
  assert.equal((await stat(folder)).mode & 0o777, 0o700);
  assert.equal((await stat(join(folder, 'results.jsonl'))).mode & 0o777, 0o600);
  assert.doesNotMatch(await readFile(join(folder, 'results.jsonl'), 'utf8'), /The deploy token/);
});

test('direct router calibration sends root tokenize and props with the body model', timeout, async t => {
  const ctx = await setup(t, ['--required-key', 'SECRETKEY-12345']);
  const config = join(ctx.home, '.config/llm-env');
  await mkdir(config, { recursive: true });
  await writeFile(join(config, 'models.yml'), '');
  const fakeYq = join(ctx.root, 'bin'); await mkdir(fakeYq);
  await import('node:fs/promises').then(fs => fs.writeFile(join(fakeYq, 'yq'), `#!/bin/sh\ncase "$2" in\n.server.api_key) printf SECRETKEY-12345;;\n.llm_server.port) printf ${ctx.port};;\n'.models[] | select(.enabled == true) | .alias') printf router-model;;\n'.models[] | select(.alias == "router-model") | .ctx_size') printf 1024;;\nesac\n`));
  await import('node:fs/promises').then(fs => fs.chmod(join(fakeYq, 'yq'), 0o755));
  const out = join(ctx.root, 'direct-output');
  const proc = await child('bash', [cli, '--via','direct','--alias','router-model','--fills','0','--out',out.slice(repo.length + 1),'--max-seconds','60'], { env: { ...process.env, HOME: ctx.home, PATH: `${fakeYq}:${process.env.PATH}` } });
  const result = await proc.closed;
  assert.equal(result.code, 0, result.stderr);
  assert.equal((await readFile(join(out, 'results.jsonl'), 'utf8')).includes('"status":"ok"'), true);
});

test('HTTP JSON context failure is recorded and later cells continue', timeout, async t => {
  const ctx = await setup(t, ['--context-limit', '500']);
  const proc = await ctx.run('tmp/cl12-process-context', ['--fills', '25,50']);
  const result = await proc.closed;
  assert.equal(result.code, 1, result.stderr);
  const { rows } = await outputs('tmp/cl12-process-context');
  assert.equal(rows.length, 2);
  assert.equal(rows[0].error_type, 'exceed_context_size_error');
  assert.match(rows[0].error_message, /exceeds the available context size/);
});

test('HTTP failure becomes a row and later cells continue', timeout, async t => {
  const ctx = await setup(t, ['--fail-call', '2']);
  const proc = await ctx.run('tmp/cl10-process-http');
  const result = await proc.closed;
  assert.equal(result.code, 0, result.stderr);
  const { rows } = await outputs('tmp/cl10-process-http');
  assert.equal(rows.length, 2); assert.equal(rows[0].status, 'ok'); assert.equal(rows[1].status, 'http_500');
});

test('two timed out requests stop and persist a summary', timeout, async t => {
  const ctx = await setup(t, ['--hang-prompt-over', '500']);
  const proc = await ctx.run('tmp/cl10-process-timeouts', ['--fills', '25,50', '--timeout', '10', '--max-seconds', '180']);
  const result = await proc.closed;
  assert.equal(result.code, 1, result.stderr);
  const { rows, summary } = await outputs('tmp/cl10-process-timeouts');
  assert.equal(rows.length, 2); assert.equal(summary.stopped, true); assert.equal(summary.stop_reason, 'consecutive_timeouts');
});

test('max-seconds writes a clean stopped summary', timeout, async t => {
  const ctx = await setup(t);
  const proc = await ctx.run('tmp/cl10-process-budget', ['--max-tokens', '4096', '--tg-tps', '1']);
  const result = await proc.closed;
  assert.equal(result.code, 0, result.stderr);
  const { rows, summary } = await outputs('tmp/cl10-process-budget');
  assert.equal(rows.length, 0); assert.equal(summary.stop_reason, 'max_seconds');
});

test('SIGTERM aborts active CLI, persists summary and exits 143 without key in argv', timeout, async t => {
  const ctx = await setup(t, ['--hang-prompt-over', '500']);
  const proc = await ctx.run('tmp/cl10-process-signal', ['--fills', '25,50', '--timeout', '30', '--max-seconds', '180']);
  for (let i = 0; i < 100 && !proc.output().stderr.includes('context-ladder:'); i++) await new Promise(resolve => setTimeout(resolve, 20));
  const argv = await readFile(`/proc/${proc.process.pid}/cmdline`, 'utf8');
  assert.doesNotMatch(argv, /SECRETKEY-12345/);
  proc.process.kill('SIGTERM');
  const result = await proc.closed;
  assert.equal(result.code, 143, result.stderr);
  assert.ok((await outputs('tmp/cl10-process-signal')).summary);
  assert.equal((await outputs('tmp/cl10-process-signal')).rows[0].status, 'interrupted');
});

test('top-level loader errors redact plain, URL-encoded and base64 key forms', timeout, async t => {
  const ctx = await setup(t);
  const key = 'CL18-secret-42', forms = [key, encodeURIComponent(key), Buffer.from(key).toString('base64')];
  const script = `import { main } from ${JSON.stringify(join(skill, 'scripts/context-ladder.mjs'))}; const secret = process.env.LEAK; let logs = 0; console.log = () => { if (++logs > 2) throw new Error(secret); }; await main(['--via','direct','--alias','fake','--fills','0','--ctx','1024','--out',process.env.OUT], { target: { baseUrl: 'http://fake/v1', secret, alias: 'fake' }, fetchFn: async url => { if (url.includes('/props?')) return { json: async () => ({ default_generation_settings: { n_ctx: 1024 } }) }; if (url.endsWith('/tokenize')) return { json: async () => ({ tokens: 1 }) }; throw new Error(secret); } });`;
  for (const [index, value] of forms.entries()) {
    const out = join(ctx.root, `top-error-${index}`);
    const proc = await child(process.execPath, ['--input-type=module', '-e', script], { env: { ...process.env, LEAK: value, OUT: out.slice(repo.length + 1) } });
    const result = await proc.closed;
    assert.equal(result.code, 1, result.stderr);
    assert.doesNotMatch(`${result.stdout}${result.stderr}`, new RegExp(value));
    assert.doesNotMatch((await readFile(`/proc/${proc.process.pid}/cmdline`).catch(() => Buffer.alloc(0))).toString(), new RegExp(value));
    for (const file of ['results.jsonl', 'summary.json']) assert.doesNotMatch(await readFile(join(out, file), 'utf8'), new RegExp(value));
  }
});

test('wrong key gives 401 without leaking key in CLI outputs, files or argv', timeout, async t => {
  const ctx = await setup(t, ['--required-key', 'SECRETKEY-12345']);
  const wrongKey = join(ctx.home, '.config/llm-env/key');
  await writeFile(wrongKey, 'SECRETKEY-WRONG-12345\n', { mode: 0o600 });
  await writeFile(join(ctx.home, '.pi/agent/models.json'), JSON.stringify({ providers: { 'router-env': { baseUrl: `http://127.0.0.1:${ctx.port}/v1`, apiKey: `!cat ${wrongKey}` } } }));
  const proc = await ctx.run('tmp/cl10-process-key');
  const argv = await readFile(`/proc/${proc.process.pid}/cmdline`, 'utf8');
  const result = await proc.closed;
  const combined = `${result.stdout}${result.stderr}${argv}`;
  assert.equal(result.code, 1, result.stderr);
  const { rows } = await outputs('tmp/cl10-process-key');
  assert.equal(rows.length, 2);
  assert.ok(rows.every(row => row.status === 'http_401' && row.error_message === 'wrong key'));
  const wrongKeyValue = 'SECRETKEY-WRONG-12345';
  const encodedWrong = [wrongKeyValue, encodeURIComponent(wrongKeyValue), Buffer.from(wrongKeyValue).toString('base64')];
  const artifacts = `${result.stdout}${result.stderr}${argv}${await readFile(join(outputPaths.get('tmp/cl10-process-key'), 'results.jsonl'), 'utf8')}${await readFile(join(outputPaths.get('tmp/cl10-process-key'), 'summary.json'), 'utf8')}`;
  for (const value of encodedWrong) assert.doesNotMatch(artifacts, new RegExp(value));
  assert.doesNotMatch(combined, new RegExp(encodeURIComponent(wrongKeyValue)));
  assert.doesNotMatch(combined, new RegExp(Buffer.from(wrongKeyValue).toString('base64')));
  assert.doesNotMatch(combined, new RegExp(wrongKeyValue));
  assert.doesNotMatch(argv, new RegExp(wrongKeyValue));
  assert.doesNotMatch(await readFile(join(outputPaths.get('tmp/cl10-process-key'), 'results.jsonl'), 'utf8'), new RegExp(wrongKeyValue));
  assert.doesNotMatch(await readFile(join(outputPaths.get('tmp/cl10-process-key'), 'summary.json'), 'utf8'), new RegExp(wrongKeyValue));
});

test('dry run makes no requests and idle check reports CPU without failing', timeout, async t => {
  const ctx = await setup(t);
  const count = join(ctx.root, 'requests.count');
  const dry = await ctx.run('tmp/cl10-process-dry', ['--dry-run']);
  const dryResult = await dry.closed;
  assert.equal(dryResult.code, 0, dryResult.stderr);
  await assert.rejects(readFile(count));
  const bin = join(ctx.root, 'bin'); await mkdir(bin);
  const fakePodman = join(bin, 'podman'); await writeFile(fakePodman, '#!/bin/sh\nif [ "$1" = inspect ]; then echo /fake/cgroup; else echo 27%; fi\n'); await chmod(fakePodman, 0o755);
  const cgroupRoot = join(ctx.root, 'cgroups'); await mkdir(join(cgroupRoot, 'fake/cgroup'), { recursive: true });
  const cpuStat = join(cgroupRoot, 'fake/cgroup/cpu.stat'); let usage = 0;
  await writeFile(cpuStat, 'usage_usec 0\n');
  const ticker = setInterval(() => { usage += 20; writeFileSync(cpuStat, `usage_usec ${usage}\n`); }, 1);
  t.after(() => clearInterval(ticker));
  const idle = await ctx.run('tmp/cl10-process-idle', ['--idle-check'], { PATH: `${bin}:${process.env.PATH}`, CONTEXT_LADDER_IDLE_INTERVAL_MS: '20', CONTEXT_LADDER_CGROUP_ROOT: cgroupRoot });
  const idleResult = await idle.closed;
  assert.equal(idleResult.code, 0, idleResult.stderr);
  clearInterval(ticker);
  const { summary } = await outputs('tmp/cl10-process-idle');
  assert.equal(summary.idle_cpu_pct.length, 2); assert.ok(summary.idle_cpu_pct.every(value => value > 0 && value < 20)); assert.equal(summary.idle, true);
  const busyScript = join(bin, 'podman'); await writeFile(busyScript, '#!/bin/sh\necho /fake/cgroup\n');
  usage = 0; await writeFile(cpuStat, 'usage_usec 0\n');
  const busyTicker = setInterval(() => { usage += 800000; writeFileSync(cpuStat, `usage_usec ${usage}\n`); }, 1);
  t.after(() => clearInterval(busyTicker));
  const busy = await ctx.run('tmp/cl10-process-busy', ['--idle-check'], { PATH: `${bin}:${process.env.PATH}`, CONTEXT_LADDER_IDLE_INTERVAL_MS: '40', CONTEXT_LADDER_CGROUP_ROOT: cgroupRoot });
  assert.equal((await busy.closed).code, 0);
  clearInterval(busyTicker);
  const busySummary = (await outputs('tmp/cl10-process-busy')).summary;
  assert.equal(busySummary.idle, false); assert.ok(busySummary.idle_cpu_pct.every(value => value > 20));
  await writeFile(busyScript, '#!/bin/sh\necho /missing/cgroup\n');
  const invalid = await ctx.run('tmp/cl10-process-idle-invalid', ['--idle-check'], { PATH: `${bin}:${process.env.PATH}`, CONTEXT_LADDER_IDLE_INTERVAL_MS: '1', CONTEXT_LADDER_CGROUP_ROOT: cgroupRoot });
  assert.equal((await invalid.closed).code, 0);
  const unavailable = (await outputs('tmp/cl10-process-idle-invalid')).summary;
  assert.equal(unavailable.idle, null); assert.match(unavailable.idle_reason, /counter unavailable/);
  const isolated = join(ctx.root, 'isolated-bin'); await mkdir(isolated);
  await import('node:fs/promises').then(fs => fs.symlink('/usr/bin/bash', join(isolated, 'bash')));
  await import('node:fs/promises').then(fs => fs.symlink(process.execPath, join(isolated, 'node')));
  await import('node:fs/promises').then(fs => fs.symlink('/usr/bin/env', join(isolated, 'env')));
  const missing = await ctx.run('tmp/cl10-process-idle-missing', ['--idle-check'], { PATH: `${isolated}:${process.env.PATH}`, CONTEXT_LADDER_IDLE_INTERVAL_MS: '1', CONTEXT_LADDER_CGROUP_ROOT: cgroupRoot });
  assert.equal((await missing.closed).code, 0);
  const noPodman = (await outputs('tmp/cl10-process-idle-missing')).summary;
  assert.equal(noPodman.idle, null); assert.match(noPodman.idle_reason, /unavailable/);
});
