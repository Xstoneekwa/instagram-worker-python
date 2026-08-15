import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from follow60_mainline_integrity_v3 import verify_runtime_integrity


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "scripts/verify-follow60-mainline-lock-v3.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("follow60_lock_v3", VERIFIER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_contract(root: Path, protected: dict[str, str], *, approval_id: str = "go-1"):
    manifest = {
        "schema": "FOLLOW60_MAINLINE_LOCK_V3",
        "lock_version": "3.0.0-test",
        "protected_files": protected,
        "protected_scope_sha256": _sha(
            json.dumps(protected, sort_keys=True, separators=(",", ":")).encode()
        ),
        "runtime_contract": {
            "runtime_mode": "mainline",
            "binding_kind": "mainline",
            "engine": "FOLLOW60_V2_MAINLINE_V1",
        },
        "approval": {"approval_id": approval_id, "status": "approved_once"},
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    approval = {
        "schema": "FOLLOW60_MAINLINE_LOCK_V3_EXTERNAL_APPROVAL",
        "approval_id": approval_id,
        "status": "consumed_once",
        "approved_scope_sha256": manifest["protected_scope_sha256"],
        "manifest_sha256": _sha(manifest_path.read_bytes()),
    }
    approval_path = root / "approval.json"
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    return manifest_path, approval_path


class Follow60MainlineLockV3Tests(unittest.TestCase):
    def setUp(self):
        self.verifier = _load_verifier()

    def _repo(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        subprocess.check_call(["git", "init", "-q", str(root)])
        subprocess.check_call(["git", "-C", str(root), "config", "user.email", "test@example.invalid"])
        subprocess.check_call(["git", "-C", str(root), "config", "user.name", "Test"])
        (root / "protected.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "outside.txt").write_text("outside\n", encoding="utf-8")
        subprocess.check_call(["git", "-C", str(root), "add", "."])
        subprocess.check_call(["git", "-C", str(root), "commit", "-qm", "base"])
        protected = {"protected.py": _sha((root / "protected.py").read_bytes())}
        manifest, approval = _write_contract(root, protected)
        return temporary, root, manifest, approval

    def test_exact_head_passes(self):
        temporary, root, manifest, approval = self._repo()
        self.addCleanup(temporary.cleanup)
        result = self.verifier.verify(root, manifest, approval)
        self.assertTrue(result["ok"], result)

    def test_descendant_single_character_change_fails(self):
        temporary, root, manifest, approval = self._repo()
        self.addCleanup(temporary.cleanup)
        (root / "protected.py").write_text("VALUE = 2\n", encoding="utf-8")
        subprocess.check_call(["git", "-C", str(root), "add", "protected.py"])
        subprocess.check_call(["git", "-C", str(root), "commit", "-qm", "tamper"])
        result = self.verifier.verify(root, manifest, approval)
        self.assertEqual("head_protected_blob_mismatch", result["reason"])

    def test_outside_scope_descendant_passes(self):
        temporary, root, manifest, approval = self._repo()
        self.addCleanup(temporary.cleanup)
        (root / "outside.txt").write_text("allowed\n", encoding="utf-8")
        subprocess.check_call(["git", "-C", str(root), "add", "outside.txt"])
        subprocess.check_call(["git", "-C", str(root), "commit", "-qm", "outside"])
        self.assertTrue(self.verifier.verify(root, manifest, approval)["ok"])

    def test_manifest_auto_change_without_external_approval_fails(self):
        temporary, root, manifest, approval = self._repo()
        self.addCleanup(temporary.cleanup)
        payload = json.loads(manifest.read_text())
        payload["lock_version"] = "self-approved"
        manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        result = self.verifier.verify(root, manifest, approval)
        self.assertEqual("approval_manifest_hash_mismatch", result["reason"])

    def test_runtime_disk_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "protected.py").write_text("VALUE = 1\n", encoding="utf-8")
            protected = {"protected.py": _sha((root / "protected.py").read_bytes())}
            manifest, _approval = _write_contract(root, protected)
            (root / "protected.py").write_text("VALUE = 2\n", encoding="utf-8")
            result = verify_runtime_integrity(
                root,
                runtime_mode="mainline",
                binding_kind="mainline",
                engine="FOLLOW60_V2_MAINLINE_V1",
                manifest_path=manifest,
            )
            self.assertEqual("FOLLOW60_MAINLINE_INTEGRITY_MISMATCH", result["reason"])

    def test_runtime_gate_precedes_mainline_configuration(self):
        source = (ROOT / "runner.py").read_text(encoding="utf-8")
        gate = source.index("_integrity = verify_runtime_integrity(")
        configure = source.index("_follow60_engine_active = bool(_configure_follow60_mainline(")
        self.assertLess(gate, configure)
        self.assertIn("device_actions_started=False", source[gate:configure])

    def test_candidate_generator_cannot_approve(self):
        source = (ROOT / "scripts/generate-follow60-mainline-lock-v3-candidate.py").read_text()
        self.assertIn('"approval": None', source)
        self.assertIn('"promotion_authorized": False', source)
        self.assertNotIn("approved_once", source)


if __name__ == "__main__":
    unittest.main()
