"""Owner-only dotenv seeding keeps later native UI edits."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent / 'files'))
import seed_dotenv  # noqa: E402


class SeedDotenvTest(unittest.TestCase):
    def seed(self, text, *arguments):
        with tempfile.TemporaryDirectory() as directory, mock.patch('os.chown'):
            path = Path(directory) / '.env'
            path.write_text(text)
            seed_dotenv.main([str(path), *arguments])
            return path.read_text(), path.stat().st_mode & 0o777

    def test_profile_seed_adds_only_absent_keys_and_skips_unknown_ids(self):
        text, mode = self.seed('DISCORD_ALLOWED_USERS=\nOTHER=1\n', 'DISCORD_ALLOWED_USERS=123',
                               'DISCORD_ALLOWED_CHANNELS=', 'DISCORD_ALLOW_BOTS=false')
        # An allowlist the owner emptied stays empty: it admits nobody.
        self.assertEqual(text, 'DISCORD_ALLOWED_USERS=\nOTHER=1\nDISCORD_ALLOW_BOTS=false\n')
        self.assertEqual(mode, 0o600)

    def test_owner_admission_fills_an_empty_allowlist_but_keeps_a_set_one(self):
        text, _ = self.seed('# DISCORD_ALLOWED_USERS=9\nDISCORD_ALLOWED_USERS=\n', '--fill-empty', 'DISCORD_ALLOWED_USERS=123')
        self.assertEqual(text, '# DISCORD_ALLOWED_USERS=9\nDISCORD_ALLOWED_USERS=123\n')
        text, _ = self.seed('DISCORD_ALLOWED_USERS=456\n', '--fill-empty', 'DISCORD_ALLOWED_USERS=123')
        self.assertEqual(text, 'DISCORD_ALLOWED_USERS=456\n')

    def test_non_decimal_discord_ids_are_refused(self):
        with self.assertRaises(SystemExit):
            self.seed('', 'DISCORD_ALLOWED_CHANNELS=abc')


if __name__ == '__main__':
    unittest.main()
