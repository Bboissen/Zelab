#!/usr/bin/env python3
"""Install or reapply Zelab with one status line per step (plain `make`).

Tool output (apt, pip, Ansible) goes to a private log; the terminal shows a
checkmark or a cross per step and, on failure, the cause, the log and the next
command. Every step is idempotent, so rerunning resumes. Runs with the system
Python before the controller exists, so it needs only the standard library.
"""

import argparse
import collections
import datetime
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import tempfile
import time

import doctor
import controller
from ansible_log import Progress, message, open_private_log, play_name, task_name
from console import colored, heading, interactive, mark, paint, plain, terminal_width, unicode
import shell


ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv/bin/python"
DEFAULT_PRIVATE_DIR = Path.home() / ".local/state/zelab-deployment"
# Short names for the plays deploy/site.yaml runs, matched by name prefix.
STAGES = (
    ("Check host prerequisites", "Host packages"),
    ("Create the pinned Debian guest", "Hermes VM"),
    ("Start pinned host downloads", "Downloads"),
    ("Verify guest administrator", "Hermes VM"),
    ("Replace retired guest egress", "VM firewall"),
    ("Build pinned upstream Hermes", "Hermes build"),
    ("Install pinned unmodified Hermes", "Hermes"),
    ("Install pinned native Paperclip", "Paperclip"),
    ("Install pinned host agent CLIs", "Host agent CLIs"),
    ("Provision the team tier accounts", "Team tiers"),
    ("Finish pinned Hermes", "Hermes dashboard"),
    ("Configure native Paperclip projects", "Paperclip projects"),
    ("Publish Paperclip's bundled task-bridge", "Paperclip projects"),
    ("Install the task-bridge skill", "Paperclip projects"),
    ("Supervise the native shared Hermes gateway", "Hermes gateway"),
    ("Seed the native Hermes API", "Hermes API"),
    ("Prepare the host vault", "Vault"),
    ("Mount the vault", "Vault"),
    ("Retire the browser Obsidian", "Obsidian"),
    ("Deploy the native Obsidian", "Obsidian"),
    ("Remove superseded", "Cleanup"),
)
APPLICATION = re.compile(r'(\w+) [\w ]+?: (HTTP \d+ ready|HTTP \d+ needs attention|VM stopped)')
SECTIONS = (
    ("Prepare the host", {"Host packages"}),
    ("Build the Hermes VM and runtime", {"Hermes VM", "Downloads", "VM firewall", "Hermes build", "Hermes"}),
    ("Install Paperclip, team tiers and Hermes", {"Paperclip", "Host agent CLIs", "Team tiers",
                                                    "Hermes dashboard", "Paperclip projects",
                                                    "Hermes gateway", "Hermes API"}),
    ("Connect the vault", {"Vault", "Obsidian"}),
    ("Verify services and prepare your guide", {"Cleanup"}),
)


class StepFailed(Exception):
    """A step stopped installation; its lines are already on the terminal."""


def stage_of(play):
    return next((label for prefix, label in STAGES if play.startswith(prefix)), play[:40])


def duration(seconds):
    return f"{seconds:.0f}s" if seconds < 60 else f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


class Log:
    """Private copy of all tool output; buffered until its directory is known."""

    def __init__(self):
        self.buffer, self.file, self.path = [], None, None

    def write(self, text):
        if self.file:
            self.file.write(text)
            self.file.flush()
        else:
            self.buffer.append(text)

    def open(self, private_dir):
        if self.file:
            return
        opened = open_private_log(private_dir)
        if not opened:
            return
        self.path, self.file = opened
        self.file.write("".join(self.buffer))
        self.file.flush()
        self.buffer = []

    def close(self):
        if self.file:
            self.file.close()
            self.file = None


class Screen:
    """One live line for the running step; finished steps keep a mark."""

    def __init__(self, stream=None):
        stream = stream if stream is not None else sys.stdout
        self.stream, self.live = stream, interactive(stream)
        self.frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if unicode(stream) else "|/-\\"
        self.lock, self.title, self.detail, self.started = threading.Lock(), None, "", 0.0
        self.stopped = threading.Event()
        self.section_index = None
        if self.live:
            threading.Thread(target=self._spin, daemon=True).start()

    def _spin(self):
        frame = 0
        while not self.stopped.wait(0.1):
            with self.lock:
                if self.title:
                    self._draw(self.frames[frame % len(self.frames)])
            frame += 1

    def _draw(self, frame):
        width = terminal_width(self.stream)
        elapsed = duration(time.monotonic() - self.started)
        ellipsis = "…" if unicode(self.stream) else "."
        title = self.title[:max(width - len(elapsed) - 12, 1)]
        room = max(width - len(title) - len(elapsed) - 12, 0)
        detail = self.detail if len(self.detail) <= room else self.detail[:max(room - 1, 0)] + ellipsis if room else ""
        line = f"  {paint(frame, 'cyan', stream=self.stream)} {title}  " + (
            (detail + "  " if detail else "") + elapsed)
        self.stream.write("\r\033[2K" + line)
        self.stream.flush()

    def start(self, title):
        with self.lock:
            self.title, self.detail, self.started = title, "", time.monotonic()
            if self.live:
                self._draw(self.frames[0])

    def update(self, detail):
        with self.lock:
            self.detail = detail

    def finish(self, label, note="", lines=()):
        with self.lock:
            elapsed = time.monotonic() - self.started
            if self.live:
                self.stream.write("\r\033[2K")
            timing = f"  {duration(elapsed)}" if elapsed >= 1 else ""
            print(f"  {mark(label, stream=self.stream)} {self.title:<19} {note}{timing}", file=self.stream)
            for line in lines:
                print(f"      {line}", file=self.stream)
            self.title = None
            self.stream.flush()

    def say(self, text):
        symbol = "›" if unicode(self.stream) else ">"
        print(f"  {paint(symbol, 'cyan', stream=self.stream)} {text}", file=self.stream, flush=True)

    def section(self, index):
        if index == self.section_index:
            return
        with self.lock:
            if self.live and self.title:
                self.stream.write("\r\033[2K")
            self.title = None
            self.section_index = index
            heading(f"{index + 1} / {len(SECTIONS)}  {SECTIONS[index][0]}", stream=self.stream)
            self.progress(index)
            print(file=self.stream)

    def progress(self, completed):
        filled = 20 * completed // len(SECTIONS)
        bar = "#" * filled + "-" * (20 - filled)
        print(f"  [{paint(bar, 'green', stream=self.stream)}] {completed * 100 // len(SECTIONS):3d}%"
              f"  ({completed}/{len(SECTIONS)} stages complete)", file=self.stream, flush=True)

    def close(self):
        self.stopped.set()


class Installer:
    def __init__(self, site_config, env_file, guide_mode="auto"):
        self.site_config, self.env_file = site_config, env_file
        self.guide_mode = guide_mode
        self.log, self.screen = Log(), Screen()
        self.applications = {"Paperclip", "Hermes", "Obsidian"}
        self.password_dir = None

    def capture(self, argv, on_line=None, env=None, terminal_output=False):
        """Run one tool with its output in the private log; return (code, last lines)."""
        started = time.monotonic()
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.log.write(f"\n$ {' '.join(argv)}\nStarted: {stamp}\n")
        tail = collections.deque(maxlen=20)
        child_env = dict(os.environ, PYTHONUNBUFFERED="1", NO_COLOR="1", CLICOLOR_FORCE="0", **(env or {}))
        if terminal_output and colored():
            child_env.pop("NO_COLOR", None)
            child_env["CLICOLOR_FORCE"] = "1"
        process = subprocess.Popen(shell.rtk(*argv), cwd=ROOT, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, bufsize=1,
                                   env=child_env)
        try:
            for line in process.stdout:
                self.log.write(line)
                tail.append(line.rstrip())
                if on_line:
                    on_line(line)
            return process.wait(), list(tail)
        except BaseException:
            process.terminate()
            restoring = 'deploy/run.py' in argv and 'deploy/site.yaml' in argv
            if restoring:
                self.screen.update('Restoring VM limits before stopping')
            previous = {sig: signal.signal(sig, signal.SIG_IGN)
                        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)} if restoring else {}
            try:
                try:
                    process.wait(timeout=360 if restoring else 5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
            raise
        finally:
            process.stdout.close()
            self.log.write(f"Elapsed: {time.monotonic() - started:.3f}s\n")

    def fail(self, note, lines=()):
        self.screen.finish("FAIL", note, lines)
        raise StepFailed

    def tool_failed(self, note, tail):
        """Keep failure output to one cause; full tool output stays in the log."""
        lines = [line.strip() for line in tail if line.strip()
                 and not re.match(r"make(?:\[\d+\])?: \*\*\*", line)]
        errors = [line for line in lines if re.search(r"error|failed|must |requires |could not", line, re.I)]
        cause = (errors or lines or ["see the private log"])[-1][:300]
        self.fail(note, [f"Cause: {cause}"])

    def inputs(self):
        return ["--site-config", str(self.site_config), "--env-file", str(self.env_file)]

    def host(self):
        self.screen.start("Host")
        # Installation adds python3-venv itself, so its absence is not a blocker here.
        failed = [check for check in doctor.host_checks() if check.label == "FAIL" and check.name != "Python venv"]
        if failed:
            self.fail("not ready", [f"{mark('FAIL')} {check.name}: {check.detail}" for check in failed])
        try:
            release = platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
        except OSError:
            release = "Linux"
        self.screen.finish("PASS", f"{release} · {platform.machine()} · KVM · Docker")

    def administration(self):
        granted = doctor.command_succeeds(["sudo", "-n", "/usr/bin/python3", "-c",
                                           "import os,sys; sys.exit(os.geteuid() != 0)"])
        if not granted:
            self.screen.say("Zelab needs your sudo password once, so the rest can run unattended.")
            if subprocess.run(shell.rtk("sudo", "-p", "    sudo password for %u: ", "-v")).returncode:
                self.screen.start("Administrator")
                self.fail("sudo was not authorized")
        self.screen.start("Administrator")
        code, tail = self.capture(["bash", "deploy/bootstrap-access.sh"])
        if code:
            self.tool_failed("bootstrap failed", tail)
        user = os.environ.get("USER", "")
        self.screen.finish("PASS", "unattended sudo ready" if granted else
                           f"granted · revoke with: sudo rm /etc/sudoers.d/zelab-agent-{user}")

    def configuration(self):
        self.screen.start("Site configuration")
        code, tail = self.capture(["python3", "deploy/configure.py", "--site-config", str(self.site_config)])
        if code:
            self.tool_failed("could not be written", tail)
        created = [line.strip() for line in tail if line.startswith("  ")]
        if any(line.startswith("Created") for line in tail):
            self.screen.finish("PASS", f"created {self.site_config} from this host", created)
        else:
            self.screen.finish("PASS", str(self.site_config))

    def controller(self):
        self.screen.start("Controller")
        if controller.ready():
            self.screen.finish("PASS", "Ansible ready")
            return
        code, tail = self.capture(['python3', 'deploy/controller.py'])
        if code:
            self.tool_failed("Ansible could not be installed", tail)
        self.screen.finish("PASS", "Ansible installed")

    def open_log(self):
        """The log lives in the site's private directory once the inputs can be read."""
        code = ("import sys,json; sys.path.insert(0, 'deploy'); from pathlib import Path; from run import read_inputs; "
                "site=read_inputs(Path(sys.argv[1]), Path(sys.argv[2])); "
                "print(json.dumps({key:site[key] for key in ('private_dir','obsidian_enabled')}))")
        result = subprocess.run(["rtk", "proxy", str(VENV_PYTHON), "-c", code, str(self.site_config),
                                 str(self.env_file)], cwd=ROOT, capture_output=True, text=True)
        settings = json.loads(result.stdout) if result.returncode == 0 else {}
        if not settings.get("obsidian_enabled", False):
            self.applications.discard("Obsidian")
        self.log.open(settings.get("private_dir", DEFAULT_PRIVATE_DIR))

    def bots(self):
        self.screen.start('Bot reconciliation')
        code, tail = self.capture([str(VENV_PYTHON), 'deploy/bots.py', 'refresh', *self.inputs(),
                                   '--runtime-dir', self.password_dir])
        if code:
            self.tool_failed('bot reconciliation failed', tail)
        warnings = [line.strip() for line in tail if line.startswith('  WARN:')]
        self.screen.finish('PASS', 'skills, scoped keys, adapters and conversation freshness', warnings)
        # Hermes' wizards need the terminal: capture() would hide them.
        command = ['rtk', 'proxy', str(VENV_PYTHON), 'deploy/bots.py', 'installer', *self.inputs(),
                   '--runtime-dir', self.password_dir]
        if subprocess.run(command, cwd=ROOT).returncode:
            self.screen.start('Hermes bots')
            self.fail('bot setup stopped; finish it with: rtk proxy make bot')

    def notes(self):
        # Offered once, in the terminal; a failure here never fails the install.
        command = ['rtk', 'proxy', str(VENV_PYTHON), 'deploy/notes.py', 'offer', *self.inputs()]
        if subprocess.run(command, cwd=ROOT).returncode:
            print('  Desktop notes are not set up yet; finish with: rtk proxy make notes')

    def ui_passwords(self, action):
        # Inherit the terminal; neither input nor credential values enter capture/logs.
        command = ['rtk', 'proxy', str(VENV_PYTHON), 'deploy/passwords.py', action,
                   *self.inputs(), '--runtime-dir', self.password_dir]
        if subprocess.run(command, cwd=ROOT).returncode:
            self.screen.start('UI passwords')
            self.fail('password setup stopped; rerun make in your terminal')

    def readiness(self):
        self.screen.start("Readiness")
        # Recover an orphaned runtime boost before checking available memory.
        # Otherwise a killed installer could leave too little headroom to reach
        # run.py, whose finally block normally restores the resources.
        code, tail = self.capture(shell.as_root(VENV_PYTHON, 'deploy/install_resources.py', 'restore',
                                                *self.inputs()))
        if code:
            self.tool_failed('VM resource recovery failed', tail)
        code, tail = self.capture([str(VENV_PYTHON), "deploy/doctor.py", *self.inputs(), "--brief"],
                                  env={"ZELAB_PASSWORD_DIR": self.password_dir or ''})
        if code:
            self.fail("blocked", [line.strip() for line in tail if line.startswith("  ")])
        self.screen.finish("PASS", "disk, memory, ports and Tailscale")

    def deploy(self):
        state = {"stage": None, "changes": 0, "task": "", "cause": None,
                 "started": 0.0, "resources": "", "deployment_failed": False,
                 "failed_stage": None, "restored": False}
        # Per-task durations for speed work; names only, never task output.
        tasks = Progress(on_task=lambda record: self.log.write('TASK_TIMING ' + json.dumps(record) + '\n'))

        def close_stage(result="PASS"):
            if state["stage"]:
                changes = state["changes"]
                self.log.write('TIMING ' + json.dumps({'stage': state['stage'], 'result': result,
                               'seconds': round(time.monotonic() - state['started'], 3),
                               'changes': changes}) + '\n')
                if result == "PASS":
                    note = "no changes" if not changes else f"{changes} change{'s' * (changes > 1)}"
                    if state['resources']:
                        note += ' · ' + state['resources']
                    self.screen.finish("PASS", note)

        def on_line(line):
            tasks.feed(line)
            if line.startswith('RESOURCE RESTORE START'):
                if state['deployment_failed']:
                    close_stage('FAIL')
                    state['failed_stage'] = state['stage']
                    self.screen.finish('FAIL', f"stopped at: {state['task'] or 'start'}",
                                       ["Cause: " + (state['cause'] or 'see the private log')])
                else:
                    close_stage()
                state.update(stage='VM normal limits', changes=0, started=time.monotonic(), resources='',
                             task='Restore normal VM resources and wait for startup')
                self.screen.start(state['stage'])
            elif line.startswith('RESOURCE RESTORE DONE '):
                resources = json.loads(line.removeprefix('RESOURCE RESTORE DONE '))
                state.update(changes=1, restored=True,
                             resources=f"{resources['cpu']} vCPU, {resources['memory_mb']} MiB RAM, {resources['cpu_quota']} quota")
            elif line.strip() == 'Installation stopped.':
                state['deployment_failed'] = True
            elif line.startswith('Installation resources: '):
                state['cause'] = line.removeprefix('Installation resources: ').strip()
            elif (text := message(line) or '').startswith('Installation VM resources: '):
                state['resources'] = text.removeprefix('Installation VM resources: ')
            elif (play := play_name(line)) is not None:
                stage = stage_of(play)
                if stage != state["stage"]:
                    close_stage()
                    state.update(stage=stage, changes=0, started=time.monotonic(), resources='')
                    section = next((index for index, (_, stages) in enumerate(SECTIONS) if stage in stages), 2)
                    self.screen.section(section)
                    self.screen.start(stage)
            elif (task := task_name(line)) is not None:
                state["task"] = task.split(" : ", 1)[-1]
                self.screen.update(state["task"])
            elif line.startswith("changed: ["):
                state["changes"] += 1
            elif line.startswith("  Cause: "):
                state["cause"] = line[9:].strip()

        self.screen.start("Deployment")
        try:
            code, _ = self.capture([str(VENV_PYTHON), "deploy/run.py", *self.inputs(), "deploy/site.yaml"],
                                   on_line, env={"ZELAB_INSTALL_LOG": str(self.log.path or ""),
                                                 "ZELAB_PASSWORD_DIR": self.password_dir or ''})
        finally:
            tasks.finish_task()
        if code:
            if state['failed_stage'] and state['restored']:
                close_stage()
                raise StepFailed
            close_stage("FAIL")
            self.fail(f"stopped at: {state['task'] or 'start'}",
                      [paint("Cause: ", "bold") + (state["cause"] or f"exit status {code}")])
        if state["stage"]:
            close_stage()
        else:
            self.fail("no deployment stages reported", ["Cause: see the private log"])

    def services(self):
        self.screen.start("Services")
        results = {}

        def on_line(line):
            if found := APPLICATION.fullmatch(message(line) or ''):
                results[found.group(1)] = found.group(2)

        code, _ = self.capture([str(VENV_PYTHON), "deploy/run.py", *self.inputs(), "deploy/status.yaml"], on_line)
        waiting = sorted(name for name in self.applications if results.get(name) not in ("HTTP 200 ready", "HTTP 401 ready"))
        if code or waiting:
            self.fail("not ready", ["Cause: " + (", ".join(waiting) + " not answering" if waiting else "status check failed"),
                                    "Check the private log, fix the cause, then rerun: rtk proxy make"])
        self.screen.finish("PASS", ", ".join(sorted(self.applications)) + " answer")

    def summary(self):
        self.screen.start("Setup guide")
        lines = []
        code, _ = self.capture([str(VENV_PYTHON), "deploy/doctor.py", *self.inputs(),
                                "--brief", "--open", "--after-install", "--guide-mode", self.guide_mode],
                               on_line=lambda line: lines.append(line.rstrip()), terminal_output=True,
                               env={"ZELAB_PASSWORD_DIR": self.password_dir or ''})
        plain_lines = [plain(line) for line in lines]
        if code and not any(line.strip().startswith(("✗", "X ")) for line in plain_lines):
            self.tool_failed("could not generate guide", lines)
        self.screen.finish("FAIL" if code else "PASS", "could not be completed" if code else "ready")
        if not code:
            print()
            self.screen.progress(len(SECTIONS))
        for line in lines:
            print(line)
        if code:
            raise StepFailed
        if self.log.path:
            print(f"\n  Install log: {self.log.path}\n")

    def single_run(self, shm=Path('/dev/shm')):
        """One make at a time; then remove plaintext working copies a killed run left behind."""
        self.run_lock = open(shm / f'zelab-make-{os.getuid()}.lock', 'a')
        try:
            fcntl.flock(self.run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another make is running for this operator; wait for it to finish.') from None
        for stale in shm.glob('zelab-passwords-*'):
            if stale.is_dir() and not stale.is_symlink() and stale.stat().st_uid == os.getuid():
                shutil.rmtree(stale, ignore_errors=True)

    def run(self):
        host = platform.node() or "this host"
        heading("ZELAB INSTALLER")
        print(f"  {host} | detailed output in a private log")
        print("  Progress counts completed stages; download and build times vary.")
        try:
            self.screen.section(0)
            self.host()
            self.administration()
            self.configuration()
            self.controller()
            self.open_log()
            self.single_run()
            # Linux RAM filesystem: plaintext inputs are never retained in Git,
            # environment values, arguments, logs or the persistent deployment state.
            with tempfile.TemporaryDirectory(prefix='zelab-passwords-', dir='/dev/shm') as directory:
                self.password_dir = directory
                self.ui_passwords('prepare')
                self.readiness()
                self.deploy()
                self.screen.section(4)
                self.services()
                self.bots()
                self.notes()
                self.ui_passwords('finish')
                self.summary()
            self.password_dir = None
        except StepFailed:
            self.after_failure()
            return 1
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            if not self.screen.title:
                self.screen.start("Installer")
            self.screen.finish("FAIL", str(error).splitlines()[0][:300])
            self.log.write(f"\nInstaller error: {error}\n")
            self.after_failure()
            return 1
        except KeyboardInterrupt:
            if self.screen.title:
                self.screen.finish("FAIL", "interrupted")
            self.after_failure()
            return 130
        return 0

    def after_failure(self):
        self.log.open(DEFAULT_PRIVATE_DIR)
        print()
        if self.log.path:
            print(f"  Log:  {self.log.path}")
        print(f"  Next: fix the cause above, then rerun: {paint('rtk proxy make', 'bold')}")
        python = VENV_PYTHON if (ROOT / ".venv/.controller-ready").exists() else "python3"
        try:
            code, lines = self.capture([str(python), "deploy/doctor.py", *self.inputs(), "--open", "--quiet",
                                        "--guide-mode", self.guide_mode], terminal_output=True)
        except (OSError, ValueError, subprocess.SubprocessError):
            code, lines = 1, []
        guide_index = next((index for index, line in enumerate(lines)
                            if plain(line).strip().startswith("Guide:")), None)
        if not code and guide_index is not None:
            for line in lines[guide_index:]:
                print("  " + line)
        else:
            print(f"  Guide: {(ROOT / 'docs/checklist.html').as_uri()}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-config", type=Path, default=Path("config/site.yaml"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--guide-mode", choices=("auto", "local", "remote"), default="auto")
    args = parser.parse_args(argv)
    runner = Installer(args.site_config, args.env_file, args.guide_mode)

    def stopped(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stopped)
    try:
        return runner.run()
    finally:
        signal.signal(signal.SIGTERM, previous)
        runner.screen.close()
        runner.log.close()


if __name__ == "__main__":
    raise SystemExit(main())
