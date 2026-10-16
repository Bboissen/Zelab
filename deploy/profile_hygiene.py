"""End stale chat contexts through Hermes' native API; preserve every transcript.

Sent over the deployment SSH connection, executed in the guest with system
Python. No model calls, chat messages, SQL writes or upstream patches.
"""

import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


CHAT_SOURCES = {'discord', 'telegram', 'slack', 'mattermost', 'matrix', 'signal',
                'whatsapp', 'email', 'sms', 'feishu', 'dingtalk', 'wecom', 'qq',
                'weixin', 'homeassistant', 'msteams', 'twitch'}


def env_values(path):
    """Read generated env entries as data, never execute a shell or echo values."""
    values = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep and not key.strip().startswith('#'):
                values[key.strip()] = value.strip().strip('\'"')
    return values


def fingerprint(profile, pins):
    digest = hashlib.sha256(('profile-hygiene-v1:' + pins).encode())
    files = [profile / 'SOUL.md', profile / 'config.yaml']
    for folder in ('skills/zelab/zelab', 'skills/paperclip/paperclip-task-bridge'):
        files.extend(sorted(p for p in (profile / folder).rglob('*') if p.is_file()))
    for path in files:
        digest.update(str(path.relative_to(profile)).encode() + b'\0')
        digest.update(path.read_bytes() if path.is_file() else b'')
    return digest.hexdigest()


class Hermes:
    def __init__(self, profile, port):
        self.name = profile.name
        self.base = f'http://127.0.0.1:{port}/p/{urllib.parse.quote(self.name, safe="")}'
        self.key = env_values(profile / '.env').get('API_SERVER_KEY')
        if not self.key:
            raise ValueError(f'{self.name}: native gateway API key is missing; repair with make bot.')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method, path, body=None):
        request = urllib.request.Request(self.base + path, method=method,
                                        headers={'Authorization': 'Bearer ' + self.key,
                                                 'Content-Type': 'application/json'},
                                        data=json.dumps(body).encode() if body is not None else None)
        deadline = time.monotonic() + 30
        while True:
            try:
                with self.opener.open(request, timeout=5) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                raise ValueError(f'{self.name}: native Hermes API refused {method} (HTTP {error.code}).') from None
            except (OSError, ValueError):
                if path == '/health/detailed' and time.monotonic() < deadline:
                    time.sleep(0.5)
                    continue
                raise ValueError(f'{self.name}: native Hermes API is unavailable; rerun make after it recovers.') from None

    def idle(self):
        health = self.request('GET', '/health/detailed')
        queues = ((health.get('readiness') or {}).get('checks') or {}).get('background_queues') or {}
        if (health.get('gateway_busy') or health.get('active_agents', 0)
                or any(queues.get(k, 0) for k in ('active_api_runs', 'process_completions', 'active_delegations'))):
            raise ValueError('Hermes is busy; let its work finish, then rerun make to refresh bot context.')

    def chats(self):
        offset, seen = 0, set()
        while True:
            page = self.request('GET', f'/api/sessions?limit=200&offset={offset}&include_children=true')
            for session in page['data']:
                sid = session['id']
                if sid not in seen and session.get('source') in CHAT_SOURCES and not session.get('end_reason'):
                    seen.add(sid)
                    yield session
            if not page.get('has_more'):
                return
            offset += 200
            if offset > 1_000_000:
                raise ValueError(f'{self.name}: too many sessions to reconcile in one pass.')


def reconcile(home, pins, action='apply', client=Hermes):
    profiles = sorted(p for p in (home / 'profiles').glob('*') if (p / 'config.yaml').is_file())
    port = env_values(home / '.env').get('API_SERVER_PORT', '8642')
    clients = {p.name: client(p, port) for p in profiles}
    for api in clients.values():
        api.idle()
    if action == 'check':
        return {'changed': 0, 'ended': 0}
    receipt = home / '.zelab-profile-context.json'
    previous = json.loads(receipt.read_text()) if receipt.is_file() else {}
    current = {p.name: fingerprint(p, pins) for p in profiles}
    if current == previous:
        return {'changed': 0, 'ended': 0}
    changed = [name for name in current if previous.get(name) != current[name]]
    ended = 0
    for name in changed:
        api = clients[name]
        sessions = list(api.chats())
        api.idle()
        for session in sessions:
            api.request('PATCH', '/api/sessions/' + urllib.parse.quote(session['id'], safe=''),
                        {'end_reason': 'session_reset'})
            ended += 1
    # A failed pass leaves the old receipt so the next make retries. Ended rows
    # are skipped, and native routing notices the boundary on the next message.
    temporary = receipt.with_suffix('.tmp')
    temporary.write_text(json.dumps(current, sort_keys=True) + '\n')
    temporary.chmod(0o600)
    temporary.replace(receipt)
    return {'changed': len(changed), 'ended': ended}


if __name__ == '__main__':
    try:
        result = reconcile(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
    except ValueError as error:
        result = {'error': str(error)}
    except Exception:
        result = {'error': 'Bot context reconciliation failed; repair the native gateway or private context receipt, then rerun make.'}
    print(json.dumps(result))
