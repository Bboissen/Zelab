"""Exercise doctor against an authenticated native API fixture without legacy passwords."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

import bridge_check


class BridgeCheckTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.private = Path(folder.name) / 'private'
        self.runtime = Path(folder.name) / 'runtime'
        self.private.mkdir(mode=0o700)
        self.runtime.mkdir(mode=0o700)
        self.calls = []
        self.login_status = 200
        self.identities = {
            'submission-key': {'id': 'submission', 'companyId': 'company', 'keyScope': {
                'kind': 'task_bridge', 'projectIds': ['project'], 'allowedAssigneeAgentIds': ['ops']}},
            'gateway-key': {'id': 'gateway', 'companyId': 'company', 'keyScope': {
                'kind': 'task_bridge', 'projectIds': ['project']}},
        }
        permissions = {'canCreateAgents': False, 'canCreateSkills': False,
                       'canAssignTasks': True, 'trustPreset': 'standard'}
        self.agents = {
            'submission': {'companyId': 'company', 'adapterType': 'process',
                           'adapterConfig': {'command': '/usr/bin/false'},
                           'runtimeConfig': {'heartbeat': {'enabled': False, 'wakeOnDemand': False}},
                           'permissions': permissions},
            'gateway': {'companyId': 'company', 'adapterType': 'hermes_gateway',
                        'adapterConfig': {'apiBaseUrl': 'http://127.0.0.1:18642',
                                          'apiKey': {'type': 'secret_ref', 'secretId': 'secret', 'version': 'latest'},
                                          'sessionKeyStrategy': 'issue'}, 'permissions': permissions},
        }
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, data, status=200, cookie=False):
                encoded = json.dumps(data).encode()
                self.send_response(status)
                if cookie:
                    self.send_header('Set-Cookie', 'session=fixture-session; HttpOnly; Path=/')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self):
                fixture.calls.append(('GET', self.path))
                if self.path == '/api/agents/me':
                    token = self.headers.get('Authorization', '').removeprefix('Bearer ')
                    self.respond(fixture.identities.get(token, {}))
                elif self.headers.get('Cookie') == 'session=fixture-session':
                    self.respond(fixture.agents.get(self.path.rsplit('/', 1)[-1], {}))
                else:
                    self.respond({'error': 'fixture-response-secret'}, 401)

            def do_POST(self):
                fixture.calls.append(('POST', self.path))
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if (self.path == '/api/auth/sign-in/email'
                        and body == {'email': 'owner@example.test', 'password': 'fixture-owner-password'}):
                    self.respond({'message': 'fixture-response-secret'}, fixture.login_status,
                                 cookie=fixture.login_status == 200)
                else:
                    self.respond({'error': 'fixture-response-secret'}, 401)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        port = self.server.server_port
        self.agents['gateway']['adapterConfig']['paperclipApiUrl'] = f'http://10.0.2.2:{port}'
        self.site = {'private_dir': str(self.private), 'paperclip_port': port,
                     'guest_api_port': 18642, 'paperclip_owner_email': 'owner@example.test'}
        for name, content in {
            'paperclip-worker-ids.json': {'company_id': 'company', 'workers': {
                'ops': {'agent_id': 'ops', 'project_id': 'project'}}},
            'paperclip-hermes-ids.json': {'agent_id': 'gateway', 'secret_id': 'secret'},
            'paperclip-bridge-ids.json': {'agent_id': 'submission'},
        }.items():
            (self.private / name).write_text(json.dumps(content))
        for name, value in [('paperclip-hermes-submission.key', 'submission-key'),
                            ('paperclip-hermes-gateway.key', 'gateway-key')]:
            (self.private / name).write_text(value)
        self.password(self.runtime)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def password(self, directory, value='fixture-owner-password'):
        path = directory / 'paperclip-owner.password'
        path.write_text(value)
        path.chmod(0o600)

    def test_fresh_install_verifies_native_contracts_using_only_ram_password(self):
        result = bridge_check.inspect(self.site, self.runtime)
        self.assertTrue(result.ready, result.detail)
        self.assertFalse(result.pending)
        self.assertFalse((self.private / 'paperclip-owner.password').exists())
        self.assertEqual(self.calls, [('GET', '/api/agents/me'), ('GET', '/api/agents/me'),
                                     ('POST', '/api/auth/sign-in/email'), ('GET', '/api/agents/submission'),
                                     ('GET', '/api/agents/gateway')])

    def test_standalone_check_without_owner_password_is_pending_not_drift(self):
        result = bridge_check.inspect(self.site)
        self.assertFalse(result.ready)
        self.assertTrue(result.pending)
        self.assertIn('owner login', result.detail)
        self.assertEqual(len(self.calls), 2)

    def test_bad_scope_blocks_even_without_owner_password(self):
        self.identities['submission-key']['keyScope']['projectIds'].append('outside')
        result = bridge_check.inspect(self.site)
        self.assertFalse(result.ready)
        self.assertFalse(result.pending)
        self.assertIn('scope', result.detail)

    def test_runtime_password_takes_priority_over_stale_legacy_copy(self):
        self.password(self.private, 'stale-fixture-password')
        self.assertTrue(bridge_check.inspect(self.site, self.runtime).ready)

    def test_missing_explicit_runtime_password_fails_without_legacy_fallback(self):
        self.password(self.private)
        (self.runtime / 'paperclip-owner.password').unlink()
        result = bridge_check.inspect(self.site, self.runtime)
        self.assertFalse(result.ready)
        self.assertFalse(result.pending)
        self.assertIn('Read the Paperclip owner password', result.detail)

    def test_legacy_password_is_supported_for_standalone_check(self):
        self.password(self.private)
        self.assertTrue(bridge_check.inspect(self.site).ready)

    def test_owner_auth_failure_is_distinct_from_drift_and_does_not_leak(self):
        self.login_status = 401
        result = bridge_check.inspect(self.site, self.runtime)
        self.assertFalse(result.ready)
        self.assertFalse(result.pending)
        self.assertIn('Authenticate the Paperclip owner', result.detail)
        self.assertNotIn('fixture', result.detail)

    def test_native_agent_drift_still_blocks_with_valid_runtime_login(self):
        for name in ('submission', 'gateway'):
            with self.subTest(agent=name):
                original = self.agents[name]['adapterType']
                self.agents[name]['adapterType'] = 'process-with-host-access'
                result = bridge_check.inspect(self.site, self.runtime)
                self.assertFalse(result.ready)
                self.assertFalse(result.pending)
                self.assertIn('drifted', result.detail)
                self.agents[name]['adapterType'] = original


if __name__ == '__main__':
    unittest.main()
