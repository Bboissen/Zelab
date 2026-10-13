#!/usr/bin/env python3
"""Qualify native bridge isolation and retry behavior with disposable inert agents.

Uses the selected installed Paperclip runtime and a separate application home,
database and ports. No production credentials, inference or SSH workers are used.
"""

import argparse
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

import check_paperclip as native

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from filter_plugins.bridge_security import submission_contract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--node', required=True, type=Path)
    parser.add_argument('--user', help='Drop root to this existing service account for embedded PostgreSQL')
    args = parser.parse_args()
    runtime, node = args.runtime.resolve(), args.node.resolve()
    repo = Path(__file__).resolve().parents[1]
    template = (repo / 'deploy/roles/paperclip/templates/config.json.j2').read_text()
    client = (repo / 'deploy/roles/hermes_bridge/files/submit-task.mjs').read_bytes()

    def configure(root, api_port, db_port):
        instance = root / 'instances/default'
        (instance / 'secrets').mkdir(parents=True, exist_ok=True)
        (instance / 'secrets/master.key').write_text(secrets.token_hex(32) + '\n')
        renderer = native.Environment(undefined=native.StrictUndefined)
        renderer.filters['to_json'] = json.dumps
        (instance / 'config.json').write_text(renderer.from_string(template).render(
            paperclip_home=str(root), paperclip_db_port=db_port, paperclip_port=api_port,
            paperclip_allowed_hosts=['127.0.0.1']))

    native.configure = configure
    if os.geteuid() == 0:
        if not args.user:
            parser.error('Embedded PostgreSQL requires --user when launched as root')
        account = pwd.getpwnam(args.user)
        if account.pw_uid == 0:
            parser.error('The fixture account must be non-root')
        os.chdir(tempfile.gettempdir())
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix='zelab-native-bridge-check-'))
    try:
        (root / 'submit-task.mjs').write_bytes(client)
        with native.server(runtime, node, root, signup=True) as (owner, environment):
            company, _ = native.seed(owner, runtime, node, root, environment, secrets.token_urlsafe(32))
            owner.request('/companies/' + company, {'requireBoardApprovalForNewAgents': False}, method='PATCH')
            projects = [owner.request('/companies/' + company + '/projects', {'name': name, 'status': 'in_progress'})
                        for name in ['Allowed', 'Outside']]
            agents = [owner.request('/companies/' + company + '/agents', {
                'name': name, 'role': 'engineer', 'adapterType': 'process',
                'adapterConfig': {'command': '/usr/bin/false'},
                'runtimeConfig': {'heartbeat': {'enabled': False, 'wakeOnDemand': False, 'maxConcurrentRuns': 1}},
                'permissions': {'canCreateAgents': False, 'canCreateSkills': False,
                                'canAssignTasks': True, 'trustPreset': 'standard'},
            }) for name in ['Submission', 'Inert Operations']]
            key_record = owner.request('/agents/' + agents[0]['id'] + '/keys', {
                'name': 'Synthetic scoped key', 'scope': {'kind': 'task_bridge',
                    'projectIds': [projects[0]['id']], 'allowedAssigneeAgentIds': [agents[1]['id']]},
            })
            key = key_record['token']
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def read(path, expected):
                request = urllib.request.Request(owner.base + '/api' + path,
                                                 headers={'Authorization': 'Bearer ' + key})
                try:
                    with opener.open(request, timeout=10) as response:
                        status, body = response.status, json.load(response)
                except urllib.error.HTTPError as error:
                    status, body = error.code, {}
                assert status == expected, f'Unexpected native status for {path.split("/")[1]}: {status}'
                return body

            outside = owner.request('/companies/' + company + '/issues', {
                'title': 'Synthetic unrelated Operations task', 'projectId': projects[1]['id'],
                'assigneeAgentId': agents[1]['id'], 'status': 'backlog'})
            assert submission_contract(owner.request('/agents/' + agents[0]['id']), company)
            read('/issues/' + outside['id'], 403)
            read('/companies/' + company + '/issues', 403)
            env = dict(os.environ, PAPERCLIP_API_URL=owner.base, PAPERCLIP_BRIDGE_API_KEY=key,
                       PAPERCLIP_COMPANY_ID=company, PAPERCLIP_AGENT_ID=agents[0]['id'],
                       HERMES_SESSION_PLATFORM='discord', HERMES_SESSION_MESSAGE_ID='123456789012345678')
            argv = ['rtk', 'proxy', str(node), str(root / 'submit-task.mjs'), '--title', 'Synthetic replay',
                    '--project-id', projects[0]['id'], '--assignee-agent-id', agents[1]['id']]

            def submit(message):
                result = subprocess.run(argv, env={**env, 'HERMES_SESSION_MESSAGE_ID': message},
                                        capture_output=True, text=True, cwd=root, timeout=40)
                assert result.returncode == 0, 'Native task client failed; fixture output withheld'
                return json.loads(result.stdout)

            first = submit('123456789012345678')
            second = submit('123456789012345678')
            assert first['id'] == second['id'] and second['reused']
            read('/issues/' + first['id'], 200)
            owner.request('/issues/' + first['id'] + '/comments', {'body': 'Synthetic host result'})
            comments = read('/issues/' + first['id'] + '/comments', 200)
            assert any(comment['body'] == 'Synthetic host result' for comment in comments)
            migration_query = ('/companies/' + company + '/issues?originKind=task_bridge&originId='
                               + key_record['id'] + '&status=backlog,todo,in_progress,in_review,blocked'
                               + '&limit=1&includeRoutineExecutions=true&includePluginOperations=true')
            assert len(owner.request(migration_query)) == 1
            owner.request('/issues/' + first['id'], {'status': 'done'}, method='PATCH')
            assert owner.request(migration_query) == []
            completed_retry = submit('123456789012345678')
            assert completed_retry['id'] == first['id'] and completed_retry['reused']
            new_event = submit('123456789012345679')
            assert new_event['id'] != first['id']
            print('PASS: unrelated work denied; submitted task/results readable; unfinished-origin query correct; retries reuse completed tasks; new events create work.')
        return 0
    finally:
        shutil.rmtree(root)


if __name__ == '__main__':
    raise SystemExit(main())
