from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import instagram_navigation as nav
import runner


class PreFollowPrivateGateTest(unittest.TestCase):
    def test_rejects_private_when_skip_setting_on(self) -> None:
        device = MagicMock()
        with patch.object(
            nav,
            "visual_detect_private_profile",
            return_value={
                "private_profile_detected": True,
                "detection_method": "ui_textContains_this_account_private_en",
                "confidence": 0.93,
                "probe_ms": 12.5,
                "hierarchy_fallback_used": False,
            },
        ):
            out = nav.visual_candidate_pre_follow_private_gate(
                device,
                source_profile_username="healthup.sw",
                dont_follow_private_accounts=True,
                follower_username="h.a.s_al",
                visual_candidate_id="xml_list:has_al",
            )
        self.assertTrue(out["reject"])
        self.assertEqual(out["reason"], "candidate_rejected_private_account")
        self.assertTrue(out["private_profile_detected"])
        device.assert_not_called()

    def test_allows_private_when_setting_off(self) -> None:
        device = MagicMock()
        with patch.object(
            nav,
            "visual_detect_private_profile",
        ) as mock_detect:
            out = nav.visual_candidate_pre_follow_private_gate(
                device,
                dont_follow_private_accounts=False,
                follower_username="h.a.s_al",
            )
        self.assertFalse(out["reject"])
        mock_detect.assert_not_called()

    def test_passes_public_profile(self) -> None:
        device = MagicMock()
        with patch.object(
            nav,
            "visual_detect_private_profile",
            return_value={
                "private_profile_detected": False,
                "detection_method": "none",
                "confidence": 0.0,
                "probe_ms": 8.0,
                "hierarchy_fallback_used": True,
            },
        ):
            out = nav.visual_candidate_pre_follow_private_gate(
                device,
                dont_follow_private_accounts=True,
                follower_username="public_user",
            )
        self.assertFalse(out["reject"])
        self.assertEqual(out["reason"], "private_not_detected")

    def test_reuses_prior_private_probe_without_second_device_probe(self) -> None:
        device = MagicMock()
        with patch.object(nav, "visual_detect_private_profile") as mock_detect:
            out = nav.visual_candidate_pre_follow_private_gate(
                device,
                dont_follow_private_accounts=True,
                follower_username="public_user",
                prior_private_probe={
                    "private_profile_detected": False,
                    "detection_method": "cached_guard_probe",
                    "confidence": 0.0,
                    "probe_ms": 3010.0,
                    "hierarchy_fallback_used": True,
                },
            )

        mock_detect.assert_not_called()
        self.assertFalse(out["reject"])
        self.assertTrue(out["probe_reused"])
        self.assertEqual(out["detection_method"], "cached_guard_probe")

    def test_screen_guard_dict_not_reused_as_private_probe(self) -> None:
        """Regression: screen-guard metadata must not short-circuit private detection."""
        device = MagicMock()
        screen_guard_payload = {
            "ok": True,
            "reason": "candidate_profile_surface_ok_fast_path",
            "fast_path": True,
            "follow_header_state": "follow",
            "action_bar_title": "dazeone0001",
            "navigation_state": "CANDIDATE_PROFILE",
        }
        with patch.object(
            nav,
            "visual_detect_private_profile",
            return_value={
                "private_profile_detected": True,
                "detection_method": "ui_textContains_this_account_private_en",
                "confidence": 0.93,
                "probe_ms": 15.0,
                "hierarchy_fallback_used": False,
            },
        ) as mock_detect:
            out = nav.visual_candidate_pre_follow_private_gate(
                device,
                dont_follow_private_accounts=True,
                follower_username="dazeone0001",
                prior_private_probe=screen_guard_payload,
            )

        mock_detect.assert_called_once_with(device, source_profile_username=None)
        self.assertFalse(out.get("probe_reused"))
        self.assertTrue(out["reject"])
        self.assertEqual(out["reason"], "candidate_rejected_private_account")

    def test_follow_visible_with_private_signal_blocks(self) -> None:
        device = MagicMock()
        with patch.object(
            nav,
            "visual_detect_private_profile",
            return_value={
                "private_profile_detected": True,
                "detection_method": "hierarchy:this_account_private",
                "confidence": 0.79,
                "probe_ms": 22.0,
                "hierarchy_fallback_used": True,
            },
        ):
            out = nav.visual_candidate_pre_follow_private_gate(
                device,
                dont_follow_private_accounts=True,
                follower_username="private_but_follow_cta",
            )
        self.assertTrue(out["reject"])
        self.assertEqual(out["reason"], "candidate_rejected_private_account")

    def test_screen_guard_fast_path_skips_observe_when_profile_already_open(self) -> None:
        device = MagicMock()
        list_row = MagicMock()
        list_row.exists.return_value = False
        device.return_value = list_row
        with patch.object(
            nav,
            "read_current_profile_username_for_follow_gate",
            return_value="candidate_user",
        ), patch(
            "navigation_engine.observe_instagram_state",
        ) as mock_observe, patch.object(
            nav,
            "detect_followers_list_screen_fresh",
        ) as mock_fresh, patch.object(
            nav,
            "_follow_ui_state_snapshot",
            return_value="follow",
        ), patch.object(
            nav,
            "_visual_raw_follow_invite_visible_quick",
            return_value=True,
        ):
            out = nav.visual_candidate_follow_pre_follow_screen_guard(
                device,
                source_profile_username="healthup.sw",
                pkg="com.instagram.android",
                pick={
                    "visual_candidate_id": "vc-1",
                    "resolved_username_hint": "candidate_user",
                },
                dont_follow_private_accounts=True,
                profile_already_open=True,
                defer_private_gate=True,
            )
        self.assertTrue(out["ok"])
        self.assertTrue(out.get("fast_path"))
        mock_observe.assert_not_called()
        mock_fresh.assert_not_called()

    def test_screen_guard_blocks_private_before_follow_surface_ok(self) -> None:
        device = MagicMock()
        with patch.object(
            nav,
            "read_current_profile_username_for_follow_gate",
            return_value="candidate_user",
        ), patch(
            "navigation_engine.observe_instagram_state",
            return_value={
                "state": "CANDIDATE_PROFILE",
                "confidence": 0.9,
                "reason": "ok",
            },
        ), patch.object(
            nav,
            "detect_followers_list_screen_fresh",
            return_value=({"is_followers_list": False}, "<xml/>"),
        ), patch.object(
            nav,
            "_follow_ui_state_snapshot",
            return_value="follow",
        ), patch.object(
            nav,
            "_visual_raw_follow_invite_visible_quick",
            return_value=True,
        ), patch.object(
            nav,
            "visual_candidate_pre_follow_private_gate",
            return_value={
                "reject": True,
                "reason": "private_account",
                "private_profile_detected": True,
                "private_gate_reason": "private_account",
            },
        ):
            out = nav.visual_candidate_follow_pre_follow_screen_guard(
                device,
                source_profile_username="healthup.sw",
                pkg="com.instagram.android",
                pick={"visual_candidate_id": "vc-1"},
                dont_follow_private_accounts=True,
            )
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "private_account")
        self.assertTrue(out["private_profile_detected"])


class CandidateFollowDecisionTest(unittest.TestCase):
    def test_private_detected_in_trace_blocks_final_follow(self) -> None:
        decision = runner._new_candidate_follow_decision(
            follower_username="private_user",
            visual_candidate_id="xml_list:private_user",
        )
        runner._candidate_follow_decision_apply_source(
            decision,
            {
                "candidate_allowed_to_follow": False,
                "blocked_reason": "private_profile_follow_private_accounts_disabled",
                "private_detected": True,
                "source_of_decision": "candidate_profile_analysis",
            },
            source="candidate_profile_analysis",
        )

        self.assertTrue(runner._candidate_follow_decision_blocks_follow(decision))
        self.assertEqual(decision["blocked_reason"], "private_account")
        self.assertTrue(decision["private_detected"])

    def test_private_detected_in_screen_guard_blocks_final_follow(self) -> None:
        decision = runner._new_candidate_follow_decision(follower_username="private_user")
        runner._candidate_follow_decision_apply_source(
            decision,
            {
                "candidate_allowed_to_follow": False,
                "blocked_reason": "private_account",
                "private_detected": True,
                "source_of_decision": "pre_follow_screen_guard_private",
            },
            source="pre_follow_screen_guard_private",
        )

        self.assertTrue(runner._candidate_follow_decision_blocks_follow(decision))
        self.assertEqual(decision["source_of_decision"], "pre_follow_screen_guard_private")

    def test_private_detected_at_final_gate_blocks_final_follow(self) -> None:
        decision = runner._new_candidate_follow_decision(follower_username="private_user")
        runner._candidate_follow_decision_block(
            decision,
            reason="private_account",
            source="pre_follow_private_gate",
            private_detected=True,
        )

        self.assertTrue(runner._candidate_follow_decision_blocks_follow(decision))
        self.assertEqual(decision["blocked_reason"], "private_account")

    def test_public_candidate_remains_allowed(self) -> None:
        decision = runner._new_candidate_follow_decision(follower_username="public_user")

        self.assertFalse(runner._candidate_follow_decision_blocks_follow(decision))
        self.assertTrue(decision["candidate_allowed_to_follow"])

    def test_should_follow_false_from_any_source_hard_stops(self) -> None:
        decision = runner._new_candidate_follow_decision(follower_username="candidate")
        runner._candidate_follow_decision_apply_source(
            decision,
            {
                "candidate_allowed_to_follow": False,
                "blocked_reason": "follow_header_not_invite:message",
                "private_detected": False,
                "source_of_decision": "pre_follow_header",
            },
            source="pre_follow_header",
        )

        self.assertTrue(runner._candidate_follow_decision_blocks_follow(decision))
        self.assertEqual(decision["blocked_reason"], "follow_header_not_invite:message")


class PerformFollowSafePrivateGateTest(unittest.TestCase):
    def _mock_follow_button(self) -> MagicMock:
        btn = MagicMock()
        btn.click = MagicMock()
        return btn

    def test_private_detected_before_tap_blocks_without_follow_tap_sent(self) -> None:
        device = MagicMock()
        device.click = MagicMock()
        btn = self._mock_follow_button()
        with patch.object(nav, "_follow_ui_state_snapshot", return_value="follow"), patch(
            "follow_action_engine.follow_action_surface_wait_and_select_element",
            return_value=(
                btn,
                {
                    "events": [],
                    "exact_follow_fast_path": True,
                    "last_ui_state": "follow",
                },
            ),
        ), patch.object(
            nav,
            "visual_detect_private_profile",
            return_value={
                "private_profile_detected": True,
                "detection_method": "ui_textContains_this_account_private_en",
                "confidence": 0.93,
                "probe_ms": 10.0,
                "hierarchy_fallback_used": False,
            },
        ), patch.object(nav, "_follow_safe_info", return_value={"bounds": {"left": 1, "top": 2, "right": 3, "bottom": 4}}):
            out = nav.perform_follow_safe(
                device,
                "private_user",
                "com.instagram.android",
                profile_already_open=True,
                dont_follow_private_accounts=True,
            )

        self.assertFalse(out["ok"])
        self.assertFalse(out["tapped"])
        self.assertEqual(out["visual_follow_failure_reason"], "follow_blocked_private_account")
        device.click.assert_not_called()
        btn.click.assert_not_called()
        event_names = [e[0] for e in out.get("events") or [] if isinstance(e, (list, tuple))]
        self.assertNotIn("follow_tap_sent", event_names)

    def test_public_profile_allows_follow_path_to_continue(self) -> None:
        device = MagicMock()
        btn = self._mock_follow_button()
        with patch.object(
            nav,
            "_follow_ui_state_snapshot",
            side_effect=["follow", "following"],
        ), patch(
            "follow_action_engine.follow_action_surface_wait_and_select_element",
            return_value=(
                btn,
                {
                    "events": [],
                    "exact_follow_fast_path": False,
                    "last_ui_state": "follow",
                },
            ),
        ), patch.object(
            nav,
            "visual_detect_private_profile",
            return_value={
                "private_profile_detected": False,
                "detection_method": "none",
                "confidence": 0.0,
                "probe_ms": 8.0,
                "hierarchy_fallback_used": True,
            },
        ), patch.object(
            nav,
            "_try_review_before_follow_popup_confirm",
            return_value=False,
        ), patch.object(
            nav,
            "_review_before_follow_popup_visible",
            return_value=False,
        ), patch("instagram_navigation.time.sleep"):
            out = nav.perform_follow_safe(
                device,
                "public_user",
                "com.instagram.android",
                profile_already_open=True,
                dont_follow_private_accounts=True,
            )

        self.assertNotEqual(
            out.get("visual_follow_failure_reason"),
            "follow_blocked_private_account",
        )
        btn.click.assert_called()

    def test_already_following_skips_private_terminal_gate(self) -> None:
        device = MagicMock()
        with patch.object(nav, "_follow_ui_state_snapshot", return_value="following"), patch.object(
            nav,
            "visual_detect_private_profile",
        ) as mock_detect:
            out = nav.perform_follow_safe(
                device,
                "already_following_user",
                "com.instagram.android",
                profile_already_open=True,
                dont_follow_private_accounts=True,
            )

        mock_detect.assert_not_called()
        self.assertTrue(out["ok"])
        self.assertTrue(out.get("skipped_tap"))

    def test_exact_fast_path_does_not_bypass_terminal_private_gate(self) -> None:
        device = MagicMock()
        device.click = MagicMock()
        btn = self._mock_follow_button()
        with patch.object(nav, "_follow_ui_state_snapshot", return_value="follow"), patch(
            "follow_action_engine.follow_action_surface_wait_and_select_element",
            return_value=(
                btn,
                {
                    "events": [],
                    "exact_follow_fast_path": True,
                    "last_ui_state": "follow",
                },
            ),
        ), patch.object(
            nav,
            "visual_detect_private_profile",
            return_value={
                "private_profile_detected": True,
                "detection_method": "hierarchy:this_account_private",
                "confidence": 0.79,
                "probe_ms": 12.0,
                "hierarchy_fallback_used": True,
            },
        ), patch.object(nav, "_follow_safe_info", return_value={"bounds": {"left": 10, "top": 20, "right": 110, "bottom": 70}}):
            out = nav.perform_follow_safe(
                device,
                "private_user",
                "com.instagram.android",
                profile_already_open=True,
                dont_follow_private_accounts=True,
            )

        self.assertFalse(out["ok"])
        self.assertEqual(out["failure_code"], 35)
        device.click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
