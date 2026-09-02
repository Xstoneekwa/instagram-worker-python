import unittest
from unittest.mock import patch

import follow_revert_restriction as guard
import runner


def observation(username, **overrides):
    values = {
        "candidate_username": username,
        "tap_sent": True,
        "follow_state_before": "follow",
        "follow_state_after": "follow",
        "failure_code": 34,
        "exact_candidate_profile_proved": True,
        "exact_followers_surface_recovered": True,
        "follow_persisted": False,
        "navigation_drift_detected": False,
    }
    values.update(overrides)
    return guard.FollowRevertObservation(**values)


class FollowRevertRestrictionTests(unittest.TestCase):
    def test_three_distinct_consecutive_reverts_trigger(self):
        tracker = guard.DistinctFollowRevertTracker()
        self.assertFalse(tracker.observe(observation("a"))["triggered"])
        self.assertFalse(tracker.observe(observation("b"))["triggered"])
        out = tracker.observe(observation("c"))
        self.assertTrue(out["triggered"])
        self.assertEqual(out["candidates"], ("a", "b", "c"))

    def test_success_or_non_strict_outcome_resets_streak(self):
        tracker = guard.DistinctFollowRevertTracker()
        tracker.observe(observation("a"))
        tracker.observe(observation("b"))
        out = tracker.observe(observation("c", follow_state_after="following"))
        self.assertEqual(out["streak_count"], 0)
        self.assertFalse(out["triggered"])

    def test_duplicate_candidate_does_not_advance(self):
        tracker = guard.DistinctFollowRevertTracker()
        tracker.observe(observation("a"))
        out = tracker.observe(observation("a"))
        self.assertEqual(out["streak_count"], 1)
        self.assertFalse(out["triggered"])

    def test_unrecovered_surface_is_not_strict(self):
        ok, reason = guard.classify_strict_follow_revert(
            observation("a", exact_followers_surface_recovered=False)
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "exact_followers_surface_not_recovered")

    @patch.object(guard.supabase_client, "apply_instagram_action_restriction")
    def test_preventive_hold_reuses_canonical_rpc_reason(self, apply):
        apply.return_value = {
            "ok": True,
            "incident_id": "incident-1",
            "dedupe_key": "account:a:instagram_action_rate_limit:v2",
        }
        with self.assertRaises(guard.InstagramActionRestrictionDetected) as raised:
            guard.apply_preventive_follow_restriction(
                device=object(),
                account_id="account-1",
                account_username="mythyl_fitness",
                run_id="run-1",
                request_id="request-1",
                candidate_username="c",
                source_profile_username="rogierhesp",
                streak_candidates=("a", "b", "c"),
                actions_completed={"follows": 18, "likes": 4},
            )
        payload = apply.call_args.args[0]
        self.assertEqual(payload["stable_reason"], "instagram_action_rate_limit")
        self.assertEqual(
            payload["metadata_safe"]["trigger_reason"],
            "preventive_follow_restriction",
        )
        self.assertEqual(raised.exception.summary["primary_status"], "48H pause required")

    @patch.object(runner, "_timed_safe_supabase_call")
    def test_verified_post_first_like_persists_when_follow_fails(self, persist):
        persist.return_value = {"_supabase_call_ok": True, "value": {"ok": True}}
        ok, liked_count = runner._persist_precompleted_like_after_follow_failure(
            precompleted_like_result={
                "phase_outcome": "success",
                "liked_count": 1,
                "target_count": 1,
                "attempted_count": 1,
                "skipped_already_liked_count": 0,
                "post_like_mode": "ordering_v2_post_first",
                "timings_ms": {"total": 10.0},
                "per_post": [{"index": 1, "verified": True}],
            },
            supabase_mode=True,
            account_id="account-1",
            candidate_username="candidate",
            source_profile_username="source",
            run_id="run-1",
            session_id="session-1",
            visual_candidate_id="visual-1",
        )
        self.assertTrue(ok)
        self.assertEqual(liked_count, 1)
        self.assertEqual(
            persist.call_args.args[:2],
            (
                "record_post_like_interaction_success_after_follow_failure",
                "record_post_like_interaction_success",
            ),
        )
        self.assertEqual(persist.call_args.kwargs["liked_count"], 1)

    @patch.object(runner, "_timed_safe_supabase_call")
    def test_verified_like_persistence_failure_blocks_next_candidate(self, persist):
        persist.return_value = {"_supabase_call_ok": False, "value": None}
        ok, liked_count = runner._persist_precompleted_like_after_follow_failure(
            precompleted_like_result={
                "phase_outcome": "partial_success",
                "liked_count": 1,
            },
            supabase_mode=True,
            account_id="account-1",
            candidate_username="candidate",
            source_profile_username="source",
            run_id="run-1",
            session_id="session-1",
            visual_candidate_id="visual-1",
        )
        self.assertFalse(ok)
        self.assertEqual(liked_count, 0)


if __name__ == "__main__":
    unittest.main()
