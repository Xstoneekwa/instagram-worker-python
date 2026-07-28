"""Pure, dormant Target Availability observation contract.

This module deliberately has no Supabase, logging, navigation, device or runtime
imports. It only normalizes and serializes observations for a future adapter.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping, Optional, Sequence, Tuple


USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")
ALLOWED_LOOKUP_RESULTS = frozenset({"found", "not_found", "unavailable", "failed", "unknown"})
ALLOWED_FOLLOWERS_SURFACES = frozenset({"normal", "restricted", "terminally_limited", "unknown"})
ALLOWED_RECOVERY_OUTCOMES = frozenset({"not_attempted", "succeeded", "failed", "ambiguous"})
ALLOWED_EVIDENCE_QUALITY = frozenset({"unknown", "low", "medium", "high"})
ALLOWED_NETWORK_STATES = frozenset({"unknown", "healthy", "degraded", "unavailable"})
ALLOWED_SESSION_STATES = frozenset({"unknown", "healthy", "restricted", "logged_out"})

TARGET_AVAILABILITY_REASON_CODES = frozenset(
    {
        "target_username_lookup_started",
        "target_profile_found",
        "target_profile_not_found",
        "target_stable_identity_observed",
        "target_verified_status_detected",
        "target_followers_surface_normal",
        "target_followers_entry_failed",
        "target_followers_surface_restricted",
        "target_followers_surface_terminally_limited",
        "target_repeated_first_profiles_detected",
        "target_navigation_retry_budget_exhausted",
        "target_navigation_timeout",
        "target_recovery_succeeded",
        "target_recovery_failed",
        "target_ui_ambiguity",
        "target_network_ambiguity",
    }
)


def _normalize_username(value: str) -> str:
    normalized = str(value or "").strip().lstrip("@").lower()
    if not USERNAME_RE.fullmatch(normalized):
        raise ValueError("invalid_target_username")
    return normalized


def _clean_optional_text(value: Optional[str], maximum: int = 200) -> Optional[str]:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    return cleaned[:maximum]


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("observed_at_timezone_required")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_mapping(value: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    payload = dict(value or {})
    json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return payload


@dataclass(frozen=True)
class TargetAvailabilityBudgets:
    """Future per-target wall-clock ceilings; not connected to runtime config."""

    lookup_budget_ms: int = 2_500
    followers_entry_budget_ms: int = 4_000
    retry_budget_count: int = 1
    navigation_budget_ms: int = 8_000
    availability_budget_ms: int = 10_000

    def __post_init__(self) -> None:
        if not 250 <= self.lookup_budget_ms <= 10_000:
            raise ValueError("lookup_budget_out_of_bounds")
        if not 500 <= self.followers_entry_budget_ms <= 15_000:
            raise ValueError("followers_entry_budget_out_of_bounds")
        if not 0 <= self.retry_budget_count <= 2:
            raise ValueError("retry_budget_out_of_bounds")
        if not self.lookup_budget_ms <= self.navigation_budget_ms <= 30_000:
            raise ValueError("navigation_budget_out_of_bounds")
        if not self.navigation_budget_ms <= self.availability_budget_ms <= 45_000:
            raise ValueError("availability_budget_out_of_bounds")


@dataclass(frozen=True)
class TargetAvailabilityObservationScope:
    tenant_id: str
    account_id: str
    target_id: str
    normalized_username: str
    stable_platform_user_id: Optional[str] = None

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "account_id", "target_id"):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError("%s_required" % field_name)
        object.__setattr__(self, "normalized_username", _normalize_username(self.normalized_username))
        object.__setattr__(
            self,
            "stable_platform_user_id",
            _clean_optional_text(self.stable_platform_user_id),
        )


@dataclass(frozen=True)
class TargetAvailabilityObservation:
    schema_version: str
    observation_id: str
    idempotency_key: str
    event_key: str
    observed_at: str
    tenant_id: str
    account_id: str
    target_id: str
    searched_username: str
    observed_username: Optional[str]
    observed_stable_platform_user_id: Optional[str]
    run_id: Optional[str]
    device_key: Optional[str]
    worker_version: Optional[str]
    instagram_version: Optional[str]
    lookup_result: str
    profile_found: Optional[bool]
    verified_badge: Optional[bool]
    followers_surface: str
    accessible_profiles_count: Optional[int]
    terminal_end_detected: bool
    repeated_first_profiles_detected: bool
    retry_count: int
    retry_budget_exhausted: bool
    navigation_timeout: bool
    recovery_outcome: str
    ui_evidence_quality: str
    network_state: str
    session_state: str
    reason_codes: Tuple[str, ...]
    evidence_safe: Mapping[str, Any]

    def to_dict(self) -> Mapping[str, Any]:
        payload = asdict(self)
        payload["reason_codes"] = list(self.reason_codes)
        payload["evidence_safe"] = dict(self.evidence_safe)
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def build_target_availability_observation(
    *,
    scope: TargetAvailabilityObservationScope,
    event_key: str,
    observed_at: datetime,
    reason_codes: Sequence[str],
    lookup_result: str = "unknown",
    profile_found: Optional[bool] = None,
    observed_username: Optional[str] = None,
    observed_stable_platform_user_id: Optional[str] = None,
    verified_badge: Optional[bool] = None,
    followers_surface: str = "unknown",
    accessible_profiles_count: Optional[int] = None,
    terminal_end_detected: bool = False,
    repeated_first_profiles_detected: bool = False,
    retry_count: int = 0,
    retry_budget_exhausted: bool = False,
    navigation_timeout: bool = False,
    recovery_outcome: str = "not_attempted",
    ui_evidence_quality: str = "unknown",
    network_state: str = "unknown",
    session_state: str = "unknown",
    run_id: Optional[str] = None,
    device_key: Optional[str] = None,
    worker_version: Optional[str] = None,
    instagram_version: Optional[str] = None,
    evidence_safe: Optional[Mapping[str, Any]] = None,
) -> TargetAvailabilityObservation:
    clean_event_key = _clean_optional_text(event_key)
    if not clean_event_key or len(clean_event_key) < 4:
        raise ValueError("event_key_invalid")
    if lookup_result not in ALLOWED_LOOKUP_RESULTS:
        raise ValueError("lookup_result_invalid")
    if followers_surface not in ALLOWED_FOLLOWERS_SURFACES:
        raise ValueError("followers_surface_invalid")
    if recovery_outcome not in ALLOWED_RECOVERY_OUTCOMES:
        raise ValueError("recovery_outcome_invalid")
    if ui_evidence_quality not in ALLOWED_EVIDENCE_QUALITY:
        raise ValueError("ui_evidence_quality_invalid")
    if network_state not in ALLOWED_NETWORK_STATES:
        raise ValueError("network_state_invalid")
    if session_state not in ALLOWED_SESSION_STATES:
        raise ValueError("session_state_invalid")
    if accessible_profiles_count is not None and int(accessible_profiles_count) < 0:
        raise ValueError("accessible_profiles_count_invalid")
    if not 0 <= int(retry_count) <= 100:
        raise ValueError("retry_count_invalid")

    normalized_reasons = tuple(sorted(set(str(reason or "").strip() for reason in reason_codes if str(reason or "").strip())))
    if not normalized_reasons or any(reason not in TARGET_AVAILABILITY_REASON_CODES for reason in normalized_reasons):
        raise ValueError("reason_codes_invalid")
    normalized_observed_username = (
        _normalize_username(observed_username) if _clean_optional_text(observed_username) else None
    )
    stable_id = _clean_optional_text(observed_stable_platform_user_id) or scope.stable_platform_user_id
    observed_at_iso = _iso_utc(observed_at)
    clean_run_id = _clean_optional_text(run_id)
    identity = "|".join(
        (
            "target-availability-observation-v1",
            scope.tenant_id,
            scope.account_id,
            scope.target_id,
            clean_run_id or "no-run",
            clean_event_key,
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return TargetAvailabilityObservation(
        schema_version="target-availability-observation-v1",
        observation_id="tao_%s" % digest[:24],
        idempotency_key="target-availability:%s" % digest,
        event_key=clean_event_key,
        observed_at=observed_at_iso,
        tenant_id=scope.tenant_id,
        account_id=scope.account_id,
        target_id=scope.target_id,
        searched_username=scope.normalized_username,
        observed_username=normalized_observed_username,
        observed_stable_platform_user_id=stable_id,
        run_id=clean_run_id,
        device_key=_clean_optional_text(device_key),
        worker_version=_clean_optional_text(worker_version),
        instagram_version=_clean_optional_text(instagram_version),
        lookup_result=lookup_result,
        profile_found=profile_found,
        verified_badge=verified_badge,
        followers_surface=followers_surface,
        accessible_profiles_count=(int(accessible_profiles_count) if accessible_profiles_count is not None else None),
        terminal_end_detected=bool(terminal_end_detected),
        repeated_first_profiles_detected=bool(repeated_first_profiles_detected),
        retry_count=int(retry_count),
        retry_budget_exhausted=bool(retry_budget_exhausted),
        navigation_timeout=bool(navigation_timeout),
        recovery_outcome=recovery_outcome,
        ui_evidence_quality=ui_evidence_quality,
        network_state=network_state,
        session_state=session_state,
        reason_codes=normalized_reasons,
        evidence_safe=_safe_mapping(evidence_safe),
    )


__all__ = [
    "TARGET_AVAILABILITY_REASON_CODES",
    "TargetAvailabilityBudgets",
    "TargetAvailabilityObservation",
    "TargetAvailabilityObservationScope",
    "build_target_availability_observation",
]
