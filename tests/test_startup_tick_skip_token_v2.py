from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from startup_tick_skip_token import (
    CANCELLATION_RECEIPT_SCHEMA,
    StartupSkipTokenError,
    TOKEN_FILENAME,
    cancel_prepared_startup_skip_token,
    consumption_receipt_path,
    consume_startup_skip_token,
    prepare_startup_skip_token,
    verify_startup_skip_consumed,
)


SHA = "a" * 40


class StartupTokenV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.run = self.home / "run-control-dispatcher"
        self.run.mkdir(mode=0o700)
        self.token = self.run / TOKEN_FILENAME
        self.binding = {
            "transaction_id": "worker-promotion:test-token-0001",
            "phase": "PROMOTE",
            "candidate_sha": SHA,
            "dispatcher_module_root": "/releases/new",
        }

    def prepare(self, binding=None):
        return prepare_startup_skip_token(self.token, self.home, self.binding if binding is None else binding)["token"]

    def test_correct_token_and_dispatcher_pass(self):
        token = self.prepare()
        consumed = consume_startup_skip_token(self.token, self.home)
        self.assertTrue(consumed["consumed"])
        proof = verify_startup_skip_consumed(self.token, self.home, token)
        self.assertTrue(proof["ok"])
        self.assertEqual(proof["binding"], self.binding)
        self.assertEqual(proof["token_id"], token["token_id"])

    def test_missing_token_fails(self):
        expected = {**self.prepare(), "inode": -1}
        self.token.unlink()
        self.assertFalse(verify_startup_skip_consumed(self.token, self.home, expected)["ok"])

    def test_wrong_transaction_fails(self):
        token = self.prepare()
        consume_startup_skip_token(self.token, self.home)
        token["binding"] = {**token["binding"], "transaction_id": "worker-promotion:wrong-0001"}
        self.assertFalse(verify_startup_skip_consumed(self.token, self.home, token)["ok"])

    def test_wrong_phase_fails(self):
        token = self.prepare()
        consume_startup_skip_token(self.token, self.home)
        token["binding"] = {**token["binding"], "phase": "ROLLBACK"}
        self.assertFalse(verify_startup_skip_consumed(self.token, self.home, token)["ok"])

    def test_wrong_dispatcher_identity_fails(self):
        token = self.prepare()
        consume_startup_skip_token(self.token, self.home)
        token["binding"] = {**token["binding"], "dispatcher_module_root": "/releases/old"}
        self.assertFalse(verify_startup_skip_consumed(self.token, self.home, token)["ok"])

    def test_existing_candidate_token_cannot_be_rebound_for_rollback(self):
        self.prepare()
        rollback = {**self.binding, "phase": "ROLLBACK", "candidate_sha": "b" * 40, "dispatcher_module_root": "/releases/old"}
        with self.assertRaises(StartupSkipTokenError):
            prepare_startup_skip_token(self.token, self.home, rollback)

    def test_consumed_candidate_token_is_not_reused_for_rollback(self):
        candidate = self.prepare()
        consume_startup_skip_token(self.token, self.home)
        rollback_binding = {**self.binding, "phase": "ROLLBACK", "candidate_sha": "b" * 40, "dispatcher_module_root": "/releases/old"}
        rollback = self.prepare(rollback_binding)
        self.assertNotEqual(candidate["token_id"], rollback["token_id"])

    def test_legacy_restart_token_remains_compatible(self):
        token = prepare_startup_skip_token(self.token, self.home)["token"]
        self.assertIsNone(token["binding"])
        self.assertTrue(consume_startup_skip_token(self.token, self.home)["consumed"])
        self.assertTrue(verify_startup_skip_consumed(self.token, self.home, token)["ok"])

    def test_pending_token_cancellation_is_durable_and_idempotent(self):
        token = self.prepare()
        first = cancel_prepared_startup_skip_token(self.token, self.home, token)
        self.assertEqual(first["status"], "STARTUP_TOKEN_CANCELLED")
        self.assertFalse(self.token.exists())
        receipt = consumption_receipt_path(self.token)
        value = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(value["schema"], CANCELLATION_RECEIPT_SCHEMA)
        self.assertEqual(value["token_id"], token["token_id"])
        self.assertEqual(value["binding"], token["binding"])
        second = cancel_prepared_startup_skip_token(self.token, self.home, token)
        self.assertEqual(second["status"], "STARTUP_TOKEN_ALREADY_CANCELLED")


if __name__ == "__main__":
    unittest.main()
