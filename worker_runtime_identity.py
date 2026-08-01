"""Authoritative Worker release identity resolved from the running checkout.

Environment metadata is accepted only as a corroborating value.  The Git HEAD
of the real runtime root is authoritative so a missing launchd environment can
never silently disable a release-bound control.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class WorkerRuntimeIdentityError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerRuntimeIdentity:
    runtime_root: str
    worker_sha: str
    source: str


def _git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkerRuntimeIdentityError("worker_runtime_git_identity_unavailable") from exc
    value = completed.stdout.strip()
    if not value:
        raise WorkerRuntimeIdentityError("worker_runtime_git_identity_empty")
    return value


def resolve_worker_runtime_identity(
    runtime_root: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> WorkerRuntimeIdentity:
    env = os.environ if environ is None else environ
    root = Path(runtime_root).expanduser().resolve()
    if not root.is_dir():
        raise WorkerRuntimeIdentityError("worker_runtime_root_missing")
    git_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    if git_root != root:
        raise WorkerRuntimeIdentityError("worker_runtime_root_mismatch")
    head = _git(root, "rev-parse", "HEAD").lower()
    if len(head) != 40 or any(ch not in "0123456789abcdef" for ch in head):
        raise WorkerRuntimeIdentityError("worker_runtime_sha_invalid")
    declared_root = str(env.get("WORKER_RUNTIME_ROOT") or "").strip()
    if declared_root and Path(declared_root).expanduser().resolve() != root:
        raise WorkerRuntimeIdentityError("worker_runtime_declared_root_mismatch")
    declared_sha = str(env.get("WORKER_GIT_SHA") or "").strip().lower()
    if declared_sha and declared_sha != head:
        raise WorkerRuntimeIdentityError("worker_runtime_declared_sha_mismatch")
    source = "env_verified_against_release_head" if declared_sha else "runtime_release_head"
    return WorkerRuntimeIdentity(str(root), head, source)


def export_worker_runtime_identity(identity: WorkerRuntimeIdentity) -> None:
    os.environ["WORKER_RUNTIME_ROOT"] = identity.runtime_root
    os.environ["WORKER_GIT_SHA"] = identity.worker_sha
    os.environ["WORKER_GIT_SHA_SOURCE"] = identity.source
