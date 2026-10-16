"""Verify the installer's step lines, stage names and failure report."""

import io
import json
from contextlib import redirect_stdout
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack

import yaml

import ansible_log
import install
from fixtures import replay


ROOT = Path(__file__).resolve().parents[1]


def site_plays(path):
    for play in yaml.safe_load(path.read_text()):
        if "import_playbook" in play:
            yield from site_plays(path.parent / play["import_playbook"])
        else:
            yield play["name"]


class InstallerTest(unittest.TestCase):
    def test_plain_make_reconciles_bots_before_the_terminal_only_wizard(self):
        runner = self.installer()
        runner.password_dir = '/dev/shm/test-runtime'
        calls = []
        with patch.object(runner, 'capture', side_effect=lambda argv: (calls.append(argv) or (0, []))), \
                patch.object(install.subprocess, 'run') as run:
            run.return_value.returncode = 0
            runner.bots()
        self.assertIn('refresh', calls[0])
        self.assertEqual(calls[0][-2:], ['--runtime-dir', '/dev/shm/test-runtime'])
        self.assertIn('installer', run.call_args.args[0])
        with patch.object(runner, 'capture', return_value=(1, ['reconciliation failed'])), \
                patch.object(install.subprocess, 'run') as run, self.assertRaises(install.StepFailed):
            runner.bots()
        run.assert_not_called()

    def test_password_lifecycle_covers_preflight_and_summary_then_removes_ram_files(self):
        runner = self.installer()
        stages = []
        directories = []

        def passwords(action):
            stages.append(action)
            path = Path(runner.password_dir)
            self.assertTrue(path.is_dir())
            if action == 'prepare':
                directories.append(path)
                (path / 'paperclip-owner.password').write_text('fixture-secret')

        def capture(argv, on_line=None, env=None, **kwargs):
            if 'deploy/doctor.py' in argv:
                stages.append('summary' if '--after-install' in argv else 'preflight')
                path = Path(env['ZELAB_PASSWORD_DIR']) / 'paperclip-owner.password'
                self.assertEqual(path.read_text(), 'fixture-secret')
            return 0, []

        with ExitStack() as stack, redirect_stdout(io.StringIO()):
            for method in ('host', 'administration', 'configuration', 'controller', 'open_log', 'single_run', 'services', 'bots'):
                stack.enter_context(patch.object(runner, method))
            stack.enter_context(patch.object(runner, 'ui_passwords', side_effect=passwords))
            stack.enter_context(patch.object(runner, 'deploy', side_effect=lambda: stages.append('deploy')))
            stack.enter_context(patch.object(runner, 'capture', side_effect=capture))
            self.assertEqual(runner.run(), 0)
        self.assertEqual(stages, ['prepare', 'preflight', 'deploy', 'finish', 'summary'])
        self.assertFalse(directories[0].exists())

    def installer(self):
        runner = install.Installer(Path("config/site.yaml"), Path(".env"))
        runner.screen = install.Screen(io.StringIO())
        self.addCleanup(runner.screen.close)
        self.addCleanup(runner.log.close)
        return runner

    def test_every_site_play_has_a_short_stage_name(self):
        for play in site_plays(ROOT / "deploy/site.yaml"):
            with self.subTest(play=play):
                self.assertTrue(any(play.startswith(prefix) for prefix, _ in install.STAGES))

    def test_site_sections_progress_in_order(self):
        sections = [next(index for index, (_, stages) in enumerate(install.SECTIONS)
                         if install.stage_of(play) in stages) for play in site_plays(ROOT / 'deploy/site.yaml')]
        self.assertEqual(sections, sorted(sections))

    def test_failed_step_does_not_report_completed_progress(self):
        runner = self.installer()
        runner.screen.section(1)
        runner.screen.start('Hermes VM')
        with self.assertRaises(install.StepFailed):
            runner.fail('stopped')
        output = runner.screen.stream.getvalue()
        self.assertIn('20%', output)
        self.assertNotIn('100%', output)

    def test_deployment_shows_one_mark_per_stage_and_counts_changes(self):
        lines = ["PLAY [Install pinned native Paperclip on the host] ***\n",
                 "TASK [paperclip : Install the service] ***\n", "changed: [zelab_host]\n",
                 "TASK [paperclip : Start it] ***\n", "changed: [zelab_host]\n",
                 "PLAY [Install pinned host agent CLIs] ***\n",
                 "TASK [codex : Download] ***\n", "ok: [zelab_host]\n", "PLAY RECAP ***\n"]
        runner = self.installer()

        capture = replay(lines)
        with patch.object(runner, "capture", side_effect=capture):
            runner.deploy()
        output = runner.screen.stream.getvalue()
        self.assertRegex(output, r"✓ Paperclip +2 changes")
        self.assertRegex(output, r"✓ Host agent CLIs +no changes")
        self.assertNotIn("ok: [", output)

    def test_failed_deployment_names_the_step_and_cause_only(self):
        lines = ["PLAY [Build pinned upstream Hermes web assets outside the small VM] ***\n",
                 "TASK [hermes_build : Download verified Hermes source archive] ***\n",
                 'fatal: [zelab_host]: FAILED! => {"msg": "Request failed"}\n',
                 "Installation stopped.\n", "  Cause: Request failed — HTTP Error 429: Too Many Requests\n"]
        runner = self.installer()

        capture = replay(lines, 2)
        with patch.object(runner, "capture", side_effect=capture), self.assertRaises(install.StepFailed):
            runner.deploy()
        output = runner.screen.stream.getvalue()
        self.assertRegex(output, r"✗ Hermes build +stopped at: Download verified Hermes source archive")
        self.assertIn("Cause: Request failed — HTTP Error 429: Too Many Requests", output)
        self.assertNotIn("fatal:", output)

    def test_deployment_logs_each_task_duration_by_name_only(self):
        lines = ["PLAY [Install pinned native Paperclip on the host] ***\n",
                 "TASK [paperclip : Install the service] ***\n", "changed: [zelab_host]\n",
                 '    "msg": "private task output"\n',
                 "TASK [paperclip : Start it] ***\n", "ok: [zelab_host]\n",
                 "PLAY [Provision the team tier accounts and SSH access] ***\n",
                 "TASK [host_projects : Create account] ***\n", "ok: [zelab_host]\n", "PLAY RECAP ***\n"]
        runner = self.installer()

        def capture(argv, on_line=None, env=None):
            for line in lines:
                runner.log.write(line)
                on_line(line)
            return 0, []
        with patch.object(runner, "capture", side_effect=capture):
            runner.deploy()
        records = [json.loads(line.removeprefix("TASK_TIMING ")) for line in "".join(runner.log.buffer).splitlines()
                   if line.startswith("TASK_TIMING ")]
        self.assertEqual([(record["play"], record["task"]) for record in records], [
            ("Install pinned native Paperclip on the host", "paperclip : Install the service"),
            ("Install pinned native Paperclip on the host", "paperclip : Start it"),
            ("Provision the team tier accounts and SSH access", "host_projects : Create account")])
        self.assertTrue(all(set(record) == {"play", "task", "seconds"} and record["seconds"] >= 0 for record in records))
        self.assertNotIn("TASK_TIMING", runner.screen.stream.getvalue())

    def test_task_timer_measures_to_the_next_task_or_recap(self):
        records, clock = [], iter([10.0, 12.5, 20.0, 21.0, 30.0, 33.0]).__next__
        progress = ansible_log.Progress(on_task=records.append, clock=clock)
        for line in ("PLAY [One] ***", "TASK [a : first] ***", "PLAY [Two] ***", "TASK [second] ***",
                     "PLAY RECAP ***", "TASK [third] ***"):
            progress.feed(line)
        # An interrupted run has no recap; the caller closes the open task once.
        progress.finish_task()
        progress.finish_task()
        self.assertEqual(records, [{"play": "One", "task": "a : first", "seconds": 2.5},
                                   {"play": "Two", "task": "second", "seconds": 1.0},
                                   {"play": "Two", "task": "third", "seconds": 3.0}])

    def test_log_is_private_and_keeps_output_written_before_it_opened(self):
        log = install.Log()
        log.write("before\n")
        with tempfile.TemporaryDirectory() as directory:
            log.open(Path(directory) / "private")
            log.write("after\n")
            log.file.close()
            self.assertEqual(log.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(log.path.read_text(), "before\nafter\n")

    def test_one_run_at_a_time_and_stale_plaintext_copies_are_removed(self):
        with tempfile.TemporaryDirectory() as shm:
            stale = Path(shm, 'zelab-passwords-killed')
            stale.mkdir(mode=0o700)
            (stale / 'paperclip').write_text('plaintext')
            runner = self.installer()
            runner.single_run(Path(shm))
            self.assertFalse(stale.exists())
            with self.assertRaisesRegex(ValueError, 'Another make is running'):
                self.installer().single_run(Path(shm))
            runner.run_lock.close()

    def test_capture_hides_tool_output_and_preserves_it_in_the_private_log(self):
        runner = self.installer()
        with tempfile.TemporaryDirectory() as directory:
            runner.log.open(directory)
            with redirect_stdout(io.StringIO()) as terminal:
                code, tail = runner.capture([sys.executable, "-c",
                                            "import sys; print('download detail'); print('tool error', file=sys.stderr); sys.exit(2)"])
            self.assertEqual(code, 2)
            self.assertEqual(terminal.getvalue(), "")
            self.assertIn("download detail", "".join(tail))
            self.assertIn("tool error", runner.log.path.read_text())

    def test_interrupted_capture_stops_the_tool_before_returning(self):
        runner = self.installer()
        tool_pid = []

        def interrupt(line):
            tool_pid.append(int(line.strip()))
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            runner.capture([sys.executable, "-c", "import os,time; print(os.getpid(), flush=True); time.sleep(30)"], interrupt)
        try:
            with self.assertRaises(ProcessLookupError):
                os.kill(tool_pid[0], 0)
        finally:
            try:
                os.kill(tool_pid[0], 15)
            except ProcessLookupError:
                pass

    def test_tool_failure_keeps_one_useful_cause(self):
        runner = self.installer()
        runner.screen.start("Controller")
        with self.assertRaises(install.StepFailed):
            runner.tool_failed("Ansible could not be installed", [
                "Downloading packages", "ERROR: dependency download failed", "  stack details",
                "make: *** [controller] Error 1"])
        output = runner.screen.stream.getvalue()
        self.assertIn("Cause: ERROR: dependency download failed", output)
        self.assertNotIn("Downloading", output)
        self.assertNotIn("stack details", output)
        self.assertNotIn("make:", output)

    def test_failure_shows_the_private_guide_path_over_ssh(self):
        runner = self.installer()
        with tempfile.TemporaryDirectory() as directory:
            runner.log.open(directory)
            with patch.object(runner, "capture", return_value=(0, ["", "Guide: file:///tmp/guide.html",
                              "Open on your Mac (run these locally):", "  open ~/Downloads/zelab-guide.html"])), \
                 redirect_stdout(io.StringIO()) as terminal:
                runner.after_failure()
        self.assertIn("Guide: file:///tmp/guide.html", terminal.getvalue())
        self.assertIn("open ~/Downloads/zelab-guide.html", terminal.getvalue())
        self.assertNotIn("in your browser", terminal.getvalue())

    def test_failure_keeps_a_static_guide_when_snapshot_cannot_be_generated(self):
        runner = self.installer()
        with tempfile.TemporaryDirectory() as directory:
            runner.log.open(directory)
            with patch.object(runner, "capture", return_value=(1, ["Traceback", "private tool output"])), \
                 redirect_stdout(io.StringIO()) as terminal:
                runner.after_failure()
        self.assertIn((ROOT / "docs/checklist.html").as_uri(), terminal.getvalue())
        self.assertNotIn("private tool output", terminal.getvalue())

    def test_unavailable_or_unreported_services_stop_installation(self):
        for lines in (["Paperclip health: HTTP 200 ready", "Hermes authentication: HTTP 401 ready"],
                      ["Paperclip health: HTTP 200 ready", "Hermes authentication: VM stopped",
                       "Obsidian authentication: HTTP 401 ready"]):
            runner = self.installer()

            def capture(argv, on_line=None, env=None):
                for line in lines:
                    on_line('    "msg": "' + line + '"\n')
                return 0, []

            with self.subTest(lines=lines), patch.object(runner, "capture", side_effect=capture), \
                 self.assertRaises(install.StepFailed):
                runner.services()
            self.assertIn("✗ Services", runner.screen.stream.getvalue())

    def test_disabled_obsidian_is_not_required_by_the_service_check(self):
        runner = self.installer()
        runner.applications.discard("Obsidian")

        capture = replay(('"msg": "Paperclip health: HTTP 200 ready"', '"msg": "Hermes authentication: HTTP 401 ready"'))

        with patch.object(runner, "capture", side_effect=capture):
            runner.services()
        self.assertIn("✓ Services", runner.screen.stream.getvalue())

    def test_plain_output_uses_the_stream_encoding_and_has_no_terminal_escapes(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="ascii")
        screen = install.Screen(stream)
        screen.start("Controller")
        screen.finish("PASS", "ready")
        screen.say("next step")
        screen.close()
        stream.flush()
        self.assertIn(b"ok Controller", buffer.getvalue())
        self.assertIn(b"> next step", buffer.getvalue())
        self.assertNotIn(b"\x1b", buffer.getvalue())

    def test_http_failures_report_the_server_response(self):
        script = ("print('PLAY [Build pinned upstream Hermes web assets outside the small VM] ***')\n"
                  "print('TASK [hermes_build : Download verified Hermes source archive] ***')\n"
                  "print('fatal: [zelab_host]: FAILED! => {\"msg\": \"Request failed\", "
                  "\"response\": \"HTTP Error 429: Too Many Requests\"}')\n"
                  "raise SystemExit(2)\n")
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-c", (
                'import sys; sys.path.insert(0, "deploy"); import run, pathlib;'
                f'raise SystemExit(run.logged_run([sys.executable, "-c", {script!r}], None, pathlib.Path({directory!r})))')],
                capture_output=True, text=True, cwd=ROOT)
        self.assertIn("Cause: Request failed — HTTP Error 429: Too Many Requests", result.stderr)


if __name__ == "__main__":
    unittest.main()
