"""Canonical provenance rules for adopting verification-code challenge screens."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from supabase_client import _request_json

ACTIVE_EMAIL_CODE_ACTION_STATUSES = frozenset(
    {"pending", "acknowledged", "pending_verification", "code_submitted", "open"}
)
PROVENANCE_KIND_ACTIVE_RUN = "active_run_post_submit"
EMAIL_CODE_CHALLENGE_TTL = timedelta(minutes=10)
SUPPORTED_VERIFICATION_CHANNELS = frozenset(
    {"email", "sms", "whatsapp", "authenticator_app"}
)


@dataclass(frozen=True)
class ChallengeProvenanceVerdict:
    accepted: bool
    reason: str
    proof_kind: str

    @classmethod
    def blocked(cls, reason: str) -> "ChallengeProvenanceVerdict":
        return cls(accepted=False, reason=reason, proof_kind="none")

    @classmethod
    def accepted_active_run(cls, reason: str) -> "ChallengeProvenanceVerdict":
        return cls(accepted=True, reason=reason, proof_kind="active_run_post_submit")

    @classmethod
    def accepted_historical(cls, reason: str) -> "ChallengeProvenanceVerdict":
        return cls(accepted=True, reason=reason, proof_kind="historical_strong")


def evaluate_pre_input_email_challenge(
    *,
    routing_signals: dict[str, Any],
    package_guard_mismatch: bool,
    account_id: str,
    run_id: str | None,
    expected_app_instance_id: str | None,
    assignment_id: str | None = None,
    credentials_version: int | None = None,
    assignment_updated_at: str | None = None,
    historical_action: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> ChallengeProvenanceVerdict:
    """Backward-compatible wrapper for the generic challenge provenance gate."""

    return evaluate_pre_input_verification_challenge(
        routing_signals=routing_signals,
        package_guard_mismatch=package_guard_mismatch,
        account_id=account_id,
        run_id=run_id,
        expected_app_instance_id=expected_app_instance_id,
        assignment_id=assignment_id,
        credentials_version=credentials_version,
        assignment_updated_at=assignment_updated_at,
        historical_action=historical_action,
        now=now,
    )


def evaluate_pre_input_verification_challenge(
    *,
    routing_signals: dict[str, Any],
    package_guard_mismatch: bool,
    account_id: str,
    run_id: str | None,
    expected_app_instance_id: str | None,
    assignment_id: str | None = None,
    credentials_version: int | None = None,
    assignment_updated_at: str | None = None,
    historical_action: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> ChallengeProvenanceVerdict:
    """Reject orphan pre-input challenges unless server-side strong provenance exists."""

    screen_type = str(routing_signals.get("screen_type") or "")
    channel = str(
        routing_signals.get("verification_channel")
        or routing_signals.get("challenge_type")
        or ""
    ).strip()
    if (
        routing_signals.get("verification_code_challenge_present") is not True
        and screen_type != "email_code_challenge"
    ):
        return ChallengeProvenanceVerdict.blocked("not_verification_code_challenge")
    if not channel and screen_type == "email_code_challenge":
        channel = "email"
    if channel not in SUPPORTED_VERIFICATION_CHANNELS:
        return ChallengeProvenanceVerdict.blocked("verification_channel_unsupported")
    if package_guard_mismatch:
        return ChallengeProvenanceVerdict.blocked("unexpected_foreground_package")
    if not str(account_id or "").strip():
        return ChallengeProvenanceVerdict.blocked("missing_account_id")

    # A challenge created during this run's credential submit is not handled here.
    if not historical_action:
        return ChallengeProvenanceVerdict.blocked("pre_input_challenge_orphan")

    return evaluate_historical_verification_challenge_provenance(
        action_row=historical_action,
        account_id=account_id,
        run_id=run_id,
        expected_app_instance_id=expected_app_instance_id,
        assignment_id=assignment_id,
        credentials_version=credentials_version,
        assignment_updated_at=assignment_updated_at,
        expected_channel=channel,
        now=now,
    )


def evaluate_historical_email_challenge_provenance(
    *,
    action_row: dict[str, Any] | None,
    account_id: str,
    run_id: str | None,
    expected_app_instance_id: str | None,
    assignment_id: str | None = None,
    credentials_version: int | None = None,
    assignment_updated_at: str | None = None,
    now: datetime | None = None,
) -> ChallengeProvenanceVerdict:
    """Backward-compatible wrapper for historical generic provenance."""

    return evaluate_historical_verification_challenge_provenance(
        action_row=action_row,
        account_id=account_id,
        run_id=run_id,
        expected_app_instance_id=expected_app_instance_id,
        assignment_id=assignment_id,
        credentials_version=credentials_version,
        assignment_updated_at=assignment_updated_at,
        expected_channel="email",
        now=now,
    )


def evaluate_historical_verification_challenge_provenance(
    *,
    action_row: dict[str, Any] | None,
    account_id: str,
    run_id: str | None,
    expected_app_instance_id: str | None,
    assignment_id: str | None = None,
    credentials_version: int | None = None,
    assignment_updated_at: str | None = None,
    expected_channel: str | None = None,
    now: datetime | None = None,
) -> ChallengeProvenanceVerdict:
    """Accept only when every strong provenance field is present and consistent."""

    current_time = now or datetime.now(timezone.utc)
    if not action_row:
        return ChallengeProvenanceVerdict.blocked("no_historical_challenge_record")

    action_type = str(action_row.get("action_type") or "").strip()
    status = str(action_row.get("status") or "").strip().lower()
    if action_type != "enter_email_verification_code":
        return ChallengeProvenanceVerdict.blocked("historical_action_type_mismatch")
    if status not in ACTIVE_EMAIL_CODE_ACTION_STATUSES:
        return ChallengeProvenanceVerdict.blocked("historical_action_not_active")

    row_account_id = str(action_row.get("account_id") or "").strip()
    if row_account_id and row_account_id != str(account_id or "").strip():
        return ChallengeProvenanceVerdict.blocked("historical_account_mismatch")

    metadata = _read_metadata(action_row)
    historical_channel = str(
        metadata.get("verification_channel") or metadata.get("challenge_type") or "email"
    ).strip()
    channel = str(expected_channel or "").strip()
    if channel and historical_channel != channel:
        return ChallengeProvenanceVerdict.blocked("historical_verification_channel_mismatch")
    if str(metadata.get("session_invalidated") or "").lower() in {"1", "true", "yes"}:
        return ChallengeProvenanceVerdict.blocked("historical_session_invalidated")

    required_fields = {
        "provenance_kind": PROVENANCE_KIND_ACTIVE_RUN,
        "stage": "post_submit",
        "run_id": str(metadata.get("run_id") or "").strip(),
        "expected_app_instance_id": str(metadata.get("expected_app_instance_id") or "").strip(),
        "assignment_id": str(metadata.get("assignment_id") or "").strip(),
    }
    missing = [key for key, value in required_fields.items() if not value]
    if missing:
        return ChallengeProvenanceVerdict.blocked("historical_provenance_partial")

    if str(metadata.get("provenance_kind") or "") != PROVENANCE_KIND_ACTIVE_RUN:
        return ChallengeProvenanceVerdict.blocked("historical_provenance_kind_mismatch")
    if str(metadata.get("stage") or "") != "post_submit":
        return ChallengeProvenanceVerdict.blocked("historical_stage_mismatch")

    current_app_instance_id = str(expected_app_instance_id or "").strip()
    if not current_app_instance_id or required_fields["expected_app_instance_id"] != current_app_instance_id:
        return ChallengeProvenanceVerdict.blocked("historical_app_instance_mismatch")

    current_assignment_id = str(assignment_id or "").strip()
    if not current_assignment_id or required_fields["assignment_id"] != current_assignment_id:
        return ChallengeProvenanceVerdict.blocked("historical_assignment_mismatch")

    action_credentials_version = metadata.get("credentials_version")
    if credentials_version is not None:
        if action_credentials_version is None:
            return ChallengeProvenanceVerdict.blocked("historical_provenance_partial")
        if int(action_credentials_version) != int(credentials_version):
            return ChallengeProvenanceVerdict.blocked("historical_credentials_version_mismatch")

    created_at = _parse_timestamp(action_row.get("created_at") or action_row.get("updated_at"))
    if created_at is None:
        return ChallengeProvenanceVerdict.blocked("historical_provenance_partial")
    if created_at + EMAIL_CODE_CHALLENGE_TTL < current_time:
        return ChallengeProvenanceVerdict.blocked("historical_challenge_expired")

    assignment_changed_at = _parse_timestamp(assignment_updated_at)
    if assignment_changed_at is not None and assignment_changed_at > created_at:
        return ChallengeProvenanceVerdict.blocked("assignment_changed_since_challenge")

    active_run_id = str(run_id or "").strip()
    historical_run_id = required_fields["run_id"]
    if active_run_id and historical_run_id != active_run_id:
        return ChallengeProvenanceVerdict.blocked("historical_run_mismatch")

    return ChallengeProvenanceVerdict.accepted_historical("historical_provenance_strong")


def load_active_email_challenge_action(account_id: str) -> Optional[dict[str, Any]]:
    aid = str(account_id or "").strip()
    if not aid:
        return None
    rows = _request_json(
        "GET",
        "account_dashboard_actions",
        query={
            "account_id": f"eq.{aid}",
            "action_type": "eq.enter_email_verification_code",
            "status": f"in.({','.join(sorted(ACTIVE_EMAIL_CODE_ACTION_STATUSES))})",
            "select": "id,account_id,action_type,status,created_at,updated_at,metadata",
            "order": "updated_at.desc",
            "limit": "1",
        },
    )
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    return row if isinstance(row, dict) else None


ChallengeProvenanceLoader = Callable[[str], Optional[dict[str, Any]]]


def default_challenge_provenance_loader(account_id: str) -> Optional[dict[str, Any]]:
    return load_active_email_challenge_action(account_id)


def _read_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
