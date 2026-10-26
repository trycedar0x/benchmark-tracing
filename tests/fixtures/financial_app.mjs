// Offline cross-repository fixture: imports the actual built app, never its env loader.
import { once } from 'node:events';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';

const [appDir, receiver, mode, scenarioJson = '{}'] = process.argv.slice(2);
// Synthetic scenarios are explicit arguments, never inherited env/config credentials.
const scenario = JSON.parse(scenarioJson);
const callId = scenario.toolCallId ?? 'actual-fixture-call-1';
const load = name => import(pathToFileURL(`${appDir}/dist/${name}.js`).href);
const { createRuntime } = await load('server');
const { createTracing } = await load('tracing');
const { createSecTools, tools } = await load('tools');
const token = 'synthetic-integration-agent-bearer-token-32-bytes-minimum';
const secRequests = [];
const requests = [];
const fixture = createSecTools({
  userAgent: () => 'Offline integration tests@example.invalid', intervalMs: 0,
  fetch: async input => {
    const url = String(input);
    assert.equal(url, 'https://www.sec.gov/files/company_tickers.json');
    secRequests.push(url);
    return Response.json({ '0': { cik_str: 320193, ticker: 'AAPL', title: 'Apple Offline Fixture' } });
  },
});
tools.lookupCompany.execute = fixture.lookupCompany.execute;
let modelCalls = 0;
const model = {
  modelId: scenario.modelId ?? 'offline/injected-financial-model',
  async *stream(call) {
    modelCalls++;
    const serialized = JSON.stringify(call.messages);
    assert.ok(!serialized.includes('EVALUATOR_ONLY_'));
    if (modelCalls === 1) {
      yield { type: 'tool-call', toolCallId: callId, toolName: 'lookupCompany', input: '{"query":"AAPL"}' };
    } else {
      assert.ok(call.messages.some(message => message.role === 'tool' && message.toolCallId === callId && message.content.includes('Apple Offline Fixture')));
      for (const text of scenario.answerChunks ?? ['Apple found using the SEC company directory.']) {
        yield { type: 'text-delta', text };
      }
      if (mode === 'partial') throw new Error('PRIVATE_PROVIDER_FAILURE');
    }
  },
};
const tracing = mode === 'missing' ? undefined : createTracing({
  endpoint: `${receiver}/v1/traces`, baseUrl: receiver, content: scenario.tracingContent ?? 'full',
  secrets: [token, ...(scenario.secrets ?? [])], timeoutMillis: 1500,
});
const runtime = await createRuntime({ localDev: true, origin: 'http://localhost:3000', apiToken: token, model, tracing });
runtime.server.prependListener('request', req => {
  const chunks = [];
  req.on('data', chunk => chunks.push(chunk));
  req.on('end', () => { if (req.url === '/api/chat') requests.push(JSON.parse(Buffer.concat(chunks).toString())); });
});
runtime.server.listen(0, '127.0.0.1');
await once(runtime.server, 'listening');
console.log(JSON.stringify({ url: `http://127.0.0.1:${runtime.server.address().port}` }));
process.once('SIGTERM', async () => {
  runtime.abortAll();
  runtime.server.closeAllConnections();
  await new Promise(resolve => runtime.server.close(resolve));
  await runtime.closeStore();
  await tracing?.shutdown();
  console.log(JSON.stringify({ requests, modelCalls, secRequests }));
});
