"""Terminal marks and colors shared by the installer and doctor (standard library only).

Colors appear only on a terminal and respect NO_COLOR; marks fall back to ASCII
when the terminal cannot encode them.
"""

import os
import re
import shutil
import sys


STYLES = {"bold": 1, "dim": 2, "red": 31, "green": 32, "yellow": 33, "cyan": 36}
ESCAPES = re.compile(r"\x1b\[[0-9;]*m")
# Doctor labels: Done, Blocked, Advice, To do.
MARKS = {"PASS": ("✓", "ok", "green"), "FAIL": ("✗", "X", "red"),
         "WARN": ("!", "!", "yellow"), "PENDING": ("○", "-", "cyan")}


def _stream(stream):
    return stream if stream is not None else sys.stdout


def interactive(stream=None):
    """True when the stream reaches a person's terminal.

    `rtk proxy` relays output through a pipe as it arrives, so standard output
    is not a TTY even in a terminal; standard input still is.
    """
    stream = _stream(stream)
    if os.environ.get("TERM", "dumb") == "dumb":
        return False
    if hasattr(stream, "isatty") and stream.isatty():
        return True
    return stream in (sys.__stdout__, sys.__stderr__) and os.isatty(0)


def colored(stream=None):
    if "NO_COLOR" in os.environ:
        return False
    return os.environ.get("CLICOLOR_FORCE", "0") not in ("", "0") or interactive(stream)


def unicode(stream=None):
    encoding = getattr(_stream(stream), "encoding", None)
    return encoding is None or encoding.lower().replace("-", "").replace("_", "") == "utf8"


def paint(text, *styles, stream=None):
    if not styles or not colored(stream):
        return text
    return f"\033[{';'.join(str(STYLES[style]) for style in styles)}m{text}\033[0m"


def plain(text):
    """Text without the color escapes paint() adds."""
    return ESCAPES.sub("", text)


def mark(label, stream=None):
    symbol, ascii_symbol, color = MARKS[label]
    return paint(symbol if unicode(stream) else ascii_symbol, color, "bold", stream=stream)


def terminal_width(stream=None):
    for descriptor in (getattr(_stream(stream), "fileno", lambda: None), lambda: 0):
        try:
            width = os.get_terminal_size(descriptor()).columns
            if width > 0:
                return width
        except (OSError, ValueError, TypeError):
            pass
    return shutil.get_terminal_size((80, 20)).columns


def heading(title, color="cyan", stream=None):
    """A readable section even when colors are unavailable."""
    stream = _stream(stream)
    print("\n  " + paint(title, color, "bold", stream=stream), file=stream)
    print("  " + paint("=" * max(1, min(56, terminal_width(stream) - 4)), color, stream=stream), file=stream)
