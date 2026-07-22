from __future__ import annotations

import json
import inspect
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import target_followers_progressive_resume_v2 as resume
from target_followers_resume_replay import compare_legacy_to_theoretical_v2, load_redacted_corpus


ACCOUNT_A = "00000000-0000-0000-0000-000000000001"
ACCOUNT_B = "00000000-0000-0000-0000-000000000002"
ACCOUNT_C = "00000000-0000-0000-0000-000000000003"
TARGET_A = "10000000-0000-0000-0000-000000000001"
TARGET_B = "10000000-0000-0000-0000-000000000002"
RUN_A = "20000000-0000-0000-0000-000000000001"


def checkpoint_row(**overrides):
    row = {
        "account_id": ACCOUNT_A,
        "target_id": TARGET_A,
        "target_username_normalized": "neutral.target",
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


class FakeRpc:
    def __init__(self, *, row=None, claim_ok=True, commit_ok=True):
        self.row = row
        self.claim_ok = claim_ok
        self.commit_ok = commit_ok
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
            return {"ok": True, "reason": "claimed", "optimistic_version": self.version}
        if name.startswith("commit_"):
            if not self.commit_ok:
                return {"ok": False, "reason": "optimistic_version_conflict", "optimistic_version": self.version + 1}
            self.version += 1
            return {"ok": True, "reason": "committed", "optimistic_version": self.version}
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
        self.assertTrue(resume.validate_depth_transition(observation(["a"]), observation(["b"])).verified)

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

    def test_31_username_changed(self):
        cp = resume.Checkpoint.from_rpc(checkpoint_row())
        self.assertEqual(resume.validate_checkpoint(cp, account_id=ACCOUNT_A, target_id=TARGET_A, target_username="renamed.target")[1], "target_username_changed")

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
            emit=lambda event, payload: sink.append((event, payload)),
        )

    def test_38_rpc_get_contract(self):
        rpc = FakeRpc(row=checkpoint_row())
        cp = resume.ResumeRepository(rpc).get(account_id=ACCOUNT_A, target_id=TARGET_A)
        self.assertEqual(cp.target_id, TARGET_A)
        self.assertEqual(rpc.calls[0][0], "get_target_followers_resume_checkpoint")

    def test_39_rpc_claim_bounds_lease(self):
        rpc = FakeRpc()
        resume.ResumeRepository(rpc).claim(account_id=ACCOUNT_A, target_id=TARGET_A, target_username="neutral.target", run_id=RUN_A, mode="shadow", expected_version=None, lease_seconds=9999)
        self.assertEqual(rpc.calls[-1][1]["p_lease_seconds"], 900)

    def test_40_claim_conflict_is_clean(self):
        events = []
        ctl = self.controller(FakeRpc(row=checkpoint_row(), claim_ok=False), events=events)
        ctl.load_and_plan()
        self.assertFalse(ctl.claim())
        self.assertIn("target_followers_checkpoint_commit_conflict", [e for e, _ in events])

    def test_41_two_runs_compare_and_swap(self):
        rpc = FakeRpc(row=checkpoint_row(), commit_ok=False)
        ctl = self.controller(rpc)
        ctl.load_and_plan(); ctl.claim()
        ctl.current_viewport = observation(["b"])
        self.assertFalse(ctl.commit_verified_progress())

    def test_42_worker_restart_loads_persisted_depth(self):
        ctl = self.controller(FakeRpc(row=checkpoint_row(shadow_last_safe_depth=5)))
        self.assertEqual(ctl.load_and_plan().previous_depth, 5)

    def test_43_baseline_then_x_plus_one(self):
        ctl = self.controller(FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0)))
        ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(["a", "b"], followers_surface_confirmed=True, expected_target_confirmed=True)
        ctl.note_scroll_sent(previous_viewport_complete=True)
        verdict = ctl.observe_viewport(["c", "d"], followers_surface_confirmed=True, expected_target_confirmed=True)
        self.assertTrue(verdict.verified)
        self.assertEqual(ctl.reached_depth, 1)
        self.assertTrue(ctl.commit_verified_progress())

    def test_44_partial_viewport_never_advances(self):
        ctl = self.controller(FakeRpc(row=checkpoint_row(shadow_last_safe_depth=0)))
        ctl.load_and_plan(); ctl.claim()
        ctl.observe_viewport(["a"], followers_surface_confirmed=True, expected_target_confirmed=True)
        ctl.note_scroll_sent(previous_viewport_complete=False)
        verdict = ctl.observe_viewport(["b"], followers_surface_confirmed=True, expected_target_confirmed=True)
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
        ctl = self.controller(rpc); ctl.load_and_plan(); ctl.claim(); ctl.current_viewport = observation(["sensitive.name"])
        ctl.commit_verified_progress(cursor_handle="sensitive.name")
        params = [p for n, p in rpc.calls if n.startswith("commit_")][-1]
        self.assertNotIn("sensitive.name", json.dumps(params))

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
        self.assertIn("target_followers_end_reached", [e for e, _ in events])

    def test_52_all_required_event_names_present(self):
        self.assertEqual(len(resume.EVENTS), 11)


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


class ShadowAccountScopeTests(unittest.TestCase):
    def flags(self, *account_ids, shadow=True, enforce=False):
        return resume.ResumeFlags(shadow, enforce, tuple(account_ids))

    def build(self, account_id, rpc, events, flags):
        return resume.build_runtime_controller(
            account_id=account_id,
            target_id=TARGET_A,
            target_username="neutral.target",
            run_id=RUN_A,
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

    def test_65_enforce_true_fails_closed_even_for_allowlisted_account(self):
        rpc = FakeRpc()
        self.assertIsNone(self.build(ACCOUNT_A, rpc, [], self.flags(ACCOUNT_A, enforce=True)))
        self.assertEqual(rpc.calls, [])

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


if __name__ == "__main__":
    unittest.main()
