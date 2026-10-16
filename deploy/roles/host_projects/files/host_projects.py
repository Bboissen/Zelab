#!/usr/bin/env python3
"""Apply the team's tier accounts from config/team.yaml (run as root by make).

For each tier: group and account zelab-team-<tier> (home <projects_dir>/<tier>,
its workspace), the preset's groups, its sudoers fragment, read or write ACLs
on its paths, read/write on the notes vault, and an SSH key restricted to the
host's own address. Accounts of tiers no longer declared, and the per-project
accounts zelab-p-<slug> of earlier versions, lose their grants and are removed.
Prints {"changed": N}; never prints key material.
"""

import json
import os
from pathlib import Path
import pwd
import grp
import re
import subprocess
import sys

COMMENT = 'Zelab team tier {tier}'
# Per-project accounts of earlier versions; removed on the next run.
LEGACY_COMMENT = 'Zelab host project {slug}'
SUDOERS = Path('/etc/sudoers.d')
SSHD_DROPIN = Path('/etc/ssh/sshd_config.d/05-zelab-projects.conf')
OPTIONAL_GROUPS = ('systemd-journal', 'adm', 'docker')


def account(tier):
    return f'zelab-team-{tier}'


def sudoers_fragment(tier, commands):
    if not commands:
        return None
    user = account(tier)
    return (f'# Zelab team tier {tier}. Managed by make.\n'
            f'Defaults:{user} !requiretty, env_reset, use_pty\n'
            f'{user} ALL=(root) NOPASSWD: {", ".join(commands)}\n')


def authorized_keys(ssh_host, public_key):
    return f'from="{ssh_host}",restrict {" ".join(public_key.split()[:2])}\n'


def allowusers(tiers, ssh_host):
    return '# Team tier accounts; managed by make.\n' + ''.join(
        f'AllowUsers {account(tier)}@{ssh_host}\n' for tier in sorted(tiers))


def acl_entries(user, perms):
    return [f'u:{user}:{perms}', f'd:u:{user}:{perms}']


def run(*argv, check=True, quiet=True):
    return subprocess.run([*map(str, argv)], check=check, text=True,
                          stdout=subprocess.PIPE if quiet else None, stderr=subprocess.PIPE if quiet else None)


def write(path, content, mode, uid=0, gid=0):
    """Write when different; return 1 when changed."""
    path = Path(path)
    current = path.read_text() if path.is_file() and not path.is_symlink() else None
    if current == content and (path.stat().st_mode & 0o7777, path.stat().st_uid, path.stat().st_gid) == (mode, uid, gid):
        return 0
    temporary = path.with_name(f'.{path.name}.zelab')
    temporary.write_text(content)
    os.chown(temporary, uid, gid)
    temporary.chmod(mode)
    temporary.replace(path)
    return 1


def ensure_dir(path, mode, uid, gid):
    path = Path(path)
    changed = 0
    if not path.is_dir():
        path.mkdir(mode=mode)
        changed = 1
    info = path.stat()
    if (info.st_mode & 0o7777, info.st_uid, info.st_gid) != (mode, uid, gid):
        os.chown(path, uid, gid)
        path.chmod(mode)
        changed = 1
    return changed


def zelab_accounts():
    """{user: state key} of accounts this file created (matched by name and comment), legacy ones included."""
    found = {}
    for entry in pwd.getpwall():
        match = re.fullmatch(r'zelab-team-([a-z0-9]+)', entry.pw_name)
        if match and entry.pw_gecos == COMMENT.format(tier=match.group(1)):
            found[entry.pw_name] = entry.pw_name
        match = re.fullmatch(r'zelab-p-([a-z0-9-]+)', entry.pw_name)
        if match and entry.pw_gecos == LEGACY_COMMENT.format(slug=match.group(1)):
            # Earlier versions keyed their grants by project slug.
            found[entry.pw_name] = match.group(1)
    return found


def ensure_account(tier, home):
    user, changed = account(tier), 0
    try:
        grp.getgrnam(user)
    except KeyError:
        run('groupadd', '--system', user)
        changed = 1
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        run('useradd', '--system', '--gid', user, '--home-dir', home, '--shell', '/bin/bash',
            '--comment', COMMENT.format(tier=tier), '--no-create-home', user)
        return 1 + changed
    if entry.pw_gecos != COMMENT.format(tier=tier) or entry.pw_dir != home:
        raise SystemExit(f'{user} exists but was not created by Zelab for this tier; rename the tier or remove it')
    return changed


def ensure_groups(user, wanted):
    current = {group.gr_name for group in grp.getgrall() if user in group.gr_mem}
    present = {name for name in OPTIONAL_GROUPS if name in {g.gr_name for g in grp.getgrall()}}
    target = (current - set(OPTIONAL_GROUPS)) | (set(wanted) & present)
    if target == current:
        return 0
    run('usermod', '--groups', ','.join(sorted(target)), user)
    return 1


def grant(path, user, perms):
    """Recursive ACL on an existing path; returns 1 when an entry was missing."""
    if not Path(path).is_dir():
        return 0
    current = run('getfacl', '-cp', path).stdout.splitlines()
    want = {f'user:{user}:{perms}', f'default:user:{user}:{perms}'}
    if want <= set(current):
        return 0
    run('setfacl', '-R', '-m', ','.join(acl_entries(user, perms)), path)
    return 1


def revoke(path, user):
    if Path(path).exists():
        run('setfacl', '-R', '-x', f'u:{user},d:u:{user}', path, check=False)


def remove_account(user, key, state):
    for path in state.get('paths', {}).pop(key, []):
        revoke(path, user)
    (SUDOERS / user).unlink(missing_ok=True)
    run('pkill', '-KILL', '-u', user, check=False)
    home = pwd.getpwnam(user).pw_dir
    run('userdel', user)
    run('groupdel', user, check=False)
    if home.startswith(state['projects_dir'] + '/'):
        run('rm', '-rf', '--one-file-system', home)
    return 1


def host_uses_allowusers():
    files = [Path('/etc/ssh/sshd_config'), *sorted(Path('/etc/ssh/sshd_config.d').glob('*.conf'))]
    return any(re.search(r'^\s*AllowUsers\s', path.read_text(), re.M | re.I)
               for path in files if path != SSHD_DROPIN and path.is_file())


def apply(config):
    tiers, projects_dir = config['tiers'], config['projects_dir']
    state_file = Path(projects_dir) / '.zelab-grants.json'
    changed = ensure_dir(projects_dir, 0o755, 0, 0)
    state = json.loads(state_file.read_text()) if state_file.is_file() else {}
    state['projects_dir'] = projects_dir
    state.setdefault('paths', {})
    wanted = dict(tiers)
    vault = config.get('vault_dir')
    for tier, cfg in sorted(wanted.items()):
        user, home = account(tier), f'{projects_dir}/{tier}'
        changed += ensure_account(tier, home)
        entry = pwd.getpwnam(user)
        changed += ensure_groups(user, cfg['groups'])
        changed += ensure_dir(home, 0o750, entry.pw_uid, entry.pw_gid)
        changed += ensure_dir(f'{home}/.ssh', 0o700, entry.pw_uid, entry.pw_gid)
        changed += ensure_dir(f'{home}/workspace', 0o750, entry.pw_uid, entry.pw_gid)
        public = Path(config['keys_dir'], f'team-{tier}.key.pub').read_text()
        changed += write(f'{home}/.ssh/authorized_keys', authorized_keys(config['ssh_host'], public),
                         0o600, entry.pw_uid, entry.pw_gid)
        fragment, target = sudoers_fragment(tier, cfg['sudo']), SUDOERS / user
        if fragment:
            if fragment != (target.read_text() if target.is_file() else None):
                temporary = SUDOERS / f'.{user}.zelab'
                temporary.write_text(fragment)
                temporary.chmod(0o440)
                if run('visudo', '-cf', temporary, check=False).returncode:
                    temporary.unlink()
                    raise SystemExit(f'sudoers fragment for {tier} does not validate')
                temporary.replace(target)
                changed += 1
        elif target.exists():
            target.unlink()
            changed += 1
        granted = sorted(set(cfg['paths']) | ({vault} if vault else set()))
        previous = state['paths'].get(user, [])
        for path in sorted(set(previous) - set(granted)):
            revoke(path, user)
            changed += 1
        for path in cfg['paths']:
            changed += grant(path, user, cfg['paths_rights'])
            if Path(path).is_dir() and run('runuser', '-u', user, '--', 'test', '-r', path, check=False).returncode:
                # An ACL is no use below a parent the account cannot enter (a closed home, say).
                raise SystemExit(f'{user} cannot reach {path}: a parent folder is closed to it; '
                                 'choose a folder outside it, or remove it from the tier paths')
        if vault:
            # Notes are shared work: every tier reads and writes the vault.
            changed += grant(vault, user, 'rwx')
        state['paths'][user] = granted
    keep = {account(tier) for tier in wanted}
    for user, key in sorted(zelab_accounts().items()):
        if user not in keep:
            changed += remove_account(user, key, state)
    # Extend an existing SSH allowlist; an open host stays open.
    ssh_changed = 0
    if host_uses_allowusers() and wanted:
        ssh_changed = write(SSHD_DROPIN, allowusers(wanted, config['ssh_host']), 0o644)
    elif SSHD_DROPIN.exists():
        SSHD_DROPIN.unlink()
        ssh_changed = 1
    if ssh_changed:
        run('/usr/sbin/sshd', '-t')
        run('systemctl', 'reload', 'ssh.service')
        changed += 1
    state_file.write_text(json.dumps(state, indent=1, sort_keys=True) + '\n')
    state_file.chmod(0o600)
    return changed


if __name__ == '__main__':
    print(json.dumps({'changed': apply(json.loads(sys.argv[1]))}))
