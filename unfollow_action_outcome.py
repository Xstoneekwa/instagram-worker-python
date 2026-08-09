"""Typed outcome and recovery policy for real Unfollow actions.

This module is deliberately pure.  UI recovery and persistence stay in the
session orchestrator, while this policy decides whether an already-attempted
action may be skipped after an exact Following-list recovery.  A missing
verification can never be promoted to a successful Unfollow.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UnfollowActionOutcomeClass(str, Enum):
    VERIFIED_UNFOLLOW = "VERIFIED_UNFOLLOW"
    ALREADY_NOT_FOLLOWING_CONFIRMED = "ALREADY_NOT_FOLLOWING_CONFIRMED"
    VERIFY_FAILED_RECOVERABLE = "VERIFY_FAILED_RECOVERABLE"
    VERIFY_FAILED_UNSAFE_STATE = "VERIFY_FAILED_UNSAFE_STATE"
    ACTION_ATTEMPTED_AMBIGUOUS = "ACTION_ATTEMPTED_AMBIGUOUS"
    SEARCH_NOT_FOUND_CONFIRMED = "SEARCH_NOT_FOUND_CONFIRMED"
    TRANSIENT_UI_FAILURE = "TRANSIENT_UI_FAILURE"
    SECURITY_BLOCK = "SECURITY_BLOCK"


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
    persistence_ok: bool,
    previous_failure_class: str,
    previous_consecutive_count: int,
    max_consecutive_failures: int,
) -> VerifyRecoveryDecision:
    """Classify one post-action result without weakening verification.

    ``should_continue`` is possible only after the failed audit outcome was
    persisted and the exact owner Following list was positively restored.
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

    safe_state_restored = bool(
        exact_following_list_restored
        and package_activity_ok
        and account_identity_ok
        and not unsafe_markers_present
        and persistence_ok
    )
    if not safe_state_restored:
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

    failure_class = UnfollowActionOutcomeClass.VERIFY_FAILED_RECOVERABLE.value
    next_count = (
        max(0, int(previous_consecutive_count)) + 1
        if str(previous_failure_class or "") == failure_class
        else 1
    )
    bounded_max = max(1, int(max_consecutive_failures))
    circuit_open = next_count > bounded_max
    return VerifyRecoveryDecision(
        candidate_outcome_class=UnfollowActionOutcomeClass.ACTION_ATTEMPTED_AMBIGUOUS,
        recovery_class=UnfollowActionOutcomeClass.VERIFY_FAILED_RECOVERABLE,
        should_continue=not circuit_open,
        circuit_breaker_open=circuit_open,
        next_consecutive_count=next_count,
        stable_reason=stable_reason,
    )
