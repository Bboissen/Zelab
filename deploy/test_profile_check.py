"""Owner admission and SSH-boundary regressions using private temporary fixtures."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


CHECK_PATH = Path(__file__).parent / 'roles/hermes_config_check/files/check_profile.py'
SPEC = importlib.util.spec_from_file_location('profile_check', CHECK_PATH)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
OWNER = '123456789012345670'
OTHER = '123456789012345671'
CHANNEL = '123456789012345672'


class ProfileCheckTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.env = {'DISCORD_ALLOWED_USERS': OWNER, 'DISCORD_ALLOWED_CHANNELS': CHANNEL,
                    'DISCORD_ALLOW_ALL_USERS': 'false', 'GATEWAY_ALLOW_ALL_USERS': 'false',
                    'DISCORD_ALLOW_BOTS': 'false', 'DISCORD_BOT_TOKEN': 'private-test-token'}
        self.terminal = {'TERMINAL_ENV': 'ssh', 'TERMINAL_SSH_HOST': '127.0.0.1',
                         'TERMINAL_SSH_PORT': '22', 'TERMINAL_SSH_USER': 'zelab-research-tools',
                         'TERMINAL_SSH_KEY': str(self.home / 'terminal.key')}

    def check(self, env=None, config=None, legacy=None, tool_user=''):
        CHECK.check_profile(self.home, OWNER, self.env if env is None else env,
                            config or {}, legacy or {}, self.terminal, tool_user)

    def test_owner_channels_models_and_additional_restrictions_are_retained(self):
        self.check(config={'model': {'default': 'owner-selected-model'},
                           'discord': {'allowed_channels': [CHANNEL]}})
        self.check(tool_user='zelab-research-tools')
        self.check(env={**self.env, 'DISCORD_ALLOWED_USERS': json.dumps([OWNER])})

    def test_env_cannot_widen_admission_or_remove_user_gate(self):
        for key, value in [('DISCORD_ALLOWED_USERS', '*'), ('DISCORD_ALLOWED_USERS', ''),
                           ('DISCORD_ALLOWED_USERS', OWNER + ',' + OTHER),
                           ('DISCORD_ALLOWED_ROLES', OTHER), ('DISCORD_ALLOW_ALL_USERS', 'true'),
                           ('GATEWAY_ALLOW_ALL_USERS', '1'), ('DISCORD_ALLOW_BOTS', 'mentions')]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.check(env={**self.env, key: value})

    def test_yaml_and_legacy_nested_grants_are_checked(self):
        for config in [
            {'gateway': {'allow_all_users': True}},
            {'platforms': {'discord': {'extra': {'allowed_roles': [OTHER]}}}},
            {'gateway': {'platforms': {'discord': {'allow_from': [OWNER, OTHER]}}}},
            {'discord': {'dm_role_auth_guild': OTHER}},
            {'gateway': {'profile_routes': [{'profile': 'default'}]}},
        ]:
            for source in ['config', 'legacy']:
                with self.subTest(source=source, config=config), self.assertRaises(ValueError):
                    self.check(**{source: config})

    def test_pairing_cannot_bypass_declared_owner(self):
        for relative in ['pairing/discord-approved.json', 'platforms/pairing/discord-approved.json']:
            path = self.home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({OWNER: {'user_name': 'owner'}}))
            self.check()
            path.write_text(json.dumps({OTHER: {'user_name': 'untrusted'}}))
            with self.assertRaises(ValueError):
                self.check()
            path.unlink()

    def test_restricted_env_cannot_change_identity_or_carry_coordinator_credentials(self):
        for key, value in [('TERMINAL_ENV', 'local'), ('TERMINAL_SSH_USER', 'zelab-hermes'),
                           ('TERMINAL_SSH_HOST', '10.0.2.2'), ('TERMINAL_SSH_KEY', '/owner.key'),
                           ('PAPERCLIP_BRIDGE_API_KEY', 'private-test-key'),
                           ('API_SERVER_KEY', 'private-test-key')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.check(env={**self.env, key: value}, tool_user='zelab-research-tools')
        changed = copy.deepcopy(self.terminal)
        self.terminal['TERMINAL_ENV'] = 'local'
        with self.assertRaises(ValueError):
            self.check(tool_user='zelab-research-tools')
        self.terminal = changed

    def test_undeclared_owner_admits_nobody(self):
        closed = {key: value for key, value in self.env.items() if key != 'DISCORD_ALLOWED_USERS'}
        CHECK.check_profile(self.home, '', closed, {}, {}, self.terminal, '')
        CHECK.check_profile(self.home, '', {**closed, 'DISCORD_ALLOWED_USERS': ''}, {}, {}, self.terminal, '')
        for env, config in [({**closed, 'DISCORD_ALLOWED_USERS': OTHER}, {}),
                            (closed, {'discord': {'allowed_users': [OTHER]}})]:
            with self.subTest(env=env, config=config), self.assertRaises(ValueError):
                CHECK.check_profile(self.home, '', env, config, {}, self.terminal, '')
        approved = self.home / 'pairing/discord-approved.json'
        approved.parent.mkdir(parents=True)
        approved.write_text(json.dumps({OTHER: {}}))
        with self.assertRaises(ValueError):
            CHECK.check_profile(self.home, '', closed, {}, {}, self.terminal, '')


if __name__ == '__main__':
    unittest.main()
