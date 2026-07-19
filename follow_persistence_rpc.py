from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime
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
    raw_eligible = str(value.get("eligible_unfollow_at") or "").strip()
    try:
        datetime.fromisoformat(raw_eligible.replace("Z", "+00:00"))
    except Exception:
        return False, "response_eligible_unfollow_at_invalid"
    invariants = {str(item) for item in (value.get("invariants_confirmed") or [])}
    missing = REQUIRED_INVARIANTS - invariants
    if missing:
        return False, "response_invariants_missing:" + ",".join(sorted(missing))
    return True, "ok"
