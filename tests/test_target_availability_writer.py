from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import time
import unittest
from unittest.mock import patch

from target_availability_observation import (
    TargetAvailabilityObservationScope,
    build_target_availability_observation,
)
from target_availability_writer import (
    BackendPipelineTransport,
    FailOpenTargetAvailabilityWriter,
    SCOPE_MODE_ALL_ACTIVE,
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
    def test_global_scope_is_explicit_and_does_not_require_an_allowlist(self):
        flags = TargetAvailabilityFeatureFlags.from_mapping({
            "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
            "TARGET_AVAILABILITY_SCOPE_MODE": SCOPE_MODE_ALL_ACTIVE,
        })
        self.assertTrue(flags.capture_allowed(ACCOUNT_ONE))
        self.assertTrue(flags.capture_allowed(ACCOUNT_TWO))
        self.assertFalse(flags.capture_allowed("invalid"))
        invalid = TargetAvailabilityFeatureFlags.from_mapping({
            "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
            "TARGET_AVAILABILITY_SCOPE_MODE": "*",
        })
        self.assertFalse(invalid.capture_allowed(ACCOUNT_ONE))
        self.assertTrue(invalid.config_invalid)

    def test_control_file_is_re_read_and_invalid_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "control.json"
            values = {"TARGET_AVAILABILITY_CONTROL_FILE": str(control)}
            control.write_text("{}", encoding="utf-8")
            self.assertFalse(TargetAvailabilityFeatureFlags.from_mapping(values).capture_allowed(ACCOUNT_ONE))
            control.write_text(json.dumps({
                "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": True,
                "TARGET_AVAILABILITY_SCOPE_MODE": SCOPE_MODE_ALL_ACTIVE,
            }), encoding="utf-8")
            self.assertTrue(TargetAvailabilityFeatureFlags.from_mapping(values).capture_allowed(ACCOUNT_ONE))
            control.write_text("{broken", encoding="utf-8")
            flags = TargetAvailabilityFeatureFlags.from_mapping(values)
            self.assertTrue(flags.kill_switch)
            self.assertFalse(flags.capture_allowed(ACCOUNT_ONE))

    def test_pipeline_requires_all_producers_and_policy_shadow_off(self):
        base = {
            "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
            "TARGET_AVAILABILITY_WRITER_ENABLED": "true",
            "TARGET_AVAILABILITY_SHADOW_ENABLED": "true",
            "TARGET_AVAILABILITY_IDENTITY_PRODUCER_ENABLED": "true",
            "TARGET_AVAILABILITY_ASSESSMENT_PRODUCER_ENABLED": "true",
            "TARGET_AVAILABILITY_CURRENT_PROJECTOR_ENABLED": "true",
            "TARGET_AVAILABILITY_SCOPE_MODE": SCOPE_MODE_ALL_ACTIVE,
        }
        self.assertTrue(TargetAvailabilityFeatureFlags.from_mapping(base).pipeline_allowed(ACCOUNT_ONE))
        for key in (
            "TARGET_AVAILABILITY_IDENTITY_PRODUCER_ENABLED",
            "TARGET_AVAILABILITY_ASSESSMENT_PRODUCER_ENABLED",
            "TARGET_AVAILABILITY_CURRENT_PROJECTOR_ENABLED",
        ):
            self.assertFalse(TargetAvailabilityFeatureFlags.from_mapping({**base, key: "false"}).pipeline_allowed(ACCOUNT_ONE))
        self.assertFalse(TargetAvailabilityFeatureFlags.from_mapping({
            **base,
            "TARGET_AVAILABILITY_POLICY_SHADOW_ENABLED": "true",
        }).pipeline_allowed(ACCOUNT_ONE))

    def test_backend_pipeline_transport_is_private_bounded_and_redacted(self):
        transport = BackendPipelineTransport(
            api_base_url="https://backend.example",
            caller_token="private-token-value",
            worker_id="dispatcher-one",
            worker_release="release-one",
            timeout_seconds=99,
            max_retries=0,
        )

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"ok": True, "data": {"active": True, "autoKilled": False}}).encode("utf-8")

        with patch("target_availability_writer.request.urlopen", return_value=Response()) as opened:
            result = transport.send_batch([observation_to_database_row(observation())])
        self.assertTrue(result["active"])
        sent = opened.call_args.args[0]
        self.assertEqual(sent.headers["X-instagram-auto-restart-tick-token"], "private-token-value")
        self.assertEqual(transport._timeout, 3.0)

    def test_backend_pipeline_transport_uses_canonical_runtime_commit(self):
        environment = {
            "INSTAGRAM_DASHBOARD_API_BASE_URL": "https://backend.example",
            "INSTAGRAM_AUTO_RESTART_TICK_TOKEN": "private-token-value",
            "RUN_CONTROL_DISPATCHER_WORKER_ID": "dispatcher-one",
            "PHONEFARM_ACTIVE_COMMIT": "05c214979d8c7c56d73df6ed18371d7c47b13af8",
        }
        with patch.dict("os.environ", environment, clear=True):
            transport = BackendPipelineTransport.from_environment()
        self.assertEqual(
            transport._worker_release,
            "05c214979d8c7c56d73df6ed18371d7c47b13af8",
        )

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

    def test_auto_kill_purges_payloads_and_stops_future_transport_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            auto_kill = Path(directory) / "auto-kill.json"
            transport = RecordingTransport(failures=10)
            writer = FailOpenTargetAvailabilityWriter(
                transport,
                circuit_failure_threshold=1,
                auto_kill_file=str(auto_kill),
            )
            self.assertTrue(writer.enqueue(observation()))
            self.assertEqual(writer.flush_once(), 0)
            self.assertTrue(auto_kill.exists())
            self.assertEqual(writer.queue_size, 0)
            self.assertEqual(writer.metrics["auto_killed"], 1)
            self.assertTrue(writer._stop.is_set())
            self.assertEqual(writer.flush_once(), 0)
            self.assertEqual(transport.failures, 9)

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

    def test_repeated_transport_failure_writes_reversible_local_auto_kill(self):
        with tempfile.TemporaryDirectory() as directory:
            auto_kill = Path(directory) / "auto-kill.json"
            writer = FailOpenTargetAvailabilityWriter(
                RecordingTransport(failures=2),
                circuit_failure_threshold=2,
                auto_kill_file=str(auto_kill),
            )
            writer.enqueue(observation())
            self.assertEqual(writer.flush_once(), 0)
            self.assertEqual(writer.flush_once(), 0)
            payload = json.loads(auto_kill.read_text(encoding="utf-8"))
            self.assertEqual(payload["reason"], "repeated_backend_pipeline_failure")
            self.assertTrue(payload["human_reenable_required"])
            self.assertEqual(writer.metrics["auto_killed"], 1)

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
