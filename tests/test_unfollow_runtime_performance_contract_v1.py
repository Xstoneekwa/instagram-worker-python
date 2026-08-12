from __future__ import annotations

import unittest
from unittest.mock import patch

import account_session_reliability_schema as reliability
import account_session_resume_engine as resume_engine
import account_session_orchestrator as session_orchestrator
import instagram_navigation as navigation
import unfollow_profile_probe as probe


class _Element:
    def __init__(self, exists: bool, bounds=None) -> None:
        self._exists = exists
        self.info = {"bounds": dict(bounds or {})}

    def exists(self, timeout=0):
        del timeout
        return self._exists


class _SheetDevice:
    def __init__(self) -> None:
        self.clicks = []

    def __call__(self, **selector):
        label = str(selector.get("text") or "")
        return _Element(
            label == "Unfollow",
            {"left": 50, "top": 700, "right": 950, "bottom": 820},
        )

    def click(self, x, y):
        self.clicks.append((x, y))


class _SearchDevice:
    def __init__(self) -> None:
        self.back_count = 0

    def press(self, key):
        self.back_count += int(key == "back")


class UnfollowRuntimePerformanceContractV1Tests(unittest.TestCase):
    def test_certified_actions_sheet_reuses_pre_tap_proof(self) -> None:
        device = _SheetDevice()
        with patch.object(probe, "guard_instagram_action_rate_limit") as guard, patch.object(
            probe.time, "sleep"
        ), patch.object(probe, "log"):
            out = probe.tap_unfollow_in_following_sheet(
                device,
                target_username="candidate",
                sheet_context_signals={
                    "unfollow_visible": True,
                    "mute_visible": True,
                    "restrict_visible": True,
                },
            )
        self.assertTrue(out["ok"])
        self.assertEqual(len(device.clicks), 1)
        guard.assert_not_called()

    def test_uncertified_sheet_keeps_full_pre_tap_restriction_guard(self) -> None:
        device = _SheetDevice()
        with patch.object(probe, "guard_instagram_action_rate_limit") as guard, patch.object(
            probe.time, "sleep"
        ), patch.object(probe, "log"):
            out = probe.tap_unfollow_in_following_sheet(
                device,
                target_username="candidate",
            )
        self.assertTrue(out["ok"])
        guard.assert_called_once()

    def test_private_confirmation_reuses_snapshot_for_restriction_guard(self) -> None:
        source = __import__("inspect").getsource(
            probe.verify_unfollow_action_success_after_tap
        )
        self.assertIn("hierarchy_xml=hierarchy", source)
        self.assertNotIn(
            'preceding_action="private_unfollow_confirmation",',
            source,
        )

    def test_exact_direct_search_return_uses_transition_proof_without_strict_dump(self) -> None:
        device = _SearchDevice()
        with patch.object(navigation, "is_lightweight_search_screen", return_value=True), patch.object(
            navigation, "apply_search_surface_reuse_metrics"
        ) as strict_reuse, patch.object(navigation, "_mark_search_surface_ok"), patch.object(
            navigation, "log"
        ), patch.object(navigation.time, "sleep"):
            ok = navigation.return_to_search_from_profile(
                device,
                "com.instagram.android",
                trusted_global_search_return=True,
            )
        self.assertTrue(ok)
        self.assertEqual(device.back_count, 1)
        strict_reuse.assert_not_called()

    def test_no_pending_unfollow_is_satisfied_not_missing(self) -> None:
        summary = {
            "session_termination_class": "completed",
            "restart_eligibility": "not_needed",
            "follow_phase_status": "completed",
            "unfollow_phase_status": "skipped_cleanly",
            "mandatory_unfollow_executed": False,
            "mandatory_unfollow_satisfied": True,
            "unfollow_quota_target": 0,
            "unfollow_actions_verified": 0,
        }
        plan = resume_engine.build_account_session_resume_plan(summary)
        snapshot = reliability.build_admin_reliability_snapshot(summary, plan)
        self.assertFalse(plan["phases_to_run"]["unfollow"])
        self.assertEqual(plan["quota_remaining"]["unfollow"], 0)
        self.assertNotIn("mandatory_unfollow_missing", snapshot["badges"])
        self.assertIsNone(reliability.build_escalation_event(summary, plan))

    def test_loriele_no_pending_runtime_projection_is_terminal_and_zero(self) -> None:
        source = __import__("inspect").getsource(session_orchestrator.run_account_session)
        self.assertIn('== "no_pending_unfollow"', source)
        self.assertIn("mandatory_unfollow_satisfied", source)
        self.assertIn("unfollow_quota_target = 0", source)


if __name__ == "__main__":
    unittest.main()
