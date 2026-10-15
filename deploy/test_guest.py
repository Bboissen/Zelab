"""Guest layout: VM unit, default size and live disk growth.

Templates are rendered with trim_blocks, as Ansible renders them: a block tag
at the end of a line removes that line break.
"""

import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest

import jinja2
import yaml

import vm_disk

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = yaml.safe_load((ROOT / 'config/defaults.yaml').read_text())


def render(template, **extra):
    """Render a role template the way Ansible does (trim_blocks, strict variables)."""
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True, trim_blocks=True)
    environment.filters['regex_replace'] = lambda value, pattern, replacement: __import__('re').sub(
        pattern, replacement, value)
    variables = {**DEFAULTS, 'vm_disk': '/var/lib/zelab-vm/data/disk.qcow2', **extra}
    return environment.from_string((ROOT / template).read_text()).render(**variables)


class VmUnit(unittest.TestCase):
    def test_unit_applies_the_declared_size(self):
        unit = render('deploy/roles/vm/templates/zelab-vm.service.j2')
        self.assertIn(f"-smp {DEFAULTS['vm_cpu']} -m {DEFAULTS['vm_memory_mb']} ", unit)
        self.assertIn(f"CPUQuota={DEFAULTS['vm_cpu_quota']}", unit)
        self.assertIn(f"MemoryMax={DEFAULTS['vm_memory_max']}", unit)
        # One directive per line, as Ansible renders it.
        self.assertEqual([line.split('=')[0] for line in unit.splitlines() if line.startswith('Exec')],
                         ['ExecStart', 'ExecStop'])

    def test_only_loopback_forwards(self):
        unit = render('deploy/roles/vm/templates/zelab-vm.service.j2')
        forwards = [part.split()[0] for part in unit.split(',') if part.startswith('hostfwd=')]
        self.assertEqual(forwards, ['hostfwd=tcp:127.0.0.1:12921-:22', 'hostfwd=tcp:127.0.0.1:18640-:8640',
                                    'hostfwd=tcp:127.0.0.1:18642-:8642'])


class FakeQmp:
    """Unix-socket QMP server holding one virtio disk."""

    def __init__(self, path, size):
        self.size, self.resizes = size, []
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(path)
        self.server.listen()
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            with connection, connection.makefile('rwb') as stream:
                stream.write(b'{"QMP": {}}\n')
                stream.flush()
                for line in stream:
                    request = json.loads(line)
                    if request['execute'] == 'query-block':
                        reply = [{'device': 'virtio0', 'inserted': {'file': '/data/disk.qcow2',
                                                                     'image': {'virtual-size': self.size}}}]
                    elif request['execute'] == 'block_resize':
                        self.resizes.append(request['arguments'])
                        self.size = request['arguments']['size']
                        reply = {}
                    else:
                        reply = {}
                    stream.write((json.dumps({'return': reply}) + '\n').encode())
                    stream.flush()

    def close(self):
        self.server.close()


class DiskGrowth(unittest.TestCase):
    def test_plan_never_shrinks(self):
        gib = vm_disk.GIB
        self.assertEqual(vm_disk.plan(16 * gib, 20), 20 * gib)
        self.assertIsNone(vm_disk.plan(20 * gib, 20))
        self.assertIsNone(vm_disk.plan(22 * gib, 20))

    def test_online_resize_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            qmp = FakeQmp(f'{directory}/qmp.sock', 20 * vm_disk.GIB)
            try:
                first = vm_disk.grow('/unused', f'{directory}/qmp.sock', 22)
                second = vm_disk.grow('/unused', f'{directory}/qmp.sock', 22)
            finally:
                qmp.close()
        self.assertEqual(first, {'changed': True, 'online': True, 'from_gib': 20.0, 'to_gib': 22.0})
        self.assertEqual(second['changed'], False)
        self.assertEqual(qmp.resizes, [{'device': 'virtio0', 'size': 22 * vm_disk.GIB}])

    @unittest.skipUnless(shutil.which('qemu-img'), 'qemu-img not installed')
    def test_offline_resize_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            disk = f'{directory}/disk.qcow2'
            subprocess.run(['qemu-img', 'create', '-q', '-f', 'qcow2', disk, '1G'], check=True)
            first = vm_disk.grow(disk, f'{directory}/missing.sock', 2)
            second = vm_disk.grow(disk, f'{directory}/missing.sock', 2)
        self.assertEqual((first['changed'], first['online'], first['to_gib']), (True, False, 2.0))
        self.assertFalse(second['changed'])


if __name__ == '__main__':
    unittest.main()
