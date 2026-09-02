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

    def test_first_natural_session_uses_canonical_progress_without_resume_plan(self) -> None:
        out = build_account_session_phase_notification_summary(
            {
                "follow_quota_target": 50,
                "follows_completed_count": 16,
                "follow_quota_remaining": 34,
                "unfollow_quota_target": 80,
                "unfollow_actions_verified": 0,
                "unfollow_quota_remaining": 80,
            },
            {},
        )
        self.assertEqual(out["follow_quota_target"], 50)
        self.assertEqual(out["follows_completed_count"], 16)
        self.assertEqual(out["follow_quota_remaining"], 34)
        self.assertEqual(out["unfollow_quota_target"], 80)
        self.assertEqual(out["unfollow_actions_verified"], 0)
        self.assertEqual(out["unfollow_quota_remaining"], 80)

    def test_resume_plan_is_fallback_not_authority_over_canonical_progress(self) -> None:
        out = build_account_session_phase_notification_summary(
            {
                "follow_quota_target": 50,
                "follows_completed_count": 16,
                "follow_quota_remaining": 34,
            },
            {"resume_plan": {"quota_remaining": {"follow": 7, "unfollow": 9}}},
        )
        self.assertEqual(out["follow_quota_target"], 50)
        self.assertEqual(out["follows_completed_count"], 16)
        self.assertEqual(out["follow_quota_remaining"], 34)

    def test_root_reason_does_not_get_overwritten_by_terminal_reason(self) -> None:
        out = build_account_session_phase_notification_summary(
            {
                "first_causal_reason": "candidate_identity_continuity",
                "follow_stop_reason": "followers_recovery_surface_unproved",
                "root_failure_code": "account_session_phase_not_terminal",
                "failure_phase": "post_recovery_return_ct",
            },
            {},
        )
        self.assertEqual(out["root_failure_reason"], "candidate_identity_continuity")
        self.assertEqual(out["root_failure_phase"], "post_recovery_return_ct")
        self.assertEqual(out["safe_stop_reason"], "followers_recovery_surface_unproved")
        self.assertEqual(out["terminal_reason"], "account_session_phase_not_terminal")

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
                        "follow_quota_target": 10,
                        "follows_completed_count": 0,
                        "follow_quota_remaining": 10,
                        "unfollow_quota_target": 5,
                        "unfollow_actions_verified": 0,
                        "unfollow_quota_remaining": 5,
                        "unfollow_actionable_remaining": 0,
                        "unfollow_candidates_on_hold": 3,
                        "unfollow_terminally_unavailable": 2,
                        "stable_reason": "unfollow_search_surface_consecutive_failure_limit_reached",
                        "root_failure_reason": "unfollow_search_surface_consecutive_failure_limit_reached",
                        "root_failure_phase": "unfollow",
                        "safe_stop_reason": "unfollow_search_surface_consecutive_failure_limit_reached",
                        "terminal_reason": "account_session_phase_not_terminal",
                        "auto_restart_allowed": False,
                        "auto_restart_blocked": True,
                        "suggested_next_action": "wait_until_next_retry_at",
                    }
                },
            }
        )
        self.assertIn("Phase: Unfollow", payload["text"])
        self.assertIn("Situation: Automation paused for account safety.", payload["text"])
        self.assertIn("Progress: 0 / 10 follows | 0 / 5 unfollows", payload["text"])
        self.assertNotIn("Follow: target", payload["text"])
        self.assertIn("Auto Restart: blocked", payload["text"])

    def test_router_v2_nab_root_chain_and_progress_wording(self) -> None:
        summary = build_account_session_phase_notification_summary(
            {
                "first_causal_reason": "candidate_identity_continuity",
                "follow_stop_reason": "followers_recovery_surface_unproved",
                "root_failure_code": "account_session_phase_not_terminal",
                "failure_phase": "post_recovery_return_ct",
                "follow_quota_target": 50,
                "follows_completed_count": 16,
                "follow_quota_remaining": 34,
                "unfollow_quota_target": 80,
                "unfollow_actions_verified": 0,
                "unfollow_quota_remaining": 80,
            },
            {},
        )
        payload = incident_notifications.build_incident_notification_payload(
            {
                "id": "incident-1",
                "incident_type": "runtime_failure",
                "severity": "warning",
                "status": "open",
                "metadata": {"phase_summary": summary},
            }
        )
        self.assertIn(
            "Reason: Candidate post identity could not be confirmed during post recovery.",
            payload["text"],
        )
        self.assertIn("Phase: Post recovery / Return CT", payload["text"])
        self.assertIn("Progress: 16 / 50 follows | 0 / 80 unfollows", payload["text"])
        self.assertIn("Open Incidents/Actions", incident_notifications.build_slack_payload(payload)["text"])
        self.assertIn("Open Incidents/Actions", incident_notifications.build_discord_payload(payload)["content"])


if __name__ == "__main__":
    unittest.main()
