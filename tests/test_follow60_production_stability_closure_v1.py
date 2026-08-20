from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import account_session_orchestrator as account_session
import instagram_navigation as navigation
import logs
import post_follow_stage_outbox
from follow_outcome_contract import build_follow_termination_decision
from worker_runtime_identity import WorkerRuntimeIdentity


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "follow60_production_stability_closure_v1.json"
)


def _partial_flush() -> dict[str, object]:
    return {
        "ok": False,
        "reason": "follow60_candidate_local_post_follow_recovery_required",
        "candidate_local": True,
        "partial_resumable": True,
        "partial_receipts_persisted": True,
        "retained_for_idempotent_recovery": True,
        "persisted_stages": ["mute_stories_verified", "return_ct_exact"],
        "missing_required_stages": ["mute_posts_verified"],
        "action_id_hash": "a" * 64,
        "candidate_username": "sanitized_candidate",
    }


def _partial_outcome(follows_completed_count: int = 1) -> dict[str, object]:
    return build_follow_termination_decision(
        exit_code=53,
        first_causal_reason="following_button_not_found",
        follows_completed_count=follows_completed_count,
        target_follow_budget_effective=120,
        target_attribution={
            "account_id": "00000000-0000-4000-8000-000000000001",
            "run_id": "00000000-0000-4000-8000-000000000002",
            "target_id": "00000000-0000-4000-8000-000000000003",
            "candidate_username": "sanitized_candidate",
            "source_profile_username": "sanitized_ct",
        },
        physical_follow_preserved=True,
        canonical_follow_receipt_present=True,
        candidate_local_failure=True,
        post_follow_recovery_required=True,
        no_new_follow_until_recovered=True,
        safe_boundary=True,
        safe_next_step="handoff_to_unfollow",
    )


class Follow60ProductionStabilityClosureV1Test(unittest.TestCase):
    @staticmethod
    def _run_exit_53_rotation(follows_completed_count: int) -> dict[str, object]:
        def run_followers(_device: object, **_kwargs: object) -> int:
            decision = _partial_outcome(follows_completed_count)
            run_followers.last_session_summary = {
                "follow_session_outcome": "partial_resumable",
                "follow_stop_reason": (
                    "follow60_candidate_local_post_follow_recovery_required"
                ),
                "first_causal_reason": "following_button_not_found",
                "follows_completed_count": follows_completed_count,
                "follow_processed_count": follows_completed_count,
                "follow_termination_decision": decision,
                "follow_outcome": decision,
            }
            return 53

        run_followers.last_session_summary = {}
        return account_session._run_follow_target_rotation(
            object(),
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="sanitized_account",
            run_id="00000000-0000-4000-8000-000000000002",
            follow_targets=[
                {
                    "target_id": "00000000-0000-4000-8000-000000000003",
                    "source_profile": "sanitized_ct",
                    "target_index": 0,
                },
                {
                    "target_id": "00000000-0000-4000-8000-000000000004",
                    "source_profile": "must_not_rotate",
                    "target_index": 1,
                },
            ],
            run_followers_list_engine_session=run_followers,
            supabase_mode=False,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=2,
            max_follows_per_target_per_run=50,
        )

    def test_follow_persisted_partial_receipts_are_local_and_never_retap(self) -> None:
        classified = post_follow_stage_outbox.classify_post_follow_flush(
            _partial_flush(), canonical_follow_persisted=True
        )
        self.assertEqual(
            classified["failure_class"],
            "target_local_follow_durable_post_follow_pending",
        )
        self.assertTrue(classified["partial_resumable"])
        self.assertFalse(classified["follow_retap_allowed"])
        self.assertEqual(classified["safe_next_step"], "handoff_to_unfollow")

    def test_return_ok_shadow_flag_is_not_part_of_authoritative_classification(self) -> None:
        flush = _partial_flush()
        flush["return_ok"] = False
        classified = post_follow_stage_outbox.classify_post_follow_flush(
            flush, canonical_follow_persisted=True
        )
        self.assertTrue(classified["candidate_local"])
        self.assertTrue(classified["safe_boundary"])

    def test_canonical_follow_persistence_failure_remains_fail_closed(self) -> None:
        classified = post_follow_stage_outbox.classify_post_follow_flush(
            _partial_flush(), canonical_follow_persisted=False
        )
        self.assertEqual(
            classified["failure_class"], "canonical_follow_persistence_failure"
        )
        self.assertFalse(classified["candidate_local"])
        self.assertEqual(classified["safe_next_step"], "stop_fail_closed")
        self.assertFalse(classified["follow_retap_allowed"])

    def test_unproved_partial_receipt_cannot_be_downgraded_to_local(self) -> None:
        flush = _partial_flush()
        flush["persisted_stages"] = ["mute_stories_verified"]
        classified = post_follow_stage_outbox.classify_post_follow_flush(
            flush, canonical_follow_persisted=True
        )
        self.assertEqual(
            classified["failure_class"],
            "post_follow_persistence_unclassified_fail_closed",
        )
        self.assertFalse(classified["safe_boundary"])

    def test_exit_53_exact_contract_can_handoff_to_unfollow(self) -> None:
        gate = account_session._evaluate_h3_follow_exit_code_gate(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="sanitized_account",
            follow_exit_code=53,
            diagnostic={
                "follow_outcome": _partial_outcome(),
                "handoff_would_run": True,
                "unfollow_enabled": True,
                "unfollow_mode": "unfollow-any",
                "pending_unfollow_count": 1,
            },
            real_max_actions_effective=1,
        )
        self.assertTrue(gate["follow_exit_code_allowed"])
        self.assertEqual(
            gate["follow_exit_code_allow_reason"],
            "follow_candidate_local_post_follow_partial_safe_for_unfollow",
        )

    def test_exit_53_without_exact_contract_is_blocked(self) -> None:
        gate = account_session._evaluate_h3_follow_exit_code_gate(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="sanitized_account",
            follow_exit_code=53,
            diagnostic={
                "follow_outcome": {"phase_status": "partial_resumable"},
                "handoff_would_run": True,
                "unfollow_enabled": True,
                "unfollow_mode": "unfollow-any",
                "pending_unfollow_count": 1,
            },
            real_max_actions_effective=1,
        )
        self.assertFalse(gate["follow_exit_code_allowed"])
        self.assertEqual(
            gate["follow_exit_code_block_reason"],
            "follow_termination_decision_invalid",
        )

    def test_follow_count_one_exit_53_is_not_reclassified_or_rotated(self) -> None:
        result = self._run_exit_53_rotation(1)
        self.assertEqual(result["exit_code"], 53)
        self.assertEqual(len(result["attempts"]), 1)
        summary = result["summary"]
        self.assertEqual(summary["follow_session_outcome"], "partial_resumable")
        self.assertEqual(summary["safe_next_step"], "handoff_to_unfollow")
        self.assertFalse(summary["target_rotation_allowed"])
        self.assertFalse(summary["follow_retap_allowed"])

    def test_follow_count_multi_exit_53_preserves_all_verified_follows(self) -> None:
        result = self._run_exit_53_rotation(7)
        self.assertEqual(result["exit_code"], 53)
        self.assertEqual(result["global_follows_completed"], 7)
        self.assertEqual(result["summary"]["follows_completed_count"], 7)
        self.assertEqual(len(result["attempts"]), 1)

    def test_mute_cy_584_compact_cta_needs_real_aligned_peer(self) -> None:
        bounds = {"left": 20, "top": 540, "right": 360, "bottom": 628}
        peer = {
            "text": "Message",
            "resource_id": "profile_action_message",
            "bounds": {"left": 380, "top": 542, "right": 760, "bottom": 626},
        }
        self.assertEqual(
            navigation._mute_engine_v2_following_cta_structure_ok(
                bounds=bounds,
                peer_controls=[peer],
                ww=1080,
                wh=2340,
                candidate_profile_confirmed=True,
            ),
            (True, "aligned_profile_action_peer"),
        )
        self.assertEqual(
            navigation._mute_engine_v2_following_cta_structure_ok(
                bounds=bounds,
                peer_controls=[],
                ww=1080,
                wh=2340,
                candidate_profile_confirmed=True,
            ),
            (False, "profile_action_row_ownership_unproved"),
        )

    def test_mute_peer_scan_includes_non_clickable_action_label_children(self) -> None:
        source = Path(navigation.__file__).read_text(encoding="utf-8")
        peer_loader = source[
            source.index("def _load_structural_peers") :
            source.index("def _element_passes", source.index("def _load_structural_peers"))
        ]
        self.assertIn('classNameMatches=r".*(Button|TextView|ImageView)"', peer_loader)
        self.assertNotIn("clickable=True", peer_loader)

    def test_recovery_budget_is_capped_at_eight_seconds(self) -> None:
        self.assertEqual(
            navigation._POST_FOLLOW_MUTE_RECOVERY_TOTAL_ATTEMPTS_MAX, 2
        )
        token = navigation._MUTE_ENGINE_V2_BUDGET_OVERRIDE_S.set(8.0)
        try:
            with patch.object(navigation.time, "perf_counter", return_value=105.0):
                self.assertEqual(navigation._mute_engine_v2_remaining_s(100.0), 3.0)
        finally:
            navigation._MUTE_ENGINE_V2_BUDGET_OVERRIDE_S.reset(token)

    def test_runtime_logs_and_receipts_default_to_mutable_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root, patch.dict(
            os.environ,
            {"PHONEFARM_RUNTIME_ROOT": temp_root},
            clear=False,
        ):
            self.assertEqual(logs._runs_dir(), Path(temp_root) / "logs" / "runs")
        self.assertNotIn(
            str(Path(__file__).resolve().parents[1]),
            str(logs._runs_dir()),
        )

    def test_exit_53_still_blocks_account_or_platform_safety_markers(self) -> None:
        gate = account_session._evaluate_h3_follow_exit_code_gate(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="sanitized_account",
            follow_exit_code=53,
            diagnostic={
                "follow_outcome": _partial_outcome(),
                "handoff_would_run": True,
                "unfollow_enabled": True,
                "unfollow_mode": "unfollow-any",
                "pending_unfollow_count": 1,
                "platform_state": "challenge",
            },
            real_max_actions_effective=1,
        )
        self.assertFalse(gate["follow_exit_code_allowed"])
        self.assertIn("unsafe_follow_signal_challenge", gate["follow_exit_code_block_reason"])

    def test_real_runtime_identity_type_is_exercised(self) -> None:
        identity = WorkerRuntimeIdentity(
            runtime_root="/sanitized/release",
            worker_sha="b" * 40,
            source="production_stability_fixture",
        )
        self.assertTrue(identity.runtime_root_ok)
        self.assertEqual(identity.worker_sha, "b" * 40)
        self.assertFalse(hasattr(identity, "full_sha"))

    def test_authoritative_incident_fixture_suite_contains_all_nineteen_cases(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "PHONE_FARM_PRODUCTION_INCIDENT_FIXTURES_V1")
        cases = payload["cases"]
        self.assertEqual(len(cases), 19)
        self.assertEqual(len({case["id"] for case in cases}), 19)
        self.assertIn(
            "follow_partial_to_unfollow", {case["id"] for case in cases}
        )


if __name__ == "__main__":
    unittest.main()
