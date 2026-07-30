from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import target_availability_runtime as runtime
from target_availability_runtime import (
    observation_from_rotation_summary,
    observe_rotation_target_loaded,
    observe_rotation_target_summary,
)
from target_availability_writer import TargetAvailabilityFeatureFlags
from target_availability_writer import SCOPE_MODE_EXPLICIT


BASE = dict(
    tenant_id="11111111-1111-4111-8111-111111111111",
    account_id="22222222-2222-4222-8222-222222222222",
    target_id="44444444-4444-4444-8444-444444444444",
    username="target.one",
    run_id="run-one",
    target_index=0,
)


class TargetAvailabilityRuntimeTests(unittest.TestCase):
    def tearDown(self):
        writer = runtime._WRITER
        runtime._WRITER = None
        if writer is not None and hasattr(writer, "close"):
            writer.close()

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

    def test_flags_off_do_not_import_runtime_or_create_thread_in_orchestrator(self):
        root = Path(__file__).resolve().parents[1]
        code = r'''
import os
import sys
import threading
for key in tuple(os.environ):
    if key.startswith("TARGET_AVAILABILITY_"):
        os.environ.pop(key, None)
import account_session_orchestrator as module
assert "target_availability_runtime" not in sys.modules
assert "target_availability_writer" not in sys.modules
before = tuple(thread.name for thread in threading.enumerate())
assert module._observe_target_availability(
    "loaded",
    tenant_id="11111111-1111-4111-8111-111111111111",
    account_id="22222222-2222-4222-8222-222222222222",
    target_id="44444444-4444-4444-8444-444444444444",
    username="synthetic.target",
    run_id="synthetic-run",
    target_index=0,
) is True
assert "target_availability_runtime" not in sys.modules
assert "target_availability_writer" not in sys.modules
assert before == tuple(thread.name for thread in threading.enumerate())
'''
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(root)
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_writer_off_and_non_allowlisted_accounts_never_create_transport(self):
        capture_only = TargetAvailabilityFeatureFlags(
            target_availability_observation_capture_enabled=True,
            scope_mode=SCOPE_MODE_EXPLICIT,
            account_allowlist=frozenset({BASE["account_id"]}),
        )
        with patch.object(runtime.BackendPipelineTransport, "from_environment", side_effect=AssertionError("transport created")):
            self.assertTrue(observe_rotation_target_loaded(**BASE, flags=capture_only))
        self.assertIsNone(runtime._WRITER)

        writer_on = TargetAvailabilityFeatureFlags(
            target_availability_observation_capture_enabled=True,
            target_availability_writer_enabled=True,
            scope_mode=SCOPE_MODE_EXPLICIT,
            account_allowlist=frozenset({BASE["account_id"]}),
        )
        other = {**BASE, "account_id": "55555555-5555-4555-8555-555555555555"}
        with patch.object(runtime.BackendPipelineTransport, "from_environment", side_effect=AssertionError("transport created")):
            self.assertTrue(observe_rotation_target_loaded(**other, flags=writer_on))
        self.assertIsNone(runtime._WRITER)

    def test_transport_unavailable_never_escapes_to_rotation(self):
        flags = TargetAvailabilityFeatureFlags(
            target_availability_observation_capture_enabled=True,
            target_availability_writer_enabled=True,
            target_availability_shadow_enabled=True,
            target_availability_identity_producer_enabled=True,
            target_availability_assessment_producer_enabled=True,
            target_availability_current_projector_enabled=True,
            scope_mode=SCOPE_MODE_EXPLICIT,
            account_allowlist=frozenset({BASE["account_id"]}),
        )
        with patch.object(runtime.BackendPipelineTransport, "from_environment", side_effect=RuntimeError("missing")):
            self.assertTrue(observe_rotation_target_loaded(**BASE, flags=flags))
        self.assertIsNone(runtime._WRITER)

    def test_synthetic_allowlisted_capture_uses_injected_writer_only(self):
        class FakeWriter:
            def __init__(self):
                self.items = []

            def enqueue(self, item):
                self.items.append(item)
                return True

        fake = FakeWriter()
        runtime._WRITER = fake
        flags = TargetAvailabilityFeatureFlags(
            target_availability_observation_capture_enabled=True,
            target_availability_writer_enabled=True,
            target_availability_shadow_enabled=True,
            target_availability_identity_producer_enabled=True,
            target_availability_assessment_producer_enabled=True,
            target_availability_current_projector_enabled=True,
            scope_mode=SCOPE_MODE_EXPLICIT,
            account_allowlist=frozenset({BASE["account_id"]}),
        )
        self.assertTrue(observe_rotation_target_loaded(**BASE, flags=flags))
        self.assertTrue(observe_rotation_target_summary(**BASE, summary={"profile_found": True}, flags=flags))
        self.assertEqual(len(fake.items), 2)
        self.assertEqual({item.account_id for item in fake.items}, {BASE["account_id"]})


if __name__ == "__main__":
    unittest.main()
