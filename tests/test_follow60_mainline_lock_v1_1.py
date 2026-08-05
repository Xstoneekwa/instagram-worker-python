import datetime as dt
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify-follow60-mainline-lock-v1-1.py"
SPEC = importlib.util.spec_from_file_location("follow60_mainline_lock_v1_1", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Follow60MainlineLockV11Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Follow60 Test"], check=True)
        (self.root / "protected.py").write_text("base\n", encoding="utf-8")
        (self.root / "test_protected.py").write_text("base test\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "base"], check=True)
        self.base = MODULE._git(self.root, "rev-parse", "HEAD")
        (self.root / "protected.py").write_text("approved\n", encoding="utf-8")
        (self.root / "test_protected.py").write_text("approved test\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "approved"], check=True)
        self.target = MODULE._git(self.root, "rev-parse", "HEAD")
        self.scope = ["protected.py", "test_protected.py"]
        self.diff_hash = MODULE.canonical_git_diff_hash(self.root, self.base, self.target, self.scope)
        self.approval_id = "one-shot-v1-1"
        self.manifest = {
            "status": "FOLLOW60_MAINLINE_APPROVED_PATCH",
            "worker_parent_sha": self.base,
            "approved_worker_sha": self.target,
            "critical_files": {
                name: hashlib.sha256((self.root / name).read_bytes()).hexdigest()
                for name in self.scope
            },
            "approved_diff": {"scope": self.scope, "sha256": self.diff_hash},
            "approval": {
                "approval_id": self.approval_id,
                "approved_by": "operator",
                "approved_at": "2026-08-05T08:00:00Z",
                "expires_at": "2026-08-05T10:00:00Z",
                "base_sha": self.base,
                "target_sha": self.target,
                "scope": self.scope,
                "diff_sha256": self.diff_hash,
                "one_shot_status": "consumed",
            },
        }
        self.ledger = {"consumed_approval_ids": [self.approval_id]}

    def tearDown(self):
        self.temp.cleanup()

    def test_approved_release_passes(self):
        self.assertTrue(MODULE.verify_target(self.root, self.manifest, self.ledger)["ok"])

    def test_unrelated_diff_is_rejected(self):
        (self.root / "unrelated.py").write_text("no\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "unrelated"], check=True)
        self.manifest["approved_worker_sha"] = MODULE._git(self.root, "rev-parse", "HEAD")
        self.manifest["approval"]["target_sha"] = self.manifest["approved_worker_sha"]
        result = MODULE.verify_target(self.root, self.manifest, self.ledger)
        self.assertEqual("approved_scope_mismatch", result["reason"])

    def test_bad_base_is_rejected(self):
        self.manifest["worker_parent_sha"] = "0" * 40
        result = MODULE.verify_target(self.root, self.manifest, self.ledger)
        self.assertIn(result["reason"], {"approved_base_not_ancestor", "approved_base_unavailable"})

    def test_bad_scope_is_rejected(self):
        self.manifest["approved_diff"]["scope"] = ["protected.py"]
        result = MODULE.verify_target(self.root, self.manifest, self.ledger)
        self.assertEqual("approved_scope_mismatch", result["reason"])

    def test_reused_go_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "approval_already_consumed"):
            MODULE.validate_one_shot_approval(
                self.manifest["approval"],
                expected_base=self.base,
                expected_target=self.target,
                expected_scope=self.scope,
                expected_diff_hash=self.diff_hash,
                ledger=self.ledger,
                now=dt.datetime(2026, 8, 5, 8, 30, tzinfo=dt.timezone.utc),
            )

    def test_manifest_hash_mismatch_is_rejected(self):
        self.manifest["critical_files"]["protected.py"] = "f" * 64
        result = MODULE.verify_target(self.root, self.manifest, self.ledger)
        self.assertEqual("manifest_hash_mismatch", result["reason"])


if __name__ == "__main__":
    unittest.main()
