#!/bin/bash
# Run locally once as the operator. Ordinary sudo authorizes the persistent grant.
set -euo pipefail
umask 077

operator=$(rtk proxy id -un)
if [[ "$operator" == root || ! "$operator" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
    echo 'Run this as the non-root deployment operator.' >&2
    exit 1
fi
# A successful `sudo true` may be only a narrow command grant. This proves an
# actual root interpreter is available to the deployment automation.
if [[ $(rtk proxy sudo -n /usr/bin/python3 -c 'import os; print(os.geteuid())' 2>/dev/null) != 0 ]]; then
    echo "Zelab asks for your sudo password once, to set up unattended administration."
    rtk proxy sudo -v
    candidate=$(rtk proxy mktemp)
    trap 'rtk proxy rm -f -- "$candidate"' EXIT
    printf '%s ALL=(ALL:ALL) NOPASSWD: ALL\n' "$operator" > "$candidate"
    rtk proxy sudo /usr/sbin/visudo -cf "$candidate" >/dev/null
    rtk proxy sudo install -o root -g root -m 0440 "$candidate" "/etc/sudoers.d/zelab-agent-$operator"
    rtk proxy sudo -n /usr/sbin/visudo -c >/dev/null
    echo "Unattended administration granted. Revoke it by removing /etc/sudoers.d/zelab-agent-$operator."
fi

# sudo deliberately drops the operator's PATH. Install its already trusted RTK
# once so root never executes a binary from a user-writable directory.
rtk_source=$(rtk proxy which rtk)
if [[ ! -e /usr/local/bin/rtk && ! -L /usr/local/bin/rtk ]]; then
    rtk proxy sudo -n install -o root -g root -m 0755 "$rtk_source" /usr/local/bin/rtk
fi
rtk proxy sudo -n /usr/bin/python3 -c '
import os, pathlib, stat
p = pathlib.Path("/usr/local/bin/rtk")
s = p.lstat()
if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022 or not os.access(p, os.X_OK):
    raise SystemExit("/usr/local/bin/rtk must be a root-owned executable regular file, writable only by root.")
'
rtk proxy sudo -n /usr/local/bin/rtk --version >/dev/null
# The controller virtual environment needs the distribution's venv package.
if ! rtk proxy python3 -c 'import ensurepip, venv' 2>/dev/null; then
    rtk proxy sudo -n apt-get install -y python3-venv
fi
