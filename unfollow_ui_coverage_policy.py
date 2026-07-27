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
UNFOLLOW_PRE_RECOVERY_STAGNATION_LIMIT = 3
UNFOLLOW_POST_RECOVERY_STAGNATION_LIMIT = 2
UNFOLLOW_PRE_RECOVERY_SEARCH_SCROLL_LIMIT = 10
UNFOLLOW_POST_RECOVERY_SEARCH_SCROLL_LIMIT = 5


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
    max_scroll_passes_absolute: int
    adaptive_scroll_budget: int
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
    scheduled_session_remaining_seconds: int
    recent_unique_usernames_per_viewport: float
    recent_candidates_found_per_viewport: float
    effective_candidate_yield_per_viewport: float
    observation_window_viewports: int
    budget_formula_version: str
    deadline_source: str
    recovery_reserve_seconds: int
    outreach_reserve_seconds: int
    navigation_reserve_seconds: int
    conservative_capacity: int
    planned_unfollows: int
    lightweight_deadline_check_interval_seconds: int
    lightweight_deadline_check_action_interval: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def derive_adaptive_coverage_budget(
    *,
    quota_remaining: int,
    eligible_remaining: int,
    session_remaining_seconds: float,
    recent_unique_usernames_per_viewport: float | None = None,
    recent_candidates_found_per_viewport: float | None = None,
    average_viewport_seconds: float | None = None,
    average_unfollow_seconds: float | None = None,
    deadline_source: str = "fallback",
    recovery_reserve_seconds: int = 75,
    outreach_reserve_seconds: int = 0,
    navigation_reserve_seconds: int = 30,
    lightweight_deadline_check_interval_seconds: int = 5 * 60,
    lightweight_deadline_check_action_interval: int = 25,
) -> AdaptiveCoverageBudget:
    """Build the immutable Unfollow phase budget at the phase handoff.

    ``session_remaining_seconds`` is measured to the scheduled session end.  The
    mandatory T-10 cleanup reserve is subtracted exactly once here.  Capacity
    is time-bounded; the policy never requires enough time for every eligible
    action before permitting the first scroll.
    """
    quota = max(0, int(quota_remaining))
    eligible = max(0, int(eligible_remaining))
    session_seconds = max(0, int(session_remaining_seconds))
    available = max(0, session_seconds - SCHEDULED_SESSION_CLEANUP_RESERVE_SECONDS)
    recovery_reserve = max(0, int(recovery_reserve_seconds))
    outreach_reserve = max(0, int(outreach_reserve_seconds))
    navigation_reserve = max(0, int(navigation_reserve_seconds))
    viewport_seconds = max(
        1,
        int(math.ceil(average_viewport_seconds or HISTORICAL_VIEWPORT_P90_SECONDS)),
    )
    action_seconds_per_unfollow = max(
        1,
        int(math.ceil(average_unfollow_seconds or HISTORICAL_ACTION_P90_SECONDS)),
    )
    action_equivalent_viewports = max(
        1,
        int(math.ceil(action_seconds_per_unfollow / viewport_seconds)),
    )
    bounded_observation_allowance = min(
        action_equivalent_viewports,
        max(1, eligible),
    )
    observation_window = action_equivalent_viewports
    recent_unique_yield = max(0.0, float(recent_unique_usernames_per_viewport or 0.0))
    recent_candidate_yield = max(0.0, float(recent_candidates_found_per_viewport or 0.0))
    # Candidate positions are unknown.  Before a positive match yield exists,
    # bootstrap from the smallest measurable progress rate: one candidate over
    # the evidence-derived observation window, never candidates/visible rows.
    bootstrap_candidate_yield = 1.0 / max(1, observation_window)
    username_discovery_ratio = (
        min(1.0, recent_unique_yield / HISTORICAL_ROWS_PER_VIEWPORT)
        if recent_unique_yield > 0
        else 1.0
    )
    effective_candidate_yield = max(
        bootstrap_candidate_yield,
        recent_candidate_yield * username_discovery_ratio,
    )
    required_viewports = (
        int(math.ceil(eligible / effective_candidate_yield)) if eligible else 0
    )
    capacity_seconds = max(
        0,
        available - recovery_reserve - outreach_reserve - navigation_reserve,
    )
    conservative_capacity = capacity_seconds // action_seconds_per_unfollow
    action_slots = min(quota, eligible, conservative_capacity)
    coverage_viewports_budget = required_viewports + bounded_observation_allowance
    coverage_seconds = coverage_viewports_budget * viewport_seconds
    recovery_budget_seconds = recovery_reserve
    # Recovery is a terminal safety reserve, not executable Unfollow time.
    # Keep it outside the phase hard-stop just like the optional Outreach
    # reserve; navigation remains part of the phase itself.
    max_phase_seconds = max(0, available - recovery_reserve - outreach_reserve)
    seconds_left_for_coverage = max(0, capacity_seconds)
    max_scroll_passes_absolute = max(
        0,
        seconds_left_for_coverage // viewport_seconds,
    )
    time_bounded_viewports = seconds_left_for_coverage // viewport_seconds
    max_scroll_passes = max(
        0,
        min(
            max_scroll_passes_absolute,
            max(0, coverage_viewports_budget - 1),
            int(time_bounded_viewports),
        ),
    )
    return AdaptiveCoverageBudget(
        max_scroll_passes=max_scroll_passes,
        max_scroll_passes_absolute=max_scroll_passes_absolute,
        adaptive_scroll_budget=max_scroll_passes,
        max_unfollow_phase_duration_seconds=max_phase_seconds,
        max_consecutive_no_progress_viewports=bounded_observation_allowance,
        max_repeated_fingerprints=bounded_observation_allowance,
        max_viewport_recoveries=bounded_observation_allowance,
        minimum_session_cleanup_reserve_seconds=SCHEDULED_SESSION_CLEANUP_RESERVE_SECONDS,
        estimated_seconds_per_unfollow=action_seconds_per_unfollow,
        estimated_seconds_per_viewport=viewport_seconds,
        historical_rows_per_viewport=HISTORICAL_ROWS_PER_VIEWPORT,
        action_slots=action_slots,
        required_viewports=required_viewports,
        diagnostic_viewport_allowance=bounded_observation_allowance,
        recovery_budget_seconds=recovery_budget_seconds,
        available_business_seconds=available,
        scheduled_session_remaining_seconds=session_seconds,
        recent_unique_usernames_per_viewport=round(recent_unique_yield, 4),
        recent_candidates_found_per_viewport=round(recent_candidate_yield, 4),
        effective_candidate_yield_per_viewport=round(effective_candidate_yield, 4),
        observation_window_viewports=observation_window,
        budget_formula_version="handoff_capacity_v3",
        deadline_source=str(deadline_source or "fallback"),
        recovery_reserve_seconds=recovery_reserve,
        outreach_reserve_seconds=outreach_reserve,
        navigation_reserve_seconds=navigation_reserve,
        conservative_capacity=int(conservative_capacity),
        planned_unfollows=int(action_slots),
        lightweight_deadline_check_interval_seconds=max(
            1, int(lightweight_deadline_check_interval_seconds)
        ),
        lightweight_deadline_check_action_interval=max(
            1, int(lightweight_deadline_check_action_interval)
        ),
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
    generation_fingerprint_counts: dict[str, int] = field(default_factory=dict)
    last_generation_fingerprint: str = ""
    viewports_observed: int = 0
    scroll_passes_used: int = 0
    consecutive_no_progress_viewports: int = 0
    repeated_fingerprints_count: int = 0
    consecutive_stagnation_count: int = 0
    navigation_generation: int = 1
    navigation_generation_reason: str = "following_list_opened"
    viewport_recoveries_used: int = 0
    no_motion_scrolls: int = 0
    terminal_fingerprint: str = ""
    stop_reason: str = ""
    total_rows_observed: int = 0
    viewport_new_username_counts: list[int] = field(default_factory=list)
    viewport_candidate_match_counts: list[int] = field(default_factory=list)
    last_progress_at: float | None = None
    initial_eligible_count: int = 0
    quota_remaining_at_start: int = 0
    initial_max_scroll_passes_absolute: int = 0
    lightweight_deadline_checks: int = 0
    last_deadline_check_elapsed_seconds: float | None = None
    last_deadline_check_verified_count: int = 0
    attempted_usernames: set[str] = field(default_factory=set)
    persisted_usernames: set[str] = field(default_factory=set)
    unavailable_usernames: set[str] = field(default_factory=set)
    last_candidate_attempted: str = ""
    last_safe_checkpoint: str = "following_list_opened"
    progress_credit_pending: bool = False
    generation_progress_events: int = 0
    pre_recovery_stagnation_count: int = 0
    post_recovery_stagnation_count: int = 0
    recovery_attempted: bool = False
    recovery_succeeded: bool = False
    post_recovery_observation_active: bool = False
    reset_reason: str = "following_list_opened"
    search_scroll_count: int = 0
    pre_recovery_search_scroll_count: int = 0
    post_recovery_search_scroll_count: int = 0
    total_search_scroll_count: int = 0
    search_recovery_attempted: bool = False
    search_recovery_succeeded: bool = False
    search_post_recovery_active: bool = False
    search_scroll_observation_pending: bool = False
    pending_recovery_reason: str = ""
    repeated_viewport_stagnation_count: int = 0

    def __post_init__(self) -> None:
        self.planned_usernames = {
            normalized
            for normalized in (normalize_username(value) for value in self.planned_usernames)
            if normalized
        }
        self.quota_target = max(0, int(self.quota_target))
        self.initial_eligible_count = len(self.planned_usernames)
        self.quota_remaining_at_start = self.quota_target
        self.initial_max_scroll_passes_absolute = self.budget.max_scroll_passes_absolute

    @property
    def remaining_planned_usernames(self) -> set[str]:
        return self.planned_usernames - self.verified_usernames

    def _stop(self, reason: str, fingerprint: str = "") -> CoverageDecision:
        self.stop_reason = reason
        if fingerprint:
            self.terminal_fingerprint = fingerprint
        return CoverageDecision("stop", reason, fingerprint)

    def begin_navigation_generation(
        self,
        reason: str,
        *,
        progress_proved: bool,
        safe_checkpoint: str | None = None,
    ) -> None:
        """Start a bounded navigation generation after a real state boundary.

        Phase-wide fingerprints remain telemetry only.  Stop decisions use the
        consecutive stagnation observed inside the current generation.
        """
        self.navigation_generation += 1
        self.navigation_generation_reason = str(reason or "navigation_changed")
        self.generation_fingerprint_counts.clear()
        self.last_generation_fingerprint = ""
        if progress_proved:
            self._reset_stagnation(str(reason or "navigation_progressed"))
            self.progress_credit_pending = True
            self.generation_progress_events += 1
        if safe_checkpoint:
            self.last_safe_checkpoint = str(safe_checkpoint)

    def _reset_stagnation(self, reason: str) -> None:
        self.consecutive_stagnation_count = 0
        self.repeated_viewport_stagnation_count = 0
        self.consecutive_no_progress_viewports = 0
        self.pre_recovery_stagnation_count = 0
        self.post_recovery_stagnation_count = 0
        self.post_recovery_observation_active = False
        self.reset_reason = str(reason or "progress_proved")

    def _reset_search(self, reason: str) -> None:
        self.search_scroll_count = 0
        self.pre_recovery_search_scroll_count = 0
        self.post_recovery_search_scroll_count = 0
        self.total_search_scroll_count = 0
        self.search_recovery_attempted = False
        self.search_recovery_succeeded = False
        self.search_post_recovery_active = False
        self.search_scroll_observation_pending = False
        self.pending_recovery_reason = ""
        self.reset_reason = str(reason or "search_progress_proved")

    def _deadline_check_due(self, *, elapsed_seconds: float) -> bool:
        if self.last_deadline_check_elapsed_seconds is None:
            return True
        elapsed_due = (
            elapsed_seconds - self.last_deadline_check_elapsed_seconds
            >= self.budget.lightweight_deadline_check_interval_seconds
        )
        action_due = (
            len(self.verified_usernames) - self.last_deadline_check_verified_count
            >= self.budget.lightweight_deadline_check_action_interval
        )
        return elapsed_due or action_due

    def preflight_decision(self, *, elapsed_seconds: float) -> Optional[CoverageDecision]:
        if len(self.verified_usernames) >= self.quota_target:
            return self._stop("unfollow_quota_reached")
        if not self.remaining_planned_usernames:
            return self._stop("eligible_targets_exhausted")
        # This is intentionally only a monotonic comparison.  The complete
        # candidate/quota/capacity plan remains immutable after handoff.
        if elapsed_seconds >= self.budget.max_unfollow_phase_duration_seconds:
            self.lightweight_deadline_checks += 1
            return self._stop("session_time_budget_exhausted")
        if self._deadline_check_due(elapsed_seconds=elapsed_seconds):
            self.lightweight_deadline_checks += 1
            self.last_deadline_check_elapsed_seconds = elapsed_seconds
            self.last_deadline_check_verified_count = len(self.verified_usernames)
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
        self.total_rows_observed += len(normalized)
        fingerprint = viewport_fingerprint(
            normalized,
            surface_signature=surface_signature,
        )
        self.terminal_fingerprint = fingerprint
        self.viewports_observed += 1
        phase_previous_count = self.fingerprint_counts.get(fingerprint, 0)
        self.fingerprint_counts[fingerprint] = phase_previous_count + 1
        if phase_previous_count:
            self.repeated_fingerprints_count += 1
        generation_previous_count = self.generation_fingerprint_counts.get(fingerprint, 0)
        self.generation_fingerprint_counts[fingerprint] = generation_previous_count + 1
        repeated_consecutively = fingerprint == self.last_generation_fingerprint
        self.last_generation_fingerprint = fingerprint

        current = set(normalized)
        new_usernames = current - self.observed_usernames
        self.observed_usernames.update(current)
        matches = current.intersection(self.remaining_planned_usernames)
        exploitable_candidate_present = bool(matches)
        progressive_search_viewport = bool(
            self.search_scroll_observation_pending
            and new_usernames
            and not exploitable_candidate_present
        )
        self.search_scroll_observation_pending = False
        if exploitable_candidate_present:
            self._reset_search("candidate_exploitable")
        elif progressive_search_viewport:
            self.total_search_scroll_count += 1
            if self.search_post_recovery_active:
                self.post_recovery_search_scroll_count += 1
                self.search_scroll_count = self.post_recovery_search_scroll_count
            else:
                self.pre_recovery_search_scroll_count += 1
                self.search_scroll_count = self.pre_recovery_search_scroll_count
        progress_credit = self.progress_credit_pending
        self.progress_credit_pending = False
        if new_usernames:
            was_post_recovery = self.post_recovery_observation_active
            self._reset_stagnation("new_usernames_visible")
            if was_post_recovery:
                self.recovery_succeeded = True
            self.last_progress_at = elapsed_seconds
            self.generation_progress_events += 1
        elif progress_credit or exploitable_candidate_present:
            # The first identical viewport after a verified+persistent action
            # and a safe profile return is expected.  Likewise, a remaining
            # planned candidate on that viewport is actionable, not stagnation.
            was_post_recovery = self.post_recovery_observation_active
            self._reset_stagnation(
                "next_candidate_exploitable"
                if exploitable_candidate_present
                else "verified_action_progress_credit"
            )
            if was_post_recovery:
                self.recovery_succeeded = True
        else:
            self.consecutive_no_progress_viewports += 1
            if self.post_recovery_observation_active:
                self.post_recovery_stagnation_count += 1
                self.consecutive_stagnation_count = self.post_recovery_stagnation_count
                self.repeated_viewport_stagnation_count = self.post_recovery_stagnation_count
            elif generation_previous_count and repeated_consecutively:
                self.pre_recovery_stagnation_count += 1
                self.consecutive_stagnation_count = self.pre_recovery_stagnation_count
                self.repeated_viewport_stagnation_count = self.pre_recovery_stagnation_count
            else:
                # A changed fingerprint is not progress by itself.  Progress
                # must be proved by one of the explicit reset signals above
                # (new usernames, an exploitable candidate, an action credit,
                # cursor/scroll movement, or a successful recovery).
                self.pre_recovery_stagnation_count += 1
                self.consecutive_stagnation_count = self.pre_recovery_stagnation_count
                self.repeated_viewport_stagnation_count = self.pre_recovery_stagnation_count

        self.viewport_new_username_counts.append(len(new_usernames))
        self.viewport_candidate_match_counts.append(len(matches))
        if end_of_list:
            if self.remaining_planned_usernames:
                return self._stop("ui_end_of_list_with_candidates_unresolved", fingerprint)
            return self._stop("eligible_targets_exhausted", fingerprint)
        if (
            self.post_recovery_observation_active
            and self.post_recovery_stagnation_count >= UNFOLLOW_POST_RECOVERY_STAGNATION_LIMIT
        ):
            return self._stop("ui_repeated_viewport_limit_after_recovery", fingerprint)
        if (
            self.search_post_recovery_active
            and self.post_recovery_search_scroll_count
            >= UNFOLLOW_POST_RECOVERY_SEARCH_SCROLL_LIMIT
        ):
            return self._stop("ui_progressive_search_limit_after_recovery", fingerprint)
        if (
            not self.search_recovery_attempted
            and self.pre_recovery_search_scroll_count
            >= UNFOLLOW_PRE_RECOVERY_SEARCH_SCROLL_LIMIT
        ):
            self.search_recovery_attempted = True
            self.pending_recovery_reason = "ui_progressive_search_limit"
            return CoverageDecision(
                "recover",
                "ui_progressive_search_limit",
                fingerprint,
                len(new_usernames),
            )
        if self.pre_recovery_stagnation_count >= UNFOLLOW_PRE_RECOVERY_STAGNATION_LIMIT:
            if self.viewport_recoveries_used < self.budget.max_viewport_recoveries:
                return CoverageDecision("recover", "ui_repeated_viewport_limit", fingerprint)
            return self._stop("ui_recovery_budget_exhausted", fingerprint)
        return CoverageDecision(
            "act" if matches else "scroll",
            fingerprint=fingerprint,
            new_usernames_count=len(new_usernames),
        )

    def mark_scroll(self, *, moved: bool) -> Optional[CoverageDecision]:
        self.scroll_passes_used += 1
        if not moved:
            self.no_motion_scrolls += 1
            self.search_scroll_observation_pending = False
        else:
            self.search_scroll_observation_pending = True
            self.begin_navigation_generation(
                "scroll_progressed",
                progress_proved=True,
                safe_checkpoint="following_list_after_scroll",
            )
        if self.scroll_passes_used > self.budget.max_scroll_passes:
            return self._stop("ui_coverage_budget_exhausted", self.terminal_fingerprint)
        return None

    def scroll_budget_decision(self) -> Optional[CoverageDecision]:
        if self.scroll_passes_used >= self.budget.max_scroll_passes:
            return self._stop("ui_coverage_budget_exhausted", self.terminal_fingerprint)
        return None

    def mark_recovery(
        self,
        *,
        succeeded: bool,
        progress_proved: bool = False,
    ) -> Optional[CoverageDecision]:
        self.viewport_recoveries_used += 1
        self.recovery_attempted = True
        recovery_reason = self.pending_recovery_reason
        self.pending_recovery_reason = ""
        if not succeeded or self.viewport_recoveries_used > self.budget.max_viewport_recoveries:
            self.recovery_succeeded = False
            if recovery_reason == "ui_progressive_search_limit":
                self.search_recovery_succeeded = False
            return self._stop("ui_recovery_budget_exhausted", self.terminal_fingerprint)
        self.navigation_generation += 1
        self.navigation_generation_reason = "viewport_recovery_completed"
        self.generation_fingerprint_counts.clear()
        self.last_generation_fingerprint = ""
        self.pre_recovery_stagnation_count = 0
        self.post_recovery_stagnation_count = 0
        self.consecutive_stagnation_count = 0
        self.repeated_viewport_stagnation_count = 0
        self.consecutive_no_progress_viewports = 0
        self.post_recovery_observation_active = not progress_proved
        self.recovery_succeeded = bool(progress_proved)
        self.reset_reason = (
            "recovery_progress_proved"
            if progress_proved
            else "recovery_completed_awaiting_progress"
        )
        self.last_safe_checkpoint = "following_list_after_recovery"
        if recovery_reason == "ui_progressive_search_limit":
            self.search_recovery_succeeded = True
            self.search_post_recovery_active = True
            self.search_scroll_count = 0
            self.post_recovery_search_scroll_count = 0
        return None

    def mark_action_attempted(self, username: str) -> None:
        normalized = normalize_username(username)
        if not normalized or normalized not in self.planned_usernames:
            raise ValueError("unplanned_unfollow_attempt")
        self.attempted_usernames.add(normalized)
        self.last_candidate_attempted = normalized

    def mark_action_verified(self, username: str) -> None:
        normalized = normalize_username(username)
        if not normalized or normalized not in self.remaining_planned_usernames:
            raise ValueError("duplicate_or_unplanned_unfollow")
        self.verified_usernames.add(normalized)
        self._reset_search("unfollow_verified")
        self._reset_stagnation("unfollow_verified")
        self.progress_credit_pending = True
        self.generation_progress_events += 1

    def mark_action_persisted(self, username: str) -> None:
        normalized = normalize_username(username)
        if not normalized or normalized not in self.verified_usernames:
            raise ValueError("unverified_unfollow_persistence")
        self.persisted_usernames.add(normalized)
        self._reset_search("unfollow_persisted")
        self._reset_stagnation("unfollow_persisted")
        self.progress_credit_pending = True
        self.generation_progress_events += 1

    def mark_candidate_unavailable(self, username: str) -> None:
        normalized = normalize_username(username)
        if normalized and normalized in self.planned_usernames:
            self.unavailable_usernames.add(normalized)

    def mark_safe_profile_return(self, username: str) -> None:
        normalized = normalize_username(username)
        if normalized not in self.persisted_usernames:
            raise ValueError("unpersisted_unfollow_safe_return")
        self.begin_navigation_generation(
            "profile_return_restored",
            progress_proved=True,
            safe_checkpoint=f"persisted:{normalized}",
        )

    def checkpoint(self) -> dict[str, Any]:
        remaining = self.planned_usernames - self.persisted_usernames
        return {
            "schema": "UNFOLLOW_CHECKPOINT_V1",
            "planned_usernames": sorted(self.planned_usernames),
            "attempted_usernames": sorted(self.attempted_usernames),
            "verified_usernames": sorted(self.verified_usernames),
            "persisted_usernames": sorted(self.persisted_usernames),
            "unavailable_usernames": sorted(self.unavailable_usernames),
            "remaining_usernames": sorted(remaining),
            "navigation_generation": self.navigation_generation,
            "navigation_generation_reason": self.navigation_generation_reason,
            "viewport_fingerprint": self.terminal_fingerprint,
            "last_safe_checkpoint": self.last_safe_checkpoint,
            "search_scroll_count": self.search_scroll_count,
            "pre_recovery_search_scroll_count": self.pre_recovery_search_scroll_count,
            "post_recovery_search_scroll_count": self.post_recovery_search_scroll_count,
            "total_search_scroll_count": self.total_search_scroll_count,
            "search_recovery_attempted": self.search_recovery_attempted,
            "search_recovery_succeeded": self.search_recovery_succeeded,
            "repeated_viewport_stagnation_count": self.repeated_viewport_stagnation_count,
        }

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
            "eligible_db_at_start": self.initial_eligible_count,
            "eligible_db_remaining": len(self.remaining_planned_usernames),
            "effective_unfollow_limit": self.quota_target,
            "quota_remaining_at_start": self.quota_remaining_at_start,
            "unique_usernames_observed": len(self.observed_usernames),
            "total_rows_observed": self.total_rows_observed,
            "new_usernames_per_viewport": list(self.viewport_new_username_counts),
            "candidates_matched": sum(self.viewport_candidate_match_counts),
            "duplicate_candidates_skipped": max(0, sum(self.viewport_candidate_match_counts) - len(set(self.observed_usernames).intersection(self.planned_usernames))),
            "verified_unique_count": len(self.verified_usernames),
            "remaining_planned_count": len(self.remaining_planned_usernames),
            "scroll_passes_used": self.scroll_passes_used,
            "consecutive_no_progress_viewports": self.consecutive_no_progress_viewports,
            "repeated_fingerprints_count": self.repeated_fingerprints_count,
            "repeated_fingerprints": self.repeated_fingerprints_count,
            "no_progress_viewports": self.consecutive_no_progress_viewports,
            "consecutive_stagnation_count": self.consecutive_stagnation_count,
            "repeated_viewport_stagnation_count": self.repeated_viewport_stagnation_count,
            "pre_recovery_stagnation_count": self.pre_recovery_stagnation_count,
            "post_recovery_stagnation_count": self.post_recovery_stagnation_count,
            "recovery_attempted": self.recovery_attempted,
            "recovery_succeeded": self.recovery_succeeded,
            "reset_reason": self.reset_reason,
            "search_scroll_count": self.search_scroll_count,
            "pre_recovery_search_scroll_count": self.pre_recovery_search_scroll_count,
            "post_recovery_search_scroll_count": self.post_recovery_search_scroll_count,
            "total_search_scroll_count": self.total_search_scroll_count,
            "search_recovery_attempted": self.search_recovery_attempted,
            "search_recovery_succeeded": self.search_recovery_succeeded,
            "navigation_generation": self.navigation_generation,
            "navigation_generation_reason": self.navigation_generation_reason,
            "generation_progress_events": self.generation_progress_events,
            "generation_unique_fingerprints_count": len(self.generation_fingerprint_counts),
            "unique_fingerprints_count": len(self.fingerprint_counts),
            "viewport_recoveries_used": self.viewport_recoveries_used,
            "viewport_recoveries": self.viewport_recoveries_used,
            "no_motion_scrolls": self.no_motion_scrolls,
            "terminal_fingerprint": self.terminal_fingerprint,
            "coverage_stop_reason": self.stop_reason,
            "stop_reason": self.stop_reason,
            "last_progress_at_seconds": self.last_progress_at,
            "lightweight_deadline_checks": self.lightweight_deadline_checks,
            "per_scroll_full_recalculations": 0,
            "theoretical_max_loop_steps": theoretical_max_loop_steps,
            "last_safe_checkpoint": self.last_safe_checkpoint,
        }


_UNFOLLOW_CRITICAL_REASONS = frozenset(
    {
        "unsafe_marker_detected",
        "active_instagram_account_mismatch",
        "challenge_detected",
        "restriction_detected",
    }
)
_UNFOLLOW_INTERNAL_FAILURE_REASONS = frozenset(
    {
        "unfollow_persistence_failed",
        "unfollow_verify_failed",
        "return_to_following_list_failed",
        "unfollow_tap_failed",
    }
)


def build_unfollow_outcome(
    *,
    stable_reason: str,
    raw_candidate_count: int,
    eligible_candidate_count: int,
    planned_candidate_count: int,
    attempted_count: int,
    verified_count: int,
    persisted_count: int,
    tracker: FollowingCoverageTracker | None,
) -> dict[str, Any]:
    """Build the single canonical Unfollow terminal outcome."""
    reason = str(stable_reason or "unfollow_outcome_unknown")
    remaining_count = max(0, int(planned_candidate_count) - int(persisted_count))
    checkpoint = tracker.checkpoint() if tracker is not None else {}
    safe_checkpoint = str(checkpoint.get("last_safe_checkpoint") or "")

    if reason == "unfollow_quota_reached":
        phase_status = "quota_reached"
    elif reason in {"eligible_targets_exhausted", "no_more_following_rows"} or remaining_count == 0:
        phase_status = "candidates_exhausted"
    elif reason in _UNFOLLOW_CRITICAL_REASONS:
        phase_status = "blocked_critical"
    elif reason in _UNFOLLOW_INTERNAL_FAILURE_REASONS:
        phase_status = "failed_internal"
    elif remaining_count > 0 and safe_checkpoint:
        phase_status = "partial_resumable"
    else:
        phase_status = "partial_not_resumable"

    resume_recommended = phase_status == "partial_resumable"
    return {
        "phase_status": phase_status,
        "stable_reason": reason,
        "raw_candidate_count": max(0, int(raw_candidate_count)),
        "eligible_candidate_count": max(0, int(eligible_candidate_count)),
        "planned_candidate_count": max(0, int(planned_candidate_count)),
        "attempted_count": max(0, int(attempted_count)),
        "verified_count": max(0, int(verified_count)),
        "persisted_count": max(0, int(persisted_count)),
        "remaining_count": remaining_count,
        "last_safe_checkpoint": safe_checkpoint or None,
        "resume_recommended": resume_recommended,
        "resume_strategy": "recalculate_db_then_resume_remaining_plan" if resume_recommended else "none",
        "navigation_generation": int(tracker.navigation_generation) if tracker else 0,
        "recovery_attempts": int(tracker.viewport_recoveries_used) if tracker else 0,
        "consecutive_stagnation_count": int(tracker.consecutive_stagnation_count) if tracker else 0,
        "pre_recovery_stagnation_count": int(tracker.pre_recovery_stagnation_count) if tracker else 0,
        "post_recovery_stagnation_count": int(tracker.post_recovery_stagnation_count) if tracker else 0,
        "recovery_attempted": bool(tracker.recovery_attempted) if tracker else False,
        "recovery_succeeded": bool(tracker.recovery_succeeded) if tracker else False,
        "reset_reason": str(tracker.reset_reason) if tracker else "",
        "search_scroll_count": int(tracker.search_scroll_count) if tracker else 0,
        "pre_recovery_search_scroll_count": (
            int(tracker.pre_recovery_search_scroll_count) if tracker else 0
        ),
        "post_recovery_search_scroll_count": (
            int(tracker.post_recovery_search_scroll_count) if tracker else 0
        ),
        "total_search_scroll_count": (
            int(tracker.total_search_scroll_count) if tracker else 0
        ),
        "repeated_viewport_stagnation_count": (
            int(tracker.repeated_viewport_stagnation_count) if tracker else 0
        ),
        "search_recovery_attempted": (
            bool(tracker.search_recovery_attempted) if tracker else False
        ),
        "search_recovery_succeeded": (
            bool(tracker.search_recovery_succeeded) if tracker else False
        ),
        "checkpoint": checkpoint or None,
    }
