from __future__ import annotations

import unittest
from pathlib import Path

from unfollow_daily_plan import (
    SCHEMA,
    checkpoint_daily_plan,
    frozen_unfollow_after_days,
    prepare_authoritative_daily_plan,
)


def candidates(count: int, *, start: int = 0) -> list[dict[str, str]]:
    return [
        {"username": f"candidate_{index:03d}", "username_normalized": f"candidate_{index:03d}"}
        for index in range(start, start + count)
    ]


class UnfollowDailyPlanTests(unittest.TestCase):
    def prepare(self, rows, *, quota=80, checkpoint=None):
        return prepare_authoritative_daily_plan(
            account_id="account-1",
            business_date_sast="2026-08-10",
            package_contract_version="growth-v1",
            daily_quota_target=quota,
            current_unfollow_after_days=3,
            current_eligible_candidates=rows,
            resume_checkpoint=checkpoint,
            created_at="2026-08-10T08:00:00+00:00",
        )

    def test_growth_80_80_initial_plan_exposes_full_initial_cohort(self):
        out = self.prepare(candidates(169), quota=80)
        self.assertEqual(out["daily_plan"]["schema"], SCHEMA)
        self.assertEqual(out["daily_plan"]["initial_db_eligible_count"], 169)
        self.assertEqual(len(out["candidates"]), 169)

    def test_pro_and_premium_120_120_initial_plan_expose_quota_when_available(self):
        for package_version in ("pro-v1", "premium-v1"):
            out = prepare_authoritative_daily_plan(
                account_id="account-1",
                business_date_sast="2026-08-10",
                package_contract_version=package_version,
                daily_quota_target=120,
                current_unfollow_after_days=3,
                current_eligible_candidates=candidates(140),
                created_at="2026-08-10T08:00:00+00:00",
            )
            self.assertGreaterEqual(len(out["candidates"]), 120)

    def test_resume_reuses_plan_id_order_and_remaining_queue(self):
        first = self.prepare(candidates(100))
        checkpoint = checkpoint_daily_plan(first["daily_plan"], {
            "remaining_usernames": [f"candidate_{index:03d}" for index in range(50, 100)],
            "verified_usernames": [f"candidate_{index:03d}" for index in range(50)],
            "persisted_usernames": [f"candidate_{index:03d}" for index in range(50)],
        })
        resumed = self.prepare(candidates(120), checkpoint=checkpoint)
        self.assertTrue(resumed["resume_reused"])
        self.assertEqual(resumed["daily_plan"]["plan_id"], first["daily_plan"]["plan_id"])
        self.assertEqual(
            [row["username_normalized"] for row in resumed["candidates"]],
            [f"candidate_{index:03d}" for index in range(50, 100)],
        )
        self.assertEqual(
            resumed["daily_plan"]["newly_eligible_after_plan_freeze"],
            [f"candidate_{index:03d}" for index in range(100, 120)],
        )

    def test_terminal_candidate_never_reenters_and_new_candidate_is_only_classified(self):
        first = self.prepare(candidates(5))
        checkpoint = checkpoint_daily_plan(first["daily_plan"], {
            "remaining_usernames": ["candidate_001", "candidate_002", "candidate_003"],
            "unavailable_usernames": ["candidate_000"],
        })
        resumed = self.prepare(
            [candidates(5)[1], candidates(5)[3], *candidates(1, start=10)],
            checkpoint=checkpoint,
        )
        self.assertEqual(
            [row["username_normalized"] for row in resumed["candidates"]],
            ["candidate_001", "candidate_003"],
        )
        self.assertEqual(resumed["daily_plan"]["currently_terminal_or_ineligible"], ["candidate_002"])
        self.assertEqual(resumed["daily_plan"]["newly_eligible_after_plan_freeze"], ["candidate_010"])

    def test_different_business_date_invalidates_prior_plan(self):
        first = self.prepare(candidates(3))
        checkpoint = checkpoint_daily_plan(first["daily_plan"], {"remaining_usernames": ["candidate_002"]})
        rebuilt = prepare_authoritative_daily_plan(
            account_id="account-1",
            business_date_sast="2026-08-11",
            package_contract_version="growth-v1",
            daily_quota_target=80,
            current_unfollow_after_days=10,
            current_eligible_candidates=candidates(4),
            resume_checkpoint=checkpoint,
        )
        self.assertFalse(rebuilt["resume_reused"])
        self.assertNotEqual(rebuilt["daily_plan"]["plan_id"], first["daily_plan"]["plan_id"])

    def test_only_an_explicit_session_cap_lower_than_daily_is_structural_multi_session(self):
        canonical = prepare_authoritative_daily_plan(
            account_id="account-1",
            business_date_sast="2026-08-10",
            package_contract_version="growth-80-80",
            daily_quota_target=80,
            session_quota_target=80,
            current_unfollow_after_days=3,
            current_eligible_candidates=candidates(100),
        )
        override = prepare_authoritative_daily_plan(
            account_id="account-1",
            business_date_sast="2026-08-10",
            package_contract_version="growth-80-20-explicit",
            daily_quota_target=80,
            session_quota_target=20,
            current_unfollow_after_days=3,
            current_eligible_candidates=candidates(100),
        )
        self.assertFalse(canonical["daily_plan"]["multi_session_required_by_explicit_cap_override"])
        self.assertTrue(override["daily_plan"]["multi_session_required_by_explicit_cap_override"])

    def test_same_business_day_reuses_plan_after_policy_change(self):
        first = self.prepare(candidates(4))
        checkpoint = checkpoint_daily_plan(
            first["daily_plan"],
            {"remaining_usernames": ["candidate_001", "candidate_002"]},
        )
        resumed = prepare_authoritative_daily_plan(
            account_id="account-1",
            business_date_sast="2026-08-10",
            package_contract_version="growth-v2-after-days-10",
            daily_quota_target=80,
            current_unfollow_after_days=10,
            current_eligible_candidates=candidates(2, start=1),
            resume_checkpoint=checkpoint,
        )
        self.assertTrue(resumed["resume_reused"])
        self.assertEqual(resumed["daily_plan"]["plan_id"], first["daily_plan"]["plan_id"])
        self.assertEqual(resumed["daily_plan"]["policy_snapshot"]["unfollow_after_days"], 3)
        self.assertTrue(resumed["daily_plan"]["package_contract_changed_after_plan_freeze"])

    def test_lower_policy_same_day_does_not_append_newly_eligible_candidate(self):
        first = self.prepare(candidates(2))
        checkpoint = checkpoint_daily_plan(
            first["daily_plan"],
            {"remaining_usernames": ["candidate_001"]},
        )
        resumed = prepare_authoritative_daily_plan(
            account_id="account-1",
            business_date_sast="2026-08-10",
            package_contract_version="growth-v2-after-days-2",
            daily_quota_target=80,
            current_unfollow_after_days=2,
            current_eligible_candidates=[candidates(2)[1], *candidates(1, start=10)],
            resume_checkpoint=checkpoint,
        )
        self.assertEqual(
            [row["username_normalized"] for row in resumed["candidates"]],
            ["candidate_001"],
        )
        self.assertEqual(
            resumed["daily_plan"]["newly_eligible_after_plan_freeze"],
            ["candidate_010"],
        )

    def test_policy_snapshot_is_used_only_for_matching_account_and_business_date(self):
        first = self.prepare(candidates(1))
        checkpoint = {"daily_plan": first["daily_plan"]}
        self.assertEqual(
            frozen_unfollow_after_days(
                account_id="account-1",
                business_date_sast="2026-08-10",
                current_after_days=10,
                resume_checkpoint=checkpoint,
            ),
            3,
        )
        self.assertEqual(
            frozen_unfollow_after_days(
                account_id="other-account",
                business_date_sast="2026-08-10",
                current_after_days=10,
                resume_checkpoint=checkpoint,
            ),
            10,
        )
        self.assertEqual(
            frozen_unfollow_after_days(
                account_id="account-1",
                business_date_sast="2026-08-11",
                current_after_days=10,
                resume_checkpoint=checkpoint,
            ),
            10,
        )

    def test_same_session_search_is_not_artificially_limited_to_ten_candidates(self):
        root = Path(__file__).resolve().parents[1]
        orchestrator_source = root.joinpath("unfollow_session_orchestrator.py").read_text()
        strategy_source = root.joinpath("unfollow_hybrid_strategy.py").read_text()
        self.assertNotIn("DIRECT_SEARCH_FALLBACK_BATCH_LIMIT", strategy_source)
        self.assertNotIn('stop_reason = "direct_fallback_batch_limit_reached"', orchestrator_source)
        self.assertIn('if hybrid.mode == "direct_exact"', orchestrator_source)
        self.assertIn("progressive_scan_exhausted_authoritative_fallback", strategy_source)


if __name__ == "__main__":
    unittest.main()
