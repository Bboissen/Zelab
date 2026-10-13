"""Reject archive link pivots before extraction; exercise real archive metadata."""

import io
import contextlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

from restore import inspect_archive, main
from runtime import maintenance_lock


class ArchiveSafetyTest(unittest.TestCase):
    def inspect(self, entries):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.tar'
            with tarfile.open(path, 'w') as archive:
                for name, kind, target in entries:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = target
                    if kind == tarfile.REGTYPE:
                        member.size = 4
                        archive.addfile(member, io.BytesIO(b'note'))
                    else:
                        archive.addfile(member)
            return inspect_archive(path)

    def test_regular_files_and_internal_hardlinks_are_preserved(self):
        count, size = self.inspect([('vault', tarfile.DIRTYPE, ''),
                                    ('vault/note', tarfile.REGTYPE, ''),
                                    ('vault/alias', tarfile.LNKTYPE, 'vault/note'),
                                    ('unit', tarfile.SYMTYPE, '/dev/null')])
        self.assertEqual(count, 4)
        self.assertGreaterEqual(size, 4)

    def test_empty_archive_is_not_a_recovery(self):
        with self.assertRaisesRegex(ValueError, 'no recoverable state'):
            self.inspect([])

    def test_path_traversal_and_absolute_members_are_refused(self):
        for path in ['/etc/owned', '../owned', 'vault/../../owned']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.inspect([(path, tarfile.REGTYPE, '')])

    def test_symlink_pivot_is_refused_in_either_member_order(self):
        entries = [('pivot', tarfile.SYMTYPE, '/tmp'), ('pivot/owned', tarfile.REGTYPE, '')]
        for order in (entries, list(reversed(entries))):
            with self.subTest(order=order), self.assertRaises(ValueError):
                self.inspect(order)

    def test_duplicate_files_and_special_nodes_are_refused(self):
        with self.assertRaises(ValueError):
            self.inspect([('note', tarfile.REGTYPE, ''), ('note', tarfile.SYMTYPE, '/etc/passwd')])
        for kind in [tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE]:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.inspect([('special', kind, '')])

    def test_external_cyclic_and_symlink_hardlink_targets_are_refused(self):
        cases = [ [('alias', tarfile.LNKTYPE, '/etc/passwd')],
                  [('alias', tarfile.LNKTYPE, '../external')],
                  [('alias', tarfile.LNKTYPE, 'missing')],
                  [('one', tarfile.LNKTYPE, 'two'), ('two', tarfile.LNKTYPE, 'one')],
                  [('alias', tarfile.LNKTYPE, 'link'), ('link', tarfile.SYMTYPE, '/etc/passwd')] ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.inspect(case)


class RestoreCommandTest(unittest.TestCase):
    """Run the actual restore CLI logic with real tar/GPG and no host services."""

    def setUp(self):
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)
        self.temporary = tempfile.TemporaryDirectory(prefix='zelab-restore-command-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.gpg_home = self.base / 'gnupg'
        self.gpg_home.mkdir(mode=0o700)
        self.environment = patch.dict(os.environ, GNUPGHOME=str(self.gpg_home))
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(lambda: subprocess.run(['rtk', 'proxy', 'gpgconf', '--homedir',
                                              str(self.gpg_home), '--kill', 'gpg-agent'], capture_output=True))
        self.key = self.base / 'key'
        self.key.write_text(secrets.token_urlsafe(32))
        self.key.chmod(0o600)
        self.source = self.base / 'source'
        self.source.mkdir(mode=0o700)
        self.note = self.source / 'note.md'
        self.note.write_text('Disposable recovery fixture.\n')
        self.note.chmod(0o600)
        os.link(self.note, self.source / 'note-hardlink')
        (self.source / 'note-link').symlink_to('note.md')
        os.setxattr(self.note, 'user.zelab-restore', b'kept')
        tar = self.base / 'source.tar'
        self.command('tar', '--acls', '--xattrs', '--numeric-owner', '-C', self.source,
                     '-cf', tar, 'note.md', 'note-link', 'note-hardlink')
        self.archive = self.base / 'source.tar.gpg'
        self.command('gpg', '--batch', '--pinentry-mode', 'loopback', '--no-symkey-cache',
                     '--passphrase-file', self.key, '--symmetric', '--cipher-algo', 'AES256',
                     '--compress-algo', 'none', '--output', self.archive, tar)
        self.archive.chmod(0o600)
        site = yaml.safe_load((Path(__file__).resolve().parents[1] / 'config/site.example.yaml').read_text())
        for name in ('vm_data', 'vm_root', 'vm_bootstrap_dir', 'paperclip_home',
                     'worker_ops_home', 'worker_code_home', 'notes_vault_dir',
                     'obsidian_state_dir', 'private_dir', 'backup_dir'):
            site[name] = str(self.base / 'site' / name)
        site['deploy_key'] = site['private_dir'] + '/deploy_key'
        site['guest_known_hosts'] = site['private_dir'] + '/known_hosts'
        site['vm_disk'] = site['vm_data'] + '/disk.qcow2'
        site['host_disk_reserve_gib'] = 1
        self.site_config = self.base / 'site.yaml'
        self.site_config.write_text(yaml.safe_dump(site))
        self.destination = self.base / 'restored'

    def command(self, *args):
        result = subprocess.run(['rtk', 'proxy', *map(str, args)], capture_output=True)
        self.assertEqual(result.returncode, 0, 'Disposable native recovery command failed')
        return result

    def restore(self):
        arguments = ['restore.py', '--site-config', str(self.site_config), '--env-file',
                     str(self.base / 'absent.env'), '--archive', str(self.archive),
                     '--destination', str(self.destination), '--key-file', str(self.key)]
        output = io.StringIO()
        with patch.object(sys, 'argv', arguments), \
                patch('restore.os', SimpleNamespace(geteuid=lambda: 0, umask=lambda _: None)), \
                patch('restore.maintenance_lock', side_effect=lambda: maintenance_lock(self.base / 'lock')), \
                contextlib.redirect_stdout(output):
            main()
        return output.getvalue()

    def test_authenticated_restore_preserves_files_links_metadata_and_receipt(self):
        output = self.restore()
        root = self.destination / 'root'
        self.assertEqual((root / 'note.md').read_bytes(), self.note.read_bytes())
        self.assertEqual((root / 'note.md').stat().st_ino, (root / 'note-hardlink').stat().st_ino)
        self.assertEqual((root / 'note-link').readlink(), Path('note.md'))
        self.assertEqual((root / 'note.md').stat().st_mode & 0o777, 0o600)
        self.assertEqual(os.getxattr(root / 'note.md', 'user.zelab-restore'), b'kept')
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o700)
        receipt = json.loads((self.destination / 'restore-receipt.json').read_text())
        self.assertEqual((self.destination / 'restore-receipt.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(receipt['members_compared'], 3)
        self.assertEqual(receipt['activated_services'], [])
        self.assertFalse((self.destination / 'authenticated.tar').exists())
        self.assertNotIn(self.key.read_text(), output)

    def test_tampered_ciphertext_never_extracts_and_removes_partial_plaintext(self):
        encrypted = bytearray(self.archive.read_bytes())
        encrypted[-10] ^= 1
        self.archive.write_bytes(encrypted)
        with self.assertRaisesRegex(RuntimeError, 'authentication failed'):
            self.restore()
        self.assertFalse((self.destination / 'root').exists())
        self.assertFalse((self.destination / 'authenticated.tar').exists())
        self.assertFalse((self.destination / 'restore-receipt.json').exists())

    def test_authenticated_link_pivot_is_rejected_before_any_extraction(self):
        outside = self.base / 'outside'
        outside.mkdir()
        sentinel = outside / 'sentinel'
        sentinel.write_text('unchanged')
        malicious = self.base / 'malicious.tar'
        with tarfile.open(malicious, 'w') as archive:
            link = tarfile.TarInfo('pivot')
            link.type = tarfile.SYMTYPE
            link.linkname = str(outside)
            archive.addfile(link)
            file = tarfile.TarInfo('pivot/sentinel')
            file.size = 4
            archive.addfile(file, io.BytesIO(b'evil'))
        self.command('gpg', '--batch', '--yes', '--pinentry-mode', 'loopback', '--no-symkey-cache',
                     '--passphrase-file', self.key, '--symmetric', '--cipher-algo', 'AES256',
                     '--compress-algo', 'none', '--output', self.archive, malicious)
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            self.restore()
        self.assertEqual(sentinel.read_text(), 'unchanged')
        self.assertFalse((self.destination / 'root').exists())
        self.assertFalse((self.destination / 'authenticated.tar').exists())

    def test_authentication_cannot_consume_the_host_reserve(self):
        with patch('restore.shutil.disk_usage', return_value=SimpleNamespace(free=2**30 + 64)):
            with self.assertRaisesRegex(RuntimeError, 'exceeded available space'):
                self.restore()
        self.assertFalse((self.destination / 'root').exists())
        self.assertFalse((self.destination / 'authenticated.tar').exists())

    def test_expanded_space_is_checked_after_authentication(self):
        usage = [SimpleNamespace(free=2**30 + 1024**2), SimpleNamespace(free=2**30 + 1)]
        with patch('restore.shutil.disk_usage', side_effect=usage):
            with self.assertRaisesRegex(ValueError, 'extracted files'):
                self.restore()
        self.assertFalse((self.destination / 'root').exists())
        self.assertFalse((self.destination / 'authenticated.tar').exists())

    def test_output_cannot_overlap_configured_but_absent_bootstrap_state(self):
        site = yaml.safe_load(self.site_config.read_text())
        self.destination = Path(site['vm_bootstrap_dir'])
        self.destination.parent.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'overlap'):
            self.restore()
        self.assertFalse(self.destination.exists())


if __name__ == '__main__':
    unittest.main()
