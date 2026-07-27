from __future__ import annotations

import unittest
from unittest.mock import patch

from instagram_action_restriction import (
    InstagramActionRestrictionDetected,
    _safe_xml_snapshot,
    classify_instagram_action_rate_limit,
    configure_restriction_runtime_context,
    guard_instagram_action_rate_limit,
    validate_restriction_preflight_policy,
)


def xml(*texts: str, package: str = "com.instagram.android", modal: bool = True) -> str:
    nodes = []
    for index, text in enumerate(texts):
        cls = "android.widget.Button" if text in {"OK", "Let us know", "Contactez-nous"} else "android.widget.TextView"
        nodes.append(f'<node index="{index}" package="{package}" class="{cls}" text="{text}" clickable="{str(cls.endswith("Button")).lower()}" />')
    klass = "android.app.Dialog" if modal else "android.widget.FrameLayout"
    return f'<hierarchy><node package="{package}" class="{klass}">{"".join(nodes)}</node></hierarchy>'


EN_TITLE = "Try again later"
EN_BODY = "We limit how often you can do certain things on Instagram to protect our community. Tell us if you think that we've made a mistake."
FR_TITLE = "Réessayer plus tard"
FR_BODY = "Nous limitons la fréquence de certaines actions que vous pouvez effectuer sur Instagram afin de protéger notre communauté. Si vous pensez que nous avons fait erreur, faites-le-nous savoir."


class RestrictionClassifierTests(unittest.TestCase):
    def test_exact_english_popup(self) -> None:
        result = classify_instagram_action_rate_limit(xml(EN_TITLE, EN_BODY, "OK", "Let us know"))
        self.assertTrue(result.detected)
        self.assertEqual(result.language, "en")

    def test_exact_french_popup(self) -> None:
        result = classify_instagram_action_rate_limit(xml(FR_TITLE, FR_BODY, "OK", "Contactez-nous"))
        self.assertTrue(result.detected)
        self.assertEqual(result.language, "fr")

    def test_partial_english_and_french(self) -> None:
        en = classify_instagram_action_rate_limit(xml(EN_TITLE, "We limit how often you can do certain things", "OK"))
        fr = classify_instagram_action_rate_limit(xml(FR_TITLE, "Nous limitons la fréquence de certaines actions", "Contactez-nous"))
        self.assertTrue(en.detected)
        self.assertTrue(fr.detected)

    def test_accents_apostrophes_and_punctuation_are_normalized(self) -> None:
        result = classify_instagram_action_rate_limit(
            xml("RÉESSAYER PLUS TARD !", "Nous limitons la frequence de certaines actions.", "Contactez–nous")
        )
        self.assertTrue(result.detected)

    def test_title_alone_is_insufficient(self) -> None:
        result = classify_instagram_action_rate_limit(xml(EN_TITLE, modal=True))
        self.assertFalse(result.detected)

    def test_body_and_buttons_without_title(self) -> None:
        result = classify_instagram_action_rate_limit(xml(EN_BODY, "OK", "Let us know"))
        self.assertTrue(result.detected)

    def test_visual_text_can_replace_missing_xml_text(self) -> None:
        result = classify_instagram_action_rate_limit(
            xml("OK", "Contactez-nous"), visual_text=f"{FR_TITLE} {FR_BODY}", modal_structure=True
        )
        self.assertTrue(result.detected)
        self.assertEqual(result.language, "fr")

    def test_non_instagram_try_again_is_rejected(self) -> None:
        result = classify_instagram_action_rate_limit(
            xml("Try again later", "We limit how often you can do certain things", "OK", package="com.example.network")
        )
        self.assertFalse(result.detected)

    def test_network_and_challenge_popups_are_rejected(self) -> None:
        for title, body in [
            ("Something went wrong", "Please check your internet connection and try again."),
            ("Confirm it's you", "Enter the security code we sent you."),
            ("Incorrect password", "The password you entered is incorrect."),
        ]:
            self.assertFalse(classify_instagram_action_rate_limit(xml(title, body, "OK")).detected)


class RestrictionRuntimeGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        configure_restriction_runtime_context(
            account_id="account-1",
            account_username="safe_username",
            run_id="run-1",
            request_id="request-1",
            device_id="device-1",
        )

    def test_confirmed_popup_applies_hold_once_and_never_clicks(self) -> None:
        class Device:
            click_count = 0

            def app_current(self):
                return {"package": "com.instagram.android"}

            def click(self, *_args, **_kwargs):
                self.click_count += 1

        device = Device()
        hierarchy = xml(EN_TITLE, EN_BODY, "OK", "Let us know")
        apply_result = {
            "ok": True,
            "incident_id": "incident-1",
            "dedupe_key": "instagram_account_restriction:account-1",
            "hold_status": "active",
            "deduplicated": False,
        }

        with (
            patch("instagram_action_restriction._capture_evidence", return_value={"xml_snapshot_captured": True}),
            patch(
                "instagram_action_restriction.supabase_client.apply_instagram_action_restriction",
                return_value=apply_result,
            ) as apply_hold,
        ):
            with self.assertRaises(InstagramActionRestrictionDetected) as raised:
                guard_instagram_action_rate_limit(
                    device,
                    phase="follow",
                    preceding_action="before_follow_tap",
                    hierarchy_xml=hierarchy,
                )

        self.assertEqual(device.click_count, 0)
        apply_hold.assert_called_once()
        self.assertEqual(raised.exception.summary["reason"], "instagram_action_rate_limit")
        self.assertEqual(raised.exception.summary["incident_id"], "incident-1")
        self.assertFalse(raised.exception.summary["auto_restart_allowed"])

    def test_database_failure_still_stops_the_session(self) -> None:
        class Device:
            def app_current(self):
                return {"package": "com.instagram.android"}

        with (
            patch("instagram_action_restriction._capture_evidence", return_value={}),
            patch(
                "instagram_action_restriction.supabase_client.apply_instagram_action_restriction",
                side_effect=RuntimeError("database unavailable"),
            ),
        ):
            with self.assertRaises(InstagramActionRestrictionDetected) as raised:
                guard_instagram_action_rate_limit(
                    Device(),
                    phase="dm",
                    preceding_action="before_dm_send_tap",
                    hierarchy_xml=xml(FR_TITLE, FR_BODY, "OK", "Contactez-nous"),
                )

        self.assertIsNone(raised.exception.summary["incident_id"])
        self.assertTrue(raised.exception.summary["account_pause_required"])

    def test_evidence_xml_redacts_unrelated_account_text_and_secrets(self) -> None:
        hierarchy = (
            '<hierarchy><node package="com.instagram.android" class="android.app.Dialog" '
            f'text="{EN_TITLE}"/><node text="private_username" token="secret-value"/>'
            '</hierarchy>'
        )
        safe = _safe_xml_snapshot(hierarchy)
        self.assertIn(EN_TITLE, safe)
        self.assertNotIn("private_username", safe)
        self.assertNotIn("secret-value", safe)
        self.assertIn("[redacted]", safe)

    def test_pause_rpc_uses_one_short_bounded_attempt(self) -> None:
        import supabase_client

        with patch(
            "supabase_client._call_rpc",
            return_value={"ok": True, "incident_id": "incident-1"},
        ) as call:
            result = supabase_client.apply_instagram_action_restriction(
                {"account_id": "account-1", "metadata_safe": {}}
            )
        self.assertTrue(result["ok"])
        self.assertEqual(call.call_args.kwargs["timeout_seconds"], 4.0)
        self.assertEqual(call.call_args.kwargs["max_retries"], 0)


class RestrictionPreflightPolicyTests(unittest.TestCase):
    def test_explicit_preflight_only_with_all_business_phases_false_is_authorized(self) -> None:
        authorized, reason, incident_id = validate_restriction_preflight_policy(
            {
                "restriction_preflight_only": True,
                "incident_id": "incident-1",
                "phases_to_run": {
                    "welcome": False,
                    "follow": False,
                    "unfollow": False,
                },
            }
        )
        self.assertTrue(authorized)
        self.assertEqual(reason, "restriction_preflight_authorized")
        self.assertEqual(incident_id, "incident-1")

    def test_missing_flag_incident_or_explicit_false_phase_fails_closed(self) -> None:
        invalid_policies = [
            {
                "incident_id": "incident-1",
                "phases_to_run": {"welcome": False, "follow": False, "unfollow": False},
            },
            {
                "restriction_preflight_only": True,
                "phases_to_run": {"welcome": False, "follow": False, "unfollow": False},
            },
            {
                "restriction_preflight_only": True,
                "incident_id": "incident-1",
                "phases_to_run": {"welcome": False, "follow": True, "unfollow": False},
            },
            {
                "restriction_preflight_only": True,
                "incident_id": "incident-1",
                "phases_to_run": {"welcome": False, "follow": False},
            },
        ]
        for policy in invalid_policies:
            with self.subTest(policy=policy):
                self.assertFalse(validate_restriction_preflight_policy(policy)[0])


if __name__ == "__main__":
    unittest.main()
