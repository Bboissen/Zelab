"""Small native process helpers shared by backup, recovery and lifecycle commands."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import pwd
import signal
import stat
import sys

DEPLOY = str(Path(__file__).resolve().parents[1] / 'deploy')
if DEPLOY not in sys.path:
    sys.path.insert(0, DEPLOY)
import shell  # noqa: E402


MAINTENANCE_LOCK = Path('/run/zelab-backup.lock')


def run(*args):
    result = shell.quiet(args)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed; private command output was suppressed')
    return result.stdout


def service_active(name):
    state = run('systemctl', 'show', name, '-p', 'ActiveState', '--value').strip()
    if state not in {b'active', b'inactive', b'failed'}:
        raise RuntimeError('A selected service is missing or changing state; retry when stable')
    return state == b'active'


def container_running(name):
    return run('docker', 'inspect', name, '--format', '{{.State.Running}}').strip() == b'true'


def require_idle_workers(site):
    # Team tier accounts (zelab-team-<tier>, earlier zelab-p-<slug>) run Paperclip's host agents.
    uids = {entry.pw_uid for entry in pwd.getpwall() if entry.pw_name.startswith(('zelab-team-', 'zelab-p-'))}
    for path in Path('/proc').glob('[0-9]*'):
        try:
            if path.stat().st_uid in uids:
                raise RuntimeError('A host worker is active; retry maintenance after its task finishes')
        except (FileNotFoundError, ProcessLookupError):
            pass


def private_file(path, label):
    """Validate a secret-bearing file without including its contents in errors."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError(f'{label} must be a private regular file, mode 0600 or stricter')
    return path


def access_acl(path):
    """Return (tag, perm, id) entries of a POSIX access ACL, or None without one."""
    try:
        raw = os.getxattr(path, 'system.posix_acl_access')
    except OSError:
        return None
    # Linux xattr layout: 4-byte version 2 header, then 8-byte tag/perm/id entries.
    return [(int.from_bytes(raw[i:i + 2], 'little'), int.from_bytes(raw[i + 2:i + 4], 'little'),
             int.from_bytes(raw[i + 4:i + 8], 'little')) for i in range(4, len(raw), 8)]


def private_directory(path, operator_user=None):
    """Create or accept a directory readable only by root and the operator.

    An ACL entry for the operator account shows up in the group mode bits, so a
    folder the operator may browse is still private."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    trusted = {0} | ({pwd.getpwnam(operator_user).pw_uid} if operator_user else set())
    acl = access_acl(path)
    if acl is None:
        private = not info.st_mode & 0o077
    else:
        # USER_OBJ is the owner; MASK only limits other entries; anything else
        # (named groups, the owning group, other) must grant nothing.
        private = all(perm == 0 or tag == 0x10 or (tag == 0x01 and info.st_uid in trusted)
                      or (tag == 0x02 and ident in trusted) for tag, perm, ident in acl)
    if not private or info.st_uid not in trusted:
        raise ValueError(f'{path} must be private to root and the operator: run '
                         f'`sudo chmod 700 {path}` (and `sudo setfacl -b {path}` if other ACL entries '
                         'exist) or set backup_dir in config/site.yaml to another folder')
    return path


def recovery_key(path):
    private_file(path, 'Recovery key')
    if not path.read_bytes().strip():
        raise ValueError('Recovery key must not be empty')
    return path


def unique_paths(paths):
    """Keep each archived subtree once, including explicitly selected files."""
    selected = []
    for path in sorted(set(paths), key=lambda p: (len(p.parts), str(p))):
        if not any(parent == path or parent in path.parents for parent in selected):
            selected.append(path)
    return selected


def lock_file(path):
    # flock works with a read-only descriptor on Linux. A root-created readable
    # file lets the unprivileged controller coordinate without running as root.
    descriptor = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_mode & 0o022 or info.st_uid not in {0, os.geteuid()}):
            raise RuntimeError('Maintenance lock must be an owned regular file without writable aliases')
        if info.st_uid == os.geteuid():
            os.fchmod(descriptor, 0o644)
        return os.fdopen(descriptor, 'r')
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def maintenance_lock(path=MAINTENANCE_LOCK):
    """Serialize deployment, start/stop, backup and migration preservation."""
    with lock_file(path) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another Zelab backup or lifecycle operation is active') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def recover_on_interrupt():
    """Turn normal termination into an exception so writer recovery still runs."""
    previous = {}

    def interrupted(signum, _frame):
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        raise RuntimeError('Maintenance interrupted; previously running services were scheduled for recovery')

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[sig] = signal.signal(sig, interrupted)
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


@contextmanager
def stopped_writers(site, vm, *, resume=True):
    """Quiesce native writers and restore the exact prior running state on errors."""
    require_idle_workers(site)
    # Restart exports before their consumers; stop the scheduler first so no new
    # task can appear between the idle check and the snapshot.
    services = ['zelab-notes-sshd.service', 'zelab-paperclip.service', vm]
    active_services = [name for name in services if service_active(name)]
    active_containers = [name for name in ['zelab-obsidian'] if container_running(name)]
    completed = False
    try:
        for name in ['zelab-paperclip.service', vm, 'zelab-notes-sshd.service']:
            run('systemctl', 'stop', name)
        for name in active_containers:
            run('docker', 'stop', '--time', '30', name)
        require_idle_workers(site)
        if any(service_active(name) for name in services) or any(container_running(name) for name in active_containers):
            raise RuntimeError('A writer did not stop')
        if (Path(site['paperclip_home']) / 'instances/default/db/postmaster.pid').exists():
            raise RuntimeError('Embedded PostgreSQL did not shut down')
        yield active_services, active_containers
        completed = True
    finally:
        errors = []
        for name in active_services if resume or not completed else []:
            try:
                run('systemctl', 'start', name)
            except Exception:
                errors.append(name)
        for name in active_containers if resume or not completed else []:
            try:
                run('docker', 'start', name)
            except Exception:
                errors.append(name)
        if errors:
            raise RuntimeError('Service recovery needs attention: ' + ', '.join(errors))


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Initialize the shared maintenance lock.')
    parser.add_argument('--prepare-lock', action='store_true', required=True)
    parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise RuntimeError('Preparing the maintenance lock requires administrator access')
        with lock_file(MAINTENANCE_LOCK):
            pass
    except Exception:
        raise SystemExit('Cannot prepare the maintenance lock; check administrator access and lock ownership.') from None
