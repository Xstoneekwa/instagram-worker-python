from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import account_session_orchestrator as account_session
import outreach_session_orchestrator


class AccountSessionUnfollowSkipTest(unittest.TestCase):
    def test_exception_summary_preserves_s3_canonical_actions_and_lineage(self) -> None:
        receipts = [
            {"action_id": f"action-{index}", "username": f"user-{index}"}
            for index in range(16)
        ]
        summary = account_session._exception_summary_preserving_unfollow_progress(
            progress={
                "run_id": "run-s3",
                "root_business_session_id": "root-s1",
                "attempt_ordinal": 3,
                "previous_run_id": "run-s2",
                "unfollow_actions_sent": 16,
                "unfollow_actions_verified": 16,
                "unfollow_results_persisted_count": 16,
                "attempted": 16,
                "verified": 16,
                "persisted": 16,
                "unfollow_observed_success_count": 16,
                "unfollow_observed_successes": receipts,
                "counter_delta": 16,
                "last_successful_action": receipts[-1],
                "plan_cursor_progress": {
                    "plan_id": "plan-root-s1",
                    "completed_usernames": [item["username"] for item in receipts],
                    "remaining_usernames": ["remaining-user"],
                },
                "first_causal_reason": "candidate_local_technical_hold",
            },
            error=ConnectionResetError("supabase_queue_read_failed"),
            real_max_actions_requested=120,
            real_max_actions_effective=120,
            real_hard_max=120,
        )
        self.assertEqual(summary["unfollow_actions_verified"], 16)
        self.assertEqual(summary["unfollow_results_persisted_count"], 16)
        self.assertEqual(summary["persisted"], 16)
        self.assertEqual(summary["root_business_session_id"], "root-s1")
        self.assertEqual(summary["attempt_ordinal"], 3)
        self.assertEqual(summary["previous_run_id"], "run-s2")
        self.assertEqual(summary["counter_delta"], 16)
        self.assertEqual(summary["last_successful_action"]["action_id"], "action-15")
        self.assertEqual(summary["plan_cursor_progress"]["remaining_usernames"], ["remaining-user"])
        self.assertEqual(summary["canonical_reason_before_exception"], "candidate_local_technical_hold")
        self.assertEqual(summary["termination_reason"], "supabase_queue_read_failed")
        self.assertEqual(len(summary["unfollow_observed_successes"]), 16)
        self.assertEqual(summary["final_exception"]["type"], "ConnectionResetError")
        self.assertIn("supabase_queue_read_failed", summary["final_exception"]["reason"])

    def test_zero_action_exception_stays_zero_without_fabrication(self) -> None:
        summary = account_session._exception_summary_preserving_unfollow_progress(
            progress={"run_id": "run-s1"},
            error=RuntimeError("before_first_action"),
            real_max_actions_requested=80,
            real_max_actions_effective=80,
            real_hard_max=80,
        )
        self.assertEqual(summary["unfollow_actions_verified"], 0)
        self.assertEqual(summary["unfollow_results_persisted_count"], 0)
        self.assertEqual(summary["final_exception"]["reason"], "before_first_action")

    def test_partial_candidate_local_progress_is_preserved_before_exception(self) -> None:
        receipts = [
            {"action_id": f"partial-action-{index}", "username": f"partial-{index}"}
            for index in range(5)
        ]
        summary = account_session._exception_summary_preserving_unfollow_progress(
            progress={
                "run_id": "run-s2",
                "root_business_session_id": "root-s1",
                "attempt_ordinal": 2,
                "previous_run_id": "run-s1",
                "unfollow_actions_sent": 6,
                "unfollow_actions_verified": 5,
                "unfollow_results_persisted_count": 5,
                "attempted": 6,
                "verified": 5,
                "persisted": 5,
                "unfollow_observed_success_count": 5,
                "unfollow_observed_successes": receipts,
                "counter_delta": 5,
                "last_successful_action": receipts[-1],
                "plan_cursor_progress": {
                    "plan_id": "plan-root-s1",
                    "completed_usernames": [item["username"] for item in receipts],
                    "remaining_usernames": ["retryable-target"],
                },
                "candidate_local_retryable_count": 1,
                "failed_candidates": [
                    {
                        "username": "retryable-target",
                        "reason": "target_profile_open_failed",
                        "outcome": "technical_hold",
                    }
                ],
            },
            error=RuntimeError("supabase_queue_read_failed"),
            real_max_actions_requested=80,
            real_max_actions_effective=80,
            real_hard_max=80,
        )

        self.assertEqual(summary["unfollow_actions_sent"], 6)
        self.assertEqual(summary["unfollow_actions_verified"], 5)
        self.assertEqual(summary["unfollow_results_persisted_count"], 5)
        self.assertEqual(summary["unfollow_observed_successes"], receipts)
        self.assertEqual(summary["candidate_local_retryable_count"], 1)
        self.assertEqual(summary["failed_candidates"][0]["outcome"], "technical_hold")
        self.assertEqual(summary["root_business_session_id"], "root-s1")
        self.assertEqual(summary["attempt_ordinal"], 2)
        self.assertEqual(summary["previous_run_id"], "run-s1")
        self.assertEqual(summary["counter_delta"], 5)
        self.assertEqual(summary["last_successful_action"]["action_id"], "partial-action-4")
        self.assertEqual(summary["final_exception"]["reason"], "supabase_queue_read_failed")

    def test_authoritative_zero_follow_quota_hands_off_to_unfollow_only(self) -> None:
        out = account_session._resolve_auto_restart_follow_phase_gate(
            default_run_follow=True,
            policy={
                "phases_to_run": {
                    "welcome": False,
                    "follow": True,
                    "unfollow": True,
                },
                "quota_remaining": {"follow": 0, "unfollow": 2},
            },
        )
        self.assertFalse(out["run_follow"])
        self.assertTrue(out["follow_quota_authoritative_zero"])
        self.assertEqual(
            out["follow_phase_skipped_reason"],
            "global_follow_cap_reached",
        )
        self.assertTrue(out["unfollow_only_resume"])

    def test_positive_follow_quota_keeps_follow_phase_enabled(self) -> None:
        out = account_session._resolve_auto_restart_follow_phase_gate(
            default_run_follow=True,
            policy={
                "phases_to_run": {
                    "welcome": False,
                    "follow": True,
                    "unfollow": True,
                },
                "quota_remaining": {"follow": 1, "unfollow": 2},
            },
        )
        self.assertTrue(out["run_follow"])
        self.assertFalse(out["unfollow_only_resume"])

    def test_disabled_follow_phase_hands_off_without_rewriting_plan_flags(self) -> None:
        phases = {"welcome": False, "follow": False, "unfollow": True}
        policy = {
            "phases_to_run": dict(phases),
            "quota_remaining": {"follow": 9, "unfollow": 2},
        }
        out = account_session._resolve_auto_restart_follow_phase_gate(
            default_run_follow=True,
            policy=policy,
        )
        self.assertFalse(out["run_follow"])
        self.assertTrue(out["unfollow_only_resume"])
        self.assertEqual(policy["phases_to_run"], phases)

    def test_unfollow_only_resume_diagnostic_does_not_require_local_follow(self) -> None:
        settings = SimpleNamespace(
            enabled=True,
            mode="unfollow",
            sort_mode="oldest",
            session_limit=35,
        )
        with (
            patch.object(account_session, "load_unfollow_settings", return_value=settings),
            patch.object(
                account_session,
                "plan_unfollow_targets",
                return_value={"candidates_count": 1, "plan_reason": "planned", "skipped_counts": {}},
            ),
        ):
            diagnostic = account_session._run_follow_to_unfollow_handoff_diagnostic(
                account_id="00000000-0000-4000-8000-000000000001",
                account_username="j_automatise_pour_toi",
                run_id="run-id",
                followers_source_username="source",
                follow_phase_executed=False,
                follow_exit_code=None,
                follow_total_ms=0.0,
                session_started_at=0.0,
                unfollow_only_resume_authorized=True,
            )

        self.assertTrue(diagnostic["handoff_would_run"])
        self.assertTrue(diagnostic["unfollow_only_resume_authorized"])
        self.assertNotIn("follow_phase_not_executed", diagnostic["handoff_skip_reasons"])

    def test_unfollow_only_resume_has_explicit_follow_gate_authorization(self) -> None:
        gate = account_session._evaluate_h3_follow_exit_code_gate(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="j_automatise_pour_toi",
            follow_exit_code=None,
            diagnostic={"unfollow_only_resume_authorized": True},
            real_max_actions_effective=27,
        )

        self.assertTrue(gate["follow_exit_code_allowed"])
        self.assertFalse(gate["follow_phase_executed"])
        self.assertEqual(
            gate["follow_exit_code_allow_reason"],
            "auto_restart_unfollow_only_resume",
        )

    def test_unfollow_only_resume_can_complete_account_attempt_without_follow(self) -> None:
        status = account_session._account_session_status(
            transition_reason="welcome_disabled",
            follow_phase_executed=False,
            follow_exit_code=None,
            welcome_blocked_follow=False,
            unfollow_only_resume=True,
        )

        self.assertEqual(status, "success")

        termination_class = account_session._session_termination_class(
            session_status=status,
            follow_phase_executed=False,
            follow_exit_code=None,
            follow_quota_remaining=None,
            follow_to_unfollow_diagnostic={"unfollow_only_resume_authorized": True},
            follow_to_unfollow_real={
                "executed": True,
                "status": "success_real_unfollow_multi",
                "unfollow_outcome": {"phase_status": "completed"},
            },
            follow_phase_skipped_reason="auto_restart_resume_skip_follow",
            transition_reason="welcome_disabled",
        )
        self.assertEqual(termination_class, "completed")

    def test_h3_prod_normal_runtime_cap_uses_domain_and_day_remaining(self) -> None:
        original_loader = account_session.load_unfollow_settings
        original_counter = account_session.supabase_client.count_successful_unfollows_today
        original_requested = getattr(account_session.config, "ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS", 1)
        original_hard = getattr(account_session.config, "ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX", 3)
        original_global = getattr(account_session.config, "UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN", 1)
        account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS = 1
        account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX = 1
        account_session.config.UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN = 1
        account_session.load_unfollow_settings = lambda *_args, **_kwargs: SimpleNamespace(
            enabled=True,
            mode="unfollow-any",
            session_limit=50,
            day_limit=200,
            runtime_cap_mode="prod_normal",
            runtime_safety_cap=None,
        )
        account_session.supabase_client.count_successful_unfollows_today = lambda *_args, **_kwargs: 5
        try:
            out = account_session._resolve_follow_to_unfollow_runtime_cap("account-id")
        finally:
            account_session.load_unfollow_settings = original_loader
            account_session.supabase_client.count_successful_unfollows_today = original_counter
            account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS = original_requested
            account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX = original_hard
            account_session.config.UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN = original_global

        self.assertEqual(out["runtime_cap"], 50)
        self.assertEqual(out["runtime_cap_mode"], "prod_normal")
        self.assertEqual(out["h3_requested_cap"], 1)
        self.assertEqual(out["h3_hard_cap"], 1)
        self.assertEqual(out["global_unfollow_env_cap"], 1)
        self.assertEqual(out["unfollow_day_remaining_today"], 195)

    def test_h3_prod_normal_runtime_cap_is_not_reduced_by_global_env_cap(self) -> None:
        original_loader = account_session.load_unfollow_settings
        original_counter = account_session.supabase_client.count_successful_unfollows_today
        original_requested = getattr(account_session.config, "ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS", 1)
        original_hard = getattr(account_session.config, "ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX", 3)
        original_global = getattr(account_session.config, "UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN", 1)
        account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS = 3
        account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX = 3
        account_session.config.UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN = 1
        account_session.load_unfollow_settings = lambda *_args, **_kwargs: SimpleNamespace(
            enabled=True,
            mode="unfollow-any",
            session_limit=50,
            day_limit=200,
            runtime_cap_mode="prod_normal",
            runtime_safety_cap=None,
        )
        account_session.supabase_client.count_successful_unfollows_today = lambda *_args, **_kwargs: 0
        try:
            out = account_session._resolve_follow_to_unfollow_runtime_cap("account-id")
        finally:
            account_session.load_unfollow_settings = original_loader
            account_session.supabase_client.count_successful_unfollows_today = original_counter
            account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS = original_requested
            account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX = original_hard
            account_session.config.UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN = original_global

        self.assertEqual(out["runtime_cap"], 50)
        self.assertEqual(out["h3_env_cap"], 3)
        self.assertEqual(out["global_unfollow_env_cap"], 1)

    def test_h3_runtime_cap_respects_day_remaining(self) -> None:
        original_loader = account_session.load_unfollow_settings
        original_counter = account_session.supabase_client.count_successful_unfollows_today
        original_requested = getattr(account_session.config, "ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS", 1)
        original_hard = getattr(account_session.config, "ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX", 3)
        original_global = getattr(account_session.config, "UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN", 1)
        account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS = 2
        account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX = 2
        account_session.config.UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN = 2
        account_session.load_unfollow_settings = lambda *_args, **_kwargs: SimpleNamespace(
            enabled=True,
            mode="unfollow",
            session_limit=50,
            day_limit=5,
            runtime_cap_mode="prod_normal",
            runtime_safety_cap=None,
        )
        account_session.supabase_client.count_successful_unfollows_today = lambda *_args, **_kwargs: 4
        try:
            out = account_session._resolve_follow_to_unfollow_runtime_cap("account-id")
        finally:
            account_session.load_unfollow_settings = original_loader
            account_session.supabase_client.count_successful_unfollows_today = original_counter
            account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS = original_requested
            account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX = original_hard
            account_session.config.UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN = original_global

        self.assertEqual(out["runtime_cap"], 1)
        self.assertEqual(out["unfollow_day_remaining_today"], 1)

    def test_prod_normal_enables_handoff_from_domain_settings(self) -> None:
        original_loader = account_session.load_unfollow_settings
        original_flag = getattr(account_session.config, "ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED", False)
        account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED = False
        account_session.load_unfollow_settings = lambda *_args, **_kwargs: SimpleNamespace(
            enabled=True,
            mode="unfollow-any",
            session_limit=120,
            day_limit=120,
            runtime_cap_mode="prod_normal",
        )
        try:
            self.assertTrue(account_session._follow_to_unfollow_real_enabled("account-id"))
        finally:
            account_session.load_unfollow_settings = original_loader
            account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED = original_flag

    def test_h3_real_allows_unfollow_any_without_db_pending_candidate(self) -> None:
        reason = account_session._follow_to_unfollow_real_skip_reason(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="cinema_catchup",
            follow_exit_code=0,
            diagnostic={
                "unfollow_enabled": True,
                "unfollow_mode": "unfollow-any",
                "has_pending_unfollow": False,
                "handoff_would_run": True,
            },
            real_max_actions_effective=1,
            follow_exit_gate={"follow_exit_code_allowed": True},
        )

        self.assertEqual(reason, "")

    def test_h3_real_rejects_unfollow_any_when_cap_exhausted(self) -> None:
        reason = account_session._follow_to_unfollow_real_skip_reason(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="cinema_catchup",
            follow_exit_code=0,
            diagnostic={
                "unfollow_enabled": True,
                "unfollow_mode": "unfollow-any",
                "has_pending_unfollow": True,
            },
            real_max_actions_effective=0,
            follow_exit_gate={"follow_exit_code_allowed": True},
        )

        self.assertEqual(reason, "unfollow_any_cap_exhausted")

    def test_h3_real_skip_reason_for_no_candidate_is_stable(self) -> None:
        reason = account_session._follow_to_unfollow_real_skip_reason(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="cinema_catchup",
            follow_exit_code=0,
            diagnostic={
                "unfollow_enabled": True,
                "unfollow_mode": "unfollow",
                "has_pending_unfollow": False,
            },
            real_max_actions_effective=1,
            follow_exit_gate={"follow_exit_code_allowed": True},
        )

        self.assertEqual(reason, "unfollow_skipped_no_safe_candidate")

    def test_h3_real_summary_maps_unfollow_any_no_candidate_reason(self) -> None:
        summary = account_session._real_summary_from_unfollow_summary(
            enabled=True,
            executed=True,
            exit_code=0,
            unfollow_summary={
                "unfollow_mode": "unfollow-any",
                "status": "no_visible_eligible_unfollow_target",
                "unfollow_actions_sent": 0,
                "unfollow_actions_verified": 0,
            },
            real_max_actions_requested=1,
            real_max_actions_effective=1,
            real_hard_max=1,
            follow_exit_gate={"follow_exit_code_allowed": True},
        )

        self.assertEqual(summary["skip_reason"], "unfollow_any_no_safe_candidate")
        self.assertEqual(summary["failure_reason"], "unfollow_any_no_safe_candidate")

    def test_real_summary_preserves_partial_ui_coverage_contract(self) -> None:
        summary = account_session._real_summary_from_unfollow_summary(
            enabled=True,
            executed=True,
            exit_code=0,
            unfollow_summary={
                "status": "success_real_unfollow_multi_partial_exhausted",
                "candidates_planned_count": 51,
                "unfollow_actions_sent": 11,
                "unfollow_actions_verified": 11,
                "unfollow_results_persisted_count": 11,
                "eligible_db_remaining": 40,
                "ui_coverage_status": "partial",
                "multi_action_stop_reason": "ui_coverage_budget_exhausted",
                "resume_recommended": False,
            },
            real_max_actions_requested=120,
            real_max_actions_effective=120,
            real_hard_max=120,
        )

        self.assertEqual(summary["last_run_eligible_at_start"], 51)
        self.assertEqual(summary["last_run_attempted"], 11)
        self.assertEqual(summary["last_run_verified"], 11)
        self.assertEqual(summary["last_run_remaining_eligible"], 40)
        self.assertEqual(summary["last_run_coverage_status"], "partial")
        self.assertEqual(summary["last_run_stop_reason"], "ui_coverage_budget_exhausted")
        self.assertFalse(summary["unfollow_resume_recommended"])

    def test_outreach_addon_disabled_does_not_touch_outreach(self) -> None:
        original_flag = getattr(account_session.config, "ACCOUNT_SESSION_OUTREACH_ADDON_ENABLED", False)
        account_session.config.ACCOUNT_SESSION_OUTREACH_ADDON_ENABLED = False
        try:
            out = account_session._run_account_session_outreach_addon(
                SimpleNamespace(),
                account_id="account-id",
                account_username="i_m_your_traker",
                run_id="parent-run-id",
            )
        finally:
            account_session.config.ACCOUNT_SESSION_OUTREACH_ADDON_ENABLED = original_flag

        self.assertFalse(out["enabled"])
        self.assertFalse(out["executed"])
        self.assertEqual(out["status"], "disabled")
        self.assertEqual(out["skip_reason"], "addon_disabled")

    def test_outreach_addon_enabled_dispatches_prepared_external_job_with_parent_run_id(self) -> None:
        original_flag = getattr(account_session.config, "ACCOUNT_SESSION_OUTREACH_ADDON_ENABLED", False)
        original_max = getattr(account_session.config, "ACCOUNT_SESSION_OUTREACH_ADDON_MAX_JOBS", 1)
        original_prepare = outreach_session_orchestrator.prepare_outreach_session
        original_dispatch = outreach_session_orchestrator.dispatch_outreach_session
        original_last_summary = outreach_session_orchestrator.get_last_outreach_session_summary
        calls: dict[str, object] = {}

        def fake_prepare(_d, **kwargs):
            calls["prepare_kwargs"] = kwargs
            return {
                "session_status": "prepared",
                "exit_code": 0,
                "prepared_jobs_count": 1,
                "prepared_job_ids": ["job-id-1"],
                "prepared_jobs": [{"id": "job-id-1", "recipient_username": "prosjektoslo.no"}],
                "max_jobs_effective": 1,
            }

        def fake_dispatch(_d, **kwargs):
            calls["dispatch_kwargs"] = kwargs
            return 0

        account_session.config.ACCOUNT_SESSION_OUTREACH_ADDON_ENABLED = True
        account_session.config.ACCOUNT_SESSION_OUTREACH_ADDON_MAX_JOBS = 1
        outreach_session_orchestrator.prepare_outreach_session = fake_prepare
        outreach_session_orchestrator.dispatch_outreach_session = fake_dispatch
        outreach_session_orchestrator.get_last_outreach_session_summary = lambda: {
            "session_status": "completed_clean",
            "jobs_claimed": 1,
            "jobs_completed": 1,
            "jobs_failed": 0,
            "jobs_skipped": 0,
        }
        try:
            out = account_session._run_account_session_outreach_addon(
                SimpleNamespace(),
                account_id="account-id",
                account_username="i_m_your_traker",
                run_id="parent-run-id",
            )
        finally:
            account_session.config.ACCOUNT_SESSION_OUTREACH_ADDON_ENABLED = original_flag
            account_session.config.ACCOUNT_SESSION_OUTREACH_ADDON_MAX_JOBS = original_max
            outreach_session_orchestrator.prepare_outreach_session = original_prepare
            outreach_session_orchestrator.dispatch_outreach_session = original_dispatch
            outreach_session_orchestrator.get_last_outreach_session_summary = original_last_summary

        prepare_kwargs = calls["prepare_kwargs"]
        dispatch_kwargs = calls["dispatch_kwargs"]
        self.assertEqual(prepare_kwargs["run_id"], "parent-run-id")
        self.assertEqual(prepare_kwargs["max_jobs_override"], 1)
        self.assertTrue(prepare_kwargs["reject_unfollow_handoff_jobs"])
        self.assertEqual(dispatch_kwargs["run_id"], "parent-run-id")
        self.assertEqual(dispatch_kwargs["prepared_outreach"]["prepared_jobs_count"], 1)
        self.assertTrue(out["executed"])
        self.assertEqual(out["jobs_claimed"], 1)
        self.assertEqual(out["jobs_completed"], 1)

    def test_outreach_addon_no_pending_job_skips_without_dispatch(self) -> None:
        original_flag = getattr(account_session.config, "ACCOUNT_SESSION_OUTREACH_ADDON_ENABLED", False)
        original_prepare = outreach_session_orchestrator.prepare_outreach_session
        original_dispatch = outreach_session_orchestrator.dispatch_outreach_session
        dispatch_called = False

        def fake_prepare(_d, **_kwargs):
            return {
                "session_status": "no_jobs",
                "exit_code": 0,
                "prepared_jobs_count": 0,
                "prepared_jobs": [],
                "max_jobs_effective": 1,
            }

        def fake_dispatch(_d, **_kwargs):
            nonlocal dispatch_called
            dispatch_called = True
            return 0

        account_session.config.ACCOUNT_SESSION_OUTREACH_ADDON_ENABLED = True
        outreach_session_orchestrator.prepare_outreach_session = fake_prepare
        outreach_session_orchestrator.dispatch_outreach_session = fake_dispatch
        try:
            out = account_session._run_account_session_outreach_addon(
                SimpleNamespace(),
                account_id="account-id",
                account_username="i_m_your_traker",
                run_id="parent-run-id",
            )
        finally:
            account_session.config.ACCOUNT_SESSION_OUTREACH_ADDON_ENABLED = original_flag
            outreach_session_orchestrator.prepare_outreach_session = original_prepare
            outreach_session_orchestrator.dispatch_outreach_session = original_dispatch

        self.assertFalse(dispatch_called)
        self.assertFalse(out["executed"])
        self.assertEqual(out["status"], "skipped")
        self.assertEqual(out["skip_reason"], "no_pending_outreach_job")


if __name__ == "__main__":
    unittest.main()
