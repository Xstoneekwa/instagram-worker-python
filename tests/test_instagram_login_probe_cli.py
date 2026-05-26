from __future__ import annotations

import argparse
import json
import os
import unittest
from unittest.mock import Mock, patch

import instagram_login_probe_cli as cli


ACCOUNT_ID = "42c625c2-e761-4100-8a9d-7ae1373de97d"
CONNECTED_XML = (
    '<node content-desc="Home" />'
    '<node content-desc="Search" />'
    '<node content-desc="Reels" />'
    '<node content-desc="Profile" />'
)
LOGIN_XML = '<node text="Log in to Instagram" /><node text="Username" /><node text="Password" />'


class FakeDevice:
    def __init__(self, hierarchy: str | None = None, exc: Exception | None = None) -> None:
        self.hierarchy = hierarchy
        self.exc = exc
        self.dump_calls = 0
        self.app_start = Mock()
        self.app_stop = Mock()
        self.click = Mock()
        self.tap = Mock()

    def dump_hierarchy(self, compressed: bool = False) -> str:
        self.dump_calls += 1
        if self.exc:
            raise self.exc
        return str(self.hierarchy or "")


def _args(*items: str) -> argparse.Namespace:
    args = list(items)
    if "--app-start" not in args and "--observe-current-screen-only" not in args:
        args.insert(0, "--observe-current-screen-only")
    return cli.build_parser().parse_args(args)


def _real_args(*items: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(list(items))


class InstagramLoginProbeCliTest(unittest.TestCase):
    def test_json_connected_returns_outcome_and_timings(self) -> None:
        publisher = Mock()
        device = FakeDevice(CONNECTED_XML)

        code, summary = cli.run_probe_command(
            _args("--json", "--no-publish"),
            connect_func=lambda _serial, _timeout: device,
            publisher=publisher,
        )

        self.assertEqual(code, 0)
        self.assertEqual(summary["outcome"], "connected")
        self.assertEqual(summary["login_status"], "connected")
        self.assertIn("connect_ms", summary["timings_ms"])
        self.assertIn("app_start_ms", summary["timings_ms"])
        self.assertIn("post_start_wait_ms", summary["timings_ms"])
        self.assertIn("dump_hierarchy_ms", summary["timings_ms"])
        self.assertIn("classify_ms", summary["timings_ms"])
        self.assertIn("total_ms", summary["timings_ms"])
        self.assertEqual(device.dump_calls, 1)
        publisher.assert_not_called()

    def test_login_screen_returns_logged_out(self) -> None:
        device = FakeDevice(LOGIN_XML)

        code, summary = cli.run_probe_command(
            _args("--json"),
            connect_func=lambda _serial, _timeout: device,
        )

        self.assertEqual(code, 0)
        self.assertEqual(summary["outcome"], "logged_out")
        self.assertEqual(summary["login_status"], "logged_out")
        self.assertEqual(summary["probe_reason"], "login_screen_signal")

    def test_publish_absent_does_not_publish_even_when_publishable(self) -> None:
        publisher = Mock()
        device = FakeDevice(CONNECTED_XML)

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}):
            code, summary = cli.run_probe_command(
                _args("--json", "--account-id", ACCOUNT_ID),
                connect_func=lambda _serial, _timeout: device,
                publisher=publisher,
            )

        self.assertEqual(code, 0)
        self.assertTrue(summary["should_publish"])
        self.assertFalse(summary["published"])
        self.assertEqual(summary["publish_reason"], "not_requested")
        publisher.assert_not_called()

    def test_publish_without_account_id_returns_validation_error(self) -> None:
        publisher = Mock()
        connect = Mock()

        code, summary = cli.run_probe_command(
            _args("--json", "--publish"),
            connect_func=connect,
            publisher=publisher,
        )

        self.assertEqual(code, 2)
        self.assertEqual(summary["error"], "account_id_required")
        self.assertEqual(summary["publish_reason"], "account_id_required")
        connect.assert_not_called()
        publisher.assert_not_called()

    def test_publish_with_flag_off_returns_disabled(self) -> None:
        publisher = Mock()
        device = FakeDevice(CONNECTED_XML)

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "false"}):
            code, summary = cli.run_probe_command(
                _args("--json", "--publish", "--account-id", ACCOUNT_ID),
                connect_func=lambda _serial, _timeout: device,
                publisher=publisher,
            )

        self.assertEqual(code, 0)
        self.assertFalse(summary["published"])
        self.assertEqual(summary["publish_reason"], "disabled")
        publisher.assert_not_called()

    def test_connect_exception_returns_safe_error(self) -> None:
        def connect(_serial: str | None, _timeout: float) -> FakeDevice:
            raise RuntimeError("connect boom")

        code, summary = cli.run_probe_command(
            _args("--json", "--device-serial", "emulator-5554"),
            connect_func=connect,
        )

        self.assertEqual(code, 1)
        self.assertEqual(summary["outcome"], "unknown")
        self.assertEqual(summary["error"], "connect_failed")
        self.assertNotIn("connect boom", json.dumps(summary))

    def test_dump_exception_returns_unknown_safe_error(self) -> None:
        device = FakeDevice(exc=RuntimeError("dump boom"))

        code, summary = cli.run_probe_command(
            _args("--json"),
            connect_func=lambda _serial, _timeout: device,
        )

        self.assertEqual(code, 1)
        self.assertEqual(summary["outcome"], "unknown")
        self.assertEqual(summary["error"], "dump_hierarchy_failed")
        self.assertEqual(summary["probe_reason"], "dump_hierarchy_failed")

    def test_timings_are_present(self) -> None:
        device = FakeDevice(CONNECTED_XML)

        code, summary = cli.run_probe_command(
            _args("--json"),
            connect_func=lambda _serial, _timeout: device,
        )

        self.assertEqual(code, 0)
        self.assertEqual(set(summary["timings_ms"].keys()), {
            "app_start_ms",
            "post_start_wait_ms",
            "connect_ms",
            "dump_hierarchy_ms",
            "classify_ms",
            "total_ms",
        })

    def test_slow_dump_adds_warning(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        ticks = iter([0.0, 0.0, 0.01, 0.01, 2.5, 2.5, 2.51, 2.51])

        code, summary = cli.run_probe_command(
            _args("--json"),
            connect_func=lambda _serial, _timeout: device,
            timer=lambda: next(ticks),
        )

        self.assertEqual(code, 0)
        self.assertGreater(summary["timings_ms"]["dump_hierarchy_ms"], 2000)
        self.assertIn("slow_dump_hierarchy", summary["warnings"])

    def test_output_summary_excludes_sensitive_raw_values(self) -> None:
        raw_xml = '<node text="Password" /><node text="secret_ref vault token" /><node text="Log in to Instagram" />'
        device = FakeDevice(raw_xml)

        code, summary = cli.run_probe_command(
            _args("--json", "--device-serial", "emulator-5554"),
            connect_func=lambda _serial, _timeout: device,
        )

        self.assertEqual(code, 0)
        rendered = json.dumps(summary, sort_keys=True)
        self.assertNotIn(raw_xml, rendered)
        self.assertNotIn("secret_ref", rendered)
        self.assertNotIn("vault", rendered)
        self.assertNotIn("token", rendered)
        self.assertNotIn("Password", rendered)

    def test_device_serial_not_in_publish_metadata(self) -> None:
        publisher = Mock(return_value={"published": True, "status_code": 200})
        device = FakeDevice(CONNECTED_XML)

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}):
            code, summary = cli.run_probe_command(
                _args(
                    "--json",
                    "--publish",
                    "--account-id",
                    ACCOUNT_ID,
                    "--device-serial",
                    "emulator-5554",
                ),
                connect_func=lambda _serial, _timeout: device,
                publisher=publisher,
            )

        self.assertEqual(code, 0)
        self.assertTrue(summary["published"])
        metadata = publisher.call_args.kwargs["metadata"]
        self.assertNotIn("device_serial", metadata)
        self.assertNotIn("adb_serial", metadata)
        self.assertNotIn("device_udid", metadata)

    def test_no_app_lifecycle_or_input_actions_called(self) -> None:
        device = FakeDevice(CONNECTED_XML)

        code, _summary = cli.run_probe_command(
            _args("--json"),
            connect_func=lambda _serial, _timeout: device,
        )

        self.assertEqual(code, 0)
        device.app_start.assert_not_called()
        device.app_stop.assert_not_called()
        device.click.assert_not_called()
        device.tap.assert_not_called()

    def test_app_start_calls_default_package(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        sleeper = Mock()

        code, summary = cli.run_probe_command(
            _args("--json", "--app-start"),
            connect_func=lambda _serial, _timeout: device,
            sleeper=sleeper,
        )

        self.assertEqual(code, 0)
        self.assertTrue(summary["app_start_requested"])
        self.assertTrue(summary["app_started"])
        self.assertEqual(summary["package_name"], "com.instagram.android")
        device.app_start.assert_called_once_with("com.instagram.android")
        sleeper.assert_called_once_with(1.5)

    def test_default_probe_starts_app_before_dump(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        sleeper = Mock()

        code, summary = cli.run_probe_command(
            _real_args("--json"),
            connect_func=lambda _serial, _timeout: device,
            sleeper=sleeper,
        )

        self.assertEqual(code, 0)
        self.assertTrue(summary["app_start_attempted"])
        self.assertTrue(summary["app_started"])
        device.app_start.assert_called_once_with("com.instagram.android")
        self.assertEqual(device.dump_calls, 1)
        sleeper.assert_called_once_with(1.5)

    def test_app_start_uses_custom_package(self) -> None:
        device = FakeDevice(CONNECTED_XML)

        code, summary = cli.run_probe_command(
            _args("--json", "--app-start", "--package-name", "com.instagram.android.clone1"),
            connect_func=lambda _serial, _timeout: device,
            sleeper=Mock(),
        )

        self.assertEqual(code, 0)
        self.assertEqual(summary["package_name"], "com.instagram.android.clone1")
        device.app_start.assert_called_once_with("com.instagram.android.clone1")

    def test_post_start_wait_is_clamped_to_max(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        sleeper = Mock()

        code, summary = cli.run_probe_command(
            _args("--json", "--app-start", "--post-start-wait-ms", "9999"),
            connect_func=lambda _serial, _timeout: device,
            sleeper=sleeper,
        )

        self.assertEqual(code, 0)
        self.assertEqual(summary["timings_ms"]["post_start_wait_ms"], 3000)
        sleeper.assert_called_once_with(3.0)

    def test_post_start_wait_is_clamped_to_zero(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        sleeper = Mock()

        code, summary = cli.run_probe_command(
            _args("--json", "--app-start", "--post-start-wait-ms", "-10"),
            connect_func=lambda _serial, _timeout: device,
            sleeper=sleeper,
        )

        self.assertEqual(code, 0)
        self.assertEqual(summary["timings_ms"]["post_start_wait_ms"], 0)
        sleeper.assert_not_called()

    def test_app_start_exception_returns_safe_error_without_dump(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        device.app_start.side_effect = RuntimeError("package missing")

        code, summary = cli.run_probe_command(
            _args("--json", "--app-start"),
            connect_func=lambda _serial, _timeout: device,
            sleeper=Mock(),
        )

        self.assertEqual(code, 1)
        self.assertEqual(summary["outcome"], "unknown")
        self.assertEqual(summary["error"], "app_start_failed")
        self.assertEqual(summary["probe_reason"], "app_start_failed")
        self.assertFalse(summary["app_started"])
        self.assertEqual(device.dump_calls, 0)
        self.assertNotIn("package missing", json.dumps(summary))

    def test_app_start_timing_is_present(self) -> None:
        device = FakeDevice(CONNECTED_XML)

        code, summary = cli.run_probe_command(
            _args("--json", "--app-start", "--post-start-wait-ms", "0"),
            connect_func=lambda _serial, _timeout: device,
            sleeper=Mock(),
        )

        self.assertEqual(code, 0)
        self.assertIn("app_start_ms", summary["timings_ms"])
        self.assertIn("post_start_wait_ms", summary["timings_ms"])

    def test_slow_app_start_adds_warning(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        ticks = iter([0.0, 0.0, 0.01, 0.01, 2.5, 2.5, 2.51, 2.51, 2.52, 2.52])

        code, summary = cli.run_probe_command(
            _args("--json", "--app-start", "--post-start-wait-ms", "0"),
            connect_func=lambda _serial, _timeout: device,
            timer=lambda: next(ticks),
            sleeper=Mock(),
        )

        self.assertEqual(code, 0)
        self.assertGreater(summary["timings_ms"]["app_start_ms"], 2000)
        self.assertIn("slow_app_start", summary["warnings"])

    def test_app_start_does_not_call_app_stop_or_input_actions(self) -> None:
        device = FakeDevice(CONNECTED_XML)

        code, summary = cli.run_probe_command(
            _args("--json", "--app-start", "--post-start-wait-ms", "0", "--no-publish"),
            connect_func=lambda _serial, _timeout: device,
            sleeper=Mock(),
        )

        self.assertEqual(code, 0)
        self.assertFalse(summary["published"])
        device.app_start.assert_called_once()
        device.app_stop.assert_not_called()
        device.click.assert_not_called()
        device.tap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
