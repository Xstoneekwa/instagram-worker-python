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
            subprocess.check_call(["git", "-C", str(root), "add", "."])
            freeze = protocol.freeze_candidate(root, auth, protected_scope_hash="scope")
            self.assertTrue(protocol.freeze_is_current(root, auth, freeze))
            (root / "runner.py").write_text("x=3\n")
            self.assertFalse(protocol.freeze_is_current(root, auth, freeze))

    def test_legacy_approval_stripped_body_hash_is_retired(self):
        with self.assertRaisesRegex(ValueError, "retired_body_hash"):
            protocol.manifest_candidate_hash({})

    def test_precommit_signed_manifest_cannot_authorize_a_tree(self):
        p = Path("/unused")
        self.assertFalse(protocol.verify_staged_final_approval(p, p, p, p)["ok"])
        self.assertFalse(protocol.verify_final_approval(p, p, p, {})["ok"])

    def test_commit_creation_does_not_grant_promotion(self):
        self.assertFalse(protocol.evaluate_gate(protocol.LockState.CANDIDATE_FROZEN, "commit").ok)
        self.assertTrue(protocol.evaluate_gate(protocol.LockState.CANDIDATE_FROZEN, "commit", frozen_index_verified=True).ok)
        self.assertFalse(protocol.evaluate_gate(protocol.LockState.CANDIDATE_COMMITTED, "release").ok)
        state = protocol.transition(protocol.LockState.CANDIDATE_FROZEN, protocol.LockState.CANDIDATE_COMMITTED)
        self.assertEqual(protocol.transition(state, protocol.LockState.MANIFEST_GENERATED), protocol.LockState.MANIFEST_GENERATED)
