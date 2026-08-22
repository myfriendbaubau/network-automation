from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class BackupRedactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        plays = yaml.safe_load(
            (ROOT / "playbooks" / "backup_configs.yml").read_text(encoding="utf-8")
        )
        cls.ios_patterns = plays[0]["vars"]["ios_scrub_patterns"]
        cls.asa_patterns = plays[1]["vars"]["asa_scrub_patterns"]

    @staticmethod
    def scrub(config, patterns):
        for pattern in patterns:
            config = re.sub(pattern, r"\1 <REDACTED>", config, flags=re.MULTILINE)
        return config

    def test_ios_credentials_are_redacted(self):
        source = """\
enable secret 9 IOS_ENABLE_HASH
enable password 7 0822455D0A16
username cisco privilege 15 secret 9 IOS_USER_HASH
key-string 7 121A0C041104
pre-shared-key IOS_PSK
snmp-server community public RO
"""

        result = self.scrub(source, self.ios_patterns)

        for secret in (
            "IOS_ENABLE_HASH",
            "0822455D0A16",
            "IOS_USER_HASH",
            "121A0C041104",
            "IOS_PSK",
            "public",
        ):
            self.assertNotIn(secret, result)
        self.assertEqual(result.count("<REDACTED>"), 6)

    def test_asa_credentials_and_identifiers_are_redacted(self):
        source = """\
enable password ASA_ENABLE encrypted
username admin password ASA_USER encrypted privilege 15
passwd ASA_PASS encrypted
pre-shared-key ASA_PSK
: Serial Number: SERIAL123
set peer 203.0.113.7
tunnel-group 203.0.113.7 type ipsec-l2l
"""

        result = self.scrub(source, self.asa_patterns)

        for secret in (
            "ASA_ENABLE",
            "ASA_USER",
            "ASA_PASS",
            "ASA_PSK",
            "SERIAL123",
            "203.0.113.7",
        ):
            self.assertNotIn(secret, result)
        self.assertEqual(result.count("<REDACTED>"), 7)


if __name__ == "__main__":
    unittest.main()
