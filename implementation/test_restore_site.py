"""Real encrypted file restoration with isolated files and mocked supervision."""

from contextlib import contextmanager, redirect_stdout, ExitStack
import io
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

from backup import COMPATIBILITY_FIELDS, SOURCE_PINS
from restore_site import Replacement, restore_site
from runtime import maintenance_lock
from archive_fixture import encrypt


ROOT = Path(__file__).resolve().parents[1]


CORE_FIELDS = ('vm_data', 'vm_root', 'vm_bootstrap_dir', 'paperclip_home', 'notes_vault_dir', 'vault_history_dir',
               'obsidian_state_dir', 'private_dir', 'notes_config_dir', 'obsidian_config_dir')
WORKER_HOMES = ('projects_dir',)


class RestoreFixture:
    """A disposable site; the project homes vary between the backup and the live site."""

    backed_up = WORKER_HOMES
    live = WORKER_HOMES

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='zelab-site-restore-')
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)
        self.gpg = self.base / 'gnupg'
        self.gpg.mkdir(mode=0o700)
        environment = patch.dict(os.environ, GNUPGHOME=str(self.gpg))
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(lambda: subprocess.run(['rtk', 'proxy', 'gpgconf', '--homedir', str(self.gpg), '--kill', 'gpg-agent'], capture_output=True))
        self.site = {**yaml.safe_load((ROOT / 'config/defaults.yaml').read_text()),
                     **yaml.safe_load((ROOT / 'config/site.example.yaml').read_text())}
        self.site['operator_user'] = pwd.getpwuid(os.geteuid()).pw_name
        for name in (*CORE_FIELDS, *WORKER_HOMES, 'backup_dir'):
            self.site[name] = str(self.base / 'live' / name)
        for name in (*CORE_FIELDS, *self.backed_up, 'backup_dir'):
            Path(self.site[name]).mkdir(parents=True)
        self.site.update(vm_disk=self.site['vm_data'] + '/disk.qcow2',
                         deploy_key=self.site['private_dir'] + '/deploy_key',
                         guest_known_hosts=self.site['private_dir'] + '/known_hosts',
                         host_disk_reserve_gib=0)
        self.paperclip_env = self.base / 'live/zelab-paperclip.env'
        self.paperclip_env.write_text('backup environment')
        self.paths = [Path(self.site[name]) for name in (*CORE_FIELDS, *self.backed_up)]
        for path in self.paths:
            (path / 'state.txt').write_text('backup state')
        (Path(self.site['vm_data']) / 'disk.qcow2').write_text('disposable disk fixture')
        private = Path(self.site['private_dir'])
        (private / 'backup-passphrase').write_text('old recovery key')
        (private / 'logs').mkdir()
        (private / 'logs/current.log').write_text('old log')
        self.key = self.base / 'key'
        self.key.write_text(secrets.token_urlsafe(32))
        self.key.chmod(0o600)
        self.staged = self.base / 'snapshot'
        self.staged.mkdir()
        # tar records the live absolute paths as members, as the real backup does.
        pins = [ROOT / relative for relative in SOURCE_PINS]
        captured = [*self.paths, self.paperclip_env, *pins]
        manifest = self.staged / 'run/zelab-backup-fixture/manifest.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({'scope': [str(path) for path in captured],
                                       'compatibility': {name: self.site.get(name) for name in COMPATIBILITY_FIELDS},
                                       'vm_chain': [{'filename': self.site['vm_disk']}]}))
        tar = self.base / 'backup.tar'
        self.command('tar', '--sparse', '--acls', '--xattrs', '--numeric-owner', '-C', '/',
                     '-cf', tar, *(str(path).lstrip('/') for path in captured),
                     '-C', self.staged, 'run/zelab-backup-fixture/manifest.json')
        self.archive = Path(self.site['backup_dir']) / 'zelab-applications-fixture.tar.gpg'
        encrypt(self, tar, self.archive, self.key)
        # The live layout can differ from the backup (harness changed or skipped).
        for name in set(self.backed_up) - set(self.live):
            shutil.rmtree(self.site[name])
        for name in set(self.live) - set(self.backed_up):
            Path(self.site[name]).mkdir()
        self.paths = [Path(self.site[name]) for name in (*CORE_FIELDS, *self.live)]
        for path in self.paths:
            (path / 'state.txt').write_text('current state')
        self.paperclip_env.write_text('current environment')
        (private / 'backup-passphrase').write_text('current recovery key')
        (private / 'logs/current.log').write_text('current log')
        self.events = []

    def command(self, *args):
        result = subprocess.run(['rtk', 'proxy', *map(str, args)], capture_output=True)
        self.assertEqual(result.returncode, 0, 'Disposable archive operation failed')

    @contextmanager
    def stopped(self, site, vm):
        self.events.append('stop')
        try:
            yield [vm, 'zelab-paperclip.service'], ['zelab-obsidian']
        finally:
            self.events.append('resume')

    def restore(self, *, confirm=None, readiness=None):
        with ExitStack() as stack:
            stack.enter_context(patch('restore_site.maintenance_lock', side_effect=lambda: maintenance_lock(self.base / 'lock')))
            stack.enter_context(patch('restore_site.PAPERCLIP_ENV', self.paperclip_env))
            stack.enter_context(patch('restore_site.supervised_mounts', return_value=['fixture.mount']))
            stack.enter_context(patch('restore_site.site_identity', return_value='fixture:zelab'))
            stack.enter_context(patch('restore_site.service_active', return_value=True))
            stack.enter_context(patch('restore_site.stopped_writers', side_effect=self.stopped))
            stack.enter_context(patch('restore_site.require_idle_workers'))
            stack.enter_context(patch('restore_site.run', side_effect=lambda *args: self.events.append(args)))
            stack.enter_context(patch('restore_site.wait_for_applications', side_effect=readiness))
            stack.enter_context(redirect_stdout(io.StringIO()))
            return restore_site(self.site, self.archive, self.key,
                                'fixture:zelab' if confirm is None else confirm)

    def assert_unchanged(self):
        self.assertTrue(all((path / 'state.txt').read_text() == 'current state' for path in self.paths))
        self.assertEqual(self.paperclip_env.read_text(), 'current environment')
        self.assertFalse(list((self.base / 'live').glob('.zelab-restore-*')))

    def assert_restored(self):
        for path in self.paths:
            self.assertEqual((path / 'state.txt').read_text(), 'backup state')
        self.assertEqual(self.paperclip_env.read_text(), 'backup environment')
        self.assertFalse(list((self.base / 'live').glob('.zelab-restore-*')))


class RestoreSiteTest(RestoreFixture, unittest.TestCase):
    def test_authenticated_state_is_activated_and_current_key_logs_are_kept(self):
        self.assertEqual(self.restore(), 0)
        self.assert_restored()
        private = Path(self.site['private_dir'])
        self.assertEqual((private / 'backup-passphrase').read_text(), 'current recovery key')
        self.assertEqual((private / 'logs/current.log').read_text(), 'current log')
        self.assertEqual(self.events[0], 'stop')
        self.assertIn('resume', self.events)
        receipt = next(Path(self.site['backup_dir']).glob('restore-*/activation.json'))
        self.assertIn('readiness passed', json.loads(receipt.read_text())['result'])
        self.assertFalse(list((self.base / 'live').glob('.zelab-restore-*')))

    def test_wrong_confirmation_never_stops_or_replaces_live_state(self):
        with self.assertRaisesRegex(ValueError, 'Nothing changed'):
            self.restore(confirm='another:site')
        self.assertEqual(self.events, [])
        self.assertTrue(all((path / 'state.txt').read_text() == 'current state' for path in self.paths))

    def test_incompatible_site_inputs_never_stop_or_replace_state(self):
        self.site['paperclip_port'] += 1
        with self.assertRaisesRegex(ValueError, 'inputs differ.*paperclip_port'):
            self.restore()
        self.assertEqual(self.events, [])
        self.assertTrue(all((path / 'state.txt').read_text() == 'current state' for path in self.paths))

    def test_different_vm_identity_never_stops_writers(self):
        self.site['vm_disk'] = self.site['vm_data'] + '/another.qcow2'
        with self.assertRaisesRegex(ValueError, 'VM identity differs'):
            self.restore()
        self.assertEqual(self.events, [])

    def test_tampered_archive_never_stops_writers(self):
        data = bytearray(self.archive.read_bytes())
        data[-10] ^= 1
        self.archive.write_bytes(data)
        with self.assertRaisesRegex(RuntimeError, 'authentication failed'):
            self.restore()
        self.assertEqual(self.events, [])

    def test_failed_application_readiness_rolls_back_current_data(self):
        with self.assertRaisesRegex(RuntimeError, 'not ready'):
            self.restore(readiness=RuntimeError('not ready'))
        self.assertTrue(all((path / 'state.txt').read_text() == 'current state' for path in self.paths))
        self.assertEqual(self.events.count('stop'), 2)
        self.assertEqual(self.events.count('resume'), 2)
        self.assertFalse(list((self.base / 'live').glob('.zelab-restore-*')))

    def test_partial_file_activation_rolls_back_every_replaced_root(self):
        original = Replacement.activate
        calls = []

        def activate(item):
            calls.append(item.target)
            if len(calls) == 3:
                raise OSError('disposable activation failure')
            return original(item)

        with patch('restore_site.Replacement.activate', new=activate), self.assertRaises(OSError):
            self.restore()
        self.assertTrue(all((path / 'state.txt').read_text() == 'current state' for path in self.paths))
        self.assertFalse(list((self.base / 'live').glob('.zelab-restore-*')))



class NoHostProjectsRestoreTest(RestoreFixture, unittest.TestCase):
    """A site without host projects has no projects_dir on either side."""

    backed_up = live = ()

    def test_restores_without_project_homes(self):
        self.assertEqual(self.restore(), 0)
        self.assert_restored()
        self.assertFalse(Path(self.site['projects_dir']).exists())


class UncoveredProjectHomesTest(RestoreFixture, unittest.TestCase):
    """Project homes created after the backup must not be left half-restored."""

    backed_up, live = (), ('projects_dir',)

    def test_backup_missing_present_project_homes_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'does not cover.*projects_dir'):
            self.restore()
        self.assertEqual(self.events, [])
        self.assert_unchanged()


class MissingLiveProjectHomesTest(RestoreFixture, unittest.TestCase):
    """Backed-up project homes need the matching installed projects first."""

    backed_up, live = ('projects_dir',), ()

    def test_backed_up_project_homes_absent_from_the_site_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'complete installed site'):
            self.restore()
        self.assertEqual(self.events, [])
        self.assert_unchanged()


if __name__ == '__main__':
    unittest.main()
