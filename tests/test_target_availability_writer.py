from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib import error

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

TENANT_ID = "11111111-1111-4111-8111-111111111111"
ACCOUNT_ONE = "22222222-2222-4222-8222-222222222222"
ACCOUNT_TWO = "33333333-3333-4333-8333-333333333333"
TARGET_ID = "44444444-4444-4444-8444-444444444444"


class RecordingTransport:
    def __init__(self, failures=0):
        self.failures = failures
        self.batches = []

    def send_batch(self, rows):
        if self.failures:
            self.failures -= 1
            raise TimeoutError("synthetic")
        self.batches.append(list(rows))


def observation(event="run:target:summary", account_id=ACCOUNT_ONE):
    return build_target_availability_observation(
        scope=TargetAvailabilityObservationScope(
            tenant_id=TENANT_ID,
            account_id=account_id,
            target_id=TARGET_ID,
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

    def test_transport_failures_are_bounded_and_redacted(self):
        service_claim = "eyJyb2xlIjoic2VydmljZV9yb2xlIn0"
        transport = SupabaseObservationTransport(
            url="https://example.supabase.co",
            service_role_key="e30.%s.signature" % service_claim,
            timeout_seconds=99,
            max_retries=0,
        )
        self.assertEqual(transport._timeout, 3.0)
        for failure in (
            TimeoutError("synthetic timeout"),
            error.URLError("synthetic dns failure"),
            error.HTTPError(transport._endpoint, 401, "unauthorized", {}, None),
            error.HTTPError(transport._endpoint, 403, "forbidden", {}, None),
            error.HTTPError(transport._endpoint, 429, "limited", {}, None),
            error.HTTPError(transport._endpoint, 500, "server", {}, None),
        ):
            with self.subTest(failure=repr(failure)):
                with patch("target_availability_writer.request.urlopen", side_effect=failure):
                    with self.assertRaisesRegex(RuntimeError, "target_availability_writer_transport_failed") as raised:
                        transport.send_batch([observation_to_database_row(observation())])
                    self.assertNotIn(transport._key, str(raised.exception))

    def test_four_flags_default_off_and_kill_switch_wins(self):
        flags = TargetAvailabilityFeatureFlags.from_mapping({})
        self.assertFalse(flags.capture_allowed(ACCOUNT_ONE))
        self.assertFalse(flags.target_availability_writer_enabled)
        self.assertFalse(flags.target_availability_shadow_enabled)
        self.assertFalse(flags.target_availability_policy_shadow_enabled)
        killed = TargetAvailabilityFeatureFlags.from_mapping({
            "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
            "TARGET_AVAILABILITY_WRITER_ENABLED": "true",
            "TARGET_AVAILABILITY_KILL_SWITCH": "true",
            "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": ACCOUNT_ONE,
        })
        self.assertFalse(killed.writer_allowed(ACCOUNT_ONE))

    def test_account_allowlist_is_mandatory_and_uuid_only(self):
        base = {"TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true"}
        for raw in (None, "", "*", "account-one", "%s;other" % ACCOUNT_ONE, "[broken"):
            values = dict(base)
            if raw is not None:
                values["TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST"] = raw
            with self.subTest(raw=raw):
                self.assertFalse(TargetAvailabilityFeatureFlags.from_mapping(values).capture_allowed(ACCOUNT_ONE))

        flags = TargetAvailabilityFeatureFlags.from_mapping({
            **base,
            "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": "%s,%s,%s" % (ACCOUNT_ONE.upper(), ACCOUNT_TWO, ACCOUNT_ONE),
        })
        self.assertEqual(flags.account_allowlist, frozenset({ACCOUNT_ONE, ACCOUNT_TWO}))
        self.assertTrue(flags.capture_allowed(ACCOUNT_ONE))
        self.assertTrue(flags.capture_allowed(ACCOUNT_TWO.upper()))
        self.assertFalse(flags.capture_allowed("55555555-5555-4555-8555-555555555555"))

    def test_json_and_sequence_allowlists_are_strict(self):
        base = {"TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": True}
        for raw in ([ACCOUNT_ONE], '["%s"]' % ACCOUNT_ONE):
            with self.subTest(raw=raw):
                flags = TargetAvailabilityFeatureFlags.from_mapping({
                    **base,
                    "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": raw,
                })
                self.assertTrue(flags.capture_allowed(ACCOUNT_ONE))
        for raw in ({"account": ACCOUNT_ONE}, '["%s", "invalid"]' % ACCOUNT_ONE, [ACCOUNT_ONE, "invalid"]):
            with self.subTest(raw=raw):
                flags = TargetAvailabilityFeatureFlags.from_mapping({
                    **base,
                    "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": raw,
                })
                self.assertFalse(flags.capture_allowed(ACCOUNT_ONE))

    def test_only_exact_true_enables_each_flag(self):
        for raw in (False, None, "", "false", "1", "yes", "on", 1):
            flags = TargetAvailabilityFeatureFlags.from_mapping({
                "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": raw,
                "TARGET_AVAILABILITY_WRITER_ENABLED": raw,
                "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": ACCOUNT_ONE,
            })
            with self.subTest(raw=raw):
                self.assertFalse(flags.capture_allowed(ACCOUNT_ONE))
                self.assertFalse(flags.writer_allowed(ACCOUNT_ONE))
        flags = TargetAvailabilityFeatureFlags.from_mapping({
            "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": " TRUE ",
            "TARGET_AVAILABILITY_WRITER_ENABLED": True,
            "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": ACCOUNT_ONE,
        })
        self.assertTrue(flags.writer_allowed(ACCOUNT_ONE))

    def test_writer_flag_cannot_bypass_capture_flag_or_allowlist(self):
        flags = TargetAvailabilityFeatureFlags.from_mapping({
            "TARGET_AVAILABILITY_WRITER_ENABLED": "true",
            "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": ACCOUNT_ONE,
        })
        self.assertFalse(flags.writer_allowed(ACCOUNT_ONE))
        flags = TargetAvailabilityFeatureFlags.from_mapping({
            "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
            "TARGET_AVAILABILITY_WRITER_ENABLED": "true",
        })
        self.assertFalse(flags.writer_allowed(ACCOUNT_ONE))

    def test_kill_switch_file_is_re_read_without_redeploy(self):
        with tempfile.TemporaryDirectory() as directory:
            switch = Path(directory) / "availability.disabled"
            values = {
                "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
                "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": ACCOUNT_ONE,
                "TARGET_AVAILABILITY_KILL_SWITCH_FILE": str(switch),
            }
            self.assertTrue(TargetAvailabilityFeatureFlags.from_mapping(values).capture_allowed(ACCOUNT_ONE))
            switch.touch()
            self.assertFalse(TargetAvailabilityFeatureFlags.from_mapping(values).capture_allowed(ACCOUNT_ONE))

    def test_invalid_or_unreadable_kill_switch_path_fails_closed(self):
        values = {
            "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
            "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": ACCOUNT_ONE,
            "TARGET_AVAILABILITY_KILL_SWITCH_FILE": "/synthetic/operator-switch",
        }
        with patch("target_availability_writer.os.stat", side_effect=PermissionError("denied")):
            self.assertFalse(TargetAvailabilityFeatureFlags.from_mapping(values).capture_allowed(ACCOUNT_ONE))
        with tempfile.TemporaryDirectory() as directory:
            values["TARGET_AVAILABILITY_KILL_SWITCH_FILE"] = directory
            self.assertFalse(TargetAvailabilityFeatureFlags.from_mapping(values).capture_allowed(ACCOUNT_ONE))

    def test_database_row_targets_only_observation_contract(self):
        item = observation()
        row = observation_to_database_row(item)
        self.assertEqual(row["source"], "worker")
        self.assertEqual(row["account_id"], ACCOUNT_ONE)
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
        transport = RecordingTransport(failures=2)
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
        self.assertEqual(writer.flush_once(), 1)
        self.assertEqual(writer.queue_size, 0)

    def test_serialization_exception_is_dropped_without_escaping(self):
        class InvalidObservation:
            def to_dict(self):
                raise ValueError("synthetic serialization failure")

        writer = FailOpenTargetAvailabilityWriter(RecordingTransport())
        self.assertFalse(writer.enqueue(InvalidObservation()))
        self.assertEqual(writer.queue_size, 0)
        self.assertEqual(writer.metrics["dropped"], 1)

    def test_background_flush_exception_does_not_kill_the_process(self):
        writer = FailOpenTargetAvailabilityWriter(RecordingTransport(), clock=lambda: (_ for _ in ()).throw(RuntimeError("clock")))
        writer.start()
        self.assertTrue(writer.enqueue(observation()))
        deadline = time.monotonic() + 1
        while writer.metrics["failures"] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(writer.thread_alive)
        self.assertEqual(writer.queue_size, 1)
        writer.close()
        self.assertFalse(writer.thread_alive)

    def test_thread_start_failure_is_fail_open(self):
        writer = FailOpenTargetAvailabilityWriter(RecordingTransport())
        with patch("target_availability_writer.threading.Thread.start", side_effect=RuntimeError("unavailable")):
            writer.start()
        self.assertFalse(writer.thread_alive)
        self.assertEqual(writer.metrics["failures"], 1)

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
        self.assertFalse(writer.thread_alive)


if __name__ == "__main__":
    unittest.main()
