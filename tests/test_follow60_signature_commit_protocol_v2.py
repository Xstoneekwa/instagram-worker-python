"""Offline A-I acceptance: synthetic Git repositories and ephemeral signing keys."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
from follow60_candidate_identity_v2 import (
    canonical_diff, commit_sha, commit_tree, committed_identity, freeze_index, git, manifest_sha256,
)
from follow60_change_protocol_v1 import verify_candidate_commit
from follow60_deployment_gate_v1 import verify_deployment_candidate
from follow60_lock_v3 import RESUME_CONTRACT_SOURCE_FILES, verify_repository
from tests import test_follow60_deployment_gate_v1 as deployment_fixture

SOURCE = Path(__file__).resolve().parents[1]
GATE_RECEIPTS = []


class SignatureCommitProtocolV2Tests(unittest.TestCase):
    def setUp(self):
        helper = deployment_fixture.Follow60DeploymentGateV1Tests()
        temporary, self.root, self.base, self.key, self.public, self.manifest, self.signature = helper._repo()
        self.addCleanup(temporary.cleanup)
        self.evidence = self.root.parent
        self.auth_path = self.evidence / "approval-1.json"
        self.auth_sig = self.evidence / "approval-1.sig"
        self.freeze_path = self.evidence / "freeze.json"
        now = datetime.now(timezone.utc)
        self.authorization = {
            "schema": "FOLLOW60_CHANGE_AUTHORIZATION_REQUEST_V1", "change_id": "synthetic-protocol",
            "base_sha": self.base, "authorized_paths": ["protected.py"],
            "authorized_dependency_scope": ["protected.py"],
            "issued_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
            "nonce": "synthetic-protocol", "one_task_only": True,
            "permits_final_promotion": False, "permits_runtime_activation": False,
        }
        self.auth_path.write_text(json.dumps(self.authorization))
        self.auth_sig.write_bytes(self.key.sign(self.auth_path.read_bytes()))
        (self.root / "protected.py").write_text("VALUE = 2\n")
        git(self.root, "add", "protected.py")
        self.freeze = freeze_index(self.root, self.authorization)
        self.freeze_path.write_text(json.dumps(self.freeze))
        self.assertTrue(self.commit_gate()["ok"])
        git(self.root, "commit", "-qm", "final synthetic candidate")
        self.sha = commit_sha(self.root, "HEAD")
        self.payload = helper._manifest(self.root, self.sha)
        self.payload["candidate_identity"] = committed_identity(self.root, self.base, freeze=self.freeze)
        self.payload["approval"].update(base_sha=self.base, change_id=self.authorization["change_id"])
        self.sign()

    def sign(self, key=None):
        self.manifest.write_text(json.dumps(self.payload, indent=2, sort_keys=True) + "\n")
        self.signature.write_bytes((key or self.key).sign(self.manifest.read_bytes()))

    def gate(self):
        result = verify_deployment_candidate(self.root, manifest_path=self.manifest,
                                            signature_path=self.signature, public_key_path=self.public)
        GATE_RECEIPTS.append({"case": self.id(), "head_commit_sha": commit_sha(self.root, "HEAD"),
                              "manifest_sha256": manifest_sha256(self.manifest), "result": result})
        return result

    def commit_gate(self):
        return verify_candidate_commit(self.root, self.auth_path, self.auth_sig, self.public, self.freeze_path)

    def test_A_correct_commit_tree_distinction_passes(self):
        self.assertNotEqual(self.sha, commit_tree(self.root, self.sha))
        result = self.gate()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["manifest_sha256"], manifest_sha256(self.manifest))
        self.assertEqual(self.freeze["canonical_diff_sha256"], result["candidate_identity"]["canonical_diff_sha256"])

    def test_B_tree_substituted_for_commit_fails(self):
        tree = commit_tree(self.root, self.sha)
        self.payload["candidate_identity"]["candidate_commit_sha"] = tree
        self.sign()
        self.assertEqual(self.gate()["reason"], "expected_commit_object")
        result = verify_repository(self.root, self.manifest, revision=tree,
                                   signature_path=self.signature, public_key_path=self.public,
                                   enforce_exact_candidate=False)
        self.assertFalse(result["ok"], result)

    def test_C_manifest_before_final_commit_fails_even_with_same_tree(self):
        previous_tree = commit_tree(self.root, self.sha)
        git(self.root, "commit", "--allow-empty", "-qm", "actual final commit after premature manifest")
        self.assertEqual(commit_tree(self.root, "HEAD"), previous_tree)
        self.assertFalse(self.gate()["ok"])

    def test_D_candidate_changed_after_manifest_fails(self):
        (self.root / "protected.py").write_text("VALUE = 3\n")
        self.assertEqual(self.gate()["reason"], "candidate_worktree_or_index_dirty")
        git(self.root, "add", "protected.py")
        self.assertFalse(self.gate()["ok"])
        git(self.root, "commit", "-qm", "changed after signature")
        self.assertFalse(self.gate()["ok"])

    def test_E_different_diff_implementation_fails(self):
        raw_diff = git(self.root, "diff", "--binary", self.base, self.sha)
        self.payload["candidate_identity"]["canonical_diff_sha256"] = hashlib.sha256(raw_diff).hexdigest()
        self.sign()
        self.assertEqual(self.gate()["reason"], "candidate_identity_mismatch")
        self.payload["candidate_identity"] = committed_identity(self.root, self.base)
        self.payload["candidate_identity"]["diff_serialization"] = "alternative"
        self.sign()
        self.assertFalse(self.gate()["ok"])

    def test_F_tampered_exact_manifest_fails(self):
        original_hash = manifest_sha256(self.manifest)
        self.manifest.write_bytes(self.manifest.read_bytes() + b" ")
        self.assertNotEqual(original_hash, manifest_sha256(self.manifest))
        self.assertFalse(self.gate()["ok"])

    def test_G_wrong_signature_fails(self):
        self.sign(Ed25519PrivateKey.generate())
        self.assertFalse(self.gate()["ok"])

    def test_H_previous_candidate_signature_fails(self):
        old_signature = self.signature.read_bytes()
        git(self.root, "commit", "--allow-empty", "-qm", "next candidate")
        self.payload["candidate_identity"] = committed_identity(self.root, self.base)
        self.payload["certified_candidate_sha"] = commit_sha(self.root, "HEAD")
        self.payload["approval"]["head_sha"] = commit_sha(self.root, "HEAD")
        self.sign()
        self.assertTrue(self.gate()["ok"])
        self.signature.write_bytes(old_signature)
        self.assertFalse(self.gate()["ok"])

    def test_I_generator_external_signer_and_deployment_cli_pass(self):
        scope = self.evidence / "scope.json"
        previous = self.evidence / "previous.json"
        recert = self.evidence / "recertification.json"
        resume = self.evidence / "resume-contract.json"
        generated = self.evidence / "generated.json"
        detached = self.evidence / "generated.sig"
        private = self.evidence / "ephemeral-test-only.pem"
        scope.write_text(json.dumps(["protected.py", *RESUME_CONTRACT_SOURCE_FILES]))
        previous.write_text("{}")
        recert.write_text(json.dumps({"candidate_sha": self.sha, **self.payload["recertification"]}))
        resume.write_text(json.dumps(self.payload["resume_state_contract"]))
        private.write_bytes(self.key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
        args = [sys.executable, str(SOURCE / "scripts/regenerate-follow60-mainline-lock-v3-1.py"),
                "--root", str(self.root), "--scope", str(scope), "--base-manifest", str(previous),
                "--recertification-receipt", str(recert), "--resume-contract-receipt", str(resume),
                "--freeze", str(self.freeze_path), "--output", str(generated),
                "--base-sha", self.base, "--change-id", self.authorization["change_id"]]
        receipt = json.loads(subprocess.check_output(args))
        self.assertEqual(receipt["candidate_identity"], self.payload["candidate_identity"])
        self.assertEqual(receipt["manifest_sha256"], manifest_sha256(generated))
        env = dict(os.environ, PATH="/opt/homebrew/bin:/usr/bin:/bin")
        subprocess.check_output([sys.executable, str(SOURCE / "scripts/FOLLOW60_LOCK_APPROVE_CHANGE"),
            "--root", str(self.root), "--manifest", str(generated), "--private-key", str(private),
            "--signature-output", str(detached)], env=env)
        command = [sys.executable, str(SOURCE / "scripts/verify-follow60-deployment-candidate-v1.py"),
                   "--target-root", str(self.root), "--manifest", str(generated),
                   "--signature", str(detached), "--public-key", str(self.public)]
        result = json.loads(subprocess.check_output(command))
        self.assertTrue(result["ok"], result)
        GATE_RECEIPTS.append({"case": self.id(), "result": result,
                              "sequence": ["freeze", "final_commit", "manifest", "external_test_signature", "deployment_gate"],
                              "signed_manifest": generated.read_text(),
                              "signature_hex": detached.read_bytes().hex(),
                              "public_key": self.public.read_text(), "test_key_only": True})
        generated.write_bytes(generated.read_bytes() + b" ")
        self.assertNotEqual(subprocess.run(command, stdout=subprocess.PIPE).returncode, 0)
        self.assertNotEqual(subprocess.run(args, capture_output=True).returncode, 0)

    def test_generator_refuses_dirty_precommit_candidate(self):
        (self.root / "protected.py").write_text("VALUE = 4\n")
        git(self.root, "add", "protected.py")
        with self.assertRaisesRegex(ValueError, "dirty"):
            committed_identity(self.root, self.base, freeze=self.freeze)

    def test_missing_identity_and_legacy_hash_fields_fail_closed(self):
        identity = self.payload.pop("candidate_identity")
        self.sign()
        self.assertFalse(self.gate()["ok"])
        self.payload["candidate_identity"] = identity
        self.payload["approval"]["final_manifest_hash"] = "a" * 64
        self.sign()
        self.assertEqual(self.gate()["reason"], "ambiguous_legacy_hash_field")

    def test_freeze_changed_index_and_wrong_authorization_are_rejected(self):
        self.assertFalse(self.commit_gate()["ok"])
        self.authorization["base_sha"] = self.sha
        self.auth_path.write_text(json.dumps(self.authorization))
        self.auth_sig.write_bytes(self.key.sign(self.auth_path.read_bytes()))
        (self.root / "protected.py").write_text("VALUE = 3\n")
        git(self.root, "add", "protected.py")
        self.freeze_path.write_text(json.dumps(freeze_index(self.root, self.authorization)))
        self.assertTrue(self.commit_gate()["ok"])
        (self.root / "protected.py").write_text("VALUE = 4\n")
        git(self.root, "add", "protected.py")
        self.assertEqual(self.commit_gate()["reason"], "staged_candidate_changed_after_freeze")
        self.auth_sig.write_bytes(Ed25519PrivateKey.generate().sign(self.auth_path.read_bytes()))
        self.assertFalse(self.commit_gate()["ok"])

    def test_diff_binary_delete_mode_and_rename_are_deterministic(self):
        (self.root / "binary").write_bytes(b"\0\xff\n")
        (self.root / "protected.py").rename(self.root / "renamed.py")
        (self.root / "renamed.py").chmod(0o755)
        git(self.root, "add", "-A")
        tree = git(self.root, "write-tree").decode().strip()
        delta = canonical_diff(self.root, self.sha, tree)
        self.assertEqual([row["path"] for row in delta["payload"]["changes"]], ["binary", "protected.py", "renamed.py"])
        git(self.root, "config", "diff.renames", "true")
        git(self.root, "config", "core.quotePath", "false")
        self.assertEqual(delta, canonical_diff(self.root, self.sha, tree))

    def test_real_hook_commit_without_final_signature_cannot_promote(self):
        for name in ("follow60_candidate_identity_v2.py", "follow60_change_protocol_v1.py", "follow60_lock_v3.py",
                     "scripts/guard-follow60-protected-index-v3-1.py", ".githooks/pre-commit"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / name, target)
        public = self.root / "docs/governance/FOLLOW60_MAINLINE_LOCK_V3_1_PUBLIC_KEY.pem"
        public.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.public, public)
        (self.root / ".githooks/pre-commit").chmod(0o755)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "synthetic governance bootstrap")
        self.authorization["base_sha"] = commit_sha(self.root, "HEAD")
        self.auth_path.write_text(json.dumps(self.authorization))
        self.auth_sig.write_bytes(self.key.sign(self.auth_path.read_bytes()))
        git(self.root, "config", "core.hooksPath", ".githooks")
        git(self.root, "config", "follow60.candidateEvidenceDir", str(self.evidence))
        (self.root / "protected.py").write_text("VALUE = 5\n")
        git(self.root, "add", "protected.py")
        self.freeze_path.write_text(json.dumps(freeze_index(self.root, self.authorization)))
        try:
            with patch.dict(os.environ, {"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin"}):
                git(self.root, "commit", "-qm", "admitted unsigned final candidate")
        except subprocess.CalledProcessError as exc:
            self.fail(exc.stderr.decode())
        self.assertFalse(self.gate()["ok"])
        (self.root / "protected.py").write_text("VALUE = 6\n")
        git(self.root, "add", "protected.py")
        with patch.dict(os.environ, {"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin"}):
            with self.assertRaises(subprocess.CalledProcessError) as rejected:
                git(self.root, "commit", "-qm", "must reject reused initial approval")
        self.assertIn("authorization_base_sha_mismatch", rejected.exception.stderr.decode())

    def test_installed_release_sidecars_are_not_committed_source(self):
        target = self.root / "docs/governance/FOLLOW60_MAINLINE_LOCK_V3.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.manifest.read_bytes())
        signature = target.with_suffix(".sig")
        signature.write_bytes(self.signature.read_bytes())
        result = verify_deployment_candidate(self.root, manifest_path=target,
                                             signature_path=signature, public_key_path=self.public)
        self.assertTrue(result["ok"], result)
        (self.root / "injected.py").write_text("# unapproved\n")
        self.assertFalse(verify_deployment_candidate(self.root, manifest_path=target,
                                                     signature_path=signature, public_key_path=self.public)["ok"])


if __name__ == "__main__":
    unittest.main()
