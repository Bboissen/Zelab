"""Shared test fixtures: throwaway local plays, fake tool output and a loopback API. Not a test module."""

import os
from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / 'deploy/ansible.cfg'),
                   ANSIBLE_ROLES_PATH=str(ROOT / 'deploy/roles'))


def flatten(tasks):
    for task in tasks or []:
        yield task
        yield from flatten(task.get('block'))


def role_tasks(path, *names):
    """Tasks of a repository task file, or the named ones (also inside blocks) in that order."""
    tasks = yaml.safe_load((ROOT / path).read_text())
    if not names:
        return tasks
    found = {task.get('name'): task for task in flatten(tasks)}
    missing = [name for name in names if name not in found]
    if missing:
        raise LookupError(f'{path} has no task named {missing}')
    return [found[name] for name in names]


def run_play(directory, *, tasks=(), roles=(), variables=None, host='localhost'):
    """Apply tasks/roles to the local host as the current user; returns the completed process."""
    playbook = Path(directory) / 'fixture-play.yaml'
    playbook.write_text(yaml.safe_dump([{
        'hosts': host, 'connection': 'local', 'gather_facts': False, 'become': False,
        'vars': variables or {}, 'tasks': list(tasks), 'roles': list(roles)}]))
    return subprocess.run([str(ROOT / '.venv/bin/ansible-playbook'), '-i', host + ',',
                           str(playbook)], capture_output=True, text=True, env=ENVIRONMENT)


def replay(lines, code=0):
    """A stand-in for Installer.capture that feeds fixed tool output to its line handler."""
    def capture(argv, on_line=None, env=None, **kwargs):
        for line in lines:
            on_line(line)
        return code, []
    return capture


def serve(test, handler):
    """Start a loopback HTTP fixture for one test; returns its port. Stopped at cleanup."""
    from http.server import ThreadingHTTPServer
    import threading
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        server.shutdown()
        server.server_close()
        thread.join()
    test.addCleanup(stop)
    return server.server_port
