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
        # The active collaborative-owner contract invalidates post identity on
        # Story/Highlight surfaces even when an exact username is visible.
        self.assertFalse(out["candidate_username_exact_in_snapshot"])
        self.assertEqual(out["candidate_username_method"], "")

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
        self.assertTrue(contract["post_surface_proved"])
        self.assertEqual(contract["post_identity_missing_signals"], [])

    def test_post_surface_proof_is_independent_from_candidate_identity(self) -> None:
        device = mock.MagicMock()
        xml = (
            '<hierarchy><node text="Posts" />'
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
            nav, "_visual_read_action_bar_username", return_value="Posts"
        ), mock.patch.object(
            nav,
            "_visual_post_viewer_header_username_from_ui",
            return_value=("", ""),
        ):
            contract = nav._post_open_live_positive_identity_contract(
                device,
                pkg="com.instagram.android",
                expected_username="cand",
                snapshot_xml=xml,
                story_detected=False,
                story_method="",
                like_surface_ok=True,
                stage_provenance=None,
            )
        self.assertTrue(contract["post_surface_proved"])
        self.assertFalse(contract["post_identity_confirmed"])
        self.assertEqual(
            contract["post_identity_missing_signals"],
            ["candidate_identity_continuity"],
        )

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
                        "post_surface_proved": True,
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
        self.assertEqual(
            return_to_profile.call_args.kwargs["open_surface_kind"],
            "post_identity_unproven",
        )
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
                self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
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
        self.assertNotEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertIsNone(out.get("post_bounds"))


if __name__ == "__main__":
    unittest.main()


# Retained first physical viewer, before the activity-triggered late reprobe.
_VIEWER_446_XML = '<?xml version=\'1.0\' encoding=\'UTF-8\' standalone=\'yes\' ?>\n<hierarchy rotation="0">\n  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,0][1080,2340]" drawing-order="0" hint="" display-id="0">\n    <node index="0" text="" resource-id="" class="android.widget.LinearLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,0][1080,2340]" drawing-order="1" hint="" display-id="0">\n      <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,0][1080,2340]" drawing-order="2" hint="" display-id="0">\n        <node index="0" text="" resource-id="com.instagram.android:id/action_bar_root" class="android.widget.LinearLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,0][1080,2340]" drawing-order="1" hint="" display-id="0">\n          <node index="0" text="" resource-id="android:id/content" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,0][1080,2340]" drawing-order="2" hint="" display-id="0">\n            <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,0][1080,2340]" drawing-order="1" hint="" display-id="0">\n              <node index="0" text="" resource-id="com.instagram.android:id/swipe_navigation_container" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,0][1080,2340]" drawing-order="1" hint="" display-id="0">\n                <node index="0" text="" resource-id="com.instagram.android:id/layout_container_center_right_coordinator_layout" class="android.view.ViewGroup" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,0][1080,2205]" drawing-order="3" hint="" display-id="0">\n                  <node index="0" text="" resource-id="com.instagram.android:id/layout_container_right" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,100][1080,2205]" drawing-order="2" hint="" display-id="0" />\n                  <node index="1" text="" resource-id="com.instagram.android:id/activity_and_camera_shared_views_main_container" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,100][1080,2205]" drawing-order="4" hint="" display-id="0" />\n                  <node index="2" text="" resource-id="com.instagram.android:id/layout_container_main_panel" class="android.view.ViewGroup" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,100][1080,2205]" drawing-order="1" hint="" display-id="0">\n                    <node index="0" text="" resource-id="com.instagram.android:id/layout_container_main_wrapper" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,100][1080,2205]" drawing-order="2" hint="" display-id="0">\n                      <node index="0" text="" resource-id="com.instagram.android:id/layout_container_main" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,100][1080,2205]" drawing-order="1" hint="" display-id="0" />\n                      <node index="1" text="" resource-id="com.instagram.android:id/swipeable_tab_view_pager" class="androidx.viewpager.widget.ViewPager" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,100][1080,2205]" drawing-order="2" hint="" display-id="0">\n                        <node index="0" text="" resource-id="com.instagram.android:id/swipeable_nav_view_pager_inner_recycler_view" class="androidx.recyclerview.widget.RecyclerView" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="true" focused="false" scrollable="true" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,100][1080,2205]" drawing-order="1" hint="" display-id="0">\n                          <node index="3" text="" resource-id="" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,100][1080,2205]" drawing-order="4" hint="" display-id="0">\n                            <node index="0" text="" resource-id="com.instagram.android:id/layout_container_swipeable" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,100][1080,2205]" drawing-order="1" hint="" display-id="0">\n                              <node index="0" text="" resource-id="com.instagram.android:id/layout_listview_parent_container" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,257][1080,2205]" drawing-order="1" hint="" display-id="0">\n                                <node index="0" text="" resource-id="" class="android.widget.LinearLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,257][1080,2205]" drawing-order="1" hint="" display-id="0">\n                                  <node index="0" text="" resource-id="" class="android.widget.LinearLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,257][1080,2205]" drawing-order="2" hint="" display-id="0">\n                                    <node index="0" text="" resource-id="com.instagram.android:id/refreshable_container" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="false" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,257][1080,2205]" drawing-order="1" hint="" display-id="0">\n                                      <node index="0" text="" resource-id="android:id/list" class="androidx.recyclerview.widget.RecyclerView" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="true" focused="false" scrollable="true" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,257][1080,2205]" drawing-order="1" hint="" display-id="0">\n                                        <node index="0" text="" resource-id="" class="android.view.ViewGroup" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,257][1080,403]" drawing-order="1" hint="" display-id="0">\n                                          <node index="0" text="" resource-id="com.instagram.android:id/row_feed_profile_header" class="android.view.ViewGroup" package="com.instagram.android" content-desc="ewa.banaszekk posted a carousel in Joshua Tree National Park 2 days ago" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,257][1080,403]" drawing-order="1" hint="" display-id="0">\n                                            <node index="0" text="" resource-id="" class="android.widget.Button" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="true" password="false" selected="false" visible-to-user="true" bounds="[0,257][144,403]" drawing-order="1" hint="" display-id="0">\n                                              <node index="0" text="" resource-id="" class="android.view.ViewGroup" package="com.instagram.android" content-desc="User avatars" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[31,257][144,403]" drawing-order="1" hint="" display-id="0" />\n                                            </node>\n                                            <node index="1" text="ewa.banaszekk and pixel.photo.video\xa0\xa0" resource-id="com.instagram.android:id/row_feed_photo_profile_name" class="android.widget.Button" package="com.instagram.android" content-desc="ewa.banaszekk and pixel.photo.video\xa0\xa0" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[144,257][956,328]" drawing-order="2" hint="" display-id="0" />\n                                            <node index="2" text="" resource-id="" class="android.view.ViewGroup" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[144,328][956,403]" drawing-order="3" hint="" display-id="0">\n                                              <node index="0" text="Joshua Tree National Park" resource-id="com.instagram.android:id/secondary_label" class="android.widget.Button" package="com.instagram.android" content-desc="Joshua Tree National Park" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[144,328][956,403]" drawing-order="1" hint="" display-id="0" />\n                                            </node>\n                                            <node index="3" text="" resource-id="com.instagram.android:id/media_option_button" class="android.widget.ImageView" package="com.instagram.android" content-desc="More actions for this post" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[956,262][1080,398]" drawing-order="4" hint="" display-id="0" />\n                                          </node>\n                                        </node>\n                                        <node index="1" text="" resource-id="" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,403][1080,1843]" drawing-order="2" hint="" display-id="0">\n                                          <node index="0" text="" resource-id="com.instagram.android:id/carousel_media_group" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,403][1080,1843]" drawing-order="1" hint="" display-id="0">\n                                            <node index="0" text="" resource-id="com.instagram.android:id/row_carousel_media_actions" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,403][1080,1843]" drawing-order="11" hint="" display-id="0" />\n                                            <node index="1" text="" resource-id="com.instagram.android:id/carousel_viewpager" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,403][1080,1843]" drawing-order="2" hint="" display-id="0">\n                                              <node index="0" text="" resource-id="com.instagram.android:id/zoomable_view_container" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,403][1080,1843]" drawing-order="1" hint="" display-id="0">\n                                                <node index="0" text="" resource-id="com.instagram.android:id/row_feed_photo_tags" class="android.view.ViewGroup" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,403][1080,1843]" drawing-order="2" hint="" display-id="0" />\n                                                <node index="1" text="" resource-id="com.instagram.android:id/row_feed_photo_media_tag_hints" class="android.view.ViewGroup" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,403][1080,1843]" drawing-order="3" hint="" display-id="0" />\n                                                <node index="2" text="" resource-id="com.instagram.android:id/carousel_video_media_group" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="Photo 1 of 8 by Ewa, 29 likes, 1 comment" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,403][1080,1843]" drawing-order="1" hint="" display-id="0">\n                                                  <node index="0" text="" resource-id="com.instagram.android:id/carousel_video_media_actions" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,403][1080,1843]" drawing-order="9" hint="" display-id="0">\n                                                    <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,403][1080,1843]" drawing-order="1" hint="" display-id="0">\n                                                      <node index="0" text="" resource-id="com.instagram.android:id/video_states" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,403][1080,1843]" drawing-order="5" hint="" display-id="0" />\n                                                    </node>\n                                                  </node>\n                                                  <node index="1" text="" resource-id="com.instagram.android:id/carousel_video_image" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,403][1080,1843]" drawing-order="2" hint="" display-id="0">\n                                                    <node index="0" text="" resource-id="" class="android.widget.ImageView" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,403][1080,1843]" drawing-order="1" hint="" display-id="0" />\n                                                  </node>\n                                                  <node index="2" text="" resource-id="com.instagram.android:id/hint_icon_container" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[501,1103][579,1142]" drawing-order="14" hint="" display-id="0" />\n                                                </node>\n                                                <node index="3" text="" resource-id="" class="android.widget.LinearLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,1730][113,1843]" drawing-order="6" hint="" display-id="0">\n                                                  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,1730][113,1843]" drawing-order="1" hint="" display-id="0">\n                                                    <node index="0" text="" resource-id="com.instagram.android:id/indicator_background_view" class="android.view.ViewGroup" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[39,1730][113,1804]" drawing-order="1" hint="" display-id="0">\n                                                      <node index="0" text="" resource-id="com.instagram.android:id/indicator_icon_view" class="android.widget.Button" package="com.instagram.android" content-desc="View tagged people" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[57,1748][95,1786]" drawing-order="1" hint="" display-id="0" />\n                                                    </node>\n                                                  </node>\n                                                </node>\n                                              </node>\n                                            </node>\n                                            <node index="2" text="" resource-id="" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[897,403][1080,557]" drawing-order="12" hint="" display-id="0">\n                                              <node index="0" text="1/8" resource-id="com.instagram.android:id/carousel_index_indicator_text_view" class="android.widget.TextView" package="com.instagram.android" content-desc="1/8" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[936,442][1041,518]" drawing-order="1" hint="" display-id="0" />\n                                            </node>\n                                            <node index="4" text="" resource-id="com.instagram.android:id/indicator_container" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="Turn sound on" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[926,1728][1080,1843]" drawing-order="10" hint="" display-id="0">\n                                              <node index="0" text="" resource-id="com.instagram.android:id/indicator" class="android.widget.LinearLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[965,1728][1041,1804]" drawing-order="1" hint="" display-id="0">\n                                                <node index="0" text="" resource-id="com.instagram.android:id/slideout_iconview_icon" class="android.widget.ImageView" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[984,1747][1022,1785]" drawing-order="1" hint="" display-id="0" />\n                                              </node>\n                                            </node>\n                                          </node>\n                                        </node>\n                                        <node index="2" text="" resource-id="" class="android.view.ViewGroup" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,1843][1080,2180]" drawing-order="3" hint="" display-id="0">\n                                          <node index="0" text="" resource-id="com.instagram.android:id/row_feed_view_group_buttons" class="android.view.ViewGroup" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,1843][1080,2029]" drawing-order="1" hint="" display-id="0">\n                                            <node index="0" text="" resource-id="" class="android.view.ViewGroup" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[34,1900][113,2029]" drawing-order="2" hint="" display-id="0">\n                                              <node index="0" text="" resource-id="com.instagram.android:id/row_feed_button_like" class="android.widget.Button" package="com.instagram.android" content-desc="Like" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[34,1900][102,2029]" drawing-order="1" hint="" display-id="0" />\n                                            </node>\n                                            <node index="1" text="29" resource-id="" class="android.widget.Button" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[113,1900][159,2029]" drawing-order="3" hint="" display-id="0" />\n                                            <node index="2" text="" resource-id="" class="android.view.ViewGroup" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[159,1900][272,2029]" drawing-order="4" hint="" display-id="0">\n                                              <node index="0" text="" resource-id="com.instagram.android:id/row_feed_button_comment" class="android.widget.Button" package="com.instagram.android" content-desc="Comment" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[193,1900][261,2029]" drawing-order="1" hint="" display-id="0" />\n                                            </node>\n                                            <node index="3" text="1" resource-id="" class="android.widget.Button" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[272,1900][288,2029]" drawing-order="5" hint="" display-id="0" />\n                                            <node index="4" text="" resource-id="" class="android.view.ViewGroup" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="true" password="false" selected="false" visible-to-user="true" bounds="[305,1900][401,2029]" drawing-order="6" hint="" display-id="0">\n                                              <node index="0" text="" resource-id="com.instagram.android:id/row_feed_button_share" class="android.view.ViewGroup" package="com.instagram.android" content-desc="Send post. Button. Double tap to choose who to send this post to." checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[322,1900][390,2029]" drawing-order="1" hint="" display-id="0" />\n                                            </node>\n                                            <node index="5" text="" resource-id="" class="android.view.View" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[427,1843][652,1927]" drawing-order="1" hint="" display-id="0" />\n                                            <node index="6" text="" resource-id="com.instagram.android:id/row_feed_button_save" class="android.widget.Button" package="com.instagram.android" content-desc="Add to Saved" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="true" password="false" selected="false" visible-to-user="true" bounds="[950,1900][1074,2018]" drawing-order="7" hint="" display-id="0" />\n                                          </node>\n                                          <node index="1" text="" resource-id="" class="android.view.ViewGroup" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,2029][1080,2092]" drawing-order="2" hint="" display-id="0">\n                                            <node index="0" text="ewa.banaszekk A desert full of beauty ..️… more" resource-id="" class="com.instagram.ui.widget.textview.IgTextLayoutView" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[34,2029][1080,2081]" drawing-order="1" hint="" display-id="0">\n                                              <node index="0" text="" resource-id="" class="android.widget.Button" package="com.instagram.android" content-desc="ewa.banaszekk" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[34,2029][318,2075]" drawing-order="0" hint="" display-id="0" />\n                                              <node index="1" text="" resource-id="" class="android.widget.Button" package="com.instagram.android" content-desc="A desert full of beauty ..️" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[328,2029][787,2075]" drawing-order="0" hint="" display-id="0" />\n                                              <node index="2" text="" resource-id="" class="android.widget.Button" package="com.instagram.android" content-desc="A desert full of beauty ..️" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[328,2029][787,2075]" drawing-order="0" hint="" display-id="0" />\n                                              <node index="3" text="" resource-id="" class="android.widget.Button" package="com.instagram.android" content-desc="more" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[823,2029][915,2075]" drawing-order="0" hint="" display-id="0" />\n                                            </node>\n                                          </node>\n                                          <node index="2" text="2 days ago  •  See translation" resource-id="" class="android.widget.TextView" package="com.instagram.android" content-desc="2 days ago  •  See translation" checkable="false" checked="false" clickable="false" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,2092][1080,2155]" drawing-order="3" hint="" display-id="0" />\n                                        </node>\n                                        <node index="3" text="" resource-id="" class="android.view.ViewGroup" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,2180][1080,2205]" drawing-order="4" hint="" display-id="0">\n                                          <node index="0" text="" resource-id="com.instagram.android:id/row_feed_profile_header" class="android.view.ViewGroup" package="com.instagram.android" content-desc="chrisroyer.pix posted a carousel in Domaine Du Viet Hyeres 11 September" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,2180][1080,2205]" drawing-order="1" hint="" display-id="0">\n                                            <node index="0" text="" resource-id="" class="android.widget.Button" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="true" password="false" selected="false" visible-to-user="true" bounds="[0,2180][144,2205]" drawing-order="1" hint="" display-id="0">\n                                              <node index="0" text="" resource-id="" class="android.view.ViewGroup" package="com.instagram.android" content-desc="User avatars" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[31,2180][144,2205]" drawing-order="1" hint="" display-id="0" />\n                                            </node>\n                                            <node index="1" text="chrisroyer.pix\xa0\xa0 and pixel.photo.video\xa0\xa0" resource-id="com.instagram.android:id/row_feed_photo_profile_name" class="android.widget.Button" package="com.instagram.android" content-desc="chrisroyer.pix\xa0\xa0 and pixel.photo.video\xa0\xa0" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[144,2180][956,2205]" drawing-order="2" hint="" display-id="0" />\n                                            <node index="3" text="" resource-id="com.instagram.android:id/media_option_button" class="android.widget.ImageView" package="com.instagram.android" content-desc="More actions for this post" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[956,2185][1080,2205]" drawing-order="4" hint="" display-id="0" />\n                                          </node>\n                                        </node>\n                                      </node>\n                                    </node>\n                                  </node>\n                                </node>\n                              </node>\n                            </node>\n                          </node>\n                        </node>\n                      </node>\n                      <node index="2" text="" resource-id="com.instagram.android:id/action_bar_container" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,100][1080,257]" drawing-order="15" hint="" display-id="0">\n                        <node index="0" text="" resource-id="com.instagram.android:id/action_bar_wrapper" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,100][1080,257]" drawing-order="4" hint="" display-id="0">\n                          <node index="0" text="" resource-id="com.instagram.android:id/action_bar" class="android.widget.LinearLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,100][1080,257]" drawing-order="1" hint="" display-id="0">\n                            <node index="0" text="" resource-id="com.instagram.android:id/action_bar_new_title_container" class="android.widget.LinearLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,100][758,257]" drawing-order="1" hint="" display-id="0">\n                              <node index="0" text="" resource-id="com.instagram.android:id/action_bar_button_back" class="android.widget.ImageView" package="com.instagram.android" content-desc="Back" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,100][158,257]" drawing-order="1" hint="" display-id="0" />\n                              <node index="1" text="" resource-id="" class="android.widget.LinearLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[158,145][388,211]" drawing-order="3" hint="" display-id="0">\n                                <node index="0" text="" resource-id="com.instagram.android:id/action_bar_title_and_icons" class="android.widget.LinearLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[158,145][388,211]" drawing-order="2" hint="" display-id="0">\n                                  <node index="0" text="Posts" resource-id="com.instagram.android:id/action_bar_title" class="android.widget.TextView" package="com.instagram.android" content-desc="Posts" checkable="false" checked="false" clickable="true" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[203,145][354,211]" drawing-order="1" hint="" display-id="0" />\n                                </node>\n                              </node>\n                            </node>\n                            <node index="1" text="" resource-id="com.instagram.android:id/follow_button" class="android.widget.Button" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="true" visible-to-user="true" bounds="[814,120][1057,237]" drawing-order="4" hint="" display-id="0">\n                              <node index="0" text="Follow" resource-id="com.instagram.android:id/action_bar_overflow_text" class="android.widget.TextView" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="true" visible-to-user="true" bounds="[814,120][1024,215]" drawing-order="2" hint="" display-id="0" />\n                            </node>\n                          </node>\n                        </node>\n                      </node>\n                    </node>\n                  </node>\n                </node>\n                <node index="1" text="" resource-id="com.instagram.android:id/bottom_sheet_camera_container" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,100][1080,2205]" drawing-order="2" hint="" display-id="0" />\n              </node>\n              <node index="1" text="" resource-id="com.instagram.android:id/modal_container" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,0][1080,2205]" drawing-order="3" hint="" display-id="0" />\n              <node index="2" text="" resource-id="com.instagram.android:id/overlay_layout_container" class="android.widget.FrameLayout" package="com.instagram.android" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,0][1080,2205]" drawing-order="5" hint="" display-id="0" />\n            </node>\n          </node>\n        </node>\n      </node>\n    </node>\n  </node>\n  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,0][1080,100]" drawing-order="0" hint="" display-id="0">\n    <node index="0" text="" resource-id="com.android.systemui:id/status_bar_launch_animation_container" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,0][1080,100]" drawing-order="1" hint="" display-id="0" />\n    <node index="1" text="" resource-id="com.android.systemui:id/container" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,0][1080,100]" drawing-order="3" hint="" display-id="0" />\n    <node index="2" text="" resource-id="com.android.systemui:id/status_bar_container" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,0][1080,100]" drawing-order="2" hint="" display-id="0">\n      <node index="0" text="" resource-id="com.android.systemui:id/status_bar" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,0][1080,100]" drawing-order="1" hint="" display-id="0">\n        <node index="0" text="" resource-id="com.android.systemui:id/status_bar_contents" class="android.widget.RelativeLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,28][1080,100]" drawing-order="2" hint="" display-id="0">\n          <node index="0" text="" resource-id="com.android.systemui:id/status_bar_left_container" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[68,28][470,100]" drawing-order="1" hint="" display-id="0">\n            <node index="0" text="" resource-id="com.android.systemui:id/status_bar_start_side_content" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[68,28][470,100]" drawing-order="1" hint="" display-id="0">\n              <node index="0" text="" resource-id="com.android.systemui:id/status_bar_start_side_except_heads_up" class="android.widget.LinearLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[68,28][470,100]" drawing-order="2" hint="" display-id="0">\n                <node index="0" text="" resource-id="com.android.systemui:id/left_clock_container" class="android.widget.LinearLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[68,28][175,100]" drawing-order="3" hint="" display-id="0">\n                  <node index="0" text="23:00" resource-id="com.android.systemui:id/clock" class="android.widget.TextView" package="com.android.systemui" content-desc="23:00" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[68,28][175,100]" drawing-order="3" hint="" display-id="0" />\n                </node>\n                <node index="1" text="" resource-id="com.android.systemui:id/samsung_notification_indicator_container" class="android.widget.LinearLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[186,28][470,100]" drawing-order="5" hint="" display-id="0">\n                  <node index="0" text="" resource-id="com.android.systemui:id/notification_icon_area" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[186,28][327,100]" drawing-order="2" hint="" display-id="0">\n                    <node index="0" text="" resource-id="com.android.systemui:id/notification_icon_area_inner" class="android.widget.LinearLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[186,28][327,100]" drawing-order="1" hint="" display-id="0">\n                      <node index="0" text="" resource-id="com.android.systemui:id/notificationIcons" class="android.view.ViewGroup" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[186,28][327,100]" drawing-order="1" hint="" display-id="0">\n                        <node index="0" text="" resource-id="" class="android.widget.ImageView" package="com.android.systemui" content-desc="Android System notification: " checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[186,28][233,100]" drawing-order="1" hint="" display-id="0" />\n                        <node index="1" text="" resource-id="" class="android.widget.ImageView" package="com.android.systemui" content-desc="Messages notification: " checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[233,28][280,100]" drawing-order="2" hint="" display-id="0" />\n                        <node index="2" text="" resource-id="" class="android.widget.ImageView" package="com.android.systemui" content-desc="Digital Wellbeing notification: " checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[280,28][327,100]" drawing-order="3" hint="" display-id="0" />\n                      </node>\n                    </node>\n                  </node>\n                </node>\n              </node>\n            </node>\n          </node>\n          <node index="2" text="" resource-id="com.android.systemui:id/system_icon_area" class="android.widget.LinearLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[797,28][1012,100]" drawing-order="3" hint="" display-id="0">\n            <node index="0" text="" resource-id="com.android.systemui:id/status_bar_end_side_content" class="android.widget.LinearLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[797,28][1012,100]" drawing-order="1" hint="" display-id="0">\n              <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[797,28][1012,100]" drawing-order="3" hint="" display-id="0">\n                <node index="0" text="" resource-id="com.android.systemui:id/system_icons" class="android.widget.LinearLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[797,28][1012,100]" drawing-order="1" hint="" display-id="0">\n                  <node index="0" text="" resource-id="com.android.systemui:id/statusIcons" class="android.widget.LinearLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[800,44][947,83]" drawing-order="1" hint="" display-id="0">\n                    <node index="1" text="" resource-id="com.android.systemui:id/wifi_combo" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[807,44][858,83]" drawing-order="20" hint="" display-id="0">\n                      <node index="0" text="" resource-id="com.android.systemui:id/wifi_group" class="android.widget.LinearLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[807,44][858,83]" drawing-order="1" hint="" display-id="0">\n                        <node index="1" text="" resource-id="com.android.systemui:id/wifi_iconset" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[807,44][858,83]" drawing-order="2" hint="" display-id="0">\n                          <node index="0" text="" resource-id="com.android.systemui:id/wifi_signal" class="android.widget.ImageView" package="com.android.systemui" content-desc="Wi-Fi two bars." checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[807,44][858,83]" drawing-order="1" hint="" display-id="0" />\n                          <node index="1" text="" resource-id="com.android.systemui:id/wifi_activity" class="android.widget.ImageView" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[807,44][858,83]" drawing-order="2" hint="" display-id="0" />\n                        </node>\n                      </node>\n                    </node>\n                    <node index="2" text="" resource-id="com.android.systemui:id/mobile_combo" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="MTNSA, three bars." checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[858,44][899,83]" drawing-order="22" hint="" display-id="0">\n                      <node index="0" text="" resource-id="com.android.systemui:id/mobile_group" class="android.widget.LinearLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[858,44][899,83]" drawing-order="1" hint="" display-id="0">\n                        <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[858,44][899,83]" drawing-order="5" hint="" display-id="0">\n                          <node index="0" text="" resource-id="com.android.systemui:id/mobile_signal" class="android.widget.ImageView" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[858,44][899,83]" drawing-order="1" hint="" display-id="0" />\n                        </node>\n                      </node>\n                    </node>\n                    <node index="3" text="" resource-id="com.android.systemui:id/mobile_combo" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="MTNSA, three bars." checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[899,44][940,83]" drawing-order="24" hint="" display-id="0">\n                      <node index="0" text="" resource-id="com.android.systemui:id/mobile_group" class="android.widget.LinearLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[899,44][940,83]" drawing-order="1" hint="" display-id="0">\n                        <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[899,44][940,83]" drawing-order="5" hint="" display-id="0">\n                          <node index="0" text="" resource-id="com.android.systemui:id/mobile_signal" class="android.widget.ImageView" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[899,44][940,83]" drawing-order="1" hint="" display-id="0" />\n                        </node>\n                      </node>\n                    </node>\n                  </node>\n                  <node index="1" text="" resource-id="com.android.systemui:id/battery" class="android.widget.LinearLayout" package="com.android.systemui" content-desc="Battery 100 per cent." checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[944,43][1012,85]" drawing-order="2" hint="" display-id="0">\n                    <node index="0" text="" resource-id="" class="android.widget.ImageView" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[944,43][1012,85]" drawing-order="1" hint="" display-id="0" />\n                  </node>\n                </node>\n              </node>\n            </node>\n          </node>\n        </node>\n      </node>\n    </node>\n  </node>\n  <node index="0" text="" resource-id="com.android.systemui:id/navigation_bar_frame" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,2205][1080,2340]" drawing-order="0" hint="" display-id="0">\n    <node index="0" text="" resource-id="com.android.systemui:id/navigation_bar_view" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,2205][1080,2340]" drawing-order="1" hint="" display-id="0">\n      <node index="0" text="" resource-id="com.android.systemui:id/navigation_inflater" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,2205][1080,2340]" drawing-order="2" hint="" display-id="0">\n        <node index="0" text="" resource-id="com.android.systemui:id/horizontal" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,2205][1080,2340]" drawing-order="1" hint="" display-id="0">\n          <node index="0" text="" resource-id="com.android.systemui:id/nav_buttons" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,2205][1080,2340]" drawing-order="1" hint="" display-id="0">\n            <node index="0" text="" resource-id="com.android.systemui:id/nav_bar_widget" class="android.widget.LinearLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,2205][1080,2340]" drawing-order="3" hint="" display-id="0" />\n            <node index="1" text="" resource-id="com.android.systemui:id/ends_group" class="android.widget.LinearLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[0,2205][1080,2340]" drawing-order="1" hint="" display-id="0">\n              <node index="0" text="" resource-id="com.android.systemui:id/recent_apps" class="android.widget.ImageView" package="com.android.systemui" content-desc="Recents" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="true" password="false" selected="false" visible-to-user="true" bounds="[118,2205][357,2340]" drawing-order="2" hint="" display-id="0" />\n              <node index="1" text="" resource-id="com.android.systemui:id/back" class="android.widget.ImageView" package="com.android.systemui" content-desc="Back" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[723,2205][962,2340]" drawing-order="4" hint="" display-id="0" />\n            </node>\n            <node index="2" text="" resource-id="com.android.systemui:id/center_group" class="android.widget.LinearLayout" package="com.android.systemui" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[420,2205][659,2340]" drawing-order="2" hint="" display-id="0">\n              <node index="0" text="" resource-id="com.android.systemui:id/home" class="android.widget.ImageView" package="com.android.systemui" content-desc="Home" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" visible-to-user="true" bounds="[420,2205][659,2340]" drawing-order="1" hint="" display-id="0" />\n            </node>\n          </node>\n        </node>\n      </node>\n    </node>\n  </node>\n</hierarchy>'
_VIEWER_446_PROVENANCE = {'intent_version': 'PostOpenIntentV2', 'intent_source': 'SAFE', 'intent_nonce': 'f4c03c99deb427024c25a261db86bfd6', 'evidence_kind': 'post_grid_evidence_bounds', 'candidate_username': 'pixel.photo.video', 'target_id': '0c32936a-356b-4978-840d-b09aaa24b42a', 'target_username': 'blkproduction.fr', 'post_bounds': {'left': 0, 'top': 1439, 'right': 356, 'bottom': 1914}, 'package': 'com.instagram.android', 'activity': '.activity.MainTabActivity', 'created_at_monotonic': 72.561266625, 'cell_proof_valid_at_tap': True, 'cell_proof_age_at_tap_ms': 0.07, 'cell_proof_ttl_ms': 1250.0, 'tap_dispatched_at_monotonic': 72.561355625, 'max_tap_to_audit_ms': 3000.0, 'same_stage_no_navigation_before_tap': True, 'navigation_generation_before': 'nav:0:scroll:0:ui:0', 'navigation_generation_at_tap': 'nav:0:scroll:0:ui:0', 'scroll_generation_before': 'nav:0:scroll:0:ui:0', 'scroll_generation_at_tap': 'nav:0:scroll:0:ui:0', 'invalidated': False, 'candidate_bound_provenance_source': 'fresh_final_mute_close_xml_physical_cell', 'stage_binding': {'account_id': '76c1dff8-d16a-40cf-83df-8138d2ca5bd4', 'run_id': '51b623cc-6f6c-4760-a518-3c9b837c269e', 'request_id': '6b6d317d-8b59-4cd0-acf9-f62fa903cf98', 'action_id': 'xml_list:pixel.photo.video', 'attempt_id': 1, 'business_session_id': '15531d07-5ece-4f44-a721-9d6a7fd93f3e', 'control_id': 'mainline:51b623cc-6f6c-4760-a518-3c9b837c269e', 'worker_sha': '3675fdf41e3533b91c5e72e46ce1540fe102b84e', 'target_id': '0c32936a-356b-4978-840d-b09aaa24b42a'}}

class Qualified446ViewerActivityTests(unittest.TestCase):
    def device(self, version=("446.0.0.49.77", 385211304), package="com.instagram.android"):
        from instagram_ui_446_compat import retain_context
        class NoIO:
            serial = "RFGL62N646V"
            def __getattr__(self, name):
                raise AssertionError("unexpected device call: " + name)
            def window_size(self):
                return (1080, 2340)
        d = NoIO()
        d._instagram_search_observed_version = (id(d), d.serial, package, *version)
        retain_context(d, serial=d.serial, package=package,
                       version_name=version[0], version_code=version[1], unsafe=False)
        return d

    def audit(self, d, activity=".activity.MainTabActivity", *, live=False,
              package="com.instagram.android", expected_package="com.instagram.android",
              provenance_change=None, elapsed=2010.20, story=False, like=True):
        import copy
        prov = copy.deepcopy(_VIEWER_446_PROVENANCE)
        prov.update(package=package, activity=activity)
        prov.update(provenance_change or {})
        now = prov["tap_dispatched_at_monotonic"] + elapsed / 1000
        xml = _VIEWER_446_XML
        if story:
            xml = xml.replace('<hierarchy', '<hierarchy', 1).replace('</hierarchy>',
                '<node resource-id="com.instagram.android:id/reel_viewer_root" /></hierarchy>')
        if not like:
            xml = xml.replace('row_feed_button_like', 'untrusted_control').replace('content-desc="Like"','content-desc="Other"')
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(nav.time, "perf_counter", return_value=now))
            stack.enter_context(mock.patch.object(nav, "log"))
            for name in ("_dump_post_viewer_hierarchy", "screenshot"):
                stack.enter_context(mock.patch.object(nav, name, side_effect=AssertionError("extra I/O")))
            stack.enter_context(mock.patch.object(nav.time, "sleep", side_effect=AssertionError("sleep")))
            if live:
                # Existing live observations are fixture inputs, not the activity authority.
                stack.enter_context(mock.patch.object(nav, "_followers_current_pkg_activity",
                    return_value={"current_package":package,"current_activity":activity}))
                stack.enter_context(mock.patch.object(nav, "_visual_read_action_bar_username", return_value="Posts"))
                stack.enter_context(mock.patch.object(nav, "_visual_post_viewer_header_username_from_ui", return_value=("", "")))
                return nav._post_open_live_positive_identity_contract(d, pkg=expected_package,
                    expected_username="pixel.photo.video",snapshot_xml=xml,story_detected=story,
                    story_method="",like_surface_ok=like,stage_provenance=prov,
                    snapshot_captured_at_monotonic=now-0.29203)
            return nav._post_open_snapshot_audit_signals(d,snapshot_xml=xml,
                proof_method="ui_description_exact_like" if like else "untrusted_control",
                expected_username="pixel.photo.video",
                posts_action_bar_hint=True,current_package=package,expected_package=expected_package,
                current_activity=activity,stage_provenance=prov,
                snapshot_captured_at_monotonic=now-0.29203)

    def test_real_initial_viewer_both_contracts_and_exact_activity_matrix(self):
        for live in (False, True):
            for activity in ("com.instagram.mainactivity.InstagramMainActivity",
                             "com.instagram.android.activity.MainTabActivity", ".activity.MainTabActivity"):
                with self.subTest(live=live,activity=activity):
                    out=self.audit(self.device(),activity,live=live)
                    self.assertTrue(out.get("post_identity_confirmed"),out)
                    self.assertTrue(out["stage_provenance_confirmed"])
                    self.assertFalse(out["candidate_header_exact"])

    def test_372_preserved_and_main_tab_not_added(self):
        for live in (False, True):
            d=self.device(("372.0.0.48.60",0),"com.instagram.androif")
            for activity, expected in (("com.instagram.mainactivity.InstagramMainActivity",True),
                                       (".activity.MainTabActivity",False)):
                out=self.audit(d,activity,live=live,package="com.instagram.androif",expected_package="com.instagram.androif")
                self.assertEqual(bool(out.get("post_identity_confirmed")),expected,out)

    def test_context_activity_and_package_negatives_both_contracts(self):
        for live in (False, True):
            devices=[self.device(("445",385211304)),self.device(("446.0.0.49.77",True)),
                     self.device(package="com.instagram.androif")]
            for field,value in (("serial","other"),("_instagram_search_observed_version",None),
                                ("_instagram_446_observed_context",None)):
                d=self.device();setattr(d,field,value);devices.append(d)
            d=self.device();d._instagram_search_observed_version=(0,d.serial,"com.instagram.android","446.0.0.49.77",385211304);devices.append(d)
            for d in devices:
                self.assertFalse(self.audit(d,live=live).get("post_identity_confirmed"))
            for activity in ("foreign.Activity","com.instagram.android.OverlayActivity","MainActivity","",None,
                             "foreign.activity.MainTabActivity"):
                self.assertFalse(self.audit(self.device(),activity,live=live).get("post_identity_confirmed"))
            self.assertFalse(self.audit(self.device(),live=live,package="foreign").get("post_identity_confirmed"))
            self.assertFalse(self.audit(self.device(),live=live,expected_package="com.instagram.androif").get("post_identity_confirmed"))

    def test_provenance_expiry_identity_story_like_still_fail_closed(self):
        for live in (False, True):
            for kw in ({"elapsed":6891.36},{"elapsed":3001},
                       {"provenance_change":{"candidate_username":"other"}},
                       {"provenance_change":{"invalidated":True}},
                       {"provenance_change":{"navigation_generation_at_tap":"wrong"}},
                       {"story":True},{"like":False}):
                with self.subTest(live=live,kw=kw):
                    self.assertFalse(self.audit(self.device(),live=live,**kw).get("post_identity_confirmed"))

    def test_exact_timing_proxy_uses_same_local_authority(self):
        from run_latency_observability_v1 import _TimedProxy
        d=self.device();events=[]
        proxy=_TimedProxy(d,kind="device",emit=lambda *a,**k:events.append((a,k)),methods=set())
        for live in (False,True):
            self.assertTrue(self.audit(proxy,live=live).get("post_identity_confirmed"))
        self.assertEqual(events,[])

    def test_original_first_audit_reuses_snapshot_without_live_reprobe(self):
        import copy
        now=_VIEWER_446_PROVENANCE["tap_dispatched_at_monotonic"]+2.01020
        nav._post_follow_open_like_proof_stash={
            "viewer_detect_path":"phase_a2_exact_like_desc_fast", "proof_method":"ui_description_exact_like",
            "posts_action_bar":True,"current_package":"com.instagram.android",
            "current_activity":".activity.MainTabActivity","stashed_at_monotonic":now,
            "post_open_snapshot_captured_at_monotonic":now-0.29203,
            "post_open_snapshot_xml":_VIEWER_446_XML,
            "post_open_stage_provenance":copy.deepcopy(_VIEWER_446_PROVENANCE),
            "source_profile_username":"blkproduction.fr","follower_username":"pixel.photo.video"}
        try:
            with mock.patch.object(nav.time,"perf_counter",return_value=now), \
                 mock.patch.object(nav,"_dump_post_viewer_hierarchy",side_effect=AssertionError("extra dump")), \
                 mock.patch.object(nav,"screenshot",side_effect=AssertionError("extra screenshot")), \
                 mock.patch.object(nav,"_post_open_live_positive_identity_contract",side_effect=AssertionError("late reprobe")), \
                 mock.patch.object(nav,"log"):
                out=nav._post_open_surface_audits(self.device(),pkg="com.instagram.android",
                    source_profile_username="blkproduction.fr",follower_username="pixel.photo.video")
            self.assertTrue(out["reused_snapshot"],out)
            self.assertTrue(out["post_identity_confirmed"],out)
            self.assertEqual(out["extra_dump_count"],0)
        finally:
            nav._clear_post_follow_open_like_proof_stash()
