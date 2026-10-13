// Minimal native Paperclip issue-create client. No broker, queue or local state.
// The upstream helper remains installed unchanged for reads/comments/status.
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function requestBody(options, env, description) {
  const company = env.PAPERCLIP_COMPANY_ID;
  const project = options['project-id'];
  const agent = options['assignee-agent-id'];
  if (![company, project, agent].every(x => typeof x === 'string' && uuid.test(x)))
    throw new Error('Valid company, project and assignee IDs are required.');
  if (!options.title?.trim() || options.title.length > 512)
    throw new Error('A title of 1–512 characters is required.');
  if (typeof description !== 'string' || description.length > 65536)
    throw new Error('The description must be text of at most 64 KiB.');
  const action = options['action-index'] ?? '0';
  if (!/^\d{1,4}$/.test(action)) throw new Error('action-index must be an integer from 0 to 9999.');
  // Hermes injects this task-local message ID into each terminal command. Do
  // not substitute a session ID, timestamp, random UUID or a hash of the title:
  // those either duplicate retries or collapse intentional repeated requests.
  const event = options['event-id'] || env.HERMES_SESSION_MESSAGE_ID;
  const platform = env.HERMES_SESSION_PLATFORM || 'explicit';
  if (typeof event !== 'string' || !event.trim() || event.length > 256)
    throw new Error('Native message ID is unavailable; supply a stable originating event-id.');
  const identity = JSON.stringify([company, project, platform, event, Number(action)]);
  const idempotencyKey = 'zelab-event-' + createHash('sha256').update(identity).digest('hex');
  return { title: options.title.trim(), description, projectId: project,
           assigneeAgentId: agent, idempotencyKey, status: 'todo' };
}

export async function submit(options, env = process.env, fetcher = fetch) {
  const description = options['description-file']
    ? await readFile(options['description-file'], 'utf8') : (options.description || '');
  if (options.description && options['description-file'])
    throw new Error('Choose description or description-file.');
  const body = requestBody(options, env, description);
  const key = env.PAPERCLIP_BRIDGE_API_KEY;
  if (!key) throw new Error('The scoped bridge credential is missing.');
  let base;
  try { base = new URL(env.PAPERCLIP_API_URL); } catch { throw new Error('Invalid bridge URL.'); }
  if (base.username || base.password || base.search || base.hash || base.pathname !== '/' ||
      !['http:', 'https:'].includes(base.protocol) ||
      (base.protocol === 'http:' && !['127.0.0.1', '10.0.2.2', '[::1]'].includes(base.hostname)))
    throw new Error('Bridge URL must be a local HTTP or authenticated HTTPS origin.');
  const url = new URL('/api/companies/' + env.PAPERCLIP_COMPANY_ID + '/issues', base);
  let response;
  try {
    response = await fetcher(url, {
      method: 'POST', redirect: 'error', signal: AbortSignal.timeout(30000),
      headers: { 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error('No task acknowledgement received. Retry with the same event and action index.');
  }
  if (![200, 201].includes(response.status))
    throw new Error('Paperclip rejected task creation (HTTP ' + response.status + ').');
  let issue;
  try { issue = await response.json(); }
  catch { throw new Error('No valid task acknowledgement received. Retry with the same event and action index.'); }
  if (!issue || !uuid.test(issue.id || '')) throw new Error('Paperclip returned no valid task identity.');
  return { id: issue.id, identifier: issue.identifier, status: issue.status,
           reused: response.status === 200, idempotencyKey: body.idempotencyKey };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const { values } = parseArgs({ options: Object.fromEntries([
      'title', 'description', 'description-file', 'project-id', 'assignee-agent-id',
      'event-id', 'action-index',
    ].map(name => [name, { type: 'string' }])), strict: true });
    console.log(JSON.stringify(await submit(values)));
  } catch (error) {
    // Do not echo URLs, response bodies, request headers, or Node parseArgs
    // errors (which may contain an accidentally supplied secret argument).
    console.error(error.code ? 'Invalid task arguments; use the installed bridge guidance.' : error.message);
    process.exitCode = 1;
  }
}
