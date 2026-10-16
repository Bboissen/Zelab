"""Read private deployment paths as data, then invoke the native Ansible CLI."""

import argparse
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

from ansible_log import Progress, open_private_log
from doctor import platform_checks, report_checks
from install_resources import resource_fraction
from configure import operator
import shell


ROOT = Path(__file__).resolve().parents[1]


ENV_FIELDS = {
    "ZELAB_PRIVATE_DIR": "private_dir",
    "ZELAB_DEPLOY_KEY": "deploy_key",
    "ZELAB_KNOWN_HOSTS": "guest_known_hosts",
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
    user = site.get("operator_user")
    if isinstance(user, str) and re.fullmatch(r"[a-z_][a-z0-9_-]{0,47}", user):
        home = operator_home(user)
        # Environment paths belong only to the account launching this checkout.
        # Do not apply one user's XDG/Codex settings to a different declared owner.
        current = pwd.getpwuid(os.getuid()).pw_name == user and os.geteuid() != 0
        state_home = (os.environ.get('XDG_STATE_HOME') or f'{home}/.local/state') if current else f'{home}/.local/state'
        instance = site.get('vm_instance', 'zelab')
        suffix = '/' + instance if isinstance(instance, str) and instance != 'zelab' else ''
        site.setdefault("private_dir", f"{state_home}/zelab-deployment{suffix}")
        site.setdefault("backup_dir", f"{home}/backups/Zelab{suffix}")
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
        site.update(read_env(env_path))
    derive_defaults(site)
    resource_fraction(site.get('vm_install_resource_fraction', 0.7))
    check_private_inputs(site)
    check_identities(site)
    site["zelab_projects"], site["zelab_tiers"], site["zelab_team"], site["zelab_project_replacements"] = read_projects(site)
    remote = site.get("notes_sync_remote", "")
    if not isinstance(remote, str) or (remote and not re.fullmatch(
            r"(?:git@github\.com:|ssh://git@github\.com/)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?(?:\.git)?", remote)):
        raise ValueError("notes_sync_remote must be empty or a GitHub SSH address such as git@github.com:OWNER/REPO.git")
    state_paths = ["vm_data", "vm_root", "paperclip_home", "projects_dir", *check_applications(site)]
    check_resources_and_names(site)
    state_paths.append("vm_bootstrap_dir")
    check_state_layout(site, state_paths, inspect_private)
    check_ports(site)
    if inspect_private:
        check_guest_pin(site)
    if site.get("ui_tailscale_enabled", False):
        check_tailnet(site)
    return site


def read_projects(site):
    """Validated projects, tiers, team and replacements from the local inputs."""
    import projects
    paths = []
    for key, default in (("projects_config", "config/projects.yaml"), ("team_config", "config/team.yaml")):
        name = site.get(key, default)
        if not isinstance(name, str) or not name:
            raise ValueError(f"{key} must name a file")
        path = Path(name) if name.startswith("/") else ROOT / name
        if path.is_symlink():
            raise ValueError(f"{key} cannot be a symlink")
        paths.append(path)
    local, team = projects.read(paths[0]), projects.read_team(paths[1])
    return local["projects"], projects.tiers(team, local), team, local['project_replacements']


def read_env(env_path):
    """The three .env path selectors, as data; never shell syntax."""
    values, seen = {}, set()
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
        values[ENV_FIELDS[name]] = absolute_path(value, name)
        seen.add(name)
    return values


def check_private_inputs(site):
    if not site.get("operator_user"):
        raise ValueError("Set operator_user in config/site.yaml (make writes it for you)")
    paths = ["private_dir", "deploy_key", "guest_known_hosts", "vm_disk", "vm_data", "vm_root",
             "paperclip_home", "projects_dir", "backup_dir", "hermes_install_dir"]
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


def check_identities(site):
    for name in ("vm_isolated", "notes_enabled", "obsidian_enabled", "ui_tailscale_enabled"):
        if type(site.get(name)) is not bool:
            raise ValueError(f"{name} must be an explicit YAML boolean")
    identities = ("operator_user", "vm_user", "paperclip_user")
    for name in (*identities, "guest_user", "vm_service", "vm_instance"):
        require_pattern(site, name, r"[a-z_][a-z0-9_-]{0,47}")
    users = [site[name] for name in identities]
    if len(users) != len(set(users)) or "root" in users or site["guest_user"] == "root":
        raise ValueError("Operator and service identities must be distinct non-root accounts")


def check_applications(site):
    """Validate the optional notes/Obsidian inputs; return their state path names."""
    state_paths = []
    for enabled, name in (("notes_enabled", "notes_vault_dir"), ("notes_enabled", "vault_history_dir"),
                          ("obsidian_enabled", "obsidian_state_dir")):
        if site[enabled]:
            absolute_path(site.get(name), name)
            state_paths.append(name)
    if site["notes_enabled"]:
        absolute_path(site.get("notes_guest_root"), "notes_guest_root")
    return state_paths


def check_resources_and_names(site):
    for name, minimum, maximum in (("vm_cpu", 1, 256), ("vm_memory_mb", 1024, 1048576),
                                    ("host_disk_reserve_gib", 0, 1048576), ("host_memory_reserve_gib", 0, 1048576)):
        if type(site.get(name)) is not int or not minimum <= site[name] <= maximum:
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    require_pattern(site, "vm_cpu_quota", r"[1-9][0-9]*%")
    require_pattern(site, "vm_memory_max", r"[1-9][0-9]*[KMGT]?")
    require_pattern(site, "worker_ssh_host", r"(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)")
    for name in ("dashboard_username", "obsidian_username"):
        require_pattern(site, name, r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
    for name in ('hermes_initial_model',):
        if name in site and site[name] != '':
            require_pattern(site, name, r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}")
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
    # The pinned Debian image plus Hermes dependencies need about 6 GiB.
    if type(site.get("vm_disk_gib")) is not int or site["vm_disk_gib"] < 8:
        raise ValueError("vm_disk_gib must be an integer of at least 8 (GiB)")


def check_state_layout(site, state_paths, inspect_private):
    """These directories are recursively owned/mounted by different identities."""
    private = Path(site['private_dir'])
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


def check_ports(site):
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


def check_guest_pin(site):
    pin = Path(site["guest_known_hosts"])
    if pin.exists():
        expected = f"[127.0.0.1]:{site['guest_ssh_port']}"
        hosts = [line.split()[0] for line in pin.read_text().splitlines() if line.strip() and not line.startswith("#")]
        if any(host != expected for host in hosts):
            raise ValueError("Fresh guest_known_hosts points at another guest; select separate .env paths before creation")


def check_tailnet(site):
    require_pattern(site, "ui_tailscale_hostname", r"[a-z0-9][a-z0-9.-]*\.ts\.net")
    tail_ports = [site.get("ui_tail_hermes_port"), site.get("ui_tail_paperclip_port")]
    if site.get("obsidian_enabled", False):
        tail_ports.append(site.get("ui_tail_obsidian_port"))
    if any(type(value) is not int or not 0 < value < 65536 for value in tail_ports) or len(tail_ports) != len(set(tail_ports)):
        raise ValueError("Tailnet HTTPS ports must be valid and distinct")


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
        if site.get("notes_enabled") is not True:
            raise SystemExit("The core site deployment requires notes_enabled.")
    # Unbuffered so the streamed log and terminal show progress as it happens.
    env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "deploy/ansible.cfg"), PYTHONUNBUFFERED="1")
    readonly = inspection_only or "--check" in native_args or all(Path(p).resolve() == ROOT / "deploy/status.yaml" for p in args.playbooks)
    lock = nullcontext()
    if not readonly:
        if not report_checks(check for check in platform_checks() if not check.ready):
            raise SystemExit("Host prerequisites failed; see docs/SETUP.md.")
        sys.path.insert(0, str(ROOT / "implementation"))
        from runtime import maintenance_lock
        result = subprocess.run(shell.command(*shell.as_root(sys.executable, ROOT / "implementation/runtime.py",
                                                          "--prepare-lock")))
        if result.returncode:
            raise SystemExit("Cannot prepare the deployment lock; verify unattended sudo.")
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
        command = [str(ROOT / ".venv/bin/ansible-playbook"), *args.playbooks, "-e", "@" + str(variables), *native_args]
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
        return subprocess.run(shell.command(*shell.as_root(sys.executable, ROOT / 'deploy/install_resources.py',
                                                       'restore', '--site-json', variables))).returncode
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def logged_run(command, env, private_dir):
    """Stream Ansible output, keep a private copy and summarize the first failure.

    Under deploy/install.py the installer already keeps this output in its own
    private log (ZELAB_INSTALL_LOG), so the failure summary points there.
    """
    caller_log = os.environ.get("ZELAB_INSTALL_LOG")
    opened = None if caller_log else open_private_log(private_dir)
    path, log = opened if opened else (caller_log, None)
    progress = Progress()

    def stopped(_signum, _frame):
        raise KeyboardInterrupt
    # Summarize both terminal interrupts and termination by a supervising process.
    previous = signal.signal(signal.SIGTERM, stopped)
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    try:
        for line in process.stdout:
            sys.stdout.write(line)
            if log:
                log.write(line)
            progress.feed(line)
        returncode = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        returncode = process.wait()
        progress.cause, progress.failed_at = "interrupted by the operator", (progress.play, progress.task)
    finally:
        signal.signal(signal.SIGTERM, previous)
        if log:
            log.close()
    if returncode:
        cause = progress.cause
        where = progress.failed_at if cause else (progress.play, progress.task)
        print("\nInstallation stopped.", file=sys.stderr)
        print(f"  Phase: {where[0] or 'before the first phase'}", file=sys.stderr)
        print(f"  Step:  {where[1] or 'not started'}", file=sys.stderr)
        print(f"  Cause: {cause or 'Ansible exited with status ' + str(returncode)}", file=sys.stderr)
        if path:
            print(f"  Private log: {path}", file=sys.stderr)
        print("  Next: fix the cause above, then run: make", file=sys.stderr)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
