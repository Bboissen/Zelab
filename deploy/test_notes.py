"""make notes against real Git: a vault, a bare remote standing in for GitHub, and a laptop clone."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import notes

GIT_ENV = {**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_AUTHOR_NAME': 'Laptop', 'GIT_AUTHOR_EMAIL': 'laptop@example.invalid',
           'GIT_COMMITTER_NAME': 'Laptop', 'GIT_COMMITTER_EMAIL': 'laptop@example.invalid'}


class LocalSync(notes.Sync):
    """The real sync, with the GitHub-only checks replaced by a local bare remote."""

    def __init__(self, site, remote):
        super().__init__({**site, 'notes_sync_remote': 'git@github.com:owner/notes.git'})
        self.remote = str(remote)

    def require_private(self):
        self.git('ls-remote', '--heads', 'origin', cwd=self.home)


class NotesSyncTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.vault, self.bare, self.laptop = root / 'vault', root / 'remote.git', root / 'laptop'
        private = root / 'private'
        for path in (self.vault, private / 'notes-sync'):
            path.mkdir(parents=True)
        (private / 'notes-sync/deploy_key').write_text('unused\n')
        (private / 'notes-sync/known_hosts').write_text('unused\n')
        self.laptop_git('init', '--quiet', '--bare', '--initial-branch=main', str(self.bare), cwd=root)
        self.sync = LocalSync({'notes_vault_dir': str(self.vault), 'private_dir': str(private)}, self.bare)
        self.root = root

    def laptop_git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.laptop, env=GIT_ENV, check=True,
                              capture_output=True, text=True).stdout

    def clone(self):
        self.laptop_git('clone', '--quiet', str(self.bare), str(self.laptop), cwd=self.root)

    def laptop_commit(self, name, text, message='laptop edit'):
        (self.laptop / name).parent.mkdir(parents=True, exist_ok=True)
        (self.laptop / name).write_text(text)
        self.laptop_git('add', '--all')
        self.laptop_git('commit', '--quiet', '-m', message)
        self.laptop_git('push', '--quiet', 'origin', 'HEAD:main')

    def test_server_and_laptop_edits_travel_both_ways(self):
        (self.vault / 'Projects/homelab').mkdir(parents=True)
        (self.vault / 'Projects/homelab/plex.md').write_text('agent note\n')
        for name in ('Hermes/scout/idea.md', 'Paperclip/Projects/homelab/decisions.md'):
            (self.vault / name).parent.mkdir(parents=True, exist_ok=True)
            (self.vault / name).write_text('direct note\n')
        self.sync.run()
        self.clone()
        self.assertEqual((self.laptop / 'Projects/homelab/plex.md').read_text(), 'agent note\n')
        for name in ('Hermes/scout/idea.md', 'Paperclip/Projects/homelab/decisions.md'):
            self.assertEqual((self.laptop / name).read_text(), 'direct note\n')
        self.laptop_commit('Inbox/idea.md', 'laptop note\n')
        self.sync.run()
        self.assertEqual((self.vault / 'Inbox/idea.md').read_text(), 'laptop note\n')
        # Different files changed on both sides merge cleanly, outside the vault.
        (self.vault / 'Server/map.md').parent.mkdir()
        (self.vault / 'Server/map.md').write_text('server map\n')
        self.laptop_commit('Inbox/second.md', 'second\n')
        self.sync.run()
        self.assertEqual((self.vault / 'Inbox/second.md').read_text(), 'second\n')
        self.laptop_git('pull', '--quiet', '--no-rebase')
        self.assertEqual((self.laptop / 'Server/map.md').read_text(), 'server map\n')

    def test_conflict_keeps_both_versions_until_the_laptop_merges(self):
        (self.vault / 'note.md').write_text('base\n')
        self.sync.run()
        self.clone()
        self.laptop_commit('note.md', 'laptop version\n')
        (self.vault / 'note.md').write_text('server version\n')
        with self.assertRaisesRegex(notes.Stop, 'server-pending'):
            self.sync.run()
        self.assertEqual((self.vault / 'note.md').read_text(), 'server version\n')
        self.assertNotIn('<<<<<<<', (self.vault / 'note.md').read_text())
        self.assertEqual(self.laptop_git('show', 'origin/main:note.md', cwd=self.laptop), 'laptop version\n')
        self.laptop_git('fetch', '--quiet')
        self.assertEqual(self.laptop_git('show', 'origin/server-pending:note.md'), 'server version\n')
        # The owner resolves on the laptop, keeping both.
        subprocess.run(['git', 'merge', '--quiet', 'origin/server-pending'], cwd=self.laptop, env=GIT_ENV,
                       capture_output=True)
        (self.laptop / 'note.md').write_text('laptop version\nserver version\n')
        self.laptop_git('add', 'note.md')
        self.laptop_git('commit', '--quiet', '--no-edit')
        self.laptop_git('push', '--quiet', 'origin', 'HEAD:main')
        self.sync.run()
        self.assertEqual((self.vault / 'note.md').read_text(), 'laptop version\nserver version\n')
        self.assertNotIn('server-pending', self.laptop_git('ls-remote', '--heads', 'origin'))

    def test_device_settings_git_files_and_symlinks_never_travel(self):
        (self.vault / '.obsidian/plugins/evil').mkdir(parents=True)
        (self.vault / '.obsidian/plugins/evil/main.js').write_text('steal()\n')
        (self.vault / '.gitignore').write_text('!.obsidian/\n')
        (self.vault / 'link.md').symlink_to('/etc/hostname')
        (self.vault / 'real.md').write_text('note\n')
        self.sync.run()
        self.clone()
        self.assertEqual(sorted(p.name for p in self.laptop.iterdir() if p.name != '.git'), ['real.md'])
        # A laptop that pushes plugin code is refused before anything reaches the vault.
        self.laptop_commit('.obsidian/plugins/x/main.js', 'code\n')
        with self.assertRaisesRegex(notes.Stop, 'must not reach the server'):
            self.sync.run()
        self.assertFalse((self.vault / '.obsidian/plugins/x').exists())

    def test_an_agent_edit_racing_the_sync_is_never_overwritten(self):
        (self.vault / 'note.md').write_text('base\n')
        self.sync.run()
        self.clone()
        self.laptop_commit('note.md', 'laptop version\n')
        # An agent writes after the server commit step: the fast-forward must refuse.
        self.sync.commit_server_edits = lambda: 0
        (self.vault / 'note.md').write_text('agent wrote this mid-sync\n')
        with self.assertRaisesRegex(notes.Stop, 'Nothing was overwritten'):
            self.sync.run()
        self.assertEqual((self.vault / 'note.md').read_text(), 'agent wrote this mid-sync\n')

    def test_remote_must_be_a_github_repository(self):
        for remote in ('', 'https://github.com/owner/notes.git', 'git@gitlab.com:owner/notes.git'):
            with self.subTest(remote=remote), self.assertRaises(notes.Stop):
                notes.Sync({'notes_vault_dir': '/tmp', 'private_dir': '/tmp', 'notes_sync_remote': remote})
        sync = notes.Sync({'notes_vault_dir': '/tmp', 'private_dir': '/tmp',
                           'notes_sync_remote': 'git@github.com:owner/zelab-notes.git'})
        self.assertEqual((sync.owner, sync.repo), ('owner', 'zelab-notes'))



class WizardTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.site = {'notes_vault_dir': str(self.root / 'vault'), 'private_dir': str(self.root / 'private')}
        self.config = self.root / 'site.yaml'
        self.config.write_text('# Local site.\noperator_user: operator\n')

    def answers(self, *values):
        return patch('builtins.input', side_effect=list(values))

    def test_remote_is_saved_in_site_yaml_and_replaced_later(self):
        notes.save_remote(self.config, 'git@github.com:owner/one.git')
        notes.save_remote(self.config, 'git@github.com:owner/two.git')
        text = self.config.read_text()
        self.assertTrue(text.startswith('# Local site.\noperator_user: operator\n'))
        self.assertEqual(text.count('notes_sync_remote:'), 1)
        self.assertIn('notes_sync_remote: git@github.com:owner/two.git', text)

    def test_create_makes_a_private_repository_and_existing_can_be_picked(self):
        calls = []

        def fake_gh(*args):
            calls.append(args)
            out = 'owner/old-vault\nowner/zelab-notes\n' if args[:2] == ('repo', 'list') else ''
            return subprocess.CompletedProcess(args, 0, out, '')
        with patch.object(notes, 'gh', side_effect=fake_gh), self.answers('1', '', 'y'), \
                patch('sys.stdout'):
            self.assertEqual(notes.choose_repository('owner'), 'owner/zelab-notes')
        self.assertIn('--private', calls[0])
        with patch.object(notes, 'gh', side_effect=fake_gh), self.answers('2', '1'), patch('sys.stdout'):
            self.assertEqual(notes.choose_repository('owner'), 'owner/old-vault')
        with patch.object(notes, 'gh', side_effect=fake_gh), self.answers('2', 'someone/notes'), patch('sys.stdout'):
            self.assertEqual(notes.choose_repository('owner'), 'someone/notes')

    def test_declining_in_plain_make_is_remembered(self):
        with patch('sys.stdin.isatty', return_value=True), self.answers('n'), patch('sys.stdout'):
            notes.wizard(self.site, self.config, offered=True)
        self.assertTrue((self.root / 'private/notes-sync/declined').exists())
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input') as asked:
            notes.wizard(self.site, self.config, offered=True)
        asked.assert_not_called()

    def test_a_repository_that_is_not_private_is_refused(self):
        def fake_gh(*args):
            out = 'PUBLIC\n' if args[:2] == ('repo', 'view') else ''
            return subprocess.CompletedProcess(args, 0, out, '')
        with patch('sys.stdin.isatty', return_value=True), patch.object(notes, 'gh_login', return_value='owner'), \
                patch.object(notes, 'gh', side_effect=fake_gh), self.answers('y', '2', 'owner/notes'), \
                patch('sys.stdout'), self.assertRaisesRegex(notes.Stop, 'not private'):
            notes.wizard(self.site, self.config, offered=False)
        self.assertNotIn('notes_sync_remote', self.config.read_text())


if __name__ == '__main__':
    unittest.main()
