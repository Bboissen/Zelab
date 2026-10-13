"""Verify local choice resets while retaining host settings and outside data."""

from contextlib import nullcontext, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import maintenance
from maintenance import clean


class CleanTest(unittest.TestCase):
    def test_clean_resets_repo_choices_but_keeps_host_settings_notes_and_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('deploy/__pycache__', 'implementation/__pycache__', 'tools/__pycache__', '.venv'):
                (root / name).mkdir(parents=True)
            (root / '.venv/pyvenv.cfg').write_text('home = /usr/bin\n')
            for name in ('.env', 'state/notes.md', 'backups/site.tar.gpg'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('keep')
            site = root / 'config/site.yaml'
            site.parent.mkdir()
            kept = '# host\noperator_user: operator\nworker_ssh_port: 42896\nprivate_dir: /private/site\n'
            site.write_text(kept + "worker_harness: both\nworker_model: selected\nvm_cpu: 9\n"
                            "notes_enabled: false\nnotes_shared_profiles:\n  - research\n"
                            "discord_owner_id: '123456789012345678'\n")
            site.chmod(0o600)
            with redirect_stdout(io.StringIO()):
                clean(root)
            self.assertFalse((root / '.venv').exists())
            self.assertFalse(list(root.rglob('__pycache__')))
            self.assertEqual(site.read_text(), kept)
            self.assertEqual(site.stat().st_mode & 0o777, 0o600)
            # Repeat without the controller or a YAML dependency.
            with redirect_stdout(io.StringIO()):
                clean(root)
            self.assertEqual(site.read_text(), kept)
            for name in ('.env', 'state/notes.md', 'backups/site.tar.gpg'):
                self.assertEqual((root / name).read_text(), 'keep')

    def test_external_inputs_and_public_source_defaults_are_never_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            (root / 'config').mkdir(parents=True)
            external = Path(directory) / 'external.yaml'
            for path in (external, root / 'config/defaults.yaml', root / 'config/site.example.yaml'):
                path.write_text('worker_harness: both\n')
                with redirect_stdout(io.StringIO()):
                    clean(root, path)
                self.assertEqual(path.read_text(), 'worker_harness: both\n')

    def test_symlinked_inputs_and_ambiguous_yaml_are_rejected_before_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            (root / 'config').mkdir(parents=True)
            cache = root / 'deploy/__pycache__'
            cache.mkdir(parents=True)
            external = Path(directory) / 'external.yaml'
            external.write_text('worker_harness: both\n')
            site = root / 'config/site.yaml'
            site.symlink_to(external)
            with self.assertRaises(ValueError):
                clean(root)
            self.assertTrue(cache.exists())
            self.assertEqual(external.read_text(), 'worker_harness: both\n')
            site.unlink()
            for content in ('worker_harness: &choice both\nother: *choice\n',
                            'worker_harness:\n  &choice both\nother: *choice\n',
                            '  worker_harness: both\n',
                            '{worker_harness: both}\n', '<<: *settings\n'):
                site.write_text(content)
                with self.assertRaises(ValueError):
                    clean(root)
                self.assertEqual(site.read_text(), content)
                self.assertTrue(cache.exists())

    def test_clean_and_successful_fclean_forward_the_selected_input(self):
        with patch.object(maintenance, 'clean') as cleaned:
            maintenance.main(['clean', '--site-config', 'config/custom.yaml'])
        cleaned.assert_called_once_with(site_config=Path('config/custom.yaml'))
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / 'site.yaml'
            site.write_text('worker_harness: both\n')
            for code in (0, 2):
                with self.subTest(code=code), \
                     patch.object(maintenance.controller, 'main', return_value=0), \
                     patch.object(maintenance.subprocess, 'run', side_effect=[Mock(returncode=0), Mock(returncode=code)]), \
                     patch.object(maintenance, 'clean') as cleaned, redirect_stdout(io.StringIO()):
                    self.assertEqual(maintenance.main(['fclean', '--site-config', str(site)]), code)
                if code == 0:
                    cleaned.assert_called_once_with(site_config=site)
                else:
                    cleaned.assert_not_called()

    def test_symlinked_or_unknown_controller_is_rejected_before_any_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            outside = Path(directory) / 'outside'
            outside.mkdir()
            cache = root / 'deploy/__pycache__'
            cache.mkdir(parents=True)
            (root / '.venv').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                clean(root)
            self.assertTrue(cache.exists())
            self.assertTrue(outside.exists())
            (root / '.venv').unlink()
            (root / '.venv').mkdir()
            (root / '.venv/notes').write_text('keep')
            with self.assertRaises(ValueError):
                clean(root)
            self.assertEqual((root / '.venv/notes').read_text(), 'keep')

    def test_permission_retry_releases_lock_and_disables_root_bytecode(self):
        guard = Mock()
        lock = Mock()
        lock.__enter__ = Mock()
        lock.__exit__ = Mock(return_value=False)
        guard.exists.return_value = True

        def retry(command, **kwargs):
            lock.__exit__.assert_called_once()
            self.assertEqual(command[:6], ['rtk', 'proxy', 'sudo', '-n', '/usr/local/bin/rtk', 'proxy'])
            self.assertEqual(command[-5:], ['-B', str(Path(maintenance.__file__).resolve()), 'clean',
                                            '--site-config', 'config/site.yaml'])
            self.assertEqual(kwargs['cwd'], maintenance.ROOT)
            return Mock(returncode=0)

        with patch('runtime.MAINTENANCE_LOCK', guard), \
             patch('runtime.maintenance_lock', return_value=lock), \
             patch.object(maintenance, 'clean_files', side_effect=PermissionError('root-owned cache')), \
             patch.object(maintenance.os, 'geteuid', return_value=1000), \
             patch.object(maintenance.sys.stdin, 'isatty', return_value=False), \
             patch.object(maintenance.subprocess, 'run', side_effect=retry) as run, \
             patch.dict(maintenance.os.environ, {}, clear=True), \
             redirect_stdout(io.StringIO()):
            clean()
        run.assert_called_once()

    def test_interactive_cleanup_allows_the_native_sudo_password_prompt(self):
        with patch('runtime.maintenance_lock', return_value=nullcontext()), \
             patch.object(maintenance, 'clean_files', side_effect=PermissionError), \
             patch.object(maintenance.os, 'geteuid', return_value=1000), \
             patch.object(maintenance.sys.stdin, 'isatty', return_value=True), \
             patch.object(maintenance.subprocess, 'run', return_value=Mock(returncode=0)) as run, \
             redirect_stdout(io.StringIO()) as output:
            clean()
        command = run.call_args.args[0]
        self.assertEqual(command[:5], ['rtk', 'proxy', 'sudo', '/usr/local/bin/rtk', 'proxy'])
        self.assertNotIn('-n', command)
        self.assertEqual(output.getvalue(), '')

    def test_permission_preflight_keeps_the_venv_marker_and_caches_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / 'tools/__pycache__'
            cache.mkdir(parents=True)
            venv = root / '.venv'
            (venv / 'lib/root-owned').mkdir(parents=True)
            (venv / 'pyvenv.cfg').write_text('home = /usr/bin\n')
            with patch.object(maintenance.os, 'access', side_effect=lambda path, mode: Path(path).name != 'root-owned'), \
                 self.assertRaises(PermissionError):
                clean(root)
            self.assertTrue(cache.exists())
            self.assertTrue((venv / 'pyvenv.cfg').is_file())

    def test_failed_privileged_retry_reports_an_actionable_error(self):
        with patch('runtime.maintenance_lock', return_value=nullcontext()), \
             patch.object(maintenance, 'clean_files', side_effect=PermissionError), \
             patch.object(maintenance.os, 'geteuid', return_value=1000), \
             patch.object(maintenance.subprocess, 'run', return_value=Mock(returncode=1)), \
             redirect_stdout(io.StringIO()), \
             self.assertRaisesRegex(RuntimeError, 'rerun rtk proxy make clean'):
            clean()

    def test_permission_error_never_escalates_other_roots_or_retries_as_root(self):
        with tempfile.TemporaryDirectory() as directory:
            for root, uid in [(Path(directory), 1000), (maintenance.ROOT, 0)]:
                with self.subTest(root=root, uid=uid), \
                     patch('runtime.maintenance_lock', return_value=nullcontext()), \
                     patch.object(maintenance, 'clean_files', side_effect=PermissionError), \
                     patch.object(maintenance.os, 'geteuid', return_value=uid), \
                     patch.object(maintenance.subprocess, 'run') as run, \
                     self.assertRaises(PermissionError):
                    clean(root)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
