from __future__ import annotations

import unittest

from follow60_ordering_v2_ledger_v1 import LedgerScope, OrderingLedger


def _scope(**overrides):
    values = {
        "account_id": "account-a",
        "run_id": "run-a",
        "request_id": "request-a",
        "business_session_id": "session-a",
        "target_id": "target-a",
        "action_id": "action-a",
        "candidate_username": "alice",
    }
    values.update(overrides)
    return LedgerScope(**values)


def _advance_to_like(ledger: OrderingLedger, *, skipped: bool = False) -> None:
    ledger.apply_receipt("profile_certified", {"exact": True})
    ledger.apply_receipt("post_opened", {"v5": True})
    ledger.apply_receipt(
        "like_skipped" if skipped else "like_verified",
        {"reason": "v5_rejected"} if skipped else {"verified": True},
    )


class Follow60OrderingV2LedgerV1Tests(unittest.TestCase):
    def test_like_verified_then_follow_verified_and_complete(self) -> None:
        ledger = OrderingLedger(_scope())
        _advance_to_like(ledger)
        ledger.apply_receipt("profile_reentry_verified", {"exact": True})
        ledger.apply_receipt("follow_pending", {"reserved": True})
        ledger.apply_receipt("follow_verified", {"state": "following"})
        ledger.apply_receipt("mute_posts_verified", {"verified": True})
        ledger.apply_receipt("mute_stories_verified", {"verified": True})
        ledger.apply_receipt("return_ct_exact", {"exact": True})
        self.assertTrue(ledger.cycle_complete)
        self.assertEqual("cycle_complete", ledger.next_stage())

    def test_duplicate_like_receipt_is_idempotent_and_never_replayed(self) -> None:
        ledger = OrderingLedger(_scope())
        _advance_to_like(ledger)
        duplicate = ledger.apply_receipt("like_verified", {"verified": True})
        self.assertTrue(duplicate["duplicate"])
        self.assertFalse(ledger.replay_plan()["like_replay_allowed"])
        with self.assertRaisesRegex(ValueError, "ledger_duplicate_conflict"):
            ledger.apply_receipt("like_verified", {"verified": False})

    def test_follow_failure_preserves_like_and_never_completes_cycle(self) -> None:
        ledger = OrderingLedger(_scope())
        _advance_to_like(ledger)
        ledger.apply_receipt("profile_reentry_verified", {"exact": True})
        ledger.apply_receipt("follow_pending", {"reserved": True})
        ledger.apply_receipt("follow_failed", {"reason": "verify_failed"})
        self.assertIn("like_verified", ledger.stages)
        self.assertFalse(ledger.cycle_complete)
        self.assertEqual("follow_failed_terminal", ledger.next_stage())
        self.assertFalse(ledger.replay_plan()["like_replay_allowed"])

    def test_stop_after_like_preserves_receipt_and_resumes_at_reentry(self) -> None:
        ledger = OrderingLedger(_scope())
        _advance_to_like(ledger)
        ledger.apply_receipt("stop_recorded", {"source": "operator"})
        plan = ledger.replay_plan()
        self.assertEqual("profile_reentry_verified", plan["next_stage"])
        self.assertFalse(plan["like_replay_allowed"])
        self.assertFalse(plan["follow_invented"])
        self.assertFalse(plan["mute_invented"])
        self.assertFalse(plan["cycle_complete"])

    def test_like_skipped_v5_reject_can_continue_but_not_fake_like(self) -> None:
        ledger = OrderingLedger(_scope())
        _advance_to_like(ledger, skipped=True)
        self.assertIn("like_skipped", ledger.stages)
        self.assertNotIn("like_verified", ledger.stages)
        self.assertEqual("profile_reentry_verified", ledger.next_stage())

    def test_scopes_are_distinct_across_account_run_candidate_and_action(self) -> None:
        base = _scope()
        keys = {
            base.idempotency_key("like_verified"),
            _scope(account_id="account-b").idempotency_key("like_verified"),
            _scope(run_id="run-b").idempotency_key("like_verified"),
            _scope(candidate_username="bob").idempotency_key("like_verified"),
            _scope(action_id="action-b").idempotency_key("like_verified"),
        }
        self.assertEqual(5, len(keys))

    def test_ack_is_idempotent_and_missing_ack_fails_closed(self) -> None:
        ledger = OrderingLedger(_scope())
        ledger.apply_receipt("profile_certified", {"exact": True})
        self.assertFalse(ledger.acknowledge("profile_certified")["duplicate"])
        self.assertTrue(ledger.acknowledge("profile_certified")["duplicate"])
        with self.assertRaisesRegex(ValueError, "ledger_receipt_missing"):
            ledger.acknowledge("like_verified")

    def test_transition_order_is_fail_closed(self) -> None:
        ledger = OrderingLedger(_scope())
        with self.assertRaisesRegex(ValueError, "ledger_transition_invalid"):
            ledger.apply_receipt("follow_verified", {"state": "following"})
        with self.assertRaisesRegex(ValueError, "ledger_transition_invalid"):
            ledger.apply_receipt("like_verified", {"verified": True})


if __name__ == "__main__":
    unittest.main()
