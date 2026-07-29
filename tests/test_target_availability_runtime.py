from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest

from target_availability_runtime import (
    observation_from_rotation_summary,
    observe_rotation_target_loaded,
    observe_rotation_target_summary,
)
from target_availability_writer import TargetAvailabilityFeatureFlags


BASE = dict(
    tenant_id="tenant-one",
    account_id="account-one",
    target_id="target-one",
    username="target.one",
    run_id="run-one",
    target_index=0,
)


class TargetAvailabilityRuntimeTests(unittest.TestCase):
    def test_flags_off_hooks_are_noop_and_allow_next_target(self):
        flags = TargetAvailabilityFeatureFlags()
        self.assertTrue(observe_rotation_target_loaded(**BASE, flags=flags))
        self.assertTrue(observe_rotation_target_summary(**BASE, summary={"exit_code": 1}, flags=flags))

    def test_existing_factual_signals_map_without_new_navigation(self):
        item = observation_from_rotation_summary(
            **BASE,
            observed_at=datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc),
            summary={
                "exit_code": 0,
                "profile_found": True,
                "observed_username": "target.one",
                "verified_badge": True,
                "followers_surface": "restricted",
                "accessible_profiles_count": 47,
                "repeated_first_profiles_detected": True,
                "pagination_stalled": True,
                "profile_ambiguous": True,
                "followers_surface_entered": True,
                "recovery_attempted": True,
                "recovery_outcome": "succeeded",
                "session_ambiguous": True,
                "source_profile_mismatch": True,
                "identity_conflict": True,
                "network_state": "healthy",
                "session_state": "healthy",
                "ui_evidence_quality": "high",
            },
        )
        self.assertIsNotNone(item)
        self.assertTrue(item.profile_found)
        self.assertTrue(item.verified_badge)
        self.assertEqual(item.accessible_profiles_count, 47)
        self.assertTrue(item.username_lookup_completed)
        self.assertTrue(item.pagination_stalled)
        self.assertTrue(item.recovery_attempted)
        self.assertEqual(item.observation_stage, "target_summary_completed")
        self.assertIn("target_followers_surface_restricted", item.reason_codes)

    def test_ambiguity_never_becomes_terminal_or_identity_proof(self):
        item = observation_from_rotation_summary(
            **BASE,
            observed_at=datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc),
            summary={"exit_code": 1, "follow_stop_reason": "navigation timeout", "network_state": "degraded"},
        )
        self.assertIsNone(item.profile_found)
        self.assertFalse(item.terminal_end_detected)
        self.assertIsNone(item.observed_stable_platform_user_id)
        self.assertTrue(item.navigation_timeout)

    def test_runtime_adapter_does_not_import_navigation_or_device(self):
        source = (Path(__file__).resolve().parents[1] / "target_availability_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("uiautomator2", source)
        self.assertNotIn("instagram_navigation", source)
        self.assertNotIn("adb", source.lower())

    def test_account_orchestrator_uses_lazy_import_when_capture_is_off(self):
        source = (Path(__file__).resolve().parents[1] / "account_session_orchestrator.py").read_text(encoding="utf-8")
        prefix = source.split("def _observe_target_availability", 1)[0]
        self.assertNotIn("from target_availability_runtime import", prefix)
        self.assertIn("if not _target_availability_capture_requested", source)


if __name__ == "__main__":
    unittest.main()
