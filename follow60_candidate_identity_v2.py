"""One canonical Git delta contract; no signing or activation capability."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

DIFF_SERIALIZATION = "FOLLOW60_TREE_DELTA_JSON_V2"
IDENTITY_SCHEMA = "FOLLOW60_COMMITTED_CANDIDATE_V2"
FREEZE_SCHEMA = "FOLLOW60_STAGED_FREEZE_V2"
RELEASE_CERTIFICATION_FILES = frozenset({
    "docs/governance/FOLLOW60_MAINLINE_LOCK_V3.json",
    "docs/governance/FOLLOW60_MAINLINE_LOCK_V3.sig",
    ".deployment/candidate-build-receipt.json",
    ".deployment/migration-attestation.json",
    ".deployment/lineage-gate-receipt.json",
})


def git(root: Path, *args: str) -> bytes:
    # Inherited index/object/replacement configuration must not select a second repository.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1")
    return subprocess.check_output([
        "git", "-c", "safe.directory=", "-c", "safe.directory=" + str(root.resolve()),
        "-C", str(root.resolve()), *args,
    ], env=env, stderr=subprocess.PIPE)


def object_sha(root: Path, revision: str, kind: str) -> str:
    sha = git(root, "rev-parse", "--verify", "--end-of-options", revision).decode().strip()
    if git(root, "cat-file", "-t", sha).decode().strip() != kind:
        raise ValueError("expected_" + kind + "_object")
    return sha


def commit_sha(root: Path, revision: str) -> str:
    return object_sha(root, revision, "commit")


def commit_tree(root: Path, revision: str) -> str:
    return object_sha(root, commit_sha(root, revision) + "^{tree}", "tree")


def _entries(root: Path, tree: str) -> dict:
    tree = object_sha(root, tree, "tree")
    result = {}
    for record in git(root, "ls-tree", "-r", "-z", "--full-tree", tree).split(b"\0"):
        if record:
            metadata, path = record.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split()
            result[path.decode("utf-8")] = {"mode": mode, "type": kind, "oid": oid}
    return result


def canonical_diff(root: Path, base_commit: str, candidate_tree: str) -> dict:
    """SHA256(UTF8(JSON sort_keys, ASCII escapes, separators=(',',':'), no LF)).

    Only implementation. Full recursive Git entries; renames are delete+add.
    Includes binary blobs, symlinks, executable modes, deletions and gitlinks.
    Invalid UTF-8 paths fail closed. Never uses git diff/textconv/rename heuristics.
    """
    base_tree = commit_tree(root, base_commit)
    target_tree = object_sha(root, candidate_tree, "tree")
    before, after = _entries(root, base_tree), _entries(root, target_tree)
    payload = {
        "serialization": DIFF_SERIALIZATION,
        "base_tree_sha": base_tree,
        "candidate_tree_sha": target_tree,
        "changes": [
            {"path": path, "before": before.get(path), "after": after.get(path)}
            for path in sorted(set(before) | set(after)) if before.get(path) != after.get(path)
        ],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return {"canonical_diff_sha256": hashlib.sha256(raw).hexdigest(), "payload": payload}


def require_clean(root: Path, *, installed_certification: bool = False) -> None:
    if installed_certification:
        # Existing release installer places signed sidecars at these fixed paths.
        # They are NOT candidate source and must not be protected source entries.
        paths = set(git(root, "diff", "--name-only", "-z", "HEAD").split(b"\0"))
        paths.update(git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"))
        paths.discard(b"")
        if paths <= {p.encode() for p in RELEASE_CERTIFICATION_FILES} and not git(root, "ls-files", "-u"):
            return
    if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("candidate_worktree_or_index_dirty")


def external_path(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return resolved
    raise ValueError("certification_artifacts_must_be_external")


def freeze_index(root: Path, authorization: dict) -> dict:
    base = commit_sha(root, authorization["base_sha"])
    if base != commit_sha(root, "HEAD"):
        raise ValueError("freeze_base_not_head")
    if git(root, "diff", "--name-only") or git(root, "ls-files", "--others", "--exclude-standard"):
        raise ValueError("freeze_requires_all_changes_staged")
    tree = object_sha(root, git(root, "write-tree").decode().strip(), "tree")
    delta = canonical_diff(root, base, tree)
    paths = [row["path"] for row in delta["payload"]["changes"]]
    if not paths or set(paths) - set(authorization["authorized_paths"]):
        raise ValueError("freeze_empty_or_unauthorized_scope")
    return {
        "schema": FREEZE_SCHEMA, "change_id": authorization["change_id"],
        "base_commit_sha": base, "candidate_tree_sha": tree,
        "diff_serialization": DIFF_SERIALIZATION,
        "canonical_diff_sha256": delta["canonical_diff_sha256"], "changed_paths": paths,
        "deployment_authorized": False,
    }


def committed_identity(root: Path, base: str, revision: str = "HEAD", freeze: dict | None = None,
                       *, installed_certification: bool = False) -> dict:
    candidate = commit_sha(root, revision)
    base = commit_sha(root, base)
    if candidate != commit_sha(root, "HEAD"):
        raise ValueError("candidate_not_checked_out_head")
    git(root, "merge-base", "--is-ancestor", base, candidate)
    require_clean(root, installed_certification=installed_certification)
    tree = commit_tree(root, candidate)
    identity = {
        "schema": IDENTITY_SCHEMA, "base_commit_sha": base,
        "candidate_commit_sha": candidate, "candidate_tree_sha": tree,
        "diff_serialization": DIFF_SERIALIZATION,
        "canonical_diff_sha256": canonical_diff(root, base, tree)["canonical_diff_sha256"],
    }
    if freeze is not None:
        if freeze.get("schema") != FREEZE_SCHEMA or any(
            freeze.get(key) != identity[key] for key in (
                "base_commit_sha", "candidate_tree_sha", "diff_serialization", "canonical_diff_sha256"
            )
        ):
            raise ValueError("committed_candidate_does_not_match_freeze")
    return identity


def verify_manifest_identity(root: Path, manifest: dict, revision: str = "HEAD") -> dict:
    try:
        identity = manifest["candidate_identity"]
        if identity.get("schema") != IDENTITY_SCHEMA:
            raise ValueError("candidate_identity_schema_invalid")
        # Validate types before comparing values. A tree cannot ever be a commit.
        commit_sha(root, identity["candidate_commit_sha"])
        object_sha(root, identity["candidate_tree_sha"], "tree")
        actual = committed_identity(root, identity["base_commit_sha"], revision, installed_certification=True)
        if set(manifest.get("protected_entries") or {}) & RELEASE_CERTIFICATION_FILES:
            raise ValueError("certification_sidecar_in_protected_source_scope")
        if identity != actual:
            raise ValueError("candidate_identity_mismatch")
        if manifest.get("certified_candidate_sha") != actual["candidate_commit_sha"]:
            raise ValueError("certified_commit_alias_mismatch")
        approval = manifest.get("approval") or {}
        if approval.get("head_sha") != actual["candidate_commit_sha"] or approval.get("base_sha") != actual["base_commit_sha"]:
            raise ValueError("approval_commit_binding_mismatch")
        for retired in ("final_diff_hash", "diff_sha256", "final_manifest_hash"):
            if retired in approval or retired in manifest:
                raise ValueError("ambiguous_legacy_hash_field")
        return {"ok": True, **actual}
    except (ValueError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError) as exc:
        return {"ok": False, "reason": str(exc) if isinstance(exc, ValueError) else "candidate_identity_invalid"}


def manifest_sha256(path: Path) -> str:
    """Exact bytes including final newline; never an approval-stripped JSON body."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
