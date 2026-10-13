"""Terminal-only UI password setup; plaintext working copies live in temporary RAM.

Hermes retains its upstream scrypt hash. Paperclip needs an owner login for each
provisioning run. Obsidian retains the native root-owned FILE__PASSWORD input.
Existing password files remain until the owner confirms password-manager custody.
"""

import argparse
import base64
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import warnings


class PasswordSetupError(ValueError):
    """A safe diagnostic containing no credential or parser output."""


def read_private(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PasswordSetupError('Password inputs must be regular owner-only files owned by the operator.')
        value = source.read().rstrip('\n')
    if not value or value != value.strip() or not value.isprintable():
        raise PasswordSetupError('Password input is empty or contains unsupported whitespace/control characters.')
    return value


def write_private(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as output:
        output.write(value)


def hash_password(value):
    # Exact format and parameters exposed by the pinned upstream basic provider.
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(value.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return 'scrypt$16384$8$1$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(digest).decode()


def matches(value, encoded):
    scheme, n, r, p, salt, digest = encoded.split('$')
    if (scheme, n, r, p) != ('scrypt', '16384', '8', '1'):
        raise PasswordSetupError('Unexpected Hermes password hash format; reconcile the retained credential.')
    actual = hashlib.scrypt(value.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    return secrets.compare_digest(actual, base64.b64decode(digest))


def prompt_password(label, fresh):
    if not sys.stdin.isatty():
        raise PasswordSetupError(f'{label} password requires your terminal. Run rtk proxy make there; never send passwords in chat.')
    # RTK pipes stdout while getpass can write directly to the terminal. Keep
    # instructions and prompts on the same flushed stream so they stay ordered.
    print(f'\n  {label}: ' + ('paste a unique password saved in your password manager (16+ characters).' if fresh else
                              'enter the current owner password for this provisioning run.'),
          file=sys.stderr, flush=True)
    while True:
        with warnings.catch_warnings():
            # Refuse getpass's echoing fallback if terminal echo cannot be disabled.
            warnings.simplefilter('error', getpass.GetPassWarning)
            value = getpass.getpass(f'  {label} password: ', stream=sys.stderr)
        if (fresh and len(value) < 16) or not value or value != value.strip() or not value.isprintable():
            print('  Use 16+ characters for a new password; no boundary whitespace or control characters.',
                  file=sys.stderr, flush=True)
            continue
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            confirmation = getpass.getpass(f'  Confirm {label} password: ', stream=sys.stderr) if fresh else value
        if value != confirmation:
            print('  Passwords differ; try again.', file=sys.stderr, flush=True)
            continue
        return value


def installed(path):
    result = subprocess.run(['rtk', 'proxy', 'sudo', '-n', '/usr/local/bin/rtk', 'proxy',
                             'test', '-f', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode not in (0, 1):
        raise PasswordSetupError('Cannot inspect installed credentials; verify administrator access.')
    return result.returncode == 0


def prepare(site, runtime):
    private = Path(site['private_dir'])
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    for directory in (private, runtime):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PasswordSetupError('Password directories must be operator-owned private directories without symlinks.')
    legacy = {}
    def retained(name):
        path = private / name
        if path.exists() or path.is_symlink():
            value = read_private(path)
            legacy[name] = hashlib.sha256(value.encode()).hexdigest()
            return value
        return None

    hermes = retained('hermes-dashboard.password')
    hashed = private / 'hermes-dashboard.password-hash'
    if not hashed.exists():
        if hermes is None:
            # An existing dashboard with a lost source must never be silently rotated.
            if Path(site['guest_known_hosts']).exists():
                raise PasswordSetupError('Existing VM has no retained Hermes password/hash. Recover it before reapplying.')
            hermes = prompt_password('Hermes', fresh=True)
        write_private(hashed, hash_password(hermes))
    elif hermes is not None and not matches(hermes, read_private(hashed)):
        raise PasswordSetupError('Hermes password and retained hash disagree; no credential was rotated.')
    else:
        read_private(hashed)
    if hermes is not None:
        write_private(runtime / 'hermes-dashboard.password', hermes)

    owner = retained('paperclip-owner.password')
    if owner is None:
        owner = prompt_password('Paperclip', fresh=not installed(Path(site['paperclip_home']) / 'owner-bootstrap-complete'))
    write_private(runtime / 'paperclip-owner.password', owner)

    if site.get('obsidian_enabled'):
        obsidian = retained('obsidian.password')
        if obsidian is None and not installed(Path(site['obsidian_config_dir']) / 'password'):
            obsidian = prompt_password('Obsidian', fresh=True)
        if obsidian is not None:
            write_private(runtime / 'obsidian.password', obsidian)
    write_private(runtime / 'legacy.json', json.dumps(legacy))
    print('\n  UI credentials ready; plaintext working copies will be removed when this run exits.')


def finish(site, runtime):
    legacy = json.loads((runtime / 'legacy.json').read_text())
    if not legacy:
        return
    print('\n  Existing plaintext recovery copies remain in the private deployment directory.')
    print('  Hermes now uses a hash; Obsidian keeps its root-owned runtime file.')
    print('  Removing the Paperclip copy means entering its owner password on each future make.')
    if not sys.stdin.isatty():
        print('  Cleanup needs terminal confirmation that the passwords are saved in your password manager.')
        return
    answer = input('  Saved all three UI passwords in your password manager? Remove deployment copies [y/N]: ')
    if answer.strip().lower() not in ('y', 'yes'):
        return
    for name, expected in legacy.items():
        path = Path(site['private_dir']) / name
        if hashlib.sha256(read_private(path).encode()).hexdigest() != expected:
            raise PasswordSetupError('A recovery copy changed during installation; cleanup stopped.')
    for name in legacy:
        (Path(site['private_dir']) / name).unlink()
    print('  Removed redundant deployment password copies. Old backups may still contain them.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'finish'])
    parser.add_argument('--site-config', type=Path, required=True)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--runtime-dir', type=Path, required=True)
    args = parser.parse_args()
    from run import read_inputs
    site = read_inputs(args.site_config, args.env_file)
    (prepare if args.action == 'prepare' else finish)(site, args.runtime_dir)


if __name__ == '__main__':
    try:
        main()
    except PasswordSetupError as error:
        raise SystemExit(str(error)) from None
    except (OSError, ValueError, EOFError, getpass.GetPassWarning):
        # Errors from parsers and I/O may embed credential values; never echo them.
        raise SystemExit('UI password setup stopped. Check private-file ownership, retained credentials and terminal access; no passwords were printed.') from None
