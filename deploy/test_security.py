"""Run native security assertions on safe and unsafe retained settings."""

import tempfile
import unittest

from fixtures import role_tasks, run_play


class SecurityDriftTest(unittest.TestCase):
    def check_assertion(self, task, variables, allowed):
        with tempfile.TemporaryDirectory() as directory:
            result = run_play(directory, tasks=[task], variables=variables)
        self.assertEqual(result.returncode, 0 if allowed else 2, result.stdout + result.stderr)

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
