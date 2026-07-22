"""Pure, passive comparison of legacy and canonical Follow limits."""

from __future__ import annotations

from typing import Any, Mapping


SHADOW_CLASSIFICATIONS = {
    "exact_match",
    "numeric_match_source_difference",
    "canonical_lower_than_legacy",
    "canonical_higher_than_legacy",
    "mixed_difference",
    "canonical_not_evaluable",
    "canonical_payload_invalid",
    "shadow_disabled",
}
FOLLOW_PACKAGES = {"growth", "pro", "premium"}
OVERRIDE_SOURCES = {"admin", "support", "migration_confirmed"}
LIMITING_SOURCES = {"package_default", "account_override", "warmup", "mixed"}
LIMITING_REASONS = {
    "limited_by_package",
    "limited_by_account_override",
    "limited_by_warmup",
    "override_above_package_bounded",
}
TOP_LEVEL_FIELDS = {
    "account_id",
    "package",
    "package_limits",
    "account_override",
    "warmup",
    "business_effective",
}
SECTION_FIELDS = {
    "package_limits": {"day", "session"},
    "account_override": {"present", "day", "session", "source", "above_package", "status"},
    "warmup": {"enabled", "day", "day_cap", "session_cap", "timezone"},
    "business_effective": {
        "day",
        "session",
        "limiting_source",
        "limiting_reason",
        "day_limiting_source",
        "session_limiting_source",
    },
}


def shadow_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _empty_result(status: str, classification: str, legacy: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "shadow_status": status,
        "legacy": dict(legacy),
        "canonical_business": {
            "day_cap": None,
            "session_cap": None,
            "limiting_source": "",
            "limiting_reason": "",
        },
        "shadow_runtime": {
            "day_cap": None,
            "session_cap": None,
            "remaining_today": None,
            "final_run_cap": None,
            "limiting_source": "",
            "limiting_reason": "",
        },
        "comparison": {
            "classification": classification,
            "day_delta": None,
            "session_delta": None,
            "run_delta": None,
            "numeric_match": False,
            "source_match": False,
        },
    }


def legacy_view(legacy_resolved_limits: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "day_cap": legacy_resolved_limits.get("effective_follow_day_cap"),
        "session_cap": legacy_resolved_limits.get("effective_follow_session_cap"),
        "remaining_today": legacy_resolved_limits.get("follow_day_remaining_today"),
        "final_run_cap": legacy_resolved_limits.get("effective_follow_max"),
        "limiting_source": str(legacy_resolved_limits.get("limiting_source") or ""),
        "limiting_reason": str(legacy_resolved_limits.get("source") or ""),
    }


def _payload_error(
    payload: Any,
    *,
    account_id: str,
    package: str,
) -> tuple[str, str] | None:
    if payload is None:
        return "not_evaluable", "canonical_not_evaluable"
    if not isinstance(payload, Mapping):
        return "invalid_payload", "canonical_payload_invalid"
    if set(payload) - TOP_LEVEL_FIELDS:
        return "invalid_payload", "canonical_payload_invalid"
    if not str(account_id or "").strip() or str(payload.get("account_id") or "").strip() != str(account_id).strip():
        return "invalid_payload", "canonical_payload_invalid"
    payload_package = str(payload.get("package") or "").strip().lower()
    expected_package = str(package or "").strip().lower()
    if payload_package != expected_package or payload_package not in FOLLOW_PACKAGES:
        return "invalid_payload", "canonical_payload_invalid"

    sections: dict[str, Mapping[str, Any]] = {}
    for name, allowed in SECTION_FIELDS.items():
        value = payload.get(name)
        if value is None:
            return "not_evaluable", "canonical_not_evaluable"
        if not isinstance(value, Mapping) or set(value) - allowed:
            return "invalid_payload", "canonical_payload_invalid"
        sections[name] = value

    package_limits = sections["package_limits"]
    override = sections["account_override"]
    warmup = sections["warmup"]
    business = sections["business_effective"]
    package_day = _positive_int(package_limits.get("day"))
    package_session = _positive_int(package_limits.get("session"))
    business_day = _positive_int(business.get("day"))
    business_session = _positive_int(business.get("session"))
    if None in {package_day, package_session, business_day, business_session}:
        return "invalid_payload", "canonical_payload_invalid"
    if business_day > package_day or business_session > package_session:
        return "invalid_payload", "canonical_payload_invalid"

    present = override.get("present")
    if not isinstance(present, bool):
        return "invalid_payload", "canonical_payload_invalid"
    override_day = override.get("day")
    override_session = override.get("session")
    override_source = override.get("source")
    if present:
        if override_source not in OVERRIDE_SOURCES:
            return "invalid_payload", "canonical_payload_invalid"
        if override_day is None and override_session is None:
            return "invalid_payload", "canonical_payload_invalid"
        if override_day is not None and _positive_int(override_day) is None:
            return "invalid_payload", "canonical_payload_invalid"
        if override_session is not None and _positive_int(override_session) is None:
            return "invalid_payload", "canonical_payload_invalid"
    elif override_day is not None or override_session is not None or override_source is not None:
        return "invalid_payload", "canonical_payload_invalid"

    warmup_enabled = warmup.get("enabled")
    if not isinstance(warmup_enabled, bool):
        return "invalid_payload", "canonical_payload_invalid"
    warmup_day = warmup.get("day")
    warmup_day_cap = warmup.get("day_cap")
    warmup_session_cap = warmup.get("session_cap")
    if warmup_enabled:
        if any(_positive_int(value) is None for value in (warmup_day, warmup_day_cap, warmup_session_cap)):
            return "not_evaluable", "canonical_not_evaluable"
        if warmup_day_cap > package_day or warmup_session_cap > package_session:
            return "invalid_payload", "canonical_payload_invalid"
    elif any(value is not None for value in (warmup_day, warmup_day_cap, warmup_session_cap)):
        return "invalid_payload", "canonical_payload_invalid"

    limiting_source = business.get("limiting_source")
    limiting_reason = business.get("limiting_reason")
    reason_valid = limiting_reason in LIMITING_REASONS or (
        isinstance(limiting_reason, str)
        and limiting_reason.startswith("day_limited_by_")
        and ";session_limited_by_" in limiting_reason
        and len(limiting_reason) <= 160
    ) or (
        isinstance(limiting_reason, str)
        and limiting_reason.startswith("warmup_day_")
        and limiting_reason[11:].isdigit()
        and len(limiting_reason) <= 32
    )
    if limiting_source not in LIMITING_SOURCES or not reason_valid:
        return "invalid_payload", "canonical_payload_invalid"

    expected_day = min(package_day, override_day or package_day, warmup_day_cap or package_day)
    expected_session = min(package_session, override_session or package_session, warmup_session_cap or package_session)
    if business_day != expected_day or business_session != expected_session:
        return "invalid_payload", "canonical_payload_invalid"
    return None


def _bounded_cap(value: Any) -> int | None:
    return _positive_int(value) if value is not None else None


def evaluate_follow_limit_shadow(
    *,
    enabled: Any,
    account_id: str,
    account_username: str | None,
    package: str,
    legacy_resolved_limits: Mapping[str, Any],
    canonical_payload: Any,
    completed_today: Any,
    ops_day_hard_cap: Any = None,
    ops_session_hard_cap: Any = None,
    run_specific_hard_cap: Any = None,
) -> dict[str, Any]:
    """Calculate shadow values without mutating or returning runtime authority."""
    del account_username  # Accepted for the caller contract; never required for calculation.
    legacy = legacy_view(legacy_resolved_limits)
    if not shadow_enabled(enabled):
        return _empty_result("disabled", "shadow_disabled", legacy)

    payload_error = _payload_error(canonical_payload, account_id=account_id, package=package)
    if payload_error:
        return _empty_result(payload_error[0], payload_error[1], legacy)
    completed = _nonnegative_int(completed_today)
    if completed is None:
        return _empty_result("invalid_payload", "canonical_payload_invalid", legacy)

    business = canonical_payload["business_effective"]
    business_day = int(business["day"])
    business_session = int(business["session"])
    ops_day = _bounded_cap(ops_day_hard_cap)
    ops_session = _bounded_cap(ops_session_hard_cap)
    run_cap = _bounded_cap(run_specific_hard_cap)
    if (ops_day_hard_cap is not None and ops_day is None) or (
        ops_session_hard_cap is not None and ops_session is None
    ) or (run_specific_hard_cap is not None and run_cap is None):
        return _empty_result("invalid_payload", "canonical_payload_invalid", legacy)

    shadow_day = min(business_day, ops_day or business_day)
    shadow_session = min(business_session, ops_session or business_session)
    remaining = max(0, shadow_day - completed)
    final_run = min(shadow_session, remaining, run_cap or shadow_session)
    shadow_source = "runtime_guard" if (shadow_day != business_day or shadow_session != business_session or final_run < shadow_session) else str(business["limiting_source"])
    shadow_reason = "bounded_by_worker_runtime_guard" if shadow_source == "runtime_guard" else str(business["limiting_reason"])

    legacy_numbers = (legacy["day_cap"], legacy["session_cap"], legacy["final_run_cap"])
    shadow_numbers = (shadow_day, shadow_session, final_run)
    deltas = tuple(
        shadow - old if isinstance(old, int) and not isinstance(old, bool) else None
        for old, shadow in zip(legacy_numbers, shadow_numbers)
    )
    numeric_match = all(delta == 0 for delta in deltas)
    source_match = legacy["limiting_source"] == business["limiting_source"]
    if numeric_match:
        classification = "exact_match" if source_match else "numeric_match_source_difference"
    elif all(delta is not None and delta <= 0 for delta in deltas) and any(delta < 0 for delta in deltas):
        classification = "canonical_lower_than_legacy"
    elif all(delta is not None and delta >= 0 for delta in deltas) and any(delta > 0 for delta in deltas):
        classification = "canonical_higher_than_legacy"
    else:
        classification = "mixed_difference"

    return {
        "shadow_status": "evaluated",
        "legacy": legacy,
        "canonical_business": {
            "day_cap": business_day,
            "session_cap": business_session,
            "limiting_source": str(business["limiting_source"]),
            "limiting_reason": str(business["limiting_reason"]),
        },
        "shadow_runtime": {
            "day_cap": shadow_day,
            "session_cap": shadow_session,
            "remaining_today": remaining,
            "final_run_cap": final_run,
            "limiting_source": shadow_source,
            "limiting_reason": shadow_reason,
        },
        "comparison": {
            "classification": classification,
            "day_delta": deltas[0],
            "session_delta": deltas[1],
            "run_delta": deltas[2],
            "numeric_match": numeric_match,
            "source_match": source_match,
        },
    }


def shadow_log_fields(result: Mapping[str, Any], *, account_id: str, account_username: str | None, run_id: str | None, package: str) -> dict[str, Any]:
    """Return the strict log allowlist; never include the source payload."""
    legacy = result.get("legacy") or {}
    canonical = result.get("canonical_business") or {}
    runtime = result.get("shadow_runtime") or {}
    comparison = result.get("comparison") or {}
    return {
        "account_id": str(account_id or ""),
        "account_username": str(account_username or "") or None,
        "run_id": str(run_id or "") or None,
        "package": str(package or ""),
        "legacy_day_cap": legacy.get("day_cap"),
        "legacy_session_cap": legacy.get("session_cap"),
        "legacy_run_cap": legacy.get("final_run_cap"),
        "canonical_business_day_cap": canonical.get("day_cap"),
        "canonical_business_session_cap": canonical.get("session_cap"),
        "shadow_runtime_day_cap": runtime.get("day_cap"),
        "shadow_runtime_session_cap": runtime.get("session_cap"),
        "shadow_runtime_run_cap": runtime.get("final_run_cap"),
        "classification": comparison.get("classification"),
        "day_delta": comparison.get("day_delta"),
        "session_delta": comparison.get("session_delta"),
        "run_delta": comparison.get("run_delta"),
        "legacy_limiting_source": legacy.get("limiting_source"),
        "legacy_limiting_reason": legacy.get("limiting_reason"),
        "canonical_limiting_source": canonical.get("limiting_source"),
        "canonical_limiting_reason": canonical.get("limiting_reason"),
        "shadow_limiting_source": runtime.get("limiting_source"),
        "shadow_limiting_reason": runtime.get("limiting_reason"),
        "flag_state": True,
    }
