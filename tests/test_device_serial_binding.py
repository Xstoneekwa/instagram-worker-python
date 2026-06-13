"""Tests for explicit ADB serial selection before uiautomator2 connects."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import device


class DeviceSerialBindingTest(unittest.TestCase):
    def test_connect_device_requires_serial_when_multiple_adb_devices_online(self) -> None:
        with (
            patch.object(
                device,
                "_connected_adb_devices",
                return_value=[
                    {"serial": "emulator-5554", "status": "device"},
                    {"serial": "RFGL145VCKE", "status": "device"},
                ],
            ),
            patch.object(device.u2, "connect") as connect,
        ):
            with self.assertRaisesRegex(RuntimeError, "device_serial_required_multiple_adb_devices"):
                device.connect_device(None)

        connect.assert_not_called()

    def test_connect_device_uses_explicit_serial_when_multiple_adb_devices_online(self) -> None:
        fake_device = type("FakeDevice", (), {"info": {"ok": True}})()
        with (
            patch.object(
                device,
                "_connected_adb_devices",
                return_value=[
                    {"serial": "emulator-5554", "status": "device"},
                    {"serial": "RFGL145VCKE", "status": "device"},
                ],
            ),
            patch.object(device.u2, "connect", return_value=fake_device) as connect,
        ):
            result = device.connect_device("emulator-5554")

        self.assertIs(result, fake_device)
        connect.assert_called_once_with("emulator-5554")

    def test_connect_device_keeps_single_device_legacy_fallback(self) -> None:
        fake_device = type("FakeDevice", (), {"info": {"ok": True}})()
        with (
            patch.object(
                device,
                "_connected_adb_devices",
                return_value=[{"serial": "emulator-5554", "status": "device"}],
            ),
            patch.object(device.u2, "connect", return_value=fake_device) as connect,
        ):
            result = device.connect_device(None)

        self.assertIs(result, fake_device)
        connect.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
