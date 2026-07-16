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


class PreFollowTapContextTest(unittest.TestCase):
    def _fresh_public_context(self) -> dict:
        return nav.build_pre_follow_tap_context(
            follower_username="public_user",
            source_profile_username="healthup.sw",
            visual_candidate_id="vc-1",
            screen_guard={
                "ok": True,
                "follow_header_state": "follow",
                "navigation_state": "CANDIDATE_PROFILE",
                "action_bar_title": "public_user",
                "fast_path": True,
            },
            private_gate={
                "reject": False,
                "private_profile_detected": False,
                "probe_ms": 3333.0,
                "probe_reused": False,
                "reason": "private_not_detected",
                "private_probe_payload": {
                    "private_profile_detected": False,
                    "detection_method": "none",
                    "confidence": 0.0,
                    "probe_ms": 3333.0,
                    "hierarchy_fallback_used": True,
                },
            },
        )

    def test_reusable_context_requires_fresh_private_probe(self) -> None:
        ctx = self._fresh_public_context()
        self.assertTrue(
            nav._is_reusable_pre_follow_tap_context(
                ctx,
                follower_username="public_user",
                source_profile_username="healthup.sw",
            )
        )

    def test_screen_guard_dict_not_reusable_as_context_private_probe(self) -> None:
        ctx = nav.build_pre_follow_tap_context(
            follower_username="dazeone0001",
            source_profile_username="cafecuba_geneve",
            visual_candidate_id="vc-private",
            screen_guard={
                "ok": True,
                "follow_header_state": "follow",
                "action_bar_title": "dazeone0001",
            },
            private_gate={
                "reject": False,
                "private_profile_detected": False,
                "probe_ms": 0.0,
                "probe_reused": True,
                "private_probe_payload": {
                    "ok": True,
                    "follow_header_state": "follow",
                    "action_bar_title": "dazeone0001",
                },
            },
        )
        self.assertFalse(
            nav._is_reusable_pre_follow_tap_context(
                ctx,
                follower_username="dazeone0001",
                source_profile_username="cafecuba_geneve",
            )
        )


class PreFollowObservationProofTest(unittest.TestCase):
    def _proof(self, **overrides) -> dict:
        values = {
            "follower_username": "public_user",
            "source_profile_username": "healthup.sw",
            "visual_candidate_id": "vc-1",
            "action_bar_title": "public_user",
            "navigation_state": "CANDIDATE_PROFILE",
            "navigation_confidence": 0.88,
            "follow_header_state": "follow",
            "private_probe_payload": {
                "private_profile_detected": False,
                "detection_method": "none",
                "confidence": 0.0,
                "probe_ms": 8.0,
                "hierarchy_fallback_used": True,
            },
            "navigation_token": "nav-1",
        }
        values.update(overrides)
        return nav.build_pre_follow_observation_proof(**values)

    def _reason(self, proof: dict, *, username: str = "public_user", token: str = "nav-1") -> str:
        return nav._pre_follow_observation_proof_reuse_block_reason(
            proof,
            follower_username=username,
            source_profile_username="healthup.sw",
            navigation_token=token,
        )

    def test_fresh_exact_follow_proof_is_reusable(self) -> None:
        self.assertEqual(self._reason(self._proof()), "")

    def test_private_profile_is_never_reusable(self) -> None:
        proof = self._proof(
            private_probe_payload={
                "private_profile_detected": True,
                "detection_method": "ui_text_private",
                "probe_ms": 8.0,
            }
        )
        self.assertEqual(self._reason(proof), "private_profile_detected")

    def test_requested_or_following_contradiction_uses_full_path(self) -> None:
        for state in ("requested", "following"):
            with self.subTest(state=state):
                self.assertEqual(
                    self._reason(self._proof(follow_header_state=state)),
                    "follow_state_contradiction",
                )

    def test_wrong_username_is_rejected(self) -> None:
        self.assertEqual(
            self._reason(self._proof(), username="other_user"),
            "candidate_username_mismatch",
        )

    def test_navigation_intervened_invalidates_proof(self) -> None:
        self.assertEqual(
            self._reason(self._proof(), token="nav-2"),
            "navigation_intervened",
        )

    def test_ttl_expired_uses_full_path(self) -> None:
        proof = self._proof(captured_at_mono=nav.time.monotonic() - 12.01)
        self.assertEqual(self._reason(proof), "proof_ttl_expired")

    def test_screen_guard_reuses_fresh_proof_without_redundant_reads(self) -> None:
        device = MagicMock()
        proof = self._proof()
        with patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="public_user"
        ) as username_read, patch.object(
            nav, "_follow_ui_state_snapshot"
        ) as header_read, patch.object(
            nav, "_visual_raw_follow_invite_visible_quick"
        ) as invite_read, patch.object(
            nav, "detect_followers_list_screen_fresh"
        ) as list_read:
            out = nav.visual_candidate_follow_pre_follow_screen_guard(
                device,
                source_profile_username="healthup.sw",
                pkg="com.instagram.android",
                pick={"visual_candidate_id": "vc-1"},
                profile_already_open=True,
                defer_private_gate=True,
                pre_follow_observation_proof=proof,
                navigation_token="nav-1",
                follower_username="public_user",
            )

        self.assertTrue(out["ok"])
        self.assertTrue(out["proof_reused"])
        username_read.assert_called_once()
        header_read.assert_not_called()
        invite_read.assert_not_called()
        list_read.assert_not_called()

    def test_expired_proof_runs_existing_screen_guard_reads(self) -> None:
        device = MagicMock()
        device.return_value.exists.return_value = False
        proof = self._proof(captured_at_mono=nav.time.monotonic() - 12.01)
        with patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="public_user"
        ) as username_read, patch.object(
            nav, "_follow_ui_state_snapshot", return_value="follow"
        ) as header_read, patch.object(
            nav, "_visual_raw_follow_invite_visible_quick", return_value=True
        ) as invite_read:
            out = nav.visual_candidate_follow_pre_follow_screen_guard(
                device,
                source_profile_username="healthup.sw",
                pkg="com.instagram.android",
                pick={"visual_candidate_id": "vc-1"},
                profile_already_open=True,
                defer_private_gate=True,
                pre_follow_observation_proof=proof,
                navigation_token="nav-1",
                follower_username="public_user",
            )

        self.assertTrue(out["ok"])
        self.assertFalse(out.get("proof_reused", False))
        username_read.assert_called_once()
        header_read.assert_called_once()
        invite_read.assert_called_once()


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

    def test_reusable_context_skips_terminal_private_probe(self) -> None:
        device = MagicMock()
        btn = self._mock_follow_button()
        ctx = nav.build_pre_follow_tap_context(
            follower_username="public_user",
            source_profile_username="healthup.sw",
            visual_candidate_id="vc-1",
            screen_guard={
                "ok": True,
                "follow_header_state": "follow",
                "navigation_state": "CANDIDATE_PROFILE",
                "action_bar_title": "public_user",
            },
            private_gate={
                "reject": False,
                "private_profile_detected": False,
                "probe_ms": 3010.0,
                "probe_reused": False,
                "private_probe_payload": {
                    "private_profile_detected": False,
                    "detection_method": "none",
                    "confidence": 0.0,
                    "probe_ms": 3010.0,
                    "hierarchy_fallback_used": True,
                },
            },
        )
        with patch.object(
            nav,
            "_follow_ui_state_snapshot",
            side_effect=["following"],
        ) as mock_snap, patch(
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
        ) as mock_detect, patch.object(
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
                source_profile_username="healthup.sw",
                pre_follow_context=ctx,
            )

        mock_detect.assert_not_called()
        self.assertNotEqual(
            out.get("visual_follow_failure_reason"),
            "follow_blocked_private_account",
        )
        btn.click.assert_called()

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

    def test_exact_selector_absent_runs_full_detection_but_returns_no_tap_target(self) -> None:
        import follow_action_engine as engine

        device = MagicMock()
        device.window_size.return_value = (1080, 1920)
        context = PreFollowTapContextTest()._fresh_public_context()
        soft_candidate = MagicMock()
        with patch.object(nav, "verify_app_foreground", return_value=True), patch.object(
            nav, "_visual_raw_follow_invite_visible_quick", return_value=True
        ), patch.object(
            engine, "try_select_exact_profile_header_follow_fast", return_value=(None, {})
        ), patch.object(
            engine,
            "detect_follow_action_surface",
            return_value={
                "follow_available": True,
                "follow_state": "follow",
                "confidence": 0.9,
                "reason": "follow_control_ready_soft",
                "signals": {"screen_class": "profile_like"},
                "candidate_buttons": [],
                "best_candidate": {"source": "hybrid_soft"},
                "follow_control_element": soft_candidate,
                "exact_follow_fast_path": False,
            },
        ) as full_detect:
            element, meta = engine.follow_action_surface_wait_and_select_element(
                device,
                "public_user",
                "com.instagram.android",
                visual_candidate_id="vc-1",
                source_profile_username="healthup.sw",
                initial_ui_state="follow",
                pre_follow_context=context,
            )

        self.assertIsNone(element)
        self.assertFalse(meta["safe_to_tap"])
        self.assertEqual(
            meta["visual_follow_failure_reason"],
            "profile_proof_exact_follow_control_absent",
        )
        full_detect.assert_called_once()

    def test_requested_contradiction_after_exact_probe_uses_full_detection(self) -> None:
        import follow_action_engine as engine

        device = MagicMock()
        device.window_size.return_value = (1080, 1920)
        context = PreFollowTapContextTest()._fresh_public_context()
        with patch.object(nav, "verify_app_foreground", return_value=True), patch.object(
            nav, "_visual_raw_follow_invite_visible_quick", return_value=False
        ), patch.object(
            engine, "try_select_exact_profile_header_follow_fast", return_value=(None, {})
        ), patch.object(
            engine,
            "detect_follow_action_surface",
            return_value={
                "follow_available": False,
                "follow_state": "requested",
                "confidence": 0.95,
                "reason": "requested",
                "signals": {"screen_class": "profile_like"},
                "candidate_buttons": [],
                "follow_control_element": None,
                "exact_follow_fast_path": False,
            },
        ) as full_detect:
            element, meta = engine.follow_action_surface_wait_and_select_element(
                device,
                "public_user",
                "com.instagram.android",
                visual_candidate_id="vc-1",
                source_profile_username="healthup.sw",
                initial_ui_state="follow",
                pre_follow_context=context,
            )

        self.assertIsNone(element)
        self.assertTrue(meta["already_following"])
        self.assertEqual(meta["ui_state"], "requested")
        full_detect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
