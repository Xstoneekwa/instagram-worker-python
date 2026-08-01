from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from worker_runtime_identity import (
    WorkerRuntimeIdentityError,
    resolve_worker_runtime_identity,
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


if __name__ == "__main__":
    unittest.main()
