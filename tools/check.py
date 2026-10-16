#!/usr/bin/env python3
"""Developer/CI source validation, separate from the operator Make interface.

Test modules run in parallel, each in its own process; modules that run real
playbooks are split further into one process per test, since one of them
would otherwise bound the wall time. A failed job's output is printed in full.
Every check must pass, as before.
"""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
import controller  # noqa: E402

# Prints "<module path> <test id>..." using the real loader, so inherited and
# mixin tests are listed exactly as unittest discover would run them.
LIST_TESTS = '''
import sys, unittest
def ids(suite):
    for item in suite:
        yield from ids(item) if isinstance(item, unittest.TestSuite) else [item.id()]
for path in sys.argv[1:]:
    folder, name = path.rsplit('/', 1)
    print(path, *ids(unittest.TestLoader().discover(folder, pattern=name, top_level_dir=folder)))
'''


def run(argv, env=None):
    started = time.monotonic()
    result = subprocess.run([*map(str, argv)], cwd=ROOT, capture_output=True, text=True,
                            env=dict(os.environ, **(env or {})))
    return result, time.monotonic() - started


def jobs(python):
    """(label, argv, env) for every check after the quick source checks."""
    modules = [path.relative_to(ROOT) for folder in ('deploy', 'implementation')
               for path in (ROOT / folder).glob('test_*.py')]
    # Larger modules run more playbooks; start them first so the pool drains evenly.
    modules.sort(key=lambda path: (ROOT / path).stat().st_size, reverse=True)
    playbooks = [str(path) for path in modules
                 if any(marker in (ROOT / path).read_text() for marker in ('ansible-playbook', 'run_play('))]
    listed = subprocess.run([python, '-c', LIST_TESTS, *playbooks], cwd=ROOT,
                            capture_output=True, text=True)
    split = {}
    for line in listed.stdout.splitlines() if listed.returncode == 0 else []:
        path, *tests = line.split()
        # An import failure is listed as a loader placeholder; the module job reports it.
        if tests and not any(test.startswith('unittest.loader.') for test in tests):
            split[path] = tests
    for path in modules:
        if str(path) in split:
            # Same imports as discover: the module's folder is on sys.path.
            yield from ((str(path), [python, '-m', 'unittest', test], {'PYTHONPATH': str(ROOT / path.parent)})
                        for test in split[str(path)])
        else:
            # Exactly `unittest discover -s <folder>` for one module.
            yield str(path), [python, '-m', 'unittest', 'discover', '-s', path.parent, '-p', path.name], None
    books = [str(path.relative_to(ROOT)) for path in sorted((ROOT / 'deploy').glob('*.yaml'))
             if path.name != 'inventory.yaml']
    yield 'playbook syntax', [python, 'deploy/run.py', '--site-config', 'config/site.example.yaml',
                              '--no-env', *books, '--syntax-check'], None
    yield 'repository', [python, 'tools/check_repository.py'], None


def main():
    if code := controller.main():
        return code
    python = str(controller.PYTHON)
    for argv in (['bash', '-n', 'deploy/bootstrap-access.sh'],
                 ['python3', '-c', "import ast,pathlib; [ast.parse(p.read_text(),filename=str(p)) for root in ('deploy','implementation','tools') for p in pathlib.Path(root).rglob('*.py')]; print('Syntax checks passed')"],
                 ['git', 'diff', '--check']):
        if code := subprocess.run([*argv], cwd=ROOT).returncode:
            return code
    started = time.monotonic()
    planned = list(jobs(python))
    remaining = defaultdict(int)
    for label, _, _ in planned:
        remaining[label] += 1
    tests, busy, failed = defaultdict(int), defaultdict(float), []
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 1) as pool:
        running = {pool.submit(run, argv, env): label for label, argv, env in planned}
        for future in as_completed(running):
            label, (result, seconds) = running[future], future.result()
            found = re.search(r'^Ran (\d+) tests?', result.stderr, re.M)
            tests[label] += int(found.group(1)) if found else 0
            busy[label] = max(busy[label], seconds)
            if result.returncode:
                failed.append((label, result))
            remaining[label] -= 1
            if not remaining[label]:
                ok = all(name != label for name, _ in failed)
                count = f'{tests[label]} tests' if label.endswith('.py') else ''
                print(f"  {'PASS' if ok else 'FAIL'}  {label:<40} {count:>9} {busy[label]:6.1f}s", flush=True)
    for label, result in sorted(failed, key=lambda item: item[0]):
        print(f'\n===== FAILED: {label} (exit {result.returncode}) =====')
        print(result.stdout + result.stderr, end='', flush=True)
    names = sorted({label for label, _ in failed})
    print(f"\n{sum(tests.values())} tests; {'FAILED: ' + ', '.join(names) if names else 'all checks passed'}"
          f" ({time.monotonic() - started:.0f}s)")
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
