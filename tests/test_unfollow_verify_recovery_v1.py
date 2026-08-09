from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import unfollow_session_orchestrator as orchestrator
from unfollow_action_outcome import (
    ACTION_ATTEMPTED_AMBIGUOUS_COOLDOWN_MINUTES,
    ACTION_ATTEMPTED_AMBIGUOUS_REASON_PREFIX,
    UnfollowActionOutcomeClass,
    ambiguous_failure_reason,
    decide_verify_failure_after_recovery,
)
from unfollow_eligibility_engine import _strict_unfollow_skip_reason


def _decision(
    *,
    verification_ok: bool = False,
    restored: bool = True,
    package_activity_ok: bool = True,
    account_identity_ok: bool = True,
    unsafe: bool = False,
    persistence_ok: bool = True,
    previous_class: str = "",
    previous_count: int = 0,
    maximum: int = 2,
):
    return decide_verify_failure_after_recovery(
        verification_ok=verification_ok,
        action_attempted=True,
        failure_reason="unfollow_verify_conditions_not_met",
        exact_following_list_restored=restored,
        package_activity_ok=package_activity_ok,
        account_identity_ok=account_identity_ok,
        unsafe_markers_present=unsafe,
        persistence_ok=persistence_ok,
        previous_failure_class=previous_class,
        previous_consecutive_count=previous_count,
        max_consecutive_failures=maximum,
    )


class UnfollowVerifyRecoveryPolicyTest(unittest.TestCase):
    def test_private_confirmation_ambiguous_failure_uses_existing_recovery_policy(self) -> None:
        out = decide_verify_failure_after_recovery(
            verification_ok=False,
            action_attempted=True,
            failure_reason="private_confirmation_disappeared_before_tap",
            exact_following_list_restored=True,
            package_activity_ok=True,
            account_identity_ok=True,
            unsafe_markers_present=False,
            persistence_ok=True,
            previous_failure_class="",
            previous_consecutive_count=0,
            max_consecutive_failures=2,
        )
        self.assertEqual(
            out.candidate_outcome_class,
            UnfollowActionOutcomeClass.ACTION_ATTEMPTED_AMBIGUOUS,
        )
        self.assertEqual(
            out.recovery_class,
            UnfollowActionOutcomeClass.VERIFY_FAILED_RECOVERABLE,
        )
        self.assertTrue(out.should_continue)

    def test_verified_unfollow_resets_failure_streak(self) -> None:
        out = _decision(
            verification_ok=True,
            previous_class=UnfollowActionOutcomeClass.VERIFY_FAILED_RECOVERABLE.value,
            previous_count=2,
        )
        self.assertEqual(
            out.candidate_outcome_class,
            UnfollowActionOutcomeClass.VERIFIED_UNFOLLOW,
        )
        self.assertTrue(out.should_continue)
        self.assertEqual(out.next_consecutive_count, 0)

    def test_missing_verify_condition_with_exact_recovery_continues_without_success(self) -> None:
        out = _decision()
        self.assertEqual(
            out.candidate_outcome_class,
            UnfollowActionOutcomeClass.ACTION_ATTEMPTED_AMBIGUOUS,
        )
        self.assertEqual(
            out.recovery_class,
            UnfollowActionOutcomeClass.VERIFY_FAILED_RECOVERABLE,
        )
        self.assertTrue(out.should_continue)
        self.assertFalse(out.circuit_breaker_open)
        self.assertTrue(
            out.stable_reason.startswith(ACTION_ATTEMPTED_AMBIGUOUS_REASON_PREFIX)
        )

    def test_recovery_following_impossible_safe_stops(self) -> None:
        out = _decision(restored=False)
        self.assertFalse(out.should_continue)
        self.assertEqual(
            out.recovery_class,
            UnfollowActionOutcomeClass.VERIFY_FAILED_UNSAFE_STATE,
        )

    def test_package_or_activity_mismatch_safe_stops(self) -> None:
        out = _decision(package_activity_ok=False)
        self.assertFalse(out.should_continue)
        self.assertEqual(
            out.recovery_class,
            UnfollowActionOutcomeClass.VERIFY_FAILED_UNSAFE_STATE,
        )

    def test_persistence_failure_is_never_recoverable(self) -> None:
        out = _decision(persistence_ok=False)
        self.assertFalse(out.should_continue)
        self.assertEqual(
            out.candidate_outcome_class,
            UnfollowActionOutcomeClass.ACTION_ATTEMPTED_AMBIGUOUS,
        )

    def test_wrong_account_is_security_block(self) -> None:
        out = _decision(account_identity_ok=False)
        self.assertFalse(out.should_continue)
        self.assertEqual(out.recovery_class, UnfollowActionOutcomeClass.SECURITY_BLOCK)

    def test_challenge_or_restriction_is_security_block(self) -> None:
        out = _decision(unsafe=True)
        self.assertFalse(out.should_continue)
        self.assertEqual(out.recovery_class, UnfollowActionOutcomeClass.SECURITY_BLOCK)

    def test_one_recoverable_failure_then_success_resets_counter(self) -> None:
        first = _decision()
        success = _decision(
            verification_ok=True,
            previous_class=first.recovery_class.value,
            previous_count=first.next_consecutive_count,
        )
        second = _decision(
            previous_class=success.recovery_class.value,
            previous_count=success.next_consecutive_count,
        )
        self.assertEqual(first.next_consecutive_count, 1)
        self.assertEqual(success.next_consecutive_count, 0)
        self.assertEqual(second.next_consecutive_count, 1)

    def test_third_consecutive_same_failure_opens_existing_two_failure_circuit(self) -> None:
        first = _decision()
        second = _decision(
            previous_class=first.recovery_class.value,
            previous_count=first.next_consecutive_count,
        )
        third = _decision(
            previous_class=second.recovery_class.value,
            previous_count=second.next_consecutive_count,
        )
        self.assertTrue(first.should_continue)
        self.assertTrue(second.should_continue)
        self.assertFalse(third.should_continue)
        self.assertTrue(third.circuit_breaker_open)
        self.assertEqual(third.next_consecutive_count, 3)

    def test_j_s1_redacted_fixture_continues_with_thirty_slots_remaining(self) -> None:
        fixture = {
            "verified_before_failure": 20,
            "session_cap": 50,
            "daily_remaining_after_verified": 60,
            "backlog_actionable": 30,
            "time_capacity": 30,
            "verify_ok": False,
            "exact_following_list_restored": True,
        }
        out = _decision(restored=fixture["exact_following_list_restored"])
        session_remaining = fixture["session_cap"] - fixture["verified_before_failure"]
        max_additional = min(
            session_remaining,
            fixture["daily_remaining_after_verified"],
            fixture["backlog_actionable"],
            fixture["time_capacity"],
        )
        self.assertTrue(out.should_continue)
        self.assertEqual(max_additional, 30)
        self.assertNotEqual(
            out.candidate_outcome_class,
            UnfollowActionOutcomeClass.VERIFIED_UNFOLLOW,
        )


class UnfollowAmbiguousDurabilityTest(unittest.TestCase):
    def _row(self, *, now: datetime, attempted_at: datetime) -> dict:
        return {
            "id": "redacted-row",
            "username": "redacted_candidate",
            "followed_by_bot": True,
            "followed_at": (now - timedelta(days=40)).isoformat(),
            "eligible_unfollow_at": (now - timedelta(days=10)).isoformat(),
            "follow_status": "following",
            "interaction_lifecycle_state": "active_following",
            "unfollowed_at": None,
            "unfollow_skip_reason": ambiguous_failure_reason(
                "unfollow_verify_conditions_not_met"
            ),
            "last_unfollow_attempt_at": attempted_at.isoformat(),
        }

    def test_ambiguous_action_is_held_durably_and_not_immediately_replayed(self) -> None:
        now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
        row = self._row(now=now, attempted_at=now - timedelta(minutes=1))
        reason = _strict_unfollow_skip_reason(
            row,
            settings=SimpleNamespace(after_days=7, mode="unfollow"),
            now=now,
        )
        self.assertEqual(reason, "candidate_action_ambiguous_cooldown")

    def test_ambiguous_action_can_be_preflighted_again_after_existing_cooldown(self) -> None:
        now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
        row = self._row(
            now=now,
            attempted_at=now
            - timedelta(minutes=ACTION_ATTEMPTED_AMBIGUOUS_COOLDOWN_MINUTES + 1),
        )
        reason = _strict_unfollow_skip_reason(
            row,
            settings=SimpleNamespace(after_days=7, mode="unfollow"),
            now=now,
        )
        self.assertEqual(reason, "")

    def test_unfollow_any_mode_also_holds_recent_ambiguous_action(self) -> None:
        now = datetime.now(timezone.utc)
        db_row = self._row(now=now, attempted_at=now - timedelta(minutes=1))
        visible_rows = [
            {
                "username": "redacted_candidate",
                "username_normalized": "redacted_candidate",
                "row_index": 1,
                "row_cta_class": "following",
            }
        ]
        with patch.object(
            orchestrator.account_protection_lists,
            "is_unfollow_protected",
            return_value=False,
        ):
            out = orchestrator._evaluate_visible_unfollow_any_with_session_cache(
                "redacted-account",
                visible_rows,
                account_username="owner_account",
                row_cache={"redacted_candidate": db_row},
                completed_usernames=set(),
            )
        self.assertEqual(out["visible_eligible_matches_count"], 0)
        self.assertEqual(
            out["visible_eligibility_skip_counts"][
                "candidate_action_ambiguous_cooldown"
            ],
            1,
        )

    def test_reason_wrapper_is_idempotent(self) -> None:
        first = ambiguous_failure_reason("unfollow_verify_conditions_not_met")
        self.assertEqual(ambiguous_failure_reason(first), first)

    def test_runtime_contract_never_replays_same_candidate_in_same_run(self) -> None:
        source = inspect.getsource(orchestrator._run_real_unfollow_multi_loop)
        recovery_block = source.split(
            '"unfollow_verify_failure_recovered_continue"', 1
        )[0].rsplit("if decision.should_continue:", 1)[1]
        self.assertIn("failed_usernames_this_run.add(target_key)", recovery_block)
        self.assertIn("completed_usernames.add(target_key)", recovery_block)
        self.assertIn("mark_candidate_technical_hold(target_key)", recovery_block)
        self.assertNotIn("tap_unfollow_in_following_sheet", recovery_block)

    def test_runtime_contract_has_exactly_one_bounded_recovery_path(self) -> None:
        source = inspect.getsource(orchestrator._run_real_unfollow_multi_loop)
        recovery_helper = source.split(
            "def recover_exact_following_after_ambiguous_action", 1
        )[1].split("def exploration_fields", 1)[0]
        self.assertEqual(
            recovery_helper.count("return_to_following_list_after_unfollow_action("),
            1,
        )
        self.assertNotIn("open_own_following_list_from_own_profile(", recovery_helper)

    def test_private_confirmation_success_uses_single_existing_persistence_path(self) -> None:
        source = inspect.getsource(orchestrator._run_real_unfollow_multi_loop)
        verify_block = source.split(
            "verify_out = verify_unfollow_action_success_after_tap(", 1
        )[1]
        self.assertIn("profile_identity_certified=True", verify_block)
        self.assertIn("private_flow_engaged=True", verify_block)
        self.assertEqual(source.count("_persist_unfollow_outcome_for_session("), 1)
        self.assertLess(
            source.index("verify_ok =", source.index("verify_out =")),
            source.index("_persist_unfollow_outcome_for_session("),
        )

    def test_single_action_runtime_also_wires_certified_private_confirmation(self) -> None:
        source = inspect.getsource(orchestrator.run_unfollow_session)
        real_action_tail = source.rsplit(
            "verify_out = verify_unfollow_action_success_after_tap(", 1
        )[1]
        self.assertIn("profile_identity_certified=True", real_action_tail)
        self.assertIn("private_flow_engaged=True", real_action_tail)

    def test_existing_already_not_following_terminal_branch_remains_action_free(self) -> None:
        source = inspect.getsource(orchestrator._run_real_unfollow_multi_loop)
        terminal_block = source.split(
            'sheet_failure_reason == "already_not_following_confirmed"', 1
        )[1].split("if recoverable and coverage_tracker", 1)[0]
        self.assertIn("record_unfollow_already_not_following_v1", terminal_block)
        self.assertIn("completed_usernames.add(target_key)", terminal_block)
        self.assertNotIn("tap_unfollow_in_following_sheet", terminal_block)


if __name__ == "__main__":
    unittest.main()
