"""Restore the shared vault's native ACLs, including files created with mode 0600.

Called by make notes with the deployment operator's existing sudo rights. Never
read note contents, follow symlinks, cross devices or change multiply linked files.
"""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys


def open_root(path):
    """Open each component separately so even ancestor symlinks are refused."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or path == Path('/'):
        raise ValueError('Expected an absolute vault directory, other than /.')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def restore(path, uids):
    uids = sorted({str(int(uid)) for uid in uids})
    if not uids or any(int(uid) < 1 for uid in uids):
        raise ValueError('Expected non-root editor UIDs.')
    changed = skipped = 0
    root = open_root(path)
    device = os.fstat(root).st_dev

    def visit(fd):
        nonlocal changed, skipped
        info = os.fstat(fd)
        directory = stat.S_ISDIR(info.st_mode)
        if info.st_dev != device or not (directory or stat.S_ISREG(info.st_mode)) or (
                not directory and info.st_nlink != 1):
            skipped += 1
            return
        # A stable descriptor prevents a concurrent rename/symlink replacement
        # from redirecting setfacl outside the inode we inspected.
        target = f'/proc/self/fd/{fd}'
        current = set(subprocess.check_output(
            ['getfacl', '-cpn', target], pass_fds=(fd,), text=True).splitlines())
        permissions = 'rwx' if directory or info.st_mode & 0o111 else 'rw-'
        wanted = {f'user:{uid}:{permissions}' for uid in uids} | {f'mask::{permissions}'}
        if directory:
            wanted |= {f'default:user:{uid}:rwx' for uid in uids}
            wanted |= {'default:user::rwx', 'default:group::rwx',
                       'default:mask::rwx', 'default:other::---'}
        # getfacl appends effective-permission comments when the mask blocks an
        # entry; strip those before comparing for an unchanged second pass.
        current = {line.split('\t', 1)[0] for line in current}
        if not wanted <= current:
            subprocess.run(['setfacl', '-m', ','.join(sorted(wanted)), target],
                           pass_fds=(fd,), check=True, capture_output=True)
            changed += 1
        if directory:
            for name in os.listdir(fd):
                try:
                    child = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    continue  # An agent renamed/deleted its note while we walked.
                try:
                    if stat.S_ISDIR(os.fstat(child).st_mode):
                        readable = os.open('.', os.O_RDONLY | os.O_DIRECTORY, dir_fd=child)
                        try:
                            visit(readable)
                        finally:
                            os.close(readable)
                    else:
                        visit(child)
                finally:
                    os.close(child)

    try:
        visit(root)
    finally:
        os.close(root)
    return {'changed': changed, 'skipped': skipped}


if __name__ == '__main__':
    try:
        print(json.dumps(restore(sys.argv[1], sys.argv[2:])))
    except (OSError, ValueError, subprocess.SubprocessError):
        raise SystemExit('Vault ACL repair failed; no note contents were read or printed.') from None
