from __future__ import annotations

import unittest
from types import SimpleNamespace

import social_memory


def _decision(row):
    return social_memory.evaluate_follow_eligibility(
        target_username="@Candidate",
        source_profile="ct_any",
        db_row=row,
        runtime_followed=set(),
        runtime_unfollowed=set(),
        runtime_interacted=set(),
        runtime_skipped=set(),
        config=SimpleNamespace(SOCIAL_MEMORY_ENABLED=True),
    )


class AlreadyInteractedFollowExclusionV1Tests(unittest.TestCase):
    def test_durable_like_blocks_future_follow(self):
        out = _decision({"account_id": "a", "username": "candidate", "posts_liked_count": 1})
        self.assertFalse(out.allowed)
        self.assertEqual(out.reason, "durable_already_interacted_like")

    def test_durable_mute_blocks_future_follow(self):
        out = _decision({
            "account_id": "a", "username": "candidate", "last_muted_at": "2026-08-23T00:00:00Z",
            "muted_posts": True, "muted_stories": False,
        })
        self.assertFalse(out.allowed)
        self.assertEqual(out.reason, "durable_already_interacted_mute")

    def test_explicit_projected_markers_are_account_username_memory(self):
        row = {
            "account_id": "a", "username": "candidate",
            "payload": {"already_interacted_like": {"durable": True}},
        }
        self.assertFalse(_decision(row).allowed)
        # A CT change does not change the account+normalized-username row.
        out = social_memory.evaluate_follow_eligibility(
            target_username="candidate", source_profile="different_ct", db_row=row,
            runtime_followed=set(), runtime_unfollowed=set(), runtime_interacted=set(),
            runtime_skipped=set(), config=SimpleNamespace(SOCIAL_MEMORY_ENABLED=True),
        )
        self.assertFalse(out.allowed)

    def test_ambiguous_ui_like_without_durable_proof_does_not_false_block(self):
        out = _decision({
            "account_id": "a", "username": "candidate",
            "payload": {"ui_like_observed": True, "already_interacted_like": {"durable": False}},
        })
        self.assertTrue(out.allowed)

    def test_true_unresolved_physical_follow_ambiguity_blocks_candidate_locally(self):
        out = _decision({
            "account_id": "a", "username": "candidate",
            "payload": {
                "follow_mutation_ambiguous": {"durable": True, "resolved": False}
            },
        })
        self.assertFalse(out.allowed)
        self.assertEqual(out.reason, "durable_follow_mutation_ambiguous_unreconciled")

    def test_clean_admission_is_not_rechecked_inside_current_v2_transaction(self):
        clean = _decision(None)
        self.assertTrue(clean.allowed)
        # The newly durable Like affects a future admission only; the already
        # admitted transaction owns its one-way LIKE→FOLLOW→MUTE→RETURN_CT path.
        future = _decision({"posts_liked_count": 1})
        self.assertFalse(future.allowed)
        self.assertTrue(clean.allowed)


if __name__ == "__main__":
    unittest.main()
