#!/usr/bin/env python3
"""Authenticate/extract backups, or restore a confirmed compatible installed site."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile

from backup import selected_paths
from runtime import run, private_file, recovery_key, maintenance_lock, recover_on_interrupt
from run import absolute_path, read_inputs


def archive_path(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('Archive contains an unsafe member or hard-link path')
    return path


def inspect_archive(plaintext):
    """Validate the complete member graph before GNU tar can write any file.

    Preserve symlinks verbatim (system backups can contain absolute links), but
    never extract through one or use one as a hard-link target. Reject duplicate
    files and special nodes so later members cannot change an earlier decision.
    """
    members = {}
    required_bytes = 0
    with tarfile.open(plaintext, 'r:') as tar:
        for member in tar:
            path = archive_path(member.name)
            if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                raise ValueError('Archive contains an unsupported special file')
            previous = members.get(path)
            if previous is not None and not (previous.isdir() and member.isdir()):
                raise ValueError('Archive contains a duplicate file path')
            if member.size < 0:
                raise ValueError('Archive contains an invalid file size')
            members[path] = member
            # A conservative upper bound keeps the host reserve even for sparse
            # images; encrypted size is not an extraction-space estimate.
            required_bytes += member.size if member.isfile() else 0
            required_bytes += 4096
    if not members:
        raise ValueError('Archive contains no recoverable state')
    for path, member in members.items():
        if any(parent in members and not members[parent].isdir() for parent in path.parents):
            raise ValueError('Archive member descends through a non-directory or symbolic link')
        if member.islnk():
            seen = {path}
            target = archive_path(member.linkname)
            while True:
                if target in seen or target not in members:
                    raise ValueError('Archive hard link has a cyclic or missing target')
                seen.add(target)
                linked = members[target]
                if linked.isfile():
                    break
                if not linked.islnk():
                    raise ValueError('Archive hard link must resolve to an archived regular file')
                target = archive_path(linked.linkname)
    return len(members), required_bytes


def authenticate(archive, key, plaintext, limit):
    """Bound decrypted output before trusting any encrypted archive metadata."""
    if limit <= 0:
        raise ValueError('Insufficient free storage for authentication and host reserve')

    # Apply limits to GPG itself. Limiting the RTK wrapper also limits writes to
    # its existing accounting database and can fail before authentication starts.
    result = subprocess.run(['rtk', 'proxy', 'prlimit', f'--fsize={limit}:{limit}', '--core=0:0',
                             '--', 'gpg', '--batch', '--pinentry-mode', 'loopback',
                             '--no-symkey-cache', '--passphrase-file', str(key), '--output',
                             str(plaintext), '--decrypt', str(archive)],
                            capture_output=True)
    if result.returncode:
        raise RuntimeError('Archive authentication failed or exceeded available space; private output was suppressed')


def extract_verified(site, archive, destination, key):
    """Authenticate/extract under the caller's maintenance lock; touch no live state."""
    absolute_path(str(destination), 'restore destination')
    if destination.exists() or destination.resolve() != destination or len(destination.parts) < 4:
        raise ValueError('Restore needs a new private directory without symlink aliases')
    for path in selected_paths(site, Path(__file__).resolve().parents[1], require_existing=False):
        if destination == path or destination in path.parents or path in destination.parents:
            raise ValueError('Restore cannot overlap live persistent state')
    private_file(archive, 'Encrypted archive')
    parent = destination.parent
    if not parent.is_dir():
        raise ValueError('Create the private restore parent before verification')
    reserve = int(site.get('host_disk_reserve_gib', 20)) * 2**30
    destination.mkdir(mode=0o700)
    plaintext = destination / 'authenticated.tar'
    try:
        print('Authenticating the complete encrypted archive before extraction.', flush=True)
        authenticate(archive, key, plaintext, shutil.disk_usage(parent).free - reserve)
        count, required_bytes = inspect_archive(plaintext)
        if shutil.disk_usage(parent).free < reserve + required_bytes:
            raise ValueError('Insufficient free storage for extracted files and host reserve')
        root = destination / 'root'
        root.mkdir(mode=0o700)
        print('Extracting private state and comparing files, ownership and metadata.', flush=True)
        run('tar', '--extract', '--sparse', '--same-permissions', '--acls', '--xattrs',
            '--numeric-owner', '-C', root, '-f', plaintext)
        run('tar', '--compare', '--acls', '--xattrs', '--numeric-owner', '-C', root, '-f', plaintext)
        with archive.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        (destination / 'restore-receipt.json').write_text(json.dumps({'archive': str(archive),
            'sha256': digest, 'members_compared': count, 'activated_services': [],
            'result': 'authenticated extraction and tar comparison passed'}, indent=2) + '\n')
    finally:
        plaintext.unlink(missing_ok=True)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site-config', type=Path, default=Path('config/site.yaml'))
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--key-file', type=Path)
    parser.add_argument('--activate', action='store_true')
    parser.add_argument('--interactive', action='store_true')
    parser.add_argument('--confirm', default='')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Restore verification requires actual administrator access')
    os.umask(0o077)
    site = read_inputs(args.site_config, args.env_file)
    key = recovery_key(args.key_file or Path(site['private_dir']) / 'backup-passphrase')
    if args.activate:
        from restore_site import restore_site
        return restore_site(site, args.archive, key, args.confirm, args.interactive,
                            args.site_config, args.env_file)
    if not args.archive or not args.destination:
        parser.error('Supply --archive and --destination for extraction-only verification')
    with maintenance_lock(), recover_on_interrupt():
        root = extract_verified(site, args.archive, args.destination, key)
    print(f'Isolated extraction verified: {root}', flush=True)
    print('No restored service or bot was activated. Application boot/task checks remain separate.')


if __name__ == '__main__':
    try:
        raise SystemExit(main() or 0)
    except Exception as error:
        if isinstance(error, (ValueError, RuntimeError)):
            raise SystemExit(str(error)) from None
        raise SystemExit('Restore verification failed; inspect private inputs and target. Sensitive details suppressed.') from None
