from __future__ import annotations

import inspect
import time
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import account_session_resume_engine
import unfollow_eligibility_engine
import unfollow_session_orchestrator as orchestrator
from unfollow_action_outcome import (
    UnfollowExecutionOutcomeClass,
    UnfollowExecutionContext,
    classify_unfollow_execution_outcome,
)
from unfollow_settings import UnfollowSettings
from unfollow_ui_coverage_policy import derive_adaptive_coverage_budget


def _settings() -> UnfollowSettings:
    return UnfollowSettings(
        account_id="account-1",
        enabled=True,
        unfollow_only=False,
        do_unfollow_first=False,
        after_days=3,
        mode="unfollow",
        sort_mode="default",
        session_limit=120,
        day_limit=200,
        runtime_cap_mode="prod_normal",
        runtime_safety_cap=None,
        defaults_used=False,
        package_default_snapshot={},
    )


def _eligible_row(*, receipt: bool) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "interaction-row-1",
        "username": "candidate_exact",
        "followed_by_bot": True,
        "followed_at": "2026-08-01T00:00:00+00:00",
        "follow_status": "following",
        "lifecycle_state": "followed",
        "unfollowed_at": None,
    }
    if receipt:
        row["canonical_follow_receipt"] = {
            "id": "receipt-1",
            "event_type": "follow_verified_persisted_v1",
            "run_id": "follow-run-1",
            "event_at": "2026-08-01T00:00:00+00:00",
        }
    return row


class UnfollowConsolidationExecutionContextTests(unittest.TestCase):
    def test_context_is_immutable_complete_and_attempt_bounded(self) -> None:
        context = UnfollowExecutionContext.build(
            account_id="account-1",
            account_username="owner",
            request_id="request-1",
            run_id="run-1",
            root_business_session_id="root-session-1",
            attempt_ordinal=1,
            business_date_sast="2026-08-22",
            worker_sha="a" * 40,
            runtime_root="/runtime/release",
            daily_plan_context={"plan_id": "plan-1", "generation": "generation-1"},
            device_id="device-1",
            app_instance_id="app-1",
        )
        self.assertEqual(context.attempt_ordinal, 1)
        self.assertEqual(context.root_business_session_id, "root-session-1")
        with self.assertRaises(Exception):
            context.run_id = "other"  # type: ignore[misc]
        with self.assertRaises(ValueError):
            UnfollowExecutionContext.build(
                account_id="account-1",
                account_username="owner",
                request_id="request-1",
                run_id="run-1",
                root_business_session_id="root-session-1",
                attempt_ordinal=4,
                business_date_sast="2026-08-22",
                worker_sha="a" * 40,
                runtime_root="/runtime/release",
                daily_plan_context={"plan_id": "plan-1", "generation": "generation-1"},
            )

    def test_real_loop_has_no_python_closure_or_free_runtime_identity(self) -> None:
        fn = orchestrator._run_real_unfollow_multi_loop
        source = inspect.getsource(fn)
        self.assertEqual(fn.__code__.co_freevars, ())
        self.assertIn("execution_context: UnfollowExecutionContext", source)
        for forbidden in (
            "request_id=request_id",
            "business_session_id=business_session_id",
            "session_attempt=session_attempt",
            "worker_sha=worker_sha",
        ):
            self.assertNotIn(forbidden, source)


class UnfollowConsolidationAdmissionTests(unittest.TestCase):
    def test_like_or_mute_only_social_memory_never_enters_unfollow_backlog(self) -> None:
        row = _eligible_row(receipt=False)
        row.update({
            "posts_liked_count": 3,
            "muted_posts": True,
            "payload": {
                "already_interacted_like": {"durable": True},
                "already_interacted_mute": {"durable": True},
            },
        })
        metadata = {
            "canonical_follow_receipt_enforced": True,
            "candidate_scan_exhaustive": True,
            "source_rows_loaded": 1,
        }
        with (
            patch.object(
                unfollow_eligibility_engine.supabase_client,
                "fetch_unfollow_candidate_availability",
                return_value={},
            ),
            patch.object(
                unfollow_eligibility_engine.supabase_client,
                "fetch_unfollow_strict_candidate_rows",
                return_value=([row], metadata),
            ),
        ):
            plan = unfollow_eligibility_engine.plan_unfollow_targets(
                "account-1", settings=_settings(), limit=10,
                as_of=datetime(2026, 8, 22, tzinfo=timezone.utc),
            )
        self.assertEqual(plan["candidates"], [])
        self.assertEqual(plan["skipped_counts"]["missing_canonical_follow_receipt"], 1)

    def test_production_scan_rejects_legacy_row_without_canonical_follow_receipt(self) -> None:
        metadata = {
            "canonical_follow_receipt_enforced": True,
            "candidate_scan_exhaustive": True,
            "source_rows_loaded": 1,
        }
        with (
            patch.object(
                unfollow_eligibility_engine.supabase_client,
                "fetch_unfollow_candidate_availability",
                return_value={},
            ),
            patch.object(
                unfollow_eligibility_engine.supabase_client,
                "fetch_unfollow_strict_candidate_rows",
                return_value=([_eligible_row(receipt=False)], metadata),
            ),
        ):
            plan = unfollow_eligibility_engine.plan_unfollow_targets(
                "account-1",
                settings=_settings(),
                limit=10,
                as_of=datetime(2026, 8, 22, tzinfo=timezone.utc),
            )
        self.assertEqual(plan["candidates"], [])
        self.assertEqual(plan["skipped_counts"]["missing_canonical_follow_receipt"], 1)

    def test_production_scan_admits_exact_receipt_bound_candidate(self) -> None:
        metadata = {
            "canonical_follow_receipt_enforced": True,
            "canonical_follow_receipt_rows_loaded": 1,
            "candidate_scan_exhaustive": True,
            "source_rows_loaded": 1,
        }
        with (
            patch.object(
                unfollow_eligibility_engine.supabase_client,
                "fetch_unfollow_candidate_availability",
                return_value={},
            ),
            patch.object(
                unfollow_eligibility_engine.supabase_client,
                "fetch_unfollow_strict_candidate_rows",
                return_value=([_eligible_row(receipt=True)], metadata),
            ),
        ):
            plan = unfollow_eligibility_engine.plan_unfollow_targets(
                "account-1",
                settings=_settings(),
                limit=10,
                as_of=datetime(2026, 8, 22, tzinfo=timezone.utc),
            )
        self.assertEqual(len(plan["candidates"]), 1)
        self.assertEqual(plan["candidates"][0]["canonical_follow_receipt_id"], "receipt-1")
        self.assertEqual(
            plan["unfollow_backlog_follow_admission_source"],
            "ig_interaction_events:follow_verified|follow_verified_persisted_v1",
        )


class UnfollowConsolidationOutcomeTests(unittest.TestCase):
    def test_authoritative_execution_taxonomy(self) -> None:
        cases = (
            ("failed_unfollow", "unfollow_execution_context_invalid", 1, False, UnfollowExecutionOutcomeClass.RUNTIME_INTERNAL_ERROR),
            ("failed_unfollow", "runtime_internal_error:NameError:request_id", 1, False, UnfollowExecutionOutcomeClass.RUNTIME_INTERNAL_ERROR),
            ("failed_unfollow", "unfollow_persistence_failed", 1, False, UnfollowExecutionOutcomeClass.PERSISTENCE_AMBIGUOUS),
            ("ok", "temporary_search_miss", 1, True, UnfollowExecutionOutcomeClass.CANDIDATE_LOCAL_RETRYABLE),
            ("ok", "username_not_found_confirmed", 0, False, UnfollowExecutionOutcomeClass.CANDIDATE_LOCAL_TERMINAL),
            ("ok", "scheduled_safe_stop", 1, True, UnfollowExecutionOutcomeClass.SCHEDULED_SAFE_STOP),
            ("ok", "partial", 1, True, UnfollowExecutionOutcomeClass.PHASE_PARTIAL_RESUMABLE),
            ("ok", "eligible_targets_exhausted", 0, False, UnfollowExecutionOutcomeClass.COMPLETED),
        )
        for status, reason, remaining, resume, expected in cases:
            with self.subTest(reason=reason):
                self.assertEqual(
                    classify_unfollow_execution_outcome(
                        status=status,
                        stable_reason=reason,
                        remaining_count=remaining,
                        resume_recommended=resume,
                    ),
                    expected,
                )

    def test_runtime_internal_error_cannot_auto_restart_same_generation(self) -> None:
        plan = account_session_resume_engine.build_account_session_resume_plan(
            {
                "root_business_session_id": "root-session-1",
                "current_attempt_id": 1,
                "unfollow_execution_outcome_class": "RUNTIME_INTERNAL_ERROR",
                "unfollow_outcome": {
                    "phase_status": "partial_resumable",
                    "resume_recommended": True,
                    "remaining_count": 3,
                    "last_safe_checkpoint": "following_list",
                },
            },
            {"auto_restart_enabled": True},
        )
        self.assertFalse(plan["restart_allowed"])
        self.assertEqual(plan["restart_block_reason"], "runtime_internal_error_same_generation")

    def test_s1_s2_s3_keep_one_root_and_never_create_s4(self) -> None:
        root = "root-session-1"
        for current, expected_next in ((1, 2), (2, 3), (3, None)):
            plan = account_session_resume_engine.build_account_session_resume_plan(
                {
                    "root_business_session_id": root,
                    "current_attempt_id": current,
                    "session_termination_class": "partial_resumable",
                    "restart_eligibility": "eligible",
                    "follow_quota_target": 0,
                    "follows_completed_count": 0,
                    "follow_quota_remaining": 0,
                    "unfollow_outcome": {
                        "phase_status": "partial_resumable",
                        "resume_recommended": True,
                        "remaining_count": 4,
                        "last_safe_checkpoint": "following_list",
                    },
                },
                {"auto_restart_enabled": True},
            )
            self.assertEqual(plan["root_business_session_id"], root)
            self.assertEqual(plan["next_attempt_id"], expected_next)
            self.assertEqual(plan["total_attempts_allowed"], 3)


class UnfollowConsolidationRealCallGraphTests(unittest.TestCase):
    def test_real_multi_loop_success_reaches_physical_tap_and_atomic_persistence(self) -> None:
        events = ["follow_to_unfollow_handoff", "unfollow_plan"]
        summaries: list[dict[str, object]] = []
        candidate = {
            "id": "interaction-row-1",
            "interaction_row_id": "interaction-row-1",
            "username": "candidate_exact",
            "username_normalized": "candidate_exact",
        }
        visible_row = {
            "username": "candidate_exact",
            "username_normalized": "candidate_exact",
            "row_index": 0,
            "row_cta_class": "following",
            "cta_text": "Following",
        }
        next_candidate = {
            "id": "interaction-row-2",
            "interaction_row_id": "interaction-row-2",
            "username": "candidate_next",
            "username_normalized": "candidate_next",
        }
        next_visible_row = {
            "username": "candidate_next",
            "username_normalized": "candidate_next",
            "row_index": 1,
            "row_cta_class": "following",
            "cta_text": "Following",
        }
        context = UnfollowExecutionContext.build(
            account_id="account-1",
            account_username="owner",
            request_id="request-1",
            run_id="run-1",
            root_business_session_id="root-session-1",
            attempt_ordinal=1,
            business_date_sast="2026-08-22",
            worker_sha="a" * 40,
            runtime_root="/runtime/release",
            daily_plan_context={"plan_id": "plan-1", "generation": "generation-1"},
        )
        budget = derive_adaptive_coverage_budget(
            quota_remaining=2,
            eligible_remaining=2,
            session_remaining_seconds=3600,
        )

        def mark(name: str, result: object):
            def inner(*_args, **_kwargs):
                events.append(name)
                return result
            return inner

        original_record_verified_success = (
            orchestrator.UnfollowSessionCompletionPolicy.record_verified_success
        )

        def record_verified_success(policy, username: str) -> None:
            events.append("cursor_advancement")
            original_record_verified_success(policy, username)

        patches = (
            patch.object(orchestrator.account_protection_lists, "is_unfollow_protected", return_value=False),
            patch.object(orchestrator, "detect_own_following_list_screen", side_effect=mark("following_list_proof", {"is_following_list": True, "detected_reason": "exact"})),
            patch.object(orchestrator, "_detect_unfollow_unsafe_markers", return_value=[]),
            patch.object(orchestrator, "_evaluate_visible_unfollow_with_session_cache", return_value={"visible_eligible_matches": [candidate, next_candidate], "visible_ineligible_rows": [], "performance": {}}),
            patch.object(orchestrator, "tap_following_list_username_row_for_unfollow_probe", side_effect=mark("candidate_selected", (True, {"ok": True}))),
            patch.object(orchestrator, "verify_unfollow_target_profile_strict", side_effect=mark("exact_candidate_profile", {"ok": True})),
            patch.object(orchestrator, "open_unfollow_actions_sheet_from_profile_probe", side_effect=mark("action_sheet", {"ok": True, "unfollow_option_visible": True, "sheet_context_signals": {"exact_target": True}})),
            patch.object(orchestrator, "_prepare_unfollow_mutation_intent", side_effect=mark("durable_mutation_intent", {"action_id": "action-1", "run_id": "run-1"})),
            patch.object(orchestrator.follow_persistence_intent, "update_intent_stage", side_effect=lambda **kwargs: {"action_id": kwargs["action_id"], "run_id": kwargs["run_id"]}),
            patch.object(orchestrator, "tap_unfollow_in_following_sheet", side_effect=mark("physical_unfollow_tap", {"ok": True})),
            patch.object(orchestrator, "verify_unfollow_action_success_after_tap", side_effect=mark("physical_verification", {"ok": True})),
            patch.object(orchestrator, "_persist_unfollow_outcome_for_session", side_effect=mark("atomic_canonical_persistence", {"ok": True, "interaction_row_id": "interaction-row-1", "status": "persisted"})),
            patch.object(orchestrator, "_log_unfollow_success_observed", return_value={"username": "candidate_exact"}),
            patch.object(orchestrator, "_return_after_unfollow_profile", side_effect=mark("return_list", {"ok": True, "destination": "following_list", "search_session_reused": True})),
            patch.object(orchestrator.UnfollowSessionCompletionPolicy, "record_verified_success", new=record_verified_success),
            patch.object(orchestrator, "checkpoint_daily_plan", side_effect=mark("daily_plan_checkpoint", {"status": "complete", "remaining_count": 0})),
            patch.object(orchestrator, "_emit_summary", side_effect=lambda summary: summaries.append(summary)),
        )
        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            rc = orchestrator._run_real_unfollow_multi_loop(
                MagicMock(),
                execution_context=context,
                settings=_settings(),
                base_summary={
                    "source_rows_loaded": 1,
                    "eligible_total": 1,
                    "unplanned_eligible_count": 0,
                    "candidate_scan_exhaustive": True,
                    "candidate_funnel_reconciled": True,
                },
                planned_usernames={"candidate_exact", "candidate_next"},
                planned_by_username={
                    "candidate_exact": candidate,
                    "candidate_next": next_candidate,
                },
                rows=[visible_row, next_visible_row],
                harvest_meta={},
                harvest_fields={},
                visible_eligibility_row_cache={},
                real_action_max=2,
                unfollow_day_limit=200,
                effective_unfollows_done_at_start=0,
                business_action_deadline=None,
                adaptive_coverage_budget=budget,
                resume_checkpoint=None,
                daily_plan_context={"plan_id": "plan-1", "generation": "generation-1"},
                diagnostic_session=None,
                t0=time.perf_counter(),
            )

        self.assertEqual(rc, 0)
        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertEqual(summary["unfollow_actions_verified"], 2)
        self.assertEqual(summary["unfollow_results_persisted_count"], 2)
        self.assertIn("physical_unfollow_tap", events)
        self.assertIn("atomic_canonical_persistence", events)
        self.assertLess(events.index("durable_mutation_intent"), events.index("physical_unfollow_tap"))
        self.assertLess(events.index("physical_unfollow_tap"), events.index("physical_verification"))
        self.assertLess(events.index("physical_verification"), events.index("atomic_canonical_persistence"))
        self.assertLess(events.index("atomic_canonical_persistence"), events.index("return_list"))
        self.assertIn("cursor_advancement", events)
        selected_indexes = [
            index for index, event in enumerate(events) if event == "candidate_selected"
        ]
        self.assertEqual(len(selected_indexes), 2)
        self.assertGreater(selected_indexes[1], events.index("cursor_advancement"))
        telemetry = [item["boundary"] for item in summary["performance_boundary_telemetry"]]
        for boundary in (
            "candidate_selected",
            "candidate_cta_tap",
            "profile_proof",
            "cta_tap",
            "action_sheet_proof",
            "intent_prepared",
            "unfollow_tap",
            "verified",
            "persisted",
            "return_list",
            "next_candidate",
        ):
            self.assertIn(boundary, telemetry)

    def test_profile_open_mismatch_is_candidate_local_and_s1_continues(self) -> None:
        for failed_username in ("manu.alfrks", "gaeldeloozottone"):
            with self.subTest(failed_username=failed_username):
                summaries: list[dict[str, object]] = []
                events: list[str] = []
                failed_candidate = {
                    "id": f"interaction-{failed_username}",
                    "interaction_row_id": f"interaction-{failed_username}",
                    "username": failed_username,
                    "username_normalized": failed_username,
                }
                next_candidate = {
                    "id": "interaction-next",
                    "interaction_row_id": "interaction-next",
                    "username": "candidate_next",
                    "username_normalized": "candidate_next",
                }
                failed_visible_row = {
                    "username": failed_username,
                    "username_normalized": failed_username,
                    "row_index": 0,
                    "row_cta_class": "following",
                    "cta_text": "Following",
                }
                next_visible_row = {
                    "username": "candidate_next",
                    "username_normalized": "candidate_next",
                    "row_index": 1,
                    "row_cta_class": "following",
                    "cta_text": "Following",
                }
                rows = [failed_visible_row, next_visible_row]
                context = UnfollowExecutionContext.build(
                    account_id="account-1",
                    account_username="owner",
                    request_id="request-1",
                    run_id="run-1",
                    root_business_session_id="root-session-1",
                    attempt_ordinal=1,
                    business_date_sast="2026-08-25",
                    worker_sha="a" * 40,
                    runtime_root="/runtime/release",
                    daily_plan_context={
                        "plan_id": "plan-1",
                        "generation": "generation-1",
                    },
                )
                budget = derive_adaptive_coverage_budget(
                    quota_remaining=1,
                    eligible_remaining=2,
                    session_remaining_seconds=3600,
                )

                profile_results = iter(
                    (
                        {
                            "ok": False,
                            "failure_reason": "target_profile_username_mismatch",
                        },
                        {"ok": True, "method": "exact_profile"},
                    )
                )

                def verify_profile(*_args, **_kwargs):
                    out = next(profile_results)
                    events.append(
                        "profile_mismatch" if not out["ok"] else "profile_exact"
                    )
                    return out

                def tap_unfollow(*_args, **_kwargs):
                    events.append("physical_unfollow_tap")
                    return {"ok": True}

                patches = (
                    patch.object(
                        orchestrator.account_protection_lists,
                        "is_unfollow_protected",
                        return_value=False,
                    ),
                    patch.object(
                        orchestrator,
                        "detect_own_following_list_screen",
                        return_value={
                            "is_following_list": True,
                            "detected_reason": "exact",
                        },
                    ),
                    patch.object(
                        orchestrator,
                        "_detect_unfollow_unsafe_markers",
                        return_value=[],
                    ),
                    patch.object(
                        orchestrator,
                        "_evaluate_visible_unfollow_with_session_cache",
                        return_value={
                            "visible_eligible_matches": [
                                failed_candidate,
                                next_candidate,
                            ],
                            "visible_ineligible_rows": [],
                            "performance": {},
                        },
                    ),
                    patch.object(
                        orchestrator,
                        "tap_following_list_username_row_for_unfollow_probe",
                        return_value=(True, {"ok": True}),
                    ),
                    patch.object(
                        orchestrator,
                        "verify_unfollow_target_profile_strict",
                        side_effect=verify_profile,
                    ),
                    patch.object(
                        orchestrator,
                        "_return_after_unfollow_profile",
                        return_value={
                            "ok": True,
                            "destination": "following",
                            "search_session_reused": False,
                        },
                    ),
                    patch.object(
                        orchestrator,
                        "harvest_visible_following_rows_for_unfollow",
                        return_value=(rows, {}),
                    ),
                    patch.object(
                        orchestrator,
                        "open_unfollow_actions_sheet_from_profile_probe",
                        return_value={
                            "ok": True,
                            "unfollow_option_visible": True,
                            "sheet_context_signals": {"exact_target": True},
                        },
                    ),
                    patch.object(
                        orchestrator,
                        "_prepare_unfollow_mutation_intent",
                        return_value={"action_id": "action-next", "run_id": "run-1"},
                    ),
                    patch.object(
                        orchestrator.follow_persistence_intent,
                        "update_intent_stage",
                        return_value={"action_id": "action-next", "run_id": "run-1"},
                    ),
                    patch.object(
                        orchestrator,
                        "tap_unfollow_in_following_sheet",
                        side_effect=tap_unfollow,
                    ),
                    patch.object(
                        orchestrator,
                        "verify_unfollow_action_success_after_tap",
                        return_value={"ok": True},
                    ),
                    patch.object(
                        orchestrator,
                        "_persist_unfollow_outcome_for_session",
                        return_value={
                            "ok": True,
                            "interaction_row_id": "interaction-next",
                            "status": "persisted",
                        },
                    ),
                    patch.object(
                        orchestrator,
                        "_log_unfollow_success_observed",
                        return_value={"username": "candidate_next"},
                    ),
                    patch.object(
                        orchestrator,
                        "checkpoint_daily_plan",
                        return_value={"status": "partial", "remaining_count": 1},
                    ),
                    patch.object(
                        orchestrator,
                        "_emit_summary",
                        side_effect=lambda summary: summaries.append(summary),
                    ),
                )
                with ExitStack() as stack:
                    for patcher in patches:
                        stack.enter_context(patcher)
                    rc = orchestrator._run_real_unfollow_multi_loop(
                        MagicMock(),
                        execution_context=context,
                        settings=_settings(),
                        base_summary={
                            "source_rows_loaded": 2,
                            "eligible_total": 2,
                            "unplanned_eligible_count": 0,
                            "candidate_scan_exhaustive": True,
                            "candidate_funnel_reconciled": True,
                        },
                        planned_usernames={failed_username, "candidate_next"},
                        planned_by_username={
                            failed_username: failed_candidate,
                            "candidate_next": next_candidate,
                        },
                        rows=rows,
                        harvest_meta={},
                        harvest_fields={},
                        visible_eligibility_row_cache={},
                        real_action_max=1,
                        unfollow_day_limit=200,
                        effective_unfollows_done_at_start=0,
                        business_action_deadline=None,
                        adaptive_coverage_budget=budget,
                        resume_checkpoint=None,
                        daily_plan_context={
                            "plan_id": "plan-1",
                            "generation": "generation-1",
                        },
                        diagnostic_session=None,
                        t0=time.perf_counter(),
                    )

                self.assertEqual(rc, 0)
                self.assertEqual(len(summaries), 1)
                summary = summaries[0]
                self.assertEqual(summary["unfollow_actions_sent"], 1)
                self.assertEqual(summary["unfollow_actions_verified"], 1)
                self.assertEqual(summary["unfollow_results_persisted_count"], 1)
                self.assertEqual(summary["recoverable_action_failures_count"], 1)
                self.assertEqual(
                    summary["recoverable_action_failure_reasons"][failed_username],
                    "target_profile_username_mismatch",
                )
                self.assertTrue(summary["session_continued_after_recoverable_failure"])
                self.assertEqual(events.count("physical_unfollow_tap"), 1)
                self.assertLess(
                    events.index("profile_mismatch"),
                    events.index("physical_unfollow_tap"),
                )

    def test_profile_open_failure_stops_when_following_recovery_is_not_proved(self) -> None:
        source = inspect.getsource(orchestrator._run_real_unfollow_multi_loop)
        profile_failure_block = source.split(
            'if not profile_det.get("ok"):', 1
        )[1].split("action_sheet_t0", 1)[0]
        self.assertIn(
            'failure_scope=("candidate_local" if return_ok else "global")',
            profile_failure_block,
        )
        self.assertIn(
            "if not candidate_decision.continue_session:",
            profile_failure_block,
        )
        self.assertIn(
            'return emit_final("failed_unfollow_multi_action", stop_reason)',
            profile_failure_block,
        )
        self.assertIn("no_unfollow_tap=True", profile_failure_block)
        self.assertIn("no_success_persistence=True", profile_failure_block)


if __name__ == "__main__":
    unittest.main()
