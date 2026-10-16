#!/usr/bin/env python3
"""Preview or perform a site-scoped fresh reset of one Zelab installation.

Only resources whose ownership is proven from the selected site are removed:
creation receipts, unit contents, account homes, file owners, container labels
and Serve backends. Personal logins, host/path inputs, backups and their keys,
SSH/Tailscale enrollment, operator administration and unrelated services stay.
A missing or wrong confirmation changes nothing.
"""

import argparse
from dataclasses import dataclass, field
import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
sys.path.insert(0, str(ROOT / 'implementation'))
import yaml  # noqa: E402
from run import read_inputs  # noqa: E402
from install_resources import InstallationResources  # noqa: E402
from console import heading, mark, paint  # noqa: E402
import shell  # noqa: E402
import artifacts  # noqa: E402
from runtime import maintenance_lock, recover_on_interrupt  # noqa: E402

SYSTEMD = Path('/etc/systemd/system')
VM_DESCRIPTION = 'Description=Zelab native Hermes guest'
# Files the installer generates in private_dir. backup-passphrase protects
# existing backups and logs/ holds diagnostics, so both are kept.
GENERATED_PRIVATE = [
    'deploy_ed25519', 'deploy_ed25519.pub', 'guest_known_hosts', 'worker_known_hosts',
    'worker-ops.key', 'worker-ops.key.pub', 'worker-code.key', 'worker-code.key.pub',
    'notes-owner.key', 'notes-owner.key.pub', 'notes-shared.key', 'notes-shared.key.pub',
    'paperclip-master.key', 'paperclip-agent-jwt.secret', 'paperclip-owner.password',
    'paperclip-worker-ids.json', 'paperclip-ids.json', 'paperclip-hermes-bridge.key', 'paperclip-hermes-gateway.key',
    'paperclip-hermes-submission.key', 'paperclip-bridge-ids.json',
    'paperclip-hermes-ids.json', 'hermes-dashboard.password', 'hermes-dashboard.password-hash', 'hermes-dashboard.session-secret',
    'hermes-api.key', 'obsidian.password', 'checklist.html', 'guide.html',
]
LEGACY_NOTES_USER, LEGACY_NOTES_GROUP = 'zelab-notes-shared', 'zelab-vault-shared'
SHARED_TOOLS = ['/usr/local/lib/zelab', '/opt/zelab-tools', '/var/cache/zelab']
BOOTSTRAP_FILES = {'ssh_host_ed25519_key', 'ssh_host_ed25519_key.pub', 'user-data', 'meta-data', 'creating', 'created'}


@dataclass
class Plan:
    site_id: str
    units: list = field(default_factory=list)          # (unit, evidence)
    containers: list = field(default_factory=list)     # names
    networks: list = field(default_factory=list)
    images: list = field(default_factory=list)
    routes: list = field(default_factory=list)         # tailnet HTTPS ports
    nft_tables: list = field(default_factory=list)
    files: list = field(default_factory=list)          # (path, evidence)
    acl_revocations: list = field(default_factory=list)  # (path, user): granted project paths
    directories: list = field(default_factory=list)    # (path, evidence)
    accounts: list = field(default_factory=list)       # (user, evidence)
    groups: list = field(default_factory=list)
    private_files: list = field(default_factory=list)
    refused: list = field(default_factory=list)        # (resource, reason)
    notes: list = field(default_factory=list)          # data-loss warnings
    protected: list = field(default_factory=list)      # never deleted or contained in a deletion
    artifact_paths: list = field(default_factory=list)  # disposable outputs, checked for active users
    transient_units: list = field(default_factory=list)  # qualification QEMU units, never unit files
    user_units: list = field(default_factory=list)  # operator's transient scratch Paperclip units

    def empty(self):
        return not any([self.units, self.containers, self.networks, self.images, self.routes,
                        self.nft_tables, self.files, self.directories, self.accounts, self.groups,
                        self.private_files, self.transient_units, self.user_units])


def command(*argv, check=True):
    result = shell.quiet(argv, text=True)
    if check and result.returncode:
        raise RuntimeError(f'{argv[0]} {argv[1] if len(argv) > 1 else ""} failed; output suppressed')
    return result


def site_identity(site, hostname=None):
    return f"{(hostname or socket.gethostname()).split('.')[0]}:{site['vm_instance']}"


def receipt_matches(path, site):
    try:
        return Path(path).read_text() == f"{site['vm_disk']}\n{site['vm_instance']}\n{site['vm_service']}\n"
    except OSError:
        return False


def unit_mentions(path, *needles):
    try:
        text = Path(path).read_text()
    except OSError:
        return False
    return all(needle in text for needle in needles)


def account(name):
    try:
        return pwd.getpwnam(name)
    except KeyError:
        return None


def owned_account(name, home=None, comment=None):
    """A system account whose home or comment matches what the installer set."""
    entry = account(name)
    if entry is None or entry.pw_uid == 0 or entry.pw_uid >= 1000:
        return None
    if home is not None and entry.pw_dir != home:
        return None
    if comment is not None and entry.pw_gecos != comment:
        return None
    return entry


def owned_by(path, uid):
    try:
        info = Path(path).lstat()
    except OSError:
        return False
    return uid is not None and info.st_uid == uid and not Path(path).is_symlink()


def mountpoints():
    points = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        points.append(Path(line.split()[4].replace('\\040', ' ')))
    return points


def safe_to_delete(path, protected, mounts):
    """Refuse symlinks, mount points, anything containing a mount, and protected trees."""
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path or len(path.parts) < 3:
        return 'not a canonical dedicated directory'
    for keep in protected:
        keep = Path(keep)
        if path == keep or keep.is_relative_to(path):
            return f'contains preserved {keep}'
    for mount in mounts:
        if mount == path or mount.is_relative_to(path):
            return f'mount {mount} is still active below it'
    return None


def count_files(path, limit=100000):
    total = 0
    for _root, _dirs, files in os.walk(path):
        total += len(files)
        if total >= limit:
            break
    return total


def owned_tail_ports(config, hostname, backends):
    """Tailnet HTTPS ports whose root handler proxies to this site's backends."""
    return sorted(port for host, port, proxy in shell.serve_routes(config or {})
                  if host == hostname and proxy in backends)


def role_defaults(name):
    return yaml.safe_load((ROOT / f'deploy/roles/{name}/defaults/main.yaml').read_text())


def other_zelab_guests(site):
    found = []
    for unit in SYSTEMD.glob('*.service'):
        if unit.name != site['vm_service'] + '.service' and unit_mentions(unit, VM_DESCRIPTION):
            found.append(unit.name)
    return found


def discover(site, hostname=None):
    plan = Plan(site_identity(site, hostname))
    notes = role_defaults('notes_host')
    obsidian_config = role_defaults('obsidian_host')['obsidian_config_dir']
    protected = [ROOT, site['private_dir'], site['backup_dir'],
                 pwd.getpwnam(site['operator_user']).pw_dir, '/etc', '/var/lib', '/opt', '/home', '/usr']
    discover_guest(plan, site)
    discover_paperclip(plan, site)
    discover_projects(plan, site)
    discover_notes(plan, site, notes)
    discover_obsidian(plan, obsidian_config)
    discover_routes(plan, site)
    discover_shared_tools(plan, site)
    discover_private(plan, site)
    artifacts.discover(plan, site)
    plan.protected = protected
    return plan


def discover_guest(plan, site):
    """Guest VM: the creation receipt is the ownership proof for its directories."""
    vm_unit = SYSTEMD / (site['vm_service'] + '.service')
    if vm_unit.exists():
        if unit_mentions(vm_unit, VM_DESCRIPTION, f"RootDirectory={site['vm_root']}"):
            plan.units.append((vm_unit.name, f"RootDirectory={site['vm_root']}"))
        else:
            plan.refused.append((str(vm_unit), 'unit does not run this site\'s VM root'))
    bootstrap = Path(site['vm_bootstrap_dir'])
    receipt = next((bootstrap / name for name in ('created', 'creating') if (bootstrap / name).exists()), None)
    vm_paths = [site['vm_data'], site['vm_root'], site['vm_bootstrap_dir']]
    vm_user = owned_account(site['vm_user'], comment='Zelab native VM')
    if vm_user:
        plan.accounts.append((site['vm_user'], 'comment "Zelab native VM"'))
    if receipt and receipt_matches(receipt, site):
        for path in vm_paths:
            if Path(path).exists():
                plan.directories.append((path, f'creation receipt {receipt.name}'))
        if Path(site['vm_disk']).exists():
            plan.notes.append('Hermes profiles, memories, sessions, logins and the Discord token inside the guest disk')
    elif receipt:
        for path in vm_paths:
            if Path(path).exists():
                plan.refused.append((path, 'creation receipt names another disk, instance or service'))
    else:
        # Interrupted before any disk was written: empty directories this installer made.
        uid = vm_user.pw_uid if vm_user else None
        for path in vm_paths[:2]:
            if Path(path).exists():
                if owned_by(path, uid) and not Path(site['vm_disk']).exists():
                    plan.directories.append((path, f"owned by {site['vm_user']}, no disk"))
                else:
                    plan.refused.append((path, 'no creation receipt; inspect it, then move it aside or choose another vm_data'))
        if bootstrap.is_dir():
            if owned_by(bootstrap, 0) and {entry.name for entry in bootstrap.iterdir()} <= BOOTSTRAP_FILES:
                plan.directories.append((str(bootstrap), 'only installer bootstrap files'))
            else:
                plan.refused.append((str(bootstrap), 'unexpected contents'))


def discover_paperclip(plan, site):
    """Paperclip coordinator, its database boundary and release directory."""
    paperclip = owned_account(site['paperclip_user'], home=site['paperclip_home'])
    if paperclip:
        plan.accounts.append((site['paperclip_user'], f"home {site['paperclip_home']}"))
    for unit, needle in (('zelab-paperclip.service', f"ReadWritePaths={site['paperclip_home']}"),
                         ('zelab-paperclip-db.service', '/etc/zelab-paperclip-db.nft')):
        path = SYSTEMD / unit
        if path.exists():
            if unit_mentions(path, needle):
                plan.units.append((unit, needle))
            else:
                plan.refused.append((str(path), 'unit content does not match this site'))
    if paperclip or any(unit == 'zelab-paperclip.service' for unit, _ in plan.units):
        for path in ('/etc/zelab-paperclip.env', '/etc/zelab-paperclip-db.nft'):
            if Path(path).exists():
                plan.files.append((path, 'Paperclip service input'))
        if command('nft', 'list', 'table', 'inet', 'zelab_paperclip_db', check=False).returncode == 0:
            plan.nft_tables.append('inet zelab_paperclip_db')
    uid = paperclip.pw_uid if paperclip else None
    if Path(site['paperclip_home']).exists():
        if owned_by(site['paperclip_home'], uid):
            plan.directories.append((site['paperclip_home'], f"owned by {site['paperclip_user']}"))
            plan.notes.append('Paperclip tasks, run history, uploads and the owner login')
        else:
            plan.refused.append((site['paperclip_home'], 'not owned by the site Paperclip account'))
    releases = Path('/opt/paperclip/releases')
    if releases.is_dir() and uid is not None and all(owned_by(r, uid) for r in releases.iterdir()):
        plan.directories.append(('/opt/paperclip', 'releases owned by the Paperclip account'))


def host_accounts():
    """{user: (label, home folder name, grants key)} of team tier accounts and earlier per-project ones."""
    found = {}
    for entry in pwd.getpwall():
        tier = entry.pw_name.removeprefix('zelab-team-')
        if entry.pw_name != tier and entry.pw_gecos == f'Zelab team tier {tier}':
            found[entry.pw_name] = (f'team tier {tier}', tier, entry.pw_name)
        slug = entry.pw_name.removeprefix('zelab-p-')
        if entry.pw_name != slug and entry.pw_gecos == f'Zelab host project {slug}':
            found[entry.pw_name] = (f'host project {slug}', slug, slug)
    return found


def discover_projects(plan, site):
    """Team tier accounts (zelab-team-<tier>; earlier zelab-p-<slug>), their grants, homes and path ACLs."""
    projects_dir = Path(site['projects_dir'])
    accounts = host_accounts()
    for user, (label, _, _) in sorted(accounts.items()):
        plan.accounts.append((user, label))
        try:
            group = grp.getgrnam(user)
            if set(group.gr_mem) <= {user}:
                plan.groups.append(user)
        except KeyError:
            pass
        sudoers = Path('/etc/sudoers.d') / user
        if sudoers.exists():
            if sudoers.read_text().startswith(f'# Zelab {label}.'):
                plan.files.append((str(sudoers), f'{label} sudo grant'))
            else:
                plan.refused.append((str(sudoers), 'unexpected content'))
    grants = projects_dir / '.zelab-grants.json'
    if grants.is_file():
        by_key = {key: user for user, (_, _, key) in accounts.items()}
        for key, paths in json.loads(grants.read_text()).get('paths', {}).items():
            user = by_key.get(key, key if key.startswith('zelab-team-') else f'zelab-p-{key}')
            plan.acl_revocations.extend((path, user) for path in paths if Path(path).exists())
    if projects_dir.is_dir():
        homes = {home for _, home, _ in accounts.values()}
        if all(entry.name == '.zelab-grants.json' or entry.name in homes for entry in projects_dir.iterdir()):
            plan.directories.append((str(projects_dir), 'team tier homes'))
        else:
            plan.refused.append((str(projects_dir), 'contains folders of accounts this site did not create'))
    dropin = Path('/etc/ssh/sshd_config.d/05-zelab-projects.conf')
    if dropin.exists():
        if dropin.read_text().startswith(('# Team tier accounts; managed by make.',
                                           '# Project host accounts; managed by make.')):
            plan.files.append((str(dropin), 'team SSH allowlist addition'))
        else:
            plan.refused.append((str(dropin), 'unexpected content'))


def discover_notes(plan, site, notes):
    """Notes share, its export units, the vault and its history."""
    notes_owner = owned_account(notes['notes_owner_user'], home='/vault')
    # Sites from the earlier Private/Shared layout also have a shared-notes account.
    notes_users = (notes['notes_owner_user'], LEGACY_NOTES_USER)
    for user in notes_users:
        if owned_account(user, home='/vault'):
            plan.accounts.append((user, 'notes-only account (home /vault)'))
    zelab_users = set(notes_users)
    for name in (notes['notes_owner_group'], LEGACY_NOTES_GROUP):
        try:
            group = grp.getgrnam(name)
        except KeyError:
            continue
        if group.gr_gid < 1000 and set(group.gr_mem) <= zelab_users:
            plan.groups.append(name)
        else:
            plan.refused.append((f'group {name}', 'has members outside Zelab'))
    for user in notes_users:
        where = f"{notes['notes_sftp_root']}/{user}/vault"
        unit = command('systemd-escape', '--path', '--suffix=mount', where).stdout.strip()
        if (SYSTEMD / unit).exists():
            if unit_mentions(SYSTEMD / unit, 'Description=Zelab', f'Where={where}'):
                plan.units.append((unit, f'Where={where}'))
            else:
                plan.refused.append((str(SYSTEMD / unit), 'mount unit content does not match'))
    notes_unit = SYSTEMD / 'zelab-notes-sshd.service'
    if notes_unit.exists():
        if unit_mentions(notes_unit, f"{notes['notes_config_dir']}/sshd.conf"):
            plan.units.append((notes_unit.name, f"{notes['notes_config_dir']}/sshd.conf"))
        else:
            plan.refused.append((str(notes_unit), 'unit content does not match'))
    config_dir, sftp_root = Path(notes['notes_config_dir']), Path(notes['notes_sftp_root'])
    if config_dir.is_dir():
        if unit_mentions(config_dir / 'sshd.conf', f'ChrootDirectory {sftp_root}/%u') or not any(config_dir.iterdir()) \
                or {entry.name for entry in config_dir.iterdir()} <= {'authorized_keys'}:
            plan.directories.append((str(config_dir), 'notes SFTP configuration'))
        else:
            plan.refused.append((str(config_dir), 'does not configure this site\'s notes export'))
    if sftp_root.is_dir():
        if {entry.name for entry in sftp_root.iterdir()} <= set(notes_users):
            plan.directories.append((str(sftp_root), 'notes export chroots only'))
        else:
            plan.refused.append((str(sftp_root), 'contains entries other than the notes export chroots'))
    owner_uid = notes_owner.pw_uid if notes_owner else None
    for path, label in ((site['notes_vault_dir'], 'notes vault'), (site['obsidian_state_dir'], 'Obsidian state')):
        if Path(path).exists():
            if owned_by(path, owner_uid):
                plan.directories.append((path, f"{label} owned by {notes['notes_owner_user']}"))
            else:
                plan.refused.append((path, f"{label} not owned by {notes['notes_owner_user']}"))
    history = Path(notes['vault_history_dir'])
    if history.is_dir():
        if unit_mentions(history / 'config', f"worktree = {site['notes_vault_dir']}"):
            plan.directories.append((str(history), 'root-only vault history'))
        else:
            plan.refused.append((str(history), 'is not this site\'s vault history'))
    for name in ('zelab-vault-history.timer', 'zelab-vault-history.service'):
        if (SYSTEMD / name).exists():
            if unit_mentions(SYSTEMD / name, 'Zelab vault history'):
                plan.units.append((name, 'Zelab vault history'))
            else:
                plan.refused.append((str(SYSTEMD / name), 'unit content does not match'))
    vault = Path(site['notes_vault_dir'])
    if any(path == str(vault) for path, _ in plan.directories):
        files = count_files(vault)
        if files:
            plan.notes.append(f'{files} file(s) in the notes vault')


def discover_obsidian(plan, obsidian_config):
    """Obsidian container: Compose labels are the ownership proof."""
    found = command('docker', 'container', 'inspect', 'zelab-obsidian', check=False)
    if found.returncode == 0:
        labels = (json.loads(found.stdout)[0].get('Config') or {}).get('Labels') or {}
        if labels.get('com.docker.compose.project') == 'zelab-notes':
            plan.containers.append('zelab-obsidian')
            image = json.loads(found.stdout)[0].get('Image')
            if image:
                plan.images.append(image)
        else:
            plan.refused.append(('container zelab-obsidian', 'not created by the zelab-notes Compose project'))
    found = command('docker', 'network', 'inspect', 'zelab-notes_default', check=False)
    if found.returncode == 0 and (json.loads(found.stdout)[0].get('Labels') or {}).get('com.docker.compose.project') == 'zelab-notes':
        plan.networks.append('zelab-notes_default')
    if Path(obsidian_config).is_dir():
        compose = Path(obsidian_config) / 'compose.yaml'
        if unit_mentions(compose, 'container_name: zelab-obsidian') or not any(Path(obsidian_config).iterdir()):
            plan.directories.append((obsidian_config, 'Obsidian Compose inputs'))
        else:
            plan.refused.append((obsidian_config, 'does not define the zelab-obsidian container'))


def discover_routes(plan, site):
    """Private HTTPS routes: only ports proxying to this site's backends."""
    if site.get('ui_tailscale_enabled'):
        status = command('tailscale', 'serve', 'status', '--json', check=False)
        config = shell.load_json(status.stdout) if status.returncode == 0 else {}
        backends = {f"http://127.0.0.1:{site[name]}" for name in
                    ('guest_dashboard_port', 'paperclip_port', 'obsidian_backend_port')}
        plan.routes = owned_tail_ports(config, site['ui_tailscale_hostname'], backends)


def discover_shared_tools(plan, site):
    """Host-wide installed software: shared only with other Zelab guests on this host."""
    others = other_zelab_guests(site)
    if others:
        plan.refused.append(('shared Zelab tools and download cache', 'also used by ' + ', '.join(others)))
    else:
        for path in SHARED_TOOLS:
            if Path(path).exists():
                plan.directories.append((path, 'Zelab-installed software or download cache'))
        for link in ('/usr/local/bin/zelab-codex', '/usr/local/bin/zelab-claude', '/usr/local/bin/node',
                     '/usr/local/bin/codex', '/usr/local/bin/claude'):
            target = os.readlink(link) if Path(link).is_symlink() else ''
            if target.startswith(('/opt/zelab-tools/', '/usr/local/bin/zelab-')):
                plan.files.append((link, f'link to {target}'))


def discover_private(plan, site):
    """Runtime VM resource receipts and generated private files."""
    private = Path(site['private_dir'])
    try:
        for path in InstallationResources(site).owned_files():
            plan.files.append((str(path), 'runtime VM resource receipt matches this site and override checksum'))
    except (OSError, ValueError, RuntimeError) as error:
        plan.refused.append(('temporary VM installation resources', str(error)))
    plan.private_files = [str(private / name) for name in GENERATED_PRIVATE if (private / name).exists()]
    keys = private / 'projects'
    if keys.is_dir() and not keys.is_symlink():
        plan.private_files += [str(path) for path in sorted(keys.iterdir()) if path.name.endswith(('.key', '.key.pub'))]


def print_plan(plan, site):
    heading('Full clean plan', 'yellow')
    print(f'  Site: {paint(plan.site_id, "bold")}')
    if plan.empty():
        print('\n  Nothing installed for this site; `make re` would run a clean installation.')
    if plan.notes:
        heading('Data that will be deleted', 'red')
        for note in plan.notes:
            print(f'  {mark("WARN")} {note}')
    if not plan.empty():
        heading('Back up before deleting', 'yellow')
        print('  Keep a verified encrypted copy of the installed site:')
        print('    ' + paint('rtk proxy make backup', 'yellow', 'bold'))
        print(f'  Backup directory: {site["backup_dir"]}')
        print('  To cancel this cleanup, press Ctrl-C before confirming.')
        heading('Resources to stop and remove', 'red')
        print('  Ownership evidence is shown in brackets.')
        for unit, evidence in plan.units:
            print(f'  unit       {unit}  [{evidence}]')
        for unit in plan.transient_units:
            print(f'  unit       {unit}  [QEMU disk belongs to this checkout\'s qualification artifacts]')
        for unit in plan.user_units:
            print(f'  user unit  {unit}  [embedded PostgreSQL runtime and data belong to this checkout\'s qualification]')
        for name in plan.containers:
            print(f'  container  {name}  [Compose project zelab-notes]')
        for name in plan.networks:
            print(f'  network    {name}  [Compose project zelab-notes]')
        for image in plan.images:
            print(f'  image      {image[:19]}  [used by zelab-obsidian]')
        for port in plan.routes:
            print(f'  serve      {site["ui_tailscale_hostname"]}:{port}  [proxies to this site]')
        for table in plan.nft_tables:
            print(f'  firewall   table {table}  [Zelab database boundary only]')
        for path, evidence in plan.files:
            print(f'  file       {path}  [{evidence}]')
        for path, evidence in plan.directories:
            print(f'  directory  {path}  [{evidence}]')
        for user, evidence in plan.accounts:
            print(f'  account    {user}  [{evidence}]')
        for name in plan.groups:
            print(f'  group      {name}  [Zelab notes access]')
        if plan.private_files:
            print(f'  secrets    {len(plan.private_files)} generated file(s) in {site["private_dir"]} (recreated on install)')
    if plan.refused:
        heading('Left in place: ownership not proven', 'yellow')
        print('  Fix or move these by hand if they block installation:')
        for resource, reason in plan.refused:
            print(f'  {resource}: {reason}')
    heading('Repository choices to reset', 'yellow')
    print('  Cleared from the local, untracked site file after a successful full reset:')
    for item in ('Saved worker harness and model selections',
                 'VM sizing, resource limits, install boost and host reserve preferences',
                 'Optional app choices, profile counts/audiences and Discord IDs'):
        print(f'  - {item}')
    from maintenance import generated_paths
    heading('Generated checkout artifacts to remove', 'yellow')
    for path in generated_paths(ROOT):
        print(f'  {path.relative_to(ROOT)}  [known generated path, no tracked files or mounts]')
    heading('Always kept', 'green')
    for item in ('Repository source files', 'Host connection settings and installation locations',
                 '.env external path selectors', 'Your personal harness logins',
                 f'Backups: {site["backup_dir"]}', f'Recovery key: {site["private_dir"]}/backup-passphrase',
                 'Private logs, the operator sudo grant and root RTK',
                 'SSH and Tailscale enrollment; other Serve routes',
                 'Discord applications in your Discord account',
                 'All unrelated services and containers'):
        print(f'  - {item}')
    heading('After reinstall')
    print('  Enter bot tokens through the native Hermes UI; the deleted guest held their working copies.')
    heading('Cleanup commands')
    print('  Remove this installation:')
    print('    ' + paint(f'rtk proxy make fclean CONFIRM={plan.site_id}', 'bold'))
    print('  Reset and reinstall:')
    print('    ' + paint(f'rtk proxy make re CONFIRM={plan.site_id}', 'bold'))


def unrelated_snapshot(plan, site):
    listing = command('docker', 'ps', '-a', '--format', '{{.ID}} {{.Names}} {{.State}}', check=False).stdout
    routes = command('tailscale', 'serve', 'status', '--json', check=False).stdout if site.get('ui_tailscale_enabled') else ''
    serve = shell.load_json(routes)
    ours = {f"{site.get('ui_tailscale_hostname')}:{port}" for port in plan.routes}
    other_routes = sorted(endpoint for endpoint in (serve.get('Web') or {}) if endpoint not in ours)
    owned_users = {user for user, _ in plan.accounts}
    return {
        'containers': sorted(line for line in listing.splitlines() if line.split()[1:2] != ['zelab-obsidian']),
        'serve_routes': other_routes,
        'accounts': sorted(entry.pw_name for entry in pwd.getpwall() if entry.pw_name not in owned_users),
    }


def stop_owned_processes(uids, timeout=30):
    def remaining():
        found = []
        for proc in Path('/proc').glob('[0-9]*'):
            try:
                if proc.stat().st_uid in uids:
                    found.append(int(proc.name))
            except (FileNotFoundError, ProcessLookupError):
                pass
        return found
    for signal_name in ('TERM', 'KILL'):
        for pid in remaining():
            command('kill', f'-{signal_name}', pid, check=False)
        deadline = time.monotonic() + timeout
        while remaining() and time.monotonic() < deadline:
            time.sleep(1)
        if not remaining():
            return
    raise RuntimeError('Processes of Zelab service accounts did not stop; nothing further was deleted')


def reset(plan, site):
    with maintenance_lock(), recover_on_interrupt():
        problems = [(path, reason) for path in plan.artifact_paths
                    if (reason := safe_to_delete(path, plan.protected, mountpoints()))]
        if problems:
            raise RuntimeError('Refusing artifact deletion: ' + '; '.join(f'{p} ({r})' for p, r in problems))
        if plan.transient_units:
            uid = pwd.getpwnam(site['operator_user']).pw_uid
            current = artifacts.qualification_units(plan.artifact_paths, uid)
            for unit in plan.transient_units:
                if unit in current and shell.output(['systemctl', 'show', unit, '-p', 'Transient', '--value']) == 'yes\n':
                    command('systemctl', 'stop', unit)
        if plan.user_units:
            uid = pwd.getpwnam(site['operator_user']).pw_uid
            current = artifacts.qualification_user_units(plan.artifact_paths, uid)
            for unit in plan.user_units:
                if unit in current and shell.output(artifacts.user_systemctl(site['operator_user'], 'show', unit,
                                                                             '-p', 'Transient', '--value')) == 'yes\n':
                    command(*artifacts.user_systemctl(site['operator_user'], 'stop', unit))
        artifacts.require_unused(plan.artifact_paths)
        before = unrelated_snapshot(plan, site)
        heading('1/3  Stop Zelab services', 'yellow')
        print('  Stopping Zelab services ...', flush=True)
        for name in plan.containers:
            command('docker', 'stop', '--time', '30', name)
            command('docker', 'rm', name)
        services = [unit for unit, _ in plan.units if unit.endswith('.service')]
        ordered = sorted(services, key=lambda u: ['zelab-paperclip.service', site['vm_service'] + '.service',
                                                  'zelab-notes-sshd.service', 'zelab-paperclip-db.service'].index(u)
                         if u in ['zelab-paperclip.service', site['vm_service'] + '.service',
                                  'zelab-notes-sshd.service', 'zelab-paperclip-db.service'] else 9)
        for unit in ordered + [unit for unit, _ in plan.units if unit.endswith('.mount')]:
            command('systemctl', 'disable', '--now', unit, check=False)
        mounts = mountpoints()
        uids = {pwd.getpwnam(user).pw_uid for user, _ in plan.accounts}
        stop_owned_processes(uids)
        problems = [(path, reason) for path, _ in plan.directories
                    if (reason := safe_to_delete(path, plan.protected, mounts))]
        if problems:
            raise RuntimeError('Refusing deletion: ' + '; '.join(f'{p} ({r})' for p, r in problems))
        heading('2/3  Remove owned resources', 'red')
        print('  Removing owned routes, units, files and state ...', flush=True)
        for port in plan.routes:
            command('tailscale', 'serve', f'--https={port}', 'off')
        for table in plan.nft_tables:
            command('nft', 'delete', 'table', *table.split())
        for unit, _ in plan.units:
            (SYSTEMD / unit).unlink(missing_ok=True)
            shutil.rmtree(SYSTEMD / (unit + '.d'), ignore_errors=True)
        for path, _ in plan.files:
            Path(path).unlink(missing_ok=True)
        command('systemctl', 'daemon-reload')
        command('systemctl', 'reset-failed', check=False)
        if any(path.startswith('/etc/ssh/') for path, _ in plan.files):
            command('/usr/sbin/sshd', '-t')
            command('systemctl', 'try-reload-or-restart', 'ssh.service', check=False)
        for path, _ in plan.directories:
            shutil.rmtree(path)
        for path, _ in plan.directories:
            # Remove a now-empty parent the installer created (e.g. /var/lib/zelab-vm).
            parent = Path(path).parent
            if parent.name.startswith('zelab') and str(parent) not in map(str, plan.protected):
                try:
                    parent.rmdir()
                except OSError:
                    pass
        # Revoke by name first: a numeric entry left behind would pass to
        # whichever account reuses the uid.
        for path, user in plan.acl_revocations:
            command('setfacl', '-R', '-x', f'u:{user},d:u:{user}', path, check=False)
        for user, _ in plan.accounts:
            command('userdel', user)
        for name in plan.groups:
            command('groupdel', name, check=False)
        for name in plan.networks:
            command('docker', 'network', 'rm', name, check=False)
        for image in plan.images:
            command('docker', 'image', 'rm', image, check=False)
        for path in plan.private_files:
            Path(path).unlink(missing_ok=True)
        try:
            (Path(site['private_dir']) / 'projects').rmdir()
        except OSError:
            pass
        after = unrelated_snapshot(plan, site)
    changed, added = compare_unrelated(before, after)
    heading('3/3  Verify unrelated resources', 'red' if changed else 'green')
    print(f"  {mark('FAIL' if changed else 'PASS')} Unrelated containers: {len(before['containers'])}, other Serve routes: {len(before['serve_routes'])}, "
          f"other accounts: {len(before['accounts'])} — "
          + ('all still present and unchanged.' if not changed else 'CHANGED during reset: ' + ', '.join(changed) + ' (inspect them).'))
    if added:
        print('Created by something else during the reset (not touched): ' + ', '.join(added))
    return not changed


def compare_unrelated(before, after):
    """A reset only stops and removes, so only missing or altered prior items count."""
    changed = [kind for kind in before if set(before[kind]) - set(after[kind])]
    added = [f'{len(set(after[kind]) - set(before[kind]))} {kind}' for kind in before if set(after[kind]) - set(before[kind])]
    return changed, added


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'reset'])
    parser.add_argument('--confirm', default='')
    parser.add_argument('--interactive', action='store_true')
    parser.add_argument('--site-config', type=Path, default=ROOT / 'config/site.yaml')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise SystemExit('fresh needs administrator access; run it through make')
    os.umask(0o077)
    site = read_inputs(args.site_config, args.env_file)
    plan = discover(site)
    if args.action == 'plan':
        print_plan(plan, site)
        return 0
    if args.interactive and not args.confirm and sys.stdin.isatty() and not plan.empty():
        print_plan(plan, site)
        heading('Confirm deletion', 'red')
        args.confirm = input(f'  Type {paint(plan.site_id, "red", "bold")} to delete the listed Zelab data: ').strip()
    if args.interactive and plan.empty() and not args.confirm and not plan.refused:
        print('Nothing installed for this site; local generated files may be cleaned.')
        return 0
    if args.confirm != plan.site_id:
        print_plan(plan, site)
        print(f'\nNothing changed: confirmation {args.confirm or "(missing)"} does not match site {plan.site_id}.')
        return 2
    if plan.empty():
        print('Nothing installed for this site; continuing with a clean installation.')
        return 0
    return 0 if reset(plan, site) else 3


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as error:
        raise SystemExit(f'Full clean stopped: {error}\nNext: fix the cause, then rerun rtk proxy make fclean') from None
