#!/usr/bin/env python3
"""Internal implementations of the small Make maintenance interface."""

import argparse
from contextlib import nullcontext
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import controller
from console import heading, mark
import shell

ROOT = Path(__file__).resolve().parents[1]

# Choices for this deployment, distinct from host connections and installed
# resource names/locations needed to find the same site after checkout cleanup.
REPO_CHOICES = frozenset({
    'worker_harness', 'worker_model', 'worker_codex_model', 'hermes_initial_model',
    'vm_disk_gib', 'vm_cpu', 'vm_memory_mb', 'vm_cpu_quota', 'vm_memory_max',
    'vm_install_resource_fraction', 'host_disk_reserve_gib', 'host_memory_reserve_gib',
    'hermes_reserve_count', 'notes_enabled', 'obsidian_enabled', 'notes_shared_profiles',
    'discord_owner_id', 'discord_channel_id', 'research_initial_allowed_user',
    'research_initial_allowed_channel', 'research_profile_parked',
    'research_disabled_toolsets', 'research_profile_description',
})
INPUT_KEY = re.compile(r'''^(?:([A-Za-z_][A-Za-z0-9_]*)|'([A-Za-z_][A-Za-z0-9_]*)'|"([A-Za-z_][A-Za-z0-9_]*)")\s*:''')


def choice_reset(root, site_config):
    """Plan a local mapping edit without requiring the disposable controller."""
    root = root.resolve()
    path = site_config if site_config.is_absolute() else root / site_config
    if not path.is_relative_to(root):
        return None
    if path.is_symlink() or path.resolve() != path:
        raise ValueError('Site inputs use a symlink alias; nothing was removed')
    if not path.exists():
        return None
    # Source defaults/examples are never cleanup inputs, even when selected.
    if path in (root / 'config/defaults.yaml', root / 'config/site.example.yaml', root / 'versions.yaml'):
        return None
    if not path.is_file():
        raise ValueError('Site inputs are not a regular file; nothing was removed')
    tracked = subprocess.run(['rtk', 'proxy', 'git', '-c', f'safe.directory={root}',
                              'ls-files', '--error-unmatch', '--',
                              str(path.relative_to(root))], cwd=root, capture_output=True)
    if tracked.returncode == 0:
        return None
    original = path.read_text()
    retained, reset, removing, seen_key = [], [], False, False
    # Site inputs use a top-level block mapping. Refuse merges/anchors/flow
    # mappings rather than risk changing a shared value or leaving a choice set.
    for line in original.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped or line.lstrip().startswith('#'):
            retained.append(line)
            continue
        if line[0].isspace():
            if not seen_key:
                raise ValueError('Use unindented top-level site input keys before cleanup; nothing was removed')
            if removing and '&' in stripped.split('#', 1)[0]:
                raise ValueError('Site inputs contain YAML anchors; nothing was removed')
            if not removing:
                retained.append(line)
            continue
        if stripped in ('---', '...'):
            raise ValueError('Use one plain site input mapping before cleanup; nothing was removed')
        found = INPUT_KEY.match(line)
        if not found:
            raise ValueError('Use plain top-level site input keys before cleanup; nothing was removed')
        key = next(value for value in found.groups() if value is not None)
        seen_key = True
        value = line[found.end():].split('#', 1)[0]
        if '&' in value:
            raise ValueError('Site inputs contain YAML anchors; nothing was removed')
        removing = key in REPO_CHOICES
        if removing:
            reset.append(key)
        else:
            retained.append(line)
    return (path, ''.join(retained), reset) if reset else None


def privileged_python(python, script, *args):
    terminal_env = [f'{name}={os.environ[name]}' for name in ('NO_COLOR', 'CLICOLOR_FORCE') if name in os.environ]
    return shell.rtk(*shell.as_root(*(['env', *terminal_env] if terminal_env else []),
                                    python, '-B', script, *args, prompt=sys.stdin.isatty()))


def clean(root=ROOT, site_config=Path('config/site.yaml')):
    sys.path.insert(0, str(ROOT / 'implementation'))
    from runtime import MAINTENANCE_LOCK, maintenance_lock
    guard = maintenance_lock() if root.resolve() == ROOT and MAINTENANCE_LOCK.exists() else nullcontext()
    try:
        with guard:
            clean_files(root, site_config)
    except PermissionError:
        if root.resolve() != ROOT or os.geteuid() == 0:
            raise
        # Elevated lifecycle commands can leave root-owned bytecode behind.
        # Release the lock before retrying; the child reacquires it and repeats
        # all generated-directory checks rather than deleting arbitrary paths.
        python = getattr(sys, '_base_executable', sys.executable)
        result = subprocess.run(privileged_python(python, Path(__file__).resolve(), 'clean',
                                                  '--site-config', str(site_config)), cwd=ROOT)
        if result.returncode:
            raise RuntimeError('Generated-file cleanup failed; check administrator access, then rerun rtk proxy make clean')


def clean_files(root, site_config=Path('config/site.yaml')):
    """Reset local deployment choices and remove controller/bytecode files."""
    root = root.resolve()
    reset = choice_reset(root, site_config)
    paths = [path for name in ('deploy', 'implementation', 'tools')
             for path in (root / name).rglob('__pycache__')]
    venv = root / '.venv'
    if venv.exists() or venv.is_symlink():
        if venv.is_symlink() or not (venv / 'pyvenv.cfg').is_file():
            raise ValueError('The checkout .venv is not a generated virtual environment; nothing was removed')
        paths.append(venv)
    for path in paths:
        if path.is_symlink() or root.resolve() not in path.resolve().parents:
            raise ValueError('A generated directory has an unsafe symlink alias; nothing was removed')
    # Check before deleting anything so a permission retry cannot encounter a
    # half-removed virtual environment whose pyvenv.cfg is already gone.
    def inaccessible(error):
        raise error

    for path in paths:
        if not os.access(path.parent, os.W_OK | os.X_OK):
            raise PermissionError(f'Cannot remove generated directory: {path}')
        for directory, _dirs, _files in os.walk(path, onerror=inaccessible):
            if not os.access(directory, os.W_OK | os.X_OK):
                raise PermissionError(f'Cannot clear generated directory: {directory}')
    if reset and not os.access(reset[0], os.W_OK):
        raise PermissionError('Cannot reset repository deployment choices')
    heading('Checkout cleanup')
    if reset:
        path, content, keys = reset
        path.write_text(content)
        print(f'  {mark("PASS")} Repository choices reset: {", ".join(sorted(set(keys)))}')
    for path in paths:
        shutil.rmtree(path)
    print(f'  {mark("PASS")} Generated controller and Python caches removed. Host settings, installation locations and installed data kept.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['clean', 'fclean', 'backup', 'restore'])
    parser.add_argument('--site-config', type=Path, default=Path('config/site.yaml'))
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--confirm', default='')
    parser.add_argument('--archive', default='')
    parser.add_argument('--key-file', type=Path)
    args = parser.parse_args(argv)
    if args.action == 'clean':
        clean(site_config=args.site_config)
        return 0
    if not args.site_config.exists():
        if args.action == 'fclean':
            raise ValueError('No site configuration exists; use make clean for local generated files')
        raise ValueError('Run rtk proxy make first to configure and install this site')
    if args.action == 'fclean':
        # Validate the later local reset before any installed resource is deleted.
        choice_reset(ROOT, args.site_config)
    if code := subprocess.run(['rtk', 'proxy', 'bash', 'deploy/bootstrap-access.sh'], cwd=ROOT).returncode:
        return code
    if code := controller.main():
        return code
    script = 'fresh.py' if args.action == 'fclean' else args.action + '.py'
    command = privileged_python(controller.PYTHON, ROOT / 'implementation' / script)
    if args.action == 'fclean':
        command += ['reset', '--interactive', '--confirm', args.confirm]
    elif args.action == 'restore':
        command += ['--activate', '--interactive', '--confirm', args.confirm]
        if args.archive:
            command += ['--archive', args.archive]
    command += ['--site-config', str(args.site_config), '--env-file', str(args.env_file)]
    if args.key_file and args.action in ('backup', 'restore'):
        command += ['--key-file', str(args.key_file)]
    code = subprocess.run(command, cwd=ROOT).returncode
    if not code and args.action == 'fclean':
        clean(site_config=args.site_config)
        print(f'\n  {mark("PASS")} Full clean complete.', flush=True)
    return code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as error:
        raise SystemExit(str(error)) from None
