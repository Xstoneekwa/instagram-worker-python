#!/usr/bin/env python3
"""Generate an unsigned V3.1 manifest for one exact final Git commit."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from follow60_lock_v3 import canonical_json, git_file_entry, sha256_bytes, transitive_import_graph  # noqa: E402
from follow60_candidate_identity_v2 import committed_identity, external_path, manifest_sha256, require_clean


def role_for(path: str) -> str:
    if path.startswith("tests/"):
        return "regression_contract"
    if path.startswith("scripts/"):
        return "lock_or_promotion_gate"
    if path.startswith(".github/"):
        return "remote_enforcement"
    if path.startswith("docs/governance/"):
        return "governance_audit"
    if path in {"runner.py", "account_run_request_consumer.py"}:
        return "runtime_entrypoint"
    if "resume" in path:
        return "ct_resume"
    if "navigation" in path or "followers" in path:
        return "followers_navigation"
    return "follow60_transitive_dependency"


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--scope", required=True)
    parser.add_argument("--base-manifest", required=True)
    parser.add_argument("--recertification-receipt", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--change-id", required=True)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--freeze", required=True)
    parser.add_argument("--resume-contract-receipt", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    output = external_path(root, Path(args.output))
    freeze = json.loads(Path(args.freeze).read_text(encoding="utf-8"))
    if freeze.get("change_id") != args.change_id:
        raise SystemExit("freeze_change_id_mismatch")
    identity = committed_identity(root, args.base_sha, args.revision, freeze)
    candidate_sha = identity["candidate_commit_sha"]
    receipt_path = Path(args.recertification_receipt).resolve()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "PASS" or receipt.get("candidate_sha") != candidate_sha:
        raise SystemExit("recertification_receipt_not_bound_to_candidate")
    if int(receipt.get("missing_canonical_delta", -1)) != 0:
        raise SystemExit("recertification_missing_canonical_delta")

    scope = json.loads(Path(args.scope).read_text(encoding="utf-8"))
    graph = transitive_import_graph(root, scope)
    protected_paths = sorted(set(scope) | set(graph) | {d for values in graph.values() for d in values})
    entries = {}
    for relative in protected_paths:
        entry = git_file_entry(root, relative, revision=args.revision)
        entry.update({"role": role_for(relative), "lock_version": "3.1.0"})
        entries[relative] = entry
    graph = transitive_import_graph(root, entries)
    previous = json.loads(Path(args.base_manifest).read_text(encoding="utf-8"))
    audit_history = list(previous.get("audit_history") or [])
    if any(entry.get("change_id") == args.change_id for entry in audit_history):
        raise SystemExit("approval_token_already_consumed")
    audit_history.append({
        "schema": "FOLLOW60_LOCK_AUDIT_V1",
        "change_id": args.change_id,
        "base_sha": args.base_sha,
        "head_sha": candidate_sha,
        "protected_delta_validation": "PASS",
        "field_status": "PENDING_NEXT_NATURAL_RUNS",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    candidate = {
        "schema": "FOLLOW60_MAINLINE_LOCK_V3_1",
        "lock_version": "3.1.0",
        "lock_state": "LOCKED",
        "certified_candidate_sha": candidate_sha,
        "candidate_identity": identity,
        "protected_entries": entries,
        "protected_scope_sha256": sha256_bytes(canonical_json(entries)),
        "protected_transitive_dependency_count": len({d for values in graph.values() for d in values}),
        "import_graph": graph,
        "import_graph_sha256": sha256_bytes(canonical_json(graph)),
        "runtime_contract": {
            "runtime_mode": "mainline", "binding_kind": "mainline",
            "engine": "FOLLOW60_V2_MAINLINE_V1", "entrypoint": "runner.py",
        },
        "approval": {
            "change_id": args.change_id,
            "base_sha": args.base_sha,
            "head_sha": candidate_sha,
            "token_status": "consumed_once",
        },
        "recertification": {
            "status": "PASS",
            "regression_test_count": int(receipt["regression_test_count"]),
            "missing_canonical_delta": 0,
            "receipt_sha256": sha256_bytes(receipt_path.read_bytes()),
        },
        "deployment_protocol": {
            "version": "FOLLOW60_DEPLOYMENT_PROTOCOL_ENFORCEMENT_V1",
            "exact_candidate_binding_required": True,
            "predeploy_integrity_check_required": True,
            "codex_bypass_available": False,
        },
        "audit_history": audit_history,
        "manifest_signature": "DETACHED_ED25519_REQUIRED",
        "field_certification": "PENDING_NEXT_NATURAL_RUNS",
    }
    candidate["resume_state_contract"] = json.loads(Path(args.resume_contract_receipt).read_text(encoding="utf-8"))
    from follow60_lock_v3 import verify_resume_state_contract_evidence
    evidence = verify_resume_state_contract_evidence(candidate)
    if not evidence["ok"]:
        raise SystemExit(evidence["reason"])
    require_clean(root)
    if committed_identity(root, args.base_sha, args.revision, freeze) != identity:
        raise SystemExit("candidate_changed_during_manifest_generation")
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(candidate, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "ok": True, "candidate_sha": candidate_sha,
        "protected_file_count": len(entries),
        "protected_scope_sha256": candidate["protected_scope_sha256"],
        "candidate_identity": identity,
        "manifest_sha256": manifest_sha256(output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
