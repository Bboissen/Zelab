#!/usr/bin/env python3
"""config/projects.yaml: the projects make creates, and their host rights.

Each project gets a Paperclip project and a vault folder Projects/<slug>/.
A project with a host_preset other than none also gets the host account
zelab-p-<slug>, its SSH key and the Paperclip SSH environment "<slug> host",
where the owner hires a Codex or Claude Code agent. Presets are a fixed
catalogue: groups, a sudoers fragment and owned paths. host_tickets says who
assigns host tickets: the owner (default) or the bots holding a key for the
project. make applies the file on each run; nothing watches it in between.
"""

import argparse
import json
from pathlib import Path
import re
import sys

import yaml

SLUG = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?')
# Paths a preset may own: real directories that are not system trees.
FORBIDDEN_PATHS = ('/', '/bin', '/boot', '/dev', '/etc', '/home', '/lib', '/lib64', '/opt', '/proc', '/root',
                   '/run', '/sbin', '/srv', '/sys', '/tmp', '/usr', '/var', '/var/lib', '/var/log')
# journalctl and systemctl start a pager on a terminal; --no-pager keeps them
# from offering a shell through sudo.
MONITOR_COMMANDS = ('/usr/bin/systemctl --no-pager status *', '/usr/bin/journalctl --no-pager *',
                    '/usr/bin/docker ps *', '/usr/bin/docker stats --no-stream *', '/usr/bin/docker logs *')
PRESETS = {
    'none': {'account': False, 'groups': [], 'sudo': None, 'paths': None},
    'monitor-readonly': {'account': True, 'groups': ['systemd-journal', 'adm'], 'sudo': MONITOR_COMMANDS,
                         'paths': 'r-x'},
    'docker': {'account': True, 'groups': ['systemd-journal', 'adm', 'docker'], 'sudo': MONITOR_COMMANDS,
               'paths': 'rwx'},
    'repo-dev': {'account': True, 'groups': [], 'sudo': None, 'paths': 'rwx'},
    'full-sudo': {'account': True, 'groups': ['systemd-journal', 'adm'], 'sudo': ('ALL',), 'paths': 'rwx'},
}
# Who assigns tickets to the project's host agents.
HOST_TICKETS = ('owner', 'bots')


def account(slug):
    return f'zelab-p-{slug}'


def validate(data):
    """Normalized {slug: {name, host_preset, paths, host_tickets}}; raises ValueError on anything unclear."""
    if data in (None, {}):
        return {}
    if not isinstance(data, dict) or set(data) != {'projects'}:
        raise ValueError('config/projects.yaml must contain only a "projects:" mapping')
    projects = data['projects'] or {}
    if not isinstance(projects, dict):
        raise ValueError('"projects:" must map project slugs to settings')
    result = {}
    for slug, settings in projects.items():
        if not isinstance(slug, str) or not SLUG.fullmatch(slug):
            raise ValueError(f'Project slug {slug!r} must be lowercase letters, digits and dashes (max 24)')
        settings = settings or {}
        if not isinstance(settings, dict) or set(settings) - {'name', 'host_preset', 'paths', 'host_tickets'}:
            raise ValueError(f'{slug}: only name, host_preset, paths and host_tickets are allowed')
        name = settings.get('name', slug)
        if not isinstance(name, str) or not re.fullmatch(r'[^\x00-\x1f]{1,80}', name) or name != name.strip():
            raise ValueError(f'{slug}: name must be one line of at most 80 characters')
        preset = settings.get('host_preset', 'none')
        if preset not in PRESETS:
            raise ValueError(f'{slug}: host_preset must be one of {", ".join(PRESETS)}')
        paths = settings.get('paths', [])
        if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
            raise ValueError(f'{slug}: paths must be a list of absolute directories')
        for path in paths:
            if not re.fullmatch(r'/[A-Za-z0-9_./-]+', path) or str(Path(path)) != path or '..' in Path(path).parts:
                raise ValueError(f'{slug}: {path!r} must be a plain canonical absolute path')
            if path in FORBIDDEN_PATHS:
                raise ValueError(f'{slug}: {path} is a system tree; name a dedicated directory')
        if paths and preset == 'none':
            raise ValueError(f'{slug}: paths need a host_preset other than none')
        host_tickets = settings.get('host_tickets', 'owner')
        if host_tickets not in HOST_TICKETS:
            raise ValueError(f'{slug}: host_tickets must be owner or bots')
        if host_tickets == 'bots' and preset == 'none':
            raise ValueError(f'{slug}: host_tickets: bots needs a host_preset other than none')
        result[slug] = {'name': name, 'host_preset': preset, 'paths': sorted(set(paths)), 'host_tickets': host_tickets}
    names = [project['name'].casefold() for project in result.values()]
    if len(names) != len(set(names)):
        raise ValueError('Two projects use the same name')
    return result


def read(path):
    path = Path(path)
    if not path.exists():
        return {}
    return validate(yaml.safe_load(path.read_text()))


def host_projects(projects):
    return {slug: cfg for slug, cfg in projects.items() if PRESETS[cfg['host_preset']]['account']}


def with_presets(projects):
    """The projects with their preset's account, groups, sudo commands and path rights."""
    expanded = {}
    for slug, cfg in projects.items():
        preset = PRESETS[cfg['host_preset']]
        expanded[slug] = {**cfg, 'account': preset['account'], 'groups': list(preset['groups']),
                          'sudo': list(preset['sudo'] or []), 'paths_rights': preset['paths'] or 'r-x'}
    return expanded


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--json', action='store_true', help='print the normalized projects as JSON')
    args = parser.parse_args(argv)
    try:
        projects = read(args.path)
    except (ValueError, yaml.YAMLError) as error:
        print(f'{args.path}: {error}', file=sys.stderr)
        return 1
    print(json.dumps(projects, sort_keys=True) if args.json else f'{len(projects)} project(s) valid')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
