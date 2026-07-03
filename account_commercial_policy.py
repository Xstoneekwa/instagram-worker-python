"""Per-account commercial policy revision guards for dispatcher and account session."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import supabase_client
from assignment_dispatch_resolver import sensitive_log_fields
from logs import log


def _read_revision_token(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    return str(row.get("revision_token") or "").strip()


def _parse_iso_ts(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        normalized = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def load_account_commercial_policy_revision(account_id: str) -> dict[str, Any] | None:
    """Latest policy revision for one account (package change signal)."""
    aid = str(account_id or "").strip()
    if not aid:
        return None
    return supabase_client.get_account_commercial_policy_revision(aid)


def stamp_commercial_policy_metadata(
    account_id: str,
    metadata_safe: dict[str, Any] | None,
) -> dict[str, Any]:
    """Attach current commercial_policy_revision when enqueueing a run request."""
    meta = dict(metadata_safe or {})
    if str(meta.get("commercial_policy_revision") or "").strip():
        return meta
    try:
        current = load_account_commercial_policy_revision(account_id) or {}
    except RuntimeError:
        return meta
    token = _read_revision_token(current)
    if token:
        meta["commercial_policy_revision"] = token
    package_code = str(current.get("package_code") or "").strip()
    if package_code:
        meta["commercial_package_code"] = package_code
    return meta


def policy_revision_changed(account_id: str, queued_revision: str | None) -> bool:
    queued = str(queued_revision or "").strip()
    if not queued:
        return False
    current = load_account_commercial_policy_revision(account_id)
    current_token = _read_revision_token(current)
    return bool(current_token and current_token != queued)


def evaluate_queued_run_commercial_policy(
    account_id: str,
    request_metadata: dict[str, Any] | None,
    *,
    request_created_at: str | None = None,
) -> tuple[bool, str | None, dict[str, Any]]:
    """Reject queued runs when commercial package revision changed since enqueue."""
    meta = dict(request_metadata or {})
    queued_revision = str(meta.get("commercial_policy_revision") or "").strip()
    current = load_account_commercial_policy_revision(account_id) or {}
    current_token = _read_revision_token(current)
    policy_ctx = {
        "queued_revision": queued_revision or None,
        "current_revision": current_token or None,
        "package_code": current.get("package_code"),
    }

    if queued_revision and current_token and queued_revision != current_token:
        return False, "commercial_policy_revision_changed", policy_ctx

    created_at = _parse_iso_ts(request_created_at)
    package_started_at = _parse_iso_ts(
        current.get("package_starts_at") or current.get("created_at"),
    )
    if not queued_revision and created_at and package_started_at and package_started_at > created_at:
        return False, "commercial_policy_revision_changed", {
            **policy_ctx,
            "package_started_after_queue": True,
        }

    return True, None, {
        **policy_ctx,
        "commercial_policy_revision": current_token or queued_revision or None,
    }


def revalidate_commercial_policy_at_boundary(
    account_id: str,
    *,
    run_id: str | None,
    boundary: str,
    bound_revision: str | None,
) -> dict[str, Any]:
    """Safe-boundary check during an active account session run."""
    current = load_account_commercial_policy_revision(account_id) or {}
    current_token = _read_revision_token(current)
    queued = str(bound_revision or "").strip()
    changed = bool(queued and current_token and queued != current_token)
    if changed:
        log(
            "info",
            "account_commercial_policy_revision_changed",
            account_id=account_id,
            run_id=run_id,
            boundary=boundary,
            queued_revision=queued,
            current_revision=current_token,
            package_code=current.get("package_code"),
        )
    return {
        "changed": changed,
        "current_revision": current_token or None,
        "package_code": current.get("package_code"),
        "boundary": boundary,
    }


def commercial_policy_boundary_blocks_phase(
    account_id: str,
    *,
    bound_revision: str | None,
    run_id: str | None,
    boundary: str,
) -> bool:
    """Central guard for all package-gated account_session phases."""
    return bool(
        revalidate_commercial_policy_at_boundary(
            account_id,
            run_id=run_id,
            boundary=boundary,
            bound_revision=bound_revision,
        ).get("changed")
    )


def load_account_effective_package_policy(account_id: str) -> dict[str, Any]:
    """Read live package caps/features for one account (no preference mutation)."""
    aid = str(account_id or "").strip()
    if not aid:
        return {}
    summary = supabase_client.get_account_package_summary(aid) or {}
    package_caps = summary.get("package_caps") if isinstance(summary.get("package_caps"), dict) else {}
    preview = summary.get("effective_caps_preview") if isinstance(summary.get("effective_caps_preview"), dict) else {}
    return {
        "account_id": aid,
        "commercial_package_code": str(summary.get("commercial_package_code") or "").strip() or None,
        "package_caps": package_caps,
        "effective_caps_preview": preview,
        "revision": _read_revision_token(load_account_commercial_policy_revision(aid)),
    }
