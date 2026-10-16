"""Regression checks for private input parsing before provisioning."""

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import subprocess
import sys
from unittest.mock import patch

import yaml

from run import read_inputs
from projects import MONITOR_COMMANDS


# Overrides for a second guest beside a full site.
SEPARATE_GUEST = {
    'vm_instance': 'zelab-fresh', 'vm_service': 'zelab-fresh-vm', 'vm_user': 'zelab-fresh-vm', 'vm_disk_gib': 16,
    'vm_root': '/var/lib/zelab-fresh-vm/root', 'vm_data': '/var/lib/zelab-fresh-vm/data',
    'vm_bootstrap_dir': '/var/lib/zelab-fresh-vm/bootstrap', 'guest_ssh_port': 12931,
    'guest_dashboard_port': 18650, 'guest_api_port': 18652,
}


class PrivateInputsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.site = self.root / "site.yaml"
        # Expand the minimal example with public defaults so tests can edit any field.
        merged = {**yaml.safe_load(Path("config/defaults.yaml").read_text()),
                  **yaml.safe_load(Path("config/site.example.yaml").read_text()),
                  'operator_user': 'operator'}
        self.site.write_text(yaml.safe_dump(merged))
        self.env = self.root / ".env"

    def write_env(self, text):
        self.env.write_text(text)
        self.env.chmod(0o600)

    def test_paths_override_site_without_expansion(self):
        self.write_env('ZELAB_PRIVATE_DIR="/var/lib/operator-state"\n'
                       'ZELAB_DEPLOY_KEY=/var/lib/operator-state/deploy_ed25519\n'
                       'ZELAB_KNOWN_HOSTS=/var/lib/operator-state/guest_known_hosts\n')
        self.assertEqual(read_inputs(self.site, self.env)["private_dir"], "/var/lib/operator-state")
        self.env.unlink()
        self.assertEqual(read_inputs(self.site, self.env)["private_dir"], "/home/operator/.local/state/zelab-deployment")

    def test_shell_text_is_rejected_without_execution(self):
        sentinel = self.root / "should-not-exist"
        self.write_env(f"ZELAB_PRIVATE_DIR=/tmp/$(touch {sentinel})\n")
        with self.assertRaises(ValueError):
            read_inputs(self.site, self.env)
        self.assertFalse(sentinel.exists())

    def test_standard_host_ssh_port_is_supported_without_allowing_service_collisions(self):
        site = yaml.safe_load(self.site.read_text())
        self.assertEqual(read_inputs(self.site, self.env)['worker_ssh_port'], 22)
        site['worker_ssh_port'] = site['paperclip_port']
        self.site.write_text(yaml.safe_dump(site))
        with self.assertRaisesRegex(ValueError, 'conflict'):
            read_inputs(self.site, self.env)

    def test_invalid_host_ssh_ports_fail_before_provisioning(self):
        site = yaml.safe_load(self.site.read_text())
        for value in [0, 65536, True, '22']:
            site['worker_ssh_port'] = value
            self.site.write_text(yaml.safe_dump(site))
            with self.subTest(port=value), self.assertRaises(ValueError):
                read_inputs(self.site, self.env)

    def test_create_cannot_overwrite_another_guests_ssh_pin(self):
        site = yaml.safe_load(self.site.read_text())
        site.update(SEPARATE_GUEST)
        pin = self.root / 'existing_known_hosts'
        pin.write_text('[127.0.0.1]:12921 ssh-ed25519 preserved-key\n')
        site['guest_known_hosts'] = str(pin)
        site['private_dir'] = str(self.root)
        site['deploy_key'] = str(self.root / 'deploy_ed25519')
        self.site.write_text(yaml.safe_dump(site))
        with self.assertRaisesRegex(ValueError, 'another guest'):
            read_inputs(self.site, self.env)

    def test_projects_file_is_validated_and_carries_its_preset(self):
        projects = self.root / 'projects.yaml'
        site = yaml.safe_load(self.site.read_text())
        site['projects_config'] = str(projects)
        self.site.write_text(yaml.safe_dump(site))
        self.assertEqual(read_inputs(self.site, self.env)['zelab_projects'], {})
        projects.write_text('projects:\n  homelab:\n    name: Homelab\n  research: {}\n'
                            'tiers:\n  ops:\n    paths: [/srv/homelab]\n')
        loaded = read_inputs(self.site, self.env)
        self.assertEqual(loaded['zelab_projects'], {'homelab': {'name': 'Homelab'}, 'research': {'name': 'research'}})
        self.assertEqual(loaded['zelab_tiers']['ops'], {'preset': 'docker', 'paths': ['/srv/homelab'], 'account': 'zelab-team-ops',
                                                        'groups': ['systemd-journal', 'adm', 'docker'],
                                                        'sudo': list(MONITOR_COMMANDS), 'paths_rights': 'rwx'})
        self.assertEqual(loaded['zelab_tiers']['root']['sudo'], ['ALL'])
        self.assertEqual(loaded['zelab_team']['members']['pm']['lead'], True)
        projects.write_text('projects:\n  homelab: {}\nproject_replacements:\n  old-lab: homelab\n')
        self.assertEqual(read_inputs(self.site, self.env)['zelab_project_replacements'], {'old-lab': 'homelab'})
        for text in ('projects:\n  Bad_Slug: {}\n', 'projects:\n  web:\n    host_preset: repo-dev\n',
                     'projects:\n  web:\n    paths: [/srv/web]\n', 'tiers:\n  ops:\n    paths: [/etc]\n',
                     'tiers:\n  nope:\n    preset: docker\n', 'tiers:\n  ops:\n    preset: root\n',
                     'projects:\n  a:\n    name: Same\n  b:\n    name: same\n', 'other: 1\n',
                     'project_replacements: []\n', 'projects:\n  lab: {}\nproject_replacements:\n  lab: lab\n',
                     'projects:\n  target: {}\nproject_replacements:\n  old: missing\n'):
            with self.subTest(text=text):
                projects.write_text(text)
                with self.assertRaises(ValueError):
                    read_inputs(self.site, self.env)

    def test_notes_sync_remote_must_be_a_github_ssh_address(self):
        site = yaml.safe_load(self.site.read_text())
        for remote, ok in (('', True), ('git@github.com:owner/zelab-notes.git', True),
                           ('https://github.com/owner/notes.git', False), ('git@evil.example:owner/notes.git', False),
                           ('git@github.com:owner/notes.git; rm -rf /', False)):
            with self.subTest(remote=remote):
                self.site.write_text(yaml.safe_dump({**site, 'notes_sync_remote': remote}))
                if ok:
                    self.assertEqual(read_inputs(self.site, self.env)['notes_sync_remote'], remote)
                else:
                    with self.assertRaises(ValueError):
                        read_inputs(self.site, self.env)

    def test_team_file_is_validated(self):
        import projects
        good = {'tiers': {'dev': {'preset': 'repo-dev'}},
                'members': {'pm': {'title': 'PM', 'role': 'pm', 'tier': 'dev', 'lead': True}}}
        team = projects.validate_team(good)
        self.assertEqual((team['harness'], team['harnesses'], team['members']['pm']['harness']),
                         ('claude', {'claude': {'model': 'claude-opus-5-5', 'effort': 'high'},
                                     'codex': {'model': 'gpt-5.6-sol', 'effort': 'high'}}, 'claude'))
        mixed = projects.validate_team({**good, 'harness': 'codex', 'codex': {'model': 'gpt-6-sol', 'effort': 'max'},
                                        'members': {**good['members'], 'dev': {'tier': 'dev', 'harness': 'claude'}}})
        self.assertEqual(({m: s['harness'] for m, s in mixed['members'].items()}, mixed['harnesses']['codex']),
                         ({'pm': 'codex', 'dev': 'claude'}, {'model': 'gpt-6-sol', 'effort': 'max'}))
        for bad in ({'tiers': {'dev': {}}}, {**good, 'members': {'pm': {'tier': 'ops'}}},
                    {**good, 'members': {'a1': {'tier': 'dev', 'lead': True}, 'b1': {'tier': 'dev', 'lead': True}}},
                    {**good, 'members': {'pm': {'tier': 'dev', 'role': 'wizard'}}}, {**good, 'claude': {'effort': 'extreme'}},
                    {**good, 'harness': 'gemini'}, {**good, 'members': {'pm': {'tier': 'dev', 'harness': 'gemini'}}},
                    {**good, 'codex': {'model': 'gpt-6-sol', 'sandbox': False}}, {**good, 'model': 'claude-opus-5-5'},
                    {**good, 'other': 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                projects.validate_team(bad)
        with self.assertRaises(ValueError):
            projects.validate_team({**good, 'members': {'ghost': {'tier': 'dev'}}}, Path('config/team'))

    def test_duplicate_or_unknown_inputs_are_rejected(self):
        # .env accepts only path names, so it cannot hold a credential.
        for text in ["ZELAB_PRIVATE_DIR=/tmp/one\nZELAB_PRIVATE_DIR=/tmp/two\n", "UNKNOWN_TOKEN=do-not-print\n"]:
            with self.subTest(text_kind=text.split("=")[0]):
                self.write_env(text)
                with self.assertRaises(ValueError):
                    read_inputs(self.site, self.env)

    def test_conflicting_ports_are_rejected(self):
        site = yaml.safe_load(self.site.read_text())
        site["paperclip_port"] = site["guest_dashboard_port"]
        self.site.write_text(yaml.safe_dump(site))
        with self.assertRaises(ValueError):
            read_inputs(self.site, self.env)

    def test_cli_refuses_isolated_core_before_provisioning(self):
        site = yaml.safe_load(self.site.read_text())
        site["vm_isolated"] = True
        self.site.write_text(yaml.safe_dump(site))
        result = subprocess.run(
            [sys.executable, "deploy/run.py", "--site-config", str(self.site), "--env-file", str(self.env), "deploy/site.yaml"],
            text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("vm_isolated", result.stderr)

    def test_create_requires_explicit_identity_paths_and_capacity(self):
        site = yaml.safe_load(self.site.read_text())
        site['vm_service'] = 'Zelab VM'
        self.site.write_text(yaml.safe_dump(site))
        with self.assertRaises(ValueError):
            read_inputs(self.site, self.env)
        site['vm_service'] = 'zelab-vm'
        for size, valid in [(7, False), (8, True), (512, True)]:
            self.site.write_text(yaml.safe_dump({**site, 'vm_disk_gib': size}))
            with self.subTest(vm_disk_gib=size):
                if valid:
                    self.assertEqual(read_inputs(self.site, self.env)['vm_disk_gib'], size)
                else:
                    with self.assertRaises(ValueError):
                        read_inputs(self.site, self.env)
        site.update(SEPARATE_GUEST)
        self.site.write_text(yaml.safe_dump(site))
        self.assertEqual(read_inputs(self.site, self.env)['vm_disk_gib'], 16)
        site['vm_service'] = 'unsafe/../unit'
        self.site.write_text(yaml.safe_dump(site))
        with self.assertRaises(ValueError):
            read_inputs(self.site, self.env)

    def test_yaml_duplicate_keys_and_invalid_types_are_rejected(self):
        original = self.site.read_text()
        self.site.write_text(original + '\nvm_cpu: 2\n')
        with self.assertRaisesRegex(ValueError, 'unique'):
            read_inputs(self.site, self.env)
        site = yaml.safe_load(original)
        for key, value in [('notes_enabled', 'false'), ('vm_cpu', True),
                           ('operator_user', 'root'), ('vm_service', 'unit;touch'),
                           ('hermes_initial_model', 'model\nINJECT=true'), ('projects_dir', 'relative/path')]:
            with self.subTest(key=key):
                self.site.write_text(yaml.safe_dump({**site, key: value}))
                with self.assertRaises(ValueError):
                    read_inputs(self.site, self.env)

    def test_state_and_private_path_aliases_are_rejected(self):
        site = yaml.safe_load(self.site.read_text())
        for updates in [{'paperclip_home': '/'},
                        {'projects_dir': site['paperclip_home'] + '/nested'},
                        {'backup_dir': site['paperclip_home'] + '/archive'},
                        {'private_dir': site['paperclip_home']},
                        {'deploy_key': '/tmp/unmanaged-key'},
                        {'vm_root': '/var/lib/../lib/guest'},
                        {'paperclip_home': '/home/operator/paperclip'},
                        {'paperclip_allowed_hosts': ['10.0.2.2']}]:
            with self.subTest(fields=list(updates)):
                self.site.write_text(yaml.safe_dump({**site, **updates}))
                with self.assertRaises(ValueError):
                    read_inputs(self.site, self.env)

    def test_existing_symlink_state_is_rejected_before_provisioning(self):
        site = yaml.safe_load(self.site.read_text())
        target = self.root / 'real-state'
        target.mkdir()
        alias = self.root / 'alias-state'
        alias.symlink_to(target, target_is_directory=True)
        site['paperclip_home'] = str(alias)
        self.site.write_text(yaml.safe_dump(site))
        with self.assertRaisesRegex(ValueError, 'symlink'):
            read_inputs(self.site, self.env)
        self.assertEqual(read_inputs(self.site, None, inspect_private=False)['paperclip_home'], str(alias))


    def test_minimal_site_derives_operator_paths_and_tailnet_host(self):
        self.site.write_text('operator_user: operator\nui_tailscale_enabled: true\n'
                             'ui_tailscale_hostname: box.example.ts.net\n')
        site = read_inputs(self.site, None)
        self.assertEqual(site['private_dir'], '/home/operator/.local/state/zelab-deployment')
        self.assertEqual(site['deploy_key'], site['private_dir'] + '/deploy_ed25519')
        self.assertEqual(site['vm_disk'], site['vm_data'] + '/disk.qcow2')
        self.assertEqual(site['paperclip_allowed_hosts'], ['127.0.0.1', '10.0.2.2', 'box.example.ts.net'])
        self.assertEqual(site['paperclip_owner_email'], 'operator@zelab.local')
        self.site.write_text('worker_ssh_port: 22\n')
        with patch('run.operator', return_value='discovered'):
            self.assertEqual(read_inputs(self.site, None)['operator_user'], 'discovered')

    def test_public_examples_follow_the_actual_account_and_environment(self):
        self.site.write_text(Path('config/site.example.yaml').read_text())
        self.write_env(Path('.env.example').read_text())
        account = SimpleNamespace(pw_name='fixture', pw_dir='/srv/users/fixture')
        with patch('run.operator', return_value='fixture'), \
             patch('run.pwd.getpwnam', return_value=account), \
             patch('run.pwd.getpwuid', return_value=account), \
             patch('run.os.geteuid', return_value=1000), \
             patch.dict('run.os.environ', {'XDG_STATE_HOME': '/srv/state/fixture',
                                           'CODEX_HOME': '/srv/auth/fixture'}):
            site = read_inputs(self.site, self.env, inspect_private=False)
            with patch.dict('run.os.environ', {'XDG_STATE_HOME': '', 'CODEX_HOME': ''}):
                empty = read_inputs(self.site, self.env, inspect_private=False)
        self.assertEqual(site['operator_user'], 'fixture')
        self.assertEqual(site['private_dir'], '/srv/state/fixture/zelab-deployment')
        self.assertEqual(site['backup_dir'], '/srv/users/fixture/backups/Zelab')
        self.assertEqual(site['dashboard_username'], 'fixture')
        self.assertFalse(site['ui_tailscale_enabled'])
        self.assertEqual(empty['private_dir'], '/srv/users/fixture/.local/state/zelab-deployment')

    def test_separate_guest_examples_derive_distinct_private_state(self):
        baseline = read_inputs(self.site, None)
        values = yaml.safe_load(self.site.read_text())
        values.update(SEPARATE_GUEST)
        self.site.write_text(yaml.safe_dump(values))
        separate = read_inputs(self.site, None)
        for key in ['private_dir', 'backup_dir', 'deploy_key', 'guest_known_hosts']:
            self.assertNotEqual(baseline[key], separate[key], key)

    def test_optional_headroom_can_be_zero(self):
        site = yaml.safe_load(self.site.read_text())
        site.update(host_disk_reserve_gib=0, host_memory_reserve_gib=0)
        self.site.write_text(yaml.safe_dump(site))
        self.assertEqual(read_inputs(self.site, None)['host_disk_reserve_gib'], 0)


if __name__ == "__main__":
    unittest.main()
