from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from login_orphan_challenge_recovery import (
    OrphanChallengeRecoveryResult,
    is_stable_login_surface,
    run_orphan_challenge_recovery_flow,
)
from login_orphan_recovery_state import (
    ORPHAN_RECOVERY_EVENT_BLOCKED,
    ORPHAN_RECOVERY_EVENT_RESTORED,
    resolve_orphan_recovery_state,
)

EMAIL_CHALLENGE_XML = (
    '<node text="Check your email" />'
    '<node text="Enter the code we sent to m*******e@hotmail.com" />'
    '<node class="android.widget.EditText" text="Enter code" editable="true" />'
)

LOGIN_FORM_XML = (
    '<node text="Phone number, username, or email" editable="true" />'
    '<node text="Password" password="true" editable="true" />'
    '<node text="Log in" clickable="true" />'
)

JOIN_INSTAGRAM_XML = (
    '<node text="Join Instagram" />'
    '<node text="Share what you&apos;re into with the people who get you." />'
    '<node text="Get started" clickable="true" bounds="[100,1480][980,1600]" />'
    '<node text="I already have a profile" clickable="true" bounds="[100,1640][980,1760]" />'
    '<node text="Meta" />'
)


class FakeDevice:
    def __init__(self, hierarchies: list[str], *, package: str = "com.instagram.androie") -> None:
        self.hierarchies = list(hierarchies)
        self.package = package
        self.press_calls: list[str] = []

    def dump_hierarchy(self, compressed: bool = False) -> str:
        if len(self.hierarchies) == 1:
            return self.hierarchies[0]
        return self.hierarchies.pop(0)

    def press(self, key: str) -> None:
        self.press_calls.append(str(key))

    def app_current(self) -> dict:
        return {"package": self.package}


class LoginOrphanChallengeRecoveryTest(unittest.TestCase):
    def test_stable_login_surface_recognizes_login_form(self) -> None:
        self.assertTrue(is_stable_login_surface({"screen_type": "login_form_empty", "ready_for_credentials_flow": True}))

    def test_stable_login_surface_recognizes_join_instagram_for_recovery(self) -> None:
        self.assertTrue(is_stable_login_surface({"screen_type": "join_instagram_landing", "join_instagram_landing_detected": True}))

    def test_back_to_join_instagram_landing_restores_for_recovery(self) -> None:
        device = FakeDevice([EMAIL_CHALLENGE_XML, JOIN_INSTAGRAM_XML])
        with patch("login_orphan_challenge_recovery.record_orphan_recovery_event") as record_event:
            result = run_orphan_challenge_recovery_flow(
                device,
                account_id="account-1",
                expected_username="xstonekwa_backup_acc",
                expected_package="com.instagram.androie",
                expected_app_instance_id="clone-1",
                assignment_id="assignment-1",
                credentials_version=1,
                run_id="request-1",
                challenge_provenance_loader=lambda _aid: None,
                sleeper=Mock(),
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.final_outcome, "restored")
        self.assertEqual(result.screen_type_after, "join_instagram_landing")
        event_types = [call.kwargs["event_type"] for call in record_event.call_args_list]
        self.assertIn(ORPHAN_RECOVERY_EVENT_RESTORED, event_types)

    def test_back_to_login_surface_restores_without_credentials(self) -> None:
        device = FakeDevice([EMAIL_CHALLENGE_XML, LOGIN_FORM_XML])
        with patch("login_orphan_challenge_recovery.record_orphan_recovery_event") as record_event:
            result = run_orphan_challenge_recovery_flow(
                device,
                account_id="account-1",
                expected_username="cinema_catchup",
                expected_package="com.instagram.androie",
                expected_app_instance_id="clone-1",
                assignment_id="assignment-1",
                credentials_version=1,
                run_id="request-1",
                challenge_provenance_loader=lambda _aid: None,
                sleeper=Mock(),
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.final_outcome, "restored")
        self.assertEqual(result.recovery_state, "login_surface_restored")
        self.assertEqual(device.press_calls, ["back"])
        event_types = [call.kwargs["event_type"] for call in record_event.call_args_list]
        self.assertIn(ORPHAN_RECOVERY_EVENT_RESTORED, event_types)

    def test_persistent_challenge_blocks_without_second_interaction(self) -> None:
        device = FakeDevice([EMAIL_CHALLENGE_XML, EMAIL_CHALLENGE_XML])
        with patch("login_orphan_challenge_recovery.record_orphan_recovery_event"):
            result = run_orphan_challenge_recovery_flow(
                device,
                account_id="account-1",
                expected_username="cinema_catchup",
                expected_package="com.instagram.androie",
                expected_app_instance_id="clone-1",
                assignment_id="assignment-1",
                credentials_version=1,
                challenge_provenance_loader=lambda _aid: None,
                sleeper=Mock(),
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.final_outcome, "blocked")
        self.assertEqual(result.recovery_state, "recovery_blocked")
        self.assertEqual(device.press_calls, ["back"])

    def test_resolve_state_marks_client_blocking_when_orphan_detected(self) -> None:
        with (
            patch("login_orphan_recovery_state._load_recent_recovery_events", return_value=[{"action_type": "orphan_login_challenge_detected", "created_at": "2026-06-24T10:00:00+00:00"}]),
            patch("login_orphan_recovery_state._load_recent_orphan_blocked_request", return_value=None),
            patch("login_orphan_recovery_state.has_active_login_provisioning_request", return_value=False),
        ):
            state = resolve_orphan_recovery_state("account-1")
        self.assertTrue(state["blocking_client"])
        self.assertTrue(state["botapp_action_available"])
        self.assertEqual(state["state"], "orphan_challenge_detected")


if __name__ == "__main__":
    unittest.main()
