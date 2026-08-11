from __future__ import annotations

import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import account_protection_lists
import supabase_client
from account_session_orchestrator import _run_follow_to_unfollow_handoff_diagnostic
from unfollow_eligibility_engine import plan_unfollow_targets
from unfollow_settings import UnfollowSettings
from unfollow_ui_coverage_policy import (
    FollowingCoverageTracker,
    build_unfollow_outcome,
    derive_adaptive_coverage_budget,
)


AS_OF = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def _row(
    index: int,
    *,
    followed_at: datetime | None = None,
    eligible_at: datetime | None = None,
    username: str | None = None,
    follow_status: str = "following",
    lifecycle: str = "active_following",
    unfollowed_at: datetime | None = None,
    following_back: bool | None = False,
) -> dict[str, object]:
    followed = followed_at if followed_at is not None else AS_OF - timedelta(days=4)
    return {
        "id": f"00000000-0000-4000-8000-{index:012d}",
        "account_id": "account-1",
        "username": username if username is not None else f"candidate_{index:04d}",
        "followed_by_bot": True,
        "followed_at": followed.isoformat() if followed is not None else None,
        "eligible_unfollow_at": (
            eligible_at.isoformat()
            if eligible_at is not None
            else (followed + timedelta(days=3)).isoformat()
        ),
        "unfollowed_at": unfollowed_at.isoformat() if unfollowed_at else None,
        "unfollowed": bool(unfollowed_at),
        "follow_status": follow_status,
        "interaction_lifecycle_state": lifecycle,
        "is_following_back": following_back,
        "run_id": "11111111-1111-4111-8111-111111111111",
        "created_at": (AS_OF - timedelta(days=5)).isoformat(),
    }


def _settings(
    *,
    mode: str = "unfollow",
    session_limit: int = 50,
    after_days: int = 3,
) -> UnfollowSettings:
    return UnfollowSettings(
        account_id="account-1",
        enabled=True,
        unfollow_only=False,
        do_unfollow_first=False,
        after_days=after_days,
        mode=mode,
        sort_mode="default",
        session_limit=session_limit,
        day_limit=120,
        runtime_cap_mode="prod_normal",
        runtime_safety_cap=None,
        defaults_used=False,
        package_default_snapshot={},
    )


class UnfollowCandidatePaginationTests(unittest.TestCase):
    def _paged_request(self, rows: list[dict[str, object]]):
        calls: list[dict[str, str]] = []

        def fake_request(method: str, table: str, *, query=None, **_kwargs):
            self.assertEqual((method, table), ("GET", "ig_interacted_users"))
            safe_query = dict(query or {})
            calls.append(safe_query)
            offset = int(safe_query["offset"])
            limit = int(safe_query["limit"])
            return rows[offset : offset + limit]

        return calls, fake_request

    def test_complete_scan_covers_100_500_and_1001_rows(self) -> None:
        for total, page_size in ((100, 100), (500, 200), (1001, 500)):
            with self.subTest(total=total, page_size=page_size):
                rows = [_row(index) for index in range(total)]
                calls, fake_request = self._paged_request(rows)
                with patch("supabase_client._request_json", side_effect=fake_request):
                    loaded, metadata = supabase_client.fetch_unfollow_strict_candidate_rows(
                        "account-1",
                        limit=page_size,
                        after_days=3,
                        as_of=AS_OF,
                        include_metadata=True,
                    )
                self.assertEqual(len(loaded), total)
                self.assertTrue(metadata["candidate_scan_exhaustive"])
                self.assertEqual(calls[0]["account_id"], "eq.account-1")
                self.assertEqual(calls[0]["followed_by_bot"], "eq.true")
                self.assertEqual(
                    calls[0]["order"],
                    "followed_at.asc.nullslast,created_at.asc,id.asc",
                )
                self.assertEqual(calls[0]["created_at"], f"lte.{AS_OF.isoformat()}")
                self.assertEqual(calls[0]["offset"], "0")
                self.assertEqual(
                    metadata["pagination_used"],
                    len(calls) > 1,
                )
                self.assertEqual(metadata["page_requests"], len(calls))

    def test_repeated_full_page_fails_closed(self) -> None:
        page = [_row(index) for index in range(50)]
        with patch("supabase_client._request_json", return_value=page):
            with self.assertRaisesRegex(
                RuntimeError,
                "unfollow_candidate_pagination_repeated_page",
            ):
                supabase_client.fetch_unfollow_strict_candidate_rows(
                    "account-1",
                    limit=50,
                    as_of=AS_OF,
                    include_metadata=True,
                )

    def test_non_list_page_fails_closed(self) -> None:
        with patch("supabase_client._request_json", return_value={"bad": "shape"}):
            with self.assertRaisesRegex(
                RuntimeError,
                "unfollow_candidate_pagination_response_not_list",
            ):
                supabase_client.fetch_unfollow_strict_candidate_rows(
                    "account-1",
                    limit=50,
                    as_of=AS_OF,
                    include_metadata=True,
                )

    def test_none_page_fails_closed(self) -> None:
        with patch("supabase_client._request_json", return_value=None):
            with self.assertRaisesRegex(
                RuntimeError,
                "unfollow_candidate_pagination_response_not_list",
            ):
                supabase_client.fetch_unfollow_strict_candidate_rows(
                    "account-1",
                    limit=50,
                    as_of=AS_OF,
                    include_metadata=True,
                )

    def test_duplicate_row_id_across_pages_fails_closed(self) -> None:
        first_page = [_row(index) for index in range(2)]

        def fake_request(_method: str, _table: str, *, query=None, **_kwargs):
            return first_page if int(dict(query or {})["offset"]) == 0 else [first_page[1]]

        with patch("supabase_client._request_json", side_effect=fake_request):
            with self.assertRaisesRegex(
                RuntimeError,
                "unfollow_candidate_pagination_duplicate_row_id",
            ):
                supabase_client.fetch_unfollow_strict_candidate_rows(
                    "account-1",
                    limit=2,
                    as_of=AS_OF,
                    include_metadata=True,
                )


class UnfollowCandidateLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.availability_patch = patch(
            "supabase_client.fetch_unfollow_candidate_availability",
            return_value={},
        )
        self.availability_patch.start()

    def tearDown(self) -> None:
        self.availability_patch.stop()

    def test_filters_every_row_before_session_slice(self) -> None:
        rows = [
            _row(index, following_back=True)
            for index in range(550)
        ] + [
            _row(index, following_back=False)
            for index in range(550, 600)
        ]
        with patch(
            "supabase_client.fetch_unfollow_strict_candidate_rows",
            return_value=(
                rows,
                {
                    "page_size": 200,
                    "pages_loaded": 3,
                    "page_requests": 4,
                    "pagination_used": True,
                    "pagination_strategy": "stable_offset_snapshot_v1",
                    "candidate_scan_exhaustive": True,
                    "scan_as_of": AS_OF.isoformat(),
                },
            ),
        ):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(mode="unfollow-non-followers", session_limit=25),
                as_of=AS_OF,
            )

        self.assertEqual(plan["source_rows_loaded"], 600)
        self.assertEqual(plan["skipped_counts"]["not_following_back"], 550)
        self.assertEqual(plan["eligible_total"], 50)
        self.assertEqual(plan["candidates_count"], 25)
        self.assertEqual(plan["unplanned_eligible_count"], 25)
        self.assertEqual(len(plan["diagnostic_eligible_candidates_at_start"]), 50)
        self.assertEqual(
            [row["username_normalized"] for row in plan["candidates"]],
            [
                row["username_normalized"]
                for row in plan["diagnostic_eligible_candidates_at_start"][:25]
            ],
        )
        self.assertTrue(plan["candidate_scan_exhaustive"])
        self.assertTrue(plan["candidate_funnel_reconciled"])

    def test_six_days_fifty_per_day_are_counted_before_slice(self) -> None:
        rows: list[dict[str, object]] = []
        for day in range(6):
            followed_at = AS_OF - timedelta(days=6 - day)
            rows.extend(
                _row(day * 50 + index, followed_at=followed_at)
                for index in range(50)
            )
        with patch(
            "supabase_client.fetch_unfollow_strict_candidate_rows",
            return_value=rows,
        ):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(session_limit=80),
                as_of=AS_OF,
            )

        self.assertEqual(plan["source_rows_loaded"], 300)
        self.assertEqual(plan["eligible_total"], 200)
        self.assertEqual(plan["candidates_count"], 80)
        self.assertEqual(plan["unplanned_eligible_count"], 120)
        self.assertEqual(plan["skipped_counts"]["too_soon"], 100)
        self.assertTrue(plan["candidate_funnel_reconciled"])

    def test_exact_j3_boundary_is_inclusive_and_microsecond_before_is_not(self) -> None:
        rows = [
            _row(1, followed_at=AS_OF - timedelta(days=3)),
            _row(2, followed_at=AS_OF - timedelta(days=3) + timedelta(microseconds=1)),
        ]
        with patch(
            "supabase_client.fetch_unfollow_strict_candidate_rows",
            return_value=rows,
        ):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(),
                as_of=AS_OF,
            )
        self.assertEqual(plan["eligible_total"], 1)
        self.assertEqual(plan["skipped_counts"]["too_soon"], 1)

    def test_legacy_due_date_is_computed_from_immutable_followed_at(self) -> None:
        row = _row(1, followed_at=AS_OF - timedelta(days=3))
        row["eligible_unfollow_at"] = None
        with patch(
            "supabase_client.fetch_unfollow_strict_candidate_rows",
            return_value=[row],
        ):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(),
                as_of=AS_OF,
            )
        self.assertEqual(plan["eligible_total"], 1)
        self.assertEqual(
            plan["candidates"][0]["eligible_unfollow_at_source"],
            "current_policy_from_followed_at",
        )
        self.assertEqual(plan["candidates"][0]["eligible_unfollow_at"], AS_OF.isoformat())

    def test_setting_change_from_three_to_two_requalifies_existing_candidate(self) -> None:
        row = _row(
            1,
            followed_at=AS_OF - timedelta(days=2, hours=12),
            eligible_at=AS_OF + timedelta(hours=12),
        )
        with patch("supabase_client.fetch_unfollow_strict_candidate_rows", return_value=[row]):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(after_days=2),
                as_of=AS_OF,
            )
        self.assertEqual(plan["eligible_total"], 1)
        self.assertEqual(
            plan["candidates"][0]["eligible_unfollow_at"],
            (AS_OF - timedelta(hours=12)).isoformat(),
        )
        self.assertEqual(
            plan["candidates"][0]["historical_eligible_unfollow_at_snapshot"],
            (AS_OF + timedelta(hours=12)).isoformat(),
        )

    def test_setting_change_from_three_to_ten_dequalifies_existing_candidate(self) -> None:
        row = _row(
            1,
            followed_at=AS_OF - timedelta(days=3),
            eligible_at=AS_OF,
        )
        with patch("supabase_client.fetch_unfollow_strict_candidate_rows", return_value=[row]):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(after_days=10),
                as_of=AS_OF,
            )
        self.assertEqual(plan["eligible_total"], 0)
        self.assertEqual(plan["skipped_counts"]["too_soon"], 1)

    def test_successive_policy_changes_are_recomputed_without_backfill(self) -> None:
        row = _row(1, followed_at=AS_OF - timedelta(days=4))
        with patch("supabase_client.fetch_unfollow_strict_candidate_rows", return_value=[row]):
            totals = [
                plan_unfollow_targets(
                    "account-1",
                    settings=_settings(after_days=days),
                    as_of=AS_OF,
                )["eligible_total"]
                for days in (3, 2, 10, 5)
            ]
        self.assertEqual(totals, [1, 1, 0, 0])

    def test_setting_change_from_ten_to_two_uses_current_policy(self) -> None:
        row = _row(
            1,
            followed_at=AS_OF - timedelta(days=2),
            eligible_at=AS_OF + timedelta(days=8),
        )
        with patch("supabase_client.fetch_unfollow_strict_candidate_rows", return_value=[row]):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(after_days=2),
                as_of=AS_OF,
            )
        self.assertEqual(plan["eligible_total"], 1)

    def test_missing_followed_at_remains_ineligible_even_with_historical_snapshot(self) -> None:
        row = _row(1)
        row["followed_at"] = None
        row["eligible_unfollow_at"] = (AS_OF - timedelta(days=1)).isoformat()
        with patch("supabase_client.fetch_unfollow_strict_candidate_rows", return_value=[row]):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(after_days=0),
                as_of=AS_OF,
            )
        self.assertEqual(plan["eligible_total"], 0)
        self.assertEqual(plan["skipped_counts"]["missing_followed_at"], 1)

    def test_dynamic_delay_is_package_agnostic(self) -> None:
        row = _row(1, followed_at=AS_OF - timedelta(days=2))
        for package_name in ("growth", "pro", "premium"):
            with self.subTest(package=package_name), patch(
                "supabase_client.fetch_unfollow_strict_candidate_rows",
                return_value=[row],
            ):
                settings = _settings(after_days=2)
                object.__setattr__(
                    settings,
                    "package_default_snapshot",
                    {"package": package_name},
                )
                plan = plan_unfollow_targets(
                    "account-1",
                    settings=settings,
                    as_of=AS_OF,
                )
                self.assertEqual(plan["eligible_total"], 1)

    def test_whitelist_status_and_duplicate_exclusions_are_explicit(self) -> None:
        rows = [
            _row(1, username="protected.user"),
            _row(2, username="already.done", unfollowed_at=AS_OF - timedelta(hours=1)),
            _row(3, username="not.following", follow_status="unfollowed"),
            _row(4, username="duplicate.user"),
            _row(5, username="DUPLICATE.USER"),
            _row(6, username=""),
            _row(7, username="normal.user"),
        ]
        with patch(
            "supabase_client.fetch_unfollow_strict_candidate_rows",
            return_value=rows,
        ):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(),
                as_of=AS_OF,
                protected_usernames={"protected.user"},
            )

        self.assertEqual(plan["eligible_total"], 2)
        self.assertEqual(plan["skipped_counts"]["whitelist"], 1)
        self.assertEqual(plan["skipped_counts"]["already_unfollowed"], 1)
        self.assertEqual(plan["skipped_counts"]["follow_status_not_following"], 1)
        self.assertEqual(plan["skipped_counts"]["duplicate_username_row"], 1)
        self.assertEqual(plan["skipped_counts"]["invalid_username"], 1)
        self.assertTrue(plan["candidate_funnel_reconciled"])

    def test_blacklist_does_not_become_unfollow_protection(self) -> None:
        row = _row(1, username="interaction.blacklisted")
        with patch(
            "supabase_client.fetch_unfollow_strict_candidate_rows",
            return_value=[row],
        ):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(),
                as_of=AS_OF,
                protected_usernames=set(),
            )
        self.assertEqual(plan["eligible_total"], 1)

    def test_zero_session_limit_preserves_global_eligible_total(self) -> None:
        with patch(
            "supabase_client.fetch_unfollow_strict_candidate_rows",
            return_value=[_row(index) for index in range(3)],
        ):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(session_limit=0),
                as_of=AS_OF,
            )
        self.assertEqual(plan["eligible_total"], 3)
        self.assertEqual(plan["candidates_count"], 0)
        self.assertEqual(plan["unplanned_eligible_count"], 3)

    def test_unplanned_backlog_prevents_false_candidates_exhausted(self) -> None:
        budget = derive_adaptive_coverage_budget(
            quota_remaining=2,
            eligible_remaining=5,
            session_remaining_seconds=3600,
        )
        tracker = FollowingCoverageTracker(budget, {"planned_a", "planned_b"}, 2)
        for username in ("planned_a", "planned_b"):
            tracker.mark_action_attempted(username)
            tracker.mark_action_verified(username)
            tracker.mark_action_persisted(username)
            tracker.mark_safe_profile_return(username)
        outcome = build_unfollow_outcome(
            stable_reason="eligible_targets_exhausted",
            raw_candidate_count=10,
            eligible_candidate_count=5,
            planned_candidate_count=2,
            unplanned_eligible_count=3,
            attempted_count=2,
            verified_count=2,
            persisted_count=2,
            tracker=tracker,
        )
        self.assertEqual(outcome["phase_status"], "partial_resumable")
        self.assertEqual(outcome["planned_remaining_count"], 0)
        self.assertEqual(outcome["remaining_count"], 3)
        self.assertTrue(outcome["resume_recommended"])

    def test_not_found_cooldown_is_excluded_from_actionable_backlog(self) -> None:
        rows = [_row(1, username="temporarily.missing"), _row(2, username="actionable")]
        with patch(
            "supabase_client.fetch_unfollow_strict_candidate_rows",
            return_value=rows,
        ), patch(
            "supabase_client.fetch_unfollow_candidate_availability",
            return_value={
                "temporarily.missing": {
                    "status": "temporary_unavailable",
                    "next_retry_at": (AS_OF + timedelta(hours=24)).isoformat(),
                }
            },
        ):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(),
                as_of=AS_OF,
            )
        self.assertEqual(plan["eligible_total"], 1)
        self.assertEqual(plan["backlog_actionable_remaining"], 1)
        self.assertEqual(plan["backlog_unavailable_remaining"], 1)
        self.assertEqual(plan["skipped_counts"]["candidate_unavailable_cooldown"], 1)

    def test_exhausted_not_found_never_reenters_actionable_backlog(self) -> None:
        with patch(
            "supabase_client.fetch_unfollow_strict_candidate_rows",
            return_value=[_row(1, username="removed.account")],
        ), patch(
            "supabase_client.fetch_unfollow_candidate_availability",
            return_value={"removed.account": {"status": "exhausted"}},
        ):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(),
                as_of=AS_OF,
            )
        self.assertEqual(plan["eligible_total"], 0)
        self.assertEqual(plan["backlog_actionable_remaining"], 0)
        self.assertEqual(plan["backlog_unavailable_remaining"], 1)
        self.assertEqual(plan["skipped_counts"]["candidate_unavailable_exhausted"], 1)

    def test_confirmed_not_found_is_terminal_for_legacy_and_v2_backlog(self) -> None:
        with patch(
            "supabase_client.fetch_unfollow_strict_candidate_rows",
            return_value=[_row(1, username="confirmed.missing")],
        ), patch(
            "supabase_client.fetch_unfollow_candidate_availability",
            return_value={
                "confirmed.missing": {"status": "username_not_found_confirmed"}
            },
        ):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(),
                as_of=AS_OF,
            )
        self.assertEqual(plan["eligible_total"], 0)
        self.assertEqual(plan["backlog_unavailable_remaining"], 1)
        self.assertEqual(plan["skipped_counts"]["candidate_unavailable_exhausted"], 1)

    def test_search_surface_hold_is_excluded_only_until_retry_time(self) -> None:
        rows = [_row(1, username="held.search"), _row(2, username="due.search")]
        with patch(
            "supabase_client.fetch_unfollow_strict_candidate_rows",
            return_value=rows,
        ), patch(
            "supabase_client.fetch_unfollow_candidate_availability",
            return_value={
                "held.search": {
                    "status": "search_surface_unhealthy",
                    "next_retry_at": (AS_OF + timedelta(hours=1)).isoformat(),
                },
                "due.search": {
                    "status": "search_surface_unhealthy",
                    "next_retry_at": (AS_OF - timedelta(microseconds=1)).isoformat(),
                },
            },
        ):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(),
                as_of=AS_OF,
            )
        self.assertEqual(plan["eligible_total"], 1)
        self.assertEqual(plan["candidates"][0]["username"], "due.search")
        self.assertEqual(plan["backlog_unavailable_remaining"], 1)
        self.assertEqual(plan["skipped_counts"]["candidate_unavailable_cooldown"], 1)

    def test_missing_retry_timestamp_does_not_invent_a_technical_hold(self) -> None:
        with patch(
            "supabase_client.fetch_unfollow_strict_candidate_rows",
            return_value=[_row(1, username="retry.unknown")],
        ), patch(
            "supabase_client.fetch_unfollow_candidate_availability",
            return_value={
                "retry.unknown": {
                    "status": "temporary_unavailable",
                    "next_retry_at": None,
                }
            },
        ):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(),
                as_of=AS_OF,
            )
        self.assertEqual(plan["eligible_total"], 1)
        self.assertEqual(plan["backlog_unavailable_remaining"], 0)

    def test_due_cooldown_allows_one_future_retry(self) -> None:
        with patch(
            "supabase_client.fetch_unfollow_strict_candidate_rows",
            return_value=[_row(1, username="retry.due")],
        ), patch(
            "supabase_client.fetch_unfollow_candidate_availability",
            return_value={
                "retry.due": {
                    "status": "temporary_unavailable",
                    "next_retry_at": (AS_OF - timedelta(microseconds=1)).isoformat(),
                }
            },
        ):
            plan = plan_unfollow_targets(
                "account-1",
                settings=_settings(),
                as_of=AS_OF,
            )
        self.assertEqual(plan["eligible_total"], 1)
        self.assertEqual(plan["backlog_unavailable_remaining"], 0)

    def test_quota_reached_does_not_recommend_same_session_resume(self) -> None:
        outcome = build_unfollow_outcome(
            stable_reason="unfollow_quota_reached",
            raw_candidate_count=200,
            eligible_candidate_count=150,
            planned_candidate_count=120,
            unplanned_eligible_count=30,
            attempted_count=120,
            verified_count=120,
            persisted_count=120,
            tracker=None,
        )
        self.assertEqual(outcome["phase_status"], "quota_reached")
        self.assertEqual(outcome["remaining_count"], 30)
        self.assertFalse(outcome["resume_recommended"])

    def test_handoff_diagnostic_uses_global_total_not_probe_slice(self) -> None:
        settings = SimpleNamespace(
            enabled=True,
            mode="unfollow",
            sort_mode="default",
            session_limit=50,
        )
        with patch(
            "account_session_orchestrator.load_unfollow_settings",
            return_value=settings,
        ), patch(
            "account_session_orchestrator.plan_unfollow_targets",
            return_value={
                "candidates_count": 1,
                "eligible_total": 127,
                "plan_reason": "strict_db_eligibility",
                "skipped_counts": {},
            },
        ), patch(
            "account_session_orchestrator.account_protection_lists.unfollow_whitelist_for_run",
            return_value=frozenset(),
        ):
            summary = _run_follow_to_unfollow_handoff_diagnostic(
                account_id="account-1",
                account_username="account_name",
                run_id="22222222-2222-4222-8222-222222222222",
                followers_source_username="source",
                follow_phase_executed=True,
                follow_exit_code=0,
                follow_total_ms=1000,
                session_started_at=time.perf_counter() - 1,
            )
        self.assertEqual(summary["pending_unfollow_count"], 127)
        self.assertEqual(
            summary["pending_unfollow_count_scope"],
            "global_exhaustive_candidate_scan",
        )


class UnfollowProtectionSnapshotTests(unittest.TestCase):
    def test_optional_missing_snapshot_is_empty(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                account_protection_lists.unfollow_whitelist_for_run("account-1"),
                frozenset(),
            )

    def test_required_missing_snapshot_fails_closed(self) -> None:
        with patch.dict(
            os.environ,
            {account_protection_lists.REQUIRED_ENV: "1"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "protection_lists_snapshot_missing"):
                account_protection_lists.unfollow_whitelist_for_run("account-1")


if __name__ == "__main__":
    unittest.main()
