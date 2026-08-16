from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worker_runtime_identity import (
    RUNTIME_IDENTITY_ENV,
    WorkerRuntimeIdentity,
    WorkerRuntimeIdentityError,
    _git,
    bind_worker_runtime_identity,
    resolve_worker_runtime_identity,
    validate_worker_runtime_identity_binding,
    worker_runtime_identity_env,
)


class WorkerRuntimeIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        (self.root / "worker.py").write_text("ok = True\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "worker.py"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "fixture"], check=True)
        self.head = subprocess.check_output(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True
        ).strip()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_environment_uses_release_head(self) -> None:
        identity = resolve_worker_runtime_identity(self.root, environ={})
        self.assertEqual(identity.worker_sha, self.head)
        self.assertEqual(identity.source, "runtime_release_head")

    def test_matching_environment_is_only_corroborating(self) -> None:
        identity = resolve_worker_runtime_identity(
            self.root,
            environ={"WORKER_RUNTIME_ROOT": str(self.root), "WORKER_GIT_SHA": self.head},
        )
        self.assertEqual(identity.source, "env_verified_against_release_head")

    def test_mismatching_sha_fails_closed(self) -> None:
        with self.assertRaisesRegex(WorkerRuntimeIdentityError, "declared_sha_mismatch"):
            resolve_worker_runtime_identity(self.root, environ={"WORKER_GIT_SHA": "0" * 40})

    def test_nested_root_is_rejected(self) -> None:
        nested = self.root / "nested"
        nested.mkdir()
        with self.assertRaisesRegex(WorkerRuntimeIdentityError, "root_mismatch"):
            resolve_worker_runtime_identity(nested, environ={})

    @mock.patch("worker_runtime_identity.subprocess.run")
    def test_git_uses_process_scoped_exact_canonical_safe_directory(
        self,
        run_mock: mock.Mock,
    ) -> None:
        alias = self.root / "runtime-alias"
        alias.symlink_to(self.root)
        command = [
            "git",
            "-c",
            f"safe.directory={self.root.resolve()}",
            "-C",
            str(self.root.resolve()),
            "rev-parse",
            "HEAD",
        ]
        run_mock.return_value = subprocess.CompletedProcess(
            command, 0, stdout=f"{self.head}\n", stderr=""
        )

        self.assertEqual(_git(alias, "rev-parse", "HEAD"), self.head)
        self.assertEqual(run_mock.call_args.args[0], command)
        self.assertNotIn("--global", command)
        self.assertNotIn("*", " ".join(command))
        self.assertNotIn("env", run_mock.call_args.kwargs)

    @mock.patch("worker_runtime_identity.subprocess.run")
    def test_git_missing_root_fails_closed_without_invoking_git(
        self,
        run_mock: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(
            WorkerRuntimeIdentityError,
            "worker_runtime_git_identity_unavailable",
        ):
            _git(self.root / "missing", "rev-parse", "HEAD")
        run_mock.assert_not_called()

    @mock.patch("worker_runtime_identity.time.sleep")
    @mock.patch("worker_runtime_identity.subprocess.run")
    def test_transient_git_timeout_is_retried_without_weakening_identity(
        self,
        run_mock: mock.Mock,
        sleep_mock: mock.Mock,
    ) -> None:
        command = ["git", "-C", str(self.root), "rev-parse", "HEAD"]
        run_mock.side_effect = [
            subprocess.TimeoutExpired(command, 5.0),
            subprocess.CompletedProcess(command, 0, stdout=f"{self.head}\n", stderr=""),
        ]

        self.assertEqual(_git(self.root, "rev-parse", "HEAD"), self.head)
        self.assertEqual(run_mock.call_count, 2)
        sleep_mock.assert_called_once_with(0.2)

    @mock.patch("worker_runtime_identity.time.sleep")
    @mock.patch("worker_runtime_identity.subprocess.run")
    def test_persistent_git_timeout_still_fails_closed(
        self,
        run_mock: mock.Mock,
        sleep_mock: mock.Mock,
    ) -> None:
        command = ["git", "-C", str(self.root), "rev-parse", "HEAD"]
        run_mock.side_effect = subprocess.TimeoutExpired(command, 5.0)

        with self.assertRaises(WorkerRuntimeIdentityError) as caught:
            _git(self.root, "rev-parse", "HEAD")

        self.assertEqual(caught.exception.reason, "worker_runtime_git_identity_unavailable")
        self.assertEqual(caught.exception.stage, "git_identity_timeout")
        self.assertEqual(caught.exception.diagnostics["attempts"], 3)
        self.assertEqual(run_mock.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_transport_round_trip_preserves_canonical_binding(self) -> None:
        original = resolve_worker_runtime_identity(
            self.root,
            environ={"WORKER_RUNTIME_WRAPPER_PID": "123"},
        )
        bound = bind_worker_runtime_identity(
            original,
            request_id="66f92055-21ee-4a7c-a042-3a857d3f8448",
            run_id="9bde8e78-7955-46d5-bd29-044548d2a911",
            attempt_id=2,
            consumer_pid=456,
        )
        transport_env = worker_runtime_identity_env(bound)
        restored = resolve_worker_runtime_identity(self.root, environ=transport_env)
        self.assertEqual(restored.worker_sha, self.head)
        self.assertEqual(restored.release_head, self.head)
        self.assertEqual(restored.wrapper_pid, 123)
        self.assertEqual(restored.consumer_pid, 456)
        self.assertEqual(restored.request_id, bound.request_id)
        self.assertEqual(restored.run_id, bound.run_id)
        self.assertEqual(restored.attempt_id, 2)
        self.assertEqual(restored.verified_at, original.verified_at)
        self.assertEqual(restored.source, "transport_verified_against_release_head")

    def test_transport_sha_mismatch_has_exact_taxonomy(self) -> None:
        identity = resolve_worker_runtime_identity(self.root, environ={})
        env = worker_runtime_identity_env(identity)
        payload = json.loads(env[RUNTIME_IDENTITY_ENV])
        payload["worker_sha"] = "0" * 40
        env[RUNTIME_IDENTITY_ENV] = json.dumps(payload)
        with self.assertRaises(WorkerRuntimeIdentityError) as caught:
            resolve_worker_runtime_identity(self.root, environ=env)
        self.assertEqual(caught.exception.reason, "env_head_mismatch")
        self.assertEqual(caught.exception.stage, "transport_verify")

    def test_transport_missing_release_head_fails_closed(self) -> None:
        identity = resolve_worker_runtime_identity(self.root, environ={})
        env = worker_runtime_identity_env(identity)
        payload = json.loads(env[RUNTIME_IDENTITY_ENV])
        payload["release_head"] = ""
        env[RUNTIME_IDENTITY_ENV] = json.dumps(payload)
        with self.assertRaises(WorkerRuntimeIdentityError) as caught:
            resolve_worker_runtime_identity(self.root, environ=env)
        self.assertEqual(caught.exception.reason, "release_head_unresolved")

    def test_binding_rejects_identity_without_worker_sha(self) -> None:
        identity = WorkerRuntimeIdentity(
            runtime_root=str(self.root),
            worker_sha="",
            source="test",
            release_path=str(self.root),
            release_head=self.head,
        )
        reason, _ = validate_worker_runtime_identity_binding(
            identity,
            request_id="request",
            run_id="run",
            attempt_id=1,
        )
        self.assertEqual(reason, "worker_sha_env_missing")


if __name__ == "__main__":
    unittest.main()
