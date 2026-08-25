from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import account_session_resume_engine
import account_identity_guard
import instagram_navigation
import unfollow_profile_probe
from incident_notifications import build_incident_notification_payload
from instagram_human_confirmation_challenge import (
    InstagramHumanConfirmationRequired,
    classify_instagram_human_confirmation,
    guard_instagram_human_confirmation,
)
from instagram_login_status_classifier import classify_login_probe_outcome
from instagram_login_ui_probe import probe_login_ui_from_hierarchy
from runtime_incident_matrix import (
    build_run_failure_incident_payload,
    classify_terminal_run_failure,
)


def challenge_xml(username: str = "bmybusinesses") -> str:
    return f'''<hierarchy>
      <node package="com.instagram.android" class="android.widget.TextView"
            text="Instagram" clickable="false" />
      <node package="com.instagram.android" class="android.widget.TextView"
            text="Confirm you're human" clickable="false" />
      <node package="com.instagram.android" class="android.widget.TextView"
            text="to use your account," clickable="false" />
      <node package="com.instagram.android" class="android.widget.TextView"
            text="{username}" clickable="false" />
      <node package="com.instagram.android" class="android.widget.Button"
            text="Continue" clickable="true" bounds="[12,900][700,980]" />
      <node package="com.instagram.android" class="android.widget.TextView"
            text="Takes about 30 seconds" clickable="false" />
    </hierarchy>'''


class HumanConfirmationClassifierTests(unittest.TestCase):
    def test_exact_field_surface_is_account_global_and_not_account_literal_bound(self) -> None:
        for username in ("bmybusinesses", "another.account"):
            result = classify_instagram_human_confirmation(
                challenge_xml(username),
                package_name="com.instagram.android",
                activity_name="com.instagram.challenge.activity.ChallengeActivity",
            )
            self.assertTrue(result.detected)
            self.assertEqual(result.reason_code, "instagram_human_confirmation_required")
            self.assertEqual(result.challenge_family, "instagram_human_confirmation")
            self.assertEqual(result.account_username_raw, username)
            self.assertIn("clickable_continue", result.structural_signals)

    def test_generic_support_or_checkpoint_copy_does_not_false_positive(self) -> None:
        xml = '''<hierarchy><node package="com.instagram.android" text="Get support" />
          <node package="com.instagram.android" text="Continue" clickable="true" /></hierarchy>'''
        self.assertFalse(
            classify_instagram_human_confirmation(
                xml, package_name="com.instagram.android"
            ).detected
        )

    def test_login_probe_and_status_keep_specific_reason(self) -> None:
        probe = probe_login_ui_from_hierarchy(challenge_xml())
        self.assertEqual(probe.reason, "instagram_human_confirmation_required")
        self.assertEqual(probe.metadata["screen_type"], "instagram_human_confirmation")
        status = classify_login_probe_outcome(probe.outcome, metadata=probe.metadata)
        self.assertEqual(status.reason, "instagram_human_confirmation_required")

    def test_bmy_after_70_replay_stops_without_continue_or_new_action(self) -> None:
        context = SimpleNamespace(
            account_id="account-bmy",
            account_username="bmybusinesses",
            run_id="run-bmy",
            request_id="request-bmy",
            actions_completed={"follow": 70, "like": 70, "mute": 70, "unfollow": 0},
            quotas_remaining={"follow": 50},
        )
        device = SimpleNamespace()
        with patch("runtime_incidents.publish_account_incident", return_value={"id": "incident-1"}):
            with self.assertRaises(InstagramHumanConfirmationRequired) as caught:
                guard_instagram_human_confirmation(
                    device,
                    hierarchy_xml=challenge_xml(),
                    package_name="com.instagram.android",
                    activity_name="ChallengeActivity",
                    phase="follow_candidate_acquisition",
                    preceding_action="candidate_profile_open_verification",
                    runtime_context=context,
                )
        summary = caught.exception.summary
        self.assertEqual(summary["actions_completed"]["follow"], 70)
        self.assertFalse(summary["business_actions_allowed"])
        self.assertFalse(summary["auto_restart_allowed"])
        self.assertFalse(summary["automatic_challenge_action_allowed"])

    def test_still_challenged_authorized_retry_fails_closed_again(self) -> None:
        class Device:
            def __init__(self) -> None:
                self.clicks = 0

            def click(self, *_args, **_kwargs) -> None:
                self.clicks += 1

        context = SimpleNamespace(
            account_id="account-bmy",
            account_username="bmybusinesses",
            run_id="run-retry",
            request_id="request-retry",
            actions_completed={"follow": 70, "like": 70, "mute": 70, "unfollow": 0},
            quotas_remaining={"follow": 50},
        )
        device = Device()
        dedupe_keys: list[str] = []

        def _publish(**payload):
            dedupe_keys.append(str(payload.get("dedupe_key") or ""))
            return {"id": "same-unresolved-incident"}

        with patch("runtime_incidents.publish_account_incident", side_effect=_publish):
            for attempt in (1, 2):
                with self.assertRaises(InstagramHumanConfirmationRequired) as caught:
                    guard_instagram_human_confirmation(
                        device,
                        hierarchy_xml=challenge_xml(),
                        package_name="com.instagram.android",
                        activity_name="ChallengeActivity",
                        phase=f"authorized_retry_{attempt}",
                        preceding_action="fresh_identity_preflight",
                        runtime_context=context,
                    )
                self.assertEqual(
                    caught.exception.summary["reason"],
                    "instagram_human_confirmation_required",
                )
                self.assertFalse(caught.exception.summary["business_actions_allowed"])

        self.assertEqual(device.clicks, 0)
        self.assertEqual(
            dedupe_keys,
            [
                "account:account-bmy:instagram_human_confirmation",
                "account:account-bmy:instagram_human_confirmation",
            ],
        )

    def test_terminal_projection_and_notification_remain_specific_and_deduplicated(self) -> None:
        summary = {
            "root_failure_code": "instagram_human_confirmation_required",
            "challenge_family": "instagram_human_confirmation",
            "source": "instagram_ui",
            "auto_restart_allowed": False,
        }
        decision = classify_terminal_run_failure(
            exit_code=79,
            run_status="failed",
            performance_summary=summary,
            run_type="account_session",
        )
        self.assertEqual(decision.incident_type, "instagram_human_confirmation_required")
        self.assertEqual(decision.reason_code, "instagram_human_confirmation_required")
        payload = build_run_failure_incident_payload(
            decision,
            account_id="account-bmy",
            account_username="bmybusinesses",
            run_id="run-bmy",
        )
        self.assertEqual(
            payload["dedupe_key"],
            "account:account-bmy:instagram_human_confirmation",
        )
        notification = build_incident_notification_payload(
            {**payload, "metadata": payload["metadata"], "severity": "critical"}
        )
        self.assertEqual(notification["reason_code"], "instagram_human_confirmation_required")
        self.assertIn("without clicking Continue", notification["text"])

    def test_resume_plan_treats_specific_reason_as_challenge_hard_stop(self) -> None:
        markers = account_session_resume_engine._unsafe_markers(
            {"root_failure_code": "instagram_human_confirmation_required"}
        )
        self.assertIn("challenge", markers)

    def test_identity_guard_specific_reason_wins_before_username_fallback(self) -> None:
        class Device:
            def dump_hierarchy(self, compressed=False):
                return challenge_xml()

            def app_current(self):
                return {
                    "package": "com.instagram.android",
                    "activity": "ChallengeActivity",
                }

        with patch("runtime_incidents.publish_account_incident", return_value={"id": "incident-1"}):
            result = account_identity_guard.verify_active_instagram_account_matches_expected(
                Device(),
                expected_account_username="bmybusinesses",
                expected_package_name="com.instagram.android",
                account_id="account-bmy",
                run_id="run-bmy",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "instagram_human_confirmation_required")
        self.assertEqual(result.identity_evidence, "unavailable_due_to_human_confirmation_surface")

    def test_follow_recovery_first_fresh_xml_raises_before_depth_actions(self) -> None:
        device = SimpleNamespace()
        detection = {
            "is_followers_list": False,
            "current_activity": "ChallengeActivity",
            "current_screen_guess": "unknown",
        }
        with (
            patch.object(
                instagram_navigation,
                "detect_followers_list_screen_fresh",
                return_value=(detection, challenge_xml()),
            ),
            patch("runtime_incidents.publish_account_incident", return_value={"id": "incident-1"}),
            self.assertRaises(InstagramHumanConfirmationRequired),
        ):
            instagram_navigation.classify_followers_recovery_surface(
                device,
                "source_ct",
                "com.instagram.android",
                candidate_username="novareply",
            )

    def test_unfollow_profile_verification_raises_before_target_timeout(self) -> None:
        class Device:
            def dump_hierarchy(self, compressed=False):
                return challenge_xml("other.account")

        with (
            patch("runtime_incidents.publish_account_incident", return_value={"id": "incident-1"}),
            self.assertRaises(InstagramHumanConfirmationRequired),
        ):
            unfollow_profile_probe.verify_unfollow_target_profile_strict(
                Device(),
                expected_target_username="candidate",
                timeout_s=0.2,
            )


if __name__ == "__main__":
    unittest.main()
