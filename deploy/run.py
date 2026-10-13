"""Read private deployment paths as data, then invoke the native Ansible CLI."""

import argparse
import datetime
import json
import os
from pathlib import Path
import pwd
import re
import signal
import subprocess
import tempfile
import sys
from contextlib import nullcontext

import yaml

from doctor import platform_checks, report_checks
from install_resources import resource_fraction
from configure import operator


ROOT = Path(__file__).resolve().parents[1]


ENV_FIELDS = {
    "ZELAB_PRIVATE_DIR": "private_dir",
    "ZELAB_DEPLOY_KEY": "deploy_key",
    "ZELAB_KNOWN_HOSTS": "guest_known_hosts",
    "ZELAB_CODEX_AUTH_FILE": "worker_codex_auth_source",
}


def absolute_path(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value):
        raise ValueError(f"{name} must be a plain absolute filesystem path")
    if str(Path(value)) != value or ".." in Path(value).parts or value.startswith("//"):
        raise ValueError(f"{name} must be canonical and cannot contain parent traversal")
    return value


class UniqueLoader(yaml.SafeLoader):
    """Reject ambiguous YAML before it can select a different privileged target."""


def unique_mapping(loader, node):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in mapping:
            raise ValueError("Site configuration keys must be unique strings")
        mapping[key] = loader.construct_object(value_node)
    return mapping


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def require_pattern(site, name, pattern):
    value = site.get(name)
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(f"Invalid or missing {name}; use the format in config/site.example.yaml")


DEFAULTS = ROOT / "config/defaults.yaml"
# Clear public names for the two Discord inputs; roles keep their original names.
# (Internal name first keeps secret scanners from mistaking the pair for a credential.)
DISCORD_ALIASES = {public: internal for internal, public in (
    ("research_initial_allowed_user", "discord_owner_id"),
    ("research_initial_allowed_channel", "discord_channel_id"))}
# Systemd units for these paths use ProtectHome, so home directories cannot work.
PROTECTED_HOME = (Path("/home"), Path("/root"), Path("/run/user"))


def load_yaml(path):
    data = yaml.load(path.read_text(), Loader=UniqueLoader)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Site configuration must be a YAML mapping")
    return data


def operator_home(user):
    try:
        return pwd.getpwnam(user).pw_dir
    except KeyError:
        return f"/home/{user}"


def derive_defaults(site):
    """Fill values that follow from other inputs; explicit site values win."""
    if 'operator_user' not in site:
        site['operator_user'] = operator()
    for alias, name in DISCORD_ALIASES.items():
        if alias in site:
            if name in site and str(site[name]) != str(site[alias]):
                raise ValueError(f"{alias} and {name} disagree; keep only {alias}")
            site[name] = site.pop(alias)
    for name in DISCORD_ALIASES.values():
        if site.get(name) in (None, ""):
            site[name] = ""
        elif isinstance(site[name], int) and not isinstance(site[name], bool):
            site[name] = str(site[name])
    user = site.get("operator_user")
    if isinstance(user, str) and re.fullmatch(r"[a-z_][a-z0-9_-]{0,47}", user):
        home = operator_home(user)
        # Environment paths belong only to the account launching this checkout.
        # Do not apply one user's XDG/Codex settings to a different declared owner.
        current = pwd.getpwuid(os.getuid()).pw_name == user and os.geteuid() != 0
        state_home = (os.environ.get('XDG_STATE_HOME') or f'{home}/.local/state') if current else f'{home}/.local/state'
        codex_home = (os.environ.get('CODEX_HOME') or f'{home}/.codex') if current else f'{home}/.codex'
        instance = site.get('vm_instance', 'zelab')
        suffix = '/' + instance if isinstance(instance, str) and instance != 'zelab' else ''
        site.setdefault("private_dir", f"{state_home}/zelab-deployment{suffix}")
        site.setdefault("backup_dir", f"{home}/backups/Zelab{suffix}")
        site.setdefault("worker_codex_auth_source", f"{codex_home}/auth.json")
        site.setdefault("dashboard_username", user)
        site.setdefault("obsidian_username", user)
        site.setdefault("paperclip_owner_email", f"{user}@zelab.local")
    if isinstance(site.get("private_dir"), str):
        site.setdefault("deploy_key", site["private_dir"] + "/deploy_ed25519")
        site.setdefault("guest_known_hosts", site["private_dir"] + "/guest_known_hosts")
    if isinstance(site.get("vm_data"), str):
        site.setdefault("vm_disk", site["vm_data"] + "/disk.qcow2")
    hosts = site.get("paperclip_allowed_hosts", ["127.0.0.1", "10.0.2.2"])
    if isinstance(hosts, list) and site.get("ui_tailscale_enabled") is True \
            and isinstance(site.get("ui_tailscale_hostname"), str) and site["ui_tailscale_hostname"] not in hosts:
        hosts = [*hosts, site["ui_tailscale_hostname"]]
    site["paperclip_allowed_hosts"] = hosts
    return site


def read_inputs(site_path, env_path, *, inspect_private=True, defaults_path=DEFAULTS):
    site = load_yaml(defaults_path) if defaults_path else {}
    site.update(load_yaml(site_path))
    if env_path is not None and (env_path.exists() or env_path.is_symlink()):
        seen = set()
        for number, line in enumerate(env_path.read_text().splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, separator, value = line.partition("=")
            name, value = name.strip(), value.strip()
            if not separator or name not in ENV_FIELDS or name in seen:
                raise ValueError(f"Invalid or duplicate deployment input at .env line {number}")
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            site[ENV_FIELDS[name]] = absolute_path(value, name)
            seen.add(name)
    derive_defaults(site)
    resource_fraction(site.get('vm_install_resource_fraction', 0.7))
    if not site.get("operator_user"):
        raise ValueError("Set operator_user in config/site.yaml (rtk proxy make writes it for you)")
    paths = ["private_dir", "deploy_key", "guest_known_hosts", "vm_disk", "vm_data", "vm_root",
             "paperclip_home", "worker_ops_home", "worker_code_home", "worker_codex_auth_source",
             "backup_dir", "hermes_install_dir"]
    for name in paths:
        absolute_path(site.get(name), name)
    private = Path(site['private_dir'])
    if len(private.parts) < 3:
        raise ValueError('private_dir must be a dedicated private directory')
    for name in ('deploy_key', 'guest_known_hosts'):
        if Path(site[name]).parent != private:
            raise ValueError(f'{name} must be directly inside private_dir')
    if site['deploy_key'] == site['guest_known_hosts']:
        raise ValueError('Deployment key and host-key pin must be different files')
    if "hermes_auth_import" in site:
        absolute_path(site["hermes_auth_import"], "hermes_auth_import")
    for name in ("vm_isolated", "notes_enabled", "obsidian_enabled", "ui_tailscale_enabled"):
        if type(site.get(name)) is not bool:
            raise ValueError(f"{name} must be an explicit YAML boolean")
    identities = ("operator_user", "vm_user", "paperclip_user", "worker_ops_user", "worker_code_user")
    for name in (*identities, "guest_user", "vm_service", "vm_instance"):
        require_pattern(site, name, r"[a-z_][a-z0-9_-]{0,47}")
    users = [site[name] for name in identities]
    if len(users) != len(set(users)) or "root" in users or site["guest_user"] == "root":
        raise ValueError("Operator and service identities must be distinct non-root accounts")
    state_paths = ["vm_data", "vm_root", "paperclip_home", "worker_ops_home", "worker_code_home"]
    for enabled, name in (("notes_enabled", "notes_vault_dir"), ("obsidian_enabled", "obsidian_state_dir")):
        if site[enabled]:
            absolute_path(site.get(name), name)
            state_paths.append(name)
    if site["notes_enabled"]:
        absolute_path(site.get("notes_guest_root"), "notes_guest_root")
        profiles = site.get("notes_shared_profiles")
        if not isinstance(profiles, list) or any(not isinstance(p, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", p) for p in profiles):
            raise ValueError("notes_shared_profiles must be a list of profile names")
        if len(profiles) != len(set(profiles)):
            raise ValueError("notes_shared_profiles must not repeat an audience")
    for name, minimum, maximum in (("vm_cpu", 1, 256), ("vm_memory_mb", 1024, 1048576),
                                    ("host_disk_reserve_gib", 0, 1048576), ("host_memory_reserve_gib", 0, 1048576),
                                    ("hermes_reserve_count", 0, 10)):
        if type(site.get(name)) is not int or not minimum <= site[name] <= maximum:
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    require_pattern(site, "vm_cpu_quota", r"[1-9][0-9]*%")
    require_pattern(site, "vm_memory_max", r"[1-9][0-9]*[KMGT]?")
    require_pattern(site, "worker_ssh_host", r"(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)")
    for name in ("dashboard_username", "obsidian_username"):
        require_pattern(site, name, r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
    if site.get('worker_harness') not in ('codex', 'claude', 'both', 'none'):
        raise ValueError('worker_harness must be codex, claude, both or none')
    for name in ('hermes_initial_model', 'worker_model', 'worker_codex_model'):
        if name in site and site[name] != '':
            require_pattern(site, name, r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}")
    # Discord IDs are needed only before the first Discord test; empty means pending.
    for alias, name in DISCORD_ALIASES.items():
        if site[name] and not re.fullmatch(r"[0-9]{17,20}", site[name]):
            raise ValueError(f"{alias} must be a numeric Discord ID (Developer Mode → Copy ID)")
    require_pattern(site, "paperclip_owner_email", r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+")
    hosts = site.get("paperclip_allowed_hosts")
    if not isinstance(hosts, list) or not hosts or any(not isinstance(h, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", h) for h in hosts):
        raise ValueError("paperclip_allowed_hosts must list explicit hostnames or IPv4 addresses")
    if "127.0.0.1" not in hosts:
        raise ValueError("paperclip_allowed_hosts must keep 127.0.0.1 for local provisioning")
    for name in ["vm_service", "vm_instance", "vm_user", "guest_user"]:
        if not isinstance(site.get(name), str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", site[name]):
            raise ValueError(f"Creation requires an explicit safe {name}")
    absolute_path(site.get("vm_bootstrap_dir"), "vm_bootstrap_dir")
    state_paths.append("vm_bootstrap_dir")
    # The pinned Debian image plus Hermes dependencies need about 6 GiB.
    if type(site.get("vm_disk_gib")) is not int or site["vm_disk_gib"] < 8:
        raise ValueError("vm_disk_gib must be an integer of at least 8 (GiB)")
    # These directories are recursively owned/mounted by different identities.
    for index, name in enumerate(state_paths):
        path = Path(site[name])
        if len(path.parts) < 4:
            raise ValueError(f"{name} must be a dedicated directory below a storage parent")
        for other in state_paths[:index]:
            previous = Path(site[other])
            if path.is_relative_to(previous) or previous.is_relative_to(path):
                raise ValueError(f"State directories {name} and {other} must not overlap")
        if private.is_relative_to(path) or path.is_relative_to(private):
            raise ValueError('Private deployment inputs must be separate from application state')
        if name in ("vm_data", "vm_root", "paperclip_home") and any(path.is_relative_to(p) for p in PROTECTED_HOME):
            raise ValueError(f"{name} cannot be under /home, /root or /run/user: its service uses ProtectHome")
    if inspect_private:
        for name in (*state_paths, 'private_dir', 'deploy_key', 'guest_known_hosts', 'backup_dir'):
            if Path(site[name]).resolve() != Path(site[name]):
                raise ValueError(f'{name} cannot use a filesystem symlink alias')
    backup = Path(site['backup_dir'])
    for name in (*state_paths, 'private_dir'):
        if backup.is_relative_to(Path(site[name])):
            raise ValueError('backup_dir must be outside all persistent input directories')
    if site["vm_disk"] != site["vm_data"] + "/disk.qcow2":
        raise ValueError("VM disk must be the declared data directory's disk.qcow2")
    if site["vm_data"] == site["vm_root"]:
        raise ValueError("VM data and runtime root must be separate directories")
    ports = ["guest_ssh_port", "guest_dashboard_port", "guest_api_port", "paperclip_port", "paperclip_db_port"]
    if site.get("notes_enabled", False):
        ports.append("notes_sftp_port")
    if site.get("obsidian_enabled", False):
        if not site.get("notes_enabled", False):
            raise ValueError("Obsidian requires the declared notes share")
        ports.append("obsidian_backend_port")
    values = [site.get(name) for name in ports]
    if any(type(value) is not int or not 1024 < value < 65536 for value in values):
        raise ValueError("Host component ports must be explicit integers between 1025 and 65535")
    worker_port = site.get("worker_ssh_port")
    if type(worker_port) is not int or not 0 < worker_port < 65536:
        raise ValueError("worker_ssh_port must name the existing SSH listener")
    values.append(worker_port)
    if len(values) != len(set(values)):
        raise ValueError("Host component ports conflict")
    if inspect_private:
        pin = Path(site["guest_known_hosts"])
        if pin.exists():
            expected = f"[127.0.0.1]:{site['guest_ssh_port']}"
            hosts = [line.split()[0] for line in pin.read_text().splitlines() if line.strip() and not line.startswith("#")]
            if any(host != expected for host in hosts):
                raise ValueError("Fresh guest_known_hosts points at another guest; select separate .env paths before creation")
    if site.get("ui_tailscale_enabled", False):
        require_pattern(site, "ui_tailscale_hostname", r"[a-z0-9][a-z0-9.-]*\.ts\.net")
        tail_ports = [site.get("ui_tail_hermes_port"), site.get("ui_tail_paperclip_port")]
        if site.get("obsidian_enabled", False):
            tail_ports.append(site.get("ui_tail_obsidian_port"))
        if any(type(value) is not int or not 0 < value < 65536 for value in tail_ports) or len(tail_ports) != len(set(tail_ports)):
            raise ValueError("Tailnet HTTPS ports must be valid and distinct")
    return site


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-config", type=Path, default=Path("config/site.yaml"))
    environment = parser.add_mutually_exclusive_group()
    environment.add_argument("--env-file", type=Path, default=Path(".env"))
    environment.add_argument("--no-env", action="store_true", help="Ignore private .env inputs (public checks)")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("playbooks", nargs="*")
    args, native_args = parser.parse_known_args()
    inspection_only = any(flag in native_args for flag in ["--syntax-check", "--list-tasks", "--list-hosts"])
    try:
        site = read_inputs(args.site_config, None if args.no_env else args.env_file, inspect_private=not inspection_only)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except (OSError, yaml.YAMLError):
        # YAML/OS exceptions can embed private values; never echo their contents.
        raise SystemExit("Invalid deployment inputs; check field types, paths, permissions and port conflicts.") from None
    if args.validate:
        print("Deployment paths and component ports validated; no changes made.")
        return 0
    if not args.playbooks:
        parser.error("Supply a playbook or --validate")
    full_site = any(Path(p).resolve() == (ROOT / "deploy/site.yaml") for p in args.playbooks)
    if full_site and not inspection_only:
        if site.get("vm_isolated") is not False:
            raise SystemExit("Complete preservation/firewall bootstrap and select vm_isolated: false before full deployment.")
        if site.get("notes_enabled") is not True or site.get("obsidian_enabled") is not True:
            raise SystemExit("The core site deployment requires notes_enabled and obsidian_enabled.")
    # Unbuffered so the streamed log and terminal show progress as it happens.
    env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "deploy/ansible.cfg"), PYTHONUNBUFFERED="1")
    readonly = inspection_only or "--check" in native_args or all(Path(p).resolve() == ROOT / "deploy/status.yaml" for p in args.playbooks)
    lock = nullcontext()
    if not readonly:
        if not report_checks(check for check in platform_checks() if not check.ready):
            raise SystemExit("Host prerequisites failed; see docs/SETUP.md.")
        sys.path.insert(0, str(ROOT / "implementation"))
        from runtime import maintenance_lock
        result = subprocess.run(["rtk", "proxy", "sudo", "-n", "/usr/local/bin/rtk", "proxy",
                                 sys.executable, str(ROOT / "implementation/runtime.py"), "--prepare-lock"])
        if result.returncode:
            raise SystemExit("Cannot prepare the deployment lock; verify unattended sudo and the root-owned RTK prerequisite.")
        lock = maintenance_lock()
    with lock, tempfile.TemporaryDirectory(prefix="zelab-inputs-") as directory:
        passwords = os.environ.get('ZELAB_PASSWORD_DIR')
        if passwords and not readonly:
            absolute_path(passwords, 'ZELAB_PASSWORD_DIR')
            site['zelab_password_dir'] = passwords
        variables = Path(directory) / "site.json"
        variables.write_text(json.dumps({**site, 'zelab_full_install': full_site and not readonly,
                                        'zelab_controller_python': sys.executable,
                                        'zelab_install_site_json': str(variables)}))
        variables.chmod(0o600)
        command = ["rtk", "proxy", str(ROOT / ".venv/bin/ansible-playbook"), *args.playbooks, "-e", "@" + str(variables), *native_args]
        if readonly:
            return subprocess.run(command, env=env).returncode
        if full_site and restore_install_resources(variables):
            raise SystemExit('Cannot restore unfinished VM installation resources; rerun make after resolving the cause above.')
        try:
            code = logged_run(command, env, Path(site["private_dir"]))
        finally:
            restored = restore_install_resources(variables) if full_site else 0
        return restored or code


def restore_install_resources(variables):
    # A second Ctrl-C must not strand the temporary resources during cleanup.
    previous = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        return subprocess.run(['rtk', 'proxy', 'sudo', '-n', '/usr/local/bin/rtk', 'proxy',
                               sys.executable, str(ROOT / 'deploy/install_resources.py'),
                               'restore', '--site-json', str(variables)]).returncode
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


FAILED = re.compile(r"^(?:fatal|failed): \[[^]]+\]")
MESSAGE = re.compile(r'"msg": "((?:[^"\\]|\\.)*)"')
# HTTP modules put the useful part (e.g. "HTTP Error 429: Too Many Requests") here.
RESPONSE = re.compile(r'"response": "((?:[^"\\]|\\.)*)"')


def install_log(private_dir):
    """Open a private log beside other deployment state; None if unavailable."""
    try:
        logs = private_dir / "logs"
        private_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        logs.mkdir(mode=0o700, exist_ok=True)
        if logs.stat().st_uid != os.getuid():
            return None
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = logs / f"install-{stamp}.log"
        return path, os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w")
    except OSError:
        return None


def logged_run(command, env, private_dir):
    """Stream Ansible output, keep a private copy and summarize the first failure.

    Under deploy/install.py the installer already keeps this output in its own
    private log (ZELAB_INSTALL_LOG), so the failure summary points there.
    """
    caller_log = os.environ.get("ZELAB_INSTALL_LOG")
    opened = None if caller_log else install_log(private_dir)
    path, log = opened if opened else (caller_log, None)
    play = task = cause = None
    hidden_cause = False

    def stopped(_signum, _frame):
        raise KeyboardInterrupt
    # Wrappers such as `rtk proxy` turn Ctrl-C into SIGTERM; summarize either way.
    previous = signal.signal(signal.SIGTERM, stopped)
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    try:
        for line in process.stdout:
            sys.stdout.write(line)
            if log:
                log.write(line)
            if line.startswith("PLAY [") and "RECAP" not in line:
                play = line[6:].split("]", 1)[0]
            elif line.startswith("TASK ["):
                task = line[6:].split("]", 1)[0]
            elif FAILED.match(line) and (cause is None or hidden_cause):
                found, response = MESSAGE.search(line), RESPONSE.search(line)
                if cause is None:
                    failed_at = (play, task)
                cause = (json.loads('"' + found.group(1) + '"') if found
                         else "Ansible hid task details to protect credentials; the private log is also censored.").splitlines()[0][:300]
                hidden_cause = found is None
                if response:
                    cause += " — " + json.loads('"' + response.group(1) + '"').splitlines()[0][:200]
            elif line.strip() == "...ignoring":
                cause = None
                hidden_cause = False
        returncode = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        returncode = process.wait()
        cause, failed_at = "interrupted by the operator", (play, task)
    finally:
        signal.signal(signal.SIGTERM, previous)
        if log:
            log.close()
    if returncode:
        where = failed_at if cause else (play, task)
        print("\nInstallation stopped.", file=sys.stderr)
        print(f"  Phase: {where[0] or 'before the first phase'}", file=sys.stderr)
        print(f"  Step:  {where[1] or 'not started'}", file=sys.stderr)
        print(f"  Cause: {cause or 'Ansible exited with status ' + str(returncode)}", file=sys.stderr)
        if path:
            print(f"  Private log: {path}", file=sys.stderr)
        print("  Next: fix the cause above, then run: rtk proxy make", file=sys.stderr)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
