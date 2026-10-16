"""Credential custody, interrupted migration and terminal-only input boundaries."""

from contextlib import redirect_stdout, redirect_stderr
import io
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import passwords
from run import read_inputs


class PasswordTest(unittest.TestCase):
    def test_loaded_site_prepares_obsidian_with_default_and_overridden_paths(self):
        for override in (None, '/etc/zelab-test-obsidian'):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = root / 'site.yaml'
                config.write_text(f'operator_user: operator\nprivate_dir: {root}/private\nobsidian_enabled: true\n' +
                                  (f'obsidian_config_dir: {override}\n' if override else ''))
                site = read_inputs(config, None)
                runtime = root / 'runtime'
                runtime.mkdir(mode=0o700)
                with patch.object(passwords, 'prompt_password', side_effect=[
                        'hermes-example-password', 'paperclip-example-password',
                        'obsidian-example-password']) as prompt, \
                     patch.object(passwords, 'installed', return_value=False) as installed, \
                     redirect_stdout(io.StringIO()):
                    passwords.prepare(site, runtime)
                self.assertEqual([call.args[0] for call in prompt.call_args_list],
                                 ['Hermes', 'Paperclip', 'Obsidian'])
                installed.assert_any_call(Path(override or '/etc/zelab-obsidian') / 'password')
                self.assertEqual(passwords.read_private(runtime / 'obsidian.password'),
                                 'obsidian-example-password')

    def test_default_site_asks_no_browser_obsidian_password(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'site.yaml'
            config.write_text(f'operator_user: operator\nprivate_dir: {root}/private\n')
            site = read_inputs(config, None)
            runtime = root / 'runtime'
            runtime.mkdir(mode=0o700)
            with patch.object(passwords, 'prompt_password', side_effect=[
                    'hermes-example-password', 'paperclip-example-password']) as prompt, \
                 patch.object(passwords, 'installed', return_value=False), redirect_stdout(io.StringIO()):
                passwords.prepare(site, runtime)
            self.assertEqual([call.args[0] for call in prompt.call_args_list], ['Hermes', 'Paperclip'])

    def test_loaded_site_resumes_after_failure_before_obsidian_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'site.yaml'
            config.write_text(f'operator_user: operator\nprivate_dir: {root}/private\nobsidian_enabled: true\n')
            site = read_inputs(config, None)
            private = Path(site['private_dir'])
            private.mkdir(mode=0o700)
            encoded = passwords.hash_password('previously-entered-hermes-password')
            passwords.write_private(private / 'hermes-dashboard.password-hash', encoded)
            runtime = root / 'runtime'
            runtime.mkdir(mode=0o700)
            with patch.object(passwords, 'prompt_password', side_effect=[
                    'paperclip-example-password', 'obsidian-example-password']) as prompt, \
                 patch.object(passwords, 'installed', return_value=False), redirect_stdout(io.StringIO()):
                passwords.prepare(site, runtime)
            self.assertEqual([call.args[0] for call in prompt.call_args_list], ['Paperclip', 'Obsidian'])
            self.assertEqual(passwords.read_private(private / 'hermes-dashboard.password-hash'), encoded)
            self.assertFalse((runtime / 'hermes-dashboard.password').exists())

    def fixture(self, directory):
        root = Path(directory)
        private, runtime = root / 'private', root / 'runtime'
        private.mkdir(mode=0o700)
        runtime.mkdir(mode=0o700)
        return {'private_dir': str(private), 'guest_known_hosts': str(root / 'guest-pin'),
                'paperclip_home': str(root / 'paperclip'), 'obsidian_config_dir': str(root / 'obsidian'),
                'obsidian_enabled': True}, private, runtime

    def test_fresh_setup_has_three_distinct_prompts_and_no_persistent_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            site, private, runtime = self.fixture(directory)
            entered = ['hermes-example-password', 'paperclip-example-password', 'obsidian-example-password']
            with patch.object(passwords, 'prompt_password', side_effect=entered) as prompt, \
                 patch.object(passwords, 'installed', return_value=False), redirect_stdout(io.StringIO()) as output:
                passwords.prepare(site, runtime)
            self.assertEqual([call.args[0] for call in prompt.call_args_list], ['Hermes', 'Paperclip', 'Obsidian'])
            self.assertEqual([p.name for p in private.iterdir()], ['hermes-dashboard.password-hash'])
            self.assertTrue(passwords.matches(entered[0], passwords.read_private(private / 'hermes-dashboard.password-hash')))
            self.assertFalse(passwords.matches('wrong-password', passwords.read_private(private / 'hermes-dashboard.password-hash')))
            for value in entered:
                self.assertNotIn(value, output.getvalue())
            for path in [*private.iterdir(), *runtime.iterdir()]:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_migration_preserves_passwords_and_hash_across_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            site, private, runtime = self.fixture(directory)
            for name in ['hermes-dashboard.password', 'paperclip-owner.password', 'obsidian.password']:
                passwords.write_private(private / name, 'existing-example-password\n')
            with patch.object(passwords, 'prompt_password', side_effect=AssertionError('must not prompt')), redirect_stdout(io.StringIO()):
                passwords.prepare(site, runtime)
                first_hash = passwords.read_private(private / 'hermes-dashboard.password-hash')
                retry = Path(directory) / 'retry'
                retry.mkdir(mode=0o700)
                passwords.prepare(site, retry)
            self.assertEqual(passwords.read_private(private / 'hermes-dashboard.password-hash'), first_hash)
            self.assertEqual(passwords.read_private(runtime / 'paperclip-owner.password'), 'existing-example-password')
            self.assertTrue((private / 'hermes-dashboard.password').exists())

    def test_mismatch_never_rotates_a_retained_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            site, private, runtime = self.fixture(directory)
            passwords.write_private(private / 'hermes-dashboard.password', 'original-password')
            encoded = passwords.hash_password('different-password')
            passwords.write_private(private / 'hermes-dashboard.password-hash', encoded)
            with self.assertRaises(ValueError):
                passwords.prepare(site, runtime)
            self.assertEqual(passwords.read_private(private / 'hermes-dashboard.password-hash'), encoded)

    def test_lost_existing_guest_source_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            site, private, runtime = self.fixture(directory)
            Path(site['guest_known_hosts']).touch()
            with patch.object(passwords, 'prompt_password', side_effect=AssertionError('must not rotate')), self.assertRaises(ValueError):
                passwords.prepare(site, runtime)
            self.assertEqual(list(private.iterdir()), [])

    def test_custody_confirmation_removes_only_redundant_deployment_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            site, private, runtime = self.fixture(directory)
            for name in ['hermes-dashboard.password', 'paperclip-owner.password', 'obsidian.password']:
                passwords.write_private(private / name, 'existing-example-password')
            with redirect_stdout(io.StringIO()):
                passwords.prepare(site, runtime)
                with patch.object(passwords.sys.stdin, 'isatty', return_value=False):
                    passwords.finish(site, runtime)
                self.assertTrue((private / 'paperclip-owner.password').exists())
                with patch.object(passwords.sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value='yes'):
                    passwords.finish(site, runtime)
            self.assertEqual([p.name for p in private.iterdir()], ['hermes-dashboard.password-hash'])
            self.assertTrue((runtime / 'paperclip-owner.password').exists())
            retry = Path(directory) / 'retry'
            retry.mkdir(mode=0o700)
            with patch.object(passwords, 'prompt_password', return_value='existing-example-password') as prompt, \
                 patch.object(passwords, 'installed', return_value=True), redirect_stdout(io.StringIO()):
                passwords.prepare(site, retry)
            prompt.assert_called_once_with('Paperclip', fresh=False)
            self.assertFalse((retry / 'obsidian.password').exists())

    def test_unsafe_source_permissions_and_symlinks_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'password'
            path.write_text('example')
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                passwords.read_private(path)
            path.chmod(0o600)
            link = Path(directory) / 'link'
            link.symlink_to(path)
            with self.assertRaises(OSError):
                passwords.read_private(link)

    def test_cleanup_refuses_changed_recovery_copies_and_keeps_machine_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            site, private, runtime = self.fixture(directory)
            for name in ['hermes-dashboard.password', 'paperclip-owner.password', 'obsidian.password']:
                passwords.write_private(private / name, 'existing-example-password')
            passwords.write_private(private / 'paperclip-master.key', 'machine-key-fixture')
            with redirect_stdout(io.StringIO()):
                passwords.prepare(site, runtime)
                (private / 'obsidian.password').write_text('changed-example-password')
                with patch.object(passwords.sys.stdin, 'isatty', return_value=True), \
                     patch('builtins.input', return_value='yes'), self.assertRaises(ValueError):
                    passwords.finish(site, runtime)
            self.assertTrue((private / 'hermes-dashboard.password').exists())
            self.assertTrue((private / 'paperclip-owner.password').exists())
            self.assertEqual(passwords.read_private(private / 'paperclip-master.key'), 'machine-key-fixture')

    def test_noninteractive_missing_password_never_reads_stdin(self):
        with patch.object(passwords.sys.stdin, 'isatty', return_value=False), \
             patch.object(passwords.getpass, 'getpass', side_effect=AssertionError('no stdin')):
            with self.assertRaises(ValueError):
                passwords.prompt_password('Paperclip', fresh=False)

    def test_terminal_echo_fallback_is_refused(self):
        import getpass
        import warnings
        def insecure_input(*args, **kwargs):
            warnings.warn('cannot disable echo', getpass.GetPassWarning)
            raise AssertionError('echoing fallback must never be reached')
        with patch.object(passwords.sys.stdin, 'isatty', return_value=True), \
             patch.object(passwords.getpass, 'getpass', side_effect=insecure_input), redirect_stdout(io.StringIO()):
            with self.assertRaises(getpass.GetPassWarning):
                passwords.prompt_password('Hermes', fresh=True)

    def test_real_terminal_never_echoes_either_password_entry(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        program = ('import sys; sys.path.insert(0,"deploy"); import passwords; '
                   'passwords.prompt_password("Hermes",fresh=True); print("PROMPT_COMPLETE")')
        child = subprocess.Popen([sys.executable, '-c', program],
                                 stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                                 cwd=Path(__file__).resolve().parents[1])
        os.close(slave)
        self.addCleanup(lambda: child.kill() if child.poll() is None else None)
        output = bytearray()
        def until(marker):
            while marker not in output:
                self.assertTrue(select.select([master], [], [], 10)[0], 'terminal prompt timed out')
                output.extend(os.read(master, 4096))
        fixture = b'terminal-fixture-password'
        until(b'Hermes password: ')
        self.assertLess(output.index(b'Hermes: paste'), output.index(b'Hermes password: '))
        os.write(master, fixture + b'\n')
        until(b'Confirm Hermes password: ')
        os.write(master, fixture + b'\n')
        until(b'PROMPT_COMPLETE')
        self.assertEqual(child.wait(timeout=10), 0)
        self.assertNotIn(fixture, output)
        self.assertNotIn(b'press Enter', output)

    def test_confirmation_and_validation_never_echo_passwords(self):
        output = io.StringIO()
        with patch.object(passwords.sys.stdin, 'isatty', return_value=True), \
             patch.object(passwords.getpass, 'getpass', side_effect=[
                 'short', 'first-example-password', 'different-example-password',
                 'final-example-password', 'final-example-password']), \
             patch('builtins.input', side_effect=AssertionError('no third prompt')), \
             redirect_stdout(output), redirect_stderr(output):
            value = passwords.prompt_password('Hermes', fresh=True)
        self.assertEqual(value, 'final-example-password')
        self.assertNotIn('example-password', output.getvalue())

    def test_matching_password_confirmation_moves_to_the_next_service(self):
        output = io.StringIO()
        with patch.object(passwords.sys.stdin, 'isatty', return_value=True), \
             patch.object(passwords.getpass, 'getpass', side_effect=[
                 'hermes-fixture-password', 'hermes-fixture-password',
                 'paperclip-fixture-password', 'paperclip-fixture-password']) as hidden_input, \
             patch('builtins.input', side_effect=AssertionError('no third prompt')), redirect_stderr(output):
            self.assertEqual(passwords.prompt_password('Hermes', fresh=True), 'hermes-fixture-password')
            self.assertEqual(passwords.prompt_password('Paperclip', fresh=True), 'paperclip-fixture-password')
        self.assertEqual([call.args[0].strip() for call in hidden_input.call_args_list],
                         ['Hermes password:', 'Confirm Hermes password:',
                          'Paperclip password:', 'Confirm Paperclip password:'])


if __name__ == '__main__':
    unittest.main()
