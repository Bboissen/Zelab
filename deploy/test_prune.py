"""Exercise the end-of-run artifact cleanup on a temporary layout."""

import os
from pathlib import Path
import tempfile
import unittest

import yaml

from fixtures import role_tasks, run_play


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'deploy/roles/artifact_prune'


class HostPruneTest(unittest.TestCase):
    def layout(self, root):
        cache, tools, releases, links, logs, units = (root / name for name in
                                                      ('cache', 'tools', 'releases', 'bin', 'logs', 'units'))
        for directory in (cache, tools, releases, links, logs, units):
            directory.mkdir(exist_ok=True)
        for name in ('hermes-web-new.tar', 'hermes-web-old.tar', 'paperclip-mcp-new.tar', 'paperclip-mcp-old.tar',
                     'node-1.tar.xz', 'debian.qcow2', '.partial'):
            (cache / name).write_text('x')
        (cache / 'hermes-agent-new').mkdir(exist_ok=True)
        for name in ('node-v2-linux-x64', 'node-v1-linux-x64', 'codex-9-package', 'codex-8-package',
                     'claude-5', 'claude-4', 'unrelated'):
            (tools / name / 'bin').mkdir(parents=True, exist_ok=True)
        for name in ('current', 'old'):
            (releases / name).mkdir(exist_ok=True)
        for name, target in (('zelab-codex', tools / 'codex-8-package/bin/codex'),
                             ('zelab-claude', tools / 'claude-5/claude'), ('node', Path('/usr/bin/env'))):
            (links / name).unlink(missing_ok=True)
            (links / name).symlink_to(target)
        for index in range(7):
            log = logs / f'install-{index}.log'
            log.write_text('log')
            os.utime(log, (1000 + index, 1000 + index))
        (units / 'zelab-vm.service').write_text('[Unit]\nDescription=Zelab native Hermes guest\n')
        return cache, tools, releases, links, logs, units

    def prune(self, root):
        cache, tools, releases, links, logs, units = (root / name for name in
                                                      ('cache', 'tools', 'releases', 'bin', 'logs', 'units'))
        variables = yaml.safe_load((ROLE / 'defaults/main.yaml').read_text())
        variables.update({
            'prune_cache_dir': str(cache), 'prune_tools_dir': str(tools), 'prune_units_dir': str(units),
            'prune_command_links': [str(links / name) for name in ('zelab-codex', 'zelab-claude', 'node')],
            'vm_service': 'zelab-vm', 'hermes_commit': 'new', 'paperclip_mcp_revision': 'new', 'node_version': '2', 'codex_version': '9',
            'claude_version': '5', 'paperclip_runtime_dir': str(releases / 'current'),
            'private_dir': str(root), 'obsidian_enabled': False})
        result = run_play(root, variables=variables, tasks=role_tasks('deploy/roles/artifact_prune/tasks/host.yaml'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_only_current_pins_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache, tools, releases, links, logs, _ = self.layout(root)
            self.prune(root)
            self.assertEqual(sorted(p.name for p in cache.iterdir()), ['hermes-web-new.tar', 'paperclip-mcp-new.tar'])
            self.assertEqual(sorted(p.name for p in tools.iterdir()),
                             ['claude-5', 'codex-9-package', 'node-v2-linux-x64', 'unrelated'])
            self.assertEqual([p.name for p in releases.iterdir()], ['current'])
            # Only a link into a removed runtime goes; others are not ours to judge.
            self.assertEqual(sorted(p.name for p in links.iterdir()), ['node', 'zelab-claude'])
            self.assertEqual(sorted(p.name for p in logs.iterdir()),
                             [f'install-{index}.log' for index in range(2, 7)])

    def test_another_zelab_guest_keeps_shared_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache, tools, releases, _, logs, units = self.layout(root)
            (units / 'zelab-other.service').write_text('[Unit]\nDescription=Zelab native Hermes guest\n')
            self.prune(root)
            self.assertEqual(len(list(cache.iterdir())), 8)
            self.assertEqual(len(list(tools.iterdir())), 7)
            self.assertEqual(len(list(releases.iterdir())), 2)
            self.assertEqual(len(list(logs.iterdir())), 5)


class OverlapTest(unittest.TestCase):
    def test_background_jobs_start_after_guest_sizing_and_finish_before_use(self):
        order = [entry['import_playbook'] for entry in yaml.safe_load((ROOT / 'deploy/site.yaml').read_text())]
        position = order.index
        # install_resources measures idle host capacity while creating the guest.
        self.assertLess(position('create-guest.yaml'), position('prefetch.yaml'))
        self.assertLess(position('prefetch.yaml'), position('guest-ready.yaml'))
        self.assertLess(position('install-hermes.yaml'), position('install-paperclip.yaml'))
        self.assertLess(position('install-paperclip.yaml'), position('finish-hermes.yaml'))
        self.assertLess(position('build-hermes.yaml'), position('finish-hermes.yaml'))
        self.assertEqual(order[-1], 'prune.yaml')

    def test_every_background_job_is_awaited_and_cleaned_up(self):
        started, awaited, cleaned = {}, {}, {}

        def visit(tasks):
            for task in tasks or []:
                visit(task.get('block'))
                if task.get('poll') == 0:
                    started[task['register']] = task.get('become_user')
                status = task.get('ansible.builtin.async_status')
                if status:
                    job = status['jid'].split('{{')[1].split('.')[0].strip()
                    if job == 'item':
                        job = task['loop'].split('{{')[1].split('.')[0].strip()
                    (cleaned if status.get('mode') == 'cleanup' else awaited)[job] = task.get('become_user')
        for path in [*ROOT.glob('deploy/roles/*/tasks/*.yaml'), *ROOT.glob('deploy/*.yaml')]:
            data = yaml.safe_load(path.read_text())
            if path.parent.name == 'deploy' and path.name != 'inventory.yaml':
                for play in data or []:
                    visit(play.get('tasks'))
            elif isinstance(data, list):
                visit(data)
        self.assertEqual(set(started), {'hermes_web_build_job', 'paperclip_install_job', 'hermes_dependencies_job',
                                        'obsidian_pull_job'})
        # async_status must run as the account whose home holds the job record.
        self.assertEqual(awaited, started)
        self.assertEqual(cleaned, started)


if __name__ == '__main__':
    unittest.main()
