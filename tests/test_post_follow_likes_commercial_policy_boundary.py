import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import instagram_navigation as nav
import supabase_client


class PostFollowLikesCommercialPolicyBoundaryTests(unittest.TestCase):
    def _enter_likes_enabled(self, stack: ExitStack) -> None:
        stack.enter_context(
            patch.object(nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True)
        )
        stack.enter_context(
            patch.object(nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True)
        )
        stack.enter_context(
            patch.object(nav.config, "POST_FOLLOW_POST_LIKES_PERCENTAGE", 100, create=True)
        )
        stack.enter_context(
            patch.object(nav.config, "POST_FOLLOW_POST_LIKES_COUNT_RANGE", "1-1", create=True)
        )
        stack.enter_context(
            patch.object(nav.config, "POST_FOLLOW_TOTAL_LIKES_LIMIT", 150, create=True)
        )
        stack.enter_context(
            patch.object(nav.config, "POST_FOLLOW_POST_LIKES_BUDGET_S", 10.0, create=True)
        )

    def test_unchanged_revision_preserves_like_phase_entry(self) -> None:
        device = MagicMock()
        with ExitStack() as stack:
            self._enter_likes_enabled(stack)
            boundary_mock = stack.enter_context(
                patch(
                    "account_commercial_policy.commercial_policy_boundary_blocks_phase",
                    return_value=False,
                )
            )
            stack.enter_context(
                patch.object(
                    nav,
                    "read_current_profile_username_for_follow_gate",
                    return_value="cand",
                )
            )
            stack.enter_context(
                patch(
                    "navigation_engine.observe_instagram_state",
                    return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9},
                )
            )
            grid_mock = stack.enter_context(
                patch.object(nav, "ensure_post_grid_visible_for_post_follow_likes")
            )
            grid_mock.return_value = {
                "ok": False,
                "skipped_reason": "post_grid_not_visible_before_open",
            }
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
                account_id="account-a",
                bound_commercial_policy_revision="rev-same",
                run_id="run-a",
            )
        boundary_mock.assert_called_once()
        self.assertEqual(
            boundary_mock.call_args.kwargs.get("boundary"),
            "before_post_follow_likes_phase",
        )
        self.assertNotEqual(out.get("skipped_reason"), "commercial_policy_revision_changed")

    def test_revision_change_blocks_before_like_attempt(self) -> None:
        device = MagicMock()
        with ExitStack() as stack:
            self._enter_likes_enabled(stack)
            stack.enter_context(
                patch(
                    "account_commercial_policy.commercial_policy_boundary_blocks_phase",
                    return_value=True,
                )
            )
            open_like_mock = stack.enter_context(patch.object(nav, "visual_like_open_post"))
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
                account_id="account-a",
                bound_commercial_policy_revision="rev-old",
                run_id="run-a",
            )
        open_like_mock.assert_not_called()
        self.assertTrue(out.get("skipped"))
        self.assertEqual(out.get("skipped_reason"), "commercial_policy_revision_changed")
        self.assertEqual(out.get("liked_count"), 0)
        self.assertEqual(out.get("attempted_count"), 0)

    def test_account_b_revision_change_does_not_block_account_a(self) -> None:
        def _boundary(account_id: str, **kwargs):
            return account_id == "account-b"

        device = MagicMock()
        with ExitStack() as stack:
            self._enter_likes_enabled(stack)
            stack.enter_context(
                patch(
                    "account_commercial_policy.commercial_policy_boundary_blocks_phase",
                    side_effect=_boundary,
                )
            )
            stack.enter_context(
                patch.object(
                    nav,
                    "read_current_profile_username_for_follow_gate",
                    return_value="cand",
                )
            )
            stack.enter_context(
                patch(
                    "navigation_engine.observe_instagram_state",
                    return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9},
                )
            )
            grid_mock = stack.enter_context(
                patch.object(nav, "ensure_post_grid_visible_for_post_follow_likes")
            )
            grid_mock.return_value = {
                "ok": False,
                "skipped_reason": "post_grid_not_visible_before_open",
            }
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
                account_id="account-a",
                bound_commercial_policy_revision="rev-old",
                run_id="run-a",
            )
        self.assertNotEqual(out.get("skipped_reason"), "commercial_policy_revision_changed")

    def test_visual_post_follow_phase_skips_likes_and_preserves_return_path(self) -> None:
        from follow_state_contract import FollowContext

        device = MagicMock()
        follow_ctx = FollowContext.from_follow_verified(
            follower_username="cand",
            source_profile_username="ct",
            visual_candidate_id="vc-1",
            follow_state_after="following",
        )
        surface_truth = {
            "decision": "candidate_profile_confirmed",
            "candidate_username": "cand",
        }
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(nav.config, "ENABLE_VISUAL_FOLLOW_MUTE_FLOW", False, create=True)
            )
            stack.enter_context(
                patch.object(
                    nav.config, "ENABLE_REAL_VISUAL_MUTE_AFTER_FOLLOW", False, create=True
                )
            )
            stack.enter_context(
                patch(
                    "account_commercial_policy.commercial_policy_boundary_blocks_phase",
                    return_value=True,
                )
            )
            likes_phase_mock = stack.enter_context(
                patch.object(nav, "run_post_follow_post_likes_phase")
            )
            return_mock = stack.enter_context(
                patch.object(
                    nav,
                    "post_follow_controlled_return_to_followers_list",
                    return_value=(True, "compact", None),
                )
            )
            stack.enter_context(
                patch.object(
                    nav, "detect_followers_list_screen", return_value={"action_bar_title": "cand"}
                )
            )
            stack.enter_context(
                patch.object(nav, "_post_follow_resolve_surface_truth", return_value=surface_truth)
            )
            stack.enter_context(patch.object(nav, "_post_follow_overlay_ui_hints", return_value={}))
            stack.enter_context(patch.object(nav, "_post_follow_screen_fingerprint", return_value={}))
            stack.enter_context(
                patch(
                    "navigation_engine.observe_instagram_state",
                    return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9},
                )
            )
            stack.enter_context(patch.object(supabase_client, "_LOG_CONTEXT_ACCOUNT_ID", "account-a"))
            stack.enter_context(patch.object(supabase_client, "_LOG_CONTEXT_RUN_ID", "run-a"))
            out = nav.run_visual_candidate_post_follow_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                visual_candidate_id="vc-1",
                follower_username="cand",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
                det={},
                follow_context=follow_ctx,
                bound_commercial_policy_revision="rev-old",
            )
        likes_phase_mock.assert_not_called()
        self.assertEqual(
            out.get("likes", {}).get("skipped_reason"),
            "commercial_policy_revision_changed",
        )
        self.assertTrue(out.get("return_ok"))
        return_mock.assert_called_once()

    def test_growth_package_caps_read_without_overwriting_preferences(self) -> None:
        from account_commercial_policy import load_account_effective_package_policy

        with patch(
            "account_commercial_policy.supabase_client.get_account_package_summary",
            return_value={
                "commercial_package_code": "growth",
                "package_caps": {"follow_day_cap": 40, "like_day_cap": 20},
                "effective_caps_preview": {"follow_day_cap": 40, "like_day_cap": 20},
            },
        ), patch(
            "account_commercial_policy.load_account_commercial_policy_revision",
            return_value={"revision_token": "rev-growth", "package_code": "growth"},
        ):
            policy = load_account_effective_package_policy("account-a")
        self.assertEqual(policy["commercial_package_code"], "growth")
        self.assertEqual(policy["package_caps"]["like_day_cap"], 20)

    def test_likes_disabled_by_config_still_skips_without_like_attempt(self) -> None:
        device = MagicMock()
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", False, create=True)
            )
            stack.enter_context(
                patch.object(nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True)
            )
            stack.enter_context(
                patch(
                    "account_commercial_policy.commercial_policy_boundary_blocks_phase",
                    return_value=False,
                )
            )
            open_like_mock = stack.enter_context(patch.object(nav, "visual_like_open_post"))
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
                account_id="account-a",
                bound_commercial_policy_revision="rev-same",
            )
        open_like_mock.assert_not_called()
        self.assertEqual(out.get("skipped_reason"), "likes_disabled_by_config")


if __name__ == "__main__":
    unittest.main()
