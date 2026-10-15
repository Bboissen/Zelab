#!/usr/bin/env python3
"""make notes: one deliberate sync of the server vault with its private Git remote.

The vault stays a plain folder the agents edit through the notes share. Its Git
directory, hooks, config and deploy key live in private_dir/notes-sync, outside
the vault and the guest, so no agent can change how the sync behaves. Each run:

  1. checks the remote is a private GitHub repository (a public one is refused);
  2. commits the agents' edits, never .obsidian/, Git files or symlinks;
  3. fetches the laptop's commits and refuses any that carry those paths;
  4. merges with git merge-tree, outside any working tree, so the live vault
     never holds conflict markers, then fast-forwards the vault; Git refuses
     rather than overwrite a file an agent changed meanwhile, and the next run
     picks it up;
  5. pushes without force. On a conflict, the server's commits go to the
     branch server-pending and the owner merges it on the laptop.

The root-only vault history is separate and untouched. See docs/NOTES.md.
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REMOTE = re.compile(r'(?:git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?')
BRANCH, PENDING = 'main', 'server-pending'
# Never synced: device settings and plugin code, Git's own files, and editor litter.
EXCLUDE = ('.obsidian/', '.trash/', '.DS_Store', 'Thumbs.db', '.fuse_hidden*', '*.swp', '.~lock*', '*~')


class Stop(Exception):
    pass


def forbidden(path, mode=''):
    """Paths that must never travel: .obsidian (plugin code runs on the laptop), Git files, symlinks."""
    parts = path.split('/')
    return parts[0] == '.obsidian' or any(part.startswith('.git') for part in parts) or mode == '120000'


class Sync:
    def __init__(self, site):
        self.vault = Path(site['notes_vault_dir'])
        self.home = Path(site['private_dir']) / 'notes-sync'
        self.git_dir = self.home / 'vault.git'
        self.key = self.home / 'deploy_key'
        self.known_hosts = self.home / 'known_hosts'
        remote = site.get('notes_sync_remote') or ''
        match = REMOTE.fullmatch(remote)
        if not match:
            raise Stop('Set notes_sync_remote in config/site.yaml to a private GitHub repository, '
                       'for example git@github.com:OWNER/zelab-notes.git (see docs/NOTES.md).')
        self.remote, self.owner, self.repo = remote, match.group(1), match.group(2)

    # --- plumbing ------------------------------------------------------------------------

    def env(self):
        return {**os.environ, 'GIT_DIR': str(self.git_dir), 'GIT_WORK_TREE': str(self.vault),
                # The operator's own Git config, hooks and helpers never apply here.
                'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0',
                'GIT_SSH_COMMAND': f'ssh -i {self.key} -o IdentitiesOnly=yes -o IdentityAgent=none '
                                   f'-o UserKnownHostsFile={self.known_hosts} -o StrictHostKeyChecking=yes'}

    def git(self, *args, cwd=None, check=True):
        env = self.env()
        # The vault belongs to the notes account; the operator edits it through an ACL.
        result = subprocess.run(['rtk', 'proxy', 'git', '-c', f'safe.directory={self.vault}',
                                 '-c', f'safe.directory={self.git_dir}', *args],
                                cwd=cwd or self.vault, env=env, capture_output=True, text=True)
        if check and result.returncode:
            raise Stop(f'git {args[0]} failed: {(result.stderr or result.stdout).strip()[-600:]}')
        return result

    def ref(self, name):
        result = self.git('rev-parse', '--verify', '--quiet', name, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def ancestor(self, older, newer):
        return self.git('merge-base', '--is-ancestor', older, newer, check=False).returncode == 0

    # --- setup ---------------------------------------------------------------------------

    def prepare(self):
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.home, 0o700)
        if not self.vault.is_dir() or not os.access(self.vault, os.R_OK | os.W_OK | os.X_OK):
            raise Stop(f'{self.vault} is not readable and writable by this account; run rtk proxy make first.')
        if not self.key.exists():
            subprocess.run(['rtk', 'proxy', 'ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'zelab-notes-sync',
                            '-f', str(self.key)], check=True)
            raise Stop('A deploy key was created for the server. In GitHub, open the private repository '
                       f'{self.owner}/{self.repo}, Settings, Deploy keys, Add deploy key; paste this key and tick '
                       f'"Allow write access", then run rtk proxy make notes again:\n\n  {self.key.with_suffix(".pub").read_text().strip()}\n')
        if not self.known_hosts.exists():
            meta = self.github('https://api.github.com/meta')
            lines = [f'github.com {key}' for key in (meta or {}).get('ssh_keys', [])]
            if not lines:
                raise Stop("Could not read GitHub's SSH host keys from api.github.com/meta.")
            self.known_hosts.write_text('\n'.join(lines) + '\n')
            self.known_hosts.chmod(0o600)
        if not (self.git_dir / 'HEAD').exists():
            self.git('init', '--quiet', f'--initial-branch={BRANCH}', cwd=self.home)
        for key, value in (('core.bare', 'false'), ('core.worktree', str(self.vault)), ('core.autocrlf', 'false'),
                           ('core.quotepath', 'false'), ('core.hooksPath', str(self.git_dir / 'hooks')),
                           ('user.name', 'Zelab server'), ('user.email', 'server@zelab.invalid'),
                           ('commit.gpgsign', 'false'), ('pull.rebase', 'false'),
                           ('remote.origin.url', self.remote),
                           ('remote.origin.fetch', '+refs/heads/*:refs/remotes/origin/*')):
            self.git('config', key, value, cwd=self.home)
        (self.git_dir / 'info').mkdir(exist_ok=True)
        (self.git_dir / 'info/exclude').write_text('# Managed by make notes.\n' + '\n'.join(EXCLUDE) + '\n')

    def github(self, url):
        request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'zelab'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise Stop(f'GitHub answered HTTP {error.code}; try again later.') from None
        except OSError:
            raise Stop('GitHub is unreachable; nothing was synced.') from None

    def require_private(self):
        """Anonymous GitHub must not see the repository; the deploy key must reach it."""
        public = self.github(f'https://api.github.com/repos/{self.owner}/{self.repo}')
        if public is not None:
            raise Stop(f'{self.owner}/{self.repo} is visible without signing in: it is public. '
                       'Make it private (or pick a private one) before any note leaves this server.')
        if self.git('ls-remote', '--heads', 'origin', check=False, cwd=self.home).returncode:
            raise Stop(f'The deploy key cannot reach {self.owner}/{self.repo}: check that the repository exists '
                       'and that the key in notes-sync/deploy_key.pub is added with write access.')

    # --- the sync --------------------------------------------------------------------------

    def commit_server_edits(self):
        self.git('add', '--all', '--', '.')
        staged = self.git('diff', '--cached', '--name-only', '-z').stdout.split('\0')
        modes = {}
        for line in self.git('ls-files', '--stage', '-z').stdout.split('\0'):
            if line:
                info, _, path = line.partition('\t')
                modes[path] = info.split()[0]
        skipped = [path for path in staged if path and forbidden(path, modes.get(path, ''))]
        if skipped:
            # Unstage only; the files stay in the vault untouched.
            self.git('rm', '--cached', '--quiet', '-r', '--ignore-unmatch', '--', *skipped)
            print(f'  Not synced (device settings, Git files or symlinks): {", ".join(skipped[:5])}'
                  + (' ...' if len(skipped) > 5 else ''))
        if self.git('diff', '--cached', '--quiet', check=False).returncode == 0:
            return 0
        count = len([p for p in self.git('diff', '--cached', '--name-only', '-z').stdout.split('\0') if p])
        self.git('commit', '--quiet', '--no-verify', '-m', f'Server notes {time.strftime("%Y-%m-%d %H:%M", time.gmtime())} UTC')
        return count

    def refuse_forbidden(self, revision):
        listing = self.git('ls-tree', '-r', '-z', revision).stdout.split('\0')
        bad = [entry.partition('\t')[2] for entry in listing
               if entry and forbidden(entry.partition('\t')[2], entry.split()[0])]
        if bad:
            raise Stop(f'The remote carries paths that must not reach the server ({", ".join(bad[:5])}). '
                       'Remove them on the laptop, push, and run make notes again.')

    def fast_forward(self, target):
        result = self.git('merge', '--ff-only', '--quiet', target, check=False)
        if result.returncode:
            raise Stop('An agent edited a note the laptop also changed, after this run saved the agents\' work. '
                       'Nothing was overwritten; run rtk proxy make notes again.\n'
                       + (result.stderr or result.stdout).strip()[-400:])

    def merge_aside(self, incoming, unrelated):
        """Merge without touching any working tree; return the merge commit, or None on a conflict."""
        args = ['merge-tree', '--write-tree', '--no-messages', *(['--allow-unrelated-histories'] if unrelated else []),
                'HEAD', incoming]
        merged = self.git(*args, check=False)
        if merged.returncode == 1:
            return None
        if merged.returncode:
            raise Stop(f'git merge-tree failed: {(merged.stderr or merged.stdout).strip()[-400:]}')
        tree = merged.stdout.split()[0]
        return self.git('commit-tree', tree, '-p', 'HEAD', '-p', incoming,
                        '-m', 'Merge laptop notes into server notes').stdout.strip()

    def run(self):
        os.umask(0o007)  # with the vault's default ACLs, files git writes stay editable by the agents
        self.prepare()
        self.require_private()
        with open(self.home / 'lock', 'w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Stop('Another make notes is running.') from None
            self.exchange()

    def exchange(self):
        sent = self.commit_server_edits()
        self.git('fetch', '--quiet', '--prune', 'origin', cwd=self.home)
        head, theirs = self.ref('HEAD'), self.ref(f'refs/remotes/origin/{BRANCH}')
        pending = self.ref(f'refs/remotes/origin/{PENDING}')
        if pending and theirs and self.ancestor(pending, theirs):
            self.git('push', '--quiet', 'origin', '--delete', PENDING, cwd=self.home)
            print(f'  The laptop merged {PENDING}; removed it.')
            pending = None
        received = 0
        if theirs:
            self.refuse_forbidden(theirs)
        if not theirs:
            if head:
                self.git('push', '--quiet', '--set-upstream', 'origin', f'HEAD:{BRANCH}', cwd=self.home)
        elif not head or self.ancestor(head, theirs):
            if head != theirs:
                received = len([p for p in self.git('diff', '--name-only', '-z', *([head] if head else
                                ['4b825dc642cb6eb9a060e54bf8d69288fbee4904']), theirs).stdout.split('\0') if p])
                self.fast_forward(theirs)
        elif self.ancestor(theirs, head):
            self.git('push', '--quiet', 'origin', f'HEAD:{BRANCH}', cwd=self.home)
        else:
            unrelated = self.git('merge-base', head, theirs, check=False).returncode != 0
            merged = self.merge_aside(theirs, unrelated)
            if merged is None:
                if pending and not self.ancestor(pending, head):
                    raise Stop(f'A conflict is already waiting in {PENDING}: merge it on the laptop first '
                               '(docs/NOTES.md, "Conflicts"), push, then run make notes again.')
                self.git('push', '--quiet', 'origin', f'HEAD:refs/heads/{PENDING}', cwd=self.home)
                raise Stop(f'The laptop and the server changed the same notes. The server\'s version is saved in the '
                           f'branch {PENDING}; nothing was overwritten. On the laptop: git pull, then '
                           f'git merge origin/{PENDING}, resolve, commit and push; then run make notes again.')
            received = len([p for p in self.git('diff', '--name-only', '-z', head, merged).stdout.split('\0') if p])
            self.fast_forward(merged)
            self.git('push', '--quiet', 'origin', f'HEAD:{BRANCH}', cwd=self.home)
        print(f'  Notes synced: {sent} server change(s) sent, {received} laptop change(s) received.')


def main(argv=None):
    venv = ROOT / '.venv/bin/python'
    if sys.prefix != str(ROOT / '.venv'):
        if not venv.exists():
            raise SystemExit('Run rtk proxy make first: make notes works on an installed site.')
        os.execv(venv, [str(venv), __file__, *(sys.argv[1:] if argv is None else argv)])
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--site-config', type=Path, default=ROOT / 'config/site.yaml')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    args = parser.parse_args(argv)
    if os.geteuid() == 0:
        raise SystemExit('  Run make notes as the operator, not root: its files and key belong to you.')
    from run import read_inputs
    site = read_inputs(args.site_config, args.env_file)
    if not site.get('notes_enabled', True):
        raise SystemExit('  Notes are turned off on this site (notes_enabled).')
    try:
        Sync(site).run()
    except Stop as error:
        raise SystemExit(f'  {error}') from None
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
