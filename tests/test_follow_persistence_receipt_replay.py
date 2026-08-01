from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import follow_persistence_intent
import follow_persistence_receipt_replay
import follow_persistence_rpc


ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"
REQUEST_ID = "33333333-3333-4333-8333-333333333333"
ACTION_ID = "44444444-4444-4444-8444-444444444444"


def _rpc_success(status: str = "created") -> dict:
    return {
        "ok": True,
        "status": status,
        "action_id": ACTION_ID,
        "interaction_id": "55555555-5555-4555-8555-555555555555",
        "follow_persisted": True,
        "eligible_unfollow_at": "2026-08-04T10:00:00+00:00",
        "audit_persisted": True,
        "counter_applied": True,
        "settings_revision_match": True,
        "invariants_confirmed": sorted(follow_persistence_rpc.REQUIRED_INVARIANTS),
        "failure_reason": None,
    }


class FollowPersistenceReceiptReplayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ,
            {"FOLLOW_PERSISTENCE_INTENT_ROOT": self.tmp.name},
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _create(self, *, verified: bool = True) -> None:
        follow_persistence_intent.create_prepared_intent(
            action_id=ACTION_ID,
            account_id=ACCOUNT_ID,
            run_id=RUN_ID,
            request_id=REQUEST_ID,
            candidate_username="Candidate",
            source_target_id=None,
            source_ct_username="ct",
            settings_revision="2026-08-01T10:00:00+00:00",
        )
        if verified:
            follow_persistence_intent.update_intent_stage(
                run_id=RUN_ID,
                action_id=ACTION_ID,
                stage="follow_physically_verified",
                followed_at="2026-08-01T10:01:00+00:00",
                metadata_safe={
                    "physical_follow_state": "following",
                    "receipt_ready_before_critical_rpc": True,
                },
            )

    def test_verified_receipt_replays_db_only_once(self) -> None:
        self._create()
        with mock.patch.object(
            follow_persistence_receipt_replay.supabase_client,
            "persist_verified_follow_success_rpc",
            return_value=_rpc_success(),
        ) as rpc:
            first = follow_persistence_receipt_replay.replay_verified_receipts()
            second = follow_persistence_receipt_replay.replay_verified_receipts()
        self.assertTrue(first["ok"])
        self.assertEqual(first["replayed"], 1)
        self.assertTrue(second["ok"])
        self.assertEqual(second["replayed"], 0)
        self.assertEqual(rpc.call_count, 1)
        self.assertEqual(
            follow_persistence_intent.load_all_nonterminal_intents(), []
        )

    def test_failed_rpc_preserves_verified_receipt(self) -> None:
        self._create()
        with mock.patch.object(
            follow_persistence_receipt_replay.supabase_client,
            "persist_verified_follow_success_rpc",
            side_effect=RuntimeError("offline"),
        ):
            result = follow_persistence_receipt_replay.replay_verified_receipts()
        self.assertFalse(result["ok"])
        pending = follow_persistence_intent.load_all_nonterminal_intents()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["stage"], "follow_physically_verified")

    def test_prepared_receipt_is_never_replayed_as_success(self) -> None:
        self._create(verified=False)
        with mock.patch.object(
            follow_persistence_receipt_replay.supabase_client,
            "persist_verified_follow_success_rpc",
        ) as rpc:
            result = follow_persistence_receipt_replay.replay_verified_receipts()
        self.assertTrue(result["ok"])
        self.assertEqual(result["ignored_prepared"], 1)
        rpc.assert_not_called()

    def test_legacy_verified_intent_is_not_backfilled(self) -> None:
        self._create()
        path = next(Path(self.tmp.name).glob("*/*.json"))
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("receipt_schema", None)
        payload["version"] = 1
        path.write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(
            follow_persistence_receipt_replay.supabase_client,
            "persist_verified_follow_success_rpc",
        ) as rpc:
            result = follow_persistence_receipt_replay.replay_verified_receipts()
        self.assertTrue(result["ok"])
        self.assertEqual(result["ignored_legacy"], 1)
        rpc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
