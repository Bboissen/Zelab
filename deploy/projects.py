#!/usr/bin/env python3
"""Projects, team tiers and the default team: the inputs make turns into Paperclip and host accounts.

config/projects.yaml (local, kept out of Git) declares projects: a Paperclip
project and a vault folder Projects/<slug>/ each. Projects carry no host
rights. It may also override a tier's preset or paths for this site.

config/team.yaml (public) declares the rights tiers and the default team. A
tier is one host account zelab-team-<tier> with a preset (a fixed catalogue of
groups, a sudoers fragment and path rights), its SSH key and the Paperclip SSH
environment "<tier> host". A member is a Claude Code agent make hires on its
tier's environment, with config/team/<member>.md as its first instructions.
make applies both files on each run; nothing watches them in between.
"""

import argparse
import json
from pathlib import Path
import re
import sys

import yaml

SLUG = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?')
TIER = re.compile(r'[a-z][a-z0-9]{1,11}')
MEMBER = re.compile(r'[a-z][a-z0-9-]{0,22}[a-z0-9]')
# Paperclip's own agent roles.
ROLES = ('ceo', 'cto', 'cmo', 'cfo', 'security', 'engineer', 'designer', 'pm', 'qa', 'devops', 'researcher', 'general')
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
# Paths a preset may own: real directories that are not system trees.
FORBIDDEN_PATHS = ('/', '/bin', '/boot', '/dev', '/etc', '/home', '/lib', '/lib64', '/opt', '/proc', '/root',
                   '/run', '/sbin', '/srv', '/sys', '/tmp', '/usr', '/var', '/var/lib', '/var/log')
# journalctl and systemctl start a pager on a terminal; --no-pager keeps them
# from offering a shell through sudo.
MONITOR_COMMANDS = ('/usr/bin/systemctl --no-pager status *', '/usr/bin/journalctl --no-pager *',
                    '/usr/bin/docker ps *', '/usr/bin/docker stats --no-stream *', '/usr/bin/docker logs *')
PRESETS = {
    'monitor-readonly': {'groups': ['systemd-journal', 'adm'], 'sudo': MONITOR_COMMANDS, 'paths': 'r-x'},
    'docker': {'groups': ['systemd-journal', 'adm', 'docker'], 'sudo': MONITOR_COMMANDS, 'paths': 'rwx'},
    'repo-dev': {'groups': [], 'sudo': None, 'paths': 'rwx'},
    'full-sudo': {'groups': ['systemd-journal', 'adm'], 'sudo': ('ALL',), 'paths': 'rwx'},
}
LEGACY_PROJECT_KEYS = {'host_preset', 'paths', 'host_tickets'}


def account(tier):
    return f'zelab-team-{tier}'


def check_paths(where, paths):
    if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
        raise ValueError(f'{where}: paths must be a list of absolute directories')
    for path in paths:
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', path) or str(Path(path)) != path or '..' in Path(path).parts:
            raise ValueError(f'{where}: {path!r} must be a plain canonical absolute path')
        if path in FORBIDDEN_PATHS:
            raise ValueError(f'{where}: {path} is a system tree; name a dedicated directory')
    return sorted(set(paths))


def one_line(where, value, limit=80):
    if not isinstance(value, str) or not re.fullmatch(rf'[^\x00-\x1f]{{1,{limit}}}', value) or value != value.strip():
        raise ValueError(f'{where} must be one line of at most {limit} characters')
    return value


def tier_settings(where, settings, allowed):
    settings = settings or {}
    if not isinstance(settings, dict) or set(settings) - allowed:
        raise ValueError(f'{where}: only {", ".join(sorted(allowed))} are allowed')
    result = {}
    if 'preset' in settings:
        if settings['preset'] not in PRESETS:
            raise ValueError(f'{where}: preset must be one of {", ".join(PRESETS)}')
        result['preset'] = settings['preset']
    if 'paths' in settings:
        result['paths'] = check_paths(where, settings['paths'])
    return result


def validate(data):
    """Local projects, tier overrides and explicit retired-project replacements."""
    if data in (None, {}):
        return {'projects': {}, 'tiers': {}, 'project_replacements': {}}
    if not isinstance(data, dict) or not set(data) or set(data) - {'projects', 'tiers', 'project_replacements'}:
        raise ValueError('config/projects.yaml may contain only projects, tiers and project_replacements')
    projects = data.get('projects') or {}
    if not isinstance(projects, dict):
        raise ValueError('"projects:" must map project slugs to settings')
    result = {}
    for slug, settings in projects.items():
        if not isinstance(slug, str) or not SLUG.fullmatch(slug):
            raise ValueError(f'Project slug {slug!r} must be lowercase letters, digits and dashes (max 24)')
        settings = settings or {}
        if isinstance(settings, dict) and set(settings) & LEGACY_PROJECT_KEYS:
            raise ValueError(f'{slug}: host rights moved from projects to team tiers (config/team.yaml); '
                             'remove host_preset, paths and host_tickets, and put site paths under "tiers:"')
        if not isinstance(settings, dict) or set(settings) - {'name'}:
            raise ValueError(f'{slug}: only name is allowed')
        result[slug] = {'name': one_line(f'{slug}: name', settings.get('name', slug))}
    names = [project['name'].casefold() for project in result.values()]
    if len(names) != len(set(names)):
        raise ValueError('Two projects use the same name')
    tiers = data.get('tiers') or {}
    if not isinstance(tiers, dict):
        raise ValueError('"tiers:" must map tier names to overrides')
    overrides = {}
    for tier, settings in tiers.items():
        if not isinstance(tier, str) or not TIER.fullmatch(tier):
            raise ValueError(f'Tier {tier!r} must be 2-12 lowercase letters and digits')
        overrides[tier] = tier_settings(f'tier {tier}', settings, {'preset', 'paths'})
    replacements = data.get('project_replacements', {})
    if not isinstance(replacements, dict):
        raise ValueError('project_replacements must map retired slugs to declared project slugs')
    for old, new in replacements.items():
        if not isinstance(old, str) or not SLUG.fullmatch(old) or old in result:
            raise ValueError('project_replacements sources must be retired project slugs')
        if not isinstance(new, str) or new not in result:
            raise ValueError('project_replacements targets must be declared projects (no chains)')
    return {'projects': result, 'tiers': overrides, 'project_replacements': replacements}


def validate_team(data, persona_dir=None):
    """config/team.yaml: model, effort, tiers and members; raises ValueError on anything unclear."""
    if not isinstance(data, dict) or set(data) - {'model', 'effort', 'tiers', 'members'}:
        raise ValueError('config/team.yaml may contain only model, effort, tiers and members')
    model = data.get('model', 'claude-opus-5-5')
    if not isinstance(model, str) or not re.fullmatch(r'[a-z0-9][a-z0-9.\-\[\]]{2,63}', model):
        raise ValueError('team model must be a Claude model ID such as claude-opus-5-5')
    effort = data.get('effort', 'high')
    if effort not in EFFORTS:
        raise ValueError(f'team effort must be one of {", ".join(EFFORTS)}')
    tiers = {}
    for tier, settings in (data.get('tiers') or {}).items():
        if not isinstance(tier, str) or not TIER.fullmatch(tier):
            raise ValueError(f'Tier {tier!r} must be 2-12 lowercase letters and digits')
        tiers[tier] = {'paths': [], **tier_settings(f'tier {tier}', settings, {'preset', 'paths'})}
        if 'preset' not in tiers[tier]:
            raise ValueError(f'tier {tier}: preset is required')
    members, leads = {}, []
    for name, settings in (data.get('members') or {}).items():
        if not isinstance(name, str) or not MEMBER.fullmatch(name):
            raise ValueError(f'Member {name!r} must be 2-24 lowercase letters, digits and dashes')
        if not isinstance(settings, dict) or set(settings) - {'title', 'role', 'tier', 'lead'}:
            raise ValueError(f'{name}: only title, role, tier and lead are allowed')
        if settings.get('role', 'engineer') not in ROLES:
            raise ValueError(f'{name}: role must be one of {", ".join(ROLES)}')
        if settings.get('tier') not in tiers:
            raise ValueError(f'{name}: tier must be one of {", ".join(tiers) or "the declared tiers"}')
        if not isinstance(settings.get('lead', False), bool):
            raise ValueError(f'{name}: lead must be true or false')
        if persona_dir is not None and not (Path(persona_dir) / f'{name}.md').is_file():
            raise ValueError(f'{name}: its persona {Path(persona_dir) / (name + ".md")} is missing')
        members[name] = {'title': one_line(f'{name}: title', settings.get('title', name)),
                         'role': settings.get('role', 'engineer'), 'tier': settings['tier'],
                         'lead': settings.get('lead', False)}
        if members[name]['lead']:
            leads.append(name)
    if len(leads) > 1:
        raise ValueError(f'Only one member can lead the team, not {", ".join(leads)}')
    return {'model': model, 'effort': effort, 'tiers': tiers, 'members': members}


def load(path):
    path = Path(path)
    return yaml.safe_load(path.read_text()) if path.exists() else None


def read(path):
    return validate(load(path))


def read_team(path):
    path = Path(path)
    if not path.exists():
        return validate_team({})
    return validate_team(load(path) or {}, path.parent / 'team')


def tiers(team, local):
    """The team's tiers with this site's overrides, expanded with their preset's groups, sudo and rights."""
    unknown = set(local['tiers']) - set(team['tiers'])
    if unknown:
        raise ValueError(f'config/projects.yaml overrides unknown tier(s): {", ".join(sorted(unknown))}')
    expanded = {}
    for tier, cfg in team['tiers'].items():
        cfg = {**cfg, **local['tiers'].get(tier, {})}
        preset = PRESETS[cfg['preset']]
        expanded[tier] = {'preset': cfg['preset'], 'paths': cfg['paths'], 'account': account(tier),
                          'groups': list(preset['groups']), 'sudo': list(preset['sudo'] or []),
                          'paths_rights': preset['paths']}
    return expanded


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--team', type=Path, default=Path(__file__).resolve().parents[1] / 'config/team.yaml')
    parser.add_argument('--json', action='store_true', help='print the normalized inputs as JSON')
    args = parser.parse_args(argv)
    try:
        local, team = read(args.path), read_team(args.team)
        result = {'projects': local['projects'], 'tiers': tiers(team, local), 'team': team,
                  'project_replacements': local['project_replacements']}
    except (ValueError, yaml.YAMLError) as error:
        print(f'{error}', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True) if args.json else
          f'{len(result["projects"])} project(s), {len(result["tiers"])} tier(s), '
          f'{len(team["members"])} team member(s) valid')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
