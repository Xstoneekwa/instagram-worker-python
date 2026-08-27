import json
import os
from pathlib import Path
import tempfile
import unittest

from follow60_write_lock_v3_1 import STATE_NAME, direct_write_attempt, verify_physical_write_lock


class Follow60PhysicalWriteLockV31Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / "protected"
        self.scope.mkdir()
        self.protected = self.scope / "engine.py"
        self.protected.write_text("VALUE = 1  # locked.\n", encoding="utf-8")
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps({
            "lock_version": "3.1.0",
            "protected_entries": {"protected/engine.py": {"path": "protected/engine.py"}},
        }), encoding="utf-8")
        (self.root / STATE_NAME).write_text(json.dumps({
            "schema": "FOLLOW60_PHYSICAL_WRITE_LOCK_V3_1",
            "lock_state": "LOCKED",
            "protected_paths": ["protected/engine.py"],
            "protected_directories": ["protected"],
        }), encoding="utf-8")
        self.protected.chmod(0o444)
        self.scope.chmod(0o555)
        (self.root / STATE_NAME).chmod(0o444)
        self.root.chmod(0o555)

    def tearDown(self):
        self.root.chmod(0o755)
        self.scope.chmod(0o755)
        self.protected.chmod(0o644)
        (self.root / STATE_NAME).chmod(0o644)

    def test_can_read_while_locked(self):
        self.assertIn("VALUE = 1", self.protected.read_text(encoding="utf-8"))

    def test_direct_byte_space_comment_and_punctuation_writes_are_blocked(self):
        for payload in (b"x", b" ", b"# comment", b"!"):
            self.assertFalse(direct_write_attempt(self.protected, payload))

    def test_delete_rename_and_create_are_blocked(self):
        with self.assertRaises(OSError):
            self.protected.unlink()
        with self.assertRaises(OSError):
            self.protected.rename(self.scope / "renamed.py")
        with self.assertRaises(OSError):
            (self.scope / "new.py").write_text("new\n", encoding="utf-8")

    def test_test_mode_verifier_accepts_read_only_scope(self):
        result = verify_physical_write_lock(
            self.root, self.manifest, require_root_owner=False, require_immutable=False
        )
        self.assertTrue(result["ok"], result)

    def test_write_bit_is_rejected(self):
        self.protected.chmod(0o644)
        result = verify_physical_write_lock(
            self.root, self.manifest, require_root_owner=False, require_immutable=False
        )
        self.assertEqual("write_bit_present", result["failures"]["protected/engine.py"])

    def test_commit_push_package_and_activation_are_fail_closed(self):
        pre_commit = (Path(__file__).resolve().parents[1] / ".githooks/pre-commit").read_text()
        pre_push = (Path(__file__).resolve().parents[1] / ".githooks/pre-push").read_text()
        promote = (Path(__file__).resolve().parents[1] / "scripts/promote-follow60-v2-mainline.sh").read_text()
        guard = (Path(__file__).resolve().parents[1] / "scripts/guard-follow60-protected-index-v3-1.py").read_text()
        self.assertIn("guard-follow60-protected-index-v3-1.py", pre_commit)
        self.assertIn("verify-follow60-deployment-candidate-v1.py", pre_push)
        self.assertIn("verify-follow60-physical-write-lock-v3-1.py", promote)
        self.assertIn("external_candidate_evidence_missing", guard)
        self.assertNotIn("BYPASS", guard)


if __name__ == "__main__":
    unittest.main()
