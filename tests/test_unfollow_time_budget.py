from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from unfollow_session_orchestrator import _unfollow_time_budget


class UnfollowTimeBudgetTest(unittest.TestCase):
    def test_short_window_uses_historical_p90_action_ceiling(self) -> None:
        now = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
        out = _unfollow_time_budget(
            120,
            business_action_deadline=(now + timedelta(seconds=100)).isoformat(),
            now=now,
        )
        self.assertEqual(out["time_bounded_action_cap"], 4)

    def test_larger_window_stays_bounded_by_time_and_reserve(self) -> None:
        now = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
        out = _unfollow_time_budget(
            120,
            business_action_deadline=(now + timedelta(seconds=310)).isoformat(),
            now=now,
        )
        self.assertEqual(out["time_bounded_action_cap"], 18)

    def test_missing_deadline_preserves_domain_cap(self) -> None:
        out = _unfollow_time_budget(120, business_action_deadline=None)
        self.assertEqual(out["time_bounded_action_cap"], 120)


if __name__ == "__main__":
    unittest.main()
