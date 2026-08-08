"""Offline proof closure for Follow60 Ordering V2.

This module is deliberately outside the runtime action path.  It reads existing
JSONL logs, reconstructs timing boundaries, validates synthetic re-entry
snapshots, and benchmarks CPU-only validation.  It never talks to a device,
Supabase, or the production dispatcher.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


DIRECT_GRID_SAFE = "DIRECT_GRID_SAFE"
COMPLETE = "completed"

STABLE_PROOF_REUSE: tuple[tuple[str, str, str], ...] = (
    ("account_id", "REUSE_SAFE", "immutable run binding"),
    ("request_id", "REUSE_SAFE", "immutable run binding"),
    ("run_id", "REUSE_SAFE", "immutable run binding"),
    ("business_session_id", "REUSE_SAFE", "immutable business session binding"),
    ("attempt_id", "REUSE_SAFE", "immutable attempt binding"),
    ("binding_kind", "REUSE_SAFE", "immutable mainline/canary binding"),
    ("worker_sha", "REUSE_SAFE", "immutable process lineage"),
    ("target_id", "REUSE_SAFE", "immutable source-target binding"),
    ("candidate_username", "REUSE_SAFE", "candidate identity expected after Back"),
    ("source_profile_username", "REUSE_SAFE", "immutable CT binding"),
    ("filter_verdict", "REUSE_SAFE", "business verdict cannot change during viewer roundtrip"),
    ("eligibility_verdict", "REUSE_SAFE", "business verdict cannot change during viewer roundtrip"),
    ("follow_budget_reservation", "REUSE_SAFE", "reservation is action-scoped"),
    ("public_private", "REUSE_SAFE", "short-lived business evidence; UI identity still refreshed"),
    ("posts_count_source", "REUSE_SAFE", "not required to authorize Follow after Back"),
    ("posts_tab_identity", "REUSE_SAFE", "not required to authorize Follow after Back"),
    ("viewer_provenance", "REUSE_SAFE", "binds the Back origin; never authorizes Follow alone"),
    ("v5_candidate_provenance", "REUSE_SAFE", "binds the opened Post to the candidate"),
    ("action_id", "REUSE_SAFE", "immutable cycle correlation id"),
)

VOLATILE_PROOF_REFRESH: tuple[tuple[str, str, str], ...] = (
    ("package", "REFRESH_REQUIRED", "must still be the assigned Instagram package"),
    ("activity", "REFRESH_REQUIRED", "must be InstagramMainActivity"),
    ("profile_surface", "REFRESH_REQUIRED", "Back invalidates viewer surface evidence"),
    ("candidate_visible_exact", "REFRESH_REQUIRED", "must equal bound candidate"),
    ("follow_cta_state", "REFRESH_REQUIRED", "must be exact Follow before the tap"),
    ("follow_cta_bounds", "REFRESH_REQUIRED", "all pre-Back bounds are stale"),
    ("overlay_absent", "REFRESH_REQUIRED", "challenge/interstitial can appear asynchronously"),
    ("navigation_generation", "REFRESH_REQUIRED", "Back creates a new UI generation"),
    ("ui_generation", "REFRESH_REQUIRED", "Back creates a new UI generation"),
)


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(str(value))


def _milliseconds(start: Mapping[str, Any], end: Mapping[str, Any]) -> float:
    return round(
        (_parse_timestamp(str(end["ts"])) - _parse_timestamp(str(start["ts"]))).total_seconds()
        * 1000.0,
        3,
    )


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(float(value) for value in values)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _candidate(event: Mapping[str, Any]) -> str:
    proof = event.get("proof") if isinstance(event.get("proof"), Mapping) else {}
    return str(
        event.get("candidate_username")
        or event.get("follower_username")
        or event.get("username")
        or proof.get("target_username")
        or ""
    ).strip().lstrip("@").lower()


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if isinstance(value, dict):
                value["_source_log"] = str(path)
                value["_source_line"] = line_number
                events.append(value)
    return events


@dataclass(frozen=True)
class RawAvoidableRow:
    run_id: str
    candidate_index: int
    candidate_username: str
    v1_path: str
    v1_cycle_ms: float
    postopen_tap_observed: bool
    raw_avoidable_ms: float | None
    pre_open_decision_ms: float | None
    reveal_ms: float
    golden_or_safe_pre_tap_ms: float
    source_evidence: str
    confidence: str
    impossibility_reason: str


@dataclass(frozen=True)
class ReentryRow:
    run_id: str
    candidate_username: str
    terminal_to_back_dispatch_ms: float
    back_dispatch_to_profile_ms: float
    profile_to_exact_identity_ms: float
    common_v1_reentry_total_ms: float


@dataclass(frozen=True)
class DirectActionRow:
    run_id: str
    candidate_username: str
    tap_to_v5_ms: float
    tap_to_candidate_exact_ms: float


def _events_by_name(events: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        grouped.setdefault(str(event.get("event") or ""), []).append(event)
    return grouped


def reconstruct_raw_avoidable_rows(events: Sequence[Mapping[str, Any]]) -> list[RawAvoidableRow]:
    """Reconstruct only work eliminated before the Post tap boundary.

    Viewer ACK, V5 and Like are excluded by construction: a successful path
    stops at ``post_open_intent_v2_consumed`` (logged immediately before the
    click).  A no-tap Golden failure stops at its explicit attempt terminal.
    """
    rows: list[RawAvoidableRow] = []
    active_candidate = ""
    active_events: list[Mapping[str, Any]] = []
    for event in events:
        name = str(event.get("event") or "")
        if name == "post_follow_post_likes_phase_started":
            active_candidate = _candidate(event)
            active_events = [event]
            continue
        if active_candidate:
            active_events.append(event)
        if name != "follow60_ordering_v2_shadow_terminal" or not active_candidate:
            continue

        terminal_candidate = _candidate(event)
        if terminal_candidate != active_candidate:
            raise ValueError(
                f"candidate boundary mismatch: {active_candidate} != {terminal_candidate}"
            )
        is_eligible = (
            event.get("event_status") == COMPLETE
            and isinstance(event.get("classification"), Mapping)
            and event["classification"].get("initial") == DIRECT_GRID_SAFE
        )
        if is_eligible:
            grouped = _events_by_name(active_events)
            starts = grouped.get("post_follow_post_likes_phase_started", [])
            intents = grouped.get("post_open_intent_v2_consumed", [])
            terminals = (
                grouped.get("follow_60s_post_grid_evidence_golden_direct_completed", [])
                or grouped.get("follow_60s_post_grid_evidence_safe_direct_completed", [])
            )
            start = starts[0] if starts else None
            boundary = intents[0] if intents else (terminals[-1] if terminals else None)
            if start is None or boundary is None:
                raw_ms = None
                source = "unavailable"
                confidence = "NONE"
                impossibility = "missing_stage_boundary"
            else:
                raw_ms = _milliseconds(start, boundary)
                source = (
                    "phase_start_to_post_open_intent_tap_boundary"
                    if intents
                    else "phase_start_to_explicit_no_tap_open_attempt_terminal"
                )
                confidence = "HIGH"
                impossibility = ""

            open_starts = grouped.get("post_follow_post_like_open_started", [])
            pre_open_ms = (
                _milliseconds(start, open_starts[0]) if start is not None and open_starts else None
            )
            reveal_starts = grouped.get("followers_scroll_or_swipe_about_to_run", [])
            reveal_ends = grouped.get("follow_60s_postgrid_after_reveal", [])
            reveal_ms = (
                _milliseconds(reveal_starts[0], reveal_ends[-1])
                if reveal_starts and reveal_ends
                else 0.0
            )
            fallbacks = grouped.get("follow_60s_post_grid_evidence_fallback_golden_direct", [])
            golden_ms = (
                _milliseconds(fallbacks[0], boundary)
                if fallbacks and boundary is not None
                else 0.0
            )
            actual = event.get("v1_actual_execution")
            path = str(actual.get("selected_path") if isinstance(actual, Mapping) else "unknown")
            opened_ns = int(event.get("opened_at_monotonic_ns") or 0)
            terminal_ns = int(event.get("terminal_at_monotonic_ns") or 0)
            if opened_ns <= 0 or terminal_ns <= opened_ns:
                raise ValueError(f"invalid cycle monotonic boundary for {terminal_candidate}")
            rows.append(
                RawAvoidableRow(
                    run_id=str(event.get("run_id") or ""),
                    candidate_index=int(event.get("candidate_index") or 0),
                    candidate_username=terminal_candidate,
                    v1_path=path,
                    v1_cycle_ms=round((terminal_ns - opened_ns) / 1_000_000.0, 3),
                    postopen_tap_observed=bool(
                        actual.get("postopen_tap_observed")
                        if isinstance(actual, Mapping)
                        else False
                    ),
                    raw_avoidable_ms=raw_ms,
                    pre_open_decision_ms=pre_open_ms,
                    reveal_ms=reveal_ms,
                    golden_or_safe_pre_tap_ms=golden_ms,
                    source_evidence=source,
                    confidence=confidence,
                    impossibility_reason=impossibility,
                )
            )
        active_candidate = ""
        active_events = []
    return rows


def reconstruct_direct_action_rows(events: Sequence[Mapping[str, Any]]) -> list[DirectActionRow]:
    """Measure the pipeline newly paid when V1 sent no Post tap."""
    rows: list[DirectActionRow] = []
    active_candidate = ""
    active_events: list[Mapping[str, Any]] = []
    for event in events:
        name = str(event.get("event") or "")
        if name == "post_follow_post_likes_phase_started":
            active_candidate = _candidate(event)
            active_events = [event]
            continue
        if active_candidate:
            active_events.append(event)
        if name != "follow60_ordering_v2_shadow_terminal" or not active_candidate:
            continue
        eligible = (
            event.get("event_status") == COMPLETE
            and isinstance(event.get("classification"), Mapping)
            and event["classification"].get("initial") == DIRECT_GRID_SAFE
        )
        if eligible:
            grouped = _events_by_name(active_events)
            intents = grouped.get("post_open_intent_v2_consumed", [])
            v5 = grouped.get("post_follow_open_like_proof_stashed", [])
            identity = [
                item
                for item in grouped.get("follow_60s_fresh_ui_proof_stashed", [])
                if item.get("purpose") == "return_candidate_profile"
            ]
            if intents and v5 and identity:
                rows.append(
                    DirectActionRow(
                        run_id=str(event.get("run_id") or ""),
                        candidate_username=active_candidate,
                        tap_to_v5_ms=_milliseconds(intents[0], v5[-1]),
                        tap_to_candidate_exact_ms=_milliseconds(intents[0], identity[-1]),
                    )
                )
        active_candidate = ""
        active_events = []
    return rows


def reconstruct_reentry_rows(events: Sequence[Mapping[str, Any]]) -> list[ReentryRow]:
    """Measure the already-paid V1 Post->candidate re-entry boundary."""
    rows: list[ReentryRow] = []
    active_candidate = ""
    active_events: list[Mapping[str, Any]] = []
    for event in events:
        name = str(event.get("event") or "")
        if name == "post_follow_post_likes_phase_started":
            active_candidate = _candidate(event)
            active_events = [event]
            continue
        if active_candidate:
            active_events.append(event)
        if name != "follow60_ordering_v2_shadow_terminal" or not active_candidate:
            continue
        eligible = (
            event.get("event_status") == COMPLETE
            and isinstance(event.get("classification"), Mapping)
            and event["classification"].get("initial") == DIRECT_GRID_SAFE
        )
        if eligible:
            grouped = _events_by_name(active_events)
            back_starts = grouped.get("visual_return_to_profile_started", [])
            profile_success = grouped.get("visual_return_to_profile_success", [])
            identity_proofs = [
                item
                for item in grouped.get("follow_60s_fresh_ui_proof_stashed", [])
                if item.get("purpose") == "return_candidate_profile"
            ]
            like_terminals = grouped.get("visual_post_like_verify_completed", [])
            if back_starts and profile_success and identity_proofs:
                terminal = (
                    like_terminals[-1]
                    if like_terminals
                    else (
                        grouped.get("post_follow_post_like_skipped_unusable_surface", [])
                        or grouped.get("post_open_surface_rejected", [])
                        or [back_starts[0]]
                    )[-1]
                )
                rows.append(
                    ReentryRow(
                        run_id=str(event.get("run_id") or ""),
                        candidate_username=active_candidate,
                        terminal_to_back_dispatch_ms=_milliseconds(terminal, back_starts[0]),
                        back_dispatch_to_profile_ms=_milliseconds(back_starts[0], profile_success[0]),
                        profile_to_exact_identity_ms=_milliseconds(profile_success[0], identity_proofs[-1]),
                        common_v1_reentry_total_ms=_milliseconds(terminal, identity_proofs[-1]),
                    )
                )
        active_candidate = ""
        active_events = []
    return rows


def measured_incremental_follow_context(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Pair existing V1 screen guard + exact Follow context costs per eligible candidate."""
    eligible = {
        _candidate(event)
        for event in events
        if event.get("event") == "follow60_ordering_v2_shadow_terminal"
        and event.get("event_status") == COMPLETE
        and isinstance(event.get("classification"), Mapping)
        and event["classification"].get("initial") == DIRECT_GRID_SAFE
    }
    values: dict[str, dict[str, float]] = {}
    for event in events:
        candidate = _candidate(event)
        if candidate not in eligible:
            continue
        if event.get("event") == "pre_follow_screen_guard_completed" and isinstance(
            event.get("duration_ms"), (int, float)
        ):
            values.setdefault(candidate, {})["screen_overlay_guard_ms"] = float(event["duration_ms"])
        if event.get("event") == "pre_follow_tap_ready" and isinstance(
            event.get("duration_ms"), (int, float)
        ):
            values.setdefault(candidate, {})["follow_tap_context_ms"] = float(event["duration_ms"])
    out: list[dict[str, Any]] = []
    for candidate in sorted(eligible):
        item = values.get(candidate, {})
        if "screen_overlay_guard_ms" not in item or "follow_tap_context_ms" not in item:
            continue
        out.append(
            {
                "candidate_username": candidate,
                **item,
                "incremental_v2_ms": round(
                    item["screen_overlay_guard_ms"] + item["follow_tap_context_ms"], 3
                ),
            }
        )
    return out


def summary(values: Sequence[float]) -> dict[str, float | int]:
    if not values:
        return {"n": 0}
    data = [float(value) for value in values]
    return {
        "n": len(data),
        "mean": round(statistics.mean(data), 3),
        "median": round(statistics.median(data), 3),
        "p90": round(_percentile(data, 0.90), 3),
        "p95": round(_percentile(data, 0.95), 3),
        "min": round(min(data), 3),
        "max": round(max(data), 3),
    }


def calculate_economic_projection(
    events: Sequence[Mapping[str, Any]],
    raw_rows: Sequence[RawAvoidableRow],
    direct_action_rows: Sequence[DirectActionRow],
    *,
    persistence_incremental_ms: float = 0.360,
) -> dict[str, Any]:
    """Build a paired V1/V2 projection without charging common work twice.

    V1 paths that already opened a Post already paid viewer, V5, Like and the
    return-to-candidate boundary.  V1 no-tap paths did not, so the future V2
    path is charged the measured tap-to-exact-candidate pipeline.  Non-eligible
    paths remain byte-for-byte V1 in the projection.
    """
    action_values = [row.tap_to_candidate_exact_ms for row in direct_action_rows]
    if not action_values:
        return {"n": 0, "reason": "direct_action_cost_unavailable"}
    action_median_ms = float(statistics.median(action_values))
    action_p95_ms = float(_percentile(action_values, 0.95))
    raw_by_key = {
        (row.run_id, row.candidate_index, row.candidate_username): row for row in raw_rows
    }
    eligible_standard: list[float] = []
    eligible_conservative: list[float] = []
    eligible_v1: list[float] = []
    standard_gains: list[float] = []
    conservative_gains: list[float] = []
    global_v1: list[float] = []
    global_standard: list[float] = []
    global_conservative: list[float] = []

    for event in events:
        if event.get("event") != "follow60_ordering_v2_shadow_terminal":
            continue
        if event.get("event_status") != COMPLETE:
            continue
        opened_ns = int(event.get("opened_at_monotonic_ns") or 0)
        terminal_ns = int(event.get("terminal_at_monotonic_ns") or 0)
        if opened_ns <= 0 or terminal_ns <= opened_ns:
            continue
        v1_ms = (terminal_ns - opened_ns) / 1_000_000.0
        candidate = _candidate(event)
        key = (str(event.get("run_id") or ""), int(event.get("candidate_index") or 0), candidate)
        row = raw_by_key.get(key)
        if row is None or row.raw_avoidable_ms is None:
            global_v1.append(v1_ms)
            global_standard.append(v1_ms)
            global_conservative.append(v1_ms)
            continue
        extra_standard_ms = 0.0 if row.postopen_tap_observed else action_median_ms
        extra_conservative_ms = 0.0 if row.postopen_tap_observed else action_p95_ms
        standard_ms = max(
            0.0,
            v1_ms - row.raw_avoidable_ms + extra_standard_ms + persistence_incremental_ms,
        )
        conservative_ms = max(
            0.0,
            v1_ms - row.raw_avoidable_ms + extra_conservative_ms + persistence_incremental_ms,
        )
        eligible_v1.append(v1_ms)
        eligible_standard.append(standard_ms)
        eligible_conservative.append(conservative_ms)
        standard_gains.append(v1_ms - standard_ms)
        conservative_gains.append(v1_ms - conservative_ms)
        global_v1.append(v1_ms)
        global_standard.append(standard_ms)
        global_conservative.append(conservative_ms)

    return {
        "eligible_n": len(eligible_v1),
        "global_n": len(global_v1),
        "eligibility_rate_percent": round(
            (len(eligible_v1) / len(global_v1) * 100.0) if global_v1 else 0.0, 3
        ),
        "new_action_pipeline_median_ms": round(action_median_ms, 3),
        "new_action_pipeline_p95_ms": round(action_p95_ms, 3),
        "persistence_incremental_ms": round(float(persistence_incremental_ms), 3),
        "eligible_v1_ms": summary(eligible_v1),
        "eligible_v2_standard_ms": summary(eligible_standard),
        "eligible_v2_conservative_ms": summary(eligible_conservative),
        "eligible_net_gain_standard_ms": summary(standard_gains),
        "eligible_net_gain_conservative_ms": summary(conservative_gains),
        "global_v1_ms": summary(global_v1),
        "global_v2_standard_ms": summary(global_standard),
        "global_v2_conservative_ms": summary(global_conservative),
        "global_gain_standard_ms": summary(
            [v1 - v2 for v1, v2 in zip(global_v1, global_standard)]
        ),
        "global_gain_conservative_ms": summary(
            [v1 - v2 for v1, v2 in zip(global_v1, global_conservative)]
        ),
    }


_EXACT_ACTIVITY = "com.instagram.mainactivity.InstagramMainActivity"


def validate_minimal_reentry_snapshot(
    snapshot: Mapping[str, Any],
    expected_binding: Mapping[str, Any],
    *,
    now_monotonic: float,
    max_age_ms: float = 1250.0,
) -> dict[str, Any]:
    """Pure fail-closed validator for a future one-capture re-entry proof."""
    stable_fields = (
        "account_id",
        "request_id",
        "run_id",
        "business_session_id",
        "attempt_id",
        "binding_kind",
        "worker_sha",
        "target_id",
        "candidate_username",
        "source_profile_username",
        "action_id",
    )
    for field_name in stable_fields:
        if str(snapshot.get(field_name) or "") != str(expected_binding.get(field_name) or ""):
            return {"ok": False, "reason": f"binding_mismatch:{field_name}"}
    if snapshot.get("package") != expected_binding.get("package"):
        return {"ok": False, "reason": "package_mismatch"}
    if snapshot.get("activity") != _EXACT_ACTIVITY:
        return {"ok": False, "reason": "activity_mismatch"}
    if snapshot.get("surface") != "candidate_profile_after_like":
        return {"ok": False, "reason": "profile_surface_not_proven"}
    if snapshot.get("candidate_visible_exact") is not True:
        return {"ok": False, "reason": "candidate_identity_not_exact"}
    if snapshot.get("follow_cta_state") != "follow_exact":
        return {"ok": False, "reason": "follow_cta_not_exact"}
    bounds = snapshot.get("follow_cta_bounds")
    if not isinstance(bounds, Mapping):
        return {"ok": False, "reason": "follow_cta_bounds_missing"}
    try:
        valid_bounds = (
            int(bounds["left"]) < int(bounds["right"])
            and int(bounds["top"]) < int(bounds["bottom"])
        )
    except (KeyError, TypeError, ValueError):
        valid_bounds = False
    if not valid_bounds:
        return {"ok": False, "reason": "follow_cta_bounds_invalid"}
    if snapshot.get("overlay_absent") is not True or snapshot.get("challenge_absent") is not True:
        return {"ok": False, "reason": "overlay_or_challenge_not_excluded"}
    if snapshot.get("navigation_generation") != snapshot.get("current_navigation_generation"):
        return {"ok": False, "reason": "navigation_generation_stale"}
    if snapshot.get("ui_generation") != snapshot.get("current_ui_generation"):
        return {"ok": False, "reason": "ui_generation_stale"}
    if snapshot.get("consumed") is True:
        return {"ok": False, "reason": "proof_already_consumed"}
    try:
        age_ms = max(0.0, (float(now_monotonic) - float(snapshot["created_at_monotonic"])) * 1000.0)
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "reason": "proof_timestamp_missing"}
    if age_ms > float(max_age_ms):
        return {"ok": False, "reason": "proof_stale", "proof_age_ms": round(age_ms, 3)}
    digest_payload = {
        field_name: snapshot.get(field_name)
        for field_name in stable_fields
    }
    digest_payload.update(
        {
            "package": snapshot.get("package"),
            "activity": snapshot.get("activity"),
            "bounds": dict(bounds),
            "navigation_generation": snapshot.get("navigation_generation"),
            "ui_generation": snapshot.get("ui_generation"),
        }
    )
    return {
        "ok": True,
        "reason": "minimal_reentry_proof_exact",
        "proof_age_ms": round(age_ms, 3),
        "proof_hash": hashlib.sha256(
            json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def benchmark_minimal_reentry_cpu(
    snapshot: Mapping[str, Any],
    expected_binding: Mapping[str, Any],
    *,
    iterations: int = 10000,
) -> dict[str, float | int]:
    samples: list[float] = []
    now = float(snapshot["created_at_monotonic"]) + 0.1
    for _index in range(max(1, int(iterations))):
        started = time.perf_counter_ns()
        result = validate_minimal_reentry_snapshot(
            snapshot,
            expected_binding,
            now_monotonic=now,
        )
        elapsed = time.perf_counter_ns() - started
        if result.get("ok") is not True:
            raise AssertionError(result)
        samples.append(float(elapsed))
    stats = summary(samples)
    stats["unit"] = "ns"
    return stats


def analyze_logs(paths: Sequence[str | Path]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for path in paths:
        events.extend(load_jsonl(path))
    raw_rows = reconstruct_raw_avoidable_rows(events)
    reentry_rows = reconstruct_reentry_rows(events)
    direct_action_rows = reconstruct_direct_action_rows(events)
    incremental_rows = measured_incremental_follow_context(events)
    raw_values = [row.raw_avoidable_ms for row in raw_rows if row.raw_avoidable_ms is not None]
    economic_projection = calculate_economic_projection(
        events,
        raw_rows,
        direct_action_rows,
    )
    return {
        "raw_avoidable_rows": [asdict(row) for row in raw_rows],
        "raw_avoidable_summary_ms": summary(raw_values),
        "raw_avoidable_total_eligible": len(raw_rows),
        "raw_avoidable_measurable": len(raw_values),
        "raw_avoidable_coverage_percent": round(
            (len(raw_values) / len(raw_rows) * 100.0) if raw_rows else 0.0, 3
        ),
        "reentry_rows": [asdict(row) for row in reentry_rows],
        "direct_action_rows": [asdict(row) for row in direct_action_rows],
        "direct_action_tap_to_v5_summary_ms": summary(
            [row.tap_to_v5_ms for row in direct_action_rows]
        ),
        "direct_action_tap_to_candidate_summary_ms": summary(
            [row.tap_to_candidate_exact_ms for row in direct_action_rows]
        ),
        "reentry_common_v1_summary_ms": summary(
            [row.common_v1_reentry_total_ms for row in reentry_rows]
        ),
        "reentry_terminal_to_back_summary_ms": summary(
            [row.terminal_to_back_dispatch_ms for row in reentry_rows]
        ),
        "reentry_back_to_profile_summary_ms": summary(
            [row.back_dispatch_to_profile_ms for row in reentry_rows]
        ),
        "reentry_profile_to_identity_summary_ms": summary(
            [row.profile_to_exact_identity_ms for row in reentry_rows]
        ),
        "incremental_follow_context_rows": incremental_rows,
        "incremental_follow_context_summary_ms": summary(
            [row["incremental_v2_ms"] for row in incremental_rows]
        ),
        "screen_overlay_guard_summary_ms": summary(
            [row["screen_overlay_guard_ms"] for row in incremental_rows]
        ),
        "follow_tap_context_summary_ms": summary(
            [row["follow_tap_context_ms"] for row in incremental_rows]
        ),
        "economic_projection": economic_projection,
        "stable_proof_reuse": STABLE_PROOF_REUSE,
        "volatile_proof_refresh": VOLATILE_PROOF_REFRESH,
        "behavior_changed": False,
        "ui_acquisition_count": 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Follow60 Ordering V2 proof closure")
    parser.add_argument("logs", nargs="+", help="Existing JSONL run logs")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    report = analyze_logs(args.logs)
    print(json.dumps(report, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
