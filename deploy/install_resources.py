#!/usr/bin/env python3
"""Temporary resources for a fresh VM's installation, using native systemd.

The permanent VM unit is never modified. A runtime drop-in and an ownership
receipt live in /run, disappear on host reboot, and are removed by run.py even
when provisioning fails. An already-running VM is left at its normal size.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


MIB = 2 ** 20
GIB = 2 ** 30


def resource_fraction(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('vm_install_resource_fraction must be a number from 0 to 1 (0 disables the boost)')
    return value


def memory_bytes(value):
    found = re.fullmatch(r'(\d+)([KMGT]?)', str(value))
    if not found:
        raise ValueError('VM MemoryMax must be a finite size in bytes, K, M, G or T')
    return int(found[1]) * 1024 ** ('', 'K', 'M', 'G', 'T').index(found[2])


def plan_resources(site, available_memory, idle_cpus):
    """Use a fraction of idle capacity, preserving host RAM and QEMU overhead."""
    fraction = resource_fraction(site.get('vm_install_resource_fraction', 0.7))
    cpu, memory = int(site['vm_cpu']), int(site['vm_memory_mb'])
    normal_max = memory_bytes(site['vm_memory_max'])
    quota = float(str(site['vm_cpu_quota']).removesuffix('%'))
    if fraction:
        spare_cpus = math.floor(idle_cpus * fraction)
        cpu = max(cpu, spare_cpus)
        budget = max(0, available_memory - int(site.get('host_memory_reserve_gib', 8)) * GIB)
        # At least 512 MiB or 10% of guest RAM for QEMU, outside guest RAM.
        allowance = min(fraction * available_memory, budget / 1.1, budget - 512 * MIB)
        memory = max(memory, math.floor(max(0, allowance) / (256 * MIB)) * 256)
        if memory > int(site['vm_memory_mb']):
            normal_max = max(normal_max, (memory + max(512, math.ceil(memory / 10))) * MIB)
        quota = max(quota, spare_cpus * 100)
    return {'cpu': cpu, 'memory_mb': memory, 'cpu_quota': f'{quota:g}%',
            'memory_max': f'{math.ceil(normal_max / MIB)}M'}


def sample_host():
    allowed = os.sched_getaffinity(0)

    def counters():
        total = idle = 0
        for line in Path('/proc/stat').read_text().splitlines():
            fields = line.split()
            if not re.fullmatch(r'cpu\d+', fields[0]) or int(fields[0][3:]) not in allowed:
                continue
            values = list(map(int, fields[1:9]))
            total += sum(values)
            idle += values[3]  # I/O wait is busy capacity, not spare compute.
        return total, idle

    before = counters()
    time.sleep(1)
    after = counters()
    delta = after[0] - before[0]
    idle_cpus = len(allowed) * (after[1] - before[1]) / delta if delta > 0 else 0
    memory = {line.split(':')[0]: int(line.split()[1]) * 1024
              for line in Path('/proc/meminfo').read_text().splitlines()}
    return memory['MemAvailable'], idle_cpus


def command(*argv):
    result = subprocess.run([*map(str, argv)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'{argv[0]} {argv[1]} failed; see systemctl status for the selected VM')
    return result.stdout


def checked_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise RuntimeError('Installation resource files must be owned regular files without writable aliases')
    return path.read_text()


def checked_directory(path, mode):
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise RuntimeError('Installation resource directories must be owned and not writable by other users')


def atomic_write(path, text, mode):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class InstallationResources:
    def __init__(self, site, *, unit_directory=Path('/etc/systemd/system'),
                 runtime_directory=Path('/run/systemd/system'),
                 receipt_directory=Path('/run/zelab-install-resources'),
                 control_directory=Path('/run/systemd/system.control')):
        self.site = site
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', site['vm_service']):
            raise ValueError('Invalid VM service identity')
        self.unit = site['vm_service'] + '.service'
        self.unit_file = unit_directory / self.unit
        self.override = runtime_directory / (self.unit + '.d') / '90-zelab-install-resources.conf'
        self.receipt = receipt_directory / (self.unit + '.json')
        # Where `systemctl set-property --runtime` keeps a live CPU quota.
        self.startup_quota = control_directory / (self.unit + '.d') / '50-CPUQuota.conf'
        self.identity = {key: site[key] for key in ('vm_service', 'vm_data', 'vm_root', 'vm_user')}

    def active(self):
        return command('systemctl', 'show', self.unit, '-p', 'ActiveState', '--value').strip() == 'active'

    def owned_unit(self):
        text = checked_file(self.unit_file)
        required = ['Description=Zelab native Hermes guest', 'User=' + self.site['vm_user'],
                    'RootDirectory=' + self.site['vm_root'],
                    'BindPaths=' + self.site['vm_data'] + ':/data /dev/kvm']
        if any(line not in text.splitlines() for line in required):
            raise RuntimeError('The VM unit does not match the selected Zelab identity and data paths')
        starts = re.findall(r'^ExecStart=(.+)$', text, re.M)
        if len(starts) != 1 or not starts[0].startswith('/usr/bin/qemu-system-x86_64 '):
            raise RuntimeError('The selected VM must retain its native QEMU start command')
        return text, starts[0]

    def begin(self):
        if self.receipt.exists() or self.override.exists() or self.receipt.is_symlink() or self.override.is_symlink():
            raise RuntimeError('An unfinished VM resource boost exists; rerun make to restore it first')
        _, start = self.owned_unit()
        if not resource_fraction(self.site.get('vm_install_resource_fraction', 0.7)) or self.active():
            return {'changed': False, 'reason': 'Normal resources retained for an already-running VM or disabled boost'}
        resources = plan_resources(self.site, *sample_host())
        if (resources['cpu'] == self.site['vm_cpu']
                and resources['memory_mb'] == self.site['vm_memory_mb']
                and resources['cpu_quota'] == self.site['vm_cpu_quota']
                and memory_bytes(resources['memory_max']) == memory_bytes(self.site['vm_memory_max'])):
            return {'changed': False, 'reason': 'No spare capacity above normal VM limits'}
        for flag, value in [('smp', resources['cpu']), ('m', resources['memory_mb'])]:
            start, count = re.subn(rf'(?<!\S)-{flag} \d+(?=\s|$)', f'-{flag} {value}', start)
            if count != 1:
                raise RuntimeError('The native VM CPU and RAM arguments must be explicit integers')
        override = '[Service]\nExecStart=\nExecStart=' + start + '\nCPUQuota=' + resources['cpu_quota'] + '\nMemoryMax=' + resources['memory_max'] + '\n'
        checked_directory(self.receipt.parent, 0o700)
        checked_directory(self.override.parent, 0o755)
        receipt = {**self.identity, 'override_sha256': hashlib.sha256(override.encode()).hexdigest(),
                   'restart_pending': False}
        # Record ownership before writing the override; interruption is recoverable.
        atomic_write(self.receipt, json.dumps(receipt), 0o600)
        atomic_write(self.override, override, 0o644)
        command('systemctl', 'daemon-reload')
        return {'changed': True, **resources}

    def wait_for_guest(self, dashboard_was_ready):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', self.site['guest_ssh_port']), timeout=2) as connection:
                    connection.settimeout(2)
                    ssh_ready = connection.recv(256).startswith(b'SSH-')
            except OSError:
                ssh_ready = False
            if ssh_ready and (not dashboard_was_ready or self.dashboard_ready()):
                return
            time.sleep(1)
        raise RuntimeError('Normal VM limits are restored, but guest startup needs attention; rerun make')

    def start_on_full_cpus(self, dashboard_was_ready):
        """Let the restarted guest use whole vCPUs until it answers, then apply the
        normal quota live. Starting Hermes under a 50% quota takes about a minute.

        Only a quota this method set is touched: an existing runtime quota is kept.
        """
        full = f"{self.site['vm_cpu'] * 100}%"
        if (self.startup_quota.exists() or self.startup_quota.is_symlink()
                or int(full[:-1]) <= int(self.site['vm_cpu_quota'][:-1])):
            self.wait_for_guest(dashboard_was_ready)
            return
        command('systemctl', 'set-property', '--runtime', self.unit, 'CPUQuota=' + full)
        try:
            self.wait_for_guest(dashboard_was_ready)
        finally:
            command('systemctl', 'set-property', '--runtime', self.unit, 'CPUQuota=' + self.site['vm_cpu_quota'])
            # The live value is now the unit's own; drop the runtime copy so a
            # later change to the permanent unit is not masked until reboot.
            self.startup_quota.unlink(missing_ok=True)
            try:
                self.startup_quota.parent.rmdir()
            except OSError:
                pass
            command('systemctl', 'daemon-reload')

    def dashboard_ready(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.site['guest_dashboard_port']}/api/profiles", timeout=2):
                return False
        except urllib.error.HTTPError as error:
            return error.code == 401
        except OSError:
            return False

    def owned_files(self):
        """Read-only ownership proof, also used by the project reset preview."""
        override_exists = self.override.exists() or self.override.is_symlink()
        if not self.receipt.exists() and not self.receipt.is_symlink():
            if override_exists:
                raise RuntimeError('A VM resource override has no ownership receipt; inspect it before recovery')
            return []
        receipt = json.loads(checked_file(self.receipt))
        if any(receipt.get(key) != value for key, value in self.identity.items()):
            raise RuntimeError('The resource receipt belongs to another VM; recovery will not change it')
        if override_exists and hashlib.sha256(checked_file(self.override).encode()).hexdigest() != receipt.get('override_sha256'):
            raise RuntimeError('The installation resource override was changed; reconcile it before recovery')
        if self.unit_file.exists() or self.unit_file.is_symlink():
            self.owned_unit()
        return [self.receipt] + ([self.override] if override_exists else [])

    def restore(self):
        files = self.owned_files()
        if not files:
            return {'changed': False}
        receipt = json.loads(checked_file(self.receipt))
        override_exists = self.override in files
        if self.unit_file.exists():
            active = self.active()
        else:
            # A confirmed reset may have stopped/deleted the unit before an
            # interruption left these two artifacts. Remove them only if the
            # VM is stopped; there is no permanent unit to restore while live.
            state = subprocess.run(['systemctl', 'is-active', '--quiet', self.unit])
            if state.returncode not in (3, 4):
                raise RuntimeError('The normal VM unit is missing and its stopped state is unproven; restore the unit before recovery')
            active = False
        print('RESOURCE RESTORE START', flush=True)
        dashboard_was_ready = self.dashboard_ready() if active else False
        restart = self.unit_file.exists() and (receipt['restart_pending'] or (active and override_exists))
        if restart and not receipt['restart_pending']:
            receipt['restart_pending'] = True
            atomic_write(self.receipt, json.dumps(receipt), 0o600)
        if restart and active:
            # Stop under the boosted limit: reloading first applies the normal
            # MemoryMax to the larger live QEMU and the kernel OOM-kills it.
            command('systemctl', 'stop', self.unit)
        if override_exists:
            self.override.unlink()
        command('systemctl', 'daemon-reload')
        if restart:
            command('systemctl', 'start', self.unit)
            receipt['restart_pending'] = False
            atomic_write(self.receipt, json.dumps(receipt), 0o600)
            self.start_on_full_cpus(dashboard_was_ready)
        self.receipt.unlink()
        # Remove only empty directories created for this boost.
        for directory in (self.override.parent, self.receipt.parent):
            try:
                directory.rmdir()
            except OSError:
                pass
        result = {'changed': True, 'cpu': self.site['vm_cpu'], 'memory_mb': self.site['vm_memory_mb'],
                  'cpu_quota': self.site['vm_cpu_quota'], 'memory_max': self.site['vm_memory_max']}
        print('RESOURCE RESTORE DONE ' + json.dumps(result), flush=True)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['begin', 'restore', 'plan'])
    parser.add_argument('--site-json', type=Path)
    parser.add_argument('--site-config', type=Path, default=Path('config/site.yaml'))
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    args = parser.parse_args()
    try:
        if args.site_json:
            site = json.loads(args.site_json.read_text())
        else:
            # Imported only by the CLI, after run.py can import this module's
            # fraction validator. Keep YAML errors free of private input text.
            from run import read_inputs, yaml
            try:
                site = read_inputs(args.site_config, args.env_file)
            except yaml.YAMLError:
                raise ValueError('Invalid deployment inputs; check config types, paths and permissions') from None
        if args.action == 'plan':
            result = plan_resources(site, *sample_host())
        else:
            if os.geteuid() != 0:
                raise RuntimeError('Installation resource changes require the deployment administrator')
            result = getattr(InstallationResources(site), args.action)()
        print(json.dumps(result))
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print('Installation resources: ' + str(error).splitlines()[0])
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
