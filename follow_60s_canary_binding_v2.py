"""Strict, account-neutral Follow 60 canary control binding.

The database control is the only business allowlist.  Environment variables may
disable the feature globally, but cannot select an account.  Invalid or partial
controls are deliberately treated as non-applicable so the caller can continue
through the normal Golden path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


BINDING_VERSION = "FOLLOW_60S_CANARY_BINDING_V2"
ONE_SHOT_CONTRACT_SCHEMA = "FOLLOW_60S_ONE_SHOT_V2"
ALLOWED_RUN_TYPES = frozenset({"account_session"})
ARMED_STATUSES = frozenset({"armed"})
BOUND_STATUSES = frozenset(
    {"armed", "running", "barrier_waiting_stop", "continuation_authorized"}
)
CONSUMER_BOUND_STATUSES = frozenset(
    {*BOUND_STATUSES, "waiting_operator_evaluation", "completed", "canceled"}
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_username(value: Any) -> str:
    return _text(value).lstrip("@").lower()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _timestamp(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _metadata(control: dict[str, Any]) -> dict[str, Any]:
    value = control.get("metadata_safe")
    return dict(value) if isinstance(value, dict) else {}


def _baseline(control: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    value = metadata.get("baseline")
    if isinstance(value, dict):
        return dict(value)
    value = control.get("baseline")
    return dict(value) if isinstance(value, dict) else {}


@dataclass(frozen=True)
class Follow60CanaryBindingV2:
    control_id: str
    account_id: str
    expected_username: str
    expected_worker_sha: str
    baseline_release_sha: str
    baseline_account_id: str
    baseline_captured_at: str
    baseline_timezone: str
    baseline_package: str
    baseline_warmup_ready: bool
    baseline_follow_count: int
    max_new_cycles: int
    current_new_cycle_count: int
    control_status: str
    armed_at: str
    expires_at: str
    business_session_id: str
    expected_package: str
    expected_run_type: str
    binding_version: str
    idempotency_key: str
    created_by: str
    source: str
    revoked_at: str
    completed_at: str
    active_control_count: int
    run_id: str
    request_id: str
    attempt_id: int
    binding_valid: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BindingVerdict:
    valid: bool
    reason: str
    binding: Follow60CanaryBindingV2 | None = None


def parse_control(control: dict[str, Any] | None) -> Follow60CanaryBindingV2:
    row = dict(control or {})
    metadata = _metadata(row)
    baseline = _baseline(row, metadata)
    return Follow60CanaryBindingV2(
        control_id=_text(row.get("control_id") or metadata.get("control_id")),
        account_id=_text(row.get("account_id")),
        expected_username=_normalized_username(
            row.get("expected_username") or metadata.get("expected_username")
        ),
        expected_worker_sha=_text(
            row.get("expected_worker_sha") or metadata.get("expected_worker_sha")
        ).lower(),
        baseline_release_sha=_text(
            row.get("baseline_release_sha")
            or metadata.get("baseline_release_sha")
            or baseline.get("worker_sha")
        ).lower(),
        baseline_account_id=_text(
            row.get("baseline_account_id")
            or metadata.get("baseline_account_id")
            or baseline.get("account_id")
        ),
        baseline_captured_at=_text(
            row.get("baseline_captured_at")
            or metadata.get("baseline_captured_at")
            or baseline.get("captured_at")
        ),
        baseline_timezone=_text(
            row.get("baseline_timezone")
            or metadata.get("baseline_timezone")
            or baseline.get("timezone")
        ),
        baseline_package=_text(
            row.get("baseline_package")
            or metadata.get("baseline_package")
            or baseline.get("package")
        ),
        baseline_warmup_ready=(
            row.get("baseline_warmup_ready") is True
            or metadata.get("baseline_warmup_ready") is True
            or baseline.get("warmup_ready") is True
        ),
        baseline_follow_count=_int(row.get("baseline_follow_count"), -1),
        max_new_cycles=_int(
            row.get("max_new_cycles")
            or metadata.get("max_new_cycles")
            or row.get("evaluation_increment"),
            0,
        ),
        current_new_cycle_count=_int(
            row.get("current_new_cycle_count")
            or metadata.get("current_new_cycle_count"),
            0,
        ),
        control_status=_text(row.get("status") or row.get("control_status")),
        armed_at=_text(row.get("armed_at") or metadata.get("armed_at")),
        expires_at=_text(row.get("expires_at") or metadata.get("expires_at")),
        business_session_id=_text(
            row.get("business_session_id") or metadata.get("business_session_id")
        ),
        expected_package=_text(
            row.get("expected_package") or metadata.get("expected_package")
        ),
        expected_run_type=_text(
            row.get("expected_run_type")
            or metadata.get("expected_run_type")
            or "account_session"
        ),
        binding_version=_text(
            row.get("binding_version")
            or metadata.get("binding_version")
            or metadata.get("runtime_binding_schema")
        ),
        idempotency_key=_text(
            row.get("idempotency_key") or metadata.get("idempotency_key")
        ),
        created_by=_text(row.get("created_by") or metadata.get("created_by")),
        source=_text(row.get("source") or metadata.get("source")),
        revoked_at=_text(row.get("revoked_at") or metadata.get("revoked_at")),
        completed_at=_text(row.get("completed_at") or metadata.get("completed_at")),
        active_control_count=_int(
            row.get("active_control_count")
            or metadata.get("active_control_count"),
            0,
        ),
        run_id=_text(row.get("run_id")),
        request_id=_text(row.get("request_id")),
        attempt_id=_int(row.get("attempt_id") or metadata.get("attempt_id"), 0),
        binding_valid=row.get("binding_valid") is True,
    )


def validate_armed_control(
    control: dict[str, Any] | None,
    *,
    account_id: str,
    account_username: str,
    active_worker_sha: str,
    run_type: str,
    package: str = "",
    now: datetime | None = None,
    allowed_statuses: frozenset[str] = ARMED_STATUSES,
) -> BindingVerdict:
    if not control:
        return BindingVerdict(False, "control_absent")
    binding = parse_control(control)
    required = {
        "control_id": binding.control_id,
        "account_id": binding.account_id,
        "expected_worker_sha": binding.expected_worker_sha,
        "baseline_release_sha": binding.baseline_release_sha,
        "baseline_account_id": binding.baseline_account_id,
        "baseline_captured_at": binding.baseline_captured_at,
        "baseline_timezone": binding.baseline_timezone,
        "baseline_package": binding.baseline_package,
        "armed_at": binding.armed_at,
        "expires_at": binding.expires_at,
        "expected_package": binding.expected_package,
        "expected_run_type": binding.expected_run_type,
        "binding_version": binding.binding_version,
        "idempotency_key": binding.idempotency_key,
        "created_by": binding.created_by,
        "source": binding.source,
    }
    missing = next((name for name, value in required.items() if not value), "")
    if missing:
        return BindingVerdict(False, f"control_incomplete_{missing}", binding)
    if binding.control_status not in allowed_statuses:
        return BindingVerdict(False, f"control_status_{binding.control_status or 'missing'}", binding)
    if binding.binding_version != BINDING_VERSION:
        return BindingVerdict(False, "binding_version_mismatch", binding)
    if binding.account_id != _text(account_id):
        return BindingVerdict(False, "control_account_mismatch", binding)
    if binding.baseline_account_id != binding.account_id:
        return BindingVerdict(False, "baseline_account_mismatch", binding)
    baseline_captured_at = _timestamp(binding.baseline_captured_at)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if baseline_captured_at is None or baseline_captured_at > current:
        return BindingVerdict(False, "baseline_captured_at_invalid", binding)
    if not binding.baseline_timezone:
        return BindingVerdict(False, "baseline_timezone_missing", binding)
    if binding.baseline_package != _text(package):
        return BindingVerdict(False, "baseline_package_mismatch", binding)
    if not binding.baseline_warmup_ready:
        return BindingVerdict(False, "baseline_warmup_not_ready", binding)
    if binding.expected_username and binding.expected_username != _normalized_username(account_username):
        return BindingVerdict(False, "expected_username_mismatch", binding)
    worker_sha = _text(active_worker_sha).lower()
    if not worker_sha or binding.expected_worker_sha != worker_sha:
        return BindingVerdict(False, "worker_sha_mismatch", binding)
    if binding.baseline_release_sha != binding.expected_worker_sha:
        return BindingVerdict(False, "baseline_release_sha_mismatch", binding)
    if binding.expected_run_type not in ALLOWED_RUN_TYPES or binding.expected_run_type != _text(run_type):
        return BindingVerdict(False, "run_type_mismatch", binding)
    if binding.expected_package and binding.expected_package != _text(package):
        return BindingVerdict(False, "expected_package_mismatch", binding)
    if binding.baseline_follow_count < 0:
        return BindingVerdict(False, "baseline_follow_count_invalid", binding)
    if binding.max_new_cycles <= 0 or binding.max_new_cycles > 50:
        return BindingVerdict(False, "max_new_cycles_invalid", binding)
    if binding.current_new_cycle_count < 0 or binding.current_new_cycle_count >= binding.max_new_cycles:
        return BindingVerdict(False, "cycle_counter_exhausted", binding)
    if binding.active_control_count != 1:
        return BindingVerdict(False, "active_control_collision", binding)
    if binding.revoked_at:
        return BindingVerdict(False, "control_revoked", binding)
    if binding.completed_at:
        return BindingVerdict(False, "control_completed", binding)
    armed_at = _timestamp(binding.armed_at)
    expires_at = _timestamp(binding.expires_at)
    if armed_at is None or armed_at > current:
        return BindingVerdict(False, "armed_at_invalid", binding)
    if expires_at is None or expires_at <= current:
        return BindingVerdict(False, "control_expired", binding)
    return BindingVerdict(True, "armed_control_valid", binding)


def validate_runtime_binding(
    control: dict[str, Any] | None,
    *,
    account_id: str,
    account_username: str,
    active_worker_sha: str,
    run_type: str,
    package: str,
    run_id: str,
    request_id: str,
    attempt_id: int,
    business_session_id: str,
    now: datetime | None = None,
) -> BindingVerdict:
    verdict = validate_armed_control(
        control,
        account_id=account_id,
        account_username=account_username,
        active_worker_sha=active_worker_sha,
        run_type=run_type,
        package=package,
        now=now,
        allowed_statuses=BOUND_STATUSES,
    )
    binding = verdict.binding
    if not verdict.valid or binding is None:
        return verdict
    if not binding.binding_valid:
        return BindingVerdict(False, "runtime_binding_not_valid", binding)
    if binding.control_status not in BOUND_STATUSES:
        return BindingVerdict(False, "runtime_control_status_invalid", binding)
    if binding.run_id != _text(run_id):
        return BindingVerdict(False, "run_binding_mismatch", binding)
    if binding.request_id != _text(request_id):
        return BindingVerdict(False, "request_binding_mismatch", binding)
    if binding.attempt_id != int(attempt_id or 0):
        return BindingVerdict(False, "attempt_binding_mismatch", binding)
    if binding.business_session_id != _text(business_session_id):
        return BindingVerdict(False, "business_session_binding_mismatch", binding)
    return BindingVerdict(True, "runtime_binding_valid", binding)


def validate_consumer_binding(
    control: dict[str, Any] | None,
    *,
    account_id: str,
    active_worker_sha: str,
    run_id: str,
    request_id: str,
    now: datetime | None = None,
) -> BindingVerdict:
    """Validate the already-bound identity used by dispatcher terminalization."""
    if not control:
        return BindingVerdict(False, "control_absent")
    binding = parse_control(control)
    required = (
        binding.control_id,
        binding.account_id,
        binding.expected_worker_sha,
        binding.baseline_release_sha,
        binding.baseline_account_id,
        binding.baseline_captured_at,
        binding.baseline_timezone,
        binding.baseline_package,
        binding.binding_version,
        binding.idempotency_key,
        binding.source,
        binding.run_id,
        binding.request_id,
        binding.business_session_id,
    )
    if not all(required):
        return BindingVerdict(False, "consumer_binding_incomplete", binding)
    if binding.binding_version != BINDING_VERSION:
        return BindingVerdict(False, "binding_version_mismatch", binding)
    if binding.control_status not in CONSUMER_BOUND_STATUSES:
        return BindingVerdict(False, "runtime_control_status_invalid", binding)
    if binding.account_id != _text(account_id):
        return BindingVerdict(False, "control_account_mismatch", binding)
    if binding.baseline_account_id != binding.account_id:
        return BindingVerdict(False, "baseline_account_mismatch", binding)
    if not binding.baseline_warmup_ready:
        return BindingVerdict(False, "baseline_warmup_not_ready", binding)
    worker_sha = _text(active_worker_sha).lower()
    if not worker_sha or binding.expected_worker_sha != worker_sha:
        return BindingVerdict(False, "worker_sha_mismatch", binding)
    if binding.baseline_release_sha != binding.expected_worker_sha:
        return BindingVerdict(False, "baseline_release_sha_mismatch", binding)
    if binding.active_control_count != 1:
        return BindingVerdict(False, "active_control_collision", binding)
    if binding.revoked_at or binding.completed_at:
        return BindingVerdict(False, "control_not_active", binding)
    expires_at = _timestamp(binding.expires_at)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expires_at is None or expires_at <= current:
        return BindingVerdict(False, "control_expired", binding)
    if not binding.binding_valid:
        return BindingVerdict(False, "runtime_binding_not_valid", binding)
    if binding.run_id != _text(run_id):
        return BindingVerdict(False, "run_binding_mismatch", binding)
    if binding.request_id != _text(request_id):
        return BindingVerdict(False, "request_binding_mismatch", binding)
    return BindingVerdict(True, "consumer_binding_valid", binding)
