"""Canonical tenant ownership resolution for Target Availability.

The Availability domain currently names its partition key ``tenant_id``. Its
canonical value is the active ``client_instagram_accounts.client_id`` for the
Instagram account. Commercial package data is a consistency signal only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID


OWNERSHIP_SOURCE = "client_instagram_accounts"
OWNERSHIP_CONFLICT_REASON = "target_availability_tenant_ownership_conflict"


@dataclass(frozen=True)
class CanonicalAccountOwnership:
    account_id: str
    client_id: str
    is_active: bool
    source: str
    resolved_at: str


@dataclass(frozen=True)
class TargetAvailabilityTenantResolution:
    account_id: str
    tenant_id: str | None
    ownership: CanonicalAccountOwnership | None
    reason_code: str | None
    lookup_count: int

    @property
    def available(self) -> bool:
        return self.tenant_id is not None and self.ownership is not None and self.reason_code is None


def _uuid_text(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return str(UUID(raw))
    except (TypeError, ValueError, AttributeError):
        return None


def _failed(account_id: str, reason_code: str, *, lookup_count: int) -> TargetAvailabilityTenantResolution:
    return TargetAvailabilityTenantResolution(
        account_id=account_id,
        tenant_id=None,
        ownership=None,
        reason_code=reason_code,
        lookup_count=lookup_count,
    )


def resolve_target_availability_tenant(
    account_id: str,
    *,
    commercial_policy_revision: Mapping[str, Any] | None = None,
    ownership_reader: Callable[[str], Sequence[Mapping[str, Any]]] | None = None,
    resolved_at: datetime | None = None,
) -> TargetAvailabilityTenantResolution:
    """Resolve one strict active ownership without affecting the Instagram run."""

    aid = _uuid_text(account_id)
    if aid is None:
        return _failed(str(account_id or "").strip(), "target_availability_tenant_account_id_invalid", lookup_count=0)

    if ownership_reader is None:
        from supabase_client import get_active_client_instagram_account_ownership_rows

        ownership_reader = get_active_client_instagram_account_ownership_rows

    try:
        raw_rows = ownership_reader(aid)
    except Exception:
        return _failed(aid, "target_availability_tenant_ownership_lookup_failed", lookup_count=1)

    if not isinstance(raw_rows, (list, tuple)):
        return _failed(aid, "target_availability_tenant_ownership_response_malformed", lookup_count=1)
    if len(raw_rows) == 0:
        return _failed(aid, "target_availability_tenant_ownership_missing", lookup_count=1)
    if len(raw_rows) > 1:
        return _failed(aid, "target_availability_tenant_ownership_ambiguous", lookup_count=1)

    row = raw_rows[0]
    if not isinstance(row, Mapping):
        return _failed(aid, "target_availability_tenant_ownership_response_malformed", lookup_count=1)
    if row.get("active") is not True:
        return _failed(aid, "target_availability_tenant_ownership_inactive", lookup_count=1)
    if _uuid_text(row.get("account_id")) != aid:
        return _failed(aid, "target_availability_tenant_ownership_response_malformed", lookup_count=1)
    client_id = _uuid_text(row.get("client_id"))
    if client_id is None:
        return _failed(aid, "target_availability_tenant_ownership_response_malformed", lookup_count=1)

    revision_client_raw = str((commercial_policy_revision or {}).get("client_id") or "").strip()
    if revision_client_raw:
        revision_client_id = _uuid_text(revision_client_raw)
        if revision_client_id is None:
            return _failed(aid, "target_availability_commercial_revision_client_id_malformed", lookup_count=1)
        if revision_client_id != client_id:
            return _failed(aid, OWNERSHIP_CONFLICT_REASON, lookup_count=1)

    observed = resolved_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    ownership = CanonicalAccountOwnership(
        account_id=aid,
        client_id=client_id,
        is_active=True,
        source=OWNERSHIP_SOURCE,
        resolved_at=observed.astimezone(timezone.utc).isoformat(),
    )
    return TargetAvailabilityTenantResolution(
        account_id=aid,
        tenant_id=client_id,
        ownership=ownership,
        reason_code=None,
        lookup_count=1,
    )


__all__ = [
    "CanonicalAccountOwnership",
    "OWNERSHIP_CONFLICT_REASON",
    "OWNERSHIP_SOURCE",
    "TargetAvailabilityTenantResolution",
    "resolve_target_availability_tenant",
]
