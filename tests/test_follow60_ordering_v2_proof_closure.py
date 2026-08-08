from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import follow60_ordering_v2_proof_closure as closure


def _event(ts: str, event: str, candidate: str = "alice", **fields):
    return {
        "ts": ts,
        "event": event,
        "follower_username": candidate,
        **fields,
    }


class Follow60OrderingV2ProofClosureTests(unittest.TestCase):
    def test_raw_avoidable_stops_before_viewer_and_v5(self) -> None:
        events = [
            _event("2026-08-08T10:00:00+00:00", "post_follow_post_likes_phase_started"),
            _event("2026-08-08T10:00:01+00:00", "post_follow_post_like_open_started"),
            _event("2026-08-08T10:00:04+00:00", "post_open_intent_v2_consumed"),
            _event("2026-08-08T10:00:06+00:00", "post_follow_viewer_detection_strategy"),
            _event("2026-08-08T10:00:09+00:00", "post_follow_open_like_proof_stashed"),
            {
                "ts": "2026-08-08T10:00:10+00:00",
                "event": "follow60_ordering_v2_shadow_terminal",
                "event_status": "completed",
                "candidate_username": "alice",
                "candidate_index": 1,
                "run_id": "run-a",
                "opened_at_monotonic_ns": 1,
                "terminal_at_monotonic_ns": 10_000_000_001,
                "classification": {"initial": "DIRECT_GRID_SAFE"},
                "v1_actual_execution": {"selected_path": "GOLDEN_DIRECT"},
            },
        ]
        rows = closure.reconstruct_raw_avoidable_rows(events)
        self.assertEqual(1, len(rows))
        self.assertEqual(4000.0, rows[0].raw_avoidable_ms)
        self.assertEqual("HIGH", rows[0].confidence)
        self.assertEqual(10000.0, rows[0].v1_cycle_ms)
        self.assertFalse(rows[0].postopen_tap_observed)

    def test_no_tap_attempt_uses_explicit_terminal(self) -> None:
        events = [
            _event("2026-08-08T10:00:00+00:00", "post_follow_post_likes_phase_started"),
            _event("2026-08-08T10:00:02+00:00", "post_follow_post_like_open_started"),
            _event(
                "2026-08-08T10:00:12+00:00",
                "follow_60s_post_grid_evidence_golden_direct_completed",
            ),
            {
                "ts": "2026-08-08T10:00:13+00:00",
                "event": "follow60_ordering_v2_shadow_terminal",
                "event_status": "completed",
                "candidate_username": "alice",
                "candidate_index": 1,
                "run_id": "run-a",
                "opened_at_monotonic_ns": 1,
                "terminal_at_monotonic_ns": 13_000_000_001,
                "classification": {"initial": "DIRECT_GRID_SAFE"},
                "v1_actual_execution": {"selected_path": "GOLDEN_DIRECT"},
            },
        ]
        row = closure.reconstruct_raw_avoidable_rows(events)[0]
        self.assertEqual(12000.0, row.raw_avoidable_ms)
        self.assertIn("no_tap", row.source_evidence)

    def test_reentry_breakdown_is_common_v1_cost(self) -> None:
        events = [
            _event("2026-08-08T10:00:00+00:00", "post_follow_post_likes_phase_started"),
            _event("2026-08-08T10:00:05+00:00", "visual_post_like_verify_completed"),
            _event("2026-08-08T10:00:05.500000+00:00", "visual_return_to_profile_started"),
            _event("2026-08-08T10:00:06.700000+00:00", "visual_return_to_profile_success"),
            _event(
                "2026-08-08T10:00:07.600000+00:00",
                "follow_60s_fresh_ui_proof_stashed",
                purpose="return_candidate_profile",
            ),
            {
                "ts": "2026-08-08T10:00:08+00:00",
                "event": "follow60_ordering_v2_shadow_terminal",
                "event_status": "completed",
                "candidate_username": "alice",
                "run_id": "run-a",
                "classification": {"initial": "DIRECT_GRID_SAFE"},
            },
        ]
        row = closure.reconstruct_reentry_rows(events)[0]
        self.assertEqual(500.0, row.terminal_to_back_dispatch_ms)
        self.assertEqual(1200.0, row.back_dispatch_to_profile_ms)
        self.assertEqual(900.0, row.profile_to_exact_identity_ms)
        self.assertEqual(2600.0, row.common_v1_reentry_total_ms)

    def _binding_and_snapshot(self):
        binding = {
            "account_id": "account-a",
            "request_id": "request-a",
            "run_id": "run-a",
            "business_session_id": "session-a",
            "attempt_id": 1,
            "binding_kind": "mainline",
            "worker_sha": "a" * 40,
            "target_id": "target-a",
            "candidate_username": "alice",
            "source_profile_username": "ct-a",
            "action_id": "action-a",
            "package": "com.instagram.android",
        }
        snapshot = {
            **binding,
            "activity": "com.instagram.mainactivity.InstagramMainActivity",
            "surface": "candidate_profile_after_like",
            "candidate_visible_exact": True,
            "follow_cta_state": "follow_exact",
            "follow_cta_bounds": {"left": 10, "top": 20, "right": 100, "bottom": 80},
            "overlay_absent": True,
            "challenge_absent": True,
            "navigation_generation": 9,
            "current_navigation_generation": 9,
            "ui_generation": 12,
            "current_ui_generation": 12,
            "created_at_monotonic": 100.0,
            "consumed": False,
        }
        return binding, snapshot

    def test_minimal_reentry_accepts_only_exact_fresh_snapshot(self) -> None:
        binding, snapshot = self._binding_and_snapshot()
        result = closure.validate_minimal_reentry_snapshot(
            snapshot, binding, now_monotonic=100.5
        )
        self.assertTrue(result["ok"])
        self.assertEqual("minimal_reentry_proof_exact", result["reason"])

    def test_minimal_reentry_fails_closed_for_wrong_profile_stale_bounds_and_overlay(self) -> None:
        binding, snapshot = self._binding_and_snapshot()
        cases = (
            ({"candidate_visible_exact": False}, "candidate_identity_not_exact"),
            ({"current_ui_generation": 13}, "ui_generation_stale"),
            ({"overlay_absent": False}, "overlay_or_challenge_not_excluded"),
            ({"follow_cta_state": "following_exact"}, "follow_cta_not_exact"),
        )
        for change, reason in cases:
            candidate = dict(snapshot)
            candidate.update(change)
            with self.subTest(reason=reason):
                result = closure.validate_minimal_reentry_snapshot(
                    candidate, binding, now_monotonic=100.5
                )
                self.assertFalse(result["ok"])
                self.assertEqual(reason, result["reason"])
        stale = closure.validate_minimal_reentry_snapshot(
            snapshot, binding, now_monotonic=102.0
        )
        self.assertEqual("proof_stale", stale["reason"])

    def test_economic_projection_charges_new_action_only_to_v1_no_tap_paths(self) -> None:
        terminal_common = {
            "event": "follow60_ordering_v2_shadow_terminal",
            "event_status": "completed",
            "run_id": "run-a",
            "classification": {"initial": "DIRECT_GRID_SAFE"},
        }
        events = [
            {
                **terminal_common,
                "candidate_username": "alice",
                "candidate_index": 1,
                "opened_at_monotonic_ns": 1,
                "terminal_at_monotonic_ns": 20_000_000_001,
            },
            {
                **terminal_common,
                "candidate_username": "bob",
                "candidate_index": 2,
                "opened_at_monotonic_ns": 1,
                "terminal_at_monotonic_ns": 20_000_000_001,
            },
        ]
        raw_rows = [
            closure.RawAvoidableRow(
                "run-a", 1, "alice", "SAFE_DIRECT", 20000.0, True,
                10000.0, 0.0, 0.0, 0.0, "tap", "HIGH", "",
            ),
            closure.RawAvoidableRow(
                "run-a", 2, "bob", "GOLDEN_DIRECT", 20000.0, False,
                10000.0, 0.0, 0.0, 0.0, "no_tap", "HIGH", "",
            ),
        ]
        action_rows = [closure.DirectActionRow("run-a", "alice", 1000.0, 5000.0)]
        result = closure.calculate_economic_projection(
            events, raw_rows, action_rows, persistence_incremental_ms=0.0
        )
        self.assertEqual(10000.0, result["eligible_v2_standard_ms"]["min"])
        self.assertEqual(15000.0, result["eligible_v2_standard_ms"]["max"])
        self.assertEqual(7500.0, result["eligible_net_gain_standard_ms"]["median"])

    def test_cli_analysis_reads_existing_jsonl_without_mutation(self) -> None:
        rows = [
            _event("2026-08-08T10:00:00+00:00", "post_follow_post_likes_phase_started"),
            _event("2026-08-08T10:00:01+00:00", "post_open_intent_v2_consumed"),
            {
                "ts": "2026-08-08T10:00:02+00:00",
                "event": "follow60_ordering_v2_shadow_terminal",
                "event_status": "completed",
                "candidate_username": "alice",
                "candidate_index": 1,
                "run_id": "run-a",
                "opened_at_monotonic_ns": 1,
                "terminal_at_monotonic_ns": 2_000_000_001,
                "classification": {"initial": "DIRECT_GRID_SAFE"},
                "v1_actual_execution": {"selected_path": "SAFE_DIRECT"},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.log"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            report = closure.analyze_logs([path])
        self.assertEqual(1, report["raw_avoidable_total_eligible"])
        self.assertEqual(100.0, report["raw_avoidable_coverage_percent"])
        self.assertFalse(report["behavior_changed"])
        self.assertEqual(0, report["ui_acquisition_count"])


if __name__ == "__main__":
    unittest.main()
