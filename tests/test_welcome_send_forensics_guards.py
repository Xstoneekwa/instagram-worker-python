from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import instagram_navigation as nav
import welcome_list_sender as sender


class _HierarchyDevice:
    def __init__(self, xml: str, package: str | None = None) -> None:
        self.xml = xml
        self.package = package or str(nav.config.INSTAGRAM_PACKAGE)

    def app_current(self) -> dict[str, str]:
        return {"package": self.package, "activity": "com.instagram.mainactivity.MainActivity"}

    def dump_hierarchy(self, **_kwargs: object) -> str:
        return self.xml


class WelcomeSendForensicsGuardsTest(unittest.TestCase):
    def test_thread_surface_is_detected_without_edittext_selector(self) -> None:
        device = _HierarchyDevice(
            '<hierarchy><node resource-id="com.instagram.android:id/direct_thread_header">'
            '<node resource-id="com.instagram.android:id/header_title" text="jonova_recrutement" />'
            '</node><node resource-id="com.instagram.android:id/message_list" /></hierarchy>'
        )

        self.assertTrue(nav.is_dm_thread_screen(device))

    def test_thread_header_is_never_accepted_as_profile_identity(self) -> None:
        device = _HierarchyDevice(
            '<hierarchy><node resource-id="com.instagram.android:id/direct_thread_header">'
            '<node resource-id="com.instagram.android:id/header_title" text="jonova_recrutement" />'
            '</node><node resource-id="com.instagram.android:id/message_list" /></hierarchy>'
        )

        ok, reason, observed = nav.verify_welcome_profile_username_exact(
            device, "jonova_recrutement"
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "dm_thread_surface")
        self.assertEqual(observed, "jonova_recrutement")

    def test_exact_thread_recipient_is_accepted(self) -> None:
        device = _HierarchyDevice(
            '<hierarchy><node resource-id="com.instagram.android:id/header_title" '
            'text="tresorsbyninel" /></hierarchy>'
        )

        ok, reason, observed = nav.verify_welcome_dm_thread_recipient_exact(
            device, "tresorsbyninel"
        )

        self.assertTrue(ok)
        self.assertEqual(reason, "exact_thread_header")
        self.assertEqual(observed, "tresorsbyninel")

    def test_display_name_title_with_exact_username_subtitle_is_accepted(self) -> None:
        device = _HierarchyDevice(
            '<hierarchy><node resource-id="com.instagram.android:id/header_title" '
            'text="Jose Manuel Justiniano" />'
            '<node resource-id="com.instagram.android:id/header_subtitle" '
            'content-desc="pepito_bravo_" /></hierarchy>'
        )

        ok, reason, observed = nav.verify_welcome_dm_thread_recipient_exact(
            device, "pepito_bravo_"
        )

        self.assertTrue(ok)
        self.assertEqual(reason, "exact_thread_header_subtitle")
        self.assertEqual(observed, "pepito_bravo_")

    def test_mismatched_username_subtitle_is_rejected_even_with_expected_title(self) -> None:
        device = _HierarchyDevice(
            '<hierarchy><node resource-id="com.instagram.android:id/header_title" '
            'text="pepito_bravo_" />'
            '<node resource-id="com.instagram.android:id/header_subtitle" '
            'content-desc="different_recipient" /></hierarchy>'
        )

        ok, reason, observed = nav.verify_welcome_dm_thread_recipient_exact(
            device, "pepito_bravo_"
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "thread_recipient_identity_mismatch")
        self.assertEqual(observed, "different_recipient")

    def test_wrong_thread_recipient_is_rejected(self) -> None:
        device = _HierarchyDevice(
            '<hierarchy><node resource-id="com.instagram.android:id/header_title" '
            'text="vipbeach" /></hierarchy>'
        )

        ok, reason, observed = nav.verify_welcome_dm_thread_recipient_exact(
            device, "athenapeinture"
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "thread_recipient_identity_mismatch")
        self.assertEqual(observed, "vipbeach")

    def test_account_review_popup_blocks_send(self) -> None:
        device = _HierarchyDevice(
            '<hierarchy><node text="Review account info carefully" />'
            '<node text="Continue" /></hierarchy>'
        )

        ok, reason, _ = nav.verify_welcome_dm_thread_recipient_exact(
            device, "aurajelita730"
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "account_review_popup")

    def test_outbound_message_node_is_proof_but_composer_is_not(self) -> None:
        bubble = (
            '<hierarchy><node resource-id="com.instagram.android:id/message_content" '
            'text="Salut, merci pour la connexion." /></hierarchy>'
        )
        composer = (
            '<hierarchy><node resource-id="com.instagram.android:id/composer" '
            'text="Salut, merci pour la connexion." /></hierarchy>'
        )

        self.assertTrue(
            nav._dm_hierarchy_has_outbound_expected_message(
                bubble, "Salut, merci pour la connexion."
            )
        )
        self.assertFalse(
            nav._dm_hierarchy_has_outbound_expected_message(
                composer, "Salut, merci pour la connexion."
            )
        )

    def test_return_stops_after_wrong_profile_without_second_back(self) -> None:
        device = MagicMock()
        with (
            patch.object(nav.config, "WELCOME_LIST_SENDER_BACK_SETTLE_S", 0, create=True),
            patch.object(nav, "_dm_post_send_signal_poll", return_value=(False, "timeout")),
            patch.object(nav, "clear_dm_draft"),
            patch.object(nav, "finalize_dm_draft_before_back"),
            patch.object(
                nav,
                "tap_instagram_action_bar_back_button",
                return_value=(True, "action_bar"),
            ) as back_mock,
            patch.object(
                nav,
                "verify_welcome_profile_username_exact",
                return_value=(False, "profile_username_mismatch", "athenapeinture"),
            ),
        ):
            out = nav.return_welcome_list_from_dm_to_followers(
                device,
                "moncercleimmo",
                "com.instagram.android",
                source_profile_username="i_m_your_traker",
            )

        self.assertFalse(out["followers_surface_ok"])
        self.assertEqual(out["observed_profile_username"], "athenapeinture")
        back_mock.assert_called_once()

    def test_own_profile_reopens_followers_without_blind_back(self) -> None:
        device = MagicMock()
        det_lost = {"is_followers_list": False, "action_bar_title": "i_m_your_traker"}
        det_ok = {"is_followers_list": True, "action_bar_title": "Followers"}
        with (
            patch.object(sender, "is_dm_thread_screen", return_value=False),
            patch.object(
                sender,
                "detect_followers_list_screen_fresh",
                side_effect=[(det_lost, ""), (det_ok, "")],
            ),
            patch.object(
                sender,
                "verify_welcome_profile_username_exact",
                side_effect=[
                    (False, "profile_username_mismatch", "i_m_your_traker"),
                    (True, "exact_profile_username", "i_m_your_traker"),
                ],
            ),
            patch("instagram_navigation.tap_instagram_action_bar_back_button") as back_mock,
            patch.object(sender, "followers_session_clear_list_committed_open"),
            patch("own_profile_navigation.open_own_profile_from_bottom_nav") as profile_mock,
            patch(
                "own_profile_navigation.open_own_followers_list_from_own_profile",
                return_value=(True, {"method": "canonical"}),
            ),
            patch.object(sender, "followers_clear_detect_hierarchy_cache"),
            patch.object(sender, "followers_refresh_detect_hierarchy_cache"),
        ):
            ok = sender._restore_followers_after_job(
                device,
                "moncercleimmo",
                pkg="com.instagram.android",
                account_username="i_m_your_traker",
            )

        self.assertTrue(ok)
        back_mock.assert_not_called()
        profile_mock.assert_not_called()

    def test_thread_return_has_one_canonical_owner_and_no_fallback_navigation(self) -> None:
        device = MagicMock()
        det_lost = {"is_followers_list": False, "action_bar_title": "jonova_recrutement"}
        det_ok = {"is_followers_list": True, "action_bar_title": "Followers"}
        with (
            patch.object(sender, "is_dm_thread_screen", return_value=True),
            patch.object(
                sender,
                "detect_followers_list_screen_fresh",
                side_effect=[(det_lost, ""), (det_ok, "")],
            ),
            patch.object(
                sender,
                "return_welcome_list_from_dm_to_followers",
                return_value={
                    "followers_surface_ok": True,
                    "profile_identity_reason": "exact_profile_username",
                },
            ) as canonical_return,
            patch.object(sender, "verify_welcome_profile_username_exact") as profile_probe,
            patch("own_profile_navigation.open_own_profile_from_bottom_nav") as own_profile,
            patch("own_profile_navigation.open_own_followers_list_from_own_profile") as reopen,
        ):
            ok = sender._restore_followers_after_job(
                device,
                "jonova_recrutement",
                pkg="com.instagram.android",
                account_username="i_m_your_traker",
            )

        self.assertTrue(ok)
        canonical_return.assert_called_once()
        profile_probe.assert_not_called()
        own_profile.assert_not_called()
        reopen.assert_not_called()

    def test_send_unverified_completion_is_terminal_without_retry(self) -> None:
        job = {"id": "job-1", "recipient_username": "tresorsbyninel"}
        with patch.object(
            sender.supabase_client,
            "complete_dm_job",
            return_value={**job, "status": "failed"},
        ) as complete_mock:
            row, outcome = sender._complete_job_send_unverified_quarantine(
                job,
                last_error="send_unverified",
                thread_state="existing_thread",
            )

        self.assertEqual(outcome, "send_unverified_quarantined")
        self.assertEqual(row["status"], "failed")
        call = complete_mock.call_args
        self.assertIsNone(call.kwargs["retry_delay_seconds"])
        self.assertTrue(call.kwargs["metadata_patch"]["automatic_retry_blocked"])

    def test_preexisting_send_unverified_is_detected_for_quarantine(self) -> None:
        self.assertTrue(
            sender._job_requires_send_unverified_quarantine(
                {"last_error": "send_unverified", "metadata": {}}
            )
        )
        self.assertFalse(
            sender._job_requires_send_unverified_quarantine(
                {"last_error": "row_not_found", "metadata": {}}
            )
        )

    def test_failure_artifacts_are_captured_before_cleanup(self) -> None:
        device = MagicMock()
        device.app_current.return_value = {
            "package": "com.instagram.android",
            "activity": "com.instagram.mainactivity.MainActivity",
        }
        with patch.object(
            sender,
            "_capture_welcome_dm_forensics_artifacts",
            return_value={"screenshot_path": "/tmp/failure.png", "xml_path": "/tmp/failure.xml"},
        ) as capture_mock, patch.object(sender, "log") as log_mock:
            result = sender._capture_welcome_failure_before_cleanup(
                device,
                account_username="i_m_your_traker",
                expected_username="tresorsbyninel",
                job_id="job-1",
                navigation_state="followers_surface_lost_after_job",
            )

        capture_mock.assert_called_once_with(
            device,
            "tresorsbyninel",
            artifact_suffix="failure_before_cleanup_job-1",
        )
        self.assertEqual(result["current_package"], "com.instagram.android")
        self.assertEqual(result["expected_username"], "tresorsbyninel")
        self.assertEqual(result["last_job_id"], "job-1")
        self.assertEqual(
            result["last_navigation_state"], "followers_surface_lost_after_job"
        )
        log_mock.assert_called_once()

    def test_preexisting_send_unverified_is_never_executed(self) -> None:
        job = {
            "id": "job-1",
            "recipient_username": "tresorsbyninel",
            "dm_type": "welcome",
            "last_error": "send_unverified",
            "metadata": {"thread_state": "existing_thread"},
        }
        with (
            patch.object(sender, "resolve_welcome_dm_real_send_enabled", return_value=(True, "test")),
            patch.object(sender, "_reset_dm_sender_session_abort"),
            patch.object(sender, "_resolve_reserved_by", return_value="device-1"),
            patch.object(sender, "_resolve_dm_sender_only_job_id", return_value=("", "none")),
            patch.object(
                sender,
                "_ensure_sender_entry_followers_surface",
                return_value=(True, {"surface_decision": "fresh_detection_confirmed"}),
            ),
            patch.object(sender, "followers_refresh_detect_hierarchy_cache"),
            patch.object(sender.supabase_client, "get_account_dm_settings", return_value={}),
            patch.object(sender, "_claim_job_for_run", return_value=job),
            patch.object(sender, "_dm_sender_session_should_abort", return_value=False),
            patch.object(
                sender,
                "_complete_job_send_unverified_quarantine",
                return_value=({**job, "status": "failed"}, "send_unverified_quarantined"),
            ) as quarantine_mock,
            patch.object(sender, "execute_welcome_list_job") as execute_mock,
            patch.object(sender, "_verify_followers_surface") as surface_mock,
        ):
            code, summary = sender.run_welcome_list_sender(
                MagicMock(),
                account_id="account-1",
                account_username="i_m_your_traker",
                run_id="run-1",
                max_jobs=1,
            )

        self.assertEqual(code, 1)
        self.assertEqual(summary["jobs_failed_count"], 1)
        quarantine_mock.assert_called_once()
        self.assertFalse(quarantine_mock.call_args.kwargs["increment_attempt"])
        execute_mock.assert_not_called()
        surface_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
