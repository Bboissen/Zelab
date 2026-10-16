"""make bot building blocks: names, the guest probe, Paperclip agent matching and the zelab skill."""

import importlib.util
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

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
                      'deploy/roles/paperclip/files/package.json', 'deploy/roles/paperclip/files/package-lock.json',
                      'deploy/roles/paperclip_mcp/files/package.json', 'deploy/roles/paperclip_mcp/files/package-lock.json')
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
        self.assertIn('| homelab | Homelab | `p1` |', text)
        self.assertIn('| market-study | Market Study | `p2` |', text)
        self.assertIn('| writer |  | `a2` |', text)
        self.assertNotIn('| scout |', text)
        self.assertNotIn('pcp_', text)
        self.assertIn('Every `paperclipCreateIssue` call includes `projectId`', text)

    def test_team_is_matched_by_metadata_and_only_the_lead_is_assignable(self):
        team = {'harness': 'claude', 'harnesses': {'claude': {'model': 'claude-opus-5-5', 'effort': 'high'}, 'codex': {'model': 'gpt-5.6-sol', 'effort': 'high'}}, 'tiers': {},
                'members': {'pm': {'title': 'Product Manager', 'role': 'pm', 'tier': 'dev', 'lead': True, 'harness': 'claude'},
                            'backend': {'title': 'Backend Engineer', 'role': 'engineer', 'tier': 'dev', 'lead': False,
                                        'harness': 'codex'}}}
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
        projects = [{'id': 'p1', 'name': 'My Project'}]
        text = bots.skill(site, 'marvin', {'marvin': {'id': 'b1'}}, projects, members, others)
        self.assertIn('Only **pm** takes work from you', text)
        self.assertIn('the right expert: helper.', text)
        self.assertIn('`assigneeAgentId` `h1`', text)
        self.assertIn('`blockedByIssueIds`', text)
        self.assertIn('without an assignee', bots.skill(site, 'marvin', {}, projects))

    def test_skill_without_a_key_or_projects_explains_setup_without_ticket_tools(self):
        site = {**SITE, 'zelab_team': {'members': {'pm': {'title': 'PM', 'lead': True}}}}
        members = {'pm': {'id': 'team-lead'}}
        for projects, task_key in (([{'id': 'p1', 'name': 'My Project'}], False), ([], True), ([], False)):
            with self.subTest(projects=projects, task_key=task_key):
                text = bots.skill(site, 'scout', {}, projects, members, task_key=task_key)
                self.assertNotIn('paperclipCreateIssue', text)
                self.assertNotIn('Create the job', text)
                self.assertNotIn('`p1`', text)
                self.assertIn('owner needs to give you a task key', text)
                self.assertIn('no terminal or files', text)
                self.assertIn('No commands', text)

    def test_chat_has_no_command_tools_and_reaches_paperclip_through_its_mcp_server(self):
        settings = bots.chat_settings()
        self.assertEqual({key.split('.', 1)[1] for key in settings if key.startswith('platform_toolsets.')},
                         {'discord', 'telegram', 'slack', 'mattermost', 'matrix', 'signal', 'whatsapp', 'email'})
        for platform in bots.CHAT_PLATFORMS:
            tools = settings[f'platform_toolsets.{platform}']
            self.assertIn('paperclip', tools)
            for forbidden in ('terminal', 'code_execution', 'file', 'browser', 'cronjob', 'delegation',
                              'kanban', 'computer_use', 'hermes-discord', 'hermes-api-server'):
                self.assertNotIn(forbidden, tools)
        self.assertNotIn('platform_toolsets.api_server', settings)
        server = settings['mcp_servers.paperclip']
        self.assertEqual(server['tools'], {'include': ['paperclipCreateIssue', 'paperclipGetIssue', 'paperclipListComments']})
        self.assertTrue(all(value.startswith('${') for value in server['env'].values()))
        self.assertEqual(server['env']['PAPERCLIP_API_KEY'], '${PAPERCLIP_BRIDGE_API_KEY}')
        self.assertTrue(server['args'][0].startswith(bots.MCP_DIR + '/'))
        self.assertEqual(settings['tools.tool_search.enabled'], 'off')
        self.assertEqual(settings['skills.auto_load'], ['zelab'])
        text = bots.skill(SITE, 'marvin', {}, [])
        for command in ('curl', 'create-task', 'update-status', ' send ', 'PAPERCLIP_RUN_ID'):
            self.assertNotIn(command, text)
        run = bots.instructions(SITE, 'marvin')
        self.assertIn('/profiles/marvin/skills/paperclip/paperclip-task-bridge/paperclip-task.mjs update-status '
                      '--issue <ticket id> --status done', run)
        self.assertIn('-p marvin send --to <Reply to> --file ./reply.txt --json', run)

    def test_mcp_server_is_the_paperclip_release(self):
        from run import load_yaml
        package = json.loads((bots.ROOT / 'deploy/roles/paperclip_mcp/files/package.json').read_text())
        lock = json.loads((bots.ROOT / 'deploy/roles/paperclip_mcp/files/package-lock.json').read_text())
        version = load_yaml(bots.ROOT / 'versions.yaml')['paperclip_npm_version']
        self.assertEqual(package['dependencies'], {'@paperclipai/mcp-server': version})
        self.assertEqual(lock['packages']['node_modules/@paperclipai/mcp-server']['version'], version)
        self.assertTrue(all(entry.get('resolved', '').startswith('https://registry.npmjs.org/') and entry.get('integrity')
                            for name, entry in lock['packages'].items() if name))

    def test_keyless_chat_disables_paperclip_but_keeps_command_tools_unavailable(self):
        settings = bots.chat_settings(task_key=False)
        self.assertEqual(settings['mcp_servers.paperclip'], {'enabled': False})
        for platform in bots.CHAT_PLATFORMS:
            tools = settings[f'platform_toolsets.{platform}']
            self.assertEqual(tools, [tool for tool in bots.CHAT_TOOLSETS if tool != 'paperclip'])
            self.assertNotIn('terminal', tools)

    def test_team_prefers_the_connected_shared_claude_connection(self):
        personal = {'id': 'c1', 'grantId': 'g1', 'provider': 'anthropic', 'method': 'subscription',
                    'ownership': 'personal', 'status': 'connected'}
        shared = {**personal, 'id': 'c2', 'grantId': 'g2', 'ownership': 'shared'}
        self.assertEqual(bots.team_binding([personal, {**shared, 'status': 'needs_attention'}]),
                         bots.initial_binding('anthropic'))
        self.assertEqual(bots.team_binding([personal, shared]),
                         {'provider': 'anthropic', 'method': 'subscription', 'mode': 'shared',
                          'connectionId': 'c2', 'grantId': 'g2'})
        codex = {**shared, 'id': 'c3', 'grantId': 'g3', 'provider': 'openai'}
        self.assertEqual(bots.team_bindings([shared, codex]), {
            'claude': bots.team_binding([shared]),
            'codex': {'provider': 'openai', 'method': 'subscription', 'mode': 'shared', 'connectionId': 'c3', 'grantId': 'g3'}})
        self.assertEqual(bots.team_bindings([shared]), {'claude': bots.team_binding([shared]),
                                                        'codex': bots.initial_binding('openai')})

    def test_sign_in_preserves_changed_harnesses_and_explicit_connections(self):
        connection = {'id': 'claude', 'grantId': 'grant', 'provider': 'anthropic',
                      'method': 'subscription', 'ownership': 'shared', 'status': 'connected'}
        codex = {**connection, 'id': 'codex', 'grantId': 'cg', 'provider': 'openai'}
        explicit = {**bots.initial_binding('anthropic'), 'mode': 'shared', 'connectionId': 'chosen', 'grantId': 'chosen-grant'}
        members = {
            'initial': {'id': 'initial', 'adapterType': 'claude_local',
                        'runtimeConfig': {'aiConnection': bots.initial_binding('anthropic'), 'heartbeat': {'enabled': False}}},
            'codex': {'id': 'codex', 'adapterType': 'codex_local',
                      'runtimeConfig': {'aiConnection': {'provider': 'openai', 'method': 'subscription',
                                                       'mode': 'shared', 'connectionId': 'codex', 'grantId': 'cg'}}},
            'codex-initial': {'id': 'codex-initial', 'adapterType': 'codex_local',
                              'runtimeConfig': {'aiConnection': bots.initial_binding('openai')}},
            'switching': {'id': 'switching', 'adapterType': 'codex_local',
                          'runtimeConfig': {'aiConnection': bots.initial_binding('anthropic')}},
            'explicit': {'id': 'explicit', 'adapterType': 'claude_local', 'runtimeConfig': {'aiConnection': explicit}},
            'legacy': {'id': 'legacy', 'adapterType': 'claude_local', 'runtimeConfig': {}},
            'other': {'id': 'other', 'adapterType': 'opencode_local',
                      'runtimeConfig': {'aiConnection': bots.initial_binding('anthropic')}},
        }
        calls = []

        class Paperclip:
            def ai_connections(self):
                return [connection, codex]

            def call(self, method, path, body):
                calls.append((method, path, body))
                members[path.rsplit('/', 1)[1]].update(body)

        runner = bots.Bots.__new__(bots.Bots)
        runner.paperclip = Paperclip()
        runner.team = lambda: (members, [])
        runner.bind_team()
        runner.bind_team()
        self.assertEqual(calls, [
            ('PATCH', '/agents/codex-initial', {'runtimeConfig': {'aiConnection': bots.team_binding([codex], 'openai')}}),
            ('PATCH', '/agents/initial', {
                'runtimeConfig': {'aiConnection': bots.team_binding([connection]), 'heartbeat': {'enabled': False}}})])

    def test_claude_connection_needing_sign_in_moves_members_to_the_connected_one(self):
        broken = {'id': 'old', 'grantId': 'old-grant', 'provider': 'anthropic', 'method': 'subscription',
                  'ownership': 'shared', 'status': 'needs_attention'}
        connected = {**broken, 'id': 'new', 'grantId': 'new-grant', 'status': 'connected'}
        stale = {**bots.initial_binding('anthropic'), 'mode': 'shared', 'connectionId': 'old', 'grantId': 'old-grant'}
        members = {'pm': {'id': 'pm', 'adapterType': 'claude_local', 'runtimeConfig': {'aiConnection': dict(stale)}},
                   'initial': {'id': 'initial', 'adapterType': 'claude_local',
                               'runtimeConfig': {'aiConnection': bots.initial_binding('anthropic')}}}
        calls = []

        class Paperclip:
            def ai_connections(self):
                return [broken, connected]

            def call(self, method, path, body):
                calls.append((method, path))
                members[path.rsplit('/', 1)[1]].update(body)

        runner = bots.Bots.__new__(bots.Bots)
        runner.paperclip = Paperclip()
        runner.team = lambda: (members, [])
        runner.bind_team(initial=False)
        runner.bind_team(initial=False)
        self.assertEqual(calls, [('PATCH', '/agents/pm')])
        self.assertEqual(members['pm']['runtimeConfig']['aiConnection'], bots.team_binding([connected]))
        self.assertEqual(members['initial']['runtimeConfig']['aiConnection'], bots.initial_binding('anthropic'))

    def test_signing_in_again_renews_the_existing_shared_connection(self):
        connections = [{'id': 'fine', 'provider': 'anthropic', 'method': 'subscription', 'ownership': 'shared',
                        'status': 'connected'},
                       {'id': 'expired', 'provider': 'anthropic', 'method': 'subscription', 'ownership': 'shared',
                        'status': 'needs_attention'},
                       {'id': 'codex', 'provider': 'openai', 'method': 'subscription', 'ownership': 'shared',
                        'status': 'needs_attention'}]
        bodies = []

        class Paperclip:
            company = 'c1'

            def ai_connections(self):
                return connections

            def call(self, method, path, body=None):
                bodies.append((path.rsplit('/', 1)[1], body))
                return {'command': 'true', 'sessionId': 's1'} if path.endswith('/attempts') else {'status': 'ready'}

        runner = bots.Bots.__new__(bots.Bots)
        runner.site = {**SITE, 'paperclip_user': 'zelab-paperclip', 'paperclip_home': '/var/lib/zelab-paperclip'}
        runner.paperclip = Paperclip()
        runner.bind_team = lambda: None
        with patch('builtins.input', side_effect=['claude', '']), patch.object(bots.subprocess, 'run'), \
                patch('sys.stdout'):
            runner.host_sign_in()
        self.assertEqual([path for path, _ in bodies], ['attempts', 'check', 'local'])
        self.assertTrue(all(body['connectionId'] == 'expired' for _, body in bodies))
        connections[1]['status'] = 'connected'
        bodies.clear()
        with patch('builtins.input', side_effect=['claude', '']), patch.object(bots.subprocess, 'run'), \
                patch('sys.stdout'):
            runner.host_sign_in()
        self.assertEqual({body['connectionId'] for _, body in bodies}, {'fine'})

    def test_a_terminated_lead_is_rehired_and_its_orphans_report_to_the_new_one(self):
        agents = [
            {'id': 'old-pm', 'name': 'pm', 'adapterType': 'claude_local', 'status': 'terminated',
             'metadata': {'zelab_team': 'pm'}},
            {'id': 'backend', 'name': 'backend', 'adapterType': 'claude_local', 'status': 'paused',
             'reportsTo': 'old-pm', 'metadata': {'zelab_team': 'backend'}},
            {'id': 'data', 'name': 'data', 'adapterType': 'claude_local', 'status': 'idle',
             'reportsTo': None, 'metadata': {'zelab_team': 'data'}},
            {'id': 'mobile', 'name': 'mobile', 'adapterType': 'claude_local', 'status': 'idle',
             'reportsTo': 'backend', 'metadata': {'zelab_team': 'mobile'}}]
        issues = [{'id': 'i1', 'identifier': 'ZEL-8', 'assigneeAgentId': 'old-pm', 'status': 'blocked'},
                  {'id': 'i2', 'identifier': 'ZEL-1', 'assigneeAgentId': 'old-pm', 'status': 'done'}]
        hired, patched = [], []

        class Paperclip:
            company = 'c1'
            ids = {'environments': {'dev host': 'env-dev'}}

            def agents(self):
                return agents

            def ai_connections(self):
                return []

            def hire(self, body):
                hired.append(body)
                agent = {**body, 'id': 'new-pm', 'status': 'idle'}
                agents.append(agent)
                return agent

            def call(self, method, path, body=None):
                if method == 'GET':
                    return issues
                patched.append((path, body))
                next(a for a in agents if a['id'] == path.rsplit('/', 1)[1]).update(body)

        runner = bots.Bots.__new__(bots.Bots)
        runner.site = {**SITE, 'zelab_tiers': {'dev': {'account': 'zelab-team-dev', 'preset': 'repo-dev', 'paths': []}},
                       'zelab_team': {'harness': 'claude', 'harnesses': {'claude': {'model': 'claude-opus-5-5', 'effort': 'high'}, 'codex': {'model': 'gpt-5.6-sol', 'effort': 'high'}}, 'members': {
                           'pm': {'title': 'Product Manager', 'role': 'pm', 'tier': 'dev', 'lead': True, 'harness': 'codex'},
                           'backend': {'title': 'Backend Engineer', 'role': 'engineer', 'tier': 'dev', 'lead': False,
                                       'harness': 'claude'},
                           'data': {'title': 'Data', 'role': 'researcher', 'tier': 'dev', 'lead': False, 'harness': 'claude'},
                           'mobile': {'title': 'Mobile', 'role': 'engineer', 'tier': 'dev', 'lead': False,
                                      'harness': 'claude'}}}}
        runner.paperclip = Paperclip()
        runner.team = lambda: bots.team_agents(runner.site, agents, Paperclip.ids)
        with patch('sys.stdout') as out:
            runner.hire_team({})
            runner.hire_team({})
        self.assertEqual([(body['name'], body['reportsTo']) for body in hired], [('pm', None)])
        self.assertEqual((hired[0]['adapterType'], hired[0]['adapterConfig'], hired[0]['runtimeConfig']['aiConnection']),
                         ('codex_local', {'engine': 'cli', 'model': 'gpt-5.6-sol', 'modelReasoningEffort': 'high',
                                          'dangerouslyBypassApprovalsAndSandbox': True},
                          bots.initial_binding('openai')))
        self.assertIn('runs you as Codex over SSH', hired[0]['instructionsBundle']['files']['AGENTS.md'])
        self.assertEqual(patched, [('/agents/backend', {'reportsTo': 'new-pm'})])
        self.assertEqual(agents[2]['reportsTo'], None)
        self.assertEqual(agents[3]['reportsTo'], 'backend')
        printed = ''.join(str(call.args[0]) for call in out.write.call_args_list if call.args)
        self.assertIn('ZEL-8', printed)
        self.assertNotIn('ZEL-1', printed)

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
                'zelab_team': {'members': {'pm': {'title': 'Product Manager', 'tier': 'root', 'lead': True, 'harness': 'claude'},
                                           'sysadmin': {'title': 'Linux Sysadmin', 'tier': 'root', 'lead': False,
                                                        'harness': 'claude'}}}}
        text = bots.team_instructions(site, 'sysadmin', {})
        persona = (bots.TEAM_DIR / 'sysadmin.md').read_text().strip()
        self.assertTrue(text.startswith(persona))
        self.assertIn((bots.TEAM_DIR / 'TEAM.md').read_text().strip(), text)
        self.assertIn('runs you as Claude Code over SSH on this host as `zelab-team-root`: unrestricted sudo', text)
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


class SetupFlowTest(unittest.TestCase):
    def test_setup_finishes_before_the_next_bot_or_host_sign_in(self):
        runner = Mock(spec=bots.Bots)
        runner.state.side_effect = [({}, {}), ({}, {}), ({'scout': {}}, {})]
        runner.add.side_effect = ['scout', 'writer']
        events = []
        runner.task_key.side_effect = lambda name: events.append(('key', name))
        runner.finish.side_effect = lambda: events.append(('finish',))
        runner.host_sign_in.side_effect = lambda: events.append(('host sign-in',))

        def setup(name, steps=None):
            events.append(('setup', name))
            bots.Bots.setup(runner, name, steps=steps)

        runner.setup.side_effect = setup
        # The native chat wizard's facts are irrelevant to this fixture.
        runner.guest = Mock()
        runner.guest.bots.return_value = []
        with patch('builtins.input', return_value='2'), redirect_stdout(io.StringIO()):
            bots.installer(runner)
        self.assertEqual(events, [('setup', 'scout'), ('key', 'scout'), ('finish',),
                                  ('setup', 'writer'), ('key', 'writer'), ('finish',),
                                  ('host sign-in',), ('finish',)])

    def test_skipped_setup_still_finishes_a_new_bot_before_returning_to_the_menu(self):
        runner = Mock(spec=bots.Bots)
        runner.state.return_value = ({}, {})
        runner.add.return_value = 'scout'
        with patch('builtins.input', side_effect=['1', KeyboardInterrupt]), \
                patch.object(bots, 'yes', return_value=False), redirect_stdout(io.StringIO()), \
                self.assertRaises(KeyboardInterrupt):
            bots.menu(runner)
        runner.finish.assert_called_once_with()
        runner.setup.assert_not_called()

    def test_skipping_all_setup_steps_still_refreshes_existing_context(self):
        runner = Mock(spec=bots.Bots)
        with redirect_stdout(io.StringIO()):
            bots.Bots.setup(runner, 'scout', steps='')
        runner.finish.assert_called_once_with()
        runner.task_key.assert_not_called()


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
        self.events = []
        self.bridge_ready, self.open_issues = True, []
        self.archive_failures = 0
        self.passthrough = [*bots.TASK_ENV, 'CUSTOM_KEY']
        self.chat = dict(bots.chat_settings())
        outer = self

        class Paperclip:
            company = 'c1'

            def projects(self):
                return outer.project_rows

            def ai_connections(self):
                return []

            def keys(self, agent):
                return outer.active

            def create_key(self, agent, scope):
                outer.created.append(scope)
                key = {'id': 'k-new', 'name': bots.KEY_NAME, 'createdAt': '2026-02-01', 'scope': scope}
                outer.active.append(key)
                return {**key, 'token': 'private-test-token'}

            def revoke(self, agent, key):
                outer.events.append(('revoke', key))
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
                    return json.dumps({'env_passthrough': outer.passthrough,
                                       'settings': {key: outer.chat.get(key) for key in json.loads(args[1])}})
                if code == bots.BRIDGE_READY:
                    outer.events.append(('verified', outer.bridge_ready))
                    return json.dumps(outer.bridge_ready)
                outer.writes[(code, args)] = stdin
                if code == bots.WRITE_FILE:
                    outer.events.append(('skill', stdin))
                if code == bots.SET_ENV:
                    outer.bridge_ready = True
                return ''

            def hermes(self, *args):
                outer.events.append(('config', args[-2]))
                outer.hermes_calls.append(args)
                if args[-2] == 'terminal.env_passthrough':
                    outer.passthrough = json.loads(args[-1])
                else:
                    outer.chat[args[-2]] = args[-1] if args[-1] == 'off' else json.loads(args[-1])
                return 0, ''

        self.runner.paperclip, self.runner.guest = Paperclip(), Guest()

    def test_keyless_profile_keeps_reconciliation_and_skill_without_key_writes_or_warning(self):
        self.profile['task_key'] = False
        self.active = [{'id': 'revoked', 'name': bots.KEY_NAME, 'revokedAt': 'date'},
                       {'id': 'unmanaged', 'name': 'Another key'}]
        self.passthrough = ['CUSTOM_KEY']
        self.agent['adapterConfig']['apiBaseUrl'] = 'http://127.0.0.1:1/p/scout'
        self.chat['platform_toolsets.discord'] = ['terminal']
        with patch.object(self.runner, 'write_key') as write_key, redirect_stdout(io.StringIO()) as output:
            self.runner.refresh()
        write_key.assert_not_called()
        self.assertEqual(self.created, [])
        self.assertFalse(any(code in (bots.SET_ENV, bots.BRIDGE_READY) for code, _ in self.writes))
        self.assertTrue(any(code == bots.COPY_SKILL for code, _ in self.writes))
        self.assertTrue(set(bots.TASK_ENV).issubset(self.passthrough))
        self.assertEqual(self.agent['adapterConfig']['apiBaseUrl'], bots.bot_url(SITE, 'scout'))
        self.assertEqual(self.chat, bots.chat_settings(task_key=False))
        text = next(value for (code, _), value in self.writes.items() if code == bots.WRITE_FILE)
        self.assertIn('cannot file tickets yet', text)
        self.assertNotIn('paperclipCreateIssue', text)
        self.assertEqual(output.getvalue(), '')

    def test_keyless_profile_does_not_stop_a_later_keyed_profile(self):
        self.runner.state = lambda: ({'alpha': {'task_key': False}, 'scout': self.profile},
                                    {'alpha': {'id': 'a0'}, 'scout': self.agent})
        keys = self.runner.paperclip.keys
        self.runner.paperclip.keys = lambda agent: [] if agent == 'a0' else keys(agent)
        self.runner.refresh()
        skills = {args[0]: value for (code, args), value in self.writes.items() if code == bots.WRITE_FILE}
        self.assertEqual(len(skills), 2)
        self.assertNotIn('paperclipCreateIssue', skills[f'{bots.HERMES_HOME}/profiles/alpha/skills/zelab/zelab/SKILL.md'])
        self.assertIn('`p-new`', skills[f'{bots.HERMES_HOME}/profiles/scout/skills/zelab/zelab/SKILL.md'])
        self.assertEqual(self.created[0]['allowedAssigneeAgentIds'], ['a0', 'a1'])

    def test_zero_projects_and_a_keyless_profile_finish(self):
        self.runner.site['zelab_project_replacements'] = {}
        self.project_rows, self.active = [], []
        self.profile['task_key'] = False
        self.runner.refresh()
        self.assertEqual(self.created, [])
        self.assertTrue(any(code == bots.WRITE_FILE for code, _ in self.writes))

    def test_task_key_skip_explains_chat_only_and_creates_no_key(self):
        self.runner.paperclip.agents = lambda: [{**self.agent, 'adapterType': 'hermes_gateway'}]
        with patch('builtins.input', return_value=''), patch.object(self.runner, 'write_key') as write_key, \
                redirect_stdout(io.StringIO()) as output:
            self.runner.task_key('scout')
        write_key.assert_not_called()
        self.assertEqual(self.created, [])
        self.assertIn('scout has no task key: it can chat but not file tickets. '
                      'Add one later with make bot → 2 → scout → d.', output.getvalue())

    def test_task_key_without_projects_points_to_the_optional_example(self):
        self.runner.paperclip.agents = lambda: [{**self.agent, 'adapterType': 'hermes_gateway'}]
        self.project_rows = []
        with patch('builtins.input', side_effect=AssertionError('No project to select')), \
                redirect_stdout(io.StringIO()) as output:
            self.runner.task_key('scout')
        self.assertEqual(self.created, [])
        self.assertIn('No Paperclip projects yet', output.getvalue())
        self.assertIn('copy config/projects.example.yaml to config/projects.yaml', output.getvalue())

    def test_writing_a_task_key_enables_paperclip_for_a_keyless_profile(self):
        self.active = []
        self.chat = bots.chat_settings(task_key=False)
        self.runner.write_key('scout', {'scout': self.agent}, ['p-new'])
        self.assertEqual(self.chat, bots.chat_settings())
        self.assertTrue(self.runner.restart_needed)
        self.assertEqual(len(self.created), 1)

    def test_task_key_publishes_only_selected_projects_before_enabling_tools(self):
        self.chat = bots.chat_settings(task_key=False)
        self.runner.site['zelab_team'] = {'members': {'pm': {'title': 'PM', 'lead': True}}}
        self.runner.team = lambda: ({'pm': {'id': 'lead'}}, [])
        self.runner.write_key('scout', {'scout': self.agent}, ['p-new'])
        text = next(value for (code, _), value in self.writes.items() if code == bots.WRITE_FILE)
        self.assertIn('`p-new`', text)
        self.assertNotIn('`p-old`', text)
        self.assertNotIn('`p-other`', text)
        self.assertIn('`assigneeAgentId` `lead`', text)
        stages = [event[0] for event in self.events]
        self.assertLess(stages.index('verified'), stages.index('skill'))
        self.assertLess(stages.index('skill'), stages.index('revoke'))
        self.assertLess(stages.index('skill'), stages.index('config'))
        self.assertNotIn('private-test-token', text)

    def test_add_writes_chat_only_instructions_before_any_setup_wizard(self):
        with patch('builtins.input', side_effect=['scout', 'Finds sources']), \
                patch.object(self.runner.guest, 'hermes', return_value=(0, '')), \
                patch.object(self.runner.paperclip, 'secret', return_value={'id': 'secret-ref'}, create=True), \
                patch.object(self.runner.paperclip, 'hire', create=True), \
                patch.object(self.runner.paperclip, 'agents',
                             return_value=[{**self.agent, 'adapterType': 'hermes_gateway'}], create=True), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(self.runner.add({'existing': {}}), 'scout')
        text = next(value for (code, _), value in self.writes.items() if code == bots.WRITE_FILE)
        self.assertIn('You are **scout**', text)
        self.assertIn('cannot file tickets yet', text)
        self.assertNotIn('paperclipCreateIssue', text)

    def test_invalid_projects_do_not_create_a_key_or_enable_tools(self):
        self.project_rows[1]['archivedAt'] = 'date'
        for project_ids in ([], ['missing'], ['p-new']):
            with self.subTest(project_ids=project_ids), self.assertRaises(bots.Stop):
                self.runner.write_key('scout', {'scout': self.agent}, project_ids)
        self.assertEqual(self.created, [])
        self.assertEqual(self.hermes_calls, [])

    def test_failed_skill_write_keeps_previous_keys_and_tools_disabled(self):
        self.chat = bots.chat_settings(task_key=False)
        original = self.runner.guest.root_python

        def fail_skill(code, *args, **kwargs):
            if code == bots.WRITE_FILE:
                raise bots.Stop('Skill write interrupted')
            return original(code, *args, **kwargs)

        with patch.object(self.runner.guest, 'root_python', side_effect=fail_skill), \
                self.assertRaisesRegex(bots.Stop, 'Skill write interrupted'):
            self.runner.write_key('scout', {'scout': self.agent}, ['p-new'])
        self.assertEqual(self.revoked, [])
        self.assertEqual(self.chat, bots.chat_settings(task_key=False))
        # Plain make completes the interrupted setup with the same selected scope.
        self.runner.refresh()
        text = next(value for (code, _), value in self.writes.items() if code == bots.WRITE_FILE)
        self.assertIn('`p-new`', text)
        self.assertEqual(self.chat, bots.chat_settings())

    def test_reapply_restores_missing_skill_without_rotating_a_valid_key(self):
        self.active[0]['scope']['projectIds'] = ['p-new']
        self.chat = bots.chat_settings(task_key=False)
        self.runner.refresh()
        text = next(value for (code, _), value in self.writes.items() if code == bots.WRITE_FILE)
        self.assertIn('`p-new`', text)
        self.assertEqual(self.created, [])
        stages = [event[0] for event in self.events]
        self.assertLess(stages.index('skill'), stages.index('config'))

    def test_reapply_auto_loads_workflow_and_preserves_other_native_skill_choices(self):
        self.active[0]['scope']['projectIds'] = ['p-new']
        self.chat['skills.auto_load'] = ['owner-workflow']
        self.runner.refresh()
        self.assertEqual(self.chat['skills.auto_load'], ['owner-workflow', 'zelab'])
        self.assertEqual(self.created, [])
        self.hermes_calls = []
        self.runner.refresh()
        self.assertEqual(self.hermes_calls, [])

    def test_refresh_still_stops_for_scope_decisions_and_unhired_profiles(self):
        scopes = ({'kind': 'custom'},
                  {'kind': 'task_bridge', 'projectIds': ['p-old'], 'custom': True},
                  {'kind': 'task_bridge', 'projectIds': ['missing']},
                  {'kind': 'task_bridge', 'projectIds': []})
        for scope in scopes:
            with self.subTest(scope=scope):
                self.active[0]['scope'] = scope
                with self.assertRaises(bots.Stop):
                    self.runner.refresh()
        self.runner.state = lambda: ({'scout': self.profile}, {})
        with self.assertRaisesRegex(bots.Stop, 'no Paperclip agent'):
            self.runner.refresh()

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
                        'runtimeConfig': {'aiConnection': bots.initial_binding('anthropic')}},
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

    def test_make_resets_chat_tools_and_restarts_once(self):
        self.chat.pop('mcp_servers.paperclip')
        self.chat['platform_toolsets.discord'] = ['hermes-discord']
        restarts = []
        self.runner.guest.root_python = (lambda original: lambda code, *args, stdin='':
                                         restarts.append(1) if code == bots.RESTART else original(code, *args, stdin=stdin)
                                         )(self.runner.guest.root_python)
        self.runner.refresh()
        self.assertEqual(self.chat, bots.chat_settings())
        self.assertEqual(len(restarts), 1)
        self.hermes_calls = []
        self.runner.refresh()
        self.assertEqual(self.hermes_calls, [])
        self.assertEqual(len(restarts), 1)

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
