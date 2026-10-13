#!/usr/bin/env python3
"""Run the Linux installer over SSH and bring its standalone guide to this device.

The client uses Python's standard library, Make, RTK and OpenSSH on macOS/Linux.
The server must already have the Zelab checkout and its documented prerequisites.
No local configuration or credentials are uploaded. SSH aliases/config are used.
"""

import argparse
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile

from console import heading, paint


def remote_command(directory, argv):
    # POSIX syntax works with bash, zsh and other login shells: use sh explicitly
    # for cwd/PATH handling, and quote once for each SSH shell boundary.
    script = ('PATH="$HOME/.local/bin:$PATH"; export PATH; '
              f"cd {shlex.quote(directory)} && exec {shlex.join(argv)}")
    return shlex.join(["sh", "-c", script])


def open_guide(path):
    command = "open" if platform.system() == "Darwin" else "xdg-open"
    if not shutil.which(command):
        return False
    if command == "xdg-open" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    return subprocess.run(["rtk", "proxy", command, str(path)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def run_remote(args):
    if args.action not in ("install", "recover", "guide"):
        raise ValueError("REMOTE_ACTION must be install, recover or guide.")
    if not args.host or args.host.startswith("-") or any(c.isspace() for c in args.host):
        raise ValueError("Set REMOTE_HOST=user@server (an SSH config alias also works).")
    if not args.directory.startswith("/") or "\n" in args.directory:
        raise ValueError("Set REMOTE_DIR to the absolute path of the Zelab checkout on the Linux server.")
    if args.port and (not args.port.isdecimal() or not 1 <= int(args.port) <= 65535):
        raise ValueError("REMOTE_PORT must be a port number from 1 to 65535; omit it to use SSH config.")
    for tool in ("ssh", "scp"):
        if not shutil.which(tool):
            raise ValueError(f"Install OpenSSH: {tool} is required on this device.")

    ssh = ["rtk", "proxy", "ssh", "-o", "ConnectTimeout=15"]
    scp = ["rtk", "proxy", "scp", "-o", "ConnectTimeout=15"]
    if args.port:
        ssh.extend(["-p", args.port])
        scp.extend(["-P", args.port])
    inputs = [f"SITE_CONFIG={args.site_config}", f"ENV_FILE={args.env_file}"]
    command = ["rtk", "proxy", "make", "--no-print-directory", "GUIDE_MODE=remote", *inputs]
    if args.action == 'guide':
        code = ("import os,subprocess; p='.venv/bin/python' if os.path.isfile('.venv/.controller-ready') else 'python3'; "
                "raise SystemExit(subprocess.call(['rtk','proxy',p,'deploy/doctor.py','--brief','--open',"
                "'--guide-mode','remote','--site-config'," + repr(args.site_config) + ","
                "'--env-file'," + repr(args.env_file) + "]))")
        command = ['rtk', 'proxy', 'python3', '-c', code]
    if "NO_COLOR" in os.environ:
        command = ["rtk", "proxy", "env", "NO_COLOR=" + os.environ["NO_COLOR"], *command]
    heading("ZELAB REMOTE INSTALLER")
    print(f"  Server: {args.host}\n  Checkout: {args.directory}\n", flush=True)
    # A real stdin terminal allows one-time sudo authentication on the server.
    terminal = ["-t"] if sys.stdin.isatty() else ["-T"]
    result = subprocess.run([*ssh, *terminal, args.host, remote_command(args.directory, command)])

    # Also retrieve an actionable guide after a failed install. The path endpoint
    # uses system Python if controller bootstrapping never finished.
    code = ("import os,subprocess,sys; "
            "p='.venv/bin/python' if os.path.isfile('.venv/.controller-ready') else 'python3'; "
            "sys.exit(subprocess.call(['rtk','proxy',p,'deploy/doctor.py','--guide-path-only',"
            "'--site-config',sys.argv[1],'--env-file',sys.argv[2]]))")
    snapshot = subprocess.run([*ssh, "-T", args.host, remote_command(args.directory,
                              ["rtk", "proxy", "python3", "-c", code, args.site_config, args.env_file])],
                              capture_output=True, text=True)
    path = snapshot.stdout.strip()
    if snapshot.returncode or not path.startswith("/") or "\n" in path or "\r" in path:
        print("\n  Could not retrieve the server guide. Rerun rtk proxy make on the server.")
        return result.returncode or 1

    destination = args.guide_destination.expanduser().resolve()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Create a private temporary file alongside the destination, then replace
    # atomically so an unsuccessful copy cannot discard the last useful guide.
    descriptor, filename = tempfile.mkstemp(prefix=".zelab-guide-", suffix=".html", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(filename)
    try:
        copy = subprocess.run([*scp, "--", f"{args.host}:{path}", str(temporary)])
        if copy.returncode:
            print("\n  Guide copy failed. Rerun rtk proxy make on the server for manual copy instructions.")
            return result.returncode or copy.returncode
        temporary.chmod(0o600)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    heading("YOUR LOCAL SETUP GUIDE")
    print(f"  {paint(destination.as_uri(), 'cyan', 'bold')}")
    print("  " + ("Opened in your browser." if open_guide(destination) else "Open this file in your browser."))
    print("\n  Rerun the same make command after completing the account steps.")
    print("  Use the same REMOTE_HOST and REMOTE_DIR values.\n")
    return result.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("REMOTE_HOST", ""))
    parser.add_argument("--directory", default=os.environ.get("REMOTE_DIR", ""))
    parser.add_argument("--port", default=os.environ.get("REMOTE_PORT", ""))
    parser.add_argument("--action", choices=("install", "recover", "guide"),
                        default=os.environ.get("REMOTE_ACTION") or "install")
    parser.add_argument("--site-config", default=os.environ.get("SITE_CONFIG") or "config/site.yaml")
    parser.add_argument("--env-file", default=os.environ.get("ENV_FILE") or ".env")
    parser.add_argument("--guide-destination", type=Path,
                        default=Path(os.environ.get("GUIDE_DEST") or Path.home() / "Downloads/zelab-guide.html"))
    args = parser.parse_args(argv)
    try:
        return run_remote(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"\n  Remote installer: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n  Interrupted. Rerun with REMOTE_ACTION=recover to resume.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
