"""Read-only checks of installed native bridge credentials and configuration.

Called by doctor only after both bridges have been provisioned. Never submits
tasks, follows redirects, prints response bodies or changes application settings.
"""

import json
from dataclasses import dataclass
from pathlib import Path
import urllib.error
import urllib.request

from filter_plugins.bridge_security import bridge_identity, gateway_contract, submission_contract
from passwords import read_private


@dataclass(frozen=True)
class Inspection:
    ready: bool
    detail: str
    pending: bool = False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def inspect(site, password_dir=None):
    private = Path(site['private_dir'])
    expected = ('paperclip-worker-ids.json', 'paperclip-hermes-ids.json',
                'paperclip-hermes-submission.key', 'paperclip-hermes-gateway.key',
                'paperclip-bridge-ids.json')
    if not all((private / name).is_file() for name in expected):
        return None  # New/interrupted installation: provisioning completes it.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    base = f"http://127.0.0.1:{site['paperclip_port']}/api"

    def request(path, headers, body=None):
        req = urllib.request.Request(base + path, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        with opener.open(req, timeout=5) as response:
            return response.headers, json.loads(response.read(4 * 1024 * 1024))

    step = 'Read retained bridge identities and credentials'
    try:
        workers = json.loads((private / expected[0]).read_text())
        remote = json.loads((private / expected[1]).read_text())
        principal = json.loads((private / expected[4]).read_text())
        company, ops = workers['company_id'], workers['workers']['ops']
        step = 'Authenticate both native bridge keys'
        _, submission = request('/agents/me', {'Authorization': 'Bearer ' + (private / expected[2]).read_text().strip()})
        _, callback = request('/agents/me', {'Authorization': 'Bearer ' + (private / expected[3]).read_text().strip()})
        if not (submission['id'] != ops['agent_id']
                and bridge_identity(submission, company, principal['agent_id'], ops['project_id'], [ops['agent_id']])
                and bridge_identity(callback, company, remote['agent_id'], ops['project_id'])):
            return Inspection(False, 'Bridge credential identity or scope differs from the declared site. '
                              'Reconcile the native settings, then rerun make. No task was dispatched.')
        owner_password = Path(password_dir or private) / 'paperclip-owner.password'
        if password_dir is None and not owner_password.exists():
            return Inspection(False, 'Bridge keys verified; native agent settings await the Paperclip owner login. '
                              'Run make in your terminal and enter the current password there.', pending=True)
        step = 'Read the Paperclip owner password for this run'
        password = read_private(owner_password)
        step = 'Authenticate the Paperclip owner for native agent checks'
        headers, _ = request('/auth/sign-in/email', {'Origin': base.removesuffix('/api'),
                                                    'Content-Type': 'application/json'},
                             {'email': site['paperclip_owner_email'],
                              'password': password})
        cookies = '; '.join(value.split(';', 1)[0] for value in headers.get_all('Set-Cookie', []))
        if not cookies:
            return Inspection(False, 'Paperclip owner login returned no session; rerun make with the current owner password.')
        step = 'Read the native submission agent settings'
        _, submission_agent = request('/agents/' + principal['agent_id'], {'Cookie': cookies})
        if not submission_contract(submission_agent, company):
            return Inspection(False, 'Native submission agent settings drifted; reconcile them, then rerun make. '
                              'No task was dispatched.')
        step = 'Read the native Hermes gateway agent settings'
        _, agent = request('/agents/' + remote['agent_id'], {'Cookie': cookies})
        valid = gateway_contract(agent, company, f"http://127.0.0.1:{site['guest_api_port']}",
                                 f"http://10.0.2.2:{site['paperclip_port']}", remote['secret_id'])
        return Inspection(valid, 'Native bridge credentials and agent settings verified.' if valid else
                          'Native Hermes gateway settings drifted; reconcile them, then rerun make. '
                          'No task was dispatched.')
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        # Never include exception strings: HTTP bodies and parser errors can contain credentials.
        return Inspection(False, step + ' failed; check retained credentials and service availability, '
                          'then rerun make. No task was dispatched.')
