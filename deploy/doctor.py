#!/usr/bin/env python3
"""Read-only readiness and connection status; never changes the host.

FAIL lines block installation. WARN lines are advice. PENDING lines are
connection steps you can finish before or after installation. During install,
only FAIL lines are printed; the installer shows the connection checklist.
"""

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import pwd
import shlex
import shutil
import socket
import stat
import subprocess
import sys

from checklist import ITEMS, PHASES, render_html
from console import heading, mark, paint


# Standard maintenance windows, checked against upstream on 2026-10-10:
# https://www.debian.org/releases/trixie/
# https://ubuntu.com/about/release-cycle
# These are recommended baselines, not a compatibility allowlist. Releases
# outside these windows warn; actual deployment prerequisites remain required.
SUPPORTED_OS = {
    ("debian", "13"): date(2028, 8, 9),
    ("ubuntu", "24.04"): date(2029, 5, 31),
    ("ubuntu", "26.04"): date(2031, 5, 31),
}
BASELINE = "Debian 13 or Ubuntu 24.04/26.04 LTS (x86_64)"
ROOT_RTK = Path("/usr/local/bin/rtk")
ROOT = Path(__file__).resolve().parents[1]
GIB = 2**30
# Pinned Debian image, Hermes/Paperclip dependencies and the Obsidian image.
DOWNLOAD_GIB = 8
# Paperclip and Obsidian beside the guest during installation.
SERVICE_MEMORY_GIB = 2


@dataclass(frozen=True)
class Check:
    name: str
    ready: bool
    detail: str
    blocking: bool = True
    pending: bool = False
    phase: str = PHASES[0]

    @property
    def label(self):
        if self.ready:
            return "PASS"
        if self.pending:
            return "PENDING"
        return "FAIL" if self.blocking else "WARN"


def phase_of(key):
    return next(item.phase for item in ITEMS if item.key == key)


def platform_checks(release=None, machine=None, today=None):
    today = today or date.today()
    machine = machine or platform.machine()
    if release is None:
        try:
            release = platform.freedesktop_os_release()
        except (OSError, AttributeError):
            release = {}
    expiry = SUPPORTED_OS.get((release.get("ID"), release.get("VERSION_ID")))
    maintained = expiry is not None and today <= expiry
    return [
        Check("Host release", maintained,
              f"Recommended baseline: {BASELINE}" if maintained else
              f"Release is outside the recommended baseline or maintenance window ({BASELINE}). "
              "Deployment can proceed when actual prerequisites pass; compatibility remains to be verified.",
              blocking=False),
        Check("Host architecture", machine == "x86_64",
              "The pinned Node, optional worker CLIs and Debian guest downloads are x86_64 builds"),
    ]


def report_checks(checks):
    """Print blocking failures only; advice belongs in the guide, not every run."""
    ready = True
    for check in checks:
        if check.label == "FAIL":
            print(f"{mark('FAIL')} {check.name}: {check.detail}")
            ready = False
    return ready


def trusted_root_rtk(path=ROOT_RTK):
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0
            and not metadata.st_mode & 0o022 and bool(metadata.st_mode & 0o111))


def command_output(argv, timeout=15):
    """Never print tool output: probes can contain private host/account data."""
    try:
        result = subprocess.run(["rtk", "proxy", *argv], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=timeout, text=True)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def command_succeeds(argv):
    return command_output(argv) is not None


def existing_parent(path):
    """Nearest ancestor the operator can inspect (state directories are private)."""
    path = Path(path)
    while path != path.parent:
        try:
            path.stat()
            return path
        except OSError:
            path = path.parent
    return path


def free_gib(path):
    return shutil.disk_usage(existing_parent(path)).free / GIB


def available_memory_gib():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024 / GIB
    return 0.0


def resource_checks(site, installed=False):
    """Hard-fail only on the actual installation need; reserves are advice.

    Once the guest exists its disk and memory are already in use, so only the
    remaining headroom is reported.
    """
    if installed:
        disk_free, memory_free = free_gib(site["vm_data"]), available_memory_gib()
        return [Check("Disk headroom", disk_free >= site["host_disk_reserve_gib"],
                      f"{disk_free:.0f} GiB free; host_disk_reserve_gib is {site['host_disk_reserve_gib']} GiB",
                      blocking=False),
                Check("Memory headroom", memory_free >= site["host_memory_reserve_gib"],
                      f"{memory_free:.1f} GiB available; host_memory_reserve_gib is "
                      f"{site['host_memory_reserve_gib']} GiB", blocking=False)]
    disk_need = site["vm_disk_gib"] + DOWNLOAD_GIB
    disk_free = free_gib(site["vm_data"])
    disk_wanted = disk_need + site["host_disk_reserve_gib"]
    memory_need = site["vm_memory_mb"] / 1024 + SERVICE_MEMORY_GIB
    memory_free = available_memory_gib()
    memory_wanted = memory_need + site["host_memory_reserve_gib"]
    checks = [Check("Free disk", disk_free >= disk_need,
                    f"{disk_free:.0f} GiB free where the guest disk is stored; installation needs "
                    f"{disk_need} GiB (vm_disk_gib + downloads). Free space or lower vm_disk_gib.")]
    if disk_free >= disk_need:
        checks.append(Check("Disk headroom", disk_free >= disk_wanted,
                            f"{disk_free:.0f} GiB free; {disk_wanted} GiB keeps the "
                            f"{site['host_disk_reserve_gib']} GiB host_disk_reserve_gib", blocking=False))
    checks.append(Check("Available memory", memory_free >= memory_need,
                        f"{memory_free:.1f} GiB available; the guest and services need {memory_need:.0f} GiB. "
                        "Stop other workloads or lower vm_memory_mb."))
    if memory_free >= memory_need:
        checks.append(Check("Memory headroom", memory_free >= memory_wanted,
                            f"{memory_free:.1f} GiB available; {memory_wanted:.0f} GiB keeps the "
                            f"{site['host_memory_reserve_gib']} GiB host_memory_reserve_gib", blocking=False))
    return checks


def host_checks():
    checks = platform_checks()
    checks.extend([
        Check("Controller Python", sys.version_info >= (3, 12),
              "The locked ansible-core 2.21 controller needs Python 3.12 or newer"),
        Check("Python venv", importlib.util.find_spec("venv") is not None
              and importlib.util.find_spec("ensurepip") is not None,
              "rtk proxy make adds python3-venv; or run: sudo apt install python3-venv"),
        Check("Native supervision", Path("/run/systemd/system").is_dir(), "The host must boot with systemd"),
        Check("KVM", Path("/dev/kvm").is_char_device(),
              "Enable virtualization (VT-x/AMD-V) in firmware and load the kvm module"),
        Check("Operator", os.geteuid() != 0, "Run as your normal account, not root"),
    ])
    # sbin need not be on the normal operator's PATH.
    search_path = os.environ.get("PATH", "") + ":/usr/local/sbin:/usr/sbin:/sbin"
    commands = {"make": "make", "sudo": "sudo", "docker": "Docker Engine", "sshd": "openssh-server", "rtk": "RTK"}
    missing = [label for command, label in commands.items() if shutil.which(command, path=search_path) is None]
    checks.append(Check("Host commands", not missing, "Install: " + ", ".join(missing)))
    return checks


def admin_checks():
    phase = phase_of("admin")
    trusted = trusted_root_rtk()
    root_prefix = ["sudo", "-n", str(ROOT_RTK), "proxy"]
    elevated = trusted and command_succeeds([*root_prefix, "/usr/bin/python3", "-c",
                                            "import os,sys; sys.exit(os.geteuid() != 0)"])
    checks = [Check("Unattended administration", elevated,
                    "rtk proxy make asks for your sudo password once",
                    pending=True, phase=phase)]
    if elevated:
        for name, argv, detail in (
            ("Docker daemon", ["docker", "info"], "Start Docker Engine (systemctl start docker)"),
            ("Docker Compose", ["docker", "compose", "version"], "Install the Docker Compose v2 plugin"),
            ("OpenSSH configuration", ["/usr/sbin/sshd", "-t"], "Fix the host SSH server configuration"),
        ):
            checks.append(Check(name, command_succeeds([*root_prefix, *argv]), detail, phase=phase))
    return checks, elevated


def port_checks(site, elevated):
    """A port must be free or held by the Zelab unit that owns it."""
    owners = {"guest_ssh_port": site["vm_service"] + ".service", "guest_dashboard_port": site["vm_service"] + ".service",
              "guest_api_port": site["vm_service"] + ".service", "paperclip_port": "zelab-paperclip.service",
              "paperclip_db_port": "zelab-paperclip.service", "notes_sftp_port": "zelab-notes-sshd.service",
              "obsidian_backend_port": "docker:zelab-obsidian"}
    busy = []
    for name, owner in owners.items():
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", site[name]))
                continue
            except OSError:
                pass
        if owner.startswith("docker:"):
            ours = elevated and (command_output(["sudo", "-n", str(ROOT_RTK), "proxy", "docker", "inspect",
                                                 "--format", "{{.State.Running}}", owner[7:]]) or "").strip() == "true"
        else:
            ours = (command_output(["systemctl", "is-active", owner]) or "").strip() == "active"
        if not ours:
            busy.append(f"{name} {site[name]}")
    return [Check("Component ports", not busy,
                  "In use by another program: " + ", ".join(busy) + ". Set a free port for each in config/site.yaml.",
                  phase=phase_of("admin"))]


def tailscale_checks(site):
    phase = phase_of("tailscale")
    if not site.get("ui_tailscale_enabled"):
        return [Check("Tailscale", False, "UIs are localhost-only. Enroll this host, then set ui_tailscale_enabled: "
                      "true and ui_tailscale_hostname in config/site.yaml", pending=True, phase=phase)]
    try:
        status = json.loads(command_output(["tailscale", "status", "--json"]) or "")
        running = status.get("BackendState") == "Running"
        name = status["Self"]["DNSName"].rstrip(".")
    except (ValueError, KeyError, TypeError, AttributeError):
        running, name = False, None
    checks = [Check("Tailscale", running and name == site["ui_tailscale_hostname"],
                    "Start Tailscale on this host and make ui_tailscale_hostname match its MagicDNS name",
                    phase=phase)]
    try:
        serve = json.loads(command_output(["tailscale", "serve", "status", "--json"]) or "{}") or {}
    except ValueError:
        serve = {}
    routes = {"ui_tail_hermes_port": site["guest_dashboard_port"], "ui_tail_paperclip_port": site["paperclip_port"]}
    if site.get("obsidian_enabled"):
        routes["ui_tail_obsidian_port"] = site["obsidian_backend_port"]
    conflicts = []
    for field, backend in routes.items():
        endpoint = f"{site['ui_tailscale_hostname']}:{site[field]}"
        handler = ((serve.get("Web") or {}).get(endpoint, {}).get("Handlers") or {}).get("/", {})
        listener = (serve.get("TCP") or {}).get(str(site[field]), {})
        if handler not in ({}, {"Proxy": f"http://127.0.0.1:{backend}"}) or listener not in ({}, {"HTTPS": True}) \
                or (serve.get("AllowFunnel") or {}).get(endpoint):
            conflicts.append(f"{field} {site[field]}")
    checks.append(Check("Tailscale Serve ports", not conflicts,
                        "Used by another Serve route or Funnel: " + ", ".join(conflicts)
                        + ". Choose free ports in config/site.yaml; other routes are never replaced.", phase=phase))
    return checks


def guest_facts(site):
    """Read booleans from the running guest; None when it is not reachable yet."""
    if (command_output(["systemctl", "is-active", site["vm_service"] + ".service"]) or "").strip() != "active":
        return None
    code = ("import json,pathlib; h=pathlib.Path('/var/lib/zelab/hermes/home'); "
            "a=h/'auth.json'; e=h/'.env'\n"
            "def auth():\n"
            " try: d=json.loads(a.read_text())\n"
            " except Exception: return False\n"
            " return bool(d.get('providers')) if isinstance(d,dict) and 'providers' in d else bool(d)\n"
            "t=e.is_file() and any(l.startswith('DISCORD_BOT_TOKEN=') and l.split('=',1)[1].strip() "
            "for l in e.read_text().splitlines())\n"
            "print(json.dumps({'hermes_auth': auth(), 'discord_token': t}))")
    output = command_output(["ssh", "-F", "/dev/null", "-i", site["deploy_key"], "-o", "BatchMode=yes",
                             "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=yes",
                             "-o", "UserKnownHostsFile=" + site["guest_known_hosts"], "-p", str(site["guest_ssh_port"]),
                             site["guest_user"] + "@127.0.0.1", "sudo -n python3 -c " + shlex.quote(code)], timeout=20)
    try:
        return json.loads(output)
    except (TypeError, ValueError):
        return None


def connection_checks(site, elevated):
    harness = site.get('worker_harness', 'none')
    if harness == 'codex':
        auth = Path(site['worker_codex_auth_source'])
        try:
            info = auth.lstat()
            login = stat.S_ISREG(info.st_mode) and not info.st_mode & 0o077 and info.st_size > 0
        except OSError:
            login = False
        checks = [Check('Codex subscription login', login,
                        f'Run `/usr/local/bin/zelab-codex login --device-auth` so {auth} exists (mode 0600), then rerun: rtk proxy make',
                        pending=True, phase=phase_of('harness'))]
    elif harness == 'claude':
        login = command_succeeds(['sudo', '-n', '-u', site['worker_ops_user'], '-H',
                                 '/usr/local/bin/zelab-claude', 'auth', 'status']) if elevated else False
        checks = [Check('Claude Code subscription login', login,
                        f'Run `rtk proxy sudo -u {site["worker_ops_user"]} -H /usr/local/bin/zelab-claude auth login --claudeai`; use Paperclip Test Environment before a worker task',
                        pending=True, phase=phase_of('harness'))]
    elif harness == 'both':
        checks = [Check('Worker adapters to choose', False,
                        'Both CLIs are installed. Choose Codex or Claude Code per worker in Paperclip, complete its native login and Test Environment, then resume Operations. Coding stays paused.',
                        pending=True, phase=phase_of('harness'))]
    else:
        checks = [Check('Worker harness skipped', True,
                        'New workers stay paused. Choose worker_harness: codex or claude in config/site.yaml when ready.',
                        phase=phase_of('harness'))]
    guest = guest_facts(site) if elevated or Path(site["deploy_key"]).exists() else None
    waiting = "Available after installation; open the Hermes URL printed by make"
    checks.append(Check("Hermes subscription sign-in", bool(guest and guest["hermes_auth"]),
                        "In Hermes, choose a provider and model on the default profile and complete its native login"
                        if guest else waiting, pending=True, phase=phase_of("hermes")))
    discord = phase_of("discord")
    checks.append(Check("Discord owner ID", bool(site["research_initial_allowed_user"]),
                        "Set discord_owner_id in config/site.yaml, then rerun: rtk proxy make",
                        pending=True, phase=discord))
    checks.append(Check("Operations bot token", bool(guest and guest["discord_token"]),
                        "Paste the Operations bot token in Hermes → Channels → Discord (see docs/SETUP.md)",
                        pending=True, phase=discord))
    checks.append(Check("First Discord → host task", False,
                        "Not provable by automation; follow the Test step in docs/SETUP.md",
                        pending=True, phase=phase_of("proof")))
    return checks


def load_site(site_path, env_path):
    """Return validated inputs, or a reason they are unavailable (never secrets)."""
    if importlib.util.find_spec("yaml") is None:
        return None, "Site checks need the controller; rtk proxy make creates it"
    from run import read_inputs
    if not site_path.exists():
        return None, f"{site_path} does not exist yet; rtk proxy make creates it from this host"
    try:
        return read_inputs(site_path, env_path), None
    except ValueError as error:
        return None, str(error)
    except Exception:
        return None, "Site configuration could not be read; check its YAML syntax and permissions"


def readiness_checks(site_path=None, env_path=None):
    checks = host_checks()
    admin, elevated = admin_checks()
    checks.extend(admin)
    if site_path is None:
        return checks
    site, problem = load_site(site_path, env_path)
    if site is None:
        checks.append(Check("Site configuration", False, problem, pending=not site_path.exists()))
        return checks
    installed = Path(f"/etc/systemd/system/{site['vm_service']}.service").exists()
    checks.extend(resource_checks(site, installed))
    checks.extend(port_checks(site, elevated))
    checks.extend(tailscale_checks(site))
    checks.extend(connection_checks(site, elevated))
    if installed:
        from bridge_check import inspect
        bridges = inspect(site, os.environ.get('ZELAB_PASSWORD_DIR') or None)
        if bridges is not None:
            checks.append(Check('Native bridge contracts', bridges.ready, bridges.detail,
                                pending=bridges.pending,
                                phase=phase_of('admin')))
    return checks


def print_grouped(checks, brief=False):
    """Print every check, or with brief only the blocking failures."""
    ready = True
    for phase in PHASES:
        selected = [check for check in checks if check.phase == phase
                    and not (brief and check.label != "FAIL")]
        if selected:
            print(f"\n{paint(phase, 'bold')}")
            for check in selected:
                detail = "" if check.ready else f" — {check.detail}"
                print(f"  {mark(check.label)} {check.name}"
                      + detail)
                ready = ready and (check.ready or check.pending or not check.blocking)
    return ready


def application_links(site):
    """Non-secret application entry points shared by the terminal and guide."""
    if not site:
        return []
    tailnet = site.get("ui_tailscale_enabled")
    applications = []
    for name, tail_port, local_port, user, secret in (
            ("Hermes", "ui_tail_hermes_port", "guest_dashboard_port", "dashboard_username", "hermes-dashboard.password"),
            ("Paperclip", "ui_tail_paperclip_port", "paperclip_port", "paperclip_owner_email", "paperclip-owner.password"),
            ("Obsidian", "ui_tail_obsidian_port", "obsidian_backend_port", "obsidian_username", "obsidian.password")):
        if name == "Obsidian" and not site.get("obsidian_enabled", True):
            continue
        url = (f"https://{site['ui_tailscale_hostname']}:{site[tail_port]}/" if tailnet
               else f"http://127.0.0.1:{site[local_port]}/")
        legacy = Path(site['private_dir']) / secret
        password = (str(legacy) if legacy.is_file() else
                    'Your password manager (entered during terminal setup)')
        applications.append({"name": name, "url": url, "user": str(site[user]),
                             "password": password, "tailnet": bool(tailnet)})
    return applications


def print_installed(site, waiting, details=None):
    """Where to log in and what is left; only credential locations are printed."""
    heading("ZELAB IS INSTALLED", "green")
    print("  Services are ready. Complete the account and Discord steps below.")
    heading("YOUR APPLICATIONS")
    for app in application_links(site):
        print(f"\n  {paint(app['name'], 'bold')}\n    {paint(app['url'], 'cyan', 'bold')}")
        print(f"    Login:    {app['user']}\n    Password: {app['password']}")
    if not site.get("ui_tailscale_enabled"):
        print("\n  Localhost URLs refer to the Linux server. For laptop access, configure")
        print("  Tailscale in the guide or use an SSH port forward.")
    if waiting:
        heading("NEXT ACTIONS", "yellow")
        print("  Follow these in order. The setup guide has the full instructions.")
        for index, name in enumerate(waiting, 1):
            print(f"\n  {paint(str(index) + '. ' + name, 'yellow', 'bold')}")
            if (details or {}).get(name):
                print(f"     {details[name]}")
    else:
        heading("NEXT ACTION", "yellow")
        print("  Run the Discord-to-host test in the guide to verify the complete flow.")


def status_snapshot(checks, destination, site=None):
    """Write the private HTML guide with doctor's own check lines; never tool output or secrets."""
    by_item, lines = {}, {}
    # Advice stays visible in the check list, but does not undo a completed step
    # or send the guide's Next link back to an optional host upgrade.
    order = {"FAIL": 0, "PENDING": 1, "PASS": 2, "WARN": 3}
    for check in checks:
        key = next(item.key for item in ITEMS if item.phase == check.phase)
        lines.setdefault(key, []).append((check.label, check.name, check.detail))
        current = by_item.get(key)
        if current is None or order[check.label] < order[current[0]]:
            by_item[key] = (check.label, check.name)
    by_item.setdefault("proof", ("MANUAL", "Not provable by automation"))
    if by_item["proof"][0] == "PENDING":
        by_item["proof"] = ("MANUAL", "Not provable by automation")
    for item in ITEMS:
        by_item.setdefault(item.key, ("UNKNOWN", "Checked once rtk proxy make has created config/site.yaml"))
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as output:
        output.write(render_html(by_item, stamp, lines, standalone=True, applications=application_links(site)))


def guide_path(site):
    """The private guide lives in private_dir; before install that is the default location."""
    return Path(site["private_dir"] if site else Path.home() / ".local/state/zelab-deployment") / "guide.html"


def ssh_session():
    return any(os.environ.get(key) for key in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"))


def laptop_copy_command(path):
    """Copy this snapshot using the current SSH server address and port."""
    connection = os.environ.get("SSH_CONNECTION", "").split()
    host, port = (connection[2], connection[3]) if len(connection) == 4 else (socket.gethostname(), "22")
    if ":" in host:
        host = f"[{host}]"
    user = pwd.getpwuid(os.getuid()).pw_name
    command = shlex.join(["rtk", "proxy", "scp", "-P", port, f"{user}@{host}:{path}"])
    return command + " ~/Downloads/zelab-guide.html"


def open_in_browser(path):
    """Open only on a local desktop; SSH instructions target the laptop."""
    if ssh_session() or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) \
            or not shutil.which("xdg-open"):
        return False
    subprocess.Popen(["rtk", "proxy", "xdg-open", str(path)], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def print_guide(path, mode="auto", open_browser=False):
    remote = mode == "remote" or ssh_session()
    # Keep Guide: as a stable textual label for the install failure summary.
    print(f"\n  {paint('Guide:', 'cyan', 'bold')} {path}" + (" (on the server)" if remote else ""))
    heading("OPEN YOUR SETUP GUIDE")
    if remote:
        print("  From an SSH session: run these on your laptop, in a new terminal.\n")
        print("    rtk proxy mkdir -p ~/Downloads")
        print("    " + laptop_copy_command(path))
        print("\n  macOS:")
        print("    rtk proxy open ~/Downloads/zelab-guide.html")
        print("  Linux:")
        print("    rtk proxy xdg-open ~/Downloads/zelab-guide.html")
        print("\n  Next time, use make REMOTE_HOST=user@server REMOTE_DIR=/absolute/checkout on the laptop.")
    else:
        opened = open_browser and open_in_browser(path)
        print("  " + ("Opened in your browser." if opened else "Open this file in your browser:"))
        print(f"    {paint(path.as_uri(), 'cyan', 'bold')}")
    print("\n  This guide is a snapshot. Rerun make to refresh it.\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform-only", action="store_true",
                        help="Report the recommended OS baseline and check the required architecture")
    parser.add_argument("--site-config", type=Path, default=ROOT / "config/site.yaml")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--html", action="store_true", help="Also write the private HTML guide with status")
    parser.add_argument("--open", action="store_true",
                        help="Write the private HTML guide, open it in a browser and never fail")
    parser.add_argument("--brief", action="store_true", help="Show only blocking failures")
    parser.add_argument("--after-install", action="store_true", help="Summarize a completed installation")
    parser.add_argument("--quiet", action="store_true", help="Print only the guide path (after a failed step)")
    parser.add_argument("--guide-mode", choices=("auto", "local", "remote"), default="auto",
                        help="Guide delivery mode; SSH sessions always avoid opening a server browser")
    parser.add_argument("--guide-path-only", action="store_true",
                        help="Generate the private guide and print only its absolute path for remote retrieval")
    args = parser.parse_args(argv)
    if args.platform_only:
        return 0 if report_checks(platform_checks()) else 1
    checks = readiness_checks(args.site_config, args.env_file)
    quiet = args.quiet or args.guide_path_only
    ready = True if quiet else print_grouped(checks, brief=args.brief)
    blockers = [check.name for check in checks if check.label == "FAIL"]
    waiting = [check.name for check in checks if check.label == "PENDING"]
    site, _ = load_site(args.site_config, args.env_file) if args.site_config.exists() else (None, None)
    if quiet:
        pass  # the failed step already explained itself
    elif blockers:
        print(f"\n{mark('FAIL')} Blocked: fix the lines marked {mark('FAIL')} (the guide explains each), "
              "then rerun: rtk proxy make")
    elif args.after_install and site:
        print_installed(site, waiting, {check.name: check.detail for check in checks if check.pending})
    elif not args.brief:
        print(f"\nNo blockers. {len(waiting)} step(s) still need you; see docs/SETUP.md.")
    if args.html or args.open or args.guide_path_only:
        path = guide_path(site)
        status_snapshot(checks, path, site)
        if args.guide_path_only:
            print(path.resolve())
        else:
            print_guide(path, args.guide_mode, args.open)
    return 0 if (not blockers if args.after_install else ready or args.open) else 1


if __name__ == "__main__":
    raise SystemExit(main())
