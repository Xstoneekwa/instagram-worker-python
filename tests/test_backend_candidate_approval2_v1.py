"""Synthetic, offline fixtures for the external Backend Approval2 profile."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from follow60_candidate_identity_v2 import committed_identity, freeze_index
from follow60_change_protocol_v1 import verify_candidate_commit
from follow60_lock_v3 import (RESUME_CONTRACT_SOURCE_FILES, RESUME_CONTRACT_GATES,
                             bind_resume_receipt_provenance, git_file_entries)

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("backend_candidate_approval2_v1",
                                             ROOT / "scripts/backend-candidate-approval2-v1.py")
profile = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = profile
SPEC.loader.exec_module(profile)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE).decode().strip()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.chmod(0o644)
    path.write_bytes(value if isinstance(value, bytes) else profile.serialized(value))
    return path


def init(root):
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Synthetic fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "commit.gpgsign", "false")


class BackendApproval2Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="backend-approval2-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.w, self.b, self.doc = [self.home / x for x in ("worker", "backend", "document")]
        self.g = self.home / "governance"
        self.g.mkdir()
        self.key = Ed25519PrivateKey.generate()
        for r in (self.w, self.b, self.doc):
            init(r)
        put(self.w / profile.KEY_RELATIVE,
            self.key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
        required_scope = {
            "follow60_status_trust_cache_v1.py", "follow60_lock_v3.py",
            "follow60_candidate_identity_v2.py", "follow60_deployment_gate_v1.py",
            "follow60_external_deployment_lock_v2.py", "follow60_write_lock_v3_1.py",
            "phonefarm_runtime_control.py", "startup_tick_skip_token.py",
            "scripts/production_lineage_gate_v1.py", "scripts/verify-follow60-status-trust-v1.py",
            "scripts/install-follow60-status-trust-cache-v1.py",
            "scripts/run_control_dispatcher_service.sh", "runtime_artifacts.py",
        }
        for rel in required_scope | set(RESUME_CONTRACT_SOURCE_FILES):
            put(self.w / rel, b"# synthetic source, no runtime action\n")
        git(self.w, "add", ".")
        git(self.w, "commit", "-qm", "fixture worker base")
        worker_base = git(self.w, "rev-parse", "HEAD")
        self.paths = ["lib/source-%d.ts" % i for i in range(5)]
        for p in self.paths:
            put(self.b / p, b"// base\n")
        git(self.b, "add", ".")
        git(self.b, "commit", "-qm", "fixture backend base")
        put(self.doc / "README", b"fixture\n")
        git(self.doc, "add", ".")
        git(self.doc, "commit", "-qm", "fixture document base")
        for p in self.paths:
            put(self.b / p, b"// candidate\n")
        self.original = self.home / "original/APPROVAL1.json"
        self.e = self.original.parent / "execution-v1"
        note = put(self.home / "unchanged-proof.json", {"fixture": True})
        self.orig_a = self.request(self.b, self.paths, [note])
        self.orig_a["expected_current_sha256"] = {p: profile.digest(self.b / p) for p in self.paths}
        self.sign_request(self.original, self.orig_a)
        original_freeze, original_gate = self.commit(self.b, self.original, self.e)
        identity = committed_identity(self.b, self.orig_a["base_sha"], freeze=original_freeze)
        for p in self.paths:
            (self.b / p).chmod(0o444)
        put(self.e / "SIGNATURE_AND_PRE_FREEZE_FENCE.json", {
            "status": "PASS", "authorization_request_sha256": profile.digest(self.original),
            "signature_sha256": profile.digest(self.original.with_suffix(".sig"))})
        put(self.e / "COMMITTED_CANDIDATE_IDENTITY.json", {
            "post_commit_canonical_identity": "PASS", "identity": identity})
        source_entries = git_file_entries(self.w, RESUME_CONTRACT_SOURCE_FILES, revision=worker_base)
        resume = bind_resume_receipt_provenance({
            "schema": "RESUME_STATE_END_TO_END_CONTRACT_V1", "status": "PASS",
            "backend_sha": identity["candidate_commit_sha"],
            "gates": {p: "PASS" for p in RESUME_CONTRACT_GATES},
            "source_files": {p: v["sha256"] for p, v in source_entries.items()},
            "backend": {"status": "PASS", "source_files": self.orig_a["expected_current_sha256"]},
            "tests": {p: {"tests": n, "status": "PASS", "errors": 0, "failures": 0}
                      for p, n in zip(RESUME_CONTRACT_GATES, (4, 3, 71))}})
        put(self.e / "RESUME_CONTRACT_EXACT_COMMIT.json", resume)
        retained = [put(self.home / ("retained-%d.json" % i), {"exit_code": 0, "failures": [],
            "totals": [f"# tests {n}", f"# pass {n}", "# fail 0", "# skipped 0"]})
            for i, n in enumerate((91, 73))]
        retained.append(put(self.home / "negative.json", {
            "all_required_negative_controls_reject": True,
            "mutations": [{"expected_rejection": True, "exit_code": 1} for _ in range(5)]}))
        recert = {"status": "PASS", "candidate_identity": identity,
            "candidate_sha": identity["candidate_commit_sha"], "missing_canonical_delta": 0,
            "source_sha256": self.orig_a["expected_current_sha256"],
            "count_breakdown": {"backend_required_retained_exact_tree": 91,
                "extended_retained_exact_tree": 73, "python_resume_reexecuted_exact_commit": 78},
            "regression_test_count": 242,
            "retained_test_receipts": [profile.reference(p) for p in retained[:2]],
            "negative_contract_proof_reused": profile.reference(retained[2]),
            "new_exact_commit_resume_receipt": profile.reference(self.e / "RESUME_CONTRACT_EXACT_COMMIT.json")}
        put(self.e / "BACKEND_EXACT_COMMIT_RECERTIFICATION.json", recert)
        self.plan = put(self.g / "CROSS_REPOSITORY_CONTRACT.json", {"cross_repository_binding": {
            "original_approval1": str(self.original),
            "original_approval1_sha256": profile.digest(self.original),
            "original_approval1_signature_sha256": profile.digest(self.original.with_suffix(".sig")),
            "exact_backend_protected_paths": self.paths,
            "backend_file_hashes": self.orig_a["expected_current_sha256"],
            "target_backend_root": str(self.b), "external_governance_document": str(self.doc / profile.DOC_PATH),
            **{k: identity[v] for k, v in (("target_commit", "candidate_commit_sha"),
                ("target_tree", "candidate_tree_sha"), ("target_base", "base_commit_sha"),
                ("target_delta", "canonical_diff_sha256"))}}})
        proof = put(self.g / "proof.json", {"resume_worker_source_provenance": {
            "commit": worker_base, "entries": source_entries}})
        evidence = [self.original, self.original.with_suffix(".sig"), self.plan, proof] + list(self.e.glob("*.json"))
        self.ap = self.g / "APPROVAL1_WORKER_BACKEND_APPROVAL2_GOVERNANCE.json"
        self.bp = self.g / "APPROVAL1_BACKEND_BACKEND_APPROVAL2_GOVERNANCE.json"
        self.a = self.request(self.w, list(profile.TOOL_PATHS), evidence)
        self.ba = self.request(self.doc, [profile.DOC_PATH], [self.plan])
        for value in (self.a, self.ba):
            value.update(common_contract=profile.reference(self.plan), target_backend_candidate=identity,
                         qualification_evidence=profile.reference(proof))
        self.sign_request(self.ap, self.a)
        self.sign_request(self.bp, self.ba)
        for rel in profile.TOOL_PATHS:
            put(self.w / rel, (ROOT / rel).read_bytes())
        self.worker_execution = self.home / "worker-execution"
        worker_freeze, _ = self.commit(self.w, self.ap, self.worker_execution)
        put(self.doc / profile.DOC_PATH, b"# Synthetic external contract\n")
        self.doc_execution = self.home / "document-execution"
        self.commit(self.doc, self.bp, self.doc_execution)
        (self.doc / profile.DOC_PATH).chmod(0o444)
        self.inputs = profile.Inputs(self.b, self.w, self.g,
            self.doc_execution / "freeze.json", self.doc_execution / "PRE_COMMIT_CANONICAL_GATE.json")
        # Real unchanged Worker generator and verifier, not a stub admission.
        scope = put(self.home / "worker-scope.json", sorted(required_scope |
            set(RESUME_CONTRACT_SOURCE_FILES) | set(profile.TOOL_PATHS) | {profile.KEY_RELATIVE}))
        previous = put(self.home / "previous.json", {"audit_history": []})
        wreceipt = put(self.home / "worker-recert.json", {"status": "PASS",
            "candidate_sha": git(self.w, "rev-parse", "HEAD"), "missing_canonical_delta": 0,
            "regression_test_count": 1})
        self.wmanifest = self.home / "worker-manifest.json"
        process = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/regenerate-follow60-mainline-lock-v3-1.py"),
            "--root", str(self.w), "--scope", str(scope), "--base-manifest", str(previous),
            "--recertification-receipt", str(wreceipt), "--output", str(self.wmanifest),
            "--base-sha", self.a["base_sha"], "--change-id", self.a["change_id"],
            "--freeze", str(self.worker_execution / "freeze.json"),
            "--resume-contract-receipt", str(self.e / "RESUME_CONTRACT_EXACT_COMMIT.json")],
            capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(process.returncode, 0, process.stderr)
        self.wsig = self.wmanifest.with_suffix(".sig")
        self.wsig.write_bytes(self.key.sign(self.wmanifest.read_bytes()))
        self.output = self.home / "backend-certificate.json"

    def request(self, root, paths, dependencies):
        now = datetime.now(timezone.utc)
        return {"schema": profile.INITIAL_SCHEMA, "change_id": root.name, "nonce": root.name,
            "base_sha": git(root, "rev-parse", "HEAD"), "authorized_paths": paths,
            "authorized_dependency_scope": [str(p) + ";sha256=" + profile.digest(p) for p in dependencies],
            "one_task_only": True, "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "permits_final_promotion": False, "permits_runtime_activation": False,
            "repository_root": str(root)}

    def sign_request(self, path, value):
        put(path, value)
        put(path.with_suffix(".sig"), self.key.sign(path.read_bytes()))

    def commit(self, root, request, destination):
        destination.mkdir(parents=True)
        git(root, "add", ".")
        approval = profile.read(request)
        freeze = freeze_index(root, approval)
        put(destination / "freeze.json", freeze)
        gate = verify_candidate_commit(root, request, request.with_suffix(".sig"),
            self.w / profile.KEY_RELATIVE, destination / "freeze.json")
        self.assertTrue(gate["ok"], gate)
        gate["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
        put(destination / "PRE_COMMIT_CANONICAL_GATE.json", gate)
        git(root, "commit", "-qm", "signed fixture candidate")
        return freeze, gate

    def generate(self):
        return profile.generate(self.inputs, self.wmanifest, self.wsig, self.output)

    def refresh_evidence(self):
        """Re-sign synthetic authority to exercise deeper contradictory evidence."""
        self.a["authorized_dependency_scope"] = [p.rsplit(";sha256=", 1)[0] + ";sha256=" +
            profile.digest(p.rsplit(";sha256=", 1)[0]) for p in self.a["authorized_dependency_scope"]]
        self.sign_request(self.ap, self.a)

    def rejects(self, action, text=None):
        with self.assertRaises((ValueError, OSError, KeyError, subprocess.CalledProcessError)) as error:
            action()
        if text:
            self.assertIn(text, str(error.exception))

    def test_exact_external_candidate_qualification(self):
        value = profile.qualify(self.inputs)
        self.assertEqual(set(value["protected_entries"]), set(self.paths))
        self.assertEqual(value["candidate_identity"], self.a["target_backend_candidate"])
        self.assertFalse(value["deployment_authorized"])

    def test_generate_then_independent_verify_exact_bytes(self):
        self.assertTrue(self.generate()["ok"])
        sig = put(self.output.with_suffix(".sig"), self.key.sign(self.output.read_bytes()))
        result = profile.verify(self.inputs, self.output, sig, self.wmanifest, self.wsig)
        self.assertEqual(result["status"], "BACKEND_CANDIDATE_APPROVAL2_PASS")
        self.assertFalse(result["deployment_authorized"])

    def test_deterministic_certificate_and_no_candidate_writes(self):
        before = git(self.b, "status", "--porcelain=v1")
        value = profile.final_payload(self.inputs, self.wmanifest, self.wsig)
        self.assertEqual(value, profile.final_payload(self.inputs, self.wmanifest, self.wsig))
        self.assertEqual(git(self.b, "status", "--porcelain=v1"), before)

    def test_no_overwrite(self):
        put(self.output, b"keep")
        self.rejects(self.generate, "output_already_exists")
        self.assertEqual(self.output.read_bytes(), b"keep")

    def test_output_inside_backend_rejected(self):
        self.rejects(lambda: profile.generate(self.inputs, self.wmanifest, self.wsig, self.b / "sidecar"))

    def test_output_inside_worker_rejected(self):
        self.rejects(lambda: profile.generate(self.inputs, self.wmanifest, self.wsig, self.w / "sidecar"))

    def test_output_inside_document_repository_rejected(self):
        self.rejects(lambda: profile.generate(self.inputs, self.wmanifest, self.wsig, self.doc / "sidecar"))

    def test_output_symlink_rejected(self):
        self.output.symlink_to(self.home / "not-created")
        self.rejects(self.generate, "output_already_exists")
        self.assertFalse((self.home / "not-created").exists())

    def test_uncertified_tooling_refused(self):
        self.wsig.write_bytes(b"invalid")
        self.rejects(self.generate, "tooling_approval2_required")
        self.assertFalse(self.output.exists())

    def test_missing_tooling_signature_refused(self):
        self.wsig.unlink()
        self.rejects(self.generate, "tooling_approval2_required")

    def test_owner_signed_tooling_wrong_approval1_lineage_rejected(self):
        value = profile.read(self.wmanifest)
        value["approval"]["change_id"] = "another-authorization"
        put(self.wmanifest, value)
        self.wsig.write_bytes(self.key.sign(self.wmanifest.read_bytes()))
        self.rejects(self.generate, "tooling_authorization_lineage_mismatch")

    def test_modified_backend_source_refused(self):
        put(self.b / self.paths[0], b"changed")
        self.rejects(lambda: profile.qualify(self.inputs))

    def test_writable_protected_source_refused(self):
        (self.b / self.paths[0]).chmod(0o644)
        self.rejects(lambda: profile.qualify(self.inputs), "backend_source_not_read_only")

    def test_extra_untracked_path_refused(self):
        put(self.b / "extra", b"outside")
        self.rejects(lambda: profile.qualify(self.inputs))

    def test_same_tree_other_commit_refused(self):
        git(self.b, "commit", "--allow-empty", "-qm", "different commit")
        self.rejects(lambda: profile.qualify(self.inputs), "target_identity_mismatch")

    def test_original_signature_missing(self):
        self.original.with_suffix(".sig").unlink()
        self.rejects(lambda: profile.qualify(self.inputs))

    def test_governance_signature_invalid(self):
        put(self.ap.with_suffix(".sig"), b"bad")
        self.rejects(lambda: profile.qualify(self.inputs), "signature_invalid")

    def test_doc_authority_signature_invalid(self):
        put(self.bp.with_suffix(".sig"), b"bad")
        self.rejects(lambda: profile.qualify(self.inputs), "signature_invalid")

    def test_governance_scope_extra_rejected(self):
        self.a["authorized_paths"].append("unexpected.py")
        self.sign_request(self.ap, self.a)
        self.rejects(lambda: profile.qualify(self.inputs), "authorization_scope_mismatch")

    def test_changed_common_contract_rejected(self):
        put(self.plan, {"wrong": True})
        self.rejects(lambda: profile.qualify(self.inputs), "authorization_dependency_changed")

    def test_false_historical_gate_rejected(self):
        p = self.e / "PRE_COMMIT_CANONICAL_GATE.json"
        value = profile.read(p);value["ok"] = False;put(p, value)
        self.refresh_evidence()
        self.rejects(lambda: profile.qualify(self.inputs), "historical_commit_gate_failed")

    def test_outside_original_authorization_time_rejected(self):
        p = self.e / "PRE_COMMIT_CANONICAL_GATE.json"
        value = profile.read(p);value["checked_at_utc"] = "2000-01-01T00:00:00+00:00";put(p, value)
        self.refresh_evidence()
        self.rejects(lambda: profile.qualify(self.inputs), "historical_admission_outside_authorization")

    def test_changed_freeze_rejected(self):
        p = self.e / "freeze.json"
        value = profile.read(p);value["canonical_diff_sha256"] = "0" * 64;put(p, value)
        self.refresh_evidence()
        self.rejects(lambda: profile.qualify(self.inputs))

    def test_recertification_failed_rejected(self):
        p = self.e / "BACKEND_EXACT_COMMIT_RECERTIFICATION.json"
        value = profile.read(p);value["status"] = "FAIL";put(p, value)
        self.refresh_evidence()
        self.rejects(lambda: profile.qualify(self.inputs), "recertification_not_bound")

    def test_missing_regression_count_rejected(self):
        p = self.e / "BACKEND_EXACT_COMMIT_RECERTIFICATION.json"
        value = profile.read(p);value["regression_test_count"] = 241;put(p, value)
        self.refresh_evidence()
        self.rejects(lambda: profile.qualify(self.inputs), "required_regression_counts_invalid")

    def test_retained_receipt_modified_rejected(self):
        put(self.home / "retained-0.json", {"status": "FAIL"})
        self.rejects(lambda: profile.qualify(self.inputs), "evidence_hash_mismatch")

    def test_bound_failed_regression_receipt_rejected(self):
        p = self.home / "retained-0.json"
        value = profile.read(p);value["exit_code"] = 1;put(p, value)
        rp = self.e / "BACKEND_EXACT_COMMIT_RECERTIFICATION.json"
        recert = profile.read(rp);recert["retained_test_receipts"][0] = profile.reference(p);put(rp, recert)
        self.refresh_evidence()
        self.rejects(lambda: profile.qualify(self.inputs), "regression_receipt_failed_or_incomplete")

    def test_bound_incomplete_negative_controls_rejected(self):
        p = self.home / "negative.json"
        value = profile.read(p);value["mutations"].pop();put(p, value)
        rp = self.e / "BACKEND_EXACT_COMMIT_RECERTIFICATION.json"
        recert = profile.read(rp);recert["negative_contract_proof_reused"] = profile.reference(p);put(rp, recert)
        self.refresh_evidence()
        self.rejects(lambda: profile.qualify(self.inputs), "negative_contract_evidence_invalid")

    def test_resume_wrong_test_count_rejected(self):
        p = self.e / "RESUME_CONTRACT_EXACT_COMMIT.json"
        value = profile.read(p);value["tests"][RESUME_CONTRACT_GATES[0]]["tests"] = 0
        put(p, bind_resume_receipt_provenance(value))
        rp = self.e / "BACKEND_EXACT_COMMIT_RECERTIFICATION.json"
        recert = profile.read(rp);recert["new_exact_commit_resume_receipt"] = profile.reference(p);put(rp, recert)
        self.refresh_evidence()
        self.rejects(lambda: profile.qualify(self.inputs), "resume_test_evidence_invalid")

    def test_resume_source_and_gate_validation_is_canonical(self):
        p = self.e / "RESUME_CONTRACT_EXACT_COMMIT.json"
        value = profile.read(p);value["gates"][RESUME_CONTRACT_GATES[0]] = "FAIL"
        put(p, bind_resume_receipt_provenance(value))
        rp = self.e / "BACKEND_EXACT_COMMIT_RECERTIFICATION.json"
        r = profile.read(rp);r["new_exact_commit_resume_receipt"] = profile.reference(p);put(rp, r)
        self.refresh_evidence()
        self.rejects(lambda: profile.qualify(self.inputs), "resume_contract_invalid")

    def test_document_content_drift_refused(self):
        put(self.doc / profile.DOC_PATH, b"changed")
        self.rejects(lambda: profile.qualify(self.inputs))

    def test_document_scope_and_freeze_refused(self):
        p = self.inputs.document_freeze
        value = profile.read(p);value["changed_paths"].append("extra")
        put(p, value)
        self.rejects(lambda: profile.qualify(self.inputs), "unauthorized_or_missing_delta")

    def test_final_signature_wrong_key_refused(self):
        self.generate()
        sig = put(self.output.with_suffix(".sig"), Ed25519PrivateKey.generate().sign(self.output.read_bytes()))
        self.rejects(lambda: profile.verify(self.inputs, self.output, sig, self.wmanifest, self.wsig), "signature_invalid")

    def test_final_signature_absent_refused(self):
        self.generate()
        self.rejects(lambda: profile.verify(self.inputs, self.output, self.home / "missing.sig", self.wmanifest, self.wsig))

    def test_final_certificate_modified_after_signing_refused(self):
        self.generate()
        sig = put(self.output.with_suffix(".sig"), self.key.sign(self.output.read_bytes()))
        self.output.write_bytes(self.output.read_bytes() + b" ")
        self.rejects(lambda: profile.verify(self.inputs, self.output, sig, self.wmanifest, self.wsig), "signature_invalid")

    def test_owner_signed_contradictory_certificate_refused(self):
        self.generate()
        value = profile.read(self.output);value["deployment_authorized"] = True;put(self.output, value)
        sig = put(self.output.with_suffix(".sig"), self.key.sign(self.output.read_bytes()))
        self.rejects(lambda: profile.verify(self.inputs, self.output, sig, self.wmanifest, self.wsig), "backend_certificate_binding_mismatch")

    def test_duplicate_json_is_not_accepted(self):
        p = put(self.home / "ambiguous.json", b'{"x":1,"x":2}')
        self.rejects(lambda: profile.read(p), "duplicate_json_key")

    def test_cli_fails_closed_without_qualification_inputs(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = profile.main(["qualify", "--backend-root", str(self.b),
                "--governance-dir", str(self.home / "missing"),
                "--document-freeze", str(self.inputs.document_freeze),
                "--document-gate", str(self.inputs.document_gate)])
        self.assertEqual(result, 2)
        self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_current_contract_document_and_cli_share_explicit_boundaries(self):
        doc = Path("/Users/admin/Projects/phonefarm-backend-approval2-document-20261019") / profile.DOC_PATH
        if not doc.exists():
            self.fail("authorized contract document must be available for qualification")
        text = doc.read_text()
        for term in (profile.SCHEMA, "qualify", "generate", "verify", "CLIENT_LIST", "AI_AGENT",
                     "96b65aa848a8e4f0b303562f97939cb69886b78b", "91/91", "73/73", "78/78"):
            self.assertIn(term, text)


if __name__ == "__main__":
    unittest.main()
