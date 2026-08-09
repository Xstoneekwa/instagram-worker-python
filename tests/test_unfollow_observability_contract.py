from __future__ import annotations

import inspect
import unittest

import unfollow_session_orchestrator as orchestrator


class UnfollowObservabilityContractTest(unittest.TestCase):
    def test_scroll_budget_does_not_claim_supply_exhaustion_when_candidates_remain(self) -> None:
        reason, remaining = orchestrator._scroll_budget_stop_reason(
            {"alice", "bob", "charlie"},
            {"alice"},
        )
        self.assertEqual(reason, "ui_coverage_budget_exhausted")
        self.assertEqual(remaining, 2)

    def test_scroll_budget_can_report_true_exhaustion_when_plan_is_complete(self) -> None:
        reason, remaining = orchestrator._scroll_budget_stop_reason(
            {"alice"},
            {"alice"},
        )
        self.assertEqual(reason, "eligible_targets_exhausted")
        self.assertEqual(remaining, 0)

    def test_structured_funnel_and_reconciliation_events_are_emitted(self) -> None:
        source = inspect.getsource(orchestrator._run_real_unfollow_multi_loop)
        self.assertIn('"unfollow_candidate_funnel"', source)
        self.assertIn('"unfollow_run_reconciliation"', source)
        self.assertIn("verified == persisted", source)
        self.assertIn("persisted_outcomes_total=persisted_outcomes", source)

    def test_failed_verification_is_an_audit_outcome_not_a_persisted_action(self) -> None:
        delta = orchestrator.unfollow_persistence_count_delta(
            verify_ok=False,
            persist_ok=True,
        )
        self.assertEqual(delta["verified_persisted"], 0)
        self.assertEqual(delta["outcome_persisted"], 1)

    def test_verified_persisted_unfollow_counts_as_action_and_outcome(self) -> None:
        delta = orchestrator.unfollow_persistence_count_delta(
            verify_ok=True,
            persist_ok=True,
        )
        self.assertEqual(delta["verified_persisted"], 1)
        self.assertEqual(delta["outcome_persisted"], 1)

    def test_summary_exposes_partial_ui_coverage_with_canonical_resume_decision(self) -> None:
        source = inspect.getsource(orchestrator._run_real_unfollow_multi_loop)
        self.assertIn('"eligible_db_remaining": global_remaining_count', source)
        self.assertIn('"planned_eligible_remaining": remaining_planned_count', source)
        self.assertIn('base_summary.get("unplanned_eligible_count")', source)
        self.assertIn('"ui_coverage_status": (', source)
        self.assertIn(
            '"resume_recommended": bool(canonical_outcome.get("resume_recommended"))',
            source,
        )

    def test_summary_exposes_required_action_time_and_stop_metrics(self) -> None:
        source = inspect.getsource(orchestrator._run_real_unfollow_multi_loop)
        for field in (
            '"attempted": sent',
            '"verified": verified',
            '"persisted": persisted',
            '"phase_duration_seconds":',
            '"cleanup_reserve_seconds":',
            '"stop_reason": exploration_stop',
        ):
            self.assertIn(field, source)

    def test_strict_runtime_uses_plan_intersection_and_current_state_validation(self) -> None:
        source = inspect.getsource(orchestrator._run_real_unfollow_multi_loop)
        self.assertIn("if visible_key not in planned_usernames", source)
        self.assertGreaterEqual(source.count("detect_own_following_list_screen("), 2)
        self.assertIn("unfollow_action_blocked_unsafe_marker", source)
        self.assertIn('"plan_guided_mode": "enforced_allowlist"', source)
        self.assertIn("recover_following_viewport", source)
        self.assertNotIn("search_for_unfollow_username", source)

    def test_account_title_mismatch_is_an_unsafe_marker(self) -> None:
        markers = orchestrator._merge_unfollow_surface_unsafe_markers(
            [],
            {"failure_reason": "following_list_account_title_mismatch"},
        )
        self.assertEqual(markers, ["active_account_mismatch"])

    def test_unfollow_diagnostic_v2_preserves_unknown_outside_capped_plan(self) -> None:
        viewport, candidates = orchestrator.build_unfollow_diagnostic_v2(
            rows=[{"username": "planned.one"}, {"username": "backlog.two"}],
            visible_eval={
                "visible_eligible_matches": [
                    {"username": "planned.one"},
                    {"username": "backlog.two"},
                ],
                "visible_ineligible_rows": [],
            },
            planned_usernames={"planned.one"},
            row_cache={"planned.one": {"id": "1"}, "backlog.two": {"id": "2"}},
            attempted_usernames={"planned.one"},
            verified_usernames={"planned.one"},
            persisted_usernames=set(),
            viewport_index=3,
            scroll_depth=2,
        )
        self.assertTrue(viewport["viewport_fingerprint"])
        by_username = {row["username"]: row for row in candidates}
        self.assertTrue(by_username["planned.one"]["db_eligible_at_start"])
        self.assertIsNone(by_username["backlog.two"]["db_eligible_at_start"])
        self.assertTrue(by_username["planned.one"]["action_attempted"])
        self.assertFalse(by_username["planned.one"]["persistence_ok"])

    def test_unfollow_diagnostic_v2_events_are_wired_without_extra_ui_acquisition(self) -> None:
        source = inspect.getsource(orchestrator._run_real_unfollow_multi_loop)
        self.assertIn('"unfollow_diagnostic_v2_viewport"', source)
        self.assertIn('"unfollow_candidate_lineage_v1"', source)
        self.assertIn("build_unfollow_diagnostic_v2(", source)


if __name__ == "__main__":
    unittest.main()
