import datetime as dt
import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify-follow60-mainline-lock.py"
SPEC = importlib.util.spec_from_file_location("follow60_mainline_lock", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
canonical_diff_hash = MODULE.canonical_diff_hash
mismatches = MODULE.mismatches
validate_approval = MODULE.validate_approval


class Follow60MainlineLockV1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "critical.py").write_text("safe\n", encoding="utf-8")
        import hashlib
        expected = hashlib.sha256(b"safe\n").hexdigest()
        self.manifest = {"critical_files": {"critical.py": expected}}
        self.now = dt.datetime(2026, 8, 4, 12, 0, tzinfo=dt.timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def changes(self):
        (self.root / "critical.py").write_text("changed\n", encoding="utf-8")
        return mismatches(self.root, self.manifest)

    def approval(self, changes):
        return {
            "approval_id": "one-shot-1",
            "approved_by": "operator",
            "expires_at": "2026-08-04T12:05:00Z",
            "base_sha": "abc",
            "scope": list(changes),
            "diff_sha256": canonical_diff_hash(changes),
        }

    def test_unchanged_tree_passes(self):
        self.assertEqual({}, mismatches(self.root, self.manifest))

    def test_changed_tree_is_detected(self):
        self.assertIn("critical.py", self.changes())

    def test_valid_one_shot_approval_passes(self):
        changes = self.changes()
        validate_approval(self.approval(changes), changes, {"consumed_approval_ids": []}, self.now, "abc")

    def test_expired_approval_fails(self):
        changes = self.changes(); approval = self.approval(changes)
        approval["expires_at"] = "2026-08-04T11:59:59Z"
        with self.assertRaisesRegex(ValueError, "approval_expired"):
            validate_approval(approval, changes, {"consumed_approval_ids": []}, self.now)

    def test_consumed_approval_fails(self):
        changes = self.changes(); approval = self.approval(changes)
        with self.assertRaisesRegex(ValueError, "approval_already_consumed"):
            validate_approval(approval, changes, {"consumed_approval_ids": ["one-shot-1"]}, self.now)

    def test_scope_mismatch_fails(self):
        changes = self.changes(); approval = self.approval(changes); approval["scope"] = []
        with self.assertRaisesRegex(ValueError, "approval_scope_mismatch"):
            validate_approval(approval, changes, {"consumed_approval_ids": []}, self.now)

    def test_diff_hash_mismatch_fails(self):
        changes = self.changes(); approval = self.approval(changes); approval["diff_sha256"] = "bad"
        with self.assertRaisesRegex(ValueError, "approval_diff_hash_mismatch"):
            validate_approval(approval, changes, {"consumed_approval_ids": []}, self.now)

    def test_missing_field_fails(self):
        changes = self.changes(); approval = self.approval(changes); del approval["base_sha"]
        with self.assertRaisesRegex(ValueError, "approval_missing_fields"):
            validate_approval(approval, changes, {"consumed_approval_ids": []}, self.now)

    def test_missing_file_is_detected(self):
        (self.root / "critical.py").unlink()
        self.assertIsNone(mismatches(self.root, self.manifest)["critical.py"]["actual"])

    def test_wrong_base_sha_fails(self):
        changes = self.changes(); approval = self.approval(changes)
        with self.assertRaisesRegex(ValueError, "approval_base_sha_mismatch"):
            validate_approval(approval, changes, {"consumed_approval_ids": []}, self.now, "different")


if __name__ == "__main__":
    unittest.main()
