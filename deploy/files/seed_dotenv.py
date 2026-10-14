"""Add owner-only admission settings to a native Hermes dotenv file.

usage: seed_dotenv.py FILE [--fill-empty] KEY=VALUE...

Pairs with an empty VALUE are skipped, so unknown IDs stay absent and an empty
allowlist admits nobody. A KEY already present keeps its value, preserving
later edits in the native UI; --fill-empty also replaces a present empty KEY.
Prints "changed" or "unchanged".
"""

import os
from pathlib import Path
import sys

DECIMAL = {'DISCORD_ALLOWED_USERS', 'DISCORD_ALLOWED_CHANNELS'}
HERMES_UID = 61102


def main(argv):
    path, *arguments = argv
    fill_empty = '--fill-empty' in arguments
    pairs = [argument.partition('=')[::2] for argument in arguments if argument != '--fill-empty']
    if any(key in DECIMAL and value and not value.isdecimal() for key, value in pairs):
        raise SystemExit('Initial owner Discord IDs must be decimal IDs.')
    path = Path(path)
    lines = path.read_text().splitlines()
    present = {line.partition('=')[0].strip(): line.partition('=')[2].strip()
               for line in lines if '=' in line and not line.lstrip().startswith('#')}
    additions = [(key, value) for key, value in pairs
                 if value and (key not in present or (fill_empty and not present[key]))]
    if additions:
        replaced = {key for key, _ in additions}
        lines = [line for line in lines if line.partition('=')[0].strip() not in replaced]
        path.write_text('\n'.join(lines + [f'{key}={value}' for key, value in additions]) + '\n')
    path.chmod(0o600)
    os.chown(path, HERMES_UID, HERMES_UID)
    print('changed' if additions else 'unchanged')


if __name__ == '__main__':
    main(sys.argv[1:])
