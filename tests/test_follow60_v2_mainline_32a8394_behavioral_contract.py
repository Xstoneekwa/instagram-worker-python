from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import unittest

import follow60_ordering_v2_behavioral_canary_v1 as ordering
from follow_state_contract import FollowContext
import instagram_navigation as nav


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "follow60_v2_mainline_32a8394_contract.json"
)


class _ActionBarSelector:
    def __init__(self, title: str) -> None:
        self._title = title

    def exists(self, timeout: float = 0.0) -> bool:
        _ = timeout
        return True

    def get_text(self) -> str:
        return self._title


class _Device:
    def app_current(self) -> dict:
        return {
            "package": "com.instagram.android",
            "activity": "com.instagram.mainactivity.InstagramMainActivity",
        }

    def __call__(self, **kwargs):
        if kwargs.get("resourceIdMatches"):
            return _ActionBarSelector("candidate")
        return MagicMock(exists=MagicMock(return_value=False), info={})


def _plan() -> ordering.CandidateCyclePlanV2:
    proof = SimpleNamespace(
        candidate_username="candidate",
        action_id="action-32a8394",
        proof_hash="proof-32a8394",
        top_left_identity="absolute_row_1_column_1_unique",
        payload=lambda: {"candidate_username": "candidate"},
        post_grid_evidence={"cell": "top_left"},
    )
    return ordering.CandidateCyclePlanV2(
        selected_path="POST_FIRST_V2",
        binding=SimpleNamespace(),
        stable_proof=proof,
        deferred_follow=SimpleNamespace(payload=lambda: {"action_id": "action-32a8394"}),
        started_at_monotonic=1.0,
        required_post_follow_mute=True,
        enforce_current_like_evidence=True,
    )


class Follow60V2Mainline32a8394BehavioralContractTests(unittest.TestCase):
    def test_full_physical_call_graph_keeps_32a8394_order_with_point_b_mute(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        physical: list[str] = []
        receipts: list[str] = []
        orchestrator = ordering.Follow60OrderingV2OrchestratorV1(
            ledger_apply=lambda stage, _payload: receipts.append(stage) or {"ok": True}
        )
        orchestrator.begin(_plan())
        like_out = orchestrator.execute_post_first(
            _plan(),
            post_like_engine=lambda _ctx: physical.append("LIKE") or {
                "post_opened": True,
                "liked_count": 1,
                "like_action_state": "LIKE_ACTION_PERFORMED_NOW",
                "real_tap_sent": True,
                "fresh_like_verified": True,
                "phase_outcome": "success",
            },
        )
        self.assertTrue(like_out["ok"])

        physical.append("FOLLOW")
        ctx = FollowContext.from_follow_verified(
            follower_username="candidate",
            source_profile_username="source",
            visual_candidate_id="action-32a8394",
            follow_state_after="following",
        )
        post_like_attempt = MagicMock()
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(nav.config, "FOLLOW_PRIVATE_ACCOUNTS", False, create=True)
            )
            stack.enter_context(
                patch.object(nav.config, "ENABLE_VISUAL_FOLLOW_MUTE_FLOW", True, create=True)
            )
            stack.enter_context(
                patch.object(nav.config, "ENABLE_REAL_VISUAL_MUTE_AFTER_FOLLOW", True, create=True)
            )
            stack.enter_context(
                patch.object(nav, "_mute_engine_v2_build_lightweight_profile_det", return_value={"action_bar_title": "candidate"})
            )
            stack.enter_context(
                patch.object(nav, "run_mute_engine_v2", side_effect=lambda *a, **k: physical.append("MUTE") or {
                    "ok": True,
                    "outcome": "success",
                    "posts_verified": True,
                    "stories_verified": True,
                    "timings_ms": {"mute_total_ms": 1.0},
                })
            )
            stack.enter_context(
                patch.object(nav, "_post_mute_state_checkpoint", return_value={
                    "navigation_state": "CANDIDATE_PROFILE",
                    "candidate_context": {"username": "candidate"},
                })
            )
            stack.enter_context(
                patch.object(nav, "_validate_post_mute_sheet_closed_proof", return_value=(False, {}, 0.0, "unit"))
            )
            stack.enter_context(
                patch.object(nav, "run_post_follow_post_likes_phase", post_like_attempt)
            )
            stack.enter_context(
                patch.object(nav, "post_follow_controlled_return_to_followers_list", side_effect=lambda *a, **k: physical.append("RETURN_CT") or (True, "unit", None))
            )
            stack.enter_context(patch("follow_60s_canary.enabled", return_value=True))
            out = nav.run_visual_candidate_post_follow_phase(
                _Device(),
                pkg="com.instagram.android",
                source_profile_username="source",
                visual_candidate_id="action-32a8394",
                follower_username="candidate",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
                det={},
                follow_context=ctx,
                stage_persist_callback=lambda stage, _payload: receipts.append(stage) or True,
                precompleted_like_result=like_out,
                follow_mutation_state="verified",
            )

        self.assertEqual(fixture["physical_order"], physical)
        self.assertEqual(0, post_like_attempt.call_count)
        self.assertTrue(out["return_ok"])
        self.assertTrue(out["mute"]["posts_verified"])
        self.assertTrue(out["mute"]["stories_verified"])
        self.assertNotIn("v1_fallback", receipts)
        self.assertFalse(out.get("global_run_stop", False))

    def test_unknown_follow_mutation_state_blocks_only_new_post_follow_like(self):
        ctx = FollowContext.from_follow_verified(
            follower_username="candidate",
            source_profile_username="source",
            visual_candidate_id="action-unknown",
            follow_state_after="following",
        )
        post_like_attempt = MagicMock()
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(nav.config, "FOLLOW_PRIVATE_ACCOUNTS", False, create=True)
            )
            stack.enter_context(
                patch.object(nav.config, "ENABLE_VISUAL_FOLLOW_MUTE_FLOW", True, create=True)
            )
            stack.enter_context(
                patch.object(nav.config, "ENABLE_REAL_VISUAL_MUTE_AFTER_FOLLOW", True, create=True)
            )
            stack.enter_context(
                patch.object(nav, "_mute_engine_v2_build_lightweight_profile_det", return_value={"action_bar_title": "candidate"})
            )
            stack.enter_context(
                patch.object(nav, "run_mute_engine_v2", return_value={
                    "ok": True,
                    "outcome": "success",
                    "posts_verified": True,
                    "stories_verified": True,
                    "timings_ms": {"mute_total_ms": 1.0},
                })
            )
            stack.enter_context(
                patch.object(nav, "_post_mute_state_checkpoint", return_value={
                    "navigation_state": "CANDIDATE_PROFILE",
                    "candidate_context": {"username": "candidate"},
                })
            )
            stack.enter_context(
                patch.object(nav, "_validate_post_mute_sheet_closed_proof", return_value=(False, {}, 0.0, "unit"))
            )
            stack.enter_context(
                patch.object(nav, "run_post_follow_post_likes_phase", post_like_attempt)
            )
            stack.enter_context(
                patch.object(nav, "post_follow_controlled_return_to_followers_list", return_value=(True, "unit", None))
            )
            stack.enter_context(patch("follow_60s_canary.enabled", return_value=True))
            out = nav.run_visual_candidate_post_follow_phase(
                _Device(),
                pkg="com.instagram.android",
                source_profile_username="source",
                visual_candidate_id="action-unknown",
                follower_username="candidate",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
                det={},
                follow_context=ctx,
                follow_mutation_state="future_unrecognized_state",
            )

        post_like_attempt.assert_not_called()
        self.assertEqual(
            "point_c_follow_mutation_state_unknown",
            out["likes"]["skipped_reason"],
        )
        self.assertEqual("UNKNOWN", out["likes"]["point_c_follow_mutation_phase"])
        self.assertTrue(out["return_ok"])


if __name__ == "__main__":
    unittest.main()
