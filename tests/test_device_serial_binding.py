"""Tests for the low-level uiautomator2 serial forwarding contract.

Multi-device exclusivity is enforced before this helper by the dispatcher
device lease and runner serial binding. ``connect_device`` only owns forwarding
an explicit serial, or preserving the legacy uiautomator2 default when absent.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import device


class DeviceSerialBindingTest(unittest.TestCase):
    def test_connect_device_forwards_explicit_serial(self) -> None:
        fake_device = type("FakeDevice", (), {"info": {"ok": True}})()
        with patch.object(device.u2, "connect", return_value=fake_device) as connect:
            result = device.connect_device("emulator-5554")

        self.assertIs(result, fake_device)
        connect.assert_called_once_with("emulator-5554")

    def test_connect_device_keeps_uiautomator_default_when_serial_missing(self) -> None:
        fake_device = type("FakeDevice", (), {"info": {"ok": True}})()
        with patch.object(device.u2, "connect", return_value=fake_device) as connect:
            result = device.connect_device(None)

        self.assertIs(result, fake_device)
        connect.assert_called_once_with()

    def test_connect_device_propagates_connection_failure_without_retry(self) -> None:
        with patch.object(
            device.u2,
            "connect",
            side_effect=RuntimeError("uiautomator_connection_failed"),
        ) as connect:
            with self.assertRaisesRegex(RuntimeError, "uiautomator_connection_failed"):
                device.connect_device("RFGL145VCKE")

        connect.assert_called_once_with("RFGL145VCKE")


if __name__ == "__main__":
    unittest.main()
