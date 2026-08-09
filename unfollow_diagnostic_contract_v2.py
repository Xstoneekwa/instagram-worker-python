"""CPU-only Unfollow diagnostic contract V2.

This module never imports a device, navigation, XML, screenshot, accessibility,
or persistence helper.  Callers provide snapshots that already exist in the
Unfollow runtime.  The diagnostic therefore cannot change traversal or acquire
additional UI evidence.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable


VIEWPORT_EVENT = "UNFOLLOW_DIAGNOSTIC_V2_VIEWPORT_V2"
CANDIDATE_EVENT = "UNFOLLOW_CANDIDATE_LINEAGE_V2"
SESSION_START_EVENT = "UNFOLLOW_SESSION_DIAGNOSTIC_START_V1"
SESSION_TERMINAL_EVENT = "UNFOLLOW_SESSION_DIAGNOSTIC_TERMINAL_V1"

EXTRA_UI_ACQUISITION = {
    "xml": 0,
    "screenshot": 0,
    "tap": 0,
    "scroll": 0,
    "accessibility_query": 0,
}

IDENTITY_FIELDS = {
    "account_id",
    "request_id",
    "run_id",
    "business_session_id",
    "worker_sha",
    "session_attempt",
}
TIMING_FIELDS = {"timestamp_utc", "timestamp_monotonic"}

VIEWPORT_SCHEMA_REQUIRED_FIELDS = IDENTITY_FIELDS | TIMING_FIELDS | {
    "viewport_index",
    "scroll_depth",
    "ordered_normalized_usernames",
    "viewport_fingerprint",
    "previous_viewport_fingerprint",
    "viewport_fingerprint_changed",
    "overlap_count",
    "overlap_percent",
    "jaccard_percent",
    "overlap_order_preserved",
    "new_username_count",
    "duplicate_username_count",
    "plan_size_initial",
    "plan_remaining",
    "plan_verified_count",
    "backlog_eligible_at_start",
    "backlog_remaining",
    "business_time_remaining",
    "unfollow_session_cap",
    "unfollow_daily_remaining",
    "action_budget_remaining",
    "cleanup_reserve",
    "deadline_monotonic",
    "candidate_eligibility_map",
    "candidate_plan_admission_map",
    "candidate_skip_reason_map",
    "candidate_actionable_map",
    "candidate_action_attempted_map",
    "candidate_action_verified_map",
    "scroll_progress",
    "loading_state",
    "end_of_list_evidence",
    "suggestions_boundary",
    "safe_stop_reason",
}

CANDIDATE_SCHEMA_REQUIRED_FIELDS = IDENTITY_FIELDS | TIMING_FIELDS | {
    "username_normalized",
    "lineage_stage",
    "db_eligible_at_session_start",
    "db_eligibility_scope",
    "job_id",
    "job_created_at",
    "job_status_at_session_start",
    "followed_at",
    "unfollow_eligible_at",
    "follow_source",
    "interacted_ledger_status",
    "whitelist_protection_status",
    "plan_admitted",
    "plan_admitted_at",
    "plan_rank",
    "plan_reason",
    "ui_seen",
    "first_seen_timestamp",
    "first_viewport",
    "first_depth",
    "last_viewport",
    "seen_count",
    "filters_passed",
    "skip_reason",
    "actionable",
    "action_attempted",
    "attempt_timestamp",
    "action_verified",
    "persistence_status",
    "terminal_outcome",
    "carryover_reason",
    "remained_in_plan",
    "remained_in_backlog",
    "list_reordered",
}

SESSION_START_SCHEMA_REQUIRED_FIELDS = IDENTITY_FIELDS | TIMING_FIELDS | {
    "total_db_eligible_at_t0",
    "total_backlog_at_t0",
    "plan_cap",
    "plan_admitted",
    "unfollow_session_quota",
    "unfollow_daily_remaining",
    "time_budget_seconds",
    "cleanup_reserve",
    "excluded_count",
    "protected_count",
    "expected_max_actions",
    "candidate_scan_exhaustive",
    "plan_reason",
}

SESSION_TERMINAL_SCHEMA_REQUIRED_FIELDS = IDENTITY_FIELDS | TIMING_FIELDS | {
    "plan_initial",
    "plan_remaining",
    "ui_unique_seen",
    "actionable_seen",
    "attempted",
    "verified",
    "failed",
    "filtered",
    "not_seen_from_plan",
    "backlog_never_admitted",
    "backlog_remaining",
    "end_of_list_status",
    "stop_reason",
    "business_time_remaining",
    "unfollow_daily_remaining",
    "action_budget_remaining",
    "cleanup_reserve",
    "session_outcome",
    "candidate_outcome_buckets",
    "diagnostic_serialization_cpu_median_ms",
    "diagnostic_serialization_cpu_p90_ms",
    "diagnostic_emission_cpu_median_ms",
    "diagnostic_emission_cpu_p90_ms",
    "diagnostic_bytes_total",
    "diagnostic_event_count",
    "diagnostic_overhead_total_ms",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_username(value: Any) -> str:
    return str(value or "").strip().lstrip("@").lower()


def _ordered_normalized(values: Iterable[Any]) -> tuple[list[str], int]:
    raw = [_normalized_username(value) for value in values]
    raw = [value for value in raw if value]
    out: list[str] = []
    seen: set[str] = set()
    duplicates = 0
    for value in raw:
        if value in seen:
            duplicates += 1
            continue
        seen.add(value)
        out.append(value)
    return out, duplicates


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    rank = max(0.0, min(1.0, percentile)) * (len(ordered) - 1)
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return round(ordered[lower], 6)
    weight = rank - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 6)


def viewport_overlap_metrics(previous: list[str], current: list[str]) -> dict[str, Any]:
    previous_keys = list(dict.fromkeys(_normalized_username(v) for v in previous if _normalized_username(v)))
    current_keys = list(dict.fromkeys(_normalized_username(v) for v in current if _normalized_username(v)))
    previous_set = set(previous_keys)
    current_set = set(current_keys)
    overlap_set = previous_set.intersection(current_set)
    union = previous_set.union(current_set)
    previous_overlap_order = [key for key in previous_keys if key in overlap_set]
    current_overlap_order = [key for key in current_keys if key in overlap_set]
    return {
        "overlap_count": len(overlap_set),
        "overlap_percent": round((len(overlap_set) / len(current_set)) * 100.0, 4) if current_set else 0.0,
        "jaccard_percent": round((len(overlap_set) / len(union)) * 100.0, 4) if union else 100.0,
        "overlap_order_preserved": previous_overlap_order == current_overlap_order,
        "new_username_count": len(current_set - previous_set),
    }


def classify_candidate_lineage(candidate: dict[str, Any]) -> str:
    if candidate.get("db_eligible_at_session_start") is False:
        return "NOT_DB_ELIGIBLE_YET"
    if candidate.get("db_eligible_at_session_start") is None:
        return "INSTRUMENTATION_GAP"
    if not bool(candidate.get("plan_admitted")):
        return "DB_ELIGIBLE_BUT_NOT_PLAN_ADMITTED"
    if not bool(candidate.get("ui_seen")):
        return "PLAN_ADMITTED_BUT_NOT_UI_SEEN"
    if bool(candidate.get("list_reordered")):
        return "INSTAGRAM_LIST_REORDERED"
    if not bool(candidate.get("filters_passed")):
        return "UI_SEEN_BUT_FILTERED"
    if bool(candidate.get("actionable")) and not bool(candidate.get("action_attempted")):
        return "UI_SEEN_ACTIONABLE_BUT_NOT_ATTEMPTED"
    if bool(candidate.get("action_attempted")) and not bool(candidate.get("action_verified")):
        return "ACTION_ATTEMPTED_BUT_FAILED"
    if bool(candidate.get("action_verified")):
        return "ACTION_VERIFIED"
    return "INSTRUMENTATION_GAP"


def reconstruct_candidate_outcomes(candidates: Iterable[dict[str, Any]]) -> dict[str, int]:
    latest: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        key = _normalized_username(candidate.get("username_normalized"))
        if key:
            latest[key] = dict(candidate)
    buckets: dict[str, int] = {}
    for candidate in latest.values():
        bucket = classify_candidate_lineage(candidate)
        buckets[bucket] = int(buckets.get(bucket, 0)) + 1
    return buckets


class UnfollowDiagnosticSession:
    """Session-local diagnostic state derived only from already loaded values."""

    def __init__(
        self,
        *,
        account_id: str,
        request_id: str | None,
        run_id: str | None,
        business_session_id: str | None,
        worker_sha: str | None,
        session_attempt: int,
        plan: dict[str, Any],
        protected_usernames: set[str] | frozenset[str],
        plan_cap: int,
        session_quota: int,
        daily_remaining: int,
        time_budget_seconds: float | None,
        cleanup_reserve: float,
        expected_max_actions: int,
        plan_admission_required: bool,
        emit: Callable[..., None],
        monotonic: Callable[[], float] = time.perf_counter,
        cpu_clock: Callable[[], float] = time.process_time,
    ) -> None:
        self.emit = emit
        self.monotonic = monotonic
        self.cpu_clock = cpu_clock
        self.started_monotonic = float(monotonic())
        self.started_utc = _utc_now()
        self.identity = {
            "account_id": str(account_id or ""),
            "request_id": str(request_id or ""),
            "run_id": str(run_id or ""),
            "business_session_id": str(business_session_id or run_id or ""),
            "worker_sha": str(worker_sha or "").lower(),
            "session_attempt": max(1, int(session_attempt or 1)),
        }
        self.plan = dict(plan or {})
        self.plan_reason = str(self.plan.get("plan_reason") or "")
        self.plan_cap = max(0, int(plan_cap or 0))
        self.session_quota = max(0, int(session_quota or 0))
        self.initial_daily_remaining = max(0, int(daily_remaining or 0))
        self.time_budget_seconds = None if time_budget_seconds is None else max(0.0, float(time_budget_seconds))
        self.cleanup_reserve = max(0.0, float(cleanup_reserve or 0.0))
        self.expected_max_actions = max(0, int(expected_max_actions or 0))
        self.plan_admission_required = bool(plan_admission_required)
        self.deadline_monotonic = (
            self.started_monotonic + self.time_budget_seconds
            if self.time_budget_seconds is not None
            else None
        )
        self.protected = {_normalized_username(value) for value in protected_usernames if _normalized_username(value)}
        admitted = list(self.plan.get("candidates") or [])
        eligible = list(self.plan.get("diagnostic_eligible_candidates_at_start") or admitted)
        self.eligible_at_start = {
            _normalized_username(row.get("username_normalized") or row.get("username")): dict(row)
            for row in eligible
            if isinstance(row, dict) and _normalized_username(row.get("username_normalized") or row.get("username"))
        }
        self.plan_by_username = {
            _normalized_username(row.get("username_normalized") or row.get("username")): dict(row)
            for row in admitted
            if isinstance(row, dict) and _normalized_username(row.get("username_normalized") or row.get("username"))
        }
        self.plan_rank = {key: index for index, key in enumerate(self.plan_by_username, start=1)}
        self.plan_admitted_at = str(self.plan.get("scan_as_of") or self.started_utc)
        self.candidate_state: dict[str, dict[str, Any]] = {}
        self.latest_candidate_events: dict[str, dict[str, Any]] = {}
        self.previous_viewport: list[str] = []
        self.previous_fingerprint = ""
        self.viewport_count = 0
        self.last_end_of_list_status = "not_observed"
        self.serialization_cpu_ms: list[float] = []
        self.emission_cpu_ms: list[float] = []
        self.event_bytes: list[int] = []
        self.terminal_emitted = False

    def _timing(self) -> dict[str, Any]:
        return {
            "timestamp_utc": _utc_now(),
            "timestamp_monotonic": round(float(self.monotonic()), 6),
        }

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        fields = {**self.identity, **self._timing(), **payload}
        serialization_t0 = float(self.cpu_clock())
        encoded = json.dumps({"event": event, **fields}, default=str, sort_keys=True, separators=(",", ":"))
        serialization_ms = max(0.0, (float(self.cpu_clock()) - serialization_t0) * 1000.0)
        self.serialization_cpu_ms.append(serialization_ms)
        self.event_bytes.append(len(encoded.encode("utf-8")))
        emission_t0 = float(self.cpu_clock())
        self.emit("info", event, **fields)
        self.emission_cpu_ms.append(max(0.0, (float(self.cpu_clock()) - emission_t0) * 1000.0))

    def _budget(self, *, verified_count: int) -> dict[str, Any]:
        now = float(self.monotonic())
        business_remaining = (
            None
            if self.deadline_monotonic is None
            else round(max(0.0, self.deadline_monotonic - now), 3)
        )
        return {
            "business_time_remaining": business_remaining,
            "unfollow_session_cap": self.session_quota,
            "unfollow_daily_remaining": max(0, self.initial_daily_remaining - max(0, int(verified_count))),
            "action_budget_remaining": max(0, self.expected_max_actions - max(0, int(verified_count))),
            "cleanup_reserve": round(self.cleanup_reserve, 3),
            "deadline_monotonic": round(self.deadline_monotonic, 6) if self.deadline_monotonic is not None else None,
        }

    def emit_start(self) -> dict[str, Any]:
        payload = {
            "total_db_eligible_at_t0": int(self.plan.get("eligible_total") or 0),
            "total_backlog_at_t0": int(self.plan.get("backlog_actionable_remaining") or self.plan.get("eligible_total") or 0),
            "plan_cap": self.plan_cap,
            "plan_admitted": len(self.plan_by_username),
            "unfollow_session_quota": self.session_quota,
            "unfollow_daily_remaining": self.initial_daily_remaining,
            "time_budget_seconds": self.time_budget_seconds,
            "cleanup_reserve": round(self.cleanup_reserve, 3),
            "excluded_count": int(self.plan.get("skipped_total") or 0),
            "protected_count": len(self.protected),
            "expected_max_actions": self.expected_max_actions,
            "candidate_scan_exhaustive": bool(self.plan.get("candidate_scan_exhaustive")),
            "plan_reason": self.plan_reason,
        }
        self._emit(SESSION_START_EVENT, payload)
        return {**self.identity, **payload}

    def _candidate_metadata(self, key: str, row_cache: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
        metadata = dict(self.eligible_at_start.get(key) or {})
        cached = row_cache.get(key)
        if isinstance(cached, dict):
            metadata = {**cached, **metadata}
        follow_source = str(metadata.get("follow_source") or "")
        if not follow_source:
            if metadata.get("followed_by_bot") is True:
                follow_source = "bot"
            elif metadata.get("followed_by_bot") is False:
                follow_source = "manual"
            else:
                follow_source = "unknown"
        ledger_status = str(
            metadata.get("interacted_ledger_status")
            or metadata.get("interaction_lifecycle_state")
            or metadata.get("follow_status")
            or ("unfollowed" if metadata.get("unfollowed_at") else "unknown")
        )
        return {
            "job_id": metadata.get("job_id"),
            "job_created_at": metadata.get("job_created_at"),
            "job_status_at_session_start": metadata.get("job_status_at_session_start"),
            "followed_at": metadata.get("followed_at"),
            "unfollow_eligible_at": metadata.get("eligible_unfollow_at") or metadata.get("unfollow_eligible_at"),
            "follow_source": follow_source,
            "interacted_ledger_status": ledger_status,
        }

    def _candidate_payload(
        self,
        key: str,
        *,
        lineage_stage: str,
        row_cache: dict[str, dict[str, Any] | None],
        eligible_now: bool,
        skip_reason: str,
        actionable: bool,
        attempted: bool,
        verified: bool,
        persisted: bool,
        terminal_outcome: str = "",
    ) -> dict[str, Any]:
        state = self.candidate_state.setdefault(key, {})
        metadata = self._candidate_metadata(key, row_cache)
        in_start_eligible = key in self.eligible_at_start
        scan_exhaustive = bool(self.plan.get("candidate_scan_exhaustive"))
        db_eligible: bool | None = True if in_start_eligible else (False if scan_exhaustive else None)
        plan_admitted = key in self.plan_by_username
        filters_passed = bool(eligible_now and not skip_reason)
        persistence_status = "persisted" if persisted else ("not_persisted" if attempted else "not_attempted")
        payload = {
            "username_normalized": key,
            "lineage_stage": lineage_stage,
            "db_eligible_at_session_start": db_eligible,
            "db_eligibility_scope": "complete_t0_scan" if scan_exhaustive else "bounded_t0_scan",
            **metadata,
            "whitelist_protection_status": "protected" if key in self.protected else "not_protected_at_session_start",
            "plan_admitted": plan_admitted,
            "plan_admitted_at": self.plan_admitted_at if plan_admitted else None,
            "plan_rank": self.plan_rank.get(key),
            "plan_reason": self.plan_reason,
            "ui_seen": bool(state.get("ui_seen")),
            "first_seen_timestamp": state.get("first_seen_timestamp"),
            "first_viewport": state.get("first_viewport"),
            "first_depth": state.get("first_depth"),
            "last_viewport": state.get("last_viewport"),
            "seen_count": int(state.get("seen_count") or 0),
            "filters_passed": filters_passed,
            "skip_reason": str(skip_reason or ""),
            "actionable": bool(actionable),
            "action_attempted": bool(attempted),
            "attempt_timestamp": state.get("attempt_timestamp"),
            "action_verified": bool(verified),
            "persistence_status": persistence_status,
            "terminal_outcome": str(terminal_outcome or ""),
            "carryover_reason": str(state.get("carryover_reason") or ""),
            "remained_in_plan": bool(plan_admitted and not persisted),
            "remained_in_backlog": bool(db_eligible is True and not persisted),
            "list_reordered": bool(state.get("list_reordered")),
        }
        self.latest_candidate_events[key] = dict(payload)
        return payload

    def observe_viewport(
        self,
        *,
        rows: list[dict[str, Any]],
        visible_eval: dict[str, Any],
        row_cache: dict[str, dict[str, Any] | None],
        scroll_depth: int,
        plan_remaining: int,
        verified_count: int,
        attempted_usernames: set[str],
        verified_usernames: set[str],
        persisted_usernames: set[str],
        harvest_meta: dict[str, Any],
        safe_stop_reason: str,
        viewport_fingerprint_fn: Callable[[list[str]], str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        self.viewport_count += 1
        ordered, duplicate_count = _ordered_normalized(
            row.get("username") for row in rows if isinstance(row, dict)
        )
        fingerprint = str(viewport_fingerprint_fn(ordered) or "")
        overlap = viewport_overlap_metrics(self.previous_viewport, ordered)
        skip_map: dict[str, str] = {}
        eligible_keys: set[str] = set()
        for candidate in list(visible_eval.get("visible_eligible_matches") or []):
            if isinstance(candidate, dict):
                key = _normalized_username(candidate.get("username_normalized") or candidate.get("username"))
                if key:
                    eligible_keys.add(key)
        for candidate in list(visible_eval.get("visible_ineligible_rows") or []):
            if isinstance(candidate, dict):
                key = _normalized_username(candidate.get("username_normalized") or candidate.get("username"))
                if key:
                    skip_map[key] = str(candidate.get("skip_reason") or "unknown")
        plan_map = {key: key in self.plan_by_username for key in ordered}
        actionable_map = {
            key: bool(
                key in eligible_keys
                and (not self.plan_admission_required or plan_map.get(key))
                and key not in persisted_usernames
            )
            for key in ordered
        }
        attempted_map = {key: key in attempted_usernames for key in ordered}
        verified_map = {key: key in verified_usernames for key in ordered}
        now_utc = _utc_now()
        for key in ordered:
            state = self.candidate_state.setdefault(key, {})
            if not state.get("ui_seen"):
                state.update(
                    {
                        "ui_seen": True,
                        "first_seen_timestamp": now_utc,
                        "first_viewport": self.viewport_count,
                        "first_depth": max(0, int(scroll_depth or 0)),
                    }
                )
            state["last_viewport"] = self.viewport_count
            state["seen_count"] = int(state.get("seen_count") or 0) + 1
            state["filters_passed"] = bool(key in eligible_keys and not skip_map.get(key))
            state["skip_reason"] = str(skip_map.get(key) or "")
            state["actionable"] = bool(actionable_map[key])
            if not overlap["overlap_order_preserved"] and key in set(self.previous_viewport):
                state["list_reordered"] = True
        end_detected = bool(harvest_meta.get("following_list_end_detected"))
        suggestions = bool(harvest_meta.get("suggested_for_you_visible"))
        if end_detected:
            self.last_end_of_list_status = str(harvest_meta.get("following_list_end_reason") or "end_marker_detected")
        scroll_progress = (
            "initial_viewport"
            if not self.previous_viewport
            else "new_rows_observed"
            if overlap["new_username_count"] > 0
            else "repeated_viewport"
        )
        stop_evidence_text = " ".join(
            str(value or "").strip().lower()
            for value in (
                harvest_meta.get("following_list_end_reason"),
                harvest_meta.get("scroll_stop_reason"),
                harvest_meta.get("loading_state"),
                safe_stop_reason,
            )
        )
        no_new_row_threshold = any(
            marker in stop_evidence_text
            for marker in ("no_new", "no-new", "repeated_viewport_threshold")
        )
        budget_stop = "budget" in stop_evidence_text
        timeout = "timeout" in stop_evidence_text or "deadline" in stop_evidence_text
        loading_failure = "loading" in stop_evidence_text and any(
            marker in stop_evidence_text
            for marker in ("fail", "stuck", "timeout", "unhealthy")
        )
        payload = {
            "viewport_index": self.viewport_count,
            "scroll_depth": max(0, int(scroll_depth or 0)),
            "ordered_normalized_usernames": ordered,
            "viewport_fingerprint": fingerprint,
            "previous_viewport_fingerprint": self.previous_fingerprint,
            "viewport_fingerprint_changed": bool(
                self.previous_fingerprint and fingerprint != self.previous_fingerprint
            ),
            **overlap,
            "duplicate_username_count": duplicate_count,
            "plan_size_initial": len(self.plan_by_username),
            "plan_remaining": max(0, int(plan_remaining)),
            "plan_verified_count": max(0, int(verified_count)),
            "backlog_eligible_at_start": int(self.plan.get("eligible_total") or 0),
            "backlog_remaining": max(
                0,
                int(self.plan.get("eligible_total") or 0) - len(persisted_usernames),
            ),
            **self._budget(verified_count=verified_count),
            "candidate_eligibility_map": {key: key in eligible_keys for key in ordered},
            "candidate_plan_admission_map": plan_map,
            "candidate_skip_reason_map": skip_map,
            "candidate_actionable_map": actionable_map,
            "candidate_action_attempted_map": attempted_map,
            "candidate_action_verified_map": verified_map,
            "scroll_progress": scroll_progress,
            "loading_state": str(harvest_meta.get("loading_state") or "not_observed"),
            "end_of_list_evidence": {
                "detected": end_detected,
                "reason": str(harvest_meta.get("following_list_end_reason") or ""),
                "suggestions_detected": suggestions,
                "end_marker_detected": end_detected,
                "repeated_viewport": scroll_progress == "repeated_viewport",
                "no_new_row_threshold": no_new_row_threshold,
                "budget_stop": budget_stop,
                "timeout": timeout,
                "loading_failure": loading_failure,
                "other_reason": str(safe_stop_reason or "")
                if safe_stop_reason and not any((budget_stop, timeout, loading_failure, no_new_row_threshold))
                else "",
            },
            "suggestions_boundary": suggestions,
            "safe_stop_reason": str(safe_stop_reason or ""),
        }
        self._emit(VIEWPORT_EVENT, payload)
        candidate_payloads: list[dict[str, Any]] = []
        for key in ordered:
            candidate = self._candidate_payload(
                key,
                lineage_stage="viewport_observed",
                row_cache=row_cache,
                eligible_now=key in eligible_keys,
                skip_reason=skip_map.get(key, ""),
                actionable=actionable_map[key],
                attempted=attempted_map[key],
                verified=verified_map[key],
                persisted=key in persisted_usernames,
            )
            self._emit(CANDIDATE_EVENT, candidate)
            candidate_payloads.append(candidate)
        self.previous_viewport = ordered
        self.previous_fingerprint = fingerprint
        return payload, candidate_payloads

    def record_action_attempt(self, username: str) -> None:
        key = _normalized_username(username)
        if key:
            self.candidate_state.setdefault(key, {})["attempt_timestamp"] = _utc_now()

    def emit_action_terminal(
        self,
        *,
        username: str,
        row_cache: dict[str, dict[str, Any] | None],
        attempted: bool,
        verified: bool,
        persisted: bool,
        failure_reason: str,
    ) -> dict[str, Any]:
        key = _normalized_username(username)
        terminal_outcome = (
            "persisted"
            if verified and persisted
            else "verified_not_persisted"
            if verified
            else "attempt_failed"
            if attempted
            else "not_attempted"
        )
        candidate = self._candidate_payload(
            key,
            lineage_stage="action_terminal",
            row_cache=row_cache,
            eligible_now=True,
            skip_reason=failure_reason,
            actionable=True,
            attempted=attempted,
            verified=verified,
            persisted=persisted,
            terminal_outcome=terminal_outcome,
        )
        self._emit(CANDIDATE_EVENT, candidate)
        return candidate

    def emit_terminal(
        self,
        *,
        status: str,
        stop_reason: str,
        row_cache: dict[str, dict[str, Any] | None],
        plan_remaining: int,
        attempted_usernames: set[str],
        verified_usernames: set[str],
        persisted_usernames: set[str],
        failed_count: int,
        filtered_count: int,
        end_of_list_status: str = "",
    ) -> dict[str, Any]:
        if self.terminal_emitted:
            return {}
        all_keys = set(self.eligible_at_start).union(self.candidate_state)
        for key in sorted(all_keys):
            state = self.candidate_state.setdefault(key, {})
            if not state.get("ui_seen"):
                state.update(
                    {
                        "ui_seen": False,
                        "first_seen_timestamp": None,
                        "first_viewport": None,
                        "first_depth": None,
                        "last_viewport": None,
                        "seen_count": 0,
                    }
                )
            if key in self.plan_by_username and key not in persisted_usernames:
                state["carryover_reason"] = "plan_admitted_not_persisted"
            elif key in self.eligible_at_start and key not in self.plan_by_username:
                state["carryover_reason"] = "db_eligible_but_not_plan_admitted"
            candidate = self._candidate_payload(
                key,
                lineage_stage="session_terminal",
                row_cache=row_cache,
                eligible_now=bool(state.get("filters_passed")),
                skip_reason=str(state.get("skip_reason") or ""),
                actionable=bool(state.get("actionable")),
                attempted=key in attempted_usernames,
                verified=key in verified_usernames,
                persisted=key in persisted_usernames,
                terminal_outcome=("persisted" if key in persisted_usernames else "carryover"),
            )
            self._emit(CANDIDATE_EVENT, candidate)
        buckets = reconstruct_candidate_outcomes(self.latest_candidate_events.values())
        total_ui_seen = sum(1 for state in self.candidate_state.values() if state.get("ui_seen"))
        actionable_seen = sum(
            1
            for candidate in self.latest_candidate_events.values()
            if candidate.get("ui_seen") and candidate.get("actionable")
        )
        payload = {
            "plan_initial": len(self.plan_by_username),
            "plan_remaining": max(0, int(plan_remaining)),
            "ui_unique_seen": total_ui_seen,
            "actionable_seen": actionable_seen,
            "attempted": len(attempted_usernames),
            "verified": len(verified_usernames),
            "failed": max(0, int(failed_count)),
            "filtered": max(0, int(filtered_count)),
            "not_seen_from_plan": sum(
                1
                for key in self.plan_by_username
                if not self.candidate_state.get(key, {}).get("ui_seen")
            ),
            "backlog_never_admitted": max(0, len(self.eligible_at_start) - len(self.plan_by_username)),
            "backlog_remaining": max(0, len(self.eligible_at_start) - len(persisted_usernames)),
            "end_of_list_status": str(end_of_list_status or self.last_end_of_list_status),
            "stop_reason": str(stop_reason or status),
            **self._budget(verified_count=len(verified_usernames)),
            "session_outcome": str(status or ""),
            "candidate_outcome_buckets": buckets,
            "diagnostic_serialization_cpu_median_ms": _percentile(self.serialization_cpu_ms, 0.5),
            "diagnostic_serialization_cpu_p90_ms": _percentile(self.serialization_cpu_ms, 0.9),
            "diagnostic_emission_cpu_median_ms": _percentile(self.emission_cpu_ms, 0.5),
            "diagnostic_emission_cpu_p90_ms": _percentile(self.emission_cpu_ms, 0.9),
            "diagnostic_bytes_total": sum(self.event_bytes),
            "diagnostic_event_count": len(self.event_bytes),
            "diagnostic_overhead_total_ms": round(sum(self.serialization_cpu_ms) + sum(self.emission_cpu_ms), 6),
        }
        self._emit(SESSION_TERMINAL_EVENT, payload)
        self.terminal_emitted = True
        return {**self.identity, **payload}


def schema_completeness(required: set[str], payload: dict[str, Any]) -> dict[str, Any]:
    present = required.intersection(payload)
    return {
        "required_fields": len(required),
        "present_fields": len(present),
        "missing_fields": sorted(required - present),
        "completeness_percent": round((len(present) / len(required)) * 100.0, 4) if required else 100.0,
    }
