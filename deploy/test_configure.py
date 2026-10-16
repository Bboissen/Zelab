"""Host discovery for the generated site file, using recorded tool output."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import configure
from run import read_inputs


SERVE_WITH_OTHER_ROUTE = json.dumps({
    "TCP": {"443": {"HTTPS": True}},
    "Web": {"box.example.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3773"}}}},
})


class ConfigureTest(unittest.TestCase):
    def test_tailnet_name_requires_a_running_backend(self):
        running = json.dumps({"BackendState": "Running", "Self": {"DNSName": "box.example.ts.net."}})
        self.assertEqual(configure.tailnet(running), "box.example.ts.net")
        self.assertIsNone(configure.tailnet(running.replace("Running", "NeedsLogin")))
        self.assertIsNone(configure.tailnet(None))
        self.assertIsNone(configure.tailnet("not json"))

    def test_serve_ports_avoid_other_routes_and_keep_existing_own_routes(self):
        ours = {"http://127.0.0.1:18640": "ui_tail_hermes_port",
                "http://127.0.0.1:13100": "ui_tail_paperclip_port",
                "http://127.0.0.1:18645": "ui_tail_obsidian_port"}
        self.assertEqual(configure.choose_tail_ports("{}", ours),
                         {"ui_tail_hermes_port": 443, "ui_tail_paperclip_port": 8443, "ui_tail_obsidian_port": 9443})
        chosen = configure.choose_tail_ports(SERVE_WITH_OTHER_ROUTE, ours)
        self.assertEqual(chosen["ui_tail_hermes_port"], 8444)
        self.assertNotIn(443, chosen.values())
        installed = json.loads(SERVE_WITH_OTHER_ROUTE)
        installed["TCP"]["8444"] = {"HTTPS": True}
        installed["Web"]["box.example.ts.net:8444"] = {"Handlers": {"/": {"Proxy": "http://127.0.0.1:18640"}}}
        self.assertEqual(configure.choose_tail_ports(json.dumps(installed), ours), chosen)

    def test_ssh_listener_prefers_loopback_then_a_lan_address(self):
        self.assertEqual(configure.ssh_listener("port 22\nlistenaddress 0.0.0.0:22\nlistenaddress [::]:22\n"),
                         ("127.0.0.1", 22))
        self.assertEqual(configure.ssh_listener("port 2222\nlistenaddress 100.64.0.1:2222\n"
                                                "listenaddress 192.168.0.1:2222\n"), ("192.168.0.1", 2222))
        self.assertEqual(configure.ssh_listener(None), ("127.0.0.1", 22))

    def test_generated_site_validates_with_public_defaults_and_is_never_overwritten(self):
        facts = {"operator_user": "operator", "worker_ssh_host": "127.0.0.1", "worker_ssh_port": 22,
                 "ui_tailscale_enabled": True, "ui_tailscale_hostname": "box.example.ts.net",
                 "ui_tail_hermes_port": 8444, "ui_tail_paperclip_port": 8443, "ui_tail_obsidian_port": 9443,
                 "_ssh_discovered": True}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site.yaml"
            with patch.object(configure, "discover", return_value=facts), \
                 patch.object(configure, "operator", return_value="operator"):
                self.assertEqual(configure.main(["--site-config", str(path)]), 0)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                site = read_inputs(path, None)
                path.write_text("operator_user: kept\n")
                configure.main(["--site-config", str(path)])
            self.assertEqual(path.read_text(), "operator_user: kept\n")
        self.assertEqual(site["ui_tail_hermes_port"], 8444)
        self.assertIn("box.example.ts.net", site["paperclip_allowed_hosts"])
