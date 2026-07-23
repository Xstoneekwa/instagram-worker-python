from __future__ import annotations

import unittest
from unittest.mock import patch

import account_run_request_consumer as consumer


def binding(index: int, package: str, *, prefix: str = "tenant-a") -> dict:
    return {
        "binding_version": "auto_login_app_instance_v1",
        "assignment_id": f"{prefix}-assignment",
        "device_id": f"{prefix}-device",
        "app_instance_id": f"{prefix}-instance-{index}",
        "package_name": package,
        "clone_index": index,
    }


def context(index: int, package: str, *, prefix: str = "tenant-a") -> dict:
    return {
        "assignment_id": f"{prefix}-assignment",
        "device_id": f"{prefix}-device",
        "app_instance_id": f"{prefix}-instance-{index}",
        "package_name": package,
        "app_instance_index": index,
    }


class AutoLoginAppInstanceBindingTests(unittest.TestCase):
    def test_primary_and_clone_bindings_are_generic(self) -> None:
        packages = (
            "com.instagram.android",
            "com.instagram.androie",
            "com.instagram.androif",
            "com.instagram.androig",
            "com.instagram.futureclone",
        )
        for index, package in enumerate(packages):
            with self.subTest(index=index):
                ok, reason, resolved = consumer._validate_login_request_binding(
                    binding(index, package),
                    context(index, package),
                )
                self.assertTrue(ok)
                self.assertIsNone(reason)
                self.assertEqual(resolved["package_name"], package)

    def test_binding_is_tenant_agnostic(self) -> None:
        for prefix in ("tenant-a-account-2", "tenant-b-account-9"):
            ok, reason, _resolved = consumer._validate_login_request_binding(
                binding(2, "com.instagram.androif", prefix=prefix),
                context(2, "com.instagram.androif", prefix=prefix),
            )
            self.assertTrue(ok)
            self.assertIsNone(reason)

    def test_lorielebras_fixture_preserves_clone_two(self) -> None:
        metadata = {
            "binding_version": "auto_login_app_instance_v1",
            "assignment_id": "fd07e592-19cc-4738-adc9-90fd3c3cd407",
            "device_id": "d663648c-800c-48a6-8684-d8605218baa5",
            "app_instance_id": "109b8382-bf54-4ca4-b8c9-58239d3a81e1",
            "package_name": "com.instagram.androif",
            "clone_index": 2,
        }
        ok, reason, resolved = consumer._validate_login_request_binding(metadata, {
            **metadata,
            "app_instance_index": 2,
        })
        self.assertTrue(ok)
        self.assertIsNone(reason)
        self.assertEqual(resolved, metadata)

    def test_missing_app_instance_fails_closed(self) -> None:
        metadata = binding(2, "com.instagram.androif")
        metadata["app_instance_id"] = ""
        ok, reason, _resolved = consumer._validate_login_request_binding(
            metadata,
            context(2, "com.instagram.androif"),
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "auto_login_app_instance_binding_missing")

    def test_missing_package_fails_closed(self) -> None:
        metadata = binding(2, "")
        ok, reason, _resolved = consumer._validate_login_request_binding(metadata, context(2, ""))
        self.assertFalse(ok)
        self.assertEqual(reason, "auto_login_app_instance_binding_missing")

    def test_invalid_package_fails_closed_without_primary_default(self) -> None:
        metadata = binding(2, "not a package")
        ok, reason, _resolved = consumer._validate_login_request_binding(
            metadata,
            context(2, "not a package"),
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "auto_login_app_instance_binding_missing")

    def test_any_binding_drift_fails_closed(self) -> None:
        base = binding(2, "com.instagram.androif")
        for field, value in (
            ("assignment_id", "other-assignment"),
            ("device_id", "other-device"),
            ("app_instance_id", "other-instance"),
            ("package_name", "com.instagram.android"),
            ("clone_index", 0),
        ):
            changed = dict(base)
            changed[field] = value
            with self.subTest(field=field):
                ok, reason, _resolved = consumer._validate_login_request_binding(
                    changed,
                    context(2, "com.instagram.androif"),
                )
                self.assertFalse(ok)
                self.assertEqual(reason, "assigned_instagram_app_instance_mismatch")

    def test_login_cli_never_defaults_to_primary(self) -> None:
        with patch.object(consumer, "_load_expected_username", return_value="expected_account"):
            with self.assertRaisesRegex(ValueError, "auto_login_package_binding_required"):
                consumer._build_login_provisioner_command(
                    "account-id",
                    "login_provisioning",
                    "request-id",
                    package_name=None,
                    app_instance_id="instance-id",
                )
            with self.assertRaisesRegex(ValueError, "auto_login_app_instance_binding_required"):
                consumer._build_login_provisioner_command(
                    "account-id",
                    "login_provisioning",
                    "request-id",
                    package_name="com.instagram.androif",
                    app_instance_id=None,
                )

    def test_missing_binding_stops_before_lock_or_subprocess(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=1.0,
            lease_seconds=60,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_provisioning"],
            test_account_ids=set(),
            subprocess_timeout_seconds=120,
            require_assignment=True,
            enforce_assignment_window=False,
        )
        request = {
            "id": "00000000-0000-4000-8000-000000000101",
            "account_id": "00000000-0000-4000-8000-000000000201",
            "requested_run_type": "login_provisioning",
            "metadata_safe": {},
            "status": "claimed",
        }
        dispatch = {
            "assignment_found": True,
            **context(2, "com.instagram.androif"),
            "assignment_type": "full_cycle",
            "adb_serial": "RFGL145VCKE",
            "reason": "assignment_resolved",
        }
        with (
            patch.object(consumer, "_account_is_launch_allowed", return_value=(True, None)),
            patch.object(consumer, "mark_account_run_request_starting", return_value=True),
            patch.object(consumer, "resolve_account_assignment_runtime_context", return_value=dispatch),
            patch.object(consumer, "_safe_complete_account_run_request") as complete,
            patch.object(consumer, "_audit"),
            patch.object(consumer, "_publish_auto_login_dispatch_failure"),
            patch.object(consumer, "transfer_device_lock") as transfer,
            patch.object(consumer.subprocess, "Popen") as popen,
        ):
            consumer._handle_claimed_request(cfg, request)

        self.assertEqual(complete.call_args.kwargs["error_code"], "auto_login_app_instance_binding_missing")
        transfer.assert_not_called()
        popen.assert_not_called()

    def test_changed_binding_stops_before_lock_or_subprocess(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=1.0,
            lease_seconds=60,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_provisioning"],
            test_account_ids=set(),
            subprocess_timeout_seconds=120,
            require_assignment=True,
            enforce_assignment_window=False,
        )
        request = {
            "id": "00000000-0000-4000-8000-000000000101",
            "account_id": "00000000-0000-4000-8000-000000000201",
            "requested_run_type": "login_provisioning",
            "metadata_safe": binding(2, "com.instagram.androif"),
            "status": "claimed",
        }
        dispatch = {
            "assignment_found": True,
            **context(0, "com.instagram.android"),
            "assignment_type": "full_cycle",
            "adb_serial": "RFGL145VCKE",
            "reason": "assignment_resolved",
        }
        with (
            patch.object(consumer, "_account_is_launch_allowed", return_value=(True, None)),
            patch.object(consumer, "mark_account_run_request_starting", return_value=True),
            patch.object(consumer, "resolve_account_assignment_runtime_context", return_value=dispatch),
            patch.object(consumer, "_safe_complete_account_run_request") as complete,
            patch.object(consumer, "_audit"),
            patch.object(consumer, "_publish_auto_login_dispatch_failure"),
            patch.object(consumer, "transfer_device_lock") as transfer,
            patch.object(consumer.subprocess, "Popen") as popen,
        ):
            consumer._handle_claimed_request(cfg, request)

        self.assertEqual(complete.call_args.kwargs["error_code"], "assigned_instagram_app_instance_mismatch")
        transfer.assert_not_called()
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
