"""make bot building blocks: names, the guest probe, Paperclip agent matching and the zelab skill."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

import bots

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = yaml.safe_load((ROOT / 'config/defaults.yaml').read_text())
SITE = {**DEFAULTS, 'paperclip_port': 13100, 'guest_api_port': 18642}


def host_projects():
    spec = importlib.util.spec_from_file_location('host_projects', ROOT / 'deploy/roles/host_projects/files/host_projects.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BotTest(unittest.TestCase):
    def test_names_are_profile_and_account_safe(self):
        self.assertIsNone(bots.valid_name('scout'))
        for name in ('Scout', 'a', 'default', 'bad_name', '-x', 'x-', 'a' * 25):
            with self.subTest(name=name):
                self.assertIsNotNone(bots.valid_name(name))
        self.assertIn('exists', bots.valid_name('scout', {'scout'}))

    def test_guest_probe_reports_booleans_never_values(self):
        with tempfile.TemporaryDirectory() as home:
            profile = Path(home, 'profiles/scout')
            profile.mkdir(parents=True)
            (profile / 'config.yaml').write_text('model:\n  provider: openai-codex\n  default: gpt-x\n')
            (profile / 'profile.yaml').write_text('description: finds sources\n')
            (profile / 'auth.json').write_text(json.dumps({'providers': {'openai-codex': {'access_token': 'SECRET-A'}}}))
            (profile / '.env').write_text('API_SERVER_KEY=SECRET-B\nDISCORD_BOT_TOKEN=SECRET-C\nTELEGRAM_BOT_TOKEN=\n')
            (profile / 'SOUL.md').write_text('You are Hermes Agent, built by Nous Research. Be direct.\n')
            writer = Path(home, 'profiles/writer')
            writer.mkdir()
            (writer / 'config.yaml').write_text('model: {}\n')
            (writer / 'SOUL.md').write_text('\ufeff# Writer\n\nYou are Writer.\n')
            draft = Path(home, 'profiles/draft')
            draft.mkdir()
            (draft / 'config.yaml').write_text('model: {}\n')
            (draft / 'SOUL.md').write_text(bots.starter('draft', '', {}))
            Path(home, 'profiles/empty').mkdir()
            output = subprocess.run([sys.executable, '-c', bots.FACTS, home], capture_output=True, text=True, check=True).stdout
        self.assertNotIn('SECRET', output)
        draft, facts, writer = json.loads(output)
        self.assertTrue(writer['soul'])
        self.assertFalse(draft['soul'])
        self.assertEqual(facts, {'name': 'scout', 'role': 'finds sources', 'model': 'openai-codex/gpt-x',
                                 'signed_in': True, 'chats': ['Discord'], 'api_key': True, 'task_key': False,
                                 'allow_bots': False, 'soul': False})

    def test_only_live_gateway_agents_for_a_profile_are_bots(self):
        prefix = 'http://127.0.0.1:18642/p/'
        agents = [{'id': '1', 'adapterType': 'hermes_gateway', 'status': 'idle', 'adapterConfig': {'apiBaseUrl': prefix + 'scout'}},
                  {'id': '2', 'adapterType': 'hermes_gateway', 'status': 'terminated', 'adapterConfig': {'apiBaseUrl': prefix + 'old'}},
                  {'id': '3', 'adapterType': 'codex_local', 'status': 'idle', 'adapterConfig': {}},
                  {'id': '4', 'adapterType': 'hermes_gateway', 'status': 'idle', 'adapterConfig': {'apiBaseUrl': 'http://elsewhere/p/x'}}]
        self.assertEqual({name: agent['id'] for name, agent in bots.bot_agents(SITE, agents).items()}, {'scout': '1'})

    def test_skill_lists_projects_and_bots_without_credentials(self):
        projects = [{'id': 'p1', 'name': 'Homelab', 'description': 'Vault folder: Projects/homelab/'},
                    {'id': 'p2', 'name': 'Market Study'}]
        agents = {'scout': {'id': 'a1', 'title': 'finds sources'}, 'writer': {'id': 'a2'}}
        text = bots.skill(SITE, 'scout', agents, projects)
        self.assertTrue(text.startswith('---\nname: zelab\n'))
        self.assertIn('| homelab | Homelab | `p1` | `/var/lib/zelab-notes/Projects/homelab/` |', text)
        self.assertIn('| market-study | Market Study | `p2` |', text)
        self.assertIn('| writer |  | `a2` |', text)
        self.assertIn('/profiles/scout/skills/paperclip/paperclip-task-bridge/paperclip-task.mjs', text)
        self.assertNotIn('pcp_', text)

    def test_starter_persona_names_the_bot_and_its_team(self):
        text = bots.starter('jarvis', 'Chief of Staff', {'scout': 'finds sources', 'writer': ''},
                            'You are Hermes Agent, built by Nous Research. Be direct.')
        self.assertTrue(text.startswith('# Jarvis\n\nYou are Jarvis, one of the owner\'s bots. In chat and in Paperclip your name is jarvis. Your role: Chief of Staff.\n'))
        self.assertIn('The other bots: scout (finds sources), writer.', text)
        self.assertTrue(text.endswith('## Tone\n\nBe direct.\n'))
        self.assertIn(bots.PLACEHOLDER, text)
        self.assertNotIn('other bots', bots.starter('x', '', {}))

    def test_remote_commands_are_shell_quoted(self):
        guest = bots.Guest({**SITE, 'deploy_key': '/k', 'guest_known_hosts': '/h', 'guest_ssh_port': 12921,
                            'guest_user': 'zelab-deploy', 'hermes_install_dir': '/opt/hermes/agent'})
        argv = guest.hermes_argv('scout', 'profile', 'create', 'x', '--description', "it's; rm -rf /")
        self.assertEqual(argv[-2:], ['--description', "it's; rm -rf /"])
        self.assertEqual(argv[argv.index('-p') + 1], 'scout')
        self.assertIn('-t', guest.ssh('true', tty=True))

    def test_menu_refuses_to_run_unattended(self):
        result = subprocess.run([sys.executable, str(ROOT / 'deploy/bots.py'), 'menu'], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, cwd=ROOT)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('terminal', result.stderr)


class HostProjectsTest(unittest.TestCase):
    def test_sudo_fragment_follows_the_preset(self):
        module = host_projects()
        self.assertIsNone(module.sudoers_fragment('web', []))
        fragment = module.sudoers_fragment('homelab', ['ALL'])
        self.assertIn('zelab-p-homelab ALL=(root) NOPASSWD: ALL', fragment)
        self.assertTrue(fragment.startswith('# Zelab host project homelab.'))

    def test_ssh_key_is_restricted_to_the_host_address(self):
        module = host_projects()
        self.assertEqual(module.authorized_keys('127.0.0.1', 'ssh-ed25519 AAAA zelab-p-x\n'),
                         'from="127.0.0.1",restrict ssh-ed25519 AAAA\n')
        self.assertEqual(module.allowusers({'b': {}, 'a': {}}, '127.0.0.1'),
                         '# Project host accounts; managed by make.\n'
                         'AllowUsers zelab-p-a@127.0.0.1\nAllowUsers zelab-p-b@127.0.0.1\n')


if __name__ == '__main__':
    unittest.main()
