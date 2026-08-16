from __future__ import annotations

import json
import tempfile
import unittest
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import follow60_change_protocol_v1 as protocol


class Follow60ChangeProtocolTests(unittest.TestCase):
    def test_only_canonical_state_transitions_are_allowed(self) -> None:
        state = protocol.LockState.LOCKED_READ_ONLY
        state = protocol.transition(state, protocol.LockState.CHANGE_AUTHORIZED)
        state = protocol.transition(state, protocol.LockState.WORK_IN_PROGRESS)
        self.assertEqual(state, protocol.LockState.WORK_IN_PROGRESS)
        with self.assertRaisesRegex(ValueError, "follow60_transition_forbidden"):
            protocol.transition(state, protocol.LockState.PROMOTABLE)

    def test_gates_fail_closed_and_scoped_writes_are_exact(self) -> None:
        self.assertTrue(protocol.evaluate_gate(protocol.LockState.LOCKED_READ_ONLY, "read").ok)
        self.assertFalse(protocol.evaluate_gate(protocol.LockState.LOCKED_READ_ONLY, "write").ok)
        self.assertTrue(protocol.evaluate_gate(
            protocol.LockState.WORK_IN_PROGRESS,
            "write", path="runner.py", authorized_paths=["runner.py"],
        ).ok)
        self.assertFalse(protocol.evaluate_gate(
            protocol.LockState.WORK_IN_PROGRESS,
            "write", path="other.py", authorized_paths=["runner.py"],
        ).ok)
        self.assertFalse(protocol.evaluate_gate(protocol.LockState.CANDIDATE_FROZEN, "package").ok)
        self.assertTrue(protocol.evaluate_gate(protocol.LockState.PROMOTABLE, "package").ok)

    def test_python_ed25519_verification_is_path_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = root / "payload.json"
            signature = root / "payload.sig"
            public = root / "public.pem"
            payload.write_text('{"ok":true}', encoding="utf-8")
            private = Ed25519PrivateKey.generate()
            signature.write_bytes(private.sign(payload.read_bytes()))
            public.write_bytes(private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
            from follow60_lock_v3 import verify_detached_signature
            self.assertEqual(verify_detached_signature(payload, signature, public), (True, "signature_valid"))

    def test_initial_approval_has_no_promotion_rights(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            import subprocess
            subprocess.check_call(["git", "init", "-q", str(root)])
            subprocess.check_call(["git", "-C", str(root), "config", "user.email", "test@example.com"])
            subprocess.check_call(["git", "-C", str(root), "config", "user.name", "Test"])
            (root / "runner.py").write_text("x=1\n")
            subprocess.check_call(["git", "-C", str(root), "add", "runner.py"])
            subprocess.check_call(["git", "-C", str(root), "commit", "-qm", "base"])
            base = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            now = datetime.now(timezone.utc)
            auth = {
                "schema": protocol.INITIAL_SCHEMA,
                "approval_status": "PENDING_LIAM_SIGNATURE_1",
                "change_id": "change-1", "base_sha": base,
                "authorized_paths": ["runner.py", "auth.json", "auth.sig", "pub.pem"],
                "authorized_dependency_scope": ["test"],
                "issued_at": (now - timedelta(minutes=1)).isoformat(),
                "expires_at": (now + timedelta(minutes=10)).isoformat(),
                "nonce": "change-1", "one_task_only": True,
                "permits_intermediate_edits_within_scope": True,
                "permits_final_promotion": False, "permits_runtime_activation": False,
            }
            auth_path = root / "auth.json"; auth_path.write_text(json.dumps(auth))
            key = Ed25519PrivateKey.generate(); sig = root / "auth.sig"; sig.write_bytes(key.sign(auth_path.read_bytes()))
            pub = root / "pub.pem"; pub.write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
            result = protocol.verify_initial_authorization(root, auth_path, sig, pub, now=now)
            self.assertTrue(result["ok"], result)

            (root / "runner.py").write_text("x=2\n")
            freeze = protocol.freeze_candidate(root, auth, protected_scope_hash="scope")
            self.assertTrue(protocol.freeze_is_current(root, auth, freeze))
            (root / "runner.py").write_text("x=3\n")
            self.assertFalse(protocol.freeze_is_current(root, auth, freeze))

    def test_signed_final_manifest_is_the_single_second_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = {
                "schema": "FOLLOW60_MAINLINE_LOCK_V3_1",
                "lock_state": "LOCKED",
                "protected_scope_sha256": "scope-hash",
                "protected_entries": {"runner.py": {"sha256": "abc"}},
                "runtime_contract": {"engine": "FOLLOW60_V2_MAINLINE_V1"},
            }
            manifest_hash = protocol.manifest_candidate_hash(manifest)
            freeze = {
                "change_id": "change-2",
                "base_sha": "base-sha",
                "final_diff_hash": "diff-hash",
                "final_manifest_hash": manifest_hash,
                "final_protected_scope_hash": "scope-hash",
            }
            manifest["approval"] = {
                "change_id": freeze["change_id"],
                "base_sha": freeze["base_sha"],
                "final_diff_hash": freeze["final_diff_hash"],
                "final_manifest_hash": freeze["final_manifest_hash"],
                "final_protected_scope_hash": freeze["final_protected_scope_hash"],
                "token_status": "consumed_once",
            }
            payload = root / "manifest.json"
            payload.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            private = Ed25519PrivateKey.generate()
            signature = root / "manifest.sig"
            signature.write_bytes(private.sign(payload.read_bytes()))
            public = root / "public.pem"
            public.write_bytes(private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
            result = protocol.verify_final_approval(payload, signature, public, freeze)
            self.assertTrue(result["ok"], result)

            manifest["protected_entries"]["runner.py"]["sha256"] = "changed"
            payload.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            signature.write_bytes(private.sign(payload.read_bytes()))
            result = protocol.verify_final_approval(payload, signature, public, freeze)
            self.assertEqual(result["reason"], "final_manifest_candidate_hash_mismatch")

    def test_staged_commit_uses_signed_final_manifest_as_approval_two(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.check_call(["git", "init", "-q", str(root)])
            subprocess.check_call(["git", "-C", str(root), "config", "user.email", "test@example.com"])
            subprocess.check_call(["git", "-C", str(root), "config", "user.name", "Test"])
            (root / "runner.py").write_text("x=1\n")
            subprocess.check_call(["git", "-C", str(root), "add", "runner.py"])
            subprocess.check_call(["git", "-C", str(root), "commit", "-qm", "base"])
            base = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            (root / "runner.py").write_text("x=2\n")
            manifest_path = root / "manifest.json"
            signature_path = root / "manifest.sig"
            public_path = root / "public.pem"
            subprocess.check_call(["git", "-C", str(root), "add", "runner.py"])
            diff = subprocess.check_output(["git", "-C", str(root), "diff", "--cached", "--binary", "HEAD", "--", "runner.py"])
            from follow60_lock_v3 import canonical_json, git_file_entry, sha256_bytes, transitive_import_graph
            tree = subprocess.check_output(["git", "-C", str(root), "write-tree"], text=True).strip()
            entry = git_file_entry(root, "runner.py", revision=tree)
            entry.update({"role": "runtime_entrypoint", "lock_version": "3.1.0"})
            entries = {"runner.py": entry}
            graph = transitive_import_graph(root, entries)
            manifest = {
                "schema": "FOLLOW60_MAINLINE_LOCK_V3_1", "lock_version": "3.1.0", "lock_state": "LOCKED",
                "protected_entries": entries, "protected_scope_sha256": sha256_bytes(canonical_json(entries)),
                "protected_transitive_dependency_count": 0, "import_graph": graph,
                "import_graph_sha256": sha256_bytes(canonical_json(graph)),
                "runtime_contract": {"runtime_mode": "mainline", "binding_kind": "mainline", "engine": "FOLLOW60_V2_MAINLINE_V1", "entrypoint": "runner.py"},
                "audit_history": [{"change_id": "change-3", "files": ["runner.py"]}],
                "manifest_signature": "DETACHED_ED25519_REQUIRED", "field_certification": "PENDING_NEXT_NATURAL_RUNS",
            }
            body_hash = protocol.manifest_candidate_hash(manifest)
            manifest["approval"] = {
                "change_id": "change-3", "base_sha": base,
                "final_diff_hash": sha256_bytes(diff), "final_manifest_hash": body_hash,
                "final_protected_scope_hash": manifest["protected_scope_sha256"], "token_status": "consumed_once",
            }
            manifest_path.write_text(json.dumps(manifest, sort_keys=True))
            private = Ed25519PrivateKey.generate()
            signature_path.write_bytes(private.sign(manifest_path.read_bytes()))
            public_path.write_bytes(private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
            subprocess.check_call(["git", "-C", str(root), "add", "manifest.json", "manifest.sig"])
            result = protocol.verify_staged_final_approval(root, manifest_path, signature_path, public_path)
            self.assertTrue(result["ok"], result)
