"""Typed outcome and recovery policy for real Unfollow actions.

This module is deliberately pure.  UI recovery and persistence stay in the
session orchestrator, while this policy decides whether an already-attempted
action may be skipped after an exact Following-list recovery.  A missing
verification can never be promoted to a successful Unfollow.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class UnfollowExecutionContext:
    """Immutable identity contract for one real Unfollow execution attempt."""

    account_id: str
    account_username: str
    request_id: str
    run_id: str
    root_business_session_id: str
    attempt_ordinal: int
    business_date_sast: str
    worker_sha: str
    runtime_root: str
    plan_id: str
    plan_generation: str
    device_id: str = ""
    app_instance_id: str = ""
    package_name: str = "com.instagram.android"

    @classmethod
    def build(
        cls,
        *,
        account_id: str,
        account_username: str,
        request_id: str | None,
        run_id: str | None,
        root_business_session_id: str | None,
        attempt_ordinal: int,
        business_date_sast: str,
        worker_sha: str | None,
        runtime_root: str,
        daily_plan_context: dict[str, Any],
        device_id: str | None = None,
        app_instance_id: str | None = None,
        package_name: str = "com.instagram.android",
    ) -> "UnfollowExecutionContext":
        plan_id = str(daily_plan_context.get("plan_id") or "").strip()
        plan_generation = str(
            daily_plan_context.get("generation")
            or daily_plan_context.get("revision")
            or daily_plan_context.get("created_at")
            or daily_plan_context.get("business_date_sast")
            or business_date_sast
            or ""
        ).strip()
        context = cls(
            account_id=str(account_id or "").strip(),
            account_username=str(account_username or "").strip(),
            request_id=str(request_id or "").strip(),
            run_id=str(run_id or "").strip(),
            root_business_session_id=str(root_business_session_id or "").strip(),
            attempt_ordinal=max(0, int(attempt_ordinal or 0)),
            business_date_sast=str(business_date_sast or "").strip(),
            worker_sha=str(worker_sha or "").strip(),
            runtime_root=str(runtime_root or "").strip(),
            plan_id=plan_id,
            plan_generation=plan_generation,
            device_id=str(device_id or "").strip(),
            app_instance_id=str(app_instance_id or "").strip(),
            package_name=str(package_name or "").strip(),
        )
        context.validate()
        return context

    def validate(self) -> None:
        required = {
            "account_id": self.account_id,
            "account_username": self.account_username,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "root_business_session_id": self.root_business_session_id,
            "business_date_sast": self.business_date_sast,
            "worker_sha": self.worker_sha,
            "runtime_root": self.runtime_root,
            "plan_id": self.plan_id,
            "plan_generation": self.plan_generation,
            "package_name": self.package_name,
        }
        missing = sorted(key for key, value in required.items() if not str(value).strip())
        if missing:
            raise ValueError("unfollow_execution_context_missing:" + ",".join(missing))
        if self.attempt_ordinal not in {1, 2, 3}:
            raise ValueError("unfollow_execution_context_attempt_out_of_range")

    def as_safe_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "account_username": self.account_username,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "root_business_session_id": self.root_business_session_id,
            "attempt_ordinal": self.attempt_ordinal,
            "business_date_sast": self.business_date_sast,
            "worker_sha": self.worker_sha,
            "runtime_root": self.runtime_root,
            "plan_id": self.plan_id,
            "plan_generation": self.plan_generation,
            "device_id": self.device_id,
            "app_instance_id": self.app_instance_id,
            "package_name": self.package_name,
        }


class UnfollowActionOutcomeClass(str, Enum):
    VERIFIED_UNFOLLOW = "VERIFIED_UNFOLLOW"
    ALREADY_NOT_FOLLOWING_CONFIRMED = "ALREADY_NOT_FOLLOWING_CONFIRMED"
    SAFE_CANDIDATE_LOCAL_AMBIGUITY = "SAFE_CANDIDATE_LOCAL_AMBIGUITY"
    VERIFY_FAILED_RECOVERABLE = "VERIFY_FAILED_RECOVERABLE"
    VERIFY_FAILED_UNSAFE_STATE = "VERIFY_FAILED_UNSAFE_STATE"
    ACTION_ATTEMPTED_AMBIGUOUS = "ACTION_ATTEMPTED_AMBIGUOUS"
    SEARCH_NOT_FOUND_CONFIRMED = "SEARCH_NOT_FOUND_CONFIRMED"
    TRANSIENT_UI_FAILURE = "TRANSIENT_UI_FAILURE"
    SECURITY_BLOCK = "SECURITY_BLOCK"


class UnfollowExecutionOutcomeClass(str, Enum):
    """Authoritative phase-level taxonomy; never infer this from exit code."""

    CANDIDATE_LOCAL_TERMINAL = "CANDIDATE_LOCAL_TERMINAL"
    CANDIDATE_LOCAL_RETRYABLE = "CANDIDATE_LOCAL_RETRYABLE"
    PHASE_PARTIAL_RESUMABLE = "PHASE_PARTIAL_RESUMABLE"
    SCHEDULED_SAFE_STOP = "SCHEDULED_SAFE_STOP"
    GLOBAL_SAFETY_BLOCKER = "GLOBAL_SAFETY_BLOCKER"
    PERSISTENCE_AMBIGUOUS = "PERSISTENCE_AMBIGUOUS"
    RUNTIME_INTERNAL_ERROR = "RUNTIME_INTERNAL_ERROR"
    COMPLETED = "COMPLETED"


_RUNTIME_INTERNAL_REASONS = {
    "unfollow_execution_context_invalid",
    "unfollow_execution_context_missing",
    "unfollow_plan_contains_protected_candidate",
    "unfollow_candidate_funnel_reconciliation_failed",
}


def classify_unfollow_execution_outcome(
    *,
    status: str,
    stable_reason: str,
    remaining_count: int,
    resume_recommended: bool,
) -> UnfollowExecutionOutcomeClass:
    normalized_status = str(status or "").strip().lower()
    reason = str(stable_reason or "").strip().lower()
    if reason.startswith("runtime_internal_error:") or reason in _RUNTIME_INTERNAL_REASONS:
        return UnfollowExecutionOutcomeClass.RUNTIME_INTERNAL_ERROR
    if "persistence" in reason or "mutation_intent" in reason:
        return UnfollowExecutionOutcomeClass.PERSISTENCE_AMBIGUOUS
    if reason in {
        "session_time_budget_exhausted",
        "business_action_deadline_reached",
        "scheduled_safe_stop",
    }:
        return UnfollowExecutionOutcomeClass.SCHEDULED_SAFE_STOP
    if reason in {
        "username_not_found_confirmed",
        "already_not_following_confirmed",
        "candidate_unavailable_exhausted",
    }:
        return UnfollowExecutionOutcomeClass.CANDIDATE_LOCAL_TERMINAL
    if reason in {
        "temporary_search_miss",
        "search_surface_unhealthy",
        "candidate_technical_hold",
        "transient_ui_failure",
    }:
        return UnfollowExecutionOutcomeClass.CANDIDATE_LOCAL_RETRYABLE
    if normalized_status.startswith("failed_"):
        return UnfollowExecutionOutcomeClass.GLOBAL_SAFETY_BLOCKER
    if remaining_count > 0 and resume_recommended:
        return UnfollowExecutionOutcomeClass.PHASE_PARTIAL_RESUMABLE
    return UnfollowExecutionOutcomeClass.COMPLETED


ACTION_ATTEMPTED_AMBIGUOUS_REASON_PREFIX = "action_attempted_ambiguous:"
# Match the existing technical candidate cooldown used by the Unfollow
# availability RPCs.  This prevents an immediate blind replay in another run,
# while allowing the normal profile-state preflight to retry later.
ACTION_ATTEMPTED_AMBIGUOUS_COOLDOWN_MINUTES = 30


def ambiguous_failure_reason(reason: str) -> str:
    stable_reason = str(reason or "unfollow_verify_failed").strip()
    if stable_reason.startswith(ACTION_ATTEMPTED_AMBIGUOUS_REASON_PREFIX):
        return stable_reason
    return f"{ACTION_ATTEMPTED_AMBIGUOUS_REASON_PREFIX}{stable_reason}"


def is_action_attempted_ambiguous_reason(reason: str) -> bool:
    return str(reason or "").strip().lower().startswith(
        ACTION_ATTEMPTED_AMBIGUOUS_REASON_PREFIX
    )


@dataclass(frozen=True)
class VerifyRecoveryDecision:
    candidate_outcome_class: UnfollowActionOutcomeClass
    recovery_class: UnfollowActionOutcomeClass
    should_continue: bool
    circuit_breaker_open: bool
    next_consecutive_count: int
    stable_reason: str


def decide_verify_failure_after_recovery(
    *,
    verification_ok: bool,
    action_attempted: bool,
    failure_reason: str,
    exact_following_list_restored: bool,
    package_activity_ok: bool,
    account_identity_ok: bool,
    unsafe_markers_present: bool,
    ambiguity_journaled: bool,
    previous_failure_class: str,
    previous_consecutive_count: int,
    max_consecutive_failures: int,
) -> VerifyRecoveryDecision:
    """Classify one post-action result without weakening verification.

    ``should_continue`` is possible only after the ambiguous action truth was
    durably journaled and the exact owner Following list was positively
    restored.  Canonical success persistence is deliberately not part of
    navigation recovery safety: an unverified action must never fabricate a
    success receipt merely to let the session continue.
    """

    if verification_ok:
        return VerifyRecoveryDecision(
            candidate_outcome_class=UnfollowActionOutcomeClass.VERIFIED_UNFOLLOW,
            recovery_class=UnfollowActionOutcomeClass.VERIFIED_UNFOLLOW,
            should_continue=True,
            circuit_breaker_open=False,
            next_consecutive_count=0,
            stable_reason="",
        )

    stable_reason = ambiguous_failure_reason(failure_reason)
    if not action_attempted:
        return VerifyRecoveryDecision(
            candidate_outcome_class=UnfollowActionOutcomeClass.TRANSIENT_UI_FAILURE,
            recovery_class=UnfollowActionOutcomeClass.VERIFY_FAILED_UNSAFE_STATE,
            should_continue=False,
            circuit_breaker_open=False,
            next_consecutive_count=0,
            stable_reason=stable_reason,
        )

    safe_candidate_local_ambiguity = bool(
        ambiguity_journaled
        and exact_following_list_restored
        and package_activity_ok
        and account_identity_ok
        and not unsafe_markers_present
    )
    if not safe_candidate_local_ambiguity:
        recovery_class = (
            UnfollowActionOutcomeClass.SECURITY_BLOCK
            if unsafe_markers_present or not account_identity_ok
            else UnfollowActionOutcomeClass.VERIFY_FAILED_UNSAFE_STATE
        )
        return VerifyRecoveryDecision(
            candidate_outcome_class=UnfollowActionOutcomeClass.ACTION_ATTEMPTED_AMBIGUOUS,
            recovery_class=recovery_class,
            should_continue=False,
            circuit_breaker_open=False,
            next_consecutive_count=0,
            stable_reason=stable_reason,
        )

    failure_class = UnfollowActionOutcomeClass.SAFE_CANDIDATE_LOCAL_AMBIGUITY.value
    next_count = (
        max(0, int(previous_consecutive_count)) + 1
        if str(previous_failure_class or "") == failure_class
        else 1
    )
    bounded_max = max(1, int(max_consecutive_failures))
    circuit_open = next_count > bounded_max
    return VerifyRecoveryDecision(
        candidate_outcome_class=UnfollowActionOutcomeClass.ACTION_ATTEMPTED_AMBIGUOUS,
        recovery_class=UnfollowActionOutcomeClass.SAFE_CANDIDATE_LOCAL_AMBIGUITY,
        should_continue=not circuit_open,
        circuit_breaker_open=circuit_open,
        next_consecutive_count=next_count,
        stable_reason=stable_reason,
    )
