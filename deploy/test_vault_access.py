"""Real POSIX ACL regressions for private-mode writes to the shared vault."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import vault_access


class VaultAccessTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vault = self.root / 'vault'
        self.vault.mkdir()
        # Use a different numeric UID: a named ACL for the owner would not
        # exercise the inherited named-user entries that a 0600 mask disables.
        self.uid = os.getuid() + 100000

    def acl(self, path):
        return subprocess.check_output(['getfacl', '-cpn', str(path)], text=True)

    def repair(self):
        return vault_access.restore(self.vault, [self.uid])

    def test_private_atomic_creation_and_replacement_restore_shared_access(self):
        self.repair()
        folder = self.vault / 'Hermes' / 'scout'
        folder.mkdir(parents=True)
        note = folder / 'Film favori.md'
        for content in (b'first note\n', b'replaced note\n'):
            fd, temporary = tempfile.mkstemp(dir=folder)
            os.write(fd, content)
            os.close(fd)
            os.replace(temporary, note)
            self.assertIn('mask::---', self.acl(note))
            self.assertGreater(self.repair()['changed'], 0)
            self.assertIn(f'user:{self.uid}:rw-', self.acl(note))
            self.assertIn('mask::rw-', self.acl(note))
            self.assertEqual(note.read_bytes(), content)
            self.assertEqual(note.stat().st_mode & 0o007, 0)
            self.assertEqual(self.repair(), {'changed': 0, 'skipped': 0})

    def test_links_and_special_files_do_not_change_outside_inodes(self):
        outside = self.root / 'outside'
        outside.mkdir()
        note = outside / 'private.md'
        note.write_text('private\n')
        note.chmod(0o600)
        before = self.acl(note)
        (self.vault / 'file-link').symlink_to(note)
        (self.vault / 'directory-link').symlink_to(outside, target_is_directory=True)
        os.link(note, self.vault / 'hard-link')
        os.mkfifo(self.vault / 'pipe')
        self.assertEqual(self.repair()['skipped'], 4)
        self.assertEqual(self.acl(note), before)
        self.assertEqual(note.read_text(), 'private\n')

    def test_root_and_ancestor_symlinks_are_refused(self):
        link = self.root / 'link'
        link.symlink_to(self.vault, target_is_directory=True)
        for path in (link, link / 'child'):
            with self.subTest(path=path), self.assertRaises(OSError):
                vault_access.restore(path, [self.uid])

    def test_replacement_during_repair_cannot_redirect_acl_changes(self):
        from unittest.mock import patch
        note = self.vault / 'note.md'
        note.write_text('note\n')
        outside = self.root / 'private.md'
        outside.write_text('private\n')
        outside.chmod(0o600)
        before = self.acl(outside)
        original = subprocess.run
        replaced = False

        def race(command, **kwargs):
            nonlocal replaced
            if command[0] == 'setfacl' and not replaced:
                info = os.stat(command[-1])
                if info.st_ino == note.stat().st_ino:
                    note.rename(self.vault / 'original.md')
                    note.symlink_to(outside)
                    replaced = True
            return original(command, **kwargs)

        with patch.object(vault_access.subprocess, 'run', side_effect=race):
            self.repair()
        self.assertTrue(replaced)
        self.assertEqual(self.acl(outside), before)
        self.assertIn(f'user:{self.uid}:rw-', self.acl(self.vault / 'original.md'))
        for path in ('/', 'relative', str(self.vault / '..')):
            with self.subTest(path=path), self.assertRaises(ValueError):
                vault_access.restore(path, [self.uid])

    def test_executable_files_keep_their_mode_and_foreign_devices_are_skipped(self):
        from unittest.mock import patch
        note = self.vault / 'executable'
        note.write_text('text\n')
        note.chmod(0o700)
        self.repair()
        self.assertIn('mask::rwx', self.acl(note))
        before = self.acl(note)
        original = os.fstat

        def foreign(fd):
            info = original(fd)
            if info.st_ino == note.stat().st_ino:
                values = list(info)
                values[2] = info.st_dev + 1
                return os.stat_result(values)
            return info

        with patch.object(vault_access.os, 'fstat', side_effect=foreign):
            self.assertEqual(self.repair()['skipped'], 1)
        self.assertEqual(self.acl(note), before)


if __name__ == '__main__':
    unittest.main()
