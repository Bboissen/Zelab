"""Exercise remote command quoting, guide delivery and truthful failure status."""

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import remote


class RemoteTest(unittest.TestCase):
    def test_shell_command_keeps_paths_and_make_inputs_literal(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "checkout ' with $(touch unwanted)"
            directory.mkdir()
            command = remote.remote_command(str(directory), ["rtk", "proxy", "python3", "-c",
                "import os,sys,json; print(json.dumps([os.getcwd(),sys.argv[1:]]))",
                "SITE_CONFIG=config/a file.yaml", "ENV_FILE=$(touch unwanted)"])
            for shell in ("sh", "bash", "zsh"):
                if not shutil.which(shell):
                    continue
                with self.subTest(shell=shell):
                    result = subprocess.run(["rtk", "proxy", shell, "-c", command],
                                            capture_output=True, text=True, check=True)
                    self.assertIn(str(directory), result.stdout)
                    self.assertIn("ENV_FILE=$(touch unwanted)", result.stdout)
                    self.assertFalse((directory / "unwanted").exists())

    def run_fixture(self, install_status=0, copy_status=0, action="install"):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        server = root / "server checkout"
        server.mkdir()
        (server / "deploy").mkdir()
        guide = server / "private guide.html"
        guide.write_text("<!doctype html><title>Private guide</title>")
        (server / "Makefile").write_text(
            "install recover guide:\n\t@rtk proxy python3 -c 'print(\"fixture install\"); exit(" + str(install_status) + ")'\n")
        (server / "deploy/doctor.py").write_text(
            "import sys\n"
            f"open({str(root / 'doctor-runs')!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            f"print({str(guide)!r})\n")
        destination = root / "laptop Downloads/guide.html"
        destination.parent.mkdir()
        destination.write_text("previous guide")
        calls = []
        native_run = subprocess.run

        def transport(argv, **kwargs):
            calls.append(argv)
            if argv[:3] == ["rtk", "proxy", "ssh"]:
                return native_run(["rtk", "proxy", "sh", "-c", argv[-1]], **kwargs)
            if argv[:3] == ["rtk", "proxy", "scp"]:
                if not copy_status:
                    shutil.copyfile(argv[-2].split(":", 1)[1], argv[-1])
                return subprocess.CompletedProcess(argv, copy_status)
            raise AssertionError(argv)

        with patch.object(remote.subprocess, "run", side_effect=transport), \
             patch.object(remote, "open_guide", return_value=False), \
             patch.object(remote.sys.stdin, "isatty", return_value=False), redirect_stdout(io.StringIO()) as output:
            status = remote.main(["--host", "fixture-server", "--directory", str(server), "--port", "2222",
                                  "--guide-destination", str(destination), "--action", action])
        self.doctor_runs = (root / "doctor-runs").read_text().splitlines() if (root / "doctor-runs").exists() else []
        return status, destination, guide, calls, output.getvalue()

    def test_remote_install_delivers_a_private_local_guide(self):
        status, destination, guide, calls, output = self.run_fixture()
        self.assertEqual(status, 0)
        self.assertEqual(destination.read_text(), guide.read_text())
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertIn("-p", calls[0])
        self.assertIn("-P", calls[2])
        self.assertIn("-T", calls[0])
        self.assertIn("GUIDE_MODE=remote", calls[0][-1])
        self.assertIn(destination.as_uri(), output)

    def test_remote_guide_runs_doctor_once_and_copies_its_guide(self):
        status, destination, guide, calls, _ = self.run_fixture(action="guide")
        self.assertEqual(status, 0)
        self.assertEqual(destination.read_text(), guide.read_text())
        self.assertEqual([call[2] for call in calls], ["ssh", "scp"])
        self.assertEqual(len(self.doctor_runs), 1)
        self.assertIn("--guide-path-only", self.doctor_runs[0])
        self.assertIn("--brief", self.doctor_runs[0])

    def test_remote_install_failure_still_copies_guide_and_stays_failed(self):
        status, destination, guide, _, _ = self.run_fixture(install_status=2)
        self.assertEqual(status, 2)
        self.assertEqual(destination.read_text(), guide.read_text())

    def test_failed_copy_keeps_the_previous_local_guide(self):
        status, destination, _, _, output = self.run_fixture(copy_status=1)
        self.assertEqual(status, 1)
        self.assertEqual(destination.read_text(), "previous guide")
        self.assertIn("Guide copy failed", output)
        self.assertEqual(list(destination.parent.glob(".zelab-guide-*")), [])

    def test_invalid_remote_settings_do_not_start_ssh(self):
        for args in (["--directory", "/checkout"], ["--host", "host", "--directory", "relative"],
                     ["--host", "host", "--directory", "/checkout", "--port", "99999"]):
            with self.subTest(args=args), patch.object(remote.subprocess, "run") as launch:
                self.assertEqual(remote.main(args), 1)
                launch.assert_not_called()

    def test_remote_action_from_make_environment_cannot_bypass_choices(self):
        with patch.dict(os.environ, {"REMOTE_HOST": "fixture", "REMOTE_DIR": "/checkout", "REMOTE_ACTION": "clean"}), \
             patch.object(remote.subprocess, "run") as launch:
            self.assertEqual(remote.main([]), 1)
            launch.assert_not_called()

class MakeLifecycleTest(unittest.TestCase):
    def test_re_serializes_clean_before_install_and_stops_after_rejected_clean(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            fixture = Path(folder)
            shutil.copyfile(root / "Makefile", fixture / "Makefile")
            (fixture / "deploy").mkdir()
            (fixture / "deploy/requirements-controller.txt").touch()
            (fixture / ".venv").mkdir()
            (fixture / ".venv/.controller-ready").touch()
            stub = fixture / "bin"
            stub.mkdir()
            # Replace only the privileged reset and installer processes. The real
            # Makefile, dependencies and recursive Make invocation are exercised.
            wrapper = stub / "rtk"
            wrapper.write_text("#!/usr/bin/env python3\n" +
                "import os,sys,subprocess\nfrom pathlib import Path\n" +
                "args=sys.argv[2:]\n" +
                "if 'deploy/maintenance.py' in args and 'fclean' in args:\n" +
                "    Path('events').open('a').write('fclean\\n')\n" +
                "    sys.exit(int(os.environ.get('FIXTURE_CLEAN_STATUS','0')))\n" +
                "if 'deploy/install.py' in args:\n" +
                "    Path('events').open('a').write('install\\n'); sys.exit(0)\n" +
                "sys.exit(subprocess.call(args))\n")
            wrapper.chmod(0o755)
            for target, confirmation in (("re", "CONFIRM=fixture:zelab"),
                                         ("fclean", "CONFIRM=fixture:zelab")):
                for fail in (False, True):
                    events = fixture / "events"
                    events.unlink(missing_ok=True)
                    env = dict(os.environ, FIXTURE_CLEAN_STATUS="2" if fail else "0")
                    result = subprocess.run(["rtk", "proxy", "make", "-j4", target, confirmation,
                                             "RTK_BIN_DIR=" + str(stub)], cwd=fixture, env=env,
                                            capture_output=True, text=True)
                    with self.subTest(target=target, fail=fail):
                        self.assertEqual(result.returncode == 0, not fail, result.stderr)
                        self.assertEqual(events.read_text().splitlines(),
                                         ["fclean"] if fail or target == "fclean" else ["fclean", "install"])


if __name__ == "__main__":
    unittest.main()
