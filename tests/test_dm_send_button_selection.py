from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import config
import dm_sender_engine
import instagram_navigation as nav


def _candidate(
    *,
    rid: str = "",
    desc: str = "",
    cx: int = 900,
    cy: int = 1700,
    clickable: bool = False,
) -> dict:
    return {
        "className": "android.widget.ImageView",
        "resourceId": rid,
        "text": "",
        "contentDescription": desc,
        "bounds": {"left": cx - 20, "top": cy - 20, "right": cx + 20, "bottom": cy + 20},
        "center_x": cx,
        "center_y": cy,
        "width": 40,
        "height": 40,
        "clickable": clickable,
        "right_of_composer": True,
        "lower_screen_half": True,
        "near_composer_vertical": True,
    }


class DmSendButtonSelectionTest(unittest.TestCase):
    def test_selects_background_over_icon_when_both_present(self) -> None:
        background = _candidate(
            rid="com.instagram.android:id/row_thread_composer_send_button_background",
            desc="Send",
        )
        icon = _candidate(
            rid="com.instagram.android:id/row_thread_composer_send_button_icon",
            desc="Send",
            cx=910,
        )
        best = nav._dm_select_best_send_candidate([icon, background])
        self.assertIsNotNone(best)
        assert best is not None
        self.assertIn("send_button_background", str(best.get("resourceId")))

    def test_rejects_systemui_noise(self) -> None:
        noisy = _candidate(
            rid="com.android.systemui:id/menu_container",
            desc="Send",
        )
        self.assertIsNone(nav._dm_select_best_send_candidate([noisy]))

    def test_no_valid_candidate_returns_none(self) -> None:
        weak = _candidate(rid="com.instagram.android:id/random_view", desc="")
        self.assertIsNone(nav._dm_select_best_send_candidate([weak]))

    def test_visual_survivors_select_best_when_multiple(self) -> None:
        composer = {"left": 100, "top": 1600, "right": 800, "bottom": 1750}
        xml = """
        <hierarchy>
          <node class="android.widget.ImageView"
                resource-id="com.instagram.android:id/row_thread_composer_send_button_background"
                content-desc="Send" bounds="[820,1620][900,1700]" enabled="true" />
          <node class="android.widget.ImageView"
                resource-id="com.instagram.android:id/row_thread_composer_send_button_icon"
                content-desc="Send" bounds="[830,1630][890,1690]" enabled="true" />
        </hierarchy>
        """
        info = nav._dm_visual_send_candidates_from_hierarchy_xml(xml, composer, 1080, 1920)
        survivors = list(info.get("surviving_candidates") or [])
        self.assertGreaterEqual(len(survivors), 1)
        best = nav._dm_select_best_send_candidate(survivors)
        self.assertIsNotNone(best)
        assert best is not None
        self.assertIn("send_button_background", str(best.get("resourceId")))

    def test_resolve_send_button_returns_tap_for_surviving_candidate(self) -> None:
        composer = {"left": 100, "top": 1600, "right": 800, "bottom": 1750}
        xml = """
        <hierarchy>
          <node class="android.widget.ImageView"
                resource-id="com.instagram.android:id/row_thread_composer_send_button_background"
                content-desc="Send" bounds="[820,1620][900,1700]" enabled="true" />
        </hierarchy>
        """
        device = MagicMock()
        tap, status, meta = nav._dm_resolve_send_button_from_hierarchy(
            device,
            xml,
            composer_bounds=composer,
            screen_w=1080,
            screen_h=1920,
            thread_state="empty_new_thread",
        )
        self.assertEqual(status, "ok")
        self.assertIsNotNone(tap)
        self.assertGreaterEqual(int(meta.get("send_button_candidate_count") or 0), 1)

    def test_send_dm_safe_uses_tap_failed_not_missing_when_click_raises(self) -> None:
        device = MagicMock()
        composer = MagicMock()
        composer.get_text.return_value = "Salut"
        tap = MagicMock()
        tap.click.side_effect = RuntimeError("tap failed")
        with (
            patch.object(config, "ENABLE_REAL_DM_SEND", True),
            patch.object(nav, "_dm_find_focus_composer", return_value=composer),
            patch.object(nav, "read_dm_composer_text", return_value="Salut"),
            patch.object(
                nav,
                "wait_for_dm_send_button_after_draft",
                return_value=(tap, "ok", {"send_button_candidate_count": 1}),
            ),
            patch.object(nav, "_dm_post_send_signal_poll", return_value=(False, "timeout")),
        ):
            out = nav.send_dm_safe(
                device,
                "user",
                "Salut",
                "empty_new_thread",
            )
        self.assertEqual(out.get("reason"), "send_button_tap_failed")
        self.assertFalse(out.get("sent"))

    def test_send_dm_safe_rejects_composer_shortened_without_outbound_bubble(self) -> None:
        device = MagicMock()
        composer = MagicMock()
        composer.get_text.return_value = "Salut"
        tap = MagicMock()
        with (
            patch.object(config, "ENABLE_REAL_DM_SEND", True),
            patch.object(nav, "_dm_find_focus_composer", return_value=composer),
            patch.object(nav, "read_dm_composer_text", return_value="Salut"),
            patch.object(
                nav,
                "wait_for_dm_send_button_after_draft",
                return_value=(tap, "ok", {"send_button_candidate_count": 1}),
            ),
            patch.object(nav, "_dm_thread_message_signature", return_value={"message_marker_count": 0}),
            patch.object(
                nav,
                "_dm_post_send_signal_poll",
                return_value=(True, "composer_text_shortened"),
            ),
            patch.object(
                nav,
                "_dm_verify_outbound_message_after_send",
                return_value=(
                    False,
                    "outbound_bubble_not_verified",
                    {"message_marker_count": 0, "expected_text_present": False},
                ),
            ),
        ):
            out = nav.send_dm_safe(
                device,
                "user",
                "Salut",
                "empty_new_thread",
            )

        self.assertFalse(out.get("sent"))
        self.assertEqual(out.get("reason"), "send_unverified")
        self.assertEqual(out.get("post_send_signal_reason"), "outbound_bubble_not_verified")
        self.assertEqual(out.get("post_send_secondary_signal_reason"), "composer_text_shortened")

    def test_send_dm_safe_accepts_new_outbound_bubble_after_tap(self) -> None:
        device = MagicMock()
        composer = MagicMock()
        composer.get_text.return_value = "Salut"
        tap = MagicMock()
        with (
            patch.object(config, "ENABLE_REAL_DM_SEND", True),
            patch.object(nav, "_dm_find_focus_composer", return_value=composer),
            patch.object(nav, "read_dm_composer_text", return_value="Salut"),
            patch.object(
                nav,
                "wait_for_dm_send_button_after_draft",
                return_value=(tap, "ok", {"send_button_candidate_count": 1}),
            ),
            patch.object(nav, "_dm_thread_message_signature", return_value={"message_marker_count": 0}),
            patch.object(nav, "_dm_post_send_signal_poll", return_value=(True, "composer_empty")),
            patch.object(
                nav,
                "_dm_verify_outbound_message_after_send",
                return_value=(
                    True,
                    "outbound_bubble_after_tap",
                    {"message_marker_count": 1, "expected_text_present": True},
                ),
            ),
        ):
            out = nav.send_dm_safe(
                device,
                "user",
                "Salut",
                "empty_new_thread",
            )

        self.assertTrue(out.get("sent"))
        self.assertEqual(out.get("post_send_signal_reason"), "outbound_bubble_after_tap")
        self.assertEqual(out.get("post_send_secondary_signal_reason"), "composer_empty")

    def test_outbound_send_verification_requires_new_bubble(self) -> None:
        device = MagicMock()
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node resource-id="com.instagram.android:id/row_thread_message"
                text="Salut" />
        </hierarchy>
        """
        pre = {"hierarchy_hash": "before", "message_marker_count": 0, "expected_text_present": False}
        with (
            patch.object(nav.config, "DM_OUTBOUND_SEND_VERIFY_MAX_S", 0.01, create=True),
            patch.object(nav.config, "DM_OUTBOUND_SEND_VERIFY_POLL_S", 0, create=True),
            patch.object(nav, "read_dm_composer_text", return_value=""),
        ):
            ok, reason, sig = nav._dm_verify_outbound_message_after_send(
                device,
                "Salut",
                pre_signature=pre,
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "outbound_bubble_after_tap")
        self.assertTrue(sig.get("expected_text_present"))

    def test_outbound_send_verification_rejects_empty_thread(self) -> None:
        device = MagicMock()
        device.dump_hierarchy.return_value = "<hierarchy/>"
        pre = {"hierarchy_hash": "before", "message_marker_count": 0, "expected_text_present": False}
        with (
            patch.object(nav.config, "DM_OUTBOUND_SEND_VERIFY_MAX_S", 0.01, create=True),
            patch.object(nav.config, "DM_OUTBOUND_SEND_VERIFY_POLL_S", 0, create=True),
            patch.object(nav, "read_dm_composer_text", return_value=""),
        ):
            ok, reason, _sig = nav._dm_verify_outbound_message_after_send(
                device,
                "Salut",
                pre_signature=pre,
            )

        self.assertFalse(ok)
        self.assertEqual(reason, "outbound_bubble_not_verified")

    def test_outbound_send_verification_accepts_pending_bubble(self) -> None:
        device = MagicMock()
        pre = {"hierarchy_hash": "before", "message_marker_count": 0, "expected_text_present": False}
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node resource-id="com.instagram.android:id/row_thread_message"
                text="Sending" />
        </hierarchy>
        """
        with (
            patch.object(nav.config, "DM_OUTBOUND_SEND_VERIFY_MAX_S", 0.01, create=True),
            patch.object(nav.config, "DM_OUTBOUND_SEND_VERIFY_POLL_S", 0, create=True),
            patch.object(nav, "read_dm_composer_text", return_value=""),
        ):
            ok, reason, sig = nav._dm_verify_outbound_message_after_send(
                device,
                "Salut",
                pre_signature=pre,
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "outbound_pending_bubble_after_tap")
        self.assertTrue(sig.get("pending_outbound_signal_present"))

    def test_outbound_send_verification_accepts_normalized_emoji_text(self) -> None:
        device = MagicMock()
        pre = {"hierarchy_hash": "before", "message_marker_count": 0, "expected_text_present": False}
        expected = "Salut 👋\nBienvenue chez nous"
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node resource-id="com.instagram.android:id/row_thread_message"
                text="Salut 👋 Bienvenue chez nous" />
        </hierarchy>
        """
        with (
            patch.object(nav.config, "DM_OUTBOUND_SEND_VERIFY_MAX_S", 0.01, create=True),
            patch.object(nav.config, "DM_OUTBOUND_SEND_VERIFY_POLL_S", 0, create=True),
            patch.object(nav, "read_dm_composer_text", return_value=""),
        ):
            ok, reason, sig = nav._dm_verify_outbound_message_after_send(
                device,
                expected,
                pre_signature=pre,
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "outbound_bubble_after_tap")
        self.assertTrue(sig.get("expected_text_present"))

    def test_outbound_send_verification_rejects_wrong_thread_text(self) -> None:
        device = MagicMock()
        pre = {"hierarchy_hash": "before", "message_marker_count": 0, "expected_text_present": False}
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node resource-id="com.instagram.android:id/row_thread_message"
                text="Different conversation" />
        </hierarchy>
        """
        with (
            patch.object(nav.config, "DM_OUTBOUND_SEND_VERIFY_MAX_S", 0.01, create=True),
            patch.object(nav.config, "DM_OUTBOUND_SEND_VERIFY_POLL_S", 0, create=True),
            patch.object(nav, "read_dm_composer_text", return_value=""),
        ):
            ok, reason, _sig = nav._dm_verify_outbound_message_after_send(
                device,
                "Salut",
                pre_signature=pre,
            )

        self.assertFalse(ok)
        self.assertEqual(reason, "outbound_bubble_not_verified")

    def test_outbound_send_verification_rejects_retry_icon_without_text(self) -> None:
        device = MagicMock()
        pre = {"hierarchy_hash": "before", "message_marker_count": 0, "expected_text_present": False}
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node resource-id="com.instagram.android:id/row_thread_message"
                text="Retry" />
        </hierarchy>
        """
        with (
            patch.object(nav.config, "DM_OUTBOUND_SEND_VERIFY_MAX_S", 0.01, create=True),
            patch.object(nav.config, "DM_OUTBOUND_SEND_VERIFY_POLL_S", 0, create=True),
            patch.object(nav, "read_dm_composer_text", return_value=""),
        ):
            ok, reason, sig = nav._dm_verify_outbound_message_after_send(
                device,
                "Salut",
                pre_signature=pre,
            )

        self.assertFalse(ok)
        self.assertEqual(reason, "outbound_bubble_not_verified")
        self.assertFalse(sig.get("pending_outbound_signal_present"))

    def test_outbound_send_verification_rejects_existing_bubble_only(self) -> None:
        device = MagicMock()
        existing = """
        <hierarchy>
          <node resource-id="com.instagram.android:id/row_thread_message"
                text="Salut" />
        </hierarchy>
        """
        device.dump_hierarchy.return_value = existing
        pre = nav._dm_thread_message_signature(device, "Salut", hierarchy_xml=existing)
        with (
            patch.object(nav.config, "DM_OUTBOUND_SEND_VERIFY_MAX_S", 0.01, create=True),
            patch.object(nav.config, "DM_OUTBOUND_SEND_VERIFY_POLL_S", 0, create=True),
            patch.object(nav, "read_dm_composer_text", return_value=""),
        ):
            ok, reason, _sig = nav._dm_verify_outbound_message_after_send(
                device,
                "Salut",
                pre_signature=pre,
            )

        self.assertFalse(ok)
        self.assertEqual(reason, "outbound_bubble_not_new")

    def test_send_dm_safe_missing_only_when_no_survivor(self) -> None:
        device = MagicMock()
        composer = MagicMock()
        with (
            patch.object(config, "ENABLE_REAL_DM_SEND", True),
            patch.object(nav, "_dm_find_focus_composer", return_value=composer),
            patch.object(nav, "read_dm_composer_text", return_value="Salut"),
            patch.object(
                nav,
                "wait_for_dm_send_button_after_draft",
                return_value=(
                    None,
                    "missing",
                    {"send_button_candidate_count": 0, "filtered_send_candidates": []},
                ),
            ),
            patch.object(nav, "screenshot"),
            patch.object(device, "dump_hierarchy", return_value="<hierarchy/>"),
            patch.object(nav, "_dm_send_button_debug_artifacts", return_value={}),
            patch.object(nav, "_dm_screen_size_for_dm", return_value=(1080, 1920)),
            patch.object(nav, "_dm_composer_bounds_u2", return_value={}),
        ):
            out = nav.send_dm_safe(
                device,
                "user",
                "Salut",
                "empty_new_thread",
            )
        self.assertEqual(out.get("reason"), "send_button_missing")

    def test_perform_real_send_reuses_existing_draft(self) -> None:
        device = MagicMock()
        with (
            patch.object(
                dm_sender_engine,
                "verify_welcome_dm_thread_recipient_exact",
                return_value=(True, "exact_thread_header", "user"),
            ),
            patch.object(dm_sender_engine, "dm_thread_shows_outgoing_message", return_value=False),
            patch.object(dm_sender_engine, "read_dm_composer_text", return_value="Salut et bienvenue"),
            patch.object(dm_sender_engine, "verify_dm_composer_safe", return_value=(True, "ok")),
            patch.object(dm_sender_engine, "_resolve_dm_text_composer", return_value=(MagicMock(), None)),
            patch.object(dm_sender_engine, "type_dm_draft_only") as type_mock,
            patch.object(dm_sender_engine, "verify_dm_draft_text", return_value=True),
            patch.object(
                dm_sender_engine,
                "send_dm_safe",
                return_value={"sent": True, "reason": None},
            ),
        ):
            ok, _out, reason = dm_sender_engine._perform_real_welcome_dm_send(
                device,
                username="user",
                message_body="Salut et bienvenue",
                thread_state="empty_new_thread",
                pkg="com.instagram.androif",
            )
        type_mock.assert_not_called()
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_perform_real_send_prevents_duplicate_when_message_in_thread(self) -> None:
        device = MagicMock()
        with (
            patch.object(
                dm_sender_engine,
                "verify_welcome_dm_thread_recipient_exact",
                return_value=(True, "exact_thread_header", "user"),
            ),
            patch.object(dm_sender_engine, "dm_thread_shows_outgoing_message", return_value=True),
            patch.object(dm_sender_engine, "send_dm_safe") as send_mock,
        ):
            ok, out, reason = dm_sender_engine._perform_real_welcome_dm_send(
                device,
                username="user",
                message_body="Salut et bienvenue",
                thread_state="empty_new_thread",
                pkg="com.instagram.androif",
            )
        send_mock.assert_not_called()
        self.assertTrue(ok)
        self.assertTrue(out.get("duplicate_prevented"))
        self.assertIsNone(reason)

    def test_outreach_confirmed_send_uses_fast_finalize(self) -> None:
        device = MagicMock()
        composer = MagicMock()
        with (
            patch.object(dm_sender_engine, "dm_thread_shows_outgoing_message", return_value=False),
            patch.object(dm_sender_engine, "read_dm_composer_text", side_effect=["", "Salut outreach"]),
            patch.object(dm_sender_engine, "verify_dm_composer_safe", return_value=(True, "ok")),
            patch.object(dm_sender_engine, "_resolve_dm_text_composer", return_value=(composer, None)),
            patch.object(dm_sender_engine, "type_dm_draft_only", return_value=(True, {"method": "set_text"})),
            patch.object(dm_sender_engine, "verify_dm_draft_text", return_value=True),
            patch.object(
                dm_sender_engine,
                "send_dm_safe",
                return_value={
                    "sent": True,
                    "post_send_signal_reason": "composer_text_shortened",
                    "composer_text_len_before_send": 15,
                },
            ),
            patch.object(dm_sender_engine, "finalize_after_real_send") as conservative_finalize,
            patch.object(dm_sender_engine, "return_to_profile_from_dm", return_value=True) as back_mock,
        ):
            ok, out, reason = dm_sender_engine._perform_real_welcome_dm_send(
                device,
                username="user",
                message_body="Salut outreach",
                thread_state="empty_new_thread",
                pkg="com.instagram.androif",
                dm_type="outreach",
            )

        self.assertTrue(ok)
        self.assertIsNone(reason)
        conservative_finalize.assert_not_called()
        back_mock.assert_called_once()
        self.assertTrue(out["post_finalize"]["post_send_fast_finalize_used"])

    def test_outreach_unconfirmed_signal_uses_conservative_finalize(self) -> None:
        device = MagicMock()
        composer = MagicMock()
        with (
            patch.object(dm_sender_engine, "dm_thread_shows_outgoing_message", return_value=False),
            patch.object(dm_sender_engine, "read_dm_composer_text", side_effect=["", "Salut outreach"]),
            patch.object(dm_sender_engine, "verify_dm_composer_safe", return_value=(True, "ok")),
            patch.object(dm_sender_engine, "_resolve_dm_text_composer", return_value=(composer, None)),
            patch.object(dm_sender_engine, "type_dm_draft_only", return_value=(True, {"method": "set_text"})),
            patch.object(dm_sender_engine, "verify_dm_draft_text", return_value=True),
            patch.object(
                dm_sender_engine,
                "send_dm_safe",
                return_value={
                    "sent": True,
                    "post_send_signal_reason": "text_marker:Sent",
                    "composer_text_len_before_send": 15,
                },
            ),
            patch.object(
                dm_sender_engine,
                "finalize_after_real_send",
                return_value={"back_to_profile_ok": True, "post_send_fast_finalize_used": False},
            ) as conservative_finalize,
            patch.object(dm_sender_engine, "return_to_profile_from_dm") as back_mock,
        ):
            ok, out, reason = dm_sender_engine._perform_real_welcome_dm_send(
                device,
                username="user",
                message_body="Salut outreach",
                thread_state="empty_new_thread",
                pkg="com.instagram.androif",
                dm_type="outreach",
            )

        self.assertTrue(ok)
        self.assertIsNone(reason)
        conservative_finalize.assert_called_once()
        back_mock.assert_not_called()
        self.assertFalse(out["post_finalize"]["post_send_fast_finalize_used"])

    def test_composer_resolve_fast_path_skips_non_text_audit_for_outreach_snapshot(self) -> None:
        device = MagicMock()
        composer = MagicMock()
        composer.info = {"bounds": {"left": 1, "top": 2, "right": 3, "bottom": 4}}
        snapshot = {
            "composer_visible": True,
            "composer_signal": "resource_id_exact_composer_pkg+hint_message_keyword",
        }
        with (
            patch.object(dm_sender_engine, "_check_dm_sender_permission_blocker", return_value=False),
            patch.object(dm_sender_engine, "_dm_audit_non_text_action_candidates") as audit_mock,
            patch.object(dm_sender_engine, "_dm_find_focus_composer", return_value=composer),
        ):
            resolved, reason = dm_sender_engine._resolve_dm_text_composer(
                device,
                pkg="com.instagram.androif",
                username="user",
                caller="real_send",
                dm_type="outreach",
                thread_state="empty_new_thread",
                thread_snapshot=snapshot,
            )

        self.assertIs(resolved, composer)
        self.assertIsNone(reason)
        audit_mock.assert_not_called()

    def test_composer_resolve_fast_path_fallback_audits_without_snapshot(self) -> None:
        device = MagicMock()
        composer = MagicMock()
        composer.info = {"bounds": {"left": 1, "top": 2, "right": 3, "bottom": 4}}
        with (
            patch.object(dm_sender_engine, "_check_dm_sender_permission_blocker", return_value=False),
            patch.object(dm_sender_engine, "_dm_audit_non_text_action_candidates") as audit_mock,
            patch.object(dm_sender_engine, "_dm_find_focus_composer", return_value=composer),
        ):
            resolved, reason = dm_sender_engine._resolve_dm_text_composer(
                device,
                pkg="com.instagram.androif",
                username="user",
                caller="real_send",
                dm_type="outreach",
                thread_state="empty_new_thread",
                thread_snapshot={},
            )

        self.assertIs(resolved, composer)
        self.assertIsNone(reason)
        audit_mock.assert_called_once()

    def test_single_prepared_job_skips_post_job_restore(self) -> None:
        device = MagicMock()
        job = {
            "id": "job-1",
            "recipient_username": "user",
            "dm_type": "welcome",
            "message_body": "Salut",
        }
        with (
            patch.object(dm_sender_engine.supabase_client, "mark_dm_job_running", return_value=True),
            patch.object(
                dm_sender_engine,
                "_navigate_to_recipient_dm_thread",
                return_value=("empty_new_thread", True),
            ),
            patch.object(dm_sender_engine, "_evaluate_welcome_sendability", return_value=(True, None)),
            patch.object(
                dm_sender_engine,
                "_perform_real_welcome_dm_send",
                return_value=(False, {"reason": "send_button_missing"}, "send_button_missing"),
            ),
            patch.object(
                dm_sender_engine,
                "_complete_job_failed_retry",
                return_value=(job, "failed_retry"),
            ),
            patch.object(dm_sender_engine, "_safe_teardown_navigation") as teardown_mock,
        ):
            result = dm_sender_engine.execute_dm_job_real_send(
                device,
                job,
                settings={},
                account_id="acct",
                skip_post_job_restore=True,
                jobs_total=1,
            )
        teardown_mock.assert_not_called()
        self.assertEqual(
            result.get("post_job_restore_final_mode"),
            "skipped_send_one",
        )

    def test_final_outreach_job_skips_restore_without_restore_failed(self) -> None:
        device = MagicMock()
        job = {
            "id": "job-1",
            "recipient_username": "user",
            "dm_type": "outreach",
            "message_body": "Salut",
        }
        with (
            patch.object(dm_sender_engine.supabase_client, "mark_dm_job_running", return_value=True),
            patch.object(
                dm_sender_engine,
                "_navigate_to_recipient_dm_thread",
                return_value=("empty_new_thread", True),
            ),
            patch.object(dm_sender_engine, "_evaluate_outreach_sendability", return_value=(True, None)),
            patch.object(
                dm_sender_engine,
                "_perform_real_welcome_dm_send",
                return_value=(True, {"sent": True}, None),
            ),
            patch.object(
                dm_sender_engine.supabase_client,
                "complete_dm_job",
                return_value={**job, "status": "sent"},
            ),
            patch.object(dm_sender_engine, "_safe_teardown_navigation") as teardown_mock,
        ):
            result = dm_sender_engine.execute_dm_job_real_send(
                device,
                job,
                settings={},
                account_id="acct",
                restore_search_after_job=False,
                jobs_total=2,
                job_index=1,
            )

        teardown_mock.assert_not_called()
        self.assertEqual(result.get("outcome"), "sent")
        self.assertEqual(result.get("post_job_restore_final_mode"), "skipped_final_job")
        self.assertEqual(
            result.get("post_job_restore_final_reason"),
            "final_job_no_restore_required",
        )
        self.assertNotEqual(result.get("post_job_restore_final_mode"), "restore_failed")

    def test_outreach_post_job_restore_skips_followers_list_probes(self) -> None:
        device = MagicMock()
        search_edit = MagicMock()
        search_edit.get_text.return_value = ""
        logs: list[tuple[str, str, dict]] = []

        with (
            patch.object(dm_sender_engine, "is_followers_list_surface_quick") as followers_probe,
            patch.object(dm_sender_engine, "_check_dm_sender_permission_blocker", return_value=False),
            patch.object(dm_sender_engine, "is_lightweight_search_screen", return_value=False),
            patch.object(dm_sender_engine, "is_dm_thread_screen", return_value=True),
            patch.object(dm_sender_engine, "_dm_sender_composer_visible_quick", return_value=True),
            patch.object(
                dm_sender_engine,
                "tap_instagram_action_bar_back_button",
                return_value=(True, "description_Back"),
            ),
            patch.object(dm_sender_engine, "verify_profile", return_value=True),
            patch.object(dm_sender_engine, "return_to_search_from_profile", return_value=True),
            patch.object(dm_sender_engine, "_wait_search_edittext", return_value=search_edit),
            patch.object(dm_sender_engine, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))),
        ):
            ok = dm_sender_engine.prepare_dm_sender_global_search_surface(
                device,
                account_username="acct",
                context="dm_sender_post_job",
                last_recipient_username="recipient",
                prefer_back_stack_to_search=True,
            )

        self.assertTrue(ok)
        followers_probe.assert_not_called()
        skipped_logs = [
            fields
            for _level, event, fields in logs
            if event == "dm_sender_post_job_followers_probe_skipped_outreach_restore"
        ]
        quick_false_logs = [
            fields
            for _level, event, fields in logs
            if event == "dm_sender_post_job_followers_probe_quick_false"
        ]
        self.assertEqual([item.get("phase") for item in skipped_logs], ["pre_exit", "post_exit"])
        self.assertTrue(all(item.get("prefer_back_stack_to_search") for item in skipped_logs))
        self.assertEqual(quick_false_logs, [])


if __name__ == "__main__":
    unittest.main()
