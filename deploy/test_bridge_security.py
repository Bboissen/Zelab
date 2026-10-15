"""Negative cases for complete native bridge scopes and adapter identity."""

import copy
import tempfile
import unittest


from fixtures import run_play
from filter_plugins.bridge_security import exact_scope, bridge_identity, gateway_contract, submission_contract


class BridgeSecurityTest(unittest.TestCase):
    def test_submission_identity_cannot_become_an_execution_worker(self):
        agent = {'companyId': 'company', 'adapterType': 'process',
                 'adapterConfig': {'command': '/usr/bin/false'},
                 'runtimeConfig': {'heartbeat': {'enabled': False, 'wakeOnDemand': False}},
                 'permissions': {'canCreateAgents': False, 'canCreateSkills': False,
                                 'canAssignTasks': True, 'trustPreset': 'standard'}}
        self.assertTrue(submission_contract(agent, 'company'))
        for change in [{'defaultEnvironmentId': 'host'}, {'adapterType': 'codex_local'},
                       {'adapterConfig': {'command': '/bin/sh'}},
                       {'runtimeConfig': {'heartbeat': {'enabled': False, 'wakeOnDemand': True}}}]:
            self.assertFalse(submission_contract({**agent, **change}, 'company'))

    def test_scope_accepts_equivalent_native_encodings(self):
        for scope in [{'kind': 'task_bridge', 'projectIds': ['p']},
                      {'kind': 'task_bridge', 'projectId': 'p'},
                      {'kind': 'task_bridge', 'projectId': 'p', 'projectIds': ['p'],
                       'parentIssueId': None, 'parentIssueIds': [], 'allowedAssigneeAgentIds': None}]:
            self.assertTrue(exact_scope(scope, 'p'))

    def test_scope_rejects_every_additional_authority_and_invalid_shape(self):
        good = {'kind': 'task_bridge', 'projectIds': ['p']}
        for change in [{'projectId': 'outside'}, {'projectIds': ['p', 'outside']},
                       {'parentIssueId': 'parent'}, {'parentIssueIds': ['parent']},
                       {'allowedAssigneeAgentIds': ['peer']}, {'projectIds': 'p'},
                       {'projectIds': [None]}, {'projectId': False},
                       {'allowedAssigneeAgentIds': 'peer'}, {'newAuthority': True},
                       {'kind': 'company'}, {'projectIds': []}]:
            with self.subTest(change=change):
                self.assertFalse(exact_scope({**good, **change}, 'p'))
        for scope in [None, [], 'task_bridge']:
            self.assertFalse(exact_scope(scope, 'p'))

    def test_dedicated_bridge_allows_only_its_declared_worker(self):
        scope = {'kind': 'task_bridge', 'projectIds': ['p'], 'allowedAssigneeAgentIds': ['ops']}
        self.assertTrue(exact_scope(scope, 'p', ['ops']))
        self.assertFalse(exact_scope(scope, 'p'))
        self.assertFalse(exact_scope({**scope, 'allowedAssigneeAgentIds': ['ops', 'code']}, 'p', ['ops']))

    def test_actual_token_must_have_expected_scope_company_and_agent(self):
        identity = {'id': 'agent', 'companyId': 'company',
                    'keyScope': {'kind': 'task_bridge', 'projectIds': ['p']}}
        self.assertTrue(bridge_identity(identity, 'company', 'agent', 'p'))
        for change in [{'id': 'other'}, {'companyId': 'other'}, {'keyScope': None},
                       {'keyScope': {'kind': 'task_bridge', 'projectIds': ['p'], 'projectId': 'outside'}}]:
            self.assertFalse(bridge_identity({**identity, **change}, 'company', 'agent', 'p'))

    def test_provisioning_loads_the_filter_and_rejects_broadened_scopes(self):
        with tempfile.TemporaryDirectory() as directory:
            for scope, allowed in [({'kind': 'task_bridge', 'projectIds': ['p']}, True),
                                   ({'kind': 'task_bridge', 'projectIds': ['p'], 'projectId': 'other'}, False),
                                   ({'kind': 'task_bridge', 'projectIds': ['p'], 'parentIssueIds': ['tree']}, False)]:
                result = run_play(directory, variables={'scope': scope}, tasks=[
                    {'ansible.builtin.assert': {'that': "scope | zelab_exact_bridge_scope('p')"}}])
                self.assertEqual(result.returncode, 0 if allowed else 2, result.stdout + result.stderr)

    def test_retained_gateway_security_fields_are_checked_without_blocking_model_edits(self):
        config = {'apiBaseUrl': 'http://127.0.0.1:18642', 'paperclipApiUrl': 'http://10.0.2.2:13100',
                  'apiKey': {'type': 'secret_ref', 'secretId': 'secret', 'version': 'latest'},
                  'sessionKeyStrategy': 'issue'}
        agent = {'companyId': 'company', 'adapterType': 'hermes_gateway', 'adapterConfig': config,
                 'permissions': {'canCreateAgents': False, 'canCreateSkills': False,
                                 'canAssignTasks': True, 'trustPreset': 'standard'}}
        args = ('company', config['apiBaseUrl'], config['paperclipApiUrl'], 'secret')
        self.assertTrue(gateway_contract(agent, *args))
        for change in [{'apiBaseUrl': 'https://unintended.example'},
                       {'paperclipApiUrl': 'http://unintended.example'},
                       {'sessionKeyStrategy': 'shared'}, {'dangerouslyAllowInsecureRemoteHttp': True},
                       {'dangerouslyAllowInsecureRemoteHttp': 'true'},
                       {'apiKey': {'type': 'secret_ref', 'secretId': 'other', 'version': 'latest'}},
                       {'apiKey': 'plaintext'}]:
            self.assertFalse(gateway_contract({**agent, 'adapterConfig': {**config, **change}}, *args))
        changed = copy.deepcopy(agent)
        changed['adapterConfig']['model'] = 'owner-selected-model'
        self.assertTrue(gateway_contract(changed, *args))
        changed['permissions']['canCreateAgents'] = True
        self.assertFalse(gateway_contract(changed, *args))
        self.assertFalse(gateway_contract({**agent, 'defaultEnvironmentId': 'other'}, *args))


if __name__ == '__main__':
    unittest.main()
