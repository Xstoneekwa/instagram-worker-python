"""Bounded hybrid selection policy for scalable Unfollow sessions."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from logs import log
from target_followers_progressive_resume_v2 import (
    bounded_anchor_hashes,
    viewport_fingerprint as hmac_viewport_fingerprint,
)
from unfollow_ui_coverage_policy import normalize_username


DIRECT_SEARCH_FALLBACK_BATCH_LIMIT = 10
CURSOR_RESTORE_SCROLL_LIMIT = 10
SEARCH_RESULT_MAX_WAIT_S = 3.5
SEARCH_RESULT_POLL_INTERVAL_S = 0.35
SEARCH_LOCAL_REFRESH_RETRY_LIMIT = 1
SEARCH_RESULT_STABLE_EXACT_POLLS = 2
SEARCH_RESULT_STABLE_NO_RESULTS_POLLS = 2
SEARCH_ROW_TAP_RETRY_LIMIT = 1

SEARCH_EXACT_RESULT_VISIBLE = "SEARCH_EXACT_RESULT_VISIBLE"
SEARCH_NO_RESULTS_CONFIRMED = "SEARCH_NO_RESULTS_CONFIRMED"
SEARCH_RESULTS_LOADING = "SEARCH_RESULTS_LOADING"
SEARCH_SURFACE_UNHEALTHY = "SEARCH_SURFACE_UNHEALTHY"


@dataclass(frozen=True)
class HybridSelection:
    mode: str
    usernames: tuple[str, ...] = ()
    reason: str = ""


_DIRECT_SEARCH_FALLBACK_SAFE_REASONS = frozenset(
    {
        "ui_progressive_search_limit_after_recovery",
        "ui_end_of_list_with_candidates_unresolved",
        "ui_coverage_budget_exhausted_with_actionable_remaining",
    }
)


def can_arm_direct_search_fallback(stop_reason: str, *, remaining_count: int) -> bool:
    """Allow direct search only after the primary list path is proved exhausted."""
    return bool(
        max(0, int(remaining_count or 0)) > 0
        and str(stop_reason or "").strip() in _DIRECT_SEARCH_FALLBACK_SAFE_REASONS
    )


def choose_hybrid_selection(
    remaining_usernames: Iterable[str],
    *,
    scan_exhausted: bool = False,
    already_direct_searched: Iterable[str] = (),
) -> HybridSelection:
    remaining = sorted(
        {
            item
            for item in (normalize_username(value) for value in remaining_usernames)
            if item
        }
    )
    searched = {
        item
        for item in (normalize_username(value) for value in already_direct_searched)
        if item
    }
    pending = tuple(item for item in remaining if item not in searched)
    if not pending and remaining:
        return HybridSelection(
            "partial_resumable",
            (),
            "direct_exact_candidates_unresolved",
        )
    if not pending:
        return HybridSelection("complete", (), "no_remaining_direct_candidate")
    if scan_exhausted:
        return HybridSelection(
            "direct_exact",
            pending[:DIRECT_SEARCH_FALLBACK_BATCH_LIMIT],
            "progressive_scan_exhausted_bounded_fallback",
        )
    # The own Following list is the primary discovery path at every backlog
    # size.  A small remainder is not evidence that progressive discovery has
    # failed: the current viewport may already contain the next candidate.
    return HybridSelection(
        "progressive_scan",
        (),
        "progressive_scan_primary_not_exhausted",
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_bounds(raw: str) -> dict[str, int]:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", str(raw or ""))
    if not match:
        return {}
    left, top, right, bottom = (int(value) for value in match.groups())
    if right <= left or bottom <= top:
        return {}
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _node_value(node: ET.Element) -> str:
    return str(node.attrib.get("text") or node.attrib.get("content-desc") or "")


def _normalized_ui_label(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold()).rstrip(
        ".:!…"
    )


def _is_search_query_node(node: ET.Element) -> bool:
    resource_id = str(node.attrib.get("resource-id") or "").casefold()
    class_name = str(node.attrib.get("class") or "").casefold()
    return bool(
        class_name.endswith("edittext")
        or any(
            token in resource_id
            for token in (
                "action_bar_search_edit_text",
                "search_edit_text",
                "search_src_text",
            )
        )
    )


def _clickable_ancestor_bounds(
    node: ET.Element,
    parent_by_id: dict[int, ET.Element],
) -> dict[str, int]:
    """Return the result row hit target, never a fixed coordinate."""
    current: ET.Element | None = node
    fallback: dict[str, int] = {}
    for _ in range(5):
        if current is None:
            break
        bounds = _parse_bounds(str(current.attrib.get("bounds") or ""))
        if bounds and not fallback:
            fallback = bounds
        if bounds and str(current.attrib.get("clickable") or "").casefold() == "true":
            return bounds
        current = parent_by_id.get(id(current))
    return fallback


def _bounds_signature(bounds: dict[str, int]) -> str:
    return ":".join(
        str(int(bounds.get(key) or 0))
        for key in ("left", "top", "right", "bottom")
    )


def _bounds_compatible(
    previous: dict[str, int],
    current: dict[str, int],
) -> bool:
    """Accept small layout motion while keeping distinct result rows separate."""
    if not previous or not current:
        return False
    previous_width = int(previous["right"]) - int(previous["left"])
    current_width = int(current["right"]) - int(current["left"])
    previous_height = int(previous["bottom"]) - int(previous["top"])
    current_height = int(current["bottom"]) - int(current["top"])
    if min(previous_width, current_width, previous_height, current_height) <= 0:
        return False
    horizontal_overlap = max(
        0,
        min(int(previous["right"]), int(current["right"]))
        - max(int(previous["left"]), int(current["left"])),
    )
    previous_center_y = (int(previous["top"]) + int(previous["bottom"])) / 2.0
    current_center_y = (int(current["top"]) + int(current["bottom"])) / 2.0
    max_center_delta = max(24.0, min(72.0, max(previous_height, current_height) * 0.75))
    return bool(
        horizontal_overlap >= min(previous_width, current_width) * 0.50
        and abs(previous_center_y - current_center_y) <= max_center_delta
    )


def _surface_bounds(root: ET.Element) -> dict[str, int]:
    """Derive the same-snapshot display bounds without another device RPC."""
    parsed = [
        bounds
        for node in root.iter()
        for bounds in [_parse_bounds(str(node.attrib.get("bounds") or ""))]
        if bounds
    ]
    if not parsed:
        return {}
    return {
        # Android hierarchy coordinates are absolute even when the snapshot
        # omits status/navigation bar nodes at origin.
        "left": 0,
        "top": 0,
        "right": max(int(bounds["right"]) for bounds in parsed),
        "bottom": max(int(bounds["bottom"]) for bounds in parsed),
    }


def _logical_exact_result_rows(
    matches: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Collapse duplicate XML representations of one physical Search row."""
    logical_rows: list[dict[str, object]] = []
    for match in matches:
        bounds = dict(match.get("bounds") or {})
        if not bounds:
            continue
        existing = next(
            (
                row
                for row in logical_rows
                if _bounds_compatible(dict(row.get("bounds") or {}), bounds)
            ),
            None,
        )
        if existing is None:
            logical_rows.append(match)
            continue
        # Keep the latest canonical hit target.  Both members are already
        # exact matches in the same row band, so this does not merge usernames.
        if bool(match.get("canonical_username_rid")) and not bool(
            existing.get("canonical_username_rid")
        ):
            existing.clear()
            existing.update(match)
    return logical_rows


def classify_search_surface_xml(
    hierarchy_xml: str,
    expected_username: str,
) -> dict[str, object]:
    """Classify one exact-query Search snapshot without approximate matches."""
    expected = normalize_username(expected_username)
    if not expected:
        return {
            "state": SEARCH_SURFACE_UNHEALTHY,
            "reason": "invalid_expected_username",
            "exact_match_count": 0,
            "bounds": {},
        }
    try:
        root = ET.fromstring(str(hierarchy_xml or ""))
    except ET.ParseError:
        return {
            "state": SEARCH_SURFACE_UNHEALTHY,
            "reason": "search_hierarchy_unparseable",
            "exact_match_count": 0,
            "bounds": {},
        }

    parent_by_id = {id(child): parent for parent in root.iter() for child in parent}
    query_nodes = [node for node in root.iter() if _is_search_query_node(node)]
    query_exact = any(normalize_username(_node_value(node)) == expected for node in query_nodes)
    if not query_exact:
        return {
            "state": SEARCH_SURFACE_UNHEALTHY,
            "reason": (
                "search_query_field_missing"
                if not query_nodes
                else "search_query_field_mismatch"
            ),
            "exact_match_count": 0,
            "bounds": {},
            "query_field_confirmed": False,
        }

    query_bottom = max(
        (
            int(bounds.get("bottom") or 0)
            for node in query_nodes
            for bounds in [_parse_bounds(str(node.attrib.get("bounds") or ""))]
            if bounds
        ),
        default=0,
    )
    matches: list[dict[str, object]] = []
    query_node_ids = {id(node) for node in query_nodes}
    for node in root.iter():
        if id(node) in query_node_ids:
            continue
        if normalize_username(_node_value(node)) != expected:
            continue
        own_bounds = _parse_bounds(str(node.attrib.get("bounds") or ""))
        if query_bottom and own_bounds and int(own_bounds.get("top") or 0) < query_bottom:
            continue
        bounds = _clickable_ancestor_bounds(node, parent_by_id)
        if bounds:
            resource_id = str(node.attrib.get("resource-id") or "")
            matches.append(
                {
                    "bounds": bounds,
                    "canonical_username_rid": bool(
                        re.search(
                            r"(?:^|[:/])id/row_search_user_username$",
                            resource_id,
                        )
                    ),
                }
            )

    # Text and accessibility content can duplicate the same row.  Enforce one
    # unique clickable hit target, not one XML label node.
    by_bounds: dict[tuple[int, int, int, int], dict[str, object]] = {}
    for match in matches:
        bounds = dict(match.get("bounds") or {})
        key = tuple(
            int(bounds.get(name) or 0)
            for name in ("left", "top", "right", "bottom")
        )
        by_bounds[key] = match
    unique_matches = _logical_exact_result_rows(list(by_bounds.values()))
    if len(unique_matches) == 1:
        bounds = dict(unique_matches[0].get("bounds") or {})
        return {
            "state": SEARCH_EXACT_RESULT_VISIBLE,
            "reason": "exact_username_result_visible",
            "exact_match_count": 1,
            "bounds": bounds,
            "signature": (
                f"{expected}:{bounds.get('left')}:{bounds.get('top')}:"
                f"{bounds.get('right')}:{bounds.get('bottom')}"
            ),
            "query_field_confirmed": True,
            "screen_bounds": _surface_bounds(root),
        }
    if len(unique_matches) > 1:
        return {
            "state": SEARCH_SURFACE_UNHEALTHY,
            "reason": "multiple_exact_account_rows",
            "exact_match_count": len(unique_matches),
            "bounds": {},
            "query_field_confirmed": True,
            "screen_bounds": _surface_bounds(root),
        }

    no_results_markers = {
        "no results",
        "no results found",
        "aucun résultat",
        "aucun resultat",
        "sin resultados",
        "nenhum resultado",
    }
    if any(_normalized_ui_label(_node_value(node)) in no_results_markers for node in root.iter()):
        return {
            "state": SEARCH_NO_RESULTS_CONFIRMED,
            "reason": "username_not_found_confirmed",
            "exact_match_count": 0,
            "bounds": {},
            "query_field_confirmed": True,
        }
    return {
        "state": SEARCH_RESULTS_LOADING,
        "reason": "search_results_loading",
        "exact_match_count": 0,
        "bounds": {},
        "query_field_confirmed": True,
    }


def exact_search_result_evidence(
    hierarchy_xml: str,
    expected_username: str,
) -> dict[str, object]:
    """Return one exact row and its fresh bounds; duplicates fail closed."""
    expected = normalize_username(expected_username)
    if not expected:
        return {"exact_match_count": 0, "bounds": {}, "signature": ""}
    try:
        root = ET.fromstring(str(hierarchy_xml or ""))
    except ET.ParseError:
        return {"exact_match_count": 0, "bounds": {}, "signature": ""}
    matches: list[dict[str, object]] = []
    for node in root.iter():
        rid = str(node.attrib.get("resource-id") or "")
        if not re.search(r"(?:^|[:/])id/row_search_user_username$", rid):
            continue
        value = normalize_username(
            str(node.attrib.get("text") or node.attrib.get("content-desc") or "")
        )
        if value == expected:
            bounds = _parse_bounds(str(node.attrib.get("bounds") or ""))
            matches.append(
                {
                    "bounds": bounds,
                    "resource_id": rid,
                    "text": str(node.attrib.get("text") or ""),
                }
            )
    if len(matches) != 1:
        return {"exact_match_count": len(matches), "bounds": {}, "signature": ""}
    match = matches[0]
    bounds = dict(match.get("bounds") or {})
    signature = (
        f"{expected}:{bounds.get('left')}:{bounds.get('top')}:"
        f"{bounds.get('right')}:{bounds.get('bottom')}"
        if bounds
        else ""
    )
    return {
        "exact_match_count": 1,
        "bounds": bounds,
        "signature": signature,
        "resource_id": str(match.get("resource_id") or ""),
    }


def exact_search_result_count(hierarchy_xml: str, expected_username: str) -> int:
    """Count exact account rows; zero/unparseable and duplicates fail closed."""
    return int(
        exact_search_result_evidence(hierarchy_xml, expected_username).get(
            "exact_match_count", 0
        )
        or 0
    )


def _search_surface_committed(hierarchy_xml: str) -> bool:
    value = str(hierarchy_xml or "")
    lowered = value.casefold()
    return bool(
        "row_search_user_username" in value
        or "no results" in lowered
        or "aucun résultat" in lowered
        or "aucun resultat" in lowered
    )


def _wait_for_exact_search_result(device: object, expected: str) -> dict[str, object]:
    """Poll one typed query until an exact row appears or absence is proved."""

    max_polls = max(
        2,
        int(SEARCH_RESULT_MAX_WAIT_S / SEARCH_RESULT_POLL_INTERVAL_S),
    )
    committed_surface_count = 0
    observed_poll_count = 0
    stable_exact_poll_count = 0
    stable_no_results_poll_count = 0
    previous_exact_signature = ""
    previous_exact_bounds: dict[str, int] = {}
    result_visible_at = ""
    result_visible_monotonic = 0.0
    last_state = SEARCH_RESULTS_LOADING
    last_reason = "search_results_loading"
    last_specific_unhealthy_reason = ""
    for poll_index in range(1, max_polls + 1):
        time.sleep(SEARCH_RESULT_POLL_INTERVAL_S)
        try:
            try:
                hierarchy = str(device.dump_hierarchy(compressed=False) or "")
            except TypeError:
                hierarchy = str(device.dump_hierarchy() or "")
        except Exception:
            hierarchy = ""
        observed_poll_count = poll_index
        classification = classify_search_surface_xml(hierarchy, expected)
        state = str(classification.get("state") or SEARCH_SURFACE_UNHEALTHY)
        last_state = state
        last_reason = str(classification.get("reason") or "search_surface_unhealthy")
        if (
            state == SEARCH_SURFACE_UNHEALTHY
            and last_reason not in {"search_hierarchy_unparseable", "search_surface_unhealthy"}
        ):
            last_specific_unhealthy_reason = last_reason
        if state in {SEARCH_EXACT_RESULT_VISIBLE, SEARCH_NO_RESULTS_CONFIRMED}:
            committed_surface_count += 1
        count = int(classification.get("exact_match_count") or 0)
        signature = str(classification.get("signature") or "")
        if state == SEARCH_EXACT_RESULT_VISIBLE and count == 1 and signature:
            current_bounds = dict(classification.get("bounds") or {})
            compatible = _bounds_compatible(previous_exact_bounds, current_bounds)
            if not result_visible_at or (previous_exact_bounds and not compatible):
                result_visible_at = _utc_now_iso()
                result_visible_monotonic = time.monotonic()
            stable_exact_poll_count = (
                stable_exact_poll_count + 1
                if signature == previous_exact_signature or compatible
                else 1
            )
            previous_exact_signature = signature
            previous_exact_bounds = current_bounds
        else:
            stable_exact_poll_count = 0
            previous_exact_signature = ""
            previous_exact_bounds = {}
        stable_no_results_poll_count = (
            stable_no_results_poll_count + 1
            if state == SEARCH_NO_RESULTS_CONFIRMED
            else 0
        )
        log(
            "info",
            "unfollow_direct_exact_result_poll",
            username=expected,
            poll_index=poll_index,
            poll_interval_ms=round(SEARCH_RESULT_POLL_INTERVAL_S * 1000.0, 2),
            deadline_ms=round(SEARCH_RESULT_MAX_WAIT_S * 1000.0, 2),
            exact_match_count=count,
            search_surface_state=state,
            committed_surface_count=committed_surface_count,
            stable_exact_poll_count=stable_exact_poll_count,
            stable_no_results_poll_count=stable_no_results_poll_count,
            exact_bounds_present=bool(classification.get("bounds")),
        )
        if state == SEARCH_SURFACE_UNHEALTHY and count > 1:
            return {
                "ok": False,
                "status": "search_surface_unhealthy",
                "reason": str(
                    classification.get("reason") or "multiple_exact_account_rows"
                ),
                "search_surface_state": SEARCH_SURFACE_UNHEALTHY,
                "exact_match_count": count,
                "confirmed_surface_count": committed_surface_count,
                "poll_count": observed_poll_count,
            }
        if (
            state == SEARCH_EXACT_RESULT_VISIBLE
            and stable_exact_poll_count >= SEARCH_RESULT_STABLE_EXACT_POLLS
        ):
            result_stable_monotonic = time.monotonic()
            return {
                "ok": True,
                "status": "exact_result_visible",
                "reason": "exact_username_result_visible",
                "search_surface_state": SEARCH_EXACT_RESULT_VISIBLE,
                "exact_match_count": 1,
                "confirmed_surface_count": committed_surface_count,
                "poll_count": observed_poll_count,
                "exact_row_bounds": dict(classification.get("bounds") or {}),
                "screen_bounds": dict(classification.get("screen_bounds") or {}),
                "result_visible_at": result_visible_at,
                "result_visible_monotonic": result_visible_monotonic,
                "result_stable_at": _utc_now_iso(),
                "result_stable_monotonic": result_stable_monotonic,
                "stable_exact_poll_count": stable_exact_poll_count,
            }
        if stable_no_results_poll_count >= SEARCH_RESULT_STABLE_NO_RESULTS_POLLS:
            return {
                "ok": False,
                "status": "username_not_found_confirmed",
                "reason": "username_not_found_confirmed",
                "search_surface_state": SEARCH_NO_RESULTS_CONFIRMED,
                "exact_match_count": 0,
                "confirmed_surface_count": committed_surface_count,
                "poll_count": observed_poll_count,
                "stable_no_results_poll_count": stable_no_results_poll_count,
            }
    return {
        "ok": False,
        "status": "search_surface_unhealthy",
        "reason": (
            "search_results_loading_timeout"
            if last_state == SEARCH_RESULTS_LOADING
            else last_specific_unhealthy_reason or last_reason
        ),
        "search_surface_state": last_state,
        "exact_match_count": 0,
        "confirmed_surface_count": committed_surface_count,
        "poll_count": observed_poll_count,
    }


def _live_exact_accessibility_result(
    device: object,
    expected: str,
) -> dict[str, object]:
    """Confirm one exact live accessibility row when XML polling is stale.

    The selector used by ``find_real_account_text_element`` is exact.  Two
    consecutive observations of the same valid bounds are still required so
    this fallback cannot turn a transient or approximate result into a tap.
    """
    from instagram_navigation import find_real_account_text_element

    stable_count = 0
    previous_signature = ""
    previous_bounds: dict[str, int] = {}
    first_visible_at = ""
    first_visible_monotonic = 0.0
    for probe_index in range(1, SEARCH_RESULT_STABLE_EXACT_POLLS + 1):
        try:
            element = find_real_account_text_element(
                device,
                expected,
                dump_on_failure=False,
                follow_ct_search_context=False,
                trace_context={
                    "phase": "unfollow",
                    "method": "accessibility_live_exact_fallback",
                    "probe_index": probe_index,
                },
            )
            raw_bounds = dict(getattr(element, "info", {}).get("bounds") or {})
            bounds = {
                key: int(raw_bounds[key])
                for key in ("left", "top", "right", "bottom")
            }
        except Exception:
            bounds = {}
        if (
            not bounds
            or bounds["right"] <= bounds["left"]
            or bounds["bottom"] <= bounds["top"]
        ):
            stable_count = 0
            previous_signature = ""
            break
        signature = _bounds_signature(bounds)
        compatible = _bounds_compatible(previous_bounds, bounds)
        if not first_visible_at or (previous_bounds and not compatible):
            first_visible_at = _utc_now_iso()
            first_visible_monotonic = time.monotonic()
        stable_count = (
            stable_count + 1
            if signature == previous_signature or compatible
            else 1
        )
        previous_signature = signature
        previous_bounds = bounds
        log(
            "info",
            "unfollow_direct_exact_accessibility_probe",
            username=expected,
            probe_index=probe_index,
            stable_exact_poll_count=stable_count,
            exact_bounds_present=True,
        )
        if probe_index < SEARCH_RESULT_STABLE_EXACT_POLLS:
            time.sleep(SEARCH_RESULT_POLL_INTERVAL_S)
    if stable_count >= SEARCH_RESULT_STABLE_EXACT_POLLS:
        result_stable_monotonic = time.monotonic()
        return {
            "ok": True,
            "status": "exact_result_visible",
            "reason": "exact_username_result_visible_accessibility_live",
            "search_surface_state": SEARCH_EXACT_RESULT_VISIBLE,
            "exact_match_count": 1,
            "confirmed_surface_count": 0,
            "poll_count": stable_count,
            "exact_row_bounds": bounds,
            "result_visible_at": first_visible_at,
            "result_visible_monotonic": first_visible_monotonic,
            "result_stable_at": _utc_now_iso(),
            "result_stable_monotonic": result_stable_monotonic,
            "stable_exact_poll_count": stable_count,
            "exact_result_method": "unfollow_direct_stable_exact_accessibility_live",
        }
    return {
        "ok": False,
        "status": "search_surface_unhealthy",
        "reason": "accessibility_live_exact_result_unconfirmed",
        "search_surface_state": SEARCH_SURFACE_UNHEALTHY,
        "exact_match_count": 0,
        "confirmed_surface_count": 0,
        "poll_count": stable_count,
    }


def build_cursor_checkpoint(
    visible_usernames: Iterable[str],
    *,
    depth: int,
    generation: int,
) -> dict[str, object]:
    visible = tuple(
        item
        for item in (normalize_username(value) for value in visible_usernames)
        if item
    )
    return {
        "cursor_schema": "UNFOLLOW_CURSOR_V2",
        "depth": max(0, int(depth)),
        "generation": max(1, int(generation)),
        "anchor_hashes": list(bounded_anchor_hashes(visible)),
        "viewport_fingerprint_hmac": hmac_viewport_fingerprint(visible),
        "recent_visible_count": min(len(visible), 12),
        "restore_scroll_limit": CURSOR_RESTORE_SCROLL_LIMIT,
    }


def cursor_anchor_matches(
    visible_usernames: Iterable[str],
    checkpoint: dict[str, object] | None,
) -> bool:
    if not isinstance(checkpoint, dict):
        return False
    expected = {
        str(value)
        for value in list(checkpoint.get("anchor_hashes") or [])[:12]
        if str(value).startswith("a3:")
    }
    if not expected:
        return False
    current = set(bounded_anchor_hashes(visible_usernames))
    return bool(expected.intersection(current))


def open_exact_profile_for_unfollow(device: object, username: str) -> dict[str, object]:
    """Open one exact Instagram account result, never an ambiguous row."""
    from instagram_navigation import open_search, tap_account_result, type_search
    from unfollow_profile_probe import verify_unfollow_target_profile_strict

    expected = normalize_username(username)
    if not expected:
        return {"ok": False, "status": "unavailable", "reason": "invalid_username"}
    search_opened = open_search(
        device,
        caller_context="unfollow_direct_exact",
        allow_percent_fallback=False,
    )
    if not search_opened:
        # A selector click only proves that the input was sent.  The natural
        # 2026-07-28 run showed the exact search-tab selector click succeeding
        # while the EditText transition never committed.  Permit one bounded,
        # selector-only retry; never fall back to coordinates.
        log(
            "warning",
            "unfollow_direct_open_search_retry_started",
            username=expected,
            reason="search_tab_click_without_committed_search_field",
            max_retries=1,
        )
        search_opened = open_search(
            device,
            caller_context="unfollow_direct_exact_retry",
            allow_percent_fallback=False,
        )
        log(
            "info",
            "unfollow_direct_open_search_retry_completed",
            username=expected,
            ok=bool(search_opened),
            reason=(
                "search_surface_committed_after_bounded_retry"
                if search_opened
                else "search_surface_uncommitted_after_bounded_retry"
            ),
        )
    if not search_opened:
        return {
            "ok": False,
            "status": "retryable",
            "reason": "open_search_failed_after_bounded_retry",
        }
    search_result: dict[str, object] = {}
    local_retry_count = 0
    total_confirmed_surfaces = 0
    last_specific_search_failure_reason = ""
    for search_attempt in range(1, SEARCH_LOCAL_REFRESH_RETRY_LIMIT + 2):
        if not type_search(device, expected):
            search_result = {
                "ok": False,
                "status": "retryable",
                "reason": "type_search_failed",
                "exact_match_count": 0,
            }
        else:
            search_result = _wait_for_exact_search_result(device, expected)
            if (
                not bool(search_result.get("ok"))
                and str(search_result.get("status") or "")
                == "search_surface_unhealthy"
                and str(search_result.get("reason") or "")
                in {
                    "search_results_loading_timeout",
                    "search_hierarchy_unparseable",
                }
            ):
                live_result = _live_exact_accessibility_result(device, expected)
                if bool(live_result.get("ok")):
                    search_result = live_result
        total_confirmed_surfaces += int(
            search_result.get("confirmed_surface_count") or 0
        )
        current_failure_reason = str(search_result.get("reason") or "")
        if current_failure_reason not in {
            "",
            "search_hierarchy_unparseable",
            "search_surface_unhealthy",
        }:
            last_specific_search_failure_reason = current_failure_reason
        if bool(search_result.get("ok")):
            break
        if search_attempt > SEARCH_LOCAL_REFRESH_RETRY_LIMIT:
            break
        local_retry_count += 1
        log(
            "warning",
            "unfollow_candidate_local_search_retry_started",
            username=expected,
            retry_index=local_retry_count,
            max_retries=SEARCH_LOCAL_REFRESH_RETRY_LIMIT,
            previous_status=str(search_result.get("status") or ""),
            previous_reason=str(search_result.get("reason") or ""),
            refresh_method="clear_and_retype_exact_username",
        )
    if not bool(search_result.get("ok")):
        result = dict(search_result)
        if (
            str(result.get("reason") or "")
            in {"search_hierarchy_unparseable", "search_surface_unhealthy"}
            and last_specific_search_failure_reason
        ):
            result["reason"] = last_specific_search_failure_reason
        result["confirmed_surface_count"] = total_confirmed_surfaces
        result["local_retry_count"] = local_retry_count
        return result
    result_stable_at = str(search_result.get("result_stable_at") or _utc_now_iso())
    log(
        "info",
        "unfollow_direct_exact_result_stable",
        username=expected,
        result_visible_at=str(search_result.get("result_visible_at") or ""),
        result_stable_at=result_stable_at,
        stable_exact_poll_count=int(search_result.get("stable_exact_poll_count") or 0),
        exact_row_bounds_present=bool(search_result.get("exact_row_bounds")),
    )
    profile: dict[str, object] | None = None
    row_click_started_at = _utc_now_iso()
    transition_started_at = row_click_started_at
    row_tap_retry_count = 0
    while True:
        tap_ok = tap_account_result(
            device,
            expected,
            preverified_exact_row_bounds=dict(
                search_result.get("exact_row_bounds") or {}
            ),
            preverified_exact_screen_bounds=dict(
                search_result.get("screen_bounds") or {}
            ),
            preverified_exact_result_at_monotonic=float(
                search_result.get("result_stable_monotonic") or 0.0
            ),
            preverified_exact_result_method=str(
                search_result.get("exact_result_method")
                or "unfollow_direct_stable_exact_xml"
            ),
        )
        # A delayed transition may have completed after tap_account_result's
        # bounded signal wait.  A reported tap success also needs exact profile
        # proof: the natural failure returned True even though the Search row
        # never transitioned.  Only retry while the exact Search surface can
        # be freshly re-proven.
        profile = verify_unfollow_target_profile_strict(
            device,
            expected_target_username=expected,
        )
        if bool(profile.get("ok")):
            break
        if row_tap_retry_count >= SEARCH_ROW_TAP_RETRY_LIMIT:
            return {
                "ok": False,
                "status": "ambiguous" if tap_ok else "retryable",
                "reason": (
                    str(
                        profile.get("failure_reason")
                        or "profile_identity_unconfirmed"
                    )
                    if tap_ok
                    else "search_exact_result_click_failed"
                ),
                "exact_match_count": 1,
                "local_retry_count": local_retry_count,
                "row_tap_retry_count": row_tap_retry_count,
            }
        row_tap_retry_count += 1
        log(
            "warning",
            "unfollow_direct_exact_row_tap_retry_started",
            username=expected,
            retry_index=row_tap_retry_count,
            max_retries=SEARCH_ROW_TAP_RETRY_LIMIT,
        )
        refreshed_result = _wait_for_exact_search_result(device, expected)
        if not bool(refreshed_result.get("ok")):
            return {
                "ok": False,
                "status": "ambiguous" if tap_ok else "retryable",
                "reason": (
                    str(
                        profile.get("failure_reason")
                        or "profile_identity_unconfirmed"
                    )
                    if tap_ok
                    else "search_exact_result_bounds_stale"
                ),
                "exact_match_count": 1,
                "local_retry_count": local_retry_count,
                "row_tap_retry_count": row_tap_retry_count,
                "refresh_reason": str(refreshed_result.get("reason") or ""),
            }
        search_result = refreshed_result
        result_stable_at = str(search_result.get("result_stable_at") or "")
    profile_transition_completed_at = _utc_now_iso()
    log(
        "info",
        "unfollow_direct_exact_row_click_completed",
        username=expected,
        result_visible_at=str(search_result.get("result_visible_at") or ""),
        result_stable_at=result_stable_at,
        row_click_started_at=row_click_started_at,
        transition_started_at=transition_started_at,
        profile_transition_completed_at=profile_transition_completed_at,
    )
    if profile is None or not bool(profile.get("ok")):
        profile = verify_unfollow_target_profile_strict(
            device,
            expected_target_username=expected,
        )
    if not bool(profile.get("ok")):
        return {
            "ok": False,
            "status": "ambiguous",
            "reason": str(profile.get("failure_reason") or "profile_identity_unconfirmed"),
            "exact_match_count": 1,
            "local_retry_count": local_retry_count,
            "row_tap_retry_count": row_tap_retry_count,
        }
    profile_exact_confirmed_at = _utc_now_iso()
    log(
        "info",
        "unfollow_direct_exact_profile_confirmed",
        username=expected,
        profile_transition_completed_at=profile_transition_completed_at,
        profile_exact_confirmed_at=profile_exact_confirmed_at,
        verification_method=str(profile.get("verification_method") or ""),
    )
    return {
        "ok": True,
        "status": "profile_opened",
        "reason": "exact_username_profile_verified",
        "exact_match_count": 1,
        "local_retry_count": local_retry_count,
        "row_tap_retry_count": row_tap_retry_count,
        "result_visible_at": str(search_result.get("result_visible_at") or ""),
        "result_stable_at": result_stable_at,
        "row_click_at": row_click_started_at,
        "transition_started_at": transition_started_at,
        "profile_opened_at": profile_transition_completed_at,
        "profile_exact_confirmed_at": profile_exact_confirmed_at,
    }
