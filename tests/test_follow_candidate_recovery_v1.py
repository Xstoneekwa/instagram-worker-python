from __future__ import annotations

import unittest
from unittest.mock import patch

import follow_candidate_recovery as recovery


class FollowCandidateRecoveryV1Test(unittest.TestCase):
    def test_key_is_account_and_source_scoped(self) -> None:
        base = recovery.recovery_key("a", "@Person", "ct-1")
        self.assertEqual(base, recovery.recovery_key("a", "person", "ct-1"))
        self.assertNotEqual(base, recovery.recovery_key("b", "person", "ct-1"))
        self.assertNotEqual(base, recovery.recovery_key("a", "person", "ct-2"))

    def test_already_liked_is_nonactionable_without_phone_retry(self) -> None:
        out = recovery.decide_recovery(
            like_state="liked", follow_state="follow", quota_available=True
        )
        self.assertFalse(out.retry_like)
        self.assertFalse(out.retry_follow)
        self.assertTrue(out.terminal)
        self.assertFalse(out.create_follow_receipt)
        self.assertFalse(out.increment_follow_counter)

    def test_ambiguous_like_never_taps_like(self) -> None:
        out = recovery.decide_recovery(
            like_state="unknown", follow_state="follow", quota_available=True
        )
        self.assertEqual(out.reason, "recovery_evidence_unknown_quarantined")
        self.assertFalse(out.retry_like)
        self.assertFalse(out.retry_follow)
        self.assertTrue(out.terminal)

    def test_already_following_is_unattributed_terminal(self) -> None:
        out = recovery.decide_recovery(
            like_state="liked", follow_state="following", quota_available=True
        )
        self.assertTrue(out.terminal)
        self.assertFalse(out.retry_follow)
        self.assertFalse(out.create_follow_receipt)
        self.assertFalse(out.increment_follow_counter)

    def test_quota_exhaustion_defers_without_mutation(self) -> None:
        out = recovery.decide_recovery(
            like_state="liked", follow_state="follow", quota_available=False
        )
        self.assertEqual(out.action, "defer")
        self.assertFalse(out.retry_like)
        self.assertFalse(out.retry_follow)

    @patch.object(recovery.supabase_client, "claim_follow_candidate_recovery_v1")
    def test_claim_is_exact_account_scoped(self, claim) -> None:
        claim.return_value = [{"id": "r1", "account_id": "a"}]
        self.assertEqual(recovery.claim_pending(account_id="a", worker_id="w"), claim.return_value)
        claim.assert_called_once_with(account_id="a", worker_id="w", limit=20)

    @patch.object(recovery.supabase_client, "enqueue_follow_candidate_recovery_v1")
    def test_like_only_never_enqueues_follow_recovery(self, enqueue) -> None:
        out = recovery.enqueue(
            account_id="00000000-0000-4000-8000-000000000001",
            candidate_username="@Person",
            source_target_id="00000000-0000-4000-8000-000000000002",
            source_ct_username="Source",
            original_run_id=None,
            original_request_id=None,
            business_session_id=None,
            evidence={
                "like_state": "liked",
                "like_tap_sent": True,
                "like_verify_success": True,
                "follow_tap_sent": False,
            },
        )
        self.assertFalse(out["enqueued"])
        self.assertEqual(out["classification"], "already_interacted_no_follow")
        enqueue.assert_not_called()

    @patch.object(recovery.supabase_client, "enqueue_follow_candidate_recovery_v1")
    def test_unknown_never_enqueues_or_preempts(self, enqueue) -> None:
        out = recovery.enqueue(
            account_id="a", candidate_username="person", source_target_id=None,
            source_ct_username=None, original_run_id=None, original_request_id=None,
            business_session_id=None, evidence={"follow_tap_sent": False},
        )
        self.assertFalse(out["enqueued"])
        self.assertEqual(out["classification"], "unknown")
        enqueue.assert_not_called()

    @patch.object(recovery.supabase_client, "enqueue_follow_candidate_recovery_v1")
    def test_physical_follow_ambiguity_uses_backend_queue(self, enqueue) -> None:
        enqueue.return_value = {"id": "r1"}
        kwargs = {
            "account_id": "00000000-0000-4000-8000-000000000001",
            "candidate_username": "@Person",
            "source_target_id": "00000000-0000-4000-8000-000000000002",
            "source_ct_username": "Source",
            "original_run_id": None,
            "original_request_id": None,
            "business_session_id": None,
            "evidence": {
                "follow_tap_sent": True,
                "follow_verified": False,
                "follow_receipt_exists": False,
            },
        }
        recovery.enqueue(**kwargs)
        recovery.enqueue(**kwargs)
        first = enqueue.call_args_list[0].kwargs["recovery_key"]
        second = enqueue.call_args_list[1].kwargs["recovery_key"]
        self.assertEqual(first, second)
        self.assertEqual(enqueue.call_args_list[0].kwargs["candidate_username"], "person")

    def test_migration_removes_search_and_requires_physical_follow_ambiguity(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        sql = (root / "supabase/migrations/20260823004413_already_interacted_p0c_no_search_v1.sql").read_text()
        runner = (root / "runner.py").read_text()
        start = runner.index("def _process_follow_candidate_recovery_batch(")
        end = runner.index("\ndef _update_run_status_safe", start)
        active_recovery = runner[start:end]
        self.assertIn("follow_candidate_recovery_requires_physical_follow_ambiguity", sql)
        self.assertIn("limit greatest(1, least(coalesce(p_limit, 20), 8))", sql)
        self.assertIn("recovery_evidence_unknown_quarantined", sql)
        self.assertIn("non_actionable_already_interacted_no_follow_recovery", sql)
        self.assertNotIn("ensure_global_search_surface", active_recovery)
        self.assertNotIn("search_username", active_recovery)
        self.assertIn("phone_searches=0", active_recovery)


if __name__ == "__main__":
    unittest.main()
