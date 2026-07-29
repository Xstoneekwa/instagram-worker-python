from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest

from target_availability_observation import (
    TargetAvailabilityObservationScope,
    build_target_availability_observation,
)
from target_availability_writer import (
    FailOpenTargetAvailabilityWriter,
    SupabaseObservationTransport,
    TargetAvailabilityFeatureFlags,
    observation_to_database_row,
)


class RecordingTransport:
    def __init__(self, failures=0):
        self.failures = failures
        self.batches = []

    def send_batch(self, rows):
        if self.failures:
            self.failures -= 1
            raise TimeoutError("synthetic")
        self.batches.append(list(rows))


def observation(event="run:target:summary", account_id="account-one"):
    return build_target_availability_observation(
        scope=TargetAvailabilityObservationScope(
            tenant_id="tenant-one",
            account_id=account_id,
            target_id="target-one",
            normalized_username="target.one",
        ),
        event_key=event,
        observed_at=datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc),
        reason_codes=["target_profile_found"],
        lookup_result="found",
        profile_found=True,
        run_id="run-one",
    )


class TargetAvailabilityWriterTests(unittest.TestCase):
    def test_transport_rejects_non_service_role_credentials(self):
        with self.assertRaisesRegex(ValueError, "supabase_service_role_key_required"):
            SupabaseObservationTransport(url="https://example.supabase.co", service_role_key="anon-key")
        service_claim = "eyJyb2xlIjoic2VydmljZV9yb2xlIn0"
        transport = SupabaseObservationTransport(
            url="https://example.supabase.co",
            service_role_key="e30.%s.signature" % service_claim,
        )
        self.assertIn("ct_target_availability_observations", transport._endpoint)

    def test_four_flags_default_off_and_kill_switch_wins(self):
        flags = TargetAvailabilityFeatureFlags.from_mapping({})
        self.assertFalse(flags.capture_allowed("account-one"))
        self.assertFalse(flags.target_availability_writer_enabled)
        self.assertFalse(flags.target_availability_shadow_enabled)
        self.assertFalse(flags.target_availability_policy_shadow_enabled)
        killed = TargetAvailabilityFeatureFlags.from_mapping({
            "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
            "TARGET_AVAILABILITY_WRITER_ENABLED": "true",
            "TARGET_AVAILABILITY_KILL_SWITCH": "true",
        })
        self.assertFalse(killed.writer_allowed("account-one"))

    def test_account_allowlist_is_fail_closed(self):
        flags = TargetAvailabilityFeatureFlags.from_mapping({
            "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
            "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": "account-one",
        })
        self.assertTrue(flags.capture_allowed("account-one"))
        self.assertFalse(flags.capture_allowed("account-two"))

    def test_kill_switch_file_is_re_read_without_redeploy(self):
        with tempfile.TemporaryDirectory() as directory:
            switch = Path(directory) / "availability.disabled"
            values = {
                "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
                "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": "account-one",
                "TARGET_AVAILABILITY_KILL_SWITCH_FILE": str(switch),
            }
            self.assertTrue(TargetAvailabilityFeatureFlags.from_mapping(values).capture_allowed("account-one"))
            switch.touch()
            self.assertFalse(TargetAvailabilityFeatureFlags.from_mapping(values).capture_allowed("account-one"))

    def test_database_row_targets_only_observation_contract(self):
        item = observation()
        row = observation_to_database_row(item)
        self.assertEqual(row["source"], "worker")
        self.assertEqual(row["account_id"], "account-one")
        self.assertEqual(row["evidence_safe"]["contract_context"]["observation_stage"], "unknown")
        self.assertNotIn("status", row)
        self.assertNotIn("archive", row)
        self.assertNotIn("replacement", row)

    def test_duplicate_is_idempotent_and_queue_is_bounded(self):
        writer = FailOpenTargetAvailabilityWriter(RecordingTransport(), capacity=1)
        first = observation()
        self.assertTrue(writer.enqueue(first))
        self.assertTrue(writer.enqueue(first))
        self.assertFalse(writer.enqueue(observation(event="run:target:second")))
        self.assertEqual(writer.queue_size, 1)
        self.assertEqual(writer.metrics["duplicates"], 1)
        self.assertEqual(writer.metrics["dropped"], 1)

    def test_flush_is_batchable_and_successful(self):
        transport = RecordingTransport()
        writer = FailOpenTargetAvailabilityWriter(transport, capacity=10, batch_size=2)
        for index in range(3):
            writer.enqueue(observation(event="run:target:%s" % index))
        self.assertEqual(writer.flush_once(), 2)
        self.assertEqual(writer.flush_once(), 1)
        self.assertEqual(writer.queue_size, 0)
        self.assertEqual(writer.metrics["flushed"], 3)

    def test_timeout_is_fail_open_and_circuit_breaker_preserves_queue(self):
        clock = [100.0]
        transport = RecordingTransport(failures=4)
        writer = FailOpenTargetAvailabilityWriter(
            transport,
            capacity=4,
            circuit_failure_threshold=2,
            circuit_cooldown_seconds=5,
            clock=lambda: clock[0],
        )
        writer.enqueue(observation())
        self.assertEqual(writer.flush_once(), 0)
        self.assertEqual(writer.flush_once(), 0)
        self.assertEqual(writer.flush_once(), 0)
        self.assertEqual(writer.queue_size, 1)
        self.assertEqual(writer.metrics["circuit_open"], 1)
        clock[0] += 6
        self.assertEqual(writer.flush_once(), 0)
        self.assertEqual(writer.queue_size, 1)

    def test_daemon_flush_never_blocks_caller(self):
        transport = RecordingTransport()
        writer = FailOpenTargetAvailabilityWriter(transport)
        writer.start()
        started = time.monotonic()
        self.assertTrue(writer.enqueue(observation()))
        self.assertLess(time.monotonic() - started, 0.1)
        deadline = time.monotonic() + 1
        while not transport.batches and time.monotonic() < deadline:
            time.sleep(0.01)
        writer.close()
        self.assertTrue(transport.batches)


if __name__ == "__main__":
    unittest.main()
