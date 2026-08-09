"""Non-destructive profile/sheet probe for Unfollow Phase 2B.

This module may tap a Following-list row, the target profile's Following
button, and an evidence-gated Unfollow confirmation. It never persists an
unfollow; the orchestrator owns terminal verification and persistence.
"""

from __future__ import annotations

import hashlib
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import uiautomator2 as u2

from logs import log
from instagram_action_restriction import guard_instagram_action_rate_limit
from own_following_navigation import detect_own_following_list_screen
from unfollow_list_harvest import normalize_unfollow_username

_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_FOLLOWING_BUTTON_LABELS = (
    "Following",
    "Suivi",
    "Suivie",
    "Suivi(e)",
    "Abonné",
    "Abonnée",
    "Abonné(e)",
    "Siguiendo",
    "Gefolgt",
)
_NOT_FOLLOWING_BUTTON_LABELS = {
    "follow": "follow",
    "suivre": "follow",
    "follow back": "follow_back",
    "suivre en retour": "follow_back",
    "requested": "requested",
    "demande envoyée": "requested",
    "demande envoyee": "requested",
}
_FOLLOWING_BUTTON_MAX_PRE_TAP_SHIFT_PX = 140
_FOLLOWING_BUTTON_RETRY_STABLE_SHIFT_PX = 50
_FOLLOWING_BUTTON_BOUNDS_SHIFT_RETRY_SETTLE_S = 1.0
_FOLLOWING_BUTTON_RETRY_SECOND_DETECT_SETTLE_S = 0.5
_FOLLOWING_BUTTON_INITIAL_MISSING_SETTLE_S = 0.8
_FOLLOWING_CTA_RENDER_POLL_INTERVAL_S = 0.45
_FOLLOWING_CTA_RENDER_MAX_PROBES = 5
_FOLLOWING_CTA_STABLE_XML_DELTA_RATIO = 0.08


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 2)


def _dump_hierarchy(d: u2.Device) -> str:
    try:
        try:
            return str(d.dump_hierarchy(compressed=False) or "")
        except TypeError:
            return str(d.dump_hierarchy() or "")
    except Exception:
        return ""


def _dump_hierarchy_with_timing(d: u2.Device) -> tuple[str, float]:
    start = time.perf_counter()
    hierarchy = _dump_hierarchy(d)
    return hierarchy, _elapsed_ms(start)


def _parse_xml_root(hierarchy_xml: str) -> ET.Element | None:
    text = str(hierarchy_xml or "").strip()
    if not text:
        return None
    try:
        try:
            return ET.fromstring(text)
        except ET.ParseError:
            return ET.fromstring(f"<wrap>{text}</wrap>")
    except Exception:
        return None


def _looks_like_username(raw: str) -> bool:
    value = str(raw or "").strip().lstrip("@")
    return bool(value and _HANDLE_RE.match(value))


def _bounds_center(bounds: dict[str, Any]) -> tuple[int, int] | None:
    if not isinstance(bounds, dict) or not bounds:
        return None
    try:
        left = int(bounds.get("left", 0))
        right = int(bounds.get("right", 0))
        top = int(bounds.get("top", 0))
        bottom = int(bounds.get("bottom", 0))
    except Exception:
        return None
    if right <= left or bottom <= top:
        return None
    return (left + right) // 2, (top + bottom) // 2


def _center_shift_metrics(
    initial_bounds: dict[str, Any],
    refreshed_bounds: dict[str, Any],
) -> tuple[int, int, int]:
    initial_center = _bounds_center(initial_bounds)
    refreshed_center = _bounds_center(refreshed_bounds)
    if initial_center is None or refreshed_center is None:
        return 0, 0, 0
    delta_x = int(refreshed_center[0] - initial_center[0])
    delta_y = int(refreshed_center[1] - initial_center[1])
    return delta_x, delta_y, max(abs(delta_x), abs(delta_y))


def _safe_window_size(d: u2.Device) -> tuple[int, int]:
    try:
        w, h = d.window_size()
        return int(w), int(h)
    except Exception:
        return 1080, 2400


def _point_inside(bounds: dict[str, Any], x: int, y: int) -> bool:
    if not isinstance(bounds, dict) or not bounds:
        return False
    try:
        return (
            int(bounds.get("left", 0)) <= int(x) <= int(bounds.get("right", 0))
            and int(bounds.get("top", 0)) <= int(y) <= int(bounds.get("bottom", 0))
        )
    except Exception:
        return False


def _parse_bounds_attr(raw: str | None) -> dict[str, int]:
    text = str(raw or "")
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", text)
    if not m:
        return {}
    return {
        "left": int(m.group(1)),
        "top": int(m.group(2)),
        "right": int(m.group(3)),
        "bottom": int(m.group(4)),
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _following_button_label_match(
    text: str,
    content_desc: str,
    *,
    expected_target_username: str = "",
) -> tuple[bool, str]:
    t = str(text or "").strip()
    cd = str(content_desc or "").strip()
    if t in _FOLLOWING_BUTTON_LABELS:
        return True, "text_exact_following"
    # Keep content-desc narrow to avoid profile stats such as "5,425 following".
    cd_low = cd.lower()
    if "following button" in cd_low or cd in _FOLLOWING_BUTTON_LABELS:
        return True, "content_desc_following_button"
    expected = normalize_unfollow_username(expected_target_username)
    if expected:
        cd_normalized = normalize_unfollow_username(cd.replace(" ", ""))
        for label in _FOLLOWING_BUTTON_LABELS:
            label_low = label.casefold()
            if cd_low.startswith(f"{label_low} ") or cd_low.startswith(f"{label_low},"):
                if expected in cd_normalized:
                    return True, "content_desc_following_exact_profile"
    return False, "text_not_exact_following"


def _nearest_clickable_ancestor(
    element: ET.Element,
    parent_map: dict[ET.Element, ET.Element],
) -> ET.Element | None:
    current = parent_map.get(element)
    while current is not None:
        if str(current.get("clickable") or "").lower() == "true":
            return current
        current = parent_map.get(current)
    return None


def _following_cta_reason(detection_method: str) -> str:
    method = str(detection_method or "")
    if method.startswith("accessibility_") or method.startswith("content_desc_"):
        return "following_cta_detected_accessibility"
    if method.startswith("vision_verified"):
        return "following_cta_detected_vision_verified"
    return "following_cta_detected_xml"


def _unfollow_button_bounds_reject_reason(
    bounds: dict[str, int],
    *,
    screen_w: int,
    screen_h: int,
) -> str:
    center = _bounds_center(bounds)
    if center is None:
        return "bounds_missing"
    cx, cy = center
    width = int(bounds.get("right", 0)) - int(bounds.get("left", 0))
    height = int(bounds.get("bottom", 0)) - int(bounds.get("top", 0))
    if cy < int(screen_h * 0.16) or cy > int(screen_h * 0.62):
        return "outside_unfollow_profile_button_band"
    if cx < int(screen_w * 0.08) or cx > int(screen_w * 0.92):
        return "outside_profile_cta_x_band"
    if width < int(screen_w * 0.12) or width > int(screen_w * 0.62):
        return "bounds_shape_rejected"
    if height < 28 or height > int(screen_h * 0.11):
        return "bounds_shape_rejected"
    return ""


def _positive_not_following_relationship_state(
    root: ET.Element | None,
    *,
    screen_w: int,
    screen_h: int,
) -> str:
    """Return a positive non-Following CTA state from the current profile.

    Absence of the Following CTA is never enough.  This proof requires an
    exact localized relationship label on a clickable profile CTA whose fresh
    bounds pass the same profile-band contract as the Following detector.
    """
    if root is None:
        return ""
    parent_map = {child: parent for parent in root.iter() for child in parent}
    for element in root.iter():
        label = str(
            element.get("text") or element.get("content-desc") or ""
        ).strip().casefold()
        state = _NOT_FOLLOWING_BUTTON_LABELS.get(label)
        if not state:
            continue
        tap_element = element
        if str(element.get("clickable") or "").casefold() != "true":
            ancestor = _nearest_clickable_ancestor(element, parent_map)
            if ancestor is None:
                continue
            tap_element = ancestor
        bounds = _parse_bounds_attr(tap_element.get("bounds"))
        if _unfollow_button_bounds_reject_reason(
            bounds,
            screen_w=screen_w,
            screen_h=screen_h,
        ):
            continue
        return state
    return ""


def _detect_following_cta_from_accessibility(
    d: u2.Device,
    *,
    expected_target_username: str,
    screen_w: int,
    screen_h: int,
) -> dict[str, Any] | None:
    """Use live accessibility selectors when a hierarchy dump is stale.

    This never falls back to a global coordinate.  The selector must expose a
    Following semantic and fresh bounds in the verified profile CTA band.
    """
    selectors: list[tuple[str, dict[str, str]]] = []
    for label in _FOLLOWING_BUTTON_LABELS:
        selectors.append(("accessibility_text_exact", {"text": label}))
        selectors.append(("accessibility_desc_contains", {"descriptionContains": label}))
    for detection_method, selector in selectors:
        try:
            element = d(**selector)
            if not element.exists(timeout=0.04):
                continue
            info = dict(element.info or {})
        except Exception:
            continue
        text = str(info.get("text") or "").strip()
        content_desc = str(
            info.get("contentDescription")
            or info.get("content-desc")
            or ""
        ).strip()
        label_ok, label_method = _following_button_label_match(
            text,
            content_desc,
            expected_target_username=expected_target_username,
        )
        if not label_ok:
            continue
        bounds_raw = info.get("bounds") or {}
        try:
            bounds = {
                key: int(bounds_raw.get(key, 0))
                for key in ("left", "top", "right", "bottom")
            }
        except Exception:
            continue
        if _unfollow_button_bounds_reject_reason(
            bounds,
            screen_w=screen_w,
            screen_h=screen_h,
        ):
            continue
        center = _bounds_center(bounds)
        if center is None:
            continue
        return {
            "text": text,
            "content_desc": content_desc,
            "resource_id": str(info.get("resourceName") or info.get("resource-id") or ""),
            "class_name": str(info.get("className") or info.get("class") or ""),
            "bounds": bounds,
            "center_x": int(center[0]),
            "center_y": int(center[1]),
            "tap_x": int(center[0]),
            "tap_y": int(center[1]),
            "clickable": bool(info.get("clickable", True)),
            "detection_method": f"{detection_method}:{label_method}",
            "expected_target_username": expected_target_username,
            "accessibility_live_probe": True,
        }
    return None


def detect_profile_following_button_for_unfollow(
    d: u2.Device,
    *,
    expected_target_username: str,
    allow_verified_wide_cta: bool = False,
) -> dict[str, Any]:
    """Find the profile Following button in the Unfollow probe context.

    This detector is intentionally more tolerant vertically than the Follow/Mute
    helper because profiles opened from the owner's Following list can place the
    CTA below bio / followed-by / link content.
    """
    total_start = time.perf_counter()
    screen_w, screen_h = _safe_window_size(d)
    hierarchy, dump_ms = _dump_hierarchy_with_timing(d)
    root = _parse_xml_root(hierarchy)
    probe_at = _utc_now_iso()
    reject_reasons_count: dict[str, int] = {}
    candidates_seen = 0
    candidates_rejected = 0
    best: dict[str, Any] | None = None
    non_following_relationship_state = _positive_not_following_relationship_state(
        root,
        screen_w=screen_w,
        screen_h=screen_h,
    )

    if root is None:
        out = {
            "ok": False,
            "failure_reason": "hierarchy_xml_parse_failed",
            "expected_target_username": expected_target_username,
            "candidates_seen_count": 0,
            "candidates_rejected_count": 0,
            "reject_reasons_count": {},
            "probe_at": probe_at,
            "hierarchy_xml_len": len(str(hierarchy or "")),
            "positive_not_following_state": non_following_relationship_state,
        }
        log(
            "info",
            "unfollow_perf_following_button_detect_ms",
            expected_target_username=expected_target_username,
            ok=False,
            failure_reason="hierarchy_xml_parse_failed",
            total_ms=_elapsed_ms(total_start),
            dump_hierarchy_ms=dump_ms,
            hierarchy_xml_len=len(str(hierarchy or "")),
            candidates_seen_count=0,
            candidates_rejected_count=0,
        )
        return out

    parent_map = {child: parent for parent in root.iter() for child in parent}
    for el in root.iter():
        text = str(el.get("text") or "").strip()
        content_desc = str(el.get("content-desc") or "").strip()
        label_ok, label_method = _following_button_label_match(
            text,
            content_desc,
            expected_target_username=expected_target_username,
        )
        if not label_ok:
            continue

        candidates_seen += 1
        source_el = el
        tap_el = el
        clickable = str(el.get("clickable") or "").lower() == "true"
        clickable_ancestor = False
        if not clickable:
            ancestor = _nearest_clickable_ancestor(el, parent_map)
            if ancestor is not None:
                tap_el = ancestor
                clickable = True
                clickable_ancestor = True
        rid = str(tap_el.get("resource-id") or source_el.get("resource-id") or "")
        cls = str(tap_el.get("class") or source_el.get("class") or "")
        bounds = _parse_bounds_attr(tap_el.get("bounds"))
        center = _bounds_center(bounds)
        cx, cy = center if center is not None else (0, 0)
        candidate = {
            "text": text,
            "content_desc": content_desc,
            "resource_id": rid,
            "class_name": cls,
            "bounds": bounds,
            "center_x": cx,
            "center_y": cy,
            "clickable": clickable,
            "clickable_ancestor": clickable_ancestor,
            "detection_method": (
                f"{label_method}:clickable_ancestor"
                if clickable_ancestor
                else label_method
            ),
            "expected_target_username": expected_target_username,
        }
        log("info", "unfollow_profile_following_button_candidate_seen", **candidate)

        reject_reason = _unfollow_button_bounds_reject_reason(
            bounds,
            screen_w=screen_w,
            screen_h=screen_h,
        )
        verified_wide_cta = bool(
            allow_verified_wide_cta
            and reject_reason == "bounds_shape_rejected"
            and int(bounds.get("right", 0)) - int(bounds.get("left", 0))
            <= int(screen_w * 0.92)
            and int(bounds.get("right", 0)) - int(bounds.get("left", 0))
            > int(screen_w * 0.62)
            and rid.rsplit("/", 1)[-1] == "profile_header_follow_button"
            and cls == "android.widget.Button"
            and clickable
            and text in _FOLLOWING_BUTTON_LABELS
        )
        if verified_wide_cta:
            reject_reason = ""
            candidate["detection_method"] = "verified_profile_wide_resource_button"
            candidate["verified_wide_cta"] = True
            log(
                "info",
                "unfollow_profile_following_button_wide_cta_accepted",
                **candidate,
            )
        if reject_reason:
            candidates_rejected += 1
            reject_reasons_count[reject_reason] = int(reject_reasons_count.get(reject_reason, 0)) + 1
            log(
                "info",
                "unfollow_profile_following_button_candidate_rejected",
                **candidate,
                reject_reason=reject_reason,
            )
            continue

        # Prefer clickable nodes, but accept non-clickable text if bounds are strong:
        # some Instagram builds expose the visible TextView while the parent handles taps.
        score = (2 if clickable else 1, -abs(cy - int(screen_h * 0.48)))
        accepted = {
            **candidate,
            "score": score,
            "tap_x": cx,
            "tap_y": cy,
        }
        if best is None or accepted["score"] > best["score"]:
            best = accepted

    if best is None:
        accessibility = _detect_following_cta_from_accessibility(
            d,
            expected_target_username=expected_target_username,
            screen_w=screen_w,
            screen_h=screen_h,
        )
        if accessibility is not None:
            best = {**accessibility, "score": (3, 0)}

    if best is None:
        out = {
            "ok": False,
            "failure_reason": "following_button_not_found",
            "expected_target_username": expected_target_username,
            "candidates_seen_count": candidates_seen,
            "candidates_rejected_count": candidates_rejected,
            "reject_reasons_count": reject_reasons_count,
            "probe_at": probe_at,
            "hierarchy_xml_len": len(str(hierarchy or "")),
            "positive_not_following_state": non_following_relationship_state,
        }
        log(
            "info",
            "unfollow_perf_following_button_detect_ms",
            expected_target_username=expected_target_username,
            ok=False,
            failure_reason="following_button_not_found",
            total_ms=_elapsed_ms(total_start),
            dump_hierarchy_ms=dump_ms,
            hierarchy_xml_len=len(str(hierarchy or "")),
            candidates_seen_count=candidates_seen,
            candidates_rejected_count=candidates_rejected,
        )
        return out

    out = {
        "ok": True,
        "failure_reason": "",
        "expected_target_username": expected_target_username,
        "detection_method": str(best.get("detection_method") or ""),
        "text": str(best.get("text") or ""),
        "content_desc": str(best.get("content_desc") or ""),
        "resource_id": str(best.get("resource_id") or ""),
        "class_name": str(best.get("class_name") or ""),
        "bounds": dict(best.get("bounds") or {}),
        "center_x": int(best.get("center_x") or 0),
        "center_y": int(best.get("center_y") or 0),
        "tap_x": int(best.get("tap_x") or 0),
        "tap_y": int(best.get("tap_y") or 0),
        "clickable": bool(best.get("clickable")),
        "verified_wide_cta": bool(best.get("verified_wide_cta")),
        "clickable_ancestor": bool(best.get("clickable_ancestor")),
        "accessibility_live_probe": bool(best.get("accessibility_live_probe")),
        "candidates_seen_count": candidates_seen,
        "candidates_rejected_count": candidates_rejected,
        "reject_reasons_count": reject_reasons_count,
        "probe_at": probe_at,
        "hierarchy_xml_len": len(str(hierarchy or "")),
        "stable_reason": _following_cta_reason(str(best.get("detection_method") or "")),
    }
    log("info", "unfollow_profile_following_button_detected", **out)
    log(
        "info",
        "unfollow_perf_following_button_detect_ms",
        expected_target_username=expected_target_username,
        ok=True,
        failure_reason="",
        total_ms=_elapsed_ms(total_start),
        dump_hierarchy_ms=dump_ms,
        hierarchy_xml_len=len(str(hierarchy or "")),
        candidates_seen_count=candidates_seen,
        candidates_rejected_count=candidates_rejected,
    )
    return out


def tap_following_list_username_row_for_unfollow_probe(
    d: u2.Device,
    row: dict[str, Any],
    *,
    selection_reason: str,
) -> tuple[bool, dict[str, Any]]:
    """Tap only the username/left row zone. Never tap Message or the overflow menu."""
    username = str(row.get("username") or "")
    w, _h = _safe_window_size(d)
    username_bounds = dict(row.get("username_bounds") or {})
    row_bounds = dict(row.get("row_bounds") or {})
    cta_bounds = dict(row.get("cta_bounds") or {})

    center = _bounds_center(username_bounds)
    tap_method = "username_bounds_center"
    if center is None:
        if row_bounds:
            try:
                left = int(row_bounds.get("left", 0))
                top = int(row_bounds.get("top", 0))
                bottom = int(row_bounds.get("bottom", 0))
                cx = min(int(w * 0.42), left + int(w * 0.34))
                cy = (top + bottom) // 2
                center = (cx, cy)
                tap_method = "row_left_center_fallback"
            except Exception:
                center = None
    if center is None:
        meta = {
            "username": username,
            "row_index": int(row.get("row_index") or 0),
            "selection_reason": selection_reason,
            "failure_reason": "tap_bounds_missing",
        }
        log("info", "unfollow_following_row_profile_tap_started", **meta)
        return False, meta

    tap_x, tap_y = center
    cta_guard_hit = _point_inside(cta_bounds, tap_x, tap_y)
    right_guard_hit = int(tap_x) > int(w * 0.62)
    meta = {
        "username": username,
        "row_index": int(row.get("row_index") or 0),
        "tap_x": tap_x,
        "tap_y": tap_y,
        "tap_method": tap_method,
        "selection_reason": selection_reason,
        "cta_guard_hit": cta_guard_hit,
        "right_guard_hit": right_guard_hit,
    }
    log("info", "unfollow_following_row_profile_tap_started", **meta)
    if cta_guard_hit or right_guard_hit:
        meta["failure_reason"] = "tap_point_not_left_safe"
        return False, meta

    try:
        d.click(tap_x, tap_y)
        log("info", "unfollow_following_row_profile_tap_dispatched", **meta)
        return True, meta
    except Exception as exc:
        meta["failure_reason"] = "tap_dispatch_failed"
        meta["error"] = str(exc)[:200]
        return False, meta


def _extract_profile_username_from_hierarchy(hierarchy_xml: str) -> tuple[str, str, dict[str, Any]]:
    root = _parse_xml_root(hierarchy_xml)
    meta: dict[str, Any] = {
        "action_bar_title": "",
        "candidate_texts": [],
        "hierarchy_xml_len": len(str(hierarchy_xml or "")),
    }
    if root is None:
        return "", "hierarchy_xml_parse_failed", meta

    candidates: list[tuple[int, str, str]] = []
    for el in root.iter():
        rid = str(el.get("resource-id") or "")
        rid_l = rid.lower()
        text = str(el.get("text") or el.get("content-desc") or "").strip().lstrip("@")
        if not text or not _looks_like_username(text):
            continue
        if "action_bar_title" in rid_l:
            meta["action_bar_title"] = text
            candidates.append((0, text, "action_bar_title"))
        elif "profile_header" in rid_l and "username" in rid_l:
            candidates.append((1, text, "profile_header_username"))
        elif "username" in rid_l and "row_search" not in rid_l and "follow_list" not in rid_l:
            candidates.append((2, text, "username_resource_id"))
        elif "title" in rid_l:
            candidates.append((3, text, "title_resource_id"))
    if not candidates:
        return "", "profile_username_not_found", meta
    candidates.sort(key=lambda item: item[0])
    meta["candidate_texts"] = [
        {"username": value, "method": method, "rank": rank}
        for rank, value, method in candidates[:8]
    ]
    _, username, method = candidates[0]
    return username, method, meta


def verify_unfollow_target_profile_strict(
    d: u2.Device,
    *,
    expected_target_username: str,
    timeout_s: float = 4.0,
) -> dict[str, Any]:
    """Require the opened profile username to exactly match the selected row."""
    expected = normalize_unfollow_username(expected_target_username)
    deadline = time.monotonic() + max(0.2, float(timeout_s))
    last_actual = ""
    last_method = ""
    last_meta: dict[str, Any] = {}
    while time.monotonic() < deadline:
        hierarchy = _dump_hierarchy(d)
        actual_raw, method, meta = _extract_profile_username_from_hierarchy(hierarchy)
        actual = normalize_unfollow_username(actual_raw)
        last_actual = actual_raw
        last_method = method
        last_meta = meta
        if actual and actual == expected:
            out = {
                "ok": True,
                "expected_target_username": expected_target_username,
                "actual_profile_username": actual_raw,
                "verification_method": f"profile_username_exact:{method}",
                "failure_reason": "",
                "meta": meta,
            }
            log("info", "unfollow_target_profile_open_verified", **out)
            return out
        time.sleep(0.18)

    out = {
        "ok": False,
        "expected_target_username": expected_target_username,
        "actual_profile_username": last_actual,
        "verification_method": f"profile_username_exact:{last_method or 'not_found'}",
        "failure_reason": (
            "target_profile_username_mismatch"
            if last_actual
            else "target_profile_username_not_detected"
        ),
        "meta": last_meta,
    }
    log("info", "unfollow_target_profile_open_failed", **out)
    return out


def _find_text(d: u2.Device, labels: tuple[str, ...]) -> tuple[Any, str, str]:
    for label in labels:
        try:
            el = d(text=label)
            if el.exists(timeout=0.12):
                return el, label, "text_exact"
        except Exception:
            continue
    return None, "", ""


def _element_bounds(el: Any) -> dict[str, int]:
    try:
        return {k: int((el.info.get("bounds") or {}).get(k, 0)) for k in ("left", "top", "right", "bottom")}
    except Exception:
        return {}


def _detect_actions_sheet_signals(d: u2.Device) -> dict[str, Any]:
    start = time.perf_counter()
    mute_el, mute_text, mute_method = _find_text(
        d,
        ("Mute", "Mettre en sourdine", "Sourdine"),
    )
    restrict_el, restrict_text, restrict_method = _find_text(
        d,
        ("Restrict", "Restreindre"),
    )
    unfollow_el, unfollow_text, unfollow_method = _find_text(
        d,
        ("Unfollow", "Ne plus suivre"),
    )
    out = {
        "mute_visible": mute_el is not None,
        "mute_text": mute_text,
        "mute_detection_method": mute_method,
        "restrict_visible": restrict_el is not None,
        "restrict_text": restrict_text,
        "restrict_detection_method": restrict_method,
        "unfollow_visible": unfollow_el is not None,
        "unfollow_text": unfollow_text,
        "unfollow_detection_method": unfollow_method,
    }
    log(
        "info",
        "unfollow_perf_actions_sheet_signal_detect_ms",
        total_ms=_elapsed_ms(start),
        mute_visible=bool(out["mute_visible"]),
        restrict_visible=bool(out["restrict_visible"]),
        unfollow_visible=bool(out["unfollow_visible"]),
    )
    return out


def detect_unfollow_option_in_following_sheet(d: u2.Device) -> dict[str, Any]:
    """Detect the exact Unfollow option in the open Following actions sheet."""
    total_start = time.perf_counter()
    el, text, method = _find_text(d, ("Unfollow", "Ne plus suivre"))
    if el is None:
        out = {
            "ok": False,
            "failure_reason": "unfollow_option_not_found",
            "option_text": "",
            "detection_method": "",
            "bounds": {},
        }
        log(
            "info",
            "unfollow_perf_unfollow_option_detect_ms",
            ok=False,
            failure_reason="unfollow_option_not_found",
            total_ms=_elapsed_ms(total_start),
            bounds_info_ms=0.0,
        )
        return out
    bounds_start = time.perf_counter()
    bounds = _element_bounds(el)
    bounds_info_ms = _elapsed_ms(bounds_start)
    center = _bounds_center(bounds)
    out = {
        "ok": center is not None,
        "failure_reason": "" if center is not None else "unfollow_option_bounds_missing",
        "option_text": text,
        "detection_method": method,
        "bounds": bounds,
        "tap_x": int(center[0]) if center else 0,
        "tap_y": int(center[1]) if center else 0,
    }
    log(
        "info",
        "unfollow_perf_unfollow_option_detect_ms",
        ok=bool(out["ok"]),
        failure_reason=str(out["failure_reason"]),
        total_ms=_elapsed_ms(total_start),
        bounds_info_ms=bounds_info_ms,
        option_text=text,
        detection_method=method,
    )
    return out


def tap_unfollow_in_following_sheet(
    d: u2.Device,
    *,
    target_username: str,
) -> dict[str, Any]:
    """Tap the exact Unfollow option. Caller must have already passed real-action guards."""
    opt = detect_unfollow_option_in_following_sheet(d)
    if not opt.get("ok"):
        out = {
            "ok": False,
            "failure_reason": str(opt.get("failure_reason") or "unfollow_option_not_found"),
            "target_username": target_username,
            "option_text": str(opt.get("option_text") or ""),
            "detection_method": str(opt.get("detection_method") or ""),
            "bounds": dict(opt.get("bounds") or {}),
            "tap_x": int(opt.get("tap_x") or 0),
            "tap_y": int(opt.get("tap_y") or 0),
        }
        log("info", "unfollow_sheet_unfollow_option_tap_started", **out)
        return out

    out = {
        "ok": True,
        "failure_reason": "",
        "target_username": target_username,
        "option_text": str(opt.get("option_text") or ""),
        "detection_method": str(opt.get("detection_method") or ""),
        "bounds": dict(opt.get("bounds") or {}),
        "tap_x": int(opt.get("tap_x") or 0),
        "tap_y": int(opt.get("tap_y") or 0),
    }
    log("info", "unfollow_sheet_unfollow_option_tap_started", **out)
    guard_instagram_action_rate_limit(
        d,
        phase="unfollow",
        preceding_action="unfollow_pre_tap",
    )
    try:
        d.click(int(out["tap_x"]), int(out["tap_y"]))
        time.sleep(0.2)
    except Exception as exc:
        out["ok"] = False
        out["failure_reason"] = "unfollow_option_tap_failed"
        out["error"] = str(exc)[:200]
        return out
    guard_instagram_action_rate_limit(
        d,
        phase="unfollow",
        preceding_action="unfollow",
    )
    log("info", "unfollow_sheet_unfollow_option_tapped", **out)
    return out


def _exact_follow_button_visible_after_unfollow(d: u2.Device) -> bool:
    for label in ("Follow", "Suivre"):
        try:
            if d(text=label).exists(timeout=0.08):
                return True
        except Exception:
            continue
    return False


def _profile_follow_state_after_unfollow(d: u2.Device) -> str:
    if _exact_follow_button_visible_after_unfollow(d):
        return "follow"
    det = detect_profile_following_button_for_unfollow(d, expected_target_username="")
    if det.get("ok"):
        return "following"
    return "following_absent"


_PRIVATE_UNFOLLOW_CONTEXT_MARKERS = (
    ("if you change your mind", "request to follow"),
    ("si vous changez d'avis", "demander à suivre"),
    ("si tu changes d'avis", "demander à suivre"),
    ("si cambias de opinión", "solicitar seguir"),
)
_PRIVATE_UNFOLLOW_CANCEL_LABELS = frozenset(("cancel", "annuler", "cancelar"))
_PRIVATE_UNFOLLOW_ACTION_LABELS = frozenset(
    ("unfollow", "ne plus suivre", "dejar de seguir")
)
_PRIVATE_UNFOLLOW_UNSAFE_MARKERS = (
    "challenge_required",
    "suspicious login",
    "verify your identity",
    "confirmez votre identité",
    "account suspended",
    "compte suspendu",
)


def classify_private_unfollow_confirmation_snapshot(
    hierarchy_xml: str,
    *,
    target_username: str,
    profile_identity_certified: bool,
    private_flow_engaged: bool,
    screen_w: int,
    screen_h: int,
) -> dict[str, Any]:
    """Classify one immutable private-Unfollow confirmation hierarchy.

    A generic Unfollow label is insufficient.  The exact target profile,
    private-account consequence text, Cancel control and a single actionable
    Unfollow control must all be present on the already engaged flow.
    """
    target = normalize_unfollow_username(target_username)
    base = {
        "present": False,
        "ok": False,
        "failure_reason": "private_confirmation_not_present",
        "target_username": target,
        "bounds": {},
        "tap_x": 0,
        "tap_y": 0,
        "snapshot_generation": "",
    }
    root = _parse_xml_root(hierarchy_xml)
    if root is None:
        base["failure_reason"] = "private_confirmation_hierarchy_unreadable"
        return base
    all_labels = [
        str(el.get("text") or el.get("content-desc") or "").strip()
        for el in root.iter()
        if str(el.get("text") or el.get("content-desc") or "").strip()
    ]
    joined = " ".join(all_labels).casefold()
    context_present = any(
        first in joined and second in joined
        for first, second in _PRIVATE_UNFOLLOW_CONTEXT_MARKERS
    )
    cancel_present = any(
        label.casefold() in _PRIVATE_UNFOLLOW_CANCEL_LABELS for label in all_labels
    )
    action_nodes = [
        el
        for el in root.iter()
        if str(el.get("text") or el.get("content-desc") or "").strip().casefold()
        in _PRIVATE_UNFOLLOW_ACTION_LABELS
    ]
    modal_present = bool(context_present and cancel_present and action_nodes)
    if not modal_present:
        return base
    base["present"] = True
    if not private_flow_engaged:
        base["failure_reason"] = "private_confirmation_flow_not_engaged"
        return base
    if not profile_identity_certified or not target:
        base["failure_reason"] = "private_confirmation_identity_not_certified"
        return base
    actual_raw, _method, _meta = _extract_profile_username_from_hierarchy(
        hierarchy_xml
    )
    actual = normalize_unfollow_username(actual_raw)
    if actual != target:
        base["failure_reason"] = "private_confirmation_profile_identity_mismatch"
        base["actual_profile_username"] = actual
        return base
    if any(marker in joined for marker in _PRIVATE_UNFOLLOW_UNSAFE_MARKERS):
        base["failure_reason"] = "private_confirmation_unsafe_surface"
        return base
    if target not in " ".join(all_labels).casefold():
        base["failure_reason"] = "private_confirmation_target_context_missing"
        return base
    parent_map = {child: parent for parent in root.iter() for child in parent}
    candidates: list[tuple[ET.Element, dict[str, int]]] = []
    for action_node in action_nodes:
        tap_node = action_node
        if str(action_node.get("clickable") or "").casefold() != "true":
            ancestor = _nearest_clickable_ancestor(action_node, parent_map)
            if ancestor is not None:
                tap_node = ancestor
        bounds = _parse_bounds_attr(tap_node.get("bounds"))
        center = _bounds_center(bounds)
        if center is None:
            continue
        cx, cy = center
        width = int(bounds.get("right", 0)) - int(bounds.get("left", 0))
        height = int(bounds.get("bottom", 0)) - int(bounds.get("top", 0))
        if not (
            int(screen_w * 0.08) <= cx <= int(screen_w * 0.92)
            and int(screen_h * 0.25) <= cy <= int(screen_h * 0.82)
            and width >= int(screen_w * 0.18)
            and 24 <= height <= int(screen_h * 0.14)
        ):
            continue
        candidates.append((tap_node, bounds))
    if len(candidates) != 1:
        base["failure_reason"] = "private_confirmation_action_not_unique"
        base["action_candidate_count"] = len(candidates)
        return base
    _tap_node, bounds = candidates[0]
    center = _bounds_center(bounds)
    assert center is not None
    generation_material = "\x1f".join(
        (target, str(bounds), " ".join(all_labels).casefold())
    )
    base.update(
        {
            "ok": True,
            "failure_reason": "",
            "bounds": bounds,
            "tap_x": int(center[0]),
            "tap_y": int(center[1]),
            "snapshot_generation": hashlib.sha256(
                generation_material.encode("utf-8")
            ).hexdigest()[:20],
            "actual_profile_username": actual,
            "context_proved": True,
            "cancel_control_proved": True,
            "action_candidate_count": 1,
        }
    )
    return base


def _private_confirmation_live_bounds_still_match(
    d: u2.Device,
    *,
    expected_bounds: dict[str, int],
) -> bool:
    """Last-moment stale-control guard; it never chooses a different control."""
    for label in ("Unfollow", "Ne plus suivre", "Dejar de seguir"):
        try:
            element = d(text=label)
            if not element.exists(timeout=0.08):
                continue
            return _element_bounds(element) == expected_bounds
        except Exception:
            continue
    return False


def verify_unfollow_action_success_after_tap(
    d: u2.Device,
    *,
    target_username: str,
    timeout_s: float = 4.0,
    profile_identity_certified: bool = False,
    private_flow_engaged: bool = False,
) -> dict[str, Any]:
    """Verify minimal post-unfollow success: sheet closed and Following no longer visible."""
    log("info", "unfollow_action_verify_started", target_username=target_username)
    timeout = max(0.5, float(timeout_s))
    deadline = time.monotonic() + timeout
    fast_path_deadline = time.monotonic() + min(1.5, max(0.5, timeout * 0.45))
    sheet_closed = False
    profile_follow_state_after = "unknown"
    iterations = 0
    follow_visible = False
    private_confirmation_detected = False
    private_confirmation_tapped = False
    private_confirmation_generation = ""
    while time.monotonic() < deadline:
        iterations += 1
        phase_start = time.perf_counter()
        signals = _detect_actions_sheet_signals(d)
        sheet_signal_check_ms = _elapsed_ms(phase_start)
        sheet_closed = not bool(
            signals.get("mute_visible")
            or signals.get("restrict_visible")
            or signals.get("unfollow_visible")
        )
        private_modal_candidate = bool(
            signals.get("unfollow_visible")
            and not signals.get("mute_visible")
            and not signals.get("restrict_visible")
            and not private_confirmation_tapped
        )
        if private_modal_candidate:
            hierarchy, hierarchy_dump_ms = _dump_hierarchy_with_timing(d)
            private_snapshot = classify_private_unfollow_confirmation_snapshot(
                hierarchy,
                target_username=target_username,
                profile_identity_certified=profile_identity_certified,
                private_flow_engaged=private_flow_engaged,
                screen_w=_safe_window_size(d)[0],
                screen_h=_safe_window_size(d)[1],
            )
            if private_snapshot.get("present"):
                private_confirmation_detected = True
                log(
                    "info",
                    "unfollow_private_confirmation_detected",
                    target_username=target_username,
                    hierarchy_dump_ms=hierarchy_dump_ms,
                    snapshot_generation=str(
                        private_snapshot.get("snapshot_generation") or ""
                    ),
                    proof_ok=bool(private_snapshot.get("ok")),
                    failure_reason=str(private_snapshot.get("failure_reason") or ""),
                )
                if not private_snapshot.get("ok"):
                    out = {
                        "ok": False,
                        "verification_method": "private_confirmation_fail_closed",
                        "sheet_closed": False,
                        "profile_following_absent": False,
                        "profile_follow_state_after": "unknown",
                        "failure_reason": str(
                            private_snapshot.get("failure_reason")
                            or "private_confirmation_unproven"
                        ),
                        "target_username": target_username,
                        "verify_iterations": iterations,
                        "private_confirmation_detected": True,
                        "private_confirmation_tapped": False,
                    }
                    log("info", "unfollow_action_verify_failed", **out)
                    return out
                private_confirmation_generation = str(
                    private_snapshot.get("snapshot_generation") or ""
                )
                log(
                    "info",
                    "unfollow_private_confirmation_tap_started",
                    target_username=target_username,
                    snapshot_generation=private_confirmation_generation,
                    bounds=dict(private_snapshot.get("bounds") or {}),
                )
                if not _private_confirmation_live_bounds_still_match(
                    d,
                    expected_bounds=dict(private_snapshot.get("bounds") or {}),
                ):
                    out = {
                        "ok": False,
                        "verification_method": "private_confirmation_stale_guard",
                        "sheet_closed": False,
                        "profile_following_absent": False,
                        "profile_follow_state_after": "unknown",
                        "failure_reason": "private_confirmation_disappeared_before_tap",
                        "target_username": target_username,
                        "verify_iterations": iterations,
                        "private_confirmation_detected": True,
                        "private_confirmation_tapped": False,
                    }
                    log("info", "unfollow_action_verify_failed", **out)
                    return out
                guard_instagram_action_rate_limit(
                    d,
                    phase="unfollow",
                    preceding_action="private_unfollow_confirmation_pre_tap",
                )
                try:
                    d.click(
                        int(private_snapshot.get("tap_x") or 0),
                        int(private_snapshot.get("tap_y") or 0),
                    )
                    private_confirmation_tapped = True
                except Exception as exc:
                    out = {
                        "ok": False,
                        "verification_method": "private_confirmation_tap",
                        "sheet_closed": False,
                        "profile_following_absent": False,
                        "profile_follow_state_after": "unknown",
                        "failure_reason": "private_confirmation_tap_failed",
                        "error": str(exc)[:200],
                        "target_username": target_username,
                        "verify_iterations": iterations,
                        "private_confirmation_detected": True,
                        "private_confirmation_tapped": False,
                    }
                    log("info", "unfollow_action_verify_failed", **out)
                    return out
                guard_instagram_action_rate_limit(
                    d,
                    phase="unfollow",
                    preceding_action="private_unfollow_confirmation",
                )
                log(
                    "info",
                    "unfollow_private_confirmation_tapped",
                    target_username=target_username,
                    snapshot_generation=private_confirmation_generation,
                    tap_count=1,
                )
                deadline = time.monotonic() + timeout
                fast_path_deadline = time.monotonic() + min(
                    1.5,
                    max(0.5, timeout * 0.45),
                )
                time.sleep(0.2)
                continue
        follow_start = time.perf_counter()
        follow_visible = _exact_follow_button_visible_after_unfollow(d)
        follow_exact_check_ms = _elapsed_ms(follow_start)
        log(
            "info",
            "unfollow_perf_post_tap_verify_phase",
            target_username=target_username,
            phase="fast_path",
            iteration=iterations,
            sheet_closed=sheet_closed,
            follow_visible=follow_visible,
            sheet_signal_check_ms=sheet_signal_check_ms,
            follow_exact_check_ms=follow_exact_check_ms,
            fallback_following_absent_check_ms=0.0,
        )
        if sheet_closed and follow_visible:
            out = {
                "ok": True,
                "verification_method": "sheet_closed_and_profile_following_absent",
                "sheet_closed": True,
                "profile_following_absent": True,
                "profile_follow_state_after": "follow",
                "failure_reason": "",
                "target_username": target_username,
                "verify_iterations": iterations,
                "private_confirmation_detected": private_confirmation_detected,
                "private_confirmation_tapped": private_confirmation_tapped,
                "private_confirmation_generation": private_confirmation_generation,
            }
            log("info", "unfollow_action_verified", **out)
            return out
        if sheet_closed and time.monotonic() >= fast_path_deadline:
            break
        time.sleep(0.25)

    fallback_start = time.perf_counter()
    det = detect_profile_following_button_for_unfollow(d, expected_target_username="")
    fallback_ms = _elapsed_ms(fallback_start)
    profile_follow_state_after = "following" if det.get("ok") else "following_absent"
    following_absent = profile_follow_state_after != "following"
    log(
        "info",
        "unfollow_perf_post_tap_verify_phase",
        target_username=target_username,
        phase="fallback_following_absent",
        iteration=iterations + 1,
        sheet_closed=sheet_closed,
        follow_visible=follow_visible,
        sheet_signal_check_ms=0.0,
        follow_exact_check_ms=0.0,
        fallback_following_absent_check_ms=fallback_ms,
        profile_follow_state_after=profile_follow_state_after,
    )
    if sheet_closed and following_absent:
        out = {
            "ok": True,
            "verification_method": "sheet_closed_and_profile_following_absent",
            "sheet_closed": True,
            "profile_following_absent": True,
            "profile_follow_state_after": profile_follow_state_after,
            "failure_reason": "",
            "target_username": target_username,
            "verify_iterations": iterations,
            "private_confirmation_detected": private_confirmation_detected,
            "private_confirmation_tapped": private_confirmation_tapped,
            "private_confirmation_generation": private_confirmation_generation,
        }
        log("info", "unfollow_action_verified", **out)
        return out

    out = {
        "ok": False,
        "verification_method": "sheet_closed_and_profile_following_absent",
        "sheet_closed": bool(sheet_closed),
        "profile_following_absent": profile_follow_state_after != "following",
        "profile_follow_state_after": profile_follow_state_after or "unknown",
        "failure_reason": "unfollow_verify_conditions_not_met",
        "target_username": target_username,
        "verify_iterations": iterations,
        "private_confirmation_detected": private_confirmation_detected,
        "private_confirmation_tapped": private_confirmation_tapped,
        "private_confirmation_generation": private_confirmation_generation,
    }
    log("info", "unfollow_action_verify_failed", **out)
    return out


def _following_button_visible_in_hierarchy(hierarchy_xml: str, *, d: u2.Device) -> bool:
    root = _parse_xml_root(hierarchy_xml)
    if root is None:
        return False
    screen_w, screen_h = _safe_window_size(d)
    for el in root.iter():
        text = str(el.get("text") or "").strip()
        content_desc = str(el.get("content-desc") or "").strip()
        label_ok, _ = _following_button_label_match(text, content_desc)
        if not label_ok:
            continue
        bounds = _parse_bounds_attr(el.get("bounds"))
        if not _unfollow_button_bounds_reject_reason(bounds, screen_w=screen_w, screen_h=screen_h):
            return True
    return False


def _post_following_tap_evidence(d: u2.Device) -> dict[str, Any]:
    hierarchy, dump_ms = _dump_hierarchy_with_timing(d)
    profile_username, method, meta = _extract_profile_username_from_hierarchy(hierarchy)
    return {
        "action_bar_title_after_tap": str(meta.get("action_bar_title") or ""),
        "profile_username_after_tap": profile_username,
        "profile_username_method_after_tap": method,
        "following_button_still_visible_after_tap": _following_button_visible_in_hierarchy(hierarchy, d=d),
        "hierarchy_len_after_tap": len(str(hierarchy or "")),
        "post_tap_hierarchy_dump_ms": dump_ms,
    }


def _actions_sheet_open_from_signals(signals: dict[str, Any]) -> bool:
    return bool(
        signals.get("mute_visible")
        or signals.get("restrict_visible")
        or signals.get("unfollow_visible")
    )


def _following_tap_retry_block_reason(
    evidence: dict[str, Any],
    *,
    expected_target_username: str,
) -> str:
    expected = normalize_unfollow_username(expected_target_username)
    action_bar_title = normalize_unfollow_username(
        str(evidence.get("action_bar_title_after_tap") or "")
    )
    profile_username = normalize_unfollow_username(
        str(evidence.get("profile_username_after_tap") or "")
    )
    if not expected:
        return "expected_target_username_missing"
    if action_bar_title != expected:
        return "action_bar_title_after_tap_mismatch"
    if profile_username != expected:
        return "profile_username_after_tap_mismatch"
    if not bool(evidence.get("following_button_still_visible_after_tap")):
        return "following_button_not_visible_after_tap"
    return ""


def _following_button_retry_detection_log_fields(det: dict[str, Any]) -> dict[str, Any]:
    return {
        "bounds": dict(det.get("bounds") or {}),
        "resource_id": str(det.get("resource_id") or ""),
        "text": str(det.get("text") or ""),
        "content_desc": str(det.get("content_desc") or ""),
        "detection_method": str(det.get("detection_method") or ""),
    }


def _following_button_methods_compatible(method_a: str, method_b: str) -> bool:
    a = str(method_a or "").strip()
    b = str(method_b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    return a in {"text_exact_following", "content_desc_following_button"} and b in {
        "text_exact_following",
        "content_desc_following_button",
    }


def _retry_following_button_after_bounds_shift(
    d: u2.Device,
    *,
    expected_target_username: str,
    initial_bounds: dict[str, Any],
    refreshed_bounds: dict[str, Any],
    center_delta_x: int,
    center_delta_y: int,
    bounds_shift_px: int,
) -> tuple[bool, dict[str, Any], dict[str, Any]]:
    """Re-check a moving Following button and accept only a stable final pair."""
    retry_meta: dict[str, Any] = {
        "retry_attempted": True,
        "retry_failure_reason": "",
        "retry_shift_px": None,
        "profile_ok": False,
        "detector_ok": False,
    }
    log(
        "info",
        "following_button_bounds_shift_retry_started",
        expected_target_username=expected_target_username,
        initial_bounds=initial_bounds,
        refreshed_bounds=refreshed_bounds,
        bounds_shift_px=bounds_shift_px,
        center_delta_x=center_delta_x,
        center_delta_y=center_delta_y,
    )

    time.sleep(_FOLLOWING_BUTTON_BOUNDS_SHIFT_RETRY_SETTLE_S)
    profile = verify_unfollow_target_profile_strict(
        d,
        expected_target_username=expected_target_username,
        timeout_s=2.0,
    )
    retry_meta["profile_ok"] = bool(profile.get("ok"))
    log(
        "info",
        "following_button_bounds_shift_retry_profile_revalidated",
        ok=retry_meta["profile_ok"],
        actual_profile_username=str(profile.get("actual_profile_username") or ""),
        failure_reason=str(profile.get("failure_reason") or ""),
    )
    if not retry_meta["profile_ok"]:
        retry_meta["retry_failure_reason"] = "target_profile_retry_revalidation_failed"
        log(
            "info",
            "following_button_bounds_shift_retry_failed",
            failure_reason=retry_meta["retry_failure_reason"],
            retry_shift_px=retry_meta["retry_shift_px"],
            profile_ok=retry_meta["profile_ok"],
            detector_ok=retry_meta["detector_ok"],
        )
        return False, {}, retry_meta

    retry_1 = detect_profile_following_button_for_unfollow(
        d,
        expected_target_username=expected_target_username,
    )
    retry_meta["detector_ok"] = bool(retry_1.get("ok"))
    log(
        "info",
        "following_button_bounds_shift_retry_detected",
        retry_index=1,
        **_following_button_retry_detection_log_fields(retry_1),
    )
    if not retry_1.get("ok"):
        retry_meta["retry_failure_reason"] = str(
            retry_1.get("failure_reason") or "following_button_retry_detect_1_failed"
        )
        log(
            "info",
            "following_button_bounds_shift_retry_failed",
            failure_reason=retry_meta["retry_failure_reason"],
            retry_shift_px=retry_meta["retry_shift_px"],
            profile_ok=retry_meta["profile_ok"],
            detector_ok=retry_meta["detector_ok"],
        )
        return False, {}, retry_meta

    time.sleep(_FOLLOWING_BUTTON_RETRY_SECOND_DETECT_SETTLE_S)
    retry_2 = detect_profile_following_button_for_unfollow(
        d,
        expected_target_username=expected_target_username,
    )
    retry_meta["detector_ok"] = bool(retry_2.get("ok"))
    log(
        "info",
        "following_button_bounds_shift_retry_detected",
        retry_index=2,
        **_following_button_retry_detection_log_fields(retry_2),
    )
    if not retry_2.get("ok"):
        retry_meta["retry_failure_reason"] = str(
            retry_2.get("failure_reason") or "following_button_retry_detect_2_failed"
        )
        log(
            "info",
            "following_button_bounds_shift_retry_failed",
            failure_reason=retry_meta["retry_failure_reason"],
            retry_shift_px=retry_meta["retry_shift_px"],
            profile_ok=retry_meta["profile_ok"],
            detector_ok=retry_meta["detector_ok"],
        )
        return False, {}, retry_meta

    final_profile = verify_unfollow_target_profile_strict(
        d,
        expected_target_username=expected_target_username,
        timeout_s=2.0,
    )
    retry_meta["profile_ok"] = bool(final_profile.get("ok"))
    log(
        "info",
        "following_button_bounds_shift_retry_profile_revalidated",
        ok=retry_meta["profile_ok"],
        actual_profile_username=str(final_profile.get("actual_profile_username") or ""),
        failure_reason=str(final_profile.get("failure_reason") or ""),
    )
    if not retry_meta["profile_ok"]:
        retry_meta["retry_failure_reason"] = "target_profile_final_revalidation_failed"
        log(
            "info",
            "following_button_bounds_shift_retry_failed",
            failure_reason=retry_meta["retry_failure_reason"],
            retry_shift_px=retry_meta["retry_shift_px"],
            profile_ok=retry_meta["profile_ok"],
            detector_ok=retry_meta["detector_ok"],
        )
        return False, {}, retry_meta

    retry_1_bounds = dict(retry_1.get("bounds") or {})
    retry_2_bounds = dict(retry_2.get("bounds") or {})
    retry_dx, retry_dy, retry_shift = _center_shift_metrics(retry_1_bounds, retry_2_bounds)
    retry_meta["retry_shift_px"] = retry_shift

    rid_1 = str(retry_1.get("resource_id") or "")
    rid_2 = str(retry_2.get("resource_id") or "")
    method_1 = str(retry_1.get("detection_method") or "")
    method_2 = str(retry_2.get("detection_method") or "")
    failure_reason = ""
    if rid_1 and rid_2 and rid_1 != rid_2:
        failure_reason = "following_button_retry_resource_id_changed"
    elif not _following_button_methods_compatible(method_1, method_2):
        failure_reason = "following_button_retry_detection_method_changed"
    elif bool(retry_1.get("clickable")) and not bool(retry_2.get("clickable")):
        failure_reason = "following_button_retry_no_longer_clickable"
    elif retry_shift > _FOLLOWING_BUTTON_RETRY_STABLE_SHIFT_PX:
        failure_reason = "following_button_retry_bounds_still_unstable"

    if failure_reason:
        retry_meta["retry_failure_reason"] = failure_reason
        log(
            "info",
            "following_button_bounds_shift_retry_failed",
            failure_reason=failure_reason,
            retry_shift_px=retry_shift,
            profile_ok=retry_meta["profile_ok"],
            detector_ok=retry_meta["detector_ok"],
        )
        return False, {}, retry_meta

    log(
        "info",
        "following_button_bounds_shift_retry_stable",
        stable_bounds=retry_2_bounds,
        stable_resource_id=rid_2,
        retry_shift_px=retry_shift,
        retry_center_delta_x=retry_dx,
        retry_center_delta_y=retry_dy,
    )
    return True, retry_2, retry_meta


def _profile_surface_xml_is_stable(lengths: list[int]) -> bool:
    nonzero = [max(0, int(value or 0)) for value in lengths if int(value or 0) > 0]
    if len(nonzero) < 2:
        return False
    previous, current = nonzero[-2:]
    return abs(current - previous) / float(max(previous, current)) <= _FOLLOWING_CTA_STABLE_XML_DELTA_RATIO


def _poll_following_cta_after_initial_miss(
    d: u2.Device,
    *,
    expected_target_username: str,
    initial_detection: dict[str, Any],
    profile_exact_confirmed: bool,
) -> dict[str, Any]:
    first_probe_at = str(initial_detection.get("probe_at") or _utc_now_iso())
    detections = [initial_detection]
    hierarchy_lengths = [int(initial_detection.get("hierarchy_xml_len") or 0)]
    identity = {"ok": bool(profile_exact_confirmed)}
    if not profile_exact_confirmed:
        identity = verify_unfollow_target_profile_strict(
            d,
            expected_target_username=expected_target_username,
            timeout_s=2.0,
        )
    if not bool(identity.get("ok")):
        return {
            **initial_detection,
            "ok": False,
            "failure_reason": "target_profile_retry_revalidation_failed",
            "terminal_reason": "following_cta_surface_not_stable",
            "profile_exact_confirmed": False,
            "first_cta_probe_at": first_probe_at,
            "probes_count": 1,
            "profile_surface_stable_at": "",
            "recovery_used": True,
        }

    for _probe_index in range(2, _FOLLOWING_CTA_RENDER_MAX_PROBES + 1):
        time.sleep(_FOLLOWING_CTA_RENDER_POLL_INTERVAL_S)
        detection = detect_profile_following_button_for_unfollow(
            d,
            expected_target_username=expected_target_username,
            allow_verified_wide_cta=True,
        )
        detections.append(detection)
        hierarchy_lengths.append(int(detection.get("hierarchy_xml_len") or 0))
        if bool(detection.get("ok")):
            out = {
                **detection,
                "profile_exact_confirmed": True,
                "first_cta_probe_at": first_probe_at,
                "probes_count": len(detections),
                "profile_surface_stable_at": "",
                "recovery_used": True,
                "terminal_reason": str(detection.get("stable_reason") or ""),
                "cta_detected_at": str(detection.get("probe_at") or _utc_now_iso()),
                "hierarchy_xml_lengths": hierarchy_lengths,
            }
            log("info", "unfollow_following_cta_render_recovery_succeeded", **out)
            return out

    surface_stable = _profile_surface_xml_is_stable(hierarchy_lengths)
    positive_states = [
        str(item.get("positive_not_following_state") or "")
        for item in detections[-2:]
    ]
    positive_not_following_state = (
        positive_states[-1]
        if len(positive_states) == 2
        and positive_states[0]
        and positive_states[0] == positive_states[1]
        else ""
    )
    terminal_reason = (
        "already_not_following_confirmed"
        if surface_stable and positive_not_following_state
        else "following_cta_terminally_absent"
        if surface_stable
        else "following_cta_surface_not_stable"
    )
    out = {
        **detections[-1],
        "ok": False,
        "failure_reason": terminal_reason,
        "terminal_reason": terminal_reason,
        "profile_exact_confirmed": True,
        "first_cta_probe_at": first_probe_at,
        "probes_count": len(detections),
        "profile_surface_stable_at": _utc_now_iso() if surface_stable else "",
        "recovery_used": True,
        "hierarchy_xml_lengths": hierarchy_lengths,
        "positive_not_following_state": positive_not_following_state,
    }
    log("info", "unfollow_following_cta_render_recovery_exhausted", **out)
    return out


def open_unfollow_actions_sheet_from_profile_probe(
    d: u2.Device,
    *,
    expected_target_username: str,
    profile_exact_confirmed: bool = False,
) -> dict[str, Any]:
    """Tap profile Following and verify the actions sheet. Never taps Unfollow."""
    btn_det = detect_profile_following_button_for_unfollow(
        d,
        expected_target_username=expected_target_username,
    )
    if not btn_det.get("ok") and str(btn_det.get("failure_reason") or "") == "following_button_not_found":
        log(
            "info",
            "unfollow_following_button_initial_missing_retry_started",
            expected_target_username=expected_target_username,
            settle_s=_FOLLOWING_CTA_RENDER_POLL_INTERVAL_S,
            max_probes=_FOLLOWING_CTA_RENDER_MAX_PROBES,
        )
        btn_det = _poll_following_cta_after_initial_miss(
            d,
            expected_target_username=expected_target_username,
            initial_detection=btn_det,
            profile_exact_confirmed=profile_exact_confirmed,
        )
        profile_exact_confirmed = bool(btn_det.get("profile_exact_confirmed"))
        log(
            "info",
            "unfollow_following_button_initial_missing_retry_completed",
            expected_target_username=expected_target_username,
            profile_revalidated=bool(btn_det.get("profile_exact_confirmed")),
            detector_ok=bool(btn_det.get("ok")),
            failure_reason=str(btn_det.get("failure_reason") or ""),
            terminal_reason=str(btn_det.get("terminal_reason") or ""),
            probes_count=int(btn_det.get("probes_count") or 0),
        )
    if not btn_det.get("ok"):
        out = {
            "ok": False,
            "failure_reason": str(btn_det.get("failure_reason") or "following_button_not_found"),
            "expected_target_username": expected_target_username,
            "following_detection_method": "",
            "unfollow_option_visible": False,
            "sheet_context_signals": {},
            "candidates_seen_count": int(btn_det.get("candidates_seen_count") or 0),
            "candidates_rejected_count": int(btn_det.get("candidates_rejected_count") or 0),
            "reject_reasons_count": dict(btn_det.get("reject_reasons_count") or {}),
            "profile_exact_confirmed": bool(btn_det.get("profile_exact_confirmed") or profile_exact_confirmed),
            "first_cta_probe_at": str(btn_det.get("first_cta_probe_at") or btn_det.get("probe_at") or ""),
            "cta_detection_method": "",
            "probes_count": int(btn_det.get("probes_count") or 1),
            "profile_surface_stable_at": str(btn_det.get("profile_surface_stable_at") or ""),
            "recovery_used": bool(btn_det.get("recovery_used")),
            "terminal_reason": str(btn_det.get("terminal_reason") or btn_det.get("failure_reason") or ""),
            "positive_not_following_state": str(
                btn_det.get("positive_not_following_state") or ""
            ),
        }
        log("info", "unfollow_actions_sheet_open_failed", **out)
        return out

    if not profile_exact_confirmed:
        identity = verify_unfollow_target_profile_strict(
            d,
            expected_target_username=expected_target_username,
            timeout_s=2.0,
        )
        profile_exact_confirmed = bool(identity.get("ok"))
        if not profile_exact_confirmed:
            out = {
                "ok": False,
                "failure_reason": str(
                    identity.get("failure_reason")
                    or "target_profile_pre_cta_revalidation_failed"
                ),
                "expected_target_username": expected_target_username,
                "following_detection_method": "",
                "unfollow_option_visible": False,
                "sheet_context_signals": {},
                "profile_exact_confirmed": False,
                "first_cta_probe_at": str(btn_det.get("first_cta_probe_at") or btn_det.get("probe_at") or ""),
                "cta_detection_method": "",
                "probes_count": int(btn_det.get("probes_count") or 1),
                "profile_surface_stable_at": "",
                "recovery_used": bool(btn_det.get("recovery_used")),
                "terminal_reason": "following_cta_surface_not_stable",
            }
            log("info", "unfollow_actions_sheet_open_failed", **out)
            return out

    initial_bounds = dict(btn_det.get("bounds") or {})
    log(
        "info",
        "unfollow_profile_following_button_pre_tap_revalidation_started",
        expected_target_username=expected_target_username,
        initial_bounds=initial_bounds,
        revalidation_method="detect_profile_following_button_for_unfollow",
    )
    refreshed = detect_profile_following_button_for_unfollow(
        d,
        expected_target_username=expected_target_username,
        allow_verified_wide_cta=bool(btn_det.get("verified_wide_cta")),
    )
    probes_count = int(btn_det.get("probes_count") or 1) + 1
    first_cta_probe_at = str(
        btn_det.get("first_cta_probe_at") or btn_det.get("probe_at") or ""
    )
    refreshed_bounds = dict(refreshed.get("bounds") or {})
    delta_x, delta_y, bounds_shift_px = _center_shift_metrics(initial_bounds, refreshed_bounds)
    revalidation_failure = ""
    initial_resource_id = str(btn_det.get("resource_id") or "")
    refreshed_resource_id = str(refreshed.get("resource_id") or "")
    if not refreshed.get("ok"):
        revalidation_failure = "following_button_pre_tap_revalidation_failed"
    elif initial_resource_id and refreshed_resource_id and initial_resource_id != refreshed_resource_id:
        revalidation_failure = "following_button_semantic_identity_changed"
    elif bool(btn_det.get("clickable")) and not bool(refreshed.get("clickable")):
        revalidation_failure = "following_button_no_longer_clickable"
    elif bounds_shift_px > _FOLLOWING_BUTTON_MAX_PRE_TAP_SHIFT_PX:
        revalidation_failure = "following_button_bounds_shift_too_large"

    if revalidation_failure:
        if revalidation_failure == "following_button_bounds_shift_too_large":
            retry_ok, retry_det, retry_meta = _retry_following_button_after_bounds_shift(
                d,
                expected_target_username=expected_target_username,
                initial_bounds=initial_bounds,
                refreshed_bounds=refreshed_bounds,
                center_delta_x=delta_x,
                center_delta_y=delta_y,
                bounds_shift_px=bounds_shift_px,
            )
            if retry_ok:
                refreshed = retry_det
                refreshed_bounds = dict(refreshed.get("bounds") or {})
                delta_x, delta_y, bounds_shift_px = _center_shift_metrics(
                    initial_bounds,
                    refreshed_bounds,
                )
                revalidation_failure = ""
            else:
                revalidation_failure = str(
                    retry_meta.get("retry_failure_reason")
                    or "following_button_bounds_shift_retry_failed"
                )

        if not revalidation_failure:
            pass
        else:
            log(
                "info",
                "unfollow_profile_following_button_pre_tap_revalidation_failed",
                expected_target_username=expected_target_username,
                initial_bounds=initial_bounds,
                refreshed_bounds=refreshed_bounds,
                center_delta_x=delta_x,
                center_delta_y=delta_y,
                bounds_shift_px=bounds_shift_px,
                revalidation_method=str(refreshed.get("detection_method") or ""),
                failure_reason=revalidation_failure,
            )
            out = {
                "ok": False,
                "failure_reason": (
                    "following_button_bounds_shift_too_large"
                    if revalidation_failure.startswith("following_button_retry_")
                    or revalidation_failure.startswith("target_profile_")
                    else revalidation_failure
                ),
                "expected_target_username": expected_target_username,
                "following_detection_method": str(btn_det.get("detection_method") or ""),
                "unfollow_option_visible": False,
                "sheet_context_signals": {},
                "initial_bounds": initial_bounds,
                "refreshed_bounds": refreshed_bounds,
                "center_delta_x": delta_x,
                "center_delta_y": delta_y,
                "bounds_shift_px": bounds_shift_px,
                "retry_failure_reason": revalidation_failure,
                "profile_exact_confirmed": profile_exact_confirmed,
                "first_cta_probe_at": first_cta_probe_at,
                "cta_detection_method": str(btn_det.get("stable_reason") or ""),
                "probes_count": probes_count,
                "profile_surface_stable_at": "",
                "recovery_used": bool(btn_det.get("recovery_used")),
                "terminal_reason": "following_cta_surface_not_stable",
            }
            log("info", "unfollow_actions_sheet_open_failed", **out)
            return out

    if bounds_shift_px > 0:
        log(
            "info",
            "unfollow_profile_following_button_bounds_refreshed",
            expected_target_username=expected_target_username,
            initial_bounds=initial_bounds,
            refreshed_bounds=refreshed_bounds,
            center_delta_x=delta_x,
            center_delta_y=delta_y,
            bounds_shift_px=bounds_shift_px,
            revalidation_method=str(refreshed.get("detection_method") or ""),
            failure_reason="",
        )
    log(
        "info",
        "unfollow_profile_following_button_pre_tap_revalidated",
        expected_target_username=expected_target_username,
        initial_bounds=initial_bounds,
        refreshed_bounds=refreshed_bounds,
        center_delta_x=delta_x,
        center_delta_y=delta_y,
        bounds_shift_px=bounds_shift_px,
        revalidation_method=str(refreshed.get("detection_method") or ""),
        failure_reason="",
    )

    method = str(refreshed.get("detection_method") or btn_det.get("detection_method") or "")
    bounds = refreshed_bounds
    tap_x = int(refreshed.get("tap_x") or refreshed.get("center_x") or 0)
    tap_y = int(refreshed.get("tap_y") or refreshed.get("center_y") or 0)
    try:
        d.click(tap_x, tap_y)
    except Exception as exc:
        out = {
            "ok": False,
            "failure_reason": "following_button_tap_failed",
            "expected_target_username": expected_target_username,
            "following_detection_method": method,
            "error": str(exc)[:200],
            "unfollow_option_visible": False,
            "sheet_context_signals": {},
            "bounds": bounds,
            "tap_x": tap_x,
            "tap_y": tap_y,
            "initial_bounds": initial_bounds,
            "refreshed_bounds": refreshed_bounds,
            "center_delta_x": delta_x,
            "center_delta_y": delta_y,
            "bounds_shift_px": bounds_shift_px,
            "profile_exact_confirmed": profile_exact_confirmed,
            "first_cta_probe_at": first_cta_probe_at,
            "cta_detection_method": str(refreshed.get("stable_reason") or btn_det.get("stable_reason") or ""),
            "probes_count": probes_count,
            "profile_surface_stable_at": str(refreshed.get("probe_at") or ""),
            "recovery_used": bool(btn_det.get("recovery_used")),
            "terminal_reason": "following_button_tap_failed",
        }
        log("info", "unfollow_actions_sheet_open_failed", **out)
        return out
    log(
        "info",
        "unfollow_profile_following_button_tapped",
        expected_target_username=expected_target_username,
        following_detection_method=method,
        bounds=bounds,
        tap_x=tap_x,
        tap_y=tap_y,
        initial_bounds=initial_bounds,
        refreshed_bounds=refreshed_bounds,
        center_delta_x=delta_x,
        center_delta_y=delta_y,
        bounds_shift_px=bounds_shift_px,
    )

    time.sleep(0.85)
    signals = _detect_actions_sheet_signals(d)
    sheet_open = _actions_sheet_open_from_signals(signals)
    out = {
        "ok": sheet_open,
        "failure_reason": "" if sheet_open else "actions_sheet_signals_missing",
        "expected_target_username": expected_target_username,
        "following_detection_method": method,
        "unfollow_option_visible": bool(signals.get("unfollow_visible")),
        "option_text": str(signals.get("unfollow_text") or ""),
        "detection_method": str(signals.get("unfollow_detection_method") or ""),
        "sheet_context_signals": signals,
        "initial_bounds": initial_bounds,
        "refreshed_bounds": refreshed_bounds,
        "center_delta_x": delta_x,
        "center_delta_y": delta_y,
        "bounds_shift_px": bounds_shift_px,
        "profile_exact_confirmed": profile_exact_confirmed,
        "first_cta_probe_at": first_cta_probe_at,
        "cta_detection_method": str(refreshed.get("stable_reason") or btn_det.get("stable_reason") or ""),
        "probes_count": probes_count,
        "profile_surface_stable_at": str(refreshed.get("probe_at") or ""),
        "recovery_used": bool(btn_det.get("recovery_used")),
        "terminal_reason": "",
        "cta_detected_at": str(btn_det.get("cta_detected_at") or btn_det.get("probe_at") or ""),
    }
    if sheet_open:
        log("info", "unfollow_actions_sheet_opened", **out)
        if signals.get("unfollow_visible"):
            log(
                "info",
                "unfollow_actions_sheet_unfollow_option_detected",
                option_text=str(signals.get("unfollow_text") or ""),
                detection_method=str(signals.get("unfollow_detection_method") or ""),
                sheet_context_signals=signals,
                expected_target_username=expected_target_username,
            )
        else:
            log(
                "warning",
                "unfollow_actions_sheet_unfollow_option_missing",
                sheet_context_signals=signals,
                expected_target_username=expected_target_username,
            )
    else:
        post_tap_evidence = _post_following_tap_evidence(d)
        out.update(post_tap_evidence)
        retry_block_reason = _following_tap_retry_block_reason(
            post_tap_evidence,
            expected_target_username=expected_target_username,
        )
        if not retry_block_reason:
            log(
                "info",
                "unfollow_profile_following_button_tap_retry_started",
                expected_target_username=expected_target_username,
                retry_index=1,
                refreshed_bounds_retry={},
                tap_x=0,
                tap_y=0,
                sheet_open_after_retry=False,
                failure_reason="",
                action_bar_title_after_tap=str(post_tap_evidence.get("action_bar_title_after_tap") or ""),
                profile_username_after_tap=str(post_tap_evidence.get("profile_username_after_tap") or ""),
                following_button_still_visible_after_tap=bool(
                    post_tap_evidence.get("following_button_still_visible_after_tap")
                ),
            )
            retry_det = detect_profile_following_button_for_unfollow(
                d,
                expected_target_username=expected_target_username,
            )
            refreshed_bounds_retry = dict(retry_det.get("bounds") or {})
            retry_tap_x = int(retry_det.get("tap_x") or retry_det.get("center_x") or 0)
            retry_tap_y = int(retry_det.get("tap_y") or retry_det.get("center_y") or 0)
            retry_failure_reason = ""
            if not retry_det.get("ok"):
                retry_failure_reason = str(
                    retry_det.get("failure_reason") or "following_button_retry_revalidation_failed"
                )
            log(
                "info",
                "unfollow_profile_following_button_tap_retry_revalidated",
                expected_target_username=expected_target_username,
                retry_index=1,
                refreshed_bounds_retry=refreshed_bounds_retry,
                tap_x=retry_tap_x,
                tap_y=retry_tap_y,
                sheet_open_after_retry=False,
                failure_reason=retry_failure_reason,
                retry_detection_method=str(retry_det.get("detection_method") or ""),
            )
            if retry_failure_reason:
                log(
                    "info",
                    "unfollow_profile_following_button_tap_retry_failed",
                    expected_target_username=expected_target_username,
                    retry_index=1,
                    refreshed_bounds_retry=refreshed_bounds_retry,
                    tap_x=retry_tap_x,
                    tap_y=retry_tap_y,
                    sheet_open_after_retry=False,
                    failure_reason=retry_failure_reason,
                )
            else:
                try:
                    d.click(retry_tap_x, retry_tap_y)
                    log(
                        "info",
                        "unfollow_profile_following_button_tap_retry_tapped",
                        expected_target_username=expected_target_username,
                        retry_index=1,
                        refreshed_bounds_retry=refreshed_bounds_retry,
                        tap_x=retry_tap_x,
                        tap_y=retry_tap_y,
                        sheet_open_after_retry=False,
                        failure_reason="",
                    )
                    time.sleep(0.85)
                    retry_signals = _detect_actions_sheet_signals(d)
                    sheet_open_after_retry = _actions_sheet_open_from_signals(retry_signals)
                    if sheet_open_after_retry:
                        out.update(
                            {
                                "ok": True,
                                "failure_reason": "",
                                "following_detection_method": str(
                                    retry_det.get("detection_method") or method
                                ),
                                "unfollow_option_visible": bool(
                                    retry_signals.get("unfollow_visible")
                                ),
                                "option_text": str(retry_signals.get("unfollow_text") or ""),
                                "detection_method": str(
                                    retry_signals.get("unfollow_detection_method") or ""
                                ),
                                "sheet_context_signals": retry_signals,
                                "retry_index": 1,
                                "refreshed_bounds_retry": refreshed_bounds_retry,
                                "retry_tap_x": retry_tap_x,
                                "retry_tap_y": retry_tap_y,
                                "sheet_open_after_retry": True,
                            }
                        )
                        log(
                            "info",
                            "unfollow_profile_following_button_tap_retry_succeeded",
                            expected_target_username=expected_target_username,
                            retry_index=1,
                            refreshed_bounds_retry=refreshed_bounds_retry,
                            tap_x=retry_tap_x,
                            tap_y=retry_tap_y,
                            sheet_open_after_retry=True,
                            failure_reason="",
                        )
                        log("info", "unfollow_actions_sheet_opened", **out)
                        if retry_signals.get("unfollow_visible"):
                            log(
                                "info",
                                "unfollow_actions_sheet_unfollow_option_detected",
                                option_text=str(retry_signals.get("unfollow_text") or ""),
                                detection_method=str(
                                    retry_signals.get("unfollow_detection_method") or ""
                                ),
                                sheet_context_signals=retry_signals,
                                expected_target_username=expected_target_username,
                            )
                        else:
                            log(
                                "warning",
                                "unfollow_actions_sheet_unfollow_option_missing",
                                sheet_context_signals=retry_signals,
                                expected_target_username=expected_target_username,
                            )
                        return out
                    retry_failure_reason = "actions_sheet_signals_missing_after_retry"
                    log(
                        "info",
                        "unfollow_profile_following_button_tap_retry_failed",
                        expected_target_username=expected_target_username,
                        retry_index=1,
                        refreshed_bounds_retry=refreshed_bounds_retry,
                        tap_x=retry_tap_x,
                        tap_y=retry_tap_y,
                        sheet_open_after_retry=False,
                        failure_reason=retry_failure_reason,
                    )
                    out.update(
                        {
                            "retry_index": 1,
                            "refreshed_bounds_retry": refreshed_bounds_retry,
                            "retry_tap_x": retry_tap_x,
                            "retry_tap_y": retry_tap_y,
                            "sheet_open_after_retry": False,
                            "retry_failure_reason": retry_failure_reason,
                            "sheet_context_signals_after_retry": retry_signals,
                        }
                    )
                except Exception as exc:
                    retry_failure_reason = "following_button_retry_tap_failed"
                    log(
                        "info",
                        "unfollow_profile_following_button_tap_retry_failed",
                        expected_target_username=expected_target_username,
                        retry_index=1,
                        refreshed_bounds_retry=refreshed_bounds_retry,
                        tap_x=retry_tap_x,
                        tap_y=retry_tap_y,
                        sheet_open_after_retry=False,
                        failure_reason=retry_failure_reason,
                        error=str(exc)[:200],
                    )
                    out.update(
                        {
                            "retry_index": 1,
                            "refreshed_bounds_retry": refreshed_bounds_retry,
                            "retry_tap_x": retry_tap_x,
                            "retry_tap_y": retry_tap_y,
                            "sheet_open_after_retry": False,
                            "retry_failure_reason": retry_failure_reason,
                            "retry_error": str(exc)[:200],
                        }
                    )
        else:
            out["retry_skipped_reason"] = retry_block_reason
        log("info", "unfollow_actions_sheet_open_failed", **out)
    return out


def return_to_following_list_after_unfollow_probe(
    d: u2.Device,
    *,
    account_username: str,
    max_back_steps: int = 3,
) -> dict[str, Any]:
    """Close any sheet/profile opened by the probe and return to owner Following."""
    log(
        "info",
        "unfollow_probe_return_to_following_list_started",
        account_username=account_username,
        max_back_steps=max_back_steps,
    )
    sheet_closed = False
    # First back is expected to close the actions sheet if it is open.
    try:
        d.press("back")
        sheet_closed = True
        log("info", "unfollow_probe_sheet_closed", account_username=account_username, method="back")
    except Exception as exc:
        log(
            "info",
            "unfollow_probe_sheet_closed",
            account_username=account_username,
            method="back",
            error=str(exc)[:200],
        )
    time.sleep(0.35)

    last_det: dict[str, Any] = {}
    for step in range(max(1, int(max_back_steps))):
        det = detect_own_following_list_screen(d, account_username=account_username)
        last_det = det
        if det.get("is_following_list"):
            out = {
                "ok": True,
                "method": "already_or_back",
                "back_steps": step,
                "sheet_closed": sheet_closed,
                "failure_reason": "",
            }
            log("info", "unfollow_probe_return_to_following_list_ok", **out)
            return out
        try:
            d.press("back")
        except Exception:
            pass
        time.sleep(0.45)

    out = {
        "ok": False,
        "method": "back",
        "back_steps": max(1, int(max_back_steps)),
        "sheet_closed": sheet_closed,
        "failure_reason": str(last_det.get("failure_reason") or "following_list_not_detected"),
        "last_detection": last_det,
    }
    log("info", "unfollow_probe_return_to_following_list_failed", **out)
    return out


def return_to_following_list_after_unfollow_action(
    d: u2.Device,
    *,
    account_username: str,
    max_back_steps: int = 3,
) -> dict[str, Any]:
    """Return from target profile to owner Following after a real Unfollow tap."""
    log(
        "info",
        "unfollow_action_return_to_following_list_started",
        account_username=account_username,
        max_back_steps=max_back_steps,
    )
    last_det: dict[str, Any] = {}
    for step in range(max(1, int(max_back_steps))):
        det = detect_own_following_list_screen(d, account_username=account_username)
        last_det = det
        if det.get("is_following_list"):
            out = {"ok": True, "method": "already_on_following", "back_steps": step, "failure_reason": ""}
            log("info", "unfollow_action_return_to_following_list_ok", **out)
            return out
        try:
            d.press("back")
        except Exception:
            pass
        time.sleep(0.45)
    out = {
        "ok": False,
        "method": "back",
        "back_steps": max(1, int(max_back_steps)),
        "failure_reason": str(last_det.get("failure_reason") or "following_list_not_detected"),
        "last_detection": last_det,
    }
    log("info", "unfollow_action_return_to_following_list_failed", **out)
    return out
