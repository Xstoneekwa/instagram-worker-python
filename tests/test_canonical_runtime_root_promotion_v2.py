from __future__ import annotations

import os
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import phonefarm_runtime_control as ctl
import follow60_status_trust_cache_v1 as status_trust


ZERO = {
    "ok": True,
    "counts": {
        "account_run_requests": 0,
        "ig_runs": 0,
        "auto_restart_device_locks": 0,
        "auto_restart_tick_locks": 0,
    },
}


def _release(root: Path) -> None:
    for relative in (
        "account_run_request_consumer.py",
        "device_heartbeat_publisher.py",
        "incident_notification_service.py",
        "scripts/run_control_dispatcher_service.sh",
        "scripts/device_heartbeat_service.sh",
        "scripts/incident_notifier_service.sh",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")
        path.chmod(0o755)


class CanonicalRuntimeRootPromotionV2Test(unittest.TestCase):
    previous_sha = "a" * 40
    candidate_sha = "b" * 40

    def _layout(self, raw: str):
        base = Path(raw)
        releases = base / "releases"
        old = releases / "old"
        new = releases / "new"
        current = base / "current"
        legacy = base / "legacy"
        registry = base / "registry.json"
        _release(old)
        _release(new)
        legacy.mkdir()
        current.symlink_to(old)
        env = {
            "PHONEFARM_RUNTIME_CURRENT_LINK": str(current),
            "PHONEFARM_RUNTIME_RELEASES_DIR": str(releases),
            "PHONEFARM_RUNTIME_HOME": str(base / "runtime"),
            "PHONEFARM_RUNTIME_LEGACY_ROOT": str(legacy),
            "PHONEFARM_CANONICAL_REGISTRY": str(registry),
            "PHONEFARM_PROMOTION_CONVERGENCE_TIMEOUT_SECONDS": "0",
        }
        return base, old, new, current, env

    def _run(self, raw: str, *, restarts=None, convergence=None, registry_effect=None, prepare_effect=None):
        base, old, new, current, env = self._layout(raw)
        transaction = mock.Mock()
        transaction.finish.return_value = "/archive/lock.json"
        evidence = {
            "ok": True,
            "candidate_sha": self.candidate_sha,
            "build": {"protected_diff_status": "PASS"},
            "migration": {"applied_versions": ["v1"]},
            "lineage": {"validated_migrations": [{"version": "v1"}]},
        }
        restart_results = restarts or [{"ok": True}, {"ok": True}, {"ok": True}]
        convergence = convergence or {
            "ok": True,
            "expected_root": str(new.resolve()),
            "expected_sha": self.candidate_sha,
            "services": {},
        }
        registry = mock.Mock(side_effect=registry_effect) if registry_effect else mock.Mock()
        prepared = {
            "ok": True,
            "status": "STARTUP_TOKEN_PREPARED",
            "token": {"device": 1, "inode": 2, "uid": 501, "gid": 20, "mode": 0o600},
        }
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(ctl, "_git_commit", return_value=self.candidate_sha[:7]), \
             mock.patch.object(ctl, "_git_full_commit", return_value=self.previous_sha), \
             mock.patch.object(ctl, "verify_deployment_candidate", return_value={"ok": True}), \
             mock.patch.object(ctl, "_validate_release_evidence", return_value=evidence), \
             mock.patch.object(ctl, "deployment_zero_gate", return_value=ZERO), \
             mock.patch.object(ctl, "_stage_receipt", side_effect=[base / "pending.stage", base / "final.stage"]) as stage, \
             mock.patch.object(ctl, "_publish_immutable_receipt") as publish, \
             mock.patch.object(ctl, "_receipt_sha256", return_value="c" * 64), \
             mock.patch.object(ctl, "_atomic_update_worker_registry", registry), \
             mock.patch.object(
                 ctl,
                 "prepare_startup_skip_token",
                 side_effect=prepare_effect,
                 return_value=prepared,
             ) as prepare, \
             mock.patch.object(ctl, "_run_canonical_controller", side_effect=restart_results) as restart, \
             mock.patch.object(ctl, "_wait_for_service_convergence", return_value=convergence):
            result = ctl._switch_release_locked(str(new), transaction)
        return result, current, old, new, transaction, stage, publish, restart, registry, prepare

    def test_all_services_converged_is_the_only_promoted_path(self):
        with tempfile.TemporaryDirectory() as raw:
            result, current, _, new, transaction, stage, publish, restart, registry, prepare = self._run(raw)
            resolved_current = current.resolve()
            resolved_new = new.resolve()
        self.assertTrue(result["ok"])
        self.assertEqual(result["receiptState"], ctl.PROMOTION_COMPLETE)
        self.assertEqual(resolved_current, resolved_new)
        self.assertEqual([call.args[:2] for call in restart.call_args_list], [
            ("dispatcher", "restart"),
            ("heartbeat", "restart"),
            ("notifier", "restart"),
        ])
        self.assertEqual(stage.call_count, 2)
        self.assertEqual(publish.call_count, 2)
        self.assertEqual(registry.call_count, 2)
        transaction.finish.assert_called_once()
        prepare.assert_called_once()

    def test_permission_setup_failure_blocks_before_active_root_switch(self):
        with tempfile.TemporaryDirectory() as raw:
            result, current, old, _, transaction, stage, publish, restart, registry, _ = self._run(
                raw,
                prepare_effect=ctl.StartupSkipTokenError("startup_token_owner_group_setup_failed"),
            )
            resolved_current = current.resolve()
            resolved_old = old.resolve()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "promotion_startup_token_prepare_failed")
        self.assertEqual(result["receiptState"], "SEALED")
        self.assertFalse(result["runtimeSwitched"])
        self.assertEqual(resolved_current, resolved_old)
        publish.assert_not_called()
        restart.assert_not_called()
        registry.assert_not_called()
        transaction.finish.assert_not_called()

    def test_convergence_requires_exact_consumption_proof_after_services_align(self):
        paths = ctl.RuntimePaths(Path("/current"), Path("/releases"), Path("/runtime"), Path("/legacy"))
        expected = {"device": 1, "inode": 2, "uid": 501, "gid": 20, "mode": 0o600}
        service_pass = {"ok": True, "status": "SERVICE_CONVERGENCE_PASS", "services": {}}
        with mock.patch.object(ctl, "canonical_service_snapshot", return_value=service_pass), \
             mock.patch.object(ctl, "verify_startup_skip_consumed", side_effect=[
                 {"ok": False, "status": "STARTUP_TOKEN_PENDING"},
                 {"ok": True, "status": "STARTUP_TOKEN_GATE_PASS"},
             ]) as verify, \
             mock.patch.object(ctl.time, "sleep"):
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

    def test_each_restart_failure_leaves_only_pending_state(self):
        for index, component in enumerate(ctl.PROMOTION_SERVICES):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as raw:
                restarts = [{"ok": True} for _ in ctl.PROMOTION_SERVICES]
                restarts[index] = {"ok": False, "reason": "fixture_failure"}
                result, _, _, _, transaction, stage, publish, restart, registry, _ = self._run(
                    raw, restarts=restarts
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["receiptState"], ctl.PROMOTION_PENDING)
            self.assertIn(f"{component}_restart_failed", result["status"])
            self.assertEqual(stage.call_count, 1)
            self.assertEqual(publish.call_count, 1)
            self.assertEqual(registry.call_count, 1)
            self.assertEqual(restart.call_count, index + 1)
            transaction.finish.assert_not_called()

    def test_old_root_or_duplicate_or_missing_service_never_promotes(self):
        for reason in ("dispatcher:cwd_old_or_unknown_root", "heartbeat:missing_service", "notifier:duplicate_service"):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as raw:
                result, _, _, _, transaction, stage, publish, _, _, _ = self._run(
                    raw,
                    convergence={
                        "ok": False,
                        "status": "SERVICE_CONVERGENCE_BLOCKED",
                        "old_root_process_count": 1 if "old_root" in reason else 0,
                        "failures": [reason],
                    },
                )
            self.assertEqual(result["receiptState"], ctl.PROMOTION_PENDING)
            self.assertEqual(stage.call_count, 1)
            self.assertEqual(publish.call_count, 1)
            transaction.finish.assert_not_called()

    def test_registry_failure_after_switch_is_pending_and_restarts_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            result, current, _, new, transaction, stage, publish, restart, registry, _ = self._run(
                raw, registry_effect=OSError("fixture registry failure")
            )
            resolved_current = current.resolve()
            resolved_new = new.resolve()
        self.assertFalse(result["ok"])
        self.assertEqual(result["receiptState"], ctl.PROMOTION_PENDING)
        self.assertEqual(resolved_current, resolved_new)
        self.assertEqual((stage.call_count, publish.call_count, registry.call_count), (1, 1, 1))
        restart.assert_not_called()
        transaction.finish.assert_not_called()

    def test_controller_crash_before_convergence_has_no_promoted_receipt(self):
        with tempfile.TemporaryDirectory() as raw:
            base, _, new, _, env = self._layout(raw)
            transaction = mock.Mock()
            evidence = {
                "ok": True,
                "candidate_sha": self.candidate_sha,
                "build": {"protected_diff_status": "PASS"},
                "migration": {"applied_versions": ["v1"]},
                "lineage": {"validated_migrations": [{"version": "v1"}]},
            }
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(ctl, "_git_commit", return_value=self.candidate_sha[:7]), \
                 mock.patch.object(ctl, "_git_full_commit", return_value=self.previous_sha), \
                 mock.patch.object(ctl, "verify_deployment_candidate", return_value={"ok": True}), \
                 mock.patch.object(ctl, "_validate_release_evidence", return_value=evidence), \
                 mock.patch.object(ctl, "deployment_zero_gate", return_value=ZERO), \
                 mock.patch.object(ctl, "_stage_receipt", return_value=base / "pending.stage") as stage, \
                 mock.patch.object(ctl, "_publish_immutable_receipt") as publish, \
                 mock.patch.object(ctl, "_receipt_sha256", return_value="c" * 64), \
                 mock.patch.object(ctl, "_atomic_update_worker_registry"), \
                 mock.patch.object(ctl, "prepare_startup_skip_token", return_value={"token": {"device": 1, "inode": 2, "uid": 501, "gid": 20, "mode": 0o600}}), \
                 mock.patch.object(ctl, "_run_canonical_controller", return_value={"ok": True}), \
                 mock.patch.object(ctl, "_wait_for_service_convergence", side_effect=RuntimeError("crash")):
                with self.assertRaisesRegex(RuntimeError, "crash"):
                    ctl._switch_release_locked(str(new), transaction)
            self.assertEqual(stage.call_count, 1)
            self.assertEqual(publish.call_count, 1)
            transaction.finish.assert_not_called()

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
        with mock.patch.object(ctl, "resolve_runtime_root", return_value=runtime_root), \
             mock.patch.object(ctl, "_ps_rows", return_value=rows), \
             mock.patch.object(ctl, "_pid_cwd", return_value=root), \
             mock.patch.object(ctl, "_resolved_process_module_root", return_value=root), \
             mock.patch.object(ctl, "_git_full_commit", return_value=sha):
            passed = ctl.canonical_service_snapshot(paths=paths, expected_root=root, expected_sha=sha)
        self.assertTrue(passed["ok"])
        self.assertEqual(passed["old_root_process_count"], 0)

        with mock.patch.object(ctl, "resolve_runtime_root", return_value=runtime_root), \
             mock.patch.object(ctl, "_ps_rows", return_value=rows + [rows[0]]), \
             mock.patch.object(ctl, "_pid_cwd", return_value=root), \
             mock.patch.object(ctl, "_resolved_process_module_root", return_value=root), \
             mock.patch.object(ctl, "_git_full_commit", return_value=sha):
            duplicate = ctl.canonical_service_snapshot(paths=paths, expected_root=root, expected_sha=sha)
        self.assertFalse(duplicate["ok"])
        self.assertIn("dispatcher:duplicate_service", duplicate["failures"])

    def test_restart_invokes_only_stable_controller_and_current_link(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            controller = base / "runtime/bin/phonefarm-runtimectl"
            controller.parent.mkdir(parents=True)
            controller.write_text("#!/bin/sh\n", encoding="utf-8")
            paths = ctl.RuntimePaths(
                base / "current", base / "releases", base / "runtime", base / "legacy"
            )
            completed = mock.Mock(
                returncode=0,
                stdout='{"ok": true, "status": "running"}\n',
                stderr="",
            )
            with mock.patch.object(ctl.subprocess, "run", return_value=completed) as run:
                result = ctl._run_canonical_controller(
                    "dispatcher", "restart", paths=paths
                )
        self.assertTrue(result["ok"])
        command = run.call_args.args[0]
        self.assertEqual(command, [str(controller), "dispatcher", "restart", "--json"])
        self.assertNotIn("releases", command[0])
        self.assertEqual(
            run.call_args.kwargs["env"]["PHONEFARM_RUNTIME_CURRENT_LINK"],
            str(paths.current_link),
        )

    def test_registry_update_is_compare_and_swap_and_preserves_other_components(self):
        with tempfile.TemporaryDirectory() as raw:
            registry = Path(raw) / "registry.json"
            payload = {
                "schema": "PHONE_FARM_CANONICAL_DELTA_REGISTRY_V1",
                "components": {
                    "worker": {"production": {"verification_state": "VERIFIED", "sha": self.previous_sha}},
                    "backend": {"production": {"verification_state": "VERIFIED", "sha": "d" * 40}},
                },
                "migrations": [{"id": "preserved"}],
            }
            registry.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            ctl._atomic_update_worker_registry(
                registry,
                expected_sha=self.previous_sha,
                release_sha=self.candidate_sha,
                runtime_root="/releases/candidate",
                receipt_path=Path("/receipts/pending.json"),
                receipt_sha256="e" * 64,
            )
            updated = json.loads(registry.read_text(encoding="utf-8"))
            self.assertEqual(updated["components"]["worker"]["production"]["sha"], self.candidate_sha)
            self.assertEqual(updated["components"]["backend"], payload["components"]["backend"])
            self.assertEqual(updated["migrations"], payload["migrations"])
            with self.assertRaisesRegex(ValueError, "compare_and_swap_mismatch"):
                ctl._atomic_update_worker_registry(
                    registry,
                    expected_sha=self.previous_sha,
                    release_sha="f" * 40,
                    runtime_root="/releases/wrong",
                    receipt_path=Path("/receipts/wrong.json"),
                    receipt_sha256="0" * 64,
                )

    def test_manifest_generator_requires_every_status_trust_protocol_file(self):
        root = Path(__file__).resolve().parents[1]
        script = root / "scripts/regenerate-follow60-mainline-lock-v3-1.py"
        spec = importlib.util.spec_from_file_location("regenerate_lock_fixture", script)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        scope = json.loads(
            (root / "docs/governance/FOLLOW60_MAINLINE_LOCK_V3_SCOPE.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(module.REQUIRED_STATUS_TRUST_SCOPE, set(status_trust.PROTOCOL_FILES))
        module.require_status_trust_scope(scope)
        for missing in sorted(module.REQUIRED_STATUS_TRUST_SCOPE):
            with self.subTest(missing=missing), self.assertRaisesRegex(
                SystemExit, "missing_status_trust_file_from_protected_scope"
            ):
                module.require_status_trust_scope([path for path in scope if path != missing])


if __name__ == "__main__":
    unittest.main()
