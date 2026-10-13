"""Verify native service paths change when the installed runtime changes."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class PaperclipReleaseTest(unittest.TestCase):
    def test_dependency_and_node_changes_leave_previous_release_paths_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = root / 'roles/paperclip/files'
            packages.mkdir(parents=True)
            (packages / 'package.json').write_text('{"dependencies":{"paperclipai":"2026.1005.0"}}')
            lock = packages / 'package-lock.json'
            versions = yaml.safe_load((ROOT / 'versions.yaml').read_text())
            playbook = root / 'check.yaml'
            versions.update(paperclip_user='fixture', paperclip_home='/tmp/fixture-state')
            paths = []
            for dependency, node in [('old', '26.7.0'), ('patched', '26.7.0'), ('patched', '26.8.0')]:
                lock.write_text(json.dumps({'fixture_dependency': dependency}))
                versions['node_version'] = node
                playbook.write_text(yaml.safe_dump([{
                    'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
                    'vars': versions, 'tasks': [{'ansible.builtin.template': {
                        'src': str(ROOT / 'deploy/roles/paperclip/templates/paperclip.service.j2'),
                        'dest': str(root / 'fixture.service'),
                    }}],
                }]))
                result = subprocess.run(['rtk', 'proxy', str(ROOT / '.venv/bin/ansible-playbook'),
                    '-i', 'localhost,', str(playbook)], capture_output=True, text=True,
                    env=dict(os.environ, ANSIBLE_CONFIG=str(ROOT / 'deploy/ansible.cfg')))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                rendered = (root / 'fixture.service').read_text()
                path = next(line.split('=', 1)[1] for line in rendered.splitlines()
                            if line.startswith('WorkingDirectory='))
                self.assertIn(path + '/node_modules/paperclipai/dist/index.js', rendered)
                self.assertIn('ReadWritePaths=/tmp/fixture-state', rendered)
                paths.append(path)
            self.assertEqual(len(set(paths)), 3, 'An update must not reinstall into the active release')


if __name__ == '__main__':
    unittest.main()
