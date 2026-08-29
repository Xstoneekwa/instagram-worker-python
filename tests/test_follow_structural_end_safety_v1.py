from __future__ import annotations

import unittest

from instagram_list_continuation import (
    FollowersContinuationMemory,
    InstagramListContinuationSignals,
    InstagramListContinuationState as State,
    classify_instagram_list_continuation,
)
import runner


def _signals(**overrides: object) -> InstagramListContinuationSignals:
    values: dict[str, object] = {
        "flow": "follow",
        "expected_surface_selected": True,
        "structural_end_contract_enabled": True,
        "list_identity_stable": True,
    }
    values.update(overrides)
    return InstagramListContinuationSignals(**values)


class FollowStructuralEndSafetyV1Tests(unittest.TestCase):
    def test_a_actionable_see_more_expands_before_any_swipe(self) -> None:
        state = classify_instagram_list_continuation(
            _signals(
                primary_row_ids=("a", "b", "c", "d", "e", "f"),
                processed_primary_row_ids=("a", "b", "c", "d", "e", "f"),
                visible_primary_rows_exhausted=True,
                see_more_visible=True,
                see_more_actionable=True,
                see_more_before_suggestions=True,
                suggestions_visible=True,
            )
        )
        self.assertIs(state, State.EXPAND_PRIMARY_LIST_AVAILABLE)
        self.assertEqual(
            runner._followers_pre_scroll_contract_decision(
                {
                    "state": state.value,
                    "structural_end_contract_enabled": True,
                    "see_more_visible": True,
                    "see_more_actionable": True,
                    "see_more_before_suggestions": True,
                    "visible_primary_rows_exhausted": True,
                },
                followers_list_proved=True,
            ),
            "expand_primary_list",
        )

    def test_b_suggested_visible_but_continuation_unproved_is_not_end(self) -> None:
        state = classify_instagram_list_continuation(
            _signals(
                suggestions_visible=True,
                structural_bottom_signature=True,
                bounded_continuation_attempted=False,
                stable_observation_count=1,
            )
        )
        self.assertIs(state, State.TRANSIENT_NO_ROWS)

    def test_c_bounded_continuation_finds_primary_rows_is_not_end(self) -> None:
        state = classify_instagram_list_continuation(
            _signals(
                primary_row_ids=("new_primary",),
                suggestions_visible=False,
                bounded_continuation_attempted=True,
                bounded_continuation_no_progress=False,
                stable_observation_count=2,
            )
        )
        self.assertIs(state, State.PRIMARY_ROWS_AVAILABLE)

    def test_d_positive_stable_structural_bottom_allows_rotation(self) -> None:
        state = classify_instagram_list_continuation(
            _signals(
                suggestions_visible=True,
                structural_bottom_signature=True,
                bounded_continuation_attempted=True,
                bounded_continuation_no_progress=True,
                stable_observation_count=2,
                checkpoint_continuity_pending=False,
            )
        )
        self.assertIs(state, State.STRUCTURAL_END_PROVED)
        contract = {
            "state": state.value,
            "is_boundary": True,
            "checkpoint_continuity_pending": False,
            "known_see_more_unresolved": False,
            "known_see_more_lost_after_own_scroll": False,
            "bounded_continuation_attempted": True,
            "bounded_continuation_no_progress": True,
            "list_identity_stable": True,
            "stable_observation_count": 2,
        }
        self.assertTrue(runner._followers_ct_rotation_allowed(contract))

    def test_e_known_see_more_lost_after_own_scroll_requires_recovery(self) -> None:
        memory = FollowersContinuationMemory()
        memory.bind(
            account_id="account-1",
            target_id="target-1",
            list_identity="target-1:followers",
            list_generation="checkpoint-1",
        )
        memory.observe(see_more_visible=True, see_more_actionable=True)
        memory.note_own_forward_scroll()
        self.assertTrue(
            memory.lost_after_own_scroll(
                see_more_visible=False,
                see_more_actionable=False,
            )
        )
        state = classify_instagram_list_continuation(
            _signals(
                suggestions_visible=True,
                structural_bottom_signature=True,
                bounded_continuation_attempted=True,
                bounded_continuation_no_progress=True,
                stable_observation_count=2,
                known_see_more_unresolved=True,
                known_see_more_lost_after_own_scroll=True,
            )
        )
        self.assertIs(state, State.CONTINUATION_RECOVERY_REQUIRED)
        self.assertFalse(
            runner._followers_ct_rotation_allowed(
                {
                    "state": state.value,
                    "is_boundary": False,
                    "known_see_more_unresolved": True,
                }
            )
        )

    def test_suggested_section_alone_is_not_primary_or_end(self) -> None:
        state = classify_instagram_list_continuation(
            _signals(suggestions_visible=True, structural_bottom_signature=True)
        )
        self.assertIs(state, State.TRANSIENT_NO_ROWS)

    def test_checkpoint_pending_blocks_otherwise_positive_end(self) -> None:
        state = classify_instagram_list_continuation(
            _signals(
                suggestions_visible=True,
                structural_bottom_signature=True,
                bounded_continuation_attempted=True,
                bounded_continuation_no_progress=True,
                stable_observation_count=2,
                checkpoint_continuity_pending=True,
            )
        )
        self.assertIs(state, State.TRANSIENT_NO_ROWS)

    def test_context_rebind_clears_unresolved_see_more(self) -> None:
        memory = FollowersContinuationMemory()
        memory.bind(
            account_id="a",
            target_id="one",
            list_identity="one:followers",
            list_generation="g1",
        )
        memory.observe(see_more_visible=True, see_more_actionable=True)
        memory.bind(
            account_id="a",
            target_id="two",
            list_identity="two:followers",
            list_generation="g2",
        )
        self.assertFalse(memory.known_see_more_unresolved)


if __name__ == "__main__":
    unittest.main()
