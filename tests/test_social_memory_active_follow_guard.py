from __future__ import annotations

import unittest
from types import SimpleNamespace

import social_memory


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        SOCIAL_MEMORY_ENABLED=True,
        SOCIAL_MEMORY_REVISIT_COOLDOWN_DAYS=0,
        SOCIAL_MEMORY_FOLLOW_REVISIT_COOLDOWN_DAYS=0,
        SOCIAL_MEMORY_INTERACTION_COOLDOWN_DAYS=0,
        SOCIAL_MEMORY_MAX_SKIP_COUNT_DB=9999,
    )


class SocialMemoryActiveFollowGuardTest(unittest.TestCase):
    def _evaluate(self, db_row: dict) -> social_memory.FollowEligibility:
        return social_memory.evaluate_follow_eligibility(
            target_username="candidate",
            source_profile="source_ct",
            db_row=db_row,
            runtime_followed=set(),
            runtime_unfollowed=set(),
            runtime_interacted=set(),
            runtime_skipped=set(),
            config=_config(),
        )

    def test_active_following_is_blocked_even_when_all_cooldowns_are_zero(self) -> None:
        result = self._evaluate(
            {
                "username": "candidate",
                "interaction_type": "follow",
                "follow_status": "following",
                "was_successful": True,
                "unfollowed_at": None,
                "interaction_lifecycle_state": "active_following",
            }
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "persistent_active_follow_connection")
        self.assertEqual(result.interaction_state, social_memory.ACTIVE_FOLLOWING)

    def test_active_follow_status_is_blocked_without_lifecycle_column(self) -> None:
        result = self._evaluate(
            {
                "interaction_type": "follow",
                "follow_status": "following",
                "was_successful": True,
                "unfollowed_at": None,
            }
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.detail["persistent_reason"], "follow_status_active_connection")

    def test_unfollowed_completed_remains_blocked_by_existing_contract(self) -> None:
        result = self._evaluate(
            {
                "interaction_type": "follow",
                "follow_status": "unfollowed",
                "was_successful": True,
                "unfollowed_at": "2026-08-01T00:00:00+00:00",
                "interaction_lifecycle_state": "unfollowed_completed",
            }
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "lifecycle_unfollowed_completed")


if __name__ == "__main__":
    unittest.main()
