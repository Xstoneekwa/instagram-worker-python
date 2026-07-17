from __future__ import annotations

import unittest

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
