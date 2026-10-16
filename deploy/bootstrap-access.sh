#!/bin/bash
# Run locally once as the operator. Ordinary sudo authorizes the persistent grant.
set -euo pipefail
umask 077

operator=$(id -un)
if [[ "$operator" == root || ! "$operator" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
    echo 'Run this as the non-root deployment operator.' >&2
    exit 1
fi
# A successful `sudo true` may be only a narrow command grant. This proves an
# actual root interpreter is available to the deployment automation.
if [[ $(sudo -n /usr/bin/python3 -c 'import os; print(os.geteuid())' 2>/dev/null) != 0 ]]; then
    echo "Zelab asks for your sudo password once, to set up unattended administration."
    sudo -v
    candidate=$(mktemp)
    trap 'rm -f -- "$candidate"' EXIT
    printf '%s ALL=(ALL:ALL) NOPASSWD: ALL\n' "$operator" > "$candidate"
    sudo /usr/sbin/visudo -cf "$candidate" >/dev/null
    sudo install -o root -g root -m 0440 "$candidate" "/etc/sudoers.d/zelab-agent-$operator"
    sudo -n /usr/sbin/visudo -c >/dev/null
    echo "Unattended administration granted. Revoke it by removing /etc/sudoers.d/zelab-agent-$operator."
fi

# The controller virtual environment needs the distribution's venv package.
if ! python3 -c 'import ensurepip, venv' 2>/dev/null; then
    sudo -n apt-get install -y python3-venv
fi
