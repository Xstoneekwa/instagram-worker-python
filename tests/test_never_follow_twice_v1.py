from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

import social_memory


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260823130933_never_follow_twice_v1.sql"


def config() -> SimpleNamespace:
    return SimpleNamespace(
        SOCIAL_MEMORY_ENABLED=True,
        SOCIAL_MEMORY_REVISIT_COOLDOWN_DAYS=0,
        SOCIAL_MEMORY_FOLLOW_REVISIT_COOLDOWN_DAYS=0,
        SOCIAL_MEMORY_INTERACTION_COOLDOWN_DAYS=0,
        SOCIAL_MEMORY_MAX_SKIP_COUNT_DB=9999,
    )


def eligibility(row: dict | None) -> social_memory.FollowEligibility:
    return social_memory.evaluate_follow_eligibility(
        target_username="@Candidate",
        source_profile="source_ct",
        db_row=row,
        runtime_followed=set(),
        runtime_unfollowed=set(),
        runtime_interacted=set(),
        runtime_skipped=set(),
        config=config(),
    )


class NeverFollowTwiceSocialMemoryTest(unittest.TestCase):
    def test_canonical_follow_projection_permanently_blocks_future_admission(self) -> None:
        result = eligibility(
            {
                "username": "candidate",
                "ever_followed_canonical_at": "2026-08-23T10:00:00+00:00",
                "ever_followed_action_id": "11111111-1111-4111-8111-111111111111",
                "follow_status": "unfollowed",
                "interaction_lifecycle_state": "unfollowed_completed",
                "unfollowed_at": "2026-08-23T12:00:00+00:00",
            }
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "already_followed_canonical_once")
        self.assertTrue(result.detail["ever_followed_canonical"])

    def test_ct_or_day_does_not_affect_the_account_username_projection(self) -> None:
        row = {
            "username": "candidate",
            "ever_followed_canonical_at": "2026-01-01T00:00:00+00:00",
            "ever_followed_action_id": "22222222-2222-4222-8222-222222222222",
            "last_source_profile": "different_ct",
            "last_run_id": "33333333-3333-4333-8333-333333333333",
        }
        self.assertEqual(eligibility(row).reason, "already_followed_canonical_once")

    def test_partial_projection_is_unknown_and_fail_closed_not_promoted(self) -> None:
        row = {"ever_followed_canonical_at": "2026-08-23T10:00:00+00:00"}
        complete, incomplete, detail = (
            social_memory.durable_ever_followed_canonical_projection(row)
        )
        self.assertFalse(complete)
        self.assertTrue(incomplete)
        self.assertFalse(detail["ever_followed_canonical"])
        self.assertEqual(eligibility(row).reason, "canonical_follow_projection_incomplete")

    def test_ui_only_following_does_not_fabricate_sticky_canonical_truth(self) -> None:
        complete, incomplete, _ = social_memory.durable_ever_followed_canonical_projection(
            {"follow_status": "following", "evidence_source": "ui_observation"}
        )
        self.assertFalse(complete)
        self.assertFalse(incomplete)

    def test_like_and_mute_existing_guards_are_preserved(self) -> None:
        like = eligibility({"posts_liked_count": 1})
        mute = eligibility(
            {
                "last_muted_at": "2026-08-23T10:00:00+00:00",
                "muted_posts": True,
            }
        )
        self.assertEqual(like.reason, "durable_already_interacted_like")
        self.assertEqual(mute.reason, "durable_already_interacted_mute")

    def test_ambiguous_follow_remains_candidate_local_and_not_permanent_truth(self) -> None:
        row = {"payload": {"follow_mutation_ambiguous": {"durable": True}}}
        complete, _, _ = social_memory.durable_ever_followed_canonical_projection(row)
        self.assertFalse(complete)
        self.assertEqual(
            eligibility(row).reason,
            "durable_follow_mutation_ambiguous_unreconciled",
        )

    def test_clean_candidate_remains_eligible(self) -> None:
        self.assertTrue(eligibility(None).allowed)


class NeverFollowTwiceMigrationContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()

    def test_forward_projection_is_monotone_and_canonical_only(self) -> None:
        self.assertIn("add column if not exists ever_followed_canonical_at", self.sql)
        self.assertIn("add column if not exists ever_followed_action_id", self.sql)
        self.assertIn("event.event_type = 'follow_verified_persisted_v1'", self.sql)
        self.assertIn("event.event_status = 'success'", self.sql)
        self.assertIn("coalesce(iu.ever_followed_canonical_at", self.sql)
        self.assertIn("coalesce(iu.ever_followed_action_id", self.sql)
        self.assertIn("ever_followed_projection_interaction_row_missing", self.sql)

    def test_rpc_path_rejects_new_action_but_allows_exact_action_replay(self) -> None:
        self.assertIn("pg_advisory_xact_lock", self.sql)
        self.assertIn("prior.id is distinct from new.id", self.sql)
        self.assertIn("follow_persistence_canonical_once_already_exists", self.sql)
        self.assertIn("before insert or update", self.sql)

    def test_backfill_is_projection_only(self) -> None:
        backfill = self.sql[self.sql.index("with canonical_first as"):]
        self.assertIn("update public.ig_interacted_users", backfill)
        self.assertNotIn("insert into public.ig_interaction_events", backfill)
        self.assertNotIn("total_follow", backfill)
        self.assertNotIn("follow_status =", backfill)
        self.assertNotIn("interaction_lifecycle_state =", backfill)

    def test_projection_cannot_be_fabricated_cleared_or_deleted(self) -> None:
        self.assertIn("ever_followed_projection_canonical_proof_missing", self.sql)
        self.assertIn("ever_followed_projection_is_monotone", self.sql)
        self.assertIn("ever_followed_projection_row_delete_forbidden", self.sql)


if __name__ == "__main__":
    unittest.main()
