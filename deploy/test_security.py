"""Run native security assertions on safe and unsafe retained settings."""

import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest

from jinja2 import Environment, StrictUndefined
import yaml

from fixtures import role_tasks, run_play


class SecurityDriftTest(unittest.TestCase):
    def check_assertion(self, task, variables, allowed):
        with tempfile.TemporaryDirectory() as directory:
            result = run_play(directory, tasks=[task], variables=variables)
        self.assertEqual(result.returncode, 0 if allowed else 2, result.stdout + result.stderr)

    def test_restricted_profile_cannot_switch_to_local_tools_or_plugins(self):
        defaults = yaml.safe_load(Path('deploy/roles/research_profile/defaults/main.yaml').read_text())
        variables = {'research_disabled_toolsets': defaults['research_disabled_toolsets'],
                     'hermes_initial_model': 'test-model', 'research_tool_home': '/test/research',
                     'research_tool_user': 'zelab-research-tools', 'research_profile_dir': '/test/profile'}
        template = Path('deploy/roles/research_profile/templates/config.yaml.j2').read_text()
        environment = Environment(undefined=StrictUndefined)
        environment.filters['to_json'] = json.dumps
        config = yaml.safe_load(environment.from_string(template).render(variables))
        task, = role_tasks('deploy/roles/research_profile/tasks/main.yaml',
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

    def test_operations_worker_stays_on_its_ssh_environment_and_permissions(self):
        task, = role_tasks('deploy/roles/paperclip_workers/tasks/worker.yaml',
                         'Require retained native worker security settings')
        permissions = {'canCreateAgents': False, 'canCreateSkills': False, 'canAssignTasks': True,
                       'trustPreset': 'standard'}
        agent = {'adapterType': 'codex_local', 'defaultEnvironmentId': 'ops', 'permissions': permissions}
        variables = {'native_environment': {'id': 'ops'}, 'worker_permissions': permissions}
        # No environment would run the adapter as Paperclip's own account on the host.
        for change, allowed in [({}, True), ({'adapterType': 'claude_local'}, True),
                                ({'defaultEnvironmentId': None}, False),
                                ({'adapterType': 'process'}, False),
                                ({'permissions': dict(permissions, canCreateAgents=True)}, False)]:
            variables['worker_current'] = {'json': dict(agent, **change)}
            self.check_assertion(task, variables, allowed)


if __name__ == '__main__':
    unittest.main()
