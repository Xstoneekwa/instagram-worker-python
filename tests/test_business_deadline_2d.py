from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import account_session_orchestrator as orchestrator


class BusinessDeadline2DTests(unittest.TestCase):
    def resolve(self, actionable: int, quota: int) -> dict:
        with (
            mock.patch.object(
                orchestrator, "_follow_to_unfollow_real_enabled", return_value=True
            ),
            mock.patch.object(
                orchestrator,
                "load_unfollow_settings",
                return_value=SimpleNamespace(mode="followed_by_bot"),
            ),
            mock.patch.object(
                orchestrator,
                "_follow_to_unfollow_real_max_actions_effective",
                return_value=quota,
            ),
            mock.patch.object(
                orchestrator.account_protection_lists,
                "unfollow_whitelist_for_run",
                return_value=set(),
            ),
            mock.patch.object(
                orchestrator,
                "plan_unfollow_targets",
                return_value={"eligible_total": actionable},
            ),
        ):
            return orchestrator._resolve_follow_time_handoff(
                "account-a",
                business_action_deadline="2026-08-20T17:50:00Z",
            )

    def test_80_actionable_reserves_48m25_before_terminal_reserve(self) -> None:
        out = self.resolve(80, 80)
        self.assertEqual(out["estimated_seconds_per_unfollow"], 29)
        self.assertEqual(out["follow_new_work_deadline"], "2026-08-20T17:01:35Z")

    def test_120_actionable_reserves_1h07m45_before_terminal_reserve(self) -> None:
        out = self.resolve(120, 120)
        self.assertEqual(out["follow_new_work_deadline"], "2026-08-20T16:42:15Z")

    def test_zero_actionable_does_not_apply_fixed_reserve(self) -> None:
        out = self.resolve(0, 120)
        self.assertIsNone(out["follow_new_work_deadline"])

    def test_actionable_count_is_bounded_by_quota(self) -> None:
        out = self.resolve(120, 80)
        self.assertEqual(out["follow_new_work_deadline"], "2026-08-20T17:01:35Z")

    def test_small_actionable_cohort_uses_dynamic_formula(self) -> None:
        out = self.resolve(3, 120)
        self.assertEqual(out["follow_new_work_deadline"], "2026-08-20T17:38:48Z")


if __name__ == "__main__":
    unittest.main()
