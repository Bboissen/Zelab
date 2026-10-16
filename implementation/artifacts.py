"""Identify disposable qualification artifacts belonging to this checkout.

New scratch tools leave a repository/path receipt. Legacy qualification roots
need their report and an owned script naming this checkout; only their known
build/download/runtime/VM children are disposable. Reports and recovery bundles
outside those children remain. A name prefix alone never proves ownership.
"""

import json
import os
from pathlib import Path
import pwd
import re
import stat

from runtime import shell

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = '.zelab-artifact.json'
LEGACY_OUTPUTS = ('build-hermes', 'dl', 'dl-debian', 'dl-hermes', 'dl-paperclip',
                  'src-hermes', 'src-paperclip', 'pc-runtime', 'vm')


def owned(path, uid, *, directory=False, private_parent=False):
    try:
        info = path.lstat()
        return (path.resolve() == path and info.st_uid == uid and (private_parent or not info.st_mode & 0o022)
                and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)))
    except OSError:
        return False


def record(path, repository=ROOT):
    """Mark a newly created, entirely disposable scratch directory, not user data."""
    path, repository = Path(path).resolve(), Path(repository).resolve()
    if not owned(path, os.geteuid(), directory=True):
        raise ValueError('Scratch artifacts need an owned, dedicated directory')
    with (path / RECEIPT).open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump({'repository': str(repository), 'path': str(path)}, stream)


def receipt_matches(path, repository, uid):
    marker = path / RECEIPT
    if not owned(marker, uid):
        return False
    try:
        return json.loads(marker.read_text()) == {'repository': str(repository), 'path': str(path)}
    except (OSError, ValueError):
        return False


def discover(plan, site, repository=ROOT, roots=None):
    operator = pwd.getpwnam(site['operator_user'])
    repository = Path(repository).resolve()
    roots = roots if roots is not None else (Path(operator.pw_dir) / '.local/state', Path('/tmp'), Path('/var/tmp'))
    for base in roots:
        if not base.is_dir() or base.resolve() != base:
            continue
        for path in sorted(base.glob('zelab-*')):
            if not owned(path, operator.pw_uid, directory=True):
                continue
            if receipt_matches(path, repository, operator.pw_uid):
                plan.artifact_paths.append(path)
                plan.directories.append((str(path), 'scratch receipt names this checkout and directory'))
                continue
            if not (path.match('zelab-v*-qual') and not path.stat().st_mode & 0o077
                    and owned(path / 'QUALIFICATION.md', operator.pw_uid, private_parent=True)
                    and owned(path / 'scripts', operator.pw_uid, directory=True, private_parent=True)):
                continue
            # This is migration evidence for the pre-receipt qualification layout.
            # Read scripts only, never passwords, profile state or diagnostic logs.
            linked = False
            for script in (path / 'scripts').iterdir():
                if script.suffix in ('.py', '.sh') and owned(script, operator.pw_uid, private_parent=True):
                    try:
                        reference = re.escape(str(repository)) + r'''(?=[/'"\s]|$)'''
                        if script.stat().st_size <= 1024 * 1024 and re.search(reference, script.read_text()):
                            linked = True
                            break
                    except (OSError, UnicodeError):
                        continue
            if not linked:
                continue
            for name in LEGACY_OUTPUTS:
                output = path / name
                if owned(output, operator.pw_uid, directory=True, private_parent=True):
                    plan.artifact_paths.append(output)
                    plan.directories.append((str(output), 'qualification report and script reference this checkout'))
            plan.notes.append(f'Disposable builds and VM in {path}; qualification reports, logs and Git bundles stay')
    for unit in qualification_units(plan.artifact_paths, operator.pw_uid):
        if shell.output(['systemctl', 'show', unit, '-p', 'Transient', '--value']) == 'yes\n':
            plan.transient_units.append(unit)
    for unit in qualification_user_units(plan.artifact_paths, operator.pw_uid):
        if shell.output(user_systemctl(site['operator_user'], 'show', unit, '-p', 'Transient', '--value')) == 'yes\n':
            plan.user_units.append(unit)


def user_systemctl(operator, *args):
    return ['systemctl', '--user', f'--machine={operator}@.host', *args]


def qualification_user_units(paths, uid, proc=Path('/proc')):
    """Only scratch Paperclip units with their own embedded PostgreSQL runtime/data."""
    runtimes = [path for path in paths if path.name == 'pc-runtime']
    if not runtimes:
        return []
    units = set()
    prefix = f'0::/user.slice/user-{uid}.slice/user@{uid}.service/app.slice/'
    for process in proc.glob('[0-9]*'):
        try:
            if process.stat().st_uid != uid:
                continue
            executable, cwd = (process / 'exe').resolve(), (process / 'cwd').resolve()
            if not any(executable == runtime / 'node_modules/@embedded-postgres/linux-x64/native/bin/postgres'
                       and cwd.is_relative_to(runtime.parent / 'pc-home') for runtime in runtimes):
                continue
            cgroup = (process / 'cgroup').read_text().strip()
            unit = cgroup.removeprefix(prefix)
            if (cgroup.startswith(prefix) and '/' not in unit and unit.startswith('zelab-')
                    and '-qual-' in unit and unit.endswith('.service')):
                units.add(unit)
        except OSError:
            continue
    return sorted(units)


def qualification_units(paths, uid, proc=Path('/proc')):
    """A transient unit is ours only when its QEMU uses an identified VM disk."""
    disks = {str(path / 'disk.qcow2') for path in paths if path.name == 'vm'}
    if not disks:
        return []
    units = set()
    for process in proc.glob('[0-9]*'):
        try:
            if os.geteuid() != 0 and process.stat().st_uid != os.geteuid():
                continue
            if process.stat().st_uid != uid or not (process / 'comm').read_text().startswith('qemu-system'):
                continue
            args = (process / 'cmdline').read_bytes().decode().split('\0')
            if not any(arg.split(',', 1)[0].removeprefix('file=') in disks
                       for index, arg in enumerate(args) if index and args[index - 1] == '-drive'):
                continue
            cgroup = (process / 'cgroup').read_text().strip()
            prefix = '0::/system.slice/'
            unit = cgroup.removeprefix(prefix)
            if (cgroup.startswith(prefix) and '/' not in unit and unit.startswith('zelab-')
                    and '-qual-' in unit and unit.endswith('.service')):
                units.add(unit)
        except (OSError, UnicodeError):
            continue
    return sorted(units)


def require_unused(paths, proc=Path('/proc')):
    """Refuse active scratch outputs after their identified VM units stop."""
    paths = [Path(path) for path in paths]
    if not paths:
        return
    for process in proc.glob('[0-9]*'):
        try:
            if os.geteuid() != 0 and process.stat().st_uid != os.geteuid():
                continue
            references = [(process / name).resolve() for name in ('cwd', 'exe')]
            references += [entry.resolve() for entry in (process / 'fd').iterdir()]
            args = (process / 'cmdline').read_bytes().decode(errors='replace').split('\0')
            references += [Path(arg) for arg in args if arg.startswith('/')]
            if any(reference == path or reference.is_relative_to(path) for reference in references for path in paths):
                raise RuntimeError('A qualification/build artifact is still in use; finish its work before fclean')
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            raise RuntimeError('Cannot inspect active artifact users; run fclean through its administrator entry point') from None
