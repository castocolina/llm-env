import { createServer } from 'node:http';
import { appendFileSync } from 'node:fs';

const options = Object.fromEntries(process.argv.slice(2).map((arg, index, all) => arg.startsWith('--') ? [arg.slice(2), all[index + 1]] : null).filter(Boolean));
const countFile = options['count-file'];
let calls = 0, chatCalls = 0, streamCalls = 0;
const server = createServer(async (req, res) => {
  calls++;
  if (req.url.endsWith('/chat/completions') && req.method === 'POST') chatCalls++;
  if (countFile) appendFileSync(countFile, '1\n');
  if (options['real-tokenize'] !== 'false' && req.url.startsWith('/v1/tokenize')) { res.writeHead(404).end('not found'); return; }
  if (req.url.startsWith('/tokenize')) {
    let body = ''; for await (const chunk of req) body += chunk;
    const request = JSON.parse(body);
    if (options['require-tokenize-model'] !== 'false' && !request.model) { res.writeHead(400).end('model required'); return; }
    res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ tokens: Array(Math.max(1, Math.floor(request.content.length / 3.7))).fill(1) })); return;
  }
  if (req.url.startsWith('/props')) { res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ default_generation_settings: { n_ctx: 1024 } })); return; }
  if (req.url.endsWith('/chat/completions') && chatCalls >= Number(options['hang-from'] || Infinity) && chatCalls < Number(options['hang-from'] || Infinity) + Number(options['hang-count'] || 0)) return;
  let body = '';
  for await (const chunk of req) body += chunk;
  const request = JSON.parse(body);
  if (request.stream && Number(options['fail-call']) === ++streamCalls) { res.writeHead(500).end('forced failure'); return; }
  if (options['required-key'] && req.headers.authorization !== `Bearer ${options['required-key']}`) { res.writeHead(401).end('wrong key'); return; }
  if (!request.stream) { const content = request.messages?.map(message => message.content).join('\n') || ''; const promptTokens = Math.max(1, Math.floor(content.split(/\s+/).filter(Boolean).length + content.length / 20)); res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ usage: { prompt_tokens: promptTokens } })); return; }
  const promptTokens = Math.max(1, Math.floor(request.messages.reduce((n, message) => n + message.content.split(/\s+/).filter(Boolean).length + message.content.length / 20, 0)));
  if (options['hang-prompt-over'] && promptTokens > Number(options['hang-prompt-over'])) return;
  if (options['hang-prompt-over'] && promptTokens > Number(options['hang-prompt-over'])) { await new Promise(resolve => req.on('close', resolve)); return; }
  if (options['context-limit'] && promptTokens > Number(options['context-limit'])) { res.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ error: { type: 'exceed_context_size_error', message: `request (${promptTokens} tokens) exceeds the available context size (${options['context-limit']} tokens)` } })); return; }
  const content = request.messages?.map(message => message.content).join('\n') || '';
  let answer = [...content.matchAll(/The deploy token for module ([\w-]+) is ([\w-]+)\./g)].map(match => `module ${match[1]} is ${match[2]}`).join('\n');
  if (options['drop-deepest'] === 'true' && Number(content.match(/at (\d+)% fill/)?.[1] ?? 0) >= Number(options['drop-after'] || 90)) answer = answer.split('\n').slice(0, -1).join('\n');
  if (options.sentinel) answer += `\n${options.sentinel}`;
  const events = [];
  if (options.reasoning === 'true') events.push('data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}\r\n\r\n');
  events.push(`data: {"choices":[{"delta":{"content":${JSON.stringify(answer)}}}]}\r\n\r\n`);
  events.push('data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":12,"completion_tokens":6}}\r\n\r\n');
  events.push('data: [DONE]\r\n\r\n');
  res.writeHead(200, { 'content-type': 'text/event-stream' });
  for (const event of events) {
    if (options.split === 'true') for (const char of event) res.write(char);
    else res.write(event);
  }
  res.end();
});
server.listen(0, '127.0.0.1', () => process.stdout.write(`${JSON.stringify({ port: server.address().port })}\n`));
process.on('SIGTERM', () => server.close(() => process.exit(0)));
