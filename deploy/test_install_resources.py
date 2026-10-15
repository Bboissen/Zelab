"""Exercise installation resource ownership, budgets and failure recovery."""

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

import install_resources as resources
import install
import run
from fixtures import replay


class ResourceBudgetTest(unittest.TestCase):
    def setUp(self):
        self.site = yaml.safe_load(Path('config/defaults.yaml').read_text())

    def test_seventy_percent_of_idle_cpu_and_ram_preserves_the_host_reserve(self):
        plan = resources.plan_resources(self.site, 21 * resources.GIB, 14.6)
        self.assertEqual(plan['cpu'], 10)
        self.assertEqual(plan['cpu_quota'], '1000%')
        self.assertLessEqual(plan['memory_mb'] * resources.MIB, 0.7 * 21 * resources.GIB)
        self.assertLessEqual(resources.memory_bytes(plan['memory_max']), 13 * resources.GIB)
        self.assertGreater(resources.memory_bytes(plan['memory_max']), plan['memory_mb'] * resources.MIB)

    def test_busy_hosts_do_not_lose_the_normal_vm_allocation(self):
        plan = resources.plan_resources(self.site, 3 * resources.GIB, 0.1)
        self.assertEqual(plan, {'cpu': self.site['vm_cpu'], 'memory_mb': self.site['vm_memory_mb'],
                                'cpu_quota': self.site['vm_cpu_quota'], 'memory_max': self.site['vm_memory_max']})

    def test_disabled_boost_preserves_explicit_native_limits(self):
        self.site['vm_install_resource_fraction'] = 0
        plan = resources.plan_resources(self.site, 100 * resources.GIB, 32)
        self.assertEqual(plan['cpu'], self.site['vm_cpu'])
        self.assertEqual(plan['memory_mb'], self.site['vm_memory_mb'])
        self.assertEqual(plan['cpu_quota'], self.site['vm_cpu_quota'])

    def test_existing_large_cpu_counts_do_not_grant_more_than_spare_capacity(self):
        self.site['vm_cpu'] = 32
        plan = resources.plan_resources(self.site, 21 * resources.GIB, 14.6)
        self.assertEqual(plan['cpu'], 32)
        self.assertEqual(plan['cpu_quota'], '1000%')

    def test_fraction_is_validated_before_provisioning(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / 'site.yaml'
            values = yaml.safe_load(Path('config/site.example.yaml').read_text())
            for value in [-0.1, 1.01, True, '0.7', float('nan'), float('inf')]:
                values['vm_install_resource_fraction'] = value
                site.write_text(yaml.safe_dump(values))
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'vm_install_resource_fraction'):
                    run.read_inputs(site, None)


class ResourceRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.site = yaml.safe_load(Path('config/defaults.yaml').read_text())
        # A throttled one-vCPU guest exercises the whole-vCPU startup quota.
        self.site.update(vm_cpu=1, vm_memory_mb=2048, vm_cpu_quota='50%', vm_memory_max='2304M')
        self.budget = resources.InstallationResources(
            self.site, unit_directory=self.root / 'etc',
            runtime_directory=self.root / 'run', receipt_directory=self.root / 'receipt',
            control_directory=self.root / 'control')
        self.budget.unit_file.parent.mkdir()
        self.normal = ('[Unit]\nDescription=Zelab native Hermes guest\n[Service]\nUser=zelab-vm\n'
                       'RootDirectory=/var/lib/zelab-vm/root\n'
                       'BindPaths=/var/lib/zelab-vm/data:/data /dev/kvm\n'
                       'ExecStart=/usr/bin/qemu-system-x86_64 -smp 1 -m 2048 -drive file=/data/disk.qcow2\n'
                       'CPUQuota=50%\nMemoryMax=2304M\n')
        self.budget.unit_file.write_text(self.normal)
        self.budget.unit_file.chmod(0o644)
        self.active = False
        self.commands = []

        def command(*args):
            self.commands.append(args)
            if args[:2] == ('systemctl', 'show'):
                return 'active\n' if self.active else 'inactive\n'
            return ''

        for target, kwargs in [
                ('install_resources.command', {'side_effect': command}),
                ('install_resources.sample_host', {'return_value': (21 * resources.GIB, 14.6)}),
                ('install_resources.InstallationResources.dashboard_ready', {'return_value': True}),
                ('install_resources.InstallationResources.wait_for_guest', {})]:
            mocked = patch(target, **kwargs)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_fresh_boost_and_restore_leave_permanent_config_and_other_dropins_intact(self):
        self.budget.override.parent.mkdir(parents=True, mode=0o755)
        unrelated = self.budget.override.parent / 'unrelated.conf'
        unrelated.write_text('[Service]\nRestartSec=10\n')
        plan = self.budget.begin()
        self.assertTrue(plan['changed'])
        self.assertIn('-smp 10 -m ', self.budget.override.read_text())
        self.assertIn('CPUQuota=1000%', self.budget.override.read_text())
        self.assertEqual(self.budget.receipt.stat().st_mode & 0o777, 0o600)
        self.active = True
        with redirect_stdout(io.StringIO()):
            self.budget.restore()
        self.assertEqual(self.budget.unit_file.read_text(), self.normal)
        self.assertTrue(unrelated.exists())
        self.assertFalse(self.budget.override.exists())
        self.assertFalse(self.budget.receipt.exists())
        lifecycle = [args for args in self.commands if args[:2] != ('systemctl', 'show')]
        self.assertEqual(lifecycle[-6:], [('systemctl', 'stop', 'zelab-vm.service'),
                                          ('systemctl', 'daemon-reload'),
                                          ('systemctl', 'start', 'zelab-vm.service'),
                                          ('systemctl', 'set-property', '--runtime', 'zelab-vm.service', 'CPUQuota=100%'),
                                          ('systemctl', 'set-property', '--runtime', 'zelab-vm.service', 'CPUQuota=50%'),
                                          ('systemctl', 'daemon-reload')])

    def test_restarted_guest_returns_to_the_normal_quota_even_if_startup_fails(self):
        self.budget.begin()
        self.active = True
        self.budget.startup_quota.parent.mkdir(parents=True)
        written = []

        def command(*args):
            self.commands.append(args)
            if args[:3] == ('systemctl', 'set-property', '--runtime'):
                written.append(args[-1])
                self.budget.startup_quota.write_text('[Service]\n' + args[-1] + '\n')
            return 'active\n' if args[:2] == ('systemctl', 'show') else ''

        with patch('install_resources.command', side_effect=command), \
                patch('install_resources.InstallationResources.wait_for_guest', side_effect=RuntimeError('slow')), \
                redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            self.budget.restore()
        self.assertEqual(written, ['CPUQuota=100%', 'CPUQuota=50%'])
        self.assertFalse(self.budget.startup_quota.parent.exists())

    def test_an_existing_runtime_quota_is_left_alone(self):
        self.budget.begin()
        self.active = True
        self.budget.startup_quota.parent.mkdir(parents=True)
        self.budget.startup_quota.write_text('[Service]\nCPUQuota=30%\n')
        with redirect_stdout(io.StringIO()):
            self.budget.restore()
        self.assertFalse(any(args[1] == 'set-property' for args in self.commands))
        self.assertEqual(self.budget.startup_quota.read_text(), '[Service]\nCPUQuota=30%\n')

    def test_boosted_vm_stops_before_the_normal_limit_is_loaded(self):
        self.budget.begin()
        self.active = True
        override_at_stop = []
        real_command = resources.command

        def command(*args):
            if args[:2] == ('systemctl', 'stop'):
                override_at_stop.append(self.budget.override.exists())
            return real_command(*args)

        with patch('install_resources.command', side_effect=command), redirect_stdout(io.StringIO()):
            self.budget.restore()
        self.assertEqual(override_at_stop, [True])

    def test_running_vm_reapply_has_no_resource_changes_or_restart(self):
        self.active = True
        self.assertFalse(self.budget.begin()['changed'])
        self.assertFalse(self.budget.restore()['changed'])
        self.assertFalse(self.budget.override.exists())
        self.assertFalse(self.budget.receipt.exists())
        self.assertFalse(any(args[:2] in {('systemctl', 'stop'), ('systemctl', 'start')} for args in self.commands))

    def test_reset_preview_can_select_only_the_proven_runtime_artifacts_without_mutating_them(self):
        self.budget.begin()
        before = (self.budget.override.read_bytes(), self.budget.receipt.read_bytes(), list(self.commands))
        self.assertEqual(self.budget.owned_files(), [self.budget.receipt, self.budget.override])
        self.assertEqual((self.budget.override.read_bytes(), self.budget.receipt.read_bytes(), self.commands), before)

    def test_cleanup_before_first_boot_does_not_start_a_vm(self):
        self.budget.begin()
        with redirect_stdout(io.StringIO()):
            self.budget.restore()
        self.assertFalse(any(args[:2] in {('systemctl', 'stop'), ('systemctl', 'start')} for args in self.commands))
        self.assertFalse(self.budget.override.exists())

    def test_interruption_after_receipt_before_override_is_recoverable(self):
        real_write = resources.atomic_write

        def write(path, text, mode):
            if path == self.budget.override:
                raise OSError('interrupted write')
            real_write(path, text, mode)

        with patch('install_resources.atomic_write', side_effect=write), self.assertRaises(OSError):
            self.budget.begin()
        with redirect_stdout(io.StringIO()):
            self.budget.restore()
        self.assertFalse(self.budget.receipt.exists())
        self.assertEqual(self.budget.unit_file.read_text(), self.normal)

    def test_interrupted_reset_can_recover_resource_files_after_removing_a_stopped_unit(self):
        self.budget.begin()
        self.budget.unit_file.unlink()
        self.assertEqual(self.budget.owned_files(), [self.budget.receipt, self.budget.override])
        with patch('install_resources.subprocess.run') as execute, redirect_stdout(io.StringIO()):
            execute.return_value.returncode = 3
            self.budget.restore()
        self.assertFalse(self.budget.override.exists())
        self.assertFalse(self.budget.receipt.exists())
        self.assertFalse(any(args[:2] in {('systemctl', 'stop'), ('systemctl', 'start')} for args in self.commands))

    def test_missing_native_unit_cannot_restore_a_vm_that_is_still_running(self):
        self.budget.begin()
        self.budget.unit_file.unlink()
        with patch('install_resources.subprocess.run') as execute:
            execute.return_value.returncode = 0
            with self.assertRaisesRegex(RuntimeError, 'stopped state is unproven'):
                self.budget.restore()
        self.assertTrue(self.budget.override.exists())
        self.assertTrue(self.budget.receipt.exists())

    def test_failed_restart_retains_a_receipt_for_the_next_recovery(self):
        self.budget.begin()
        self.active = True
        real_command = resources.command

        def command(*args):
            if args[:2] == ('systemctl', 'stop'):
                self.active = False
            if args[:2] == ('systemctl', 'start'):
                raise RuntimeError('restart failed')
            return real_command(*args)

        with patch('install_resources.command', side_effect=command), redirect_stdout(io.StringIO()), \
                self.assertRaisesRegex(RuntimeError, 'restart failed'):
            self.budget.restore()
        self.assertFalse(self.budget.override.exists())
        self.assertTrue(json.loads(self.budget.receipt.read_text())['restart_pending'])
        self.commands.clear()
        with redirect_stdout(io.StringIO()):
            self.budget.restore()
        self.assertFalse(self.budget.receipt.exists())
        self.assertIn(('systemctl', 'start', 'zelab-vm.service'), self.commands)

    def test_failed_stop_keeps_the_boost_loaded_for_the_next_recovery(self):
        self.budget.begin()
        self.active = True
        self.commands.clear()
        real_command = resources.command

        def command(*args):
            if args[:2] == ('systemctl', 'stop'):
                raise RuntimeError('stop failed')
            return real_command(*args)

        with patch('install_resources.command', side_effect=command), redirect_stdout(io.StringIO()), \
                self.assertRaisesRegex(RuntimeError, 'stop failed'):
            self.budget.restore()
        self.assertTrue(self.budget.override.exists())
        self.assertNotIn(('systemctl', 'daemon-reload'), self.commands)
        self.assertTrue(json.loads(self.budget.receipt.read_text())['restart_pending'])

    def test_changed_override_or_foreign_receipt_is_never_removed(self):
        self.budget.begin()
        original = self.budget.override.read_text()
        self.budget.override.write_text(original + '# another editor\n')
        with self.assertRaisesRegex(RuntimeError, 'override was changed'):
            self.budget.restore()
        self.assertTrue(self.budget.override.exists())
        self.budget.override.write_text(original)
        receipt = json.loads(self.budget.receipt.read_text())
        receipt['vm_data'] = '/unrelated/data'
        self.budget.receipt.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(RuntimeError, 'another VM'):
            self.budget.restore()
        self.assertTrue(self.budget.override.exists())

    def test_unknown_override_and_unrelated_vm_unit_are_rejected(self):
        self.budget.override.parent.mkdir(parents=True)
        self.budget.override.write_text('[Service]\nCPUQuota=900%\n')
        with self.assertRaisesRegex(RuntimeError, 'no ownership receipt'):
            self.budget.restore()
        self.budget.override.unlink()
        self.budget.unit_file.write_text(self.normal.replace('User=zelab-vm', 'User=someone-else'))
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            self.budget.begin()
        self.assertFalse(self.budget.receipt.exists())

    def test_symlink_to_another_override_is_never_followed_or_removed(self):
        self.budget.begin()
        target = self.root / 'another.conf'
        target.write_text(self.budget.override.read_text())
        self.budget.override.unlink()
        self.budget.override.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, 'owned regular files'):
            self.budget.restore()
        self.assertTrue(target.exists())
        self.assertTrue(self.budget.override.is_symlink())


class ResourceOutputTest(unittest.TestCase):
    def runner(self):
        runner = install.Installer(Path('config/site.yaml'), Path('.env'))
        runner.screen.close()
        runner.screen = install.Screen(io.StringIO())
        self.addCleanup(runner.screen.close)
        self.addCleanup(runner.log.close)
        return runner

    def test_orphaned_resources_are_restored_before_memory_readiness_checks(self):
        runner = self.runner()
        with patch.object(runner, 'capture', return_value=(0, [])) as capture:
            runner.readiness()
        calls = [call.args[0] for call in capture.call_args_list]
        self.assertIn('deploy/install_resources.py', calls[0])
        self.assertIn('restore', calls[0])
        self.assertIn('deploy/doctor.py', calls[1])

    def test_resource_recovery_failure_stops_before_readiness_or_provisioning(self):
        runner = self.runner()
        with patch.object(runner, 'capture', return_value=(1, ['Installation resources: restart failed'])) as capture, \
                self.assertRaises(install.StepFailed):
            runner.readiness()
        self.assertEqual(capture.call_count, 1)
        self.assertIn('VM resource recovery failed', runner.screen.stream.getvalue())

    def test_interrupted_site_capture_allows_resource_restoration_to_finish(self):
        runner = self.runner()

        def interrupt(line):
            raise KeyboardInterrupt

        with patch('install.subprocess.Popen') as popen:
            process = popen.return_value
            process.stdout = io.StringIO('installing\n')
            with self.assertRaises(KeyboardInterrupt):
                runner.capture(['python3', 'deploy/run.py', 'deploy/site.yaml'], on_line=interrupt)
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=360)
            process.kill.assert_not_called()

    def test_restoration_does_not_mark_a_failed_installation_stage_as_successful(self):
        runner = install.Installer(Path('config/site.yaml'), Path('.env'))
        runner.screen.close()
        runner.screen = install.Screen(io.StringIO())
        self.addCleanup(runner.screen.close)
        self.addCleanup(runner.log.close)
        lines = ['PLAY [Install pinned unmodified Hermes in the adopted guest] ***\n',
                 'TASK [hermes : Install dependencies] ***\n',
                 'Installation stopped.\n', '  Cause: dependency failure\n',
                 'RESOURCE RESTORE START\n',
                 'RESOURCE RESTORE DONE {"cpu": 1, "memory_mb": 2048, "cpu_quota": "50%"}\n']

        capture = replay(lines, 2)

        with patch.object(runner, 'capture', side_effect=capture), self.assertRaises(install.StepFailed):
            runner.deploy()
        output = runner.screen.stream.getvalue()
        self.assertIn('✗ Hermes', output)
        self.assertNotIn('✓ Hermes', output)
        self.assertIn('✓ VM normal limits', output)
        self.assertIn('dependency failure', output)
        timings = [json.loads(line.removeprefix('TIMING ')) for line in ''.join(runner.log.buffer).splitlines()
                   if line.startswith('TIMING ')]
        self.assertEqual([(item['stage'], item['result']) for item in timings],
                         [('Hermes', 'FAIL'), ('VM normal limits', 'PASS')])

    def test_restore_failure_prevents_installation_success(self):
        runner = install.Installer(Path('config/site.yaml'), Path('.env'))
        runner.screen.close()
        runner.screen = install.Screen(io.StringIO())
        self.addCleanup(runner.screen.close)
        self.addCleanup(runner.log.close)

        def capture(argv, on_line=None, env=None):
            for line in ['PLAY [Deploy the native Obsidian browser container and private HTTPS] ***\n',
                         'RESOURCE RESTORE START\n', 'Installation resources: restart failed\n']:
                on_line(line)
            return 1, []

        with patch.object(runner, 'capture', side_effect=capture), self.assertRaises(install.StepFailed):
            runner.deploy()
        output = runner.screen.stream.getvalue()
        self.assertIn('✗ VM normal limits', output)
        self.assertIn('Cause: restart failed', output)


if __name__ == '__main__':
    unittest.main()
