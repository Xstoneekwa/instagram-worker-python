"""Own profile -> own Following list navigation for Unfollow Phase 2A.

This is intentionally separate from the Followers/Welcome helpers. It opens and
validates the Following surface only; it never taps a row CTA.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import uiautomator2 as u2

import config
from logs import log
from own_profile_navigation import open_own_profile_from_bottom_nav, verify_own_profile
from unfollow_list_harvest import classify_following_row_cta, normalize_unfollow_username

_FOLLOWING_TAB_TEXTS = (
    "Following",
    "Suivis",
    "Abonnements",
    "Siguiendo",
)
_FOLLOWERS_TAB_TEXTS = (
    "Followers",
    "Follower",
    "Abonnés",
    "Abonné(e)s",
    "Seguidores",
)
_UNFOLLOW_SORT_OPTION_TEXT_BY_MODE = {
    "oldest-to-newest": "Date followed: earliest",
    "newest-to-oldest": "Date followed: latest",
}
_UNFOLLOW_SORT_OPTION_TEXTS = (
    "Default",
    "Date followed: latest",
    "Date followed: earliest",
)


def _dump_hierarchy(d: u2.Device) -> str:
    try:
        try:
            return str(d.dump_hierarchy(compressed=False) or "")
        except TypeError:
            return str(d.dump_hierarchy() or "")
    except Exception:
        return ""


def _parse_bounds(raw: str | None) -> dict[str, int]:
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


def _bounds_center(bounds: dict[str, int]) -> tuple[int, int] | None:
    if not bounds:
        return None
    left = int(bounds.get("left", 0))
    right = int(bounds.get("right", 0))
    top = int(bounds.get("top", 0))
    bottom = int(bounds.get("bottom", 0))
    if right <= left or bottom <= top:
        return None
    return (left + right) // 2, (top + bottom) // 2


def _parent_map(root: ET.Element) -> dict[ET.Element, ET.Element | None]:
    parents: dict[ET.Element, ET.Element | None] = {root: None}
    for parent in root.iter():
        for child in list(parent):
            parents[child] = parent
    return parents


def _element_selected(el: ET.Element, parents: dict[ET.Element, ET.Element | None]) -> bool:
    cur: ET.Element | None = el
    while cur is not None:
        if str(cur.get("selected") or "").lower() == "true":
            return True
        cur = parents.get(cur)
    return False


def _element_text(el: ET.Element) -> str:
    return str(el.get("text") or el.get("content-desc") or "").strip()


def _is_following_label(raw: str) -> bool:
    text = str(raw or "").strip().lower()
    if not text:
        return False
    if "followers" in text or "follower" in text:
        return False
    if text in {x.lower() for x in _FOLLOWING_TAB_TEXTS}:
        return True
    if re.search(r"\b\d[\d,.\s]*\s+following\b", text):
        return True
    if re.search(r"\b\d[\d,.\s]*\s+suivis\b", text):
        return True
    return "following" in text and "follower" not in text


def _is_followers_label(raw: str) -> bool:
    text = str(raw or "").strip().lower()
    if not text:
        return False
    if "following" in text:
        return False
    if text in {x.lower() for x in _FOLLOWERS_TAB_TEXTS}:
        return True
    if "followers" in text or "follower" in text:
        return True
    if "abonné" in text or "abonnés" in text:
        return True
    return False


def _parse_xml_root(hierarchy_xml: str) -> ET.Element | None:
    hierarchy = str(hierarchy_xml or "").strip()
    if not hierarchy:
        return None
    try:
        try:
            return ET.fromstring(hierarchy)
        except ET.ParseError:
            return ET.fromstring(f"<wrap>{hierarchy}</wrap>")
    except Exception:
        return None


def _guess_sort_mode_from_label(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    if "earliest" in text:
        return "oldest-to-newest"
    if "latest" in text:
        return "newest-to-oldest"
    if "default" in text:
        return "default"
    return ""


def _sort_option_signals(hierarchy_xml: str) -> dict[str, Any]:
    root = _parse_xml_root(hierarchy_xml)
    signals: dict[str, Any] = {
        "default_visible": False,
        "latest_visible": False,
        "earliest_visible": False,
        "visible_options": [],
    }
    if root is None:
        return signals
    visible_options: list[str] = []
    for el in root.iter():
        text = _element_text(el)
        if text in _UNFOLLOW_SORT_OPTION_TEXTS:
            visible_options.append(text)
        if text == "Default":
            signals["default_visible"] = True
        elif text == "Date followed: latest":
            signals["latest_visible"] = True
        elif text == "Date followed: earliest":
            signals["earliest_visible"] = True
    signals["visible_options"] = visible_options
    return signals


def _find_exact_text_element(
    hierarchy_xml: str,
    wanted_text: str,
) -> tuple[ET.Element | None, dict[str, int], tuple[int, int] | None, str]:
    root = _parse_xml_root(hierarchy_xml)
    if root is None:
        return None, {}, None, ""
    for el in root.iter():
        text = _element_text(el)
        if text != wanted_text:
            continue
        bounds = _parse_bounds(el.get("bounds"))
        center = _bounds_center(bounds)
        if center is None:
            continue
        return el, bounds, center, "exact_text"
    return None, {}, None, ""


def detect_unfollow_following_sort_control(
    d: u2.Device,
    *,
    hierarchy_xml: str | None = None,
) -> dict[str, Any]:
    """Detect the visible Sort by control on the Following list."""
    hierarchy = str(hierarchy_xml or "").strip() or _dump_hierarchy(d)
    out: dict[str, Any] = {
        "ok": False,
        "current_sort_label": "",
        "current_sort_mode_ui_guess": "",
        "bounds": {},
        "resource_id": "",
        "text": "",
        "failure_reason": "",
    }
    root = _parse_xml_root(hierarchy)
    if root is None:
        out["failure_reason"] = "hierarchy_xml_parse_failed"
        log("info", "unfollow_sort_control_not_found", **out)
        return out

    candidates: list[tuple[int, int, ET.Element, dict[str, int], str, str]] = []
    for el in root.iter():
        text = _element_text(el)
        text_l = text.lower()
        rid = str(el.get("resource-id") or "")
        rid_l = rid.lower()
        if not text:
            continue
        looks_like_sort = (
            text_l.startswith("sorted by ")
            or text_l in {"sorted by default", "sorted by date followed: latest", "sorted by date followed: earliest"}
            or ("sort" in rid_l and ("default" in text_l or "date followed" in text_l))
        )
        if not looks_like_sort:
            continue
        bounds = _parse_bounds(el.get("bounds"))
        center = _bounds_center(bounds)
        if center is None:
            continue
        cx, cy = center
        candidates.append((cy, cx, el, bounds, text, rid))

    if not candidates:
        out["failure_reason"] = "sort_control_not_found"
        log("info", "unfollow_sort_control_not_found", **out)
        return out

    candidates.sort(key=lambda item: (item[0], item[1]))
    _, _, el, bounds, text, rid = candidates[0]
    out.update(
        {
            "ok": True,
            "current_sort_label": text,
            "current_sort_mode_ui_guess": _guess_sort_mode_from_label(text),
            "bounds": bounds,
            "resource_id": rid,
            "text": text,
            "class": str(el.get("class") or ""),
        }
    )
    log("info", "unfollow_sort_control_detected", **out)
    return out


def open_unfollow_following_sort_sheet(
    d: u2.Device,
    *,
    requested_sort_mode: str,
    sort_control: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Open Instagram's Following-list Sort by sheet/menu."""
    control = sort_control if isinstance(sort_control, dict) else {}
    if not control.get("ok"):
        control = detect_unfollow_following_sort_control(d)

    out: dict[str, Any] = {
        "ok": False,
        "requested_sort_mode": str(requested_sort_mode or ""),
        "sort_control_text": str(control.get("text") or control.get("current_sort_label") or ""),
        "sort_control_bounds": dict(control.get("bounds") or {}),
        "sheet_option_signals": {},
        "failure_reason": "",
    }
    if not control.get("ok"):
        out["failure_reason"] = str(control.get("failure_reason") or "sort_control_not_found")
        log("info", "unfollow_sort_sheet_open_failed", **out)
        return out

    center = _bounds_center(dict(control.get("bounds") or {}))
    if center is None:
        out["failure_reason"] = "sort_control_bounds_missing"
        log("info", "unfollow_sort_sheet_open_failed", **out)
        return out

    tap_x, tap_y = center
    log("info", "unfollow_sort_sheet_open_started", **out, tap_x=tap_x, tap_y=tap_y)
    try:
        d.click(tap_x, tap_y)
    except Exception as exc:
        out["failure_reason"] = "sort_control_tap_failed"
        out["error"] = str(exc)[:200]
        log("info", "unfollow_sort_sheet_open_failed", **out, tap_x=tap_x, tap_y=tap_y)
        return out

    time.sleep(0.65)
    signals = _sort_option_signals(_dump_hierarchy(d))
    sheet_open = bool(
        signals.get("default_visible")
        or signals.get("latest_visible")
        or signals.get("earliest_visible")
    )
    out.update(
        {
            "ok": sheet_open,
            "sheet_option_signals": signals,
            "failure_reason": "" if sheet_open else "sort_sheet_options_missing",
            "tap_x": tap_x,
            "tap_y": tap_y,
        }
    )
    log("info", "unfollow_sort_sheet_opened" if sheet_open else "unfollow_sort_sheet_open_failed", **out)
    return out


def apply_unfollow_following_sort_mode(
    d: u2.Device,
    *,
    requested_sort_mode: str,
    sort_control: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply requested Following sort mode. Default is an explicit no-op."""
    requested = str(requested_sort_mode or "default").strip().lower() or "default"
    if requested == "default":
        log(
            "info",
            "unfollow_sort_apply_skipped_default_mode",
            requested_sort_mode=requested,
        )
        return {
            "ok": True,
            "skipped": True,
            "requested_sort_mode": requested,
            "failure_reason": "",
            "option_text": "",
            "detection_method": "",
            "bounds": {},
            "tap_x": 0,
            "tap_y": 0,
        }

    option_text = _UNFOLLOW_SORT_OPTION_TEXT_BY_MODE.get(requested)
    if not option_text:
        return {
            "ok": False,
            "skipped": False,
            "requested_sort_mode": requested,
            "failure_reason": "unsupported_unfollow_sort_mode",
            "option_text": "",
            "detection_method": "",
            "bounds": {},
            "tap_x": 0,
            "tap_y": 0,
        }

    opened = open_unfollow_following_sort_sheet(
        d,
        requested_sort_mode=requested,
        sort_control=sort_control,
    )
    if not opened.get("ok"):
        return {
            **opened,
            "skipped": False,
            "option_text": option_text,
            "detection_method": "",
            "bounds": {},
            "tap_x": 0,
            "tap_y": 0,
        }

    _, bounds, center, method = _find_exact_text_element(_dump_hierarchy(d), option_text)
    if center is None:
        out = {
            **opened,
            "ok": False,
            "skipped": False,
            "failure_reason": "sort_option_not_found",
            "option_text": option_text,
            "detection_method": "",
            "bounds": {},
            "tap_x": 0,
            "tap_y": 0,
        }
        log("info", "unfollow_sort_option_detected", **out)
        return out

    tap_x, tap_y = center
    out = {
        **opened,
        "ok": True,
        "skipped": False,
        "failure_reason": "",
        "option_text": option_text,
        "detection_method": method,
        "bounds": bounds,
        "tap_x": tap_x,
        "tap_y": tap_y,
    }
    log("info", "unfollow_sort_option_detected", **out)
    log("info", "unfollow_sort_option_tap_started", **out)
    try:
        d.click(tap_x, tap_y)
    except Exception as exc:
        out["ok"] = False
        out["failure_reason"] = "sort_option_tap_failed"
        out["error"] = str(exc)[:200]
        return out
    log("info", "unfollow_sort_option_tapped", **out)
    return out


def verify_unfollow_following_sort_applied(
    d: u2.Device,
    *,
    account_username: str,
    requested_sort_mode: str,
    settle_s: float = 1.25,
) -> dict[str, Any]:
    """Verify sort application and that we are still on the owner Following list."""
    requested = str(requested_sort_mode or "default").strip().lower() or "default"
    if settle_s > 0:
        time.sleep(min(float(settle_s), 4.0))

    hierarchy = _dump_hierarchy(d)
    det = detect_own_following_list_screen(
        d,
        account_username=account_username,
        hierarchy_xml=hierarchy,
    )
    control = detect_unfollow_following_sort_control(d, hierarchy_xml=hierarchy)
    ui_after = str(control.get("current_sort_mode_ui_guess") or "")
    strong = requested == "default" or (bool(control.get("ok")) and ui_after == requested)
    surface_ok = bool(det.get("is_following_list"))
    ok = surface_ok and (strong or requested != "default")
    out = {
        "ok": ok,
        "requested_sort_mode": requested,
        "following_surface_ok": surface_ok,
        "sort_mode_ui_after": ui_after,
        "sort_label_after": str(control.get("current_sort_label") or ""),
        "verification_strength": "strong_ui_label" if strong else "surface_only_fallback",
        "failure_reason": "" if ok else str(det.get("failure_reason") or "following_list_not_verified_after_sort"),
    }
    log("info", "unfollow_sort_apply_verified" if ok else "unfollow_sort_apply_verify_failed", **out)
    return out


def detect_own_following_list_screen(
    d: u2.Device,
    *,
    account_username: str = "",
    hierarchy_xml: str | None = None,
) -> dict[str, Any]:
    """Detect the owner Following list surface, explicitly rejecting Followers."""
    hierarchy = str(hierarchy_xml or "").strip() or _dump_hierarchy(d)
    out: dict[str, Any] = {
        "is_following_list": False,
        "detected_reason": "",
        "failure_reason": "",
        "active_tab_text": "",
        "followers_tab_active": False,
        "following_tab_active": False,
        "usernames_visible_count": 0,
        "follow_list_container_present": False,
        "unified_follow_list_tab_layout_present": False,
        "recycler_present": False,
        "listview_present": False,
        "action_bar_title": "",
        "cta_counts": {},
        "hierarchy_xml_len": len(hierarchy),
        "account_username": str(account_username or ""),
    }
    root = _parse_xml_root(hierarchy)
    if root is None:
        out["failure_reason"] = "hierarchy_xml_parse_failed"
        log("info", "unfollow_following_list_detect_failed", **out)
        return out

    parents = _parent_map(root)
    source_key = normalize_unfollow_username(account_username)
    usernames: set[str] = set()
    cta_counts: dict[str, int] = {
        "following": 0,
        "message": 0,
        "follow": 0,
        "follow_back": 0,
        "unknown": 0,
    }

    for el in root.iter():
        rid = str(el.get("resource-id") or "")
        rid_l = rid.lower()
        cls_l = str(el.get("class") or "").lower()
        text = str(el.get("text") or el.get("content-desc") or "").strip()
        if "unified_follow_list_tab_layout" in rid_l:
            out["unified_follow_list_tab_layout_present"] = True
        if "follow_list_container" in rid_l:
            out["follow_list_container_present"] = True
        if "recyclerview" in cls_l:
            out["recycler_present"] = True
        if cls_l == "android.widget.listview" or rid_l.endswith(":id/list"):
            out["listview_present"] = True
        if "action_bar_title" in rid_l and text:
            out["action_bar_title"] = text
        if "follow_list_username" in rid_l and text:
            username_key = normalize_unfollow_username(text)
            if username_key and username_key != source_key:
                usernames.add(username_key)
        if "follow_list_row_large_follow_button" in rid_l:
            cta_class = classify_following_row_cta(text)
            cta_counts[cta_class] = int(cta_counts.get(cta_class, 0)) + 1
        if text and _element_selected(el, parents):
            if _is_following_label(text):
                out["following_tab_active"] = True
                out["active_tab_text"] = text
            elif _is_followers_label(text):
                out["followers_tab_active"] = True
                if not out["active_tab_text"]:
                    out["active_tab_text"] = text

    out["usernames_visible_count"] = len(usernames)
    out["cta_counts"] = cta_counts

    list_chrome_ok = bool(
        out["follow_list_container_present"]
        or out["recycler_present"]
        or out["listview_present"]
    )
    rows_ok = int(out["usernames_visible_count"] or 0) > 0
    expected_key = normalize_unfollow_username(account_username)
    action_bar_key = normalize_unfollow_username(str(out.get("action_bar_title") or ""))
    if bool(out["followers_tab_active"]):
        out["failure_reason"] = "followers_tab_active"
    elif expected_key and action_bar_key and action_bar_key != expected_key:
        out["failure_reason"] = "following_list_account_title_mismatch"
    elif not bool(out["following_tab_active"]):
        out["failure_reason"] = "following_tab_not_active"
    elif not list_chrome_ok:
        out["failure_reason"] = "following_list_chrome_missing"
    elif not rows_ok:
        out["failure_reason"] = "following_usernames_missing"
    else:
        out["is_following_list"] = True
        out["detected_reason"] = "selected_following_tab_with_visible_usernames"

    if out["is_following_list"]:
        log("info", "unfollow_following_list_detected", **out)
    else:
        if out.get("failure_reason") == "following_list_account_title_mismatch":
            log(
                "error",
                "unfollow_following_list_account_check_failed",
                expected_account_username=str(account_username or ""),
                actual_action_bar_title=str(out.get("action_bar_title") or ""),
                failure_reason="following_list_account_title_mismatch",
            )
        log("info", "unfollow_following_list_detect_failed", **out)
    return out


def _tap_profile_following_stat(d: u2.Device) -> tuple[bool, dict[str, Any]]:
    diag: dict[str, Any] = {
        "tap_method": "",
        "tap_x": None,
        "tap_y": None,
        "following_stat_bounds": None,
        "following_stat_text": "",
    }
    hierarchy = _dump_hierarchy(d)
    root = _parse_xml_root(hierarchy)
    try:
        w, h = d.window_size()
    except Exception:
        w, h = 1080, 2400

    if root is not None:
        candidates: list[tuple[int, int, dict[str, int], str, str]] = []
        for el in root.iter():
            rid = str(el.get("resource-id") or "")
            rid_l = rid.lower()
            text = str(el.get("text") or el.get("content-desc") or "").strip()
            bounds = _parse_bounds(el.get("bounds"))
            center = _bounds_center(bounds)
            if center is None:
                continue
            cx, cy = center
            if cy < int(h * 0.08) or cy > int(h * 0.48):
                continue
            if "profile_header_following" in rid_l and "follower" not in rid_l:
                candidates.append((0, cx, bounds, text, "resource_id_profile_header_following"))
            elif _is_following_label(text) and cx > int(w * 0.50):
                candidates.append((1, cx, bounds, text, "text_following_right_column"))
        if candidates:
            candidates.sort(key=lambda item: (item[0], -item[1]))
            _, cx, bounds, text, method = candidates[0]
            cy = (int(bounds["top"]) + int(bounds["bottom"])) // 2
            try:
                d.click(cx, cy)
                diag.update(
                    {
                        "tap_method": method,
                        "tap_x": cx,
                        "tap_y": cy,
                        "following_stat_bounds": bounds,
                        "following_stat_text": text,
                    }
                )
                return True, diag
            except Exception as exc:
                diag["error"] = str(exc)[:200]

    # Last resort: right column of the profile stats band. This is only the profile stat tap,
    # not a row CTA tap.
    try:
        cx = int(w * 0.82)
        cy = int(h * 0.185)
        d.click(cx, cy)
        diag.update({"tap_method": "coordinate_right_stats_column", "tap_x": cx, "tap_y": cy})
        return True, diag
    except Exception as exc:
        diag["error"] = str(exc)[:200]
        return False, diag


def open_own_following_list_from_own_profile(
    d: u2.Device,
    account_username: str,
    *,
    pkg: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Open the owner Following list and validate the dedicated Following surface."""
    del pkg  # kept for symmetry with existing own profile helpers.
    account_username = str(account_username or "").strip()
    log("info", "unfollow_open_own_profile_started", account_username=account_username)
    if not open_own_profile_from_bottom_nav(d):
        log("info", "unfollow_open_following_list_failed", reason="own_profile_open_failed")
        return False, {"failure_reason": "own_profile_open_failed"}

    profile_ok, profile_meta = verify_own_profile(d, account_username)
    if not profile_ok:
        log(
            "info",
            "unfollow_open_following_list_failed",
            reason="own_profile_verify_failed",
            profile_meta=profile_meta,
        )
        return False, {"failure_reason": "own_profile_verify_failed", "profile_meta": profile_meta}
    log("info", "unfollow_open_own_profile_ok", account_username=account_username)

    log("info", "unfollow_open_following_list_started", account_username=account_username)
    tapped, tap_diag = _tap_profile_following_stat(d)
    if not tapped:
        log(
            "info",
            "unfollow_open_following_list_failed",
            reason="following_stat_tap_failed",
            tap_diag=tap_diag,
        )
        return False, {"failure_reason": "following_stat_tap_failed", "tap_diag": tap_diag}
    log("info", "unfollow_open_following_list_tapped", account_username=account_username, **tap_diag)

    wait_s = float(getattr(config, "WELCOME_BASELINE_FOLLOWERS_OPEN_WAIT_S", 4.0) or 4.0)
    if wait_s > 0:
        time.sleep(min(wait_s, 8.0))

    det = detect_own_following_list_screen(d, account_username=account_username)
    if not bool(det.get("is_following_list")):
        log(
            "info",
            "unfollow_open_following_list_failed",
            reason=str(det.get("failure_reason") or "following_list_not_detected"),
            det=det,
        )
        return False, {"failure_reason": str(det.get("failure_reason") or "following_list_not_detected"), "tap_diag": tap_diag, "det": det}

    log(
        "info",
        "unfollow_open_following_list_ok",
        account_username=account_username,
        active_tab_text=str(det.get("active_tab_text") or ""),
        usernames_visible_count=int(det.get("usernames_visible_count") or 0),
    )
    return True, {"open_method": "profile_following_stat_tap", "tap_diag": tap_diag, "det": det}
