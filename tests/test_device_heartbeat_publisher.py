from __future__ import annotations

import unittest
from unittest.mock import patch

import device_heartbeat_publisher as publisher


class DeviceHeartbeatPublisherTest(unittest.TestCase):
    def test_parse_adb_devices_l_keeps_safe_fields_and_states(self) -> None:
        out = """
List of devices attached
RFGL145VCKE device usb:1-1 product:a16nsxx model:SM_A165F device:a16 transport_id:7
RFGL145LZHE unauthorized usb:1-2 product:a16nsxx model:SM_A165F device:a16 transport_id:8
emulator-5554 offline transport_id:9
"""

        rows = publisher.parse_adb_devices_l(out)

        self.assertEqual([row.adb_serial for row in rows], ["RFGL145VCKE", "RFGL145LZHE", "emulator-5554"])
        self.assertEqual(rows[0].adb_state, "device")
        self.assertEqual(rows[0].model, "SM_A165F")
        self.assertEqual(rows[1].adb_state, "unauthorized")
        self.assertEqual(rows[2].adb_state, "offline")

    def test_status_mapping_matches_device_heartbeats_constraint(self) -> None:
        self.assertEqual(publisher.heartbeat_status_for_adb_state("device"), "online")
        self.assertEqual(publisher.heartbeat_status_for_adb_state("offline"), "offline")
        self.assertEqual(publisher.heartbeat_status_for_adb_state("unauthorized"), "unauthorized")
        self.assertEqual(publisher.heartbeat_status_for_adb_state("recovery"), "unknown")

    def test_parse_battery_level_bounds_value(self) -> None:
        self.assertEqual(publisher.parse_battery_level("AC powered: false\nlevel: 87\n"), 87)
        self.assertIsNone(publisher.parse_battery_level("level: 101\n"))
        self.assertIsNone(publisher.parse_battery_level("level: nope\n"))

    def test_publish_observations_maps_serial_to_phone_device_and_publishes(self) -> None:
        observation = publisher.AdbDeviceObservation(
            adb_serial="RFGL145VCKE",
            adb_state="device",
            model="SM_A165F",
            product="a16nsxx",
            device="a16",
            transport_id="7",
        )
        phone_rows = [{
            "id": "00000000-0000-4000-8000-000000000001",
            "adb_serial": "RFGL145VCKE",
            "name": "Samsung A16-01",
        }]

        with patch.object(
            publisher.runtime_heartbeat,
            "heartbeat_device",
            return_value={"published": True, "row": {"last_seen_at": "2026-06-02T20:00:00+00:00"}},
        ) as heartbeat:
            summary = publisher.publish_observations([observation], phone_rows, host_label="mac-a")

        self.assertEqual(summary["published_count"], 1)
        self.assertEqual(summary["skipped_count"], 0)
        heartbeat.assert_called_once()
        kwargs = heartbeat.call_args.kwargs
        self.assertEqual(kwargs["status"], "online")
        self.assertEqual(kwargs["adb_serial"], "RFGL145VCKE")
        self.assertEqual(kwargs["host_machine"], "mac-a")
        self.assertEqual(kwargs["metadata"]["source"], "local_adb_devices_l")
        self.assertEqual(kwargs["metadata"]["model"], "SM_A165F")

    def test_publish_observations_skips_unregistered_serial(self) -> None:
        observation = publisher.AdbDeviceObservation(adb_serial="UNKNOWN", adb_state="device")

        summary = publisher.publish_observations([observation], [], host_label="mac-a")

        self.assertEqual(summary["published_count"], 0)
        self.assertEqual(summary["skipped_count"], 1)
        self.assertEqual(summary["skipped"][0]["reason"], "phone_device_not_registered")

    def test_dry_run_does_not_write(self) -> None:
        observation = publisher.AdbDeviceObservation(adb_serial="RFGL145VCKE", adb_state="device")
        phone_rows = [{"id": "00000000-0000-4000-8000-000000000001", "adb_serial": "RFGL145VCKE"}]

        with patch.object(publisher.runtime_heartbeat, "heartbeat_device") as heartbeat:
            summary = publisher.publish_observations([observation], phone_rows, host_label="mac-a", dry_run=True)

        self.assertEqual(summary["published_count"], 1)
        heartbeat.assert_not_called()

    def test_write_cycle_state_persists_json(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            state_file = f"{tmp}/state.json"
            publisher.write_cycle_state(state_file, {"ok": True, "published_count": 2})
            payload = Path(state_file).read_text(encoding="utf-8")
            self.assertIn('"ok": true', payload)
            self.assertIn('"published_count": 2', payload)

    def test_append_rotating_json_log_rotates_existing_large_file(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "heartbeat.log"
            log_path.write_text("x" * 1024 * 1024, encoding="utf-8")

            publisher.append_rotating_json_log(str(log_path), {"ok": True}, max_bytes=1024)

            self.assertTrue(log_path.exists())
            self.assertTrue(log_path.with_suffix(".log.1").exists())
            self.assertIn('"ok": true', log_path.read_text(encoding="utf-8"))

    def test_serve_forever_runs_initial_cycle_and_respects_shutdown(self) -> None:
        publisher._shutdown_requested = False
        calls: list[dict[str, object]] = []
        monotonic_values = iter([0.0, 0.0, 1.0, 1.0, 2.0, 62.0, 62.0, 63.0])

        def fake_cycle(**kwargs):
            calls.append(kwargs)
            publisher._shutdown_requested = len(calls) >= 2
            return {"ok": True, "published_count": 1, "observed_count": 1}

        with patch.object(publisher, "run_publish_cycle", side_effect=fake_cycle):
            with patch.object(publisher.time, "sleep", return_value=None):
                with patch.object(publisher.time, "monotonic", side_effect=lambda: next(monotonic_values, 100.0)):
                    exit_code = publisher.serve_forever(
                        adb_path="adb",
                        host_label="mac-a",
                        include_battery=False,
                        allowed_serials=None,
                        interval_seconds=60,
                        state_file="/tmp/state.json",
                    )

        self.assertEqual(exit_code, 0)
        self.assertGreaterEqual(len(calls), 2)
        publisher._shutdown_requested = False


if __name__ == "__main__":
    unittest.main()

