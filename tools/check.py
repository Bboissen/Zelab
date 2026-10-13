#!/usr/bin/env python3
"""Developer/CI source validation, separate from the operator Make interface."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
import controller


def main():
    if code := controller.main():
        return code
    python = str(controller.PYTHON)
    books = [str(path.relative_to(ROOT)) for path in sorted((ROOT / 'deploy').glob('*.yaml'))
             if path.name != 'inventory.yaml']
    commands = [
        ['bash', '-n', 'deploy/bootstrap-access.sh'],
        ['python3', '-c', "import ast,pathlib; [ast.parse(p.read_text(),filename=str(p)) for root in ('deploy','implementation','tools') for p in pathlib.Path(root).rglob('*.py')]; print('Syntax checks passed')"],
        ['git', 'diff', '--check'],
        [python, '-m', 'unittest', 'discover', '-s', 'deploy', '-p', 'test_*.py'],
        [python, '-m', 'unittest', 'discover', '-s', 'implementation', '-p', 'test_*.py'],
        [python, 'deploy/run.py', '--site-config', 'config/site.example.yaml', '--no-env', *books, '--syntax-check'],
        [python, 'tools/check_repository.py'],
    ]
    for argv in commands:
        if code := subprocess.run(['rtk', 'proxy', *argv], cwd=ROOT).returncode:
            return code
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
