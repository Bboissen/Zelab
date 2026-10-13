"""Ask the selected native Hermes runtime for its systemd unit identity."""

import os
from pathlib import Path
import sys

install = Path(sys.argv[1])
sys.path.insert(0, str(install))
from hermes_cli._launchers import runtime_command

command = runtime_command(install, args=[], code=(
    'from hermes_cli.gateway import get_service_name; '
    'print(get_service_name() + ".service")'
))
os.execv(command[0], command)
