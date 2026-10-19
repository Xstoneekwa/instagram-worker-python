#!/usr/bin/env python3
"""External Backend candidate certification. No signing, networking or activation."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from follow60_candidate_identity_v2 import (  # noqa: E402
    canonical_diff, committed_identity, external_path, require_clean,
    verify_manifest_identity,
)
from follow60_lock_v3 import (  # noqa: E402
    RESUME_CONTRACT_SOURCE_FILES, git_file_entries, verify_detached_signature,
    verify_resume_state_contract_evidence,
)
from follow60_deployment_gate_v1 import verify_deployment_candidate  # noqa: E402

SCHEMA = "PHONE_FARM_BACKEND_CANDIDATE_APPROVAL2_V1"
INITIAL_SCHEMA = "FOLLOW60_CHANGE_AUTHORIZATION_REQUEST_V1"
KEY_RELATIVE = "docs/governance/FOLLOW60_MAINLINE_LOCK_V3_1_PUBLIC_KEY.pem"
KEY_SHA256 = "325cadb1c3969550651936a8d60038bb09a295a7494342bbee79a9aff627097f"
TOOL_PATHS = ("scripts/backend-candidate-approval2-v1.py",
              "tests/test_backend_candidate_approval2_v1.py")
DOC_PATH = "docs/governance/BACKEND_CANDIDATE_APPROVAL2_V1.md"


class Rejected(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Rejected(reason)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reference(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": digest(path)}


def _pairs(pairs):
    result = {}
    for name, value in pairs:
        require(name not in result, "duplicate_json_key")
        result[name] = value
    return result


def read(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    require(isinstance(value, dict), "json_object_required")
    return value


def bound(ref):
    require(set(ref) == {"path", "sha256"}, "reference_contract_invalid")
    require(digest(ref["path"]) == ref["sha256"], "evidence_hash_mismatch")
    return read(ref["path"])


def signed(path, signature, key):
    ok, reason = verify_detached_signature(Path(path), Path(signature), Path(key))
    require(ok, reason)
    return read(path)


def authorization(path, signature, key, paths):
    value = signed(path, signature, key)
    require(value.get("schema") == INITIAL_SCHEMA, "authorization_schema_invalid")
    require(value.get("one_task_only") is True, "authorization_not_scoped")
    require(value.get("nonce") == value.get("change_id"), "authorization_nonce_invalid")
    require(not value.get("permits_final_promotion") and
            not value.get("permits_runtime_activation"), "authorization_privilege_invalid")
    require(sorted(value["authorized_paths"]) == sorted(paths) and
            len(value["authorized_paths"]) == len(paths), "authorization_scope_mismatch")
    return value


def dependencies(value):
    refs = {}
    for entry in value["authorized_dependency_scope"]:
        path, expected = entry.rsplit(";sha256=", 1)
        path = str(Path(path).resolve(strict=True))
        require(path not in refs, "duplicate_dependency")
        require(digest(path) == expected, "authorization_dependency_changed")
        refs[path] = expected
    require(bool(refs), "authorization_dependencies_empty")
    return refs


def admitted(root, approval, freeze, gate):
    """Check consumed historical commit admission, never renew Approval1."""
    identity = committed_identity(root, approval["base_sha"], freeze=freeze)
    delta = canonical_diff(root, approval["base_sha"], identity["candidate_tree_sha"])
    actual_paths = sorted(row["path"] for row in delta["payload"]["changes"])
    require(actual_paths == sorted(approval["authorized_paths"]) ==
            sorted(freeze["changed_paths"]), "unauthorized_or_missing_delta")
    require(gate.get("ok") is True and
            gate.get("reason") == "candidate_commit_only_not_deployable",
            "historical_commit_gate_failed")
    for field in ("schema", "base_commit_sha", "candidate_tree_sha",
                  "canonical_diff_sha256", "change_id", "changed_paths"):
        require(gate[field] == freeze[field], "historical_gate_freeze_mismatch")
    require(freeze["change_id"] == approval["change_id"] and
            freeze.get("deployment_authorized") is False, "freeze_authority_invalid")
    issued, checked, expiry = (datetime.fromisoformat(x.replace("Z", "+00:00"))
                              for x in (approval["issued_at"], gate["checked_at_utc"],
                                        approval["expires_at"]))
    require(all(x.tzinfo is not None for x in (issued, checked, expiry)) and
            issued <= checked <= expiry, "historical_admission_outside_authorization")
    return identity


@dataclass(frozen=True)
class Inputs:
    backend_root: Path
    worker_root: Path
    governance_dir: Path
    document_freeze: Path
    document_gate: Path

    @property
    def key(self):
        return self.worker_root / KEY_RELATIVE


def qualify(inputs):
    """Read-only qualification. This cannot certify the new tooling itself."""
    d = inputs.governance_dir.resolve(strict=True)
    ap = d / "APPROVAL1_WORKER_BACKEND_APPROVAL2_GOVERNANCE.json"
    bp = d / "APPROVAL1_BACKEND_BACKEND_APPROVAL2_GOVERNANCE.json"
    a = authorization(ap, ap.with_suffix(".sig"), inputs.key, TOOL_PATHS)
    b = authorization(bp, bp.with_suffix(".sig"), inputs.key, [DOC_PATH])
    a_deps, b_deps = dependencies(a), dependencies(b)
    require(a["common_contract"] == b["common_contract"], "cross_contract_mismatch")
    for dep in (a_deps, b_deps):
        require(dep.get(str(Path(a["common_contract"]["path"]).resolve())) ==
                a["common_contract"]["sha256"], "common_contract_not_authorized")
    plan = bound(a["common_contract"])
    binding = plan["cross_repository_binding"]
    require(Path(a["repository_root"]).resolve() == inputs.worker_root.resolve(),
            "worker_repository_mismatch")
    require(Path(binding["target_backend_root"]).resolve() == inputs.backend_root.resolve(),
            "backend_repository_mismatch")
    require(a["target_backend_candidate"] == b["target_backend_candidate"],
            "governance_candidate_binding_mismatch")
    original = Path(binding["original_approval1"])
    require(digest(original) == binding["original_approval1_sha256"] and
            digest(original.with_suffix(".sig")) == binding["original_approval1_signature_sha256"],
            "original_authority_changed")
    expected_paths = binding["exact_backend_protected_paths"]
    require(len(expected_paths) == 5 and len(set(expected_paths)) == 5, "backend_scope_not_five")
    original_a = authorization(original, original.with_suffix(".sig"), inputs.key, expected_paths)
    dependencies(original_a)
    execution = original.parent / "execution-v1"
    evidence_paths = [original, original.with_suffix(".sig")] + [execution / name for name in (
        "freeze.json", "PRE_COMMIT_CANONICAL_GATE.json", "SIGNATURE_AND_PRE_FREEZE_FENCE.json",
        "COMMITTED_CANDIDATE_IDENTITY.json", "BACKEND_EXACT_COMMIT_RECERTIFICATION.json",
        "RESUME_CONTRACT_EXACT_COMMIT.json")]
    for p in evidence_paths:
        require(a_deps.get(str(p.resolve())) == digest(p), "candidate_evidence_not_authorized")
    freeze, gate = (read(execution / n) for n in ("freeze.json", "PRE_COMMIT_CANONICAL_GATE.json"))
    identity = admitted(inputs.backend_root, original_a, freeze, gate)
    require(identity == a["target_backend_candidate"], "target_identity_mismatch")
    for name, field in (("target_commit", "candidate_commit_sha"),
                        ("target_tree", "candidate_tree_sha"), ("target_base", "base_commit_sha"),
                        ("target_delta", "canonical_diff_sha256")):
        require(binding[name] == identity[field], "plan_identity_mismatch")
    fence = read(execution / "SIGNATURE_AND_PRE_FREEZE_FENCE.json")
    require(fence["status"] == "PASS" and
            fence["authorization_request_sha256"] == digest(original) and
            fence["signature_sha256"] == digest(original.with_suffix(".sig")), "original_signature_fence_invalid")
    post = read(execution / "COMMITTED_CANDIDATE_IDENTITY.json")
    require(post["post_commit_canonical_identity"] == "PASS" and post["identity"] == identity,
            "post_commit_identity_invalid")
    entries = git_file_entries(inputs.backend_root, expected_paths,
                               revision=identity["candidate_commit_sha"])
    hashes = original_a["expected_current_sha256"]
    require(hashes == binding["backend_file_hashes"], "authorized_backend_hashes_mismatch")
    for path in expected_paths:
        source = inputs.backend_root / path
        require(entries[path]["sha256"] == hashes[path] == digest(source), "backend_source_changed")
        require(not source.stat().st_mode & 0o222, "backend_source_not_read_only")
    recert = read(execution / "BACKEND_EXACT_COMMIT_RECERTIFICATION.json")
    require(recert["status"] == "PASS" and recert["candidate_identity"] == identity and
            recert["candidate_sha"] == identity["candidate_commit_sha"] and
            recert["missing_canonical_delta"] == 0 and recert["source_sha256"] == hashes,
            "recertification_not_bound")
    require(recert["count_breakdown"] == {"backend_required_retained_exact_tree": 91,
            "extended_retained_exact_tree": 73, "python_resume_reexecuted_exact_commit": 78} and
            recert["regression_test_count"] == 242, "required_regression_counts_invalid")
    receipt_refs = recert["retained_test_receipts"] + [recert["new_exact_commit_resume_receipt"],
                                                     recert["negative_contract_proof_reused"]]
    retained = [bound(ref) for ref in recert["retained_test_receipts"]]
    require(len(retained) >= 2, "regression_receipts_missing")
    for row, count in zip(retained, (91, 73)):
        require(row.get("exit_code") == 0 and row.get("failures") == [] and
                all(line in row.get("totals", []) for line in
                    (f"# tests {count}", f"# pass {count}", "# fail 0", "# skipped 0")),
                "regression_receipt_failed_or_incomplete")
    negative = bound(recert["negative_contract_proof_reused"])
    require(negative.get("all_required_negative_controls_reject") is True and
            len(negative.get("mutations", [])) == 5 and
            all(row.get("expected_rejection") is True and row.get("exit_code") != 0
                for row in negative["mutations"]), "negative_contract_evidence_invalid")
    resume = bound(recert["new_exact_commit_resume_receipt"])
    require(resume["backend_sha"] == identity["candidate_commit_sha"], "resume_backend_mismatch")
    require(set(resume["tests"]) == set(resume["gates"]) and
            sum(row["tests"] for row in resume["tests"].values()) == 78 and
            all(row["status"] == "PASS" and row["errors"] == 0 and row["failures"] == 0
                for row in resume["tests"].values()), "resume_test_evidence_invalid")
    proof = bound(a["qualification_evidence"])
    worker_commit = proof["resume_worker_source_provenance"]["commit"]
    worker_entries = git_file_entries(inputs.worker_root, RESUME_CONTRACT_SOURCE_FILES,
                                      revision=worker_commit)
    require(worker_entries == proof["resume_worker_source_provenance"]["entries"],
            "worker_contract_provenance_changed")
    require(verify_resume_state_contract_evidence({"resume_state_contract": resume,
            "protected_entries": worker_entries})["ok"], "resume_contract_invalid")
    source_refs = resume["backend"]["source_files"]
    current = git_file_entries(inputs.backend_root, source_refs,
                               revision=identity["candidate_commit_sha"])
    require(all(current[p]["sha256"] == h for p, h in source_refs.items()),
            "resume_backend_source_mismatch")
    doc_root = Path(b["repository_root"]).resolve(strict=True)
    doc = doc_root / DOC_PATH
    require(doc.resolve() == Path(binding["external_governance_document"]).resolve(),
            "document_path_mismatch")
    doc_identity = admitted(doc_root, b, read(inputs.document_freeze), read(inputs.document_gate))
    require(doc.is_file() and not doc.is_symlink(), "document_missing_or_symlink")
    require(not doc.stat().st_mode & 0o222, "document_not_read_only")
    payload = {
        "schema": SCHEMA, "candidate_identity": identity,
        "certified_candidate_sha": identity["candidate_commit_sha"],
        "approval": {"base_sha": identity["base_commit_sha"],
                     "head_sha": identity["candidate_commit_sha"], "change_id": original_a["change_id"]},
        "protected_entries": entries, "certification_only": True,
        "deployment_authorized": False, "source_authority": [reference(p) for p in evidence_paths],
        "recertification_receipts": receipt_refs,
        "resume_state_contract": resume,
        "external_worker_contract": {"commit": worker_commit, "entries": worker_entries},
        "governance_document": {"file": reference(doc), "identity": doc_identity,
            "authorization": reference(bp), "signature": reference(bp.with_suffix(".sig")),
            "freeze": reference(inputs.document_freeze), "commit_gate": reference(inputs.document_gate)},
        "governance_authorization": {"request": reference(ap), "signature": reference(ap.with_suffix(".sig")),
            "contract": a["common_contract"]},
    }
    require(verify_manifest_identity(inputs.backend_root, payload)["ok"], "canonical_identity_rejected")
    return payload


def tooling_admission(inputs, manifest, signature):
    result = verify_deployment_candidate(inputs.worker_root, manifest_path=Path(manifest),
                                         signature_path=Path(signature), public_key_path=inputs.key)
    require(result.get("ok"), "tooling_approval2_required:" + str(result.get("reason")))
    certified = read(manifest)
    a_path = inputs.governance_dir / "APPROVAL1_WORKER_BACKEND_APPROVAL2_GOVERNANCE.json"
    authority = authorization(a_path, a_path.with_suffix(".sig"), inputs.key, TOOL_PATHS)
    require(certified["approval"]["change_id"] == authority["change_id"] and
            certified["candidate_identity"]["base_commit_sha"] == authority["base_sha"],
            "tooling_authorization_lineage_mismatch")
    delta = canonical_diff(inputs.worker_root, authority["base_sha"],
                           certified["candidate_identity"]["candidate_tree_sha"])
    require(sorted(row["path"] for row in delta["payload"]["changes"]) == sorted(TOOL_PATHS),
            "tooling_delta_outside_authorized_scope")
    entries = certified.get("protected_entries", {})
    for path in TOOL_PATHS:
        require(entries.get(path, {}).get("sha256") == digest(inputs.worker_root / path),
                "tooling_component_not_certified")
    return {"manifest": reference(manifest), "signature": reference(signature),
            "candidate_sha": result["candidate_sha"]}


def final_payload(inputs, tooling_manifest, tooling_signature):
    require_clean(inputs.worker_root)
    payload = qualify(inputs)
    payload["tooling_certification"] = tooling_admission(inputs, tooling_manifest, tooling_signature)
    return payload


def serialized(payload):
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def external_certificate_path(inputs, path):
    doc_authority = read(inputs.governance_dir / "APPROVAL1_BACKEND_BACKEND_APPROVAL2_GOVERNANCE.json")
    for root in (inputs.worker_root, inputs.backend_root, Path(doc_authority["repository_root"])):
        external_path(root, Path(path))


def generate(inputs, tooling_manifest, tooling_signature, output):
    require(not Path(output).is_symlink(), "output_already_exists")
    output = Path(output).resolve()
    external_certificate_path(inputs, output)
    # No candidate, document or evidence dependency may be overwritten.
    require(not output.exists() and not output.is_symlink(), "output_already_exists")
    payload = final_payload(inputs, tooling_manifest, tooling_signature)
    require(payload == final_payload(inputs, tooling_manifest, tooling_signature), "inputs_changed_during_generation")
    with output.open("xb") as stream:
        stream.write(serialized(payload))
    return {"ok": True, "status": "BACKEND_APPROVAL2_SIGNATURE_REQUIRED",
            "manifest_sha256": digest(output), "candidate_identity": payload["candidate_identity"]}


def verify(inputs, manifest, signature, tooling_manifest, tooling_signature):
    external_certificate_path(inputs, manifest)
    external_certificate_path(inputs, signature)
    payload = signed(manifest, signature, inputs.key)
    require(payload == final_payload(inputs, tooling_manifest, tooling_signature), "backend_certificate_binding_mismatch")
    require(Path(manifest).read_bytes() == serialized(payload), "certificate_serialization_mismatch")
    return {"ok": True, "status": "BACKEND_CANDIDATE_APPROVAL2_PASS",
            "manifest_sha256": digest(manifest), "candidate_identity": payload["candidate_identity"],
            "deployment_authorized": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("qualify", "generate", "verify"))
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--governance-dir", type=Path, required=True)
    parser.add_argument("--document-freeze", type=Path, required=True)
    parser.add_argument("--document-gate", type=Path, required=True)
    parser.add_argument("--tooling-manifest", type=Path)
    parser.add_argument("--tooling-signature", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--signature", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        inputs = Inputs(args.backend_root.resolve(), ROOT, args.governance_dir.resolve(),
                        args.document_freeze.resolve(), args.document_gate.resolve())
        require(digest(inputs.key) == KEY_SHA256, "canonical_public_key_changed")
        if args.mode == "qualify":
            payload = qualify(inputs)
            result = {"ok": True, "status": "BACKEND_INPUT_QUALIFICATION_ONLY",
                      "candidate_identity": payload["candidate_identity"], "approval2_certified": False}
        else:
            require(args.tooling_manifest is not None and args.tooling_signature is not None,
                    "tooling_approval2_required")
            if args.mode == "generate":
                require(args.output is not None, "output_required")
                result = generate(inputs, args.tooling_manifest, args.tooling_signature, args.output)
            else:
                require(args.manifest is not None and args.signature is not None, "signed_certificate_required")
                result = verify(inputs, args.manifest, args.signature, args.tooling_manifest, args.tooling_signature)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError,
            subprocess.CalledProcessError) as exc:
        print(json.dumps({"ok": False, "reason": str(exc), "error_type": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
