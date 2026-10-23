"""Real runner version/serial gates with only external transports doubled."""
from __future__ import annotations

from contextlib import ExitStack
import copy
import json
import os
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

import account_run_control
import device
import device_action_latch as latch
import follow_60s_canary as follow60
import instagram_ui_profile_v1 as ui
import post_follow_stage_outbox as outbox
import runner

ACCOUNT_ID, RUN_ID, REQUEST_ID, DEVICE_ID, INSTANCE_ID, ROOT_ID, LEASE_ID = [
    f"00000000-0000-4000-8000-{i:012d}" for i in range(201, 208)]
SERIAL = "emulator-5554"
PACKAGE = "com.instagram.android"


def base_argv(*extra):
    return ["runner.py", "--account-id", ACCOUNT_ID, "--run-type", "account_session",
            "--device-serial", SERIAL, "--run-request-id", REQUEST_ID,
            "--admitted-run-id", RUN_ID, *extra]


class OfflineRunnerProof:
    """Coherent canonical request/run/lease/assignment at transport boundaries.

    _prepare_instagram_ui_profile, arm, attach, lineage, CAS/readback and package
    selection all execute unchanged. An attempted live connection is an error.
    """
    def __init__(self, case, *, package=PACKAGE, missing_proof=False):
        self.case = case
        self.package = package
        self.context = dict(assignment_found=True, assignment_id=ROOT_ID,
            account_id=ACCOUNT_ID, assignment_type="full_cycle", slot_kind="full_cycle_6h",
            device_id=DEVICE_ID, app_instance_id=INSTANCE_ID, clone_id=None,
            device_kind="physical_phone", adb_serial=SERIAL, package_name=package,
            source="account_assignments", fallback_used=False, reason="assignment_resolved",
            run_type="account_session")
        self.request = dict(id=REQUEST_ID, account_id=ACCOUNT_ID, run_id=RUN_ID,
            device_id=DEVICE_ID, app_instance_id=INSTANCE_ID,
            root_business_session_id=ROOT_ID, execution_attempt_no=1, retry_index=0,
            metadata_safe={})
        if missing_proof:
            self.request.pop("app_instance_id")
        self.row = dict(id=RUN_ID, account_id=ACCOUNT_ID, status="running",
            updated_at="2026-10-23T00:00:00Z", performance_summary={"retained": True})
        self.plan = dict(run_id=RUN_ID, run_request_id=REQUEST_ID, account_id=ACCOUNT_ID,
            device_id=DEVICE_ID, app_instance_id=INSTANCE_ID, expected_package=package,
            last_updated_at="2026-10-23T00:00:00Z",
            plan=dict(root_business_session_id=ROOT_ID, execution_attempt_no=1))
        self.lease = dict(device_id=DEVICE_ID, request_id=REQUEST_ID, account_id=ACCOUNT_ID,
            worker_id="worker", lease_id=LEASE_ID, generation=LEASE_ID, lease_seconds=300)
        self.fake_device = type("FakeDevice", (), {"serial": SERIAL, "info": {"ok": True}})()
        self.seen = {}
        self.writes = []

    def transport(self, method, table, **kw):
        if table == "ig_runs" and method == "PATCH":
            self.case.assertEqual(set(kw["body"]), {"performance_summary"})
            self.case.assertEqual(kw["query"]["updated_at"], "eq." + self.row["updated_at"])
            self.case.assertEqual(kw["max_retries"], 0)
            self.row.update(copy.deepcopy(kw["body"]))
            self.writes.append(table)
            return []
        if table == "account_session_resume_plans":
            if method == "GET":
                return [copy.deepcopy(self.plan)]
            if method == "PATCH":
                self.case.assertEqual(set(kw["body"]), {"plan", "last_updated_at"})
                self.case.assertEqual(kw["query"]["last_updated_at"], "eq." + self.plan["last_updated_at"])
                self.case.assertEqual(kw["max_retries"], 0)
                self.plan.update(copy.deepcopy(kw["body"]))
                self.writes.append(table)
                return [copy.deepcopy(self.plan)]
        raise AssertionError("UNEXPECTED_DB_TRANSPORT:" + method + ":" + table)

    def run(self, argv):
        def safe(name, **kw):
            if name == "load_account":
                return {"id": ACCOUNT_ID, "username": "cinema_catchup"}
            if name == "create_run":
                return {"id": RUN_ID}
            return None

        def metadata_transport(serial, args, **kw):
            self.case.assertEqual(serial, SERIAL)
            self.case.assertIn(self.package, args)
            return (0, f"Packages:\n  Package [{self.package}] (123):\n    versionCode=385211304 minSdk=28\n    versionName=446.0.0.49.77\n", "")

        def health(*args, **kw):
            self.seen["package"] = runner.config.INSTAGRAM_PACKAGE
            self.seen["binding"] = ui._SESSION.binding.to_dict()
            return False

        with ExitStack() as stack:
            runtime = stack.enter_context(tempfile.TemporaryDirectory(prefix="runner-proof-"))
            patches = [
                patch.dict(os.environ, FLEET_DEVICE_LOCK_BINDING_JSON=json.dumps(self.lease),
                    AUTO_RESTART_RESUME_PLAN_V1="", FOLLOW60_MAINLINE_ENABLED="true",
                    RUN_CONTROL_DISPATCHER_WORKER_ID="worker",
                    PHONEFARM_RUNTIME_HOME=runtime),
                patch.object(socket.socket, "connect", side_effect=AssertionError("LIVE_NETWORK_FORBIDDEN")),
                patch.object(ui, "_SESSION", None), patch.object(follow60, "_RUNTIME", follow60._RUNTIME),
                patch.object(latch, "_ENABLED", False), patch.object(latch, "_STOP_REQUESTED", False),
                patch.object(latch, "_ACTIVE_ACTIONS", 0),
                patch.object(runner, "_CURRENT_FOLLOW_PERSISTENCE_RUN_BINDING", None),
                patch.object(sys, "argv", argv),
                patch.object(runner, "_safe_supabase_call", side_effect=safe),
                patch.object(runner, "_load_account_session_follow_targets", return_value=([], None)),
                patch.object(runner, "_abort_if_run_request_canceled", return_value=False),
                patch.object(runner.supabase_client, "set_log_context"),
                patch.object(runner.supabase_client, "log_performance_event"),
                patch.object(runner.supabase_client, "get_follow_60s_canary_control_v1", return_value=None),
                patch.object(runner.supabase_client, "begin_device_activity_v1",
                    return_value={"ok": True, "irreversible_work_state": "STARTED_OR_AMBIGUOUS"}),
                patch.object(account_run_control, "get_account_run_request", side_effect=lambda *a, **k: copy.deepcopy(self.request)),
                patch.object(account_run_control, "get_ig_run_by_id", side_effect=lambda *a, **k: copy.deepcopy(self.row)),
                patch.object(runner.supabase_client, "load_run_row", side_effect=lambda *a, **k: copy.deepcopy(self.row)),
                patch.object(runner.supabase_client, "_request_json", side_effect=self.transport),
                patch.object(device, "_adb_run", side_effect=metadata_transport),
                patch.object(runner, "verify_runtime_integrity", return_value={"ok": True}),
                patch.object(outbox, "flush_pending", return_value={"ok": True}),
                patch.object(runner, "init_run_file_logging", return_value=None),
                patch.object(runner, "_orf_set_runtime_context"), patch.object(runner, "_orf_publish_event"),
                patch.object(runner, "_orf_heartbeat_worker"), patch.object(runner, "_orf_heartbeat_device"),
                patch.object(runner, "connect_device", return_value=self.fake_device),
                patch.object(runner, "disable_android_animations"),
                patch.object(runner, "health_check", side_effect=health),
                patch.object(runner, "_update_run_status_safe"), patch.object(runner, "reset_perf_counters"),
                patch.object(runner, "_emit_performance_summary"),
                patch.object(runner.config, "INSTAGRAM_PACKAGE", PACKAGE),
                patch.object(runner.config, "ACCOUNT_ASSIGNMENT_DISPATCH_ENABLED", True),
                patch.object(runner.config, "ACCOUNT_ASSIGNMENT_DISPATCH_RUN_TYPES", "account_session"),
                patch.object(runner, "resolve_account_assignment_runtime_context", return_value=self.context),
            ]
            mocks = [stack.enter_context(p) for p in patches]
            self.connect = mocks[next(i for i,p in enumerate(patches) if getattr(p, "attribute", None) == "connect_device")]
            code = runner.main()
            self.seen["armed"] = ui.armed()
            self.seen["attached"] = ui._SESSION is not None and ui._SESSION.device is self.fake_device
            return code, self.seen


class RunnerDeviceSerialTest(unittest.TestCase):
    def test_runner_passes_explicit_device_serial_to_connect_device(self):
        proof = OfflineRunnerProof(self)
        code, seen = proof.run(base_argv())
        self.assertNotEqual(code, 0)
        proof.connect.assert_called_once_with(SERIAL)
        self.assertTrue(seen["armed"])
        self.assertTrue(seen["attached"])
        self.assertEqual(seen["binding"]["serial"], SERIAL)
        self.assertEqual(proof.writes, ["ig_runs", "account_session_resume_plans"])

    def test_missing_canonical_instance_proof_never_connects(self):
        proof = OfflineRunnerProof(self, missing_proof=True)
        code, seen = proof.run(base_argv())
        self.assertNotEqual(code, 0)
        proof.connect.assert_not_called()
        self.assertFalse(seen["armed"])
        self.assertEqual(proof.writes, [])


if __name__ == "__main__":
    unittest.main()
