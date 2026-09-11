from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import storage_protection_registry_v3 as storage


class StorageProtectionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.releases = base / "releases"
        self.projects = base / "projects"
        self.releases.mkdir()
        self.projects.mkdir()
        self.active = self.releases / "active"
        self.rollback = self.releases / "rollback"
        self.controller = self.releases / "controller"
        for item in (self.active, self.rollback, self.controller):
            item.mkdir()
        self.current = base / "current"
        self.current.symlink_to(self.active)
        (self.controller / "phonefarm_runtime_control.py").write_text("# controller\n")
        self.controller_link = base / "stable-controller"
        self.controller_link.symlink_to(self.controller / "phonefarm_runtime_control.py")
        self.registry = base / "canonical.json"
        self.registry.write_text(json.dumps({
            "components": {
                "worker": {
                    "production": {"sha": "a" * 40, "runtime_root": str(self.active)},
                    "rollback": {"sha": "b" * 40, "runtime_root": str(self.rollback)},
                }
            }
        }))

    def identity(self, path):
        path = Path(path).resolve()
        values = {self.active.resolve(): "a" * 40, self.rollback.resolve(): "b" * 40, self.controller.resolve(): "c" * 40}
        return {"path": str(path), "sha": values[path]}

    def bootstrap(self):
        with (
            mock.patch.object(storage, "_release_identity", side_effect=self.identity),
            mock.patch.object(storage, "discover_pending_approvals", return_value=[{"path": "/candidate/pending", "reason": "APPROVAL_FREEZE_PROTECTED"}]),
            mock.patch.object(storage, "discover_parallel_worktrees", return_value=[{"path": "/parallel/task", "reason": "PARALLEL_TASK_PROTECTED"}]),
        ):
            return storage.bootstrap_registry(
                current_link=self.current,
                releases_root=self.releases,
                stable_controller_link=self.controller_link,
                canonical_registry=self.registry,
                storage_registry=None,
                projects_root=self.projects,
                repo_root=self.projects,
            )

    def test_all_dynamic_authorities_are_protected(self):
        value = self.bootstrap()
        expected = {
            str(self.active.resolve()), str(self.rollback.resolve()), str(self.controller.resolve()),
            "/candidate/pending", "/parallel/task",
        }
        self.assertTrue(expected.issubset(set(value["protected_paths"])))
        self.assertTrue(value["bootstrap_complete"])

    def test_stable_controller_cannot_be_classified_for_delete(self):
        value = self.bootstrap()
        result = storage.classify_release(self.controller, value)
        self.assertEqual(result["classification"], "PROTECTED")
        self.assertIn("CONTROL_PLANE_DEPENDENCY", result["reasons"])
        self.assertFalse(result["delete_eligible_now"])

    def test_active_rollback_pending_parallel_all_survive(self):
        value = self.bootstrap()
        for path in (self.active, self.rollback, Path("/candidate/pending"), Path("/parallel/task")):
            with self.subTest(path=path):
                result = storage.classify_release(path, value)
                self.assertEqual(result["classification"], "PROTECTED")
                self.assertFalse(result["delete_eligible_now"])

    def test_unknown_is_keep(self):
        value = self.bootstrap()
        result = storage.classify_release(self.releases / "unknown", value)
        self.assertEqual(result["classification"], "UNKNOWN")
        self.assertEqual(result["reasons"], ["UNKNOWN_KEEP"])
        self.assertFalse(result["delete_eligible_now"])

    def test_missing_rollback_never_claims_complete_bootstrap(self):
        payload = json.loads(self.registry.read_text())
        payload["components"]["worker"].pop("rollback")
        self.registry.write_text(json.dumps(payload))
        with (
            mock.patch.object(storage, "_release_identity", side_effect=self.identity),
            mock.patch.object(storage, "discover_pending_approvals", return_value=[]),
            mock.patch.object(storage, "discover_parallel_worktrees", return_value=[]),
        ):
            value = storage.bootstrap_registry(
                current_link=self.current,
                releases_root=self.releases,
                stable_controller_link=self.controller_link,
                canonical_registry=self.registry,
                storage_registry=None,
                projects_root=self.projects,
                repo_root=self.projects,
            )
        self.assertFalse(value["bootstrap_complete"])
        self.assertEqual(value["unknown_policy"], "NEVER_DELETE")

    def test_validated_storage_projection_reconstructs_missing_canonical_rollback(self):
        payload = json.loads(self.registry.read_text())
        payload["components"]["worker"].pop("rollback")
        self.registry.write_text(json.dumps(payload))
        projected = self.registry.parent / "storage.json"
        projected.write_text(json.dumps({"rollback_worker": {"path": str(self.rollback), "sha": "b" * 40}}))
        with (
            mock.patch.object(storage, "_release_identity", side_effect=self.identity),
            mock.patch.object(storage, "discover_pending_approvals", return_value=[]),
            mock.patch.object(storage, "discover_parallel_worktrees", return_value=[]),
        ):
            value = storage.bootstrap_registry(
                current_link=self.current,
                releases_root=self.releases,
                stable_controller_link=self.controller_link,
                canonical_registry=self.registry,
                storage_registry=projected,
                projects_root=self.projects,
                repo_root=self.projects,
            )
        self.assertTrue(value["bootstrap_complete"])
        self.assertEqual(value["rollback_worker"]["sha"], "b" * 40)


if __name__ == "__main__":
    unittest.main()
