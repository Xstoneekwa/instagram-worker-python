from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import TestCase, mock

import phonefarm_runtime_control as ctl


def _worker_release(root: Path, commit: str = "abc1234") -> None:
    (root / "scripts").mkdir(parents=True)
    for rel in (
        "account_run_request_consumer.py",
        "device_heartbeat_publisher.py",
        "scripts/run_control_dispatcher_service.sh",
        "scripts/device_heartbeat_service.sh",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# test\n", encoding="utf-8")
        path.chmod(0o755)


class PhoneFarmRuntimeControlTest(TestCase):
    def _env(self, tmp: Path, current: Path, releases: Path, legacy: Path) -> dict[str, str]:
        return {
            "PHONEFARM_RUNTIME_CURRENT_LINK": str(current),
            "PHONEFARM_RUNTIME_RELEASES_DIR": str(releases),
            "PHONEFARM_RUNTIME_HOME": str(tmp / "runtime"),
            "PHONEFARM_RUNTIME_LEGACY_ROOT": str(legacy),
        }

    def test_resolve_active_root_validates_release_and_commit(self) -> None:
        with self.subTest("valid release"):
            import tempfile

            with tempfile.TemporaryDirectory() as raw:
                tmp = Path(raw)
                releases = tmp / "releases"
                release = releases / "abc1234"
                legacy = tmp / "legacy"
                current = tmp / "current"
                _worker_release(release)
                legacy.mkdir()
                current.symlink_to(release)
                with mock.patch.dict(os.environ, self._env(tmp, current, releases, legacy), clear=False):
                    with mock.patch.object(ctl, "_git_commit", return_value="abc1234"):
                        root = ctl.resolve_runtime_root()
                self.assertTrue(root.ok)
                self.assertEqual(root.commit, "abc1234")
                self.assertEqual(Path(root.resolved_root), release.resolve())

    def test_git_commit_uses_process_scoped_exact_canonical_safe_directory(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            release = Path(raw) / "releases" / "locked"
            release.mkdir(parents=True)
            alias = Path(raw) / "release-alias"
            alias.symlink_to(release)
            completed = mock.Mock(returncode=0, stdout="746dea5\n")
            with mock.patch.object(ctl.subprocess, "run", return_value=completed) as run:
                commit = ctl._git_commit(alias)

        self.assertEqual(commit, "746dea5")
        command = run.call_args.args[0]
        self.assertEqual(
            command,
            [
                "git",
                "-c",
                f"safe.directory={release.resolve()}",
                "-C",
                str(release.resolve()),
                "rev-parse",
                "--short",
                "HEAD",
            ],
        )
        self.assertNotIn("--global", command)
        self.assertNotIn("*", " ".join(command))
        self.assertNotIn("env", run.call_args.kwargs)

    def test_git_commit_missing_root_fails_closed_without_invoking_git(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            missing = Path(raw) / "missing-release"
            with mock.patch.object(ctl.subprocess, "run") as run:
                commit = ctl._git_commit(missing)

        self.assertEqual(commit, "")
        run.assert_not_called()

    def test_resolve_active_root_rejects_missing_and_legacy(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            releases = tmp / "releases"
            releases.mkdir()
            legacy = tmp / "legacy"
            _worker_release(legacy)
            current = tmp / "current"
            with mock.patch.dict(os.environ, self._env(tmp, current, releases, legacy), clear=False):
                missing = ctl.resolve_runtime_root()
            self.assertFalse(missing.ok)
            self.assertEqual(missing.reason, "active_root_missing")

            current.symlink_to(legacy)
            with mock.patch.dict(os.environ, self._env(tmp, current, releases, legacy), clear=False):
                with mock.patch.object(ctl, "_git_commit") as git_commit:
                    rejected = ctl.resolve_runtime_root()
            self.assertFalse(rejected.ok)
            self.assertEqual(rejected.reason, "active_root_not_in_releases_dir")
            git_commit.assert_not_called()

    def test_switch_release_atomically_updates_current_link(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            releases = tmp / "releases"
            old = releases / "old"
            new = releases / "new"
            legacy = tmp / "legacy"
            current = tmp / "current"
            _worker_release(old)
            _worker_release(new)
            legacy.mkdir()
            current.symlink_to(old)
            with mock.patch.dict(os.environ, self._env(tmp, current, releases, legacy), clear=False):
                with mock.patch.object(ctl, "_git_commit", return_value="newsha"):
                    with mock.patch.object(
                        ctl, "deployment_zero_gate", return_value={"ok": True}
                    ):
                        result = ctl.switch_release("new")
            self.assertTrue(result["ok"])
            self.assertEqual(current.resolve(), new.resolve())
            self.assertEqual(result["previousRoot"], str(old.resolve()))

    def test_switch_release_refuses_non_zero_production_gate(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            releases = tmp / "releases"
            old = releases / "old"
            new = releases / "new"
            legacy = tmp / "legacy"
            current = tmp / "current"
            _worker_release(old)
            _worker_release(new)
            legacy.mkdir()
            current.symlink_to(old)
            with mock.patch.dict(
                os.environ, self._env(tmp, current, releases, legacy), clear=False
            ):
                with mock.patch.object(
                    ctl,
                    "deployment_zero_gate",
                    return_value={
                        "ok": False,
                        "status": "deployment_gate_blocked",
                        "reason": "active_runtime_work_present",
                        "counts": {"account_run_requests": 1},
                    },
                ):
                    result = ctl.switch_release("new")
            self.assertFalse(result["ok"])
            self.assertEqual(current.resolve(), old.resolve())

    def test_deployment_zero_gate_is_fail_closed_and_generic(self) -> None:
        with mock.patch.object(
            ctl,
            "_runtime_secret_env",
            return_value={"SUPABASE_URL": "https://example.test", "SUPABASE_SERVICE_ROLE_KEY": "key"},
        ), mock.patch.object(ctl, "_rest_select_ids", return_value=[]) as rest:
            result = ctl.deployment_zero_gate()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(rest.call_count, 4)
        selected = {
            call.kwargs["table"]: call.kwargs["identity_column"]
            for call in rest.call_args_list
        }
        self.assertEqual(selected["account_run_requests"], "id")
        self.assertEqual(selected["ig_runs"], "id")
        self.assertEqual(selected["auto_restart_device_locks"], "device_id")
        self.assertEqual(selected["auto_restart_tick_locks"], "idempotency_key")

        with mock.patch.object(
            ctl,
            "_runtime_secret_env",
            return_value={"SUPABASE_URL": "https://example.test", "SUPABASE_SERVICE_ROLE_KEY": "key"},
        ), mock.patch.object(
            ctl, "_rest_select_ids", side_effect=[[{"id": "active"}], [], [], []]
        ):
            blocked = ctl.deployment_zero_gate()
        self.assertFalse(blocked["ok"])
        self.assertIn("account_run_requests", blocked["blockers"])

    def test_runtime_root_mismatch_is_reported_for_foreign_process(self) -> None:
        root = ctl.RuntimeRoot(True, "valid", "/tmp/current", "/tmp/releases/current", "abc1234")
        payload = {"ok": True, "status": "starting", "processRunning": False}
        with mock.patch.object(ctl, "_ps_rows", return_value=[(123, 1, "python account_run_request_consumer.py")]):
            with mock.patch.object(ctl, "_pid_cwd", return_value="/tmp/other-release"):
                result = ctl._detect_component_mismatch("dispatcher", payload, root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "runtime_root_mismatch")

    def test_running_heartbeat_cannot_hide_foreign_release_process(self) -> None:
        root = ctl.RuntimeRoot(True, "valid", "/tmp/current", "/tmp/releases/current", "abc1234")
        payload = {
            "ok": True,
            "status": "running",
            "processRunning": True,
            "pid": 96246,
            "processCount": 1,
        }
        with mock.patch.object(
            ctl,
            "_ps_rows",
            return_value=[(96246, 96223, "python /tmp/releases/old/device_heartbeat_publisher.py --serve")],
        ):
            with mock.patch.object(ctl, "_pid_cwd", return_value="/tmp/releases/old"):
                result = ctl._detect_component_mismatch("heartbeat", payload, root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "runtime_root_mismatch")
        self.assertEqual(result["lastError"], "service_running_from_non_active_root")
        self.assertEqual(result["processes"][0]["pid"], 96246)

    def _make_valid_runtime(self, tmp: Path) -> dict[str, Path]:
        releases = tmp / "releases"
        release = releases / "abc1234"
        legacy = tmp / "legacy"
        current = tmp / "current"
        _worker_release(release)
        legacy.mkdir()
        current.symlink_to(release)
        return {"tmp": tmp, "releases": releases, "release": release, "legacy": legacy, "current": current}

    def test_serve_execs_wrapper_without_timeout_parent(self) -> None:
        import tempfile

        for component, wrapper_name in (
            ("dispatcher", "run_control_dispatcher_service.sh"),
            ("heartbeat", "device_heartbeat_service.sh"),
        ):
            with self.subTest(component=component):
                with tempfile.TemporaryDirectory() as raw:
                    layout = self._make_valid_runtime(Path(raw))
                    env_patch = self._env(layout["tmp"], layout["current"], layout["releases"], layout["legacy"])
                    with mock.patch.dict(os.environ, env_patch, clear=False):
                        with mock.patch.object(ctl, "_git_commit", return_value="abc1234"):
                            with mock.patch.object(ctl.os, "execve") as execve:
                                with mock.patch.object(ctl.subprocess, "run") as sub_run:
                                    with mock.patch.object(ctl.os, "chdir") as chdir:
                                        ctl.serve_component(component)
                # exec real du wrapper : pas de subprocess parent avec timeout.
                sub_run.assert_not_called()
                execve.assert_called_once()
                argv0, argv, env = execve.call_args.args
                self.assertTrue(str(argv0).endswith(f"scripts/{wrapper_name}"))
                self.assertEqual(argv[1], "start")
                self.assertEqual(env["PHONEFARM_ACTIVE_COMMIT"], "abc1234")
                self.assertEqual(env["PHONEFARM_ACTIVE_ROOT"], str(layout["release"].resolve()))
                chdir.assert_called_once_with(str(layout["release"].resolve()))

    def test_serve_refuses_invalid_root_without_exec(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            releases = tmp / "releases"
            releases.mkdir()
            legacy = tmp / "legacy"
            legacy.mkdir()
            current = tmp / "current"  # lien absent
            with mock.patch.dict(os.environ, self._env(tmp, current, releases, legacy), clear=False):
                with mock.patch.object(ctl.os, "execve") as execve:
                    code = ctl.serve_component("dispatcher")
        self.assertEqual(code, 2)
        execve.assert_not_called()

    def test_run_wrapper_rejects_long_lived_commands(self) -> None:
        result = ctl._run_wrapper("dispatcher", "serve", [])
        self.assertFalse(result["ok"])
        self.assertEqual(result["lastError"], "long_lived_command_requires_exec")

    def test_control_start_is_idempotent_when_running(self) -> None:
        running = {"ok": True, "status": "running", "service_state": "running", "processRunning": True, "pid": 4242}
        with mock.patch.object(ctl, "_run_wrapper", return_value=running) as run_wrapper:
            with mock.patch.object(ctl, "_launchctl_kickstart") as kickstart:
                with mock.patch.object(ctl.os, "execve") as execve:
                    result = ctl.control_start("dispatcher")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "running")
        self.assertIn("already_running", result["message"])
        # start court : un seul status lecture seule, aucun kickstart, aucun exec.
        run_wrapper.assert_called_once_with("dispatcher", "status", [], timeout=20)
        kickstart.assert_not_called()
        execve.assert_not_called()

    def test_control_start_safe_recovery_when_absence_is_proven(self) -> None:
        stopped = {"ok": True, "status": "stopped", "service_state": "stopped", "processRunning": False, "pid": None}
        starting = {"ok": True, "status": "starting", "service_state": "starting", "processRunning": False}
        armed = {"ok": True, "status": "unknown"}
        resumed = {"ok": True, "status": "starting"}
        with mock.patch.object(ctl, "_run_wrapper", side_effect=[stopped, stopped, armed, resumed, starting]) as run_wrapper, \
             mock.patch.object(ctl, "_active_worker_children", return_value=[]), \
             mock.patch.object(ctl, "deployment_zero_gate", return_value={"ok": True}), \
             mock.patch.object(ctl, "_acquire_autoheal_lock", return_value=(Path("/tmp/test-lock"), True)), \
             mock.patch.object(ctl, "_release_autoheal_lock"):
            result = ctl.control_start("dispatcher")
        self.assertTrue(result["ok"])
        self.assertEqual(result["recoveryAction"], "safe_resume")
        self.assertTrue(result["startupTickSkipArmed"])
        commands = [call.args[1] for call in run_wrapper.call_args_list]
        self.assertEqual(commands, ["status", "status", "prepare-auto-restart-startup-skip", "resume", "status"])

    def test_control_start_preserves_non_dispatcher_kickstart_contract(self) -> None:
        stopped = {"ok": True, "status": "stopped", "processRunning": False, "pid": None}
        running = {"ok": True, "status": "running", "processRunning": True, "pid": 42}
        with mock.patch.object(ctl, "_run_wrapper", side_effect=[stopped, running]) as run_wrapper, \
             mock.patch.object(ctl, "_launchctl_kickstart", return_value=(True, "")) as kickstart:
            result = ctl.control_start("heartbeat")
        self.assertTrue(result["launchdKickstart"])
        kickstart.assert_called_once_with("com.boost.phonefarm.device-heartbeat")
        self.assertEqual([call.args[1] for call in run_wrapper.call_args_list], ["status", "status"])

    def test_timeout_unknown_never_mutates(self) -> None:
        unknown = {
            "ok": False,
            "status": "unknown",
            "service_state": "unknown",
            "service_health": "degraded",
            "processRunning": None,
            "launchdLoaded": None,
            "lastError": "dispatcher_command_timeout",
        }
        with mock.patch.object(ctl, "_run_wrapper", return_value=unknown), \
             mock.patch.object(ctl, "_acquire_autoheal_lock") as acquire, \
             mock.patch.object(ctl, "deployment_zero_gate") as gate:
            result = ctl.control_start("dispatcher")
        self.assertEqual(result["recoveryAction"], "none")
        acquire.assert_not_called()
        gate.assert_not_called()

    def test_active_child_blocks_recovery_before_any_resume(self) -> None:
        stopped = {"ok": True, "status": "stopped", "service_state": "stopped", "processRunning": False}
        with mock.patch.object(ctl, "_run_wrapper", side_effect=[stopped, stopped]) as run_wrapper, \
             mock.patch.object(ctl, "_active_worker_children", return_value=[{"pid": 99}]), \
             mock.patch.object(ctl, "_acquire_autoheal_lock", return_value=(Path("/tmp/test-lock"), True)), \
             mock.patch.object(ctl, "_release_autoheal_lock"), \
             mock.patch.object(ctl, "deployment_zero_gate") as gate:
            result = ctl.control_start("dispatcher")
        self.assertEqual(result["recoveryAction"], "none")
        self.assertIn("active_child", result["message"])
        self.assertEqual([call.args[1] for call in run_wrapper.call_args_list], ["status", "status"])
        gate.assert_not_called()

    def test_concurrent_recovery_lock_fails_closed(self) -> None:
        stopped = {"ok": True, "status": "stopped", "service_state": "stopped", "processRunning": False}
        with mock.patch.object(ctl, "_run_wrapper", return_value=stopped) as run_wrapper, \
             mock.patch.object(ctl, "_acquire_autoheal_lock", return_value=(Path("/tmp/test-lock"), False)):
            result = ctl.control_start("dispatcher")
        self.assertEqual(result["recoveryAction"], "none")
        self.assertIn("already_in_progress", result["message"])
        run_wrapper.assert_called_once()

    def test_dead_autoheal_lock_from_prior_boot_is_reclaimed(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw) / "run"
            paths = mock.Mock(run_dir=run_dir)
            lock_dir = run_dir / "dispatcher-autoheal.lock"
            lock_dir.mkdir(parents=True)
            (lock_dir / "owner.json").write_text(
                json.dumps({"pid": 999999, "created_epoch": 999999999999.0}),
                encoding="utf-8",
            )
            with mock.patch.object(ctl, "_pid_alive", return_value=False):
                acquired_dir, acquired = ctl._acquire_autoheal_lock(paths)
            self.assertTrue(acquired)
            self.assertEqual(acquired_dir, lock_dir)
            owner = json.loads((lock_dir / "owner.json").read_text(encoding="utf-8"))
            self.assertEqual(owner["pid"], os.getpid())
            ctl._release_autoheal_lock(lock_dir)

    def test_status_stays_read_only(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            layout = self._make_valid_runtime(Path(raw))
            wrapper = layout["release"] / "scripts" / "run_control_dispatcher_service.sh"
            wrapper.write_text('#!/bin/bash\necho \'{"ok": true, "status": "stopped", "processRunning": false}\'\n', encoding="utf-8")
            wrapper.chmod(0o755)
            env_patch = self._env(layout["tmp"], layout["current"], layout["releases"], layout["legacy"])
            with mock.patch.dict(os.environ, env_patch, clear=False):
                with mock.patch.object(ctl, "_git_commit", return_value="abc1234"):
                    with mock.patch.object(ctl, "_ps_rows", return_value=[]):
                        with mock.patch.object(ctl, "_launchctl_kickstart") as kickstart:
                            with mock.patch.object(ctl.os, "execve") as execve:
                                result = ctl._run_wrapper("dispatcher", "status", [])
        self.assertEqual(result["status"], "stopped")
        kickstart.assert_not_called()
        execve.assert_not_called()

    def test_scheduler_status_reports_backend_disabled_reason(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            releases = tmp / "releases"
            release = releases / "abc1234"
            legacy = tmp / "legacy"
            current = tmp / "current"
            log_dir = tmp / "runtime" / "logs" / "run-control-dispatcher"
            _worker_release(release)
            legacy.mkdir()
            current.symlink_to(release)
            log_dir.mkdir(parents=True)
            log_dir.joinpath("dispatcher.log").write_text(
                json.dumps({
                    "event": "auto_restart_dispatcher_tick_completed",
                    "reason": "scheduler_disabled",
                    "evaluated_count": 0,
                    "eligible_count": 0,
                }),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, self._env(tmp, current, releases, legacy), clear=False):
                with mock.patch.object(ctl, "_git_commit", return_value="abc1234"):
                    status = ctl.scheduler_status()
            self.assertTrue(status["ok"])
            self.assertEqual(status["status"], "disabled_by_config")
            self.assertTrue(status["embedded"])
