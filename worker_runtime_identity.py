"""Authoritative Worker release identity resolved from the running checkout.

Environment metadata is accepted only as a corroborating value.  The Git HEAD
of the real runtime root is authoritative so a missing launchd environment can
never silently disable a release-bound control.
"""

from __future__ import annotations

import os
import json
import subprocess
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


RUNTIME_IDENTITY_ENV = "WORKER_RUNTIME_IDENTITY_V2"
_FULL_SHA_LENGTH = 40
_GIT_TIMEOUT_SECONDS = 5.0
_GIT_TIMEOUT_ATTEMPTS = 3
_GIT_RETRY_DELAY_SECONDS = 0.2


class WorkerRuntimeIdentityError(RuntimeError):
    def __init__(
        self,
        reason: str,
        *,
        stage: str = "resolve",
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.stage = stage
        self.diagnostics = dict(diagnostics or {})


@dataclass(frozen=True)
class WorkerRuntimeIdentity:
    runtime_root: str
    worker_sha: str
    source: str
    release_path: str = ""
    release_head: str = ""
    runtime_root_ok: bool = True
    verified_at: str = ""
    wrapper_pid: int | None = None
    consumer_pid: int | None = None
    request_id: str = ""
    run_id: str = ""
    attempt_id: int | None = None


def _valid_full_sha(value: str) -> bool:
    return len(value) == _FULL_SHA_LENGTH and all(
        ch in "0123456789abcdef" for ch in value
    )


def _safe_pid(value: Any) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _transport_payload(environ: Mapping[str, str]) -> dict[str, Any]:
    raw = str(environ.get(RUNTIME_IDENTITY_ENV) or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise WorkerRuntimeIdentityError(
            "identity_not_propagated",
            stage="transport_decode",
            diagnostics={"transport_present": True, "exception_type": type(exc).__name__},
        ) from exc
    if not isinstance(value, dict):
        raise WorkerRuntimeIdentityError(
            "identity_not_propagated",
            stage="transport_decode",
            diagnostics={"transport_present": True, "payload_type": type(value).__name__},
        )
    if value.get("schema") != "WORKER_RUNTIME_IDENTITY_V2":
        raise WorkerRuntimeIdentityError(
            "identity_not_propagated",
            stage="transport_decode",
            diagnostics={"transport_present": True, "schema_valid": False},
        )
    return value


def _git(root: Path, *args: str) -> str:
    try:
        exact_root = Path(root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise WorkerRuntimeIdentityError("worker_runtime_git_identity_unavailable") from exc
    command = [
        "git",
        "-c",
        f"safe.directory={exact_root}",
        "-C",
        str(exact_root),
        *args,
    ]
    completed: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, _GIT_TIMEOUT_ATTEMPTS + 1):
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
            break
        except subprocess.TimeoutExpired as exc:
            if attempt >= _GIT_TIMEOUT_ATTEMPTS:
                raise WorkerRuntimeIdentityError(
                    "worker_runtime_git_identity_unavailable",
                    stage="git_identity_timeout",
                    diagnostics={
                        "attempts": attempt,
                        "timeout_seconds": _GIT_TIMEOUT_SECONDS,
                    },
                ) from exc
            time.sleep(_GIT_RETRY_DELAY_SECONDS)
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorkerRuntimeIdentityError("worker_runtime_git_identity_unavailable") from exc
    if completed is None:
        raise WorkerRuntimeIdentityError("worker_runtime_git_identity_unavailable")
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
    transport = _transport_payload(env)
    root = Path(runtime_root).expanduser().resolve()
    if not root.is_dir():
        raise WorkerRuntimeIdentityError("worker_runtime_root_missing")
    git_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    if git_root != root:
        raise WorkerRuntimeIdentityError("worker_runtime_root_mismatch")
    head = _git(root, "rev-parse", "HEAD").lower()
    if not _valid_full_sha(head):
        raise WorkerRuntimeIdentityError("worker_runtime_sha_invalid")
    declared_root = str(env.get("WORKER_RUNTIME_ROOT") or "").strip()
    if declared_root and Path(declared_root).expanduser().resolve() != root:
        raise WorkerRuntimeIdentityError("worker_runtime_declared_root_mismatch")
    declared_root_ok = str(env.get("WORKER_RUNTIME_ROOT_OK") or "").strip().lower()
    if declared_root_ok and declared_root_ok not in {"1", "true", "yes", "on"}:
        raise WorkerRuntimeIdentityError("runtime_root_unresolved")
    declared_sha = str(env.get("WORKER_GIT_SHA") or "").strip().lower()
    if declared_sha and declared_sha != head:
        raise WorkerRuntimeIdentityError("worker_runtime_declared_sha_mismatch")
    declared_head = str(env.get("WORKER_RELEASE_HEAD") or "").strip().lower()
    if declared_head and declared_head != head:
        raise WorkerRuntimeIdentityError("worker_runtime_declared_head_mismatch")
    transported_root = str(transport.get("runtime_root") or "").strip()
    transported_release_path = str(transport.get("release_path") or "").strip()
    transported_sha = str(transport.get("worker_sha") or "").strip().lower()
    transported_head = str(transport.get("release_head") or "").strip().lower()
    if transport and not transported_root:
        raise WorkerRuntimeIdentityError(
            "runtime_root_unresolved",
            stage="transport_verify",
            diagnostics={"transport_present": True, "runtime_root_candidate_present": False},
        )
    if transport and transport.get("runtime_root_ok") is not True:
        raise WorkerRuntimeIdentityError(
            "runtime_root_unresolved",
            stage="transport_verify",
            diagnostics={"transport_present": True, "runtime_root_ok": False},
        )
    if transported_root and Path(transported_root).expanduser().resolve() != root:
        raise WorkerRuntimeIdentityError(
            "runtime_root_mismatch",
            stage="transport_verify",
            diagnostics={"transport_present": True, "runtime_root_candidate_present": True},
        )
    if transport and not transported_release_path:
        raise WorkerRuntimeIdentityError(
            "runtime_root_unresolved",
            stage="transport_verify",
            diagnostics={"transport_present": True, "release_path_present": False},
        )
    if transported_release_path and Path(transported_release_path).expanduser().resolve() != root:
        raise WorkerRuntimeIdentityError(
            "runtime_root_mismatch",
            stage="transport_verify",
            diagnostics={"transport_present": True, "release_path_present": True},
        )
    if transport and not transported_sha:
        raise WorkerRuntimeIdentityError(
            "worker_sha_env_missing",
            stage="transport_verify",
            diagnostics={"transport_present": True, "worker_sha_present": False},
        )
    if transported_sha and transported_sha != head:
        raise WorkerRuntimeIdentityError(
            "env_head_mismatch",
            stage="transport_verify",
            diagnostics={"transport_present": True, "worker_sha_present": True},
        )
    if transport and not transported_head:
        raise WorkerRuntimeIdentityError(
            "release_head_unresolved",
            stage="transport_verify",
            diagnostics={"transport_present": True, "release_head_present": False},
        )
    if transported_head and transported_head != head:
        raise WorkerRuntimeIdentityError(
            "env_head_mismatch",
            stage="transport_verify",
            diagnostics={"transport_present": True, "release_head_present": True},
        )
    source = (
        "transport_verified_against_release_head"
        if transport
        else "env_verified_against_release_head"
        if declared_sha
        else "runtime_release_head"
    )
    return WorkerRuntimeIdentity(
        runtime_root=str(root),
        worker_sha=head,
        source=source,
        release_path=str(root),
        release_head=head,
        runtime_root_ok=True,
        verified_at=(
            str(transport.get("verified_at") or "").strip()
            or datetime.now(timezone.utc).isoformat()
        ),
        wrapper_pid=(
            _safe_pid(transport.get("wrapper_pid"))
            or _safe_pid(env.get("WORKER_RUNTIME_WRAPPER_PID"))
        ),
        consumer_pid=(
            _safe_pid(transport.get("consumer_pid"))
            or _safe_pid(env.get("WORKER_RUNTIME_CONSUMER_PID"))
        ),
        request_id=str(transport.get("request_id") or "").strip(),
        run_id=str(transport.get("run_id") or "").strip(),
        attempt_id=_safe_pid(transport.get("attempt_id")),
    )


def bind_worker_runtime_identity(
    identity: WorkerRuntimeIdentity,
    *,
    request_id: str | None = None,
    run_id: str | None = None,
    attempt_id: int | None = None,
    consumer_pid: int | None = None,
) -> WorkerRuntimeIdentity:
    return replace(
        identity,
        request_id=str(request_id if request_id is not None else identity.request_id or "").strip(),
        run_id=str(run_id if run_id is not None else identity.run_id or "").strip(),
        attempt_id=(int(attempt_id) if attempt_id is not None else identity.attempt_id),
        consumer_pid=(int(consumer_pid) if consumer_pid else identity.consumer_pid),
    )


def worker_runtime_identity_env(identity: WorkerRuntimeIdentity) -> dict[str, str]:
    payload = {
        "schema": "WORKER_RUNTIME_IDENTITY_V2",
        "worker_sha": identity.worker_sha,
        "release_path": identity.release_path or identity.runtime_root,
        "release_head": identity.release_head or identity.worker_sha,
        "runtime_root": identity.runtime_root,
        "runtime_root_ok": bool(identity.runtime_root_ok),
        "source": identity.source,
        "verified_at": identity.verified_at,
        "wrapper_pid": identity.wrapper_pid,
        "consumer_pid": identity.consumer_pid,
        "request_id": identity.request_id,
        "run_id": identity.run_id,
        "attempt_id": identity.attempt_id,
    }
    return {
        "WORKER_RUNTIME_ROOT": identity.runtime_root,
        "WORKER_GIT_SHA": identity.worker_sha,
        "WORKER_GIT_SHA_SOURCE": identity.source,
        "WORKER_RELEASE_HEAD": identity.release_head or identity.worker_sha,
        "WORKER_RUNTIME_ROOT_OK": "true" if identity.runtime_root_ok else "false",
        "WORKER_RUNTIME_CONSUMER_PID": str(identity.consumer_pid or ""),
        RUNTIME_IDENTITY_ENV: json.dumps(payload, separators=(",", ":"), sort_keys=True),
    }


def validate_worker_runtime_identity_binding(
    identity: WorkerRuntimeIdentity | None,
    *,
    request_id: str,
    run_id: str,
    attempt_id: int,
) -> tuple[str, dict[str, Any]]:
    diagnostics: dict[str, Any] = {
        "stage": "ct_resume_identity_binding",
        "pid": os.getpid(),
        "cwd": str(Path.cwd()),
        "worker_sha_env_present": bool(os.environ.get("WORKER_GIT_SHA")),
        "runtime_root_env_present": bool(os.environ.get("WORKER_RUNTIME_ROOT")),
        "transport_present": bool(os.environ.get(RUNTIME_IDENTITY_ENV)),
    }
    if identity is None:
        return "identity_not_propagated", diagnostics
    diagnostics.update(
        {
            "identity_source": identity.source,
            "worker_sha_present": bool(identity.worker_sha),
            "worker_sha_candidate": identity.worker_sha or None,
            "release_head_present": bool(identity.release_head),
            "release_head_candidate": identity.release_head or None,
            "runtime_root_candidate_present": bool(identity.runtime_root),
            "runtime_root_ok": bool(identity.runtime_root_ok),
            "release_path_redacted": Path(identity.release_path or identity.runtime_root).name,
            "wrapper_pid": identity.wrapper_pid,
            "consumer_pid": identity.consumer_pid,
            "identity_request_id_present": bool(identity.request_id),
            "identity_run_id_present": bool(identity.run_id),
            "identity_attempt_id": identity.attempt_id,
        }
    )
    if not identity.worker_sha:
        return "worker_sha_env_missing", diagnostics
    if not _valid_full_sha(identity.worker_sha.lower()):
        return "worker_sha_invalid_format", diagnostics
    if not identity.release_head:
        return "release_head_unresolved", diagnostics
    if identity.release_head.lower() != identity.worker_sha.lower():
        return "env_head_mismatch", diagnostics
    if not identity.runtime_root:
        return "runtime_root_unresolved", diagnostics
    if not identity.runtime_root_ok:
        return "runtime_root_unresolved", diagnostics
    if Path(identity.release_path or identity.runtime_root).resolve() != Path(identity.runtime_root).resolve():
        return "runtime_root_mismatch", diagnostics
    if identity.request_id and identity.request_id != str(request_id or ""):
        return "identity_request_mismatch", diagnostics
    if identity.run_id and identity.run_id != str(run_id or ""):
        return "identity_run_mismatch", diagnostics
    if identity.attempt_id is not None and int(identity.attempt_id) != int(attempt_id):
        return "identity_attempt_mismatch", diagnostics
    return "identity_verified", diagnostics


def export_worker_runtime_identity(identity: WorkerRuntimeIdentity) -> None:
    os.environ.update(worker_runtime_identity_env(identity))
