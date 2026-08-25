from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from follow60_deployment_gate_v1 import verify_deployment_candidate
from follow60_lock_v3 import canonical_json, git_file_entry, sha256_bytes, transitive_import_graph


class Follow60DeploymentGateV1Tests(unittest.TestCase):
    def _repo(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        subprocess.check_call(["git", "init", "-q", str(root)])
        subprocess.check_call(["git", "-C", str(root), "config", "user.email", "test@example.invalid"])
        subprocess.check_call(["git", "-C", str(root), "config", "user.name", "Test"])
        (root / "protected.py").write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.check_call(["git", "-C", str(root), "add", "protected.py"])
        subprocess.check_call(["git", "-C", str(root), "commit", "-qm", "candidate"])
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        public = root / "public.pem"
        manifest = root / "manifest.json"
        signature = root / "manifest.sig"
        private = Ed25519PrivateKey.generate()
        public.write_bytes(private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
        return temporary, root, sha, private, public, manifest, signature

    def _manifest(self, root: Path, sha: str, *, recertified: bool = True) -> dict:
        entry = git_file_entry(root, "protected.py")
        entry.update({"role": "runtime", "lock_version": "3.1.0"})
        entries = {"protected.py": entry}
        graph = transitive_import_graph(root, entries)
        return {
            "schema": "FOLLOW60_MAINLINE_LOCK_V3_1",
            "lock_version": "3.1.0", "lock_state": "LOCKED",
            "certified_candidate_sha": sha,
            "protected_entries": entries,
            "protected_scope_sha256": sha256_bytes(canonical_json(entries)),
            "import_graph": graph,
            "import_graph_sha256": sha256_bytes(canonical_json(graph)),
            "runtime_contract": {"runtime_mode": "mainline", "binding_kind": "mainline", "engine": "FOLLOW60_V2_MAINLINE_V1", "entrypoint": "runner.py"},
            "approval": {"token_status": "consumed_once"},
            "recertification": {
                "status": "PASS" if recertified else "PENDING",
                "regression_test_count": 12 if recertified else 0,
                "missing_canonical_delta": 0,
                "receipt_sha256": "a" * 64,
            },
        }

    @staticmethod
    def _sign(payload: Path, signature: Path, private: Ed25519PrivateKey) -> None:
        signature.write_bytes(private.sign(payload.read_bytes()))

    def test_protected_change_with_stale_manifest_fails(self):
        temporary, root, sha, private, public, manifest, signature = self._repo()
        self.addCleanup(temporary.cleanup)
        manifest.write_text(json.dumps(self._manifest(root, sha), sort_keys=True), encoding="utf-8")
        self._sign(manifest, signature, private)
        (root / "protected.py").write_text("VALUE = 2\n", encoding="utf-8")
        subprocess.check_call(["git", "-C", str(root), "add", "protected.py"])
        subprocess.check_call(["git", "-C", str(root), "commit", "-qm", "changed"])
        result = verify_deployment_candidate(root, manifest_path=manifest, signature_path=signature, public_key_path=public)
        self.assertFalse(result["ok"])
        self.assertIn(result["reason"], {"manifest_certified_sha_mismatch", "manifest_regen_required"})

    def test_regenerated_but_not_recertified_or_signed_fails(self):
        temporary, root, sha, _private, public, manifest, signature = self._repo()
        self.addCleanup(temporary.cleanup)
        manifest.write_text(json.dumps(self._manifest(root, sha, recertified=False), sort_keys=True), encoding="utf-8")
        result = verify_deployment_candidate(root, manifest_path=manifest, signature_path=signature, public_key_path=public)
        self.assertEqual("follow60_recertification_missing_or_failed", result["reason"])

    def test_exact_candidate_recertified_and_signed_passes(self):
        temporary, root, sha, private, public, manifest, signature = self._repo()
        self.addCleanup(temporary.cleanup)
        manifest.write_text(json.dumps(self._manifest(root, sha), sort_keys=True), encoding="utf-8")
        self._sign(manifest, signature, private)
        result = verify_deployment_candidate(root, manifest_path=manifest, signature_path=signature, public_key_path=public)
        self.assertTrue(result["ok"], result)
        self.assertEqual(sha, result["manifest_certified_sha"])
        self.assertFalse(result["write_lock_bypass_available_to_codex"])


if __name__ == "__main__":
    unittest.main()

