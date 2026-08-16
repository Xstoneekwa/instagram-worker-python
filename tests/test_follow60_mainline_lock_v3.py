import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from follow60_mainline_integrity_v3 import verify_runtime_integrity
from follow60_lock_v3 import (
    canonical_json,
    git,
    git_file_entry,
    sha256_bytes,
    transitive_import_graph,
    verify_repository,
    verify_runtime,
)


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

    def test_git_read_trust_is_exact_process_scoped_and_sanitized(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("follow60_lock_v3.subprocess.check_output", return_value=b"ok") as checked:
                with patch.dict(
                    "follow60_lock_v3.os.environ",
                    {
                        "GIT_CONFIG_COUNT": "1",
                        "GIT_CONFIG_KEY_0": "safe.directory",
                        "GIT_CONFIG_VALUE_0": "*",
                    },
                ):
                    self.assertEqual(b"ok", git(root, "rev-parse", "HEAD"))
            command = checked.call_args.args[0]
            environment = checked.call_args.kwargs["env"]
            exact_root = str(root.resolve())
            self.assertEqual(
                [
                    "git",
                    "-c", "safe.directory=",
                    "-c", f"safe.directory={exact_root}",
                    "-C", exact_root,
                    "rev-parse", "HEAD",
                ],
                command,
            )
            self.assertEqual("1", environment["GIT_CONFIG_NOSYSTEM"])
            self.assertEqual(os.devnull, environment["GIT_CONFIG_GLOBAL"])
            self.assertNotIn("GIT_CONFIG_COUNT", environment)
            self.assertNotIn("GIT_CONFIG_KEY_0", environment)
            self.assertNotIn("GIT_CONFIG_VALUE_0", environment)

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
        self.assertIn("--request-signature", source)
        self.assertIn("verify_detached_signature", source)
        self.assertIn("CANDIDATE_REQUIRES_EXTERNAL_MANIFEST_SIGNATURE", source)
        self.assertNotIn("pkeyutl\", \"-sign", source)

    def _signed_v31_repo(self, *, source: str = "VALUE = 1\n", include_import: bool = False):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        subprocess.check_call(["git", "init", "-q", str(root)])
        subprocess.check_call(["git", "-C", str(root), "config", "user.email", "test@example.invalid"])
        subprocess.check_call(["git", "-C", str(root), "config", "user.name", "Test"])
        (root / "protected.py").write_text(source, encoding="utf-8")
        if include_import:
            (root / "imported.py").write_text("IMPORTED = 1\n", encoding="utf-8")
        subprocess.check_call(["git", "-C", str(root), "add", "."])
        subprocess.check_call(["git", "-C", str(root), "commit", "-qm", "base"])
        entry = git_file_entry(root, "protected.py")
        entry.update({"role": "runtime", "lock_version": "3.1.0"})
        entries = {"protected.py": entry}
        graph = transitive_import_graph(root, entries)
        manifest = {
            "schema": "FOLLOW60_MAINLINE_LOCK_V3_1",
            "lock_version": "3.1.0",
            "lock_state": "LOCKED",
            "protected_entries": entries,
            "protected_scope_sha256": sha256_bytes(canonical_json(entries)),
            "import_graph": graph,
            "import_graph_sha256": sha256_bytes(canonical_json(graph)),
            "runtime_contract": {
                "runtime_mode": "mainline",
                "binding_kind": "mainline",
                "engine": "FOLLOW60_V2_MAINLINE_V1",
                "entrypoint": "runner.py",
            },
            "approval": {"token_status": "consumed_once"},
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        private_key = root / "private.pem"
        public_key = root / "public.pem"
        signature = root / "manifest.sig"
        subprocess.check_call(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private_key)])
        subprocess.check_call(["openssl", "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)])
        subprocess.check_call([
            "openssl", "pkeyutl", "-sign", "-inkey", str(private_key),
            "-rawin", "-in", str(manifest_path), "-out", str(signature),
        ])
        return temporary, root, manifest_path, signature, public_key

    def test_v31_signed_exact_head_passes(self):
        temporary, root, manifest, signature, public_key = self._signed_v31_repo()
        self.addCleanup(temporary.cleanup)
        result = verify_repository(
            root, manifest, signature_path=signature, public_key_path=public_key
        )
        self.assertTrue(result["ok"], result)

    def test_v31_space_comment_and_punctuation_mutations_fail(self):
        mutations = ["VALUE = 1 \n", "VALUE = 1  # comment\n", "VALUE = (1,)\n"]
        for mutated in mutations:
            temporary, root, manifest, signature, public_key = self._signed_v31_repo()
            try:
                (root / "protected.py").write_text(mutated, encoding="utf-8")
                subprocess.check_call(["git", "-C", str(root), "add", "protected.py"])
                subprocess.check_call(["git", "-C", str(root), "commit", "-qm", "tamper"])
                result = verify_repository(
                    root, manifest, signature_path=signature, public_key_path=public_key
                )
                self.assertEqual("protected_entry_mismatch", result["reason"])
            finally:
                temporary.cleanup()

    def test_v31_new_local_import_outside_manifest_fails(self):
        temporary, root, manifest, signature, public_key = self._signed_v31_repo(
            source="import imported\nVALUE = imported.IMPORTED\n",
            include_import=True,
        )
        self.addCleanup(temporary.cleanup)
        result = verify_repository(
            root, manifest, signature_path=signature, public_key_path=public_key
        )
        self.assertEqual("unprotected_transitive_dependency", result["reason"])

    def test_v31_unsigned_manifest_change_fails(self):
        temporary, root, manifest, signature, public_key = self._signed_v31_repo()
        self.addCleanup(temporary.cleanup)
        payload = json.loads(manifest.read_text())
        payload["lock_version"] = "3.1.1-tampered"
        manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        result = verify_repository(
            root, manifest, signature_path=signature, public_key_path=public_key
        )
        self.assertEqual("signature_invalid", result["reason"])

    def test_v31_verifier_file_mutation_fails(self):
        temporary, root, manifest, signature, public_key = self._signed_v31_repo(
            source="def verify():\n    return True\n"
        )
        self.addCleanup(temporary.cleanup)
        (root / "protected.py").write_text("def verify():\n    return False\n", encoding="utf-8")
        subprocess.check_call(["git", "-C", str(root), "add", "protected.py"])
        subprocess.check_call(["git", "-C", str(root), "commit", "-qm", "weaken verifier"])
        result = verify_repository(
            root, manifest, signature_path=signature, public_key_path=public_key
        )
        self.assertEqual("protected_entry_mismatch", result["reason"])

    def test_v31_runtime_tamper_fails_closed(self):
        temporary, root, manifest, signature, public_key = self._signed_v31_repo()
        self.addCleanup(temporary.cleanup)
        (root / "protected.py").write_text("VALUE = 2\n", encoding="utf-8")
        subprocess.check_call(["git", "-C", str(root), "add", "protected.py"])
        subprocess.check_call(["git", "-C", str(root), "commit", "-qm", "runtime tamper"])
        result = verify_runtime(
            root,
            runtime_mode="mainline",
            binding_kind="mainline",
            engine="FOLLOW60_V2_MAINLINE_V1",
            manifest_path=manifest,
            signature_path=signature,
            public_key_path=public_key,
        )
        self.assertEqual("FOLLOW60_MAINLINE_INTEGRITY_MISMATCH", result["reason"])

    def test_v31_consumed_approval_is_not_reusable(self):
        generator = (ROOT / "scripts/generate-follow60-mainline-lock-v3-candidate.py").read_text()
        self.assertIn("approval_token_already_consumed", generator)
        self.assertIn('entry.get("change_id") == request.get("change_id")', generator)

    def test_v31_approval_is_bound_to_exact_diff(self):
        generator = (ROOT / "scripts/generate-follow60-mainline-lock-v3-candidate.py").read_text()
        self.assertIn("approved_diff_mismatch", generator)
        self.assertIn("approved_file_set_mismatch", generator)
        self.assertIn("approved_head_mismatch", generator)

    def test_package_script_rejects_unlocked_state(self):
        source = (ROOT / "scripts/promote-follow60-v2-mainline.sh").read_text()
        self.assertIn("PACKAGE_WHILE_UNLOCKED=FAIL", source)
        self.assertIn('test "$LOCK_STATE" = "UNLOCKED"', source)

    def test_dispatcher_startup_has_integrity_gate(self):
        source = (ROOT / "account_run_request_consumer.py").read_text()
        identity = source.index("runtime_identity = resolve_worker_runtime_identity")
        integrity = source.index("integrity = verify_runtime_integrity(", identity)
        forever = source.index("return run_forever()", integrity)
        self.assertLess(identity, integrity)
        self.assertLess(integrity, forever)
        self.assertIn("device_actions_started=False", source[integrity:forever])


if __name__ == "__main__":
    unittest.main()
