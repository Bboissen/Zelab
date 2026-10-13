"""Exercise native worker provisioning against a disposable owner API fixture."""

import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class WorkerHarnessTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.environments, self.projects, self.agents, self.mutations = [], [], [], []
        self.agent_failure_status = None
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, data, status=200):
                encoded = json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def collection(self):
                return {'environments': fixture.environments, 'projects': fixture.projects,
                        'agents': fixture.agents}.get(self.path.rsplit('/', 1)[-1])

            def do_GET(self):
                collection = self.collection()
                if collection is not None:
                    self.respond(collection)
                else:
                    self.respond(next(agent for agent in fixture.agents
                                      if self.path.endswith('/' + agent['id'])))

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                collection = self.collection()
                if collection is fixture.agents:
                    if fixture.agent_failure_status:
                        self.respond({'error': 'fixture-response-secret'}, fixture.agent_failure_status)
                        return
                    environment = next((item for item in fixture.environments
                                        if item['id'] == body.get('defaultEnvironmentId')), None)
                    if body['adapterType'] == 'process' and environment and environment['driver'] != 'local':
                        self.respond({'error': 'Environment driver "ssh" is not allowed here'}, 422)
                        return
                created = dict(body, id=f'fixture-{self.path.rsplit("/", 1)[-1]}-{len(collection)}')
                if collection is fixture.agents:
                    created['status'] = 'idle'
                collection.append(created)
                fixture.mutations.append(('POST', copy.deepcopy(body)))
                self.respond(created, 201)

            def do_PATCH(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                agent = next(agent for agent in fixture.agents if self.path.endswith('/' + agent['id']))
                candidate = dict(agent, **body)
                environment = next((item for item in fixture.environments
                                    if item['id'] == candidate.get('defaultEnvironmentId')), None)
                if candidate['adapterType'] == 'process' and environment and environment['driver'] != 'local':
                    self.respond({'error': 'Environment driver "ssh" is not allowed here'}, 422)
                    return
                agent.update({key: value for key, value in body.items() if key != 'replaceAdapterConfig'})
                fixture.mutations.append(('PATCH', copy.deepcopy(body)))
                self.respond(agent)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        for role in ('ops', 'code'):
            (self.root / f'worker-{role}.key').write_text('fixture-only key')
        (self.root / 'worker_known_hosts').write_text('fixture-only host key')

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def provision(self, harness, role='ops', *, expect_failure=False):
        worker = {'role': role, 'name': 'Operations' if role == 'ops' else 'Coding',
                  'agent_role': 'devops' if role == 'ops' else 'engineer',
                  'user': 'fixture-' + role, 'home': '/fixture/' + role}
        variables = {'worker_harness': harness, 'worker_model': '', 'worker': worker,
                     'worker_ops_user': 'fixture-ops', 'worker_code_user': 'fixture-code',
                     'worker_ssh_host': '127.0.0.1', 'worker_ssh_port': 22, 'node_version': '26.7.0',
                     'private_dir': str(self.root), 'worker_company': {'id': 'fixture-company'},
                     'worker_owner': {'cookies_string': 'fixture-only'},
                     'paperclip_port': self.server.server_port}
        playbook = self.root / 'check.yaml'
        playbook.write_text(yaml.safe_dump([{
            'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
            'become': False, 'vars': variables,
            'tasks': [{'no_log': True, 'block': [
                {'ansible.builtin.include_role': {'name': 'paperclip_workers', 'tasks_from': 'worker'}},
            ]}],
        }]))
        result = subprocess.run(['rtk', 'proxy', str(ROOT / '.venv/bin/ansible-playbook'),
                                 '-i', 'localhost,', str(playbook)], text=True, capture_output=True,
                                env=dict(os.environ, ANSIBLE_ROLES_PATH=str(ROOT / 'deploy/roles')))
        if expect_failure:
            self.assertNotEqual(result.returncode, 0)
            return result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.agents[-1]

    def test_skipping_creates_inert_paused_workers_and_reapply_changes_nothing(self):
        for role in ('ops', 'code'):
            agent = self.provision('none', role)
            self.assertEqual(agent['adapterType'], 'process')
            self.assertEqual(agent['adapterConfig']['command'], '/usr/bin/false')
            self.assertEqual(agent['status'], 'paused')
            self.assertFalse(agent['runtimeConfig']['heartbeat']['wakeOnDemand'])
            self.assertIsNone(agent['defaultEnvironmentId'])
        count = len(self.mutations)
        self.provision('none', 'code')
        self.assertEqual(len(self.mutations), count)

    def test_both_leaves_the_worker_paused_until_an_adapter_is_chosen(self):
        agent = self.provision('both')
        self.assertEqual(agent['adapterType'], 'process')
        self.assertEqual(agent['adapterConfig']['command'], '/usr/bin/false')
        self.assertEqual(agent['status'], 'paused')
        self.assertFalse(agent['runtimeConfig']['heartbeat']['wakeOnDemand'])
        self.assertTrue(agent['metadata']['zelabHarnessPending'])
        self.assertIsNone(agent['defaultEnvironmentId'])

    def test_both_native_harnesses_activate_a_skipped_operations_worker(self):
        for harness in ('codex', 'claude'):
            with self.subTest(harness=harness):
                self.environments.clear()
                self.projects.clear()
                self.agents.clear()
                initial = self.provision('none')
                identifier = initial['id']
                permissions = copy.deepcopy(initial['permissions'])
                agent = self.provision(harness)
                self.assertEqual(agent['id'], identifier)
                self.assertEqual(agent['adapterType'], harness + '_local')
                self.assertEqual(agent['defaultEnvironmentId'], self.environments[0]['id'])
                self.assertEqual(agent['adapterConfig']['engine'], 'cli')
                self.assertEqual(agent['adapterConfig']['command'], '/usr/local/bin/zelab-' + harness)
                self.assertNotIn('model', agent['adapterConfig'])
                self.assertEqual(agent['permissions'], permissions)
                self.assertEqual(agent['status'], 'idle')
                self.assertFalse(agent['metadata']['zelabHarnessPending'])
                if harness == 'claude':
                    self.assertEqual(agent['adapterConfig']['env']['CLAUDE_CONFIG_DIR'], '/fixture/ops/.claude')
                    self.assertEqual(agent['adapterConfig']['env']['DISABLE_UPDATES'], '1')

    def test_new_claude_coding_worker_stays_paused_with_its_restricted_identity(self):
        agent = self.provision('claude', 'code')
        self.assertEqual(agent['adapterType'], 'claude_local')
        self.assertEqual(agent['status'], 'paused')
        self.assertEqual(agent['adapterConfig']['env'], {})
        self.assertEqual(agent['permissions']['trustPreset'], 'low_trust_review')
        self.assertFalse(agent['permissions']['canAssignTasks'])

    def test_rejected_agent_creation_reports_status_without_private_response(self):
        self.agent_failure_status = 422
        output = self.provision('both', expect_failure=True)
        self.assertIn('Paperclip worker creation failed (HTTP 422)', output)
        self.assertIn('check adapter and environment compatibility', output)
        self.assertNotIn('fixture-response-secret', output)
        self.assertNotIn('fixture-only', output)
        self.assertEqual(self.agents, [])

    def test_reapply_keeps_an_owner_configured_adapter_and_model(self):
        agent = self.provision('codex')
        agent['adapterType'] = 'claude_local'
        agent['adapterConfig'] = {'engine': 'cli', 'model': 'owner-selected-model', 'command': '/owner/claude'}
        previous = copy.deepcopy(agent)
        count = len(self.mutations)
        self.provision('none')
        self.assertEqual(self.agents[0], previous)
        self.assertEqual(len(self.mutations), count)


if __name__ == '__main__':
    unittest.main()
