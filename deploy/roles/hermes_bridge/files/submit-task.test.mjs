import { test } from 'node:test';
import assert from 'node:assert/strict';
import { requestBody, submit } from './submit-task.mjs';

const company = '11111111-1111-4111-8111-111111111111';
const project = '22222222-2222-4222-8222-222222222222';
const agent = '33333333-3333-4333-8333-333333333333';
const issue = '44444444-4444-4444-8444-444444444444';
const env = { PAPERCLIP_COMPANY_ID: company, PAPERCLIP_AGENT_ID: agent,
  PAPERCLIP_API_URL: 'http://127.0.0.1:13100', PAPERCLIP_BRIDGE_API_KEY: 'synthetic',
  HERMES_SESSION_MESSAGE_ID: '123456789012345678', HERMES_SESSION_PLATFORM: 'discord' };
const options = { title: 'Synthetic work', 'project-id': project, 'assignee-agent-id': agent };

test('event/action idempotency survives retries and credential rotation', () => {
  const key = requestBody(options, env, '').idempotencyKey;
  assert.equal(requestBody({ ...options, title: 'Rephrased retry' }, { ...env, PAPERCLIP_BRIDGE_API_KEY: 'rotated' }, '').idempotencyKey, key);
  for (const [opts, vars] of [[{ ...options, 'action-index': '1' }, env], [options, { ...env, HERMES_SESSION_MESSAGE_ID: '123456789012345679' }]])
    assert.notEqual(requestBody(opts, vars, '').idempotencyKey, key);
  assert.throws(() => requestBody(options, { ...env, HERMES_SESSION_MESSAGE_ID: '' }, ''), /event-id/);
  assert.throws(() => requestBody({ ...options, 'assignee-agent-id': undefined }, env, ''), /assignee/);
});

test('native creation carries idempotency, refuses redirects and does not leak response bodies', async () => {
  let captured;
  const result = await submit(options, env, async (url, init) => {
    captured = { url, init };
    return { status: 200, json: async () => ({ id: issue, status: 'done' }) };
  });
  assert.equal(result.reused, true);
  assert.equal(captured.init.redirect, 'error');
  assert.equal(captured.url.pathname, '/api/companies/' + company + '/issues');
  assert.ok(JSON.parse(captured.init.body).idempotencyKey);
  await assert.rejects(submit(options, env, async () => ({ status: 403, text: async () => 'sensitive' })), /HTTP 403/);
  await assert.rejects(submit(options, env, async () => { throw new Error('sensitive'); }), /same event/);
  await assert.rejects(submit(options, env, async () => ({ status: 201, json: async () => { throw new Error('sensitive response'); } })), /same event/);
});

test('remote cleartext and URLs with credentials cannot receive the token', async () => {
  for (const url of ['http://untrusted.example', 'http://user:pass@127.0.0.1', 'http://127.0.0.1/redirect'])
    await assert.rejects(submit(options, { ...env, PAPERCLIP_API_URL: url }, async () => { assert.fail('request sent'); }), /Bridge URL/);
});
