from __future__ import annotations

import unittest
from unittest.mock import patch

from unfollow_eligibility_engine import plan_unfollow_targets
from unfollow_settings import UnfollowSettings


class UnfollowEligibilityTimestampPrecisionTests(unittest.TestCase):
    def test_variable_precision_postgres_timestamp_remains_eligible(self) -> None:
        settings = UnfollowSettings(
            account_id="account-1",
            enabled=True,
            unfollow_only=False,
            do_unfollow_first=False,
            after_days=3,
            mode="unfollow",
            sort_mode="default",
            session_limit=120,
            day_limit=120,
            runtime_cap_mode="prod_normal",
            runtime_safety_cap=None,
            defaults_used=False,
            package_default_snapshot={},
        )
        row = {
            "id": "interaction-1",
            "username": "eligible_user",
            "followed_by_bot": True,
            "followed_at": "2026-07-17T16:05:33.83451+00:00",
            "eligible_unfollow_at": "2026-07-20T16:05:33.83451+00:00",
            "unfollowed_at": None,
            "whitelist_protected": False,
            "follow_status": "following",
            "interaction_lifecycle_state": "active_following",
        }

        with patch(
            "supabase_client.fetch_unfollow_strict_candidate_rows",
            return_value=[row],
        ):
            plan = plan_unfollow_targets("account-1", settings=settings)

        self.assertEqual(plan["candidates_count"], 1)
        self.assertEqual(plan["skipped_counts"]["missing_followed_at"], 0)
        self.assertEqual(plan["source_rows_loaded"], 1)
        self.assertEqual(plan["query_limit"], 480)
        self.assertFalse(plan["pagination_used"])


if __name__ == "__main__":
    unittest.main()
