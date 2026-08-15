#!/usr/bin/env python3
"""Generate a V3.1 candidate only from an externally signed exact-diff request."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from follow60_lock_v3 import (  # noqa: E402
    canonical_json,
    git_file_entry,
    sha256_bytes,
    transitive_import_graph,
    verify_detached_signature,
)


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--scope", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--request-signature", required=True)
    parser.add_argument("--public-key", required=True)
    parser.add_argument("--base-manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--revision", default="HEAD")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    request_path = Path(args.request).resolve()
    signed, reason = verify_detached_signature(
        request_path,
        Path(args.request_signature).resolve(),
        Path(args.public_key).resolve(),
    )
    if not signed:
        raise SystemExit(f"request_approval_{reason}")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("schema") != "FOLLOW60_LOCK_CHANGE_REQUEST_V1":
        raise SystemExit("request_schema_mismatch")
    if request.get("approval_status") != "REQUESTED_NOT_APPROVED":
        raise SystemExit("request_payload_mutated")
    expires_at = datetime.fromisoformat(str(request.get("expires_at")))
    if expires_at <= datetime.now(timezone.utc):
        raise SystemExit("approval_expired")
    revision_sha = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", args.revision]
    ).decode().strip()
    if revision_sha != request.get("head_sha"):
        raise SystemExit("approved_head_mismatch")
    diff = subprocess.check_output([
        "git", "-C", str(root), "diff", "--binary",
        f"{request['base_sha']}..{revision_sha}",
    ])
    files = subprocess.check_output([
        "git", "-C", str(root), "diff", "--name-only",
        f"{request['base_sha']}..{revision_sha}",
    ]).decode().splitlines()
    if sha256_bytes(diff) != request.get("diff_sha256"):
        raise SystemExit("approved_diff_mismatch")
    if sorted(set(files)) != sorted(request.get("files_requested") or []):
        raise SystemExit("approved_file_set_mismatch")
    base_manifest = json.loads(Path(args.base_manifest).read_text(encoding="utf-8"))
    historical = list(base_manifest.get("audit_history") or [])
    for entry in historical:
        if entry.get("change_id") == request.get("change_id"):
            raise SystemExit("approval_token_already_consumed")
    audit = {
        "schema": "FOLLOW60_LOCK_AUDIT_V1",
        "change_id": request["change_id"],
        "base_sha": request["base_sha"],
        "head_sha": revision_sha,
        "diff_sha256": request["diff_sha256"],
        "files": request["files_requested"],
        "requested_scope": request["requested_scope"],
        "approval_token_status": "consumed_once",
        "consumed_at": datetime.now(timezone.utc).isoformat(),
        "field_status": "PENDING_NEXT_NATURAL_RUNS",
    }
    scope = json.loads(Path(args.scope).read_text(encoding="utf-8"))
    if not isinstance(scope, list) or not scope:
        raise SystemExit("protected_scope_must_be_non_empty_json_array")
    graph = transitive_import_graph(root, scope)
    protected_paths = sorted(set(str(item) for item in scope) | set(graph) | {
        dependency for imports in graph.values() for dependency in imports
    })
    entries = {}
    for relative in protected_paths:
        entry = git_file_entry(root, relative, revision=args.revision)
        entry["role"] = role_for(relative)
        entry["lock_version"] = "3.1.0"
        entries[relative] = entry
    graph = transitive_import_graph(root, entries)
    candidate = {
        "schema": "FOLLOW60_MAINLINE_LOCK_V3_1",
        "lock_version": "3.1.0",
        "lock_state": "LOCKED",
        "protected_entries": entries,
        "protected_scope_sha256": sha256_bytes(canonical_json(entries)),
        "protected_transitive_dependency_count": len(
            {dependency for imports in graph.values() for dependency in imports}
        ),
        "import_graph": graph,
        "import_graph_sha256": sha256_bytes(canonical_json(graph)),
        "runtime_contract": {
            "runtime_mode": "mainline",
            "binding_kind": "mainline",
            "engine": "FOLLOW60_V2_MAINLINE_V1",
            "entrypoint": "runner.py",
        },
        "approval": {
            "change_id": request["change_id"],
            "base_sha": request["base_sha"],
            "head_sha": revision_sha,
            "diff_sha256": request["diff_sha256"],
            "request_sha256": sha256_bytes(request_path.read_bytes()),
            "token_status": "consumed_once",
            "expires_at": request["expires_at"],
        },
        "audit_history": historical + [audit],
        "manifest_signature": "DETACHED_ED25519_REQUIRED",
        "field_certification": "PENDING_NEXT_NATURAL_RUNS",
    }
    Path(args.output).write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "status": "CANDIDATE_REQUIRES_EXTERNAL_MANIFEST_SIGNATURE",
        "protected_file_count": len(entries),
        "protected_scope_sha256": candidate["protected_scope_sha256"],
        "import_graph_sha256": candidate["import_graph_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
