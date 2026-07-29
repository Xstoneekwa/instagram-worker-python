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


@dataclass(frozen=True)
class HybridSelection:
    mode: str
    usernames: tuple[str, ...] = ()
    reason: str = ""


_DIRECT_SEARCH_FALLBACK_SAFE_REASONS = frozenset(
    {
        "ui_progressive_search_limit_after_recovery",
        "ui_end_of_list_with_candidates_unresolved",
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
    previous_exact_signature = ""
    result_visible_at = ""
    result_visible_monotonic = 0.0
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
        if _search_surface_committed(hierarchy):
            committed_surface_count += 1
        evidence = exact_search_result_evidence(hierarchy, expected)
        count = int(evidence.get("exact_match_count") or 0)
        signature = str(evidence.get("signature") or "")
        if count == 1 and signature:
            if not result_visible_at:
                result_visible_at = _utc_now_iso()
                result_visible_monotonic = time.monotonic()
            stable_exact_poll_count = (
                stable_exact_poll_count + 1
                if signature == previous_exact_signature
                else 1
            )
            previous_exact_signature = signature
        else:
            stable_exact_poll_count = 0
            previous_exact_signature = ""
        log(
            "info",
            "unfollow_direct_exact_result_poll",
            username=expected,
            poll_index=poll_index,
            poll_interval_ms=round(SEARCH_RESULT_POLL_INTERVAL_S * 1000.0, 2),
            deadline_ms=round(SEARCH_RESULT_MAX_WAIT_S * 1000.0, 2),
            exact_match_count=count,
            committed_surface_count=committed_surface_count,
            stable_exact_poll_count=stable_exact_poll_count,
            exact_bounds_present=bool(evidence.get("bounds")),
        )
        if count > 1:
            return {
                "ok": False,
                "status": "ambiguous",
                "reason": "multiple_exact_account_rows",
                "exact_match_count": count,
                "confirmed_surface_count": committed_surface_count,
                "poll_count": observed_poll_count,
            }
        if count == 1 and stable_exact_poll_count >= SEARCH_RESULT_STABLE_EXACT_POLLS:
            return {
                "ok": True,
                "status": "exact_result_visible",
                "reason": "exact_username_result_visible",
                "exact_match_count": 1,
                "confirmed_surface_count": committed_surface_count,
                "poll_count": observed_poll_count,
                "exact_row_bounds": dict(evidence.get("bounds") or {}),
                "result_visible_at": result_visible_at,
                "result_visible_monotonic": result_visible_monotonic,
                "result_stable_at": _utc_now_iso(),
                "stable_exact_poll_count": stable_exact_poll_count,
            }
    return {
        "ok": False,
        "status": "unavailable" if committed_surface_count >= 2 else "retryable",
        "reason": (
            "exact_username_not_found"
            if committed_surface_count >= 2
            else "search_surface_unconfirmed"
        ),
        "exact_match_count": 0,
        "confirmed_surface_count": committed_surface_count,
        "poll_count": observed_poll_count,
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
        total_confirmed_surfaces += int(
            search_result.get("confirmed_surface_count") or 0
        )
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
        result["confirmed_surface_count"] = total_confirmed_surfaces
        result["local_retry_count"] = local_retry_count
        if total_confirmed_surfaces >= 2:
            result["status"] = "unavailable"
            result["reason"] = "unfollow_candidate_account_unavailable"
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
    row_click_started_at = _utc_now_iso()
    transition_started_at = row_click_started_at
    if not tap_account_result(
        device,
        expected,
        preverified_exact_row_bounds=dict(search_result.get("exact_row_bounds") or {}),
        preverified_exact_result_at_monotonic=float(
            search_result.get("result_visible_monotonic") or 0.0
        ),
        preverified_exact_result_method="unfollow_direct_stable_exact_xml",
    ):
        return {
            "ok": False,
            "status": "retryable",
            "reason": "exact_account_row_tap_failed",
            "exact_match_count": 1,
            "local_retry_count": local_retry_count,
        }
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
        "result_visible_at": str(search_result.get("result_visible_at") or ""),
        "result_stable_at": result_stable_at,
        "row_click_at": row_click_started_at,
        "transition_started_at": transition_started_at,
        "profile_opened_at": profile_transition_completed_at,
        "profile_exact_confirmed_at": profile_exact_confirmed_at,
    }
