"""Fresh reset safety rules, exercised without touching installed services."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fresh


SITE = {'vm_disk': '/srv/zelab/data/disk.qcow2', 'vm_instance': 'zelab', 'vm_service': 'zelab-vm'}


class FreshTest(unittest.TestCase):
    def test_site_identity_names_host_and_instance(self):
        self.assertEqual(fresh.site_identity(SITE, 'fixture-host.example.net'), 'fixture-host:zelab')

    def test_only_an_exact_creation_receipt_proves_disk_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / 'created'
            receipt.write_text('/srv/zelab/data/disk.qcow2\nzelab\nzelab-vm\n')
            self.assertTrue(fresh.receipt_matches(receipt, SITE))
            for other in [{**SITE, 'vm_service': 'other-vm'}, {**SITE, 'vm_disk': '/srv/other.qcow2'}]:
                self.assertFalse(fresh.receipt_matches(receipt, other))
            self.assertFalse(fresh.receipt_matches(Path(directory) / 'missing', SITE))

    def test_deletion_refuses_symlinks_mounts_and_preserved_trees(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            state = root / 'state'
            (state / 'vault').mkdir(parents=True)
            alias = root / 'alias'
            alias.symlink_to(state, target_is_directory=True)
            keep = root / 'state/private'
            self.assertIsNone(fresh.safe_to_delete(state, [], []))
            self.assertIn('canonical', fresh.safe_to_delete(alias, [], []))
            self.assertIn('mount', fresh.safe_to_delete(state, [], [state / 'vault']))
            self.assertIn('mount', fresh.safe_to_delete(state, [], [state]))
            self.assertIn('preserved', fresh.safe_to_delete(state, [keep], []))
            self.assertIn('preserved', fresh.safe_to_delete(state, [state], []))
            self.assertIn('canonical', fresh.safe_to_delete(Path('/srv'), [], []))

    def test_only_routes_proxying_to_this_site_are_selected(self):
        config = {'Web': {
            'box.ts.net:443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:3773'}}},
            'box.ts.net:8444': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:18640'}}},
            'box.ts.net:8443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:13100'}}},
            'other.ts.net:9443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:18645'}}},
            'box.ts.net:9443': {'Handlers': {'/': {'Path': '/srv/site'}}},
        }}
        backends = {'http://127.0.0.1:18640', 'http://127.0.0.1:13100', 'http://127.0.0.1:18645'}
        self.assertEqual(fresh.owned_tail_ports(config, 'box.ts.net', backends), [8443, 8444])
        self.assertEqual(fresh.owned_tail_ports({}, 'box.ts.net', backends), [])

    def test_backup_key_logs_and_personal_login_are_never_generated_files(self):
        self.assertNotIn('backup-passphrase', fresh.GENERATED_PRIVATE)
        self.assertFalse(any(name.startswith(('logs', 'auth')) for name in fresh.GENERATED_PRIVATE))

    def test_wrong_or_missing_confirmation_changes_nothing(self):
        plan = fresh.Plan('fixture-host:zelab', units=[('zelab-vm.service', 'evidence')])
        site = {'ui_tailscale_hostname': 'box.ts.net', 'backup_dir': '/b', 'private_dir': '/p'}
        for confirmation in ['', 'fixture-host:other', 'zelab']:
            with self.subTest(confirmation=confirmation), \
                 patch.object(fresh.os, 'geteuid', return_value=0), \
                 patch.object(fresh, 'read_inputs', return_value=site), \
                 patch.object(fresh, 'discover', return_value=plan), \
                 patch.object(fresh, 'reset') as reset, \
                 patch.object(fresh, 'command') as command, \
                 redirect_stdout(io.StringIO()) as output:
                self.assertEqual(fresh.main(['reset', '--confirm', confirmation]), 2)
            reset.assert_not_called()
            command.assert_not_called()
            self.assertIn('Nothing changed', output.getvalue())

    def test_owned_accounts_require_matching_home_or_comment(self):
        entry = fresh.pwd.struct_passwd(('zelab-paperclip', 'x', 998, 998, '', '/var/lib/zelab-paperclip', '/bin/false'))
        with patch.object(fresh, 'account', return_value=entry):
            self.assertIsNotNone(fresh.owned_account('zelab-paperclip', home='/var/lib/zelab-paperclip'))
            self.assertIsNone(fresh.owned_account('zelab-paperclip', home='/srv/other'))
            self.assertIsNone(fresh.owned_account('zelab-paperclip', comment='Zelab native VM'))
        person = fresh.pwd.struct_passwd(('operator', 'x', 1000, 1000, '', '/var/lib/zelab-paperclip', '/bin/bash'))
        with patch.object(fresh, 'account', return_value=person):
            self.assertIsNone(fresh.owned_account('operator', home='/var/lib/zelab-paperclip'))

    def test_only_missing_or_altered_unrelated_items_fail_the_reset(self):
        before = {'containers': ['a web running', 'b db running'], 'serve_routes': ['h:443'], 'accounts': ['bob']}
        grown = {**before, 'containers': before['containers'] + ['c job running']}
        self.assertEqual(fresh.compare_unrelated(before, grown), ([], ['1 containers']))
        stopped = {**before, 'containers': ['a web exited', 'b db running']}
        self.assertEqual(fresh.compare_unrelated(before, stopped)[0], ['containers'])
        self.assertEqual(fresh.compare_unrelated(before, {**before, 'serve_routes': []})[0], ['serve_routes'])


if __name__ == '__main__':
    unittest.main()
