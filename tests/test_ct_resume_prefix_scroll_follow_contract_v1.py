from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timedelta, timezone

import instagram_navigation as nav
import runner
import target_followers_progressive_resume_v2 as resume
from tests.test_target_followers_progressive_resume_v2 import (
    ACCOUNT_A,
    FULL_RELEASE_SHA,
    REQUEST_A,
    RUN_A,
    TARGET_A,
    FakeRpc,
    RepositoryAndControllerTests,
    checkpoint_row,
    legacy_scroll_diag,
)


class CtResumeScrollContractTests(unittest.TestCase):
    def controller(self, *, row=None, events=None):
        ctl = RepositoryAndControllerTests().controller(
            FakeRpc(row=row or checkpoint_row(shadow_last_safe_depth=0)),
            flags=resume.ResumeFlags(False, True),
            events=events,
        )
        ctl.source_request_id = REQUEST_A
        ctl.release_sha = FULL_RELEASE_SHA
        ctl.load_and_plan()
        self.assertTrue(ctl.claim())
        return ctl

    def test_01_ct_uses_follow_adaptive_primitive_with_one_attempt(self):
        source = inspect.getsource(runner._run_followers_list_engine_session)
        self.assertIn('scroll_profile="canonical_adaptive"', source)
        self.assertIn("canonical_attempt_limit=1", source)
        self.assertNotIn('scroll_profile="canonical_controlled"', source.split("# CT Resume", 1)[1].split("_ff_verdict", 1)[0])

    def test_02_follow_geometry_targets_one_row_overlap(self):
        geometry = nav.adaptive_follow_scroll_geometry(
            1080, 2400, [300, 450, 600, 750, 900, 1050, 1200, 1350]
        )
        self.assertEqual(geometry["target_overlap_rows"], 1)
        self.assertEqual(geometry["target_new_rows"], 7)

    def test_03_two_row_overlap_and_six_new_is_staged(self):
        before = [f"old{i}" for i in range(8)]
        after = before[-2:] + [f"new{i}" for i in range(6)]
        ctl = self.controller()
        ctl.observe_viewport(before, followers_surface_confirmed=True, expected_target_confirmed=True)
        verdict = ctl.note_legacy_scroll_progress(
            legacy_scroll_diag(before, after),
            observed_scroll_index=1,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertEqual(verdict.reason, "legacy_scroll_proof_staged")
        self.assertTrue(ctl.observe_viewport(after, followers_surface_confirmed=True, expected_target_confirmed=True).verified)
        self.assertEqual(ctl.reached_depth, 1)

    def test_04_six_overlap_one_new_is_under_progressed(self):
        before = [f"old{i}" for i in range(8)]
        after = before[-6:] + ["new0"]
        ctl = self.controller()
        ctl.observe_viewport(before, followers_surface_confirmed=True, expected_target_confirmed=True)
        verdict = ctl.note_legacy_scroll_progress(
            legacy_scroll_diag(before, after),
            observed_scroll_index=1,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertEqual(verdict.reason, "ct_resume_scroll_under_progressed")
        self.assertEqual(ctl.reached_depth, 0)
        self.assertIsNone(ctl.pending_legacy_scroll)

    def test_05_three_overlap_is_outside_target(self):
        before = [f"old{i}" for i in range(8)]
        after = before[-3:] + [f"new{i}" for i in range(5)]
        ctl = self.controller()
        ctl.observe_viewport(before, followers_surface_confirmed=True, expected_target_confirmed=True)
        verdict = ctl.note_legacy_scroll_progress(
            legacy_scroll_diag(before, after),
            observed_scroll_index=1,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertEqual(verdict.reason, "ct_resume_overlap_outside_target")
        self.assertEqual(ctl.reached_depth, 0)

    def test_06_zero_overlap_falls_back_without_depth(self):
        before, after = ["a", "b"], ["c", "d"]
        ctl = self.controller()
        ctl.observe_viewport(before, followers_surface_confirmed=True, expected_target_confirmed=True)
        verdict = ctl.note_legacy_scroll_progress(
            legacy_scroll_diag(before, after),
            observed_scroll_index=1,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertEqual(verdict.reason, "continuity_unproven")
        self.assertEqual(ctl.reached_depth, 0)

    def test_07_wrong_target_falls_back(self):
        before, after = ["a", "b"], ["b", "c"]
        ctl = self.controller()
        ctl.observe_viewport(before, followers_surface_confirmed=True, expected_target_confirmed=True)
        verdict = ctl.note_legacy_scroll_progress(
            legacy_scroll_diag(before, after),
            observed_scroll_index=1,
            followers_surface_confirmed=True,
            expected_target_confirmed=False,
        )
        self.assertEqual(verdict.reason, "target_identity_changed")

    def test_08_fingerprint_mismatch_after_collection_never_advances(self):
        before, expected_after = ["a", "b"], ["b", "c"]
        ctl = self.controller()
        ctl.observe_viewport(before, followers_surface_confirmed=True, expected_target_confirmed=True)
        ctl.note_legacy_scroll_progress(
            legacy_scroll_diag(before, expected_after),
            observed_scroll_index=1,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        verdict = ctl.observe_viewport(["b", "different"], followers_surface_confirmed=True, expected_target_confirmed=True)
        self.assertEqual(verdict.reason, "continuity_unproven")
        self.assertEqual(ctl.reached_depth, 0)

    def test_09_non_follow_primitive_is_rejected(self):
        before, after = ["a", "b"], ["b", "c"]
        diag = legacy_scroll_diag(before, after)
        diag["scroll_primitive_source"] = "duplicated_ct_swipe"
        ctl = self.controller()
        ctl.observe_viewport(before, followers_surface_confirmed=True, expected_target_confirmed=True)
        self.assertEqual(
            ctl.note_legacy_scroll_progress(diag, observed_scroll_index=1, followers_surface_confirmed=True, expected_target_confirmed=True).reason,
            "continuity_unproven",
        )


class CtResumeCertifiedPrefixTests(unittest.TestCase):
    def proof(self, visible, checkpoint_handles, **overrides):
        args = {
            "checkpoint_fingerprint": resume.viewport_fingerprint(checkpoint_handles),
            "checkpoint_depth": 1,
            "checkpoint_version": 3,
            "checkpoint_target_matches": True,
            "checkpoint_surface_matches": True,
        }
        args.update(overrides)
        return resume.find_certified_checkpoint_prefix_cursor(
            visible,
            resume.bounded_anchor_hashes(checkpoint_handles),
            **args,
        )

    def test_10_exact_two_row_suffix_prefix_is_skipped(self):
        self.assertEqual(
            self.proof(["old7", "old8", "new1"], [f"old{i}" for i in range(1, 9)]),
            (2, "anchor_found_certified_prefix"),
        )

    def test_11_exact_one_row_suffix_prefix_is_skipped(self):
        self.assertEqual(
            self.proof(["old8", "new1"], [f"old{i}" for i in range(1, 9)]),
            (1, "anchor_found_certified_prefix"),
        )

    def test_12_unknown_username_before_anchor_fails_closed(self):
        cursor, reason = self.proof(["unknown", "old8", "new1"], [f"old{i}" for i in range(1, 9)])
        self.assertEqual((cursor, reason), (0, "anchor_prefix_continuity_unproven"))

    def test_13_order_gap_fails_closed(self):
        cursor, reason = self.proof(["old7", "old6", "new1"], [f"old{i}" for i in range(1, 9)])
        self.assertEqual((cursor, reason), (0, "anchor_prefix_continuity_unproven"))

    def test_14_wrong_target_fails_closed(self):
        self.assertEqual(
            self.proof(["old8", "new1"], ["old7", "old8"], checkpoint_target_matches=False)[0],
            0,
        )

    def test_15_stale_or_legacy_checkpoint_fails_closed(self):
        self.assertEqual(
            self.proof(["old8", "new1"], ["old7", "old8"], checkpoint_version=2)[0],
            0,
        )
        self.assertEqual(
            self.proof(["old8", "new1"], ["old7", "old8"], checkpoint_fingerprint="bad")[0],
            0,
        )

    def test_16_more_than_two_checkpoint_rows_are_never_skipped(self):
        checkpoint = ["old6", "old7", "old8"]
        cursor, _ = self.proof(checkpoint + ["new1"], checkpoint)
        self.assertLessEqual(cursor, 2)

    def test_17_checkpoint_row_target_and_surface_remain_bound(self):
        for override in (
            {"checkpoint_target_matches": False},
            {"checkpoint_surface_matches": False},
        ):
            self.assertEqual(self.proof(["old8", "new1"], ["old7", "old8"], **override)[0], 0)

    def test_18_release_and_safe_stop_contract_remain_present(self):
        source = inspect.getsource(resume.ProgressiveResumeController)
        self.assertIn("def mark_safe_stop", source)
        self.assertIn("def release", source)
        self.assertIn("def flush_verified_progress", source)


if __name__ == "__main__":
    unittest.main()
