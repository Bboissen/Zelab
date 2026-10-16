"""Fail only when a component cannot fit; report reduced host headroom.

usage: check_headroom.py PATH DISK_GIB RAM_MIB DISK_RESERVE_GIB RAM_RESERVE_GIB WHAT DISK_HINT RAM_HINT

PATH is measured on its nearest existing parent; "docker" measures Docker's
own data root. Warnings for the preferred host reserves are printed, not fatal.
"""

import shutil
import subprocess
import sys
from pathlib import Path


def free_gib(location):
    if location == 'docker':
        found = subprocess.run(['docker', 'info', '--format', '{{.DockerRootDir}}'],
                               capture_output=True, text=True).stdout.strip()
        location = found or '/var/lib/docker'
    path = Path(location)
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free / 2**30


def available_mib():
    memory = {line.split(':')[0]: int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()}
    return memory['MemAvailable'] / 1024


def main(argv):
    location, disk, ram, disk_reserve, ram_reserve = argv[0], *map(int, argv[1:5])
    what, disk_hint, ram_hint = argv[5:8]
    free, available = free_gib(location), available_mib()
    if free < disk:
        raise SystemExit(f'{free:.0f} GiB free; {what} needs {disk} GiB. {disk_hint}')
    if available < ram:
        raise SystemExit(f'{available:.0f} MiB RAM available; {what} needs {ram} MiB. {ram_hint}')
    warnings = []
    if free < disk + disk_reserve:
        warnings.append(f'disk headroom below host_disk_reserve_gib ({free:.0f} GiB free)')
    if available < ram + ram_reserve * 1024:
        warnings.append(f'RAM headroom below host_memory_reserve_gib ({available / 1024:.1f} GiB available)')
    print('; '.join(warnings))


if __name__ == '__main__':
    main(sys.argv[1:])
