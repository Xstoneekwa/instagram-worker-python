from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import instagram_navigation as nav
from follow_state_contract import FollowContext


class _ActionBarSelector:
    def __init__(self, title: str) -> None:
        self._title = title

    def exists(self, timeout: float = 0.0) -> bool:
        _ = timeout
        return bool(self._title)

    def get_text(self) -> str:
        return self._title


class _Device:
    def __init__(self, action_bar_title: str = "candidate") -> None:
        self.action_bar_title = action_bar_title

    def app_current(self) -> dict:
        return {
            "package": "com.instagram.android",
            "activity": "com.instagram.mainactivity.InstagramMainActivity",
        }

    def __call__(self, **kwargs):
        if kwargs.get("resourceIdMatches"):
            return _ActionBarSelector(self.action_bar_title)
        return MagicMock(exists=MagicMock(return_value=False), info={})


def _likes_out() -> dict:
    out = nav._post_follow_post_likes_out_template()
    out.update({"skipped": True, "phase_outcome": "skipped", "skipped_reason": "unit"})
    return out


def _run_phase(
    *,
    device: _Device,
    follower_username: str = "candidate",
    follow_state_after: str = "following",
    follow_success_verified: bool = True,
    skipped_tap: bool = False,
    follow_private_accounts: bool = False,
    stage_persist_callback=None,
    mute_v2: dict | None = None,
    precompleted_like_result: dict | None = None,
    post_mute_return_proof: tuple[bool, dict, float, str] | None = None,
) -> tuple[dict, dict]:
    logs: list[tuple[str, str, dict]] = []
    ctx = FollowContext.from_follow_verified(
        follower_username=follower_username,
        source_profile_username="source",
        visual_candidate_id="vc-1",
        follow_state_after=follow_state_after,
    )
    mute_v2_result = dict(mute_v2 or {
        "ok": True,
        "outcome": "success",
        "posts_verified": True,
        "stories_verified": True,
        "timings_ms": {"mute_total_ms": 1.0},
    })
    with patch.object(nav.config, "FOLLOW_PRIVATE_ACCOUNTS", follow_private_accounts, create=True), patch.object(
        nav.config,
        "ENABLE_VISUAL_FOLLOW_MUTE_FLOW",
        True,
        create=True,
    ), patch.object(
        nav.config,
        "ENABLE_REAL_VISUAL_MUTE_AFTER_FOLLOW",
        True,
        create=True,
    ), patch.object(
        nav,
        "detect_followers_list_screen",
        return_value={"action_bar_title": follower_username, "is_followers_list": False},
    ) as mock_detect, patch.object(
        nav,
        "_post_follow_overlay_ui_hints",
        return_value={},
    ) as mock_overlay, patch.object(
        nav,
        "run_mute_engine_v2",
        return_value=mute_v2_result,
    ) as mock_mute, patch.object(
        nav,
        "_post_mute_state_checkpoint",
        return_value={
            "navigation_state": "CANDIDATE_PROFILE",
            "candidate_context": {"username": follower_username},
        },
    ), patch.object(
        nav,
        "_validate_post_mute_sheet_closed_proof",
        return_value=(
            post_mute_return_proof
            if post_mute_return_proof is not None
            else (False, {}, 0.0, "missing_proof")
        ),
    ), patch.object(
        nav,
        "run_post_follow_post_likes_phase",
        return_value=_likes_out(),
    ) as mock_likes, patch.object(
        nav,
        "post_follow_controlled_return_to_followers_list",
        return_value=(True, "unit", None),
    ) as mock_return, patch.object(
        __import__("follow_60s_canary"),
        "enabled",
        return_value=True,
    ), patch.object(
        nav,
        "log",
        side_effect=lambda level, event, **kw: logs.append((level, event, kw)),
    ):
        out = nav.run_visual_candidate_post_follow_phase(
            device,
            pkg="com.instagram.android",
            source_profile_username="source",
            visual_candidate_id="vc-1",
            follower_username=follower_username,
            follow_success_verified=follow_success_verified,
            follow_state_after=follow_state_after,
            skipped_tap=skipped_tap,
            det={},
            follow_context=ctx,
            stage_persist_callback=stage_persist_callback,
            precompleted_like_result=precompleted_like_result,
        )
    probes = {
        "logs": logs,
        "detect_calls": mock_detect.call_count,
        "overlay_calls": mock_overlay.call_count,
        "mute_calls": mock_mute.call_count,
        "mute_kwargs": mock_mute.call_args.kwargs if mock_mute.call_args else {},
        "like_calls": mock_likes.call_count,
        "return_kwargs": mock_return.call_args.kwargs if mock_return.call_args else {},
    }
    return out, probes


class PostFollowVerifiedContextFastPathTest(unittest.TestCase):
    def test_ordering_v2_reuses_fresh_post_mute_boundary_for_one_back_return(self) -> None:
        precompleted = nav._post_follow_post_likes_out_template()
        precompleted.update(
            {"ok": True, "phase_outcome": "success", "liked_count": 1, "skipped": False}
        )
        out, probes = _run_phase(
            device=_Device(action_bar_title="candidate"),
            precompleted_like_result=precompleted,
            post_mute_return_proof=(
                True,
                {
                    "sheet_closed": True,
                    "candidate_username": "candidate",
                    "immutable_verdict": True,
                },
                17.0,
                "",
            ),
        )

        self.assertTrue(out["return_ok"])
        self.assertEqual(probes["like_calls"], 0)
        self.assertTrue(probes["return_kwargs"]["immediate_candidate_back_proof"])
        events = [event for _level, event, _kw in probes["logs"]]
        self.assertIn("follow_60s_return_post_mute_candidate_proof_reused", events)

    def test_ordering_v2_missing_post_mute_boundary_keeps_safe_return_fallback(self) -> None:
        precompleted = nav._post_follow_post_likes_out_template()
        precompleted.update(
            {"ok": True, "phase_outcome": "success", "liked_count": 1, "skipped": False}
        )
        out, probes = _run_phase(
            device=_Device(action_bar_title="candidate"),
            precompleted_like_result=precompleted,
            post_mute_return_proof=(False, {}, 2500.0, "stale_proof"),
        )

        self.assertTrue(out["return_ok"])
        self.assertFalse(probes["return_kwargs"]["immediate_candidate_back_proof"])
        events = [event for _level, event, _kw in probes["logs"]]
        self.assertIn("follow_60s_return_candidate_handoff_rejected", events)

    def test_strong_following_context_skips_heavy_probes_and_starts_mute(self) -> None:
        out, probes = _run_phase(device=_Device(action_bar_title="candidate"))

        self.assertTrue(out["mute"]["mute_started"])
        self.assertEqual(probes["detect_calls"], 0)
        self.assertEqual(probes["overlay_calls"], 0)
        self.assertEqual(probes["mute_calls"], 1)
        det_hint = probes["mute_kwargs"]["det_hint"]
        self.assertEqual(det_hint["action_bar_title"], "candidate")
        self.assertFalse(det_hint["is_followers_list"])
        events = [event for _level, event, _kw in probes["logs"]]
        self.assertIn("post_follow_verified_context_reused_for_mute", events)
        self.assertIn("post_follow_mute_decision", events)

    def test_candidate_mismatch_falls_back_to_existing_surface_probes(self) -> None:
        out, probes = _run_phase(device=_Device(action_bar_title="other_candidate"))

        self.assertTrue(out["mute"]["mute_started"])
        self.assertGreaterEqual(probes["detect_calls"], 1)
        self.assertGreaterEqual(probes["overlay_calls"], 1)

    def test_requested_private_policy_uses_existing_skip_path(self) -> None:
        out, probes = _run_phase(
            device=_Device(action_bar_title="candidate"),
            follow_state_after="requested",
            follow_private_accounts=True,
        )

        self.assertEqual(out["mute"]["skipped_reason"], "private_follow_request_pending")
        self.assertGreaterEqual(probes["detect_calls"], 1)
        self.assertEqual(probes["mute_calls"], 0)

    def test_skipped_tap_falls_back_and_skips_mute(self) -> None:
        out, probes = _run_phase(
            device=_Device(action_bar_title="candidate"),
            skipped_tap=True,
        )

        self.assertEqual(out["mute"]["skipped_reason"], "already_following_skipped_tap")
        self.assertGreaterEqual(probes["detect_calls"], 1)
        self.assertEqual(probes["mute_calls"], 0)

    def test_failed_mute_receipt_blocks_like_but_keeps_safe_return(self) -> None:
        persisted: list[str] = []

        def persist(stage: str, _payload: dict) -> bool:
            persisted.append(stage)
            return stage != "mute_posts_verified"

        out, probes = _run_phase(
            device=_Device(action_bar_title="candidate"),
            stage_persist_callback=persist,
        )

        self.assertIn("mute_posts_verified", persisted)
        self.assertIn("mute_stories_verified", persisted)
        self.assertEqual(probes["like_calls"], 0)
        self.assertEqual(out["likes"]["skipped_reason"], "critical_stage_persist_failed")
        self.assertFalse(out["stage_persist_ok"])
        self.assertTrue(out["return_ok"])

    def test_partial_required_mute_blocks_like_and_keeps_safe_return(self) -> None:
        persisted: list[tuple[str, dict]] = []

        def persist(stage: str, payload: dict) -> bool:
            persisted.append((stage, dict(payload)))
            return True

        out, probes = _run_phase(
            device=_Device(action_bar_title="candidate"),
            stage_persist_callback=persist,
            mute_v2={
                "ok": True,
                "outcome": "partial_success",
                "partial": True,
                "posts_verified": True,
                "stories_verified": False,
                "timings_ms": {"mute_total_ms": 1.0},
            },
        )

        self.assertEqual(probes["like_calls"], 0)
        self.assertEqual(
            out["likes"]["skipped_reason"],
            "required_mute_verification_incomplete",
        )
        self.assertEqual(out["required_mute_missing_axes"], ["stories"])
        self.assertTrue(out["return_ok"])
        self.assertEqual(
            [stage for stage, _payload in persisted],
            ["mute_posts_verified", "return_ct_exact"],
        )
        return_payload = persisted[-1][1]
        self.assertFalse(return_payload["required_mute_verification_complete"])
        self.assertEqual(return_payload["required_mute_missing_axes"], ["stories"])


if __name__ == "__main__":
    unittest.main()
