"""Shared fail-closed profile CTA stabilization primitives.

The module is deliberately device-agnostic.  Callers acquire a fresh UI
observation, then use these pure helpers to decide whether the *fresh* bounds
may be consumed.  Old bounds are never returned as tap authority.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Iterable


_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def parse_android_bounds(value: Any) -> dict[str, int] | None:
    if isinstance(value, dict):
        try:
            left = int(value.get("left", 0))
            top = int(value.get("top", 0))
            right = int(value.get("right", 0))
            bottom = int(value.get("bottom", 0))
        except (TypeError, ValueError):
            return None
    else:
        match = _BOUNDS_RE.fullmatch(str(value or "").strip())
        if not match:
            return None
        left, top, right, bottom = map(int, match.groups())
    if right <= left or bottom <= top:
        return None
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def center_shift_metrics(
    initial_bounds: dict[str, Any],
    refreshed_bounds: dict[str, Any],
) -> tuple[int, int, int]:
    initial = parse_android_bounds(initial_bounds)
    refreshed = parse_android_bounds(refreshed_bounds)
    if initial is None or refreshed is None:
        return 0, 0, 0
    initial_x = (initial["left"] + initial["right"]) // 2
    initial_y = (initial["top"] + initial["bottom"]) // 2
    refreshed_x = (refreshed["left"] + refreshed["right"]) // 2
    refreshed_y = (refreshed["top"] + refreshed["bottom"]) // 2
    delta_x = refreshed_x - initial_x
    delta_y = refreshed_y - initial_y
    return delta_x, delta_y, max(abs(delta_x), abs(delta_y))


def normalize_cta_semantic(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def validate_profile_cta_transition(
    initial: dict[str, Any],
    refreshed: dict[str, Any],
    *,
    accepted_semantics: Iterable[str],
    max_center_shift_px: int,
    require_profile_identity: bool = True,
) -> dict[str, Any]:
    """Validate a fresh CTA observation and return only its fresh bounds."""

    initial_bounds = parse_android_bounds(initial.get("bounds"))
    fresh_bounds = parse_android_bounds(refreshed.get("bounds"))
    delta_x, delta_y, shift = center_shift_metrics(
        initial_bounds or {}, fresh_bounds or {}
    )
    base = {
        "ok": False,
        "reason": "profile_cta_revalidation_failed",
        "fresh_bounds": fresh_bounds or {},
        "old_bounds_reused": False,
        "center_delta_x": delta_x,
        "center_delta_y": delta_y,
        "bounds_shift_px": shift,
    }
    if initial_bounds is None or fresh_bounds is None:
        return {**base, "reason": "profile_cta_bounds_invalid"}
    if require_profile_identity and not bool(refreshed.get("profile_identity_ok")):
        return {**base, "reason": "profile_identity_changed"}
    if bool(refreshed.get("overlay_present")):
        return {**base, "reason": "profile_overlay_present"}
    if not bool(refreshed.get("clickable")):
        return {**base, "reason": "profile_cta_no_longer_clickable"}
    initial_resource = str(initial.get("resource_id") or "")
    fresh_resource = str(refreshed.get("resource_id") or "")
    if not fresh_resource or (initial_resource and fresh_resource != initial_resource):
        return {**base, "reason": "profile_cta_semantic_identity_changed"}
    initial_package = str(initial.get("package") or "")
    fresh_package = str(refreshed.get("package") or "")
    initial_activity = str(initial.get("activity") or "")
    fresh_activity = str(refreshed.get("activity") or "")
    if initial_package and fresh_package != initial_package:
        return {**base, "reason": "profile_package_changed"}
    if initial_activity and fresh_activity != initial_activity:
        return {**base, "reason": "profile_activity_changed"}
    accepted = {normalize_cta_semantic(item) for item in accepted_semantics}
    semantic = normalize_cta_semantic(
        refreshed.get("semantic") or refreshed.get("text") or refreshed.get("content_desc")
    )
    if semantic not in accepted:
        return {**base, "reason": "profile_cta_semantic_changed"}
    if shift > max(0, int(max_center_shift_px)):
        return {**base, "reason": "profile_cta_bounds_shift_too_large"}
    return {**base, "ok": True, "reason": "fresh_profile_cta_valid"}


def classify_music_audio_sheet(
    hierarchy_xml: str,
    *,
    screen_width: int,
    screen_height: int,
) -> dict[str, Any]:
    """Classify only the strong Instagram music/audio bottom-sheet contract."""

    base = {
        "proved": False,
        "surface_type": "UNKNOWN_MODAL",
        "reason": "music_sheet_contract_not_proved",
        "sheet_bounds": {},
        "use_audio_present": False,
        "add_to_profile_present": False,
        "audio_semantic_present": False,
    }
    try:
        root = ET.fromstring(str(hierarchy_xml or ""))
    except Exception:
        return {**base, "reason": "hierarchy_unreadable"}

    nodes = list(root.iter())
    parent = {child: owner for owner in nodes for child in owner}
    use_nodes: list[ET.Element] = []
    add_nodes: list[ET.Element] = []
    audio_semantic = False
    for node in nodes:
        labels = {
            normalize_cta_semantic(node.get("text")),
            normalize_cta_semantic(node.get("content-desc")),
        }
        labels.discard("")
        label = " ".join(sorted(labels))
        if "use audio" in labels:
            use_nodes.append(node)
        if "add to profile" in labels:
            add_nodes.append(node)
        if any(marker in label for marker in ("audio", "music", "song", "original audio")):
            audio_semantic = True

    base.update(
        {
            "use_audio_present": bool(use_nodes),
            "add_to_profile_present": bool(add_nodes),
            "audio_semantic_present": audio_semantic,
        }
    )
    if not use_nodes or not add_nodes:
        return base

    def ancestors(node: ET.Element) -> list[ET.Element]:
        chain = [node]
        while node in parent:
            node = parent[node]
            chain.append(node)
        return chain

    use_chain = ancestors(use_nodes[0])
    add_chain = set(ancestors(add_nodes[0]))
    common = next((node for node in use_chain if node in add_chain), None)
    sheet_bounds = parse_android_bounds(common.get("bounds") if common is not None else None)
    if sheet_bounds is None:
        return {**base, "reason": "music_sheet_bounds_missing"}
    width = sheet_bounds["right"] - sheet_bounds["left"]
    height = sheet_bounds["bottom"] - sheet_bounds["top"]
    coherent = (
        width >= int(max(1, screen_width) * 0.70)
        and height >= int(max(1, screen_height) * 0.18)
        and sheet_bounds["bottom"] >= int(max(1, screen_height) * 0.72)
        and sheet_bounds["top"] > 0
    )
    if not coherent:
        return {**base, "reason": "music_sheet_bounds_incoherent", "sheet_bounds": sheet_bounds}

    # The two exact action labels are already strong audio semantics.  An
    # explicit music/audio label is additional evidence, not a substitute.
    return {
        **base,
        "proved": True,
        "surface_type": "MUSIC_AUDIO_SHEET",
        "reason": "music_audio_sheet_strong_contract",
        "sheet_bounds": sheet_bounds,
    }


def derive_safe_sheet_dismiss_point(
    hierarchy_xml: str,
    *,
    sheet_bounds: dict[str, Any],
    screen_width: int,
    screen_height: int,
) -> tuple[int, int] | None:
    """Find a non-clickable point just above a proved sheet, or return None."""

    sheet = parse_android_bounds(sheet_bounds)
    if sheet is None:
        return None
    try:
        root = ET.fromstring(str(hierarchy_xml or ""))
    except Exception:
        return None
    forbidden: list[dict[str, int]] = []
    for node in root.iter():
        if str(node.get("clickable") or "").casefold() != "true":
            continue
        bounds = parse_android_bounds(node.get("bounds"))
        if bounds is not None:
            forbidden.append(bounds)
    minimum_y = int(max(1, screen_height) * 0.16)
    maximum_y = sheet["top"] - 12
    if maximum_y <= minimum_y:
        return None
    x_candidates = (
        int(max(1, screen_width) * 0.50),
        int(max(1, screen_width) * 0.72),
        int(max(1, screen_width) * 0.28),
    )
    y_candidates = range(maximum_y, minimum_y - 1, -24)
    for y in y_candidates:
        for x in x_candidates:
            if any(
                item["left"] <= x <= item["right"]
                and item["top"] <= y <= item["bottom"]
                for item in forbidden
            ):
                continue
            return x, y
    return None
