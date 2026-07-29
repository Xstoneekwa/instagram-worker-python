from __future__ import annotations

import gc
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
import weakref
from unittest.mock import patch

import account_session_orchestrator as orchestrator
import target_availability_runtime as runtime
from target_availability_memory_probe import (
    MAX_SNAPSHOT_BYTES,
    TargetAvailabilityMemoryProbe,
    _main as memory_probe_main,
)
from target_availability_writer import TargetAvailabilityFeatureFlags


PILOT_ID = "22222222-2222-4222-8222-222222222222"
OTHER_ID = "55555555-5555-4555-8555-555555555555"
BASE = {
    "tenant_id": "11111111-1111-4111-8111-111111111111",
    "account_id": PILOT_ID,
    "target_id": "44444444-4444-4444-8444-444444444444",
    "username": "target.one",
    "run_id": "run-secret-value",
    "target_index": 0,
}


class TargetAvailabilityMemoryProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.status = Path(self.temp.name) / "status.json"
        self.kill_switch = Path(self.temp.name) / "kill-switch"
        self.environment = {
            "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
            "TARGET_AVAILABILITY_WRITER_ENABLED": "false",
            "TARGET_AVAILABILITY_SHADOW_ENABLED": "false",
            "TARGET_AVAILABILITY_POLICY_SHADOW_ENABLED": "false",
            "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": PILOT_ID,
            "TARGET_AVAILABILITY_MEMORY_PROBE_ENABLED": "true",
            "TARGET_AVAILABILITY_MEMORY_PROBE_STATUS_FILE": str(self.status),
            "TARGET_AVAILABILITY_KILL_SWITCH_FILE": str(self.kill_switch),
        }

    def tearDown(self):
        probe = runtime._MEMORY_PROBE
        runtime._MEMORY_PROBE = None
        if probe is not None:
            probe.cleanup()
        writer = runtime._WRITER
        runtime._WRITER = None
        if writer is not None and hasattr(writer, "close"):
            writer.close()
        self.temp.cleanup()

    def _payload(self):
        return json.loads(self.status.read_text(encoding="utf-8"))

    def _capture_flags(self, account_id=PILOT_ID):
        return TargetAvailabilityFeatureFlags(
            target_availability_observation_capture_enabled=True,
            account_allowlist=frozenset({account_id}),
        )

    def test_flags_off_or_missing_allowlist_never_create_probe_snapshot(self):
        with patch.dict(os.environ, self.environment, clear=False):
            self.assertTrue(runtime.observe_rotation_target_loaded(**BASE, flags=TargetAvailabilityFeatureFlags()))
            self.assertFalse(self.status.exists())
            no_allowlist = TargetAvailabilityFeatureFlags(
                target_availability_observation_capture_enabled=True,
            )
            self.assertTrue(runtime.observe_rotation_target_loaded(**BASE, flags=no_allowlist))
            self.assertFalse(self.status.exists())

    def test_probe_flag_off_or_invalid_environment_allowlist_never_writes_snapshot(self):
        probe_off = {**self.environment, "TARGET_AVAILABILITY_MEMORY_PROBE_ENABLED": "false"}
        with patch.dict(os.environ, probe_off, clear=False):
            self.assertTrue(runtime.observe_rotation_target_loaded(**BASE, flags=self._capture_flags()))
            self.assertFalse(self.status.exists())
        invalid_allowlist = {
            **self.environment,
            "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": "not-a-uuid",
        }
        with patch.dict(os.environ, invalid_allowlist, clear=False):
            self.assertTrue(orchestrator._observe_target_availability("loaded", **BASE))
            self.assertFalse(self.status.exists())

    def test_non_pilot_account_produces_zero_probe_events(self):
        wrong = {**BASE, "account_id": OTHER_ID}
        with patch.dict(os.environ, self.environment, clear=False):
            self.assertTrue(runtime.observe_rotation_target_loaded(**wrong, flags=self._capture_flags()))
        self.assertFalse(self.status.exists())
        self.assertIsNone(runtime._MEMORY_PROBE)

    def test_allowlisted_capture_counts_valid_serialized_observations(self):
        with patch.dict(os.environ, self.environment, clear=False):
            flags = self._capture_flags()
            self.assertTrue(runtime.observe_rotation_target_loaded(**BASE, flags=flags))
            self.assertTrue(
                runtime.observe_rotation_target_summary(
                    **BASE,
                    summary={"profile_found": True, "follow_stop_reason": "global_follow_cap_reached"},
                    flags=flags,
                )
            )
        payload = self._payload()
        current = payload["current_run"]
        self.assertEqual(current["capture_attempt_count"], 2)
        self.assertEqual(current["observation_created_count"], 2)
        self.assertEqual(current["observation_valid_count"], 2)
        self.assertEqual(current["observation_rejected_count"], 0)
        self.assertEqual(current["observation_error_count"], 0)
        self.assertEqual(current["payload_retained_count"], 0)
        self.assertEqual(current["last_account_id"], PILOT_ID)
        self.assertEqual(current["last_stage"], "target_summary_completed")
        self.assertGreater(current["hook_total_duration_ns"], 0)
        self.assertGreater(current["hook_max_duration_ns"], 0)
        self.assertEqual(len(current["last_run_id_hash"]), 24)

    def test_invalid_scope_is_rejected_and_exception_is_counted_fail_open(self):
        with patch.dict(os.environ, self.environment, clear=False):
            flags = self._capture_flags()
            invalid = {**BASE, "username": "not a valid instagram username"}
            self.assertTrue(runtime.observe_rotation_target_loaded(**invalid, flags=flags))
            with patch.object(runtime, "build_target_availability_observation", side_effect=RuntimeError("boom")):
                self.assertTrue(runtime.observe_rotation_target_loaded(**BASE, flags=flags))
        current = self._payload()["current_run"]
        self.assertEqual(current["capture_attempt_count"], 2)
        self.assertEqual(current["observation_rejected_count"], 1)
        self.assertEqual(current["observation_error_count"], 1)
        self.assertEqual(current["payload_retained_count"], 0)

    def test_observation_reference_is_released_after_counting(self):
        references = []
        original = runtime.build_target_availability_observation

        def observed_builder(**kwargs):
            item = original(**kwargs)
            references.append(weakref.ref(item))
            return item

        with patch.dict(os.environ, self.environment, clear=False), patch.object(
            runtime, "build_target_availability_observation", side_effect=observed_builder
        ):
            self.assertTrue(runtime.observe_rotation_target_loaded(**BASE, flags=self._capture_flags()))
        gc.collect()
        self.assertIsNone(references[0]())
        self.assertEqual(self._payload()["current_run"]["payload_retained_count"], 0)

    def test_many_targets_keep_fixed_cardinality_and_bounded_snapshot(self):
        with patch.dict(os.environ, self.environment, clear=False):
            flags = self._capture_flags()
            for target_index in range(500):
                kwargs = {**BASE, "target_index": target_index}
                self.assertTrue(runtime.observe_rotation_target_loaded(**kwargs, flags=flags))
        payload = self._payload()
        self.assertEqual(payload["current_run"]["capture_attempt_count"], 500)
        self.assertEqual(payload["current_run"]["payload_retained_count"], 0)
        self.assertEqual(payload["payload_retention_capacity"], 0)
        self.assertLessEqual(self.status.stat().st_size, MAX_SNAPSHOT_BYTES)
        self.assertLess(payload["aggregate_memory_bytes"], MAX_SNAPSHOT_BYTES)

    def test_new_run_replaces_current_and_keeps_only_one_previous_summary(self):
        with patch.dict(os.environ, self.environment, clear=False):
            flags = self._capture_flags()
            for run_id in ("run-one", "run-two", "run-three"):
                self.assertTrue(
                    runtime.observe_rotation_target_loaded(
                        **{**BASE, "run_id": run_id},
                        flags=flags,
                    )
                )
        payload = self._payload()
        self.assertEqual(payload["current_run"]["last_run_id_hash"], runtime._MEMORY_PROBE._current["last_run_id_hash"])
        self.assertEqual(payload["current_run"]["capture_attempt_count"], 1)
        self.assertEqual(payload["previous_run"]["capture_attempt_count"], 1)
        self.assertNotEqual(
            payload["current_run"]["last_run_id_hash"],
            payload["previous_run"]["last_run_id_hash"],
        )

    def test_snapshot_is_private_bounded_and_contains_no_payload_identity(self):
        secret_stable_id = "stable-platform-user-secret"
        with patch.dict(os.environ, self.environment, clear=False):
            self.assertTrue(
                runtime.observe_rotation_target_loaded(
                    **BASE,
                    stable_platform_user_id=secret_stable_id,
                    flags=self._capture_flags(),
                )
            )
        raw = self.status.read_text(encoding="utf-8")
        self.assertNotIn(BASE["username"], raw)
        self.assertNotIn(BASE["target_id"], raw)
        self.assertNotIn(BASE["run_id"], raw)
        self.assertNotIn(secret_stable_id, raw)
        self.assertNotIn("service_role", raw)
        self.assertEqual(stat.S_IMODE(self.status.stat().st_mode), 0o600)
        self.assertLessEqual(len(raw.encode("utf-8")), MAX_SNAPSHOT_BYTES)

    def test_writer_shadow_or_policy_shadow_disables_probe(self):
        with patch.dict(os.environ, self.environment, clear=False):
            for field in (
                "target_availability_writer_enabled",
                "target_availability_shadow_enabled",
                "target_availability_policy_shadow_enabled",
            ):
                flags = TargetAvailabilityFeatureFlags(
                    target_availability_observation_capture_enabled=True,
                    account_allowlist=frozenset({PILOT_ID}),
                    **{field: True},
                )
                self.assertTrue(runtime.observe_rotation_target_loaded(**BASE, flags=flags))
                self.assertFalse(self.status.exists())

    def test_writer_off_probe_starts_no_thread_transport_network_or_queue(self):
        before = tuple(thread.ident for thread in threading.enumerate())
        with patch.dict(os.environ, self.environment, clear=False), patch.object(
            runtime.SupabaseObservationTransport,
            "from_environment",
            side_effect=AssertionError("transport must stay dormant"),
        ):
            self.assertTrue(runtime.observe_rotation_target_loaded(**BASE, flags=self._capture_flags()))
        after = tuple(thread.ident for thread in threading.enumerate())
        self.assertEqual(before, after)
        self.assertIsNone(runtime._WRITER)
        self.assertFalse(hasattr(runtime._MEMORY_PROBE, "queue"))

    def test_kill_switch_changes_capture_on_next_hook_without_restart(self):
        with patch.dict(os.environ, self.environment, clear=False):
            self.assertTrue(orchestrator._observe_target_availability("loaded", **BASE))
            first_count = self._payload()["current_run"]["capture_attempt_count"]
            self.kill_switch.write_text("ON\n", encoding="utf-8")
            self.assertTrue(orchestrator._observe_target_availability("loaded", **BASE))
            self.assertEqual(self._payload()["current_run"]["capture_attempt_count"], first_count)
            self.kill_switch.unlink()
            self.assertTrue(orchestrator._observe_target_availability("loaded", **BASE))
            self.assertEqual(self._payload()["current_run"]["capture_attempt_count"], first_count + 1)

    def test_operator_cleanup_removes_snapshot_and_next_hook_resets_summary(self):
        with patch.dict(os.environ, self.environment, clear=False):
            flags = self._capture_flags()
            self.assertTrue(runtime.observe_rotation_target_loaded(**BASE, flags=flags))
            self.assertTrue(runtime.observe_rotation_target_loaded(**BASE, flags=flags))
            self.assertEqual(self._payload()["current_run"]["capture_attempt_count"], 2)
            self.status.unlink()
            self.assertTrue(runtime.observe_rotation_target_loaded(**BASE, flags=flags))
        self.assertEqual(self._payload()["current_run"]["capture_attempt_count"], 1)
        runtime._MEMORY_PROBE.cleanup()
        self.assertFalse(self.status.exists())

    def test_operator_initialize_creates_private_zero_snapshot_without_observation(self):
        self.assertEqual(
            memory_probe_main(["initialize", "--status-file", str(self.status)]),
            0,
        )
        payload = self._payload()
        self.assertEqual(payload["current_run"], payload["previous_run"])
        self.assertEqual(payload["current_run"]["capture_attempt_count"], 0)
        self.assertEqual(payload["current_run"]["observation_created_count"], 0)
        self.assertEqual(payload["current_run"]["payload_retained_count"], 0)
        self.assertEqual(stat.S_IMODE(self.status.stat().st_mode), 0o600)
        self.assertLessEqual(self.status.stat().st_size, MAX_SNAPSHOT_BYTES)

    def test_probe_source_has_no_network_database_queue_thread_or_logging_import(self):
        source = (Path(__file__).resolve().parents[1] / "target_availability_memory_probe.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("import threading", "import urllib", "import supabase", "deque", "from logs", "import requests"):
            self.assertNotIn(forbidden, source.lower())


if __name__ == "__main__":
    unittest.main()
