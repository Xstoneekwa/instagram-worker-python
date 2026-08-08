"""Follow60 Ordering V2 evidence shadow.

The shadow is a log/memory-only observer.  It consumes the immutable pre-Follow
capture and structured events already produced by Follow60 Mainline V1.  It
never acquires UI state, authorizes an action, changes ordering, or persists a
business action.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping


SCHEMA = "FOLLOW60_ORDERING_V2_SHADOW_EVENT_V2"
PUBLIC_EVENT = "follow60_ordering_v2_shadow_terminal"
_ENABLED_ENV = "FOLLOW60_ORDERING_V2_SHADOW_ENABLED"
_ALLOWLIST_ENV = "FOLLOW60_ORDERING_V2_SHADOW_ACCOUNT_IDS"
_TRUE = {"1", "true", "yes", "on"}
_BOUNDS_RE = re.compile(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$")
_UNKNOWN = "unknown"
_LOCK = threading.RLock()
_CONTEXTS: dict[tuple[str, str, str], "Follow60OrderingV2ShadowContext"] = {}
_ACTIVE_KEY: tuple[str, str, str] | None = None
_TERMINAL_EVENT_IDS: set[str] = set()
_CANDIDATE_INDEX_BY_RUN: dict[tuple[str, str], int] = {}
_PENDING_TERMINALS: list[dict[str, Any]] = []


def enabled_for_account(
    account_id: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Fail closed: flag plus explicit account allowlist are both required."""
    env = os.environ if environ is None else environ
    if str(env.get(_ENABLED_ENV) or "").strip().lower() not in _TRUE:
        return False
    allowlist = {
        value.strip().lower()
        for value in str(env.get(_ALLOWLIST_ENV) or "").split(",")
        if value.strip()
    }
    account = str(account_id or "").strip().lower()
    return bool(account and allowlist and account in allowlist)


def _norm(value: Any) -> str:
    return str(value or "").strip().lstrip("@").lower()


def _stable_text(value: Any, default: str = _UNKNOWN) -> str:
    text = str(value or "").strip()
    return text if text else default


def _viewport_from_existing_xml(xml: str) -> tuple[int, int] | None:
    """Resolve the existing XML frame without a device call or fixed coordinates."""
    try:
        root = ET.fromstring(str(xml or ""))
    except Exception:
        return None
    max_right = 0
    max_bottom = 0
    for node in root.iter():
        match = _BOUNDS_RE.match(str((node.attrib or {}).get("bounds") or ""))
        if not match:
            continue
        _left, _top, right, bottom = (int(value) for value in match.groups())
        max_right = max(max_right, right)
        max_bottom = max(max_bottom, bottom)
    if max_right <= 0 or max_bottom <= 0:
        return None
    return max_right, max_bottom


def _redacted_grid_geometry(evidence: Mapping[str, Any]) -> dict[str, Any]:
    cells = list(evidence.get("physical_cells") or [])
    post_bounds = evidence.get("post_bounds")
    top_left_cells = [
        cell for cell in cells
        if isinstance(cell, Mapping)
        and cell.get("absolute_row_index") == 1
        and cell.get("absolute_column_index") == 1
    ]
    first_row_cells = [
        cell for cell in cells
        if isinstance(cell, Mapping) and cell.get("absolute_row_index") == 1
    ]
    first_row = evidence.get("first_row_fully_visible")
    if first_row is None:
        first_row = bool(
            evidence.get("top_left_fully_visible")
            and first_row_cells
            and all(int(cell.get("bottom") or 0) > int(cell.get("top") or 0) for cell in first_row_cells)
        )
    suggested_detected = bool(evidence.get("suggested_region_detected"))
    highlights_detected = bool(evidence.get("highlights_region_detected"))
    suggested_separate = evidence.get("suggested_region_separate")
    highlights_separate = evidence.get("highlights_region_separate")
    return {
        "posts_count": evidence.get("posts_count", _UNKNOWN),
        "posts_count_source": _stable_text(
            evidence.get("posts_count_source")
            or evidence.get("post_count_source")
            or evidence.get("posts_count_evidence_source")
        ),
        "posts_count_positive": bool(evidence.get("post_count_positive")),
        "posts_count_zero_exact": bool(evidence.get("posts_count_zero_exact")),
        "posts_tab_selected": bool(
            evidence.get("posts_tab_selected") or evidence.get("grid_selected")
        ),
        "posts_tab_source": _stable_text(evidence.get("posts_tab_source")),
        "visible_post_cell_count": int(
            evidence.get("physical_cell_count")
            or evidence.get("visible_post_count")
            or len(cells)
        ),
        "first_row_fully_visible": bool(first_row),
        "absolute_top_left_unique": bool(
            len(top_left_cells) == 1
            and evidence.get("absolute_top_left_origin_proven")
            and evidence.get("top_left_fully_visible")
            and post_bounds
        ),
        "absolute_top_left_row": evidence.get("absolute_row_index", _UNKNOWN),
        "absolute_top_left_column": evidence.get("absolute_column_index", _UNKNOWN),
        "suggested_overlap": bool(suggested_detected and suggested_separate is not True),
        "highlights_overlap": bool(highlights_detected and highlights_separate is not True),
        "suggested_region_detected": suggested_detected,
        "highlights_region_detected": highlights_detected,
        "reels_selected": bool(
            evidence.get("reels_selected")
            or evidence.get("reels_tab_state") == "selected"
        ),
        "tagged_selected": bool(
            evidence.get("tagged_selected")
            or evidence.get("tagged_tab_state") == "selected"
        ),
        "bounds_redacted": True,
        "top_left_proof_hash": (
            hashlib.sha256(
                json.dumps(post_bounds, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()[:20]
            if post_bounds
            else ""
        ),
    }


def _posts_count_from_existing_xml(xml: str) -> tuple[int | str, str]:
    """CPU-only extraction from the immutable mono XML already held by V1."""
    try:
        root = ET.fromstring(str(xml or ""))
    except Exception:
        return _UNKNOWN, _UNKNOWN
    pattern = re.compile(r"\b([0-9][0-9., ]*)\s+(?:posts?|publications?)\b", re.I)
    for node in root.iter():
        attrs = node.attrib or {}
        label = " ".join(str(attrs.get(name) or "") for name in ("text", "content-desc"))
        match = pattern.search(label)
        if match is None:
            continue
        digits = re.sub(r"[^0-9]", "", match.group(1))
        if digits:
            return int(digits), "existing_pre_follow_mono_xml_profile_count"
    return _UNKNOWN, _UNKNOWN


def classify_existing_pre_follow_capture(
    mono_capture: dict[str, Any] | None,
    *,
    account_id: str,
    run_id: str,
    request_id: str,
    candidate_username: str,
    source_profile_username: str,
    visual_candidate_id: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Classify an already-acquired capture for telemetry only."""
    cpu_started = time.perf_counter_ns()
    if not enabled_for_account(account_id, environ=environ):
        return None
    capture = dict(mono_capture or {})
    xml = str(capture.get("xml") or "")
    capture_fingerprint = str(
        capture.get("xml_fingerprint")
        or hashlib.sha256(xml.encode("utf-8", errors="replace")).hexdigest()[:20]
    )
    cached_fingerprint = str(
        capture.get("_ordering_v2_existing_xml_fingerprint") or ""
    )
    cached_viewport = capture.get("_ordering_v2_existing_viewport")
    cached_evidence = capture.get("_ordering_v2_existing_grid_evidence")
    cache_valid = bool(
        cached_fingerprint
        and cached_fingerprint == capture_fingerprint
        and isinstance(cached_viewport, (list, tuple))
        and len(cached_viewport) == 2
        and int(cached_viewport[0] or 0) > 0
        and int(cached_viewport[1] or 0) > 0
        and isinstance(cached_evidence, Mapping)
    )
    viewport = (
        (int(cached_viewport[0]), int(cached_viewport[1]))
        if cache_valid
        else _viewport_from_existing_xml(xml)
    )
    private_probe = dict(capture.get("private_probe_payload") or {})
    private_detected = bool(private_probe.get("private_profile_detected"))
    identity_exact = bool(capture.get("exact_identity"))
    profile_surface = bool(capture.get("profile_surface"))
    follow_cta_positive = bool(capture.get("follow_cta_positive"))
    evidence: dict[str, Any] = {}
    if not xml or viewport is None:
        classification = "AMBIGUOUS"
        reason = "existing_pre_follow_capture_missing_or_unframed"
    elif private_detected:
        classification = "PRIVATE"
        reason = "pre_follow_mono_capture_private_positive"
    elif not bool(capture.get("ok")) or not identity_exact:
        classification = "AMBIGUOUS"
        reason = "pre_follow_positive_profile_contract_incomplete"
    else:
        from instagram_navigation import _post_follow_post_grid_evidence_from_xml

        evidence = (
            deepcopy(dict(cached_evidence))
            if cache_valid
            else _post_follow_post_grid_evidence_from_xml(
                xml,
                candidate_username=candidate_username,
                ww=int(viewport[0]),
                wh=int(viewport[1]),
                profile_identity_exact=True,
                profile_origin_exact=True,
            )
        )
        outcome = str(evidence.get("outcome") or "")
        geometry = _redacted_grid_geometry(evidence)
        critical_positive = bool(
            identity_exact
            and profile_surface
            and follow_cta_positive
            and geometry["posts_count_positive"]
            and geometry["posts_tab_selected"]
            and geometry["visible_post_cell_count"] > 0
            and geometry["first_row_fully_visible"]
            and geometry["absolute_top_left_unique"]
            and geometry["absolute_top_left_row"] == 1
            and geometry["absolute_top_left_column"] == 1
            and not geometry["suggested_overlap"]
            and not geometry["highlights_overlap"]
            and not geometry["reels_selected"]
            and not geometry["tagged_selected"]
            and not evidence.get("private_profile_visible")
            and not evidence.get("loading_visible")
        )
        if outcome == "NO_POSTS_POSITIVE":
            classification = "NO_POSTS"
            reason = str(evidence.get("no_posts_reason") or "no_posts_positive")
        elif outcome == "POST_ROW_POSITIVE_SAFE" and critical_positive:
            classification = "DIRECT_GRID_SAFE"
            reason = "strict_v2_positive_top_left_grid_contract"
        elif outcome in {"POST_ROW_POSITIVE_BUT_CLIPPED", "POST_GRID_REVEAL_REQUIRED"}:
            classification = "BELOW_FOLD"
            reason = str(
                evidence.get("reveal_required_reason")
                or evidence.get("clipped_reason")
                or evidence.get("rejection_reason")
                or "post_grid_reveal_required"
            )
        else:
            classification = "AMBIGUOUS"
            reason = str(evidence.get("rejection_reason") or "post_grid_ambiguous")
    geometry = _redacted_grid_geometry(evidence)
    posts_count, posts_count_source = _posts_count_from_existing_xml(xml)
    if geometry.get("posts_count") == _UNKNOWN:
        geometry["posts_count"] = posts_count
    if geometry.get("posts_count_source") == _UNKNOWN:
        geometry["posts_count_source"] = posts_count_source
    return {
        "schema": SCHEMA,
        "classification": classification,
        "reason": reason,
        "eligible_for_ordering_v2": classification == "DIRECT_GRID_SAFE",
        "capture_fingerprint": str(
            capture_fingerprint
        ),
        "capture_duration_ms": float(capture.get("duration_ms") or 0.0),
        "identity_exact": identity_exact,
        "profile_surface": profile_surface,
        "follow_cta_positive": follow_cta_positive,
        "private_detected": private_detected,
        "loading_detected": bool(evidence.get("loading_visible")),
        "restricted_detected": bool(
            evidence.get("restricted_visible") or evidence.get("challenge_visible")
        ),
        "profile_public": bool(
            identity_exact and profile_surface and not private_detected
            and not evidence.get("loading_visible")
        ),
        "post_grid_geometry": geometry,
        "v1_grid_outcome": str(evidence.get("outcome") or ""),
        "v1_grid_rejection_reason": str(evidence.get("rejection_reason") or ""),
        "navigation_generation": str(capture.get("navigation_generation") or ""),
        "ui_generation": int(capture.get("ui_generation") or 0),
        "classification_cpu_ns": time.perf_counter_ns() - cpu_started,
        "mono_grid_classification_reused": bool(cache_valid),
        "acquisition_count": 0,
        "extra_screenshots": 0,
        "extra_xml": 0,
        "behavior_changed": False,
        "v2_default_enabled": False,
    }


@dataclass
class Follow60OrderingV2ShadowContext:
    key: tuple[str, str, str]
    binding: dict[str, Any]
    business_eligibility: dict[str, Any]
    initial_surface: dict[str, Any]
    post_grid_geometry: dict[str, Any]
    classification: dict[str, Any]
    candidate_index: int
    opened_at_monotonic_ns: int
    cpu_timings: dict[str, int]
    v1_actual_execution: dict[str, Any] = field(default_factory=dict)
    reentry_evidence: dict[str, Any] = field(default_factory=dict)
    event_id: str = ""
    terminal: bool = False


def _event_id_for(key: tuple[str, str, str]) -> str:
    return "ordv2_" + hashlib.sha256("|".join(key).encode("utf-8")).hexdigest()[:24]


def begin_candidate_shadow(
    mono_capture: dict[str, Any] | None,
    *,
    account_id: str,
    run_id: str,
    request_id: str,
    business_session_id: str,
    attempt_id: int,
    binding_kind: str,
    worker_sha: str,
    target_id: str,
    candidate_username: str,
    source_profile_username: str,
    visual_candidate_id: str,
    action_id: str,
    business_evidence: Mapping[str, Any] | None = None,
    expected_binding: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Create one candidate context; never emit a public event here."""
    creation_started = time.perf_counter_ns()
    classified = classify_existing_pre_follow_capture(
        mono_capture,
        account_id=account_id,
        run_id=run_id,
        request_id=request_id,
        candidate_username=candidate_username,
        source_profile_username=source_profile_username,
        visual_candidate_id=visual_candidate_id,
        environ=environ,
    )
    if classified is None:
        return None
    account = str(account_id or "")
    run = str(run_id or "")
    correlation = str(action_id or visual_candidate_id or candidate_username or "")
    key = (account, run, correlation)
    run_key = (account, run)
    with _LOCK:
        global _ACTIVE_KEY
        if _ACTIVE_KEY is not None and _ACTIVE_KEY in _CONTEXTS and _ACTIVE_KEY != key:
            previous = _CONTEXTS[_ACTIVE_KEY]
            previous_payload = _finalize_context(
                previous,
                status="partial_error",
                reason="next_candidate_started_before_previous_shadow_terminal",
            )
            if previous_payload is not None:
                _PENDING_TERMINALS.append(previous_payload)
        _CANDIDATE_INDEX_BY_RUN[run_key] = _CANDIDATE_INDEX_BY_RUN.get(run_key, 0) + 1
        candidate_index = _CANDIDATE_INDEX_BY_RUN[run_key]
        business = dict(business_evidence or {})
        business.setdefault("filter_evaluated", True)
        business.setdefault("filter_passed", True)
        business.setdefault("filter_reason", "candidate_reached_v1_follow_gate")
        business.setdefault("eligibility_passed", True)
        business.setdefault("eligibility_reason", "candidate_reached_v1_follow_gate")
        business.setdefault("follow_budget_available", True)
        binding = {
            "account_id": account,
            "request_id": str(request_id or ""),
            "run_id": run,
            "business_session_id": str(business_session_id or ""),
            "attempt_id": int(attempt_id or 0),
            "binding_kind": str(binding_kind or ""),
            "worker_sha": str(worker_sha or "").lower(),
            "target_id": str(target_id or ""),
            "source_profile_username": _norm(source_profile_username),
            "candidate_username": _norm(candidate_username),
            "visual_candidate_id": str(visual_candidate_id or ""),
            "action_id": correlation,
        }
        required = (
            "account_id", "request_id", "run_id", "business_session_id",
            "attempt_id", "binding_kind", "worker_sha", "target_id",
            "candidate_username", "action_id",
        )
        binding_mismatches = [name for name in required if not binding.get(name)]
        for name, expected in dict(expected_binding or {}).items():
            if name in binding and str(binding.get(name)) != str(expected):
                binding_mismatches.append(f"{name}_mismatch")
        binding["validation_ok"] = not binding_mismatches
        binding["validation_reason"] = (
            "binding_exact" if not binding_mismatches
            else "binding_invalid:" + ",".join(sorted(set(binding_mismatches)))
        )
        context = Follow60OrderingV2ShadowContext(
            key=key,
            binding=binding,
            business_eligibility=business,
            initial_surface={
                "identity_exact": bool(classified.get("identity_exact")),
                "profile_public": bool(classified.get("profile_public")),
                "profile_private": bool(classified.get("private_detected")),
                "profile_loading": bool(classified.get("loading_detected")),
                "profile_restricted": bool(classified.get("restricted_detected")),
                "challenge_or_overlay_status": (
                    "present" if classified.get("restricted_detected") else "absent"
                ),
                "follow_cta_state": (
                    "follow_exact" if classified.get("follow_cta_positive") else _UNKNOWN
                ),
                "capture_fingerprint": classified.get("capture_fingerprint"),
                "navigation_generation": classified.get("navigation_generation"),
                "ui_generation": classified.get("ui_generation"),
            },
            post_grid_geometry=dict(classified.get("post_grid_geometry") or {}),
            classification={
                "initial": classified.get("classification"),
                "initial_reason": classified.get("reason"),
                "v1_grid_outcome": classified.get("v1_grid_outcome"),
                "first_rejection_reason": classified.get("v1_grid_rejection_reason")
                or classified.get("reason"),
                "strict_direct_grid_safe": bool(
                    classified.get("classification") == "DIRECT_GRID_SAFE"
                ),
            },
            candidate_index=candidate_index,
            opened_at_monotonic_ns=time.perf_counter_ns(),
            cpu_timings={
                "classification_cpu_ns": int(classified.get("classification_cpu_ns") or 0),
                "context_creation_cpu_ns": 0,
                "enrichment_cpu_ns": 0,
                "terminal_serialization_cpu_ns": 0,
            },
            v1_actual_execution={
                "selected_path": _UNKNOWN,
                "reveal_count": 0,
                "postopen_started_monotonic_ns": 0,
                "postopen_terminal_monotonic_ns": 0,
                "postopen_tap_observed": False,
                "viewer_opened": False,
                "v5_result": _UNKNOWN,
                "like_result": _UNKNOWN,
                "return_ct_result": _UNKNOWN,
                "cycle_complete": False,
                "steps_observed": [],
                "avoidable_stages": [],
            },
            reentry_evidence={
                "classification": "REENTRY_NOT_PROVEN",
                "one_back_used": False,
                "back_count": 0,
                "candidate_profile_observed": False,
                "candidate_exact_proven": False,
                "cta_observed": _UNKNOWN,
                "package": _UNKNOWN,
                "activity": _UNKNOWN,
                "navigation_generation": _UNKNOWN,
                "ui_generation": _UNKNOWN,
                "overlay_status": _UNKNOWN,
                "proof_source": _UNKNOWN,
                "cost_already_paid_by_v1_ms": _UNKNOWN,
            },
            event_id=_event_id_for(key),
        )
        context.cpu_timings["context_creation_cpu_ns"] = (
            time.perf_counter_ns() - creation_started
        )
        _CONTEXTS[key] = context
        _ACTIVE_KEY = key
    return deepcopy(classified)


def _candidate_from_fields(fields: Mapping[str, Any]) -> str:
    return _norm(
        fields.get("candidate_username")
        or fields.get("follower_username")
        or fields.get("username")
    )


def _matching_context(fields: Mapping[str, Any]) -> Follow60OrderingV2ShadowContext | None:
    candidate = _candidate_from_fields(fields)
    run_id = str(fields.get("run_id") or "")
    with _LOCK:
        candidates = list(_CONTEXTS.values())
        if candidate:
            for context in reversed(candidates):
                if context.binding["candidate_username"] == candidate and (
                    not run_id or context.binding["run_id"] == run_id
                ):
                    return context
            return None
        if run_id:
            run_candidates = [
                context for context in candidates
                if context.binding["run_id"] == run_id
            ]
            if len(run_candidates) == 1:
                return run_candidates[0]
            return None
        if _ACTIVE_KEY is not None:
            return _CONTEXTS.get(_ACTIVE_KEY)
    return None


def _append_step(context: Follow60OrderingV2ShadowContext, event: str) -> None:
    steps = context.v1_actual_execution.setdefault("steps_observed", [])
    if event not in steps:
        steps.append(event)


def _observe_into_context(
    context: Follow60OrderingV2ShadowContext,
    event: str,
    fields: Mapping[str, Any],
) -> None:
    started = time.perf_counter_ns()
    v1 = context.v1_actual_execution
    reentry = context.reentry_evidence
    _append_step(context, event)
    if event in {"follow_action_verified", "follow_action_exact_follow_verified"}:
        v1["follow_result"] = str(fields.get("follow_state_after") or "verified")
    elif event == "follow_60s_post_grid_evidence_created_at_final_mute_close":
        v1["initial_post_mute_grid_outcome"] = _stable_text(fields.get("outcome"))
    elif event == "follow_60s_postgrid_after_reveal":
        v1["reveal_count"] = max(
            int(v1.get("reveal_count") or 0),
            int(fields.get("reveal_count_total_for_like_phase") or 1),
        )
        v1["selected_path"] = (
            "REVEAL_PLUS_GOLDEN"
            if str(fields.get("post_open_method") or "").lower() == "golden"
            else "REVEAL_PLUS_SAFE"
        )
        v1["avoidable_stages"].append("bounded_reveal")
    elif event == "follow_60s_post_grid_evidence_fallback_golden_direct":
        if int(v1.get("reveal_count") or 0) > 0:
            v1["selected_path"] = "REVEAL_PLUS_GOLDEN"
        else:
            v1["selected_path"] = "GOLDEN_DIRECT"
        v1["avoidable_stages"].append("golden_fallback")
    elif event == "follow_60s_post_grid_evidence_golden_direct_completed":
        v1["selected_path"] = (
            "REVEAL_PLUS_GOLDEN"
            if int(fields.get("reveal_count_total_for_like_phase") or 0) > 0
            else "GOLDEN_DIRECT"
        )
        v1["postopen_method"] = "Golden"
        v1["golden_attempt_count"] = int(fields.get("golden_attempt_count") or 1)
        v1["postopen_result"] = (
            "viewer_opened" if fields.get("post_detected") is True else "not_opened"
        )
    elif event == "follow_60s_post_grid_evidence_safe_direct_completed":
        v1["selected_path"] = (
            "REVEAL_PLUS_SAFE"
            if int(fields.get("reveal_count_total_for_like_phase") or 0) > 0
            else "SAFE_DIRECT"
        )
        v1["postopen_method"] = "SAFE"
        v1["postopen_result"] = (
            "viewer_opened" if fields.get("ok") is True else "not_opened"
        )
    elif event in {
        "follow_60s_post_grid_evidence_direct_cell_tap_sent",
        "follow_60s_direct_post_cell_tap_sent",
    }:
        v1["selected_path"] = "SAFE_DIRECT"
        v1["postopen_tap_observed"] = True
    elif event == "post_follow_post_like_open_started":
        v1["postopen_started_monotonic_ns"] = time.perf_counter_ns()
    elif event in {"post_follow_like_open_tap_sent", "post_follow_post_like_tap_sent"}:
        v1["postopen_tap_observed"] = True
    elif event == "post_follow_viewer_detection_strategy":
        v1["viewer_opened"] = bool(fields.get("viewer_confirmed"))
        v1["viewer_detection_strategy"] = _stable_text(fields.get("final_strategy"))
        v1["viewer_detection_ms"] = fields.get("a2_elapsed_ms", _UNKNOWN)
    elif event == "post_follow_open_like_proof_stashed":
        v1["v5_result"] = "positive_post_identity_proven"
        v1["v5_proof_method"] = _stable_text(fields.get("proof_method"))
        reentry["classification"] = "REENTRY_LEVEL_0_EVIDENCE_AVAILABLE"
        reentry["proof_source"] = "v1_post_open_stage_provenance"
    elif event in {"post_open_surface_rejected", "post_follow_like_v5_rejected"}:
        v1["v5_result"] = "rejected"
        v1["v5_rejection_reason"] = _stable_text(fields.get("reason"))
        v1["selected_path"] = "V5_REJECT"
    elif event == "visual_post_like_verify_completed":
        v1["like_result"] = (
            "verified" if fields.get("liked_verified") is True else "not_verified"
        )
        v1["like_verify_ms"] = fields.get("verify_total_ms", _UNKNOWN)
    elif event == "post_follow_like_completed":
        v1["like_result"] = (
            "verified" if int(fields.get("liked_count") or 0) > 0 else "skipped"
        )
    elif event == "post_follow_post_likes_perf_summary":
        v1["postopen_duration_ms"] = fields.get("post_open_total_ms", _UNKNOWN)
        v1["postopen_terminal_monotonic_ns"] = time.perf_counter_ns()
        if fields.get("phase_outcome"):
            v1["like_phase_outcome"] = fields.get("phase_outcome")
    elif event == "follow60_golden_stage_timings":
        golden_total_ms = fields.get("golden_total_ms")
        v1["golden_stage_timings"] = {
            key: fields.get(key)
            for key in (
                "golden_total_ms",
                "golden_profile_context_ms",
                "golden_grid_search_ms",
                "golden_viewer_detect_ms",
                "golden_viewer_to_v5_ms",
                "golden_instrumentation_overhead_ms",
            )
            if fields.get(key) is not None
        }
        if golden_total_ms is not None:
            v1["golden_total_ms"] = golden_total_ms
    elif event == "visual_profile_no_posts_tier1_check_completed" and fields.get("tier1_detected"):
        v1["selected_path"] = "NO_POSTS"
        v1["like_result"] = "skipped_no_posts"
    elif event == "post_follow_post_likes_return_to_profile_success":
        reentry["one_back_used"] = True
        reentry["back_count"] = max(1, int(reentry.get("back_count") or 0))
        reentry["candidate_profile_observed"] = True
        reentry["cost_already_paid_by_v1_ms"] = fields.get(
            "return_to_profile_total_ms", _UNKNOWN
        )
        reentry["proof_source"] = "v1_return_to_profile_fast_confirmation"
        reentry["classification"] = "REENTRY_LEVEL_1_EVIDENCE_AVAILABLE"
    elif event == "post_follow_like_profile_guard_fast_proof_reused":
        reentry["package"] = _stable_text(fields.get("current_package"))
        reentry["candidate_exact_proven"] = bool(
            _norm(fields.get("action_bar_title")) == context.binding["candidate_username"]
        )
        reentry["cta_observed"] = "following_or_profile_context"
    elif event == "follow_60s_return_candidate_proof_used":
        reentry["back_count"] = int(fields.get("back_count") or 0)
        reentry["one_back_used"] = reentry["back_count"] == 1
        reentry["proof_source"] = "follow_60s_return_candidate_proof_used"
        reentry["cost_already_paid_by_v1_ms"] = fields.get("ct_poll_elapsed_ms", _UNKNOWN)
        if fields.get("final_ct_exact") is True:
            reentry["classification"] = "REENTRY_LEVEL_1_EVIDENCE_AVAILABLE"
    elif event == "post_follow_return_ct_started":
        v1["return_ct_started_monotonic_ns"] = time.perf_counter_ns()
        if reentry.get("classification") == "REENTRY_NOT_PROVEN":
            reentry["classification"] = "REENTRY_LEVEL_2_WOULD_BE_REQUIRED"
    elif event in {"post_follow_return_ct_success", "visual_candidate_profile_return_ct_success"}:
        v1["return_ct_result"] = "exact"
    elif event == "follow_60s_stage_journaled_v2":
        stage = str(fields.get("stage") or "")
        if stage:
            v1.setdefault("persisted_stage_receipts", {})[stage] = bool(fields.get("ok"))
    elif event == "candidate_local_persistence_summary":
        v1["persistence_summary"] = {
            "composite_flush_ok": bool(fields.get("composite_flush_ok")),
            "critical_rpc_acknowledged": bool(fields.get("critical_rpc_acknowledged")),
            "next_candidate_allowed": bool(fields.get("next_candidate_allowed")),
        }
    context.cpu_timings["enrichment_cpu_ns"] += time.perf_counter_ns() - started


def _terminal_payload(
    context: Follow60OrderingV2ShadowContext,
    *,
    status: str,
    reason: str,
) -> dict[str, Any]:
    terminal_started = time.perf_counter_ns()
    terminal_at = time.perf_counter_ns()
    if context.binding.get("validation_ok") is not True:
        status = "shadow_internal_error_non_blocking"
        reason = str(context.binding.get("validation_reason") or "binding_invalid")
    v1 = deepcopy(context.v1_actual_execution)
    if v1.get("postopen_started_monotonic_ns") and v1.get("postopen_terminal_monotonic_ns"):
        v1["v1_postopen_duration_ms_from_shadow_boundaries"] = round(
            (
                int(v1["postopen_terminal_monotonic_ns"])
                - int(v1["postopen_started_monotonic_ns"])
            )
            / 1_000_000.0,
            3,
        )
    else:
        v1["v1_postopen_duration_ms_from_shadow_boundaries"] = _UNKNOWN
    avoidable = []
    if "golden_fallback" in v1.get("avoidable_stages", []):
        avoidable.append("golden_fallback_if_strict_initial_grid_had_been_reusable")
    if "bounded_reveal" in v1.get("avoidable_stages", []):
        avoidable.append("bounded_reveal_if_initial_top_left_had_been_fully_safe")
    v1["potentially_avoidable_stages"] = avoidable
    golden_total_ms = v1.get("golden_total_ms")
    v1["v1_avoidable_stage_duration_ms"] = (
        golden_total_ms
        if golden_total_ms is not None
        and context.classification.get("strict_direct_grid_safe") is True
        and "golden_fallback" in v1.get("avoidable_stages", [])
        else _UNKNOWN
    )
    v1["cycle_complete"] = status == "completed"
    operation_counters = {
        "shadow_extra_screenshot_count": 0,
        "shadow_extra_xml_count": 0,
        "shadow_extra_poll_count": 0,
        "shadow_extra_tap_count": 0,
        "shadow_extra_accessibility_query_count": 0,
        "shadow_path_override_count": 0,
        "shadow_intent_created_count": 0,
    }
    payload = {
        "schema_version": SCHEMA,
        "event_id": context.event_id,
        "event_status": status,
        "event_terminal_reason": reason,
        **context.binding,
        "candidate_index": context.candidate_index,
        "opened_at_monotonic_ns": context.opened_at_monotonic_ns,
        "terminal_at_monotonic_ns": terminal_at,
        "binding": deepcopy(context.binding),
        "business_eligibility": deepcopy(context.business_eligibility),
        "initial_surface": deepcopy(context.initial_surface),
        "post_grid_geometry": deepcopy(context.post_grid_geometry),
        "classification": deepcopy(context.classification),
        "v1_actual_execution": v1,
        "reentry_evidence": deepcopy(context.reentry_evidence),
        "operation_counters": operation_counters,
        "cpu_timings": deepcopy(context.cpu_timings),
        "safety_assertions": {
            "behavior_changed": False,
            "v1_only_behavioral_engine": True,
            "new_ui_acquisition": False,
            "raw_xml_exported": False,
            "screenshot_path_exported": False,
            "coordinates_exported": False,
            "shadow_failure_blocks_v1": False,
        },
    }
    serialization_started = time.perf_counter_ns()
    json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    payload["cpu_timings"]["terminal_serialization_cpu_ns"] = (
        time.perf_counter_ns() - serialization_started
    )
    payload["cpu_timings"]["total_shadow_cpu_ns"] = (
        int(payload["cpu_timings"].get("context_creation_cpu_ns") or 0)
        + int(payload["cpu_timings"].get("classification_cpu_ns") or 0)
        + int(payload["cpu_timings"].get("enrichment_cpu_ns") or 0)
        + int(payload["cpu_timings"].get("terminal_serialization_cpu_ns") or 0)
    )
    payload["cpu_timings"]["terminal_build_cpu_ns"] = (
        time.perf_counter_ns() - terminal_started
    )
    return payload


def _finalize_context(
    context: Follow60OrderingV2ShadowContext,
    *,
    status: str,
    reason: str,
) -> dict[str, Any] | None:
    global _ACTIVE_KEY
    with _LOCK:
        if context.terminal or context.event_id in _TERMINAL_EVENT_IDS:
            return None
        context.terminal = True
        payload = _terminal_payload(context, status=status, reason=reason)
        _TERMINAL_EVENT_IDS.add(context.event_id)
        _CONTEXTS.pop(context.key, None)
        if _ACTIVE_KEY == context.key:
            _ACTIVE_KEY = None
        return payload


def observe_runtime_event(event: str, fields: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Copy already-produced V1 log evidence and return terminal events to the writer."""
    if event in {PUBLIC_EVENT, "follow60_ordering_v2_shadow_evaluated"}:
        return []
    with _LOCK:
        pending = list(_PENDING_TERMINALS)
        _PENDING_TERMINALS.clear()
    context = _matching_context(fields)
    if context is None:
        return pending
    try:
        _observe_into_context(context, str(event or ""), fields)
        if event == "visual_candidate_profile_return_ct_success":
            payload = _finalize_context(
                context,
                status="completed",
                reason="v1_cycle_return_ct_and_persistence_complete",
            )
            return pending + ([payload] if payload else [])
        if event in {"private_skip_fast_path_completed", "follow_private_account_skipped_by_setting"}:
            payload = _finalize_context(
                context,
                status="completed",
                reason="v1_private_skip_completed",
            )
            return pending + ([payload] if payload else [])
        if event in {"visual_candidate_profile_flow_failed", "post_follow_flow_failed"}:
            payload = _finalize_context(
                context,
                status="partial_error",
                reason=_stable_text(fields.get("failure_reason") or fields.get("reason")),
            )
            return pending + ([payload] if payload else [])
    except Exception:
        return []
    return pending


def finalize_active_contexts(
    *,
    status: str,
    reason: str,
    account_id: str = "",
    run_id: str = "",
) -> list[dict[str, Any]]:
    """Finalize honest partial events at Stop/run terminal; memory-only and idempotent."""
    allowed = {
        "partial_manual_stop",
        "partial_worker_stop",
        "partial_error",
        "shadow_internal_error_non_blocking",
    }
    stable_status = status if status in allowed else "partial_worker_stop"
    with _LOCK:
        contexts = [
            context
            for context in list(_CONTEXTS.values())
            if (not account_id or context.binding["account_id"] == str(account_id))
            and (not run_id or context.binding["run_id"] == str(run_id))
        ]
    out = []
    for context in contexts:
        payload = _finalize_context(context, status=stable_status, reason=reason)
        if payload is not None:
            out.append(payload)
    return out


def reset_shadow_state_for_tests() -> None:
    """Test-only registry cleanup."""
    global _ACTIVE_KEY
    with _LOCK:
        _CONTEXTS.clear()
        _TERMINAL_EVENT_IDS.clear()
        _CANDIDATE_INDEX_BY_RUN.clear()
        _PENDING_TERMINALS.clear()
        _ACTIVE_KEY = None
