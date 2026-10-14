"""Exercise native worker provisioning against a disposable owner API fixture."""

import copy
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import tempfile
import unittest


from fixtures import run_play, serve


ROOT = Path(__file__).resolve().parents[1]


class WorkerHarnessTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.environments, self.projects, self.agents, self.issues, self.mutations = [], [], [], [], []
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
                        'agents': fixture.agents, 'issues': fixture.issues}.get(self.path.rsplit('/', 1)[-1])

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
                if collection is fixture.issues:
                    reused = next((issue for issue in fixture.issues
                                   if issue['idempotencyKey'] == body['idempotencyKey']), None)
                    if reused:
                        self.respond(reused, 200)
                        return
                created = dict(body, id=f'fixture-{self.path.rsplit("/", 1)[-1]}-{len(collection)}')
                if collection is fixture.agents:
                    created['status'] = 'idle'
                collection.append(created)
                fixture.mutations.append(('POST', copy.deepcopy(body)))
                self.respond(created, 201)

            def do_PATCH(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if '/workspaces/' in self.path:
                    workspace = next(item for project in fixture.projects
                                     for item in project.get('workspaces', [])
                                     if self.path.endswith('/' + item['id']))
                    workspace.update(body)
                    fixture.mutations.append(('PATCH', copy.deepcopy(body)))
                    self.respond(workspace)
                    return
                agent = next(agent for agent in fixture.agents if self.path.endswith('/' + agent['id']))
                candidate = dict(agent, **body)
                environment = next((item for item in fixture.environments
                                    if item['id'] == candidate.get('defaultEnvironmentId')), None)
                if candidate['adapterType'] == 'process' and environment and environment['driver'] != 'local':
                    self.respond({'error': 'Environment driver "ssh" is not allowed here'}, 422)
                    return
                if 'adapterConfig' in body and not body.get('replaceAdapterConfig'):
                    body = dict(body, adapterConfig=dict(agent.get('adapterConfig', {}), **body['adapterConfig']))
                agent.update({key: value for key, value in body.items() if key != 'replaceAdapterConfig'})
                fixture.mutations.append(('PATCH', copy.deepcopy(body)))
                self.respond(agent)

        self.port = serve(self, Handler)
        (self.root / 'worker-ops.key').write_text('fixture-only key')
        (self.root / 'worker_known_hosts').write_text('fixture-only host key')

    def provision(self, harness, *, model='', expect_failure=False):
        worker = {'role': 'ops', 'name': 'Operations', 'agent_role': 'devops',
                  'user': 'fixture-ops', 'home': '/fixture/ops',
                  'paperclip_workspace': '/fixture/paperclip/ops'}
        variables = {'worker_harness': harness, 'worker_model': model, 'worker': worker,
                     'worker_ops_user': 'fixture-ops',
                     'worker_ssh_host': '127.0.0.1', 'worker_ssh_port': 22, 'node_version': '26.7.0',
                     'private_dir': str(self.root), 'worker_company': {'id': 'fixture-company'},
                     'paperclip_owner_session': {'cookies_string': 'fixture-only'},
                     'paperclip_port': self.port}
        result = run_play(self.root, variables=variables, tasks=[{'no_log': True, 'block': [
            {'ansible.builtin.include_role': {'name': 'paperclip_workers', 'tasks_from': 'worker'}}]}])
        if expect_failure:
            self.assertNotEqual(result.returncode, 0)
            return result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.agents[-1] if self.agents else None

    def assert_fully_configured(self, agent, harness):
        self.assertEqual(agent['adapterType'], harness + '_local')
        self.assertEqual(agent['defaultEnvironmentId'], self.environments[0]['id'])
        self.assertEqual(self.environments[0]['config']['username'], 'fixture-ops')
        self.assertEqual(agent['adapterConfig']['engine'], 'cli')
        self.assertEqual(agent['adapterConfig']['command'], '/usr/local/bin/zelab-' + harness)
        self.assertEqual(agent['adapterConfig']['cwd'], '/fixture/paperclip/ops')
        self.assertEqual(self.environments[0]['config']['remoteWorkspacePath'], '/fixture/ops/workspace')
        self.assertTrue(agent['runtimeConfig']['heartbeat']['wakeOnDemand'])
        self.assertEqual(agent['status'], 'idle')
        self.assertTrue(agent['permissions']['canAssignTasks'])
        self.assertFalse(agent['metadata']['zelabProofTaskPending'])
        if harness == 'claude':
            self.assertEqual(agent['adapterConfig']['env']['CLAUDE_CONFIG_DIR'], '/fixture/ops/.claude')
            self.assertEqual(agent['adapterConfig']['env']['DISABLE_UPDATES'], '1')

    def assert_one_waiting_proof(self, agent):
        self.assertEqual(len(self.issues), 1)
        proof = self.issues[0]
        self.assertEqual(proof['title'], 'Prove Operations host access and sudo')
        self.assertEqual(proof['status'], 'backlog')
        self.assertEqual(proof['assigneeAgentId'], agent['id'])
        self.assertEqual(proof['projectId'], self.projects[0]['id'])
        self.assertIn('sudo -n true', proof['description'])
        self.assertIn('top -b -n 1', proof['description'])

    def test_skip_creates_only_the_project_and_reapply_changes_nothing(self):
        self.assertIsNone(self.provision('none'))
        self.assertEqual([project['name'] for project in self.projects], ['Operations'])
        self.assertEqual((self.environments, self.agents, self.issues), ([], [], []))
        count = len(self.mutations)
        self.provision('none')
        self.assertEqual(len(self.mutations), count)

    def test_selected_harness_creates_one_fully_configured_worker_without_a_model(self):
        for harness in ('codex', 'claude'):
            with self.subTest(harness=harness):
                for collection in (self.environments, self.projects, self.agents, self.issues):
                    collection.clear()
                agent = self.provision(harness)
                self.assertEqual(len(self.agents), 1)
                self.assert_fully_configured(agent, harness)
                self.assertNotIn('model', agent['adapterConfig'])
                self.assert_one_waiting_proof(agent)

    def test_reapply_neither_duplicates_the_proof_nor_rewrites_the_worker(self):
        agent = self.provision('codex')
        count = len(self.mutations)
        self.provision('codex')
        self.assertEqual(len(self.mutations), count)
        self.assert_one_waiting_proof(agent)

    def test_interrupted_proof_recording_reuses_the_same_task(self):
        agent = self.provision('codex')
        agent['metadata']['zelabProofTaskPending'] = True
        self.provision('codex')
        self.assert_one_waiting_proof(agent)
        self.assertFalse(agent['metadata']['zelabProofTaskPending'])

    def test_site_model_preset_is_applied_when_provided(self):
        agent = self.provision('codex', model='gpt-6.1-sol')
        self.assertEqual(agent['adapterConfig']['model'], 'gpt-6.1-sol')

    def test_unchanged_legacy_placeholder_is_configured_and_gets_the_proof(self):
        self.provision('none')
        self.agents.append({'id': 'legacy-ops', 'name': 'Operations', 'adapterType': 'process',
                            'adapterConfig': {'command': '/usr/bin/false', 'args': [], 'cwd': '/fixture/ops/workspace'},
                            'defaultEnvironmentId': None, 'status': 'paused',
                            'metadata': {'zelabHarnessPending': True},
                            'runtimeConfig': {'heartbeat': {'enabled': False, 'wakeOnDemand': False}},
                            'permissions': {'canCreateAgents': False, 'canCreateSkills': False,
                                            'canAssignTasks': True, 'trustPreset': 'standard'}})
        agent = self.provision('claude')
        self.assertEqual(agent['id'], 'legacy-ops')
        self.assert_fully_configured(agent, 'claude')
        self.assertFalse(agent['metadata']['zelabHarnessPending'])
        self.assert_one_waiting_proof(agent)

    def test_earlier_worker_folder_source_moves_to_the_coordinator_side(self):
        agent = self.provision('codex')
        agent['adapterConfig']['cwd'] = '/fixture/ops/workspace'
        self.projects[0]['workspaces'] = [{'id': 'fixture-workspace', 'cwd': '/fixture/ops/workspace'}]
        self.provision('codex')
        self.assertEqual(agent['adapterConfig']['cwd'], '/fixture/paperclip/ops')
        self.assertEqual(agent['adapterConfig']['command'], '/usr/local/bin/zelab-codex')
        self.assertEqual(self.projects[0]['workspaces'][0]['cwd'], '/fixture/paperclip/ops')
        count = len(self.mutations)
        self.provision('codex')
        self.assertEqual(len(self.mutations), count)

    def test_rejected_agent_creation_reports_status_without_private_response(self):
        self.agent_failure_status = 422
        output = self.provision('codex', expect_failure=True)
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
        self.provision('codex')
        self.assertEqual(self.agents[0], previous)
        self.assertEqual(len(self.mutations), count)

    def test_worker_moved_off_its_ssh_environment_is_refused(self):
        agent = self.provision('codex')
        agent['defaultEnvironmentId'] = None
        output = self.provision('codex', expect_failure=True)
        self.assertIn('declared SSH environment', output)


if __name__ == '__main__':
    unittest.main()
