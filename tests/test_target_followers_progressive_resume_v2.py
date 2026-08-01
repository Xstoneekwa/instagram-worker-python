from __future__ import annotations

import ast
import json
import inspect
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import runner
import target_followers_progressive_resume_v2 as resume
import supabase_client
from target_followers_resume_replay import compare_legacy_to_theoretical_v2, load_redacted_corpus


ACCOUNT_A = "00000000-0000-0000-0000-000000000001"
ACCOUNT_B = "00000000-0000-0000-0000-000000000002"
ACCOUNT_C = "00000000-0000-0000-0000-000000000003"
TARGET_A = "10000000-0000-0000-0000-000000000001"
TARGET_B = "10000000-0000-0000-0000-000000000002"
RUN_A = "20000000-0000-0000-0000-000000000001"
RUN_B = "20000000-0000-0000-0000-000000000002"
REQUEST_A = "30000000-0000-4000-8000-000000000001"
REQUEST_B = "30000000-0000-4000-8000-000000000002"
FULL_RELEASE_SHA = "a" * 40
TEST_HMAC_SECRET = "test-only-target-followers-resume-v2-secret-0001"
os.environ[resume.HMAC_SECRET_FLAG] = TEST_HMAC_SECRET


def checkpoint_row(**overrides):
    row = {
        "id": "40000000-0000-4000-8000-000000000010",
        "account_id": ACCOUNT_A,
        "target_id": TARGET_A,
        "surface": "followers",
        "checkpoint_version": 2,
        "last_safe_depth": 3,
        "last_safe_anchor": resume.anchor_hash("row3"),
        "anchor_fingerprint": resume.viewport_fingerprint(["row1", "row2", "row3"]),
        "last_visible_anchor_hashes": list(resume.bounded_anchor_hashes(["row1", "row2", "row3"])),
        "shadow_last_safe_depth": 2,
        "shadow_last_safe_anchor": resume.anchor_hash("row2"),
        "shadow_anchor_fingerprint": resume.viewport_fingerprint(["row1", "row2"]),
        "shadow_visible_anchor_hashes": list(resume.bounded_anchor_hashes(["row1", "row2"])),
        "status": "active",
        "optimistic_version": 4,
        "last_run_id": RUN_A,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "last_instagram_version": "372.0.0.48.60",
        "invalidation_reason": "",
        "lease_owner_run_id": "",
        "lease_mode": "",
        "lease_expires_at": None,
        "lease_heartbeat_at": None,
        "lease_generation": 0,
        "last_verified_at": None,
        "end_reached": False,
    }
    row.update(overrides)
    return row


def observation(handles, **overrides):
    args = {
        "followers_surface_confirmed": True,
        "expected_target_confirmed": True,
        "list_moved": True,
        "recoverable": True,
        "ambiguous_surface": False,
    }
    args.update(overrides)
    return resume.ViewportObservation.build(handles, **args)


def legacy_scroll_diag(before, after, *, surface_state="PRIMARY_ROWS_AVAILABLE"):
    overlap, new_rows = resume.viewport_continuity_counts(before, after)
    return {
        "viewport_fingerprint_before": resume.legacy_viewport_fingerprint(before),
        "viewport_fingerprint_after": resume.legacy_viewport_fingerprint(after),
        "overlap_count": overlap,
        "new_primary_row_count": new_rows,
        "depth_advanced": bool(overlap > 0 and new_rows > 0),
        "surface_state_after": surface_state,
    }


class FakeRpc:
    def __init__(
        self,
        *,
        row=None,
        claim_ok=True,
        commit_ok=True,
        provenance_persisted=True,
        commit_event_id="40000000-0000-4000-8000-000000000001",
    ):
        self.row = row
        self.claim_ok = claim_ok
        self.commit_ok = commit_ok
        self.provenance_persisted = provenance_persisted
        self.commit_event_id = commit_event_id
        self.calls = []
        self.version = int((row or {}).get("optimistic_version") or 1)

    def __call__(self, name, params):
        self.calls.append((name, dict(params)))
        if name.startswith("get_"):
            return self.row
        if name.startswith("claim_"):
            if not self.claim_ok:
                return {"ok": False, "reason": "lease_held", "optimistic_version": self.version}
            self.version += 1
            return {"ok": True, "reason": "claimed", "optimistic_version": self.version, "lease_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
        if name.startswith("renew_"):
            self.version += 1
            return {"ok": True, "reason": "renewed", "optimistic_version": self.version, "lease_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
        if name.startswith("release_"):
            self.version += 1
            return {"ok": True, "reason": "released", "optimistic_version": self.version}
        if name.startswith("commit_"):
            if not self.commit_ok:
                return {"ok": False, "reason": "optimistic_version_conflict", "optimistic_version": self.version + 1}
            self.version += 1
            return {
                "ok": True,
                "reason": "committed",
                "optimistic_version": self.version,
                "provenance_persisted": self.provenance_persisted,
                "commit_event_id": self.commit_event_id,
            }
        return {"ok": True, "reason": "ok", "optimistic_version": self.version + 1}


class FlagsTests(unittest.TestCase):
    def test_01_flags_default_off(self):
        self.assertEqual(resume.ResumeFlags.from_env({}), resume.ResumeFlags(False, False, ()))

    def test_02_shadow_flag(self):
        self.assertEqual(resume.ResumeFlags.from_env({resume.SHADOW_FLAG: "true"}).mode, "shadow")

    def test_03_enforce_flag(self):
        flags = resume.ResumeFlags.from_env({resume.ENFORCE_FLAG: "1"})
        self.assertTrue(flags.enforce_enabled)
        self.assertEqual(flags.mode, "enforce")

    def test_04_enforce_wins_when_both_set(self):
        flags = resume.ResumeFlags(True, True)
        self.assertEqual(flags.mode, "enforce")

    def test_05_disabled_plan_keeps_legacy(self):
        plan = resume.build_resume_plan(None, flags=resume.ResumeFlags(), account_id=ACCOUNT_A, target_id=TARGET_A, target_username="neutral.target")
        self.assertTrue(plan.use_legacy_navigation)
        self.assertEqual(plan.reason, "feature_disabled")

    def test_06_shadow_plan_keeps_legacy(self):
        cp = resume.Checkpoint.from_rpc(checkpoint_row())
        plan = resume.build_resume_plan(cp, flags=resume.ResumeFlags(True, False), account_id=ACCOUNT_A, target_id=TARGET_A, target_username="neutral.target")
        self.assertTrue(plan.use_legacy_navigation)
        self.assertEqual(plan.previous_depth, 2)


class HashAndCursorTests(unittest.TestCase):
    def test_07_handle_normalization(self):
        self.assertEqual(resume.normalize_handle(" @Neutral.Target "), "neutral.target")

    def test_08_invalid_handle_rejected(self):
        self.assertEqual(resume.normalize_handle("bad handle"), "")

    def test_09_fingerprint_is_order_sensitive(self):
        self.assertNotEqual(resume.viewport_fingerprint(["a", "b"]), resume.viewport_fingerprint(["b", "a"]))

    def test_10_fingerprint_deduplicates(self):
        self.assertEqual(resume.viewport_fingerprint(["a", "a", "b"]), resume.viewport_fingerprint(["a", "b"]))

    def test_11_anchors_are_bounded(self):
        self.assertEqual(len(resume.bounded_anchor_hashes([f"row{i}" for i in range(40)])), resume.MAX_ANCHORS)

    def test_12_anchors_do_not_contain_username(self):
        self.assertNotIn("neutral", resume.anchor_hash("neutral"))

    def test_13_anchor_found_cursor_after_contiguous_anchors(self):
        anchors = resume.bounded_anchor_hashes(["a", "b"])
        cursor, reason = resume.find_resume_cursor(["a", "b", "c"], anchors)
        self.assertEqual((cursor, reason), (2, "anchor_found"))

    def test_14_unknown_prefix_is_not_skipped(self):
        anchors = [resume.anchor_hash("b")]
        cursor, reason = resume.find_resume_cursor(["a", "b", "c"], anchors)
        self.assertEqual((cursor, reason), (0, "anchor_found_unhandled_prefix"))

    def test_15_social_memory_allows_contiguous_advance(self):
        anchors = [resume.anchor_hash("b")]
        cursor, _ = resume.find_resume_cursor(["a", "b", "c"], anchors, terminally_handled=lambda h: h == "a")
        self.assertEqual(cursor, 2)

    def test_16_anchor_missing_scans_from_zero(self):
        self.assertEqual(resume.find_resume_cursor(["a"], [resume.anchor_hash("z")]), (0, "anchor_missing"))

    def test_17_empty_viewport_scans_from_zero(self):
        self.assertEqual(resume.find_resume_cursor([], []), (0, "viewport_empty"))


class TransitionTests(unittest.TestCase):
    def test_18_valid_transition(self):
        self.assertTrue(
            resume.validate_depth_transition(
                observation(["a", "b"]),
                observation(["b", "c"]),
            ).verified
        )

    def test_19_swipe_without_movement(self):
        out = resume.validate_depth_transition(observation(["a"]), observation(["b"], list_moved=False))
        self.assertEqual(out.reason, "scroll_no_movement")

    def test_20_unchanged_fingerprint(self):
        out = resume.validate_depth_transition(observation(["a"]), observation(["a"]))
        self.assertEqual(out.reason, "viewport_fingerprint_unchanged")

    def test_21_suggestions_surface_rejected(self):
        out = resume.validate_depth_transition(observation(["a"]), observation(["b"], followers_surface_confirmed=False))
        self.assertEqual(out.reason, "followers_surface_unconfirmed")

    def test_22_wrong_target_rejected(self):
        out = resume.validate_depth_transition(observation(["a"]), observation(["b"], expected_target_confirmed=False))
        self.assertEqual(out.reason, "expected_target_unconfirmed")

    def test_23_ambiguous_surface_rejected(self):
        out = resume.validate_depth_transition(observation(["a"]), observation(["b"], ambiguous_surface=True))
        self.assertEqual(out.reason, "ambiguous_surface")

    def test_24_unrecoverable_surface_rejected(self):
        out = resume.validate_depth_transition(observation(["a"]), observation(["b"], recoverable=False))
        self.assertEqual(out.reason, "surface_not_recoverable")

    def test_25_missing_before_rejected(self):
        self.assertEqual(resume.validate_depth_transition(None, observation(["b"])).reason, "viewport_evidence_missing")

    def test_26_empty_fingerprint_rejected(self):
        self.assertEqual(resume.validate_depth_transition(observation([]), observation(["b"])).reason, "viewport_fingerprint_missing")

    def test_26a_distinct_fingerprint_without_positional_overlap_rejected(self):
        verdict = resume.validate_depth_transition(
            observation(["a", "b"]),
            observation(["c", "d"]),
        )
        self.assertFalse(verdict.verified)
        self.assertEqual(verdict.reason, "viewport_overlap_missing")

    def test_26b_overlap_without_new_unique_row_rejected(self):
        verdict = resume.validate_depth_transition(
            observation(["a", "b", "c"]),
            observation(["b", "c", "a"]),
        )
        self.assertFalse(verdict.verified)
        self.assertEqual(verdict.reason, "viewport_new_rows_missing")


class CheckpointTests(unittest.TestCase):
    def test_27_checkpoint_depth_zero(self):
        cp = resume.Checkpoint.from_rpc(checkpoint_row(last_safe_depth=0, shadow_last_safe_depth=0))
        self.assertEqual(cp.depth("enforce"), 0)

    def test_28_checkpoint_x_enforce(self):
        cp = resume.Checkpoint.from_rpc(checkpoint_row(last_safe_depth=7))
        plan = resume.build_resume_plan(cp, flags=resume.ResumeFlags(False, True), account_id=ACCOUNT_A, target_id=TARGET_A, target_username="neutral.target")
        self.assertEqual(plan.planned_depth, 7)
        self.assertFalse(plan.use_legacy_navigation)

    def test_29_same_target_two_accounts_mismatch(self):
        cp = resume.Checkpoint.from_rpc(checkpoint_row())
        self.assertEqual(resume.validate_checkpoint(cp, account_id=ACCOUNT_B, target_id=TARGET_A, target_username="neutral.target")[1], "account_id_mismatch")

    def test_30_two_targets_same_account_mismatch(self):
        cp = resume.Checkpoint.from_rpc(checkpoint_row())
        self.assertEqual(resume.validate_checkpoint(cp, account_id=ACCOUNT_A, target_id=TARGET_B, target_username="neutral.target")[1], "target_id_mismatch")

    def test_31_username_is_not_part_of_checkpoint_identity(self):
        cp = resume.Checkpoint.from_rpc(checkpoint_row())
        self.assertEqual(resume.validate_checkpoint(cp, account_id=ACCOUNT_A, target_id=TARGET_A, target_username="renamed.target")[1], "checkpoint_valid")

    def test_32_checkpoint_stale(self):
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        cp = resume.Checkpoint.from_rpc(checkpoint_row(updated_at=old))
        self.assertEqual(resume.validate_checkpoint(cp, account_id=ACCOUNT_A, target_id=TARGET_A, target_username="neutral.target")[1], "checkpoint_too_old")

    def test_33_target_archived_status(self):
        cp = resume.Checkpoint.from_rpc(checkpoint_row(status="invalidated", invalidation_reason="target_archived"))
        self.assertIn("invalidated", resume.validate_checkpoint(cp, account_id=ACCOUNT_A, target_id=TARGET_A, target_username="neutral.target")[1])

    def test_34_target_deleted_status(self):
        cp = resume.Checkpoint.from_rpc(checkpoint_row(status="reset_required", invalidation_reason="target_deleted"))
        self.assertIn("reset_required", resume.validate_checkpoint(cp, account_id=ACCOUNT_A, target_id=TARGET_A, target_username="neutral.target")[1])

    def test_35_exhausted_uses_legacy_no_depth_growth(self):
        cp = resume.Checkpoint.from_rpc(checkpoint_row(status="exhausted"))
        plan = resume.build_resume_plan(cp, flags=resume.ResumeFlags(False, True), account_id=ACCOUNT_A, target_id=TARGET_A, target_username="neutral.target")
        self.assertTrue(plan.use_legacy_navigation)
        self.assertEqual(plan.reason, "checkpoint_exhausted")

    def test_36_excessive_depth_falls_back(self):
        cp = resume.Checkpoint.from_rpc(checkpoint_row(last_safe_depth=60))
        plan = resume.build_resume_plan(cp, flags=resume.ResumeFlags(False, True), account_id=ACCOUNT_A, target_id=TARGET_A, target_username="neutral.target")
        self.assertEqual(plan.reason, "fast_forward_depth_exceeds_bound")

    def test_37_malformed_anchor_array_is_trimmed(self):
        cp = resume.Checkpoint.from_rpc(checkpoint_row(last_visible_anchor_hashes=[resume.anchor_hash(str(i)) for i in range(20)]))
        self.assertEqual(len(cp.last_visible_anchor_hashes), resume.MAX_ANCHORS)

    def test_37a_v3_verified_shadow_checkpoint_promotes_into_first_enforce(self):
        verified_at = datetime.now(timezone.utc).isoformat()
        cp = resume.Checkpoint.from_rpc(
            checkpoint_row(
                checkpoint_version=3,
                last_safe_depth=0,
                last_visible_anchor_hashes=[],
                shadow_last_safe_depth=2,
                shadow_visible_anchor_hashes=list(
                    resume.bounded_anchor_hashes(["row1", "row2"])
                ),
                last_verified_at=verified_at,
            )
        )
        plan = resume.build_resume_plan(
            cp,
            flags=resume.ResumeFlags(False, True),
            account_id=ACCOUNT_A,
            target_id=TARGET_A,
            target_username="neutral.target",
        )
        self.assertFalse(plan.use_legacy_navigation)
        self.assertEqual(plan.planned_depth, 2)
        self.assertEqual(plan.anchor_hashes, cp.shadow_visible_anchor_hashes)
        self.assertEqual(plan.reason, "shadow_plan_promoted_for_enforce")

    def test_37b_unverified_shadow_checkpoint_is_never_promoted(self):
        cp = resume.Checkpoint.from_rpc(
            checkpoint_row(
                checkpoint_version=3,
                last_safe_depth=0,
                shadow_last_safe_depth=2,
                last_verified_at=None,
            )
        )
        plan = resume.build_resume_plan(
            cp,
            flags=resume.ResumeFlags(False, True),
            account_id=ACCOUNT_A,
            target_id=TARGET_A,
            target_username="neutral.target",
        )
        self.assertEqual(plan.planned_depth, 0)
        self.assertEqual(plan.anchor_hashes, cp.last_visible_anchor_hashes)
        self.assertEqual(plan.reason, "checkpoint_ready")

    def test_37c_paysdorange_verified_depth_two_fixture_promotes_safely(self):
        cp = resume.Checkpoint.from_rpc(
            checkpoint_row(
                checkpoint_version=3,
                last_safe_depth=0,
                last_visible_anchor_hashes=[],
                shadow_last_safe_depth=2,
                shadow_visible_anchor_hashes=list(
                    resume.bounded_anchor_hashes(["fixture.row.one", "fixture.row.two"])
                ),
                last_verified_at=datetime.now(timezone.utc).isoformat(),
                end_reached=False,
            )
        )
        plan = resume.build_resume_plan(
            cp,
            flags=resume.ResumeFlags(False, True),
            account_id=ACCOUNT_A,
            target_id=TARGET_A,
            target_username="paysdorangeenprovencetourisme",
        )
        self.assertFalse(plan.use_legacy_navigation)
        self.assertEqual(plan.planned_depth, 2)
        self.assertEqual(plan.reason, "shadow_plan_promoted_for_enforce")


class RepositoryAndControllerTests(unittest.TestCase):
    def controller(self, rpc, *, flags=resume.ResumeFlags(True, False), events=None):
        sink = events if events is not None else []
        return resume.ProgressiveResumeController(
            repository=resume.ResumeRepository(rpc),
            flags=flags,
            account_id=ACCOUNT_A,
            target_id=TARGET_A,
            target_username="neutral.target",
            run_id=RUN_A,
            source_request_id=REQUEST_A,
            source_attempt_id=1,
            release_sha=FULL_RELEASE_SHA,
            hmac_secret=TEST_HMAC_SECRET,
            emit=lambda event, payload: sink.append((event, payload)),
        )

    def test_38_rpc_get_contract(self):
        rpc = FakeRpc(row=checkpoint_row())
        cp = resume.ResumeRepository(rpc).get(account_id=ACCOUNT_A, target_id=TARGET_A)
        self.assertEqual(cp.target_id, TARGET_A)
        self.assertEqual(rpc.calls[0][0], "get_target_followers_resume_checkpoint_v3")

    def test_39_rpc_claim_bounds_lease(self):
        rpc = FakeRpc()
        resume.ResumeRepository(rpc).claim(account_id=ACCOUNT_A, target_id=TARGET_A, run_id=RUN_A, mode="shadow", expected_version=None, lease_seconds=9999)
        self.assertEqual(rpc.calls[-1][1]["p_lease_seconds"], resume.MAX_LEASE_SECONDS)

    def test_40_claim_conflict_is_clean(self):
        events = []
        ctl = self.controller(FakeRpc(row=checkpoint_row(), claim_ok=False), events=events)
        ctl.load_and_plan()
        self.assertFalse(ctl.claim())
        self.assertIn("checkpoint_conflict", [e for e, _ in events])

    def test_41_two_runs_compare_and_swap(self):
        rpc = FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0), commit_ok=False)
        ctl = self.controller(rpc)
        ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(["a", "b"], followers_surface_confirmed=True, expected_target_confirmed=True)
        ctl.note_scroll_sent(previous_viewport_complete=True)
        ctl.observe_viewport(["b", "c"], followers_surface_confirmed=True, expected_target_confirmed=True)
        self.assertFalse(ctl.commit_verified_progress())

    def test_42_worker_restart_loads_persisted_depth(self):
        ctl = self.controller(FakeRpc(row=checkpoint_row(shadow_last_safe_depth=5)))
        self.assertEqual(ctl.load_and_plan().previous_depth, 5)

    def test_43_baseline_then_x_plus_one(self):
        ctl = self.controller(FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0)))
        ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(["a", "b"], followers_surface_confirmed=True, expected_target_confirmed=True)
        ctl.note_scroll_sent(previous_viewport_complete=True)
        verdict = ctl.observe_viewport(["b", "c"], followers_surface_confirmed=True, expected_target_confirmed=True)
        self.assertTrue(verdict.verified)
        self.assertEqual(ctl.reached_depth, 1)
        self.assertTrue(ctl.commit_verified_progress())

    def test_44_partial_viewport_never_advances(self):
        ctl = self.controller(FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0)))
        ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(["a", "b"], followers_surface_confirmed=True, expected_target_confirmed=True)
        ctl.note_scroll_sent(previous_viewport_complete=False)
        verdict = ctl.observe_viewport(["b", "c"], followers_surface_confirmed=True, expected_target_confirmed=True)
        self.assertEqual(verdict.reason, "previous_viewport_not_fully_evaluated")
        self.assertEqual(ctl.reached_depth, 0)

    def test_45_crash_before_commit_preserves_storage(self):
        rpc = FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0))
        ctl = self.controller(rpc); ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(["a"], followers_surface_confirmed=True, expected_target_confirmed=True)
        ctl.note_scroll_sent(previous_viewport_complete=True)
        self.assertFalse(any(name.startswith("commit_") for name, _ in rpc.calls))

    def test_46_safe_stop_before_commit(self):
        ctl = self.controller(FakeRpc(row=checkpoint_row()))
        ctl.load_and_plan(); ctl.claim(); ctl.current_viewport = observation(["a"]); ctl.mark_safe_stop()
        self.assertFalse(ctl.commit_verified_progress())

    def test_47_shadow_does_not_mutate_navigation(self):
        ctl = self.controller(FakeRpc(row=checkpoint_row()))
        ctl.load_and_plan()
        self.assertEqual(ctl.navigation_mutations, 0)
        self.assertTrue(ctl.plan.use_legacy_navigation)

    def test_48_enforce_false_preserves_legacy(self):
        ctl = self.controller(FakeRpc(row=checkpoint_row()), flags=resume.ResumeFlags(True, False))
        self.assertTrue(ctl.load_and_plan().use_legacy_navigation)

    def test_49_commit_uses_hashes_not_raw_handles(self):
        rpc = FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0))
        ctl = self.controller(rpc); ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(["before", "sensitive.name"], followers_surface_confirmed=True, expected_target_confirmed=True)
        ctl.note_scroll_sent(previous_viewport_complete=True)
        ctl.observe_viewport(["sensitive.name", "after"], followers_surface_confirmed=True, expected_target_confirmed=True)
        ctl.commit_verified_progress(cursor_handle="sensitive.name")
        params = [p for n, p in rpc.calls if n.startswith("commit_")][-1]
        self.assertNotIn("sensitive.name", json.dumps(params))
        self.assertEqual(
            [name for name, _ in rpc.calls if name.startswith("commit_")],
            ["commit_target_followers_resume_checkpoint_v4"],
        )
        self.assertEqual(params["p_commit_context"]["source_request_id"], REQUEST_A)
        self.assertEqual(params["p_commit_context"]["release_sha"], FULL_RELEASE_SHA)

    def test_50_repeated_no_progress_does_not_increment(self):
        ctl = self.controller(FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0)))
        ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(["a"], followers_surface_confirmed=True, expected_target_confirmed=True)
        ctl.note_scroll_sent(previous_viewport_complete=True)
        verdict = ctl.observe_viewport(["a"], followers_surface_confirmed=True, expected_target_confirmed=True)
        self.assertEqual(verdict.reason, "viewport_fingerprint_unchanged")
        self.assertEqual(ctl.reached_depth, 0)

    def test_51_end_reached_observable(self):
        events = []
        ctl = self.controller(FakeRpc(row=checkpoint_row()), events=events)
        ctl.load_and_plan(); ctl.mark_end_reached(reason="repeated_no_progress")
        self.assertIn("end_reached", [e for e, _ in events])

    def test_52_all_required_event_names_present(self):
        self.assertTrue(
            {
                "target_followers_checkpoint_loaded",
                "resume_plan_built",
                "fast_forward_started",
                "depth_transition_verified",
                "anchor_found",
                "anchor_not_found",
                "checkpoint_claimed",
                "checkpoint_committed",
                "checkpoint_conflict",
                "checkpoint_invalidated",
                "end_reached",
                "resume_fallback_legacy",
            }.issubset(resume.EVENTS),
        )

    def test_52a_hmac_is_keyed_and_stable(self):
        first = resume.anchor_hash("sensitive.name", secret=TEST_HMAC_SECRET)
        self.assertEqual(first, resume.anchor_hash("sensitive.name", secret=TEST_HMAC_SECRET))
        self.assertNotEqual(first, resume.anchor_hash("sensitive.name", secret=TEST_HMAC_SECRET + "x"))
        self.assertTrue(first.startswith("a3:"))

    def test_52b_secret_absent_fails_open_without_rpc(self):
        rpc = FakeRpc()
        events = []
        controller = resume.build_runtime_controller(
            account_id=ACCOUNT_A, target_id=TARGET_A, target_username="neutral.target", run_id=RUN_A,
            flags=resume.ResumeFlags(True, False, (ACCOUNT_A,)), rpc_call=rpc,
            emit=lambda event, payload: events.append((event, payload)), hmac_secret="",
        )
        self.assertIsNone(controller)
        self.assertEqual(rpc.calls, [])
        self.assertEqual(events[0][0], "v2_failed_open")

    def test_52c_first_pass_evaluated_prefix_commits_without_false_depth(self):
        rpc = FakeRpc(
            row=checkpoint_row(
                checkpoint_version=3,
                shadow_last_safe_depth=0,
                shadow_last_safe_anchor="",
                shadow_anchor_fingerprint="",
                shadow_visible_anchor_hashes=[],
            )
        )
        ctl = self.controller(rpc)
        ctl.load_and_plan(); self.assertTrue(ctl.claim())
        handles = ["done.one", "done.two", "not.evaluated"]
        ctl.observe_viewport(
            handles,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
            list_moved=False,
        )
        terminal = {"done.one", "done.two"}
        self.assertEqual(
            ctl.note_first_pass_evaluated_prefix(
                handles,
                terminally_handled=lambda handle: handle in terminal,
            ),
            2,
        )
        self.assertTrue(ctl.flush_verified_progress(boundary="safe_stop"))
        self.assertEqual(ctl.reached_depth, 0)
        name, params = [item for item in rpc.calls if item[0].startswith("commit_")][-1]
        self.assertEqual(
            name, "commit_target_followers_resume_first_pass_progress_v5"
        )
        self.assertEqual(params["p_commit_context"]["evaluated_count"], 2)
        self.assertNotIn("done.one", json.dumps(params))
        self.assertNotIn("not.evaluated", json.dumps(params))

    def test_52d_first_pass_prefix_stops_at_first_unhandled_row(self):
        rpc = FakeRpc(row=checkpoint_row(checkpoint_version=3, shadow_last_safe_depth=0))
        ctl = self.controller(rpc)
        ctl.load_and_plan(); self.assertTrue(ctl.claim())
        handles = ["done.one", "gap", "done.three"]
        ctl.observe_viewport(
            handles,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
            list_moved=False,
        )
        terminal = {"done.one", "done.three"}
        self.assertEqual(
            ctl.note_first_pass_evaluated_prefix(
                handles,
                terminally_handled=lambda handle: handle in terminal,
            ),
            1,
        )
        self.assertEqual(ctl.first_pass_evaluated_handles, ("done.one",))

    def test_52e_second_pass_skips_only_committed_prefix(self):
        prefix = ["done.one", "done.two"]
        row = checkpoint_row(
            checkpoint_version=3,
            last_safe_depth=0,
            last_safe_anchor=resume.anchor_hash(prefix[-1]),
            anchor_fingerprint=resume.viewport_fingerprint(prefix),
            last_visible_anchor_hashes=list(resume.bounded_anchor_hashes(prefix)),
            shadow_last_safe_depth=0,
        )
        ctl = self.controller(
            FakeRpc(row=row), flags=resume.ResumeFlags(False, True)
        )
        plan = ctl.load_and_plan(); self.assertFalse(plan.use_legacy_navigation)
        self.assertTrue(ctl.claim())
        viewport = prefix + ["not.evaluated"]
        ctl.observe_viewport(
            viewport,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
            list_moved=False,
        )
        cursor, reason = ctl.enforce_cursor_for_viewport(viewport)
        self.assertEqual(cursor, 2)
        self.assertTrue(reason.startswith("anchor_found"))

    def test_52c_release_is_idempotent_and_calls_rpc_once(self):
        rpc = FakeRpc(row=checkpoint_row())
        ctl = self.controller(rpc)
        ctl.load_and_plan(); ctl.claim()
        self.assertTrue(ctl.release())
        self.assertTrue(ctl.release())
        self.assertEqual(sum(name.startswith("release_") for name, _ in rpc.calls), 1)

    def test_52d_renewal_occurs_when_lease_is_near_expiry(self):
        rpc = FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0))
        ctl = self.controller(rpc)
        ctl.load_and_plan(); ctl.claim()
        ctl.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=5)
        ctl.observe_viewport(["first", "overlap"], followers_surface_confirmed=True, expected_target_confirmed=True)
        ctl.note_scroll_sent(previous_viewport_complete=True)
        ctl.observe_viewport(["overlap", "next"], followers_surface_confirmed=True, expected_target_confirmed=True)
        self.assertTrue(ctl.commit_verified_progress())
        self.assertEqual(sum(name.startswith("renew_") for name, _ in rpc.calls), 1)

    def test_52e_no_renewal_when_hour_lease_is_healthy(self):
        rpc = FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0))
        ctl = self.controller(rpc)
        ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(["first", "overlap"], followers_surface_confirmed=True, expected_target_confirmed=True)
        ctl.note_scroll_sent(previous_viewport_complete=True)
        ctl.observe_viewport(["overlap", "next"], followers_surface_confirmed=True, expected_target_confirmed=True)
        self.assertTrue(ctl.commit_verified_progress())
        self.assertEqual(sum(name.startswith("renew_") for name, _ in rpc.calls), 0)

    def test_52f_cas_uses_one_reload_and_one_retry(self):
        class CasOnceRpc:
            def __init__(self):
                self.calls = []
                self.gets = 0
                self.commits = 0

            def __call__(self, name, params):
                self.calls.append((name, dict(params)))
                if name.startswith("get_"):
                    self.gets += 1
                    version = 7 if self.gets == 1 else 9
                    return checkpoint_row(
                        shadow_last_safe_depth=0,
                        optimistic_version=version,
                        lease_owner_run_id=RUN_A if self.gets > 1 else "",
                        lease_mode="shadow" if self.gets > 1 else "",
                        lease_expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                    )
                if name.startswith("claim_"):
                    return {"ok": True, "reason": "claimed", "optimistic_version": 8, "lease_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
                if name.startswith("commit_"):
                    self.commits += 1
                    if self.commits == 1:
                        return {"ok": False, "reason": "optimistic_version_conflict", "optimistic_version": 9}
                    return {
                        "ok": True,
                        "reason": "committed",
                        "optimistic_version": 10,
                        "lease_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                        "provenance_persisted": True,
                        "commit_event_id": "40000000-0000-4000-8000-000000000001",
                    }
                raise AssertionError(name)

        rpc = CasOnceRpc()
        ctl = self.controller(rpc)
        ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(["first", "overlap"], followers_surface_confirmed=True, expected_target_confirmed=True)
        ctl.note_scroll_sent(previous_viewport_complete=True)
        ctl.observe_viewport(["overlap", "next"], followers_surface_confirmed=True, expected_target_confirmed=True)
        self.assertTrue(ctl.commit_verified_progress())
        self.assertEqual((ctl.cas_reloads, ctl.cas_retries, rpc.commits), (1, 1, 2))
        commit_contexts = [
            params["p_commit_context"]
            for name, params in rpc.calls
            if name == "commit_target_followers_resume_checkpoint_v4"
        ]
        self.assertEqual(len(commit_contexts), 2)
        self.assertEqual(commit_contexts[0], commit_contexts[1])

    def test_52fa_repository_rejects_zero_scroll_index_without_rpc(self):
        rpc = FakeRpc()
        response = resume.ResumeRepository(rpc).commit(
            account_id=ACCOUNT_A,
            target_id=TARGET_A,
            run_id=RUN_A,
            mode="shadow",
            expected_version=1,
            depth=1,
            observation=observation(["overlap", "next"]),
            commit_context=resume.LegacyScrollEvidence(
                observed_scroll_index=0,
                overlap_count=1,
                new_unique_rows=1,
                fingerprint_before=resume.legacy_viewport_fingerprint(
                    ["first", "overlap"]
                ),
                fingerprint_after=resume.legacy_viewport_fingerprint(
                    ["overlap", "next"]
                ),
                surface_state_after="PRIMARY_ROWS_AVAILABLE",
            ),
            source_request_id=REQUEST_A,
            source_attempt_id=1,
            release_sha=FULL_RELEASE_SHA,
            cursor_anchor=resume.anchor_hash("next"),
            instagram_version="372.0.0.48.60",
        )
        self.assertEqual(response, {"ok": False, "reason": "commit_context_invalid"})
        self.assertEqual(rpc.calls, [])

    def test_52fb_commit_rpc_exception_safe_stops_only_v2(self):
        class CommitExceptionRpc(FakeRpc):
            def __call__(self, name, params):
                if name.startswith("commit_"):
                    self.calls.append((name, dict(params)))
                    raise RuntimeError("v4 unavailable")
                return super().__call__(name, params)

        rpc = CommitExceptionRpc(row=checkpoint_row(shadow_last_safe_depth=0))
        events = []
        ctl = self.controller(rpc, events=events)
        ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(
            ["first", "overlap"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        ctl.note_scroll_sent(previous_viewport_complete=True)
        ctl.observe_viewport(
            ["overlap", "next"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )

        self.assertFalse(ctl.commit_verified_progress())
        self.assertTrue(ctl._safe_stop)
        self.assertIsNone(ctl.last_verified_commit_context)
        reached_depth = ctl.reached_depth
        verdict = ctl.observe_viewport(
            ["next", "later"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertFalse(verdict.verified)
        self.assertEqual(ctl.reached_depth, reached_depth)
        self.assertEqual(
            sum(name.startswith("commit_") for name, _ in rpc.calls),
            1,
        )
        self.assertTrue(ctl.release())
        self.assertIsNone(ctl.last_verified_commit_context)
        self.assertIn("v2_failed_open", [event for event, _ in events])

    def test_52g_worker_contract_contains_no_plaintext_checkpoint_field(self):
        source = Path(resume.__file__).read_text(encoding="utf-8")
        self.assertNotIn("target_username_normalized", source)

    def test_52h_runner_releases_controller_in_session_finally(self):
        runner_source = (Path(resume.__file__).parent / "runner.py").read_text(encoding="utf-8")
        self.assertIn("target_followers_resume_controller.flush_verified_progress(", runner_source)
        self.assertIn("target_followers_resume_controller.release()", runner_source)

    def test_52i_stolm_two_legacy_scrolls_commit_two_safe_depths(self):
        before = [f"stolm.row{i:02d}" for i in range(1, 9)]
        after_one = before[-2:] + [f"stolm.row{i:02d}" for i in range(9, 16)]
        after_two = after_one[-2:] + [f"stolm.row{i:02d}" for i in range(16, 23)]
        rpc = FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0))
        events = []
        ctl = self.controller(rpc, events=events)
        ctl.source_request_id = REQUEST_A
        ctl.source_attempt_id = 2
        ctl.release_sha = FULL_RELEASE_SHA
        ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(
            before,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )

        staged_one = ctl.note_legacy_scroll_progress(
            legacy_scroll_diag(before, after_one),
            observed_scroll_index=1,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertEqual(staged_one.reason, "legacy_scroll_proof_staged")
        self.assertTrue(
            ctl.observe_viewport(
                after_one,
                followers_surface_confirmed=True,
                expected_target_confirmed=True,
            ).verified
        )
        self.assertTrue(ctl.commit_verified_progress(cursor_handle=after_one[-1]))

        ctl.note_legacy_scroll_progress(
            legacy_scroll_diag(after_one, after_two),
            observed_scroll_index=2,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertTrue(
            ctl.observe_viewport(
                after_two,
                followers_surface_confirmed=True,
                expected_target_confirmed=True,
            ).verified
        )
        self.assertTrue(ctl.commit_verified_progress(cursor_handle=after_two[-1]))
        self.assertTrue(ctl.flush_verified_progress(boundary="target_rotation"))
        self.assertTrue(ctl.release())

        commits = [
            params["p_last_safe_depth"]
            for name, params in rpc.calls
            if name.startswith("commit_")
        ]
        self.assertEqual(commits, [1, 2])
        transitions = [
            payload for event, payload in events if event == "depth_transition_verified"
        ]
        self.assertEqual(
            [(item["overlap_count"], item["new_unique_rows"]) for item in transitions],
            [(2, 7), (2, 7)],
        )
        committed_events = [
            payload for event, payload in events if event == "checkpoint_committed"
        ]
        self.assertEqual(committed_events[-1]["checkpoint_depth_after"], 2)
        self.assertEqual(committed_events[-1]["commit_status"], "committed")
        self.assertEqual(committed_events[-1]["lease_status"], "active")
        self.assertEqual(
            committed_events[-1]["source_request_id"],
            REQUEST_A,
        )
        self.assertEqual(committed_events[-1]["source_attempt_id"], 2)
        self.assertEqual(committed_events[-1]["release_sha"], FULL_RELEASE_SHA)
        commit_contexts = [
            params["p_commit_context"]
            for name, params in rpc.calls
            if name == "commit_target_followers_resume_checkpoint_v4"
        ]
        self.assertEqual(
            [
                (item["overlap_count"], item["new_unique_rows"], item["observed_scroll_index"])
                for item in commit_contexts
            ],
            [(2, 7, 1), (2, 7, 2)],
        )
        self.assertTrue(all(len(item["release_sha"]) == 40 for item in commit_contexts))
        self.assertLess(
            next(i for i, (name, _) in enumerate(rpc.calls) if name.startswith("commit_")),
            next(i for i, (name, _) in enumerate(rpc.calls) if name.startswith("release_")),
        )

    def test_52j_legacy_scroll_without_overlap_never_commits(self):
        before = ["a", "b"]
        after = ["c", "d"]
        rpc = FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0))
        events = []
        ctl = self.controller(rpc, events=events)
        ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(
            before,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        verdict = ctl.note_legacy_scroll_progress(
            legacy_scroll_diag(before, after),
            observed_scroll_index=1,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertFalse(verdict.verified)
        self.assertEqual(verdict.reason, "continuity_unproven")
        self.assertFalse(ctl.flush_verified_progress(boundary="target_end"))
        self.assertFalse(any(name.startswith("commit_") for name, _ in rpc.calls))
        reasons = [
            payload["reason"]
            for event, payload in events
            if event == "checkpoint_not_committed"
        ]
        self.assertIn("continuity_unproven", reasons)

    def test_52k_suggestions_boundary_never_commits(self):
        before = ["a", "b"]
        after = ["b", "c"]
        rpc = FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0))
        events = []
        ctl = self.controller(rpc, events=events)
        ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(
            before,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        verdict = ctl.note_legacy_scroll_progress(
            legacy_scroll_diag(
                before,
                after,
                surface_state="SUGGESTIONS_BOUNDARY_CONFIRMED",
            ),
            observed_scroll_index=1,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertEqual(verdict.reason, "suggestions_boundary_reached")
        self.assertFalse(ctl.flush_verified_progress(boundary="target_end"))
        self.assertFalse(any(name.startswith("commit_") for name, _ in rpc.calls))

    def test_52l_expired_lease_rejects_progress_and_commit(self):
        before = ["a", "b"]
        after = ["b", "c"]
        rpc = FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0))
        events = []
        ctl = self.controller(rpc, events=events)
        ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(
            before,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        ctl.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        verdict = ctl.note_legacy_scroll_progress(
            legacy_scroll_diag(before, after),
            observed_scroll_index=1,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertEqual(verdict.reason, "lease_invalid")
        self.assertFalse(ctl.flush_verified_progress(boundary="target_rotation"))
        self.assertFalse(any(name.startswith("commit_") for name, _ in rpc.calls))

    def test_52m_clean_terminal_flush_commits_pending_safe_depth(self):
        rpc = FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0))
        events = []
        ctl = self.controller(rpc, events=events)
        ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(
            ["a", "b"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        ctl.note_scroll_sent(previous_viewport_complete=True)
        self.assertTrue(
            ctl.observe_viewport(
                ["b", "c"],
                followers_surface_confirmed=True,
                expected_target_confirmed=True,
            ).verified
        )
        self.assertTrue(ctl.flush_verified_progress(boundary="clean_terminal"))
        self.assertTrue(ctl.release())
        self.assertEqual(
            [p["p_last_safe_depth"] for n, p in rpc.calls if n.startswith("commit_")],
            [1],
        )
        self.assertIn("checkpoint_flush_completed", [event for event, _ in events])

    def test_52n_crash_abandons_pending_depth_without_commit_then_releases(self):
        rpc = FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0))
        events = []
        ctl = self.controller(rpc, events=events)
        ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(
            ["a", "b"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        ctl.note_scroll_sent(previous_viewport_complete=True)
        self.assertTrue(
            ctl.observe_viewport(
                ["b", "c"],
                followers_surface_confirmed=True,
                expected_target_confirmed=True,
            ).verified
        )
        ctl.abandon_before_release(reason="run_terminal_before_checkpoint_flush")
        self.assertTrue(ctl.release())
        self.assertFalse(any(name.startswith("commit_") for name, _ in rpc.calls))
        self.assertTrue(any(name.startswith("release_") for name, _ in rpc.calls))
        reasons = [
            payload["reason"]
            for event, payload in events
            if event == "checkpoint_not_committed"
        ]
        self.assertIn("run_terminal_before_checkpoint_flush", reasons)

    def test_52o_exact_action_bar_proves_stolm_without_session_commit(self):
        detection = {
            "is_followers_list": True,
            "action_bar_title": "stolm_",
            "visible_header_texts": ["Followers"],
        }
        self.assertTrue(
            resume.target_surface_identity_proved(
                detection,
                expected_target="stolm_",
                session_committed=False,
            )
        )
        self.assertFalse(
            resume.target_surface_identity_proved(
                {**detection, "action_bar_title": "foreign_target"},
                expected_target="stolm_",
                session_committed=True,
            )
        )
        self.assertTrue(
            resume.target_surface_identity_proved(
                {**detection, "action_bar_title": "Followers"},
                expected_target="stolm_",
                session_committed=True,
            )
        )

    def test_52p_runner_bridges_legacy_scroll_and_releases_after_flush(self):
        runner_source = (Path(resume.__file__).parent / "runner.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("note_legacy_scroll_progress(", runner_source)
        self.assertIn("_main_scroll_diag,", runner_source)
        self.assertIn('boundary=_v2_flush_boundary', runner_source)
        self.assertIn("abandon_before_release(", runner_source)
        self.assertLess(
            runner_source.index("target_followers_resume_controller.flush_verified_progress("),
            runner_source.index("target_followers_resume_controller.release()"),
        )


class ReplayAndStaticSafetyTests(unittest.TestCase):
    def test_53_redacted_replay_loads(self):
        path = Path(__file__).parent / "fixtures/target_followers_resume_v2/redacted_real_runs.json"
        corpus = load_redacted_corpus(path)
        self.assertEqual(corpus["run_count_total"], 17)

    def test_54_replay_does_not_invent_gain(self):
        path = Path(__file__).parent / "fixtures/target_followers_resume_v2/redacted_real_runs.json"
        rows = compare_legacy_to_theoretical_v2(load_redacted_corpus(path))
        self.assertTrue(all(row["estimated_seconds_saved"] is None for row in rows))

    def test_55_replay_does_not_invent_depth(self):
        path = Path(__file__).parent / "fixtures/target_followers_resume_v2/redacted_real_runs.json"
        rows = compare_legacy_to_theoretical_v2(load_redacted_corpus(path))
        self.assertTrue(all(row["resume_scrolls"] is None for row in rows))

    def test_56_no_account_hardcoded_in_product(self):
        source = Path(resume.__file__).read_text(encoding="utf-8")
        self.assertNotIn(ACCOUNT_A, source)
        self.assertNotIn("CORPUS_ACCOUNT", source)

    def test_57_no_sensitive_artifact_fields(self):
        source = Path(resume.__file__).read_text(encoding="utf-8")
        self.assertNotIn("screenshot_path", source)
        self.assertNotIn("xml_raw", source)

    def test_58_future_account_is_generic(self):
        cp = resume.Checkpoint.from_rpc(checkpoint_row(account_id=ACCOUNT_B, target_id=TARGET_B))
        valid, _ = resume.validate_checkpoint(cp, account_id=ACCOUNT_B, target_id=TARGET_B, target_username="neutral.target")
        self.assertTrue(valid)

    def test_59_build_controller_off_import_has_no_rpc(self):
        with patch.dict(os.environ, {resume.SHADOW_FLAG: "false", resume.ENFORCE_FLAG: "false"}, clear=False):
            self.assertIsNone(resume.build_runtime_controller(account_id=ACCOUNT_A, target_id=TARGET_A, target_username="neutral.target", run_id=RUN_A))


class RunnerResumeProvenanceTests(unittest.TestCase):
    def test_first_pass_v5_migration_is_additive_service_role_only(self):
        root = Path(__file__).resolve().parents[1]
        sql = (root / "supabase" / "migrations" / "20260802003000_target_followers_resume_first_pass_progress_v5.sql").read_text()
        self.assertIn(
            "commit_target_followers_resume_first_pass_progress_v5", sql
        )
        self.assertIn("depth_unchanged', true", sql)
        self.assertIn("evaluated_prefix_divergence", sql)
        self.assertIn("grant execute", sql.lower())
        self.assertIn("to service_role", sql.lower())
        self.assertIn("from public, anon, authenticated, service_role", sql.lower())
        self.assertNotIn("grant execute on function public.commit_target_followers_resume_first_pass_progress_v5(\n  uuid,uuid,text,uuid,text,bigint,jsonb,text,text,jsonb,text,integer\n) to anon", sql.lower())

    def test_runner_syncs_first_pass_prefix_before_terminal_flush(self):
        source = inspect.getsource(runner)
        self.assertIn(
            "target_followers_resume_controller.note_first_pass_evaluated_prefix(",
            source,
        )
        self.assertLess(
            source.rindex(
                "target_followers_resume_controller.note_first_pass_evaluated_prefix("
            ),
            source.rindex(
                "target_followers_resume_controller.flush_verified_progress("
            ),
        )

    def test_direct_followers_engine_keeps_follow_and_ct_request_channels_separate(self):
        tree = ast.parse(inspect.getsource(runner._main_impl))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_run_followers_list_engine_session"
        ]
        self.assertEqual(len(calls), 1)
        keyword_values = {
            keyword.arg: ast.unparse(keyword.value)
            for keyword in calls[0].keywords
            if keyword.arg is not None
        }
        self.assertEqual(keyword_values.get("run_request_id"), "run_request_id")
        self.assertEqual(
            keyword_values.get("target_followers_resume_source_request_id"),
            "run_request_id",
        )

    def test_active_root_short_commit_resolves_to_matching_full_sha(self):
        full_sha = "a" * 40
        module_root = str(Path(runner.__file__).resolve().parent)
        with (
            patch.dict(
                os.environ,
                {
                    "PHONEFARM_ACTIVE_ROOT": module_root,
                    "PHONEFARM_ACTIVE_COMMIT": full_sha[:7],
                },
                clear=False,
            ),
            patch.object(runner, "_full_git_commit_for_root", return_value=full_sha),
        ):
            self.assertEqual(runner._resolve_active_worker_release_sha(), full_sha)

    def test_active_root_commit_mismatch_fails_closed(self):
        full_sha = "a" * 40
        module_root = str(Path(runner.__file__).resolve().parent)
        invalid_runtime = SimpleNamespace(
            ok=False,
            resolved_root="",
            commit="",
        )
        with (
            patch.dict(
                os.environ,
                {
                    "PHONEFARM_ACTIVE_ROOT": module_root,
                    "PHONEFARM_ACTIVE_COMMIT": "b" * 7,
                },
                clear=False,
            ),
            patch.object(runner, "_full_git_commit_for_root", return_value=full_sha),
            patch(
                "phonefarm_runtime_control.resolve_runtime_root",
                return_value=invalid_runtime,
            ),
        ):
            self.assertEqual(runner._resolve_active_worker_release_sha(), "")

    def test_auto_restart_attempt_two_is_used_from_explicit_policy(self):
        full_sha = "c" * 40
        with patch.object(
            runner,
            "_resolve_active_worker_release_sha",
            return_value=full_sha,
        ):
            provenance, reason = runner._resolve_target_followers_resume_provenance(
                target_followers_resume_source_request_id="30000000-0000-4000-8000-000000000002",
                auto_restart_resume_policy={"attempt_id": 2, "retry_index": 1},
            )
        self.assertEqual(reason, "provenance_resolved")
        self.assertEqual(provenance["source_attempt_id"], 2)
        self.assertEqual(provenance["release_sha"], full_sha)

    def test_auto_restart_missing_attempt_never_falls_back_to_one(self):
        with patch.object(
            runner,
            "_resolve_active_worker_release_sha",
            return_value="d" * 40,
        ):
            provenance, reason = runner._resolve_target_followers_resume_provenance(
                target_followers_resume_source_request_id="30000000-0000-4000-8000-000000000002",
                auto_restart_resume_policy={"phases_to_run": {"follow": True}},
            )
        self.assertIsNone(provenance)
        self.assertEqual(reason, "canonical_request_attempt_missing_or_invalid")

    def test_initial_non_restart_request_uses_attempt_one(self):
        with patch.object(
            runner,
            "_resolve_active_worker_release_sha",
            return_value="e" * 40,
        ):
            provenance, reason = runner._resolve_target_followers_resume_provenance(
                target_followers_resume_source_request_id="30000000-0000-4000-8000-000000000001",
                auto_restart_resume_policy=None,
            )
        self.assertEqual(reason, "provenance_resolved")
        self.assertEqual(provenance["source_attempt_id"], 1)

    def test_resolved_provenance_is_recorded_on_checkpoint_commit(self):
        full_sha = "f" * 40
        events = []
        rpc = FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0))
        with patch.object(
            runner,
            "_resolve_active_worker_release_sha",
            return_value=full_sha,
        ):
            provenance, reason = runner._resolve_target_followers_resume_provenance(
                target_followers_resume_source_request_id="30000000-0000-4000-8000-000000000002",
                auto_restart_resume_policy={"attempt_id": 2, "retry_index": 1},
            )
        self.assertEqual(reason, "provenance_resolved")
        controller = resume.build_runtime_controller(
            account_id=ACCOUNT_A,
            target_id=TARGET_A,
            target_username="neutral.target",
            run_id=RUN_A,
            **provenance,
            flags=resume.ResumeFlags(True, False, (ACCOUNT_A,)),
            rpc_call=rpc,
            hmac_secret=TEST_HMAC_SECRET,
            emit=lambda event, payload: events.append((event, payload)),
        )
        controller.load_and_plan()
        controller.claim()
        controller.observe_viewport(
            ["first", "overlap"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        controller.note_scroll_sent(previous_viewport_complete=True)
        controller.observe_viewport(
            ["overlap", "next"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertTrue(controller.commit_verified_progress())
        committed = [payload for event, payload in events if event == "checkpoint_committed"]
        self.assertEqual(committed[-1]["source_request_id"], provenance["source_request_id"])
        self.assertEqual(committed[-1]["source_attempt_id"], 2)
        self.assertEqual(committed[-1]["release_sha"], full_sha)
        commit = [params for name, params in rpc.calls if name.endswith("_v4")][-1]
        self.assertEqual(commit["p_commit_context"]["source_request_id"], provenance["source_request_id"])
        self.assertEqual(commit["p_commit_context"]["source_attempt_id"], 2)
        self.assertEqual(commit["p_commit_context"]["release_sha"], full_sha)

    def test_commit_response_without_atomic_provenance_fails_open_to_legacy(self):
        events = []
        rpc = FakeRpc(
            row=checkpoint_row(shadow_last_safe_depth=0),
            provenance_persisted=False,
        )
        controller = RepositoryAndControllerTests().controller(rpc, events=events)
        controller.load_and_plan()
        controller.claim()
        controller.observe_viewport(
            ["first", "overlap"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        controller.note_scroll_sent(previous_viewport_complete=True)
        controller.observe_viewport(
            ["overlap", "next"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertFalse(controller.commit_verified_progress())
        self.assertTrue(controller._safe_stop)
        self.assertIsNone(controller.last_verified_commit_context)
        reached_depth = controller.reached_depth
        controller.note_legacy_scroll_progress(
            legacy_scroll_diag(["overlap", "next"], ["next", "later"]),
            observed_scroll_index=2,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        verdict = controller.observe_viewport(
            ["next", "later"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertFalse(verdict.verified)
        self.assertEqual(controller.reached_depth, reached_depth)
        self.assertEqual(
            sum(name.startswith("commit_") for name, _ in rpc.calls),
            1,
        )
        self.assertTrue(controller.release())
        self.assertIsNone(controller.last_verified_commit_context)
        self.assertIn("v2_failed_open", [event for event, _ in events])

    def test_commit_response_requires_valid_atomic_provenance_event_id(self):
        for commit_event_id in (None, "", "not-a-uuid"):
            with self.subTest(commit_event_id=commit_event_id):
                events = []
                rpc = FakeRpc(
                    row=checkpoint_row(shadow_last_safe_depth=0),
                    provenance_persisted=True,
                    commit_event_id=commit_event_id,
                )
                controller = RepositoryAndControllerTests().controller(rpc, events=events)
                controller.load_and_plan()
                controller.claim()
                controller.observe_viewport(
                    ["first", "overlap"],
                    followers_surface_confirmed=True,
                    expected_target_confirmed=True,
                )
                controller.note_scroll_sent(previous_viewport_complete=True)
                controller.observe_viewport(
                    ["overlap", "next"],
                    followers_surface_confirmed=True,
                    expected_target_confirmed=True,
                )
                claimed_version_before = controller.claimed_version
                committed_depth_before = controller.last_committed_depth

                self.assertFalse(controller.commit_verified_progress())
                self.assertTrue(controller._safe_stop)
                self.assertEqual(controller.claimed_version, claimed_version_before)
                self.assertEqual(controller.last_committed_depth, committed_depth_before)
                self.assertEqual(controller.commit_count, 0)
                self.assertIsNone(controller.last_verified_commit_context)
                reasons = [
                    payload.get("reason")
                    for event, payload in events
                    if event in {"checkpoint_conflict", "v2_failed_open"}
                ]
                self.assertEqual(
                    reasons[-2:],
                    [
                        "commit_provenance_event_missing_or_invalid",
                        "commit_provenance_event_missing_or_invalid",
                    ],
                )

    def test_builder_rejects_invalid_checkpoint_provenance_before_any_rpc(self):
        events = []
        rpc = FakeRpc()
        controller = resume.build_runtime_controller(
            account_id=ACCOUNT_A,
            target_id=TARGET_A,
            target_username="neutral.target",
            run_id=RUN_A,
            source_request_id="not-a-uuid",
            source_attempt_id=0,
            release_sha="short",
            flags=resume.ResumeFlags(True, False, (ACCOUNT_A,)),
            rpc_call=rpc,
            hmac_secret=TEST_HMAC_SECRET,
            emit=lambda event, payload: events.append((event, payload)),
        )
        self.assertIsNone(controller)
        self.assertEqual(rpc.calls, [])
        self.assertEqual(events[-1][0], "v2_failed_open")
        self.assertEqual(events[-1][1]["reason"], "checkpoint_provenance_invalid")


class ShadowAccountScopeTests(unittest.TestCase):
    def flags(self, *account_ids, shadow=True, enforce=False):
        return resume.ResumeFlags(shadow, enforce, tuple(account_ids))

    def build(self, account_id, rpc, events, flags):
        return resume.build_runtime_controller(
            account_id=account_id,
            target_id=TARGET_A,
            target_username="neutral.target",
            run_id=RUN_A,
            source_request_id=REQUEST_A,
            source_attempt_id=1,
            release_sha=FULL_RELEASE_SHA,
            flags=flags,
            rpc_call=rpc,
            emit=lambda event, payload: events.append((event, payload)),
        )

    def test_60_allowed_canary_executes_shadow_and_keeps_legacy_navigation(self):
        rpc = FakeRpc(row=checkpoint_row())
        events = []
        flags = resume.ResumeFlags.from_env(
            {
                resume.SHADOW_FLAG: "true",
                resume.SHADOW_ACCOUNT_IDS_FLAG: ACCOUNT_A,
                resume.ENFORCE_FLAG: "false",
            }
        )
        controller = self.build(ACCOUNT_A, rpc, events, flags)
        self.assertIsNotNone(controller)
        plan = controller.load_and_plan()
        controller.claim()
        self.assertTrue(plan.use_legacy_navigation)
        self.assertEqual(controller.flags.mode, "shadow")
        self.assertGreater(len(rpc.calls), 0)

    def test_61_other_account_performs_zero_v2_rpc_or_event(self):
        rpc = FakeRpc()
        events = []
        self.assertIsNone(self.build(ACCOUNT_B, rpc, events, self.flags(ACCOUNT_A)))
        self.assertEqual(rpc.calls, [])
        self.assertEqual(events, [])

    def test_62_future_third_account_is_off_by_default(self):
        rpc = FakeRpc()
        events = []
        self.assertIsNone(self.build(ACCOUNT_C, rpc, events, self.flags(ACCOUNT_A)))
        self.assertEqual(rpc.calls, [])
        self.assertEqual(events, [])

    def test_63_global_shadow_false_disables_v2_for_all_accounts(self):
        for account_id in (ACCOUNT_A, ACCOUNT_B, ACCOUNT_C):
            rpc = FakeRpc()
            self.assertIsNone(self.build(account_id, rpc, [], self.flags(ACCOUNT_A, shadow=False)))
            self.assertEqual(rpc.calls, [])

    def test_64_empty_allowlist_disables_v2_when_shadow_true(self):
        rpc = FakeRpc()
        self.assertIsNone(self.build(ACCOUNT_A, rpc, [], self.flags()))
        self.assertEqual(rpc.calls, [])

    def test_65_enforce_true_is_limited_to_existing_allowlisted_account(self):
        rpc = FakeRpc(row=checkpoint_row())
        controller = self.build(ACCOUNT_A, rpc, [], self.flags(ACCOUNT_A, enforce=True))
        self.assertIsNotNone(controller)
        self.assertEqual(controller.flags.mode, "enforce")
        other_rpc = FakeRpc(row=checkpoint_row(account_id=ACCOUNT_B))
        self.assertIsNone(self.build(ACCOUNT_B, other_rpc, [], self.flags(ACCOUNT_A, enforce=True)))
        self.assertEqual(other_rpc.calls, [])

    def test_66_no_account_id_is_hardcoded_in_product(self):
        source = Path(resume.__file__).read_text(encoding="utf-8")
        runner_source = (Path(resume.__file__).parent / "runner.py").read_text(encoding="utf-8")
        config_source = (Path(resume.__file__).parent / "config.py").read_text(encoding="utf-8")
        for account_id in (ACCOUNT_A, ACCOUNT_B, ACCOUNT_C):
            self.assertNotIn(account_id, source + runner_source + config_source)
        self.assertNotIn("username", inspect.getsource(resume.ResumeFlags.shadow_allowed_for))

    def test_67_scheduler_accounts_keep_legacy_and_only_canary_gets_shadow(self):
        flags = self.flags(ACCOUNT_A)
        controllers = []
        rpc_by_account = {}
        for account_id in (ACCOUNT_A, ACCOUNT_B, ACCOUNT_C):
            rpc = FakeRpc(row=checkpoint_row(account_id=account_id))
            rpc_by_account[account_id] = rpc
            controllers.append(self.build(account_id, rpc, [], flags))
        self.assertEqual([controller is not None for controller in controllers], [True, False, False])
        self.assertTrue(controllers[0].load_and_plan().use_legacy_navigation)
        self.assertEqual(rpc_by_account[ACCOUNT_B].calls, [])
        self.assertEqual(rpc_by_account[ACCOUNT_C].calls, [])

    def test_68_allowlist_parser_is_bounded_and_fails_closed(self):
        self.assertEqual(resume.parse_account_id_allowlist(f"{ACCOUNT_A},{ACCOUNT_A}"), (ACCOUNT_A,))
        self.assertEqual(resume.parse_account_id_allowlist("not-an-account-id"), ())
        oversized = ",".join(str(uuid.UUID(int=index + 1)) for index in range(resume.MAX_SHADOW_ACCOUNT_IDS + 1))
        self.assertEqual(resume.parse_account_id_allowlist(oversized), ())

    def test_69_shadow_events_redact_target_id_and_report_rpc_duration(self):
        rpc = FakeRpc(row=checkpoint_row())
        events = []
        controller = self.build(ACCOUNT_A, rpc, events, self.flags(ACCOUNT_A))
        controller.load_and_plan()
        controller.claim()
        payloads = [payload for _, payload in events]
        self.assertTrue(all("target_id" not in payload for payload in payloads))
        self.assertTrue(all(TARGET_A not in json.dumps(payload) for payload in payloads))
        self.assertTrue(any("rpc_duration_ms" in payload for payload in payloads))

    def test_70_anchor_proposal_is_observable_but_never_changes_navigation(self):
        rpc = FakeRpc(row=checkpoint_row(shadow_visible_anchor_hashes=[resume.anchor_hash("b")]))
        events = []
        controller = self.build(ACCOUNT_A, rpc, events, self.flags(ACCOUNT_A))
        controller.load_and_plan()
        controller.observe_viewport(
            ["a", "b", "c"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        anchor_payload = next(payload for event, payload in events if event == "anchor_found")
        self.assertEqual(anchor_payload["proposed_cursor"], 0)
        self.assertTrue(anchor_payload["theoretical"])
        self.assertEqual(controller.navigation_mutations, 0)
        self.assertTrue(controller.plan.use_legacy_navigation)

    def test_71_shadow_rpc_uses_one_short_attempt(self):
        with patch.object(supabase_client, "_call_rpc", return_value={"ok": True}) as call:
            result = supabase_client.call_rpc_shadow("safe_test", {"p": 1})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(call.call_args.kwargs["max_retries"], 0)
        self.assertLessEqual(call.call_args.kwargs["timeout_seconds"], 5.0)

    def test_72_runner_shadow_failure_is_fail_open_and_redacted(self):
        runner_source = (Path(resume.__file__).parent / "runner.py").read_text(encoding="utf-8")
        self.assertIn('"checkpoint_load_or_claim_failed"', runner_source)
        self.assertIn('"viewport_observation_failed"', runner_source)
        self.assertIn("target_followers_resume_controller = None", runner_source)
        self.assertIn('"target_id_hash": target_followers_resume_v2.stable_id_hash(target_id)', runner_source)

    def test_73_same_ct_run_b_loads_run_a_shadow_checkpoint_and_progresses(self):
        flags = self.flags(ACCOUNT_A)
        run_a_rpc = FakeRpc(row=None)
        run_a_events = []
        run_a = resume.build_runtime_controller(
            account_id=ACCOUNT_A,
            target_id=TARGET_A,
            target_username="neutral.target",
            run_id=RUN_A,
            source_request_id=REQUEST_A,
            source_attempt_id=1,
            release_sha=FULL_RELEASE_SHA,
            flags=flags,
            rpc_call=run_a_rpc,
            emit=lambda event, payload: run_a_events.append((event, payload)),
        )
        self.assertIsNotNone(run_a)
        self.assertTrue(run_a.load_and_plan().use_legacy_navigation)
        loaded_a = next(
            payload
            for event, payload in run_a_events
            if event == "target_followers_checkpoint_loaded"
        )
        self.assertFalse(loaded_a["checkpoint_found"])
        self.assertFalse(loaded_a["checkpoint_loaded"])
        self.assertEqual(loaded_a["checkpoint_depth_before"], 0)
        self.assertTrue(run_a.claim())
        run_a.observe_viewport(
            ["row1", "row2"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertTrue(run_a.note_scroll_sent(previous_viewport_complete=True))
        verdict_a = run_a.observe_viewport(
            ["row2", "row3"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertTrue(verdict_a.verified)
        self.assertTrue(run_a.commit_verified_progress(cursor_handle="row3"))
        commit_a = next(
            params for name, params in run_a_rpc.calls if name.startswith("commit_")
        )
        self.assertEqual(commit_a["p_last_safe_depth"], 1)

        run_b_rpc = FakeRpc(
            row=checkpoint_row(
                shadow_last_safe_depth=1,
                shadow_last_safe_anchor=resume.anchor_hash("row3"),
                shadow_anchor_fingerprint=resume.viewport_fingerprint(["row2", "row3"]),
                shadow_visible_anchor_hashes=list(
                    resume.bounded_anchor_hashes(["row2", "row3"])
                ),
                last_run_id=RUN_A,
            )
        )
        events = []
        run_b = resume.build_runtime_controller(
            account_id=ACCOUNT_A,
            target_id=TARGET_A,
            target_username="neutral.target",
            run_id=RUN_B,
            source_request_id=REQUEST_B,
            source_attempt_id=1,
            release_sha=FULL_RELEASE_SHA,
            flags=flags,
            rpc_call=run_b_rpc,
            emit=lambda event, payload: events.append((event, payload)),
        )
        self.assertIsNotNone(run_b)
        plan_b = run_b.load_and_plan()
        self.assertTrue(plan_b.use_legacy_navigation)
        self.assertEqual(plan_b.previous_depth, 1)
        self.assertEqual(run_b.reached_depth, 0)
        self.assertEqual(run_b.last_committed_depth, 1)
        loaded_b = next(
            payload
            for event, payload in events
            if event == "target_followers_checkpoint_loaded"
        )
        self.assertTrue(loaded_b["checkpoint_found"])
        self.assertTrue(loaded_b["checkpoint_loaded"])
        self.assertEqual(loaded_b["checkpoint_depth_before"], 1)
        self.assertEqual(loaded_b["proposed_resume_depth"], 1)
        self.assertEqual(loaded_b["legacy_start_depth"], 0)
        self.assertTrue(run_b.claim())
        run_b.observe_viewport(
            ["row1", "row2"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertTrue(run_b.note_scroll_sent(previous_viewport_complete=True))
        first_physical_scroll = run_b.observe_viewport(
            ["row2", "row3"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertTrue(first_physical_scroll.verified)
        self.assertEqual(run_b.reached_depth, 1)
        self.assertFalse(run_b.commit_verified_progress(cursor_handle="row3"))
        self.assertFalse(any(name.startswith("commit_") for name, _ in run_b_rpc.calls))

        self.assertTrue(run_b.note_scroll_sent(previous_viewport_complete=True))
        second_physical_scroll = run_b.observe_viewport(
            ["row3", "row4"],
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
        )
        self.assertTrue(second_physical_scroll.verified)
        self.assertEqual(run_b.reached_depth, 2)
        self.assertTrue(run_b.commit_verified_progress(cursor_handle="row4"))
        commit_b = next(
            params for name, params in run_b_rpc.calls if name.startswith("commit_")
        )
        self.assertEqual(commit_b["p_last_safe_depth"], 2)
        self.assertEqual(run_b.navigation_mutations, 0)
        self.assertTrue(any(event == "target_followers_checkpoint_loaded" for event, _ in events))

    def test_74_stolm_missing_checkpoint_two_legacy_scrolls_then_run_b_loads_depth_two(self):
        flags = self.flags(ACCOUNT_A)
        before = [f"stolm.row{i:02d}" for i in range(1, 9)]
        after_one = before[-2:] + [f"stolm.row{i:02d}" for i in range(9, 16)]
        after_two = after_one[-2:] + [f"stolm.row{i:02d}" for i in range(16, 23)]
        run_a_rpc = FakeRpc(row=None)
        run_a = resume.build_runtime_controller(
            account_id=ACCOUNT_A,
            target_id=TARGET_A,
            target_username="stolm_",
            run_id=RUN_A,
            source_request_id=REQUEST_A,
            source_attempt_id=1,
            release_sha=FULL_RELEASE_SHA,
            flags=flags,
            rpc_call=run_a_rpc,
        )
        self.assertIsNotNone(run_a)
        self.assertEqual(run_a.load_and_plan().previous_depth, 0)
        self.assertTrue(run_a.claim())
        run_a.observe_viewport(before, followers_surface_confirmed=True, expected_target_confirmed=True)
        for index, (viewport_before, viewport_after) in enumerate(
            ((before, after_one), (after_one, after_two)),
            start=1,
        ):
            staged = run_a.note_legacy_scroll_progress(
                legacy_scroll_diag(viewport_before, viewport_after),
                observed_scroll_index=index,
                followers_surface_confirmed=True,
                expected_target_confirmed=True,
            )
            self.assertEqual(staged.reason, "legacy_scroll_proof_staged")
            self.assertTrue(
                run_a.observe_viewport(
                    viewport_after,
                    followers_surface_confirmed=True,
                    expected_target_confirmed=True,
                ).verified
            )
            self.assertTrue(run_a.commit_verified_progress(cursor_handle=viewport_after[-1]))
        self.assertEqual(run_a.last_committed_depth, 2)
        self.assertTrue(run_a.flush_verified_progress(boundary="target_end"))
        self.assertTrue(run_a.release())

        run_b_events = []
        run_b = resume.build_runtime_controller(
            account_id=ACCOUNT_A,
            target_id=TARGET_A,
            target_username="stolm_",
            run_id=RUN_B,
            source_request_id=REQUEST_B,
            source_attempt_id=1,
            release_sha=FULL_RELEASE_SHA,
            flags=flags,
            rpc_call=FakeRpc(
                row=checkpoint_row(
                    shadow_last_safe_depth=2,
                    shadow_last_safe_anchor=resume.anchor_hash(after_two[-1]),
                    shadow_anchor_fingerprint=resume.viewport_fingerprint(after_two),
                    shadow_visible_anchor_hashes=list(resume.bounded_anchor_hashes(after_two)),
                    last_run_id=RUN_A,
                )
            ),
            emit=lambda event, payload: run_b_events.append((event, payload)),
        )
        self.assertIsNotNone(run_b)
        plan_b = run_b.load_and_plan()
        self.assertEqual(plan_b.previous_depth, 2)
        self.assertEqual(run_b.reached_depth, 0)
        loaded = next(
            payload
            for event, payload in run_b_events
            if event == "target_followers_checkpoint_loaded"
        )
        self.assertTrue(loaded["checkpoint_found"])
        self.assertTrue(loaded["checkpoint_loaded"])
        self.assertEqual(loaded["checkpoint_depth_before"], 2)
        self.assertEqual(loaded["proposed_resume_depth"], 2)
        self.assertEqual(loaded["legacy_start_depth"], 0)
        plan_event = next(
            payload for event, payload in run_b_events if event == "resume_plan_built"
        )
        self.assertEqual(plan_event["theoretical_scrolls_avoided"], 2)
        self.assertTrue(plan_b.use_legacy_navigation)
        self.assertEqual(run_b.navigation_mutations, 0)


if __name__ == "__main__":
    unittest.main()
