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


if __name__ == "__main__":
    unittest.main()
