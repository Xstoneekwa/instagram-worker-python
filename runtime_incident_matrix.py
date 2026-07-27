"""Canonical runtime incident matrix (P2).

Maps a *structured* terminal run failure (ig_runs.performance_summary written
by the runner + dispatcher exit context) to a canonical incident decision:

    true reason -> incident_type / reason_code / severity / operator copy

Priority contract (never invert):
  1. a concrete, stable, known cause (identity, package, login, device);
  2. a structured runtime error transmitted by the worker;
  3. ``worker_exit_nonzero`` strictly as the last fallback.

Normal scheduler gates and voluntary stops must never become incidents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from auto_login_failure_contract import (
    AUTO_LOGIN_REASON_PHASES,
    normalize_auto_login_failure,
)

# Reasons that describe normal control-flow gates, not runtime failures.
# They must never produce an incident nor a Slack/Discord notification.
NON_INCIDENT_REASONS = frozenset(
    {
        "scheduler_disabled",
        "resume_plan_missing",
        "manual_only_requires_manual_trigger",
        "assignment_window_inactive",
        "schedule_window_closed",
        "schedule_window_not_open",
        "no_eligible_targets",
        "no_pending_targets",
        "account_daily_cap_reached",
        "package_follow_day_cap_reached",
        "daily_cap_reached",
        "manual_run_canceled",
        "manual_run_canceled_after_run_started",
        "manual_stop",
        "auto_restart_enqueue_deduplicated",
    }
)

# Identity guard failure reasons that mean "identity could not be proven".
IDENTITY_NOT_PROVEN_REASONS = frozenset(
    {
        "actual_logged_in_username_not_detected",
        "own_profile_open_failed",
        "expected_account_username_missing",
    }
)

# Reasons proving the assigned package/clone could not be used at all.
PACKAGE_UNAVAILABLE_REASONS = frozenset(
    {
        "instagram_not_foreground",
        "instagram_version_lock_unsafe",
        "assignment_type_incompatible",
        "assignment_device_missing_adb_serial",
        "app_start_failed",
        "wrong_package_foreground",
    }
)

# Reasons proving Instagram asked for a login / challenge mid-flow.
LOGIN_REQUIRED_REASONS = frozenset(
    {
        "login_required",
        "logged_out_detected",
        "login_challenge_detected",
        "challenge_required",
        "unexpected_login_surface",
    }
)

# Reasons proving the device disappeared while a run was active.
DEVICE_UNAVAILABLE_REASONS = frozenset(
    {
        "device_connect_failed",
        "device_disconnected",
        "device_offline",
        "adb_unavailable",
        "device_health_check_failed",
    }
)

WELCOME_SURFACE_FAILURE_REASONS = frozenset(
    {
        "welcome_surface_unstable",
        "followers_surface_missing_at_start",
        "recovered_snapshot_rejected",
        "followers_suggestions_boundary_revalidation_failed",
        "followers_suggestions_boundary_recovery_exhausted",
    }
)

MYTHYL_OPERATOR_MESSAGE = (
    "The active Instagram account could not be confirmed. "
    "Human review is required before resuming."
)

AUTO_LOGIN_RUN_TYPES = frozenset({"login_provisioning", "login_email_code_resume"})
RECOVERABLE_PYTHON_FAILURE_CATEGORY = "recoverable_python_runtime_failure"
RECOVERABLE_PYTHON_FAILURE_SIGNATURE = "python:unfollow:duplicate_stop_reason"
RECOVERABLE_PYTHON_ROOT_FAILURE_CODE = "unfollow_runtime_exception"
AUTO_RESTART_MAX_RETRIES_AFTER_INITIAL_FAILURE = 2


@dataclass(frozen=True)
class RecoverablePythonRetryDecision:
    applies: bool
    restart_allowed: bool = False
    retries_exhausted: bool = False
    notify_informational: bool = False
    event_type: str = ""
    block_reason: str = ""
    business_session_id: str = ""
    attempt_id: int = 1
    retry_index: int = 0
    next_attempt_id: int | None = None
    next_retry_index: int | None = None
    previous_run_id: str = ""
    root_failure_code: str = ""
    failure_signature: str = ""
    failure_category: str = ""


def _explicit_unsafe_markers(summary: dict[str, Any]) -> list[str]:
    raw = summary.get("unsafe_markers")
    markers = (
        [str(item).strip().lower() for item in raw if str(item).strip()]
        if isinstance(raw, list)
        else []
    )
    reason_fields = {
        str(summary.get("reason") or "").strip().lower(),
        str(summary.get("account_identity_failure_reason") or "").strip().lower(),
    }
    explicit = {
        "challenge",
        "checkpoint",
        "restriction",
        "action_block",
        "active_instagram_account_mismatch",
        "account_mismatch",
        "device_offline",
        "cleanup_uncertain",
        "critical_action_result_unknown",
        "device_lock_incoherent",
    }
    for value in reason_fields:
        if value in explicit:
            markers.append(value)
    return sorted(set(markers))


def classify_recoverable_python_retry(
    *,
    performance_summary: dict[str, Any] | None,
    request_metadata: dict[str, Any] | None = None,
    run_id: str | None = None,
    cleanup_completed: bool | None,
    lock_released: bool | None,
) -> RecoverablePythonRetryDecision:
    """Classify the locked two-retry policy without scheduling anything.

    Generic text such as ``error`` or ``failed`` is deliberately ignored as a
    safety marker. Only structured unsafe markers can escape the silent path.
    """
    summary = dict(performance_summary or {})
    metadata = dict(request_metadata or {})
    category = str(summary.get("failure_category") or "").strip()
    root_code = str(summary.get("root_failure_code") or "").strip()
    signature = str(summary.get("failure_signature") or "").strip()
    applies = (
        category == RECOVERABLE_PYTHON_FAILURE_CATEGORY
        and root_code == RECOVERABLE_PYTHON_ROOT_FAILURE_CODE
        and signature == RECOVERABLE_PYTHON_FAILURE_SIGNATURE
        and str(summary.get("session_termination_class") or "").strip()
        == "partial_resumable"
    )
    if not applies:
        return RecoverablePythonRetryDecision(applies=False)

    try:
        attempt_id = int(metadata.get("attempt_id") or summary.get("attempt_id") or 1)
    except (TypeError, ValueError):
        attempt_id = 1
    try:
        retry_index = int(
            metadata.get("retry_index")
            if metadata.get("retry_index") is not None
            else summary.get("retry_index", max(0, attempt_id - 1))
        )
    except (TypeError, ValueError):
        retry_index = max(0, attempt_id - 1)
    business_session_id = str(
        metadata.get("business_session_id")
        or summary.get("business_session_id")
        or run_id
        or ""
    ).strip()
    previous_run_id = str(
        metadata.get("previous_run_id")
        or metadata.get("prior_run_id")
        or summary.get("previous_run_id")
        or ""
    ).strip()
    unsafe = _explicit_unsafe_markers(summary)
    if unsafe:
        return RecoverablePythonRetryDecision(
            applies=True,
            block_reason="unsafe_markers:" + ",".join(unsafe),
            business_session_id=business_session_id,
            attempt_id=attempt_id,
            retry_index=retry_index,
            previous_run_id=previous_run_id,
            root_failure_code=root_code,
            failure_signature=signature,
            failure_category=category,
        )
    if cleanup_completed is not True:
        return RecoverablePythonRetryDecision(
            applies=True,
            block_reason="cleanup_uncertain",
            business_session_id=business_session_id,
            attempt_id=attempt_id,
            retry_index=retry_index,
            previous_run_id=previous_run_id,
            root_failure_code=root_code,
            failure_signature=signature,
            failure_category=category,
        )
    if lock_released is not True:
        return RecoverablePythonRetryDecision(
            applies=True,
            block_reason="device_lock_incoherent",
            business_session_id=business_session_id,
            attempt_id=attempt_id,
            retry_index=retry_index,
            previous_run_id=previous_run_id,
            root_failure_code=root_code,
            failure_signature=signature,
            failure_category=category,
        )
    if not business_session_id or attempt_id != retry_index + 1 or retry_index < 0:
        return RecoverablePythonRetryDecision(
            applies=True,
            block_reason="retry_identity_invalid",
            business_session_id=business_session_id,
            attempt_id=attempt_id,
            retry_index=retry_index,
            previous_run_id=previous_run_id,
            root_failure_code=root_code,
            failure_signature=signature,
            failure_category=category,
        )

    exhausted = retry_index >= AUTO_RESTART_MAX_RETRIES_AFTER_INITIAL_FAILURE
    next_retry_index = None if exhausted else retry_index + 1
    next_attempt_id = None if exhausted else attempt_id + 1
    event_type = (
        "recoverable_python_bug_retries_exhausted"
        if exhausted
        else f"recoverable_python_failure_restart_{next_retry_index}_scheduled"
    )
    return RecoverablePythonRetryDecision(
        applies=True,
        restart_allowed=not exhausted,
        retries_exhausted=exhausted,
        notify_informational=exhausted,
        event_type=event_type,
        block_reason="auto_restart_retries_exhausted" if exhausted else "",
        business_session_id=business_session_id,
        attempt_id=attempt_id,
        retry_index=retry_index,
        next_attempt_id=next_attempt_id,
        next_retry_index=next_retry_index,
        previous_run_id=previous_run_id,
        root_failure_code=root_code,
        failure_signature=signature,
        failure_category=category,
    )

def _auto_login_phase(reason: str, summary: dict[str, Any]) -> str:
    explicit = str(summary.get("phase") or "").strip().lower()
    if explicit:
        return explicit
    return AUTO_LOGIN_REASON_PHASES.get(reason, "identity_verification")


def _classify_auto_login_failure(
    *,
    reason: str,
    exit_code: int,
    run_type: str,
    summary: dict[str, Any],
    metadata_safe: dict[str, Any],
) -> IncidentDecision:
    contract = normalize_auto_login_failure(
        reason,
        phase=_auto_login_phase(reason, summary),
        correction_deployed=True,
    )
    code = contract.persisted_error_code
    phase = contract.phase
    challenge = phase in {"email_challenge", "email_code_resume"}
    identity = phase == "identity_verification"
    device = phase == "device_lock" or code in DEVICE_UNAVAILABLE_REASONS
    expired = code == "request_expired"
    incident_type = (
        "auto_login_verification_required"
        if challenge
        else "auto_login_identity_mismatch"
        if identity
        else "auto_login_device_unavailable"
        if device
        else "auto_login_request_expired"
        if expired
        else "auto_login_failed"
    )
    operator_label = (
        "Auto Login verification required"
        if challenge
        else "Auto Login identity mismatch"
        if identity
        else "Auto Login device unavailable"
        if device
        else "Auto Login request expired"
        if expired
        else "Auto Login failed"
    )
    retryable = contract.retryable
    action = (
        "Enter the Instagram verification code, then resume Auto Login once."
        if challenge
        else "Verify the expected Instagram identity before retrying Auto Login."
        if identity
        else "Verify the assigned phone and device lock before retrying Auto Login."
        if device
        else "Review the redacted Auto Login runtime logs for this request."
        if code == "unclassified_auto_login_failure"
        else contract.recommended_action
    )
    client_message = (
        "Un code de vérification Instagram est nécessaire pour terminer la connexion."
        if challenge
        else "La connexion Instagram n’a pas pu être finalisée. Notre équipe technique a été informée."
    )
    metadata_safe.update(
        {
            "domain": "auto_login",
            "run_type": run_type,
            "phase": phase,
            "reason_code": code,
            "retryable": retryable,
            "operator_action_required": True,
            "client_safe_message": client_message,
            "operator_message": contract.operator_message,
        }
    )
    warning_codes = {
        "active_request_exists",
        "device_busy",
        "device_lock_held",
        "request_expired",
        "verification_code_expired",
    }
    return IncidentDecision(
        should_publish=True,
        incident_type=incident_type,
        reason_code=code,
        severity="warning" if challenge or code in warning_codes else "error",
        operator_label=operator_label,
        action_required=action,
        requires_operator_review=True,
        blocking_campaign=True,
        admin_message=contract.operator_message,
        notify_channels=True,
        metadata_safe=metadata_safe,
    )


@dataclass(frozen=True)
class IncidentDecision:
    """Outcome of classifying one terminal run failure."""

    should_publish: bool
    incident_type: str = ""
    reason_code: str = ""
    severity: str = "error"
    operator_label: str = ""
    action_required: str = ""
    requires_operator_review: bool = False
    blocking_campaign: bool = False
    admin_message: str = ""
    notify_channels: bool = False
    skip_reason: str = ""
    metadata_safe: dict[str, Any] = field(default_factory=dict)

    def to_log_fields(self) -> dict[str, Any]:
        return {
            "should_publish": self.should_publish,
            "incident_type": self.incident_type or None,
            "reason_code": self.reason_code or None,
            "severity": self.severity or None,
            "skip_reason": self.skip_reason or None,
            "requires_operator_review": self.requires_operator_review,
            "blocking_campaign": self.blocking_campaign,
        }


def build_incident_dedupe_key(
    *,
    account_id: str | None,
    run_ref: str | None,
    incident_type: str,
    phase: str | None = None,
    reason_code: str | None = None,
) -> str:
    """Canonical dedupe key: one incident per (account, run, incident_type).

    Repetitions of the same run + same incident_type enrich the existing
    incident (RPC occurrence_count). A different incident_type for the same
    run creates a distinct incident. Cross-run repetitions create per-run
    incidents on purpose: each failed run is an operator-actionable event.
    """
    aid = str(account_id or "").strip() or "unknown"
    ref = str(run_ref or "").strip() or "unknown"
    suffix = incident_type
    if phase or reason_code:
        suffix = f"{incident_type}:{str(phase or 'unknown')}:{str(reason_code or 'unknown')}"
    return f"account:{aid}:run:{ref}:{suffix}"


_SAFE_METADATA_KEYS = (
    "domain",
    "run_type",
    "phase",
    "reason_code",
    "reason",
    "failure_reason",
    "retryable",
    "operator_action_required",
    "client_safe_message",
    "operator_message",
    "request_id",
    "run_id",
    "account_id",
    "device_id",
    "app_instance_id",
    "timestamp",
    "account_identity_failure_reason",
    "account_identity_verification_method",
    "expected_account_username",
    "actual_logged_in_username",
    "welcome_scan_stop_reason",
    "welcome_scan_jobs_enqueued_count",
    "welcome_sender_failure_reason",
    "welcome_entry_surface_decision",
    "root_failure_code",
    "failure_phase",
    "failure_module",
    "failure_function",
    "specific_failure_reason",
    "session_termination_class",
    "restart_allowed",
    "restart_block_reason",
    "failure_category",
    "failure_signature",
    "business_session_id",
    "attempt_id",
    "retry_index",
    "incident_dedupe_key",
    "incident_type",
    "language",
    "confidence",
    "physical_preflight_required",
)


def _safe_summary_metadata(summary: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _SAFE_METADATA_KEYS:
        value = summary.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if key == "specific_failure_reason":
            text = re.sub(r"<[^>]*>", "[redacted]", text)
            text = re.sub(
                r"(?i)\b(password|token|secret|authorization)\b\s*[:=]\s*\S+",
                r"\1=[redacted]",
                text,
            )
            text = re.sub(r"/(?:Users|private|var|tmp)/\S+", "[path]", text)
        if text:
            out[key] = text[:200]
    return out


def _skip(reason: str) -> IncidentDecision:
    return IncidentDecision(should_publish=False, skip_reason=reason)


def classify_terminal_run_failure(
    *,
    exit_code: int,
    timed_out: bool = False,
    run_status: str | None = None,
    canceled: bool = False,
    performance_summary: dict[str, Any] | None = None,
    run_type: str | None = None,
) -> IncidentDecision:
    """Classify one terminal run outcome into a canonical incident decision.

    ``performance_summary`` is the structured payload the runner persisted on
    the linked ``ig_runs`` row (never raw text logs).
    """
    summary = dict(performance_summary or {})
    status = str(run_status or "").strip().lower()
    normalized_run_type = str(run_type or summary.get("run_type") or "").strip().lower()
    root_failure_code = str(summary.get("root_failure_code") or "").strip()
    reason = str(
        root_failure_code
        or summary.get("reason_code")
        or summary.get("failure_reason")
        or summary.get("reason")
        or ""
    ).strip()
    phase_terminal_contract = summary.get("phase_terminal_contract")
    if (
        not reason
        and isinstance(phase_terminal_contract, dict)
        and phase_terminal_contract.get("ok") is False
    ):
        reason = str(
            phase_terminal_contract.get("reason") or "account_session_phase_not_terminal"
        ).strip()
        summary["reason"] = reason
    identity_reason = str(summary.get("account_identity_failure_reason") or "").strip()

    if exit_code == 0 and not timed_out:
        return _skip("exit_zero")
    if canceled or status == "canceled":
        return _skip("manual_cancel")
    if status == "stopped":
        return _skip("manual_stop")
    if exit_code == 143 and not timed_out:
        # SIGTERM outside dispatcher timeout: voluntary stop path.
        return _skip("sigterm_manual_stop")
    if reason in NON_INCIDENT_REASONS:
        return _skip(f"non_incident_reason:{reason}")

    metadata_safe = _safe_summary_metadata(summary)
    metadata_safe["exit_code"] = int(exit_code)
    if timed_out:
        metadata_safe["timed_out"] = True

    if normalized_run_type in AUTO_LOGIN_RUN_TYPES:
        if timed_out and not reason:
            reason = "dispatcher_claim_timeout"
        return _classify_auto_login_failure(
            reason=reason,
            exit_code=exit_code,
            run_type=normalized_run_type,
            summary=summary,
            metadata_safe=metadata_safe,
        )

    if reason == "instagram_action_rate_limit":
        return IncidentDecision(
            should_publish=True,
            incident_type="instagram_account_restriction",
            reason_code="instagram_action_rate_limit",
            severity="error",
            operator_label="Instagram action restriction detected",
            action_required=(
                "Open the account manually, verify whether Instagram still shows the action "
                "restriction, wait if necessary, then resolve the incident only after confirming "
                "the restriction is cleared."
            ),
            requires_operator_review=True,
            blocking_campaign=True,
            admin_message=(
                "Instagram is temporarily limiting actions. The campaign is paused and Auto Restart "
                "must remain blocked until human review and a physical preflight pass."
            ),
            notify_channels=True,
            metadata_safe=metadata_safe,
        )

    # 1) Identity guard outcomes (concrete, stable causes).
    if reason == "active_instagram_account_mismatch" or identity_reason:
        if identity_reason in IDENTITY_NOT_PROVEN_REASONS or (
            not identity_reason and reason in IDENTITY_NOT_PROVEN_REASONS
        ):
            code = identity_reason or reason
            return IncidentDecision(
                should_publish=True,
                incident_type="run_identity_verification_failed",
                reason_code=code,
                severity="critical",
                operator_label="Instagram identity could not be verified",
                action_required=MYTHYL_OPERATOR_MESSAGE,
                requires_operator_review=True,
                blocking_campaign=True,
                admin_message=(
                    "Identity preflight could not verify the active username "
                    f"({code}). The run stopped before any business action."
                ),
                notify_channels=True,
                metadata_safe=metadata_safe,
            )
        return IncidentDecision(
            should_publish=True,
            incident_type="active_instagram_account_mismatch",
            reason_code="active_instagram_account_mismatch",
            severity="critical",
            operator_label="Wrong active Instagram account",
            action_required=(
                "A different Instagram account is signed in on the assigned clone. "
                "Verify the active account before resuming."
            ),
            requires_operator_review=True,
            blocking_campaign=True,
            admin_message=(
                "Identity preflight detected a username different from the expected "
                "account. The run stopped before any business action."
            ),
            notify_channels=True,
            metadata_safe=metadata_safe,
        )

    if reason in WELCOME_SURFACE_FAILURE_REASONS:
        return IncidentDecision(
            should_publish=True,
            incident_type="welcome_surface_unstable",
            reason_code=reason,
            severity="critical",
            operator_label="Welcome followers surface unstable",
            action_required="Review the Welcome followers-surface evidence before the next launch.",
            requires_operator_review=True,
            blocking_campaign=True,
            admin_message=(
                "Welcome stopped safely because the followers surface proof became unstable "
                f"({reason}). Review the run evidence before the next launch."
            ),
            notify_channels=True,
            metadata_safe=metadata_safe,
        )

    # 2) Assigned package / clone unusable.
    if reason in PACKAGE_UNAVAILABLE_REASONS or exit_code in {13, 43}:
        code = reason or ("instagram_version_lock_unsafe" if exit_code == 43 else "assigned_instagram_package_unavailable")
        return IncidentDecision(
            should_publish=True,
            incident_type="assigned_instagram_package_unavailable",
            reason_code=code,
            severity="critical" if exit_code == 43 else "error",
            operator_label="Assigned Instagram package or clone unavailable",
            action_required=(
                "The assigned Instagram clone could not be used. "
                "Verify the device and app instance before resuming."
            ),
            requires_operator_review=True,
            blocking_campaign=True,
            admin_message=f"Assigned package unusable ({code}), exit code {exit_code}.",
            notify_channels=True,
            metadata_safe=metadata_safe,
        )

    # 3) Login / challenge surfaces.
    if reason in LOGIN_REQUIRED_REASONS:
        return IncidentDecision(
            should_publish=True,
            incident_type="account_login_required",
            reason_code=reason,
            severity="critical",
            operator_label="Instagram account signed out or challenged",
            action_required=(
                "The Instagram account is signed out or a challenge is displayed. "
                "Human review is required before resuming."
            ),
            requires_operator_review=True,
            blocking_campaign=True,
            admin_message=f"Login/challenge surface detected ({reason}).",
            notify_channels=True,
            metadata_safe=metadata_safe,
        )

    # 4) Device lost while running.
    if reason in DEVICE_UNAVAILABLE_REASONS:
        return IncidentDecision(
            should_publish=True,
            incident_type="run_device_unavailable",
            reason_code=reason,
            severity="error",
            operator_label="Device unavailable during the run",
            action_required=(
                "The assigned phone became unavailable during the run. "
                "Verify the device connection."
            ),
            requires_operator_review=True,
            blocking_campaign=True,
            admin_message=f"Device unavailable during run ({reason}).",
            notify_channels=True,
            metadata_safe=metadata_safe,
        )

    # 5) Dispatcher timeout: run started then never finished normally.
    if timed_out:
        return IncidentDecision(
            should_publish=True,
            incident_type="run_worker_failure",
            reason_code="subprocess_timeout",
            severity="error",
            operator_label="Run stopped by dispatcher timeout",
            action_required=(
                "The run exceeded the dispatcher timeout and was stopped. "
                "Verify the device and account state."
            ),
            requires_operator_review=True,
            blocking_campaign=True,
            admin_message="Worker subprocess exceeded dispatcher timeout.",
            notify_channels=True,
            metadata_safe=metadata_safe,
        )

    # 6) Structured runtime error transmitted by the worker.
    if reason:
        return IncidentDecision(
            should_publish=True,
            incident_type="run_worker_failure",
            reason_code=reason,
            severity="error",
            operator_label="Structured run failure",
            action_required=(
                "The run failed with a structured runtime error. "
                "Review the internal details before resuming."
            ),
            requires_operator_review=True,
            blocking_campaign=True,
            admin_message=f"Structured worker failure ({reason}), exit code {exit_code}.",
            notify_channels=True,
            metadata_safe=metadata_safe,
        )

    # 7) Last-resort fallback only: nothing more precise was transmitted.
    return IncidentDecision(
        should_publish=True,
        incident_type="run_worker_failure",
        reason_code="worker_exit_nonzero",
        severity="error",
        operator_label="Worker failed without a structured reason",
        action_required=(
            "The worker exited with an error and no structured reason. "
            "Review the internal run logs."
        ),
        requires_operator_review=True,
        blocking_campaign=True,
        admin_message=f"Worker subprocess exited with code {exit_code} and no structured reason.",
        notify_channels=True,
        metadata_safe=metadata_safe,
    )


def build_run_failure_incident_payload(
    decision: IncidentDecision,
    *,
    account_id: str | None,
    account_username: str | None = None,
    run_id: str | None = None,
    run_request_id: str | None = None,
    run_type: str | None = None,
    device_id: str | None = None,
    app_instance_id: str | None = None,
    source: str = "run_dispatcher",
    dedupe_run_ref: str | None = None,
) -> dict[str, Any]:
    """Pure builder: IncidentDecision -> upsert_account_incident payload.

    ``dedupe_run_ref`` lets a failed human-confirmed resume run enrich the
    ORIGINAL incident (deduped on the original run) instead of opening a new
    one; the new run id stays visible in metadata.
    """
    run_ref = (
        str(dedupe_run_ref or "").strip()
        or str(run_id or "").strip()
        or str(run_request_id or "").strip()
    )
    metadata = dict(decision.metadata_safe)
    if run_request_id:
        metadata["run_request_id"] = str(run_request_id)
    if run_type:
        metadata.setdefault("run_type", str(run_type))
    if dedupe_run_ref and run_id and str(dedupe_run_ref).strip() != str(run_id).strip():
        metadata["resume_run_id"] = str(run_id)
    metadata["operator_label"] = decision.operator_label
    if device_id:
        metadata.setdefault("device_id", str(device_id))
    if app_instance_id:
        metadata.setdefault("app_instance_id", str(app_instance_id))
    is_auto_login = metadata.get("domain") == "auto_login"
    return {
        "incident_type": decision.incident_type,
        "dedupe_key": str(metadata.get("incident_dedupe_key") or "").strip()
        or build_incident_dedupe_key(
            account_id=account_id,
            run_ref=run_ref,
            incident_type=decision.incident_type,
            phase=str(metadata.get("phase") or "") if is_auto_login else None,
            reason_code=decision.reason_code if is_auto_login else None,
        ),
        "severity": decision.severity,
        "status": "open",
        "account_id": str(account_id or "").strip() or None,
        "account_username": str(account_username or "").strip() or None,
        "run_id": str(run_id or "").strip() or None,
        "device_id": str(device_id or "").strip() or None,
        "source": source,
        "reason": decision.reason_code,
        "failure_reason": decision.reason_code,
        "action_required": decision.action_required,
        "safe_client_message": str(
            metadata.get("client_safe_message") or "Automation paused for account safety."
        ),
        "assistant_message": decision.operator_label,
        "admin_message": decision.admin_message,
        "metadata": metadata,
    }
