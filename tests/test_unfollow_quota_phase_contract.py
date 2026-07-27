from __future__ import annotations

import inspect
import unittest

from account_session_resume_engine import _phase_to_run_unfollow, _unfollow_quota
from unfollow_session_orchestrator import (
    _run_real_unfollow_multi_loop,
    resolve_effective_unfollow_day_progress,
    unfollow_quota_reached_after_persist,
)


class UnfollowQuotaPhaseContractTest(unittest.TestCase):
    def test_resume_plan_can_only_reduce_stale_db_budget(self) -> None:
        progress = resolve_effective_unfollow_day_progress(
            day_limit=120,
            persisted_daily_count=89,
            quota_remaining_hint=18,
        )
        self.assertEqual(progress["effective_done"], 102)
        self.assertEqual(progress["remaining"], 18)

    def test_direct_persisted_count_wins_over_older_resume_plan(self) -> None:
        progress = resolve_effective_unfollow_day_progress(
            day_limit=120,
            persisted_daily_count=110,
            quota_remaining_hint=18,
        )
        self.assertEqual(progress["effective_done"], 110)
        self.assertEqual(progress["remaining"], 10)

    def test_remaining_one_stops_on_exact_last_persist(self) -> None:
        self.assertTrue(
            unfollow_quota_reached_after_persist(
                day_limit=120,
                effective_done_at_start=119,
                verified_persisted_in_run=1,
            )
        )

    def test_no_off_by_one_before_last_persist(self) -> None:
        self.assertFalse(
            unfollow_quota_reached_after_persist(
                day_limit=120,
                effective_done_at_start=118,
                verified_persisted_in_run=1,
            )
        )

    def test_quota_reached_is_terminal_in_resume_plan(self) -> None:
        summary = {
            "unfollow_outcome": {
                "phase_status": "quota_reached",
                "remaining_count": 42,
                "persisted_count": 1,
            }
        }
        target, done, remaining = _unfollow_quota(summary, {})
        self.assertEqual(remaining, 0)
        self.assertFalse(_phase_to_run_unfollow(summary, target, done, remaining))

    def test_candidates_exhausted_is_terminal_even_below_package_cap(self) -> None:
        summary = {
            "unfollow_outcome": {
                "phase_status": "candidates_exhausted",
                "remaining_count": 0,
                "persisted_count": 100,
            }
        }
        target, done, remaining = _unfollow_quota(summary, {})
        self.assertEqual(remaining, 0)
        self.assertFalse(_phase_to_run_unfollow(summary, target, done, remaining))

    def test_quota_stop_precedes_any_return_navigation(self) -> None:
        source = inspect.getsource(_run_real_unfollow_multi_loop)
        quota_branch = source.index("if quota_reached:")
        immediate_stop = source.index(
            'return emit_final("success_real_unfollow_multi_quota_reached")',
            quota_branch,
        )
        return_navigation = source.index("ret = (", immediate_stop)
        self.assertLess(quota_branch, immediate_stop)
        self.assertLess(immediate_stop, return_navigation)


if __name__ == "__main__":
    unittest.main()
