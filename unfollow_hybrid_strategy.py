"""Bounded hybrid selection policy for scalable Unfollow sessions."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable

from target_followers_progressive_resume_v2 import (
    bounded_anchor_hashes,
    viewport_fingerprint as hmac_viewport_fingerprint,
)
from unfollow_ui_coverage_policy import normalize_username


DIRECT_SEARCH_REMAINING_LIMIT = 10
DIRECT_SEARCH_FALLBACK_BATCH_LIMIT = 10
CURSOR_RESTORE_SCROLL_LIMIT = 10
SEARCH_RESULT_INITIAL_SETTLE_S = 0.6
SEARCH_RESULT_CONFIRM_MISSING_S = 0.8


@dataclass(frozen=True)
class HybridSelection:
    mode: str
    usernames: tuple[str, ...] = ()
    reason: str = ""


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
    if len(remaining) <= DIRECT_SEARCH_REMAINING_LIMIT:
        return HybridSelection(
            "direct_exact",
            pending[:DIRECT_SEARCH_REMAINING_LIMIT],
            "remaining_at_or_below_direct_threshold",
        )
    if scan_exhausted:
        return HybridSelection(
            "direct_exact",
            pending[:DIRECT_SEARCH_FALLBACK_BATCH_LIMIT],
            "progressive_scan_exhausted_bounded_fallback",
        )
    return HybridSelection("progressive_scan", (), "remaining_above_direct_threshold")


def exact_search_result_count(hierarchy_xml: str, expected_username: str) -> int:
    """Count exact account rows; zero/unparseable and duplicates fail closed."""
    expected = normalize_username(expected_username)
    if not expected:
        return 0
    try:
        root = ET.fromstring(str(hierarchy_xml or ""))
    except ET.ParseError:
        return 0
    matches = 0
    for node in root.iter():
        rid = str(node.attrib.get("resource-id") or "")
        if not re.search(r"(?:^|[:/])id/row_search_user_username$", rid):
            continue
        value = normalize_username(
            str(node.attrib.get("text") or node.attrib.get("content-desc") or "")
        )
        if value == expected:
            matches += 1
    return matches


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
    if not open_search(
        device,
        caller_context="unfollow_direct_exact",
        allow_percent_fallback=False,
    ):
        return {"ok": False, "status": "retryable", "reason": "open_search_failed"}
    if not type_search(device, expected):
        return {"ok": False, "status": "retryable", "reason": "type_search_failed"}

    def dump_search_hierarchy() -> str:
        try:
            try:
                return str(device.dump_hierarchy(compressed=False) or "")
            except TypeError:
                return str(device.dump_hierarchy() or "")
        except Exception:
            return ""

    time.sleep(SEARCH_RESULT_INITIAL_SETTLE_S)
    hierarchies = [dump_search_hierarchy()]
    count = exact_search_result_count(hierarchies[0], expected)
    if count == 0:
        # Search results can attach one render late.  Never classify a stale
        # one-shot dump as a renamed/deleted account.
        time.sleep(SEARCH_RESULT_CONFIRM_MISSING_S)
        hierarchies.append(dump_search_hierarchy())
        count = exact_search_result_count(hierarchies[-1], expected)
    if count > 1:
        return {
            "ok": False,
            "status": "ambiguous",
            "reason": "multiple_exact_account_rows",
            "exact_match_count": count,
        }
    if count == 0:
        # Two independently committed surfaces without the exact handle prove
        # unavailable/renamed.  Anything weaker remains retryable.
        committed = all(
            bool("row_search_user_username" in hierarchy or "No results" in hierarchy)
            for hierarchy in hierarchies
        ) and len(hierarchies) >= 2
        return {
            "ok": False,
            "status": "unavailable" if committed else "retryable",
            "reason": "exact_username_not_found" if committed else "search_surface_unconfirmed",
            "exact_match_count": 0,
            "confirmed_surface_count": sum(
                bool("row_search_user_username" in hierarchy or "No results" in hierarchy)
                for hierarchy in hierarchies
            ),
        }
    if not tap_account_result(device, expected):
        return {
            "ok": False,
            "status": "retryable",
            "reason": "exact_account_row_tap_failed",
            "exact_match_count": 1,
        }
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
        }
    return {
        "ok": True,
        "status": "profile_opened",
        "reason": "exact_username_profile_verified",
        "exact_match_count": 1,
    }
