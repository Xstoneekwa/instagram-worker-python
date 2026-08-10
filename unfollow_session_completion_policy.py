"""Pure session-completion policy for authoritative Unfollow plans.

Candidate-local recovery is deliberately distinct from the global safety
circuit.  A transient candidate failure may be retried once after the rest of
the current Search generation; only consecutive failures across the session
can open the systemic circuit.  Any verified+persistent action resets that
consecutive circuit immediately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from unfollow_ui_coverage_policy import normalize_username


CANDIDATE_RECOVERY_LIMIT = 1
SESSION_CONSECUTIVE_FAILURE_LIMIT = 3


@dataclass(frozen=True)
class CandidateFailureDecision:
    continue_session: bool
    retry_in_same_session: bool
    candidate_recovery_exhausted: bool
    global_circuit_open: bool
    stable_reason: str


@dataclass
class UnfollowSessionCompletionPolicy:
    candidate_recovery_limit: int = CANDIDATE_RECOVERY_LIMIT
    session_consecutive_failure_limit: int = SESSION_CONSECUTIVE_FAILURE_LIMIT
    candidate_failure_counts: dict[str, int] = field(default_factory=dict)
    retry_pending: set[str] = field(default_factory=set)
    deferred_for_later_session: set[str] = field(default_factory=set)
    consecutive_candidate_failures: int = 0
    total_candidate_failures: int = 0
    successful_resets: int = 0
    retry_generations_started: int = 0

    def record_candidate_failure(
        self,
        username: str,
        *,
        safe_state_restored: bool,
        replay_forbidden: bool = False,
    ) -> CandidateFailureDecision:
        normalized = normalize_username(username)
        if not normalized:
            return CandidateFailureDecision(
                False,
                False,
                True,
                True,
                "invalid_candidate_identity",
            )
        self.total_candidate_failures += 1
        self.consecutive_candidate_failures += 1
        count = self.candidate_failure_counts.get(normalized, 0) + 1
        self.candidate_failure_counts[normalized] = count
        global_open = (
            not safe_state_restored
            or self.consecutive_candidate_failures
            >= max(1, int(self.session_consecutive_failure_limit))
        )
        if global_open:
            self.deferred_for_later_session.add(normalized)
            return CandidateFailureDecision(
                False,
                False,
                True,
                True,
                (
                    "unfollow_global_safe_state_not_restored"
                    if not safe_state_restored
                    else "unfollow_session_consecutive_candidate_failure_limit_reached"
                ),
            )
        recovery_exhausted = bool(
            replay_forbidden
            or count > max(0, int(self.candidate_recovery_limit))
        )
        if recovery_exhausted:
            self.retry_pending.discard(normalized)
            self.deferred_for_later_session.add(normalized)
            return CandidateFailureDecision(
                True,
                False,
                True,
                False,
                "candidate_recovery_budget_exhausted_continue_session",
            )
        self.retry_pending.add(normalized)
        return CandidateFailureDecision(
            True,
            True,
            False,
            False,
            "candidate_recovery_deferred_same_session",
        )

    def record_verified_success(self, username: str) -> None:
        normalized = normalize_username(username)
        if normalized:
            self.retry_pending.discard(normalized)
            self.deferred_for_later_session.discard(normalized)
        if self.consecutive_candidate_failures:
            self.successful_resets += 1
        self.consecutive_candidate_failures = 0

    def begin_retry_generation(
        self,
        remaining_usernames: Iterable[str],
        attempted_in_generation: Iterable[str],
    ) -> set[str]:
        remaining = {
            item
            for item in (normalize_username(value) for value in remaining_usernames)
            if item
        }
        attempted = {
            item
            for item in (normalize_username(value) for value in attempted_in_generation)
            if item
        }
        # Never interrupt a generation that still has a first-pass candidate.
        if remaining - attempted:
            return set()
        retryable = (
            remaining
            & self.retry_pending
            - self.deferred_for_later_session
        )
        if not retryable:
            return set()
        self.retry_pending.difference_update(retryable)
        self.retry_generations_started += 1
        return retryable

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate_recovery_limit": int(self.candidate_recovery_limit),
            "candidate_failure_counts": dict(self.candidate_failure_counts),
            "candidate_retry_pending_count": len(self.retry_pending),
            "candidate_deferred_count": len(self.deferred_for_later_session),
            "candidate_deferred_usernames": sorted(self.deferred_for_later_session),
            "session_consecutive_candidate_failures": self.consecutive_candidate_failures,
            "session_consecutive_failure_limit": int(
                self.session_consecutive_failure_limit
            ),
            "total_candidate_failures": self.total_candidate_failures,
            "successful_recovery_counter_resets": self.successful_resets,
            "direct_search_retry_generations_started": self.retry_generations_started,
        }


def healthy_session_target(eligible_count: int, effective_session_cap: int) -> int:
    """Return the authoritative action target for one healthy session."""
    return min(max(0, int(eligible_count)), max(0, int(effective_session_cap)))
