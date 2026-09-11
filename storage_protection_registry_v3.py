"""Dynamic storage protection discovery for Worker release machinery.

Classification is conservative: discovered authorities are protected and any
unclassified release remains UNKNOWN/KEEP. No deletion is implemented here.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any


SCHEMA = "phonefarm-asset-registry-v3"


class StorageProtectionError(RuntimeError):
    pass


def _release_identity(path: Path) -> dict[str, str]:
    resolved = path.resolve(strict=True)
    proc = subprocess.run(
        ["git", "-c", f"safe.directory={resolved}", "-C", str(resolved), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    sha = proc.stdout.strip()
    if proc.returncode or len(sha) != 40:
        raise StorageProtectionError(f"release_sha_unavailable:{resolved}")
    return {"path": str(resolved), "sha": sha}


def _symlink_release(path: Path, release_root: Path, reason: str) -> dict[str, str]:
    if not path.is_symlink():
        raise StorageProtectionError(f"{reason}_symlink_missing")
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(release_root.resolve(strict=True))
    except ValueError as exc:
        raise StorageProtectionError(f"{reason}_outside_release_root") from exc
    if not relative.parts:
        raise StorageProtectionError(f"{reason}_release_root_itself_invalid")
    release = release_root.resolve(strict=True) / relative.parts[0]
    return {**_release_identity(release), "reason": reason, "reference": str(path)}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise StorageProtectionError(f"json_object_required:{path}")
    return value


def _git_tree(path: Path) -> str:
    resolved = path.resolve(strict=True)
    proc = subprocess.run(
        ["git", "-c", f"safe.directory={resolved}", "-C", str(resolved), "rev-parse", "HEAD^{tree}"],
        text=True, capture_output=True, check=False, timeout=10,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def discover_pending_approvals(projects_root: Path, releases_root: Path | None = None) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    frozen_trees: dict[str, str] = {}
    for approval in projects_root.glob("*approval*/approval-1.json"):
        try:
            request = _read_json(approval)
        except Exception:
            continue
        candidate_root = request.get("candidate_root") or request.get("worktree") or request.get("root")
        if isinstance(candidate_root, str) and Path(candidate_root).is_absolute() and Path(candidate_root).exists():
            values.append({"path": str(Path(candidate_root).resolve()), "reason": "APPROVAL_FREEZE_PROTECTED", "approval": str(approval)})
        freeze = approval.parent / "freeze.json"
        if freeze.is_file() and not freeze.is_symlink():
            try:
                frozen = _read_json(freeze)
                tree = str(frozen.get("candidate_tree_sha") or frozen.get("tree_sha") or "")
                if len(tree) == 40:
                    frozen_trees[tree] = str(approval)
            except Exception:
                pass
    if releases_root and releases_root.is_dir() and not releases_root.is_symlink():
        for release in releases_root.iterdir():
            if not release.is_dir() or release.is_symlink():
                continue
            tree = _git_tree(release)
            if tree in frozen_trees:
                values.append({
                    "path": str(release.resolve()),
                    "reason": "APPROVAL_FREEZE_PROTECTED",
                    "approval": frozen_trees[tree],
                })
    return values


def discover_parallel_worktrees(repo_root: Path) -> list[dict[str, str]]:
    roots: set[Path] = set()
    candidates = [repo_root]
    if not (repo_root / ".git").exists() and repo_root.is_dir():
        candidates = [path for path in repo_root.iterdir() if path.is_dir() and (path / ".git").exists()]
    for candidate in candidates:
        proc = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            text=True, capture_output=True, check=False, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            roots.add(Path(proc.stdout.strip()).resolve(strict=False))
    values: dict[str, dict[str, str]] = {}
    for root in roots:
        proc = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain"],
            text=True, capture_output=True, check=False, timeout=15,
        )
        if proc.returncode:
            raise StorageProtectionError("git_worktree_inventory_failed")
        current: dict[str, str] = {}
        for line in [*proc.stdout.splitlines(), ""]:
            if line.startswith("worktree "):
                current = {"path": str(Path(line.removeprefix("worktree ")).resolve(strict=False)), "reason": "PARALLEL_TASK_PROTECTED"}
            elif line.startswith("HEAD ") and current:
                current["sha"] = line.removeprefix("HEAD ")
            elif not line and current:
                values[current["path"]] = current
                current = {}
    return sorted(values.values(), key=lambda item: item["path"])


def bootstrap_registry(
    *,
    current_link: Path,
    releases_root: Path,
    stable_controller_link: Path,
    canonical_registry: Path,
    storage_registry: Path | None,
    projects_root: Path,
    repo_root: Path,
) -> dict[str, Any]:
    active = _symlink_release(current_link, releases_root, "ACTIVE_REQUIRED")
    controller = _symlink_release(stable_controller_link, releases_root, "CONTROL_PLANE_DEPENDENCY")
    canonical = _read_json(canonical_registry)
    worker = ((canonical.get("components") or {}).get("worker") or {})
    rollback_value = worker.get("rollback")
    if not isinstance(rollback_value, dict) and storage_registry and storage_registry.is_file():
        legacy_storage = _read_json(storage_registry)
        projected = legacy_storage.get("rollback_worker")
        if isinstance(projected, dict):
            rollback_value = {
                "sha": projected.get("sha"),
                "runtime_root": projected.get("path"),
                "source": "validated_storage_registry_projection",
            }
    rollback = None
    if isinstance(rollback_value, dict) and rollback_value.get("runtime_root"):
        rollback = {**_release_identity(Path(rollback_value["runtime_root"])), "reason": "ROLLBACK_REQUIRED"}
        if rollback.get("sha") != rollback_value.get("sha"):
            raise StorageProtectionError("rollback_registry_identity_mismatch")
    pending = discover_pending_approvals(projects_root, releases_root)
    parallel = discover_parallel_worktrees(projects_root)
    parallel_shas = {item.get("sha") for item in parallel if item.get("sha")}
    if parallel_shas and releases_root.is_dir():
        for release in releases_root.iterdir():
            if not release.is_dir() or release.is_symlink():
                continue
            try:
                identity = _release_identity(release)
            except StorageProtectionError:
                continue
            if identity["sha"] in parallel_shas:
                parallel.append({**identity, "reason": "PARALLEL_TASK_PROTECTED"})
    authorities = [active, controller, *([rollback] if rollback else []), *pending, *parallel]
    protected_paths = sorted({item["path"] for item in authorities})
    return {
        "schema": SCHEMA,
        "active_worker": active,
        "rollback_worker": rollback,
        "control_plane_dependencies": [controller],
        "approval_freeze_protected": pending,
        "parallel_task_protected": parallel,
        "protected_paths": protected_paths,
        "release_delete_default": "UNKNOWN_KEEP",
        "unknown_policy": "NEVER_DELETE",
        "bootstrap_complete": rollback is not None,
    }


def classify_release(path: Path, registry: dict[str, Any]) -> dict[str, Any]:
    resolved = str(path.resolve(strict=False))
    reasons = []
    for key in ("active_worker", "rollback_worker"):
        item = registry.get(key)
        if isinstance(item, dict) and item.get("path") == resolved:
            reasons.append(item.get("reason"))
    for key in ("control_plane_dependencies", "approval_freeze_protected", "parallel_task_protected"):
        reasons.extend(
            item.get("reason")
            for item in registry.get(key) or []
            if isinstance(item, dict) and item.get("path") == resolved
        )
    reasons = sorted({reason for reason in reasons if reason})
    return {
        "path": resolved,
        "classification": "PROTECTED" if reasons else "UNKNOWN",
        "reasons": reasons or ["UNKNOWN_KEEP"],
        "delete_eligible_now": False,
    }
