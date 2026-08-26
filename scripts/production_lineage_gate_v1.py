#!/usr/bin/env python3
"""Fail-closed pre-deploy lineage gate for Phone Farm production artifacts.

The gate is intentionally independent from application tests: a candidate may
be functionally green and still be rejected when it is not a descendant of the
exact production revision or when an ACTIVE canonical delta is absent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_DELTA_STATUSES = {"ACTIVE", "SUPERSEDED", "RETIRED"}
REQUIRED_DELTA_FIELDS = {
    "id",
    "repo",
    "introduced_by_sha",
    "scope",
    "activated_at",
    "required",
    "superseded_by",
    "migration_dependencies",
    "tests",
    "notes",
    "status",
}


class GateFailure(RuntimeError):
    """A deterministic, user-actionable lineage rejection."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GateFailure(f"json_unreadable:{path}:{type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise GateFailure(f"json_root_not_object:{path}")
    return payload


def _full_sha(value: Any, *, field: str) -> str:
    sha = str(value or "").strip().lower()
    if not FULL_SHA_RE.fullmatch(sha):
        raise GateFailure(f"full_sha_required:{field}")
    return sha


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().replace("\n", " ")[:240]
        raise GateFailure(f"git_command_failed:{' '.join(args)}:{detail}")
    return proc.stdout.strip()


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "merge-base", "--is-ancestor", ancestor, descendant],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if proc.returncode not in (0, 1):
        raise GateFailure("git_ancestor_check_failed")
    return proc.returncode == 0


def _registry_hash(registry: dict[str, Any]) -> str:
    canonical = json.dumps(registry, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _worktree_status(repo_root: Path) -> list[str]:
    output = _git(repo_root, "status", "--porcelain=v1", "--untracked-files=all")
    return [line for line in output.splitlines() if line.strip()]


def _candidate_contains_path(repo_root: Path, candidate_sha: str, path: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "cat-file", "-e", f"{candidate_sha}:{path}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def _candidate_file_sha256(repo_root: Path, candidate_sha: str, path: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"{candidate_sha}:{path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise GateFailure(f"candidate_migration_unreadable:{path}")
    return hashlib.sha256(proc.stdout).hexdigest()


def _candidate_paths(repo_root: Path, candidate_sha: str, prefix: str) -> list[str]:
    output = _git(repo_root, "ls-tree", "-r", "--name-only", candidate_sha, "--", prefix)
    return [line.strip() for line in output.splitlines() if line.strip()]


def _validate_registry(registry: dict[str, Any]) -> None:
    if registry.get("schema") != "PHONE_FARM_CANONICAL_DELTA_REGISTRY_V1":
        raise GateFailure("registry_schema_mismatch")
    components = registry.get("components")
    if not isinstance(components, dict) or not components:
        raise GateFailure("registry_components_missing")
    seen_delta_ids: set[str] = set()
    for component_name, component in components.items():
        if not isinstance(component, dict):
            raise GateFailure(f"component_malformed:{component_name}")
        production = component.get("production")
        if not isinstance(production, dict):
            raise GateFailure(f"production_identity_missing:{component_name}")
        state = str(production.get("verification_state") or "")
        if state not in {"VERIFIED", "UNVERIFIED"}:
            raise GateFailure(f"production_verification_state_invalid:{component_name}")
        if state == "VERIFIED":
            _full_sha(production.get("sha"), field=f"{component_name}.production.sha")
        deltas = component.get("deltas")
        if not isinstance(deltas, list):
            raise GateFailure(f"deltas_missing:{component_name}")
        component_ids: set[str] = set()
        for delta in deltas:
            if not isinstance(delta, dict):
                raise GateFailure(f"delta_malformed:{component_name}")
            delta_id = str(delta.get("id") or "").strip()
            if not delta_id or delta_id in seen_delta_ids:
                raise GateFailure(f"delta_id_invalid_or_duplicate:{delta_id}")
            seen_delta_ids.add(delta_id)
            component_ids.add(delta_id)
            missing_fields = sorted(REQUIRED_DELTA_FIELDS - set(delta))
            if missing_fields:
                raise GateFailure(
                    f"delta_required_fields_missing:{delta_id}:{','.join(missing_fields)}"
                )
            if str(delta.get("repo") or "") != component_name:
                raise GateFailure(f"delta_repo_mismatch:{delta_id}")
            status = str(delta.get("status") or "")
            if status not in ALLOWED_DELTA_STATUSES:
                raise GateFailure(f"delta_status_invalid:{delta_id}")
            _full_sha(
                delta.get("introduced_by_sha"), field=f"{delta_id}.introduced_by_sha"
            )
            if not isinstance(delta.get("required"), bool):
                raise GateFailure(f"delta_required_not_boolean:{delta_id}")
            if not isinstance(delta.get("migration_dependencies"), list):
                raise GateFailure(f"delta_migration_dependencies_not_list:{delta_id}")
            if not isinstance(delta.get("tests"), list) or not delta.get("tests"):
                raise GateFailure(f"delta_tests_missing:{delta_id}")
            if status == "SUPERSEDED" and not str(delta.get("superseded_by") or "").strip():
                raise GateFailure(f"superseded_successor_missing:{delta_id}")
            if status == "RETIRED" and not str(delta.get("retirement_reason") or "").strip():
                raise GateFailure(f"retirement_reason_missing:{delta_id}")
        for delta in deltas:
            if str(delta.get("status")) == "SUPERSEDED":
                successor = str(delta.get("superseded_by") or "")
                if successor not in component_ids:
                    raise GateFailure(f"superseded_successor_unknown:{delta.get('id')}")
    migrations = registry.get("migrations")
    if not isinstance(migrations, list):
        raise GateFailure("registry_migrations_missing")
    seen_migration_ids: set[str] = set()
    for migration in migrations:
        if not isinstance(migration, dict):
            raise GateFailure("migration_malformed")
        migration_id = str(migration.get("id") or "").strip()
        if not migration_id or migration_id in seen_migration_ids:
            raise GateFailure(f"migration_id_invalid_or_duplicate:{migration_id}")
        seen_migration_ids.add(migration_id)
        if migration.get("status") not in ALLOWED_DELTA_STATUSES:
            raise GateFailure(f"migration_status_invalid:{migration_id}")
        if migration.get("status") == "ACTIVE":
            if not str(migration.get("production_version") or "").strip():
                raise GateFailure(f"migration_production_version_missing:{migration_id}")
            source_repo = migration.get("repo")
            source_path = migration.get("path")
            if (source_repo is None) != (source_path is None):
                raise GateFailure(f"migration_source_incomplete:{migration_id}")
            if source_repo is not None and migration.get("content_attestation_required"):
                digest = str(migration.get("sha256") or "").strip().lower()
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise GateFailure(f"migration_sha256_invalid:{migration_id}")
                canonical_name = str(migration.get("canonical_name") or "").strip()
                if not canonical_name:
                    raise GateFailure(f"migration_canonical_name_missing:{migration_id}")
                invariants = migration.get("required_zero_invariants", [])
                if not isinstance(invariants, list) or any(
                    not isinstance(item, str) or not item.strip() for item in invariants
                ):
                    raise GateFailure(f"migration_required_zero_invariants_invalid:{migration_id}")


def evaluate_gate(
    *,
    registry: dict[str, Any],
    component_name: str,
    repo_root: Path,
    candidate_sha: str,
    actual_production_sha: str,
    applied_migrations: set[str] | None = None,
    migration_attestation: dict[str, Any] | None = None,
    artifact_provenance: dict[str, Any] | None = None,
    require_clean_worktree: bool = True,
) -> dict[str, Any]:
    _validate_registry(registry)
    components = dict(registry["components"])
    component = components.get(component_name)
    if not isinstance(component, dict):
        raise GateFailure(f"component_unknown:{component_name}")

    candidate = _full_sha(candidate_sha, field="candidate_sha")
    actual = _full_sha(actual_production_sha, field="actual_production_sha")
    production = dict(component.get("production") or {})
    if production.get("verification_state") != "VERIFIED":
        raise GateFailure(f"production_identity_unverified:{component_name}")
    registered = _full_sha(production.get("sha"), field="registered_production_sha")
    if actual != registered:
        raise GateFailure("actual_production_sha_registry_mismatch")

    worktree_status = _worktree_status(repo_root)
    if require_clean_worktree and worktree_status:
        raise GateFailure("dirty_worktree")

    resolved_candidate = _full_sha(
        _git(repo_root, "rev-parse", candidate), field="resolved_candidate_sha"
    )
    resolved_actual = _full_sha(
        _git(repo_root, "rev-parse", actual), field="resolved_production_sha"
    )
    if resolved_candidate != candidate or resolved_actual != actual:
        raise GateFailure("git_revision_resolution_mismatch")
    if not _is_ancestor(repo_root, actual, candidate):
        raise GateFailure("candidate_not_descendant_of_exact_production")

    missing_active: list[str] = []
    for delta in component.get("deltas") or []:
        if delta.get("status") != "ACTIVE":
            continue
        introduced = _full_sha(
            delta.get("introduced_by_sha"),
            field=f"{delta.get('id')}.introduced_by_sha",
        )
        if not _is_ancestor(repo_root, introduced, candidate):
            missing_active.append(str(delta.get("id")))
    if missing_active:
        raise GateFailure("active_canonical_deltas_missing:" + ",".join(sorted(missing_active)))

    required_migrations = {
        str(item.get("production_version") or "").strip()
        for item in registry.get("migrations", [])
        if isinstance(item, dict) and item.get("status") == "ACTIVE"
    }
    required_migrations.discard("")
    component_migrations = [
        item
        for item in registry.get("migrations", [])
        if isinstance(item, dict)
        and item.get("status") == "ACTIVE"
        and item.get("repo") == component_name
    ]
    missing_candidate_migrations = sorted(
        str(item.get("id"))
        for item in component_migrations
        if not _candidate_contains_path(repo_root, candidate, str(item.get("path")))
    )
    if missing_candidate_migrations:
        raise GateFailure(
            "candidate_migration_lineage_missing:" + ",".join(missing_candidate_migrations)
        )

    strict_component_migrations = [
        item for item in component_migrations if item.get("content_attestation_required")
    ]
    validated_migrations: list[dict[str, Any]] = []
    for item in strict_component_migrations:
        migration_id = str(item.get("id") or "")
        expected_path = str(item.get("path") or "")
        expected_hash = str(item.get("sha256") or "").lower()
        actual_hash = _candidate_file_sha256(repo_root, candidate, expected_path)
        if actual_hash != expected_hash:
            raise GateFailure(f"candidate_migration_hash_mismatch:{migration_id}")
        canonical_name = str(item.get("canonical_name") or "")
        aliases = [
            path for path in _candidate_paths(repo_root, candidate, "supabase/migrations")
            if path.endswith(f"_{canonical_name}.sql")
        ]
        if aliases != [expected_path]:
            raise GateFailure(f"unexpected_pending_canonical_migration:{migration_id}")
        validated_migrations.append({
            "id": migration_id,
            "version": str(item.get("production_version") or ""),
            "path": expected_path,
            "sha256": actual_hash,
        })

    if component_name in {"backend", "worker"} and required_migrations:
        if applied_migrations is None:
            raise GateFailure("applied_migrations_evidence_required")
        missing_migrations = sorted(required_migrations - applied_migrations)
        if missing_migrations:
            raise GateFailure("active_migrations_missing:" + ",".join(missing_migrations))

    attestation_hash = None
    required_attested = [
        item for item in strict_component_migrations
        if item.get("required_zero_invariants") or item.get("require_schema_match")
    ]
    if required_attested:
        if not isinstance(migration_attestation, dict):
            raise GateFailure("migration_attestation_required")
        if migration_attestation.get("schema") != "PHONE_FARM_MIGRATION_ATTESTATION_V1":
            raise GateFailure("migration_attestation_schema_mismatch")
        attested = migration_attestation.get("migrations")
        invariants = migration_attestation.get("invariants")
        if not isinstance(attested, dict) or not isinstance(invariants, dict):
            raise GateFailure("migration_attestation_malformed")
        for item in required_attested:
            version = str(item.get("production_version") or "")
            evidence = attested.get(version)
            if not isinstance(evidence, dict):
                raise GateFailure(f"migration_schema_evidence_missing:{version}")
            if evidence.get("name") != item.get("canonical_name"):
                raise GateFailure(f"migration_schema_name_mismatch:{version}")
            if str(evidence.get("sql_sha256") or "").lower() != str(item.get("sha256") or "").lower():
                raise GateFailure(f"migration_schema_hash_mismatch:{version}")
            if item.get("require_schema_match") and evidence.get("deployed_schema_matches") is not True:
                raise GateFailure(f"migration_schema_drift:{version}")
            for invariant in item.get("required_zero_invariants") or []:
                if invariants.get(invariant) != 0:
                    raise GateFailure(f"migration_schema_invariant_failed:{invariant}")
        attestation_hash = hashlib.sha256(
            json.dumps(migration_attestation, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    if bool(component.get("artifact_provenance_required")):
        if not isinstance(artifact_provenance, dict):
            raise GateFailure("artifact_provenance_required")
        artifact_sha = _full_sha(
            artifact_provenance.get("source_sha"), field="artifact_provenance.source_sha"
        )
        if artifact_sha != candidate:
            raise GateFailure("artifact_provenance_sha_mismatch")

    return {
        "ok": True,
        "status": "PRODUCTION_LINEAGE_GATE_V1_PASS",
        "component": component_name,
        "actual_production_sha": actual,
        "candidate_sha": candidate,
        "candidate_base_sha": actual,
        "ancestry_pass": True,
        "worktree_clean": not worktree_status,
        "registry_sha256": _registry_hash(registry),
        "missing_deltas": [],
        "migration_gate": "PASS",
        "validated_migrations": validated_migrations,
        "migration_attestation_sha256": attestation_hash,
        "active_delta_count": sum(
            1 for item in component.get("deltas") or [] if item.get("status") == "ACTIVE"
        ),
        "required_migration_count": (
            len(required_migrations) if component_name in {"backend", "worker"} else 0
        ),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--component", required=True, choices=("worker", "backend", "botapp"))
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--actual-production-sha", required=True)
    parser.add_argument("--applied-migrations", type=Path)
    parser.add_argument("--artifact-provenance", type=Path)
    parser.add_argument("--migration-attestation", type=Path)
    parser.add_argument("--receipt-out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        registry = _load_json(args.registry)
        migration_set: set[str] | None = None
        if args.applied_migrations:
            migration_payload = _load_json(args.applied_migrations)
            versions = migration_payload.get("applied_versions")
            if not isinstance(versions, list):
                raise GateFailure("applied_migrations_list_missing")
            migration_set = {str(item).strip() for item in versions if str(item).strip()}
        provenance = _load_json(args.artifact_provenance) if args.artifact_provenance else None
        migration_attestation = _load_json(args.migration_attestation) if args.migration_attestation else None
        result = evaluate_gate(
            registry=registry,
            component_name=args.component,
            repo_root=args.repo_root,
            candidate_sha=args.candidate_sha,
            actual_production_sha=args.actual_production_sha,
            applied_migrations=migration_set,
            migration_attestation=migration_attestation,
            artifact_provenance=provenance,
            require_clean_worktree=True,
        )
    except GateFailure as exc:
        print(json.dumps({"ok": False, "status": "PRODUCTION_LINEAGE_GATE_V1_BLOCKED", "reason": str(exc)}, sort_keys=True))
        return 2
    if args.receipt_out:
        receipt = {
            **result,
            "receipt_schema": "PHONE_FARM_PRODUCTION_DEPLOYMENT_RECEIPT_V1",
            "deployment_state": "PRE_DEPLOY_CERTIFIED",
            "deployment_id": None,
            "production_sha_after": None,
            "health_status": None,
            "deployed_at": None,
        }
        args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
        args.receipt_out.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
