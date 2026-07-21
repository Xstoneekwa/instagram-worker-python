"""Pure, device-free policy for bounded Following-list coverage.

The runtime adapter owns navigation and Instagram UI operations.  This module
only calculates budgets and reduces observations into explicit decisions, so it
can be replayed offline without importing uiautomator2.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional


# Derived from 2026-05-23 historical Worker JSONL artifacts:
# 25 completed Unfollow iterations: p90=14.541 s -> conservative ceiling 15 s.
# 4 completed Following-list scrolls: p90=2.471 s -> conservative ceiling 3 s.
# The same artifacts consistently exposed 7 usernames per viewport.
HISTORICAL_ACTION_P90_SECONDS = 15
HISTORICAL_VIEWPORT_P90_SECONDS = 3
HISTORICAL_ROWS_PER_VIEWPORT = 7
HISTORICAL_ACTION_SAMPLE_COUNT = 25
HISTORICAL_VIEWPORT_SAMPLE_COUNT = 4

# The scheduler already defines business_action_deadline=session_end-10 minutes.
SCHEDULED_SESSION_CLEANUP_RESERVE_SECONDS = 10 * 60


def normalize_username(value: str) -> str:
    return str(value or "").strip().lstrip("@").lower()


def viewport_fingerprint(
    usernames: Iterable[str],
    *,
    surface_signature: str = "following_confirmed",
) -> str:
    """Return a stable, order-sensitive fingerprint without logging usernames."""
    normalized = [normalize_username(value) for value in usernames]
    material = "\x1f".join([str(surface_signature or ""), *[v for v in normalized if v]])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class AdaptiveCoverageBudget:
    max_scroll_passes: int
    max_unfollow_phase_duration_seconds: int
    max_consecutive_no_progress_viewports: int
    max_repeated_fingerprints: int
    max_viewport_recoveries: int
    minimum_session_cleanup_reserve_seconds: int
    estimated_seconds_per_unfollow: int
    estimated_seconds_per_viewport: int
    historical_rows_per_viewport: int
    action_slots: int
    required_viewports: int
    diagnostic_viewport_allowance: int
    recovery_budget_seconds: int
    available_business_seconds: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def derive_adaptive_coverage_budget(
    *,
    quota_remaining: int,
    eligible_remaining: int,
    session_remaining_seconds: float,
) -> AdaptiveCoverageBudget:
    """Derive every bound from measured timings, supply, quota and session time.

    ``session_remaining_seconds`` is measured to the scheduled session end.  The
    mandatory T-10 cleanup reserve is subtracted exactly once here.
    """
    quota = max(0, int(quota_remaining))
    eligible = max(0, int(eligible_remaining))
    session_seconds = max(0, int(session_remaining_seconds))
    available = max(0, session_seconds - SCHEDULED_SESSION_CLEANUP_RESERVE_SECONDS)
    action_slots = min(quota, eligible)
    required_viewports = (
        int(math.ceil(eligible / HISTORICAL_ROWS_PER_VIEWPORT)) if eligible else 0
    )
    action_equivalent_viewports = max(
        1,
        int(math.ceil(HISTORICAL_ACTION_P90_SECONDS / HISTORICAL_VIEWPORT_P90_SECONDS)),
    )
    bounded_observation_allowance = min(
        action_equivalent_viewports,
        max(1, required_viewports),
    )
    action_seconds = action_slots * HISTORICAL_ACTION_P90_SECONDS
    coverage_viewports_budget = required_viewports + bounded_observation_allowance
    coverage_seconds = coverage_viewports_budget * HISTORICAL_VIEWPORT_P90_SECONDS
    recovery_budget_seconds = (
        bounded_observation_allowance * HISTORICAL_ACTION_P90_SECONDS
    )
    derived_phase_seconds = action_seconds + coverage_seconds + recovery_budget_seconds
    max_phase_seconds = min(available, derived_phase_seconds)
    seconds_left_for_coverage = max(
        0,
        max_phase_seconds - action_seconds - recovery_budget_seconds,
    )
    time_bounded_viewports = seconds_left_for_coverage // HISTORICAL_VIEWPORT_P90_SECONDS
    max_scroll_passes = max(
        0,
        min(max(0, coverage_viewports_budget - 1), int(time_bounded_viewports)),
    )
    return AdaptiveCoverageBudget(
        max_scroll_passes=max_scroll_passes,
        max_unfollow_phase_duration_seconds=max_phase_seconds,
        max_consecutive_no_progress_viewports=bounded_observation_allowance,
        max_repeated_fingerprints=bounded_observation_allowance,
        max_viewport_recoveries=bounded_observation_allowance,
        minimum_session_cleanup_reserve_seconds=SCHEDULED_SESSION_CLEANUP_RESERVE_SECONDS,
        estimated_seconds_per_unfollow=HISTORICAL_ACTION_P90_SECONDS,
        estimated_seconds_per_viewport=HISTORICAL_VIEWPORT_P90_SECONDS,
        historical_rows_per_viewport=HISTORICAL_ROWS_PER_VIEWPORT,
        action_slots=action_slots,
        required_viewports=required_viewports,
        diagnostic_viewport_allowance=bounded_observation_allowance,
        recovery_budget_seconds=recovery_budget_seconds,
        available_business_seconds=available,
    )


@dataclass(frozen=True)
class CoverageDecision:
    action: str
    stop_reason: str = ""
    fingerprint: str = ""
    new_usernames_count: int = 0


@dataclass
class FollowingCoverageTracker:
    budget: AdaptiveCoverageBudget
    planned_usernames: set[str]
    quota_target: int
    observed_usernames: set[str] = field(default_factory=set)
    verified_usernames: set[str] = field(default_factory=set)
    fingerprint_counts: dict[str, int] = field(default_factory=dict)
    viewports_observed: int = 0
    scroll_passes_used: int = 0
    consecutive_no_progress_viewports: int = 0
    repeated_fingerprints_count: int = 0
    viewport_recoveries_used: int = 0
    no_motion_scrolls: int = 0
    terminal_fingerprint: str = ""
    stop_reason: str = ""

    def __post_init__(self) -> None:
        self.planned_usernames = {
            normalized
            for normalized in (normalize_username(value) for value in self.planned_usernames)
            if normalized
        }
        self.quota_target = max(0, int(self.quota_target))

    @property
    def remaining_planned_usernames(self) -> set[str]:
        return self.planned_usernames - self.verified_usernames

    def _stop(self, reason: str, fingerprint: str = "") -> CoverageDecision:
        self.stop_reason = reason
        if fingerprint:
            self.terminal_fingerprint = fingerprint
        return CoverageDecision("stop", reason, fingerprint)

    def preflight_decision(self, *, elapsed_seconds: float) -> Optional[CoverageDecision]:
        if len(self.verified_usernames) >= self.quota_target:
            return self._stop("unfollow_quota_reached")
        if not self.remaining_planned_usernames:
            return self._stop("eligible_targets_exhausted")
        if elapsed_seconds >= self.budget.max_unfollow_phase_duration_seconds:
            return self._stop("session_time_budget_exhausted")
        return None

    def observe_viewport(
        self,
        usernames: Iterable[str],
        *,
        elapsed_seconds: float,
        following_confirmed: bool,
        unsafe_marker: bool = False,
        end_of_list: bool = False,
        surface_signature: str = "following_confirmed",
    ) -> CoverageDecision:
        preflight = self.preflight_decision(elapsed_seconds=elapsed_seconds)
        if preflight is not None:
            return preflight
        if unsafe_marker:
            return self._stop("unsafe_marker_detected")
        if not following_confirmed:
            if self.viewport_recoveries_used >= self.budget.max_viewport_recoveries:
                return self._stop("ui_recovery_budget_exhausted")
            return CoverageDecision("recover")

        normalized = [normalize_username(value) for value in usernames]
        normalized = [value for value in normalized if value]
        fingerprint = viewport_fingerprint(
            normalized,
            surface_signature=surface_signature,
        )
        self.terminal_fingerprint = fingerprint
        self.viewports_observed += 1
        previous_count = self.fingerprint_counts.get(fingerprint, 0)
        self.fingerprint_counts[fingerprint] = previous_count + 1
        if previous_count:
            self.repeated_fingerprints_count += 1

        current = set(normalized)
        new_usernames = current - self.observed_usernames
        self.observed_usernames.update(current)
        if new_usernames:
            self.consecutive_no_progress_viewports = 0
        else:
            self.consecutive_no_progress_viewports += 1

        if end_of_list:
            if self.remaining_planned_usernames:
                return self._stop("ui_end_of_list_with_candidates_unresolved", fingerprint)
            return self._stop("eligible_targets_exhausted", fingerprint)
        if self.repeated_fingerprints_count >= self.budget.max_repeated_fingerprints:
            return self._stop("ui_repeated_viewport_limit", fingerprint)
        if (
            self.consecutive_no_progress_viewports
            >= self.budget.max_consecutive_no_progress_viewports
        ):
            return self._stop("ui_no_progress", fingerprint)
        matches = current.intersection(self.remaining_planned_usernames)
        return CoverageDecision(
            "act" if matches else "scroll",
            fingerprint=fingerprint,
            new_usernames_count=len(new_usernames),
        )

    def mark_scroll(self, *, moved: bool) -> Optional[CoverageDecision]:
        self.scroll_passes_used += 1
        if not moved:
            self.no_motion_scrolls += 1
        if self.scroll_passes_used > self.budget.max_scroll_passes:
            return self._stop("ui_coverage_budget_exhausted", self.terminal_fingerprint)
        return None

    def scroll_budget_decision(self) -> Optional[CoverageDecision]:
        if self.scroll_passes_used >= self.budget.max_scroll_passes:
            return self._stop("ui_coverage_budget_exhausted", self.terminal_fingerprint)
        return None

    def mark_recovery(self, *, succeeded: bool) -> Optional[CoverageDecision]:
        self.viewport_recoveries_used += 1
        if not succeeded or self.viewport_recoveries_used > self.budget.max_viewport_recoveries:
            return self._stop("ui_recovery_budget_exhausted", self.terminal_fingerprint)
        return None

    def mark_action_verified(self, username: str) -> None:
        normalized = normalize_username(username)
        if not normalized or normalized not in self.remaining_planned_usernames:
            raise ValueError("duplicate_or_unplanned_unfollow")
        self.verified_usernames.add(normalized)

    def summary(self) -> dict[str, Any]:
        theoretical_max_loop_steps = (
            self.budget.action_slots
            + self.budget.max_scroll_passes
            + self.budget.max_viewport_recoveries
            + max(
                self.budget.max_consecutive_no_progress_viewports,
                self.budget.max_repeated_fingerprints,
            )
            + 1
        )
        return {
            **self.budget.as_dict(),
            "viewports_observed": self.viewports_observed,
            "unique_usernames_observed": len(self.observed_usernames),
            "verified_unique_count": len(self.verified_usernames),
            "remaining_planned_count": len(self.remaining_planned_usernames),
            "scroll_passes_used": self.scroll_passes_used,
            "consecutive_no_progress_viewports": self.consecutive_no_progress_viewports,
            "repeated_fingerprints_count": self.repeated_fingerprints_count,
            "unique_fingerprints_count": len(self.fingerprint_counts),
            "viewport_recoveries_used": self.viewport_recoveries_used,
            "no_motion_scrolls": self.no_motion_scrolls,
            "terminal_fingerprint": self.terminal_fingerprint,
            "coverage_stop_reason": self.stop_reason,
            "theoretical_max_loop_steps": theoretical_max_loop_steps,
        }
