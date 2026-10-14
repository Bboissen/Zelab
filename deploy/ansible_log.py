"""Private install logs and the parts of Ansible output the installer reports.

Standard library only: install.py imports this before the controller exists.
Only play names, task names and short failure causes are extracted; task
output itself stays in the private log.
"""

import datetime
import json
import os
from pathlib import Path
import re
import time


FAILED = re.compile(r"^(?:fatal|failed): \[[^]]+\]")
MESSAGE = re.compile(r'"msg": "((?:[^"\\]|\\.)*)"')
# HTTP modules put the useful part (e.g. "HTTP Error 429: Too Many Requests") here.
RESPONSE = re.compile(r'"response": "((?:[^"\\]|\\.)*)"')
CENSORED = "Ansible hid task details to protect credentials; the private log is also censored."


def open_private_log(private_dir):
    """Create a new 0600 install log under private_dir/logs; (path, file) or None.

    None when the directory cannot be used or belongs to another account."""
    try:
        logs = Path(private_dir) / "logs"
        logs.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        logs.mkdir(mode=0o700, exist_ok=True)
        if logs.stat().st_uid != os.getuid():
            return None
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = logs / f"install-{stamp}.log"
        return path, os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w")
    except OSError:
        return None


def play_name(line):
    """The name a `PLAY [...]` line starts, else None (PLAY RECAP is not a play)."""
    if line.startswith("PLAY [") and "RECAP" not in line:
        return line[6:].split("]", 1)[0]
    return None


def task_name(line):
    """The name a `TASK [role : name]` line starts, else None."""
    return line[6:].split("]", 1)[0] if line.startswith("TASK [") else None


def message(line, pattern=MESSAGE):
    """The decoded JSON string of a `"msg"` (or other pattern) field, else None."""
    found = pattern.search(line)
    if not found:
        return None
    try:
        return json.loads('"' + found.group(1) + '"')
    except ValueError:
        return found.group(1)


def first_line(text, limit):
    return (text.splitlines() or [""])[0][:limit]


class Progress:
    """Current play and task, per-task timings and the first failure of one run.

    on_task receives {"play", "task", "seconds"} when a task ends: at the next
    task, at PLAY RECAP, or when the caller calls finish_task().
    """

    def __init__(self, on_task=None, clock=time.monotonic):
        self.play = self.task = self.cause = None
        self.failed_at = (None, None)
        self.hidden = False
        self.on_task, self.clock, self.timed = on_task, clock, None

    def feed(self, line):
        if (name := play_name(line)) is not None:
            self.play = name
        elif (name := task_name(line)) is not None:
            self.finish_task()
            self.task = name
            self.timed = (self.play, name, self.clock())
        elif line.startswith("PLAY RECAP"):
            self.finish_task()
        elif FAILED.match(line) and (self.cause is None or self.hidden):
            # A rescue's safe diagnostic replaces a censored cause but keeps its step.
            found, response = message(line), message(line, RESPONSE)
            if self.cause is None:
                self.failed_at = (self.play, self.task)
            self.cause = first_line(CENSORED if found is None else found, 300)
            self.hidden = found is None
            if response:
                self.cause += " — " + first_line(response, 200)
        elif line.strip() == "...ignoring":
            self.cause, self.hidden = None, False

    def finish_task(self):
        if self.timed and self.on_task:
            play, task, started = self.timed
            self.on_task({"play": play, "task": task, "seconds": round(self.clock() - started, 3)})
        self.timed = None
