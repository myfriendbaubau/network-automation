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
        cls.ios_replacement = plays[0]["vars"]["redaction_replacement"]
        cls.asa_patterns = plays[1]["vars"]["asa_scrub_patterns"]
        cls.asa_replacement = plays[1]["vars"]["redaction_replacement"]

    @staticmethod
    def scrub(config, patterns, replacement):
        for pattern in patterns:
            config = re.sub(pattern, replacement, config, flags=re.MULTILINE)
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

        result = self.scrub(source, self.ios_patterns, self.ios_replacement)

        for secret in (
            "IOS_ENABLE_HASH",
            "0822455D0A16",
            "IOS_USER_HASH",
            "121A0C041104",
            "IOS_PSK",
            "public",
        ):
            self.assertNotIn(secret, result)
        self.assertEqual(
            result,
            """\
enable secret 9 <REDACTED>
enable password 7 <REDACTED>
username cisco privilege 15 secret 9 <REDACTED>
key-string 7 <REDACTED>
pre-shared-key <REDACTED>
snmp-server community <REDACTED> RO
""",
        )
        self.assertNotIn(r"\1 <REDACTED>", result)

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

        result = self.scrub(source, self.asa_patterns, self.asa_replacement)

        for secret in (
            "ASA_ENABLE",
            "ASA_USER",
            "ASA_PASS",
            "ASA_PSK",
            "SERIAL123",
            "203.0.113.7",
        ):
            self.assertNotIn(secret, result)
        self.assertEqual(
            result,
            """\
enable password <REDACTED> encrypted
username admin password <REDACTED> encrypted privilege 15
passwd <REDACTED> encrypted
pre-shared-key <REDACTED>
: Serial Number: <REDACTED>
set peer <REDACTED>
tunnel-group <REDACTED> type ipsec-l2l
""",
        )
        self.assertNotIn(r"\1 <REDACTED>", result)


if __name__ == "__main__":
    unittest.main()
