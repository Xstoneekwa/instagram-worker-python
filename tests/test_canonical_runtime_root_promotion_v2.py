from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import phonefarm_runtime_control as ctl
import follow60_status_trust_cache_v1 as status_trust


class CanonicalRuntimeRootPromotionV2Test(unittest.TestCase):
    candidate_sha = "b" * 40

    def test_convergence_requires_exact_consumption_proof_after_services_align(self):
        paths = ctl.RuntimePaths(Path("/current"), Path("/releases"), Path("/runtime"), Path("/legacy"))
        expected = {"device": 1, "inode": 2, "uid": 501, "gid": 20, "mode": 0o600}
        service_pass = {"ok": True, "status": "SERVICE_CONVERGENCE_PASS", "services": {}}
        with (
            mock.patch.object(ctl, "canonical_service_snapshot", return_value=service_pass),
            mock.patch.object(ctl, "verify_startup_skip_consumed", side_effect=[
                {"ok": False, "status": "STARTUP_TOKEN_PENDING"},
                {"ok": True, "status": "STARTUP_TOKEN_GATE_PASS"},
            ]) as verify,
            mock.patch.object(ctl.time, "sleep"),
        ):
            result = ctl._wait_for_service_convergence(
                paths=paths,
                expected_root="/release/new",
                expected_sha=self.candidate_sha,
                expected_startup_token=expected,
                timeout_seconds=2,
                interval_seconds=0.01,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["startupTokenGate"]["status"], "STARTUP_TOKEN_GATE_PASS")
        self.assertEqual(verify.call_count, 2)

    def test_token_timeout_is_never_returned_with_ok_true(self):
        paths = ctl.RuntimePaths(Path("/current"), Path("/releases"), Path("/runtime"), Path("/legacy"))
        with (
            mock.patch.object(ctl, "canonical_service_snapshot", return_value={"ok": True, "services": {}}),
            mock.patch.object(ctl, "verify_startup_skip_consumed", return_value={"ok": False, "reason": "missing"}),
        ):
            result = ctl._wait_for_service_convergence(
                paths=paths,
                expected_root="/release/new",
                expected_sha=self.candidate_sha,
                expected_startup_token={"token_id": "missing"},
                timeout_seconds=0,
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "SERVICE_CONVERGENCE_TIMEOUT")

    def test_inventory_requires_exact_one_root_module_and_sha_per_service(self):
        root = "/releases/new"
        sha = self.candidate_sha
        rows = [
            (11, 1, "python account_run_request_consumer.py"),
            (12, 1, "python device_heartbeat_publisher.py"),
            (13, 1, "python incident_notification_service.py"),
        ]
        paths = ctl.RuntimePaths(Path("/current"), Path("/releases"), Path("/runtime"), Path("/legacy"))
        runtime_root = ctl.RuntimeRoot(True, "valid", "/current", root, sha[:7])
        with (
            mock.patch.object(ctl, "resolve_runtime_root", return_value=runtime_root),
            mock.patch.object(ctl, "_ps_rows", return_value=rows),
            mock.patch.object(ctl, "_pid_cwd", return_value="/runtime"),
            mock.patch.object(ctl, "_resolved_process_module_root", return_value=root),
            mock.patch.object(ctl, "_git_full_commit", return_value=sha),
        ):
            passed = ctl.canonical_service_snapshot(paths=paths, expected_root=root, expected_sha=sha)
        self.assertTrue(passed["ok"])

        with (
            mock.patch.object(ctl, "resolve_runtime_root", return_value=runtime_root),
            mock.patch.object(ctl, "_ps_rows", return_value=rows + [rows[0]]),
            mock.patch.object(ctl, "_pid_cwd", return_value="/runtime"),
            mock.patch.object(ctl, "_resolved_process_module_root", return_value=root),
            mock.patch.object(ctl, "_git_full_commit", return_value=sha),
        ):
            duplicate = ctl.canonical_service_snapshot(paths=paths, expected_root=root, expected_sha=sha)
        self.assertFalse(duplicate["ok"])
        self.assertIn("dispatcher:duplicate_service", duplicate["failures"])

    def test_candidate_service_helper_uses_verified_user_domain(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            controller = base / "runtime/bin/phonefarm-runtimectl"
            controller.parent.mkdir(parents=True)
            controller.write_text("#!/bin/sh\n")
            paths = ctl.RuntimePaths(base / "current", base / "releases", base / "runtime", base / "legacy")
            context = {"uid": 501, "gid": 20, "user": "operator", "home": "/Users/operator", "launchd_domain": "gui/501"}
            completed = mock.Mock(returncode=0, stdout='{"ok": true, "status": "running"}\n', stderr="")
            with (
                mock.patch.object(ctl, "_promotion_service_context", return_value=context),
                mock.patch.object(ctl.os, "geteuid", return_value=501),
                mock.patch.object(ctl.subprocess, "run", return_value=completed) as run,
            ):
                result = ctl._run_canonical_controller("dispatcher", "pause", paths=paths, expected_context=context)
        self.assertTrue(result["ok"])
        command = run.call_args.args[0]
        self.assertIn(str(Path(ctl.__file__).resolve().parent / "promotion_service_control_v2.py"), command)
        self.assertNotIn(str(controller), command)
        self.assertEqual(result["runtimeUserContext"]["launchd_domain"], "gui/501")

    def test_switch_release_delegates_to_transactional_machine(self):
        validation = ctl.RuntimeRoot(True, "valid", "/release/new", "/release/new", "bbbbbbb")
        transaction = mock.MagicMock()
        transaction.__enter__.return_value = transaction
        with (
            mock.patch.object(ctl, "resolve_runtime_root", return_value=validation),
            mock.patch("follow60_external_deployment_lock_v2.DeploymentTransaction", return_value=transaction),
            mock.patch("promotion_runtime_adapter_v1.run_transactional_switch", return_value={"ok": True, "status": "PROMOTED"}) as run,
        ):
            result = ctl.switch_release("/release/new")
        self.assertTrue(result["ok"])
        run.assert_called_once()

    def test_manifest_generator_requires_every_status_trust_protocol_file(self):
        root = Path(__file__).resolve().parents[1]
        script = root / "scripts/regenerate-follow60-mainline-lock-v3-1.py"
        spec = importlib.util.spec_from_file_location("regenerate_lock_fixture", script)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        scope = json.loads((root / "docs/governance/FOLLOW60_MAINLINE_LOCK_V3_SCOPE.json").read_text())
        self.assertEqual(module.REQUIRED_STATUS_TRUST_SCOPE, set(status_trust.PROTOCOL_FILES))
        module.require_status_trust_scope(scope)


if __name__ == "__main__":
    unittest.main()
