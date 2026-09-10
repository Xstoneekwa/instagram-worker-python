from __future__ import annotations

import unittest

from follow_target_private_streak_retirement_v1 import (
    PRIVATE_OPEN_STREAK_RETIRE_THRESHOLD,
    PRIVATE_OPEN_STREAK_RETIRE_REASON,
    PrivateProfileStreakRetirement,
)


class PrivateProfileStreakRetirementTests(unittest.TestCase):
    def tracker(self) -> PrivateProfileStreakRetirement:
        return PrivateProfileStreakRetirement(
            account_id="account-1",
            target_id="target-1",
            target_username="travel.antwerpen",
            run_id="run-1",
            worker_sha="47b48ff",
        )

    def observe(self, tracker: PrivateProfileStreakRetirement, values: list[bool]) -> None:
        for index, value in enumerate(values):
            tracker.observe_opened_profile(
                candidate_key=f"candidate-{index}",
                is_private=value,
                opened_and_confirmed=True,
            )

    def test_seven_private_does_not_retire(self) -> None:
        tracker = self.tracker()
        self.observe(tracker, [True] * 7)
        self.assertFalse(tracker.retire_pending)
        self.assertEqual(tracker.current_streak, 7)

    def test_eighth_private_retires(self) -> None:
        tracker = self.tracker()
        self.observe(tracker, [True] * 8)
        self.assertTrue(tracker.retire_pending)
        self.assertFalse(tracker.eligible_for_new_work)
        self.assertEqual(PRIVATE_OPEN_STREAK_RETIRE_THRESHOLD, 8)

    def test_public_before_threshold_resets(self) -> None:
        tracker = self.tracker()
        self.observe(tracker, [True] * 7 + [False] + [True])
        self.assertEqual(tracker.current_streak, 1)
        self.assertFalse(tracker.retire_pending)

    def test_other_rejection_is_not_counted(self) -> None:
        tracker = self.tracker()
        tracker.observe_opened_profile(
            candidate_key="candidate-filtered",
            is_private=True,
            opened_and_confirmed=False,
        )
        self.assertEqual(tracker.current_streak, 0)

    def test_duplicate_probe_for_same_open_is_not_counted(self) -> None:
        tracker = self.tracker()
        for _ in range(3):
            tracker.observe_opened_profile(
                candidate_key="same-open",
                is_private=True,
                opened_and_confirmed=True,
            )
        self.assertEqual(tracker.current_streak, 1)

    def test_retirement_is_irrevocable_after_later_public(self) -> None:
        tracker = self.tracker()
        self.observe(tracker, [True] * 8)
        tracker.observe_opened_profile(
            candidate_key="later-public",
            is_private=False,
            opened_and_confirmed=True,
        )
        self.assertTrue(tracker.retire_pending)
        self.assertEqual(tracker.current_streak, 8)

    def test_pending_target_is_excluded_in_same_run(self) -> None:
        tracker = self.tracker()
        self.observe(tracker, [True] * 8)
        self.assertFalse(tracker.eligible_for_new_work)

    def test_archive_requires_pending_retirement(self) -> None:
        with self.assertRaisesRegex(ValueError, "retirement_not_pending"):
            self.tracker().mark_archived()

    def test_archive_marks_target_terminal(self) -> None:
        tracker = self.tracker()
        self.observe(tracker, [True] * 8)
        tracker.mark_archived(archived_at="2026-09-10T00:00:00+00:00")
        self.assertTrue(tracker.archived)
        self.assertFalse(tracker.eligible_for_new_work)

    def test_persistence_failure_preserves_pending_exclusion(self) -> None:
        tracker = self.tracker()
        self.observe(tracker, [True] * 8)
        tracker.mark_persistence_failed("database unavailable")
        self.assertTrue(tracker.retire_pending)
        self.assertFalse(tracker.eligible_for_new_work)
        self.assertEqual(tracker.persistence_error, "database unavailable")

    def test_streak_is_scoped_to_one_tracker_target(self) -> None:
        first = self.tracker()
        second = PrivateProfileStreakRetirement(
            account_id="account-1",
            target_id="target-2",
            target_username="another.target",
            run_id="run-1",
        )
        self.observe(first, [True] * 8)
        self.assertTrue(first.retire_pending)
        self.assertEqual(second.current_streak, 0)

    def test_summary_contains_audit_fields(self) -> None:
        tracker = self.tracker()
        self.observe(tracker, [True, True])
        summary = tracker.summary()
        self.assertEqual(summary["private_streak_current"], 2)
        self.assertEqual(summary["private_streak_threshold"], 8)
        self.assertEqual(summary["private_streak_distinct_opened_count"], 2)
        self.assertEqual(
            summary["private_streak_retire_reason"],
            PRIVATE_OPEN_STREAK_RETIRE_REASON,
        )
        self.assertEqual(summary["private_streak_worker_sha"], "47b48ff")


if __name__ == "__main__":
    unittest.main()
