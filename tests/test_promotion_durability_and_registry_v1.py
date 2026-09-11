from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from promotion_runtime_adapter_v1 import PhoneFarmPromotionAdapter, _durable_registry_replace
from promotion_transaction_v1 import PromotionFailure, durable_json


class DurabilityAndRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "registry.json"
        self.old_worker = {"sha": "a" * 40, "runtime_root": "/old"}
        self.payload = {
            "schema": "PHONE_FARM_CANONICAL_DELTA_REGISTRY_V1",
            "components": {
                "worker": {"production": self.old_worker},
                "backend": {"production": {"sha": "b" * 40}},
            },
        }
        self.path.write_text(json.dumps(self.payload))

    def test_parallel_unrelated_registry_change_is_preserved(self):
        fresh = json.loads(self.path.read_text())
        fresh["components"]["backend"]["production"]["sha"] = "c" * 40
        self.path.write_text(json.dumps(fresh))
        result = _durable_registry_replace(
            self.path,
            {"production": {"sha": "d" * 40}, "rollback": self.old_worker},
            self.old_worker,
        )
        self.assertEqual(result["components"]["backend"]["production"]["sha"], "c" * 40)
        self.assertEqual(result["components"]["worker"]["production"]["sha"], "d" * 40)

    def test_parallel_worker_metadata_change_is_preserved(self):
        fresh = json.loads(self.path.read_text())
        fresh["components"]["worker"]["parallel_task_marker"] = "newer-value"
        self.path.write_text(json.dumps(fresh))
        result = _durable_registry_replace(
            self.path,
            {"production": {"sha": "d" * 40}, "rollback": self.old_worker, "parallel_task_marker": "stale-value"},
            self.old_worker,
        )
        self.assertEqual(result["components"]["worker"]["parallel_task_marker"], "newer-value")
        self.assertEqual(result["components"]["worker"]["production"]["sha"], "d" * 40)

    def test_parallel_worker_change_fails_cas_without_overwrite(self):
        fresh = json.loads(self.path.read_text())
        fresh["components"]["worker"]["production"]["sha"] = "e" * 40
        self.path.write_text(json.dumps(fresh))
        before = self.path.read_bytes()
        with self.assertRaises(PromotionFailure):
            _durable_registry_replace(
                self.path,
                {"production": {"sha": "d" * 40}},
                self.old_worker,
            )
        self.assertEqual(self.path.read_bytes(), before)

    def test_rename_failure_leaves_old_registry(self):
        before = self.path.read_bytes()
        with mock.patch("promotion_runtime_adapter_v1.os.replace", side_effect=OSError("rename")):
            with self.assertRaises(OSError):
                _durable_registry_replace(
                    self.path,
                    {"production": {"sha": "d" * 40}, "rollback": self.old_worker},
                    self.old_worker,
                )
        self.assertEqual(self.path.read_bytes(), before)

    def test_interrupted_write_before_fsync_leaves_previous_generation(self):
        target = Path(self.tmp.name) / "journal.json"
        target.write_text('{"old":true}\n')
        before = target.read_bytes()
        with mock.patch("promotion_transaction_v1.os.fsync", side_effect=OSError(28, "ENOSPC")):
            with self.assertRaises(OSError):
                durable_json(target, {"new": True})
        self.assertEqual(target.read_bytes(), before)

    def test_interrupted_rename_leaves_previous_generation(self):
        target = Path(self.tmp.name) / "journal-rename.json"
        target.write_text('{"old":true}\n')
        before = target.read_bytes()
        with mock.patch("promotion_transaction_v1.os.replace", side_effect=OSError("rename failed")):
            with self.assertRaises(OSError):
                durable_json(target, {"new": True})
        self.assertEqual(target.read_bytes(), before)

    def test_new_file_fsync_failure_leaves_receipt_absent(self):
        target = Path(self.tmp.name) / "absent.json"
        with mock.patch("promotion_transaction_v1.os.fsync", side_effect=OSError(28, "ENOSPC")):
            with self.assertRaises(OSError):
                durable_json(target, {"new": True})
        self.assertFalse(target.exists())

    def test_committed_generation_requires_exact_roots_and_transaction(self):
        transaction_id = "worker-promotion:test-registry-0001"
        candidate_sha = "d" * 40
        worker = {
            "production": {
                "sha": candidate_sha,
                "runtime_root": "/new",
                "committed_by_transaction": transaction_id,
            },
            "rollback": {
                "sha": self.old_worker["sha"],
                "runtime_root": "/old",
                "committed_by_transaction": transaction_id,
            },
        }
        self.path.write_text(json.dumps({"components": {"worker": worker}}))
        adapter = PhoneFarmPromotionAdapter.__new__(PhoneFarmPromotionAdapter)
        adapter.c = SimpleNamespace(
            _canonical_registry_path=lambda: self.path,
            _read_json_object=lambda path: json.loads(Path(path).read_text()),
        )
        plan = SimpleNamespace(
            candidate_sha=candidate_sha,
            candidate_release="/new",
            old_active_sha=self.old_worker["sha"],
            old_active_release="/old",
            transaction_id=transaction_id,
            registry_pre_state={"worker_production": self.old_worker, "worker_component": {"production": self.old_worker}},
        )
        self.assertTrue(adapter.committed_generation_state(plan)["committed"])
        worker["production"]["committed_by_transaction"] = "worker-promotion:other-0001"
        self.path.write_text(json.dumps({"components": {"worker": worker}}))
        self.assertFalse(adapter.committed_generation_state(plan)["committed"])

    def test_storage_projection_replaces_old_runtime_authorities_without_accumulation(self):
        runtime_home = Path(self.tmp.name) / "runtime"
        storage = runtime_home / "storage-maintenance" / "registry.v2.json"
        storage.parent.mkdir(parents=True)
        storage.write_text(json.dumps({
            "active_worker": {"path": "/historical-active", "sha": "1" * 40},
            "rollback_worker": {"path": "/historical-rollback", "sha": "2" * 40},
            "control_plane_dependencies": [{"path": "/historical-controller"}],
            "protected_paths": [
                "/historical-active",
                "/historical-rollback",
                "/historical-controller",
                "/parallel-or-approval-protection",
            ],
        }))
        adapter = PhoneFarmPromotionAdapter.__new__(PhoneFarmPromotionAdapter)
        adapter.paths = SimpleNamespace(runtime_home=runtime_home)
        plan = SimpleNamespace(
            candidate_release="/new-active",
            candidate_sha="3" * 40,
            old_active_release="/new-rollback",
            old_active_sha="4" * 40,
            controller_identity={"release_root": "/current-controller"},
        )
        result = adapter.reconcile_projections(plan, promoted=True)
        self.assertTrue(result["ok"])
        projected = json.loads(storage.read_text())
        self.assertEqual(
            set(projected["protected_paths"]),
            {"/new-active", "/new-rollback", "/current-controller", "/parallel-or-approval-protection"},
        )


if __name__ == "__main__":
    unittest.main()
