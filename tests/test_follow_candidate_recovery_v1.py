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

    def test_already_liked_retries_follow_only(self) -> None:
        out = recovery.decide_recovery(
            like_state="liked", follow_state="follow", quota_available=True
        )
        self.assertFalse(out.retry_like)
        self.assertTrue(out.retry_follow)
        self.assertFalse(out.create_follow_receipt)
        self.assertFalse(out.increment_follow_counter)

    def test_ambiguous_like_never_taps_like(self) -> None:
        out = recovery.decide_recovery(
            like_state="unknown", follow_state="follow", quota_available=True
        )
        self.assertEqual(out.reason, "ambiguous_like_skip")
        self.assertFalse(out.retry_like)
        self.assertTrue(out.retry_follow)

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
    def test_duplicate_enqueue_uses_same_account_target_candidate_key(self, enqueue) -> None:
        enqueue.return_value = {"id": "r1"}
        kwargs = {
            "account_id": "00000000-0000-4000-8000-000000000001",
            "candidate_username": "@Person",
            "source_target_id": "00000000-0000-4000-8000-000000000002",
            "source_ct_username": "Source",
            "original_run_id": None,
            "original_request_id": None,
            "business_session_id": None,
            "evidence": {"like_state": "liked"},
        }
        recovery.enqueue(**kwargs)
        recovery.enqueue(**kwargs)
        first = enqueue.call_args_list[0].kwargs["recovery_key"]
        second = enqueue.call_args_list[1].kwargs["recovery_key"]
        self.assertEqual(first, second)
        self.assertEqual(enqueue.call_args_list[0].kwargs["candidate_username"], "person")


if __name__ == "__main__":
    unittest.main()
