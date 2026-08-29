from __future__ import annotations

import inspect
import unittest

import runner
from follow_outcome_contract import build_follow_outcome


SEE_MORE_REASON = "see_more_click_exhausted_after_bounded_recovery"


class FollowSeeMorePolicyUnificationV1Tests(unittest.TestCase):
    def test_normal_and_resume_share_expand_before_swipe_decision(self) -> None:
        continuation = {
            "state": "EXPAND_PRIMARY_LIST_AVAILABLE",
            "see_more_visible": True,
            "selected_followers_tab": True,
        }
        normal = runner._followers_pre_scroll_contract_decision(
            continuation,
            followers_list_proved=True,
        )
        resume_veto = runner._followers_resume_pre_scroll_see_more_veto(
            continuation,
            followers_list_proved=True,
        )
        self.assertEqual(normal, "expand_primary_list")
        self.assertTrue(resume_veto)

    def test_resume_without_actionable_see_more_keeps_checkpoint_scroll(self) -> None:
        continuation = {
            "state": "PRIMARY_ROWS_AVAILABLE",
            "see_more_visible": False,
            "selected_followers_tab": True,
        }
        self.assertFalse(
            runner._followers_resume_pre_scroll_see_more_veto(
                continuation,
                followers_list_proved=True,
            )
        )

    def test_resume_fast_forward_calls_shared_handler_before_scroll(self) -> None:
        source = inspect.getsource(runner._run_followers_list_engine_session)
        start = source.index("ct_resume_pre_scroll_boundary_check")
        end = source.index("ct_resume_v4_enforce_scroll_applied", start)
        fast_forward = source[start:end]
        handler = fast_forward.index("followers_try_expand_primary_list(")
        swipe = fast_forward.index("scroll_followers_list_forward(")
        self.assertLess(handler, swipe)
        self.assertIn("physical_swipe_attempted=False", fast_forward)
        self.assertNotIn("followers_refresh_detect_hierarchy_cache", fast_forward)
        self.assertNotIn("dump_hierarchy", fast_forward)
        self.assertNotIn("time.sleep", fast_forward)

    def test_ct_resume_marks_checkpoint_viewport_exhausted_without_advancing_it(self) -> None:
        source = inspect.getsource(runner._run_followers_list_engine_session)
        start = source.index("_ff_processed_rows =")
        handler = source.index("followers_try_expand_primary_list(", start)
        swipe = source.index("scroll_followers_list_forward(", handler)
        checkpoint_stage = source.index("note_legacy_scroll_progress(", swipe)
        fast_forward = source[start:swipe]
        self.assertIn("visible_primary_rows_exhausted=True", fast_forward)
        self.assertLess(handler, swipe)
        self.assertGreater(checkpoint_stage, swipe)
        self.assertNotIn("commit_verified_progress(", source[handler:swipe])

    def test_normal_exhausted_viewport_uses_same_handler_before_scroll(self) -> None:
        source = inspect.getsource(runner._run_followers_list_engine_session)
        start = source.index("_visible_window_processed_rows =")
        handler = source.index("followers_try_expand_primary_list(", start)
        swipe = source.index("scroll_followers_list_forward(", handler)
        normal = source[start:swipe]
        self.assertIn("visible_primary_rows_exhausted=True", normal)
        self.assertIn("_followers_pre_scroll_contract_decision", normal)
        self.assertLess(handler, swipe)

    def test_debug_capture_is_not_before_selector_dispatch(self) -> None:
        import instagram_navigation as nav

        source = inspect.getsource(nav.followers_try_expand_primary_list)
        click = source.index("selector.click()")
        capture = source.index("see_more_after_dispatch_")
        self.assertLess(click, capture)
        self.assertNotIn("see_more_before_", source[:click])

    def test_see_more_failure_rotates_when_another_ct_remains(self) -> None:
        outcome = build_follow_outcome(
            stable_reason=SEE_MORE_REASON,
            verified_actions=119,
            target_actions=120,
            current_target_id="ct-final-local",
            remaining_target_ids=["ct-next"],
            safe_boundary=True,
        )
        self.assertEqual(outcome["phase_status"], "partial_resumable")
        self.assertEqual(outcome["scope"], "current_ct")
        self.assertEqual(outcome["safe_next_step"], "rotate_next_ct")

    def test_final_ct_see_more_failure_schedules_resume(self) -> None:
        outcome = build_follow_outcome(
            stable_reason=SEE_MORE_REASON,
            verified_actions=119,
            target_actions=120,
            current_target_id="ct-final",
            remaining_target_ids=[],
            safe_boundary=True,
        )
        self.assertEqual(outcome["phase_status"], "partial_resumable")
        self.assertEqual(outcome["scope"], "current_ct")
        self.assertTrue(outcome["resumable"])
        self.assertEqual(outcome["safe_next_step"], "schedule_resume")

    def test_genuine_instagram_blocker_remains_global(self) -> None:
        outcome = build_follow_outcome(
            stable_reason="instagram_challenge_detected",
            verified_actions=119,
            target_actions=120,
            remaining_target_ids=["ct-next"],
            safe_boundary=True,
        )
        self.assertEqual(outcome["phase_status"], "blocked_critical")
        self.assertEqual(outcome["scope"], "account_session")
        self.assertEqual(outcome["safe_next_step"], "end_session")


if __name__ == "__main__":
    unittest.main()
