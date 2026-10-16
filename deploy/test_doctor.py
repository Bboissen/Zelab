"""Verify advisory OS findings and blocking deployment prerequisites."""

from contextlib import nullcontext, redirect_stderr, redirect_stdout
from datetime import date, timedelta
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

import doctor
import run
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "implementation"))
import runtime


class HostReadinessTest(unittest.TestCase):
    def site(self):
        return {'deploy_key': '/fixture/key', 'zelab_projects': {'research': {'name': 'research'}},
                'zelab_tiers': {'dev': {'account': 'zelab-team-dev'}}}

    def test_each_bot_reports_its_own_pending_setup_steps(self):
        facts = [{'name': 'scout', 'signed_in': True, 'chats': ['Discord'], 'task_key': False, 'soul': True},
                 {'name': 'writer', 'signed_in': False, 'chats': [], 'task_key': True, 'soul': False}]
        with patch.object(doctor, 'bot_facts', return_value=facts), patch.object(Path, 'exists', return_value=True):
            checks = {check.name: check.label for check in doctor.connection_checks(self.site(), elevated=True)}
        self.assertEqual(checks['Hermes bots'], 'PASS')
        self.assertEqual((checks['scout: model sign-in'], checks['scout: chat app'], checks['scout: task key']),
                         ('PASS', 'PASS', 'PENDING'))
        self.assertEqual((checks['writer: model sign-in'], checks['writer: chat app'], checks['writer: task key']),
                         ('PENDING', 'PENDING', 'PASS'))
        self.assertEqual((checks['scout: persona'], checks['writer: persona']), ('PASS', 'PENDING'))
        self.assertEqual(checks['Team tiers'], 'PASS')

    def test_no_bots_or_no_guest_is_a_pending_step_not_a_blocker(self):
        for facts, detail in (([], 'make bot'), (None, 'after installation')):
            with self.subTest(facts=facts), patch.object(doctor, 'bot_facts', return_value=facts), \
                    patch.object(Path, 'exists', return_value=True):
                check = doctor.connection_checks(self.site(), elevated=True)[0]
            self.assertEqual((check.name, check.label), ('Hermes bots', 'PENDING'))
            self.assertIn(detail, check.detail)

    def test_allowlisted_release_expires_at_its_maintenance_boundary(self):
        for (family, version), expiry in doctor.SUPPORTED_OS.items():
            release = {"ID": family, "VERSION_ID": version}
            with self.subTest(family=family, version=version):
                self.assertTrue(all(check.ready for check in doctor.platform_checks(release, "x86_64", expiry)))
                self.assertFalse(doctor.platform_checks(release, "x86_64", expiry + timedelta(days=1))[0].ready)

    def test_older_interim_and_derivative_releases_warn_without_blocking(self):
        for release in ({"ID": "ubuntu", "VERSION_ID": "25.04"},
                        {"ID": "ubuntu", "VERSION_ID": "20.04"},
                        {"ID": "debian", "VERSION_ID": "11"},
                        {"ID": "linuxmint", "ID_LIKE": "ubuntu", "VERSION_ID": "24.04"}, {}):
            with self.subTest(release=release):
                checks = doctor.platform_checks(release, "x86_64", date(2026, 10, 10))
                self.assertFalse(checks[0].ready)
                with redirect_stdout(io.StringIO()) as output:
                    self.assertTrue(doctor.report_checks(checks))
                # Advice is shown in the guide, not on every terminal run.
                self.assertEqual(output.getvalue(), "")

    def test_supported_os_cannot_bypass_pinned_architecture(self):
        checks = doctor.platform_checks({"ID": "ubuntu", "VERSION_ID": "24.04"}, "aarch64", date(2026, 10, 10))
        self.assertTrue(checks[0].ready)
        self.assertFalse(checks[1].ready)

    def test_platform_warning_never_runs_external_commands(self):
        with patch.object(doctor, "platform_checks", return_value=[doctor.Check("Host release", False, "outside baseline", blocking=False)]), \
             patch.object(doctor.subprocess, "run") as run, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(doctor.main(["--platform-only"]), 0)
        run.assert_not_called()
        self.assertEqual(output.getvalue(), "")

    def test_direct_deploy_rejects_wrong_architecture_before_privileged_commands(self):
        with patch.object(run, "platform_checks", return_value=[doctor.Check("Host architecture", False, "Pinned releases require x86_64")]), \
             patch.object(run.subprocess, "run") as command, \
             patch.object(run.subprocess, "Popen", side_effect=AssertionError("unpatched launch")), \
             patch.object(run.sys, "argv", ["run.py", "--site-config", "config/site.example.yaml", "--no-env", "deploy/install-paperclip.yaml"]), \
             redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(SystemExit, "Host prerequisites failed"):
                run.main()
        command.assert_not_called()
        self.assertIn("✗ Host architecture", output.getvalue())

    def test_direct_deploy_on_older_release_reaches_ansible(self):
        checks = doctor.platform_checks({"ID": "ubuntu", "VERSION_ID": "25.04"}, "x86_64", date(2026, 10, 11))
        # Never reach a real playbook from a unit test: both launch paths are replaced.
        with patch.object(run, "platform_checks", return_value=checks), \
             patch.object(runtime, "maintenance_lock", return_value=nullcontext()), \
             patch.object(run.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as command, \
             patch.object(run.subprocess, "Popen", side_effect=AssertionError("unpatched launch")), \
             patch.object(run, "logged_run", return_value=0) as launched, \
             patch.object(run.sys, "argv", ["run.py", "--site-config", "config/site.example.yaml", "--no-env", "deploy/install-paperclip.yaml"]), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run.main(), 0)
        self.assertEqual(command.call_count, 1)
        self.assertIn("--prepare-lock", command.call_args_list[0].args[0])
        self.assertIn(str(run.ROOT / ".venv/bin/ansible-playbook"), launched.call_args.args[0])
        self.assertNotIn("Host release", output.getvalue())

    def test_advisory_release_does_not_mask_missing_prerequisites(self):
        checks = [doctor.Check("Host release", False, "outside baseline", blocking=False),
                  doctor.Check("KVM", False, "Host must provide /dev/kvm")]
        with patch.object(doctor, "readiness_checks", return_value=checks), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(doctor.main([]), 1)
        self.assertRegex(output.getvalue(), r"! Host release")
        self.assertRegex(output.getvalue(), r"✗ KVM")
        with patch.object(doctor, "readiness_checks", return_value=checks), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(doctor.main(["--brief"]), 1)
        self.assertNotIn("Host release", output.getvalue())
        self.assertRegex(output.getvalue(), r"✗ KVM")

    def test_pending_connections_never_block_installation(self):
        checks = [doctor.Check("Codex subscription login", False, "sign in", pending=True),
                  doctor.Check("Unattended administration", False, "bootstrap", pending=True)]
        with patch.object(doctor, "readiness_checks", return_value=checks), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(doctor.main([]), 0)
        self.assertRegex(output.getvalue(), r"○ Codex subscription login")
        # The install run stays quiet: pending steps are listed in the guide it opens.
        with patch.object(doctor, "readiness_checks", return_value=checks), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(doctor.main(["--brief"]), 0)
        self.assertEqual(output.getvalue(), "")

    def test_after_install_does_not_hide_a_blocker_when_opening_the_guide(self):
        checks = [doctor.Check("KVM", False, "missing")]
        with patch.object(doctor, "readiness_checks", return_value=checks), \
             patch.object(doctor, "load_site", return_value=(None, None)), \
             patch.object(doctor, "status_snapshot"), patch.object(doctor, "open_in_browser", return_value=False), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(doctor.main(["--brief", "--open", "--after-install"]), 1)
        self.assertIn("Guide:", output.getvalue())
        self.assertNotIn("Zelab is installed", output.getvalue())

    def test_advice_does_not_undo_a_completed_guide_step(self):
        checks = [doctor.Check("Host release", False, "outside the recommended baseline", blocking=False),
                  doctor.Check("KVM", True, "ready")]
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory) / "guide.html"
            doctor.status_snapshot(checks, page)
            html = page.read_text()
        self.assertIn('id="step-host" checked disabled', html)
        self.assertIn('Next: <a href="#admin">', html)
        self.assertNotIn('Next: <a href="#host">', html)

    def test_ssh_does_not_launch_a_browser_on_the_server(self):
        with patch.dict(os.environ, {"SSH_CONNECTION": "192.0.2.1 45000 192.0.2.2 2222", "DISPLAY": ":0"}), \
             patch.object(doctor.shutil, "which", return_value="/usr/bin/xdg-open"), \
             patch.object(doctor.subprocess, "Popen") as launch:
            self.assertFalse(doctor.open_in_browser(Path("/tmp/guide.html")))
        launch.assert_not_called()

    def test_ssh_guide_prints_commands_for_the_laptop_with_the_server_port(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.html"
            with patch.dict(os.environ, {"SSH_CONNECTION": "192.0.2.1 45000 2001:db8::1 2222"}), \
                 patch.object(doctor, "readiness_checks", return_value=[]), \
                 patch.object(doctor, "load_site", return_value=(None, None)), \
                 patch.object(doctor, "guide_path", return_value=path), \
                 patch.object(doctor, "open_in_browser") as server_browser, \
                 redirect_stdout(io.StringIO()) as output:
                self.assertEqual(doctor.main(["--open", "--quiet"]), 0)
            self.assertTrue(path.exists())
        server_browser.assert_not_called()
        self.assertIn("scp -P 2222", output.getvalue())
        self.assertIn("@[2001:db8::1]:", output.getvalue())
        self.assertIn("rtk proxy open ~/Downloads/zelab-guide.html", output.getvalue())

    def test_machine_guide_path_output_is_not_mixed_with_readiness_or_color(self):
        with tempfile.TemporaryDirectory() as directory:
            guide = Path(directory) / 'guide.html'
            with patch.object(doctor, 'readiness_checks', return_value=[doctor.Check('KVM', False, 'missing')]), \
                 patch.object(doctor, 'guide_path', return_value=guide), \
                 patch.object(doctor, 'status_snapshot') as snapshot, \
                 redirect_stdout(io.StringIO()) as output:
                self.assertEqual(doctor.main(['--guide-path-only']), 0)
            self.assertEqual(output.getvalue(), str(guide) + '\n')
            snapshot.assert_called_once()

    def test_remote_guide_path_keeps_blockers_off_standard_output(self):
        with tempfile.TemporaryDirectory() as directory:
            guide = Path(directory) / 'guide.html'
            with patch.object(doctor, 'readiness_checks', return_value=[doctor.Check('KVM', False, 'missing')]), \
                 patch.object(doctor, 'guide_path', return_value=guide), \
                 patch.object(doctor, 'status_snapshot'), \
                 redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(doctor.main(['--guide-path-only', '--brief']), 0)
        self.assertEqual(output.getvalue(), str(guide) + '\n')
        self.assertIn('KVM', errors.getvalue())

    def test_site_inputs_are_validated_once_per_run(self):
        with patch.object(doctor, 'load_site', return_value=(None, 'fixture problem')) as load, \
             patch.object(doctor, 'host_checks', return_value=[]), \
             patch.object(doctor, 'admin_checks', return_value=([], False)), \
             redirect_stdout(io.StringIO()):
            doctor.main(['--site-config', 'fixture-site.yaml'])
        load.assert_called_once()

    def test_busy_ports_ask_each_owning_unit_once(self):
        site = {"vm_service": "fixture-vm", "guest_ssh_port": 1, "guest_dashboard_port": 2, "guest_api_port": 3,
                "paperclip_port": 4, "paperclip_db_port": 5, "notes_sftp_port": 6, "obsidian_backend_port": 7}
        with patch.object(doctor.socket.socket, 'bind', side_effect=OSError), \
             patch.object(doctor, 'command_output', return_value='active\n') as probe:
            checks = doctor.port_checks(site, elevated=False)
        # Without administration the container owner cannot be proven.
        self.assertIn('In use by another program: obsidian_backend_port 7.', checks[0].detail)
        self.assertEqual(sorted(call.args[0][-1] for call in probe.call_args_list),
                         ['fixture-vm.service', 'zelab-notes-sshd.service', 'zelab-paperclip.service'])

    def test_resource_checks_fail_on_need_and_warn_on_headroom(self):
        site = {"vm_disk_gib": 16, "vm_data": "/", "host_disk_reserve_gib": 20,
                "vm_memory_mb": 2048, "host_memory_reserve_gib": 8}
        with patch.object(doctor, "free_gib", return_value=30), \
             patch.object(doctor, "available_memory_gib", return_value=5):
            checks = {check.name: check for check in doctor.resource_checks(site)}
        self.assertEqual(checks["Free disk"].label, "PASS")
        self.assertEqual(checks["Disk headroom"].label, "WARN")
        self.assertEqual(checks["Memory headroom"].label, "WARN")
        with patch.object(doctor, "free_gib", return_value=20), \
             patch.object(doctor, "available_memory_gib", return_value=3):
            checks = {check.name: check for check in doctor.resource_checks(site)}
        self.assertEqual(checks["Free disk"].label, "FAIL")
        self.assertEqual(checks["Available memory"].label, "FAIL")

    def test_installed_site_reports_only_headroom_and_tolerates_private_state(self):
        site = {"vm_disk_gib": 16, "vm_data": "/", "host_disk_reserve_gib": 20,
                "vm_memory_mb": 2048, "host_memory_reserve_gib": 8}
        with patch.object(doctor, "free_gib", return_value=10), \
             patch.object(doctor, "available_memory_gib", return_value=3):
            checks = doctor.resource_checks(site, installed=True)
        self.assertEqual({check.label for check in checks}, {"WARN"})
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "private"
            (private / "data").mkdir(parents=True)
            private.chmod(0)
            try:
                self.assertEqual(doctor.existing_parent(private / "data"), private)
                doctor.free_gib(private / "data")
            finally:
                private.chmod(0o700)

    def test_probe_errors_do_not_reveal_command_output(self):
        with patch.object(doctor.subprocess, "run", side_effect=subprocess.TimeoutExpired(["private-value"], 15,
                          output=b"private-value")), redirect_stdout(io.StringIO()) as output:
            self.assertFalse(doctor.command_succeeds(["docker", "info"]))
        self.assertEqual(output.getvalue(), "")

    def test_root_binary_cannot_be_a_symlink_or_writable_by_other_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "rtk"
            target.write_text("fixture")
            target.chmod(0o777)
            self.assertFalse(doctor.trusted_root_rtk(target))
            link = Path(directory) / "alias"
            link.symlink_to(target)
            self.assertFalse(doctor.trusted_root_rtk(link))

    def test_preflight_uses_the_shared_guard_before_installation(self):
        tasks = yaml.safe_load(Path("deploy/preflight.yaml").read_text())[0]["tasks"]
        self.assertEqual(tasks[0]["ansible.builtin.command"]["argv"][-2:],
                         ["{{ playbook_dir }}/doctor.py", "--platform-only"])
        self.assertFalse(tasks[0]["changed_when"])
        self.assertTrue(any("ansible.builtin.apt" in task for task in tasks[1:]))


if __name__ == "__main__":
    unittest.main()
