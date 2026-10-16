"""Regression checks for recovery safety; never operate real services."""

import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from backup import require_separate_backup, selected_paths
from runtime import maintenance_lock, private_directory, stopped_writers


class RecoverySafetyTest(unittest.TestCase):
    def test_inventory_includes_custom_vm_unit_and_reserved_optional_state(self):
        with tempfile.TemporaryDirectory() as directory:
            site = {name: str(Path(directory) / name) for name in (
                'vm_data', 'vm_root', 'paperclip_home', 'projects_dir',
                'notes_vault_dir', 'vault_history_dir', 'obsidian_state_dir',
                'private_dir', 'vm_bootstrap_dir')}
            site['vm_service'] = 'research-guest'
            paths = selected_paths(site, Path(directory) / 'repository', require_existing=False)
            self.assertIn(Path('/etc/systemd/system/research-guest.service'), paths)
            self.assertIn(Path('/etc/systemd/system/research-guest.service.d'), paths)
            self.assertIn(Path(site['vm_bootstrap_dir']), paths)
            repository = Path(directory) / 'repository'
            self.assertIn(repository / 'versions.yaml', paths)
            self.assertIn(repository / 'deploy/roles/paperclip/files/package.json', paths)
            self.assertIn(repository / 'deploy/roles/paperclip/files/package-lock.json', paths)

    def test_explicit_stop_leaves_writers_stopped_after_success(self):
        active = {'vm.service', 'zelab-paperclip.service', 'zelab-notes-sshd.service'}
        def command(*args):
            if args[:2] == ('systemctl', 'stop'):
                active.discard(args[2])
            elif args[:2] == ('systemctl', 'start'):
                self.fail('Successful explicit stop must not restart services')
        with tempfile.TemporaryDirectory() as directory, \
                patch('runtime.require_idle_workers'), \
                patch('runtime.run', side_effect=command), \
                patch('runtime.service_active', side_effect=lambda name: name in active), \
                patch('runtime.container_running', return_value=False):
            with stopped_writers({'paperclip_home': directory}, 'vm.service', resume=False):
                pass
        self.assertFalse(active)

    def test_failed_snapshot_restores_only_previously_running_writers(self):
        active = {'vm.service', 'zelab-notes-sshd.service'}
        commands = []
        def command(*args):
            commands.append(args)
            if args[:2] == ('systemctl', 'stop'):
                active.discard(args[2])
            if args[:2] == ('systemctl', 'start'):
                active.add(args[2])
        with tempfile.TemporaryDirectory() as directory, \
                patch('runtime.require_idle_workers'), \
                patch('runtime.run', side_effect=command), \
                patch('runtime.service_active', side_effect=lambda name: name in active), \
                patch('runtime.container_running', return_value=False):
            with self.assertRaisesRegex(ValueError, 'snapshot failed'):
                with stopped_writers({'paperclip_home': directory}, 'vm.service'):
                    self.assertFalse(active)
                    raise ValueError('snapshot failed')
        self.assertEqual(active, {'vm.service', 'zelab-notes-sshd.service'})
        self.assertNotIn(('systemctl', 'start', 'zelab-paperclip.service'), commands)

    def test_failed_restart_does_not_prevent_other_service_recovery(self):
        active = {'vm.service', 'zelab-notes-sshd.service', 'zelab-paperclip.service'}
        started = []
        def command(*args):
            if args[:2] == ('systemctl', 'stop'):
                active.discard(args[2])
            if args[:2] == ('systemctl', 'start'):
                started.append(args[2])
                if args[2] == 'zelab-notes-sshd.service':
                    raise RuntimeError('test restart failed')
        with tempfile.TemporaryDirectory() as directory, \
                patch('runtime.require_idle_workers'), \
                patch('runtime.run', side_effect=command), \
                patch('runtime.service_active', side_effect=lambda name: name in active), \
                patch('runtime.container_running', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Service recovery needs attention'):
                with stopped_writers({'paperclip_home': directory}, 'vm.service'):
                    pass
        self.assertEqual(started, ['zelab-notes-sshd.service', 'zelab-paperclip.service', 'vm.service'])

    def test_lifecycle_cannot_acquire_backup_lock_until_released(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / 'maintenance.lock'
            command = [sys.executable, '-c',
                       'from pathlib import Path; from runtime import maintenance_lock; '
                       'import sys; \nwith maintenance_lock(Path(sys.argv[1])): pass', str(lock)]
            with maintenance_lock(lock):
                result = subprocess.run(command, cwd=Path(__file__).parent,
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Another Zelab backup or lifecycle operation', result.stderr)
            result = subprocess.run(command, cwd=Path(__file__).parent, capture_output=True)
            self.assertEqual(result.returncode, 0)

    def test_backup_cannot_archive_its_own_output(self):
        paths = [Path('/var/lib/vault'), Path('/var/lib/private')]
        for destination in [paths[0], paths[1] / 'backups']:
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                require_separate_backup(destination, paths)
        require_separate_backup(Path('/var/backups/zelab'), paths)

    def test_backup_directory_accepts_operator_acl_but_not_wider_access(self):
        operator = pwd.getpwuid(os.geteuid()).pw_name
        uid = os.geteuid()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'backups'
            self.assertEqual(private_directory(destination, operator), destination)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
            destination.chmod(0o750)
            with self.assertRaisesRegex(ValueError, 'private to root and the operator'):
                private_directory(destination, operator)
            # Owner rwx, operator r-x, owning group ---, mask r-x, other ---.
            operator_only = [(0x01, 7, -1), (0x02, 5, uid), (0x04, 0, -1), (0x10, 5, -1), (0x20, 0, -1)]
            with patch('runtime.access_acl', return_value=operator_only):
                private_directory(destination, operator)
            with patch('runtime.access_acl', return_value=[*operator_only, (0x08, 5, 4242)]), \
                    self.assertRaisesRegex(ValueError, 'setfacl -b'):
                private_directory(destination, operator)
            with patch('runtime.access_acl', return_value=operator_only), \
                    self.assertRaises(ValueError):
                private_directory(destination, None)


if __name__ == '__main__':
    unittest.main()
