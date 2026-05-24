from __future__ import annotations

import unittest
from unittest.mock import patch

import runtime_heartbeat


class RuntimeHeartbeatTest(unittest.TestCase):
    def setUp(self) -> None:
        runtime_heartbeat._LAST_WORKER_HEARTBEAT.clear()
        runtime_heartbeat._LAST_DEVICE_HEARTBEAT.clear()

    def test_disabled_returns_disabled(self) -> None:
        with patch.object(runtime_heartbeat.config, "RUNTIME_HEARTBEATS_ENABLED", False, create=True):
            out = runtime_heartbeat.heartbeat_worker(status="running")
        self.assertEqual(out["reason"], "disabled")

    def test_worker_id_default_hostname_pid_works(self) -> None:
        with (
            patch.object(runtime_heartbeat.config, "RUNTIME_HEARTBEATS_ENABLED", True, create=True),
            patch.object(runtime_heartbeat.socket, "gethostname", return_value="host-a"),
            patch.object(runtime_heartbeat.os, "getpid", return_value=1234),
            patch.object(
                runtime_heartbeat.supabase_client,
                "upsert_worker_heartbeat",
                return_value={"worker_id": "host-a:1234"},
            ) as upsert_worker,
            patch.dict(runtime_heartbeat.os.environ, {}, clear=True),
        ):
            out = runtime_heartbeat.heartbeat_worker(status="running")
        self.assertTrue(out["published"])
        self.assertEqual(out["worker_id"], "host-a:1234")
        self.assertEqual(upsert_worker.call_args.args[0]["worker_id"], "host-a:1234")

    def test_worker_heartbeat_success_calls_fake_upsert(self) -> None:
        with (
            patch.object(runtime_heartbeat.config, "RUNTIME_HEARTBEATS_ENABLED", True, create=True),
            patch.object(
                runtime_heartbeat.supabase_client,
                "upsert_worker_heartbeat",
                return_value={"worker_id": "worker-1"},
            ) as upsert_worker,
        ):
            out = runtime_heartbeat.heartbeat_worker(worker_id="worker-1", status="idle")
        self.assertTrue(out["published"])
        self.assertEqual(upsert_worker.call_args.args[0]["status"], "idle")
        self.assertIn("last_seen_at", upsert_worker.call_args.args[0])

    def test_worker_heartbeat_payload_includes_last_seen_at(self) -> None:
        fixed_ts = "2026-05-25T12:00:00+00:00"
        with (
            patch.object(runtime_heartbeat.config, "RUNTIME_HEARTBEATS_ENABLED", True, create=True),
            patch.object(runtime_heartbeat, "_utc_now_iso", return_value=fixed_ts),
            patch.object(
                runtime_heartbeat.supabase_client,
                "upsert_worker_heartbeat",
                return_value={"worker_id": "worker-1"},
            ) as upsert_worker,
        ):
            runtime_heartbeat.heartbeat_worker(worker_id="worker-1", status="running")
        self.assertEqual(upsert_worker.call_args.args[0]["last_seen_at"], fixed_ts)

    def test_device_heartbeat_missing_device_id_skips(self) -> None:
        with patch.object(runtime_heartbeat.config, "RUNTIME_HEARTBEATS_ENABLED", True, create=True):
            out = runtime_heartbeat.heartbeat_device(None, status="online")
        self.assertEqual(out["reason"], "missing_device_id")

    def test_device_heartbeat_success_calls_fake_upsert(self) -> None:
        with (
            patch.object(runtime_heartbeat.config, "RUNTIME_HEARTBEATS_ENABLED", True, create=True),
            patch.object(
                runtime_heartbeat.supabase_client,
                "upsert_device_heartbeat",
                return_value={"device_id": "00000000-0000-4000-8000-00000022c001"},
            ) as upsert_device,
        ):
            out = runtime_heartbeat.heartbeat_device(
                "00000000-0000-4000-8000-00000022c001",
                status="busy",
                adb_serial="emulator-5554",
            )
        self.assertTrue(out["published"])
        self.assertEqual(upsert_device.call_args.args[0]["status"], "busy")
        self.assertEqual(upsert_device.call_args.args[0]["adb_serial"], "emulator-5554")
        self.assertIn("last_seen_at", upsert_device.call_args.args[0])

    def test_device_heartbeat_payload_includes_last_seen_at(self) -> None:
        fixed_ts = "2026-05-25T12:00:01+00:00"
        with (
            patch.object(runtime_heartbeat.config, "RUNTIME_HEARTBEATS_ENABLED", True, create=True),
            patch.object(runtime_heartbeat, "_utc_now_iso", return_value=fixed_ts),
            patch.object(
                runtime_heartbeat.supabase_client,
                "upsert_device_heartbeat",
                return_value={"device_id": "00000000-0000-4000-8000-00000022c001"},
            ) as upsert_device,
        ):
            runtime_heartbeat.heartbeat_device(
                "00000000-0000-4000-8000-00000022c001",
                status="busy",
            )
        self.assertEqual(upsert_device.call_args.args[0]["last_seen_at"], fixed_ts)

    def test_force_heartbeat_updates_last_seen_at_each_call(self) -> None:
        timestamps = ["2026-05-25T12:00:00+00:00", "2026-05-25T12:00:30+00:00"]
        with (
            patch.object(runtime_heartbeat.config, "RUNTIME_HEARTBEATS_ENABLED", True, create=True),
            patch.object(runtime_heartbeat, "_utc_now_iso", side_effect=timestamps),
            patch.object(
                runtime_heartbeat.supabase_client,
                "upsert_worker_heartbeat",
                return_value={"worker_id": "worker-1"},
            ) as upsert_worker,
        ):
            runtime_heartbeat.heartbeat_worker(worker_id="worker-1", status="running", force=True)
            runtime_heartbeat.heartbeat_worker(worker_id="worker-1", status="idle", force=True)
        payloads = [call.args[0] for call in upsert_worker.call_args_list]
        self.assertEqual(payloads[0]["last_seen_at"], timestamps[0])
        self.assertEqual(payloads[1]["last_seen_at"], timestamps[1])

    def test_heartbeat_failure_fail_open_no_raise(self) -> None:
        with (
            patch.object(runtime_heartbeat.config, "RUNTIME_HEARTBEATS_ENABLED", True, create=True),
            patch.object(
                runtime_heartbeat.supabase_client,
                "upsert_worker_heartbeat",
                side_effect=RuntimeError("network down"),
            ),
        ):
            out = runtime_heartbeat.heartbeat_worker(worker_id="worker-1", status="running")
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "upsert_failed")

    def test_throttle_skips_repeated_heartbeat(self) -> None:
        with (
            patch.object(runtime_heartbeat.config, "RUNTIME_HEARTBEATS_ENABLED", True, create=True),
            patch.object(runtime_heartbeat.config, "RUNTIME_HEARTBEAT_INTERVAL_SECONDS", 30, create=True),
            patch.object(
                runtime_heartbeat.supabase_client,
                "upsert_worker_heartbeat",
                return_value={"worker_id": "worker-1"},
            ) as upsert_worker,
        ):
            first = runtime_heartbeat.heartbeat_worker(worker_id="worker-1", status="running")
            second = runtime_heartbeat.heartbeat_worker(worker_id="worker-1", status="running")
        self.assertTrue(first["published"])
        self.assertEqual(second["reason"], "throttled")
        self.assertEqual(upsert_worker.call_count, 1)

    def test_force_heartbeat_bypasses_throttle(self) -> None:
        with (
            patch.object(runtime_heartbeat.config, "RUNTIME_HEARTBEATS_ENABLED", True, create=True),
            patch.object(runtime_heartbeat.config, "RUNTIME_HEARTBEAT_INTERVAL_SECONDS", 30, create=True),
            patch.object(
                runtime_heartbeat.supabase_client,
                "upsert_worker_heartbeat",
                return_value={"worker_id": "worker-1"},
            ) as upsert_worker,
        ):
            runtime_heartbeat.heartbeat_worker(worker_id="worker-1", status="running")
            forced = runtime_heartbeat.heartbeat_worker(worker_id="worker-1", status="running", force=True)
        self.assertTrue(forced["published"])
        self.assertEqual(upsert_worker.call_count, 2)

    def test_metadata_redaction_applied(self) -> None:
        with (
            patch.object(runtime_heartbeat.config, "RUNTIME_HEARTBEATS_ENABLED", True, create=True),
            patch.object(
                runtime_heartbeat.supabase_client,
                "upsert_worker_heartbeat",
                return_value={"worker_id": "worker-1"},
            ) as upsert_worker,
        ):
            runtime_heartbeat.heartbeat_worker(
                worker_id="worker-1",
                status="running",
                metadata={"password": "secret", "safe": "ok"},
            )
        metadata = upsert_worker.call_args.args[0]["metadata"]
        self.assertEqual(metadata["safe"], "ok")
        self.assertNotIn("password", metadata)


if __name__ == "__main__":
    unittest.main()
