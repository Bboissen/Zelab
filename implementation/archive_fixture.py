"""Disposable encrypted-archive fixtures for restore tests. Not a test module."""

import subprocess


def run(test, *args):
    result = subprocess.run(['rtk', 'proxy', *map(str, args)], capture_output=True)
    test.assertEqual(result.returncode, 0, 'Disposable archive command failed')
    return result


def encrypt(test, tar, archive, key):
    """Encrypt like the native backup: gpg symmetric AES256, no compression, 0600."""
    run(test, 'gpg', '--batch', '--pinentry-mode', 'loopback', '--no-symkey-cache',
        '--passphrase-file', key, '--symmetric', '--cipher-algo', 'AES256',
        '--compress-algo', 'none', '--output', archive, tar)
    archive.chmod(0o600)
