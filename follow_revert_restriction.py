"""Generic consecutive Follow-revert classification and preventive hold.

The module has no device probes and never performs an Instagram gesture.  It
consumes only already-fresh Worker evidence after exact Following-surface
recovery, then reuses the canonical 48-hour incident pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import supabase_client
from instagram_action_restriction import InstagramActionRestrictionDetected
from logs import log


STABLE_REASON = "preventive_follow_restriction"
CANONICAL_RESTRICTION_REASON = "instagram_action_rate_limit"
PRIMARY_STATUS = "48H pause required"
INCIDENT_TYPE = "instagram_account_restriction"
THRESHOLD = 3


def _handle(value: Any) -> str:
    return str(value or "").strip().lower().lstrip("@")


@dataclass(frozen=True)
class FollowRevertObservation:
    candidate_username: str
    tap_sent: bool
    follow_state_before: str
    follow_state_after: str
    failure_code: int
    exact_candidate_profile_proved: bool
    exact_followers_surface_recovered: bool
    follow_persisted: bool = False
    navigation_drift_detected: bool = False


def classify_strict_follow_revert(
    observation: FollowRevertObservation,
) -> tuple[bool, str]:
    candidate = _handle(observation.candidate_username)
    if not candidate:
        return False, "candidate_identity_missing"
    if not observation.exact_candidate_profile_proved:
        return False, "candidate_profile_not_proved"
    if not observation.tap_sent:
        return False, "physical_follow_tap_not_sent"
    if _handle(observation.follow_state_before) != "follow":
        return False, "pre_tap_state_not_follow"
    if _handle(observation.follow_state_after) != "follow":
        return False, "fresh_post_tap_state_not_follow"
    if int(observation.failure_code or 0) != 34:
        return False, "follow_verify_failure_code_mismatch"
    if observation.follow_persisted:
        return False, "follow_already_persisted"
    if observation.navigation_drift_detected:
        return False, "navigation_drift_detected"
    if not observation.exact_followers_surface_recovered:
        return False, "exact_followers_surface_not_recovered"
    return True, "strict_follow_action_reverted"


@dataclass
class DistinctFollowRevertTracker:
    threshold: int = THRESHOLD
    _candidates: list[str] = field(default_factory=list)

    def reset(self, *, reason: str = "non_revert_outcome") -> dict[str, Any]:
        previous = tuple(self._candidates)
        self._candidates.clear()
        return {
            "triggered": False,
            "streak_count": 0,
            "candidates": (),
            "reason": reason,
            "previous_candidates": previous,
        }

    def observe(self, observation: FollowRevertObservation) -> dict[str, Any]:
        strict, reason = classify_strict_follow_revert(observation)
        if not strict:
            return self.reset(reason=reason)
        candidate = _handle(observation.candidate_username)
        if self._candidates and self._candidates[-1] == candidate:
            return {
                "triggered": False,
                "streak_count": len(self._candidates),
                "candidates": tuple(self._candidates),
                "reason": "duplicate_candidate_does_not_advance_streak",
            }
        if candidate in self._candidates:
            self._candidates[:] = [candidate]
            return {
                "triggered": False,
                "streak_count": 1,
                "candidates": tuple(self._candidates),
                "reason": "distinctness_broken_streak_restarted",
            }
        self._candidates.append(candidate)
        triggered = len(self._candidates) >= max(1, int(self.threshold or THRESHOLD))
        return {
            "triggered": triggered,
            "streak_count": len(self._candidates),
            "candidates": tuple(self._candidates),
            "reason": STABLE_REASON if triggered else reason,
        }


def apply_preventive_follow_restriction(
    *,
    device: Any,
    account_id: str,
    account_username: str,
    run_id: str,
    request_id: str,
    candidate_username: str,
    source_profile_username: str,
    streak_candidates: tuple[str, ...],
    actions_completed: dict[str, int] | None = None,
) -> None:
    """Publish the canonical incident and terminate via the existing handler."""
    detected_at = datetime.now(timezone.utc)
    pause_until = detected_at + timedelta(hours=48)
    metadata = {
        "trigger_reason": STABLE_REASON,
        "primary_status": PRIMARY_STATUS,
        "risk_class": "temporary_restriction",
        "blocking_campaign": True,
        "is_temporary_restriction": True,
        "manual_incident_resolution_required": True,
        "auto_restart_allowed": False,
        "account_pause_required": False,
        "physical_preflight_required": False,
        "candidate_local_evidence": True,
        "strict_follow_revert_threshold": THRESHOLD,
        "strict_follow_revert_candidates": list(streak_candidates),
        "candidate_username": _handle(candidate_username),
        "source_profile_username": _handle(source_profile_username),
        "actions_completed": dict(actions_completed or {}),
        "detected_at": detected_at.isoformat(),
        "recommended_pause_until": pause_until.isoformat(),
        "pause_policy_source": "bmb_operational_policy_48h",
        "instagram_exact_expiry_provided": False,
        "restriction_evidence": "preventive",
    }
    result = supabase_client.apply_instagram_action_restriction(
        {
            "account_id": account_id,
            "account_username": account_username,
            "run_id": run_id or None,
            "request_id": request_id or None,
            # The live RPC deliberately accepts only this canonical value.
            "stable_reason": CANONICAL_RESTRICTION_REASON,
            "metadata_safe": metadata,
        }
    )
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("preventive_follow_restriction_publication_failed")
    incident_id = str(result.get("incident_id") or "") or None
    log(
        "error",
        "preventive_follow_restriction_applied",
        account_id=account_id or None,
        run_id=run_id or None,
        request_id=request_id or None,
        incident_id=incident_id,
        candidate_username=_handle(candidate_username),
        streak_count=len(streak_candidates),
        streak_candidates=list(streak_candidates),
        primary_status=PRIMARY_STATUS,
        no_further_business_actions=True,
    )
    summary = {
        "reason": STABLE_REASON,
        "reason_code": STABLE_REASON,
        "failure_reason": STABLE_REASON,
        "root_failure_code": STABLE_REASON,
        "canonical_restriction_reason": CANONICAL_RESTRICTION_REASON,
        "primary_status": PRIMARY_STATUS,
        "incident_type": INCIDENT_TYPE,
        "risk_class": "temporary_restriction",
        "account_id": account_id,
        "account_username": account_username,
        "run_id": run_id,
        "request_id": request_id,
        "phase": "follow",
        "preceding_action": "strict_follow_action_reverted",
        "blocking_campaign": True,
        "is_temporary_restriction": True,
        "manual_incident_resolution_required": True,
        "auto_restart_allowed": False,
        "account_pause_required": False,
        "physical_preflight_required": False,
        "incident_id": incident_id,
        "incident_dedupe_key": str(result.get("dedupe_key") or "") or None,
        "detected_at": detected_at.isoformat(),
        "recommended_pause_until": pause_until.isoformat(),
        "pause_policy_source": "bmb_operational_policy_48h",
        "actions_completed": dict(actions_completed or {}),
        "strict_follow_revert_candidates": list(streak_candidates),
    }
    raise InstagramActionRestrictionDetected(device=device, summary=summary)
