from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest import TestCase, mock

import phonefarm_runtime_control as ctl
from cooperative_business_stop import read_termination_origin


ROOT = Path(__file__).resolve().parents[1]


class DispatcherPhase1ReliabilityContractTest(TestCase):
    def test_status_shell_path_is_local_only(self) -> None:
        source = (ROOT / "scripts" / "run_control_dispatcher_service.sh").read_text(encoding="utf-8")
        status_body = source.split("_status_json() {", 1)[1].split("\n}\n", 1)[0]
        self.assertNotIn("account_run_request_consumer.py preflight", status_body)
        self.assertNotIn("SUPABASE", status_body.upper())
        self.assertNotIn("adb ", status_body.lower())
        self.assertIn('"service_state"', status_body)
        self.assertIn('"preflight_state": "not_checked"', status_body)
        self.assertIn('DISPATCHER_STATUS_PROCESS_PROBE_KNOWN', status_body)
        self.assertIn('DISPATCHER_STATUS_LAUNCHD_PROBE_KNOWN', status_body)
        self.assertIn('local_liveness_probe_inconclusive', status_body)

    def test_install_has_no_automatic_internal_callers(self) -> None:
        shell = (ROOT / "scripts" / "run_control_dispatcher_service.sh").read_text(encoding="utf-8")
        self.assertNotIn('$0" install', shell)
        self.assertEqual(shell.count("  install)"), 1)

    def test_command_timeout_is_unknown_not_stopped(self) -> None:
        root = ctl.RuntimeRoot(True, "valid", "/tmp/current", "/tmp/release", "abc1234")
        with mock.patch.object(ctl, "resolve_runtime_root", return_value=root), \
             mock.patch.object(ctl, "_component_env", return_value={}), \
             mock.patch.object(ctl.subprocess, "run", side_effect=subprocess.TimeoutExpired(["status"], 20)):
            result = ctl._run_wrapper("dispatcher", "status", [], timeout=20)
        self.assertEqual(result["service_state"], "unknown")
        self.assertIsNone(result["processRunning"])
        self.assertIsNone(result["launchdLoaded"])

    def test_active_run_gate_blocks_recovery(self) -> None:
        stopped = {"ok": True, "status": "stopped", "service_state": "stopped", "processRunning": False}
        with mock.patch.object(ctl, "_run_wrapper", side_effect=[stopped, stopped]) as wrapper, \
             mock.patch.object(ctl, "_active_worker_children", return_value=[]), \
             mock.patch.object(ctl, "deployment_zero_gate", return_value={"ok": False, "reason": "active_runtime_work_present"}), \
             mock.patch.object(ctl, "_acquire_autoheal_lock", return_value=(Path("/tmp/lock"), True)), \
             mock.patch.object(ctl, "_release_autoheal_lock"):
            result = ctl.control_start("dispatcher")
        self.assertEqual(result["recoveryAction"], "none")
        self.assertEqual([call.args[1] for call in wrapper.call_args_list], ["status", "status"])

    def test_heartbeat_env_is_bound_to_active_runtime_identity(self) -> None:
        paths = ctl.RuntimePaths(Path("/tmp/current"), Path("/tmp/releases"), Path("/tmp/runtime"), Path("/tmp/legacy"))
        root = ctl.RuntimeRoot(True, "valid", "/tmp/current", "/tmp/releases/new", "newsha")
        env = ctl._component_env(paths, root, "heartbeat")
        self.assertEqual(env["PHONEFARM_ACTIVE_ROOT"], "/tmp/releases/new")
        self.assertEqual(env["PHONEFARM_ACTIVE_COMMIT"], "newsha")

    def test_foreign_runtime_process_is_running_degraded_not_absent(self) -> None:
        root = ctl.RuntimeRoot(True, "valid", "/tmp/current", "/tmp/releases/new", "newsha")
        payload = {"ok": True, "status": "stopped", "service_state": "stopped", "processRunning": False}
        with mock.patch.object(ctl, "_ps_rows", return_value=[(77, 1, "python account_run_request_consumer.py")]), \
             mock.patch.object(ctl, "_pid_cwd", return_value="/tmp/releases/old"):
            result = ctl._detect_component_mismatch("dispatcher", payload, root)
        self.assertEqual(result["status"], "runtime_root_mismatch")
        self.assertEqual(result["service_state"], "running")
        self.assertTrue(result["processRunning"])

    def test_inconclusive_child_probe_blocks_recovery(self) -> None:
        stopped = {"ok": True, "status": "stopped", "service_state": "stopped", "processRunning": False}
        with mock.patch.object(ctl, "_run_wrapper", side_effect=[stopped, stopped]) as wrapper, \
             mock.patch.object(ctl, "_active_worker_children", return_value=None), \
             mock.patch.object(ctl, "_acquire_autoheal_lock", return_value=(Path("/tmp/lock"), True)), \
             mock.patch.object(ctl, "_release_autoheal_lock"):
            result = ctl.control_start("dispatcher")
        self.assertEqual(result["recoveryAction"], "none")
        self.assertIn("child_probe_inconclusive", result["message"])
        self.assertEqual([call.args[1] for call in wrapper.call_args_list], ["status", "status"])

    def test_infrastructure_signal_has_non_human_provenance(self) -> None:
        wrapper = (ROOT / "scripts" / "run_control_dispatcher_service.sh").read_text(encoding="utf-8")
        consumer = (ROOT / "account_run_request_consumer.py").read_text(encoding="utf-8")
        self.assertIn('RUN_CONTROL_SIGNAL_ORIGIN="control_plane_service_stop"', wrapper)
        self.assertIn('human_manual_stop=False', consumer)
        self.assertIn('termination_origin=signal_origin', consumer)
        runner = (ROOT / "runner.py").read_text(encoding="utf-8")
        self.assertIn('"control_plane_service_stop": "control_plane_service_stop_signal"', runner)
        with mock.patch.dict(os.environ, {"RUN_CONTROL_SIGNAL_ORIGIN": "control_plane_service_stop"}, clear=False):
            self.assertEqual(read_termination_origin(None), "control_plane_service_stop")
