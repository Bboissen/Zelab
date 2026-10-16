#!/usr/bin/env python3
"""Grow the guest disk to vm_disk_gib: QMP block_resize while the VM runs,
qemu-img resize while it is stopped. Never shrinks. Prints one JSON line.

The guest partition and filesystem are grown afterwards inside the guest
(growpart, resize2fs), both online.
"""

import argparse
import json
import socket
import subprocess

GIB = 2 ** 30


def plan(current_bytes, wanted_gib):
    """Target size in bytes, or None when the disk is already at least that big."""
    wanted = int(wanted_gib) * GIB
    return wanted if wanted > current_bytes else None


class Qmp:
    def __init__(self, path, timeout=30):
        self.connection = socket.socket(socket.AF_UNIX)
        self.connection.settimeout(timeout)
        self.connection.connect(path)
        self.stream = self.connection.makefile('rwb')
        self.stream.readline()
        self.call('qmp_capabilities')

    def call(self, command, arguments=None):
        request = {'execute': command, **({'arguments': arguments} if arguments else {})}
        self.stream.write((json.dumps(request) + '\n').encode())
        self.stream.flush()
        while True:
            reply = json.loads(self.stream.readline())
            if 'error' in reply:
                raise RuntimeError(f"QEMU refused {command}: {reply['error'].get('class')}")
            if 'return' in reply:
                return reply['return']

    def close(self):
        self.connection.close()


def disk_device(blocks, disk):
    """QMP device name of the drive backed by `disk`."""
    for block in blocks:
        inserted = block.get('inserted') or {}
        image = inserted.get('image') or {}
        if disk in (inserted.get('file'), image.get('filename')):
            return block['device'], image.get('virtual-size', 0)
    raise RuntimeError('The running VM has no drive for the selected disk')


def grow(disk, qmp_path, wanted_gib, guest_path='/data/disk.qcow2'):
    try:
        qmp = Qmp(qmp_path)
    except OSError:
        qmp = None
    if qmp:
        try:
            device, current = disk_device(qmp.call('query-block'), guest_path)
            target = plan(current, wanted_gib)
            if target:
                qmp.call('block_resize', {'device': device, 'size': target})
            return {'changed': bool(target), 'online': True, 'from_gib': current / GIB,
                    'to_gib': (target or current) / GIB}
        finally:
            qmp.close()
    info = json.loads(subprocess.run(['qemu-img', 'info', '--output=json', disk], check=True,
                                     capture_output=True, text=True).stdout)
    target = plan(info['virtual-size'], wanted_gib)
    if target:
        subprocess.run(['qemu-img', 'resize', disk, str(target)], check=True, capture_output=True)
    return {'changed': bool(target), 'online': False, 'from_gib': info['virtual-size'] / GIB,
            'to_gib': (target or info['virtual-size']) / GIB}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--disk', required=True, help='host path of the guest disk')
    parser.add_argument('--qmp', required=True, help="the VM's QMP socket")
    parser.add_argument('--gib', type=int, required=True)
    parser.add_argument('--guest-path', default='/data/disk.qcow2', help='disk path as QEMU sees it')
    args = parser.parse_args(argv)
    print(json.dumps(grow(args.disk, args.qmp, args.gib, args.guest_path)))


if __name__ == '__main__':
    main()
