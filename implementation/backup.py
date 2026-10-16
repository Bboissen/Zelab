#!/usr/bin/env python3
"""Stop Zelab writers, encrypt persistent state, compare it, and resume services."""

import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from run import absolute_path, read_inputs
from runtime import (run, maintenance_lock, private_directory, recovery_key, unique_paths,
                     recover_on_interrupt, stopped_writers)


SOURCE_PINS = (
    'versions.yaml',
    'deploy/roles/paperclip/files/package.json',
    'deploy/roles/paperclip/files/package-lock.json',
)

# These values affect native paths, credentials and connectivity. Restore keeps
# current host configuration, so it must match the state being recovered.
COMPATIBILITY_FIELDS = (
    'operator_user', 'vm_instance', 'vm_service', 'vm_user', 'vm_disk',
    'vm_data', 'vm_root', 'guest_user', 'guest_ssh_port', 'guest_dashboard_port',
    'guest_api_port', 'private_dir', 'paperclip_home', 'paperclip_user',
    'paperclip_port', 'paperclip_db_port', 'projects_dir', 'worker_ssh_host', 'worker_ssh_port',
    'notes_vault_dir', 'vault_history_dir', 'notes_sftp_port', 'obsidian_state_dir',
    'obsidian_backend_port', 'obsidian_username', 'dashboard_username',
    'ui_tailscale_hostname',
)


def selected_paths(site, repository, require_existing=True):
    required = [Path(site[name]) for name in ['vm_data', 'vm_root', 'paperclip_home',
                'notes_vault_dir', 'vault_history_dir', 'private_dir']]
    # A site without a team may never have created projects_dir, and one that never
    # ran the browser Obsidian has no obsidian_state_dir.
    workers = [Path(site['projects_dir']), Path(site['obsidian_state_dir'])]
    workers = [path for path in workers if not require_existing or path.exists()]
    for path in required + workers:
        absolute_path(str(path), 'persistent state')
        if (require_existing and not path.is_dir()) or path.resolve() != path:
            raise ValueError('A required persistent directory is absent or uses a symlink alias')
    paths = required + workers + [Path('/etc/ssh')]
    for pattern in ['/etc/zelab-*', '/etc/systemd/system/zelab-*',
                    '/etc/systemd/system/var-lib-zelab*', '/etc/sudoers.d/zelab-*']:
        paths.extend(Path('/').glob(pattern.lstrip('/')))
    for name in [site.get('obsidian_config_dir', '/etc/zelab-obsidian'),
                 site.get('vm_bootstrap_dir', ''), site.get('deploy_key', ''),
                 site.get('guest_known_hosts', ''),
                 f"/etc/systemd/system/{site['vm_service']}.service",
                 f"/etc/systemd/system/{site['vm_service']}.service.d",
                 *(str(repository / name) for name in SOURCE_PINS)]:
        if name and (not require_existing or Path(name).exists() or Path(name).is_symlink()):
            paths.append(Path(name))
    return unique_paths(paths)


def allocated_bytes(paths):
    total = 0
    for path in paths:
        if path.is_file() and not path.is_symlink():
            total += path.stat().st_blocks * 512
        elif path.is_dir() and not path.is_symlink():
            def fail(error):
                raise error

            for folder, _, names in os.walk(path, onerror=fail):
                for name in names:
                    info = Path(folder, name).lstat()
                    if stat.S_ISREG(info.st_mode):
                        total += info.st_blocks * 512
    return total


def require_separate_backup(destination, paths):
    if any(destination == path or path in destination.parents for path in paths):
        raise ValueError('Backup destination cannot be inside the state being archived')


def pipeline(first, second, log):
    with log.open('ab') as errors:
        producer = subprocess.Popen(['rtk', 'proxy', *map(str, first)], stdout=subprocess.PIPE, stderr=errors,
                                    start_new_session=True)
        consumer = None
        try:
            consumer = subprocess.Popen(['rtk', 'proxy', *map(str, second)], stdin=producer.stdout,
                                        stdout=subprocess.DEVNULL, stderr=errors, start_new_session=True)
            producer.stdout.close()
            consumer_code = consumer.wait()
            producer_code = producer.wait()
        except BaseException:
            producer.stdout.close()
            for process in [producer, consumer]:
                if process is not None and process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            for process in [producer, consumer]:
                if process is not None:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
            raise
    if producer_code or consumer_code:
        raise RuntimeError('Archive operation failed; inspect its private backup log')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site-config', type=Path, default=Path('config/site.yaml'))
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--key-file', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Backup requires actual administrator access')
    os.umask(0o077)
    site = read_inputs(args.site_config, args.env_file)
    if site.get('notes_enabled') is not True:
        raise ValueError('Application backup requires the notes vault (notes_enabled)')
    repository = Path(__file__).resolve().parents[1]
    key = recovery_key(args.key_file or Path(site['private_dir']) / 'backup-passphrase')
    destination = Path(absolute_path(site.get('backup_dir'), 'backup_dir'))
    if destination.resolve() != destination:
        raise ValueError('Backup destination cannot use symlink aliases')
    private_directory(destination, site.get('operator_user'))
    paths = selected_paths(site, repository)
    paths += [args.site_config.resolve()]
    if args.env_file.exists():
        paths.append(args.env_file.resolve())
    paths = unique_paths(paths)
    require_separate_backup(destination, paths)
    reserve = int(site.get('host_disk_reserve_gib', 20)) * 2**30
    if shutil.disk_usage(destination).free < reserve + allocated_bytes(paths) * 2 + 2**30:
        raise ValueError('Insufficient storage for encrypted backup and host reserve')
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    archive = destination / f'zelab-applications-{timestamp}.tar.gpg'
    if archive.exists() or archive.with_suffix('.gpg.partial').exists():
        raise ValueError('Backup filename already exists')
    vm = site.get('vm_service', 'zelab-vm') + '.service'
    if run('systemctl', 'show', vm, '-p', 'RootDirectory', '--value').decode().strip() != site['vm_root']:
        raise ValueError('Selected VM service does not own the configured VM root')
    log = destination / f'zelab-applications-{timestamp}.log'
    with maintenance_lock(), recover_on_interrupt():
        print('Stopping Zelab writers for a consistent backup.', flush=True)
        with stopped_writers(site, vm) as (active_services, active_containers):
            chain = json.loads(run('qemu-img', 'info', '--backing-chain', '--output=json', site['vm_disk']))
            if not chain or any(Path(image['filename']).resolve().parent != Path(site['vm_data']) for image in chain):
                raise ValueError('VM backing chain escapes the selected data directory')
            with tempfile.TemporaryDirectory(prefix='zelab-backup-', dir='/run') as folder:
                stage = Path(folder)
                receipt = stage / 'manifest.json'
                receipt.write_text(json.dumps({'timestamp': timestamp, 'scope': [str(p) for p in paths],
                    'vm_chain': chain, 'services_resumed': active_services, 'containers_resumed': active_containers,
                    'site_config': str(args.site_config.resolve()),
                    'env_file': str(args.env_file.resolve()) if args.env_file.exists() else None,
                    'compatibility': {name: site.get(name) for name in COMPATIBILITY_FIELDS},
                    'retention_days': 90}, indent=2) + '\n')
                listing = stage / 'paths.list'
                listing.write_bytes(b''.join(os.fsencode(str(p).lstrip('/')) + b'\0' for p in paths + [receipt]))
                gpg = ['gpg', '--batch', '--pinentry-mode', 'loopback', '--no-symkey-cache', '--passphrase-file', key]
                partial = archive.with_suffix('.gpg.partial')
                pipeline(['tar', '--sparse', '--acls', '--xattrs', '--numeric-owner',
                          '--exclude=*/console.sock', '--exclude=*/qmp.sock', '-C', '/', '-cf', '-',
                          '--null', '--verbatim-files-from', '--files-from', listing],
                         [*gpg, '--symmetric', '--cipher-algo', 'AES256', '--compress-algo', 'none', '--output', partial], log)
                print('Comparing decrypted archive with stopped persistent state.', flush=True)
                pipeline([*gpg, '--decrypt', partial],
                         ['tar', '--compare', '--acls', '--xattrs', '--numeric-owner', '-C', '/', '-f', '-'], log)
                partial.rename(archive)
        print(f'Verified encrypted application backup: {archive}', flush=True)
        print('Previously running Zelab services resumed. Retain this backup for 90 days; no backup was pruned.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if isinstance(error, (ValueError, RuntimeError)):
            raise SystemExit(str(error)) from None
        raise SystemExit('Backup failed; check private inputs and service state. Sensitive details suppressed.') from None
