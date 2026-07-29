import unittest

import incident_notifications
from account_session_phase_notification import (
    build_account_session_phase_notification_summary,
)


class AccountSessionPhaseNotificationTests(unittest.TestCase):
    def test_zero_follow_quota_has_specific_reason_code(self) -> None:
        out = build_account_session_phase_notification_summary(
            {"root_failure_code": "global_follow_cap_reached"},
            {
                "resume_plan": {
                    "phases_to_run": {"follow": True, "unfollow": True},
                    "quota_remaining": {"follow": 0, "unfollow": 2},
                }
            },
        )
        self.assertEqual(out["reason_code"], "FOLLOW_ZERO_QUOTA_MISCLASSIFIED")
        self.assertEqual(out["follow_remaining"], 0)

    def test_missing_fallback_has_specific_reason_code(self) -> None:
        out = build_account_session_phase_notification_summary(
            {"root_failure_code": "ui_coverage_budget_exhausted"},
            {},
        )
        self.assertEqual(
            out["reason_code"],
            "UNFOLLOW_FALLBACK_NOT_ARMED_AFTER_COVERAGE_EXHAUSTED",
        )

    def test_search_unhealthy_notification_exposes_structured_root_cause(self) -> None:
        out = build_account_session_phase_notification_summary(
            {
                "phase_terminal_contract": {
                    "non_terminal_phases": {"unfollow": "partial_resumable"},
                },
                "follow_quota_remaining": 0,
                "follow_to_unfollow_real": {
                    "stop_reason": "search_results_loading_timeout",
                    "last_run_remaining_eligible": 4,
                    "technical_hold_candidates_count": 2,
                },
                "restart_allowed": False,
            },
            {
                "resume_plan": {
                    "phases_to_run": {"follow": False, "unfollow": True},
                    "quota_remaining": {"follow": 0, "unfollow": 4},
                }
            },
        )
        self.assertEqual(out["reason_code"], "SEARCH_SURFACE_UNHEALTHY_BATCH_CONTINUED")
        self.assertEqual(out["blocked_phase"], "unfollow")
        self.assertEqual(out["unfollow_candidates_on_hold"], 2)
        self.assertTrue(out["auto_restart_blocked"])

    def test_confirmed_not_found_recommends_next_candidate(self) -> None:
        out = build_account_session_phase_notification_summary(
            {
                "unfollow_outcome": {
                    "stable_reason": "username_not_found_confirmed",
                    "remaining_count": 3,
                }
            },
            {},
        )
        self.assertEqual(out["reason_code"], "USERNAME_NOT_FOUND_REMOVED_FROM_BACKLOG")
        self.assertEqual(out["suggested_next_action"], "continue_next_candidate")

    def test_circuit_notification_exposes_retry_action(self) -> None:
        out = build_account_session_phase_notification_summary(
            {
                "root_failure_code": (
                    "unfollow_search_surface_consecutive_failure_limit_reached"
                )
            },
            {},
        )
        self.assertEqual(out["reason_code"], "UNFOLLOW_AUTO_RESTART_CIRCUIT_BREAKER")
        self.assertEqual(out["suggested_next_action"], "wait_until_next_retry_at")

    def test_primary_failure_reason_is_preserved_when_phase_summary_is_sparse(self) -> None:
        out = build_account_session_phase_notification_summary(
            {},
            {},
            primary_failure_reason="actual_logged_in_username_not_detected",
        )
        self.assertEqual(
            out["stable_reason"],
            "actual_logged_in_username_not_detected",
        )

    def test_slack_discord_base_payload_contains_structured_phase_fields(self) -> None:
        payload = incident_notifications.build_incident_notification_payload(
            {
                "incident_type": "runtime_failure",
                "severity": "warning",
                "status": "open",
                "metadata": {
                    "phase_summary": {
                        "reason_code": "UNFOLLOW_AUTO_RESTART_CIRCUIT_BREAKER",
                        "blocked_phase": "unfollow",
                        "requested_phases": {"follow": True, "unfollow": True},
                        "follow_target": 10,
                        "follow_remaining": 10,
                        "unfollow_actionable_remaining": 0,
                        "unfollow_candidates_on_hold": 3,
                        "unfollow_terminally_unavailable": 2,
                        "stable_reason": "unfollow_search_surface_consecutive_failure_limit_reached",
                        "auto_restart_allowed": False,
                        "auto_restart_blocked": True,
                        "suggested_next_action": "wait_until_next_retry_at",
                    }
                },
            }
        )
        self.assertIn("Phase: unfollow", payload["text"])
        self.assertIn("Follow: target 10 | remaining 10", payload["text"])
        self.assertIn("Unfollow: actionable 0 | hold 3 | terminal 2", payload["text"])
        self.assertIn("Auto Restart: blocked", payload["text"])


if __name__ == "__main__":
    unittest.main()
