"""Exercise fresh private-state preparation without starting a VM."""

import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import unittest

import yaml


class BootstrapTest(unittest.TestCase):
    def test_empty_private_directory_is_seeded_once(self):
        tasks = yaml.safe_load(Path('deploy/roles/vm/tasks/main.yaml').read_text())
        names = {'Prepare private deployment state',
                 'Generate the fresh guest deployment key once',
                 'Prepare a private recovery key once'}
        tasks = [task for task in tasks if task.get('name') in names]
        self.assertEqual(len(tasks), 3)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / 'private'
            playbook = root / 'bootstrap.yaml'
            playbook.write_text(yaml.safe_dump([{
                'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
                'become': False,
                'vars': {'private_dir': str(private),
                         'deploy_key': str(private / 'deploy_ed25519'),
                         'operator_user': pwd.getpwuid(os.getuid()).pw_name},
                'tasks': tasks,
            }]))
            def apply():
                result = subprocess.run(
                    ['rtk', 'proxy', '.venv/bin/ansible-playbook', '-i', 'localhost,', str(playbook)],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout

            apply()
            self.assertEqual(private.stat().st_mode & 0o777, 0o700)
            keys = [private / 'deploy_ed25519', private / 'backup-passphrase']
            first = [key.read_bytes() for key in keys]
            for key in keys:
                self.assertTrue(key.read_bytes().strip())
                self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            self.assertIn('changed=0', apply())
            self.assertEqual(first, [key.read_bytes() for key in keys])


class InterruptedCreationTest(unittest.TestCase):
    NAMES = ['Inspect the explicitly selected fresh disk', 'Inspect the fresh creation receipt',
             'Inspect an unfinished creation from an interrupted run',
             'Inspect the selected native service before any host mutation',
             'Refuse replacement or ambiguous reuse of an existing disk',
             'Validate creation receipts before reusing state',
             "Discard this site's own unfinished, never-booted disk",
             'Re-inspect the selected disk after unfinished-creation cleanup']

    def apply(self, root, receipts):
        tasks = yaml.safe_load(Path('deploy/roles/vm/tasks/create.yaml').read_text())
        tasks = [task for task in tasks if task.get('name') in self.NAMES]
        self.assertEqual(len(tasks), len(self.NAMES))
        data, bootstrap = root / 'data', root / 'bootstrap'
        for directory in (data, bootstrap):
            directory.mkdir(exist_ok=True)
        disk = data / 'disk.qcow2'
        disk.write_text('guest data')
        for name in ('created', 'creating'):
            (bootstrap / name).unlink(missing_ok=True)
        for name, service in receipts.items():
            (bootstrap / name).write_text(f'{disk}\nzelab\n{service}\n')
        playbook = root / 'create.yaml'
        playbook.write_text(yaml.safe_dump([{
            'hosts': 'localhost', 'connection': 'local', 'gather_facts': False, 'become': False,
            'vars': {'vm_disk': str(disk), 'vm_data': str(data), 'vm_bootstrap_dir': str(bootstrap),
                     'vm_instance': 'zelab', 'vm_service': 'zelab-test-absent-unit'},
            'tasks': tasks}]))
        result = subprocess.run(['rtk', 'proxy', '.venv/bin/ansible-playbook', '-i', 'localhost,', str(playbook)],
                                capture_output=True, text=True)
        return result.returncode, disk.exists()

    def test_only_this_sites_unfinished_disk_is_recreated(self):
        own = 'zelab-test-absent-unit'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(self.apply(root, {}), (2, True), 'unrelated disk must be refused and kept')
            self.assertEqual(self.apply(root, {'creating': 'other-vm'}), (2, True))
            self.assertEqual(self.apply(root, {'creating': own}), (0, False), 'own interrupted disk is redone')
            self.assertEqual(self.apply(root, {'created': own}), (0, True), 'completed disk is reused')


if __name__ == '__main__':
    unittest.main()
