#!/usr/bin/env python3
"""Prepare the hash-locked controller as an internal installer dependency."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
READY = ROOT / '.venv/.controller-ready'
LOCK = ROOT / 'deploy/requirements-controller.txt'
PYTHON = ROOT / '.venv/bin/python'


def ready():
    return PYTHON.is_file() and READY.is_file() and READY.stat().st_mtime >= LOCK.stat().st_mtime


def main():
    if ready():
        return 0
    if sys.version_info < (3, 12):
        raise SystemExit('Controller requires Python 3.12 or newer.')
    for argv in ([sys.executable, '-m', 'venv', str(ROOT / '.venv')],
                 [str(PYTHON), '-m', 'pip', 'install', '--disable-pip-version-check',
                  '--require-hashes', '-r', str(LOCK)]):
        code = subprocess.run(['rtk', 'proxy', *argv], cwd=ROOT).returncode
        if code:
            return code
    READY.touch()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
