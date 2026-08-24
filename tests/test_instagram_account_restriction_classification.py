from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import account_identity_guard as identity_guard
import incident_notifications
import runtime_incident_matrix
import runtime_incidents
from account_identity_guard import (
    MESSAGES_DISABLED_REASON,
    UNKNOWN_RESTRICTION_SCOPE_REASON,
    classify_instagram_account_restriction,
)


FIXTURE = Path(__file__).parent / "fixtures" / "nab_account_restriction_messages_disabled.xml"
HOME_XML = '<node text="Instagram" /><node content-desc="Profile" />'
NORMAL_PROFILE_XML = (
    '<node resource-id="com.instagram.android:id/action_bar_title" text="nab_youss" />'
)
ACCOUNT_ID = "76c1dff8-d16a-40cf-83df-8138d2ca5bd4"
RUN_ID = "3ff948da-a887-4460-9960-cc464db12e3c"


class RestrictionSurfaceClassifierTest(unittest.TestCase):
    def test_nab_field_xml_parses_specific_semantics_and_date_only_values(self) -> None:
        result = classify_instagram_account_restriction(FIXTURE.read_text())
        self.assertTrue(result.detected)
        self.assertEqual(result.reason_code, MESSAGES_DISABLED_REASON)
        self.assertEqual(result.restriction_family, "instagram_account_restriction")
        self.assertEqual(result.restriction_scope, "messaging")
        self.assertEqual(result.restriction_action, "send_messages")
        self.assertEqual(result.restriction_state, "disabled")
        self.assertEqual(result.restriction_start_date, "2026-08-23")
        self.assertEqual(result.restriction_end_date, "2026-09-22")
        self.assertEqual(result.restriction_title_raw, "We added a restriction to your account")
        self.assertEqual(result.restriction_detail_raw, "You can't send messages")

    def test_proved_family_without_supported_detail_is_unknown_scope(self) -> None:
        result = classify_instagram_account_restriction(
            '<node text="We added a restriction to your account" />'
            '<node text="Review this restriction" />'
        )
        self.assertTrue(result.detected)
        self.assertEqual(result.reason_code, UNKNOWN_RESTRICTION_SCOPE_REASON)
        self.assertEqual(result.restriction_scope, "unknown")

    def test_normal_profile_is_not_a_restriction(self) -> None:
        self.assertFalse(classify_instagram_account_restriction(NORMAL_PROFILE_XML).detected)


class NabIdentityGuardReplayTest(unittest.TestCase):
    def _verify(self, hierarchies: list[str]):
        with (
            patch.object(identity_guard, "_dump_hierarchy", side_effect=hierarchies),
            patch.object(identity_guard, "open_own_profile_from_bottom_nav", return_value=True),
            patch.object(identity_guard, "_capture_identity_failure_artifacts") as capture,
            patch.object(runtime_incidents, "publish_account_incident", return_value={"published": True}) as publish,
            patch.object(identity_guard, "_extract_own_profile_username_from_hierarchy", wraps=identity_guard._extract_own_profile_username_from_hierarchy) as extract,
        ):
            result = identity_guard.verify_active_instagram_account_matches_expected(
                Mock(),
                expected_account_username="nab_youss",
                account_id=ACCOUNT_ID,
                run_type="supabase_account_run",
                run_id=RUN_ID,
                stage="runner_account_identity_preflight",
            )
        return result, capture, publish, extract

    def test_run_a_replay_specific_restriction_wins_before_identity_fallback(self) -> None:
        result, capture, publish, extract = self._verify([HOME_XML, FIXTURE.read_text()])
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, MESSAGES_DISABLED_REASON)
        self.assertEqual(result.identity_evidence, "unavailable_due_to_restriction_surface")
        self.assertEqual(result.meta["identity_proof"], "unavailable_due_to_restriction_surface")
        self.assertEqual(result.meta["safety_scope"], "account_global")
        self.assertFalse(result.meta["business_actions_allowed"])
        self.assertEqual(result.meta["restriction_end_date"], "2026-09-22")
        capture.assert_not_called()
        extract.assert_not_called()
        payload = publish.call_args.kwargs
        self.assertEqual(payload["reason"], MESSAGES_DISABLED_REASON)
        self.assertIn("2026-09-22", payload["admin_message"])
        self.assertEqual(payload["metadata"]["restriction_detail_raw"], "You can't send messages")

    def test_restriction_already_occluding_pre_profile_surface_stops_without_navigation(self) -> None:
        with (
            patch.object(identity_guard, "_dump_hierarchy", return_value=FIXTURE.read_text()),
            patch.object(identity_guard, "open_own_profile_from_bottom_nav") as open_profile,
            patch.object(identity_guard, "_capture_identity_failure_artifacts") as capture,
            patch.object(runtime_incidents, "publish_account_incident", return_value={"published": True}),
        ):
            result = identity_guard.verify_active_instagram_account_matches_expected(
                Mock(),
                expected_account_username="nab_youss",
                account_id=ACCOUNT_ID,
                run_type="supabase_account_run",
                run_id=RUN_ID,
            )
        self.assertEqual(result.failure_reason, MESSAGES_DISABLED_REASON)
        open_profile.assert_not_called()
        capture.assert_not_called()

    def test_run_b_replay_requires_and_accepts_fresh_exact_identity(self) -> None:
        result, capture, publish, extract = self._verify([HOME_XML, NORMAL_PROFILE_XML])
        self.assertTrue(result.ok)
        self.assertEqual(result.actual_logged_in_username, "nab_youss")
        self.assertIn("own_profile_username_exact", result.verification_method)
        capture.assert_not_called()
        publish.assert_not_called()
        extract.assert_called_once()

    def test_resolved_backend_but_restriction_still_visible_fails_closed_again(self) -> None:
        first, _, _, _ = self._verify([HOME_XML, FIXTURE.read_text()])
        second, _, _, _ = self._verify([HOME_XML, FIXTURE.read_text()])
        self.assertEqual(first.failure_reason, MESSAGES_DISABLED_REASON)
        self.assertEqual(second.failure_reason, MESSAGES_DISABLED_REASON)
        self.assertFalse(second.meta["business_actions_allowed"])


class RestrictionIncidentAndNotifierTest(unittest.TestCase):
    def _summary(self) -> dict:
        classification = classify_instagram_account_restriction(FIXTURE.read_text()).to_dict()
        return {
            "reason": MESSAGES_DISABLED_REASON,
            "account_identity_failure_reason": MESSAGES_DISABLED_REASON,
            "account_identity_verification_method": "instagram_account_restriction_surface",
            **classification,
            "identity_proof": "unavailable_due_to_restriction_surface",
            "safety_scope": "account_global",
            "business_actions_allowed": False,
        }

    def test_terminal_matrix_keeps_specific_reason_and_account_global_dedupe(self) -> None:
        decision = runtime_incident_matrix.classify_terminal_run_failure(
            exit_code=75,
            performance_summary=self._summary(),
        )
        self.assertEqual(decision.incident_type, "instagram_account_restriction")
        self.assertEqual(decision.reason_code, MESSAGES_DISABLED_REASON)
        payload = runtime_incident_matrix.build_run_failure_incident_payload(
            decision,
            account_id=ACCOUNT_ID,
            account_username="nab_youss",
            run_id=RUN_ID,
        )
        self.assertEqual(
            payload["dedupe_key"],
            f"account:{ACCOUNT_ID}:instagram_restriction:{MESSAGES_DISABLED_REASON}:2026-09-22",
        )
        self.assertEqual(payload["metadata"]["restriction_scope"], "messaging")
        self.assertEqual(payload["metadata"]["restriction_end_date"], "2026-09-22")

    def test_slack_and_discord_payloads_keep_specific_reason_and_expiry(self) -> None:
        incident = {
            "id": "incident-nab",
            "severity": "critical",
            "status": "open",
            "incident_type": "instagram_account_restriction",
            "account_id": ACCOUNT_ID,
            "account_username": "nab_youss",
            "run_id": RUN_ID,
            "reason": MESSAGES_DISABLED_REASON,
            "metadata": self._summary(),
        }
        payload = incident_notifications.build_incident_notification_payload(incident)
        self.assertEqual(payload["reason_code"], MESSAGES_DISABLED_REASON)
        self.assertIn("Restriction: Cannot send messages", payload["text"])
        self.assertIn("Start: 23 Aug 2026", payload["text"])
        self.assertIn("Ends: 22 Sep 2026", payload["text"])
        self.assertIn("before any Follow/Unfollow action", payload["text"])
        self.assertIn("Cannot send messages", incident_notifications.build_slack_payload(payload)["text"])
        self.assertIn("22 Sep 2026", incident_notifications.build_discord_payload(payload)["content"])

    def test_no_end_date_does_not_invent_expiry(self) -> None:
        incident = {
            "severity": "critical",
            "status": "open",
            "incident_type": "instagram_account_restriction",
            "account_username": "nab_youss",
            "reason": UNKNOWN_RESTRICTION_SCOPE_REASON,
            "metadata": {
                "reason_code": UNKNOWN_RESTRICTION_SCOPE_REASON,
                "restriction_scope": "unknown",
            },
        }
        payload = incident_notifications.build_incident_notification_payload(incident)
        self.assertNotIn("Ends:", payload["text"])


if __name__ == "__main__":
    unittest.main()
