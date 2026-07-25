"""Pure, shared Instagram list-continuation contract.

This module deliberately knows nothing about Follow, Unfollow or Welcome-DM
actions.  Flow adapters provide already-redacted row identifiers and surface
signals; the classifier only decides whether the primary list can continue.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import statistics
from typing import Iterable, Sequence


class InstagramListContinuationState(str, Enum):
    PRIMARY_ROWS_AVAILABLE = "PRIMARY_ROWS_AVAILABLE"
    EXPAND_PRIMARY_LIST_AVAILABLE = "EXPAND_PRIMARY_LIST_AVAILABLE"
    SUGGESTIONS_BOUNDARY_CONFIRMED = "SUGGESTIONS_BOUNDARY_CONFIRMED"
    NO_PROGRESS = "NO_PROGRESS"
    AMBIGUOUS_SURFACE = "AMBIGUOUS_SURFACE"


@dataclass(frozen=True)
class InstagramListContinuationSignals:
    flow: str
    expected_surface_selected: bool
    primary_row_ids: tuple[str, ...] = ()
    processed_primary_row_ids: tuple[str, ...] = ()
    see_more_visible: bool = False
    see_more_actionable: bool = False
    see_more_before_suggestions: bool = False
    suggestions_visible: bool = False
    loading: bool = False
    scroll_attempted: bool = False
    viewport_fingerprint_before: str = ""
    viewport_fingerprint_after: str = ""
    overlap_count: int = 0
    scroll_excessive: bool = False
    continuation_probe_count: int = 0


@dataclass(frozen=True)
class ViewportContinuity:
    fingerprint_before: str
    fingerprint_after: str
    overlap_count: int
    new_row_count: int
    unchanged: bool
    excessive: bool
    continuity_proved: bool
    reason: str


def _normalize_row_ids(row_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        value
        for value in (
            str(raw or "").strip().lstrip("@").casefold() for raw in row_ids
        )
        if value
    )


def viewport_fingerprint(row_ids: Iterable[str]) -> str:
    """Return a stable, non-reversible fingerprint; never log raw usernames."""
    normalized = _normalize_row_ids(row_ids)
    if not normalized:
        return ""
    return hashlib.sha256("\x1f".join(normalized).encode("utf-8")).hexdigest()[:20]


def compare_instagram_list_viewports(
    before_row_ids: Sequence[str],
    after_row_ids: Sequence[str],
    *,
    require_overlap: bool = True,
) -> ViewportContinuity:
    before = _normalize_row_ids(before_row_ids)
    after = _normalize_row_ids(after_row_ids)
    before_set = set(before)
    after_set = set(after)
    # A forward viewport is adjacent only when a suffix of the previous page is
    # the prefix of the next one.  A username reappearing elsewhere is not
    # enough to prove that no intermediate candidate page was skipped.
    overlap = 0
    for candidate_count in range(min(len(before), len(after)), 0, -1):
        if before[-candidate_count:] == after[:candidate_count]:
            overlap = candidate_count
            break
    new_rows = len(after_set.difference(before_set))
    before_fp = viewport_fingerprint(before)
    after_fp = viewport_fingerprint(after)
    unchanged = bool(before_fp and before_fp == after_fp)
    # An ordinary page advance must retain a positional edge anchor.  Losing it
    # while both viewports contain rows is treated as an excessive jump.
    excessive = bool(require_overlap and before and after and overlap == 0)
    continuity_proved = bool(after and new_rows > 0 and (overlap > 0 or not require_overlap))
    if unchanged:
        reason = "viewport_unchanged"
    elif excessive:
        reason = "no_viewport_overlap"
    elif continuity_proved:
        reason = "overlap_and_new_rows"
    elif not after:
        reason = "no_primary_rows_after_scroll"
    else:
        reason = "movement_not_proved"
    return ViewportContinuity(
        fingerprint_before=before_fp,
        fingerprint_after=after_fp,
        overlap_count=overlap,
        new_row_count=new_rows,
        unchanged=unchanged,
        excessive=excessive,
        continuity_proved=continuity_proved,
        reason=reason,
    )


def canonical_follow_scroll_geometry(width: int, height: int) -> dict[str, int | float]:
    """Viewport-relative single-page gesture, clear of top/bottom system bars."""
    w = max(1, int(width or 0))
    h = max(1, int(height or 0))
    start_ratio = 0.70
    end_ratio = 0.46
    return {
        "x": int(w * 0.50),
        "y_start": int(h * start_ratio),
        "y_end": int(h * end_ratio),
        "distance_px": int(h * (start_ratio - end_ratio)),
        "distance_ratio": round(start_ratio - end_ratio, 4),
        "duration_s": 0.32,
    }


def adaptive_follow_scroll_geometry(
    width: int,
    height: int,
    row_centers_y: Sequence[int],
    *,
    previous_actual_overlap: int | None = None,
    partial_row_count: int = 0,
) -> dict[str, int | float | bool]:
    """Compute one soft Follow gesture from the measured visible row cadence.

    The legacy 0.24-height gesture remains the deterministic fallback.  A normal
    traversal aims for seven new fully visible rows and one positional anchor.
    ``row_centers_y`` is deliberately the physical full-row cadence, not the
    de-duplicated identity list: truncated accessibility labels can otherwise
    make two distinct rows look identical and shorten the gesture.  A previous
    normal overlap above two adds at most half one measured row, while never
    exceeding the already validated 0.78 -> 0.22 safe band.
    """
    w = max(1, int(width or 0))
    h = max(1, int(height or 0))
    centers = sorted(
        {
            max(0, min(h - 1, int(value)))
            for value in row_centers_y
            if value is not None
        }
    )
    positive_gaps = [
        right - left
        for left, right in zip(centers, centers[1:])
        if right - left > 0
    ]
    short = canonical_follow_scroll_geometry(w, h)
    if len(centers) < 3 or not positive_gaps:
        return {
            **short,
            "adaptive": False,
            "visible_row_count": len(centers),
            "median_row_spacing_px": 0,
            "target_new_rows": 0,
            "target_overlap_rows": 0,
            "partial_row_count": max(0, int(partial_row_count or 0)),
            "adaptive_adjustment_px": 0,
            "adaptive_adjustment_reason": "insufficient_measured_rows",
            "fallback_reason": "insufficient_measured_rows",
        }

    median_spacing = max(1, int(round(float(statistics.median(positive_gaps)))))
    visible_count = len(centers)
    target_new_rows = min(7, max(1, visible_count - 1))
    target_overlap_rows = max(1, visible_count - target_new_rows)
    y_start = int(h * 0.78)
    safe_y_end = int(h * 0.22)
    min_distance = int(h * 0.24)
    max_distance = max(
        min_distance,
        min(int(h * 0.56), y_start - safe_y_end),
    )
    adaptive_adjustment_px = 0
    adaptive_adjustment_reason = "previous_overlap_not_available"
    if previous_actual_overlap is not None:
        previous_overlap = max(0, int(previous_actual_overlap))
        if previous_overlap > 2:
            adaptive_adjustment_px = min(
                max(1, median_spacing // 2),
                max(1, int(h * 0.04)),
            )
            adaptive_adjustment_reason = "previous_overlap_above_two_increase_bounded"
        elif previous_overlap in (1, 2):
            adaptive_adjustment_reason = "previous_overlap_safe_keep_distance"
        else:
            adaptive_adjustment_reason = "previous_zero_overlap_not_reusable"
    desired_distance = median_spacing * target_new_rows + adaptive_adjustment_px
    distance_px = max(min_distance, min(max_distance, desired_distance))
    y_end = max(safe_y_end, y_start - distance_px)
    distance_px = y_start - y_end
    distance_ratio = round(float(distance_px) / float(h), 4)
    # Longer gestures stay deliberately slow enough to remain a soft scroll.
    duration_s = round(min(0.46, max(0.32, 0.32 + (distance_ratio - 0.24) * 0.35)), 3)
    return {
        "x": int(w * 0.50),
        "y_start": y_start,
        "y_end": y_end,
        "distance_px": distance_px,
        "distance_ratio": distance_ratio,
        "duration_s": duration_s,
        "adaptive": True,
        "visible_row_count": visible_count,
        "median_row_spacing_px": median_spacing,
        "target_new_rows": target_new_rows,
        "target_overlap_rows": target_overlap_rows,
        "partial_row_count": max(0, int(partial_row_count or 0)),
        "adaptive_adjustment_px": adaptive_adjustment_px,
        "adaptive_adjustment_reason": adaptive_adjustment_reason,
        "fallback_reason": "",
    }


def classify_instagram_list_continuation(
    signals: InstagramListContinuationSignals,
) -> InstagramListContinuationState:
    """Classify list continuation using the business-safe priority order."""
    if not signals.expected_surface_selected or signals.loading:
        return InstagramListContinuationState.AMBIGUOUS_SURFACE
    if signals.scroll_excessive:
        return InstagramListContinuationState.AMBIGUOUS_SURFACE

    processed = set(_normalize_row_ids(signals.processed_primary_row_ids))
    visible = _normalize_row_ids(signals.primary_row_ids)
    if any(row_id not in processed for row_id in visible):
        return InstagramListContinuationState.PRIMARY_ROWS_AVAILABLE

    if (
        signals.see_more_visible
        and signals.see_more_actionable
        and signals.see_more_before_suggestions
    ):
        return InstagramListContinuationState.EXPAND_PRIMARY_LIST_AVAILABLE

    if (
        signals.scroll_attempted
        and signals.viewport_fingerprint_before
        and signals.viewport_fingerprint_before == signals.viewport_fingerprint_after
    ):
        return InstagramListContinuationState.NO_PROGRESS

    if (
        signals.suggestions_visible
        and not signals.see_more_visible
        and not visible
        and signals.continuation_probe_count >= 1
        and (not signals.scroll_attempted or signals.overlap_count >= 0)
    ):
        return InstagramListContinuationState.SUGGESTIONS_BOUNDARY_CONFIRMED

    return InstagramListContinuationState.AMBIGUOUS_SURFACE
