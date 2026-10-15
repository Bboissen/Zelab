"""Exercise deployment boundaries without contacting or changing installed services."""

import copy
from pathlib import Path
import tempfile
import unittest

import yaml

from fixtures import role_tasks, run_play


ROOT = Path(__file__).resolve().parents[1]


class ProvisioningTest(unittest.TestCase):
    def test_qualified_bridge_upgrade_removes_old_staging_and_unchanged_make_does_nothing(self):
        plays = yaml.safe_load((ROOT / 'deploy/paperclip-projects.yaml').read_text())
        tasks = copy.deepcopy(plays[1]['tasks'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for task in tasks:
                for module in ('ansible.builtin.stat', 'ansible.builtin.file', 'ansible.builtin.copy'):
                    for key, value in list(task.get(module, {}).items()):
                        if isinstance(value, str):
                            task[module][key] = value.replace('/usr/local/lib/zelab', str(root / 'export'))
                    task.get(module, {}).pop('owner', None)
                    task.get(module, {}).pop('group', None)
            runtime = root / 'runtime-one'
            source = runtime / 'node_modules/@paperclipai/hermes-paperclip-adapter/skills/paperclip-task-bridge'
            source.mkdir(parents=True)
            (source / 'SKILL.md').write_text('first upstream skill')
            (source / 'removed.mjs').write_text('obsolete helper')
            self.run_play(directory, tasks=tasks, variables={'paperclip_runtime_dir': str(runtime)})
            result = self.run_play(directory, tasks=tasks, variables={'paperclip_runtime_dir': str(runtime)})
            self.assertIn('changed=0', result.stdout)
            candidate = root / 'runtime-two'
            upgraded = candidate / source.relative_to(runtime)
            upgraded.mkdir(parents=True)
            (upgraded / 'SKILL.md').write_text('second upstream skill')
            self.run_play(directory, tasks=tasks, variables={'paperclip_runtime_dir': str(candidate)})
            exported = root / 'export/paperclip-task-bridge'
            self.assertEqual((exported / 'SKILL.md').read_text(), 'second upstream skill')
            self.assertFalse((exported / 'removed.mjs').exists())

    def test_database_binaries_need_no_runtime_chmod_in_the_readonly_service(self):
        task, = role_tasks('deploy/roles/paperclip/tasks/main.yaml', 'Prepare native database executables for the read-only service runtime')
        task['ansible.builtin.file'].pop('owner')
        task['ansible.builtin.file'].pop('group')
        with tempfile.TemporaryDirectory() as directory:
            binaries = Path(directory) / 'node_modules/@embedded-postgres/linux-x64/native/bin'
            binaries.mkdir(parents=True)
            for name in ('postgres', 'pg_ctl', 'initdb'):
                path = binaries / name
                path.write_bytes(b'fixture executable')
                path.chmod(0o770)
            self.run_play(directory, tasks=[task], variables={'paperclip_runtime_dir': directory})
            for path in binaries.iterdir():
                # This is the required mask in pinned embedded-postgres's
                # ensureBinIsExecutable; missing bits trigger chmod on startup.
                self.assertEqual(path.stat().st_mode & 0o555, 0o555)
            result = self.run_play(directory, tasks=[task], variables={'paperclip_runtime_dir': directory})
            self.assertIn('changed=0', result.stdout)

    def test_public_dashboard_archive_is_readable_after_restrictive_packaging_umask(self):
        task, = role_tasks('deploy/roles/hermes_build/tasks/main.yaml', 'Make the public dashboard archive readable by the controller')
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'dashboard.tar'
            archive.write_bytes(b'public build output')
            archive.chmod(0o600)
            task['ansible.builtin.file']['path'] = str(archive)
            # Exercise the production mode correction as an ordinary test user.
            task['ansible.builtin.file'].pop('owner')
            task['ansible.builtin.file'].pop('group')
            self.run_play(directory, tasks=[task])
            self.assertEqual(archive.stat().st_mode & 0o777, 0o644)
            self.assertEqual(archive.read_bytes(), b'public build output')
            result = self.run_play(directory, tasks=[task])
            self.assertIn('changed=0', result.stdout)

    def run_play(self, directory, *, tasks=(), roles=(), variables=None, succeeds=True):
        result = run_play(directory, tasks=tasks, roles=roles, variables=variables, host='zelab_host')
        self.assertEqual(result.returncode, 0 if succeeds else 2, result.stdout + result.stderr)
        return result

    def test_retained_credentials_are_required_only_after_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            installed = Path(directory) / 'installed'
            source = Path(directory) / 'private-source'
            variables = {'retained_secrets': [{'installed': str(installed), 'source': str(source)}]}
            self.run_play(directory, roles=['retained_secrets'], variables=variables)
            installed.write_text('existing application state')
            self.run_play(directory, roles=['retained_secrets'], variables=variables, succeeds=False)
            self.assertFalse(source.exists(), 'A lost credential must never be regenerated by the guard')
            source.write_text('retained credential')
            self.run_play(directory, roles=['retained_secrets'], variables=variables)
            self.assertEqual(installed.read_text(), 'existing application state')

    def test_disabled_optional_roles_do_not_touch_the_host_or_guest(self):
        roles = []
        for name in ['notes-share.yaml', 'obsidian.yaml']:
            plays = yaml.safe_load((ROOT / 'deploy' / name).read_text())
            for play in plays:
                roles.extend(role for role in play.get('roles', [])
                             if isinstance(role, dict) and role.get('role') in
                             {'notes_host', 'notes_guest', 'obsidian_host'})
        self.assertEqual(len(roles), 3)
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_play(directory, roles=roles, variables={
                'notes_enabled': False, 'obsidian_enabled': False,
            })
        self.assertIn('changed=0', result.stdout)

    def test_serve_routes_never_replace_other_handlers_or_public_routes(self):
        task, = role_tasks('deploy/roles/private_ui/tasks/check.yaml', 'Preserve existing routes and reject public Funnel on owned endpoints')
        endpoint = 'host.example.ts.net:443'
        backend = 'http://127.0.0.1:18640'
        safe = {'TCP': {'443': {'HTTPS': True}},
                'Web': {endpoint: {'Handlers': {'/': {'Proxy': backend}}}}}
        conflicts = []
        for handler in [{'Text': 'existing site'}, {'Path': '/srv/site'},
                        {'Proxy': 'http://127.0.0.1:9999'}]:
            current = copy.deepcopy(safe)
            current['Web'][endpoint]['Handlers']['/'] = handler
            conflicts.append(current)
        for listener in [{'TCPForward': '127.0.0.1:9999'}, {'HTTPS': False}]:
            current = copy.deepcopy(safe)
            current['TCP']['443'] = listener
            conflicts.append(current)
        public = copy.deepcopy(safe)
        public['AllowFunnel'] = {endpoint: True}
        conflicts.append(public)
        with tempfile.TemporaryDirectory() as directory:
            for current, allowed in [({}, True), (safe, True),
                                     *((config, False) for config in conflicts)]:
                with self.subTest(config=current):
                    self.run_play(directory, tasks=[task], variables={
                        'ui_tailscale_hostname': 'host.example.ts.net',
                        'ui_tail_routes': [{'port': 443, 'field': 'ui_tail_hermes_port', 'backend': backend}],
                        'ui_tail_config': current,
                    }, succeeds=allowed)


if __name__ == '__main__':
    unittest.main()
