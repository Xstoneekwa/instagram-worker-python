from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import unfollow_session_orchestrator as unfollow
from unfollow_daily_plan import checkpoint_daily_plan, prepare_authoritative_daily_plan


ACCOUNT_ID = "00000000-0000-4000-8000-000000000001"
BUSINESS_DATE = "2026-08-10"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
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
        package_default_snapshot={"package": "pro"},
    )


def _candidates() -> list[dict[str, str]]:
    return [
        {"username": "candidate_one", "username_normalized": "candidate_one"},
        {"username": "candidate_two", "username_normalized": "candidate_two"},
    ]


class FollowUnfollowDailyPlanHandoffTests(unittest.TestCase):
    def _run_until_identity_guard(
        self,
        *,
        resume_checkpoint: dict | None = None,
    ) -> tuple[int, dict]:
        captured: dict = {}
        real_prepare = prepare_authoritative_daily_plan

        def capture_prepare(**kwargs):
            captured.update(kwargs)
            result = real_prepare(**kwargs)
            captured["result"] = result
            return result

        identity_failure = SimpleNamespace(
            ok=False,
            actual_logged_in_username=None,
            failure_reason="test_stop_after_plan_resolution",
            verification_method="test",
        )
        with (
            patch.object(unfollow, "load_unfollow_settings", return_value=_settings()),
            patch.object(unfollow.supabase_client, "count_successful_unfollows_today", return_value=0),
            patch.object(
                unfollow.supabase_client,
                "sast_business_day_window",
                return_value=(BUSINESS_DATE, "start", "end"),
            ),
            patch.object(
                unfollow.account_protection_lists,
                "unfollow_whitelist_for_run",
                return_value=set(),
            ),
            patch.object(
                unfollow,
                "plan_unfollow_targets",
                return_value={
                    "candidates": _candidates(),
                    "candidates_count": 2,
                    "diagnostic_eligible_candidates_at_start": _candidates(),
                },
            ),
            patch.object(unfollow, "prepare_authoritative_daily_plan", side_effect=capture_prepare),
            patch.object(
                unfollow,
                "verify_active_instagram_account_matches_expected",
                return_value=identity_failure,
            ),
        ):
            exit_code = unfollow.run_unfollow_session(
                object(),
                account_id=ACCOUNT_ID,
                account_username="account_one",
                run_id="follow-handoff-run",
                business_session_id="business-session-one",
                dry_probe_only=True,
                real_action_enabled_override=True,
                real_action_max_override=120,
                resume_checkpoint=resume_checkpoint,
                quota_remaining_hint=120,
            )
        return exit_code, captured

    def test_normal_handoff_creates_daily_plan_v1_with_canonical_session_cap(self) -> None:
        exit_code, captured = self._run_until_identity_guard()

        self.assertEqual(exit_code, 1)
        self.assertEqual(captured["daily_quota_target"], 120)
        self.assertEqual(captured["session_quota_target"], 120)
        self.assertEqual(captured["business_date_sast"], BUSINESS_DATE)
        self.assertFalse(captured["result"]["resume_reused"])
        self.assertEqual(captured["result"]["daily_plan"]["schema"], "UNFOLLOW_DAILY_PLAN_V1")

    def test_resume_reuses_daily_plan_v1_without_run_or_attempt_scoping(self) -> None:
        first = prepare_authoritative_daily_plan(
            account_id=ACCOUNT_ID,
            business_date_sast=BUSINESS_DATE,
            package_contract_version=unfollow.unfollow_daily_plan_contract_version(_settings()),
            daily_quota_target=120,
            session_quota_target=120,
            current_eligible_candidates=_candidates(),
            created_at="2026-08-10T16:00:00+00:00",
        )
        checkpoint = checkpoint_daily_plan(
            first["daily_plan"],
            {"remaining_usernames": ["candidate_two"]},
        )

        exit_code, captured = self._run_until_identity_guard(resume_checkpoint=checkpoint)

        self.assertEqual(exit_code, 1)
        self.assertTrue(captured["result"]["resume_reused"])
        self.assertEqual(
            captured["result"]["daily_plan"]["plan_id"],
            first["daily_plan"]["plan_id"],
        )
        self.assertEqual(
            [row["username_normalized"] for row in captured["result"]["candidates"]],
            ["candidate_two"],
        )


if __name__ == "__main__":
    unittest.main()
