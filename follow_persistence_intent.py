from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL_STAGES = {
    "persisted",
    "reconciled",
    "terminal",
    "abandoned_before_verified_follow",
    "abandoned_before_physical_attempt",
    "review_required",
}
RECEIPT_SCHEMA = "FOLLOW_CANDIDATE_LOCAL_RECEIPT_V2"
MUTATION_INTENT_SCHEMA = "AMBIGUOUS_MUTATION_INTENT_V1"


class FollowPersistenceRuntimeUnavailable(RuntimeError):
    """Global fail-closed boundary: durable Follow intent storage is unusable."""

    reason = "follow_persistence_runtime_unavailable"


def preflight_runtime_storage() -> dict[str, Any]:
    """Prove the intent journal can durably create/replace/fsync before UI work."""

    root = _root()
    probe_dir = root / ".preflight"
    probe_path = probe_dir / f"{os.getpid()}.json"
    try:
        _atomic_write(
            probe_path,
            {
                "schema": "FOLLOW_PERSISTENCE_RUNTIME_PREFLIGHT_V1",
                "pid": os.getpid(),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        probe_path.unlink()
        dir_fd = os.open(probe_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception as exc:
        raise FollowPersistenceRuntimeUnavailable(
            f"follow_persistence_runtime_unavailable:{type(exc).__name__}"
        ) from exc
    return {"ok": True, "root": str(root), "schema": "FOLLOW_PERSISTENCE_RUNTIME_PREFLIGHT_V1"}


def _root() -> Path:
    runtime_home = Path(os.getenv("PHONEFARM_RUNTIME_HOME", "/Users/admin/phonefarm-runtime"))
    return Path(
        os.getenv(
            "FOLLOW_PERSISTENCE_INTENT_ROOT",
            str(runtime_home / "run" / "follow-persistence-intents"),
        )
    )


def _intent_path(run_id: str, action_id: str) -> Path:
    return _root() / str(run_id) / f"{action_id}.json"


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        offset = 0
        while offset < len(encoded):
            offset += os.write(fd, encoded[offset:])
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def create_prepared_intent(
    *,
    action_id: str,
    account_id: str,
    run_id: str,
    request_id: str,
    candidate_username: str,
    source_target_id: str | None,
    source_ct_username: str | None,
    settings_revision: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "version": 2,
        "receipt_schema": RECEIPT_SCHEMA,
        "action_id": str(action_id),
        "account_id": str(account_id),
        "run_id": str(run_id),
        "request_id": str(request_id),
        "candidate_username": str(candidate_username).strip().lstrip("@").lower(),
        "source_target_id": str(source_target_id or "") or None,
        "source_ct_username": str(source_ct_username or "").strip().lstrip("@").lower() or None,
        "settings_revision": str(settings_revision),
        "stage": "prepared_before_follow_tap",
        "followed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    _atomic_write(_intent_path(run_id, action_id), payload)
    return payload


def create_mutation_intent(
    *,
    action_id: str,
    action_type: str,
    account_id: str,
    run_id: str,
    request_id: str,
    candidate_username: str,
    business_session_id: str | None = None,
    attempt_id: str | None = None,
    intent_generation: str | None = None,
    source_target_id: str | None = None,
    source_ct_username: str | None = None,
    business_date: str | None = None,
    worker_sha: str | None = None,
    settings_revision: str | None = None,
    interaction_row_id: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    action = str(action_type or "").strip().lower()
    if action not in {"follow", "unfollow"}:
        raise ValueError("unsupported_mutation_action_type")
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "version": 3,
        "receipt_schema": MUTATION_INTENT_SCHEMA,
        "action_id": str(action_id),
        "idempotency_key": str(action_id),
        "action_type": action,
        "account_id": str(account_id),
        "business_session_id": str(business_session_id or "") or None,
        "run_id": str(run_id),
        "request_id": str(request_id),
        "attempt_id": str(attempt_id or "") or None,
        "intent_generation": str(intent_generation or attempt_id or "") or None,
        "candidate_username": str(candidate_username).strip().lstrip("@").lower(),
        "source_target_id": str(source_target_id or "") or None,
        "source_ct_username": str(source_ct_username or "").strip().lstrip("@").lower() or None,
        "business_date": str(business_date or "") or None,
        "worker_sha": str(worker_sha or "") or None,
        "settings_revision": str(settings_revision or ""),
        "interaction_row_id": str(interaction_row_id or "") or None,
        "mode": str(mode or "") or None,
        "stage": "prepared",
        "physical_attempt_started_at": None,
        "physical_retry_count": 0,
        "followed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    _atomic_write(_intent_path(run_id, action_id), payload)
    return payload


def update_intent_stage(
    *,
    run_id: str,
    action_id: str,
    stage: str,
    followed_at: str | None = None,
    metadata_safe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = _intent_path(run_id, action_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["stage"] = str(stage)
    if str(stage) == "physical_attempt_started" and not payload.get(
        "physical_attempt_started_at"
    ):
        payload["physical_attempt_started_at"] = datetime.now(timezone.utc).isoformat()
    if followed_at is not None:
        payload["followed_at"] = str(followed_at)
    if metadata_safe:
        payload["receipt_metadata_safe"] = {
            **dict(payload.get("receipt_metadata_safe") or {}),
            **dict(metadata_safe),
        }
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(path, payload)
    return payload


def update_intent_metadata(
    *, run_id: str, action_id: str, metadata_safe: dict[str, Any]
) -> dict[str, Any]:
    path = _intent_path(run_id, action_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["receipt_metadata_safe"] = {
        **dict(payload.get("receipt_metadata_safe") or {}),
        **dict(metadata_safe or {}),
    }
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(path, payload)
    return payload


def load_nonterminal_intents(*, account_id: str, run_id: str) -> list[dict[str, Any]]:
    run_dir = _root() / str(run_id)
    if not run_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(run_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"follow_persistence_intent_unreadable:{path.name}") from exc
        if str(payload.get("account_id") or "") != str(account_id):
            raise RuntimeError(f"follow_persistence_intent_account_mismatch:{path.name}")
        if str(payload.get("run_id") or "") != str(run_id):
            raise RuntimeError(f"follow_persistence_intent_run_mismatch:{path.name}")
        if str(payload.get("stage") or "") not in TERMINAL_STAGES:
            out.append(payload)
    return out


def load_scoped_nonterminal_intents(
    *,
    account_id: str,
    current_run_id: str,
    business_session_id: str | None,
    attempt_id: str | None = None,
    intent_generation: str | None = None,
    lineage_run_ids: list[str] | tuple[str, ...] | None = None,
    strict_lineage_only: bool = False,
) -> list[dict[str, Any]]:
    """Load only ambiguity that can belong to the active business lineage.

    Auto Restart creates a new run id while retaining the business-session id.
    A current-run-only lookup would therefore lose the exact interruption this
    journal exists to reconcile.  Conversely, an old business session must
    never gate future work.  Legacy V2 receipts have no business-session id and
    remain deliberately current-run scoped.
    """
    session_id = str(business_session_id or "").strip()
    attempt = str(attempt_id or "").strip()
    generation = str(intent_generation or attempt_id or "").strip()
    # P0C is a synchronous recovery boundary.  Never spend its fixed budget
    # walking the historical journal.  The caller must provide the exact
    # current/restart lineage; current_run_id is always inspected first.
    if not strict_lineage_only and lineage_run_ids is None and session_id:
        # Compatibility path for non-P0C maintenance callers.  The C+ recovery
        # boundary always supplies strict_lineage_only=True and can therefore
        # never reach this historical journal scan.
        return [
            payload
            for payload in load_all_nonterminal_intents(limit=1000)
            if str(payload.get("account_id") or "") == str(account_id)
            and str(payload.get("business_session_id") or "").strip() == session_id
        ]
    exact_run_ids: list[str] = []
    for raw in (str(current_run_id), *(lineage_run_ids or ())):
        value = str(raw or "").strip()
        if value and value not in exact_run_ids:
            exact_run_ids.append(value)
    out: list[dict[str, Any]] = []
    for run_key in exact_run_ids:
        run_dir = _root() / run_key
        if not run_dir.is_dir():
            continue
        for path in sorted(run_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise RuntimeError(
                    f"follow_persistence_intent_unreadable:{path.name}"
                ) from exc
            if str(payload.get("stage") or "") in TERMINAL_STAGES:
                continue
            if str(payload.get("account_id") or "") != str(account_id):
                continue
            payload_run_id = str(payload.get("run_id") or "")
            payload_session_id = str(payload.get("business_session_id") or "").strip()
            payload_attempt = str(payload.get("attempt_id") or "").strip()
            payload_generation = str(
                payload.get("intent_generation") or payload_attempt
            ).strip()
            if payload_run_id != run_key:
                raise RuntimeError(
                    f"follow_persistence_intent_run_mismatch:{path.name}"
                )
            if session_id and payload_session_id != session_id:
                continue
            if not session_id and payload_run_id != str(current_run_id):
                continue
            if attempt and payload_attempt and payload_attempt != attempt:
                continue
            if generation and payload_generation and payload_generation != generation:
                continue
            out.append(payload)
    return out


def load_all_nonterminal_intents(*, limit: int = 100) -> list[dict[str, Any]]:
    root = _root()
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"follow_persistence_intent_unreadable:{path.name}") from exc
        if str(payload.get("stage") or "") not in TERMINAL_STAGES:
            out.append(payload)
            if len(out) >= max(1, int(limit or 1)):
                break
    return out
