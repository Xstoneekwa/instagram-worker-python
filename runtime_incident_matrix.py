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
from typing import Any

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
    }
)

MYTHYL_OPERATOR_MESSAGE = (
    "Impossible de confirmer le compte Instagram actif. "
    "Intervention humaine requise avant reprise."
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
        }


def build_incident_dedupe_key(
    *,
    account_id: str | None,
    run_ref: str | None,
    incident_type: str,
) -> str:
    """Canonical dedupe key: one incident per (account, run, incident_type).

    Repetitions of the same run + same incident_type enrich the existing
    incident (RPC occurrence_count). A different incident_type for the same
    run creates a distinct incident. Cross-run repetitions create per-run
    incidents on purpose: each failed run is an operator-actionable event.
    """
    aid = str(account_id or "").strip() or "unknown"
    ref = str(run_ref or "").strip() or "unknown"
    return f"account:{aid}:run:{ref}:{incident_type}"


_SAFE_METADATA_KEYS = (
    "run_type",
    "reason",
    "account_identity_failure_reason",
    "account_identity_verification_method",
    "expected_account_username",
    "actual_logged_in_username",
    "welcome_scan_stop_reason",
    "welcome_scan_jobs_enqueued_count",
    "welcome_sender_failure_reason",
    "welcome_entry_surface_decision",
)


def _safe_summary_metadata(summary: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _SAFE_METADATA_KEYS:
        value = summary.get(key)
        if value is None:
            continue
        text = str(value).strip()
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
) -> IncidentDecision:
    """Classify one terminal run outcome into a canonical incident decision.

    ``performance_summary`` is the structured payload the runner persisted on
    the linked ``ig_runs`` row (never raw text logs).
    """
    summary = dict(performance_summary or {})
    status = str(run_status or "").strip().lower()
    reason = str(summary.get("reason") or "").strip()
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
                operator_label="Identité Instagram non vérifiable",
                action_required=MYTHYL_OPERATOR_MESSAGE,
                admin_message=(
                    "Le préflight identité n'a pas pu prouver le pseudo actif "
                    f"({code}). Le run a été arrêté avant toute action."
                ),
                notify_channels=True,
                metadata_safe=metadata_safe,
            )
        return IncidentDecision(
            should_publish=True,
            incident_type="active_instagram_account_mismatch",
            reason_code="active_instagram_account_mismatch",
            severity="critical",
            operator_label="Mauvais compte Instagram actif",
            action_required=(
                "Un autre compte Instagram est connecté dans le clone assigné. "
                "Vérifier le compte actif avant reprise."
            ),
            admin_message=(
                "Le préflight identité a détecté un pseudo différent du compte "
                "attendu. Le run a été arrêté avant toute action."
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
            action_required="operator_review_required",
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
            operator_label="Package / clone Instagram assigné inutilisable",
            action_required=(
                "Le clone Instagram assigné n'a pas pu être utilisé. "
                "Vérifier le device et l'app instance avant reprise."
            ),
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
            operator_label="Compte Instagram déconnecté ou challenge",
            action_required=(
                "Le compte Instagram n'est plus connecté ou un challenge est "
                "affiché. Intervention humaine requise avant reprise."
            ),
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
            operator_label="Device indisponible pendant le run",
            action_required=(
                "Le téléphone assigné est devenu indisponible pendant le run. "
                "Vérifier la connexion du device."
            ),
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
            operator_label="Run interrompu par timeout dispatcher",
            action_required=(
                "Le run a dépassé le timeout du dispatcher et a été arrêté. "
                "Vérifier l'état du device et du compte."
            ),
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
            operator_label="Échec runtime du run",
            action_required=(
                "Le run a échoué avec une erreur runtime structurée. "
                "Vérifier les détails internes avant reprise."
            ),
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
        operator_label="Échec worker sans raison structurée",
        action_required=(
            "Le worker s'est terminé en erreur sans raison structurée. "
            "Vérifier les logs internes du run."
        ),
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
    return {
        "incident_type": decision.incident_type,
        "dedupe_key": build_incident_dedupe_key(
            account_id=account_id,
            run_ref=run_ref,
            incident_type=decision.incident_type,
        ),
        "severity": decision.severity,
        "status": "open",
        "account_id": str(account_id or "").strip() or None,
        "account_username": str(account_username or "").strip() or None,
        "run_id": str(run_id or "").strip() or None,
        "source": source,
        "reason": decision.reason_code,
        "failure_reason": decision.reason_code,
        "action_required": decision.action_required,
        "safe_client_message": "Automation paused for account safety.",
        "assistant_message": decision.operator_label,
        "admin_message": decision.admin_message,
        "metadata": metadata,
    }
