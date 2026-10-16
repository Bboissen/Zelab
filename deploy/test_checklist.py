"""Keep the checklist, guide, HTML page and documented commands consistent."""

from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest

import checklist


ROOT = Path(__file__).resolve().parents[1]


class ChecklistTest(unittest.TestCase):
    def test_public_html_matches_the_checklist_data(self):
        self.assertEqual((ROOT / 'docs/checklist.html').read_text(), checklist.render_html(),
                         'Regenerate with: .venv/bin/python deploy/checklist.py --html docs/checklist.html')

    def test_setup_guide_lists_every_checklist_item_in_order(self):
        guide = (ROOT / 'docs/SETUP.md').read_text()
        positions = [guide.find(f'### {item.title}\n') for item in checklist.ITEMS]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_documented_make_commands_exist(self):
        makefile = (ROOT / 'Makefile').read_text()
        targets = set(re.findall(r'^([a-z][a-z0-9 /-]*):', makefile, re.M))
        targets = {name for line in targets for name in line.split()}
        self.assertEqual(targets, {'all', 'clean', 'fclean', 're', 'help', 'backup', 'restore', 'bot', 'notes'})
        for doc in ['README.md', 'docs/SETUP.md', 'docs/OPERATIONS.md']:
            # Match command examples, not prose such as "make asks for a password".
            text = (ROOT / doc).read_text()
            examples = re.findall(r'(?<!`)`([^`\n]+)`(?!`)', text)
            examples += re.findall(r'^```(?:sh|bash)\n(.*?)^```', text, re.M | re.S)
            for example in examples:
                for command in re.findall(r'^make ([a-z][a-z-]*)', example, re.M):
                    with self.subTest(doc=doc, command=command):
                        self.assertIn(command, targets)

    def test_html_status_snapshot_escapes_details(self):
        page = checklist.render_html({'host': ('PASS', '<script>alert(1)</script>')}, 'now',
                                     {'host': [('FAIL', 'Docker', '<img src=x onerror=alert(1)>')]})
        self.assertNotIn('<script>alert(1)', page)
        self.assertNotIn('<img', page)
        self.assertIn('class="status PASS"', page)

    def test_every_step_is_a_guide_heading_with_a_checkbox(self):
        headings = re.findall(r'^### (.*)$', (ROOT / 'docs/SETUP.md').read_text(), re.M)
        self.assertEqual(headings, list(checklist.STEPS))
        page = checklist.render_html()
        for item in checklist.ITEMS:
            self.assertIn(f'id="step-{item.key}"', page)

    def test_private_guide_marks_done_steps_and_names_the_next_one(self):
        page = checklist.render_html({'host': ('PASS', 'Docker'), 'admin': ('PENDING', 'sudo')}, 'now',
                                     link_base=ROOT / 'docs')
        self.assertIn('1 of 8 steps done', page)
        self.assertIn('Next: <a href="#admin">', page)
        self.assertIn('id="step-host" checked disabled', page)

    def test_steps_show_one_line_and_fold_the_rest(self):
        page = checklist.render_html({'host': ('PASS', ''), 'admin': ('PENDING', 'sudo')}, 'now',
                                     {'admin': [('PENDING', 'Sudo', 'Run `sudo -v`'), ('PASS', 'Disk', 'ok')]})
        self.assertIn('class="step PENDING next" id="admin"', page)
        self.assertIn('<p class="hint PENDING"><strong>Sudo</strong>: Run <code>sudo -v</code></p>', page)
        self.assertNotIn('<strong>Disk</strong>', page)
        self.assertEqual(page.count('<details open>'), 1)
        self.assertNotIn('<h3', page)

    def test_private_guide_distinguishes_tailnet_links_from_server_local_urls(self):
        apps = [{'name': 'Hermes', 'url': 'https://box.example.ts.net:8444/', 'user': 'operator',
                 'password': '/private/hermes-dashboard.password', 'tailnet': True},
                {'name': 'Paperclip', 'url': 'http://127.0.0.1:13100/', 'user': 'operator',
                 'password': '/private/paperclip-owner.password', 'tailnet': False}]
        page = checklist.render_html(applications=apps)
        self.assertIn('href="https://box.example.ts.net:8444/"', page)
        self.assertNotIn('href="http://127.0.0.1:', page)
        self.assertIn('Password:', page)


class FailureSummaryTest(unittest.TestCase):
    def test_safe_rescue_diagnostic_replaces_censored_cause_and_keeps_original_step(self):
        script = ("print('PLAY [Configure native Paperclip workers] ***')\n"
                  "print('TASK [paperclip_workers : Create the native worker agent once] ***')\n"
                  "print('fatal: [zelab_host]: FAILED! => {\"censored\": \"no_log\"}')\n"
                  "print('TASK [paperclip_workers : Report worker creation failure] ***')\n"
                  "print('fatal: [zelab_host]: FAILED! => {\"msg\": \"Paperclip worker creation failed (HTTP 422).\"}')\n"
                  "raise SystemExit(2)\n")
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-c', (
                'import sys; sys.path.insert(0, "deploy"); import run, pathlib;'
                f'raise SystemExit(run.logged_run([sys.executable, "-c", {script!r}], None, pathlib.Path({directory!r})))')],
                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Step:  paperclip_workers : Create the native worker agent once', result.stderr)
        self.assertIn('Cause: Paperclip worker creation failed (HTTP 422).', result.stderr)
        self.assertNotIn('see the private log', result.stderr)

    def test_censored_failure_does_not_promise_details_in_the_private_log(self):
        script = ("print('fatal: [zelab_host]: FAILED! => {\"censored\": \"no_log\"}')\n"
                  "raise SystemExit(2)\n")
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-c', (
                'import sys; sys.path.insert(0, "deploy"); import run, pathlib;'
                f'raise SystemExit(run.logged_run([sys.executable, "-c", {script!r}], None, pathlib.Path({directory!r})))')],
                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 2)
        self.assertIn('the private log is also censored', result.stderr)
        self.assertNotIn('see the private log', result.stderr)

    def test_failed_install_names_phase_step_cause_log_and_next_command(self):
        script = ("print('PLAY [Install pinned native Paperclip on the host] ***')\n"
                  "print('TASK [paperclip : Wait for the native health endpoint] ***')\n"
                  "print('fatal: [zelab_host]: FAILED! => {\"changed\": false, \"msg\": \"Status code was -1 \\\\u2192 retry\"}')\n"
                  "print('PLAY RECAP ***')\n"
                  "raise SystemExit(2)\n")
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / 'private'
            result = subprocess.run([sys.executable, '-c', (
                'import sys; sys.path.insert(0, "deploy"); import run, pathlib;'
                f'raise SystemExit(run.logged_run([sys.executable, "-c", {script!r}], None, pathlib.Path({str(private)!r})))')],
                capture_output=True, text=True, cwd=ROOT)
            logs = list((private / 'logs').glob('install-*.log'))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].stat().st_mode & 0o777, 0o600)
        self.assertIn('Phase: Install pinned native Paperclip on the host', result.stderr)
        self.assertIn('Step:  paperclip : Wait for the native health endpoint', result.stderr)
        self.assertIn('Cause: Status code was -1 → retry', result.stderr)
        self.assertIn('Next: fix the cause above, then run: make', result.stderr)

    def test_terminated_install_still_reports_where_it_stopped(self):
        script = ("import time\nprint('PLAY [Install pinned unmodified Hermes in the adopted guest] ***', flush=True)\n"
                  "print('TASK [hermes : Install native dashboard service] ***', flush=True)\ntime.sleep(30)\n")
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'stderr'
            with log.open('w') as stderr:
                process = subprocess.Popen([sys.executable, '-c', (
                    'import sys; sys.path.insert(0, "deploy"); import run, pathlib;'
                    f'raise SystemExit(run.logged_run([sys.executable, "-c", {script!r}], None, pathlib.Path({directory!r})))')],
                    stdout=subprocess.DEVNULL, stderr=stderr, cwd=ROOT)
                deadline = time.monotonic() + 20
                while not list(Path(directory).glob('logs/install-*.log')) and time.monotonic() < deadline:
                    time.sleep(0.1)
                time.sleep(1)
                process.terminate()
                process.wait(timeout=20)
            output = log.read_text()
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('Step:  hermes : Install native dashboard service', output)
        self.assertIn('Cause: interrupted by the operator', output)

if __name__ == '__main__':
    unittest.main()
