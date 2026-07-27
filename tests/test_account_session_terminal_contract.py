from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import account_session_orchestrator as orchestrator
from runtime_incident_matrix import classify_terminal_run_failure


def _successful_unfollow(**overrides):
    summary = {
        "enabled": True,
        "executed": True,
        "status": "success_real_unfollow_multi_partial_end_of_list",
        "exit_code": 0,
        "unfollow_actions_failed": 0,
        "unfollow_actions_verified": 6,
        "unfollow_results_persisted_count": 6,
    }
    summary.update(overrides)
    return summary


class AccountSessionTerminalContractTest(unittest.TestCase):
    def test_persisted_summary_exports_backend_auto_restart_contract(self) -> None:
        projection = orchestrator._auto_restart_performance_projection(
            {
                "restart_allowed": True,
                "restart_block_reason": "",
                "reason": "quota_remaining",
                "phases_to_run": {"welcome": False, "follow": False, "unfollow": True},
                "quota_remaining": {"follow": 0, "unfollow": 3, "total": 3},
            }
        )

        self.assertTrue(projection["auto_restart_restart_allowed"])
        self.assertEqual(
            projection["auto_restart_phases_to_run"],
            {"welcome": False, "follow": False, "unfollow": True},
        )
        self.assertEqual(
            projection["auto_restart_quota_remaining"],
            {"follow": 0, "unfollow": 3, "total": 3},
        )
        self.assertEqual(projection["auto_restart_reason"], "quota_remaining")

    def test_outreach_time_budget_allows_open_deadline(self) -> None:
        now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
        out = orchestrator._outreach_time_budget(
            (now + timedelta(minutes=20)).isoformat(),
            now=now,
        )
        self.assertTrue(out["allowed"])
        self.assertEqual(out["remaining_seconds"], 1200.0)

    def test_outreach_time_budget_skips_when_cleanup_window_is_too_close(self) -> None:
        now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
        out = orchestrator._outreach_time_budget(
            (now + timedelta(seconds=299)).isoformat(),
            now=now,
        )
        self.assertFalse(out["allowed"])
        self.assertEqual(out["deadline_source"], "scheduler_business_action_deadline")

    def test_no_outreach_deadline_does_not_add_a_new_block(self) -> None:
        out = orchestrator._outreach_time_budget(None)
        self.assertTrue(out["allowed"])
        self.assertEqual(out["deadline_source"], "fallback_unavailable_at_outreach_boundary")

    def test_run_e44ef22a_phase_contract_is_terminal(self) -> None:
        statuses = orchestrator._phase_statuses(
            welcome_enabled=False,
            welcome_phase_executed=False,
            welcome_session_status="skipped",
            follow_phase_executed=True,
            follow_phase_skipped_reason=None,
            follow_exit_code=0,
            follow_to_unfollow_real=_successful_unfollow(),
            account_session_outreach_addon={
                "enabled": False,
                "executed": False,
                "status": "disabled",
                "skip_reason": "addon_disabled",
            },
        )

        self.assertEqual(statuses, ("not_planned", "completed", "completed", "not_planned"))
        contract = orchestrator._phase_terminal_contract(
            welcome=statuses[0], follow=statuses[1], unfollow=statuses[2], outreach=statuses[3]
        )
        self.assertTrue(contract["ok"])
        self.assertEqual(contract["non_terminal_phases"], {})

    def test_unknown_or_interrupted_phase_never_satisfies_contract(self) -> None:
        contract = orchestrator._phase_terminal_contract(
            welcome="completed", follow="completed", unfollow="unknown", outreach="not_planned"
        )

        self.assertFalse(contract["ok"])
        self.assertEqual(contract["reason"], "account_session_phase_not_terminal")
        self.assertEqual(contract["non_terminal_phases"], {"unfollow": "unknown"})

    def test_partial_resumable_is_terminal_for_current_attempt(self) -> None:
        contract = orchestrator._phase_terminal_contract(
            welcome="not_planned",
            follow="completed",
            unfollow="partial_resumable",
            outreach="not_planned",
        )

        self.assertTrue(contract["ok"])
        self.assertEqual(contract["non_terminal_phases"], {})
        self.assertEqual(contract["reason"], "all_planned_phases_terminal")

    def test_unfollow_end_without_candidate_is_clean_terminal(self) -> None:
        statuses = orchestrator._phase_statuses(
            welcome_enabled=False,
            welcome_phase_executed=False,
            welcome_session_status="skipped",
            follow_phase_executed=True,
            follow_phase_skipped_reason=None,
            follow_exit_code=0,
            follow_to_unfollow_real=_successful_unfollow(
                status="no_visible_eligible_unfollow_target",
                unfollow_actions_verified=0,
                unfollow_results_persisted_count=0,
            ),
            account_session_outreach_addon={
                "enabled": True,
                "executed": False,
                "status": "skipped",
                "skip_reason": "no_pending_outreach_job",
            },
        )

        self.assertEqual(statuses[2], "skipped_cleanly")
        self.assertEqual(statuses[3], "skipped_cleanly")

    def test_unfollow_verified_without_matching_persistence_is_not_completed(self) -> None:
        statuses = orchestrator._phase_statuses(
            welcome_enabled=False,
            welcome_phase_executed=False,
            welcome_session_status="skipped",
            follow_phase_executed=True,
            follow_phase_skipped_reason=None,
            follow_exit_code=0,
            follow_to_unfollow_real=_successful_unfollow(unfollow_results_persisted_count=5),
            account_session_outreach_addon={"enabled": False, "executed": False, "status": "disabled"},
        )

        self.assertEqual(statuses[2], "success_real_unfollow_multi_partial_end_of_list")
        self.assertFalse(orchestrator._phase_terminal_contract(
            welcome=statuses[0], follow=statuses[1], unfollow=statuses[2], outreach=statuses[3]
        )["ok"])

    def test_exhausted_unfollow_with_recoverable_candidate_failure_is_terminal(self) -> None:
        statuses = orchestrator._phase_statuses(
            welcome_enabled=False,
            welcome_phase_executed=False,
            welcome_session_status="skipped",
            follow_phase_executed=True,
            follow_phase_skipped_reason=None,
            follow_exit_code=0,
            follow_to_unfollow_real=_successful_unfollow(
                status="success_real_unfollow_multi_partial_exhausted",
                unfollow_actions_failed=1,
                unfollow_actions_verified=68,
                unfollow_results_persisted_count=68,
            ),
            account_session_outreach_addon={
                "enabled": False,
                "executed": False,
                "status": "disabled",
                "skip_reason": "addon_disabled",
            },
        )

        self.assertEqual(statuses[2], "completed")
        contract = orchestrator._phase_terminal_contract(
            welcome=statuses[0], follow=statuses[1], unfollow=statuses[2], outreach=statuses[3]
        )
        self.assertTrue(contract["ok"])

    def test_exhausted_unfollow_with_persistence_gap_remains_non_terminal(self) -> None:
        statuses = orchestrator._phase_statuses(
            welcome_enabled=False,
            welcome_phase_executed=False,
            welcome_session_status="skipped",
            follow_phase_executed=True,
            follow_phase_skipped_reason=None,
            follow_exit_code=0,
            follow_to_unfollow_real=_successful_unfollow(
                status="success_real_unfollow_multi_partial_exhausted",
                unfollow_actions_failed=1,
                unfollow_actions_verified=68,
                unfollow_results_persisted_count=67,
            ),
            account_session_outreach_addon={"enabled": False, "executed": False},
        )

        self.assertEqual(statuses[2], "success_real_unfollow_multi_partial_exhausted")
        self.assertFalse(orchestrator._phase_terminal_contract(
            welcome=statuses[0], follow=statuses[1], unfollow=statuses[2], outreach=statuses[3]
        )["ok"])

    def test_non_terminal_contract_is_published_with_structured_reason(self) -> None:
        decision = classify_terminal_run_failure(
            exit_code=1,
            run_status="failed",
            performance_summary={
                "phase_terminal_contract": {
                    "ok": False,
                    "reason": "account_session_phase_not_terminal",
                    "non_terminal_phases": {"unfollow": "unknown"},
                }
            },
        )

        self.assertTrue(decision.should_publish)
        self.assertEqual(decision.reason_code, "account_session_phase_not_terminal")
        self.assertNotEqual(decision.reason_code, "worker_exit_nonzero")
