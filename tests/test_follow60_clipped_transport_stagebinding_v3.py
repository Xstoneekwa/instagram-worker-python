from __future__ import annotations

import inspect
import time
import unittest
from contextlib import ExitStack
from unittest import mock

import follow_60s_canary as canary
import instagram_navigation as nav
import runner
from tests.follow60_generic_fixtures import (
    TEST_CANARY_ACCOUNT_ID,
    TEST_CANARY_USERNAME,
    configure_canary,
)


PKG = "com.instagram.android"
ACT = "com.instagram.mainactivity.InstagramMainActivity"


def _grid_xml(
    candidate: str,
    *,
    width: int,
    height: int,
    clipped: bool,
    tabs: bool = True,
    post_count: bool = True,
    suggested_after_tabs: bool = False,
) -> str:
    """Resolution-independent synthetic profile hierarchy."""
    cell_width = width // 3
    nav_top = int(height * 0.94)
    cell_top = int(height * (0.91 if clipped else 0.60))
    cell_bottom = nav_top if clipped else cell_top + cell_width
    posts = f'<node text="{candidate}"/>'
    if post_count:
        posts += '<node text="9 posts"/>'
    tab = '<node content-desc="Profile tab grid" selected="true"/>' if tabs else ""
    suggested = '<node text="Suggested for you"/>' if suggested_after_tabs else ""
    media = (
        '<node class="android.widget.ImageView" content-desc="Post thumbnail" '
        f'bounds="[0,{cell_top}][{cell_width},{cell_bottom}]"/>'
    )
    nav_bar = (
        '<node resource-id="com.instagram.android:id/bottom_navigation" '
        f'bounds="[0,{nav_top}][{width},{height}]"/>'
    )
    return f"<hierarchy>{posts}{tab}{suggested}{media}{nav_bar}</hierarchy>"


class Follow60ClippedTransportStageBindingV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(
            configure_canary(
                canary,
                account_id=TEST_CANARY_ACCOUNT_ID,
                account_username=TEST_CANARY_USERNAME,
                run_id="replay-v3",
                package=PKG,
                resume_policy=None,
            )
        )

    def tearDown(self) -> None:
        configure_canary(
            canary,
            account_id="other",
            account_username="other",
            run_id="reset",
            package=PKG,
            resume_policy=None,
        )

    def _binding(self, *, candidate: str = "candidate_1", action: str = "action_1") -> dict[str, object]:
        return {
            "account_id": TEST_CANARY_ACCOUNT_ID,
            "run_id": "replay-v3",
            "request_id": "request-v3",
            "action_id": action,
            "attempt_id": 1,
            "business_session_id": "business-v3",
            "control_id": "control-v3",
            "worker_sha": "worker-v3",
            "candidate_username": candidate,
            "source_target_id": "target-v3",
        }

    def _clipped(self, candidate: str, *, width: int = 1080, height: int = 2340) -> dict[str, object]:
        return nav._post_follow_post_grid_evidence_from_xml(
            _grid_xml(candidate, width=width, height=height, clipped=True),
            candidate_username=candidate,
            ww=width,
            wh=height,
        )

    def test_replay_cycle_1_clipped_transport_reveal_and_fresh_safe(self) -> None:
        evidence = self._clipped("candidate_1")
        self.assertEqual(evidence["classification"], "POST_ROW_POSITIVE_BUT_CLIPPED")
        self.assertTrue(evidence["profile_tabs_present"])
        self.assertTrue(evidence["posts_tab_selected"])
        self.assertEqual(evidence["physical_cell_count"], 1)
        self.assertTrue(evidence["media_band_detected"])
        device = mock.MagicMock()
        device.dump_hierarchy.return_value = "<fresh/>"
        fresh = {
            "outcome": "POST_ROW_POSITIVE_SAFE",
            "post_bounds": {"left": 0, "top": 900, "right": 360, "bottom": 1260},
        }
        with ExitStack() as stack:
            swipe = stack.enter_context(mock.patch.object(
                nav, "_post_follow_likes_profile_scroll_swipe",
                return_value={"swipe_ok": True, "scroll_distance_px": 420},
            ))
            stack.enter_context(mock.patch.object(
                nav, "_post_follow_post_grid_evidence_from_xml",
                return_value=dict(fresh),
            ))
            invalidate = stack.enter_context(mock.patch.object(canary, "invalidate"))
            stack.enter_context(mock.patch.object(nav.time, "sleep"))
            promoted = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device, evidence, ww=1080, wh=2340, candidate_username="candidate_1"
            )
        swipe.assert_called_once()
        device.dump_hierarchy.assert_called_once_with(compressed=False)
        invalidate.assert_called_once_with("post_grid_v2_single_reveal_scroll")
        self.assertEqual(promoted["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertEqual(promoted["reveal_count_total_for_like_phase"], 1)
        self.assertTrue(promoted["old_bounds_invalidated"])

    def test_replay_cycle_2_clipped_has_one_reveal_then_ambiguous_for_one_golden(self) -> None:
        evidence = self._clipped("candidate_2", width=720, height=1600)
        device = mock.MagicMock()
        device.dump_hierarchy.return_value = "<fresh-ambiguous/>"
        with ExitStack() as stack:
            swipe = stack.enter_context(mock.patch.object(
                nav, "_post_follow_likes_profile_scroll_swipe",
                return_value={"swipe_ok": True},
            ))
            stack.enter_context(mock.patch.object(
                nav, "_post_follow_post_grid_evidence_from_xml",
                return_value={
                    "outcome": "POST_GRID_AMBIGUOUS_FINAL",
                    "rejection_reason": "post_grid_ambiguous",
                },
            ))
            stack.enter_context(mock.patch.object(canary, "invalidate"))
            stack.enter_context(mock.patch.object(nav.time, "sleep"))
            promoted = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device, evidence, ww=720, wh=1600, candidate_username="candidate_2"
            )
        swipe.assert_called_once()
        device.dump_hierarchy.assert_called_once()
        self.assertEqual(promoted["outcome"], "POST_GRID_AMBIGUOUS_FINAL")
        self.assertEqual(promoted["reveal_count_total_for_like_phase"], 1)

    def test_replay_cycle_3_authoritative_binding_creates_like_context(self) -> None:
        binding, reason = nav._authoritative_stage_binding_v2(
            self._binding(candidate="candidate_3", action="action_3"),
            expected_candidate_username="candidate_3",
            expected_action_id="action_3",
        )
        self.assertEqual(reason, "")
        self.assertEqual(binding["request_id"], "request-v3")
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        device.dump_hierarchy.return_value = (
            '<hierarchy><node text="Posts"/><node text="candidate_3"/>'
            '<node content-desc="Like" clickable="true" '
            'bounds="[40,1700][120,1800]"/></hierarchy>'
        )
        with mock.patch.object(
            nav, "_followers_current_pkg_activity",
            return_value={"current_package": PKG, "current_activity": ACT},
        ):
            context, create_reason = nav._create_like_tap_context_v2(
                device,
                expected_package=PKG,
                expected_follower_username="candidate_3",
                expected_stage_binding=binding,
                post_open_context={},
            )
        self.assertEqual(create_reason, "")
        self.assertEqual(context["request_id"], "request-v3")
        self.assertEqual(context["action_id"], "action_3")
        ok, validation_reason, _ = nav._validate_like_tap_context_v2(
            context,
            expected_stage_binding=binding,
            expected_follower_username="candidate_3",
        )
        self.assertTrue(ok)
        self.assertEqual(validation_reason, "")

    def test_replay_cycle_4_ambiguous_goes_directly_to_golden_without_reveal(self) -> None:
        evidence = nav._post_follow_post_grid_evidence_from_xml(
            "<hierarchy><node text='candidate_4'/></hierarchy>",
            candidate_username="candidate_4", ww=1080, wh=2340,
        )
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "_post_follow_likes_profile_scroll_swipe"
        ) as swipe:
            promoted = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device, evidence, ww=1080, wh=2340, candidate_username="candidate_4"
            )
        swipe.assert_not_called()
        self.assertEqual(promoted["outcome"], "POST_GRID_AMBIGUOUS_FINAL")
        self.assertEqual(promoted["reveal_count_total_for_like_phase"], 0)

    def test_replay_cycle_5_missing_tabs_never_promotes_suggested_media(self) -> None:
        xml = _grid_xml(
            "candidate_5", width=1080, height=2340, clipped=True,
            tabs=False, suggested_after_tabs=True,
        )
        evidence = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate_5", ww=1080, wh=2340,
        )
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "_post_follow_likes_profile_scroll_swipe"
        ) as swipe:
            promoted = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device, evidence, ww=1080, wh=2340, candidate_username="candidate_5"
            )
        swipe.assert_not_called()
        self.assertFalse(evidence["profile_tabs_present"])
        self.assertNotEqual(evidence["outcome"], "POST_ROW_POSITIVE_BUT_CLIPPED")
        self.assertEqual(promoted["reveal_count_total_for_like_phase"], 0)

    def test_runner_transmits_exact_request_id_and_candidate_context_uses_binding(self) -> None:
        runner_source = inspect.getsource(runner._run_followers_list_engine_session)
        nav_source = inspect.getsource(nav.run_visual_candidate_post_follow_phase)
        self.assertIn('"request_id": str(run_request_id or "")', runner_source)
        self.assertIn("expected_stage_binding=_pf_expected_stage_binding", runner_source)
        self.assertIn("if follow60_canary_active:", runner_source)
        self.assertNotIn("_follow60_canary_active", runner_source)
        self.assertNotIn("_follow60_canary_control", runner_source)
        self.assertIn('"request_id": str(binding_context.get("request_id") or "")', nav_source)
        self.assertNotIn("_LOG_CONTEXT_REQUEST_ID", nav_source)

    def test_stage_binding_rejects_previous_action_candidate_and_missing_request(self) -> None:
        missing = self._binding()
        missing["request_id"] = ""
        out, reason = nav._authoritative_stage_binding_v2(
            missing,
            expected_candidate_username="candidate_1",
            expected_action_id="action_1",
        )
        self.assertIsNone(out)
        self.assertEqual(reason, "liketapcontext_stage_binding_missing")
        previous = self._binding()
        previous["action_id"] = "previous_action"
        out, reason = nav._authoritative_stage_binding_v2(
            previous,
            expected_candidate_username="candidate_1",
            expected_action_id="action_1",
        )
        self.assertIsNone(out)
        self.assertEqual(reason, "liketapcontext_action_id_binding_mismatch")
        foreign = self._binding(candidate="foreign_candidate")
        out, reason = nav._authoritative_stage_binding_v2(
            foreign,
            expected_candidate_username="candidate_1",
            expected_action_id="action_1",
        )
        self.assertIsNone(out)
        self.assertEqual(reason, "liketapcontext_candidate_binding_mismatch")

    def test_creation_reason_is_not_masked_by_none_validation(self) -> None:
        reason = nav._liketapcontext_rejection_reason(
            None,
            create_reason="liketapcontext_stage_binding_missing",
            validation_reason="liketapcontext_version_missing",
        )
        self.assertEqual(reason, "liketapcontext_stage_binding_missing")


if __name__ == "__main__":
    unittest.main()
