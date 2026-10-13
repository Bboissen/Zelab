"""Integrity checks for the qualified native Hermes dependency manifests."""

import hashlib
import json
import re
from pathlib import Path
import tomllib
import unittest

import yaml


class HermesDependencyTest(unittest.TestCase):
    def test_inventory_and_manifests_match_the_qualified_source(self):
        root = Path('deploy/roles/hermes/files/dependencies')
        manifest = json.loads((root / 'manifest.json').read_text())
        self.assertEqual(manifest['upstream_commit'], yaml.safe_load(Path('versions.yaml').read_text())['hermes_commit'])
        for name, hashes in manifest['files'].items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), hashes['sha256'])
        inventory = (root / 'runtime-requirements.txt').read_bytes()
        self.assertEqual(hashlib.sha256(inventory).hexdigest(), manifest['inventory_sha256'])
        packages = {(re.sub(r'[-_.]+', '-', p['name']).lower(), p.get('version'))
                    for p in tomllib.loads((root / 'uv.lock').read_text())['package']}
        for line in inventory.decode().splitlines():
            name, version = line.split('==')
            self.assertIn((re.sub(r'[-_.]+', '-', name).lower(), version), packages)
        self.assertEqual(manifest['selection'], ['all', 'ddgs'])


if __name__ == '__main__':
    unittest.main()
