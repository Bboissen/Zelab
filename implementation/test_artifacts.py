"""Real disposable artifacts exercise discovery, preservation and active-use refusal."""

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import pwd
import tempfile
import unittest
from unittest.mock import patch

import artifacts
import fresh


class ArtifactTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name).resolve()
        self.repository = self.base / 'checkout'
        self.repository.mkdir()
        self.site = {'operator_user': pwd.getpwuid(os.geteuid()).pw_name}
        self.proc = self.base / 'proc'
        self.proc.mkdir()
        (self.proc / str(os.getpid())).symlink_to(Path('/proc') / str(os.getpid()), target_is_directory=True)

    def discover(self):
        plan = fresh.Plan('fixture:zelab')
        artifacts.discover(plan, self.site, self.repository, roots=[self.base])
        return plan

    def test_receipt_binds_scratch_to_both_checkout_and_exact_path(self):
        scratch = self.base / 'zelab-paperclip-check-fixture'
        scratch.mkdir(mode=0o700)
        artifacts.record(scratch, self.repository)
        self.assertEqual(self.discover().artifact_paths, [scratch])
        moved = self.base / 'zelab-moved'
        scratch.rename(moved)
        self.assertEqual(self.discover().artifact_paths, [])
        self.assertFalse(artifacts.receipt_matches(moved, self.repository, os.geteuid()))

    def test_other_checkouts_names_and_symlinks_do_not_prove_ownership(self):
        scratch = self.base / 'zelab-other'
        scratch.mkdir(mode=0o700)
        artifacts.record(scratch, self.base / 'other-checkout')
        (self.base / 'zelab-alias').symlink_to(scratch, target_is_directory=True)
        (self.base / 'zelab-unmarked').mkdir()
        self.assertEqual(self.discover().artifact_paths, [])
        (scratch / artifacts.RECEIPT).unlink()
        artifacts.record(scratch, self.repository)
        scratch.chmod(0o777)
        self.assertEqual(self.discover().artifact_paths, [])

    def test_legacy_cleanup_preserves_reports_logs_and_git_bundles(self):
        qualification = self.base / 'zelab-v11-qual'
        (qualification / 'scripts').mkdir(parents=True)
        qualification.chmod(0o700)
        for name in ('QUALIFICATION.md', 'old.log', 'saved.bundle'):
            (qualification / name).write_text('evidence')
        script = qualification / 'scripts/run.py'
        script.write_text(f"repository = {str(self.repository)!r}\n")
        for name in ('vm', 'pc-runtime', 'dl'):
            (qualification / name).mkdir()
        (qualification / 'private').mkdir()
        with patch.object(artifacts, 'qualification_units', return_value=[]):
            plan = self.discover()
        self.assertEqual({p.name for p in plan.artifact_paths}, {'vm', 'pc-runtime', 'dl'})
        self.assertTrue(all(str(qualification / name) not in {p for p, _ in plan.directories}
                            for name in ('private', 'saved.bundle', 'old.log', 'QUALIFICATION.md')))
        script.write_text('repository = "another checkout"\n')
        self.assertEqual(self.discover().artifact_paths, [])
        script.write_text(f"repository = {str(self.repository) + '-other'!r}\n")
        self.assertEqual(self.discover().artifact_paths, [])

    def test_qemu_unit_requires_owned_disk_and_direct_system_service_cgroup(self):
        vm = self.base / 'vm'
        vm.mkdir()
        proc = self.base / 'qemu-proc'
        process = proc / '123'
        process.mkdir(parents=True)
        (process / 'comm').write_text('qemu-system-x86\n')
        (process / 'cmdline').write_bytes(f'qemu\0-drive\0file={vm}/disk.qcow2,if=virtio\0'.encode())
        cgroup = process / 'cgroup'
        cgroup.write_text('0::/system.slice/zelab-v11-qual-vm.service\n')
        self.assertEqual(artifacts.qualification_units([vm], os.geteuid(), proc), ['zelab-v11-qual-vm.service'])
        self.assertEqual(artifacts.qualification_units([self.base / 'other'], os.geteuid(), proc), [])
        cgroup.write_text('0::/user.slice/zelab-v11-qual-vm.service\n')
        self.assertEqual(artifacts.qualification_units([vm], os.geteuid(), proc), [])

    def test_open_artifact_file_blocks_cleanup_until_closed(self):
        scratch = self.base / 'zelab-build'
        scratch.mkdir()
        with (scratch / 'download.tar').open('w'):
            with self.assertRaisesRegex(RuntimeError, 'still in use'):
                artifacts.require_unused([scratch], self.proc)
        artifacts.require_unused([scratch], self.proc)

    def test_scratch_paperclip_unit_needs_own_postgres_runtime_data_and_operator(self):
        runtime = self.base / 'pc-runtime'
        database = self.base / 'pc-home/instances/default/db'
        executable = runtime / 'node_modules/@embedded-postgres/linux-x64/native/bin/postgres'
        executable.parent.mkdir(parents=True)
        executable.write_text('fixture')
        database.mkdir(parents=True)
        proc = self.base / 'paperclip-proc'
        process = proc / '123'
        process.mkdir(parents=True)
        (process / 'exe').symlink_to(executable)
        (process / 'cwd').symlink_to(database, target_is_directory=True)
        uid = os.geteuid()
        cgroup = process / 'cgroup'
        cgroup.write_text(f'0::/user.slice/user-{uid}.slice/user@{uid}.service/app.slice/zelab-v11-qual-pc.service\n')
        self.assertEqual(artifacts.qualification_user_units([runtime], uid, proc), ['zelab-v11-qual-pc.service'])
        self.assertEqual(artifacts.qualification_user_units([runtime], uid + 1, proc), [])
        (process / 'cwd').unlink()
        (process / 'cwd').symlink_to(self.base / 'production-db', target_is_directory=True)
        self.assertEqual(artifacts.qualification_user_units([runtime], uid, proc), [])

    def test_confirmed_reset_removes_disposable_outputs_and_keeps_unrelated_evidence(self):
        scratch = self.base / 'zelab-build'
        scratch.mkdir(mode=0o700)
        artifacts.record(scratch, self.repository)
        (scratch / 'download.tar').write_text('disposable')
        report = self.base / 'saved.bundle'
        report.write_text('preserve')
        plan = self.discover()
        plan.transient_units = ['zelab-v11-qual-vm.service']
        plan.user_units = ['zelab-v11-qual-pc.service']
        snapshot = {'containers': [], 'serve_routes': [], 'accounts': []}
        commands = []
        require_unused = artifacts.require_unused

        def run(*argv, **kwargs):
            commands.append(argv)

        def unused(paths):
            self.assertIn(('systemctl', 'stop', 'zelab-v11-qual-vm.service'), commands)
            self.assertIn(tuple(artifacts.user_systemctl(self.site['operator_user'], 'stop', 'zelab-v11-qual-pc.service')), commands)
            require_unused(paths, self.proc)

        with patch.object(fresh, 'read_inputs', return_value={**self.site, 'private_dir': str(self.base)}), \
             patch.object(fresh.os, 'geteuid', return_value=0), \
             patch.object(fresh, 'discover', return_value=plan), \
             patch.object(fresh, 'maintenance_lock'), patch.object(fresh, 'recover_on_interrupt'), \
             patch.object(fresh, 'command', side_effect=run), \
             patch.object(fresh, 'unrelated_snapshot', return_value=snapshot), \
             patch.object(fresh, 'stop_owned_processes'), \
             patch.object(artifacts, 'qualification_units', return_value=plan.transient_units), \
             patch.object(artifacts, 'qualification_user_units', return_value=plan.user_units), \
             patch.object(artifacts.shell, 'output', return_value='yes\n'), \
             patch.object(artifacts, 'require_unused', side_effect=unused), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(fresh.main(['reset', '--confirm', plan.site_id]), 0)
        self.assertFalse(scratch.exists())
        self.assertEqual(report.read_text(), 'preserve')

    def test_non_transient_qualification_service_is_not_selected(self):
        with patch.object(artifacts, 'qualification_units', return_value=['zelab-v11-qual-vm.service']), \
             patch.object(artifacts, 'qualification_user_units', return_value=['zelab-v11-qual-pc.service']), \
             patch.object(artifacts.shell, 'output', return_value='no\n'):
            plan = self.discover()
        self.assertEqual(plan.transient_units, [])
        self.assertEqual(plan.user_units, [])

    def test_reset_checks_artifact_use_before_stopping_installed_services(self):
        scratch = self.base / 'zelab-build'
        scratch.mkdir()
        plan = fresh.Plan('fixture:zelab', artifact_paths=[scratch])
        require_unused = artifacts.require_unused
        with (scratch / 'download.tar').open('w'), \
             patch.object(fresh, 'maintenance_lock'), patch.object(fresh, 'recover_on_interrupt'), \
             patch.object(artifacts, 'require_unused', side_effect=lambda paths: require_unused(paths, self.proc)), \
             patch.object(fresh, 'command') as command, redirect_stdout(io.StringIO()), \
             self.assertRaisesRegex(RuntimeError, 'still in use'):
            fresh.reset(plan, {})
        command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
