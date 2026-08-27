import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from follow60_write_lock_v3_1 import STATE_NAME, direct_write_attempt, verify_physical_write_lock


class Follow60PhysicalWriteLockV31Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.external = self.root.parent / (self.root.name + '-external-state.json')
        self.addCleanup(lambda: self.external.unlink(missing_ok=True))
        import follow60_external_deployment_lock_v2 as external
        self.admission = {'candidate_commit_sha': 'a' * 40, 'manifest_sha256': 'b' * 64}
        for patcher in (mock.patch.object(external, 'seal_path', return_value=self.external),
                        mock.patch.object(external, 'exact_admission', return_value=self.admission)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.scope = self.root / "protected"
        self.scope.mkdir()
        self.protected = self.scope / "engine.py"
        self.protected.write_text("VALUE = 1  # locked.\n", encoding="utf-8")
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps({
            "lock_version": "3.1.0",
            "protected_entries": {"protected/engine.py": {"path": "protected/engine.py"}},
        }), encoding="utf-8")
        self.external.write_text(json.dumps({
            "schema": "FOLLOW60_PHYSICAL_WRITE_LOCK_V3_1",
            "lock_state": "LOCKED",
            "protocol_version": external.PROTOCOL_VERSION,
            "verified_revision": 'a' * 40,
            **self.admission,
            "protected_paths": ["protected/engine.py"],
            "protected_directories": ["protected"],
        }), encoding="utf-8")
        self.protected.chmod(0o444)
        self.scope.chmod(0o555)
        self.external.chmod(0o444)
        self.root.chmod(0o555)

    def tearDown(self):
        self.root.chmod(0o755)
        self.scope.chmod(0o755)
        self.protected.chmod(0o644)
        self.external.chmod(0o644)

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
