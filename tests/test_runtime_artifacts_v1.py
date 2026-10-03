from __future__ import annotations

import os
import ast
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import runtime_artifacts as artifacts


class RuntimeArtifactsV1Tests(unittest.TestCase):
    def test_canonical_root_precedence_and_safe_components(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with mock.patch.dict(
                os.environ,
                {
                    "PHONEFARM_RUNTIME_ROOT": str(root / "explicit-root"),
                    "PHONEFARM_RUNTIME_HOME": str(root / "home-fallback"),
                },
                clear=False,
            ):
                self.assertEqual(
                    artifacts.runtime_artifact_path("screenshots", "proof.png"),
                    (root / "explicit-root/logs/screenshots/proof.png").resolve(),
                )
                self.assertEqual(
                    artifacts.deployment_evidence_dir("a" * 40),
                    (root / "explicit-root/deployment-evidence" / ("a" * 40)).resolve(),
                )
                self.assertEqual(
                    artifacts.historical_auto_login_runtime_root(),
                    (root / "explicit-root/historical-auto-login").resolve(),
                )

    def test_traversal_absolute_and_ambiguous_components_fail_closed(self) -> None:
        for parts in ((), ("",), ("..",), ("a/b",), ("/absolute",), (".",)):
            with self.subTest(parts=parts), self.assertRaises(ValueError):
                artifacts.runtime_artifact_path(*parts)
        for sha in ("", "a" * 39, "g" * 40, "../" + "a" * 40):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                artifacts.deployment_evidence_dir(sha)

    def test_runtime_home_inside_declared_release_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            release = Path(raw) / "release"
            with mock.patch.dict(
                os.environ,
                {
                    "WORKER_RUNTIME_ROOT": str(release),
                    "PHONEFARM_RUNTIME_ROOT": str(release / "logs"),
                },
                clear=False,
            ), self.assertRaisesRegex(
                RuntimeError, "runtime_home_inside_immutable_release"
            ):
                artifacts.runtime_home()

    def test_sha_bound_external_certification_precedes_legacy_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            release = base / "release"
            governance = release / "docs/governance"
            governance.mkdir(parents=True)
            local_manifest = governance / artifacts.FOLLOW60_MANIFEST_NAME
            local_signature = governance / artifacts.FOLLOW60_SIGNATURE_NAME
            local_manifest.write_text("legacy", encoding="utf-8")
            local_signature.write_bytes(b"legacy")
            runtime = base / "runtime"
            sha = "b" * 40
            with mock.patch.dict(
                os.environ,
                {"PHONEFARM_RUNTIME_ROOT": "", "PHONEFARM_RUNTIME_HOME": str(runtime)},
                clear=False,
            ), mock.patch.object(artifacts, "git_commit_sha", return_value=sha):
                evidence = runtime / "deployment-evidence" / sha
                evidence.mkdir(parents=True)
                external_manifest = evidence / artifacts.FOLLOW60_MANIFEST_NAME
                external_signature = evidence / artifacts.FOLLOW60_SIGNATURE_NAME
                external_manifest.write_text("external", encoding="utf-8")
                external_signature.write_bytes(b"external")
                self.assertEqual(
                    artifacts.follow60_certification_paths(release),
                    (external_manifest.resolve(), external_signature.resolve()),
                )
                external_signature.unlink()
                self.assertEqual(
                    artifacts.follow60_certification_paths(release),
                    (external_manifest.resolve(), external_signature.resolve()),
                )

    def test_signed_context_generation_selects_exact_subnamespace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw); release = base / "release"; release.mkdir()
            runtime = base / "runtime"; sha = "c" * 40; generation = "authority-renewal-v3"
            evidence = runtime / "deployment-evidence" / sha / generation
            evidence.mkdir(parents=True)
            (evidence / artifacts.FOLLOW60_MANIFEST_NAME).write_text("manifest")
            (evidence / artifacts.FOLLOW60_SIGNATURE_NAME).write_bytes(b"sig")
            with mock.patch.dict(os.environ, {"PHONEFARM_RUNTIME_HOME": str(runtime), "PHONEFARM_FOLLOW60_GENERATION": generation}, clear=False), mock.patch.object(artifacts, "git_commit_sha", return_value=sha):
                self.assertEqual(artifacts.follow60_certification_paths(release), (evidence / artifacts.FOLLOW60_MANIFEST_NAME, evidence / artifacts.FOLLOW60_SIGNATURE_NAME))

    def test_generation_missing_incomplete_and_unsafe_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw); release = base / "release"; release.mkdir(); runtime = base / "runtime"; sha = "d" * 40
            with mock.patch.dict(os.environ, {"PHONEFARM_RUNTIME_HOME": str(runtime), "PHONEFARM_FOLLOW60_GENERATION": "missing"}, clear=False), mock.patch.object(artifacts, "git_commit_sha", return_value=sha), self.assertRaisesRegex(RuntimeError, "generation_missing"):
                artifacts.follow60_certification_paths(release)
            with mock.patch.dict(os.environ, {"PHONEFARM_RUNTIME_HOME": str(runtime), "PHONEFARM_FOLLOW60_GENERATION": "../escape"}, clear=False), mock.patch.object(artifacts, "git_commit_sha", return_value=sha), self.assertRaisesRegex(RuntimeError, "selector_invalid"):
                artifacts.follow60_certification_paths(release)

    def test_named_runtime_modules_have_no_release_relative_log_root(self) -> None:
        root = Path(__file__).resolve().parents[1]
        names = (
            "instagram_navigation.py",
            "runner.py",
            "account_identity_guard.py",
            "instagram_action_restriction.py",
            "visual_follow_history.py",
            "instagram_login_provisioner_cli.py",
            "tail_only.py",
            "account_run_request_consumer.py",
        )
        forbidden = (
            'Path(__file__).resolve().parent / "logs"',
            'os.path.dirname(os.path.abspath(__file__)), "logs"',
            'Path("/tmp/phonefarm-follow60-stop-traces")',
        )
        for name in names:
            source = (root / name).read_text(encoding="utf-8")
            for marker in forbidden:
                with self.subTest(file=name, marker=marker):
                    self.assertNotIn(marker, source)

        adapter = (root / "historical_auto_login_07ee_adapter.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("historical_auto_login_runtime_root", adapter)
        historical_names = (
            "historical_auto_login_07ee/logs.py",
            "historical_auto_login_07ee/device.py",
            "historical_auto_login_07ee/instagram_navigation.py",
            "historical_auto_login_07ee/instagram_login_provisioner_cli.py",
            "historical_auto_login_07ee/config.py",
        )
        for name in historical_names:
            source = (root / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertIn(
                    "PHONEFARM_HISTORICAL_AUTO_LOGIN_RUNTIME_ROOT", source
                )
                self.assertNotIn(
                    'Path(__file__).resolve().parent / "logs"', source
                )

    def test_service_wrapper_defaults_are_external_and_disable_bytecode(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for name in (
            "scripts/run_control_dispatcher_service.sh",
            "scripts/device_heartbeat_service.sh",
            "scripts/incident_notifier_service.sh",
        ):
            source = (root / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertIn('RUNTIME_HOME="${PHONEFARM_RUNTIME_HOME:-/Users/admin/phonefarm-runtime}"', source)
                self.assertIn("export PYTHONDONTWRITEBYTECODE=1", source)
                self.assertIn("runtime_home_inside_immutable_release", source)
                self.assertIn('cd "$RUNTIME_HOME_PHYSICAL"', source)
                self.assertNotIn(':-$ROOT_DIR/logs/', source)
                self.assertNotIn(':-$ROOT_DIR/.local/', source)

        consumer = (root / "account_run_request_consumer.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("runner_workdir = runtime_home()", consumer)
        calls = [node for node in ast.walk(ast.parse(consumer))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == "_fleet_spawn_or_legacy"]
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(ast.dump(call.args[2]), ast.dump(ast.Name(id="runner_workdir", ctx=ast.Load())))

        # Exercise the current spawn boundary in both isolation modes. The callback
        # must carry the external workdir through to Popen, not merely mention it.
        import account_run_request_consumer as consumer_module
        with tempfile.TemporaryDirectory() as raw:
            workdir = Path(raw).resolve()
            command = ["synthetic-runner"]
            environment = {"fixture": "isolated"}
            binding = {"request_id": "fixture-request"}
            child = object()
            session = mock.Mock()
            session.environment.return_value = {"fleet": "scoped"}
            session.spawn.side_effect = lambda spawn: spawn()
            for active in (False, True):
                with self.subTest(fleet=active), mock.patch.object(
                    consumer_module, "_FLEET_EXECUTIONS", {"fixture-request": session} if active else {}
                ), mock.patch.object(consumer_module.subprocess, "Popen", return_value=child) as spawn, mock.patch(
                    "fleet_runtime_barrier.fleet_spawn_isolated_v1", side_effect=lambda callback, **kw: callback()
                ) as isolated, mock.patch.object(consumer_module, "runtime_artifact_path", return_value=workdir / "isolation"):
                    self.assertIs(consumer_module._fleet_spawn_or_legacy(
                        "fixture-request", command, workdir, environment, launch_binding=binding), child)
                    spawn.assert_called_once_with(command, cwd=str(workdir),
                        env={**environment, **({"fleet": "scoped"} if active else {})}, start_new_session=True)
                    self.assertFalse(workdir.is_relative_to(root))
                    if active:
                        isolated.assert_not_called()
                        session.register_child.assert_called_once_with(child)
                    else:
                        self.assertEqual(isolated.call_args.kwargs["binding"], binding)
                        self.assertEqual(isolated.call_args.kwargs["root"], workdir / "isolation")

        config_source = (root / "config.py").read_text(encoding="utf-8")
        live_view_source = (root / "live_view_host_agent_core.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('str(runtime_home() / "live-view-frames")', config_source)
        self.assertIn('str(runtime_home() / "live-view-frames")', live_view_source)


if __name__ == "__main__":
    unittest.main()
