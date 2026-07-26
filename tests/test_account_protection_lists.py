from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import account_protection_lists as protection


def payload() -> dict[str, object]:
    return {
        "ok": True,
        "account_id": "11111111-1111-4111-8111-111111111111",
        "source": "account_protection_list_entries",
        "loaded_at": "2026-07-26T03:00:00+00:00",
        "lists": {
            "interaction_blacklist": ["blocked.user", "both"],
            "unfollow_whitelist": ["protected_user", "both"],
        },
        "versions": {"interaction_blacklist": 7, "unfollow_whitelist": 4},
    }


class AccountProtectionListsTest(unittest.TestCase):
    def test_loader_calls_backend_once_and_freezes_sets(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        def rpc(name: str, params: dict[str, object]) -> object:
            calls.append((name, params))
            return payload()

        snapshot = protection.load_snapshot_for_run(
            "11111111-1111-4111-8111-111111111111",
            rpc,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "get_account_protection_lists_for_run")
        self.assertEqual(snapshot.interaction_blacklist, frozenset({"blocked.user", "both"}))
        self.assertEqual(snapshot.unfollow_whitelist, frozenset({"protected_user", "both"}))

    def test_action_semantics_and_overlap(self) -> None:
        with patch.dict(os.environ, {protection.SNAPSHOT_ENV: json.dumps(payload())}, clear=False):
            self.assertTrue(protection.is_interaction_blocked("@blocked.user"))
            self.assertFalse(protection.is_unfollow_protected("blocked.user"))
            self.assertTrue(protection.is_unfollow_protected("protected_user"))
            self.assertFalse(protection.is_interaction_blocked("protected_user"))
            self.assertTrue(protection.is_interaction_blocked("both"))
            self.assertTrue(protection.is_unfollow_protected("both"))

    def test_missing_or_malformed_snapshot_fails_closed(self) -> None:
        with patch.dict(os.environ, {protection.REQUIRED_ENV: "1"}, clear=True):
            with self.assertRaisesRegex(ValueError, "snapshot_missing"):
                protection.is_interaction_blocked("someone")
        malformed = payload()
        malformed["lists"] = {"interaction_blacklist": [], "unfollow_whitelist": "bad"}
        with patch.dict(os.environ, {protection.SNAPSHOT_ENV: json.dumps(malformed)}, clear=False):
            with self.assertRaisesRegex(ValueError, "unfollow_whitelist_invalid"):
                protection.is_unfollow_protected("someone")

    def test_account_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "account_mismatch"):
            protection.parse_snapshot(payload(), expected_account_id="22222222-2222-4222-8222-222222222222")


if __name__ == "__main__":
    unittest.main()
