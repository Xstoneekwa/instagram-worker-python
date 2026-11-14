from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import account_run_request_consumer as consumer
import assignment_dispatch_resolver as resolver


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


class CanonicalLoginAssignmentAdmissionTests(unittest.TestCase):
    def test_exact_new_onboarding_binding_passes_without_legacy_device(self):
        for pool in ("full_cycle", "outreach_only"):
            for run_type in ("login_provisioning", "login_email_code_resume"):
                for required in (False, True):
                    with self.subTest(pool=pool, run_type=run_type, required=required):
                        b = binding(1, "com.instagram.androie")
                        row = dict(id=b["assignment_id"], device_id=b["device_id"], app_instance_id=b["app_instance_id"],
                            assignment_type=pool, slot_kind="manual_only", status="reserved",
                            phone_device={"adb_serial": "SYNTHETIC_SERIAL"},
                            phone_app_instance={"id":b["app_instance_id"], "instance_index":1, "package_name":b["package_name"]})
                        cfg = SimpleNamespace(require_assignment=required, enforce_assignment_window=False, test_account_ids=set())
                        with patch.object(consumer.supabase_client,"load_account",return_value={"id":"new-account", "status":"inactive", "device_id":None}), patch.object(
                                resolver.supabase_client,"load_open_account_assignment_for_dispatch",return_value=row):
                            self.assertEqual(consumer._account_is_launch_allowed("new-account",cfg),(True,None))
                            ok, reason, ctx = consumer._validate_assignment("new-account",run_type,cfg)
                        self.assertTrue(ok, reason)
                        self.assertTrue(consumer._validate_login_request_binding(b,ctx)[0])
                        with patch.object(consumer,"_load_expected_username",return_value="synthetic_owner"):
                            command = consumer._build_login_provisioner_command("new-account",run_type,"request",device_serial=ctx["adb_serial"],
                                package_name=ctx["package_name"],app_instance_id=ctx["app_instance_id"])
                        self.assertEqual(Path(command[1]), Path(consumer.__file__).resolve().parent / "instagram_login_provisioner_cli.py")
                        self.assertEqual(command[command.index("--package-name")+1],b["package_name"])

    def test_legacy_optional_assignment_never_admits_unknown_login_context(self):
        cfg = SimpleNamespace(require_assignment=False,enforce_assignment_window=False)
        for ctx in ({"assignment_found":False,"reason":"assignment_type_incompatible"},
                    {"assignment_found":False,"reason":"assignment_not_found"},
                    {"assignment_found":True,"reason":"unknown"},
                    {"assignment_found":True,"reason":"assignment_resolved","fallback_used":True},
                    {"assignment_found":True}):
            with self.subTest(ctx=ctx), patch.object(consumer,"resolve_account_assignment_runtime_context",return_value=ctx) as read:
                self.assertFalse(consumer._validate_assignment("account","login_provisioning",cfg)[0])
                self.assertTrue(read.call_args.kwargs["require_assignment"])

    def test_noop_cannot_invent_a_login_device(self):
        cfg=SimpleNamespace(require_assignment=False,enforce_assignment_window=False)
        with patch.object(consumer,"resolve_account_assignment_runtime_context",return_value={"reason":"assignment_device_missing_adb_serial"}), patch.object(
                consumer,"_integration_noop_runner_enabled",return_value=True):
            self.assertFalse(consumer._validate_assignment("account","login_provisioning",cfg)[0])

    def test_business_optional_assignment_behavior_is_unchanged(self):
        cfg=SimpleNamespace(require_assignment=False,enforce_assignment_window=False)
        ctx={"assignment_found":False,"reason":"assignment_not_found"}
        with patch.object(consumer,"resolve_account_assignment_runtime_context",return_value=ctx) as read:
            self.assertTrue(consumer._validate_assignment("account","account_session",cfg)[0])
            self.assertFalse(read.call_args.kwargs["require_assignment"])


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
            patch.object(
                consumer,
                "_load_package_runtime_contract",
                return_value=(True, "ready", {"ready": True}),
            ),
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
            patch.object(
                consumer,
                "_load_package_runtime_contract",
                return_value=(True, "ready", {"ready": True}),
            ),
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
