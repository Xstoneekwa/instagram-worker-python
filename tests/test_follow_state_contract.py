from __future__ import annotations

import unittest

import runner
from follow_state_contract import (
    FollowContext,
    FollowPhysicalState,
    candidate_follow_decision_apply_source,
    candidate_follow_decision_block,
    candidate_follow_decision_blocks_follow,
    evaluate_like_precheck_contract,
    new_candidate_follow_decision,
)


class FollowStateContractTransitionsTest(unittest.TestCase):
    def test_private_trace_blocks_follow_allowed(self) -> None:
        ctx = FollowContext.new(follower_username="private_user")
        ctx.mark_profile_opened()
        ctx.apply_nav_trace_decision(
            {
                "private_detected": True,
                "candidate_allowed_to_follow": False,
                "blocked_reason": "private_account",
                "source_of_decision": "candidate_profile_analysis",
            }
        )
        self.assertEqual(ctx.current_state, FollowPhysicalState.FOLLOW_BLOCKED)
        self.assertTrue(ctx.blocks_follow())
        ok, reason = ctx.assert_can_perform_follow_safe()
        self.assertFalse(ok)
        self.assertEqual(reason, "private_account")

    def test_should_follow_false_trace_blocks(self) -> None:
        ctx = FollowContext.new(follower_username="blocked_user")
        ctx.apply_nav_trace_decision(
            {
                "candidate_allowed_to_follow": False,
                "blocked_reason": "should_follow_false",
            },
            source="candidate_profile_analysis",
        )
        self.assertTrue(ctx.blocks_follow())
        self.assertFalse(ctx.private_detected)

    def test_public_trace_then_allow_follow(self) -> None:
        ctx = FollowContext.new(follower_username="public_user")
        ctx.apply_nav_trace_decision(
            {"candidate_allowed_to_follow": True, "private_detected": False},
        )
        ctx.allow_follow(source="pre_follow_gates_passed")
        ok, reason = ctx.assert_can_perform_follow_safe()
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertEqual(ctx.current_state, FollowPhysicalState.FOLLOW_ALLOWED)

    def test_private_gate_reject_blocks(self) -> None:
        ctx = FollowContext.new(follower_username="priv")
        ctx.apply_private_gate_result({"reject": True, "private_profile_detected": True})
        self.assertTrue(ctx.private_detected)
        ok, _ = ctx.assert_can_perform_follow_safe()
        self.assertFalse(ok)

    def test_legacy_dict_helpers_block_and_apply(self) -> None:
        decision = new_candidate_follow_decision(follower_username="u1")
        decision = candidate_follow_decision_apply_source(
            decision,
            {"private_detected": True, "source_of_decision": "screen_guard"},
            source="screen_guard",
        )
        self.assertTrue(candidate_follow_decision_blocks_follow(decision))
        self.assertEqual(decision["current_state"], FollowPhysicalState.FOLLOW_BLOCKED.value)

    def test_legacy_block_helper(self) -> None:
        decision = new_candidate_follow_decision()
        decision = candidate_follow_decision_block(
            decision,
            reason="private_account",
            source="pre_follow_private_gate",
            private_detected=True,
        )
        self.assertFalse(decision["candidate_allowed_to_follow"])


class FollowStateContractLikePrecheckTest(unittest.TestCase):
    def test_mute_sheet_still_open_no_like_surface_ready(self) -> None:
        _ctx, ok, reason = evaluate_like_precheck_contract(
            sheet_precheck={
                "sheet_visible": True,
                "dismissed": False,
                "still_open": True,
                "skip_like": True,
                "skip_reason": "mute_sheet_still_open",
            },
            surface_precheck={},
            follower_username="cand",
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "mute_sheet_still_open")
        self.assertNotEqual(_ctx.current_state, FollowPhysicalState.LIKE_SURFACE_READY)

    def test_sheet_not_visible_profile_visible_allows_probe(self) -> None:
        _ctx, ok, reason = evaluate_like_precheck_contract(
            sheet_precheck={
                "sheet_visible": False,
                "dismissed": False,
                "still_open": False,
                "skip_like": False,
            },
            surface_precheck={
                "followers_list_visible": False,
                "profile_candidate_visible": True,
                "skip_like": False,
                "skip_reason": "",
            },
            follower_username="cand",
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertEqual(_ctx.current_state, FollowPhysicalState.POST_GRID_READY)

    def test_followers_list_visible_blocks_grid_probe(self) -> None:
        _ctx, ok, reason = evaluate_like_precheck_contract(
            sheet_precheck={"sheet_visible": False, "skip_like": False},
            surface_precheck={
                "followers_list_visible": True,
                "profile_candidate_visible": False,
                "skip_like": True,
                "skip_reason": "followers_list_visible_before_like",
            },
            follower_username="cand",
        )
        self.assertFalse(ok)
        self.assertIn("followers_list", reason)

    def test_profile_not_visible_blocks_like(self) -> None:
        _ctx, ok, reason = evaluate_like_precheck_contract(
            sheet_precheck={"sheet_visible": False, "skip_like": False},
            surface_precheck={
                "followers_list_visible": False,
                "profile_candidate_visible": False,
                "skip_like": True,
                "skip_reason": "candidate_profile_not_visible_before_like",
            },
            follower_username="cand",
        )
        self.assertFalse(ok)

    def test_like_precheck_uses_carried_follow_verified_context(self) -> None:
        ctx = FollowContext.from_follow_verified(
            follower_username="cand",
            source_profile_username="ct",
            visual_candidate_id="vc-1",
            follow_state_after="following",
        )
        out_ctx, ok, reason = evaluate_like_precheck_contract(
            sheet_precheck={"sheet_visible": False, "skip_like": False},
            surface_precheck={
                "followers_list_visible": False,
                "profile_candidate_visible": True,
                "skip_like": False,
                "skip_reason": "",
            },
            follower_username="cand",
            source_profile_username="ct",
            visual_candidate_id="vc-1",
            initial_context=ctx,
        )

        self.assertIs(out_ctx, ctx)
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertEqual(ctx.transition_history[1]["to_state"], FollowPhysicalState.FOLLOW_VERIFIED.value)
        self.assertEqual(ctx.current_state, FollowPhysicalState.POST_GRID_READY)

    def test_profile_lost_marks_post_grid_blocked(self) -> None:
        ctx = FollowContext.from_follow_verified(
            follower_username="cand",
            source_profile_username="ct",
            visual_candidate_id="vc-1",
            follow_state_after="following",
        )
        _ctx, ok, reason = evaluate_like_precheck_contract(
            sheet_precheck={"sheet_visible": False, "skip_like": False},
            surface_precheck={
                "followers_list_visible": False,
                "profile_candidate_visible": False,
                "skip_like": True,
                "skip_reason": "post_follow_candidate_profile_lost",
            },
            follower_username="cand",
            source_profile_username="ct",
            visual_candidate_id="vc-1",
            initial_context=ctx,
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "post_follow_candidate_profile_lost")
        self.assertEqual(ctx.current_state, FollowPhysicalState.POST_GRID_BLOCKED)


class FollowStateContractRunnerGuardTest(unittest.TestCase):
    def test_assert_follow_allowed_requires_follow_allowed_state(self) -> None:
        decision = new_candidate_follow_decision(follower_username="u2")
        ok, _deny, updated = runner._candidate_follow_decision_assert_follow_allowed(
            decision,
            source="final_pre_follow_guard",
        )
        self.assertTrue(ok)
        self.assertEqual(updated.get("current_state"), FollowPhysicalState.FOLLOW_ALLOWED.value)

    def test_assert_follow_allowed_denies_blocked(self) -> None:
        decision = candidate_follow_decision_block(
            new_candidate_follow_decision(follower_username="priv"),
            reason="private_account",
            source="pre_follow_private_gate",
            private_detected=True,
        )
        ok, deny, _ = runner._candidate_follow_decision_assert_follow_allowed(decision)
        self.assertFalse(ok)
        self.assertEqual(deny, "private_account")
