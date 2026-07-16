from __future__ import annotations

import unittest
from types import SimpleNamespace

import account_session_orchestrator as account_session
import outreach_session_orchestrator


class AccountSessionUnfollowSkipTest(unittest.TestCase):
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
