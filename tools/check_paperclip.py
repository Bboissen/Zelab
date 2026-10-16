#!/usr/bin/env python3
"""Check native Paperclip authentication and optional recovery in disposable state."""

import argparse
from contextlib import contextmanager
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from jinja2 import Environment, StrictUndefined

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'implementation'))
from backup import pipeline
from restore import authenticate, inspect_archive
from runtime import recover_on_interrupt, run
from artifacts import record


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


@contextmanager
def private_keyring(root):
    directory = root / 'gnupg'
    directory.mkdir(mode=0o700)
    previous = os.environ.get('GNUPGHOME')
    os.environ['GNUPGHOME'] = str(directory)
    try:
        yield
    finally:
        try:
            subprocess.run(['rtk', 'proxy', 'gpgconf', '--homedir', str(directory),
                            '--kill', 'gpg-agent'], check=True, capture_output=True, timeout=15)
        finally:
            if previous is None:
                os.environ.pop('GNUPGHOME', None)
            else:
                os.environ['GNUPGHOME'] = previous


def configure(root, api_port, db_port):
    instance = root / 'instances/default'
    (instance / 'secrets').mkdir(parents=True, exist_ok=True)
    key = instance / 'secrets/master.key'
    if not key.exists():
        key.write_text(secrets.token_hex(32) + '\n')
    template = Path(__file__).resolve().parents[1] / 'deploy/roles/paperclip/templates/config.json.j2'
    renderer = Environment(undefined=StrictUndefined)
    renderer.filters['to_json'] = json.dumps
    config = renderer.from_string(template.read_text()).render(
        paperclip_home=str(root), paperclip_db_port=db_port,
        paperclip_port=api_port, paperclip_allowed_hosts=['127.0.0.1'],
    )
    (instance / 'config.json').write_text(config)


def wait_for_database_stop(runtime, database):
    """A wrapper exit is not evidence that its daemonized database has exited."""
    marker = database / 'postmaster.pid'
    deadline = time.monotonic() + 15
    while marker.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    if not marker.exists():
        return
    # PostgreSQL's fast shutdown also handles clients left by a failed wrapper.
    # Signal only the postmaster whose executable and data directory we own.
    lines = marker.read_text().splitlines()
    pid = int(lines[0])
    process = Path('/proc') / str(pid)
    expected = runtime / 'node_modules/@embedded-postgres/linux-x64/native/bin/postgres'
    if (pid <= 1 or len(lines) < 2 or Path(lines[1]) != database
            or not process.exists() or process.stat().st_uid != os.geteuid()
            or (process / 'exe').resolve() != expected.resolve()):
        raise RuntimeError('Fixture database ownership could not be verified; refusing to archive or signal it')
    command = (process / 'cmdline').read_bytes().split(b'\0')
    if os.fsencode(database) not in command:
        raise RuntimeError('Fixture database command does not match its data directory')
    os.kill(pid, signal.SIGINT)
    deadline = time.monotonic() + 15
    while marker.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    if marker.exists():
        raise RuntimeError('Fixture database did not stop cleanly; refusing to archive or remove it')


@contextmanager
def server(runtime, node, root, *, signup=False):
    api_port, db_port = free_port(), free_port()
    while db_port == api_port:
        db_port = free_port()
    configure(root, api_port, db_port)
    jwt = root / 'jwt.key'
    if not jwt.exists():
        jwt.write_text(secrets.token_hex(32))
    instance = root / 'instances/default'
    # Do not inherit provider credentials or a personal Paperclip configuration.
    environment = {
        'PATH': f'{node.parent}:{Path(shutil.which("rtk")).parent}:/usr/local/bin:/usr/bin:/bin', 'HOME': str(root),
        'PAPERCLIP_HOME': str(root), 'PAPERCLIP_CONFIG': str(instance / 'config.json'),
        'PAPERCLIP_INSTANCE_ID': 'default', 'PAPERCLIP_DEPLOYMENT_MODE': 'authenticated',
        'PAPERCLIP_DEPLOYMENT_EXPOSURE': 'private', 'PAPERCLIP_AUTH_DISABLE_SIGN_UP': str(not signup).lower(),
        'PAPERCLIP_BIND': 'loopback', 'PORT': str(api_port), 'PAPERCLIP_ALLOWED_HOSTNAMES': '127.0.0.1',
        'PAPERCLIP_UI_DEV_MIDDLEWARE': 'false', 'PAPERCLIP_MIGRATION_PROMPT': 'never',
        'PAPERCLIP_MIGRATION_AUTO_APPLY': 'true', 'PAPERCLIP_AGENT_JWT_SECRET': jwt.read_text(),
    }
    entry = runtime / 'node_modules/@paperclipai/server/dist/index.js'
    with (root / 'server.log').open('ab') as log:
        process = subprocess.Popen(['rtk', 'proxy', str(node), str(entry)],
                                   cwd=root, env=environment, stdout=log, stderr=log, start_new_session=True)
        try:
            anonymous = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError('Native server exited before health was ready')
                try:
                    with anonymous.open(f'http://127.0.0.1:{api_port}/api/health', timeout=2) as response:
                        health = json.load(response)
                    break
                except (OSError, ValueError):
                    time.sleep(1)
            else:
                raise RuntimeError('Native server startup timed out')
            if (health.get('deploymentMode'), health.get('deploymentExposure')) != ('authenticated', 'private'):
                raise RuntimeError('Native server did not honor private authentication mode')
            try:
                anonymous.open(f'http://127.0.0.1:{api_port}/api/companies', timeout=2)
                raise RuntimeError('Anonymous company access unexpectedly succeeded')
            except urllib.error.HTTPError as error:
                if error.code != 403:
                    raise RuntimeError('Unexpected anonymous API response') from None
            yield Client(api_port), environment
        finally:
            # The wrapper may have exited while a database child is still alive.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            else:
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            wait_for_database_stop(runtime, instance / 'db')


class Client:
    def __init__(self, port):
        self.base = f'http://127.0.0.1:{port}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, body=None, method=None):
        request = urllib.request.Request(self.base + '/api' + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method, headers={'Origin': self.base, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # Responses may contain credentials. Print neither body nor headers.
            route = re.sub(r'/invites/[^/]+', '/invites/[redacted]', path)
            raise RuntimeError(f'Native fixture request failed: {request.method} {route} ({error.code})') from None


def login(client, password):
    client.request('/auth/sign-in/email', {'email': 'recovery@example.invalid', 'password': password})


def seed(client, runtime, node, root, environment, password):
    client.request('/auth/sign-up/email', {'email': 'recovery@example.invalid',
                   'password': password, 'name': 'Recovery fixture'})
    claim = subprocess.run(['rtk', 'proxy', str(node),
        str(runtime / 'node_modules/paperclipai/dist/index.js'), 'auth', 'bootstrap-ceo',
        '--config', str(root / 'instances/default/config.json')], env=environment,
        cwd=root, capture_output=True, text=True, timeout=60)
    token = re.search(r'pcp_bootstrap_[a-f0-9]+', claim.stdout)
    if claim.returncode or not token:
        raise RuntimeError('Native fixture administrator bootstrap failed')
    client.request('/invites/' + token.group() + '/accept', {'requestType': 'human'})
    company = client.request('/companies', {'name': 'Recovery fixture'})
    issue = client.request(f'/companies/{company["id"]}/issues',
                           {'title': 'Recovery checkpoint', 'description': 'Synthetic task; no agent or inference',
                            'status': 'backlog'})
    (root / 'fixture-note.md').write_text('Checkpoint note\n')
    return company['id'], issue['id']


def verify_state(client, root, password, company, issue, status='backlog'):
    login(client, password)
    if company not in {item['id'] for item in client.request('/companies')}:
        raise RuntimeError('Recovered owner cannot access the original company')
    task = client.request('/issues/' + issue)
    if task['title'] != 'Recovery checkpoint' or task['status'] != status:
        raise RuntimeError('Task state did not survive the native restart/restore')
    if (root / 'fixture-note.md').read_text() != 'Checkpoint note\n':
        raise RuntimeError('Checkpoint note did not survive restore')


def project_retirement_check(client, runtime, node, root, environment):
    """Exercise the deployer's actual archive payload against the pinned server."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
    from bots import Bots, project_replacements

    company, _ = seed(client, runtime, node, root, environment, secrets.token_urlsafe(32))
    old = client.request(f'/companies/{company}/projects',
                         {'name': 'Retired fixture', 'description': 'Vault folder: Projects/old-lab/'})
    target = client.request(f'/companies/{company}/projects',
                            {'name': 'Current fixture', 'description': 'Vault folder: Projects/current/'})
    issue = client.request(f'/companies/{company}/issues',
                           {'title': 'Retained retirement fixture', 'description': 'Keep this synthetic history.',
                            'projectId': old['id'], 'status': 'done'})

    class Owner:
        def __init__(self):
            self.company = company
            self.archives = 0

        def call(self, method, path, body=None):
            if method == 'PATCH' and path.startswith('/projects/'):
                self.archives += 1
            return client.request(path, body, method=method)

    runner = Bots.__new__(Bots)
    runner.paperclip = Owner()
    site = {'zelab_project_replacements': {'old-lab': 'current'}}
    projects = client.request(f'/companies/{company}/projects')
    replacements = project_replacements(site, projects)
    runner.retire_projects(projects, replacements, check_only=True)
    if client.request('/projects/' + old['id']).get('archivedAt'):
        raise RuntimeError('Retirement preflight changed the project')
    runner.retire_projects(projects, replacements)
    archived = client.request('/projects/' + old['id'])
    if not archived.get('archivedAt'):
        raise RuntimeError('Native project archive was not retained')
    if client.request('/projects/' + target['id']).get('archivedAt'):
        raise RuntimeError('Replacement project was unexpectedly archived')
    kept = client.request('/issues/' + issue['id'])
    if (kept['projectId'], kept['status'], kept['description']) != (old['id'], 'done', issue['description']):
        raise RuntimeError('Project retirement changed existing ticket history')
    # A new process reads fresh native state after an interrupted/completed pass.
    projects = client.request(f'/companies/{company}/projects')
    runner.retire_projects(projects, project_replacements(site, projects))
    if runner.paperclip.archives != 1:
        raise RuntimeError('Repeated retirement did not converge without another archive write')
    print('PASS: native project retirement, retained ticket history, untouched replacement and idempotent resume.')


def recovery_check(runtime, previous, node, folder):
    original = folder / 'original'
    password = secrets.token_urlsafe(32)
    with server(previous or runtime, node, original, signup=True) as (client, environment):
        company, issue = seed(client, previous or runtime, node, original, environment, password)
    key_bytes = (original / 'instances/default/secrets/master.key').read_bytes()
    key = folder / 'recovery.key'
    key.write_text(secrets.token_hex(32))
    encrypted = folder / 'checkpoint.tar.gpg'
    gpg = ['gpg', '--batch', '--pinentry-mode', 'loopback', '--no-symkey-cache', '--passphrase-file', key]
    pipeline(['tar', '--sparse', '--acls', '--xattrs', '-C', original, '-cf', '-',
              *sorted(path.name for path in original.iterdir())],
             [*gpg, '--symmetric', '--cipher-algo', 'AES256', '--output', encrypted], folder / 'backup.log')
    with server(runtime, node, original) as (client, _):
        verify_state(client, original, password, company, issue)
        client.request('/issues/' + issue, {'status': 'done'}, method='PATCH')
        verify_state(client, original, password, company, issue, status='done')
    restored = folder / 'restored'
    restored.mkdir()
    plaintext = folder / 'authenticated.tar'
    try:
        authenticate(encrypted, key, plaintext, shutil.disk_usage(folder).free - 2**30)
        _, required = inspect_archive(plaintext)
        if required + 2**30 > shutil.disk_usage(folder).free:
            raise RuntimeError('Insufficient fixture restore space')
        run('tar', '--extract', '--sparse', '--acls', '--xattrs', '-C', restored, '-f', plaintext)
        run('tar', '--compare', '--acls', '--xattrs', '-C', restored, '-f', plaintext)
    finally:
        plaintext.unlink(missing_ok=True)
    if (restored / 'instances/default/secrets/master.key').read_bytes() != key_bytes:
        raise RuntimeError('Recovered encryption key differs')
    with server(previous or runtime, node, restored) as (client, _):
        verify_state(client, restored, password, company, issue)
        client.request('/issues/' + issue, {'status': 'done'}, method='PATCH')
        verify_state(client, restored, password, company, issue, status='done')
    print('PASS: native owner/company/task/note persistence, encrypted restore, retained key and post-restore task update.')
    if previous:
        print('PASS: supplied previous runtime → candidate runtime → restored previous runtime.')
    print('Worker execution, inference, Discord delivery and whole-site recovery are not exercised by this fixture.')


class McpClient:
    """Minimal stdio client for the pinned Paperclip MCP server, as Hermes starts it: no run ID."""

    def __init__(self, node, mcp_runtime, env):
        server = mcp_runtime / 'node_modules/@paperclipai/mcp-server/dist/stdio.js'
        self.process = subprocess.Popen([str(node), str(server)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env={'PATH': '/usr/bin:/bin', **env})
        self.next_id = 0
        self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                'clientInfo': {'name': 'zelab-check', 'version': '1'}})
        self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()

    def rpc(self, method, params):
        self.next_id += 1
        self.send({'jsonrpc': '2.0', 'id': self.next_id, 'method': method, 'params': params})
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError('Paperclip MCP server exited')
            message = json.loads(line)
            if message.get('id') == self.next_id:
                return message

    def call(self, name, arguments):
        """The tool's JSON result, or None when Paperclip refused it."""
        result = self.rpc('tools/call', {'name': name, 'arguments': arguments}).get('result') or {}
        text = (result.get('content') or [{}])[0].get('text', '')
        try:
            data = json.loads(text)
        except ValueError:
            return None
        return None if result.get('isError') or (isinstance(data, dict) and 'error' in data) else data

    def close(self):
        self.process.terminate()
        self.process.wait(timeout=10)


def chat_handoff_check(client, runtime, mcp_runtime, node, root, environment):
    """Exercise the chat flow through the MCP server and one reply run, without inference or chat."""
    company, _ = seed(client, runtime, node, root, environment, secrets.token_urlsafe(32))
    project = client.request(f'/companies/{company}/projects', {'name': 'Handoff fixture'})
    lead = client.request(f'/companies/{company}/agents', {
        'name': 'Fixture lead', 'role': 'general', 'adapterType': 'process',
        'adapterConfig': {'command': '/usr/bin/true'},
        'runtimeConfig': {'heartbeat': {'enabled': False, 'wakeOnDemand': False}}})
    script = root / 'reply.py'
    state = root / 'reply.json'
    bot = client.request(f'/companies/{company}/agents', {
        'name': 'Fixture bot', 'role': 'general', 'adapterType': 'process',
        'adapterConfig': {'command': sys.executable, 'args': [str(script)], 'cwd': str(root), 'timeoutSec': 30},
        'runtimeConfig': {'heartbeat': {'enabled': False, 'wakeOnDemand': True, 'maxConcurrentRuns': 1}}})
    key = client.request(f'/agents/{bot["id"]}/keys', {'name': 'Synthetic scoped task key',
        'scope': {'kind': 'task_bridge', 'projectIds': [project['id']], 'allowedAssigneeAgentIds': [lead['id']]}})
    env = {'PAPERCLIP_API_URL': client.base, 'PAPERCLIP_API_KEY': key['token'],
           'PAPERCLIP_COMPANY_ID': company, 'PAPERCLIP_AGENT_ID': bot['id']}

    def runs():
        return client.request(f'/companies/{company}/heartbeat-runs?agentId={bot["id"]}')

    # The chat: the exact tool calls the generated skill describes, with no Paperclip run.
    chat = McpClient(node, mcp_runtime, env)
    try:
        tools = {tool['name'] for tool in chat.rpc('tools/list', {})['result']['tools']}
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
        from bots import MCP_TOOLS, skill
        from run import load_yaml
        if not set(MCP_TOOLS) <= tools:
            raise RuntimeError('Pinned MCP server lacks a tool the bots are configured to use')
        job = chat.call('paperclipCreateIssue', {'projectId': project['id'], 'assigneeAgentId': lead['id'],
                        'status': 'todo', 'title': 'Is the media server up?', 'description': 'Depth: quick.'})
        reply = job and chat.call('paperclipCreateIssue', {'projectId': project['id'], 'assigneeAgentId': bot['id'],
                                  'status': 'blocked', 'blockedByIssueIds': [job['id']], 'title': 'Reply: media server',
                                  'description': 'Reply to: discord:1\nAsked by: Fixture, not technical\nQuestion: Is it up?'})
        if not reply:
            raise RuntimeError('Task key could not file the job and the waiting reply ticket outside a run')
        if not chat.call('paperclipCreateIssue', {'projectId': project['id'], 'title': 'Chat delivery failed: fixture',
                                                  'description': 'Unassigned delivery report'}):
            raise RuntimeError('Task key could not file an unassigned delivery report')
        if (chat.call('paperclipGetIssue', {'issueId': job['id']}) or {}).get('status') != 'todo':
            raise RuntimeError('Task key could not read the job it filed')
        if chat.call('paperclipListIssues', {}) is not None:
            raise RuntimeError('Task key unexpectedly listed company-wide issues')
        if chat.call('paperclipUpdateIssue', {'issueId': reply['id'], 'status': 'done'}) is not None:
            raise RuntimeError('Task key unexpectedly changed a ticket outside a Paperclip run')
    finally:
        chat.close()
    waiting = client.request('/issues/' + reply['id'])
    if waiting['status'] != 'blocked' or [b['id'] for b in waiting.get('blockedBy', [])] != [job['id']]:
        raise RuntimeError('Reply ticket does not wait on the job')
    time.sleep(3)
    if runs():
        raise RuntimeError('The waiting reply ticket woke the bot before the job finished')

    # The reply run: read the result through the MCP server, then the skill's exact close command.
    site = load_yaml(Path(__file__).resolve().parents[1] / 'config/defaults.yaml')
    close = next(line.strip() for line in skill(site, 'fixture', {}, []).splitlines() if 'update-status' in line)
    helper = runtime / 'node_modules/@paperclipai/hermes-paperclip-adapter/skills/paperclip-task-bridge/paperclip-task.mjs'
    if not helper.is_file():
        raise RuntimeError('Pinned task-bridge helper is missing')
    close = close.split(' update-status ', 1)[1].replace('<ticket id>', reply['id'])
    script.write_text(f'''import json, os, shlex, subprocess, sys
sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
from check_paperclip import McpClient
from pathlib import Path
env = json.loads(os.environ['FIXTURE_ENV'])
mcp = McpClient(Path({str(node)!r}), Path({str(mcp_runtime)!r}), env)
comments = mcp.call('paperclipListComments', {{'issueId': {job['id']!r}}}) or []
mcp.close()
result = {{'read': any('91%' in (c.get('body') or '') for c in comments)}}
command = [{str(node)!r}, {str(helper)!r}, 'update-status', *shlex.split({close!r})]
done = subprocess.run(command, capture_output=True, text=True, env={{'PATH': '/usr/bin:/bin',
    'PAPERCLIP_API_URL': env['PAPERCLIP_API_URL'], 'PAPERCLIP_BRIDGE_API_KEY': env['PAPERCLIP_API_KEY'],
    'PAPERCLIP_RUN_ID': os.environ['PAPERCLIP_RUN_ID']}})
result['closed'] = done.returncode == 0
Path({str(state)!r}).write_text(json.dumps(result))
''')
    config = {**bot['adapterConfig'], 'env': {'FIXTURE_ENV': json.dumps(env)}}
    client.request('/agents/' + bot['id'], {'adapterConfig': config}, method='PATCH')
    client.request('/issues/' + job['id'], {'status': 'done', 'comment': 'Up; one disk is 91% full.'}, method='PATCH')
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and not state.exists():
        time.sleep(0.5)
    # Paperclip schedules a disposition repair at once when a run leaves its ticket open.
    time.sleep(10)
    result = json.loads(state.read_text()) if state.exists() else {}
    if not result.get('read'):
        raise RuntimeError('Reply run could not read the job result through the MCP server')
    if not result.get('closed') or client.request('/issues/' + reply['id'])['status'] != 'done':
        raise RuntimeError('Generated close command did not finish the reply ticket')
    reasons = [(run['status'], run.get('contextSnapshot', {}).get('wakeReason')) for run in runs()]
    if reasons != [('succeeded', 'issue_blockers_resolved')]:
        raise RuntimeError(f'Unexpected bot runs during the handoff: {reasons}')
    print('PASS: chat-side MCP calls file the job and a waiting reply ticket; no run, no company-wide reads.')
    print('PASS: the finished job wakes the bot once; it reads the result and the generated command closes the ticket.')
    print('Inference and Discord delivery: NOT RUN.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True, help='Directory containing installed node_modules')
    parser.add_argument('--node', type=Path, required=True, help='Pinned Node executable')
    parser.add_argument('--recovery', action='store_true', help='Also verify native task state through encrypted restore')
    parser.add_argument('--project-retirement', action='store_true', help='Verify deployed project retirement against the native API')
    parser.add_argument('--chat-handoff', action='store_true', help='Verify the MCP chat handoff without inference or chat')
    parser.add_argument('--mcp-runtime', type=Path, help='Installed Paperclip MCP server (with --chat-handoff)')
    parser.add_argument('--previous-runtime', type=Path, help='Verify update and snapshot rollback against this installed runtime')
    args = parser.parse_args()
    if args.previous_runtime and not args.recovery:
        parser.error('--previous-runtime requires --recovery')
    if args.chat_handoff and not (args.mcp_runtime and (args.mcp_runtime / 'node_modules/@paperclipai/mcp-server').is_dir()):
        parser.error('--chat-handoff needs --mcp-runtime with the installed MCP server')
    if sum((args.project_retirement, args.recovery, args.chat_handoff)) > 1:
        parser.error('Run --project-retirement, --chat-handoff and --recovery separately')
    for runtime in [args.runtime, *([args.previous_runtime] if args.previous_runtime else [])]:
        if not (runtime / 'node_modules/@paperclipai/server/dist/index.js').is_file():
            parser.error('Supply installed Paperclip runtimes')
    if not args.node.is_file() or not shutil.which('rtk'):
        parser.error('Supply the pinned Node executable and install RTK')
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix='zelab-paperclip-check-'))
    record(root)
    success = False
    try:
        with recover_on_interrupt():
            if args.recovery:
                with private_keyring(root):
                    recovery_check(args.runtime.resolve(), args.previous_runtime.resolve() if args.previous_runtime else None,
                                   args.node.resolve(), root)
            else:
                with server(args.runtime.resolve(), args.node.resolve(), root / 'state',
                            signup=args.project_retirement or args.chat_handoff) as (client, environment):
                    if args.project_retirement:
                        project_retirement_check(client, args.runtime.resolve(), args.node.resolve(),
                                                 root / 'state', environment)
                    if args.chat_handoff:
                        chat_handoff_check(client, args.runtime.resolve(), args.mcp_runtime.resolve(),
                                           args.node.resolve(), root / 'state', environment)
                print('PASS: fresh native database/migrations, authenticated private health and anonymous API denial.')
        success = True
    finally:
        if success:
            shutil.rmtree(root)
        else:
            print(f'Private fixture diagnostics: {root}')


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
