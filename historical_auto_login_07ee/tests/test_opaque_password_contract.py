from __future__ import annotations

import json
import unittest

from instagram_credentials_runtime_access import parse_vault_secret_for_login


class HistoricalOpaquePasswordContractTest(unittest.TestCase):
    def test_historical_reader_preserves_opaque_passwords(self) -> None:
        synthetic_passwords = (
            " password",
            "password ",
            " password ",
            "pa ss word",
            "mot-de-passe-é§🔐",
            "!@#$%^&*()_+-=[];':,.<>/?\\|`~",
        )
        for password in synthetic_passwords:
            plain = parse_vault_secret_for_login(password)
            envelope = parse_vault_secret_for_login(json.dumps({"password": password}))
            self.assertTrue(plain.ok)
            self.assertTrue(envelope.ok)
            self.assertEqual(plain.password, password)
            self.assertEqual(envelope.password, password)


if __name__ == "__main__":
    unittest.main()
