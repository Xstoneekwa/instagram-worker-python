from __future__ import annotations

import time
import unittest
from contextlib import ExitStack
from unittest import mock

import instagram_navigation as nav
from tests.test_post_follow_like_samsung_fast import (
    _like_phase_contract_ctx,
    _patch_like_phase_common,
)


_DETERMINISTIC_MONOTONIC_BASE = 100.0


def _fresh_stage_provenance(
    *,
    now_monotonic: float = _DETERMINISTIC_MONOTONIC_BASE,
    **overrides: object,
) -> dict[str, object]:
    now = float(now_monotonic)
    base: dict[str, object] = {
        "evidence_kind": "fresh_post_cell_proof_bounds",
        "candidate_username": "cand",
        "source_profile_username": "ct",
        "visual_candidate_id": "vc-1",
        "post_bounds": {"left": 0, "top": 900, "right": 360, "bottom": 1260},
        "package": "com.instagram.android",
        "activity": "com.instagram.mainactivity.InstagramMainActivity",
        "created_at_monotonic": now - 2.6,
        "cell_proof_valid_at_tap": True,
        "cell_proof_age_at_tap_ms": 900.0,
        "cell_proof_ttl_ms": 1250.0,
        "tap_dispatched_at_monotonic": now - 1.8,
        "max_tap_to_audit_ms": 3000.0,
        "snapshot_captured_at_monotonic": now - 0.08,
        "same_stage_no_navigation_before_tap": True,
        "navigation_generation_before": "nav-1",
        "navigation_generation_at_tap": "nav-1",
        "scroll_generation_before": "scroll-1",
        "scroll_generation_at_tap": "scroll-1",
        "invalidated": False,
    }
    base.update(overrides)
    return base


class PostViewerStoryHighlightIdentityGuardTests(unittest.TestCase):
    def tearDown(self) -> None:
        nav._clear_post_follow_open_like_proof_stash()

    def test_story_resource_id_wins_even_when_like_control_is_visible(self) -> None:
        xml = (
            '<hierarchy><node resource-id="com.instagram.android:id/reel_viewer_root" />'
            '<node text="cand" />'
            '<node content-desc="Like" bounds="[50,1700][110,1800]" '
            'clickable="true" /></hierarchy>'
        )
        out = nav._post_open_hierarchy_identity_signals(
            xml,
            expected_username="cand",
        )
        self.assertTrue(out["snapshot_valid"])
        self.assertTrue(out["story_or_highlight_detected"])
        self.assertEqual(
            out["story_or_highlight_method"],
            "snapshot_story_highlight_resource_id",
        )
        self.assertTrue(out["candidate_username_exact_in_snapshot"])

    def test_positive_post_identity_requires_posts_title_and_exact_candidate(self) -> None:
        xml = (
            '<hierarchy><node text="Posts" />'
            '<node text="cand" />'
            '<node content-desc="Like" bounds="[50,1700][110,1800]" '
            'clickable="true" /></hierarchy>'
        )
        hierarchy = nav._post_open_hierarchy_identity_signals(
            xml,
            expected_username="cand",
        )
        self.assertTrue(hierarchy["posts_action_bar_in_snapshot"])
        self.assertTrue(hierarchy["candidate_username_exact_in_snapshot"])
        self.assertFalse(hierarchy["story_or_highlight_detected"])

        device = mock.MagicMock()
        with mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={
                "current_package": "com.instagram.android",
                "current_activity": "com.instagram.mainactivity.InstagramMainActivity",
            },
        ), mock.patch.object(
            nav,
            "_visual_read_action_bar_username",
            return_value="Posts",
        ), mock.patch.object(
            nav,
            "_visual_post_viewer_header_username_from_ui",
            return_value=("cand", "ui_post_header_text_exact"),
        ):
            contract = nav._post_open_live_positive_identity_contract(
                device,
                pkg="com.instagram.android",
                expected_username="cand",
                snapshot_xml=xml,
                story_detected=False,
                story_method="",
                like_surface_ok=True,
            )
        self.assertTrue(contract["post_identity_confirmed"])
        self.assertEqual(contract["post_identity_missing_signals"], [])

    def test_real_a2_without_header_reuses_fresh_stage_provenance_without_dump_or_screenshot(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2400)
        now = _DETERMINISTIC_MONOTONIC_BASE
        snapshot_xml = (
            '<hierarchy><node text="Posts" />'
            '<node content-desc="Like" bounds="[50,1700][110,1800]" '
            'clickable="true" /></hierarchy>'
        )
        nav._post_follow_open_like_proof_stash = {
            "viewer_detect_path": "phase_a2_exact_like_desc_fast",
            "proof_method": "ui_description_exact_like",
            "posts_action_bar": True,
            "current_package": "com.instagram.android",
            "current_activity": "com.instagram.mainactivity.InstagramMainActivity",
            "stashed_at_monotonic": now,
            "post_open_snapshot_captured_at_monotonic": now - 0.08,
            "post_open_snapshot_xml": snapshot_xml,
            "post_open_stage_provenance": _fresh_stage_provenance(
                now_monotonic=now
            ),
            "source_profile_username": "ct",
            "follower_username": "cand",
        }
        with mock.patch.object(nav.time, "perf_counter", return_value=now), \
             mock.patch.object(nav, "_dump_post_viewer_hierarchy") as dump, \
             mock.patch.object(nav, "screenshot") as shot, \
             mock.patch.object(nav, "log"):
            out = nav._post_open_surface_audits(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
            )
        self.assertTrue(out["reused_snapshot"])
        self.assertTrue(out["like_surface_ok"])
        self.assertTrue(out["post_identity_confirmed"])
        self.assertTrue(out["stage_provenance_confirmed"])
        self.assertFalse(out["candidate_header_exact"])
        self.assertEqual(out["extra_dump_count"], 0)
        dump.assert_not_called()
        shot.assert_not_called()

    def test_a2_without_header_and_without_provenance_is_fail_closed(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2400)
        out = nav._post_open_snapshot_audit_signals(
            device,
            snapshot_xml=(
                '<hierarchy><node text="Posts" />'
                '<node content-desc="Like" bounds="[50,1700][110,1800]" '
                'clickable="true" /></hierarchy>'
            ),
            proof_method="ui_description_exact_like",
            expected_username="cand",
            posts_action_bar_hint=True,
            current_package="com.instagram.android",
            expected_package="com.instagram.android",
            current_activity="com.instagram.mainactivity.InstagramMainActivity",
            stage_provenance=None,
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "snapshot_post_identity_unconfirmed")
        self.assertIn(
            "candidate_identity_continuity",
            out["post_identity_missing_signals"],
        )

    def test_legacy_safe_a2_exact_header_without_provenance_passes_without_happy_path_capture(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2400)
        now = time.perf_counter()
        nav._post_follow_open_like_proof_stash = {
            "viewer_detect_path": "phase_a2_exact_like_desc_fast",
            "proof_method": "ui_description_exact_like",
            "posts_action_bar": True,
            "current_package": "com.instagram.android",
            "current_activity": "com.instagram.mainactivity.InstagramMainActivity",
            "stashed_at_monotonic": now,
            "post_open_snapshot_captured_at_monotonic": now,
            "post_open_snapshot_xml": (
                '<hierarchy><node text="Posts"/><node text="cand"/>'
                '<node content-desc="Like" bounds="[50,1700][110,1800]" '
                'clickable="true"/></hierarchy>'
            ),
            "source_profile_username": "ct",
            "follower_username": "cand",
            "proof_source": "legacy_safe",
        }
        with mock.patch.object(nav, "_dump_post_viewer_hierarchy") as dump, \
             mock.patch.object(nav, "screenshot") as shot, \
             mock.patch.object(nav, "log"):
            out = nav._post_open_surface_audits(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
            )
        self.assertTrue(out["reused_snapshot"])
        self.assertTrue(out["like_surface_ok"])
        self.assertTrue(out["candidate_header_exact"])
        self.assertEqual(out["extra_dump_count"], 0)
        dump.assert_not_called()
        shot.assert_not_called()

    def test_legacy_safe_story_highlight_wins_over_like_before_dispatch(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2400)
        now = time.perf_counter()
        nav._post_follow_open_like_proof_stash = {
            "viewer_detect_path": "phase_a2_exact_like_desc_fast",
            "proof_method": "ui_description_exact_like",
            "posts_action_bar": True,
            "current_package": "com.instagram.android",
            "current_activity": "com.instagram.mainactivity.InstagramMainActivity",
            "stashed_at_monotonic": now,
            "post_open_snapshot_captured_at_monotonic": now,
            "post_open_snapshot_xml": (
                '<hierarchy><node text="Posts"/><node text="cand"/>'
                '<node resource-id="com.instagram.android:id/highlight_viewer_root"/>'
                '<node content-desc="Like" bounds="[50,1700][110,1800]" '
                'clickable="true"/></hierarchy>'
            ),
            "source_profile_username": "ct",
            "follower_username": "cand",
            "proof_source": "legacy_safe",
        }
        with mock.patch.object(nav, "_dump_post_viewer_hierarchy") as dump, \
             mock.patch.object(nav, "screenshot") as shot, \
             mock.patch.object(nav, "log"):
            out = nav._post_open_surface_audits(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
            )
        self.assertTrue(out["reused_snapshot"])
        self.assertTrue(out["story_detected"])
        self.assertFalse(out["like_surface_ok"])
        self.assertEqual(out["extra_dump_count"], 0)
        dump.assert_not_called()
        shot.assert_not_called()

    def test_stage_provenance_stale_or_navigation_scroll_changed_is_rejected(self) -> None:
        now = _DETERMINISTIC_MONOTONIC_BASE
        cases = {
            "tap_to_audit_expired": _fresh_stage_provenance(
                now_monotonic=now,
                tap_dispatched_at_monotonic=now - 3.2,
            ),
            "cell_stale_at_tap": _fresh_stage_provenance(
                cell_proof_age_at_tap_ms=1300.0,
            ),
            "navigation": _fresh_stage_provenance(
                navigation_generation_at_tap="nav-2",
            ),
            "scroll": _fresh_stage_provenance(
                scroll_generation_at_tap="scroll-2",
            ),
            "invalidated": _fresh_stage_provenance(invalidated=True),
        }
        for label, provenance in cases.items():
            with self.subTest(label=label):
                out = nav._post_open_stage_provenance_contract(
                    provenance,
                    expected_username="cand",
                    expected_package="com.instagram.android",
                    current_activity=(
                        "com.instagram.mainactivity.InstagramMainActivity"
                    ),
                    snapshot_captured_at_monotonic=float(
                        provenance["snapshot_captured_at_monotonic"]
                    ),
                    now_monotonic=now,
                )
                self.assertFalse(out["stage_provenance_confirmed"])

    def test_fresh_ui_proof_age_is_recomputed_with_monotonic_clock_at_tap(self) -> None:
        proof = mock.Mock(created_at_monotonic=98.9, ttl_ms=1250.0)
        fresh = nav._fresh_ui_proof_age_at_tap(
            proof,
            now_monotonic=100.0,
        )
        self.assertTrue(fresh["valid"])
        self.assertEqual(fresh["age_ms"], 1100.0)

        stale = nav._fresh_ui_proof_age_at_tap(
            proof,
            now_monotonic=100.3,
        )
        self.assertFalse(stale["valid"])
        self.assertEqual(stale["age_ms"], 1400.0)

    def test_cell_proof_old_but_valid_at_tap_passes_real_viewer_delay_then_expires_at_three_seconds(self) -> None:
        now = _DETERMINISTIC_MONOTONIC_BASE
        provenance = _fresh_stage_provenance(
            now_monotonic=now,
            created_at_monotonic=now - 2.7,
            cell_proof_age_at_tap_ms=1000.0,
            tap_dispatched_at_monotonic=now - 1.9,
            snapshot_captured_at_monotonic=now - 0.12,
        )
        accepted = nav._post_open_stage_provenance_contract(
            provenance,
            expected_username="cand",
            expected_package="com.instagram.android",
            current_activity="com.instagram.mainactivity.InstagramMainActivity",
            snapshot_captured_at_monotonic=float(
                provenance["snapshot_captured_at_monotonic"]
            ),
            now_monotonic=now,
        )
        self.assertTrue(accepted["stage_provenance_confirmed"])
        self.assertGreaterEqual(accepted["tap_to_audit_ms"], 1700.0)
        self.assertLessEqual(accepted["tap_to_audit_ms"], 2000.0)

        expired = nav._post_open_stage_provenance_contract(
            provenance,
            expected_username="cand",
            expected_package="com.instagram.android",
            current_activity="com.instagram.mainactivity.InstagramMainActivity",
            snapshot_captured_at_monotonic=now + 1.25,
            now_monotonic=now + 1.25,
        )
        self.assertFalse(expired["stage_provenance_confirmed"])
        self.assertIn(
            "tap_to_audit_within_bound",
            expired["stage_provenance_missing_signals"],
        )

    def test_like_only_surface_is_not_authorized_when_story_probe_misses(self) -> None:
        device = mock.MagicMock()
        nav._post_follow_open_like_proof_stash = {
            "viewer_detect_path": "phase_a_like_unlike_fast",
            "proof_method": "com.instagram.android:id/row_feed_button_like",
            "posts_action_bar": False,
            "stashed_at_monotonic": time.perf_counter(),
            "post_open_snapshot_captured_at_monotonic": time.perf_counter(),
            "post_open_snapshot_xml": (
                '<hierarchy><node content-desc="Like" '
                'bounds="[50,1700][110,1800]" clickable="true" /></hierarchy>'
            ),
            "source_profile_username": "ct",
            "follower_username": "cand",
        }
        with mock.patch.object(
            nav,
            "_ui_story_or_highlight_viewer_detected",
            return_value=(False, ""),
        ), mock.patch.object(
            nav,
            "_ui_post_viewer_like_action_bar_exploitable",
            return_value=(True, "row_feed_button_like"),
        ), mock.patch.object(
            nav,
            "_dump_post_viewer_hierarchy",
            return_value=(
                '<hierarchy><node content-desc="Like" '
                'bounds="[50,1700][110,1800]" clickable="true" /></hierarchy>'
            ),
        ), mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={
                "current_package": "com.instagram.android",
                "current_activity": "com.instagram.mainactivity.InstagramMainActivity",
            },
        ), mock.patch.object(
            nav,
            "_visual_read_action_bar_username",
            return_value="",
        ), mock.patch.object(
            nav,
            "_visual_post_viewer_header_username_from_ui",
            return_value=("", ""),
        ), mock.patch.object(
            nav,
            "_ui_post_viewer_facebook_shared_content_detected",
            return_value=(False, ""),
        ), mock.patch.object(nav, "log"):
            out = nav._post_open_surface_audits(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
            )

        self.assertFalse(out["reused_snapshot"])
        self.assertFalse(out["story_detected"])
        self.assertFalse(out["like_surface_ok"])
        self.assertFalse(out["post_identity_confirmed"])
        self.assertIn("posts_action_bar", out["post_identity_missing_signals"])
        self.assertIn(
            "candidate_identity_continuity",
            out["post_identity_missing_signals"],
        )

    def test_explicit_highlight_surface_is_rejected_before_like(self) -> None:
        device = mock.MagicMock()
        xml = (
            '<hierarchy><node resource-id="com.instagram.android:id/highlight_viewer_root" />'
            '<node text="cand" />'
            '<node content-desc="Like" bounds="[50,1700][110,1800]" '
            'clickable="true" /></hierarchy>'
        )
        with mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={
                "current_package": "com.instagram.android",
                "current_activity": "com.instagram.mainactivity.InstagramMainActivity",
            },
        ), mock.patch.object(
            nav,
            "_visual_read_action_bar_username",
            return_value="Posts",
        ), mock.patch.object(
            nav,
            "_visual_post_viewer_header_username_from_ui",
            return_value=("cand", "ui_post_header_text_exact"),
        ):
            contract = nav._post_open_live_positive_identity_contract(
                device,
                pkg="com.instagram.android",
                expected_username="cand",
                snapshot_xml=xml,
                story_detected=False,
                story_method="",
                like_surface_ok=True,
                stage_provenance=_fresh_stage_provenance(),
            )
        self.assertFalse(contract["post_identity_confirmed"])
        self.assertTrue(contract["story_or_highlight_detected"])
        self.assertIn("story_highlight_absent", contract["post_identity_missing_signals"])

    def test_ambiguous_wrong_surface_recovers_once_without_like(self) -> None:
        device = mock.MagicMock()
        contract_ctx = _like_phase_contract_ctx()
        with ExitStack() as stack:
            _patch_like_phase_common(stack, contract_ctx=contract_ctx)
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_follow_likes_open_top_left_legacy_visual_safe",
                    return_value={
                        "ok": True,
                        "post_detected": True,
                        "failure_reason": "",
                        "open_strategy": "vision_open_top_left_legacy_safe",
                        "tap_x": 180,
                        "tap_y": 1282,
                        "likes_perf_post_open": {},
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_open_surface_audits",
                    return_value={
                        "story_detected": False,
                        "story_method": "",
                        "facebook_detected": False,
                        "facebook_method": "",
                        "like_surface_ok": False,
                        "like_surface_method": (
                            "post_identity_contract_failed:posts_action_bar,"
                            "candidate_header_exact"
                        ),
                        "post_identity_confirmed": False,
                        "post_identity_missing_signals": [
                            "posts_action_bar",
                            "candidate_header_exact",
                        ],
                    },
                )
            )
            like_tap = stack.enter_context(
                mock.patch.object(nav, "visual_like_open_post")
            )
            verify = stack.enter_context(
                mock.patch.object(nav, "visual_verify_post_liked")
            )
            return_to_profile = stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_return_to_profile_from_post",
                    return_value={"ok": True},
                )
            )
            rejection_capture = stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_open_boundary_screenshot_evidence",
                    return_value={
                        "ok": True,
                        "stage": "rejected_surface_before_recovery",
                        "captured_now": True,
                    },
                )
            )
            stack.enter_context(mock.patch.object(nav, "log"))
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        self.assertEqual(out["phase_outcome"], "skipped")
        self.assertEqual(
            out["skipped_reason"],
            "post_like_wrong_surface_identity_unconfirmed_recovered",
        )
        like_tap.assert_not_called()
        verify.assert_not_called()
        return_to_profile.assert_called_once()
        rejection_capture.assert_called_once()
        self.assertTrue(
            out["per_post"][0]["post_open_rejection_evidence"]["captured_now"]
        )

    def test_twenty_profile_cycles_never_select_highlight_bounds_as_post(self) -> None:
        for cycle in range(20):
            highlight_top = 360 + (cycle % 4) * 12
            post_top = 900 + (cycle % 3) * 18
            cells = "".join(
                (
                    '<node class="android.widget.ImageView" '
                    f'content-desc="Post thumbnail {index + 1}" '
                    f'bounds="[{index * 360},{post_top}]'
                    f'[{(index + 1) * 360},{post_top + 360}]"/>'
                )
                for index in range(3)
            )
            xml = (
                '<hierarchy><node text="cand" />'
                '<node resource-id="com.instagram.android:id/highlight_tray" '
                'content-desc="Highlight" '
                f'bounds="[0,{highlight_top}][1080,{highlight_top + 220}]"/>'
                '<node resource-id="com.instagram.android:id/profile_tabs_container" '
                'bounds="[0,700][1080,820]"/>'
                '<node content-desc="Profile tab grid" selected="true" '
                'bounds="[0,700][360,820]"/>'
                f'{cells}</hierarchy>'
            )
            with self.subTest(cycle=cycle):
                out = nav._post_follow_post_grid_evidence_from_xml(
                    xml,
                    candidate_username="cand",
                    ww=1080,
                    wh=2340,
                )
                self.assertEqual(out["outcome"], "safe_post")
                self.assertEqual(out["visible_post_count"], 3)
                self.assertGreaterEqual(out["post_bounds"]["top"], post_top)
                self.assertGreater(out["post_bounds"]["top"], highlight_top + 220)

    def test_highlight_only_profile_never_yields_safe_post(self) -> None:
        xml = (
            '<hierarchy><node text="cand" />'
            '<node resource-id="com.instagram.android:id/highlight_tray" '
            'content-desc="Highlight" bounds="[0,360][1080,620]"/>'
            '<node resource-id="com.instagram.android:id/profile_tabs_container" '
            'bounds="[0,700][1080,820]"/>'
            '<node content-desc="Profile tab grid" selected="true" '
            'bounds="[0,700][360,820]"/></hierarchy>'
        )
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml,
            candidate_username="cand",
            ww=1080,
            wh=2340,
        )
        self.assertNotEqual(out["outcome"], "safe_post")
        self.assertIsNone(out.get("post_bounds"))


if __name__ == "__main__":
    unittest.main()
