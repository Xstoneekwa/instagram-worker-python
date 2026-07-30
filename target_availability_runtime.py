"""Least-risk dormant hooks over existing CT rotation signals.

No Instagram action is performed here. The adapter only translates facts that
the current rotation path already produced. Missing or ambiguous facts remain
unknown and the caller always continues to the next target.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Mapping, Optional

from target_availability_observation import (
    TargetAvailabilityObservationScope,
    build_target_availability_observation,
)
from target_availability_writer import (
    BackendPipelineTransport,
    DEFAULT_AUTO_KILL_FILE,
    FailOpenTargetAvailabilityWriter,
    TargetAvailabilityFeatureFlags,
)


_WRITER: FailOpenTargetAvailabilityWriter | None = None
_MEMORY_PROBE = None

_SCOPE_REJECTION_REASONS = frozenset(
    {
        "target_availability_commercial_revision_client_id_malformed",
        "target_availability_tenant_account_id_invalid",
        "target_availability_tenant_ownership_ambiguous",
        "target_availability_tenant_ownership_conflict",
        "target_availability_tenant_ownership_inactive",
        "target_availability_tenant_ownership_lookup_failed",
        "target_availability_tenant_ownership_missing",
        "target_availability_tenant_ownership_response_malformed",
    }
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _integer(value: object, default: int = 0) -> int:
    try:
        return max(0, int(value or default))
    except (TypeError, ValueError):
        return default


def _scope_rejection_reason(value: object) -> str:
    reason = _text(value)
    return reason if reason in _SCOPE_REJECTION_REASONS else "invalid_observation_scope"


def _scope(*, tenant_id: str, account_id: str, target_id: str, username: str, stable_id: str | None = None):
    if not all(_text(value) for value in (tenant_id, account_id, target_id, username)):
        return None
    try:
        return TargetAvailabilityObservationScope(
            tenant_id=tenant_id,
            account_id=account_id,
            target_id=target_id,
            normalized_username=username,
            stable_platform_user_id=stable_id,
        )
    except (TypeError, ValueError):
        return None


def _writer(flags: TargetAvailabilityFeatureFlags) -> FailOpenTargetAvailabilityWriter | None:
    global _WRITER
    if (
        not flags.target_availability_writer_enabled
        or not flags.target_availability_shadow_enabled
        or not flags.target_availability_identity_producer_enabled
        or not flags.target_availability_assessment_producer_enabled
        or not flags.target_availability_current_projector_enabled
        or flags.target_availability_policy_shadow_enabled
        or flags.kill_switch
    ):
        return None
    if _WRITER is None:
        try:
            candidate = FailOpenTargetAvailabilityWriter(
                BackendPipelineTransport.from_environment(),
                auto_kill_file=os.getenv("TARGET_AVAILABILITY_AUTO_KILL_FILE") or DEFAULT_AUTO_KILL_FILE,
            )
            candidate.start()
            if not candidate.thread_alive:
                return None
            _WRITER = candidate
        except Exception:
            return None
    return _WRITER


def _capture(observation, *, flags: TargetAvailabilityFeatureFlags) -> bool:
    if not flags.pipeline_allowed(str(getattr(observation, "account_id", "") or "")):
        return True
    writer = _writer(flags)
    if writer is None:
        return True
    return writer.enqueue(observation)


def _memory_probe(flags: TargetAvailabilityFeatureFlags, account_id: str):
    """Resolve the fixed-cardinality probe independently of DB writer state."""

    global _MEMORY_PROBE
    if not flags.capture_allowed(account_id):
        return None
    if str(os.getenv("TARGET_AVAILABILITY_MEMORY_PROBE_ENABLED") or "").strip().lower() != "true":
        return None
    if _MEMORY_PROBE is None:
        try:
            from target_availability_memory_probe import TargetAvailabilityMemoryProbe

            _MEMORY_PROBE = TargetAvailabilityMemoryProbe.from_mapping()
        except Exception:
            return None
    try:
        _MEMORY_PROBE.set_runtime_state(
            writer_enabled=flags.target_availability_writer_enabled,
            shadow_enabled=flags.target_availability_shadow_enabled,
        )
    except Exception:
        return None
    return _MEMORY_PROBE


def _probe_started(probe, *, account_id: str, run_id: object, stage: str):
    if probe is None:
        return None
    try:
        return probe.start_hook(account_id=account_id, run_id=run_id, stage=stage)
    except Exception:
        return None


def _probe_finished(probe, started_ns) -> None:
    if probe is None or started_ns is None:
        return
    try:
        probe.finish_hook(started_ns)
    except Exception:
        pass


def observe_rotation_target_loaded(
    *,
    tenant_id: str,
    account_id: str,
    target_id: str,
    username: str,
    run_id: str | None,
    target_index: int,
    stable_platform_user_id: str | None = None,
    scope_rejection_reason: str | None = None,
    flags: TargetAvailabilityFeatureFlags | None = None,
) -> bool:
    active = flags or TargetAvailabilityFeatureFlags.from_mapping()
    if not active.capture_allowed(account_id):
        return True
    probe = _memory_probe(active, account_id)
    probe_started_ns = _probe_started(
        probe,
        account_id=account_id,
        run_id=run_id,
        stage="username_lookup_started",
    )
    try:
        scope = _scope(tenant_id=tenant_id, account_id=account_id, target_id=target_id, username=username, stable_id=stable_platform_user_id)
        if scope is None:
            if probe is not None:
                probe.record_rejected(_scope_rejection_reason(scope_rejection_reason))
            return True
        observation = build_target_availability_observation(
            scope=scope,
            event_key="%s:target:%s:loaded" % (_text(run_id) or "no-run", int(target_index)),
            observed_at=datetime.now(timezone.utc),
            reason_codes=["target_username_lookup_started"],
            observation_stage="username_lookup_started",
            identity_source="target_record" if stable_platform_user_id else "unknown",
            identity_confidence="unknown",
            username_lookup_started=True,
            run_id=run_id,
            instance_id=os.getenv("PHONEFARM_INSTANCE_ID") or os.getenv("WORKER_INSTANCE_ID"),
            device_key=os.getenv("PHONE_DEVICE_ID") or os.getenv("DEVICE_ID"),
            worker_version=os.getenv("PHONEFARM_WORKER_RELEASE") or os.getenv("GIT_SHA"),
            instagram_version=os.getenv("INSTAGRAM_VERSION"),
            evidence_safe={"target_index": int(target_index), "signal": "ct_rotation_target_loaded"},
        )
        if probe is not None and not probe.record_observation(observation):
            del observation
            return True
        captured = _capture(observation, flags=active)
        del observation
        return captured
    except (TypeError, ValueError) as exc:
        if probe is not None:
            probe.record_rejected(type(exc).__name__)
        return True
    except Exception as exc:
        if probe is not None:
            probe.record_error(type(exc).__name__)
        return True
    finally:
        _probe_finished(probe, probe_started_ns)


def observation_from_rotation_summary(
    *,
    tenant_id: str,
    account_id: str,
    target_id: str,
    username: str,
    run_id: str | None,
    target_index: int,
    summary: Mapping[str, Any],
    stable_platform_user_id: str | None = None,
    observed_at: Optional[datetime] = None,
):
    scope = _scope(tenant_id=tenant_id, account_id=account_id, target_id=target_id, username=username, stable_id=stable_platform_user_id)
    if scope is None:
        return None
    reason = _text(summary.get("follow_stop_reason") or summary.get("follow_session_outcome")).lower()
    profile_found_raw = summary.get("profile_found")
    if profile_found_raw is None and summary.get("source_profile_resolved") is True:
        profile_found_raw = True
    followers_surface = _text(summary.get("followers_surface")).lower()
    if followers_surface not in {"normal", "restricted", "terminally_limited"}:
        followers_surface = "unknown"
    terminal = bool(summary.get("terminal_end_detected") is True)
    repeated = bool(summary.get("repeated_first_profiles_detected") is True)
    profile_ambiguous = bool(summary.get("profile_ambiguous") is True)
    followers_surface_entered = bool(summary.get("followers_surface_entered") is True or followers_surface != "unknown")
    followers_entry_failed = bool(summary.get("followers_entry_failed") is True)
    pagination_stalled = bool(summary.get("pagination_stalled") is True)
    source_profile_mismatch = bool(summary.get("source_profile_mismatch") is True)
    identity_conflict = bool(summary.get("identity_conflict") is True)
    ui_ambiguous = bool(summary.get("ui_ambiguous") is True)
    session_ambiguous = bool(summary.get("session_ambiguous") is True)
    timeout = bool(summary.get("navigation_timeout") is True or "timeout" in reason)
    retry_count = _integer(summary.get("navigation_retry_count") or summary.get("retry_count"))
    exhausted = bool(summary.get("navigation_retry_budget_exhausted") is True)
    recovery = _text(summary.get("recovery_outcome")).lower()
    if recovery not in {"not_attempted", "succeeded", "failed", "ambiguous"}:
        recovery = "not_attempted"
    network_state = _text(summary.get("network_state")).lower()
    if network_state not in {"unknown", "healthy", "degraded", "unavailable"}:
        network_state = "unknown"
    session_state = _text(summary.get("session_state")).lower()
    if session_state not in {"unknown", "healthy", "restricted", "logged_out"}:
        session_state = "unknown"
    lookup_result = _text(summary.get("lookup_result")).lower()
    if lookup_result not in {"found", "not_found", "unavailable", "failed", "unknown"}:
        lookup_result = "found" if profile_found_raw is True else "not_found" if profile_found_raw is False else "failed" if timeout else "unknown"

    reasons: list[str] = []
    reasons.append("target_username_lookup_completed")
    if profile_found_raw is True:
        reasons.append("target_profile_found")
    elif profile_found_raw is False:
        reasons.append("target_profile_not_found")
    if profile_ambiguous:
        reasons.append("target_profile_ambiguous")
    if stable_platform_user_id:
        reasons.append("target_stable_identity_observed")
    if identity_conflict:
        reasons.append("target_identity_conflict")
    if source_profile_mismatch:
        reasons.append("target_source_profile_mismatch")
    if summary.get("verified_badge") is True:
        reasons.append("target_verified_status_detected")
    if followers_surface == "normal":
        reasons.append("target_followers_surface_normal")
    elif followers_surface == "restricted":
        reasons.append("target_followers_surface_restricted")
    elif followers_surface == "terminally_limited":
        reasons.append("target_followers_surface_terminally_limited")
    if followers_surface_entered:
        reasons.append("target_followers_surface_entered")
    if followers_entry_failed:
        reasons.append("target_followers_entry_failed")
    if pagination_stalled:
        reasons.append("target_pagination_stalled")
    if terminal:
        reasons.append("target_followers_surface_terminally_limited")
    if repeated:
        reasons.append("target_repeated_first_profiles_detected")
    if exhausted:
        reasons.append("target_navigation_retry_budget_exhausted")
    if timeout:
        reasons.append("target_navigation_timeout")
    recovery_attempted = bool(summary.get("recovery_attempted") is True or recovery != "not_attempted")
    if recovery_attempted:
        reasons.append("target_recovery_attempted")
    if recovery == "succeeded":
        reasons.append("target_recovery_succeeded")
    elif recovery == "failed":
        reasons.append("target_recovery_failed")
    if ui_ambiguous:
        reasons.append("target_ui_ambiguity")
    if network_state in {"degraded", "unavailable"}:
        reasons.append("target_network_ambiguity")
    if session_ambiguous:
        reasons.append("target_session_ambiguity")
    if not reasons:
        reasons.append("target_ui_ambiguity")

    return build_target_availability_observation(
        scope=scope,
        event_key="%s:target:%s:summary" % (_text(run_id) or "no-run", int(target_index)),
        observed_at=observed_at or datetime.now(timezone.utc),
        reason_codes=reasons,
        observation_stage="target_summary_completed",
        identity_source="existing_runtime_signal" if stable_platform_user_id else "unknown",
        identity_confidence=_text(summary.get("identity_confidence")) if _text(summary.get("identity_confidence")) in {"low", "medium", "high"} else "unknown",
        lookup_result=lookup_result,
        profile_found=profile_found_raw if isinstance(profile_found_raw, bool) else None,
        observed_username=_text(summary.get("observed_username")) or (username if profile_found_raw is True else None),
        observed_stable_platform_user_id=stable_platform_user_id,
        verified_badge=summary.get("verified_badge") if isinstance(summary.get("verified_badge"), bool) else None,
        followers_surface=followers_surface,
        accessible_profiles_count=summary.get("accessible_profiles_count"),
        terminal_end_detected=terminal,
        repeated_first_profiles_detected=repeated,
        retry_count=retry_count,
        retry_budget_exhausted=exhausted,
        navigation_timeout=timeout,
        username_lookup_completed=True,
        profile_ambiguous=profile_ambiguous,
        followers_surface_entered=followers_surface_entered,
        followers_surface_entry_failed=followers_entry_failed,
        pagination_stalled=pagination_stalled,
        recovery_attempted=recovery_attempted,
        ui_ambiguous=ui_ambiguous,
        network_ambiguous=network_state in {"degraded", "unavailable"},
        session_ambiguous=session_ambiguous,
        source_profile_mismatch=source_profile_mismatch,
        identity_conflict=identity_conflict,
        recovery_outcome=recovery,
        ui_evidence_quality=_text(summary.get("ui_evidence_quality")) if _text(summary.get("ui_evidence_quality")) in {"low", "medium", "high"} else "unknown",
        network_state=network_state,
        session_state=session_state,
        run_id=run_id,
        request_id=_text(summary.get("request_id")) or None,
        instance_id=os.getenv("PHONEFARM_INSTANCE_ID") or os.getenv("WORKER_INSTANCE_ID"),
        device_key=os.getenv("PHONE_DEVICE_ID") or os.getenv("DEVICE_ID"),
        worker_version=os.getenv("PHONEFARM_WORKER_RELEASE") or os.getenv("GIT_SHA"),
        instagram_version=os.getenv("INSTAGRAM_VERSION"),
        evidence_safe={
            "target_index": int(target_index),
            "signal": "ct_rotation_existing_summary",
            "exit_code": summary.get("exit_code") if isinstance(summary.get("exit_code"), int) else None,
            "follow_stop_reason": reason[:120] or None,
        },
    )


def observe_rotation_target_summary(**kwargs: Any) -> bool:
    flags = kwargs.pop("flags", None) or TargetAvailabilityFeatureFlags.from_mapping()
    scope_rejection_reason = kwargs.pop("scope_rejection_reason", None)
    account_id = _text(kwargs.get("account_id"))
    if not flags.capture_allowed(account_id):
        return True
    probe = _memory_probe(flags, account_id)
    probe_started_ns = _probe_started(
        probe,
        account_id=account_id,
        run_id=kwargs.get("run_id"),
        stage="target_summary_completed",
    )
    try:
        observation = observation_from_rotation_summary(**kwargs)
        if observation is None:
            if probe is not None:
                probe.record_rejected(_scope_rejection_reason(scope_rejection_reason))
            return True
        if probe is not None and not probe.record_observation(observation):
            del observation
            return True
        captured = _capture(observation, flags=flags)
        del observation
        return captured
    except (TypeError, ValueError) as exc:
        if probe is not None:
            probe.record_rejected(type(exc).__name__)
        return True
    except Exception as exc:
        if probe is not None:
            probe.record_error(type(exc).__name__)
        return True
    finally:
        _probe_finished(probe, probe_started_ns)


__all__ = [
    "observe_rotation_target_loaded",
    "observe_rotation_target_summary",
    "observation_from_rotation_summary",
]
