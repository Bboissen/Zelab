"""Run native security assertions on safe and unsafe retained settings."""

import base64
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from jinja2 import Environment, StrictUndefined
import yaml


def assertion(path, name):
    return next(task for task in yaml.safe_load(Path(path).read_text()) if task.get('name') == name)


class SecurityDriftTest(unittest.TestCase):
    def check_assertion(self, task, variables, allowed):
        with tempfile.TemporaryDirectory() as directory:
            playbook = Path(directory) / 'check.yaml'
            playbook.write_text(yaml.safe_dump([{
                'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
                'become': False, 'vars': variables, 'tasks': [task],
            }]))
            result = subprocess.run(
                ['rtk', 'proxy', '.venv/bin/ansible-playbook', '-i', 'localhost,', str(playbook)],
                text=True, capture_output=True,
            )
            if allowed:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_restricted_profile_cannot_switch_to_local_tools_or_plugins(self):
        defaults = yaml.safe_load(Path('deploy/roles/research_profile/defaults/main.yaml').read_text())
        variables = {'research_disabled_toolsets': defaults['research_disabled_toolsets'],
                     'hermes_initial_model': 'test-model', 'research_tool_home': '/test/research',
                     'research_tool_user': 'zelab-research-tools', 'research_profile_dir': '/test/profile'}
        template = Path('deploy/roles/research_profile/templates/config.yaml.j2').read_text()
        environment = Environment(undefined=StrictUndefined)
        environment.filters['to_json'] = json.dumps
        config = yaml.safe_load(environment.from_string(template).render(variables))
        task = assertion('deploy/roles/research_profile/tasks/main.yaml',
                         'Require the restricted backend and toolsets after native UI edits')
        unsafe_backend = copy.deepcopy(config)
        unsafe_backend['terminal']['backend'] = 'local'
        unsafe_plugin = copy.deepcopy(config)
        unsafe_plugin['plugins']['enabled'] = ['untrusted-plugin']
        unsafe_override = copy.deepcopy(config)
        unsafe_override['agent']['disabled_toolsets'].remove('code_execution')
        unsafe_platform = copy.deepcopy(config)
        unsafe_platform['platform_toolsets']['telegram'] = ['code_execution']
        unsafe_route = copy.deepcopy(config)
        unsafe_route['gateway'] = {'profile_routes': [{'profile': 'default'}]}
        safe_ui_edit = copy.deepcopy(config)
        safe_ui_edit['model']['default'] = 'owner-selected-model'
        safe_ui_edit['agent']['disabled_toolsets'].append('memory')
        safe_ui_edit['platform_toolsets']['discord'] = ['search']
        for current, allowed in [(config, True), (safe_ui_edit, True), (unsafe_backend, False),
                                 (unsafe_plugin, False), (unsafe_override, False),
                                 (unsafe_platform, False), (unsafe_route, False)]:
            variables['research_live_config'] = {'content': base64.b64encode(yaml.safe_dump(current).encode()).decode()}
            self.check_assertion(task, variables, allowed)

    def test_coding_must_remain_paused_on_its_declared_identity(self):
        task = assertion('deploy/roles/paperclip_workers/tasks/worker.yaml',
                         'Require retained native worker permissions and unsupported Coding pause')
        permissions = {'canCreateAgents': False, 'canCreateSkills': False, 'canAssignTasks': False,
                       'trustPreset': 'low_trust_review', 'authorizationPolicy': {'trustBoundary': {
                           'mode': 'low_trust_review', 'companyId': 'company', 'projectIds': ['coding'],
                           'allowedToolClasses': ['git.read', 'git.write', 'tests.local']}}}
        agent = {'adapterType': 'codex_local', 'defaultEnvironmentId': 'coding',
                 'permissions': permissions, 'status': 'paused'}
        variables = {'worker': {'role': 'code'}, 'native_environment': {'id': 'coding'},
                     'worker_permissions': permissions}
        for environment, status, allowed in [('coding', 'paused', True), ('ops', 'paused', False),
                                              ('coding', 'idle', False)]:
            current = dict(agent, defaultEnvironmentId=environment, status=status)
            variables['worker_current'] = {'json': current}
            self.check_assertion(task, variables, allowed)
        variables['worker_current'] = {'json': dict(agent, adapterType='claude_local')}
        self.check_assertion(task, variables, True)


if __name__ == '__main__':
    unittest.main()
