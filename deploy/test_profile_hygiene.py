"""Deployment context changes use native session boundaries without deleting history."""

import json
from pathlib import Path
import tempfile
import unittest

import profile_hygiene


class ContextTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.profile = self.home / 'profiles/scout'
        self.profile.mkdir(parents=True)
        (self.profile / 'config.yaml').write_text('model: example\n')
        (self.profile / 'SOUL.md').write_text('Scout\n')
        self.history = {'chat': [{'role': 'user', 'content': 'check the service'}]}
        self.sessions = [{'id': 'chat', 'source': 'discord', 'end_reason': None},
                         {'id': 'paperclip', 'source': 'api_server', 'end_reason': None}]
        self.calls, self.busy, self.fail = [], False, False
        outer = self

        class API:
            def __init__(self, profile, port):
                self.name = profile.name

            def idle(self):
                if outer.busy:
                    raise ValueError('busy')

            def chats(self):
                return (row for row in outer.sessions if row['source'] in profile_hygiene.CHAT_SOURCES
                        and not row['end_reason'])

            def request(self, method, path, body=None):
                if outer.fail:
                    raise ValueError('interrupted')
                outer.calls.append((method, path, body))
                sid = path.rsplit('/', 1)[1]
                next(row for row in outer.sessions if row['id'] == sid)['end_reason'] = body['end_reason']

        self.client = API

    def run_hygiene(self, pins='pins', action='apply'):
        return profile_hygiene.reconcile(self.home, pins, action, self.client)

    def test_first_adoption_ends_chats_but_keeps_history_and_paperclip_sessions(self):
        self.assertEqual(self.run_hygiene(), {'changed': 1, 'ended': 1})
        self.assertEqual(self.calls, [('PATCH', '/api/sessions/chat', {'end_reason': 'session_reset'})])
        self.assertEqual(self.sessions[1]['end_reason'], None)
        self.assertEqual(self.history['chat'][0]['content'], 'check the service')
        receipt = self.home / '.zelab-profile-context.json'
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        before = receipt.stat().st_mtime_ns
        self.assertEqual(self.run_hygiene(), {'changed': 0, 'ended': 0})
        self.assertEqual(receipt.stat().st_mtime_ns, before)

    def test_persona_skill_and_pin_changes_refresh_once(self):
        self.run_hygiene()
        changes = [(self.profile / 'SOUL.md', 'a new persona'),
                   (self.profile / 'skills/zelab/zelab/SKILL.md', 'new project scope'),
                   (self.profile / 'skills/paperclip/paperclip-task-bridge/paperclip-task.mjs', 'upstream helper')]
        for path, content in changes:
            with self.subTest(path=path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                self.assertEqual(self.run_hygiene()['changed'], 1)
                self.assertEqual(self.run_hygiene()['changed'], 0)
        self.assertEqual(self.run_hygiene('new-pins')['changed'], 1)
        self.assertEqual(self.run_hygiene('new-pins')['changed'], 0)

    def test_idle_check_and_failed_refresh_leave_no_success_receipt(self):
        self.assertEqual(self.run_hygiene(action='check'), {'changed': 0, 'ended': 0})
        self.assertFalse((self.home / '.zelab-profile-context.json').exists())
        for flag in ('busy', 'fail'):
            setattr(self, flag, True)
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                self.run_hygiene()
            self.assertFalse((self.home / '.zelab-profile-context.json').exists())
            self.assertEqual(self.sessions[0]['end_reason'], None)
            setattr(self, flag, False)
        self.assertEqual(self.run_hygiene()['ended'], 1)

    def test_native_session_listing_paginates_deduplicates_and_skips_ended_rows(self):
        api = profile_hygiene.Hermes.__new__(profile_hygiene.Hermes)
        pages = [{'data': [{'id': 'old', 'source': 'discord', 'end_reason': 'session_reset'},
                           {'id': 'pin', 'source': 'discord'}, {'id': 'pc', 'source': 'api_server'}], 'has_more': True},
                 {'data': [{'id': 'pin', 'source': 'discord'}, {'id': 'chat', 'source': 'telegram'}], 'has_more': False}]
        calls = []

        def request(method, path):
            calls.append(path)
            return pages.pop(0)

        api.request = request
        self.assertEqual([row['id'] for row in api.chats()], ['pin', 'chat'])
        self.assertIn('offset=200', calls[1])

    def test_secrets_are_not_written_to_context_receipt(self):
        (self.profile / 'config.yaml').write_text('model:\n  api_key: PRIVATE-TEST-VALUE\n')
        self.run_hygiene()
        text = (self.home / '.zelab-profile-context.json').read_text()
        self.assertNotIn('PRIVATE', text)
        self.assertEqual(len(json.loads(text)['scout']), 64)


if __name__ == '__main__':
    unittest.main()
