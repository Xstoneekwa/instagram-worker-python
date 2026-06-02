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


if __name__ == "__main__":
    unittest.main()
