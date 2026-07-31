from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


FOLLOW_PERSISTENCE_ACTION_NAMESPACE = uuid.UUID("20eac96d-8122-49ab-8e0c-d37de55e744e")
REQUIRED_INVARIANTS = {
    "account_request_run_consistent",
    "canonical_username_confirmed",
    "settings_locked_revision_match",
    "interaction_persisted",
    "audit_persisted",
    "counter_applied_or_not_applicable",
}


def rpc_v1_enabled() -> bool:
    return str(os.getenv("FOLLOW_PERSISTENCE_RPC_V1_ENABLED", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def canonical_username(value: str) -> str:
    return str(value or "").strip().lstrip("@").lower()


def deterministic_action_id(account_id: str, run_id: str, username: str) -> str:
    name = "|".join(
        (
            str(account_id or "").strip(),
            str(run_id or "").strip(),
            canonical_username(username),
            "follow_verified:v1",
        )
    )
    return str(uuid.uuid5(FOLLOW_PERSISTENCE_ACTION_NAMESPACE, name))


def action_id_hash(action_id: str) -> str:
    return hashlib.sha256(str(action_id or "").encode("utf-8")).hexdigest()[:16]


def parse_postgres_timestamp(value: Any) -> datetime | None:
    """Parse a timezone-aware Postgres timestamp without relaxing validity.

    Python 3.9's ``datetime.fromisoformat`` rejects some fractional widths that
    Postgres legitimately emits (notably five digits).  Normalize only that
    representation detail, while still requiring a real timezone-aware value.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    fractional = re.fullmatch(
        r"(?P<prefix>.*\.)(?P<fraction>\d+)(?P<offset>[+-]\d{2}:\d{2})",
        raw,
    )
    if fractional:
        digits = (fractional.group("fraction") + "000000")[:6]
        raw = f'{fractional.group("prefix")}{digits}{fractional.group("offset")}'
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def rpc_response_shape(value: Any) -> dict[str, Any]:
    """Return redacted field-level observability; never include raw values."""
    fields = (
        "ok",
        "status",
        "action_id",
        "interaction_id",
        "follow_persisted",
        "eligible_unfollow_at",
        "audit_persisted",
        "counter_applied",
        "settings_revision_match",
        "invariants_confirmed",
        "failure_reason",
    )
    if not isinstance(value, dict):
        return {
            "response_type": type(value).__name__,
            "field_present": {field: False for field in fields},
            "field_types": {},
            "eligible_unfollow_at_parse_status": "not_object",
        }
    raw_eligible = value.get("eligible_unfollow_at")
    return {
        "response_type": "dict",
        "field_present": {field: field in value for field in fields},
        "field_types": {
            field: type(value.get(field)).__name__ for field in fields if field in value
        },
        "eligible_unfollow_at_parse_status": (
            "valid" if parse_postgres_timestamp(raw_eligible) is not None else "invalid"
        ),
    }


def validate_rpc_response(
    value: Any, *, expected_action_id: str | None = None
) -> tuple[bool, str]:
    if not isinstance(value, dict):
        return False, "response_not_object"
    if value.get("ok") is not True:
        return False, "response_not_ok"
    if str(value.get("status") or "") not in {"created", "idempotent_replay"}:
        return False, "response_status_invalid"
    for field in (
        "follow_persisted",
        "audit_persisted",
        "counter_applied",
        "settings_revision_match",
    ):
        if value.get(field) is not True:
            return False, f"response_{field}_false"
    response_action_id = str(value.get("action_id") or "").strip()
    if not response_action_id:
        return False, "response_action_id_missing"
    if expected_action_id and response_action_id != str(expected_action_id):
        return False, "response_action_id_mismatch"
    if not str(value.get("interaction_id") or "").strip():
        return False, "response_interaction_id_missing"
    if value.get("failure_reason") is not None:
        return False, "response_failure_reason_present"
    if parse_postgres_timestamp(value.get("eligible_unfollow_at")) is None:
        return False, "response_eligible_unfollow_at_invalid"
    invariants = {str(item) for item in (value.get("invariants_confirmed") or [])}
    missing = REQUIRED_INVARIANTS - invariants
    if missing:
        return False, "response_invariants_missing:" + ",".join(sorted(missing))
    return True, "ok"


def validate_canonical_persistence_evidence(
    evidence: Any,
    *,
    expected_action_id: str,
    expected_account_id: str,
    expected_request_id: str,
    expected_run_id: str,
    expected_username: str,
    expected_settings_revision: str,
) -> tuple[bool, str, list[str], dict[str, Any] | None]:
    """Fail-closed reconciliation of a committed Follow from canonical rows."""
    mismatches: list[str] = []
    if not isinstance(evidence, dict):
        return False, "canonical_evidence_not_object", ["evidence"], None
    event = evidence.get("event")
    interaction = evidence.get("interaction")
    settings = evidence.get("unfollow_settings")
    if not isinstance(event, dict):
        mismatches.append("event_missing")
        event = {}
    if not isinstance(interaction, dict):
        mismatches.append("interaction_missing")
        interaction = {}
    if not isinstance(settings, dict):
        mismatches.append("unfollow_settings_missing")
        settings = {}

    expected_username_canonical = canonical_username(expected_username)
    event_payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    interaction_payload = (
        interaction.get("payload") if isinstance(interaction.get("payload"), dict) else {}
    )

    exact_checks = (
        ("event_action_id", str(event.get("id") or ""), str(expected_action_id)),
        (
            "interaction_action_id",
            str(interaction_payload.get("action_id") or ""),
            str(expected_action_id),
        ),
        ("event_account_id", str(event.get("account_id") or ""), str(expected_account_id)),
        (
            "interaction_account_id",
            str(interaction.get("account_id") or ""),
            str(expected_account_id),
        ),
        (
            "unfollow_settings_account_id",
            str(settings.get("account_id") or ""),
            str(expected_account_id),
        ),
        ("event_request_id", str(event.get("request_id") or ""), str(expected_request_id)),
        ("event_run_id", str(event.get("run_id") or ""), str(expected_run_id)),
        (
            "event_username",
            canonical_username(event.get("username")),
            expected_username_canonical,
        ),
        (
            "interaction_username",
            canonical_username(interaction.get("username")),
            expected_username_canonical,
        ),
        ("event_type", str(event.get("event_type") or ""), "follow_verified_persisted_v1"),
        ("event_status", str(event.get("event_status") or ""), "success"),
        ("event_interaction_type", str(event.get("interaction_type") or ""), "follow"),
        (
            "event_interaction_status",
            str(event.get("interaction_status") or ""),
            "success",
        ),
        (
            "row_interaction_status",
            str(interaction.get("interaction_status") or ""),
            "success",
        ),
        ("follow_status", str(interaction.get("follow_status") or ""), "following"),
        (
            "settings_revision",
            str(event_payload.get("settings_revision") or ""),
            str(expected_settings_revision),
        ),
    )
    for field, got, expected in exact_checks:
        if got != expected:
            mismatches.append(field)

    boolean_checks = (
        ("was_successful", interaction.get("was_successful")),
        ("followed_by_bot", interaction.get("followed_by_bot")),
        ("follow_persisted", event_payload.get("follow_persisted")),
        ("counter_applied", event_payload.get("counter_applied")),
        ("audit_persisted", event_payload.get("audit_persisted")),
        ("settings_revision_match", event_payload.get("settings_revision_match")),
    )
    for field, value in boolean_checks:
        if value is not True:
            mismatches.append(field)

    interaction_id = str(interaction.get("id") or "")
    if not interaction_id or str(event_payload.get("interaction_id") or "") != interaction_id:
        mismatches.append("interaction_id")
    invariants = {str(item) for item in (event_payload.get("invariants_confirmed") or [])}
    if REQUIRED_INVARIANTS - invariants:
        mismatches.append("invariants_confirmed")

    followed_at = parse_postgres_timestamp(interaction.get("followed_at"))
    eligible_at = parse_postgres_timestamp(interaction.get("eligible_unfollow_at"))
    payload_eligible_at = parse_postgres_timestamp(event_payload.get("eligible_unfollow_at"))
    if followed_at is None:
        mismatches.append("followed_at")
    if eligible_at is None:
        mismatches.append("eligible_unfollow_at")
    if payload_eligible_at is None or (
        eligible_at is not None and payload_eligible_at != eligible_at
    ):
        mismatches.append("payload_eligible_unfollow_at")

    try:
        unfollow_after_days = int(settings.get("unfollow_after_days"))
    except (TypeError, ValueError):
        unfollow_after_days = -1
    if unfollow_after_days < 0:
        mismatches.append("unfollow_after_days")
    settings_revision = parse_postgres_timestamp(settings.get("updated_at"))
    expected_revision = parse_postgres_timestamp(expected_settings_revision)
    if settings_revision is None or expected_revision is None or settings_revision != expected_revision:
        mismatches.append("unfollow_settings_revision")
    if (
        followed_at is not None
        and eligible_at is not None
        and unfollow_after_days >= 0
        and eligible_at != followed_at + timedelta(days=unfollow_after_days)
    ):
        mismatches.append("eligible_unfollow_contract")

    if mismatches:
        unique = sorted(set(mismatches))
        return False, "canonical_evidence_mismatch", unique, None
    reconciled = {
        "ok": True,
        "status": "idempotent_replay",
        "action_id": str(expected_action_id),
        "interaction_id": interaction_id,
        "follow_persisted": True,
        "eligible_unfollow_at": str(interaction.get("eligible_unfollow_at")),
        "audit_persisted": True,
        "counter_applied": True,
        "settings_revision_match": True,
        "invariants_confirmed": sorted(REQUIRED_INVARIANTS),
        "failure_reason": None,
    }
    valid, reason = validate_rpc_response(
        reconciled, expected_action_id=str(expected_action_id)
    )
    if not valid:
        return False, reason, ["reconciled_response"], None
    return True, "ok", [], reconciled
