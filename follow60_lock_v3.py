"""Canonical Follow60 V3/V3.1 lock verification primitives.

V3.1 manifests are detached-signature authenticated and bind exact Git blobs,
raw bytes, file modes, the local Python import graph and a consumed one-shot
change approval.  This module is read-only: it never generates or approves a
change and never rewrites a manifest.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key


SCHEMA_V3 = "FOLLOW60_MAINLINE_LOCK_V3"
SCHEMA_V3_1 = "FOLLOW60_MAINLINE_LOCK_V3_1"
APPROVAL_SCHEMA_V3 = "FOLLOW60_MAINLINE_LOCK_V3_EXTERNAL_APPROVAL"
LOCKED = "LOCKED"

RESUME_CONTRACT_GATES = (
    "RESUME_STATE_SCHEMA_CONTRACT_GATE",
    "RESUME_STATE_PYTHON_API_CONTRACT_GATE",
    "RESUME_STATE_END_TO_END_CONTRACT_GATE",
)
RESUME_CONTRACT_SOURCE_FILES = (
    "account_session_resume_state_contract.py", "account_session_resume_plan_store.py",
    "auto_restart_runtime.py", "scripts/verify-resume-state-end-to-end-contract.py",
    "tests/resume-state-backend-contract.mjs", "tests/test_resume_state_python_api_contract.py",
    "tests/test_resume_state_end_to_end_contract.py", "tests/test_resume_state_schema_contract_gate.py",
    "tests/test_human_confirmed_resume_claim.py", "tests/test_account_session_resume_state_contract.py",
    "tests/test_account_session_resume_plan_store.py", "tests/test_auto_restart_runtime.py",
    "tests/test_auto_restart_hard_stop.py",
)


def verify_resume_state_contract_evidence(manifest: dict[str, Any]) -> dict[str, Any]:
    """Require signed PASS evidence bound to the exact protected contract bytes.

    This is an admission check, not a test runner at service boot. The offline
    gate executes fixtures; the detached manifest signature attests its receipt.
    """
    evidence = manifest.get("resume_state_contract")
    if not isinstance(evidence, dict) or evidence.get("schema") != "RESUME_STATE_END_TO_END_CONTRACT_V1":
        return {"ok": False, "reason": "resume_state_contract_evidence_missing"}
    gates = evidence.get("gates")
    if evidence.get("status") != "PASS" or not isinstance(gates, dict) or any(
        gates.get(name) != "PASS" for name in RESUME_CONTRACT_GATES
    ):
        return {"ok": False, "reason": "resume_state_contract_gate_failed"}
    def is_hex(value: Any, length: int) -> bool:
        return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)
    if not is_hex(evidence.get("receipt_sha256"), 64) or not is_hex(evidence.get("backend_sha"), 40):
        return {"ok": False, "reason": "resume_state_contract_provenance_missing"}
    sources = evidence.get("source_files")
    entries = manifest.get("protected_entries")
    if not isinstance(sources, dict) or not isinstance(entries, dict):
        return {"ok": False, "reason": "resume_state_contract_source_binding_missing"}
    for name in RESUME_CONTRACT_SOURCE_FILES:
        entry = entries.get(name)
        if not isinstance(entry, dict) or not is_hex(sources.get(name), 64) or sources[name] != entry.get("sha256"):
            return {"ok": False, "reason": "resume_state_contract_source_binding_mismatch", "path": name}
    return {"ok": True, "status": "RESUME_STATE_END_TO_END_CONTRACT_GATE_PASS"}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def git(root: Path, *args: str) -> bytes:
    exact_root = Path(root).expanduser().resolve(strict=True)
    environment = os.environ.copy()
    for key in tuple(environment):
        if key == "GIT_CONFIG_COUNT" or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            environment.pop(key, None)
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    return subprocess.check_output(
        [
            "git",
            "-c", "safe.directory=",
            "-c", f"safe.directory={exact_root}",
            "-C", str(exact_root),
            *args,
        ],
        env=environment,
    )


def git_file_entry(root: Path, relative: str, *, revision: str = "HEAD") -> dict[str, Any]:
    line = git(root, "ls-tree", revision, "--", relative).decode("utf-8").strip()
    if not line:
        raise ValueError(f"protected_file_missing:{relative}")
    metadata, listed_path = line.split("\t", 1)
    mode, object_type, oid = metadata.split(" ", 2)
    if listed_path != relative or object_type != "blob":
        raise ValueError(f"protected_path_not_blob:{relative}")
    payload = git(root, "show", f"{revision}:{relative}")
    return {
        "path": relative,
        "git_blob_oid": oid,
        "sha256": sha256_bytes(payload),
        "size": len(payload),
        "mode": mode,
    }


def _resolve_local_import(root: Path, owner: str, module: str, level: int) -> str | None:
    owner_path = Path(owner)
    if level:
        base_parts = list(owner_path.parent.parts)
        remove = max(0, level - 1)
        if remove:
            base_parts = base_parts[:-remove]
        parts = base_parts + ([part for part in module.split(".") if part] if module else [])
    else:
        parts = [part for part in module.split(".") if part]
    if not parts:
        return None
    candidates = [
        Path(*parts).with_suffix(".py"),
        Path(*parts) / "__init__.py",
    ]
    for candidate in candidates:
        if (root / candidate).is_file():
            return candidate.as_posix()
    return None


def direct_local_imports(root: Path, relative: str, payload: bytes | None = None) -> set[str]:
    if not relative.endswith(".py"):
        return set()
    source = payload if payload is not None else (root / relative).read_bytes()
    try:
        tree = ast.parse(source.decode("utf-8"), filename=relative)
    except (SyntaxError, UnicodeDecodeError):
        return set()
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _resolve_local_import(root, relative, alias.name, 0)
                if resolved:
                    imports.add(resolved)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_local_import(root, relative, node.module or "", int(node.level or 0))
            if resolved:
                imports.add(resolved)
            elif node.module:
                for alias in node.names:
                    nested = f"{node.module}.{alias.name}"
                    resolved = _resolve_local_import(root, relative, nested, int(node.level or 0))
                    if resolved:
                        imports.add(resolved)
    return imports


def transitive_import_graph(root: Path, roots: Iterable[str]) -> dict[str, list[str]]:
    pending = list(sorted(set(str(path) for path in roots if str(path).endswith(".py"))))
    visited: set[str] = set()
    graph: dict[str, list[str]] = {}
    while pending:
        relative = pending.pop(0)
        if relative in visited or not (root / relative).is_file():
            continue
        visited.add(relative)
        imports = sorted(direct_local_imports(root, relative))
        graph[relative] = imports
        for imported in imports:
            if imported not in visited:
                pending.append(imported)
    return dict(sorted(graph.items()))


def verify_detached_signature(payload: Path, signature: Path, public_key: Path) -> tuple[bool, str]:
    if not payload.is_file():
        return False, "signed_payload_missing"
    if not signature.is_file():
        return False, "detached_signature_missing"
    if not public_key.is_file():
        return False, "public_key_missing"
    try:
        key = load_pem_public_key(public_key.read_bytes())
        if not isinstance(key, Ed25519PublicKey):
            return False, "public_key_not_ed25519"
        key.verify(signature.read_bytes(), payload.read_bytes())
    except InvalidSignature:
        return False, "signature_invalid"
    except (OSError, TypeError, ValueError):
        return False, "signature_invalid"
    return True, "signature_valid"


def _verify_v3_legacy(root: Path, manifest_path: Path, approval_path: Path, revision: str) -> dict[str, Any]:
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    if approval.get("schema") != APPROVAL_SCHEMA_V3:
        return {"ok": False, "reason": "approval_schema_mismatch"}
    if approval.get("status") != "consumed_once":
        return {"ok": False, "reason": "approval_not_consumed_once"}
    manifest_approval = manifest.get("approval") or {}
    if manifest_approval.get("status") != "approved_once":
        return {"ok": False, "reason": "manifest_not_approved"}
    if approval.get("approval_id") != manifest_approval.get("approval_id"):
        return {"ok": False, "reason": "approval_id_mismatch"}
    if approval.get("approved_scope_sha256") != manifest.get("protected_scope_sha256"):
        return {"ok": False, "reason": "approval_scope_hash_mismatch"}
    if approval.get("manifest_sha256") != sha256_bytes(manifest_raw):
        return {"ok": False, "reason": "approval_manifest_hash_mismatch"}
    protected = manifest.get("protected_files") or {}
    mismatches: dict[str, Any] = {}
    for relative, expected in protected.items():
        try:
            actual = sha256_bytes(git(root, "show", f"{revision}:{relative}"))
        except subprocess.CalledProcessError:
            actual = None
        if actual != expected:
            mismatches[relative] = {"expected": expected, "actual": actual}
    if mismatches:
        return {"ok": False, "reason": "head_protected_blob_mismatch", "mismatches": mismatches}
    scope_hash = sha256_bytes(canonical_json(protected))
    if scope_hash != manifest.get("protected_scope_sha256"):
        return {"ok": False, "reason": "protected_scope_hash_mismatch"}
    return {
        "ok": True,
        "status": "FOLLOW60_MAINLINE_LOCK_V3_OK",
        "revision": git(root, "rev-parse", revision).decode().strip(),
        "approval_id": approval.get("approval_id"),
        "protected_file_count": len(protected),
        "protected_scope_sha256": scope_hash,
    }


def verify_repository(
    root: Path,
    manifest_path: Path,
    *,
    revision: str = "HEAD",
    approval_path: Path | None = None,
    signature_path: Path | None = None,
    public_key_path: Path | None = None,
    enforce_exact_candidate: bool = True,
) -> dict[str, Any]:
    root = Path(root).resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "reason": "manifest_unreadable", "error_type": type(exc).__name__}
    if manifest.get("schema") == SCHEMA_V3:
        if approval_path is None:
            return {"ok": False, "reason": "approval_missing"}
        return _verify_v3_legacy(root, manifest_path, approval_path, revision)
    if manifest.get("schema") != SCHEMA_V3_1:
        return {"ok": False, "reason": "manifest_schema_mismatch"}
    if manifest.get("lock_state") != LOCKED:
        return {"ok": False, "reason": "lock_state_not_locked"}
    if (manifest.get("approval") or {}).get("token_status") != "consumed_once":
        return {"ok": False, "reason": "approval_token_not_consumed"}
    if signature_path is None or public_key_path is None:
        return {"ok": False, "reason": "signature_configuration_missing"}
    signed, signature_reason = verify_detached_signature(
        manifest_path, signature_path, public_key_path
    )
    if not signed:
        return {"ok": False, "reason": signature_reason}
    from follow60_candidate_identity_v2 import commit_sha, verify_manifest_identity
    try:
        revision_sha = commit_sha(root, revision)
    except (ValueError, subprocess.CalledProcessError):
        return {"ok": False, "reason": "candidate_revision_not_commit"}
    if "candidate_identity" in manifest:
        identity = verify_manifest_identity(root, manifest, revision)
        if not identity.get("ok"):
            return identity
    certified_candidate_sha = str(manifest.get("certified_candidate_sha") or "").strip()
    entries = manifest.get("protected_entries") or {}
    if not isinstance(entries, dict) or not entries:
        return {"ok": False, "reason": "protected_scope_empty"}
    mismatches: dict[str, Any] = {}
    for relative, expected in entries.items():
        try:
            actual = git_file_entry(root, relative, revision=revision)
        except (subprocess.CalledProcessError, ValueError):
            actual = None
        comparable = dict(actual or {})
        if comparable:
            comparable["role"] = expected.get("role")
            comparable["lock_version"] = expected.get("lock_version")
        if comparable != expected:
            mismatches[relative] = {"expected": expected, "actual": actual}
    if mismatches:
        return {"ok": False, "reason": "protected_entry_mismatch", "mismatches": mismatches}
    scope_hash = sha256_bytes(canonical_json(entries))
    if scope_hash != manifest.get("protected_scope_sha256"):
        return {"ok": False, "reason": "protected_scope_hash_mismatch"}
    graph = transitive_import_graph(root, entries.keys())
    missing_dependencies = sorted(
        {dependency for imports in graph.values() for dependency in imports} - set(entries)
    )
    if missing_dependencies:
        return {
            "ok": False,
            "reason": "unprotected_transitive_dependency",
            "missing_dependencies": missing_dependencies,
        }
    graph_hash = sha256_bytes(canonical_json(graph))
    if graph_hash != manifest.get("import_graph_sha256"):
        return {"ok": False, "reason": "import_graph_mismatch", "actual": graph_hash}
    contract = manifest.get("runtime_contract") or {}
    if contract != {
        "runtime_mode": "mainline",
        "binding_kind": "mainline",
        "engine": "FOLLOW60_V2_MAINLINE_V1",
        "entrypoint": "runner.py",
    }:
        return {"ok": False, "reason": "runtime_contract_mismatch"}
    if enforce_exact_candidate and not certified_candidate_sha:
        return {"ok": False, "reason": "manifest_exact_candidate_binding_missing"}
    if enforce_exact_candidate and certified_candidate_sha != revision_sha:
        return {
            "ok": False,
            "reason": "manifest_certified_sha_mismatch",
            "manifest_certified_sha": certified_candidate_sha,
            "revision": revision_sha,
        }
    resume_contract = verify_resume_state_contract_evidence(manifest)
    if not resume_contract["ok"]:
        return resume_contract
    return {
        "ok": True,
        "status": "FOLLOW60_MAINLINE_LOCK_V3_1_OK",
        "lock_version": manifest.get("lock_version"),
        "revision": revision_sha,
        "manifest_certified_sha": certified_candidate_sha,
        "protected_file_count": len(entries),
        "protected_scope_sha256": scope_hash,
        "import_graph_sha256": graph_hash,
        "manifest_signature": "PASS",
        "approval_token_consumed": True,
    }


def verify_runtime(
    root: Path,
    *,
    runtime_mode: str,
    binding_kind: str,
    engine: str,
    manifest_path: Path,
    signature_path: Path | None = None,
    public_key_path: Path | None = None,
) -> dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "reason": "follow60_integrity_manifest_unreadable", "error_type": type(exc).__name__}
    contract = manifest.get("runtime_contract") or {}
    if contract.get("runtime_mode") != runtime_mode:
        return {"ok": False, "reason": "follow60_integrity_runtime_mode_mismatch"}
    if contract.get("binding_kind") != binding_kind:
        return {"ok": False, "reason": "follow60_integrity_binding_mismatch"}
    if contract.get("engine") != engine:
        return {"ok": False, "reason": "follow60_integrity_engine_mismatch"}
    if manifest.get("schema") == SCHEMA_V3:
        if (manifest.get("approval") or {}).get("status") != "approved_once":
            return {"ok": False, "reason": "follow60_integrity_approval_missing"}
        protected = manifest.get("protected_files") or {}
        if not isinstance(protected, dict) or not protected:
            return {"ok": False, "reason": "follow60_integrity_empty_scope"}
        for relative, expected in protected.items():
            candidate = root / str(relative)
            actual = sha256_bytes(candidate.read_bytes()) if candidate.is_file() else None
            if actual != expected:
                return {"ok": False, "reason": "FOLLOW60_MAINLINE_INTEGRITY_MISMATCH"}
        if sha256_bytes(canonical_json(protected)) != manifest.get("protected_scope_sha256"):
            return {"ok": False, "reason": "follow60_integrity_scope_hash_mismatch"}
        return {"ok": True, "status": "FOLLOW60_MAINLINE_LOCK_V3_RUNTIME_OK"}
    verified = verify_repository(
        root,
        manifest_path,
        revision="HEAD",
        signature_path=signature_path,
        public_key_path=public_key_path,
    )
    if not verified.get("ok"):
        verified["reason"] = "FOLLOW60_MAINLINE_INTEGRITY_MISMATCH"
    return verified
