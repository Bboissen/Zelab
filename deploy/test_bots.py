"""make bot building blocks: names, the guest probe, Paperclip agent matching and the zelab skill."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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
    def test_context_revision_changes_on_dependency_only_upgrades(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = ('versions.yaml', 'deploy/roles/hermes/files/dependencies/manifest.json',
                      'deploy/roles/paperclip/files/package.json', 'deploy/roles/paperclip/files/package-lock.json')
            for name in inputs:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('unchanged pin')
            previous = bots.integration_revision(root)
            for name in inputs[1:]:
                (root / name).write_text('qualified dependency update')
                current = bots.integration_revision(root)
                self.assertNotEqual(current, previous)
                self.assertEqual(current, bots.integration_revision(root))
                previous = current

    def test_declared_replacements_resolve_ids_without_guessing_new_permissions(self):
        projects = [{'id': 'old', 'name': 'Old Lab'}, {'id': 'new', 'name': 'Homelab'}]
        self.assertEqual(bots.project_replacements(SITE, projects), {})
        site = {**SITE, 'zelab_project_replacements': {'old-lab': 'homelab'}}
        self.assertEqual(bots.project_replacements(site, projects), {'old': 'new'})
        for values in (projects + [projects[1]], projects[:1], [projects[0], {**projects[1], 'archivedAt': 'date'}]):
            with self.subTest(values=values), self.assertRaises(bots.Stop):
                bots.project_replacements(site, values)

    def test_generated_files_and_bundled_skill_copy_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'profile/skill/SKILL.md'
            code = bots.WRITE_FILE.replace('61102', str(os.getuid()))
            args = [sys.executable, '-c', code, str(target), str(root)]
            subprocess.run(args, input='instructions', text=True, check=True)
            before = target.stat().st_mtime_ns
            subprocess.run(args, input='instructions', text=True, check=True)
            self.assertEqual(target.stat().st_mtime_ns, before)
            source = root / 'upstream'
            source.mkdir()
            (source / 'SKILL.md').write_text('upstream')
            (source / 'helper.mjs').write_text('one')
            code = bots.COPY_SKILL.replace('61102', str(os.getuid()))
            args = [sys.executable, '-c', code, str(source), str(target.parent)]
            subprocess.run(args, check=True)
            before = target.stat().st_mtime_ns
            subprocess.run(args, check=True)
            self.assertEqual(target.stat().st_mtime_ns, before)
            (source / 'helper.mjs').write_text('two')
            subprocess.run(args, check=True)
            self.assertEqual((target.parent / 'helper.mjs').read_text(), 'two')

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
            (profile / '.env').write_text('API_SERVER_KEY=SECRET-B\nDISCORD_BOT_TOKEN=SECRET-C\nTELEGRAM_BOT_TOKEN=\n'
                                          'DISCORD_HOME_CHANNEL="123"\n')
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
                                 'allow_bots': False, 'soul': False, 'home_channel': True, 'free_home': False})

    def test_home_channel_probe_prints_only_that_channel(self):
        with tempfile.TemporaryDirectory() as profile:
            Path(profile, '.env').write_text('DISCORD_BOT_TOKEN=SECRET\nDISCORD_HOME_CHANNEL="123"\n')
            output = subprocess.run([sys.executable, '-c', bots.HOME_CHANNEL, profile],
                                    capture_output=True, text=True, check=True).stdout
        self.assertEqual(output, '123')

    def test_only_live_gateway_agents_for_a_profile_are_bots(self):
        prefix = 'http://127.0.0.1:18642/p/'
        agents = [{'id': '1', 'adapterType': 'hermes_gateway', 'status': 'idle', 'adapterConfig': {'apiBaseUrl': prefix + 'scout'}},
                  {'id': '2', 'adapterType': 'hermes_gateway', 'status': 'terminated', 'adapterConfig': {'apiBaseUrl': prefix + 'old'}},
                  {'id': '3', 'adapterType': 'codex_local', 'status': 'idle', 'adapterConfig': {}},
                  {'id': '4', 'adapterType': 'hermes_gateway', 'status': 'idle', 'adapterConfig': {'apiBaseUrl': 'http://elsewhere/p/x'}}]
        self.assertEqual({name: agent['id'] for name, agent in bots.bot_agents(SITE, agents).items()}, {'scout': '1'})
        agents[0]['adapterConfig']['apiBaseUrl'] = 'http://127.0.0.1:12345/p/scout'
        self.assertIn('scout', bots.bot_agents(SITE, agents))

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

    def test_team_is_matched_by_metadata_and_only_the_lead_is_assignable(self):
        team = {'model': 'claude-opus-5-5', 'effort': 'high', 'tiers': {},
                'members': {'pm': {'title': 'Product Manager', 'role': 'pm', 'tier': 'dev', 'lead': True},
                            'backend': {'title': 'Backend Engineer', 'role': 'engineer', 'tier': 'dev', 'lead': False}}}
        site = {**SITE, 'zelab_team': team}
        ids = {'environments': {'dev host': 'ed', 'root host': 'er'}}
        agents = [{'id': 'h1', 'name': 'pm', 'adapterType': 'claude_local', 'status': 'idle', 'metadata': {'zelab_team': 'pm'}},
                  {'id': 'h2', 'name': 'backend', 'adapterType': 'claude_local', 'status': 'terminated',
                   'metadata': {'zelab_team': 'backend'}},
                  {'id': 'h3', 'name': 'helper', 'adapterType': 'codex_local', 'status': 'idle', 'defaultEnvironmentId': 'er'},
                  {'id': 'h4', 'name': 'elsewhere', 'adapterType': 'codex_local', 'status': 'idle'},
                  {'id': 'b1', 'adapterType': 'hermes_gateway', 'status': 'idle', 'defaultEnvironmentId': 'ed'}]
        members, others = bots.team_agents(site, agents, ids)
        self.assertEqual(({m: a['id'] for m, a in members.items()}, [a['id'] for a in others]), ({'pm': 'h1'}, ['h3']))
        self.assertEqual(bots.assignees(site, {'marvin': {'id': 'b1'}}, members), ['b1', 'h1'])
        self.assertEqual(bots.assignees(site, {'marvin': {'id': 'b1'}}, {}), ['b1'])
        text = bots.skill(site, 'marvin', {'marvin': {'id': 'b1'}}, [], members, others)
        self.assertIn('| pm | Product Manager (lead) | `h1` | yes |', text)
        self.assertIn('| helper |  | `h3` | no |', text)
        self.assertIn('--assignee-agent-id h1', text)
        self.assertIn('--unassigned', bots.skill(site, 'marvin', {}, []))

    def test_team_prefers_the_connected_shared_claude_connection(self):
        personal = {'id': 'c1', 'grantId': 'g1', 'provider': 'anthropic', 'method': 'subscription',
                    'ownership': 'personal', 'status': 'connected'}
        shared = {**personal, 'id': 'c2', 'grantId': 'g2', 'ownership': 'shared'}
        self.assertIs(bots.team_binding([personal, {**shared, 'status': 'needs_attention'}]), bots.TEAM_CONNECTION)
        self.assertEqual(bots.team_binding([personal, shared]),
                         {'provider': 'anthropic', 'method': 'subscription', 'mode': 'shared',
                          'connectionId': 'c2', 'grantId': 'g2'})

    def test_claude_sign_in_preserves_changed_harnesses_and_explicit_connections(self):
        connection = {'id': 'claude', 'grantId': 'grant', 'provider': 'anthropic',
                      'method': 'subscription', 'ownership': 'shared', 'status': 'connected'}
        explicit = {**bots.TEAM_CONNECTION, 'mode': 'shared', 'connectionId': 'chosen', 'grantId': 'chosen-grant'}
        members = {
            'initial': {'id': 'initial', 'adapterType': 'claude_local',
                        'runtimeConfig': {'aiConnection': dict(bots.TEAM_CONNECTION), 'heartbeat': {'enabled': False}}},
            'codex': {'id': 'codex', 'adapterType': 'codex_local',
                      'runtimeConfig': {'aiConnection': {'provider': 'openai', 'method': 'subscription',
                                                       'mode': 'shared', 'connectionId': 'codex', 'grantId': 'cg'}}},
            'switching': {'id': 'switching', 'adapterType': 'codex_local',
                          'runtimeConfig': {'aiConnection': dict(bots.TEAM_CONNECTION)}},
            'explicit': {'id': 'explicit', 'adapterType': 'claude_local', 'runtimeConfig': {'aiConnection': explicit}},
            'legacy': {'id': 'legacy', 'adapterType': 'claude_local', 'runtimeConfig': {}},
        }
        calls = []

        class Paperclip:
            def ai_connections(self):
                return [connection]

            def call(self, method, path, body):
                calls.append((method, path, body))
                members[path.rsplit('/', 1)[1]].update(body)

        runner = bots.Bots.__new__(bots.Bots)
        runner.paperclip = Paperclip()
        runner.team = lambda: (members, [])
        runner.bind_team()
        runner.bind_team()
        self.assertEqual(calls, [('PATCH', '/agents/initial', {
            'runtimeConfig': {'aiConnection': bots.team_binding([connection]), 'heartbeat': {'enabled': False}}})])

    def test_onboarding_ticket_is_filed_once_for_the_lead(self):
        calls = []

        class FakePaperclip:
            company = 'c1'

            def call(self, method, path, body=None):
                calls.append((method, path, body))
                return {'identifier': 'ZEL-1'}

        with tempfile.TemporaryDirectory() as private:
            runner = bots.Bots.__new__(bots.Bots)
            runner.site = {**SITE, 'private_dir': private,
                           'zelab_team': {'members': {'pm': {'title': 'PM', 'tier': 'dev', 'lead': True}}}}
            runner.paperclip = FakePaperclip()
            runner.team = lambda: ({'pm': {'id': 'a-pm'}}, [])
            runner.onboard()
            runner.onboard()
            self.assertEqual(len(calls), 1)
            method, path, body = calls[0]
            self.assertEqual((method, path, body['assigneeAgentId'], body['idempotencyKey']),
                             ('POST', '/companies/c1/issues', 'a-pm', 'zelab-team-onboarding'))
            self.assertEqual(body['title'], 'Map this server into the vault')
            self.assertIn('No secrets in notes', body['description'])
            self.assertTrue((Path(private) / 'team-onboarding.json').is_file())

    def test_member_instructions_join_persona_team_rules_and_this_server(self):
        site = {**SITE, 'zelab_projects': {'media': {'name': 'Media Center'}},
                'zelab_tiers': {'root': {'account': 'zelab-team-root', 'preset': 'full-sudo', 'paths': []}},
                'zelab_team': {'members': {'pm': {'title': 'Product Manager', 'tier': 'root', 'lead': True},
                                           'sysadmin': {'title': 'Linux Sysadmin', 'tier': 'root', 'lead': False}}}}
        text = bots.team_instructions(site, 'sysadmin', {})
        persona = (bots.TEAM_DIR / 'sysadmin.md').read_text().strip()
        self.assertTrue(text.startswith(persona))
        self.assertIn((bots.TEAM_DIR / 'TEAM.md').read_text().strip(), text)
        self.assertIn('as `zelab-team-root`: unrestricted sudo', text)
        self.assertIn('Projects/media/`: Media Center', text)
        self.assertIn('You report to **pm**', text)
        self.assertIn('You lead the team', bots.team_instructions(site, 'pm', {}))

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


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        self.runner = bots.Bots.__new__(bots.Bots)
        self.runner.site = {**SITE, 'zelab_project_replacements': {'old-lab': 'homelab'}}
        self.runner.restart_needed = False
        self.profile = {'task_key': True}
        self.agent = {'id': 'a1', 'adapterConfig': {'apiBaseUrl': bots.bot_url(SITE, 'scout'),
                     'paperclipApiUrl': 'http://10.0.2.2:13100', 'instructions': bots.instructions(SITE, 'scout'),
                     'apiKey': {'type': 'secret_ref', 'secretId': 'retained'}, 'timeoutSec': 123},
                      'runtimeConfig': {'heartbeat': {'wakeOnDemand': True, 'enabled': False}}}
        self.runner.state = lambda: ({'scout': self.profile}, {'scout': self.agent})
        self.runner.team = lambda: ({}, [])
        self.runner.hire_team = lambda _: None
        self.runner.onboard = lambda: None
        self.runner.profile_hygiene = lambda action: None
        self.project_rows = [{'id': 'p-old', 'name': 'Old Lab'}, {'id': 'p-new', 'name': 'Homelab'},
                             {'id': 'p-other', 'name': 'Another Project'}]
        self.active = [{'id': 'k-old', 'name': bots.KEY_NAME, 'createdAt': '2026-01-01',
                        'scope': {'kind': 'task_bridge', 'projectIds': ['p-old'], 'allowedAssigneeAgentIds': ['a1']}}]
        self.calls, self.writes, self.hermes_calls, self.revoked, self.created = [], {}, [], [], []
        self.bridge_ready, self.open_issues = True, []
        self.archive_failures = 0
        self.passthrough = [*bots.TASK_ENV, 'CUSTOM_KEY']
        outer = self

        class Paperclip:
            company = 'c1'

            def projects(self):
                return outer.project_rows

            def keys(self, agent):
                return outer.active

            def create_key(self, agent, scope):
                outer.created.append(scope)
                key = {'id': 'k-new', 'name': bots.KEY_NAME, 'createdAt': '2026-02-01', 'scope': scope}
                outer.active.append(key)
                return {**key, 'token': 'private-test-token'}

            def revoke(self, agent, key):
                outer.revoked.append(key)
                next(k for k in outer.active if k['id'] == key)['revokedAt'] = 'date'

            def call(self, method, path, body=None):
                outer.calls.append((method, path, body))
                if method == 'GET' and '/issues?' in path:
                    return outer.open_issues
                if method == 'PATCH' and '/projects/' in path:
                    if outer.archive_failures:
                        outer.archive_failures -= 1
                        raise bots.Stop('Fixture rejected archive request (HTTP 400).')
                    next(p for p in outer.project_rows if p['id'] == path.rsplit('/', 1)[1]).update(body)
                if method == 'PATCH' and '/agents/' in path:
                    outer.agent.update(body)

        class Guest:
            def root_python(self, code, *args, stdin=''):
                if code == bots.PROFILE_SETTINGS:
                    return json.dumps({'env_passthrough': outer.passthrough})
                if code == bots.BRIDGE_READY:
                    return json.dumps(outer.bridge_ready)
                outer.writes[(code, args)] = stdin
                if code == bots.SET_ENV:
                    outer.bridge_ready = True
                return ''

            def hermes(self, *args):
                outer.hermes_calls.append(args)
                outer.passthrough = json.loads(args[-1])
                return 0, ''

        self.runner.paperclip, self.runner.guest = Paperclip(), Guest()

    def test_make_migrates_keys_and_skill_then_archives_without_deleting_anything(self):
        self.runner.refresh()
        self.assertEqual(self.created, [{'kind': 'task_bridge', 'projectIds': ['p-new'],
                                         'allowedAssigneeAgentIds': ['a1']}])
        self.assertEqual(self.revoked, ['k-old'])
        text = next(value for (code, _), value in self.writes.items() if code == bots.WRITE_FILE)
        self.assertIn('`p-new`', text)
        self.assertNotIn('`p-old`', text)
        self.assertNotIn('`p-other`', text)
        self.assertIsNotNone(self.project_rows[0]['archivedAt'])
        # Paperclip's pinned z.string().datetime() contract requires Z, and
        # rejects Python's otherwise valid ISO-8601 +00:00 representation.
        self.assertRegex(self.project_rows[0]['archivedAt'],
                         r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$')
        self.assertFalse(any(method == 'DELETE' for method, _, _ in self.calls))
        calls, written = list(self.calls), dict(self.writes)
        self.runner.refresh()
        self.assertEqual(self.calls, calls)
        self.assertEqual(self.created, [{'kind': 'task_bridge', 'projectIds': ['p-new'],
                                         'allowedAssigneeAgentIds': ['a1']}])
        self.assertEqual(self.writes, written)

    def test_make_preserves_native_team_harness_model_and_connection_choices(self):
        members = {
            'pm': {'id': 'host-pm', 'adapterType': 'codex_local', 'status': 'idle',
                   'adapterConfig': {'model': 'chosen-codex-model'},
                   'runtimeConfig': {'aiConnection': {'provider': 'openai', 'method': 'subscription',
                                                    'mode': 'shared', 'connectionId': 'codex', 'grantId': 'cg'}}},
            'backend': {'id': 'host-backend', 'adapterType': 'claude_local', 'status': 'idle',
                        'adapterConfig': {'model': 'chosen-claude-model'},
                        'runtimeConfig': {'aiConnection': dict(bots.TEAM_CONNECTION)}},
        }
        before = json.loads(json.dumps(members))
        self.runner.site['zelab_team'] = {'members': {
            'pm': {'title': 'Product Manager', 'lead': True},
            'backend': {'title': 'Backend Engineer', 'lead': False}}}
        self.runner.team = lambda: (members, [])
        self.runner.paperclip.ai_connections = lambda: [
            {'id': 'claude', 'grantId': 'grant', 'provider': 'anthropic', 'method': 'subscription',
             'ownership': 'shared', 'status': 'connected'}]
        self.runner.refresh()
        self.runner.refresh()
        self.assertEqual(members, before)
        self.assertFalse(any(method == 'PATCH' and path.startswith('/agents/host-')
                             for method, path, _ in self.calls))

    def test_interrupted_rotation_recreates_latest_scope_and_revokes_both_old_keys(self):
        self.active.append({**self.active[0], 'id': 'candidate', 'createdAt': '2026-01-02',
                            'scope': {**self.active[0]['scope'], 'projectIds': ['p-new']}})
        self.bridge_ready = False
        self.runner.refresh()
        self.assertEqual(self.created[0]['projectIds'], ['p-new'])
        self.assertEqual(self.revoked, ['k-old', 'candidate'])

    def test_failed_archive_resumes_without_another_key_rotation(self):
        self.archive_failures = 1
        with self.assertRaisesRegex(bots.Stop, 'HTTP 400'):
            self.runner.refresh()
        self.assertEqual(len(self.created), 1)
        self.assertNotIn('archivedAt', self.project_rows[0])
        self.runner.refresh()
        self.assertEqual(len(self.created), 1)
        self.assertIsNotNone(self.project_rows[0]['archivedAt'])

    def test_runtime_repairs_preserve_native_customizations(self):
        self.agent['adapterConfig']['apiBaseUrl'] = 'http://127.0.0.1:1/p/scout'
        self.passthrough = ['CUSTOM_KEY']
        self.runner.refresh()
        self.assertEqual(self.agent['adapterConfig']['apiBaseUrl'], bots.bot_url(SITE, 'scout'))
        self.assertEqual(self.agent['adapterConfig']['apiKey']['secretId'], 'retained')
        self.assertEqual(self.agent['adapterConfig']['timeoutSec'], 123)
        self.assertIn('CUSTOM_KEY', self.passthrough)
        self.assertTrue(set(bots.TASK_ENV).issubset(self.passthrough))

    def test_open_work_prevents_archiving_retired_projects(self):
        self.open_issues = [{'status': 'todo'}]
        with self.assertRaisesRegex(bots.Stop, 'open tickets'):
            self.runner.refresh()
        self.assertNotIn('archivedAt', self.project_rows[0])
        self.assertEqual(self.created, [])

    def test_no_declared_migration_preserves_project_scope(self):
        self.runner.site['zelab_project_replacements'] = {}
        self.runner.refresh()
        self.assertEqual(self.created, [])
        self.assertEqual(self.calls, [])


class HostProjectsTest(unittest.TestCase):
    def test_sudo_fragment_follows_the_preset(self):
        module = host_projects()
        self.assertIsNone(module.sudoers_fragment('web', []))
        fragment = module.sudoers_fragment('root', ['ALL'])
        self.assertIn('zelab-team-root ALL=(root) NOPASSWD: ALL', fragment)
        self.assertTrue(fragment.startswith('# Zelab team tier root.'))

    def test_ssh_key_is_restricted_to_the_host_address(self):
        module = host_projects()
        self.assertEqual(module.authorized_keys('127.0.0.1', 'ssh-ed25519 AAAA zelab-p-x\n'),
                         'from="127.0.0.1",restrict ssh-ed25519 AAAA\n')
        self.assertEqual(module.allowusers({'b': {}, 'a': {}}, '127.0.0.1'),
                         '# Team tier accounts; managed by make.\n'
                         'AllowUsers zelab-team-a@127.0.0.1\nAllowUsers zelab-team-b@127.0.0.1\n')


if __name__ == '__main__':
    unittest.main()
