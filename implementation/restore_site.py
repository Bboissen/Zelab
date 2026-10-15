"""Restore authenticated application state to an installed, compatible site."""

import datetime
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request

from backup import COMPATIBILITY_FIELDS, SOURCE_PINS, allocated_bytes, pipeline
from fresh import site_identity
from install_resources import InstallationResources
from restore import extract_verified
from run import read_inputs
from runtime import (maintenance_lock, private_directory, recover_on_interrupt,
                     require_idle_workers, run, service_active, stopped_writers)

ROOT = Path(__file__).resolve().parents[1]
STATE_FIELDS = ('vm_data', 'vm_root', 'paperclip_home', 'projects_dir', 'notes_vault_dir',
                'vault_history_dir', 'obsidian_state_dir', 'private_dir')
# A site without host projects may have no projects_dir; backup.py archives it
# only when it exists.
WORKER_HOMES = frozenset({'projects_dir'})
PAPERCLIP_ENV = Path('/etc/zelab-paperclip.env')


def select_archive(site, archive, interactive):
    if archive:
        return archive.absolute()
    choices = sorted(Path(site['backup_dir']).glob('zelab-applications-*.tar.gpg'), reverse=True)
    if not interactive or not sys.stdin.isatty():
        raise ValueError('Select a backup with make restore RESTORE_ARCHIVE=/absolute/path/backup.tar.gpg')
    if not choices:
        raise ValueError('No application backups found; set RESTORE_ARCHIVE to a private encrypted archive')
    for index, path in enumerate(choices, 1):
        print(f'  {index}. {path.name}')
    answer = input('Backup number (Enter selects 1): ').strip() or '1'
    if not answer.isdecimal() or not 1 <= int(answer) <= len(choices):
        raise ValueError('No backup selected; nothing changed')
    return choices[int(answer) - 1]


def present(path):
    return path.exists() or path.is_symlink()


def restore_paths(site, snapshot):
    """Live paths a restore replaces, given the authenticated extraction root.

    A worker home is included when the live site or the backup has it, so
    validate_snapshot refuses a backup that misses a present home and a site
    that lacks a backed-up one; a home absent from both is not required.
    """
    paths = [Path(site[name]) for name in STATE_FIELDS if name not in WORKER_HOMES
             or present(Path(site[name])) or present(snapshot / Path(site[name]).relative_to('/'))]
    paths += [Path(site.get('vm_bootstrap_dir', '/var/lib/zelab-vm/bootstrap')),
              Path(site.get('notes_config_dir', '/etc/zelab-notes')),
              Path(site.get('obsidian_config_dir', '/etc/zelab-obsidian')),
              PAPERCLIP_ENV]
    return paths


def validate_snapshot(site, root, paths, repository=ROOT, site_config=None, env_file=None):
    manifests = list((root / 'run').glob('zelab-backup-*/manifest.json'))
    if len(manifests) != 1:
        raise ValueError('This is not a complete Zelab application backup (one native manifest is required)')
    manifest = json.loads(manifests[0].read_text())
    scope = {Path(path) for path in manifest.get('scope', [])}
    chain = manifest.get('vm_chain', [])
    if (not chain or Path(chain[0]['filename']) != Path(site['vm_disk'])
            or any(Path(image['filename']).parent != Path(site['vm_data']) for image in chain)):
        raise ValueError('Backup VM identity differs from this site; no live state was changed')
    compatible = manifest.get('compatibility')
    if compatible is None:
        # Earlier backups contain their input files rather than this index.
        saved_site = Path(manifest.get('site_config') or site_config or repository / 'config/site.yaml')
        saved_env = Path(manifest.get('env_file') or env_file or repository / '.env')
        config = root / saved_site.resolve().relative_to('/')
        environment = root / saved_env.resolve().relative_to('/')
        if not config.is_file() or config.is_symlink():
            raise ValueError('Backup lacks its original site configuration; select a complete application backup')
        compatible = read_inputs(config, environment if environment.exists() else None, inspect_private=False)
    differences = [name for name in COMPATIBILITY_FIELDS if compatible.get(name) != site.get(name)]
    if differences:
        raise ValueError('Backup inputs differ from the installed site: ' + ', '.join(differences)
                         + '. Use the matching site inputs before restore.')
    for relative in SOURCE_PINS:
        matches = [path for path in scope if path.as_posix().endswith('/' + relative)]
        if len(matches) != 1:
            raise ValueError('Backup is missing its exact source/runtime pins')
        saved = root / matches[0].relative_to('/')
        if saved.is_symlink() or not saved.is_file() or saved.read_bytes() != (repository / relative).read_bytes():
            raise ValueError('Backup runtime pins differ; use the matching Zelab source before restoring')
    for path in paths:
        saved = root / path.relative_to('/')
        if not any(path == parent or parent in path.parents for parent in scope):
            raise ValueError(f'Backup does not cover this site state path: {path}')
        if path.is_symlink() or path.resolve() != path or path.is_mount():
            raise ValueError(f'Restore target is a symlink or filesystem mount: {path}')
        if not path.exists() or saved.is_symlink() or not saved.exists():
            raise ValueError('Restore requires the complete installed site and all backed-up state; run make first after fclean')
        before, after = path.stat(), saved.stat()
        if (stat.S_IFMT(before.st_mode), before.st_uid, before.st_gid) != (stat.S_IFMT(after.st_mode), after.st_uid, after.st_gid):
            raise ValueError(f'Backup service ownership/type differs at {path}; restore with matching installed identities')
    return manifest


def clone(source, parent, log):
    pipeline(['tar', '--sparse', '--acls', '--xattrs', '--numeric-owner',
              '-C', source.parent, '-cf', '-', source.name],
             ['tar', '--extract', '--sparse', '--same-permissions', '--acls', '--xattrs',
              '--numeric-owner', '-C', parent, '-f', '-'], log)
    return parent / source.name


def supervised_mounts(site):
    """Prove the running site's native ownership without reset-only gates."""
    InstallationResources(site).owned_unit()
    systemd = Path('/etc/systemd/system')
    paperclip = (systemd / 'zelab-paperclip.service').read_text()
    writable = [part for line in paperclip.splitlines() if line.startswith('ReadWritePaths=')
                for part in line.partition('=')[2].split()]
    if site['paperclip_home'] not in writable:
        raise ValueError('Paperclip service does not belong to the selected site')
    container = json.loads(run('docker', 'container', 'inspect', 'zelab-obsidian'))[0]
    labels = (container.get('Config') or {}).get('Labels') or {}
    if labels.get('com.docker.compose.project') != 'zelab-notes':
        raise ValueError('Obsidian container is not owned by this Zelab site')
    bound = {mount.get('Destination'): mount.get('Source') for mount in container.get('Mounts', [])}
    if (bound.get('/vault') != site['notes_vault_dir']
            or bound.get('/config') != site['obsidian_state_dir'] + '/config'):
        raise ValueError('Obsidian does not mount this site vault/state')
    exports = site.get('notes_sftp_root', '/var/lib/zelab-notes-sftp')
    sources = {site['notes_vault_dir']}
    mounts = []
    for path in systemd.glob('*.mount'):
        text = path.read_text()
        fields = dict(line.partition('=')[::2] for line in text.splitlines() if '=' in line)
        if fields.get('Description', '').startswith('Zelab') and fields.get('What') in sources:
            where = Path(fields.get('Where', '/'))
            if Path(exports) not in where.parents:
                raise ValueError('A Zelab notes mount has an unexpected export path')
            mounts.append(path.name)
    if len(mounts) != 2:
        raise ValueError('Both installed native notes exports are required before restore; run make first')
    return mounts


class Replacement:
    """Stage beside the live path so activation and rollback use atomic renames."""

    def __init__(self, target, source, log, reserve):
        self.target = target
        self.folder = None
        self.old_moved = self.new_moved = False
        if shutil.disk_usage(target.parent).free < reserve + allocated_bytes([source]):
            raise ValueError(f'Insufficient staging space on the filesystem receiving {target}')
        self.folder = Path(tempfile.mkdtemp(prefix='.zelab-restore-', dir=target.parent))
        self.old = self.folder / 'previous'
        try:
            self.new = clone(source, self.folder, log)
        except BaseException:
            shutil.rmtree(self.folder)
            raise

    def activate(self):
        self.target.rename(self.old)
        self.old_moved = True
        try:
            self.new.rename(self.target)
            self.new_moved = True
        except BaseException:
            self.old.rename(self.target)
            self.old_moved = False
            raise

    def rollback(self):
        if self.new_moved:
            self.target.rename(self.new)
            self.new_moved = False
        if self.old_moved:
            self.old.rename(self.target)
            self.old_moved = False

    def finish(self):
        shutil.rmtree(self.folder)


def wait_for_applications(site, services, containers, timeout=180):
    checks = []
    if site['vm_service'] + '.service' in services:
        checks.append((f"http://127.0.0.1:{site['guest_dashboard_port']}/api/profiles", 401))
    if 'zelab-paperclip.service' in services:
        checks.append((f"http://127.0.0.1:{site['paperclip_port']}/api/health", 200))
    if 'zelab-obsidian' in containers:
        checks.append((f"http://127.0.0.1:{site['obsidian_backend_port']}/", 401))
    deadline = time.monotonic() + timeout
    while checks and time.monotonic() < deadline:
        waiting = []
        for url, expected in checks:
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    status = response.status
            except urllib.error.HTTPError as error:
                status = error.code
            except OSError:
                status = 0
            if status != expected:
                waiting.append((url, expected))
        checks = waiting
        if checks:
            time.sleep(1)
    if checks:
        raise RuntimeError('Restored application readiness failed')


def restore_site(site, archive, key, confirm='', interactive=False, site_config=None, env_file=None):
    archive = select_archive(site, archive, interactive)
    destination = Path(site['backup_dir']) / ('restore-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))
    private_directory(destination.parent, site['operator_user'])
    vm = site['vm_service'] + '.service'
    with maintenance_lock(), recover_on_interrupt():
        root = extract_verified(site, archive, destination, key)
        paths = restore_paths(site, root)
        validate_snapshot(site, root, paths, site_config=site_config, env_file=env_file)
        installed_mounts = supervised_mounts(site)
        print(f'Restore {archive.name} to {site_identity(site)}. The current application state will be replaced:')
        for path in paths:
            print('  ' + str(path))
        print('Keeps current inputs, personal harness logins, SSH/Tailscale, backups, recovery key and private logs.')
        if not confirm and interactive and sys.stdin.isatty():
            confirm = input(f'Type {site_identity(site)} to restore this backup: ').strip()
        if confirm != site_identity(site):
            raise ValueError(f'Nothing changed. Confirm this site with CONFIRM={site_identity(site)}')
        replacements = []
        log = destination / 'restore.log'
        receipt = destination / 'activation.json'
        reserve = int(site.get('host_disk_reserve_gib', 20)) * 2**30
        mounts = [unit for unit in installed_mounts if service_active(unit)]
        active_services = active_containers = []
        try:
            for path in paths:
                replacements.append(Replacement(path, root / path.relative_to('/'), log, reserve))
            private = next(item for item in replacements if item.target == Path(site['private_dir']))
            for name in ('backup-passphrase', 'logs'):
                current = private.target / name
                if current.exists():
                    if current.is_symlink():
                        raise ValueError('Preserved recovery key/logs must not use symlink aliases')
                    saved = private.new / name
                    if saved.is_dir() and not saved.is_symlink():
                        shutil.rmtree(saved)
                    else:
                        saved.unlink(missing_ok=True)
                    clone(current, private.new, log)
            receipt.write_text(json.dumps({'archive': str(archive), 'paths': [
                {'target': str(item.target), 'previous': str(item.old), 'candidate': str(item.new)}
                for item in replacements], 'result': 'staged; live state unchanged'}, indent=2))
            with stopped_writers(site, vm) as (active_services, active_containers):
                try:
                    for mount in reversed(mounts):
                        run('systemctl', 'stop', mount)
                    for item in replacements:
                        item.activate()
                    receipt.write_text(json.dumps({'archive': str(archive), 'paths': [
                        {'target': str(item.target), 'previous': str(item.old)} for item in replacements],
                        'result': 'activated; restarting native applications'}, indent=2))
                except BaseException:
                    for item in reversed(replacements):
                        item.rollback()
                    raise
                finally:
                    for mount in mounts:
                        run('systemctl', 'start', mount)
            wait_for_applications(site, active_services, active_containers)
        except BaseException:
            if any(item.old_moved for item in replacements):
                # Stop restored writers before touching their state again. If
                # new work appeared, retain both copies for explicit recovery.
                require_idle_workers(site)
                with stopped_writers(site, vm):
                    try:
                        for mount in reversed(mounts):
                            run('systemctl', 'stop', mount)
                        for item in reversed(replacements):
                            item.rollback()
                    finally:
                        for mount in mounts:
                            run('systemctl', 'start', mount)
                for service in active_services:
                    run('systemctl', 'start', service)
                for container in active_containers:
                    run('docker', 'start', container)
            for item in replacements:
                if not item.old_moved:
                    item.finish()
            raise
        for item in replacements:
            item.finish()
        receipt.write_text(json.dumps({'archive': str(archive), 'restored_paths': [str(path) for path in paths],
            'services_resumed': active_services, 'containers_resumed': active_containers,
            'result': 'application state restored; native readiness passed'}, indent=2))
    print(f'Backup restored. Previously running applications answer; receipt: {receipt}')
    return 0
