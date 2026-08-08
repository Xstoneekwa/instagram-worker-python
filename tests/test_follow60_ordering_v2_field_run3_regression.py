from __future__ import annotations

import inspect
import json
import statistics
import tempfile
import unittest
from pathlib import Path

import follow_action_engine
import follow60_ordering_v2_ledger_v1 as ledger
import instagram_navigation


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "follow60_ordering_v2_field_run3.json"
)


class Follow60OrderingV2FieldRun3RegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.field = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _store(self, suffix: str) -> ledger.DurableOrderingLedgerV1:
        return ledger.DurableOrderingLedgerV1(
            ledger.LedgerScope(
                account_id="account-a",
                run_id=f"run-{suffix}",
                request_id=f"request-{suffix}",
                business_session_id="session-a",
                target_id="target-a",
                action_id=f"action-{suffix}",
                candidate_username=f"candidate-{suffix}",
            ),
            path=Path(self.temp.name) / f"{suffix}.sqlite3",
        )

    def test_field_fixture_proves_three_identical_generation_failures(self) -> None:
        cycles = self.field["cycles"]
        self.assertEqual(3, len(cycles))
        self.assertTrue(all(cycle["like_verified"] for cycle in cycles))
        self.assertTrue(all(cycle["profile_returned"] for cycle in cycles))
        self.assertEqual(
            {"v2_reentry_generation_missing"},
            {cycle["reentry_live_reason"] for cycle in cycles},
        )
        self.assertEqual(
            858.96,
            statistics.median(cycle["intent_age_ms"] for cycle in cycles),
        )
        self.assertEqual(
            {
                "golden_calls": 0,
                "reveal_calls": 0,
                "extra_xml": 0,
                "extra_screenshots": 0,
                "extra_polls": 0,
                "extra_taps": 0,
            },
            self.field["v2_operation_budget"],
        )

    def test_post_like_graph_requires_reentry_follow_mute_and_return(self) -> None:
        store = self._store("complete")
        stages = (
            "profile_certified",
            "post_opened",
            "like_verified",
            "profile_reentry_verified",
            "follow_pending",
            "follow_verified",
            "mute_posts_verified",
            "mute_stories_verified",
            "return_ct_exact",
            "cycle_complete",
        )
        for stage in stages:
            store.apply_receipt(stage, {"verified": True})
        state = store.load()
        self.assertTrue(state.cycle_complete)
        self.assertTrue(set(stages).issubset(state.stages))

    def test_reentry_failure_cannot_be_recorded_as_normal_return(self) -> None:
        store = self._store("reentry-failed")
        for stage in ("profile_certified", "post_opened", "like_verified"):
            store.apply_receipt(stage, {"verified": True})
        state = store.load()
        self.assertEqual("profile_reentry_verified", state.next_stage())
        self.assertNotIn("follow_pending", state.stages)
        self.assertNotIn("return_ct_exact", state.stages)
        self.assertFalse(state.cycle_complete)

    def test_partial_mute_and_stop_boundaries_never_invent_acks(self) -> None:
        partial = self._store("partial-mute")
        for stage in (
            "profile_certified",
            "post_opened",
            "like_verified",
            "profile_reentry_verified",
            "follow_pending",
            "follow_verified",
            "mute_posts_verified",
        ):
            partial.apply_receipt(stage, {"verified": True})
        partial_state = partial.load()
        self.assertEqual("mute_stories_verified", partial_state.next_stage())
        self.assertFalse(partial_state.cycle_complete)

        for suffix, prefix in (
            ("stop-after-like", ("profile_certified", "post_opened", "like_verified")),
            (
                "stop-during-follow",
                (
                    "profile_certified",
                    "post_opened",
                    "like_verified",
                    "profile_reentry_verified",
                    "follow_pending",
                ),
            ),
        ):
            store = self._store(suffix)
            for stage in prefix:
                store.apply_receipt(stage, {"verified": True})
            store.apply_receipt("stop_recorded", {"source": "operator"})
            state = store.load()
            self.assertIn("stop_recorded", state.stages)
            self.assertNotIn("follow_verified", state.stages)
            self.assertNotIn("return_ct_exact", state.stages)
            self.assertFalse(state.cycle_complete)

    def test_v2_happy_path_defers_only_duplicate_live_surface_read(self) -> None:
        phase_source = inspect.getsource(
            instagram_navigation.run_post_follow_post_likes_phase
        )
        dispatch_source = inspect.getsource(
            instagram_navigation._dispatch_post_open_intent_v2_tap
        )
        reentry_source = inspect.getsource(
            follow_action_engine.capture_ordering_v2_profile_reentry_follow_surface
        )

        self.assertIn("live_validation_deferred_to_post_intent", phase_source)
        self.assertIn("duplicate_pre_intent_app_current", phase_source)
        self.assertEqual(1, dispatch_source.count("_followers_current_pkg_activity(d)"))
        self.assertNotIn("dump_hierarchy", dispatch_source)
        self.assertNotIn("screenshot", dispatch_source)
        self.assertNotIn("sleep(", dispatch_source)
        self.assertNotIn("visual_open_recent_post_from_profile", dispatch_source)
        self.assertIn(
            '_follow60_invalidate("ordering_v2_profile_reentry_navigation")',
            reentry_source,
        )


if __name__ == "__main__":
    unittest.main()
