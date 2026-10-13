"""Read-only deployment check, executed with the pinned native Hermes runtime.

Uses upstream readers rather than sourcing dotenv or installing a runtime gate.
It never changes profile state or substitutes for real OS-boundary acceptance.
"""

import json
import os
from pathlib import Path
import sys


def identities(value):
    if value is None or value == '':
        return set()
    if isinstance(value, str):
        if value.strip().startswith('['):
            value = json.loads(value)
        else:
            value = value.split(',')
    if not isinstance(value, list):
        raise ValueError('Discord admission must contain a list of IDs')
    return {str(item).strip() for item in value if str(item).strip()}


def disabled(value):
    return value is None or str(value).strip().lower() in {'', 'false', '0', 'no', 'off', 'none'}


def check_config(config, owner):
    """Check all native YAML/legacy admission forms, including nested extra blocks."""
    if not isinstance(config, dict):
        raise ValueError('Native configuration must be a mapping')
    for key, value in config.items():
        if key in {'allow_all_users', 'allow_bots', 'dm_role_auth_guild'} and not disabled(value):
            raise ValueError('Public, bot or role-based admission is outside the owner-only release')
        if key in {'allow_from', 'allowed_users'} and identities(value) - {owner}:
            raise ValueError('Native user grants extend beyond the declared owner')
        if key == 'allowed_roles' and identities(value):
            raise ValueError('Native Discord roles extend admission beyond the declared owner')
        if key == 'profile_routes' and value:
            raise ValueError('Cross-profile routing needs explicit audience qualification')
        if isinstance(value, dict):
            check_config(value, owner)


def check_profile(home, owner, env, config, legacy, terminal, tool_user):
    # Before discord_owner_id is configured nobody is admitted.
    if identities(env.get('DISCORD_ALLOWED_USERS')) != ({owner} if owner else set()):
        raise ValueError('Retain exactly the declared owner in DISCORD_ALLOWED_USERS')
    for name in ['DISCORD_ALLOW_ALL_USERS', 'GATEWAY_ALLOW_ALL_USERS', 'DISCORD_ALLOW_BOTS']:
        if not disabled(env.get(name)):
            raise ValueError('Public or bot admission is outside the owner-only release')
    if identities(env.get('DISCORD_ALLOWED_ROLES')):
        raise ValueError('Discord role grants must remain empty')
    check_config(config, owner)
    check_config(legacy, owner)
    for relative in ['pairing/discord-approved.json', 'platforms/pairing/discord-approved.json']:
        path = home / relative
        if path.exists():
            approved = json.loads(path.read_text())
            if not isinstance(approved, dict) or set(approved) - ({owner} if owner else set()):
                raise ValueError('Revoke Discord pairings outside the declared owner')
    if tool_user:
        required = {
            'TERMINAL_ENV': 'ssh', 'TERMINAL_SSH_HOST': '127.0.0.1',
            'TERMINAL_SSH_PORT': '22', 'TERMINAL_SSH_USER': tool_user,
            'TERMINAL_SSH_KEY': str(home / 'terminal.key'),
        }
        if any(terminal.get(key) != value or (key in env and env[key] != value)
               for key, value in required.items()):
            raise ValueError('Retain the declared SSH tool backend in YAML and dotenv')
        if any(value and (key.startswith('PAPERCLIP_') or key == 'API_SERVER_KEY')
               for key, value in env.items()):
            raise ValueError('Restricted profiles cannot retain coordinator credentials')


def main():
    # These are the pinned upstream parsers in the installed dependency generation.
    from agent.secret_scope import load_env_file
    from gateway.config_loader import read_yaml_layers
    from tools.terminal_scope import build_profile_terminal_scope

    owner, tool_user, mode = sys.argv[1:]
    home = Path(os.environ['HERMES_HOME'])
    if mode == 'skip-parked' and (home / 'gateway.parked').is_file():
        return
    if not (home / '.env').is_file() or not (home / 'config.yaml').is_file():
        raise ValueError('Native profile configuration is incomplete')
    legacy_path = home / 'gateway.json'
    legacy = json.loads(legacy_path.read_text()) if legacy_path.exists() else {}
    check_profile(home, owner, load_env_file(home / '.env'), read_yaml_layers(home),
                  legacy, build_profile_terminal_scope(home) if tool_user else {}, tool_user)


if __name__ == '__main__':
    # Ask the pinned upstream launcher to select its interpreter/dependencies;
    # never guess a venv path or install dependencies in the guest system Python.
    install = Path(sys.argv.pop(1))
    sys.path.insert(0, str(install))
    from hermes_cli._launchers import runtime_command

    command = runtime_command(install, args=sys.argv, code=(
        'import runpy,sys; sys.argv=sys.argv[1:]; '
        'runpy.run_path(sys.argv[0], run_name="__zelab_native_check__")'
    ))
    os.execv(command[0], command)

if __name__ == '__zelab_native_check__':
    try:
        main()
    except Exception:
        # Native errors can include private file values. Ansible supplies the
        # actionable reconciliation message without printing those values.
        raise SystemExit(1) from None
