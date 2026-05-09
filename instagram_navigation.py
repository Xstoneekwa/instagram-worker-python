"""Safe Instagram navigation (search → Accounts → profile) with optional Follow SAFE layer."""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from pathlib import Path
from typing import Any, Callable
import xml.etree.ElementTree as ET

import uiautomator2 as u2

import config
from device import (
    get_device_serial,
    is_fast_ime_available,
    retry_until,
    retry_until_jitter,
    run_fast_ime_input,
    screenshot,
    shell,
)
from logs import log

# Set by runner after open_accounts_tab: "accounts_tab" | "mixed_results"
_search_ui_mode = "accounts_tab"

_TYPE_SEARCH_FAILURE_REASON: str | None = None


def get_type_search_failure_reason() -> str | None:
    return _TYPE_SEARCH_FAILURE_REASON

# Warm search surface cache (same session / activity family)
_LAST_SEARCH_SURFACE_TS: float = 0.0
_LAST_SEARCH_SURFACE_OK: bool = False
_LAST_SEARCH_SURFACE_PKG: str = ""
_LAST_SEARCH_SURFACE_ACTIVITY_FAMILY: str = ""

# Phase timings for performance_summary (reset each run from runner)
_perf: dict[str, float | int | bool] = {}

_WAIT_EVENT_CALLBACK: Callable[..., None] | None = None
_LAST_DM_THREAD_CLASSIFY_SNAPSHOT: dict[str, Any] = {}
_LAST_DM_SEND_RESULT: dict[str, Any] = {}
_LAST_DM_THREAD_ATTEMPTED: bool = False
_LAST_DM_THREAD_STATE: str = "unknown"

_SEARCH_TAB_RID_SUFFIXES = ("search_tab", "bottom_bar_search", "tab_search")

# Instagram account search row username (Propulse / GramAddict style)
ROW_SEARCH_USER_USERNAME_RES_NAME = "row_search_user_username"
# Prefer exact package id before resourceIdMatches (v2 hot path + full row lookup)
ROW_SEARCH_USERNAME_EXACT_RES = "com.instagram.android:id/row_search_user_username"

# FastIME success: defer typing confirm to first row_search_user_username in tap_account_result
_PENDING_FUSED_FAST_IME_ROW: bool = False
_PENDING_FUSED_FAST_IME_USERNAME: str = ""

# Debug artifacts (relative to this package)
_LOGS_ROOT = Path(__file__).resolve().parent / "logs"
_SCREENSHOTS_DIR = _LOGS_ROOT / "screenshots"
_XML_DIR = _LOGS_ROOT / "xml"

# Horizontal search tab chips (EN/FR + variants)
_ACCOUNTS_TAB_LABELS = (
    "Accounts",
    "Account",
    "Comptes",
    "Profils",
    "People",
    "Personnes",
)

# Tab strip is usually in the upper portion; avoid matching list rows.
_TAB_CHIP_MAX_Y_RATIO = 0.34


def set_search_ui_mode(mode: str) -> None:
    global _search_ui_mode
    if mode in ("accounts_tab", "mixed_results"):
        _search_ui_mode = mode


def get_search_ui_mode() -> str:
    return _search_ui_mode


def reset_perf_counters() -> None:
    global _perf
    _perf = {
        "search_click_ms": 0.0,
        "search_field_ready_ms": 0.0,
        "search_surface_reused": False,
        "search_back_to_search_ms": 0.0,
        "search_open_skipped_ms": 0.0,
        "typing_command_ms": 0.0,
        "typing_confirm_ms": 0.0,
        "row_detect_ms": 0.0,
        "row_tap_command_ms": 0.0,
        "post_tap_settle_ms": 0.0,
        "profile_transition_wait_ms": 0.0,
        "profile_verify_ms": 0.0,
        "xml_fetches": 0,
        "recovery_used": False,
    }


def get_perf_snapshot() -> dict[str, float | int | bool]:
    return dict(_perf)


def _bump_xml_fetch() -> None:
    global _perf
    if "xml_fetches" not in _perf:
        _perf["xml_fetches"] = 0
    _perf["xml_fetches"] = int(_perf["xml_fetches"]) + 1


def _activity_family(activity: str | None) -> str:
    if not activity:
        return ""
    a = str(activity).strip()
    return a.rsplit(".", 1)[-1]


def _session_modal_or_crash(d: u2.Device) -> bool:
    for frag in (
        "Unfortunately",
        "isn't responding",
        "is not responding",
        "has stopped",
        "Close app",
        "Fermer l",
    ):
        try:
            if d(textContains=frag).wait(timeout=0.03):
                return True
        except Exception:
            continue
    return False


def instagram_warm_session_eligible(d: u2.Device, pkg: str | None = None) -> tuple[bool, str]:
    """Skip force-stop when IG is foreground and UI looks healthy."""
    pkg = pkg or config.INSTAGRAM_PACKAGE
    try:
        if _session_modal_or_crash(d):
            return False, "modal_or_crash"
        cur = d.app_current()
        if (cur or {}).get("package", "") != pkg:
            return False, "wrong_package"
        try:
            d.window_size()
        except Exception:
            return False, "broken_ui"
        return True, "ok"
    except Exception as e:
        return False, str(e)


def invalidate_search_surface_cache(reason: str = "") -> None:
    global _LAST_SEARCH_SURFACE_OK
    _LAST_SEARCH_SURFACE_OK = False
    log("debug", "search_surface_cache_invalidated", reason=reason)


def is_lightweight_search_screen(d: u2.Device, pkg: str | None = None) -> bool:
    """
    True if IG search entry is likely active: foreground, top-band EditText, optional search tab selected.
    No XML / dump.
    """
    pkg = pkg or config.INSTAGRAM_PACKAGE
    try:
        cur = d.app_current()
        if (cur or {}).get("package", "") != pkg:
            return False
        if _session_modal_or_crash(d):
            return False
        ed = d(className="android.widget.EditText")
        if not ed.wait(timeout=0.08):
            return False
        try:
            b = ed.info["bounds"]
            _w, h = d.window_size()
            if int(b["bottom"]) > int(h * 0.38):
                return False
        except Exception:
            pass
        for suffix in _SEARCH_TAB_RID_SUFFIXES:
            try:
                sel = d(resourceIdMatches=f".*:id/{suffix}")
                if not sel.wait(timeout=0.04):
                    continue
                info = sel.info or {}
                if info.get("selected") is True:
                    return True
            except Exception:
                continue
        for rid_hint in (
            "com.instagram.android:id/action_bar_search_edit_text",
            "com.instagram.android:id/search_bar",
        ):
            try:
                if d(resourceId=rid_hint).wait(timeout=0.04):
                    return True
            except Exception:
                continue
        return True
    except Exception:
        return False


def apply_search_surface_reuse_metrics(d: u2.Device, pkg: str, reason: str) -> bool:
    """Set perf + log search_surface_reused when EditText is ready (no nav click)."""
    global _perf
    t_ed = time.perf_counter()
    if _wait_search_edittext(d) is None:
        return False
    _perf["search_field_ready_ms"] = (time.perf_counter() - t_ed) * 1000
    _perf["search_click_ms"] = 0.0
    _perf["search_surface_reused"] = True
    _perf["search_open_skipped_ms"] = float(
        getattr(config, "SEARCH_OPEN_SKIP_CREDIT_MS", 2800.0)
    )
    log(
        "info",
        "search_surface_reused",
        reason=reason,
        package=pkg,
        search_field_ready_ms=round(_perf["search_field_ready_ms"], 2),
        search_open_skipped_ms=_perf["search_open_skipped_ms"],
    )
    log(
        "info",
        "open_search_timing",
        search_click_ms=0.0,
        search_field_ready_ms=round(float(_perf["search_field_ready_ms"]), 2),
        selector=f"surface_reuse:{reason}",
        ok=True,
    )
    return True


def return_to_search_from_profile(d: u2.Device, pkg: str | None = None) -> bool:
    """
    One back + poll for lightweight search. Sets search_back_to_search_ms.
    On success applies search surface reuse metrics (skip open_search).
    """
    global _perf
    pkg = pkg or config.INSTAGRAM_PACKAGE
    t0 = time.perf_counter()
    try:
        d.press("back")
    except Exception as e:
        log("warning", "search_back_press_failed", error=str(e))
        _perf["search_back_to_search_ms"] = (time.perf_counter() - t0) * 1000
        return False
    deadline = time.monotonic() + float(getattr(config, "BACK_TO_SEARCH_MAX_WAIT_S", 3.0))
    while time.monotonic() < deadline:
        if is_lightweight_search_screen(d, pkg):
            _perf["search_back_to_search_ms"] = (time.perf_counter() - t0) * 1000
            log(
                "info",
                "search_back_to_search_ok",
                search_back_to_search_ms=round(_perf["search_back_to_search_ms"], 2),
            )
            return apply_search_surface_reuse_metrics(d, pkg, "back_from_profile")
        time.sleep(0.08)
    _perf["search_back_to_search_ms"] = (time.perf_counter() - t0) * 1000
    log(
        "warning",
        "search_back_to_search_timeout",
        search_back_to_search_ms=round(_perf["search_back_to_search_ms"], 2),
    )
    return False


def should_reuse_search_surface(d: u2.Device, pkg: str) -> bool:
    if not _LAST_SEARCH_SURFACE_OK:
        return False
    if time.time() - _LAST_SEARCH_SURFACE_TS > float(
        getattr(config, "SEARCH_SURFACE_CACHE_TTL_S", 600.0)
    ):
        invalidate_search_surface_cache("ttl")
        return False
    if _LAST_SEARCH_SURFACE_PKG != pkg:
        return False
    try:
        cur = d.app_current()
        if (cur or {}).get("package", "") != pkg:
            return False
        act_f = _activity_family((cur or {}).get("activity"))
        if act_f != _LAST_SEARCH_SURFACE_ACTIVITY_FAMILY:
            return False
        if _session_modal_or_crash(d):
            return False
        ed = d(className="android.widget.EditText")
        if not ed.wait(timeout=0.12):
            return False
        return True
    except Exception:
        return False


def _mark_search_surface_ok(d: u2.Device, pkg: str) -> None:
    global _LAST_SEARCH_SURFACE_TS, _LAST_SEARCH_SURFACE_OK
    global _LAST_SEARCH_SURFACE_PKG, _LAST_SEARCH_SURFACE_ACTIVITY_FAMILY
    try:
        cur = d.app_current()
        _LAST_SEARCH_SURFACE_OK = True
        _LAST_SEARCH_SURFACE_TS = time.time()
        _LAST_SEARCH_SURFACE_PKG = pkg
        _LAST_SEARCH_SURFACE_ACTIVITY_FAMILY = _activity_family((cur or {}).get("activity"))
    except Exception:
        _LAST_SEARCH_SURFACE_OK = False


def _instagram_package_candidates(d: u2.Device) -> list[str]:
    """Packages to build full resource ids (current app + config + common variants)."""
    out: list[str] = []
    try:
        cur = d.app_current()
        p = (cur or {}).get("package", "") or ""
        if p and p not in out:
            out.append(p)
    except Exception:
        pass
    for p in (
        config.INSTAGRAM_PACKAGE,
        "com.instagram.android",
        "com.instagram.androii",
    ):
        if p and p not in out:
            out.append(p)
    return out


def _row_search_username_resource_id_list(d: u2.Device) -> list[str]:
    """Full resource-id strings for row_search_user_username."""
    ids: list[str] = []
    for pkg in _instagram_package_candidates(d):
        rid = f"{pkg}:id/{ROW_SEARCH_USER_USERNAME_RES_NAME}"
        if rid not in ids:
            ids.append(rid)
    return ids


def _clear_pending_fused_fast_ime_row() -> None:
    global _PENDING_FUSED_FAST_IME_ROW, _PENDING_FUSED_FAST_IME_USERNAME
    _PENDING_FUSED_FAST_IME_ROW = False
    _PENDING_FUSED_FAST_IME_USERNAME = ""


def _set_pending_fused_fast_ime_row(username: str) -> None:
    global _PENDING_FUSED_FAST_IME_ROW, _PENDING_FUSED_FAST_IME_USERNAME
    _PENDING_FUSED_FAST_IME_ROW = True
    _PENDING_FUSED_FAST_IME_USERNAME = username


def _peek_pending_fused_fast_ime_row(username: str) -> bool:
    return _PENDING_FUSED_FAST_IME_ROW and _PENDING_FUSED_FAST_IME_USERNAME == username


def _collect_raw_row_search_elements(d: u2.Device) -> list[tuple[object, str]]:
    """
    Ordered candidates: exact com.instagram.android:id/row_search_user_username,
    then resourceIdMatches .*/id/row_search_user_username, then per-package ids,
    then legacy .* :id pattern. No XPath/XML.
    """
    raw: list[tuple[object, str]] = []
    seen_el: set[int] = set()

    def _append_from_selector(sel, rid_hint: str) -> None:
        try:
            arr = sel.all() if hasattr(sel, "all") else list(sel)
        except Exception:
            arr = []
        for el in arr:
            oid = id(el)
            if oid in seen_el:
                continue
            seen_el.add(oid)
            raw.append((el, rid_hint))

    try:
        _append_from_selector(d(resourceId=ROW_SEARCH_USERNAME_EXACT_RES), ROW_SEARCH_USERNAME_EXACT_RES)
    except Exception:
        pass
    try:
        _append_from_selector(
            d(resourceIdMatches=r".*/id/row_search_user_username"),
            "",
        )
    except Exception:
        pass

    if not raw:
        for rid in _row_search_username_resource_id_list(d):
            try:
                s2 = d(resourceId=rid)
                got = s2.all() if hasattr(s2, "all") else []
                if not got and s2.wait(timeout=0.06):
                    got = [s2]
                for el in got:
                    oid = id(el)
                    if oid in seen_el:
                        continue
                    seen_el.add(oid)
                    raw.append((el, rid))
            except Exception:
                continue

    if not raw:
        try:
            _append_from_selector(
                d(resourceIdMatches=f".*:id/{ROW_SEARCH_USER_USERNAME_RES_NAME}"),
                "",
            )
        except Exception:
            pass

    return raw


def find_first_row_search_username_hot(d: u2.Device, username: str):
    """
    First exact-normalized match on resource-id row_search_user_username only.
    No avatar/parent/XPath/XML/ranking.
    """
    target = _normalize_handle(username)
    for el, _rid_hint in _collect_raw_row_search_elements(d):
        try:
            txt = el.get_text() or ""
            if _normalize_handle(txt) == target:
                return el
        except Exception:
            continue
    return None


def find_username_elements_by_resource_id(
    d: u2.Device, username: str
) -> list[tuple[object, str]]:
    """
    Strong signal: TextViews with id row_search_user_username matching exact handle.
    Returns [(element, matched_resource_id), ...].
    """
    target = _normalize_handle(username)
    seen: set[int] = set()
    matches: list[tuple[object, str]] = []
    raw = _collect_raw_row_search_elements(d)

    for el, rid_hint in raw:
        try:
            oid = id(el)
            if oid in seen:
                continue
            txt = el.get_text() or ""
            if _normalize_handle(txt) != target:
                continue
            try:
                info = el.info
                rid_full = (
                    info.get("resourceName")
                    or info.get("resourceId")
                    or rid_hint
                    or f":id/{ROW_SEARCH_USER_USERNAME_RES_NAME}"
                )
            except Exception:
                rid_full = rid_hint or f":id/{ROW_SEARCH_USER_USERNAME_RES_NAME}"
            seen.add(oid)
            matches.append((el, str(rid_full)))
        except Exception:
            continue

    return matches


def _normalize_handle(username: str) -> str:
    return username.strip().lstrip("@").lower()


def _account_row_search_texts(username: str) -> list[str]:
    """
    Priority: plain handle variants first, then @handle variants (exact UiSelector text).
    """
    u = username.strip().lstrip("@")
    if not u:
        return []
    cap = u[0].upper() + u[1:].lower() if len(u) > 1 else u.upper()
    plain: list[str] = []
    at_suffix: list[str] = []
    sp: set[str] = set()
    sa: set[str] = set()

    def add_plain(s: str) -> None:
        if s and s not in sp:
            sp.add(s)
            plain.append(s)

    def add_at(s: str) -> None:
        if s and s not in sa:
            sa.add(s)
            at_suffix.append(s)

    add_plain(u)
    add_plain(u.lower())
    add_plain(cap)
    add_at(f"@{u}")
    add_at(f"@{u.lower()}")
    add_at(f"@{cap}")
    return plain + at_suffix


def _text_matches_handle(visible: str, username: str) -> bool:
    return _normalize_handle(visible) == _normalize_handle(username)


def _ensure_debug_dirs() -> None:
    _SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    _XML_DIR.mkdir(parents=True, exist_ok=True)


def _estimate_tab_strip_bottom_y(d: u2.Device, height: int) -> int:
    """Lower edge of horizontal search category chips (lightweight text lookup)."""
    labels = (
        "For you",
        "Pour toi",
        "Accounts",
        "Account",
        "Comptes",
        "Posts",
        "People",
        "Personnes",
        "Profils",
    )
    max_bottom = 0
    for lab in labels:
        try:
            o = d(className="android.widget.TextView", text=lab)
            if not o.wait(timeout=0.04):
                continue
            b = o.info["bounds"]
            if b["top"] > height * 0.38:
                continue
            max_bottom = max(max_bottom, b["bottom"])
        except Exception:
            continue
    return max_bottom + 10 if max_bottom > 0 else int(height * 0.14)


def _find_posts_section_top_y(d: u2.Device, tab_strip_bottom: int, height: int) -> int | None:
    try:
        o = d(className="android.widget.TextView", text="Posts")
        if not o.wait(timeout=0.12):
            return None
        top = int(o.info["bounds"]["top"])
        if top > tab_strip_bottom + 24:
            return top
    except Exception:
        pass
    return None


def _serp_y_band(d: u2.Device) -> tuple[int, int | None, int]:
    """Returns (tab_strip_bottom, posts_section_top_or_none, screen_height)."""
    _w, h = d.window_size()
    tab_b = _estimate_tab_strip_bottom_y(d, h)
    posts_t = _find_posts_section_top_y(d, tab_b, h)
    return tab_b, posts_t, h


def _cy_in_accounts_results_band(cy: int, tab_bottom: int, posts_top: int | None, height: int) -> bool:
    """Exclude search chrome and Posts/Reels block when possible."""
    if cy <= tab_bottom:
        return False
    margin = 6
    if posts_top is not None and cy >= posts_top - margin:
        return False
    # Ignore extreme bottom (often media / suggestions)
    if cy >= int(height * 0.92):
        return False
    return True


def _cy_in_mixed_results_band(
    cy: int, posts_top: int | None, height: int, search_bottom: int, width: int
) -> bool:
    """No Accounts-tab strip: allow results below search chrome; still skip Posts/media band."""
    if cy <= search_bottom + _scale_px(2, width):
        return False
    margin = 6
    if posts_top is not None and cy >= posts_top - margin:
        return False
    if cy >= int(height * 0.92):
        return False
    return True


def _iter_textviews_for_handle(d: u2.Device, username: str):
    """Every visible TextView whose text normalizes to the target handle (all on-screen matches)."""
    target = _normalize_handle(username)
    seen: set[int] = set()
    try:
        sel = d.xpath("//android.widget.TextView")
        nodes = sel.all() if hasattr(sel, "all") else list(sel)
    except Exception:
        return
    for el in nodes[:280]:
        try:
            oid = id(el)
            if oid in seen:
                continue
            txt = el.get_text() or ""
            if _normalize_handle(txt) != target:
                continue
            seen.add(oid)
            yield el
        except Exception:
            continue


def _scale_px(px_at_ref: int, width: int) -> int:
    return max(1, int(px_at_ref * width / config.REFERENCE_SCREEN_WIDTH))


def _overlap_y(a0: int, a1: int, b0: int, b1: int) -> bool:
    return not (a1 < b0 or b1 < a0)


def _find_recent_header_bottom_y(d: u2.Device, height: int) -> int | None:
    for label in ("Recent", "Récents", "Recent searches"):
        o = d(className="android.widget.TextView", text=label)
        if o.wait(timeout=0.06):
            try:
                return int(o.info["bounds"]["bottom"])
            except Exception:
                continue
    return None


def _chrome_bottom_y(d: u2.Device, height: int, tab_strip_bottom: int) -> int:
    bottom = tab_strip_bottom
    try:
        ed = d(className="android.widget.EditText")
        if ed.wait(timeout=0.08):
            b = ed.info["bounds"]
            bottom = max(bottom, int(b["bottom"]))
    except Exception:
        pass
    return bottom


def _row_avatar_vertical_overlap_fraction(row_top: int, row_bottom: int, av_top: int, av_bottom: int) -> float:
    ov = min(row_bottom, av_bottom) - max(row_top, av_top)
    if ov <= 0:
        return 0.0
    row_h = max(1, row_bottom - row_top)
    return ov / row_h


def _find_best_avatar_for_row(
    d: u2.Device, text_bounds: dict, width: int, height: int
) -> tuple[bool, int]:
    """
    Relaxed avatar: ImageView left of username, ≥30% vertical overlap with row,
    min side ≥ 28dp scaled, gap (username_left - avatar_right) in [0, max_gap_scaled].
    Returns (found, best_area_px2).
    """
    tl = int(text_bounds["left"])
    tt = int(text_bounds["top"])
    tb_b = int(text_bounds["bottom"])
    row_h = max(12, tb_b - tt)
    min_side = _scale_px(28, width)
    max_gap = _scale_px(config.AVATAR_TO_USERNAME_MAX_GAP_PX, width)
    edge_slop = _scale_px(12, width)

    best_area = 0
    try:
        sel = d.xpath("//android.widget.ImageView")
        nodes = sel.all() if hasattr(sel, "all") else list(sel)
    except Exception:
        return False, 0

    for el in nodes[:120]:
        try:
            b = el.info["bounds"]
            al, at, ar, ab = int(b["left"]), int(b["top"]), int(b["right"]), int(b["bottom"])
            iw, ih = ar - al, ab - at
            if min(iw, ih) < min_side:
                continue
            frac = _row_avatar_vertical_overlap_fraction(tt, tb_b, at, ab)
            if frac < 0.30:
                continue
            if ar > tl + edge_slop:
                continue
            gap = tl - ar
            if gap < -4 or gap > max_gap:
                continue
            area = iw * ih
            if area > best_area:
                best_area = area
        except Exception:
            continue

    return (best_area > 0), best_area


def _remove_button_same_row(d: u2.Device, text_bounds: dict, width: int, height: int) -> bool:
    tt = int(text_bounds["top"])
    tb = int(text_bounds["bottom"])
    y0, y1 = tt - 8, tb + 8
    x_edge = int(width * 0.70)
    for desc in ("Remove", "Clear", "Supprimer", "Effacer", "Close"):
        o = d(descriptionContains=desc)
        if not o.wait(timeout=0.02):
            continue
        try:
            b = o.info["bounds"]
            if b["left"] < x_edge - 60:
                continue
            if _overlap_y(y0, y1, int(b["top"]), int(b["bottom"])):
                return True
        except Exception:
            continue
    # Small clear icon on the far right (content-desc often empty; catch "X" text)
    try:
        x = d(text="×")
        if x.wait(timeout=0.02):
            b = x.info["bounds"]
            if b["left"] >= x_edge and _overlap_y(y0, y1, b["top"], b["bottom"]):
                return True
    except Exception:
        pass
    return False


def _candidate_payload(
    *,
    tb: dict,
    center_y: int,
    has_avatar: bool,
    has_remove_button: bool,
    reason: str | None,
    username: str,
    avatar_area: int = 0,
) -> dict:
    return {
        "username": username,
        "bounds": tb,
        "left": tb["left"],
        "top": tb["top"],
        "center_y": center_y,
        "has_avatar": has_avatar,
        "has_remove_button": has_remove_button,
        "avatar_area": avatar_area,
        "reason": reason,
    }


def evaluate_account_row_candidate(
    d: u2.Device, text_el, username: str, *, mixed_results: bool = False
) -> dict:
    """
    Full candidate evaluation for logging + ranking.
    Rows with avatar are never rejected as too_close_search; top suggestion is no-avatar-only.
    """
    w, h = d.window_size()
    target = _normalize_handle(username)
    try:
        txt = text_el.get_text()
        tb = {k: int(text_el.info["bounds"][k]) for k in ("left", "top", "right", "bottom")}
    except Exception:
        return {
            "accept": False,
            "reason": "bad_element",
            "el": text_el,
            "bounds": {},
            "center_y": 0,
            "has_avatar": False,
            "has_remove_button": False,
            "avatar_area": 0,
        }

    cy = (tb["top"] + tb["bottom"]) // 2
    if _normalize_handle(txt) != target:
        return {
            "accept": False,
            "reason": "text_mismatch",
            "el": text_el,
            "bounds": tb,
            "center_y": cy,
            "has_avatar": False,
            "has_remove_button": False,
            "avatar_area": 0,
        }

    tab_b, posts_t, hh = _serp_y_band(d)
    search_bottom = _chrome_bottom_y(d, hh, tab_b)

    if mixed_results:
        if not _cy_in_mixed_results_band(cy, posts_t, hh, search_bottom, w):
            return {
                "accept": False,
                "reason": "y_band",
                "el": text_el,
                "bounds": tb,
                "center_y": cy,
                "has_avatar": False,
                "has_remove_button": False,
                "avatar_area": 0,
            }
    else:
        if not _cy_in_accounts_results_band(cy, tab_b, posts_t, hh):
            return {
                "accept": False,
                "reason": "y_band",
                "el": text_el,
                "bounds": tb,
                "center_y": cy,
                "has_avatar": False,
                "has_remove_button": False,
                "avatar_area": 0,
            }

    lx_min = _scale_px(config.ACCOUNT_ROW_USERNAME_LEFT_X_MIN, w)
    lx_max = _scale_px(config.ACCOUNT_ROW_USERNAME_LEFT_X_MAX, w)
    if tb["left"] < lx_min or tb["left"] > lx_max:
        return {
            "accept": False,
            "reason": "layout_username_x",
            "el": text_el,
            "bounds": tb,
            "center_y": cy,
            "has_avatar": False,
            "has_remove_button": False,
            "avatar_area": 0,
        }

    has_av, av_area = _find_best_avatar_for_row(d, tb, w, hh)
    has_remove = _remove_button_same_row(d, tb, w, hh)

    suggest_cutoff = search_bottom + _scale_px(config.SUGGESTION_ZONE_PADDING_PX, w)
    in_top_suggestion_zone = tb["top"] < suggest_cutoff

    if not has_av and in_top_suggestion_zone:
        return {
            "accept": False,
            "reason": "top_suggestion_row",
            "el": text_el,
            "bounds": tb,
            "center_y": cy,
            "has_avatar": False,
            "has_remove_button": has_remove,
            "avatar_area": 0,
        }

    recent_bot = _find_recent_header_bottom_y(d, hh)
    if recent_bot is not None:
        depth = _scale_px(config.RECENT_BLOCK_DEPTH, hh)
        if recent_bot <= cy <= recent_bot + depth and not has_av:
            return {
                "accept": False,
                "reason": "recent_query",
                "el": text_el,
                "bounds": tb,
                "center_y": cy,
                "has_avatar": False,
                "has_remove_button": has_remove,
                "avatar_area": 0,
            }

    if has_remove:
        return {
            "accept": False,
            "reason": "remove_button",
            "el": text_el,
            "bounds": tb,
            "center_y": cy,
            "has_avatar": has_av,
            "has_remove_button": True,
            "avatar_area": av_area,
        }

    if not has_av:
        return {
            "accept": False,
            "reason": "no_avatar",
            "el": text_el,
            "bounds": tb,
            "center_y": cy,
            "has_avatar": False,
            "has_remove_button": False,
            "avatar_area": 0,
        }

    return {
        "accept": True,
        "reason": None,
        "el": text_el,
        "bounds": tb,
        "center_y": cy,
        "has_avatar": True,
        "has_remove_button": False,
        "avatar_area": av_area,
    }


def evaluate_row_search_username_element(
    d: u2.Device, text_el, username: str, *, mixed_results: bool
) -> dict:
    """
    row_search_user_username resource id: account SERP row; accept without avatar.
    No top_suggestion_row / layout_username_x / recent-without-avatar rejections.
    """
    w, h = d.window_size()
    target = _normalize_handle(username)
    try:
        txt = text_el.get_text()
        tb = {k: int(text_el.info["bounds"][k]) for k in ("left", "top", "right", "bottom")}
    except Exception:
        return {
            "accept": False,
            "reason": "bad_element",
            "el": text_el,
            "bounds": {},
            "center_y": 0,
            "has_avatar": False,
            "has_remove_button": False,
            "avatar_area": 0,
            "from_resource_id": True,
        }

    cy = (tb["top"] + tb["bottom"]) // 2
    if _normalize_handle(txt) != target:
        return {
            "accept": False,
            "reason": "text_mismatch",
            "el": text_el,
            "bounds": tb,
            "center_y": cy,
            "has_avatar": False,
            "has_remove_button": False,
            "avatar_area": 0,
            "from_resource_id": True,
        }

    tab_b, posts_t, hh = _serp_y_band(d)
    search_bottom = _chrome_bottom_y(d, hh, tab_b)

    if mixed_results:
        if not _cy_in_mixed_results_band(cy, posts_t, hh, search_bottom, w):
            return {
                "accept": False,
                "reason": "y_band",
                "el": text_el,
                "bounds": tb,
                "center_y": cy,
                "has_avatar": False,
                "has_remove_button": False,
                "avatar_area": 0,
                "from_resource_id": True,
            }
    else:
        if not _cy_in_accounts_results_band(cy, tab_b, posts_t, hh):
            return {
                "accept": False,
                "reason": "y_band",
                "el": text_el,
                "bounds": tb,
                "center_y": cy,
                "has_avatar": False,
                "has_remove_button": False,
                "avatar_area": 0,
                "from_resource_id": True,
            }

    has_av, av_area = _find_best_avatar_for_row(d, tb, w, hh)
    has_remove = _remove_button_same_row(d, tb, w, hh)

    if has_remove:
        return {
            "accept": False,
            "reason": "remove_button",
            "el": text_el,
            "bounds": tb,
            "center_y": cy,
            "has_avatar": has_av,
            "has_remove_button": True,
            "avatar_area": av_area,
            "from_resource_id": True,
        }

    return {
        "accept": True,
        "reason": None,
        "el": text_el,
        "bounds": tb,
        "center_y": cy,
        "has_avatar": has_av,
        "has_remove_button": False,
        "avatar_area": av_area,
        "from_resource_id": True,
    }


def is_real_account_row(d: u2.Device, text_el, username: str) -> tuple[bool, str | None]:
    mixed = get_search_ui_mode() == "mixed_results"
    ev = evaluate_account_row_candidate(d, text_el, username, mixed_results=mixed)
    return ev["accept"], ev["reason"]


def _dump_no_real_account_row_debug(d: u2.Device, username: str) -> None:
    _ensure_debug_dirs()
    shot = _SCREENSHOTS_DIR / "no_real_account_row.png"
    xml_path = _XML_DIR / "no_real_account_row.xml"
    try:
        screenshot(d, str(shot))
    except Exception as e:
        log("warning", "no_real_account_row_screenshot_failed", error=str(e), username=username)
    try:
        try:
            hier = d.dump_hierarchy(compressed=False)
        except TypeError:
            hier = d.dump_hierarchy()
        xml_path.write_text(hier, encoding="utf-8")
        _bump_xml_fetch()
        log("info", "no_real_account_row_hierarchy_dumped", path=str(xml_path), username=username)
    except Exception as e:
        log("warning", "no_real_account_row_xml_failed", error=str(e), username=username)


def _iter_exact_text_match_elements(d: u2.Device, disp: str):
    """Yield all TextViews with exact text=disp (handles duplicates in list)."""
    sel = d(className="android.widget.TextView", text=disp)
    if hasattr(sel, "all"):
        try:
            found = sel.all()
            if found:
                for o in found:
                    yield o
                return
        except Exception:
            pass
    if sel.wait(timeout=0.05):
        yield sel


def find_real_account_text_element(d: u2.Device, username: str, *, dump_on_failure: bool = True):
    """
    Resource-id row_search_user_username first (GramAddict/Propulse style), then XPath TextViews.
    Ranking: resource-id rows, then has_avatar, then smallest center_y, then largest avatar.
    """
    mixed = get_search_ui_mode() == "mixed_results"
    accepted: list[dict] = []
    weak_rows: list[tuple[int, dict, object, str | None]] = []

    for el, rid in find_username_elements_by_resource_id(d, username):
        try:
            txt = el.get_text()
            tb = {k: int(el.info["bounds"][k]) for k in ("left", "top", "right", "bottom")}
            cy = (tb["top"] + tb["bottom"]) // 2
        except Exception:
            continue

        log(
            "info",
            "candidate_account_row_seen_resource_id",
            username=username,
            resource_id=rid,
            text=txt,
            bounds=tb,
            left=tb["left"],
            top=tb["top"],
            center_y=cy,
        )

        ev = evaluate_row_search_username_element(d, el, username, mixed_results=mixed)
        pl = _candidate_payload(
            tb=ev["bounds"],
            center_y=ev["center_y"],
            has_avatar=ev["has_avatar"],
            has_remove_button=ev["has_remove_button"],
            reason=ev["reason"] or "accepted",
            username=username,
            avatar_area=ev["avatar_area"],
        )
        pl["resource_id"] = rid
        pl["from_resource_id"] = True

        if ev["accept"]:
            ev["resource_id"] = rid
            if mixed:
                log("info", "real_account_row_accepted_resource_id", **pl)
                log(
                    "info",
                    "real_account_result_found",
                    username=username,
                    selected_y=ev["center_y"],
                    left=ev["bounds"]["left"],
                    candidates=1,
                    avatar_area=ev["avatar_area"],
                    search_ui_mode=get_search_ui_mode(),
                    via_resource_id=True,
                    resource_id=rid,
                    fast_path="mixed_resource_id_first",
                )
                return el
            accepted.append(ev)
            log("info", "real_account_row_accepted_resource_id", **pl)
        else:
            log("warning", "candidate_account_row_rejected", **pl)

    if accepted:
        accepted.sort(
            key=lambda e: (
                -int(e.get("from_resource_id", False)),
                -int(e["has_avatar"]),
                e["center_y"],
                -e["avatar_area"],
            )
        )
        best = accepted[0]
        log(
            "info",
            "real_account_result_found",
            username=username,
            selected_y=best["center_y"],
            left=best["bounds"]["left"],
            candidates=len(accepted),
            avatar_area=best["avatar_area"],
            search_ui_mode=get_search_ui_mode(),
            via_resource_id=best.get("from_resource_id", False),
            resource_id=best.get("resource_id"),
        )
        return best["el"]

    for o in _iter_textviews_for_handle(d, username):
        try:
            txt = o.get_text()
            tb = {k: int(o.info["bounds"][k]) for k in ("left", "top", "right", "bottom")}
            cy = (tb["top"] + tb["bottom"]) // 2
        except Exception:
            continue

        log(
            "info",
            "candidate_account_row_seen",
            **_candidate_payload(
                tb=tb,
                center_y=cy,
                has_avatar=False,
                has_remove_button=False,
                reason="evaluating",
                username=username,
            ),
        )

        ev = evaluate_account_row_candidate(d, o, username, mixed_results=mixed)
        ev["from_resource_id"] = False
        pl = _candidate_payload(
            tb=ev["bounds"],
            center_y=ev["center_y"],
            has_avatar=ev["has_avatar"],
            has_remove_button=ev["has_remove_button"],
            reason=ev["reason"] or "accepted",
            username=username,
            avatar_area=ev["avatar_area"],
        )

        if ev["accept"]:
            accepted.append(ev)
            log("info", "real_account_row_accepted", **pl)
        else:
            log("warning", "candidate_account_row_rejected", **pl)
            if ev["reason"] == "top_suggestion_row":
                log("info", "suggestion_row_rejected", **pl)

        if config.ALLOW_TEXT_ONLY_RECENT_RESULT and not ev["accept"] and ev["reason"] in (
            "no_avatar",
            "recent_query",
            "layout_username_x",
        ):
            weak_rows.append((ev["center_y"], ev["bounds"], ev["el"], ev["reason"]))

    if accepted:
        accepted.sort(
            key=lambda e: (
                -int(e.get("from_resource_id", False)),
                -int(e["has_avatar"]),
                e["center_y"],
                -e["avatar_area"],
            )
        )
        best = accepted[0]
        log(
            "info",
            "real_account_result_found",
            username=username,
            selected_y=best["center_y"],
            left=best["bounds"]["left"],
            candidates=len(accepted),
            avatar_area=best["avatar_area"],
            search_ui_mode=get_search_ui_mode(),
            via_resource_id=best.get("from_resource_id", False),
            resource_id=best.get("resource_id"),
        )
        return best["el"]

    if weak_rows:
        weak_rows.sort(key=lambda t: t[0], reverse=True)
        cy, tb, el, _r = weak_rows[0]
        log(
            "warning",
            "using_text_only_recent_fallback",
            username=username,
            selected_y=cy,
            allowed_by_config=True,
        )
        return el

    if dump_on_failure:
        _dump_no_real_account_row_debug(d, username)
    log("error", "no_real_account_row", username=username)
    return None


def wrong_surface_after_tap(d: u2.Device) -> str | None:
    """If still on search SERP or For You–style grid after a bad row tap."""
    w, h = d.window_size()
    try:
        ed = d(className="android.widget.EditText")
        if ed.wait(timeout=0.12):
            b = ed.info["bounds"]
            if int(b["bottom"]) <= int(h * 0.26):
                return "search_results"
    except Exception:
        pass

    for label in ("For you", "Pour toi"):
        o = d(text=label)
        if not o.wait(timeout=0.08):
            continue
        try:
            br = o.info["bounds"]
            if int(br["bottom"]) >= int(h * 0.22):
                continue
            ed2 = d(className="android.widget.EditText")
            has_top_search = ed2.wait(timeout=0.06) and int(ed2.info["bounds"]["bottom"]) <= int(h * 0.26)
            rv = d(className="androidx.recyclerview.widget.RecyclerView")
            has_rv = rv.wait(timeout=0.1)
            if has_rv and not has_top_search:
                return "for_you_grid"
        except Exception:
            continue
    return None


def _collect_top_visible_text_labels(d: u2.Device, *, max_y_ratio: float = 0.42, limit: int = 60) -> list[dict]:
    """Best-effort TextView texts in the upper window (for debugging)."""
    _w, h = d.window_size()
    y_max = int(h * max_y_ratio)
    rows: list[dict] = []
    nodes = []
    try:
        sel = d.xpath("//android.widget.TextView")
        if hasattr(sel, "all"):
            nodes = sel.all()
        else:
            nodes = list(sel)
    except Exception:
        try:
            sel2 = d(className="android.widget.TextView")
            nodes = sel2.all() if hasattr(sel2, "all") else []
        except Exception:
            return rows
    for el in nodes[:120]:
        try:
            b = el.info["bounds"]
            cy = (b["top"] + b["bottom"]) // 2
            if cy > y_max:
                continue
            txt = el.get_text() or ""
            if not txt.strip():
                continue
            rows.append({"text": txt[:120], "y": cy, "top": b["top"]})
        except Exception:
            continue
    rows.sort(key=lambda r: (r["top"], r["text"]))
    return rows[:limit]


def _dump_search_edittext_debug(d: u2.Device) -> None:
    _ensure_debug_dirs()
    shot = _SCREENSHOTS_DIR / "search_edittext_not_found.png"
    xml_path = _XML_DIR / "search_edittext_not_found.xml"
    try:
        screenshot(d, str(shot))
    except Exception as e:
        log("warning", "search_edittext_screenshot_failed", error=str(e))
    try:
        try:
            hier = d.dump_hierarchy(compressed=False)
        except TypeError:
            hier = d.dump_hierarchy()
        xml_path.write_text(hier, encoding="utf-8")
        _bump_xml_fetch()
        log("info", "search_edittext_hierarchy_dumped", path=str(xml_path))
    except Exception as e:
        log("warning", "search_edittext_xml_dump_failed", error=str(e))
    labels = _collect_top_visible_text_labels(d)
    log("warning", "search_edittext_top_text_labels", count=len(labels), labels=labels)


def _tap_search_bar_fallback(d: u2.Device) -> None:
    """Tap near top-center (IG search field) when EditText is not discovered."""
    w, h = d.window_size()
    x, y = int(w * 0.45), int(h * 0.08)
    d.click(x, y)
    log("info", "search_bar_fallback_tap", x=x, y=y, x_ratio=0.45, y_ratio=0.08)
    time.sleep(0.18)


def _dump_accounts_tab_debug(d: u2.Device) -> None:
    _ensure_debug_dirs()
    shot = _SCREENSHOTS_DIR / "accounts_tab_not_found.png"
    xml_path = _XML_DIR / "accounts_tab_not_found.xml"
    try:
        screenshot(d, str(shot))
    except Exception as e:
        log("warning", "accounts_tab_screenshot_failed", error=str(e))
    try:
        try:
            hier = d.dump_hierarchy(compressed=False)
        except TypeError:
            hier = d.dump_hierarchy()
        xml_path.write_text(hier, encoding="utf-8")
        _bump_xml_fetch()
        log("info", "accounts_tab_hierarchy_dumped", path=str(xml_path))
    except Exception as e:
        log("warning", "accounts_tab_xml_dump_failed", error=str(e))
    labels = _collect_top_visible_text_labels(d)
    log("warning", "accounts_tab_top_text_labels", count=len(labels), labels=labels)


def _try_click_tab_chip(
    d: u2.Device, selector, height: int, name: str, *, wait_timeout: float = 0.12
) -> bool:
    try:
        if not selector.wait(timeout=wait_timeout):
            return False
        b = selector.info["bounds"]
        cy = (b["top"] + b["bottom"]) // 2
        if cy > int(height * _TAB_CHIP_MAX_Y_RATIO):
            return False
        selector.click()
        log("info", "accounts_tab_opened", strategy=name, y=cy)
        time.sleep(0.12)
        return True
    except Exception:
        return False


def _accounts_tab_selectors_for_label(d: u2.Device, label: str) -> list[tuple[str, object]]:
    """Pairs (strategy_name, selector object)."""
    return [
        (f"text={label}", d(className="android.widget.TextView", text=label)),
        (f"textContains={label}", d(className="android.widget.TextView", textContains=label)),
        (f"desc={label}", d(description=label)),
        (f"descContains={label}", d(descriptionContains=label)),
    ]


def open_accounts_tab(d: u2.Device) -> bool:
    """
    Select the Accounts / People / … chip if present (short budget — then mixed-results mode).
    """
    deadline = time.monotonic() + config.ACCOUNTS_TAB_TRY_MAX_S
    _w, h = d.window_size()
    chip_wait = min(0.08, max(0.04, config.ACCOUNTS_TAB_TRY_MAX_S / 20))

    for label in _ACCOUNTS_TAB_LABELS:
        if time.monotonic() >= deadline:
            break
        for strat, sel in _accounts_tab_selectors_for_label(d, label):
            if time.monotonic() >= deadline:
                break
            if _try_click_tab_chip(d, sel, h, strat, wait_timeout=chip_wait):
                return True

    log(
        "warning",
        "accounts_tab_not_found",
        budget_s=config.ACCOUNTS_TAB_TRY_MAX_S,
        message="Switching to mixed_results search UI mode.",
    )
    if getattr(config, "ACCOUNTS_TAB_DEBUG_DUMP", False):
        _dump_accounts_tab_debug(d)
    return False


def open_search(d: u2.Device) -> bool:
    """Open bottom-nav Search: resource-id first, short settle, exit as soon as EditText exists."""
    global _perf
    settle = min(float(getattr(config, "OPEN_SEARCH_SETTLE_S", 0.12)), 0.12)
    pkg = config.INSTAGRAM_PACKAGE

    if should_reuse_search_surface(d, pkg):
        if apply_search_surface_reuse_metrics(d, pkg, "cache_ttl"):
            log("info", "search_surface_cache_hit", message="TTL cache + EditText ok")
            return True

    if is_lightweight_search_screen(d, pkg):
        if apply_search_surface_reuse_metrics(d, pkg, "lightweight_signals"):
            return True

    _perf["search_surface_reused"] = False
    _perf["search_open_skipped_ms"] = 0.0
    t_click_phase = time.perf_counter()
    clicked = False
    click_name: str | None = None

    for suffix in _SEARCH_TAB_RID_SUFFIXES:
        try:
            sel = d(resourceIdMatches=f".*:id/{suffix}")
            if sel.wait(timeout=0.06):
                sel.click()
                clicked = True
                click_name = f"rid_matches_{suffix}"
                log("info", "open_search_clicked", selector=click_name)
                break
        except Exception as e:
            log("debug", "open_search_rid_failed", suffix=suffix, error=str(e))

    if not clicked:
        for ipkg in _instagram_package_candidates(d):
            for suffix in _SEARCH_TAB_RID_SUFFIXES:
                rid = f"{ipkg}:id/{suffix}"
                try:
                    s = d(resourceId=rid)
                    if s.wait(timeout=0.05):
                        s.click()
                        clicked = True
                        click_name = rid
                        log("info", "open_search_clicked", selector=click_name)
                        break
                except Exception:
                    continue
            if clicked:
                break

    if not clicked:
        selectors: list[tuple[str, Callable[[], object]]] = [
            ("desc_en", lambda: d(descriptionContains="Search")),
            ("desc_fr", lambda: d(descriptionContains="Recherche")),
            ("desc_explore", lambda: d(descriptionContains="Search and explore")),
            ("desc_explorer", lambda: d(descriptionContains="Explorer")),
        ]
        for name, factory in selectors:
            try:
                o = factory()
                if o.wait(timeout=0.05):
                    o.click()
                    clicked = True
                    click_name = name
                    log("info", "open_search_clicked", selector=name)
                    break
            except Exception as e:
                log("debug", "open_search_attempt_failed", selector=name, error=str(e))

    if not clicked:
        w, h = d.window_size()
        d.click(int(w * 0.72), int(h * 0.94))
        click_name = "percent_fallback"
        _perf["recovery_used"] = True
        log("warning", "open_search_fallback_tap", x_ratio=0.72, y_ratio=0.94)

    search_click_ms = (time.perf_counter() - t_click_phase) * 1000
    time.sleep(settle)

    t_wait = time.perf_counter()
    ed = retry_until(
        lambda: _wait_search_edittext(d),
        timeout_s=config.OPEN_SEARCH_MAX_WAIT_S,
        poll_s=config.UI_FAST_POLL_S,
        desc="open_search_edittext",
    )
    search_field_ready_ms = (time.perf_counter() - t_wait) * 1000
    ok = ed is not None
    if not ok:
        ok = _wait_search_edittext(d) is not None

    _perf["search_click_ms"] = search_click_ms
    _perf["search_field_ready_ms"] = search_field_ready_ms
    log(
        "info",
        "open_search_timing",
        search_click_ms=round(search_click_ms, 2),
        search_field_ready_ms=round(search_field_ready_ms, 2),
        selector=click_name,
        ok=ok,
    )
    if not ok:
        invalidate_search_surface_cache("open_search_no_edittext")
    return ok


def _wait_search_edittext(d: u2.Device):
    ed = d(className="android.widget.EditText")
    if ed.wait(timeout=0.08):
        return ed
    return None


def _normalize_search_field_visible_text(s: str | None) -> str:
    return " ".join((s or "").split()).strip().lower()


# Instagram search bar shows hint/placeholder as get_text() on some builds — treat as empty.
_SEARCH_PLACEHOLDER_NORMALIZED: frozenset[str] = frozenset(
    _normalize_search_field_visible_text(x)
    for x in (
        "Search",
        "Search with Meta AI",
        "Recherche",
        "Rechercher",
        "Search or type URL",
    )
)


def _is_search_placeholder(text: str | None) -> bool:
    if text is None:
        return True
    key = _normalize_search_field_visible_text(text)
    if not key:
        return True
    return key in _SEARCH_PLACEHOLDER_NORMALIZED


def _search_edittext_text_strip(ed) -> str:
    try:
        return (ed.get_text() or "").strip()
    except Exception:
        return ""


def _field_still_matches_previous_query(cur: str, previous_username: str | None) -> bool:
    if not previous_username or not cur:
        return False
    if _is_search_placeholder(cur):
        return False
    prev = _normalize_handle(previous_username)
    cur_n = _normalize_handle(cur)
    if not prev:
        return False
    return prev == cur_n or cur_n.startswith(prev) or prev in cur_n


def _search_field_clear_snapshot_ok(txt: str, previous_username: str | None) -> bool:
    """True when field is empty, placeholder hint, or otherwise safe to type a new query."""
    if not (txt or "").strip():
        return True
    if _is_search_placeholder(txt):
        return True
    if previous_username and _field_still_matches_previous_query(txt, previous_username):
        return False
    return False


def _log_search_field_clear_success(
    txt: str,
    methods: list[str],
    *,
    extra_method_suffix: str = "",
) -> str:
    """Log success path; returns method string for return tuple."""
    method = "+".join(methods) if methods else "clear_text"
    if extra_method_suffix:
        method = f"{method}+{extra_method_suffix}" if method else extra_method_suffix
    if _is_search_placeholder(txt):
        log(
            "info",
            "search_field_placeholder_detected",
            placeholder_preview=(txt or "")[:120],
        )
        log("info", "search_field_effectively_empty", state="placeholder")
    else:
        log("info", "search_field_effectively_empty", state="empty")
    log("info", "search_field_clear_success", search_field_clear_method=method)
    return method


def clear_search_field_robust(
    d: u2.Device,
    serial: str | None,
    *,
    ed: object | None = None,
    previous_username: str | None = None,
    intended_username: str = "",
) -> tuple[bool, str]:
    """
    Clear Instagram search EditText before typing (multi-target safe). No XML.
    Returns (success, method_summary).
    """
    log(
        "info",
        "search_field_clear_started",
        intended_username=intended_username,
        has_previous=bool(previous_username),
    )
    if ed is None:
        ed = _wait_search_edittext(d)
    if ed is None:
        log("error", "search_field_clear_failed", reason="no_edittext", search_field_clear_method="none")
        return False, "none"

    try:
        ed.click()
        time.sleep(0.05)
    except Exception:
        pass

    methods: list[str] = []

    def _refresh_ed():
        nonlocal ed
        try:
            nxt = _wait_search_edittext(d)
            if nxt is not None:
                ed = nxt
        except Exception:
            pass

    def _snapshot() -> str:
        return _search_edittext_text_strip(ed)

    # A–D: clear_text + verify (empty and localized placeholders count as clear)
    try:
        ed.clear_text()
        methods.append("clear_text")
    except Exception as e:
        log("debug", "search_field_clear_text_failed", error=str(e))
    time.sleep(0.05)
    txt = _snapshot()
    if _search_field_clear_snapshot_ok(txt, previous_username):
        m = _log_search_field_clear_success(txt, methods)
        return True, m

    try:
        ed.set_text("")
        methods.append("set_text_empty")
    except Exception:
        pass
    time.sleep(0.05)
    txt = _snapshot()
    if _search_field_clear_snapshot_ok(txt, previous_username):
        m = _log_search_field_clear_success(txt, methods)
        return True, m

    # E: KEYCODE_DEL (67) via adb in batches (e.g. stuck real query; hints may reappear as placeholder)
    max_ev = int(getattr(config, "SEARCH_FIELD_CLEAR_MAX_DEL_EVENTS", 120))
    batch = int(getattr(config, "SEARCH_FIELD_CLEAR_DEL_BATCH_SIZE", 12))
    sent = 0
    del_batches = 0
    while sent < max_ev:
        chunk = min(batch, max_ev - sent)
        try:
            keys = " ".join(["67"] * chunk)
            code, _, _ = shell(d, f"input keyevent {keys}")
            if code != 0:
                for _ in range(chunk):
                    shell(d, "input keyevent 67")
        except Exception:
            for _ in range(chunk):
                try:
                    shell(d, "input keyevent 67")
                except Exception:
                    pass
        sent += chunk
        del_batches += 1
        time.sleep(0.04)
        _refresh_ed()
        txt = _snapshot()
        if _search_field_clear_snapshot_ok(txt, previous_username):
            extra = f"keyevent_67_batches_{del_batches}_total_{sent}"
            m = _log_search_field_clear_success(txt, methods, extra_method_suffix=extra)
            return True, f"del_total_{sent}"

    txt = _snapshot()
    if _search_field_clear_snapshot_ok(txt, previous_username):
        m = _log_search_field_clear_success(
            txt,
            methods,
            extra_method_suffix=f"keyevent_67_batches_{del_batches}_edge",
        )
        return True, "edge_empty_after_del"

    still_prev = bool(previous_username) and _field_still_matches_previous_query(
        txt, previous_username
    )
    unknown_typed = bool((txt or "").strip()) and not _is_search_placeholder(txt)
    log(
        "error",
        "search_field_clear_failed",
        remaining_preview=txt[:120],
        search_field_clear_method="+".join(methods + [f"keyevent_67_batches_{del_batches}"]),
        remaining_len=len(txt),
        still_matches_previous=still_prev,
        non_placeholder_unknown=unknown_typed and not still_prev,
    )
    return False, "incomplete"


def _typing_confirmed(d: u2.Device, ed, username: str) -> bool:
    """
    After fast IME broadcast (or any typing): success when search EditText shows the handle
    OR a row_search_user_username element matches the exact username.
    """
    try:
        cur = ed.get_text() or ""
        if username in cur or _normalize_handle(cur) == _normalize_handle(username):
            return True
    except Exception:
        pass
    if find_username_elements_by_resource_id(d, username):
        return True
    return False


def type_search(
    d: u2.Device, username: str, *, previous_username: str | None = None
) -> bool:
    """Robust clear, then FastIME or set_text; fused row detect when FastIME."""
    global _perf, _TYPE_SEARCH_FAILURE_REASON
    _TYPE_SEARCH_FAILURE_REASON = None
    _clear_pending_fused_fast_ime_row()
    t_typ = time.perf_counter()
    ed = retry_until(
        lambda: _wait_search_edittext(d),
        timeout_s=config.SEARCH_FIELD_WAIT_S,
        poll_s=config.UI_FAST_POLL_S,
        desc="search_edittext",
    )
    if ed is None:
        log(
            "warning",
            "search_edittext_missing_try_fallback_tap",
            message="Tapping top search bar area then retrying EditText detection.",
        )
        _perf["recovery_used"] = True
        _tap_search_bar_fallback(d)
        ed = retry_until(
            lambda: _wait_search_edittext(d),
            timeout_s=config.SEARCH_EDITTEXT_RETRY_AFTER_TAP_S,
            poll_s=config.UI_FAST_POLL_S,
            desc="search_edittext_after_fallback_tap",
        )
    if ed is None:
        log("error", "search_edittext_not_found")
        invalidate_search_surface_cache("no_edittext")
        _dump_search_edittext_debug(d)
        _perf["typing_command_ms"] = (time.perf_counter() - t_typ) * 1000
        _perf["typing_confirm_ms"] = 0.0
        return False

    serial = get_device_serial(d)
    t_cmd_start = time.perf_counter()
    ok_clear, clear_method = clear_search_field_robust(
        d,
        serial,
        ed=ed,
        previous_username=previous_username,
        intended_username=username,
    )
    log(
        "info",
        "search_field_clear_method",
        search_field_clear_method=clear_method,
        ok=ok_clear,
    )
    if not ok_clear:
        _TYPE_SEARCH_FAILURE_REASON = "search_field_not_cleared"
        _perf["typing_command_ms"] = (time.perf_counter() - t_typ) * 1000
        _perf["typing_confirm_ms"] = 0.0
        log("error", "type_search_aborted", reason="search_field_not_cleared")
        return False

    ed = _wait_search_edittext(d) or ed
    preview = _search_edittext_text_strip(ed)
    log(
        "info",
        "search_field_before_typing_text",
        text_preview=preview[:100],
        text_len=len(preview),
        username=username,
    )
    if not _search_field_clear_snapshot_ok(preview, previous_username):
        _TYPE_SEARCH_FAILURE_REASON = "search_field_not_cleared"
        _perf["typing_command_ms"] = (time.perf_counter() - t_typ) * 1000
        _perf["typing_confirm_ms"] = 0.0
        log(
            "error",
            "search_field_clear_failed",
            reason="non_empty_before_type",
            preview=preview[:80],
            is_placeholder=_is_search_placeholder(preview),
            still_matches_previous=_field_still_matches_previous_query(
                preview, previous_username
            ),
        )
        return False

    focused = False
    try:
        focused = bool((ed.info or {}).get("focused"))
    except Exception:
        pass
    if not focused:
        try:
            ed.click()
            time.sleep(0.04)
        except Exception:
            pass

    typing_method = "set_text"
    used_fast_path = False
    fast_ime = getattr(config, "FAST_IME", "") or ""
    fast_ime_switch_ok: bool | None = None
    fast_ime_broadcast_ok: bool | None = None

    if fast_ime and is_fast_ime_available(serial):
        ok_cmd, tag, fast_ime_switch_ok, fast_ime_broadcast_ok = run_fast_ime_input(
            serial, username, fast_ime_id=fast_ime
        )
        log(
            "info",
            "fast_ime_typing",
            typing_method="fast_ime",
            fast_ime_switch_ok=fast_ime_switch_ok,
            fast_ime_broadcast_ok=fast_ime_broadcast_ok,
            command_ok=ok_cmd,
        )
        if ok_cmd:
            typing_method = tag
            used_fast_path = True

    if not used_fast_path:
        try:
            ed.set_text(username)
            typing_method = "set_text"
        except Exception:
            typing_method = "send_keys"
            try:
                d.send_keys(username)
            except Exception:
                typing_method = "shell_input"
                safe = username.replace(" ", "%s")
                shell(d, f"input text {safe}")

    if used_fast_path:
        settle = min(
            0.15,
            float(getattr(config, "FAST_IME_POST_BROADCAST_SETTLE_S", 0.15)),
        )
        time.sleep(settle)
        typing_command_ms = (time.perf_counter() - t_cmd_start) * 1000
        _perf["typing_command_ms"] = typing_command_ms
        _perf["typing_confirm_ms"] = 0.0
        _set_pending_fused_fast_ime_row(username)
        st_log: dict = {
            "username": username,
            "typing_method": typing_method,
            "typing_confirm_ms": 0.0,
            "ok": True,
            "fused_row_confirm_deferred": True,
        }
        if fast_ime_switch_ok is not None:
            st_log["fast_ime_switch_ok"] = fast_ime_switch_ok
            st_log["fast_ime_broadcast_ok"] = bool(fast_ime_broadcast_ok)
        log("info", "search_typed", **st_log)
        return True

    typing_command_ms = (time.perf_counter() - t_cmd_start) * 1000
    _perf["typing_command_ms"] = typing_command_ms

    def _confirm_typing_deadline() -> float:
        return time.monotonic() + float(
            getattr(config, "TYPING_CONFIRM_MAX_S", config.TYPE_SEARCH_CONFIRM_S)
        )

    # Non-fastIME: field text or row_search_user_username (exact handle).
    t_confirm_start = time.perf_counter()
    deadline = _confirm_typing_deadline()
    while time.monotonic() < deadline:
        if _typing_confirmed(d, ed, username):
            break
        time.sleep(config.UI_FAST_POLL_S)
    typing_confirm_ms = (time.perf_counter() - t_confirm_start) * 1000

    ok = _typing_confirmed(d, ed, username)

    _perf["typing_confirm_ms"] = typing_confirm_ms
    st_log: dict = {
        "username": username,
        "typing_method": typing_method,
        "typing_confirm_ms": round(typing_confirm_ms, 2),
        "ok": ok,
    }
    if fast_ime_switch_ok is not None:
        st_log["fast_ime_switch_ok"] = fast_ime_switch_ok
        st_log["fast_ime_broadcast_ok"] = bool(fast_ime_broadcast_ok)
    log("info", "search_typed", **st_log)
    if ok:
        _mark_search_surface_ok(d, config.INSTAGRAM_PACKAGE)
    else:
        invalidate_search_surface_cache("type_not_confirmed")
    return ok


def find_exact_account_result(d: u2.Device, username: str):
    """Alias for real-account selection (avatar-aware)."""
    return find_real_account_text_element(d, username)


def _bounds_contain(outer: dict, inner: dict) -> bool:
    return (
        outer["left"] <= inner["left"]
        and outer["right"] >= inner["right"]
        and outer["top"] <= inner["top"]
        and outer["bottom"] >= inner["bottom"]
    )


def _tap_bounds_for_account_row(d: u2.Device, el) -> dict:
    """Username view center, or a larger clickable ancestor row if the view is a thin label."""
    w, _h = d.window_size()
    try:
        b = {k: int(el.info["bounds"][k]) for k in ("left", "top", "right", "bottom")}
    except Exception:
        return {}
    row_h = b["bottom"] - b["top"]
    min_h = _scale_px(48, w)
    if row_h >= min_h:
        return b
    try:
        ax = el.xpath('ancestor::*[@clickable="true"]')
        ancestors = ax.all() if hasattr(ax, "all") else []
        for anc in ancestors[:8]:
            try:
                b2 = {k: int(anc.info["bounds"][k]) for k in ("left", "top", "right", "bottom")}
                if b2["bottom"] - b2["top"] >= max(min_h, int(row_h * 1.12)) and _bounds_contain(
                    b2, b
                ):
                    return b2
            except Exception:
                continue
    except Exception:
        pass
    return b


def _tap_hot_username_center_jitter(d: u2.Device, el) -> tuple[int, int, dict]:
    """Direct tap on username view bounds center ± tiny jitter (no parent traversal)."""
    try:
        b = {k: int(el.info["bounds"][k]) for k in ("left", "top", "right", "bottom")}
    except Exception:
        return 0, 0, {}
    j = 2
    cx = (b["left"] + b["right"]) // 2 + random.randint(-j, j)
    cy = (b["top"] + b["bottom"]) // 2 + random.randint(-j, j)
    w, h = d.window_size()
    cx = max(1, min(w - 1, cx))
    cy = max(1, min(h - 1, cy))
    return cx, cy, b


def _try_dismiss_keyboard_light(d: u2.Device) -> None:
    """Avoid false negatives when IME still up; does not rely on back (navigation)."""
    try:
        ed = d(className="android.widget.EditText")
        if not ed.wait(timeout=0.05):
            return
        info = ed.info
        if not info.get("focused"):
            return
        w, h = d.window_size()
        d.click(w // 2, int(h * 0.38))
        time.sleep(0.08)
    except Exception:
        pass


def _profile_signal_a_header_ids(d: u2.Device) -> str | None:
    try:
        if d(resourceIdMatches=".*:id/profile_header.*").wait(timeout=0.08):
            return "header_resource_id"
    except Exception:
        pass
    try:
        if d(resourceIdMatches=".*profile_header.*").wait(timeout=0.06):
            return "header_resource_id"
    except Exception:
        pass
    try:
        if d(resourceIdMatches=".*:id/action_bar_title").wait(timeout=0.08):
            return "action_bar_title_resource_id"
    except Exception:
        pass
    try:
        if d(resourceIdMatches=".*action_bar_title.*").wait(timeout=0.06):
            return "action_bar_title_resource_id"
    except Exception:
        pass
    return None


def _profile_signal_b_username_top_band(d: u2.Device, username: str) -> str | None:
    """Exact handle TextView in top ~35% — UiSelector only, no XML walk."""
    w, h = d.window_size()
    y_max = int(h * 0.35)
    target = _normalize_handle(username)
    for disp in _account_row_search_texts(username):
        try:
            o = d(className="android.widget.TextView", text=disp)
            if not o.wait(timeout=0.05):
                continue
            b = o.info["bounds"]
            if int(b["top"]) > y_max:
                continue
            if _normalize_handle(o.get_text() or "") != target:
                continue
            return "username_top_band"
        except Exception:
            continue
    return None


def _profile_signal_c_chrome(d: u2.Device, package: str) -> str | None:
    """Instagram foreground + any common profile chrome control."""
    try:
        cur = d.app_current()
        if (cur or {}).get("package", "") != package:
            return None
    except Exception:
        return None

    checks: list[tuple[str, list[Callable[[], object]]]] = [
        (
            "follow_button",
            [
                lambda: d(textContains="Follow"),
                lambda: d(descriptionContains="Follow"),
                lambda: d(text="Following"),
                lambda: d(text="Suivre"),
                lambda: d(descriptionContains="Suivre"),
            ],
        ),
        (
            "message_button",
            [
                lambda: d(textContains="Message"),
                lambda: d(text="Message"),
                lambda: d(descriptionContains="Message"),
            ],
        ),
        (
            "professional_dashboard",
            [
                lambda: d(textContains="Professional"),
                lambda: d(textContains="professional"),
            ],
        ),
        (
            "profile_tabs",
            [
                lambda: d(descriptionContains="Grid"),
                lambda: d(descriptionContains="Reels"),
                lambda: d(text="Posts"),
                lambda: d(resourceIdMatches=".*profile_tab.*"),
            ],
        ),
        (
            "bio_container",
            [
                lambda: d(resourceIdMatches=".*:id/profile_bio.*"),
                lambda: d(resourceIdMatches=".*profile_bio.*"),
                lambda: d(resourceIdMatches=".*:id/user_bio.*"),
            ],
        ),
        (
            "follower_counters",
            [
                lambda: d(textContains="followers"),
                lambda: d(textContains="Followers"),
                lambda: d(textContains="following"),
                lambda: d(textContains="abonnés"),
            ],
        ),
    ]

    for name, factories in checks:
        for fact in factories:
            try:
                el = fact()
                if el.wait(timeout=0.045):
                    return name
            except Exception:
                continue
    return None


def _early_profile_transition_signal(d: u2.Device, username: str) -> str | None:
    """
    First profile-open hints after row tap (tight resource-ids + chrome).
    Used only for transition timing; verify_profile remains authoritative.
    """
    rid_pairs = (
        ("action_bar_title", "com.instagram.android:id/action_bar_title"),
        ("profile_header", "com.instagram.android:id/profile_header"),
        ("profile_tabs_container", "com.instagram.android:id/profile_tabs_container"),
    )
    for name, rid in rid_pairs:
        try:
            o = d(resourceId=rid)
            if o.wait(timeout=0.04):
                return name
        except Exception:
            continue
    try:
        if _profile_signal_b_username_top_band(d, username):
            return "username_top_band"
    except Exception:
        pass
    quick_chrome: list[tuple[str, Callable[[], object]]] = [
        ("message", lambda: d(textContains="Message")),
        ("follow", lambda: d(textContains="Follow")),
        ("following", lambda: d(text="Following")),
        ("suivre", lambda: d(text="Suivre")),
    ]
    for name, fact in quick_chrome:
        try:
            el = fact()
            if el.wait(timeout=0.04):
                return name
        except Exception:
            continue
    return None


def _try_profile_signals_once(d: u2.Device, username: str, package: str) -> str | None:
    s = _profile_signal_a_header_ids(d)
    if s:
        return s
    s = _profile_signal_b_username_top_band(d, username)
    if s:
        return s
    s = _profile_signal_c_chrome(d, package)
    if s:
        return s
    return None


def tap_account_result(d: u2.Device, username: str) -> bool:
    """Tap chosen row: FastIME+fused uses hot resource-id poll + direct tap; else legacy find."""

    def scan_once():
        return find_real_account_text_element(d, username, dump_on_failure=False)

    global _perf
    fused = _peek_pending_fused_fast_ime_row(username)
    if fused:
        _clear_pending_fused_fast_ime_row()

    hot_el_found = False
    el = None
    t_detect = time.perf_counter()
    poll_s = float(getattr(config, "HOT_ROW_POLL_S", 0.12))

    if fused:
        if get_search_ui_mode() == "mixed_results":
            timeout_s = float(getattr(config, "MIXED_HOT_ROW_DETECT_MAX_S", 4.0))
        else:
            timeout_s = float(config.ACCOUNTS_RESULT_WAIT_S)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            el = find_first_row_search_username_hot(d, username)
            if el is not None:
                hot_el_found = True
                log(
                    "info",
                    "row_hot_path_first_match",
                    username=username,
                    search_ui_mode=get_search_ui_mode(),
                )
                break
            time.sleep(poll_s)
        if el is None:
            timeout_legacy = (
                float(getattr(config, "MIXED_RESULTS_ROW_DETECT_MAX_S", 2.5))
                if get_search_ui_mode() == "mixed_results"
                else float(config.ACCOUNTS_RESULT_WAIT_S)
            )
            el = retry_until_jitter(
                scan_once,
                timeout_s=timeout_legacy,
                poll_min_s=0.05,
                poll_max_s=0.07,
                desc="real_account_textview_after_hot_miss",
            )
            hot_el_found = False
    else:
        timeout = (
            float(getattr(config, "MIXED_RESULTS_ROW_DETECT_MAX_S", 2.5))
            if get_search_ui_mode() == "mixed_results"
            else float(config.ACCOUNTS_RESULT_WAIT_S)
        )
        el = retry_until_jitter(
            scan_once,
            timeout_s=timeout,
            poll_min_s=0.05,
            poll_max_s=0.07,
            desc="real_account_textview",
        )

    _perf["row_detect_ms"] = (time.perf_counter() - t_detect) * 1000
    if el is None:
        _perf["row_tap_command_ms"] = 0.0
        _perf["post_tap_settle_ms"] = 0.0
        _perf["profile_transition_wait_ms"] = 0.0
        _dump_no_real_account_row_debug(d, username)
        log("error", "tap_account_no_element", username=username)
        return False
    _perf["row_tap_command_ms"] = 0.0
    _perf["post_tap_settle_ms"] = 0.0
    _perf["profile_transition_wait_ms"] = 0.0
    try:
        if hot_el_found:
            cx, cy, b = _tap_hot_username_center_jitter(d, el)
            tap_mode = "hot_center_jitter"
        else:
            b = _tap_bounds_for_account_row(d, el)
            if not b:
                b = {k: int(el.info["bounds"][k]) for k in ("left", "top", "right", "bottom")}
            cx = (b["left"] + b["right"]) // 2
            cy = (b["top"] + b["bottom"]) // 2
            tap_mode = "legacy_bounds"

        t_click = time.perf_counter()
        d.click(cx, cy)
        row_tap_ms = (time.perf_counter() - t_click) * 1000
        _perf["row_tap_command_ms"] = row_tap_ms
        log(
            "info",
            "row_tap_command_done",
            row_tap_command_ms=round(row_tap_ms, 2),
            x=cx,
            y=cy,
            username=username,
            tap_mode=tap_mode,
            search_ui_mode=get_search_ui_mode(),
        )

        settle_s = min(
            0.15,
            float(getattr(config, "POST_TAP_SETTLE_S", 0.12)),
        )
        t_settle = time.perf_counter()
        time.sleep(settle_s)
        _perf["post_tap_settle_ms"] = (time.perf_counter() - t_settle) * 1000

        t_trans = time.perf_counter()
        trans_deadline = time.monotonic() + float(
            getattr(config, "PROFILE_TRANSITION_POLL_MAX_S", 4.0)
        )
        trans_signal: str | None = None
        sleep_lo = float(getattr(config, "PROFILE_TRANSITION_POLL_SLEEP_MIN_S", 0.08))
        sleep_hi = float(getattr(config, "PROFILE_TRANSITION_POLL_SLEEP_MAX_S", 0.12))
        while time.monotonic() < trans_deadline:
            trans_signal = _early_profile_transition_signal(d, username)
            if trans_signal:
                log(
                    "info",
                    "profile_transition_signal_early",
                    signal=trans_signal,
                    username=username,
                )
                break
            time.sleep(random.uniform(sleep_lo, sleep_hi))
        _perf["profile_transition_wait_ms"] = (time.perf_counter() - t_trans) * 1000

        log(
            "info",
            "account_row_tapped",
            x=cx,
            y=cy,
            username=username,
            search_ui_mode=get_search_ui_mode(),
            tap_bounds=b,
            tap_mode=tap_mode,
        )
        log("info", "real_account_row_tap_center", x=cx, y=cy, username=username)
        if fused or hot_el_found:
            _mark_search_surface_ok(d, config.INSTAGRAM_PACKAGE)
        return True
    except Exception as e:
        log("error", "account_row_tap_failed", error=str(e))
        _perf["post_tap_settle_ms"] = 0.0
        _perf["profile_transition_wait_ms"] = 0.0
        return False


def verify_profile(d: u2.Device, username: str) -> bool:
    """
    Lightweight multi-signal profile open detection (no XML dumps, no single-selector dependency).
    """
    global _perf
    t0 = time.perf_counter()
    pkg = config.INSTAGRAM_PACKAGE
    time.sleep(config.PROFILE_POST_TAP_STABILIZE_S)
    deadline = time.monotonic() + config.PROFILE_VERIFY_LIGHTWEIGHT_MAX_S
    attempt = 0

    try:
        while time.monotonic() < deadline:
            attempt += 1
            _try_dismiss_keyboard_light(d)

            signal = _try_profile_signals_once(d, username, pkg)
            if signal:
                log(
                    "info",
                    "profile_open_detected",
                    username=username,
                    signal=signal,
                    attempt=attempt,
                )
                log(
                    "info",
                    "profile_verify_signal_found",
                    username=username,
                    signal=signal,
                    attempt=attempt,
                )
                log(
                    "info",
                    "profile_verify_success",
                    username=username,
                    signal=signal,
                    attempts=attempt,
                )
                return True

            log(
                "info",
                "profile_verify_retry",
                username=username,
                attempt=attempt,
                max_wait_s=config.PROFILE_VERIFY_LIGHTWEIGHT_MAX_S,
            )
            time.sleep(config.PROFILE_VERIFY_POLL_S)

        log(
            "error",
            "profile_verify_failed",
            username=username,
            reason="no_signal_within_lightweight_window",
            waited_s=config.PROFILE_VERIFY_LIGHTWEIGHT_MAX_S,
        )
        return False
    finally:
        _perf["profile_verify_ms"] = (time.perf_counter() - t0) * 1000


def _dm_parse_bounds_attr_xml(raw: str | None) -> dict[str, int] | None:
    """Parse Android bounds '[l,t][r,b]' from hierarchy XML."""
    if not raw or "[" not in raw:
        return None
    try:
        inner = raw.replace("][", ",").replace("[", "").replace("]", "")
        parts = [int(x.strip()) for x in inner.split(",") if x.strip()]
        if len(parts) != 4:
            return None
        l, t, r, b = parts
        return {"left": l, "top": t, "right": r, "bottom": b}
    except Exception:
        return None


def _dm_bounds_intersect_bottom_band(
    bd: dict[str, int], screen_h: int, bottom_fraction: float
) -> bool:
    cut_top = int(screen_h * max(0.0, min(1.0, 1.0 - bottom_fraction)) + 0.5)
    top = int(bd.get("top", 0))
    bottom = int(bd.get("bottom", 0))
    return bottom > cut_top and top < screen_h


def _dm_nearest_clickable_xml_ancestor(
    elem: ET.Element, parent_map: dict[ET.Element, ET.Element | None]
) -> ET.Element | None:
    cur = parent_map.get(elem)
    while cur is not None:
        if cur.get("clickable", "").lower() == "true":
            return cur
        cur = parent_map.get(cur)
    return None


def _dm_visible_from_xml(elem: ET.Element) -> bool:
    for key in ("visible-to-user", "displayed", "visible"):
        v = elem.get(key)
        if v is not None:
            return str(v).lower() != "false"
    return True


def _dm_bottom_ui_dump_from_hierarchy(
    xml_text: str,
    composer_bounds: dict | None,
    screen_w: int,
    screen_h: int,
    *,
    bottom_fraction: float = 0.45,
    max_nodes: int = 250,
) -> dict[str, Any]:
    """
    Parse saved hierarchy XML; collect nodes intersecting the bottom fraction of the screen.
    No class or clickable filtering. Sorted bottom-first, then toward the right.
    """
    cb = composer_bounds or {}
    cright = int(cb.get("right", 0)) if cb else 0
    out: dict[str, Any] = {
        "bottom_ui_dump_node_count": 0,
        "bottom_ui_dump_bottom_fraction": bottom_fraction,
        "bottom_ui_dump_max_nodes": max_nodes,
        "nodes": [],
    }
    text = (xml_text or "").strip()
    if not text:
        return out
    try:
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            root = ET.fromstring(f"<_root>{text}</_root>")
        parent_map: dict[ET.Element, ET.Element | None] = {root: None}

        def index_parents(el: ET.Element) -> None:
            for ch in list(el):
                parent_map[ch] = el
                index_parents(ch)

        index_parents(root)
        candidates: list[tuple[int, int, dict[str, Any]]] = []
        for el in root.iter():
            if el is root and root.tag in ("_root",):
                continue
            bd = _dm_parse_bounds_attr_xml(el.get("bounds"))
            if not bd:
                continue
            if not _dm_bounds_intersect_bottom_band(bd, screen_h, bottom_fraction):
                continue
            left, top, right, bottom = (
                int(bd["left"]),
                int(bd["top"]),
                int(bd["right"]),
                int(bd["bottom"]),
            )
            wi = abs(right - left)
            hi = abs(bottom - top)
            cx = (left + right) // 2
            cy = (top + bottom) // 2
            cls = el.get("class") or el.get("className") or el.tag or ""
            rid = el.get("resource-id") or el.get("resourceId")
            txt = el.get("text") or ""
            cdesc = el.get("content-desc") or el.get("contentDescription") or ""
            clk = el.get("clickable", "")
            en = el.get("enabled", "")
            rec: dict[str, Any] = {
                "className": cls,
                "resourceId": rid,
                "text": txt,
                "contentDescription": cdesc,
                "bounds": dict(bd),
                "clickable": True
                if clk.lower() == "true"
                else False
                if clk.lower() == "false"
                else None,
                "enabled": True
                if en.lower() == "true"
                else False
                if en.lower() == "false"
                else None,
                "visible": _dm_visible_from_xml(el),
                "center_x": cx,
                "center_y": cy,
                "width": wi,
                "height": hi,
                "right_of_composer": bool(cb) and cx >= cright - 140,
                "distance_to_composer_right": (cx - cright) if cb else None,
                "lower_screen_half": cy >= (screen_h // 2),
            }
            anc = _dm_nearest_clickable_xml_ancestor(el, parent_map)
            if anc is not None:
                ap = _dm_parse_bounds_attr_xml(anc.get("bounds"))
                rec["nearest_clickable_ancestor"] = {
                    "className": anc.get("class") or anc.get("className"),
                    "resourceId": anc.get("resource-id") or anc.get("resourceId"),
                    "clickable": anc.get("clickable"),
                    "enabled": anc.get("enabled"),
                    "bounds": dict(ap) if ap else None,
                }
            else:
                rec["nearest_clickable_ancestor"] = None
            par_el = parent_map.get(el)
            if par_el is not None:
                pp = _dm_parse_bounds_attr_xml(par_el.get("bounds"))
                rec["parent"] = {
                    "className": par_el.get("class") or par_el.get("className"),
                    "resourceId": par_el.get("resource-id") or par_el.get("resourceId"),
                    "clickable": par_el.get("clickable"),
                    "enabled": par_el.get("enabled"),
                    "bounds": dict(pp) if pp else None,
                }
                try:
                    sibs = list(par_el)
                    ix = sibs.index(el)
                    if ix + 1 < len(sibs):
                        nx = sibs[ix + 1]
                        np = _dm_parse_bounds_attr_xml(nx.get("bounds"))
                        rec["next_sibling"] = {
                            "className": nx.get("class") or nx.get("className"),
                            "resourceId": nx.get("resource-id") or nx.get("resourceId"),
                            "clickable": nx.get("clickable"),
                            "bounds": dict(np) if np else None,
                        }
                    else:
                        rec["next_sibling"] = None
                except (ValueError, IndexError):
                    rec["next_sibling"] = None
            else:
                rec["parent"] = None
                rec["next_sibling"] = None
            # Sort: lower on screen first (larger cy), then more to the right (larger cx).
            candidates.append((-cy, cx, rec))
        candidates.sort(key=lambda t: (t[0], t[1]))
        nodes = [t[2] for t in candidates[:max_nodes]]
        out["nodes"] = nodes
        out["bottom_ui_dump_node_count"] = len(nodes)
        return out
    except Exception as e:
        out["nodes"] = []
        out["bottom_ui_dump_node_count"] = 0
        out["error"] = str(e)
        return out


def _dm_send_button_debug_artifacts(
    *,
    hierarchy_xml: str,
    composer_bounds: dict | None,
    screen_w: int,
    screen_h: int,
    debug_screenshot_path: str | None = None,
    debug_xml_path: str | None = None,
    bottom_fraction: float = 0.45,
    max_nodes: int = 250,
) -> dict[str, Any]:
    """
    After screenshot + hierarchy XML are saved (draft still visible), build telemetry for send_out.

    Correct _dm_bottom_ui_dump_from_hierarchy call order:
    (hierarchy_xml, composer_bounds, screen_w, screen_h) — never (xml, w, h, bounds).
    """
    dump_payload = _dm_bottom_ui_dump_from_hierarchy(
        hierarchy_xml,
        composer_bounds,
        screen_w,
        screen_h,
        bottom_fraction=bottom_fraction,
        max_nodes=max_nodes,
    )
    out: dict[str, Any] = {
        "dm_send_bottom_ui_dump": dump_payload,
        "bottom_ui_dump_node_count": dump_payload["bottom_ui_dump_node_count"],
        "bottom_ui_dump_bottom_fraction": dump_payload["bottom_ui_dump_bottom_fraction"],
        "bottom_ui_dump_max_nodes": dump_payload["bottom_ui_dump_max_nodes"],
    }
    if debug_screenshot_path is not None:
        out["debug_screenshot_path"] = debug_screenshot_path
    if debug_xml_path is not None:
        out["debug_xml_path"] = debug_xml_path
    if dump_payload.get("error"):
        out["bottom_ui_dump_error"] = dump_payload["error"]
    return out


_DM_SEND_POSITION_NOISE: tuple[str, ...] = (
    "camera",
    "mic",
    "microphone",
    "gallery",
    "emoji",
    "plus",
    "attach",
    "sticker",
    "stickers",
    "gif",
    "like",
    "row_thread_right_composer_button_write_with_ai",
    "row_thread_composer_button_sticker_shortcut",
)


def _dm_position_fallback_obvious_noise(rid: str, desc: str, txt: str) -> bool:
    blob = f"{rid} {desc} {txt}".lower()
    for frag in _DM_SEND_POSITION_NOISE:
        if frag in blob:
            return True
    return False


def _dm_position_fallback_class_ok(class_name: str) -> bool:
    if not class_name:
        return False
    cn = class_name
    if "FrameLayout" in cn:
        return True
    if "ImageView" in cn or "ImageButton" in cn:
        return True
    if cn == "android.view.View" or cn.endswith("android.view.View"):
        return True
    return False


def _dm_gather_send_raw_candidates(
    xml_text: str, screen_w: int, screen_h: int
) -> list[dict[str, Any]]:
    """
    Broad visual scan for send-like controls in lower-right zone.
    Intentionally permissive: no strict keyword/rid filtering at this stage.
    """
    out: list[dict[str, Any]] = []
    text = (xml_text or "").strip()
    if not text:
        return out
    try:
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            root = ET.fromstring(f"<_root>{text}</_root>")
    except ET.ParseError:
        return out

    allowed_classes = (
        "android.widget.ImageView",
        "android.widget.ImageButton",
        "android.view.View",
        "android.widget.FrameLayout",
    )
    for el in root.iter():
        bd = _dm_parse_bounds_attr_xml(el.get("bounds"))
        if not bd:
            continue
        left, top, right, bottom = (
            int(bd["left"]),
            int(bd["top"]),
            int(bd["right"]),
            int(bd["bottom"]),
        )
        wi, hi = abs(right - left), abs(bottom - top)
        if not (30 <= wi <= 200 and 30 <= hi <= 200):
            continue
        cx = (left + right) // 2
        cy = (top + bottom) // 2
        if cx < int(screen_w * 0.5):
            continue
        if cy < int(screen_h * 0.5):
            continue
        cn = (el.get("class") or el.get("className") or "").strip()
        if cn not in allowed_classes and not (
            cn.endswith("ImageView")
            or cn.endswith("ImageButton")
            or cn.endswith("android.view.View")
            or cn.endswith("FrameLayout")
        ):
            continue
        if not _dm_xml_enabled_visible_ok(el):
            continue

        out.append(
            {
                "className": cn,
                "resourceId": el.get("resource-id") or el.get("resourceId") or "",
                "text": el.get("text") or "",
                "contentDescription": el.get("content-desc")
                or el.get("contentDescription")
                or "",
                "bounds": dict(bd),
                "center_x": cx,
                "center_y": cy,
                "width": wi,
                "height": hi,
            }
        )
    return out


_DM_EXACT_SEND_RIDS: tuple[str, ...] = (
    "com.instagram.android:id/row_thread_composer_send_button_container",
    "com.instagram.android:id/row_thread_composer_send_button_background",
    "com.instagram.android:id/row_thread_composer_send_button_icon",
)


def _dm_extract_xml_node_candidate(el: ET.Element) -> dict[str, Any] | None:
    bd = _dm_parse_bounds_attr_xml(el.get("bounds"))
    if not bd:
        return None
    left, top, right, bottom = (
        int(bd["left"]),
        int(bd["top"]),
        int(bd["right"]),
        int(bd["bottom"]),
    )
    wi, hi = abs(right - left), abs(bottom - top)
    return {
        "className": (el.get("class") or el.get("className") or ""),
        "resourceId": el.get("resource-id") or el.get("resourceId") or "",
        "text": el.get("text") or "",
        "contentDescription": el.get("content-desc") or el.get("contentDescription") or "",
        "bounds": dict(bd),
        "center_x": (left + right) // 2,
        "center_y": (top + bottom) // 2,
        "width": wi,
        "height": hi,
        "clickable": str(el.get("clickable", "")).lower() == "true",
        "enabled": str(el.get("enabled", "true")).lower() != "false",
    }


def _dm_find_exact_instagram_send_candidate(
    xml_text: str,
    composer_bounds: dict[str, int],
    screen_h: int,
) -> dict[str, Any]:
    """
    Priority path for known Instagram send ids.
    Returns:
      {
        "selected": candidate|None,
        "seen": [candidate...],
        "rejected": [{"candidate":..., "reject_reason": "..."}...]
      }
    """
    out: dict[str, Any] = {"selected": None, "seen": [], "rejected": []}
    text = (xml_text or "").strip()
    if not text:
        return out
    try:
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            root = ET.fromstring(f"<_root>{text}</_root>")
    except ET.ParseError:
        return out

    parent_map: dict[ET.Element, ET.Element | None] = {root: None}

    def index_parents(el: ET.Element) -> None:
        for ch in list(el):
            parent_map[ch] = el
            index_parents(ch)

    index_parents(root)

    cright = int(composer_bounds.get("right", 0))
    ctop = int(composer_bounds.get("top", 0))
    cbot = int(composer_bounds.get("bottom", 0))
    y_lo = ctop - 120
    y_hi = cbot + 120
    lower_half_cut = int(screen_h * 0.5)

    for el in root.iter():
        rid = (el.get("resource-id") or el.get("resourceId") or "").strip()
        if rid not in _DM_EXACT_SEND_RIDS:
            continue

        raw = _dm_extract_xml_node_candidate(el)
        if raw is None:
            continue
        raw["matched_resource_id"] = rid
        out["seen"].append(raw)

        # For background/icon, prefer clickable ancestor container.
        target_el = el
        target_reason = ""
        if rid.endswith("row_thread_composer_send_button_background") or rid.endswith(
            "row_thread_composer_send_button_icon"
        ):
            anc = _dm_nearest_clickable_xml_ancestor(el, parent_map)
            if anc is None:
                target_reason = "no_clickable_ancestor"
            else:
                anc_rid = (anc.get("resource-id") or anc.get("resourceId") or "").strip()
                anc_click = str(anc.get("clickable", "")).lower() == "true"
                anc_enabled = str(anc.get("enabled", "true")).lower() != "false"
                if (
                    anc_rid
                    == "com.instagram.android:id/row_thread_composer_send_button_container"
                    and anc_click
                    and anc_enabled
                ):
                    target_el = anc
                else:
                    target_reason = "ancestor_not_clickable_send_container"

        target = _dm_extract_xml_node_candidate(target_el)
        if target is None:
            out["rejected"].append({"candidate": raw, "reject_reason": "invalid_target_bounds"})
            continue
        target_rid = str(target.get("resourceId") or "")
        target_clickable = bool(target.get("clickable"))
        target_enabled = bool(target.get("enabled"))
        cx = int(target.get("center_x") or 0)
        cy = int(target.get("center_y") or 0)
        ttop = int((target.get("bounds") or {}).get("top", 0))
        tbot = int((target.get("bounds") or {}).get("bottom", 0))
        right_of_composer = cx > (cright - 20)
        in_vertical_band = not (tbot < y_lo or ttop > y_hi)
        lower_half = cy >= lower_half_cut

        reject_reason = ""
        if target_reason:
            reject_reason = target_reason
        elif target_rid != "com.instagram.android:id/row_thread_composer_send_button_container":
            reject_reason = "target_not_send_container"
        elif not target_clickable:
            reject_reason = "target_not_clickable"
        elif not target_enabled:
            reject_reason = "target_not_enabled"
        elif not right_of_composer:
            reject_reason = "target_not_right_of_composer"
        elif not in_vertical_band:
            reject_reason = "target_not_in_composer_vertical_band"
        elif not lower_half:
            reject_reason = "target_not_in_lower_half"

        target["matched_resource_id"] = rid
        target["right_of_composer"] = right_of_composer
        target["lower_screen_half"] = lower_half
        target["in_composer_vertical_band"] = in_vertical_band

        if reject_reason:
            out["rejected"].append({"candidate": target, "reject_reason": reject_reason})
            continue

        out["selected"] = target
        return out

    return out


def _dm_visual_send_candidates_from_hierarchy_xml(
    xml_text: str,
    composer_bounds: dict[str, int],
    screen_w: int,
    screen_h: int,
) -> dict[str, Any]:
    cright = int(composer_bounds.get("right", 0))
    ctop = int(composer_bounds.get("top", 0))
    cbot = int(composer_bounds.get("bottom", 0))
    out: dict[str, Any] = {
        "candidates": [],
        "surviving_candidates": [],
        "visual_candidates_rejected": [],
        "rejection_breakdown": {},
    }
    raw = _dm_gather_send_raw_candidates(xml_text, screen_w, screen_h)
    out["surviving_candidates"] = raw
    survivors: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    reasons_count: dict[str, int] = {}

    for c in raw:
        reason = ""
        cn = str(c.get("className") or "")
        rid = str(c.get("resourceId") or "")
        txt = str(c.get("text") or "")
        desc = str(c.get("contentDescription") or "")
        cx = int(c.get("center_x") or 0)
        cy = int(c.get("center_y") or 0)
        wi = int(c.get("width") or 0)
        hi = int(c.get("height") or 0)

        right_of_composer = cx > (cright - 20)
        lower_screen_half = cy >= int(screen_h * 0.5)
        near_composer_vertical = (ctop - 120) <= cy <= (cbot + 120)
        class_plausible = (
            "ImageView" in cn
            or "ImageButton" in cn
            or cn == "android.view.View"
            or cn.endswith("android.view.View")
            or "FrameLayout" in cn
        )
        size_plausible = 30 <= wi <= 200 and 30 <= hi <= 200

        if not class_plausible:
            reason = "class_not_plausible"
        elif not size_plausible:
            reason = "size_not_plausible"
        elif not right_of_composer:
            reason = "not_right_of_composer"
        elif not lower_screen_half:
            reason = "not_lower_half"
        elif not near_composer_vertical:
            reason = "not_near_composer_vertical"
        elif _dm_position_fallback_obvious_noise(rid.lower(), desc.lower(), txt.lower()):
            reason = "obvious_noise"

        c["right_of_composer"] = bool(right_of_composer)
        c["lower_screen_half"] = bool(lower_screen_half)
        c["near_composer_vertical"] = bool(near_composer_vertical)

        if reason:
            c["reject_reason"] = reason
            rejected.append(c)
            reasons_count[reason] = int(reasons_count.get(reason, 0)) + 1
            continue
        survivors.append(c)

    out["candidates"] = survivors
    out["visual_candidates_rejected"] = rejected
    out["rejection_breakdown"] = reasons_count
    return out


def _dm_screen_size_for_dm(d: u2.Device) -> tuple[int, int]:
    try:
        w, h = d.window_size()
        return int(w), int(h)
    except Exception:
        return 1080, 1920


def _dm_composer_bounds_u2(composer) -> dict[str, int]:
    try:
        inf = composer.info or {}
        b = inf.get("bounds") or {}
        return {k: int(b[k]) for k in ("left", "top", "right", "bottom") if k in b}
    except Exception:
        return {}


def _dm_xml_enabled_visible_ok(elem: ET.Element) -> bool:
    en = elem.get("enabled", "")
    if en and en.lower() == "false":
        return False
    return _dm_visible_from_xml(elem)


def _dm_position_fallback_candidates_from_hierarchy_xml(
    xml_text: str,
    composer_bounds: dict[str, int],
    screen_w: int,
    screen_h: int,
) -> list[dict[str, Any]]:
    """Compatibility wrapper: return only surviving visual candidates."""
    info = _dm_visual_send_candidates_from_hierarchy_xml(
        xml_text, composer_bounds, screen_w, screen_h
    )
    return list(info.get("candidates") or [])


def _dm_coordinate_send_point_from_composer(
    composer_bounds: dict[str, int], screen_w: int
) -> tuple[int, int]:
    cright = int(composer_bounds["right"])
    ctop = int(composer_bounds["top"])
    cbot = int(composer_bounds["bottom"])
    x = int(cright + (screen_w - cright) / 2)
    y = (ctop + cbot) // 2
    return x, y


class _DmSendTapOnce:
    """Single physical tap (guards accidental double-click on same target)."""

    __slots__ = ("_d", "_x", "_y", "_spent")

    def __init__(self, d: u2.Device, x: int, y: int) -> None:
        self._d = d
        self._x = int(x)
        self._y = int(y)
        self._spent = False

    def click(self) -> None:
        if self._spent:
            log("warning", "dm_send_tap_ignored_already_clicked", x=self._x, y=self._y)
            return
        self._d.click(self._x, self._y)
        self._spent = True


def _dm_try_unique_send_resource_u2(d: u2.Device) -> Any | None:
    for pat in (
        ".*:id/.*send.*",
        ".*:id/.*direct_send.*",
        ".*:id/.*row_thread_composer_button_send.*",
        ".*:id/.*button_send.*",
        ".*:id/.*composer.*send.*",
    ):
        try:
            sel = d(resourceIdMatches=pat)
            objs = sel.all() if hasattr(sel, "all") else []
            if len(objs) == 1:
                return objs[0]
        except Exception:
            continue
    return None


def wait_for_dm_send_button_after_draft(
    d: u2.Device,
    composer,
    *,
    thread_state: str,
    draft_matches_expected: bool = True,
) -> tuple[Any | None, str, dict[str, Any]]:
    """
    Poll for a unique Send control. For empty_new_thread + draft verified, adds hierarchy position
    fallback (paper-plane zone right of composer) and optional coordinate fallback (config-gated).
    """
    w, h = _dm_screen_size_for_dm(d)
    composer_bounds = _dm_composer_bounds_u2(composer)
    max_wait = float(getattr(config, "DM_SEND_BUTTON_WAIT_MAX_S", 1.5))
    poll_s = float(getattr(config, "DM_SEND_BUTTON_POLL_S", 0.1))
    deadline = time.monotonic() + max_wait
    meta: dict[str, Any] = {
        "send_button_candidate_count": 0,
        "send_button_position_fallback": False,
        "send_button_coordinate_fallback": False,
        "surviving_candidates": [],
        "visual_candidates_rejected": [],
        "rejection_breakdown": {},
    }
    allow_pos = (
        (thread_state or "").strip() == "empty_new_thread"
        and draft_matches_expected
        and bool(composer_bounds)
    )
    coord_fb = bool(getattr(config, "ENABLE_COORDINATE_SEND_FALLBACK", False))
    log(
        "info",
        "dm_send_button_wait_started",
        max_wait_s=max_wait,
        poll_s=poll_s,
        position_fallback_allowed=allow_pos,
    )
    while time.monotonic() < deadline:
        u = _dm_try_unique_send_resource_u2(d)
        if u is not None:
            meta["send_button_candidate_count"] = 1
            log("info", "dm_send_button_wait_ok", path="resource_id")
            return u, "ok", meta

        if allow_pos:
            try:
                hier = d.dump_hierarchy(compressed=False)
            except Exception:
                hier = d.dump_hierarchy()
            exact = _dm_find_exact_instagram_send_candidate(hier, composer_bounds, h)
            for s in list(exact.get("seen") or []):
                log(
                    "info",
                    "dm_send_exact_instagram_candidate_seen",
                    matched_resource_id=s.get("matched_resource_id"),
                    used_click_target_resource_id=s.get("resourceId"),
                    used_click_target_class=s.get("className"),
                    used_click_target_bounds=s.get("bounds"),
                    composer_bounds=composer_bounds,
                    thread_state=thread_state,
                )
            for rej in list(exact.get("rejected") or []):
                c = rej.get("candidate") or {}
                log(
                    "info",
                    "dm_send_exact_instagram_candidate_rejected",
                    reject_reason=rej.get("reject_reason"),
                    matched_resource_id=c.get("matched_resource_id"),
                    used_click_target_resource_id=c.get("resourceId"),
                    used_click_target_class=c.get("className"),
                    used_click_target_bounds=c.get("bounds"),
                    composer_bounds=composer_bounds,
                    thread_state=thread_state,
                )
            ex_sel = exact.get("selected")
            if isinstance(ex_sel, dict):
                log(
                    "info",
                    "dm_send_exact_instagram_candidate_selected",
                    matched_resource_id=ex_sel.get("matched_resource_id"),
                    used_click_target_resource_id=ex_sel.get("resourceId"),
                    used_click_target_class=ex_sel.get("className"),
                    used_click_target_bounds=ex_sel.get("bounds"),
                    composer_bounds=composer_bounds,
                    thread_state=thread_state,
                )
                meta["send_button_candidate_count"] = 1
                meta["send_button_position_fallback"] = True
                return _DmSendTapOnce(d, ex_sel["center_x"], ex_sel["center_y"]), "ok", meta
            info = _dm_visual_send_candidates_from_hierarchy_xml(
                hier, composer_bounds, w, h
            )
            pos = list(info.get("candidates") or [])
            meta["send_button_candidate_count"] = len(pos)
            meta["surviving_candidates"] = list(info.get("surviving_candidates") or [])
            meta["visual_candidates_rejected"] = list(
                info.get("visual_candidates_rejected") or []
            )
            meta["rejection_breakdown"] = dict(info.get("rejection_breakdown") or {})
            if len(pos) == 1:
                c = pos[0]
                log(
                    "info",
                    "dm_send_button_detected_visual_only",
                    className=c.get("className"),
                    resourceId=c.get("resourceId"),
                    text=c.get("text"),
                    contentDescription=c.get("contentDescription"),
                    bounds=c.get("bounds"),
                    center_x=c.get("center_x"),
                    center_y=c.get("center_y"),
                    width=c.get("width"),
                    height=c.get("height"),
                    right_of_composer=c.get("right_of_composer"),
                    lower_screen_half=c.get("lower_screen_half"),
                    candidate=c,
                    thread_state=thread_state,
                )
                meta["send_button_position_fallback"] = True
                tap = _DmSendTapOnce(d, c["center_x"], c["center_y"])
                return tap, "ok", meta
        time.sleep(poll_s)

    meta["send_button_candidate_count"] = 0
    try:
        hier = d.dump_hierarchy(compressed=False)
    except Exception:
        hier = d.dump_hierarchy()
    if allow_pos and composer_bounds:
        exact = _dm_find_exact_instagram_send_candidate(hier, composer_bounds, h)
        for s in list(exact.get("seen") or []):
            log(
                "info",
                "dm_send_exact_instagram_candidate_seen",
                matched_resource_id=s.get("matched_resource_id"),
                used_click_target_resource_id=s.get("resourceId"),
                used_click_target_class=s.get("className"),
                used_click_target_bounds=s.get("bounds"),
                composer_bounds=composer_bounds,
                thread_state=thread_state,
            )
        for rej in list(exact.get("rejected") or []):
            c = rej.get("candidate") or {}
            log(
                "info",
                "dm_send_exact_instagram_candidate_rejected",
                reject_reason=rej.get("reject_reason"),
                matched_resource_id=c.get("matched_resource_id"),
                used_click_target_resource_id=c.get("resourceId"),
                used_click_target_class=c.get("className"),
                used_click_target_bounds=c.get("bounds"),
                composer_bounds=composer_bounds,
                thread_state=thread_state,
            )
        ex_sel = exact.get("selected")
        if isinstance(ex_sel, dict):
            log(
                "info",
                "dm_send_exact_instagram_candidate_selected",
                matched_resource_id=ex_sel.get("matched_resource_id"),
                used_click_target_resource_id=ex_sel.get("resourceId"),
                used_click_target_class=ex_sel.get("className"),
                used_click_target_bounds=ex_sel.get("bounds"),
                composer_bounds=composer_bounds,
                thread_state=thread_state,
            )
            meta["send_button_candidate_count"] = 1
            meta["send_button_position_fallback"] = True
            return _DmSendTapOnce(d, ex_sel["center_x"], ex_sel["center_y"]), "ok", meta
        info = _dm_visual_send_candidates_from_hierarchy_xml(
            hier, composer_bounds, w, h
        )
        pos = list(info.get("candidates") or [])
        meta["send_button_candidate_count"] = len(pos)
        meta["surviving_candidates"] = list(info.get("surviving_candidates") or [])
        meta["visual_candidates_rejected"] = list(
            info.get("visual_candidates_rejected") or []
        )
        meta["rejection_breakdown"] = dict(info.get("rejection_breakdown") or {})
        if len(pos) == 1:
            c = pos[0]
            log(
                "info",
                "dm_send_button_detected_visual_only",
                className=c.get("className"),
                resourceId=c.get("resourceId"),
                text=c.get("text"),
                contentDescription=c.get("contentDescription"),
                bounds=c.get("bounds"),
                center_x=c.get("center_x"),
                center_y=c.get("center_y"),
                width=c.get("width"),
                height=c.get("height"),
                right_of_composer=c.get("right_of_composer"),
                lower_screen_half=c.get("lower_screen_half"),
                candidate=c,
                thread_state=thread_state,
            )
            meta["send_button_position_fallback"] = True
            tap = _DmSendTapOnce(d, c["center_x"], c["center_y"])
            return tap, "ok", meta
        log(
            "info",
            "dm_send_candidate_summary",
            thread_state=thread_state,
            candidate_count=len(pos),
            surviving_candidates=meta.get("surviving_candidates"),
            rejection_breakdown=meta.get("rejection_breakdown"),
            visual_candidates_rejected=meta.get("visual_candidates_rejected"),
        )
        if len(pos) == 0:
            rx, ry = _dm_coordinate_send_point_from_composer(composer_bounds, w)
            log(
                "info",
                "dm_send_coordinate_candidate_from_composer",
                x=rx,
                y=ry,
                thread_state=thread_state,
                coordinate_fallback_enabled=coord_fb,
            )
            if coord_fb:
                meta["send_button_coordinate_fallback"] = True
                meta["send_button_candidate_count"] = 1
                return _DmSendTapOnce(d, rx, ry), "ok", meta

    return None, "missing", meta


def _dm_read_composer_text_len(d: u2.Device) -> int:
    try:
        cur = _dm_find_focus_composer(d)
        if cur is None:
            return 0
        return len(str(cur.get_text() or ""))
    except Exception:
        return 0


def _dm_post_send_signal_poll(
    d: u2.Device, *, pre_send_text_len: int
) -> tuple[bool, str]:
    """
    Short bounded poll: thread/delivery hints or composer text shrinking after send.
    """
    max_s = float(getattr(config, "DM_POST_SEND_SIGNAL_MAX_S", 2.0))
    poll_s = float(getattr(config, "DM_POST_SEND_SIGNAL_POLL_S", 0.12))
    deadline = time.monotonic() + max_s
    reason = ""
    while time.monotonic() < deadline:
        try:
            for frag in ("Sent", "Delivered", "Envoyé", "Envoye", "Vu"):
                if d(textContains=frag).exists(timeout=0.04):
                    reason = f"text_marker:{frag}"
                    return True, reason
        except Exception:
            pass
        try:
            cur_len = _dm_read_composer_text_len(d)
            if pre_send_text_len > 0 and cur_len < max(0, pre_send_text_len - 3):
                reason = "composer_text_shortened"
                return True, reason
            if pre_send_text_len > 0 and cur_len == 0:
                reason = "composer_empty"
                return True, reason
        except Exception:
            pass
        time.sleep(poll_s)
    reason = "timeout"
    return False, reason


def finalize_after_real_send(
    d: u2.Device,
    username: str,
    pkg: str | None = None,
    *,
    use_fast_reset_between_targets: bool = False,
    pre_send_composer_text_len: int = 0,
) -> dict[str, Any]:
    """
    Best-effort post-real-send: short UI signal poll, draft cleanup, profile, search, temp reset.
    Does not click Send again. Instrumented for metrics and logs.
    """
    global _perf
    pkg = pkg or config.INSTAGRAM_PACKAGE
    t_total = time.perf_counter()
    out: dict[str, Any] = {
        "post_send_signal_ok": False,
        "post_send_signal_reason": "",
        "back_to_profile_ok": False,
        "back_to_search_ok": False,
        "post_send_cleanup_reason": "",
    }
    log("info", "dm_send_post_finalize_started", username=username)

    t_sig = time.perf_counter()
    sig_ok, sig_reason = _dm_post_send_signal_poll(
        d, pre_send_text_len=int(pre_send_composer_text_len or 0)
    )
    post_detect_ms = (time.perf_counter() - t_sig) * 1000
    out["post_send_signal_ok"] = bool(sig_ok)
    out["post_send_signal_reason"] = sig_reason
    set_perf_metric("post_send_detect_ms", post_detect_ms)
    if sig_ok:
        log(
            "info",
            "dm_send_post_signal_detected",
            username=username,
            reason=sig_reason,
            post_send_detect_ms=round(post_detect_ms, 2),
        )
    else:
        log(
            "warning",
            "dm_send_post_signal_timeout",
            username=username,
            reason=sig_reason,
            post_send_detect_ms=round(post_detect_ms, 2),
        )

    try:
        clear_dm_draft(d)
    except Exception:
        pass
    try:
        finalize_dm_draft_before_back(d)
    except Exception:
        pass

    t_prof = time.perf_counter()
    ok_profile = return_to_profile_from_dm(d, username, pkg)
    back_prof_ms = (time.perf_counter() - t_prof) * 1000
    out["back_to_profile_ok"] = bool(ok_profile)
    set_perf_metric("post_send_back_to_profile_ms", back_prof_ms)
    if ok_profile:
        log(
            "info",
            "dm_send_post_back_to_profile_ok",
            username=username,
            ms=round(back_prof_ms, 2),
        )
    else:
        log(
            "warning",
            "dm_send_post_back_to_profile_failed",
            username=username,
            ms=round(back_prof_ms, 2),
        )

    t_search = time.perf_counter()
    ok_search = False
    if use_fast_reset_between_targets:
        ok_search = bool(reset_to_search_for_next_target(d, pkg))
    else:
        ok_search = bool(return_to_search_from_profile(d, pkg))
        if not ok_search:
            try:
                ok_search = bool(open_search(d))
            except Exception:
                ok_search = False
    search_ms = (time.perf_counter() - t_search) * 1000
    out["back_to_search_ok"] = bool(ok_search)
    set_perf_metric("post_send_profile_to_search_ms", search_ms)
    if ok_search:
        log(
            "info",
            "dm_send_post_back_to_search_ok",
            username=username,
            ms=round(search_ms, 2),
        )
    else:
        log(
            "warning",
            "dm_send_post_back_to_search_failed",
            username=username,
            ms=round(search_ms, 2),
        )

    # Do not reset DM send/thread snapshots here: runner still reads them for Supabase logs.
    invalidate_search_surface_cache("after_real_dm_sent")

    total_ms = (time.perf_counter() - t_total) * 1000
    set_perf_metric("post_send_finalize_total_ms", total_ms)
    if ok_profile and ok_search:
        cleanup = "complete"
    elif ok_profile:
        cleanup = "partial_search_failed"
    elif ok_search:
        cleanup = "partial_profile_failed"
    else:
        cleanup = "partial_profile_and_search_failed"
    out["post_send_cleanup_reason"] = cleanup
    set_perf_metric("post_send_cleanup_reason", cleanup)
    log(
        "info",
        "dm_send_post_finalize_done",
        username=username,
        post_send_cleanup_reason=cleanup,
        post_send_finalize_total_ms=round(total_ms, 2),
    )
    return out


def set_perf_metric(key: str, value: float | int | str) -> None:
    global _perf
    _perf[key] = value


def set_wait_event_callback(cb: Callable[..., None] | None) -> None:
    global _WAIT_EVENT_CALLBACK
    _WAIT_EVENT_CALLBACK = cb


def _emit_wait_event(wait_reason: str, wait_duration_ms: float) -> None:
    cb = _WAIT_EVENT_CALLBACK
    if cb is None:
        return
    try:
        cb(wait_reason, wait_duration_ms)
    except Exception:
        pass


def reset_dm_send_run_state() -> None:
    global _LAST_DM_SEND_RESULT
    _LAST_DM_SEND_RESULT = {}


def reset_dm_thread_probe_state() -> None:
    global _LAST_DM_THREAD_ATTEMPTED, _LAST_DM_THREAD_STATE, _LAST_DM_THREAD_CLASSIFY_SNAPSHOT
    _LAST_DM_THREAD_ATTEMPTED = False
    _LAST_DM_THREAD_STATE = "unknown"
    _LAST_DM_THREAD_CLASSIFY_SNAPSHOT = {}


def get_last_dm_thread_classify_snapshot() -> dict[str, Any]:
    return dict(_LAST_DM_THREAD_CLASSIFY_SNAPSHOT)


def get_last_dm_send_result() -> dict[str, Any]:
    return dict(_LAST_DM_SEND_RESULT)


def get_last_dm_thread_attempted() -> bool:
    return bool(_LAST_DM_THREAD_ATTEMPTED)


def get_last_dm_thread_state() -> str:
    return str(_LAST_DM_THREAD_STATE or "unknown")


def detect_unsupported_start_surface(d: u2.Device) -> str | None:
    try:
        if (
            (d(text="Allow").exists(timeout=0.05) or d(textContains="Allow").exists(timeout=0.05))
            and (
                d(textContains="photos and videos").exists(timeout=0.05)
                or d(textContains="Photos").exists(timeout=0.05)
            )
        ):
            return "android_permission_dialog"
    except Exception:
        pass
    return None


def dismiss_android_permission_dialog(d: u2.Device) -> bool:
    for label in ("Don't allow", "Deny", "Refuser", "OK", "ALLOW"):
        try:
            o = d(text=label)
            if o.exists(timeout=0.08):
                o.click()
                return True
        except Exception:
            continue
    return False


def is_dm_thread_screen(d, pkg=None) -> bool:
    try:
        if not verify_app_foreground(d, pkg or config.INSTAGRAM_PACKAGE):
            return False
        w, h = d.window_size()
        for ed in d(className="android.widget.EditText").all():
            b = (ed.info or {}).get("bounds") or {}
            if int(b.get("top", 0)) > h * 0.25:
                return True
    except Exception:
        pass
    return False


def reset_to_search_for_next_target(d: u2.Device, pkg: str | None = None) -> bool:
    return return_to_search_from_profile(d, pkg)


def _dm_find_focus_composer(d: u2.Device) -> Any | None:
    """Bottom-half Instagram DM composer EditText (best-effort)."""
    w, h = d.window_size()
    # Prefer explicit composer resource id when available.
    try:
        cur_pkg = (d.app_current() or {}).get("package") or config.INSTAGRAM_PACKAGE
    except Exception:
        cur_pkg = config.INSTAGRAM_PACKAGE

    bottom_y_min = int(h * 0.5)
    rid_candidates = (
        "com.instagram.android:id/row_thread_composer_edittext",
        f"{cur_pkg}:id/row_thread_composer_edittext",
    )
    for rid in rid_candidates:
        try:
            el = d(resourceId=rid)
            if el.wait(timeout=0.08):
                b = ((el.info or {}).get("bounds") or {})
                top = int(b.get("top", 0))
                if top >= bottom_y_min:
                    return el
        except Exception:
            continue

    # ResourceIdMatches fallback (package-agnostic).
    try:
        sel = d(resourceIdMatches=r".*:id/row_thread_composer_edittext.*")
        objs = sel.all() if hasattr(sel, "all") else []
        for ed in objs:
            b = ((ed.info or {}).get("bounds") or {})
            top = int(b.get("top", 0))
            if top >= bottom_y_min:
                return ed
    except Exception:
        pass

    # Generic: bottom-half EditText.
    y_min = int(h * 0.28)
    best = None
    best_top = -1
    try:
        for ed in d(className="android.widget.EditText").all():
            info = ed.info or {}
            b = info.get("bounds") or {}
            top = int(b.get("top", 0))
            if top < y_min:
                continue
            if top >= best_top:
                best_top = top
                best = ed
    except Exception:
        return None
    return best


def _dm_restricted_surfaces(d: u2.Device) -> bool:
    try:
        needles = (
            "can't message",
            "Cannot message",
            "can't reply",
            "Message unavailable",
            "restricted",
            "Ne peut pas envoyer",
        )
        for frag in needles:
            if d(textContains=frag).exists(timeout=0.05):
                return True
    except Exception:
        pass
    return False


def _dm_hierarchy_suggests_existing_thread(hier: str) -> bool:
    if not hier:
        return False
    markers = (
        "row_thread_message",
        "direct_message_text",
        "message_content",
        "thread_message",
        "inbox_message",
        "message_bubble",
    )
    blob = hier.lower()
    return any(m in blob for m in markers)


def _dm_message_keyword_blobs() -> tuple[str, ...]:
    # Covers: Message / Send message / Envoyer un message / Écrire un message
    return (
        "Message",
        "Send message",
        "Envoyer un message",
        "Ecrire un message",
        "Écrire un message",
        "Write a message",
    )


def _dm_detect_composer_signal(
    d: u2.Device, *, current_pkg: str, timeout_s: float
) -> tuple[bool, str, dict[str, int] | None, Any | None]:
    """
    Best-effort composer detection.
    Returns: (composer_visible, composer_signal, composer_bounds, composer_el)
    """
    deadline = time.monotonic() + float(timeout_s)
    w, h = d.window_size()
    bottom_y_min = int(h * 0.5)
    composer_el: Any | None = None
    composer_signal = ""
    composer_bounds: dict[str, int] | None = None
    exact_rid = "com.instagram.android:id/row_thread_composer_edittext"
    pkg_rid = f"{current_pkg}:id/row_thread_composer_edittext"

    # 1) resource-id exact
    while time.monotonic() < deadline:
        try:
            el = d(resourceId=exact_rid)
            if el.wait(timeout=0.05):
                b = (el.info or {}).get("bounds") or {}
                top = int(b.get("top", 0))
                if top >= bottom_y_min:
                    composer_el = el
                    composer_bounds = b
                    composer_signal = "resource_id_exact_composer"
                    break
        except Exception:
            pass

        # 2) resourceIdMatches
        try:
            sel = d(resourceIdMatches=r".*:id/row_thread_composer_edittext.*")
            objs = sel.all() if hasattr(sel, "all") else []
            for ed in objs:
                b = (ed.info or {}).get("bounds") or {}
                top = int(b.get("top", 0))
                if top >= bottom_y_min:
                    composer_el = ed
                    composer_bounds = b
                    composer_signal = "resource_id_matches_composer"
                    break
            if composer_el is not None:
                break
        except Exception:
            pass

        # 3) pkg-specific exact rid
        try:
            el = d(resourceId=pkg_rid)
            if el.wait(timeout=0.05):
                b = (el.info or {}).get("bounds") or {}
                top = int(b.get("top", 0))
                if top >= bottom_y_min:
                    composer_el = el
                    composer_bounds = b
                    composer_signal = "resource_id_exact_composer_pkg"
                    break
        except Exception:
            pass

        # 4) className EditText in lower half
        try:
            for ed in d(className="android.widget.EditText").all():
                b = (ed.info or {}).get("bounds") or {}
                top = int(b.get("top", 0))
                if top >= bottom_y_min:
                    composer_el = ed
                    composer_bounds = b
                    composer_signal = "edittext_bottom_half_composer"
                    break
            if composer_el is not None:
                break
        except Exception:
            pass

        time.sleep(float(getattr(config, "DM_THREAD_POLL_S", 0.08)))

    if composer_el is None:
        return False, "", None, None

    # 5) hint/text/content-desc containing keywords
    try:
        info = composer_el.info or {}
        # uiautomator2 may expose hint via contentDescription depending on node.
        blob = " ".join(
            str(info.get(k, "") or "")
            for k in ("text", "contentDescription", "description", "resourceName")
        )
        try:
            t = composer_el.get_text() or ""
        except Exception:
            t = ""
        blob = f"{blob} {t}".lower()
        if any(kw.lower() in blob for kw in _dm_message_keyword_blobs()):
            if composer_signal:
                composer_signal = f"{composer_signal}+hint_message_keyword"
            else:
                composer_signal = "hint_message_keyword"
    except Exception:
        pass

    return True, composer_signal, composer_bounds, composer_el


def _dm_detect_history_signal(
    d: u2.Device, *, composer_bounds: dict[str, int] | None
) -> tuple[bool, bool, str]:
    """Best-effort history detection in DM thread."""
    # 1) resource-id containing message_container
    try:
        if d(resourceIdMatches=r".*:id/.*message_container.*").exists(timeout=0.06):
            return True, True, "resource_message_container"
    except Exception:
        pass

    # 2) resource-id exact direct_text_message_text_view (package-agnostic)
    try:
        if d(resourceIdMatches=r".*:id/direct_text_message_text_view").exists(timeout=0.06):
            return True, True, "resource_direct_text_message_text_view"
    except Exception:
        pass

    # 3) resource-id containing direct_text_message
    try:
        if d(resourceIdMatches=r".*:id/.*direct_text_message.*").exists(timeout=0.06):
            return True, True, "resource_direct_text_message"
    except Exception:
        pass

    # 4) recycler/list with messages above composer
    composer_top = None
    try:
        if composer_bounds:
            composer_top = int(composer_bounds.get("top"))
    except Exception:
        composer_top = None

    if composer_top is not None:
        # Keep it simple: if a list/recycler view exists and is mostly above the composer,
        # treat that as history visible.
        for cls in (
            "androidx.recyclerview.widget.RecyclerView",
            "android.widget.ListView",
        ):
            try:
                for el in d(className=cls).all():
                    b = (el.info or {}).get("bounds") or {}
                    top = int(b.get("top", 0))
                    bottom = int(b.get("bottom", 0))
                    if bottom <= composer_top and top < composer_top:
                        return True, True, "recycler_list_above_composer"
            except Exception:
                pass

    # 5) day/status texts
    for txt in ("Today", "Yesterday", "Vu", "Delivered", "Sent", "Envoyé", "Envoye"):
        try:
            if d(textContains=txt).exists(timeout=0.06):
                return True, True, "status_text_marker"
        except Exception:
            pass

    return False, False, ""


def detect_dm_thread_state(
    d: u2.Device, username: str, pkg: str | None = None
) -> tuple[str, dict[str, Any]]:
    """
    Detect DM thread state after opening Message:
    - empty_new_thread: composer visible, no history after short probe window
    - existing_thread: composer visible and history signal present
    Never returns unknown when composer is visible.
    """
    start = time.perf_counter()
    current_pkg = pkg or (d.app_current() or {}).get("package") or config.INSTAGRAM_PACKAGE
    # Snapshot fields requested by runner debugging.
    snap: dict[str, Any] = {
        "thread_state": "unknown",
        "composer_visible": False,
        "composer_signal": "",
        "has_history": False,
        "has_history_signal": False,
        "history_signal_type": "",
        "draft_only_fast": False,
        "package": current_pkg,
        "username": username,
    }

    composer_timeout_s = float(getattr(config, "DM_THREAD_DETECT_MAX_S", 2.5))
    composer_visible = False
    composer_signal = ""
    composer_bounds: dict[str, int] | None = None

    # Wait for composer to appear using multiple strategies.
    composer_visible, composer_signal, composer_bounds, _ = _dm_detect_composer_signal(
        d, current_pkg=current_pkg, timeout_s=composer_timeout_s
    )
    snap["composer_visible"] = bool(composer_visible)
    snap["composer_signal"] = str(composer_signal or "")

    if not composer_visible:
        return "unknown", snap

    # Restricted surfaces should stay as their own state.
    if _dm_restricted_surfaces(d):
        snap["thread_state"] = "restricted_account"
        log(
            "info",
            "dm_thread_state_detected",
            username=username,
            thread_state="restricted_account",
            dm_thread_detect_ms=round((time.perf_counter() - start) * 1000, 2),
        )
        return "restricted_account", snap

    log(
        "info",
        "dm_thread_composer_signal_seen",
        username=username,
        composer_visible=True,
        composer_signal=snap["composer_signal"],
    )

    has_history, has_hist_signal, hist_type = _dm_detect_history_signal(
        d, composer_bounds=composer_bounds
    )
    snap["has_history"] = bool(has_history)
    snap["has_history_signal"] = bool(has_hist_signal)
    snap["history_signal_type"] = str(hist_type or "")

    log(
        "info",
        "dm_thread_existing_history_signal",
        username=username,
        has_history=bool(has_history),
        history_signal_type=snap["history_signal_type"],
    )

    if has_history:
        snap["thread_state"] = "existing_thread"
        return "existing_thread", snap

    # No history yet: short probe window.
    snap["draft_only_fast"] = True
    probe_s = float(getattr(config, "DM_EXISTING_THREAD_PROBE_S", 0.8))
    poll_s = float(getattr(config, "DM_EXISTING_THREAD_POLL_S", 0.1))
    probe_deadline = time.monotonic() + probe_s
    thread_state = "empty_new_thread"

    while time.monotonic() < probe_deadline:
        has_history, has_hist_signal, hist_type = _dm_detect_history_signal(
            d, composer_bounds=composer_bounds
        )
        if has_history:
            thread_state = "existing_thread"
            snap["has_history"] = True
            snap["has_history_signal"] = True
            snap["history_signal_type"] = str(hist_type or "")
            snap["draft_only_fast"] = False
            break
        time.sleep(poll_s)

    snap["thread_state"] = thread_state
    return thread_state, snap


def open_dm_thread_from_profile(d: u2.Device, username: str) -> str:
    global _LAST_DM_THREAD_ATTEMPTED, _LAST_DM_THREAD_STATE, _LAST_DM_THREAD_CLASSIFY_SNAPSHOT
    _LAST_DM_THREAD_ATTEMPTED = True
    try:
        pkg = (d.app_current() or {}).get("package") or config.INSTAGRAM_PACKAGE
    except Exception:
        pkg = config.INSTAGRAM_PACKAGE
    _LAST_DM_THREAD_STATE = "unknown"

    t_open = time.perf_counter()
    button_signal = ""
    tapped = False
    for label, factory in (
        ("text:Message", lambda: d(text="Message")),
        ("textContains:Message", lambda: d(textContains="Message")),
        ("description:Message", lambda: d(descriptionContains="Message")),
    ):
        try:
            el = factory()
            if el.wait(timeout=0.4):
                el.click()
                tapped = True
                button_signal = label
                break
        except Exception:
            continue
    dm_open_click_ms = (time.perf_counter() - t_open) * 1000
    _perf["dm_open_click_ms"] = dm_open_click_ms

    if not tapped:
        log("error", "dm_message_button_missing", username=username)
        _LAST_DM_THREAD_STATE = "dm_not_available"
        return "dm_not_available"

    log(
        "info",
        "dm_thread_opened",
        username=username,
        button_signal=button_signal,
        dm_open_click_ms=round(dm_open_click_ms, 2),
    )

    time.sleep(float(getattr(config, "DM_THREAD_POST_OPEN_SETTLE_S", 0.45)))
    # detect_dm_thread_state (must be called after clicking Message)
    t_detect = time.perf_counter()
    thread_state, snap = detect_dm_thread_state(d, username, pkg=pkg)
    _perf["dm_thread_detect_ms"] = (time.perf_counter() - t_detect) * 1000
    _LAST_DM_THREAD_STATE = thread_state
    _LAST_DM_THREAD_CLASSIFY_SNAPSHOT = snap

    log(
        "info",
        "dm_thread_state_detected",
        username=username,
        thread_state=thread_state,
        dm_thread_detect_ms=round(float(_perf["dm_thread_detect_ms"]), 2),
    )
    return thread_state


def verify_dm_composer_safe(d: u2.Device, pkg: str | None = None) -> tuple[bool, str]:
    p = pkg or config.INSTAGRAM_PACKAGE
    if not verify_app_foreground(d, p):
        return False, "not_foreground"
    if is_lightweight_search_screen(d, p):
        return False, "on_search_not_dm_accepts"
    acc = float(getattr(config, "FAST_DM_COMPOSER_ACCEPT_S", 1.45))
    w0 = time.perf_counter()
    composer: Any | None = None
    while (time.perf_counter() - w0) < acc:
        composer = _dm_find_focus_composer(d)
        if composer is not None:
            return True, "ok"
        time.sleep(0.08)
    _emit_wait_event("dm_composer_wait", (time.perf_counter() - w0) * 1000)
    return False, "no_dm_composer"


def type_dm_draft_only(d: u2.Device, draft: str, pkg: str | None = None) -> tuple[bool, Any]:
    _ = pkg or config.INSTAGRAM_PACKAGE
    t0 = time.perf_counter()
    ed = _dm_find_focus_composer(d)
    if ed is None:
        return False, "no_composer"
    try:
        ed.click()
        time.sleep(0.05)
    except Exception:
        pass
    text = str(draft or "")

    def _read_composer_text() -> str:
        try:
            cur = _dm_find_focus_composer(d) or ed
            return str((cur.get_text() if cur is not None else "") or "")
        except Exception:
            return ""

    def _prefix20(s: str) -> str:
        return str(s or "")[:20]

    def _closeness(actual: str, expected: str) -> float:
        a = str(actual or "")
        e = str(expected or "")
        if not e:
            return 1.0
        # Prefix-oriented similarity for truncated FastIME behavior.
        n = min(len(a), len(e))
        i = 0
        while i < n and a[i] == e[i]:
            i += 1
        return float(i) / float(len(e))

    serial = get_device_serial(d)
    fast_ime = (getattr(config, "FAST_IME", "") or "").strip()
    type_meta: dict[str, Any] = {
        "method": "",
        "fastime_used": False,
        "set_text_used": False,
        "composer_visible": True,
        "expected_text_len": len(text),
        "actual_text_len": 0,
        "text_prefix_preview": "",
        "verify_will_continue": False,
        "fastime_partial_text": False,
        "fastime_truncated": False,
        "fallback_set_text_started": False,
        "fallback_set_text_ok": False,
        "fallback_set_text_failed": False,
        "actual_text_len_after_fastime": 0,
        "text_prefix_after_fastime": "",
        "actual_text_len_after_set_text": 0,
        "text_prefix_after_set_text": "",
    }

    if fast_ime and is_fast_ime_available(serial):
        cmd_ok, _, sw_ok, br_ok = run_fast_ime_input(serial, text, fast_ime_id=fast_ime)
        fastime_ok = bool(cmd_ok and (sw_ok or br_ok))
        type_meta["method"] = "fast_ime"
        type_meta["fastime_used"] = True
        if not fastime_ok:
            _perf["dm_draft_typing_ms"] = (time.perf_counter() - t0) * 1000
            return False, "fast_ime_failed"

        actual_fastime = _read_composer_text()
        type_meta["actual_text_len_after_fastime"] = len(actual_fastime)
        type_meta["text_prefix_after_fastime"] = _prefix20(actual_fastime)
        clos_fastime = _closeness(actual_fastime, text)
        truncated = len(actual_fastime) < len(text)
        sufficient_portion = clos_fastime >= 0.75
        partial_fastime = truncated or (not sufficient_portion)
        type_meta["fastime_truncated"] = bool(truncated)
        type_meta["fastime_partial_text"] = bool(partial_fastime)

        if partial_fastime:
            type_meta["fallback_set_text_started"] = True
            type_meta["set_text_used"] = True
            try:
                ed.set_text(text)
            except Exception as e:
                type_meta["fallback_set_text_failed"] = True
                type_meta["actual_text_len"] = len(actual_fastime)
                type_meta["text_prefix_preview"] = _prefix20(actual_fastime)
                _perf["dm_draft_typing_ms"] = (time.perf_counter() - t0) * 1000
                return False, {"reason": str(e), **type_meta}

            actual_set = _read_composer_text()
            type_meta["actual_text_len_after_set_text"] = len(actual_set)
            type_meta["text_prefix_after_set_text"] = _prefix20(actual_set)
            clos_set = _closeness(actual_set, text)
            if clos_set >= clos_fastime:
                type_meta["fallback_set_text_ok"] = True
                type_meta["method"] = "set_text"
                type_meta["actual_text_len"] = len(actual_set)
                type_meta["text_prefix_preview"] = _prefix20(actual_set)
                type_meta["verify_will_continue"] = True
                _perf["dm_draft_typing_ms"] = (time.perf_counter() - t0) * 1000
                return True, type_meta

            type_meta["fallback_set_text_failed"] = True
            type_meta["actual_text_len"] = len(actual_set)
            type_meta["text_prefix_preview"] = _prefix20(actual_set)
            _perf["dm_draft_typing_ms"] = (time.perf_counter() - t0) * 1000
            return False, {
                "reason": "set_text_not_better_than_fastime",
                **type_meta,
            }

        type_meta["actual_text_len"] = len(actual_fastime)
        type_meta["text_prefix_preview"] = _prefix20(actual_fastime)
        type_meta["verify_will_continue"] = True
        _perf["dm_draft_typing_ms"] = (time.perf_counter() - t0) * 1000
        return True, type_meta
    else:
        ok = False
        try:
            ed.set_text(text)
            ok = True
        except Exception as e:
            return False, str(e)
        actual_set = _read_composer_text()
        type_meta["method"] = "set_text"
        type_meta["set_text_used"] = True
        type_meta["actual_text_len_after_set_text"] = len(actual_set)
        type_meta["text_prefix_after_set_text"] = _prefix20(actual_set)
        type_meta["actual_text_len"] = len(actual_set)
        type_meta["text_prefix_preview"] = _prefix20(actual_set)
        type_meta["verify_will_continue"] = bool(ok)

    _perf["dm_draft_typing_ms"] = (time.perf_counter() - t0) * 1000
    if not ok:
        return False, "fast_ime_failed"
    return True, type_meta


def verify_dm_draft_text(d: u2.Device, expected: str) -> bool:
    t0 = time.perf_counter()
    ed = _dm_find_focus_composer(d)
    if ed is None:
        return False
    try:
        got = ed.get_text() or ""
    except Exception:
        return False
    _perf["dm_draft_verify_ms"] = (time.perf_counter() - t0) * 1000
    return got.strip() == str(expected).strip()


# --- Follow SAFE V1 (profile header; single tap; bounded verify) ---

_FOLLOW_RID_PATTERNS: tuple[str, ...] = (
    r".*:id/profile_header_follow_button",
    r".*:id/action_bar_button_follow",
    r".*:id/action_bar_follow_button",
    r".*:id/button_follow",
)

_FOLLOW_ACCEPT_TEXT: frozenset[str] = frozenset({"follow", "suivre"})

_FOLLOW_REJECT_TEXT: frozenset[str] = frozenset(
    {
        "following",
        "requested",
        "message",
        "contact",
        "add friend",
        "inviter",
        "invite",
        "suivi",
        "abonné",
        "abonnée",
        "demande envoyée",
        "en attente",
    }
)


def _follow_norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _follow_safe_info(el: Any) -> dict[str, Any]:
    try:
        return dict(el.info or {})
    except Exception:
        return {}


def _follow_bounds_center_y(info: dict[str, Any], screen_h: int) -> int | None:
    b = info.get("bounds") or {}
    try:
        t = int(b.get("top", 0))
        bot = int(b.get("bottom", 0))
    except Exception:
        return None
    if bot <= t:
        return None
    cy = (t + bot) // 2
    if cy < 0 or cy > int(screen_h * 1.05):
        return None
    return cy


def _follow_ui_state_snapshot(d: u2.Device) -> str:
    """Coarse header state for logging and post-tap verification."""
    try:
        h = int(d.window_size()[1])
    except Exception:
        h = 1920
    y_lim = int(h * 0.52)

    def _exists_near_top(text: str) -> bool:
        sel = d(text=text)
        if not sel.exists(timeout=0.05):
            return False
        cy = _follow_bounds_center_y(_follow_safe_info(sel), h)
        return cy is not None and cy <= y_lim

    for lab in ("Requested", "Demande envoyée", "En attente"):
        if _exists_near_top(lab):
            return "requested"
    for lab in ("Following", "Abonné", "Abonnée", "Suivi"):
        if _exists_near_top(lab):
            return "following"
    if d(text="Follow").exists(timeout=0.06) or d(text="Suivre").exists(timeout=0.06):
        return "follow"
    return "unknown"


def _follow_rid_suspicious(rid: str) -> bool:
    r = _follow_norm(rid)
    if not r:
        return False
    if re.search(
        r"(following|requested|message|messenger|contact|friend|invite|inviter)(?!.*follow)",
        r,
    ):
        return True
    if re.search(r"_following|_requested|_message\b", r):
        return True
    return False


def _follow_score_candidate(
    d: u2.Device,
    el: Any,
    *,
    username: str,
) -> tuple[int, dict[str, Any]] | tuple[None, dict[str, Any]]:
    inf = _follow_safe_info(el)
    rid = str(inf.get("resourceName") or "")
    txt = _follow_norm(inf.get("text"))
    desc = _follow_norm(inf.get("contentDescription"))
    try:
        h = int(d.window_size()[1])
    except Exception:
        h = 1920
    cy = _follow_bounds_center_y(inf, h)
    meta: dict[str, Any] = {
        "resource_id": rid,
        "text": inf.get("text"),
        "content_description": inf.get("contentDescription"),
        "clickable": bool(inf.get("clickable")),
        "enabled": bool(inf.get("enabled")),
        "center_y": cy,
        "screen_h": h,
        "target_username": username,
    }
    if cy is None:
        return None, {**meta, "reject": "no_bounds"}
    # Header band: avoid bottom-sheet / list rows
    if cy < int(h * 0.07) or cy > int(h * 0.44):
        return None, {**meta, "reject": "off_header_band"}
    if _follow_rid_suspicious(rid):
        return None, {**meta, "reject": "rid_blocked"}
    if txt in _FOLLOW_REJECT_TEXT or desc in _FOLLOW_REJECT_TEXT:
        return None, {**meta, "reject": "text_blocked"}
    blob = f"{txt} {desc}"
    if any(b in blob for b in ("following", "requested", "message", "contact", "friend", "invite")):
        return None, {**meta, "reject": "blob_blocked"}
    rid_l = _follow_norm(rid)
    accept_text = (txt in _FOLLOW_ACCEPT_TEXT) or (desc in _FOLLOW_ACCEPT_TEXT)
    rid_followish = ("follow" in rid_l) and not _follow_rid_suspicious(rid)
    if not accept_text and not rid_followish:
        return None, {**meta, "reject": "not_follow_control"}
    if not inf.get("enabled", True):
        return None, {**meta, "reject": "disabled"}
    cn = str(inf.get("className") or "")
    if not inf.get("clickable") and "Button" not in cn:
        return None, {**meta, "reject": "not_clickable"}
    score = 0
    if rid_followish:
        score += 120
    if txt in _FOLLOW_ACCEPT_TEXT:
        score += 80
    elif desc in _FOLLOW_ACCEPT_TEXT:
        score += 60
    if inf.get("clickable"):
        score += 40
    ideal = int(h * 0.20)
    score += max(0, 35 - abs(cy - ideal) // 25)
    return score, meta


def _follow_collect_elements(d: u2.Device) -> list[Any]:
    seen: set[tuple[Any, ...]] = set()
    out: list[Any] = []

    def _add(el: Any) -> None:
        sig = (
            _follow_safe_info(el).get("resourceName"),
            (_follow_safe_info(el).get("bounds") or {}).get("top"),
            (_follow_safe_info(el).get("bounds") or {}).get("left"),
        )
        if sig in seen:
            return
        seen.add(sig)
        out.append(el)

    for pat in _FOLLOW_RID_PATTERNS:
        try:
            for el in d(resourceIdMatches=pat).all():
                _add(el)
        except Exception:
            continue
    for lab in ("Follow", "Suivre"):
        try:
            for el in d(text=lab).all():
                _add(el)
        except Exception:
            continue
    return out


def wait_for_follow_button_safe(
    d: u2.Device,
    username: str,
    pkg: str | None = None,
) -> tuple[Any | None, dict[str, Any]]:
    """
    Wait for a single safe Follow / Suivre control. Rejects Following, Requested, Message, etc.
    Returns (element, meta). meta includes 'events' list of (event_name, payload) for Supabase mirroring.
    """
    pkg = pkg or getattr(config, "INSTAGRAM_PACKAGE", "") or ""
    events: list[tuple[str, dict[str, Any]]] = []
    deadline = time.monotonic() + float(getattr(config, "FOLLOW_BUTTON_WAIT_S", 4.0))
    poll_s = float(getattr(config, "POLL_INTERVAL_S", 0.08) or 0.08)
    last_ui = "unknown"

    def _record(event: str, payload: dict[str, Any]) -> None:
        events.append((event, dict(payload)))
        log("info", event, **payload)

    while time.monotonic() < deadline:
        if not verify_app_foreground(d, pkg):
            time.sleep(poll_s)
            continue
        last_ui = _follow_ui_state_snapshot(d)
        if last_ui in ("following", "requested"):
            return None, {
                "outcome": "already_connected",
                "ui_state": last_ui,
                "already_following": True,
                "events": events,
            }

        candidates = _follow_collect_elements(d)
        best: tuple[int, Any, dict[str, Any]] | None = None
        for el in candidates:
            scored = _follow_score_candidate(d, el, username=username)
            score, meta = scored
            _record(
                "follow_button_candidate_seen",
                {
                    **meta,
                    "target_username": username,
                    "score": score,
                    "navigation_state": "profile",
                },
            )
            if score is None:
                _record(
                    "follow_button_candidate_rejected",
                    {
                        "target_username": username,
                        "reason": meta.get("reject", "rejected"),
                        "resource_id": meta.get("resource_id"),
                        "text": meta.get("text"),
                        "navigation_state": "profile",
                    },
                )
                continue
            if best is None or int(score) > best[0]:
                best = (int(score), el, meta)

        if best is not None:
            _sc, el_pick, meta_pick = best
            _record(
                "follow_button_candidate_selected",
                {
                    "target_username": username,
                    "score": _sc,
                    "resource_id": meta_pick.get("resource_id"),
                    "text": meta_pick.get("text"),
                    "center_y": meta_pick.get("center_y"),
                    "navigation_state": "profile",
                },
            )
            return el_pick, {
                "outcome": "ready",
                "pick_meta": meta_pick,
                "events": events,
            }
        time.sleep(poll_s)

    _record(
        "follow_button_not_found",
        {
            "target_username": username,
            "last_ui_state": last_ui,
            "wait_s": round(float(getattr(config, "FOLLOW_BUTTON_WAIT_S", 4.0)), 3),
            "navigation_state": "profile",
        },
    )
    return None, {"outcome": "not_found", "last_ui_state": last_ui, "events": events}


def perform_follow_safe(
    d: u2.Device,
    username: str,
    pkg: str | None = None,
) -> dict[str, Any]:
    """
    Single-tap follow with bounded verification (Following or Requested). No retries / multi-tap.
    """
    pkg = pkg or getattr(config, "INSTAGRAM_PACKAGE", "") or ""
    events: list[tuple[str, dict[str, Any]]] = []
    t_all = time.perf_counter()

    def _record(event: str, payload: dict[str, Any]) -> None:
        events.append((event, dict(payload)))
        log("info", event, **payload)

    state_before = _follow_ui_state_snapshot(d)
    _record(
        "follow_started",
        {
            "target_username": username,
            "follow_state_before": state_before,
            "follow_state_after": None,
            "verify_attempts": 0,
            "navigation_state": "profile",
            "timings_ms": {"phase": 0.0},
        },
    )

    btn, meta = wait_for_follow_button_safe(d, username, pkg)
    events.extend(meta.get("events") or [])

    if meta.get("already_following"):
        _record(
            "follow_completed",
            {
                "target_username": username,
                "follow_state_before": meta.get("ui_state", state_before),
                "follow_state_after": meta.get("ui_state"),
                "verify_attempts": 0,
                "navigation_state": "profile",
                "skipped_tap": True,
                "timings_ms": {"total": round((time.perf_counter() - t_all) * 1000, 2)},
            },
        )
        return {
            "ok": True,
            "failure_code": None,
            "tapped": False,
            "skipped_tap": True,
            "follow_state_before": meta.get("ui_state", state_before),
            "follow_state_after": meta.get("ui_state"),
            "verify_attempts": 0,
            "events": events,
        }

    if btn is None:
        return {
            "ok": False,
            "failure_code": 33,
            "tapped": False,
            "follow_state_before": state_before,
            "follow_state_after": meta.get("last_ui_state"),
            "verify_attempts": 0,
            "events": events,
        }

    try:
        btn.click()
    except Exception as e:
        _record(
            "follow_verify_failed",
            {
                "target_username": username,
                "follow_state_before": state_before,
                "follow_state_after": _follow_ui_state_snapshot(d),
                "verify_attempts": 0,
                "navigation_state": "profile",
                "error": str(e),
                "timings_ms": {"total": round((time.perf_counter() - t_all) * 1000, 2)},
            },
        )
        _record(
            "follow_completed",
            {
                "target_username": username,
                "ok": False,
                "failure_code": 34,
                "navigation_state": "profile",
                "timings_ms": {"total": round((time.perf_counter() - t_all) * 1000, 2)},
            },
        )
        return {
            "ok": False,
            "failure_code": 34,
            "tapped": True,
            "follow_state_before": state_before,
            "follow_state_after": _follow_ui_state_snapshot(d),
            "verify_attempts": 0,
            "events": events,
        }

    _record(
        "follow_tap_sent",
        {
            "target_username": username,
            "follow_state_before": state_before,
            "follow_state_after": None,
            "verify_attempts": 0,
            "navigation_state": "profile",
            "timings_ms": {"to_tap_ms": round((time.perf_counter() - t_all) * 1000, 2)},
        },
    )

    verify_timeout_ms = int(getattr(config, "FOLLOW_VERIFY_TIMEOUT_MS", 4000) or 4000)
    verify_deadline = time.monotonic() + verify_timeout_ms / 1000.0
    poll_v = 0.12
    attempts = 0
    state_after = state_before
    while time.monotonic() < verify_deadline:
        attempts += 1
        state_after = _follow_ui_state_snapshot(d)
        if state_after in ("following", "requested"):
            _record(
                "follow_verify_success",
                {
                    "target_username": username,
                    "follow_state_before": state_before,
                    "follow_state_after": state_after,
                    "verify_attempts": attempts,
                    "navigation_state": "profile",
                    "timings_ms": {
                        "verify_phase_ms": round((time.perf_counter() - t_all) * 1000, 2)
                    },
                },
            )
            _record(
                "follow_completed",
                {
                    "target_username": username,
                    "follow_state_before": state_before,
                    "follow_state_after": state_after,
                    "verify_attempts": attempts,
                    "navigation_state": "profile",
                    "timings_ms": {"total": round((time.perf_counter() - t_all) * 1000, 2)},
                },
            )
            return {
                "ok": True,
                "failure_code": None,
                "tapped": True,
                "follow_state_before": state_before,
                "follow_state_after": state_after,
                "verify_attempts": attempts,
                "events": events,
            }
        time.sleep(poll_v)

    _record(
        "follow_verify_failed",
        {
            "target_username": username,
            "follow_state_before": state_before,
            "follow_state_after": state_after,
            "verify_attempts": attempts,
            "navigation_state": "profile",
            "timings_ms": {"total": round((time.perf_counter() - t_all) * 1000, 2)},
        },
    )
    _record(
        "follow_completed",
        {
            "target_username": username,
            "ok": False,
            "failure_code": 34,
            "follow_state_before": state_before,
            "follow_state_after": state_after,
            "verify_attempts": attempts,
            "navigation_state": "profile",
        },
    )
    return {
        "ok": False,
        "failure_code": 34,
        "tapped": True,
        "follow_state_before": state_before,
        "follow_state_after": state_after,
        "verify_attempts": attempts,
        "events": events,
    }


# --- Followers list engine V1 (source profile → followers list → follower profile) ---

_FOLLOWERS_HANDLE_RE = re.compile(r"^[a-zA-Z0-9._]{1,30}$")
_FOLLOWERS_TITLE_TEXTS = frozenset(
    {
        "Followers",
        "Abonnés",
        "Abonné(e)s",
        "Seguidores",
        "Подписчики",
    }
)
# Substrings for action_bar_title when exact locale string differs slightly.
_FOLLOWERS_TITLE_SUBSTRINGS = (
    "followers",
    "abonnés",
    "seguidores",
    "подписчик",
)
# UI / chrome strings that must not count as list usernames (relaxed detection).
_USERNAME_NOT_HANDLE_TEXTS = frozenset(
    {
        "follow",
        "following",
        "followers",
        "follow back",
        "message",
        "messages",
        "posts",
        "post",
        "edit",
        "share",
        "more",
        "about",
        "blocked",
        "requested",
        "abonnés",
        "abonné",
        "abonné(e)s",
        "suivre",
        "suivis",
        "publications",
        "publication",
        "seguidores",
        "siguiendo",
        "suggestions",
        "sort",
        "close",
        "copier",
        "copy",
    }
)


def _looks_like_instagram_username(text: str) -> bool:
    """
    Heuristic handle: 3–30 chars, [A-Za-z0-9._] only, no spaces, not common UI labels.
    """
    raw = (text or "").strip().lstrip("@")
    if not raw or " " in raw or "\n" in raw:
        return False
    if len(raw) < 3 or len(raw) > 30:
        return False
    if not re.fullmatch(r"[a-zA-Z0-9._]+", raw):
        return False
    low = raw.lower()
    if low in _USERNAME_NOT_HANDLE_TEXTS:
        return False
    if low in {x.lower() for x in _FOLLOWERS_TITLE_TEXTS}:
        return False
    for sub in _FOLLOWERS_TITLE_SUBSTRINGS:
        if low == sub:
            return False
    return True


# Official IG header block for followers (tap via center coordinates; see _tap_profile_followers_stat).
PROFILE_HEADER_FOLLOWERS_STACKED_FAMILIAR_RID = (
    "com.instagram.android:id/profile_header_followers_stacked_familiar"
)

# Until immediate post-tap screenshot/XML/hierarchy capture completes, block programmatic list scroll/swipe.
_FOLLOWERS_POST_TAP_IMMEDIATE_CAPTURE_DONE: bool = False

# Count of detect_followers_list_screen calls after followers stat tap (reset per open attempt).
_FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT: int = 0

# While True: block programmatic scroll/swipe (see _followers_abort_scroll_if_post_tap_lock).
POST_TAP_FOLLOWERS_DETECTION_IN_PROGRESS: bool = False

# Followers list engine session (reset each open_followers_list_from_profile).
_FOLLOWERS_LAST_OPEN_DETECTION_METHOD: str | None = None
_FOLLOWERS_VISUAL_XML_RECOVERY_ATTEMPTED: bool = False
_FOLLOWERS_VISUAL_XML_STALE_EXHAUSTED: bool = False
_FOLLOWERS_LAST_ITER_CANDIDATE_COUNT: int = 0
_FOLLOWERS_VISUAL_HAD_NONEMPTY_CANDIDATE_ROWS: bool = False

# Set when followers engine must exit without follow/scroll (stale XML after visual open).
_FOLLOWERS_ENGINE_STOP_REASON: str | None = None

FOLLOWERS_ENGINE_XML_STALE_EXIT_REASONS = frozenset(
    {
        "visual_open_xml_empty",
        "followers_xml_still_stale_after_force_refresh",
    }
)


def get_followers_engine_stop_reason() -> str | None:
    return _FOLLOWERS_ENGINE_STOP_REASON


def _followers_reset_followers_list_session_state() -> None:
    global _FOLLOWERS_LAST_OPEN_DETECTION_METHOD
    global _FOLLOWERS_VISUAL_XML_RECOVERY_ATTEMPTED
    global _FOLLOWERS_VISUAL_XML_STALE_EXHAUSTED
    global _FOLLOWERS_LAST_ITER_CANDIDATE_COUNT
    global _FOLLOWERS_VISUAL_HAD_NONEMPTY_CANDIDATE_ROWS
    global _FOLLOWERS_ENGINE_STOP_REASON
    _FOLLOWERS_LAST_OPEN_DETECTION_METHOD = None
    _FOLLOWERS_VISUAL_XML_RECOVERY_ATTEMPTED = False
    _FOLLOWERS_VISUAL_XML_STALE_EXHAUSTED = False
    _FOLLOWERS_LAST_ITER_CANDIDATE_COUNT = 0
    _FOLLOWERS_VISUAL_HAD_NONEMPTY_CANDIDATE_ROWS = False
    _FOLLOWERS_ENGINE_STOP_REASON = None


def _followers_set_last_open_detection_method(method: str | None) -> None:
    global _FOLLOWERS_LAST_OPEN_DETECTION_METHOD
    _FOLLOWERS_LAST_OPEN_DETECTION_METHOD = method


def _followers_set_post_tap_detection_lock(active: bool) -> None:
    global POST_TAP_FOLLOWERS_DETECTION_IN_PROGRESS
    POST_TAP_FOLLOWERS_DETECTION_IN_PROGRESS = bool(active)


def _followers_enable_post_tap_detection_lock(source_profile_username: str) -> None:
    _followers_set_post_tap_detection_lock(True)
    log(
        "info",
        "followers_post_tap_detection_lock_enabled",
        source_profile_username=source_profile_username,
    )


def _followers_release_post_tap_detection_lock(source_profile_username: str) -> None:
    _followers_set_post_tap_detection_lock(False)
    log(
        "info",
        "followers_post_tap_detection_lock_released",
        source_profile_username=source_profile_username,
    )


def _followers_pkg_activity_for_scroll_log(d: u2.Device) -> tuple[Any, Any]:
    try:
        meta = _followers_current_pkg_activity(d)
        return meta.get("current_package"), meta.get("current_activity")
    except Exception:
        return None, None


def _followers_log_scroll_or_swipe_about_to_run(
    d: u2.Device,
    *,
    source_function: str,
    reason: str,
) -> None:
    pkg, act = _followers_pkg_activity_for_scroll_log(d)
    log(
        "info",
        "followers_scroll_or_swipe_about_to_run",
        source_function=source_function,
        reason=reason,
        post_tap_lock=POST_TAP_FOLLOWERS_DETECTION_IN_PROGRESS,
        current_activity=act,
        current_package=pkg,
    )


def _followers_abort_scroll_if_post_tap_lock(source: str) -> bool:
    """If True, caller must not scroll/swipe (post-tap followers detection still active)."""
    if POST_TAP_FOLLOWERS_DETECTION_IN_PROGRESS:
        log("warning", "followers_post_tap_scroll_prevented", source=source)
        return True
    return False


def _followers_reset_post_tap_capture_gate() -> None:
    global _FOLLOWERS_POST_TAP_IMMEDIATE_CAPTURE_DONE, _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT
    _FOLLOWERS_POST_TAP_IMMEDIATE_CAPTURE_DONE = False
    _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT = 0


def _followers_post_tap_capture_gate_ok() -> bool:
    return _FOLLOWERS_POST_TAP_IMMEDIATE_CAPTURE_DONE


def _followers_mark_post_tap_immediate_capture_done() -> None:
    global _FOLLOWERS_POST_TAP_IMMEDIATE_CAPTURE_DONE
    _FOLLOWERS_POST_TAP_IMMEDIATE_CAPTURE_DONE = True


def _followers_bounds_center(b: dict[str, Any]) -> tuple[int, int] | None:
    try:
        left, top, right, bottom = (
            int(b.get("left", 0)),
            int(b.get("top", 0)),
            int(b.get("right", 0)),
            int(b.get("bottom", 0)),
        )
    except Exception:
        return None
    if right <= left or bottom <= top:
        return None
    return (left + right) // 2, (top + bottom) // 2


_STAT_NUMERIC_RE = re.compile(r"^[\d\s,\.kKmM\+]+$")


def _followers_reject_posts_label(text: str) -> bool:
    s = (text or "").lower()
    return "post" in s or "publication" in s


def _followers_reject_following_column_label(text: str) -> bool:
    """Avoid tapping the 'Following' / suivis column (not Followers)."""
    s = (text or "").strip().lower()
    if not s:
        return False
    if "followers" in s and "following" not in s.replace("followers", ""):
        return False
    if s == "following" or s.startswith("following"):
        return True
    if "following" in s and "follower" not in s:
        return True
    if s in ("suivis", "abonnements") and "abonné" not in s:
        return True
    return False


def _followers_label_match(text: str) -> bool:
    """True if this TextView is a Followers column label (EN/FR), not Posts/Following."""
    s = (text or "").strip().lower()
    if not s:
        return False
    if _followers_reject_following_column_label(text):
        return False
    if _followers_reject_posts_label(text):
        return False
    if "abonnés" in s or "abonné" in s or "abonne" in s:
        return True
    if "followers" in s:
        return True
    if s == "follower":
        return True
    if "follower" in s and "following" not in s:
        return True
    return False


def _followers_merge_bounds(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    try:
        return {
            "left": min(int(a.get("left", 0)), int(b.get("left", 0))),
            "top": min(int(a.get("top", 0)), int(b.get("top", 0))),
            "right": max(int(a.get("right", 0)), int(b.get("right", 0))),
            "bottom": max(int(a.get("bottom", 0)), int(b.get("bottom", 0))),
        }
    except Exception:
        return dict(a or b or {})


def _followers_debug_capture(d: u2.Device, stem: str) -> dict[str, Any]:
    """Save screenshot + hierarchy for followers-list debugging."""
    _ensure_debug_dirs()
    out: dict[str, Any] = {"screenshot_path": None, "xml_path": None}
    shot = _SCREENSHOTS_DIR / f"{stem}.png"
    xml_path = _XML_DIR / f"{stem}.xml"
    try:
        screenshot(d, str(shot))
        out["screenshot_path"] = str(shot)
    except Exception as e:
        out["screenshot_error"] = str(e)
    try:
        try:
            hier = d.dump_hierarchy(compressed=False)
        except TypeError:
            hier = d.dump_hierarchy()
        xml_path.write_text(hier, encoding="utf-8")
        _bump_xml_fetch()
        out["xml_path"] = str(xml_path)
    except Exception as e:
        out["xml_error"] = str(e)
    return out


def _followers_current_pkg_activity(d: u2.Device) -> dict[str, Any]:
    meta: dict[str, Any] = {"current_package": None, "current_activity": None}
    try:
        cur = d.app_current() or {}
        meta["current_package"] = cur.get("package")
        meta["current_activity"] = cur.get("activity")
    except Exception as e:
        meta["app_current_error"] = str(e)
    return meta


def _guess_profile_screen(d: u2.Device, pkg: str, username_hint: str = "") -> str:
    if _try_profile_signals_once(d, username_hint or "", pkg):
        return "likely_profile"
    try:
        if d(resourceIdMatches=r".*:id/profile_header.*").exists(timeout=0.08):
            return "profile_header_rid"
        if d(resourceIdMatches=r".*:id/action_bar_title.*").exists(timeout=0.06):
            return "action_bar_title"
    except Exception:
        pass
    return "unknown"


def _collect_profile_stats_band_texts(d: u2.Device, w: int, h: int) -> list[dict[str, Any]]:
    """TextViews in the profile header stats row (posts / followers / following).

    Vertical window stops below the stats strip (~22% h) so long bio TextViews
    (e.g. containing the word \"followers\") are not mixed into the band.
    """
    y_lo, y_hi = int(h * 0.08), int(h * 0.22)
    x_lo, x_hi = int(w * 0.04), int(w * 0.96)
    rows: list[dict[str, Any]] = []
    try:
        for el in d(className="android.widget.TextView").all():
            inf = _follow_safe_info(el)
            b = inf.get("bounds") or {}
            cy = _followers_bounds_center(b)
            if cy is None:
                continue
            cx, cyy = cy
            if cyy < y_lo or cyy > y_hi or cx < x_lo or cx > x_hi:
                continue
            txt = (inf.get("text") or "").strip()
            if not txt:
                continue
            rows.append(
                {
                    "text": txt,
                    "cx": cx,
                    "cyy": cyy,
                    "bounds": dict(b),
                    "resource_id": inf.get("resourceName"),
                }
            )
    except Exception:
        pass
    rows.sort(key=lambda r: (r["cyy"], r["cx"]))
    return rows


def _followers_collect_profile_text_dump(d: u2.Device, w: int, h: int) -> list[dict[str, Any]]:
    """TextViews in the profile header / stats strip (scaled band ~150–650px @ 2400h)."""
    y_lo, y_hi = int(h * 150 // 2400), int(h * 650 // 2400)
    out: list[dict[str, Any]] = []
    try:
        for el in d(className="android.widget.TextView").all():
            inf = _follow_safe_info(el)
            b = inf.get("bounds") or {}
            cy = _followers_bounds_center(b)
            if cy is None:
                continue
            _, cyy = cy
            if not (y_lo <= cyy <= y_hi):
                continue
            out.append(
                {
                    "text": (inf.get("text") or "").strip(),
                    "bounds": dict(b),
                    "className": str(inf.get("className") or ""),
                    "resourceId": str(inf.get("resourceName") or ""),
                }
            )
    except Exception:
        pass
    out.sort(
        key=lambda r: (
            int(r["bounds"].get("top", 0)),
            int(r["bounds"].get("left", 0)),
        )
    )
    return out


def _followers_stat_strip_y_for_text_labels(h: int) -> tuple[int, int]:
    """Vertical band where the posts/followers/following labels sit (excludes bio)."""
    return int(h * 0.13), int(h * 0.24)


def _followers_stats_dump_effectively_empty(dump: list[dict[str, Any]]) -> bool:
    """True when there is no stats strip text yet (empty list or only blank TextViews)."""
    if not dump:
        return True
    return not any((str(r.get("text") or "").strip()) for r in dump)


def _followers_profile_rescan_stats_if_empty(
    d: u2.Device, pkg_meta: dict[str, Any]
) -> dict[str, Any]:
    """
    If stats TextViews are invisible (empty dump), nudge the list with a light upward swipe
    and rescan. Logs include current package/activity.
    """
    try:
        w, h = d.window_size()
    except Exception:
        w, h = 1080, 1920
    dump = _followers_collect_profile_text_dump(d, w, h)
    vis = list(dump)
    rescan_done = False
    if _followers_stats_dump_effectively_empty(dump):
        if _followers_abort_scroll_if_post_tap_lock("_followers_profile_rescan_stats_if_empty"):
            return {
                "followers_stat_text_dump": dump,
                "profile_stats_visible": vis,
                "profile_rescan_swiped": False,
            }
        if not _followers_post_tap_capture_gate_ok():
            log(
                "info",
                "followers_profile_rescan_swipe_skipped",
                reason="post_tap_immediate_capture_not_done",
            )
            return {
                "followers_stat_text_dump": dump,
                "profile_stats_visible": vis,
                "profile_rescan_swiped": False,
            }
        log(
            "info",
            "followers_profile_rescan_after_swipe",
            profile_stats_visible=vis,
            followers_stat_text_dump=dump,
            current_activity=pkg_meta.get("current_activity"),
            current_package=pkg_meta.get("current_package"),
        )
        try:
            xmid = int(w * 0.5)
            sy, ey = int(h * 0.42), int(h * 0.36)
            _followers_log_scroll_or_swipe_about_to_run(
                d,
                source_function="_followers_profile_rescan_stats_if_empty",
                reason="light_upward_nudge_stats_strip",
            )
            d.swipe(xmid, sy, xmid, ey, 0.08)
        except Exception:
            pass
        time.sleep(0.35)
        pkg_meta.update(_followers_current_pkg_activity(d))
        dump = _followers_collect_profile_text_dump(d, w, h)
        vis = list(dump)
        rescan_done = True
        log(
            "info",
            "followers_profile_rescan_result",
            profile_stats_visible=vis,
            followers_stat_text_dump=dump,
            current_activity=pkg_meta.get("current_activity"),
            current_package=pkg_meta.get("current_package"),
        )
    return {
        "followers_stat_text_dump": dump,
        "profile_stats_visible": vis,
        "profile_rescan_swiped": rescan_done,
    }


def _followers_profile_post_failure_debug_swipe(
    d: u2.Device,
    pkg_meta: dict[str, Any],
    *,
    followers_open_failure_logged: bool = False,
) -> None:
    """
    After followers_list_open_failed was logged: optional light swipe + rescan logs (no retry tap).
    Must not run during POST_TAP_FOLLOWERS_DETECTION_IN_PROGRESS.
    """
    if not followers_open_failure_logged:
        log(
            "warning",
            "followers_profile_post_failure_debug_swipe_skipped",
            reason="followers_list_open_failed_not_logged_yet",
        )
        return
    if POST_TAP_FOLLOWERS_DETECTION_IN_PROGRESS:
        log(
            "warning",
            "followers_post_tap_scroll_prevented",
            source="_followers_profile_post_failure_debug_swipe",
        )
        return
    log("info", "followers_profile_post_failure_debug_swipe", phase="post_failure_debug")
    try:
        w, h = d.window_size()
    except Exception:
        w, h = 1080, 1920
    try:
        dump0 = _followers_collect_profile_text_dump(d, w, h)
        vis0 = list(dump0)
        log(
            "info",
            "followers_profile_rescan_after_swipe",
            profile_stats_visible=vis0,
            followers_stat_text_dump=dump0,
            current_activity=pkg_meta.get("current_activity"),
            current_package=pkg_meta.get("current_package"),
            phase="post_failure_debug_before_swipe",
        )
    except Exception:
        pass
    try:
        xmid = int(w * 0.5)
        sy, ey = int(h * 0.42), int(h * 0.36)
        _followers_log_scroll_or_swipe_about_to_run(
            d,
            source_function="_followers_profile_post_failure_debug_swipe",
            reason="post_failure_debug_profile_rescan",
        )
        d.swipe(xmid, sy, xmid, ey, 0.08)
    except Exception:
        pass
    time.sleep(0.35)
    try:
        pkg_meta.update(_followers_current_pkg_activity(d))
    except Exception:
        pass
    try:
        dump1 = _followers_collect_profile_text_dump(d, w, h)
        vis1 = list(dump1)
        log(
            "info",
            "followers_profile_rescan_result",
            profile_stats_visible=vis1,
            followers_stat_text_dump=dump1,
            current_activity=pkg_meta.get("current_activity"),
            current_package=pkg_meta.get("current_package"),
            phase="post_failure_debug_after_swipe",
        )
    except Exception:
        pass


def _tap_profile_followers_stat(
    d: u2.Device,
    *,
    pre_scan: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """
    Tap the followers stat on a source profile. Returns (ok, diagnostics dict).
    Order: exact RID profile_header_followers_stacked_familiar → text followers/abonnés in stats
    zone → other resource-ids / middle column. Coordinate fallback is separate.
    """
    diag: dict[str, Any] = {
        "followers_stat_found": False,
        "followers_stat_text": None,
        "followers_stat_bounds": None,
        "tap_x": None,
        "tap_y": None,
        "tap_method": None,
        "profile_stats_visible": [],
        "stats_band_texts": [],
        "followers_stat_text_detected": None,
        "followers_stat_text_bounds": None,
        "followers_stat_tap_x": None,
        "followers_stat_tap_y": None,
        "followers_stat_tap_source": None,
        "followers_stat_text_dump": [],
        "followers_stat_coordinate_retry": False,
        "profile_rescan_swiped": False,
        "debug_before_scan_screenshot_path": None,
        "debug_before_scan_xml_path": None,
        "debug_after_fallback_screenshot_path": None,
        "debug_after_fallback_xml_path": None,
        "followers_exact_rid_used": False,
    }
    try:
        w, h = d.window_size()
    except Exception:
        w, h = 1080, 1920

    y_lo, y_hi = int(h * 0.10), int(h * 0.46)
    x_max = int(w * 0.96)
    ideal_cx = int(w * 0.60)
    ly_lo, ly_hi = _followers_stat_strip_y_for_text_labels(h)

    if pre_scan:
        diag["followers_stat_text_dump"] = list(pre_scan.get("followers_stat_text_dump") or [])
        diag["profile_stats_visible"] = list(pre_scan.get("profile_stats_visible") or [])
        diag["profile_rescan_swiped"] = bool(pre_scan.get("profile_rescan_swiped"))
        if pre_scan.get("debug_before_scan_screenshot_path"):
            diag["debug_before_scan_screenshot_path"] = pre_scan.get(
                "debug_before_scan_screenshot_path"
            )
        if pre_scan.get("debug_before_scan_xml_path"):
            diag["debug_before_scan_xml_path"] = pre_scan.get("debug_before_scan_xml_path")
    else:
        text_dump = _followers_collect_profile_text_dump(d, w, h)
        diag["followers_stat_text_dump"] = text_dump
        diag["profile_stats_visible"] = list(text_dump)

    log(
        "info",
        "followers_stat_text_dump",
        text_view_count=len(diag["followers_stat_text_dump"]),
        profile_stats_visible_sample=(diag["followers_stat_text_dump"] or [])[:25],
        profile_rescan_swiped=diag.get("profile_rescan_swiped"),
    )

    # Priority: exact Instagram RID — always use center coordinates (d.click), not UiObject.click().
    try:
        sel_exact = d(resourceId=PROFILE_HEADER_FOLLOWERS_STACKED_FAMILIAR_RID)
        if sel_exact.exists(timeout=0.38):
            for el in sel_exact.all():
                inf = _follow_safe_info(el)
                b = dict(inf.get("bounds") or {})
                try:
                    left = int(b.get("left", 0))
                    right = int(b.get("right", 0))
                    top = int(b.get("top", 0))
                    bottom = int(b.get("bottom", 0))
                except Exception:
                    continue
                if right <= left or bottom <= top:
                    continue
                cy_chk = _followers_bounds_center(b)
                if cy_chk is None:
                    continue
                _, cyy = cy_chk
                if cyy < y_lo or cyy > y_hi or left > x_max:
                    continue
                cx = (left + right) // 2
                cy = (top + bottom) // 2
                rid_seen = str(inf.get("resourceName") or PROFILE_HEADER_FOLLOWERS_STACKED_FAMILIAR_RID)
                log(
                    "info",
                    "followers_exact_rid_found",
                    resource_id=rid_seen,
                    bounds={"left": left, "right": right, "top": top, "bottom": bottom},
                    clickable=inf.get("clickable"),
                    enabled=inf.get("enabled"),
                )
                log(
                    "info",
                    "followers_exact_rid_click",
                    center_x=cx,
                    center_y=cy,
                    bounds={"left": left, "right": right, "top": top, "bottom": bottom},
                )
                try:
                    d.click(cx, cy)
                except Exception as e:
                    diag["tap_error"] = str(e)
                    return False, diag
                tbounds = dict(b)
                diag["followers_stat_found"] = True
                diag["followers_exact_rid_used"] = True
                diag["tap_method"] = "resource_id_profile_header_followers_stacked_familiar_exact"
                diag["followers_stat_tap_source"] = "exact_rid_coordinate_tap"
                diag["followers_stat_text_detected"] = "profile_header_followers_stacked_familiar"
                diag["followers_stat_text_bounds"] = tbounds
                diag["followers_stat_tap_x"] = cx
                diag["followers_stat_tap_y"] = cy
                diag["tap_x"], diag["tap_y"] = cx, cy
                diag["followers_stat_text"] = "profile_header_followers_stacked_familiar"
                diag["followers_stat_bounds"] = tbounds
                log(
                    "info",
                    "followers_stat_tap_selected",
                    followers_stat_text_detected=diag["followers_stat_text_detected"],
                    followers_stat_text_bounds=tbounds,
                    followers_stat_tap_x=cx,
                    followers_stat_tap_y=cy,
                    followers_stat_tap_source=diag["followers_stat_tap_source"],
                    tap_method=diag["tap_method"],
                )
                return True, diag
    except Exception:
        pass

    band = _collect_profile_stats_band_texts(d, w, h)
    diag["stats_band_texts"] = [(r.get("text") or "").strip() for r in band[:40]]

    numeric_row = [
        r
        for r in band
        if _STAT_NUMERIC_RE.match((r.get("text") or "").strip())
        and not _followers_reject_posts_label(r.get("text") or "")
        and not _followers_reject_following_column_label(r.get("text") or "")
    ]
    numeric_row.sort(key=lambda r: r["cx"])

    candidates: list[dict[str, Any]] = []

    # 0) Instagram familiar layout: clickable followers stacked container (see hierarchy XML).
    try:
        stacked = d(resourceIdMatches=r".*:id/profile_header_followers_stacked_familiar$")
        if stacked.exists(timeout=0.28):
            for el in stacked.all():
                inf = _follow_safe_info(el)
                b = inf.get("bounds") or {}
                c = _followers_bounds_center(b)
                if c is None:
                    continue
                _cx, cyy = c
                if cyy < y_lo or cyy > y_hi or int(b.get("left", 0)) > x_max:
                    continue
                rw = int(b.get("right", 0)) - int(b.get("left", 0))
                candidates.append(
                    {
                        "score": 12000 + min(rw, 500),
                        "cx": _cx,
                        "cy": cyy,
                        "rw": max(rw, 1),
                        "el": el,
                        "method": "resource_id_followers_stacked_familiar",
                        "tap_source": "parent_block",
                        "detected": "profile_header_followers_stacked_familiar",
                        "tbounds": dict(b),
                    }
                )
                break
    except Exception:
        pass

    # 1) Text: followers / follower / abonnés… — prefer label; merge with count above in same column.
    try:
        for el in d(className="android.widget.TextView").all():
            inf = _follow_safe_info(el)
            raw_txt = (inf.get("text") or "").strip()
            if not raw_txt or not _followers_label_match(raw_txt):
                continue
            b = inf.get("bounds") or {}
            cy = _followers_bounds_center(b)
            if cy is None:
                continue
            _cx, cyy = cy
            if cyy < y_lo or cyy > y_hi or int(b.get("left", 0)) > x_max:
                continue
            rw = int(b.get("right", 0)) - int(b.get("left", 0))
            if rw < 8:
                continue
            low = raw_txt.lower()
            if "following" in low and "follower" not in low:
                continue
            rid_l = str(inf.get("resourceName") or "")
            if (
                "profile_header_familiar_followers" not in rid_l
                and "followers_stacked" not in rid_l
            ):
                if not (ly_lo <= cyy <= ly_hi):
                    continue

            pair: dict[str, Any] | None = None
            for r in band:
                rt = (r.get("text") or "").strip()
                if not _STAT_NUMERIC_RE.match(rt):
                    continue
                if _followers_reject_posts_label(rt) or _followers_reject_following_column_label(rt):
                    continue
                rcx, rcyy = int(r["cx"]), int(r["cyy"])
                if abs(rcx - _cx) > int(w * 0.09):
                    continue
                if rcyy >= cyy - 4:
                    continue
                if (cyy - rcyy) > int(h * 0.14):
                    continue
                pair = r
                break

            if pair is not None:
                mb = _followers_merge_bounds(b, pair.get("bounds") or {})
                mc = _followers_bounds_center(mb)
                if mc is None:
                    continue
                tcx, tcy = mc
                bw = int(mb.get("right", 0)) - int(mb.get("left", 0))
                score = (
                    8500
                    - abs(tcx - ideal_cx)
                    + min(bw, 400)
                )
                candidates.append(
                    {
                        "score": score,
                        "cx": tcx,
                        "cy": tcy,
                        "rw": max(bw, 1),
                        "el": el,
                        "method": "text_label_with_count_block",
                        "tap_source": "parent_block",
                        "detected": f"{(pair.get('text') or '').strip()}|{raw_txt}",
                        "tbounds": dict(mb),
                    }
                )
            else:
                score = 6200 - abs(_cx - ideal_cx) + min(rw, 320)
                candidates.append(
                    {
                        "score": score,
                        "cx": _cx,
                        "cy": cyy,
                        "rw": rw,
                        "el": el,
                        "method": "text_followers_label",
                        "tap_source": "text_element",
                        "detected": raw_txt,
                        "tbounds": dict(b),
                    }
                )
    except Exception:
        pass

    # 2) Resource-id hints (counts; stacked handled in phase 0).
    rid_patterns = (
        r".*:id/.*follower.*count.*",
        r".*:id/.*followers.*count.*",
        r".*:id/.*stacked.*follower.*",
    )
    for pat in rid_patterns:
        try:
            for el in d(resourceIdMatches=pat).all():
                inf = _follow_safe_info(el)
                b = inf.get("bounds") or {}
                cy = _followers_bounds_center(b)
                if cy is None:
                    continue
                _cx, cyy = cy
                if cyy < y_lo or cyy > y_hi or int(b.get("left", 0)) > x_max:
                    continue
                rw = int(b.get("right", 0)) - int(b.get("left", 0))
                score = 5000 + rw * 100 - abs(_cx - ideal_cx) // 2
                try:
                    det_txt = (el.get_text() or "").strip()
                except Exception:
                    det_txt = ""
                candidates.append(
                    {
                        "score": score,
                        "cx": _cx,
                        "cy": cyy,
                        "rw": max(rw, 1),
                        "el": el,
                        "method": f"resource_id:{pat}",
                        "tap_source": "text_element",
                        "detected": det_txt or pat,
                        "tbounds": dict(b),
                    }
                )
        except Exception:
            continue

    # 3) Middle numeric column when three stats visible.
    if len(numeric_row) >= 3:
        mid = numeric_row[len(numeric_row) // 2]
        b = mid.get("bounds") or {}
        c = _followers_bounds_center(b)
        if c:
            rw = int(b.get("right", 0)) - int(b.get("left", 0))
            candidates.append(
                {
                    "score": 2500 - abs(c[0] - ideal_cx),
                    "cx": c[0],
                    "cy": c[1],
                    "rw": max(rw, 1),
                    "el": None,
                    "method": "middle_stat_column",
                    "tap_source": "text_element",
                    "detected": (mid.get("text") or "").strip(),
                    "tbounds": dict(b),
                }
            )

    if not candidates:
        return False, diag

    best_c = max(candidates, key=lambda x: int(x["score"]))
    cx, cy = int(best_c["cx"]), int(best_c["cy"])
    el_pick = best_c["el"]
    method = str(best_c["method"])
    tap_src = str(best_c["tap_source"])
    detected = str(best_c["detected"])
    tbounds: dict[str, Any] = dict(best_c["tbounds"] or {})

    diag["followers_stat_found"] = True
    diag["tap_method"] = method
    diag["followers_stat_text_detected"] = detected
    diag["followers_stat_text_bounds"] = tbounds
    diag["followers_stat_tap_x"] = cx
    diag["followers_stat_tap_y"] = cy
    diag["followers_stat_tap_source"] = tap_src
    diag["tap_x"], diag["tap_y"] = cx, cy
    diag["followers_stat_text"] = detected
    diag["followers_stat_bounds"] = tbounds

    log(
        "info",
        "followers_stat_tap_selected",
        followers_stat_text_detected=detected,
        followers_stat_text_bounds=tbounds,
        followers_stat_tap_x=cx,
        followers_stat_tap_y=cy,
        followers_stat_tap_source=tap_src,
        tap_method=method,
    )

    try:
        if method == "resource_id_followers_stacked_familiar":
            d.click(cx, cy)
        elif el_pick is not None and tap_src == "parent_block":
            try:
                el_pick.click()
            except Exception:
                d.click(cx, cy)
        elif tap_src == "text_element" and el_pick is not None:
            try:
                el_pick.click()
            except Exception:
                d.click(cx, cy)
        else:
            d.click(cx, cy)
        return True, diag
    except Exception:
        try:
            d.click(cx, cy)
            diag["tap_note"] = "coordinate_after_click_failed"
            return True, diag
        except Exception as e:
            diag["tap_error"] = str(e)
            return False, diag


def _tap_followers_coord_fallback(
    d: u2.Device, diag: dict[str, Any]
) -> tuple[bool, dict[str, Any]]:
    """
    Last resort when text/RID fails: ~60% x 18.5% (1080x2400 → ~648, 444).
    """
    diag["followers_exact_rid_used"] = False
    try:
        w, h = d.window_size()
    except Exception:
        w, h = 1080, 1920
    band = _collect_profile_stats_band_texts(d, w, h)
    cx = int(w * 0.60)
    cy = int(h * 0.185)
    y_src = "ratio_w0.60_h0.185"
    diag["followers_stat_coordinate_retry"] = True
    diag["tap_method"] = "coordinate_fallback_middle_stats_column"
    diag["stats_band_center_y_source"] = y_src
    diag["tap_x"], diag["tap_y"] = cx, cy
    diag["followers_coord_fallback"] = True
    diag["followers_stat_tap_source"] = "coordinate_middle_stats_column"
    diag["followers_stat_text_detected"] = None
    diag["followers_stat_text_bounds"] = None
    diag["followers_stat_tap_x"] = cx
    diag["followers_stat_tap_y"] = cy
    diag["followers_stat_text"] = None
    diag["followers_stat_bounds"] = None
    if not diag.get("stats_band_texts"):
        diag["stats_band_texts"] = [(r.get("text") or "").strip() for r in band[:40]]
    log(
        "info",
        "followers_stat_tap_selected",
        _include_null_fields=True,
        followers_stat_text_detected=None,
        followers_stat_text_bounds=None,
        followers_stat_tap_x=cx,
        followers_stat_tap_y=cy,
        followers_stat_tap_source="coordinate_middle_stats_column",
        tap_method=diag["tap_method"],
    )
    try:
        d.click(cx, cy)
        cap_af = _followers_debug_capture(d, "followers_profile_after_fallback_tap")
        diag["debug_after_fallback_screenshot_path"] = cap_af.get("screenshot_path")
        diag["debug_after_fallback_xml_path"] = cap_af.get("xml_path")
        return True, diag
    except Exception as e:
        diag["fallback_tap_error"] = str(e)
        return False, diag


def _followers_profile_tabs_visible(d: u2.Device) -> bool:
    """
    True when typical Instagram profile tab chrome is present (grid / reels / tagged strip).
    Used to infer followers list when tabs disappear but list rows remain.
    """
    checks: list[Callable[[], object]] = [
        lambda: d(resourceIdMatches=r".*:id/profile_tabs_container"),
        lambda: d(resourceIdMatches=r".*:id/profile_tab_layout"),
        lambda: d(resourceIdMatches=r".*:id/profile_tab_icon_view"),
        lambda: d(resourceIdMatches=r".*:id/media_tab"),
        lambda: d(descriptionContains="Grid"),
        lambda: d(descriptionContains="Reels"),
        lambda: d(descriptionContains="Tagged"),
        lambda: d(descriptionMatches=r"(?i).*tagged.*"),
        lambda: d(text="Posts"),
        lambda: d(textMatches=r"(?i).*posts.*"),
        lambda: d(resourceIdMatches=r".*:id/profile_tabs[^_].*"),
    ]
    for fact in checks:
        try:
            el = fact()
            if el.exists(timeout=0.06):
                return True
        except Exception:
            continue
    return False


def _followers_stacked_central_textview_run(d: u2.Device, w: int, h: int) -> int:
    """Longest run of vertically stacked TextView centers in the central list band (condition C)."""
    y_lo, y_hi = int(h * 0.28), int(h * 0.88)
    x_lo, x_hi = int(w * 0.18), int(w * 0.82)
    centers: list[int] = []
    try:
        for el in d(className="android.widget.TextView").all():
            try:
                inf = el.info
                t = (inf.get("text") or "").strip()
                if not t:
                    continue
                b = inf.get("bounds") or {}
                cx = (int(b.get("left", 0)) + int(b.get("right", 0))) // 2
                cy = (int(b.get("top", 0)) + int(b.get("bottom", 0))) // 2
                if not (y_lo <= cy <= y_hi and x_lo <= cx <= x_hi):
                    continue
                centers.append(cy)
            except Exception:
                continue
    except Exception:
        return 0
    if len(centers) < 2:
        return len(centers)
    centers.sort()
    max_run = 1
    run = 1
    for i in range(1, len(centers)):
        dy = centers[i] - centers[i - 1]
        if 6 <= dy <= 140:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 1
    return max_run


def detect_followers_list_screen(
    d: u2.Device,
    *,
    source_profile_username: str = "",
) -> dict[str, Any]:
    """
    Heuristic followers list: strict title + list chrome, OR relaxed list/content signals
    (RecyclerView / scrollable / usernames / stacked rows) without requiring Followers title.
    """
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "com.instagram.android")
    cur_pkg: str | None = None
    cur_act: str | None = None
    try:
        cur = d.app_current() or {}
        cur_pkg = cur.get("package")
        cur_act = cur.get("activity")
    except Exception:
        pass

    out: dict[str, Any] = {
        "is_followers_list": False,
        "title_match": False,
        "action_bar_title": "",
        "recycler_present": False,
        "listview_present": False,
        "scrollable_present": False,
        "scrollable_count": 0,
        "profile_tabs_absent": False,
        "visible_usernames_sample": [],
        "signals": [],
        "visible_header_texts": [],
        "candidate_username_count": 0,
        "stacked_central_textview_run": 0,
        "relaxed_rules_matched": [],
        "strict_list_open": False,
        "relaxed_list_open": False,
        "current_package": cur_pkg,
        "current_activity": cur_act,
        "current_screen_guess": "unknown",
    }
    try:
        w, h = d.window_size()
    except Exception:
        w, h = 1080, 1920

    try:
        out["current_screen_guess"] = _guess_profile_screen(d, pkg, source_profile_username)
    except Exception:
        pass

    try:
        ab = d(resourceIdMatches=r".*:id/action_bar_title.*")
        if ab.exists(timeout=0.12):
            out["action_bar_title"] = str(ab.get_text() or "").strip()
    except Exception:
        pass

    # Toolbar / top strip texts for diagnostics
    try:
        y_hdr = int(h * 0.14)
        hdr_texts: list[str] = []
        for el in d(className="android.widget.TextView").all():
            try:
                inf = el.info
                b = inf.get("bounds") or {}
                if int(b.get("bottom", 0)) > y_hdr:
                    continue
                t = (inf.get("text") or "").strip()
                if t and t not in hdr_texts:
                    hdr_texts.append(t)
            except Exception:
                continue
        out["visible_header_texts"] = hdr_texts[:20]
    except Exception:
        pass

    title_ok = False
    for t in _FOLLOWERS_TITLE_TEXTS:
        try:
            if d(text=t).exists(timeout=0.06):
                title_ok = True
                out["signals"].append(f"title_text:{t}")
                break
        except Exception:
            continue
    if not title_ok and out["action_bar_title"]:
        ab_l = out["action_bar_title"].strip().lower()
        for cand in _FOLLOWERS_TITLE_TEXTS:
            if ab_l == cand.lower():
                title_ok = True
                out["signals"].append("title_action_bar")
                break
        if not title_ok:
            if any(sub in ab_l for sub in _FOLLOWERS_TITLE_SUBSTRINGS):
                title_ok = True
                out["signals"].append("title_action_bar_substring")
    out["title_match"] = title_ok

    try:
        rvs = d(classNameMatches=".*RecyclerView.*").all()
        out["recycler_present"] = len(rvs) > 0
        if out["recycler_present"]:
            out["signals"].append(f"recyclerview_count:{len(rvs)}")
    except Exception:
        pass

    try:
        lvs = d(className="android.widget.ListView").all()
        out["listview_present"] = len(lvs) > 0
        if out["listview_present"]:
            out["signals"].append(f"listview_count:{len(lvs)}")
    except Exception:
        pass

    try:
        sc = d(scrollable=True).all()
        out["scrollable_count"] = len(sc)
        out["scrollable_present"] = len(sc) > 0
        if out["scrollable_present"]:
            out["signals"].append(f"scrollable_count:{len(sc)}")
    except Exception:
        pass

    list_chrome = bool(
        out["recycler_present"] or out["listview_present"] or out["scrollable_present"]
    )

    try:
        out["profile_tabs_absent"] = not _followers_profile_tabs_visible(d)
    except Exception:
        out["profile_tabs_absent"] = False

    sample: list[str] = []
    handle_keys: set[str] = set()
    try:
        y_min = int(h * 0.08)
        src_key = _normalize_handle(source_profile_username or "")
        for el in d(className="android.widget.TextView").all():
            try:
                inf = el.info
                raw_t = (inf.get("text") or "").strip().lstrip("@")
                if not _looks_like_instagram_username(raw_t):
                    continue
                b = inf.get("bounds") or {}
                cy = (int(b.get("top", 0)) + int(b.get("bottom", 0))) // 2
                if cy < y_min or cy > int(h * 0.96):
                    continue
                hk = _normalize_handle(raw_t)
                if hk == src_key:
                    continue
                handle_keys.add(hk)
                if len(sample) < 12:
                    sample.append(raw_t)
            except Exception:
                continue
    except Exception:
        pass
    out["visible_usernames_sample"] = sample
    out["candidate_username_count"] = len(handle_keys)

    stacked_run = _followers_stacked_central_textview_run(d, w, h)
    out["stacked_central_textview_run"] = stacked_run

    # --- Relaxed OR conditions (no Followers title required) ---
    cond_a = list_chrome and out["candidate_username_count"] >= 3
    cond_b = len(sample) >= 2
    cond_c = bool(out["scrollable_present"] and stacked_run >= 5)
    ab_norm = _normalize_handle(out["action_bar_title"] or "")
    src_norm = _normalize_handle(source_profile_username or "")
    cond_d = bool(
        src_norm
        and ab_norm == src_norm
        and out["scrollable_present"]
        and (out["candidate_username_count"] >= 1 or len(sample) >= 1)
    )
    handles_visible_ok = bool(
        out["candidate_username_count"] >= 1 or len(sample) >= 1
    )
    cond_e = bool(
        out["profile_tabs_absent"]
        and list_chrome
        and handles_visible_ok
    )

    relaxed_reasons: list[str] = []
    if cond_a:
        relaxed_reasons.append("A_recycler_or_scrollable_and_3plus_handles")
        log(
            "info",
            "followers_list_detect_recycler_match",
            action_bar_title=out["action_bar_title"],
            recycler_present=out["recycler_present"],
            listview_present=out.get("listview_present"),
            scrollable_present=out["scrollable_present"],
            scrollable_count=out["scrollable_count"],
            candidate_username_count=out["candidate_username_count"],
            visible_usernames_sample=out["visible_usernames_sample"],
            visible_header_texts=out["visible_header_texts"],
            current_activity=out["current_activity"],
            current_package=out["current_package"],
            current_screen_guess=out["current_screen_guess"],
        )
    if cond_b:
        relaxed_reasons.append("B_multi_plausible_username_sample")
        log(
            "info",
            "followers_list_detect_username_match",
            action_bar_title=out["action_bar_title"],
            recycler_present=out["recycler_present"],
            listview_present=out.get("listview_present"),
            scrollable_present=out["scrollable_present"],
            scrollable_count=out["scrollable_count"],
            candidate_username_count=out["candidate_username_count"],
            visible_usernames_sample=out["visible_usernames_sample"],
            visible_header_texts=out["visible_header_texts"],
            current_activity=out["current_activity"],
            current_package=out["current_package"],
            current_screen_guess=out["current_screen_guess"],
        )
    if cond_c:
        relaxed_reasons.append("C_scrollable_and_stacked_central_textviews")
        log(
            "info",
            "followers_list_detect_scrollable_match",
            action_bar_title=out["action_bar_title"],
            recycler_present=out["recycler_present"],
            listview_present=out.get("listview_present"),
            scrollable_present=out["scrollable_present"],
            scrollable_count=out["scrollable_count"],
            stacked_central_textview_run=stacked_run,
            candidate_username_count=out["candidate_username_count"],
            visible_usernames_sample=out["visible_usernames_sample"],
            visible_header_texts=out["visible_header_texts"],
            current_activity=out["current_activity"],
            current_package=out["current_package"],
            current_screen_guess=out["current_screen_guess"],
        )
    if cond_d:
        relaxed_reasons.append("D_action_bar_is_source_profile_with_list")
        log(
            "info",
            "followers_list_detect_relaxed_match",
            reason="D_title_equals_source_profile",
            action_bar_title=out["action_bar_title"],
            source_profile_username=source_profile_username,
            recycler_present=out["recycler_present"],
            listview_present=out.get("listview_present"),
            scrollable_present=out["scrollable_present"],
            scrollable_count=out["scrollable_count"],
            candidate_username_count=out["candidate_username_count"],
            visible_usernames_sample=out["visible_usernames_sample"],
            visible_header_texts=out["visible_header_texts"],
            current_activity=out["current_activity"],
            current_package=out["current_package"],
            current_screen_guess=out["current_screen_guess"],
        )
    if cond_e:
        relaxed_reasons.append(
            "E_profile_tabs_absent_with_list_chrome_and_plausible_handles"
        )
        log(
            "info",
            "followers_list_detect_profile_tabs_absent_match",
            profile_tabs_absent=out["profile_tabs_absent"],
            action_bar_title=out["action_bar_title"],
            source_profile_username=source_profile_username,
            recycler_present=out["recycler_present"],
            listview_present=out.get("listview_present"),
            scrollable_present=out["scrollable_present"],
            scrollable_count=out["scrollable_count"],
            candidate_username_count=out["candidate_username_count"],
            visible_usernames_sample=out["visible_usernames_sample"],
            visible_header_texts=out["visible_header_texts"],
            current_activity=out["current_activity"],
            current_package=out["current_package"],
            current_screen_guess=out["current_screen_guess"],
        )

    relaxed_hit = cond_a or cond_b or cond_c or cond_d or cond_e
    out["relaxed_rules_matched"] = relaxed_reasons
    out["relaxed_list_open"] = relaxed_hit

    strict_hit = bool(title_ok and list_chrome)
    out["strict_list_open"] = strict_hit

    out["is_followers_list"] = bool(relaxed_hit or strict_hit)
    if out["is_followers_list"]:
        out["open_detection_method"] = "xml"
        if relaxed_hit:
            out["signals"].append(f"relaxed:{','.join(relaxed_reasons)}")
        if strict_hit:
            out["signals"].append("strict:title_and_list_chrome")
    else:
        out["open_detection_method"] = None

    log(
        "info",
        "followers_list_detect_debug",
        is_followers_list=out["is_followers_list"],
        title_match=out["title_match"],
        strict_list_open=out["strict_list_open"],
        relaxed_list_open=out["relaxed_list_open"],
        relaxed_rules_matched=out["relaxed_rules_matched"],
        action_bar_title=out["action_bar_title"],
        recycler_present=out["recycler_present"],
        listview_present=out["listview_present"],
        scrollable_present=out["scrollable_present"],
        scrollable_count=out["scrollable_count"],
        candidate_username_count=out["candidate_username_count"],
        visible_usernames_sample=out["visible_usernames_sample"],
        visible_header_texts=out["visible_header_texts"],
        stacked_central_textview_run=out["stacked_central_textview_run"],
        profile_tabs_absent=out.get("profile_tabs_absent"),
        current_activity=out["current_activity"],
        current_package=out["current_package"],
        current_screen_guess=out["current_screen_guess"],
    )
    return out


def _ig_follow_button_blue_pixel(r: int, g: int, b: int) -> bool:
    """Heuristic: Instagram Follow pill blues (no OCR)."""
    if b < 105:
        return False
    if r < 120 and b > r + 40 and b >= g - 20:
        return True
    if r < 160 and b > r + 35 and b > g + 8:
        return True
    return False


def _visual_downscale_rgb(im: Any, max_w: int = 400) -> Any:
    from PIL import Image

    w, h = im.size
    if w <= max_w:
        return im.convert("RGB")
    nh = max(1, int(h * max_w / w))
    return im.convert("RGB").resize((max_w, nh), Image.Resampling.BILINEAR)


def _visual_count_right_column_blue_bands(im_rgb: Any) -> int:
    """Repeated blue regions on the right edge (Follow buttons column)."""
    w, h = im_rgb.size
    x0 = int(w * 0.70)
    x1 = w - 1
    y0 = int(h * 0.10)
    y1 = int(h * 0.90)
    if x1 <= x0 or y1 <= y0:
        return 0
    roi = im_rgb.crop((x0, y0, x1 + 1, y1 + 1))
    wroi, hroi = roi.size
    flat = list(roi.getdata())
    row_scores: list[float] = []
    for yy in range(hroi):
        blue_n = 0
        off = yy * wroi
        for xx in range(wroi):
            r, g, b = flat[off + xx]
            if _ig_follow_button_blue_pixel(int(r), int(g), int(b)):
                blue_n += 1
        row_scores.append(blue_n / max(wroi, 1))
    smoothed: list[float] = []
    for i in range(len(row_scores)):
        a = row_scores[max(0, i - 1)] + row_scores[i] + row_scores[min(len(row_scores) - 1, i + 1)]
        smoothed.append(a / 3.0)
    thr = 0.028
    bands = 0
    i = 0
    while i < len(smoothed):
        if smoothed[i] >= thr:
            start = i
            while i < len(smoothed) and smoothed[i] >= thr * 0.55:
                i += 1
            height = i - start
            if 2 <= height <= max(90, hroi // 6):
                bands += 1
        else:
            i += 1
    return bands


def _visual_detect_search_strip(im_rgb: Any) -> bool:
    w, h = im_rgb.size
    x0, x1 = int(w * 0.08), int(w * 0.92)
    y0, y1 = int(h * 0.045), int(h * 0.135)
    roi = im_rgb.crop((x0, y0, x1 + 1, y1 + 1))
    flat_bright = 0
    n = 0
    for r, g, b in roi.getdata():
        n += 1
        lum = (int(r) + int(g) + int(b)) / 3
        spread = max(int(r), int(g), int(b)) - min(int(r), int(g), int(b))
        if lum > 200 and spread < 45:
            flat_bright += 1
    return n > 0 and (flat_bright / n) > 0.16


def _visual_count_left_column_row_bands(im_rgb: Any) -> int:
    """Avatar / row structure: elevated luminance variance in left strip by band."""
    w, h = im_rgb.size
    x0, x1 = int(w * 0.03), int(w * 0.24)
    y0, y1 = int(h * 0.12), int(h * 0.88)
    roi = im_rgb.crop((x0, y0, x1 + 1, y1 + 1))
    wroi, hroi = roi.size
    px = list(roi.getdata())
    band_h = max(12, hroi // 22)
    bands = 0
    yy = 0
    while yy + band_h <= hroi:
        lums: list[float] = []
        for row in range(yy, yy + band_h):
            off = row * wroi
            for xx in range(wroi):
                r, g, b = px[off + xx]
                lums.append((int(r) + int(g) + int(b)) / 3)
        if lums:
            mu = sum(lums) / len(lums)
            var = sum((x - mu) ** 2 for x in lums) / len(lums)
            if var > 380:
                bands += 1
        yy += band_h
    return bands


def _visual_header_dark_text_hint(im_rgb: Any) -> bool:
    w, h = im_rgb.size
    x0, x1 = int(w * 0.10), int(w * 0.90)
    y0, y1 = int(h * 0.055), int(h * 0.12)
    roi = im_rgb.crop((x0, y0, x1 + 1, y1 + 1))
    dark = 0
    px = list(roi.getdata())
    n = len(px)
    for r, g, b in px:
        if (int(r) + int(g) + int(b)) / 3 < 100:
            dark += 1
    return n > 0 and (dark / n) > 0.010


def _visual_bottom_strip_tabs_heuristic(im_rgb: Any, follow_bands: int) -> bool:
    """
    True when bottom strip looks unlike a 3-icon profile tab bar (very rough).
    """
    if follow_bands >= 4:
        return True
    w, h = im_rgb.size
    x0, x1 = int(w * 0.06), int(w * 0.94)
    y0, y1 = int(h * 0.78), int(h * 0.97)
    roi = im_rgb.crop((x0, y0, x1 + 1, y1 + 1))
    lums = [(int(r) + int(g) + int(b)) / 3 for r, g, b in roi.getdata()]
    if not lums:
        return False
    mu = sum(lums) / len(lums)
    var = sum((x - mu) ** 2 for x in lums) / len(lums)
    return var < 2200 or mu > 245


def _visual_collect_follow_row_y_spans(im_rgb: Any) -> list[tuple[int, int, float]]:
    """Vertical spans in image Y where right-column blue (Follow) density peaks."""
    w, h = im_rgb.size
    x0 = int(w * 0.68)
    x1 = w - 1
    y0 = int(h * 0.11)
    y1 = int(h * 0.90)
    if x1 <= x0 or y1 <= y0:
        return []
    roi = im_rgb.crop((x0, y0, x1 + 1, y1 + 1))
    wroi, hroi = roi.size
    flat = list(roi.getdata())
    row_scores: list[float] = []
    for yy in range(hroi):
        blue_n = 0
        off = yy * wroi
        for xx in range(wroi):
            r, g, b = flat[off + xx]
            if _ig_follow_button_blue_pixel(int(r), int(g), int(b)):
                blue_n += 1
        row_scores.append(blue_n / max(wroi, 1))
    smoothed: list[float] = []
    for i in range(len(row_scores)):
        a = row_scores[max(0, i - 1)] + row_scores[i] + row_scores[min(len(row_scores) - 1, i + 1)]
        smoothed.append(a / 3.0)
    thr = 0.028
    spans: list[tuple[int, int, float]] = []
    i = 0
    while i < len(smoothed):
        if smoothed[i] >= thr:
            start = i
            peak = smoothed[i]
            while i < len(smoothed) and smoothed[i] >= thr * 0.55:
                peak = max(peak, smoothed[i])
                i += 1
            height = i - start
            if 2 <= height <= max(100, hroi // 5):
                yt = y0 + start
                yb = y0 + i - 1
                spans.append((yt, yb, float(peak)))
        else:
            i += 1
    return spans


def _visual_tight_blue_bounds(
    im_rgb: Any,
    *,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> dict[str, int] | None:
    if right <= left or bottom <= top:
        return None
    crop = im_rgb.crop((left, top, right + 1, bottom + 1))
    cw, ch = crop.size
    flat = list(crop.getdata())
    minx, miny, maxx, maxy = 10**9, 10**9, -1, -1
    for yy in range(ch):
        off = yy * cw
        for xx in range(cw):
            r, g, b = flat[off + xx]
            if _ig_follow_button_blue_pixel(int(r), int(g), int(b)):
                if xx < minx:
                    minx = xx
                if yy < miny:
                    miny = yy
                if xx > maxx:
                    maxx = xx
                if yy > maxy:
                    maxy = yy
    if maxx < 0:
        return None
    return {
        "left": left + minx,
        "top": top + miny,
        "right": left + maxx + 1,
        "bottom": top + maxy + 1,
    }


def _visual_row_left_content_variance(
    im_rgb: Any, row_top: int, row_bottom: int
) -> float:
    w, h = im_rgb.size
    xl, xr = int(w * 0.03), int(w * 0.24)
    yt = max(0, row_top)
    yb = min(h - 1, row_bottom)
    if yb <= yt or xr <= xl:
        return 0.0
    roi = im_rgb.crop((xl, yt, xr + 1, yb + 1))
    lums: list[float] = []
    for r, g, b in roi.getdata():
        lums.append((int(r) + int(g) + int(b)) / 3)
    if not lums:
        return 0.0
    mu = sum(lums) / len(lums)
    return sum((x - mu) ** 2 for x in lums) / len(lums)


def _scale_bounds_to_original(
    bd: dict[str, int], *, aw: int, ah: int, orig_w: int, orig_h: int
) -> dict[str, int]:
    sx = orig_w / max(aw, 1)
    sy = orig_h / max(ah, 1)
    return {
        "left": int(bd["left"] * sx),
        "top": int(bd["top"] * sy),
        "right": int(bd["right"] * sx),
        "bottom": int(bd["bottom"] * sy),
    }


def followers_visual_candidate_diagnostic(
    d: u2.Device,
    *,
    screenshot_path: str | None = None,
    source_profile_username: str | None = None,
) -> dict[str, Any]:
    """
    Read-only: infer follower list row layout (avatar / text / Follow) from screenshot pixels.
    No UiAutomator queries beyond optional path; no tap, scroll, or follow.
    """
    out: dict[str, Any] = {
        "visual_candidate_rows": [],
        "visual_candidate_count": 0,
        "visual_follow_button_count": 0,
        "visual_confidence": 0.0,
        "screenshot_path": "",
        "source_profile_username": source_profile_username or "",
        "diagnostic_error": None,
    }
    path = screenshot_path
    if not path:
        out["diagnostic_error"] = "no_screenshot_path"
        return out
    out["screenshot_path"] = path
    try:
        from PIL import Image

        im_orig = Image.open(path)
        orig_w, orig_h = im_orig.size
        im_rgb = _visual_downscale_rgb(im_orig, max_w=480)
    except Exception as e:
        out["diagnostic_error"] = f"pil_failed:{e}"
        return out

    aw, ah = im_rgb.size
    log(
        "info",
        "followers_visual_candidate_diagnostic_started",
        screenshot_path=path,
        source_profile_username=source_profile_username,
        analysis_size=(aw, ah),
        original_size=(orig_w, orig_h),
    )

    spans = _visual_collect_follow_row_y_spans(im_rgb)
    out["visual_follow_button_count"] = len(spans)
    rows_out: list[dict[str, Any]] = []
    confidences: list[float] = []

    w, h = aw, ah
    for idx, (yt, yb, peak) in enumerate(spans):
        pad = max(4, (yb - yt + 1) // 3)
        row_top = max(int(h * 0.10), yt - pad)
        row_bottom = min(int(h * 0.93), yb + pad)
        approx_row_bounds = {
            "left": int(w * 0.02),
            "top": row_top,
            "right": int(w * 0.98),
            "bottom": row_bottom,
        }
        approx_avatar_bounds = {
            "left": int(w * 0.03),
            "top": row_top,
            "right": int(w * 0.20),
            "bottom": row_bottom,
        }
        approx_text_area_bounds = {
            "left": int(w * 0.20),
            "top": row_top,
            "right": int(w * 0.66),
            "bottom": row_bottom,
        }
        fb_left, fb_right = int(w * 0.66), w - 2
        tight = _visual_tight_blue_bounds(
            im_rgb,
            left=fb_left,
            top=yt,
            right=fb_right,
            bottom=yb,
        )
        if tight is None:
            approx_follow_button_bounds = {
                "left": fb_left,
                "top": yt,
                "right": fb_right,
                "bottom": yb,
            }
            fb_score = peak
        else:
            approx_follow_button_bounds = tight
            fb_score = peak + 0.04

        lvar = _visual_row_left_content_variance(im_rgb, row_top, row_bottom)
        row_conf = float(
            min(
                0.98,
                0.22 + min(0.45, fb_score * 10.0) + min(0.28, (lvar / 5000.0) ** 0.5 * 0.28),
            )
        )
        confidences.append(row_conf)

        row_analysis = {
            "row_index": idx,
            "approx_row_bounds": _scale_bounds_to_original(
                approx_row_bounds, aw=aw, ah=ah, orig_w=orig_w, orig_h=orig_h
            ),
            "approx_avatar_bounds": _scale_bounds_to_original(
                approx_avatar_bounds, aw=aw, ah=ah, orig_w=orig_w, orig_h=orig_h
            ),
            "approx_text_area_bounds": _scale_bounds_to_original(
                approx_text_area_bounds, aw=aw, ah=ah, orig_w=orig_w, orig_h=orig_h
            ),
            "approx_follow_button_bounds": _scale_bounds_to_original(
                approx_follow_button_bounds, aw=aw, ah=ah, orig_w=orig_w, orig_h=orig_h
            ),
            "confidence": round(row_conf, 4),
        }
        rows_out.append(row_analysis)
        log(
            "info",
            "followers_visual_candidate_row_detected",
            screenshot_path=path,
            source_profile_username=source_profile_username,
            row_index=idx,
            confidence=row_analysis["confidence"],
            approx_follow_button_bounds=row_analysis["approx_follow_button_bounds"],
            approx_row_bounds=row_analysis["approx_row_bounds"],
        )

    out["visual_candidate_rows"] = rows_out
    out["visual_candidate_count"] = len(rows_out)
    out["visual_confidence"] = float(
        sum(confidences) / len(confidences) if confidences else 0.0
    )

    sample_rows = rows_out[:12]
    log(
        "info",
        "followers_visual_candidate_diagnostic_done",
        screenshot_path=path,
        source_profile_username=source_profile_username,
        visual_candidate_count=out["visual_candidate_count"],
        visual_follow_button_count=out["visual_follow_button_count"],
        visual_confidence=round(out["visual_confidence"], 4),
        sample_rows=sample_rows,
    )

    if out["visual_candidate_count"] > 0:
        log(
            "info",
            "followers_visual_candidate_strategy_possible",
            screenshot_path=path,
            source_profile_username=source_profile_username,
            visual_candidate_count=out["visual_candidate_count"],
            visual_confidence=round(out["visual_confidence"], 4),
        )
    else:
        log(
            "info",
            "followers_visual_candidate_strategy_not_ready",
            screenshot_path=path,
            source_profile_username=source_profile_username,
            visual_follow_button_count=out["visual_follow_button_count"],
            diagnostic_error=out.get("diagnostic_error"),
        )

    return out


def visual_extract_followers_candidates_from_screenshot(
    d: u2.Device,
    *,
    screenshot_path: str | None = None,
    source_profile_username: str | None = None,
    runtime_seen: set[str] | None = None,
) -> dict[str, Any]:
    """
    Build structured visual follower-row candidates from an on-disk screenshot (PIL pixels only).
    No device taps, scroll, swipe, or follow. ``d`` is unused; kept for API symmetry.
    ``runtime_seen`` is reserved for future username-based filtering (no OCR in V1).
    """
    _ = d
    _ = runtime_seen
    dry_run = bool(getattr(config, "VISUAL_FOLLOWERS_PICKER_DRY_RUN", True))
    max_pick = int(getattr(config, "VISUAL_FOLLOWERS_MAX_CANDIDATES_PER_SCREEN", 5) or 5)
    max_pick = max(1, min(max_pick, 50))

    out: dict[str, Any] = {
        "candidates": [],
        "candidate_count": 0,
        "mean_confidence": 0.0,
        "screenshot_path": str(screenshot_path or ""),
        "source_profile_username": source_profile_username or "",
        "sample_candidates": [],
        "picker_error": None,
    }
    path = screenshot_path
    if not path:
        out["picker_error"] = "no_screenshot_path"
        log(
            "info",
            "visual_followers_candidate_picker_empty",
            screenshot_path="",
            source_profile_username=source_profile_username,
            candidate_count=0,
            sample_candidates=[],
            confidence=0.0,
            dry_run=dry_run,
            visual_only=True,
            reason="no_screenshot_path",
        )
        return out

    try:
        from PIL import Image

        im_orig = Image.open(path)
        orig_w, orig_h = im_orig.size
        im_rgb = _visual_downscale_rgb(im_orig, max_w=480)
    except Exception as e:
        out["picker_error"] = f"pil_failed:{e}"
        log(
            "info",
            "visual_followers_candidate_picker_empty",
            screenshot_path=str(path),
            source_profile_username=source_profile_username,
            candidate_count=0,
            sample_candidates=[],
            confidence=0.0,
            dry_run=dry_run,
            visual_only=True,
            reason=out["picker_error"],
        )
        return out

    aw, ah = im_rgb.size
    log(
        "info",
        "visual_followers_candidate_picker_started",
        screenshot_path=str(path),
        source_profile_username=source_profile_username,
        dry_run=dry_run,
        visual_only=True,
        max_candidates=max_pick,
        analysis_size=(aw, ah),
        original_size=(orig_w, orig_h),
    )

    spans = _visual_collect_follow_row_y_spans(im_rgb)
    w, h = aw, ah
    built: list[dict[str, Any]] = []
    confidences: list[float] = []
    src_key = (source_profile_username or "").strip().lstrip("@").lower()[:40]

    for idx, (yt, yb, peak) in enumerate(spans):
        pad = max(4, (yb - yt + 1) // 3)
        row_top = max(int(h * 0.10), yt - pad)
        row_bottom = min(int(h * 0.93), yb + pad)
        approx_row_bounds = {
            "left": int(w * 0.02),
            "top": row_top,
            "right": int(w * 0.98),
            "bottom": row_bottom,
        }
        approx_avatar_bounds = {
            "left": int(w * 0.03),
            "top": row_top,
            "right": int(w * 0.20),
            "bottom": row_bottom,
        }
        approx_username_text_zone = {
            "left": int(w * 0.20),
            "top": row_top,
            "right": int(w * 0.66),
            "bottom": row_bottom,
        }
        fb_left, fb_right = int(w * 0.66), w - 2
        tight = _visual_tight_blue_bounds(
            im_rgb,
            left=fb_left,
            top=yt,
            right=fb_right,
            bottom=yb,
        )
        if tight is None:
            approx_follow_button_bounds = {
                "left": fb_left,
                "top": yt,
                "right": fb_right,
                "bottom": yb,
            }
            fb_score = peak
        else:
            approx_follow_button_bounds = tight
            fb_score = peak + 0.04

        lvar = _visual_row_left_content_variance(im_rgb, row_top, row_bottom)
        row_conf = float(
            min(
                0.98,
                0.22 + min(0.45, fb_score * 10.0) + min(0.28, (lvar / 5000.0) ** 0.5 * 0.28),
            )
        )

        row_o = _scale_bounds_to_original(
            approx_row_bounds, aw=aw, ah=ah, orig_w=orig_w, orig_h=orig_h
        )
        cy = (int(row_o["top"]) + int(row_o["bottom"])) // 2
        if cy < int(orig_h * 0.14) or cy > int(orig_h * 0.86):
            continue

        av_o = _scale_bounds_to_original(
            approx_avatar_bounds, aw=aw, ah=ah, orig_w=orig_w, orig_h=orig_h
        )
        tz_o = _scale_bounds_to_original(
            approx_username_text_zone, aw=aw, ah=ah, orig_w=orig_w, orig_h=orig_h
        )
        fb_o = _scale_bounds_to_original(
            approx_follow_button_bounds, aw=aw, ah=ah, orig_w=orig_w, orig_h=orig_h
        )

        digest = hashlib.sha1(
            f"{src_key}|{idx}|{row_o['left']}|{row_o['top']}|{row_o['right']}|{row_o['bottom']}".encode()
        ).hexdigest()[:12]
        visual_candidate_id = f"vfp_{idx}_{digest}"

        cand = {
            "visual_candidate_id": visual_candidate_id,
            "row_index": len(built),
            "approx_row_bounds": row_o,
            "approx_follow_button_bounds": fb_o,
            "approx_avatar_bounds": av_o,
            "approx_username_text_zone": tz_o,
            "confidence": round(row_conf, 4),
            "source_profile_username": source_profile_username or "",
            "selection_method": "visual_screenshot",
        }
        built.append(cand)
        confidences.append(row_conf)
        log(
            "info",
            "visual_followers_candidate_detected",
            screenshot_path=str(path),
            source_profile_username=source_profile_username,
            visual_candidate_id=visual_candidate_id,
            row_index=cand["row_index"],
            confidence=cand["confidence"],
            approx_row_bounds=row_o,
            dry_run=dry_run,
            visual_only=True,
        )
        if len(built) >= max_pick:
            break

    out["candidates"] = built
    out["candidate_count"] = len(built)
    out["mean_confidence"] = float(
        sum(confidences) / len(confidences) if confidences else 0.0
    )
    out["sample_candidates"] = built[:5]

    if out["candidate_count"] == 0:
        log(
            "info",
            "visual_followers_candidate_picker_empty",
            screenshot_path=str(path),
            source_profile_username=source_profile_username,
            candidate_count=0,
            sample_candidates=[],
            confidence=0.0,
            dry_run=dry_run,
            visual_only=True,
            reason="no_rows_after_filter" if spans else "no_blue_follow_spans",
        )
    else:
        log(
            "info",
            "visual_followers_candidate_picker_done",
            screenshot_path=str(path),
            source_profile_username=source_profile_username,
            candidate_count=out["candidate_count"],
            sample_candidates=out["sample_candidates"],
            confidence=round(out["mean_confidence"], 4),
            dry_run=dry_run,
            visual_only=True,
        )

    return out


def _visual_follower_username_zone_tap_xy(
    candidate: dict[str, Any],
) -> tuple[int, int, str | None]:
    """
    Pick a tap point in the username / handle column, left of the Follow pill. Returns (x,y) or failure reason.
    """
    min_conf = float(getattr(config, "FOLLOWERS_VISUAL_FALLBACK_MIN_CONFIDENCE", 0.65))
    if float(candidate.get("confidence") or 0) < min_conf:
        return 0, 0, "confidence_below_threshold"
    tz = candidate.get("approx_username_text_zone") or {}
    fb = candidate.get("approx_follow_button_bounds") or {}
    l, t, r, b = tz.get("left"), tz.get("top"), tz.get("right"), tz.get("bottom")
    if l is None or t is None or r is None or b is None:
        row = candidate.get("approx_row_bounds") or {}
        l, t, r, b = row.get("left"), row.get("top"), row.get("right"), row.get("bottom")
        if l is None or t is None or r is None or b is None:
            return 0, 0, "missing_bounds"
    l, t, r, b = int(l), int(t), int(r), int(b)
    if r <= l + 4 or b <= t + 4:
        return 0, 0, "invalid_bounds"
    fbl = fb.get("left")
    r_eff = r
    if fbl is not None:
        r_eff = min(r, int(fbl) - 12)
    if r_eff <= l + 4:
        return 0, 0, "tap_zone_collides_with_follow"
    cx = (l + r_eff) // 2
    cy = (t + b) // 2
    return cx, cy, None


def open_visual_follower_candidate_from_screenshot(
    d: u2.Device,
    candidate: dict[str, Any],
    *,
    source_profile_username: str | None = None,
) -> dict[str, Any]:
    """
    Tap the username / row zone (never the Follow pill), then verify Instagram profile chrome.
    Does not follow, DM, or scroll.
    """
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "")
    row_b = dict(candidate.get("approx_row_bounds") or {})
    tz_b = dict(candidate.get("approx_username_text_zone") or {})
    fb_b = dict(candidate.get("approx_follow_button_bounds") or {})
    vid = str(candidate.get("visual_candidate_id") or "")
    ridx = candidate.get("row_index")

    def _payload(
        *,
        tap_x: int | None,
        tap_y: int | None,
        profile_detected: bool,
        failure_reason: str | None,
        cur_pkg: str | None = None,
        cur_act: str | None = None,
    ) -> dict[str, Any]:
        return {
            "visual_candidate_id": vid,
            "row_index": ridx,
            "tap_x": tap_x,
            "tap_y": tap_y,
            "approx_row_bounds": row_b,
            "approx_username_text_zone": tz_b,
            "source_profile_username": source_profile_username or "",
            "current_activity": cur_act,
            "current_package": cur_pkg,
            "profile_detected": profile_detected,
            "failure_reason": failure_reason,
        }

    cx, cy, pre_fail = _visual_follower_username_zone_tap_xy(candidate)
    if pre_fail:
        log(
            "error",
            "visual_follower_candidate_open_failed",
            **_payload(
                tap_x=None,
                tap_y=None,
                profile_detected=False,
                failure_reason=pre_fail,
            ),
        )
        return {
            **_payload(
                tap_x=None,
                tap_y=None,
                profile_detected=False,
                failure_reason=pre_fail,
            ),
            "ok": False,
        }

    try:
        w, h = d.window_size()
        cx = max(2, min(int(w) - 3, cx))
        cy = max(2, min(int(h) - 3, cy))
    except Exception:
        pass

    log(
        "info",
        "visual_follower_candidate_open_started",
        **_payload(
            tap_x=cx,
            tap_y=cy,
            profile_detected=False,
            failure_reason=None,
        ),
    )

    tap_inside_follow = False
    if fb_b:
        try:
            fl, ft, frb, fbot = (
                int(fb_b["left"]),
                int(fb_b["top"]),
                int(fb_b["right"]),
                int(fb_b["bottom"]),
            )
            tap_inside_follow = fl <= cx < frb and ft <= cy < fbot
        except (KeyError, TypeError, ValueError):
            tap_inside_follow = False

    log(
        "info",
        "visual_follower_candidate_open_target_zone_verified",
        visual_candidate_id=vid,
        tap_x=cx,
        tap_y=cy,
        approx_username_text_zone=tz_b,
        approx_follow_button_bounds=fb_b,
        tap_inside_follow_button=tap_inside_follow,
    )

    if tap_inside_follow:
        meta = _followers_current_pkg_activity(d)
        fr_guard = "tap_would_hit_follow_button_after_clamp"
        log(
            "error",
            "visual_follower_candidate_open_failed",
            **_payload(
                tap_x=cx,
                tap_y=cy,
                profile_detected=False,
                failure_reason=fr_guard,
                cur_pkg=meta.get("current_package"),
                cur_act=meta.get("current_activity"),
            ),
        )
        return {
            **_payload(
                tap_x=cx,
                tap_y=cy,
                profile_detected=False,
                failure_reason=fr_guard,
                cur_pkg=meta.get("current_package"),
                cur_act=meta.get("current_activity"),
            ),
            "ok": False,
        }

    try:
        d.click(cx, cy)
    except Exception as e:
        fr = f"tap_failed:{e}"
        meta = _followers_current_pkg_activity(d)
        log(
            "error",
            "visual_follower_candidate_open_failed",
            **_payload(
                tap_x=cx,
                tap_y=cy,
                profile_detected=False,
                failure_reason=fr,
                cur_pkg=meta.get("current_package"),
                cur_act=meta.get("current_activity"),
            ),
        )
        return {
            **_payload(
                tap_x=cx,
                tap_y=cy,
                profile_detected=False,
                failure_reason=fr,
                cur_pkg=meta.get("current_package"),
                cur_act=meta.get("current_activity"),
            ),
            "ok": False,
        }

    log(
        "info",
        "visual_follower_candidate_open_tap_sent",
        **_payload(
            tap_x=cx,
            tap_y=cy,
            profile_detected=False,
            failure_reason=None,
        ),
    )

    time.sleep(1.5)
    try:
        try:
            d.dump_hierarchy(compressed=False)
        except TypeError:
            d.dump_hierarchy()
    except Exception:
        pass

    meta = _followers_current_pkg_activity(d)
    cur_pkg = meta.get("current_package")
    cur_act = meta.get("current_activity")
    signal = _try_profile_signals_once(d, "", pkg)
    guess = _guess_profile_screen(d, pkg, "")
    profile_detected = bool(
        signal
        or guess in ("likely_profile", "profile_header_rid", "action_bar_title")
    )
    fr: str | None = None if profile_detected else "profile_not_verified"

    if profile_detected:
        log(
            "info",
            "visual_follower_candidate_open_success",
            **_payload(
                tap_x=cx,
                tap_y=cy,
                profile_detected=True,
                failure_reason=None,
                cur_pkg=cur_pkg,
                cur_act=cur_act,
            ),
            profile_signal=signal,
            screen_guess=guess,
        )
        return {
            **_payload(
                tap_x=cx,
                tap_y=cy,
                profile_detected=True,
                failure_reason=None,
                cur_pkg=cur_pkg,
                cur_act=cur_act,
            ),
            "ok": True,
            "profile_signal": signal,
            "screen_guess": guess,
        }

    log(
        "error",
        "visual_follower_candidate_open_failed",
        **_payload(
            tap_x=cx,
            tap_y=cy,
            profile_detected=False,
            failure_reason=fr,
            cur_pkg=cur_pkg,
            cur_act=cur_act,
        ),
        profile_signal=signal,
        screen_guess=guess,
    )
    return {
        **_payload(
            tap_x=cx,
            tap_y=cy,
            profile_detected=False,
            failure_reason=fr,
            cur_pkg=cur_pkg,
            cur_act=cur_act,
        ),
        "ok": False,
        "profile_signal": signal,
        "screen_guess": guess,
    }


# --- Visual post grid + like dry-run (screenshot heuristics; like tap never sent when VISUAL_POST_LIKE_DRY_RUN) ---
_VISUAL_POST_LIKE_TAPS_RECORDED: int = 0


def _visual_image_cell_luma_variance(
    im: Any, x0: int, y0: int, cw: int, ch: int
) -> float:
    W, H = im.size
    x1 = min(W - 1, x0 + cw)
    y1 = min(H - 1, y0 + ch)
    if x1 <= x0 + 4 or y1 <= y0 + 4:
        return 0.0
    roi = im.crop((x0, y0, x1 + 1, y1 + 1))
    lums = [(int(r) + int(g) + int(b)) / 3.0 for r, g, b in roi.getdata()]
    if not lums:
        return 0.0
    mu = sum(lums) / len(lums)
    return sum((x - mu) ** 2 for x in lums) / len(lums)


def _visual_xy_image_to_device(
    ix: int, iy: int, iw: int, ih: int, ww: int, wh: int
) -> tuple[int, int]:
    if iw <= 0 or ih <= 0:
        return max(1, ix), max(1, iy)
    return max(1, int(ix * ww / iw)), max(1, int(iy * wh / ih))


def visual_open_recent_post_from_profile(
    d: u2.Device,
    *,
    source_profile_username: str | None = None,
) -> dict[str, Any]:
    """
    From an open profile grid: screenshot, pick a high-variance 3×3 grid cell (top-left first),
    tap to open the post. Does not like or follow. Post viewer inferred via UI / activity.
    """
    global _VISUAL_POST_LIKE_TAPS_RECORDED
    _VISUAL_POST_LIKE_TAPS_RECORDED = 0
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "")
    meta0 = _followers_current_pkg_activity(d)
    act0 = meta0.get("current_activity")
    pkg0 = meta0.get("current_package")
    prof0 = bool(
        _try_profile_signals_once(d, "", pkg)
        or _guess_profile_screen(d, pkg, "")
        in ("likely_profile", "profile_header_rid", "action_bar_title")
    )

    log(
        "info",
        "visual_recent_post_open_started",
        tap_x=None,
        tap_y=None,
        current_activity=act0,
        current_package=pkg0,
        profile_detected=prof0,
        post_detected=False,
        source_profile_username=source_profile_username or "",
    )

    try:
        ww, wh = d.window_size()
    except Exception:
        ww, wh = 1080, 2400

    _ensure_debug_dirs()
    shot = str(
        _SCREENSHOTS_DIR / f"visual_recent_post_grid_{int(time.time() * 1000)}.png"
    )
    try:
        screenshot(d, shot)
    except Exception as e:
        log(
            "error",
            "visual_recent_post_open_failed",
            tap_x=None,
            tap_y=None,
            current_activity=act0,
            current_package=pkg0,
            profile_detected=prof0,
            post_detected=False,
            source_profile_username=source_profile_username or "",
            failure_reason=f"screenshot_failed:{e}",
        )
        return {
            "ok": False,
            "tap_x": None,
            "tap_y": None,
            "current_activity": act0,
            "current_package": pkg0,
            "profile_detected": prof0,
            "post_detected": False,
            "source_profile_username": source_profile_username or "",
            "failure_reason": f"screenshot_failed:{e}",
        }

    try:
        from PIL import Image

        im = Image.open(shot).convert("RGB")
    except Exception as e:
        log(
            "error",
            "visual_recent_post_open_failed",
            tap_x=None,
            tap_y=None,
            current_activity=act0,
            current_package=pkg0,
            profile_detected=prof0,
            post_detected=False,
            source_profile_username=source_profile_username or "",
            failure_reason=f"pil_failed:{e}",
        )
        return {
            "ok": False,
            "tap_x": None,
            "tap_y": None,
            "current_activity": act0,
            "current_package": pkg0,
            "profile_detected": prof0,
            "post_detected": False,
            "source_profile_username": source_profile_username or "",
            "failure_reason": f"pil_failed:{e}",
        }

    iw, ih = im.size
    grid_y0 = int(ih * 0.33)
    grid_y1 = int(ih * 0.92)
    cols = 3
    cell_w = max(24, iw // cols)
    cell_h = cell_w
    var_thr = 120.0
    order = [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)]
    chosen: tuple[int, int, int, int, float] | None = None
    for col, row in order:
        x0 = col * cell_w
        y0 = grid_y0 + row * cell_h
        if y0 + cell_h > grid_y1 or x0 + cell_w > iw:
            continue
        v = _visual_image_cell_luma_variance(im, x0, y0, cell_w, cell_h)
        if col == 0 and row == 0 and v >= var_thr:
            chosen = (col, row, x0, y0, v)
            break
        if chosen is None or v > chosen[4]:
            chosen = (col, row, x0, y0, v)

    if chosen is None:
        log(
            "error",
            "visual_recent_post_open_failed",
            tap_x=None,
            tap_y=None,
            current_activity=act0,
            current_package=pkg0,
            profile_detected=prof0,
            post_detected=False,
            source_profile_username=source_profile_username or "",
            failure_reason="no_grid_cell",
        )
        return {
            "ok": False,
            "tap_x": None,
            "tap_y": None,
            "current_activity": act0,
            "current_package": pkg0,
            "profile_detected": prof0,
            "post_detected": False,
            "source_profile_username": source_profile_username or "",
            "failure_reason": "no_grid_cell",
        }

    col, row, x0, y0, var = chosen
    cx_img = x0 + cell_w // 2
    cy_img = y0 + cell_h // 2
    tap_x, tap_y = _visual_xy_image_to_device(cx_img, cy_img, iw, ih, ww, wh)
    tap_x = max(2, min(ww - 3, tap_x))
    tap_y = max(2, min(wh - 3, tap_y))

    log(
        "info",
        "visual_recent_post_candidate_detected",
        col=col,
        row=row,
        variance=round(float(var), 2),
        tap_x=tap_x,
        tap_y=tap_y,
        cell_w=cell_w,
        current_activity=act0,
        current_package=pkg0,
        profile_detected=prof0,
        post_detected=False,
        source_profile_username=source_profile_username or "",
    )

    try:
        d.click(tap_x, tap_y)
    except Exception as e:
        log(
            "error",
            "visual_recent_post_open_failed",
            tap_x=tap_x,
            tap_y=tap_y,
            current_activity=act0,
            current_package=pkg0,
            profile_detected=prof0,
            post_detected=False,
            source_profile_username=source_profile_username or "",
            failure_reason=f"tap_failed:{e}",
        )
        return {
            "ok": False,
            "tap_x": tap_x,
            "tap_y": tap_y,
            "current_activity": act0,
            "current_package": pkg0,
            "profile_detected": prof0,
            "post_detected": False,
            "source_profile_username": source_profile_username or "",
            "failure_reason": f"tap_failed:{e}",
        }

    meta_mid = _followers_current_pkg_activity(d)
    log(
        "info",
        "visual_recent_post_open_tap_sent",
        tap_x=tap_x,
        tap_y=tap_y,
        current_activity=meta_mid.get("current_activity"),
        current_package=meta_mid.get("current_package"),
        profile_detected=prof0,
        post_detected=False,
        source_profile_username=source_profile_username or "",
    )

    time.sleep(1.6)
    meta1 = _followers_current_pkg_activity(d)
    act1 = meta1.get("current_activity")
    post_detected = False
    for label in (
        "Like",
        "Unlike",
        "J'aime",
        "Jaime",
        "Gefällt mir",
        "Me gusta",
    ):
        try:
            if d(descriptionContains=label).exists(timeout=0.12):
                post_detected = True
                break
        except Exception:
            continue
    if not post_detected and act1 != act0:
        post_detected = True
    if not post_detected:
        prof_still = bool(
            _try_profile_signals_once(d, "", pkg)
            or _guess_profile_screen(d, pkg, "")
            in ("likely_profile", "profile_header_rid", "action_bar_title")
        )
        post_detected = not prof_still

    meta_fin = _followers_current_pkg_activity(d)
    if post_detected:
        log(
            "info",
            "visual_recent_post_open_success",
            tap_x=tap_x,
            tap_y=tap_y,
            current_activity=meta_fin.get("current_activity"),
            current_package=meta_fin.get("current_package"),
            profile_detected=prof0,
            post_detected=True,
            source_profile_username=source_profile_username or "",
        )
        return {
            "ok": True,
            "tap_x": tap_x,
            "tap_y": tap_y,
            "current_activity": meta_fin.get("current_activity"),
            "current_package": meta_fin.get("current_package"),
            "profile_detected": prof0,
            "post_detected": True,
            "source_profile_username": source_profile_username or "",
            "failure_reason": None,
        }

    log(
        "error",
        "visual_recent_post_open_failed",
        tap_x=tap_x,
        tap_y=tap_y,
        current_activity=meta_fin.get("current_activity"),
        current_package=meta_fin.get("current_package"),
        profile_detected=prof0,
        post_detected=False,
        source_profile_username=source_profile_username or "",
        failure_reason="post_viewer_not_detected",
    )
    return {
        "ok": False,
        "tap_x": tap_x,
        "tap_y": tap_y,
        "current_activity": meta_fin.get("current_activity"),
        "current_package": meta_fin.get("current_package"),
        "profile_detected": prof0,
        "post_detected": False,
        "source_profile_username": source_profile_username or "",
        "failure_reason": "post_viewer_not_detected",
    }


def visual_like_open_post(
    d: u2.Device,
    *,
    source_profile_username: str | None = None,
) -> dict[str, Any]:
    """
    Estimate like control from a screenshot of the open post; dry-run never sends the like tap.
    """
    global _VISUAL_POST_LIKE_TAPS_RECORDED
    max_l = int(getattr(config, "VISUAL_POST_MAX_LIKES_PER_PROFILE", 1) or 1)
    dry = bool(getattr(config, "VISUAL_POST_LIKE_DRY_RUN", True))
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "")
    meta0 = _followers_current_pkg_activity(d)

    log(
        "info",
        "visual_post_like_started",
        source_profile_username=source_profile_username or "",
        current_activity=meta0.get("current_activity"),
        current_package=meta0.get("current_package"),
        dry_run=dry,
    )

    if _VISUAL_POST_LIKE_TAPS_RECORDED >= max_l:
        log(
            "error",
            "visual_post_like_failed",
            like_button_bounds=None,
            tap_x=None,
            tap_y=None,
            confidence=0.0,
            current_activity=meta0.get("current_activity"),
            current_package=meta0.get("current_package"),
            source_profile_username=source_profile_username or "",
            failure_reason="max_likes_per_profile",
        )
        return {
            "ok": False,
            "like_button_bounds": None,
            "tap_x": None,
            "tap_y": None,
            "confidence": 0.0,
            "current_activity": meta0.get("current_activity"),
            "current_package": meta0.get("current_package"),
            "source_profile_username": source_profile_username or "",
            "failure_reason": "max_likes_per_profile",
            "dry_run": dry,
        }

    try:
        ww, wh = d.window_size()
    except Exception:
        ww, wh = 1080, 2400

    _ensure_debug_dirs()
    shot = str(_SCREENSHOTS_DIR / f"visual_post_like_{int(time.time() * 1000)}.png")
    try:
        screenshot(d, shot)
    except Exception as e:
        log(
            "error",
            "visual_post_like_failed",
            like_button_bounds=None,
            tap_x=None,
            tap_y=None,
            confidence=0.0,
            current_activity=meta0.get("current_activity"),
            current_package=meta0.get("current_package"),
            source_profile_username=source_profile_username or "",
            failure_reason=f"screenshot_failed:{e}",
        )
        return {
            "ok": False,
            "like_button_bounds": None,
            "tap_x": None,
            "tap_y": None,
            "confidence": 0.0,
            "current_activity": meta0.get("current_activity"),
            "current_package": meta0.get("current_package"),
            "source_profile_username": source_profile_username or "",
            "failure_reason": f"screenshot_failed:{e}",
            "dry_run": dry,
        }

    try:
        from PIL import Image

        im = Image.open(shot).convert("RGB")
    except Exception as e:
        log(
            "error",
            "visual_post_like_failed",
            like_button_bounds=None,
            tap_x=None,
            tap_y=None,
            confidence=0.0,
            current_activity=meta0.get("current_activity"),
            current_package=meta0.get("current_package"),
            source_profile_username=source_profile_username or "",
            failure_reason=f"pil_failed:{e}",
        )
        return {
            "ok": False,
            "like_button_bounds": None,
            "tap_x": None,
            "tap_y": None,
            "confidence": 0.0,
            "current_activity": meta0.get("current_activity"),
            "current_package": meta0.get("current_package"),
            "source_profile_username": source_profile_username or "",
            "failure_reason": f"pil_failed:{e}",
            "dry_run": dry,
        }

    iw, ih = im.size
    lb_left = int(iw * 0.055)
    lb_top = int(ih * 0.555)
    lb_right = int(iw * 0.145)
    lb_bottom = int(ih * 0.63)
    like_button_bounds: dict[str, int] = {
        "left": lb_left,
        "top": lb_top,
        "right": lb_right,
        "bottom": lb_bottom,
    }
    cx = (lb_left + lb_right) // 2
    cy = (lb_top + lb_bottom) // 2
    tap_x, tap_y = _visual_xy_image_to_device(cx, cy, iw, ih, ww, wh)
    tap_x = max(2, min(ww - 3, tap_x))
    tap_y = max(2, min(wh - 3, tap_y))
    roi_var = _visual_image_cell_luma_variance(
        im, lb_left, lb_top, lb_right - lb_left, lb_bottom - lb_top
    )
    confidence = float(min(0.95, 0.52 + min(0.42, (roi_var / 9000.0) ** 0.5 * 0.38)))

    log(
        "info",
        "visual_post_like_target_detected",
        like_button_bounds=like_button_bounds,
        tap_x=tap_x,
        tap_y=tap_y,
        confidence=round(confidence, 4),
        current_activity=meta0.get("current_activity"),
        current_package=meta0.get("current_package"),
        source_profile_username=source_profile_username or "",
        dry_run=dry,
    )

    _VISUAL_POST_LIKE_TAPS_RECORDED += 1
    meta1 = _followers_current_pkg_activity(d)

    if dry:
        log(
            "info",
            "visual_post_like_dry_run_complete",
            like_button_bounds=like_button_bounds,
            tap_x=tap_x,
            tap_y=tap_y,
            confidence=round(confidence, 4),
            current_activity=meta1.get("current_activity"),
            current_package=meta1.get("current_package"),
            source_profile_username=source_profile_username or "",
            dry_run=True,
        )
        return {
            "ok": True,
            "like_button_bounds": like_button_bounds,
            "tap_x": tap_x,
            "tap_y": tap_y,
            "confidence": confidence,
            "current_activity": meta1.get("current_activity"),
            "current_package": meta1.get("current_package"),
            "source_profile_username": source_profile_username or "",
            "failure_reason": None,
            "dry_run": True,
        }

    try:
        d.click(tap_x, tap_y)
    except Exception as e:
        log(
            "error",
            "visual_post_like_failed",
            like_button_bounds=like_button_bounds,
            tap_x=tap_x,
            tap_y=tap_y,
            confidence=round(confidence, 4),
            current_activity=meta1.get("current_activity"),
            current_package=meta1.get("current_package"),
            source_profile_username=source_profile_username or "",
            failure_reason=f"tap_failed:{e}",
        )
        return {
            "ok": False,
            "like_button_bounds": like_button_bounds,
            "tap_x": tap_x,
            "tap_y": tap_y,
            "confidence": confidence,
            "current_activity": meta1.get("current_activity"),
            "current_package": meta1.get("current_package"),
            "source_profile_username": source_profile_username or "",
            "failure_reason": f"tap_failed:{e}",
            "dry_run": False,
        }

    log(
        "info",
        "visual_post_like_dry_run_complete",
        like_button_bounds=like_button_bounds,
        tap_x=tap_x,
        tap_y=tap_y,
        confidence=round(confidence, 4),
        current_activity=_followers_current_pkg_activity(d).get("current_activity"),
        current_package=_followers_current_pkg_activity(d).get("current_package"),
        source_profile_username=source_profile_username or "",
        dry_run=False,
    )
    return {
        "ok": True,
        "like_button_bounds": like_button_bounds,
        "tap_x": tap_x,
        "tap_y": tap_y,
        "confidence": confidence,
        "current_activity": _followers_current_pkg_activity(d).get("current_activity"),
        "current_package": _followers_current_pkg_activity(d).get("current_package"),
        "source_profile_username": source_profile_username or "",
        "failure_reason": None,
        "dry_run": False,
    }


def visual_return_to_profile_from_post(
    d: u2.Device,
    *,
    source_profile_username: str | None = None,
) -> dict[str, Any]:
    """Single Back from post/reel viewer toward profile; verify profile chrome. No scroll."""
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "")
    meta0 = _followers_current_pkg_activity(d)
    log(
        "info",
        "visual_return_to_profile_started",
        source_profile_username=source_profile_username or "",
        current_activity=meta0.get("current_activity"),
        current_package=meta0.get("current_package"),
    )
    try:
        d.press("back")
    except Exception as e:
        meta = _followers_current_pkg_activity(d)
        log(
            "error",
            "visual_return_to_profile_failed",
            source_profile_username=source_profile_username or "",
            current_activity=meta.get("current_activity"),
            current_package=meta.get("current_package"),
            profile_detected=False,
            failure_reason=str(e),
        )
        return {
            "ok": False,
            "profile_detected": False,
            "current_activity": meta.get("current_activity"),
            "current_package": meta.get("current_package"),
            "source_profile_username": source_profile_username or "",
            "failure_reason": str(e),
        }

    time.sleep(1.25)
    meta = _followers_current_pkg_activity(d)
    profile_detected = bool(
        _try_profile_signals_once(d, "", pkg)
        or _guess_profile_screen(d, pkg, "")
        in ("likely_profile", "profile_header_rid", "action_bar_title")
    )
    if profile_detected:
        log(
            "info",
            "visual_return_to_profile_success",
            source_profile_username=source_profile_username or "",
            current_activity=meta.get("current_activity"),
            current_package=meta.get("current_package"),
            profile_detected=True,
        )
        return {
            "ok": True,
            "profile_detected": True,
            "current_activity": meta.get("current_activity"),
            "current_package": meta.get("current_package"),
            "source_profile_username": source_profile_username or "",
            "failure_reason": None,
        }

    log(
        "error",
        "visual_return_to_profile_failed",
        source_profile_username=source_profile_username or "",
        current_activity=meta.get("current_activity"),
        current_package=meta.get("current_package"),
        profile_detected=False,
        failure_reason="profile_not_verified_after_back",
    )
    return {
        "ok": False,
        "profile_detected": False,
        "current_activity": meta.get("current_activity"),
        "current_package": meta.get("current_package"),
        "source_profile_username": source_profile_username or "",
        "failure_reason": "profile_not_verified_after_back",
    }


def _visual_bounds_map_to_device(
    bd: dict[str, int], iw: int, ih: int, ww: int, wh: int
) -> dict[str, int]:
    if iw <= 0 or ih <= 0:
        return dict(bd)
    return {
        "left": max(0, int(bd["left"] * ww / iw)),
        "top": max(0, int(bd["top"] * wh / ih)),
        "right": max(1, int(bd["right"] * ww / iw)),
        "bottom": max(1, int(bd["bottom"] * wh / ih)),
    }


def _visual_blue_fill_ratio_in_bounds(im: Any, bd: dict[str, int]) -> float:
    l, t, r, b = int(bd["left"]), int(bd["top"]), int(bd["right"]), int(bd["bottom"])
    if r <= l + 2 or b <= t + 2:
        return 0.0
    crop = im.crop((l, t, r + 1, b + 1))
    n_blue = 0
    n = 0
    for rr, gg, bb in crop.getdata():
        n += 1
        if _ig_follow_button_blue_pixel(int(rr), int(gg), int(bb)):
            n_blue += 1
    return n_blue / max(n, 1)


def visual_detect_follow_button_on_profile(
    d: u2.Device,
    *,
    source_profile_username: str | None = None,
) -> dict[str, Any]:
    """
    Screenshot-based blue Follow pill in the profile header action row (upper-middle right).
    """
    log(
        "info",
        "visual_follow_button_detect_started",
        source_profile_username=source_profile_username or "",
    )
    try:
        ww, wh = d.window_size()
    except Exception:
        ww, wh = 1080, 2400

    _ensure_debug_dirs()
    shot = str(
        _SCREENSHOTS_DIR / f"visual_profile_follow_{int(time.time() * 1000)}.png"
    )
    try:
        screenshot(d, shot)
    except Exception as e:
        log(
            "info",
            "visual_follow_button_not_found",
            source_profile_username=source_profile_username or "",
            reason=f"screenshot_failed:{e}",
        )
        return {
            "ok": False,
            "follow_button_bounds": None,
            "tap_x": None,
            "tap_y": None,
            "confidence": 0.0,
            "button_type": "follow",
            "failure_reason": f"screenshot_failed:{e}",
        }

    try:
        from PIL import Image

        im = Image.open(shot).convert("RGB")
    except Exception as e:
        log(
            "info",
            "visual_follow_button_not_found",
            source_profile_username=source_profile_username or "",
            reason=f"pil_failed:{e}",
        )
        return {
            "ok": False,
            "follow_button_bounds": None,
            "tap_x": None,
            "tap_y": None,
            "confidence": 0.0,
            "button_type": "follow",
            "failure_reason": f"pil_failed:{e}",
        }

    iw, ih = im.size
    x0, x1 = int(iw * 0.40), iw - 2
    y0, y1 = int(ih * 0.10), int(ih * 0.36)
    tb = _visual_tight_blue_bounds(im, left=x0, top=y0, right=x1, bottom=y1)
    if tb is None or (tb["right"] - tb["left"]) > int(iw * 0.44):
        x1n = int(iw * 0.68)
        tb = _visual_tight_blue_bounds(im, left=x0, top=y0, right=x1n, bottom=y1)
    if tb is None:
        log(
            "info",
            "visual_follow_button_not_found",
            source_profile_username=source_profile_username or "",
            reason="no_blue_pill_in_header_roi",
        )
        return {
            "ok": False,
            "follow_button_bounds": None,
            "tap_x": None,
            "tap_y": None,
            "confidence": 0.0,
            "button_type": "follow",
            "failure_reason": "no_blue_pill_in_header_roi",
        }

    ratio = _visual_blue_fill_ratio_in_bounds(im, tb)
    confidence = float(min(0.97, 0.35 + ratio * 1.15))
    bd_dev = _visual_bounds_map_to_device(tb, iw, ih, ww, wh)
    tap_x = (bd_dev["left"] + bd_dev["right"]) // 2
    tap_y = (bd_dev["top"] + bd_dev["bottom"]) // 2
    tap_x = max(2, min(ww - 3, tap_x))
    tap_y = max(2, min(wh - 3, tap_y))

    log(
        "info",
        "visual_follow_button_detected",
        source_profile_username=source_profile_username or "",
        follow_button_bounds=bd_dev,
        tap_x=tap_x,
        tap_y=tap_y,
        confidence=round(confidence, 4),
        button_type="follow",
    )
    return {
        "ok": True,
        "follow_button_bounds": bd_dev,
        "tap_x": tap_x,
        "tap_y": tap_y,
        "confidence": confidence,
        "button_type": "follow",
        "failure_reason": None,
    }


def visual_follow_profile_dry_run(
    d: u2.Device,
    *,
    source_profile_username: str | None = None,
    follow_detection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Compute Follow tap from screenshot; never taps when VISUAL_FOLLOW_MUTE_DRY_RUN is True.
    """
    dry = bool(getattr(config, "VISUAL_FOLLOW_MUTE_DRY_RUN", True))
    meta0 = _followers_current_pkg_activity(d)
    log(
        "info",
        "visual_follow_profile_started",
        source_profile_username=source_profile_username or "",
        current_activity=meta0.get("current_activity"),
        current_package=meta0.get("current_package"),
        dry_run=dry,
    )

    det = follow_detection
    if det is None:
        det = visual_detect_follow_button_on_profile(
            d, source_profile_username=source_profile_username
        )
    if not det.get("ok"):
        log(
            "error",
            "visual_follow_profile_failed",
            tap_x=None,
            tap_y=None,
            confidence=0.0,
            current_activity=meta0.get("current_activity"),
            current_package=meta0.get("current_package"),
            source_profile_username=source_profile_username or "",
            failure_reason=det.get("failure_reason") or "follow_button_not_found",
        )
        return {
            "ok": False,
            "tap_x": None,
            "tap_y": None,
            "confidence": 0.0,
            "current_activity": meta0.get("current_activity"),
            "current_package": meta0.get("current_package"),
            "source_profile_username": source_profile_username or "",
            "failure_reason": det.get("failure_reason") or "follow_button_not_found",
            "dry_run": dry,
        }

    tap_x = int(det["tap_x"])
    tap_y = int(det["tap_y"])
    confidence = float(det.get("confidence") or 0.0)
    meta1 = _followers_current_pkg_activity(d)
    log(
        "info",
        "visual_follow_profile_target_verified",
        tap_x=tap_x,
        tap_y=tap_y,
        confidence=round(confidence, 4),
        current_activity=meta1.get("current_activity"),
        current_package=meta1.get("current_package"),
        source_profile_username=source_profile_username or "",
        dry_run=dry,
    )

    if dry:
        log(
            "info",
            "visual_follow_profile_dry_run_complete",
            tap_x=tap_x,
            tap_y=tap_y,
            confidence=round(confidence, 4),
            current_activity=meta1.get("current_activity"),
            current_package=meta1.get("current_package"),
            source_profile_username=source_profile_username or "",
        )
        return {
            "ok": True,
            "tap_x": tap_x,
            "tap_y": tap_y,
            "confidence": confidence,
            "current_activity": meta1.get("current_activity"),
            "current_package": meta1.get("current_package"),
            "source_profile_username": source_profile_username or "",
            "failure_reason": None,
            "dry_run": True,
        }

    try:
        d.click(tap_x, tap_y)
    except Exception as e:
        log(
            "error",
            "visual_follow_profile_failed",
            tap_x=tap_x,
            tap_y=tap_y,
            confidence=round(confidence, 4),
            current_activity=_followers_current_pkg_activity(d).get("current_activity"),
            current_package=_followers_current_pkg_activity(d).get("current_package"),
            source_profile_username=source_profile_username or "",
            failure_reason=str(e),
        )
        return {
            "ok": False,
            "tap_x": tap_x,
            "tap_y": tap_y,
            "confidence": confidence,
            "current_activity": _followers_current_pkg_activity(d).get("current_activity"),
            "current_package": _followers_current_pkg_activity(d).get("current_package"),
            "source_profile_username": source_profile_username or "",
            "failure_reason": str(e),
            "dry_run": False,
        }

    log(
        "info",
        "visual_follow_profile_dry_run_complete",
        tap_x=tap_x,
        tap_y=tap_y,
        confidence=round(confidence, 4),
        current_activity=_followers_current_pkg_activity(d).get("current_activity"),
        current_package=_followers_current_pkg_activity(d).get("current_package"),
        source_profile_username=source_profile_username or "",
    )
    return {
        "ok": True,
        "tap_x": tap_x,
        "tap_y": tap_y,
        "confidence": confidence,
        "current_activity": _followers_current_pkg_activity(d).get("current_activity"),
        "current_package": _followers_current_pkg_activity(d).get("current_package"),
        "source_profile_username": source_profile_username or "",
        "failure_reason": None,
        "dry_run": False,
    }


def visual_detect_follow_post_actions(
    d: u2.Device,
    *,
    source_profile_username: str | None = None,
) -> dict[str, Any]:
    """
    Heuristic detection of follow confirmation sheet / mute rows (UiAutomator labels + bright sheet ROI).
    """
    log(
        "info",
        "visual_follow_actions_detect_started",
        source_profile_username=source_profile_username or "",
    )
    meta0 = _followers_current_pkg_activity(d)
    mute_posts_detected = False
    mute_stories_detected = False
    following_detected = False

    def _exists_text(pred: Callable[[], object], timeout: float = 0.12) -> bool:
        try:
            return bool(pred().exists(timeout=timeout))
        except Exception:
            return False

    if _exists_text(lambda: d(textContains="Mute posts")) or _exists_text(
        lambda: d(text="Mute posts")
    ):
        mute_posts_detected = True
    if _exists_text(lambda: d(textContains="Mute stories")) or _exists_text(
        lambda: d(text="Mute stories")
    ):
        mute_stories_detected = True
    if _exists_text(lambda: d(textContains="Following")) or _exists_text(
        lambda: d(text="Following")
    ):
        following_detected = True
    if not mute_posts_detected and _exists_text(lambda: d(descriptionContains="Mute")):
        try:
            if d(textContains="posts").exists(timeout=0.08):
                mute_posts_detected = True
        except Exception:
            pass
        try:
            if d(textContains="stories").exists(timeout=0.08):
                mute_stories_detected = True
        except Exception:
            pass

    _ensure_debug_dirs()
    shot = str(
        _SCREENSHOTS_DIR / f"visual_follow_actions_{int(time.time() * 1000)}.png"
    )
    popup_detected = False
    pil_conf = 0.25
    try:
        screenshot(d, shot)
        from PIL import Image

        im = Image.open(shot).convert("RGB")
        iw, ih = im.size
        roi = im.crop((0, int(ih * 0.48), iw, ih))
        lums = [(int(r) + int(g) + int(b)) / 3 for r, g, b in roi.getdata()]
        if lums:
            mu = sum(lums) / len(lums)
            var = sum((x - mu) ** 2 for x in lums) / len(lums)
            if mu > 210 and 120 < var < 8000:
                popup_detected = True
                pil_conf = 0.35
    except Exception:
        pass

    if mute_posts_detected or mute_stories_detected or following_detected:
        popup_detected = True
        pil_conf = max(pil_conf, 0.55)

    confidence = float(
        min(
            0.95,
            pil_conf
            + (0.12 if mute_posts_detected else 0)
            + (0.12 if mute_stories_detected else 0)
            + (0.1 if following_detected else 0),
        )
    )

    payload = {
        "mute_posts_detected": mute_posts_detected,
        "mute_stories_detected": mute_stories_detected,
        "following_detected": following_detected,
        "popup_detected": popup_detected,
        "confidence": round(confidence, 4),
        "current_activity": meta0.get("current_activity"),
        "current_package": meta0.get("current_package"),
        "source_profile_username": source_profile_username or "",
    }

    if popup_detected or mute_posts_detected or mute_stories_detected or following_detected:
        log("info", "visual_follow_actions_detected", **payload)
    else:
        log("info", "visual_follow_actions_not_detected", **payload)

    return payload


def visual_mute_after_follow_dry_run(
    d: u2.Device,
    *,
    source_profile_username: str | None = None,
    actions_out: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Estimate mute row tap points from typical bottom-sheet layout; never taps (dry-run only).
    """
    dry = bool(getattr(config, "VISUAL_FOLLOW_MUTE_DRY_RUN", True))
    if not dry:
        return {
            "ok": False,
            "failure_reason": "dry_run_disabled",
            "source_profile_username": source_profile_username or "",
        }

    want_posts = bool(getattr(config, "VISUAL_MUTE_POSTS_AFTER_FOLLOW", True))
    want_stories = bool(getattr(config, "VISUAL_MUTE_STORIES_AFTER_FOLLOW", True))
    act = actions_out or {}

    try:
        ww, wh = d.window_size()
    except Exception:
        ww, wh = 1080, 2400

    _ensure_debug_dirs()
    shot = str(
        _SCREENSHOTS_DIR / f"visual_mute_sheet_{int(time.time() * 1000)}.png"
    )
    try:
        screenshot(d, shot)
        from PIL import Image

        im = Image.open(shot).convert("RGB")
        iw, ih = im.size
    except Exception as e:
        log(
            "info",
            "visual_mute_dry_run_complete",
            ok=False,
            source_profile_username=source_profile_username or "",
            failure_reason=f"screenshot_failed:{e}",
        )
        return {
            "ok": False,
            "failure_reason": f"screenshot_failed:{e}",
            "source_profile_username": source_profile_username or "",
        }

    # Typical sheet row centers (image ratios); map to device.
    posts_y_img = int(ih * 0.58)
    stories_y_img = int(ih * 0.665)
    tap_x_img = int(iw * 0.48)
    posts_tx, posts_ty = _visual_xy_image_to_device(
        tap_x_img, posts_y_img, iw, ih, ww, wh
    )
    stories_tx, stories_ty = _visual_xy_image_to_device(
        tap_x_img, stories_y_img, iw, ih, ww, wh
    )

    meta = _followers_current_pkg_activity(d)
    if want_posts:
        log(
            "info",
            "visual_mute_posts_target_detected",
            tap_x=posts_tx,
            tap_y=posts_ty,
            source_profile_username=source_profile_username or "",
            ui_mute_posts_hint=act.get("mute_posts_detected"),
            current_activity=meta.get("current_activity"),
            current_package=meta.get("current_package"),
            dry_run=True,
        )
    if want_stories:
        log(
            "info",
            "visual_mute_stories_target_detected",
            tap_x=stories_tx,
            tap_y=stories_ty,
            source_profile_username=source_profile_username or "",
            ui_mute_stories_hint=act.get("mute_stories_detected"),
            current_activity=meta.get("current_activity"),
            current_package=meta.get("current_package"),
            dry_run=True,
        )

    log(
        "info",
        "visual_mute_dry_run_complete",
        source_profile_username=source_profile_username or "",
        mute_posts_target=(want_posts, posts_tx, posts_ty),
        mute_stories_target=(want_stories, stories_tx, stories_ty),
        current_activity=meta.get("current_activity"),
        current_package=meta.get("current_package"),
        dry_run=True,
    )
    return {
        "ok": True,
        "mute_posts_tap": (posts_tx, posts_ty) if want_posts else None,
        "mute_stories_tap": (stories_tx, stories_ty) if want_stories else None,
        "current_activity": meta.get("current_activity"),
        "current_package": meta.get("current_package"),
        "source_profile_username": source_profile_username or "",
        "dry_run": True,
    }


def detect_followers_list_screen_visual_fallback(
    d: u2.Device,
    *,
    source_profile_username: str | None = None,
    screenshot_path: str | None = None,
) -> dict[str, Any]:
    """
    Screenshot-only followers list hint when XML is stale. Does not tap, scroll, or select rows.
    """
    out: dict[str, Any] = {
        "visual_match": False,
        "visual_confidence": 0.0,
        "visual_follow_button_count": 0,
        "visual_search_area_detected": False,
        "visual_user_rows_detected": 0,
        "visual_profile_tabs_absent": False,
        "visual_followers_title_hint": False,
        "visual_signals": [],
        "screenshot_path_used": None,
        "visual_error": None,
    }
    path = screenshot_path
    temp_shot: str | None = None
    if not path:
        try:
            _ensure_debug_dirs()
            temp_shot = str(
                _SCREENSHOTS_DIR / f"followers_visual_fallback_{int(time.time() * 1000)}.png"
            )
            screenshot(d, temp_shot)
            path = temp_shot
        except Exception as e:
            out["visual_error"] = f"screenshot_failed:{e}"
            return out
    out["screenshot_path_used"] = path
    try:
        from PIL import Image

        im = Image.open(path)
        im_rgb = _visual_downscale_rgb(im, max_w=420)
    except Exception as e:
        out["visual_error"] = f"pil_open_failed:{e}"
        return out

    follow_bands = _visual_count_right_column_blue_bands(im_rgb)
    search_ok = _visual_detect_search_strip(im_rgb)
    user_rows = _visual_count_left_column_row_bands(im_rgb)
    title_hint = _visual_header_dark_text_hint(im_rgb)
    tabs_absent = _visual_bottom_strip_tabs_heuristic(im_rgb, follow_bands)

    out["visual_follow_button_count"] = follow_bands
    out["visual_search_area_detected"] = search_ok
    out["visual_user_rows_detected"] = user_rows
    out["visual_profile_tabs_absent"] = tabs_absent
    out["visual_followers_title_hint"] = title_hint

    min_btns = int(getattr(config, "FOLLOWERS_VISUAL_MIN_FOLLOW_BUTTONS", 3))
    min_conf = float(getattr(config, "FOLLOWERS_VISUAL_FALLBACK_MIN_CONFIDENCE", 0.65))

    conf = 0.0
    sig: list[str] = []
    if follow_bands >= min_btns:
        conf += 0.38 + min(0.22, (follow_bands - min_btns) * 0.05)
        sig.append(f"right_blue_bands:{follow_bands}")
    if search_ok:
        conf += 0.22
        sig.append("search_strip")
    if user_rows >= 3:
        conf += 0.18
        sig.append(f"left_row_bands:{user_rows}")
    elif user_rows >= 2:
        conf += 0.10
        sig.append(f"left_row_bands:{user_rows}")
    if title_hint:
        conf += 0.10
        sig.append("header_dark_text")
    if tabs_absent:
        conf += 0.08
        sig.append("bottom_tabs_absent_heuristic")

    conf = min(1.0, conf)
    out["visual_confidence"] = conf
    out["visual_signals"] = sig
    out["visual_match"] = bool(
        follow_bands >= min_btns and conf >= min_conf and (search_ok or user_rows >= 2)
    )
    return out


def _followers_visual_fallback_log_payload(
    vf: dict[str, Any],
    xml_det: dict[str, Any],
    *,
    screenshot_path: str | None,
    source_profile_username: str | None,
) -> dict[str, Any]:
    return {
        "screenshot_path": screenshot_path or vf.get("screenshot_path_used"),
        "source_profile_username": source_profile_username,
        "visual_follow_button_count": vf.get("visual_follow_button_count"),
        "visual_search_area_detected": vf.get("visual_search_area_detected"),
        "visual_user_rows_detected": vf.get("visual_user_rows_detected"),
        "visual_profile_tabs_absent": vf.get("visual_profile_tabs_absent"),
        "visual_confidence": vf.get("visual_confidence"),
        "xml_current_screen_guess": xml_det.get("current_screen_guess"),
        "xml_candidate_username_count": xml_det.get("candidate_username_count"),
        "xml_scrollable_count": xml_det.get("scrollable_count"),
    }


def _followers_apply_visual_fallback_if_needed(
    d: u2.Device,
    det: dict[str, Any],
    screenshot_path: str | None,
    source_profile_username: str,
    *,
    phase: str,
) -> dict[str, Any]:
    if det.get("is_followers_list"):
        return det
    if not bool(getattr(config, "ENABLE_FOLLOWERS_VISUAL_FALLBACK", False)):
        return det

    log(
        "info",
        "followers_visual_fallback_started",
        phase=phase,
        screenshot_path=screenshot_path,
        source_profile_username=source_profile_username,
        xml_current_screen_guess=det.get("current_screen_guess"),
    )
    vf = detect_followers_list_screen_visual_fallback(
        d,
        source_profile_username=source_profile_username,
        screenshot_path=screenshot_path,
    )
    payload = {
        **_followers_visual_fallback_log_payload(
            vf, det, screenshot_path=screenshot_path, source_profile_username=source_profile_username
        ),
        "visual_match": bool(vf.get("visual_match")),
        "visual_signals": vf.get("visual_signals"),
        "visual_error": vf.get("visual_error"),
        "phase": phase,
    }
    log("info", "followers_visual_fallback_result", **payload)

    if not vf.get("visual_match"):
        log("warning", "followers_visual_fallback_failed", **payload)
        return det

    merged = dict(det)
    merged["is_followers_list"] = True
    merged["open_detection_method"] = "visual_fallback"
    merged["visual_fallback_detail"] = vf
    sigs = list(merged.get("signals") or [])
    sigs.append(f"visual_fallback:{','.join(vf.get('visual_signals') or [])}")
    merged["signals"] = sigs
    log("info", "followers_visual_fallback_match", **payload)
    return merged


def _followers_poll_hierarchy_refresh(d: u2.Device) -> None:
    """Force a fresh accessibility tree (best-effort) before detection."""
    try:
        try:
            d.dump_hierarchy(compressed=False)
        except TypeError:
            d.dump_hierarchy()
    except Exception:
        pass


def followers_force_hierarchy_refresh(
    d: u2.Device,
    source_profile_username: str | None = None,
) -> None:
    """
    Aggressive UiAutomator hierarchy refresh (no scroll/swipe/tap).
    Sequence: dump → sleep 0.5 → dump → sleep 0.5 → dump → app_current.
    """
    log(
        "info",
        "followers_force_refresh_started",
        source_profile_username=source_profile_username,
    )
    for i in range(1, 4):
        _followers_poll_hierarchy_refresh(d)
        pkg, act = _followers_pkg_activity_for_scroll_log(d)
        log(
            "info",
            "followers_force_refresh_dump",
            refresh_index=i,
            current_activity=act,
            current_package=pkg,
            source_profile_username=source_profile_username,
        )
        if i < 3:
            time.sleep(0.5)
    try:
        d.app_current()
    except Exception:
        pass
    pkg, act = _followers_pkg_activity_for_scroll_log(d)
    log(
        "info",
        "followers_force_refresh_done",
        refresh_index=3,
        current_activity=act,
        current_package=pkg,
        source_profile_username=source_profile_username,
    )


def followers_refresh_hierarchy_for_candidates(d: u2.Device) -> None:
    """Single dump (legacy); prefer followers_force_hierarchy_refresh for stale XML after visual open."""
    _followers_poll_hierarchy_refresh(d)


def _followers_after_tap_detect_log_payload(
    det: dict[str, Any],
    attempt_index: int,
) -> dict[str, Any]:
    """Structured payload for post-tap followers list detection logs."""
    return {
        "attempt_index": attempt_index,
        "current_activity": det.get("current_activity"),
        "current_package": det.get("current_package"),
        "action_bar_title": det.get("action_bar_title"),
        "recycler_present": det.get("recycler_present"),
        "listview_present": det.get("listview_present"),
        "scrollable_present": det.get("scrollable_present"),
        "scrollable_count": det.get("scrollable_count"),
        "candidate_username_count": det.get("candidate_username_count"),
        "visible_usernames_sample": det.get("visible_usernames_sample"),
        "profile_tabs_absent": det.get("profile_tabs_absent"),
        "current_screen_guess": det.get("current_screen_guess"),
        "is_followers_list": bool(det.get("is_followers_list")),
    }


def _followers_after_tap_immediate_capture_and_detect(
    d: u2.Device,
    source_profile_username: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    After followers stat tap: settle (no swipe), single dump_hierarchy to XML, screenshot,
    app_current, then detect_followers_list_screen (no extra hierarchy dumps on hot path).
    """
    log(
        "info",
        "followers_after_tap_delayed_capture",
        settle_seconds=2.0,
        phase="before_single_dump_and_screenshot",
    )
    time.sleep(2.0)
    _ensure_debug_dirs()
    shot = _SCREENSHOTS_DIR / "followers_after_tap_immediate.png"
    xml_path = _XML_DIR / "followers_after_tap_immediate.xml"
    paths: dict[str, Any] = {"screenshot_path": str(shot), "xml_path": str(xml_path)}
    try:
        screenshot(d, str(shot))
    except Exception as e:
        paths["screenshot_error"] = str(e)
    try:
        try:
            hier = d.dump_hierarchy(compressed=False)
        except TypeError:
            hier = d.dump_hierarchy()
        xml_path.write_text(hier, encoding="utf-8")
        _bump_xml_fetch()
    except Exception as e:
        paths["xml_error"] = str(e)
    try:
        d.app_current()
    except Exception:
        pass
    pkg_meta = _followers_current_pkg_activity(d)
    log(
        "info",
        "followers_after_tap_immediate_capture",
        screenshot_path=paths.get("screenshot_path"),
        xml_path=paths.get("xml_path"),
        current_package=pkg_meta.get("current_package"),
        current_activity=pkg_meta.get("current_activity"),
        action_bar_title=None,
        recycler_present=None,
        listview_present=None,
        scrollable_present=None,
        scrollable_count=None,
        candidate_username_count=None,
        visible_usernames_sample=None,
        current_screen_guess=None,
        phase="before_first_detect",
    )
    det = detect_followers_list_screen(
        d, source_profile_username=source_profile_username
    )
    det = _followers_apply_visual_fallback_if_needed(
        d,
        det,
        paths.get("screenshot_path"),
        source_profile_username,
        phase="immediate_after_capture",
    )
    global _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT
    _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT += 1
    log(
        "info",
        "followers_after_tap_detect_attempt",
        **_followers_after_tap_detect_log_payload(
            det, _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT
        ),
        phase="immediate_after_capture",
    )
    log(
        "info",
        "followers_after_tap_immediate_snapshot",
        screenshot_path=paths.get("screenshot_path"),
        xml_path=paths.get("xml_path"),
        current_package=det.get("current_package"),
        current_activity=det.get("current_activity"),
        action_bar_title=det.get("action_bar_title"),
        recycler_present=det.get("recycler_present"),
        listview_present=det.get("listview_present"),
        scrollable_present=det.get("scrollable_present"),
        scrollable_count=det.get("scrollable_count"),
        candidate_username_count=det.get("candidate_username_count"),
        visible_usernames_sample=det.get("visible_usernames_sample"),
        current_screen_guess=det.get("current_screen_guess"),
        is_followers_list=bool(det.get("is_followers_list")),
    )
    _followers_mark_post_tap_immediate_capture_done()
    if det.get("is_followers_list"):
        log(
            "info",
            "followers_after_tap_detect_success",
            **_followers_after_tap_detect_log_payload(
                det, _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT
            ),
        )
    return paths, det


def _followers_poll_followers_list_opened_after_immediate(
    d: u2.Device,
    source_profile_username: str,
    *,
    first_det: dict[str, Any],
) -> tuple[bool, dict[str, Any], dict[str, Any], int]:
    """
    After immediate post-tap capture+detect failed: at most 3 recovery cycles (no scroll/swipe).
    Each cycle: sleep 0.8s, single dump_hierarchy, app_current, detect_followers_list_screen.
    """
    log(
        "info",
        "followers_list_post_tap_refresh_started",
        phase="recovery_after_immediate_miss_max_3",
    )
    first_det_return = first_det
    last_det = first_det
    global _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT

    def _detect_step(*, phase: str, screenshot_path: str | None) -> dict[str, Any]:
        nonlocal last_det
        global _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT
        det = detect_followers_list_screen(
            d, source_profile_username=source_profile_username
        )
        det = _followers_apply_visual_fallback_if_needed(
            d,
            det,
            screenshot_path,
            source_profile_username,
            phase=phase,
        )
        last_det = det
        _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT += 1
        attempt_index = _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT
        log(
            "info",
            "followers_after_tap_detect_attempt",
            **_followers_after_tap_detect_log_payload(det, attempt_index),
            phase=phase,
        )
        return det

    for recovery_i in range(1, 4):
        time.sleep(0.8)
        log(
            "info",
            "followers_after_tap_delayed_capture",
            settle_seconds=0.8,
            phase=f"recovery_dump_before_detect_{recovery_i}_of_3",
        )
        shot_path = str(
            _SCREENSHOTS_DIR / f"followers_post_tap_recovery_{recovery_i}.png"
        )
        try:
            screenshot(d, shot_path)
        except Exception:
            shot_path = None
        _followers_poll_hierarchy_refresh(d)
        try:
            d.app_current()
        except Exception:
            pass
        det = _detect_step(
            phase=f"recovery_poll_{recovery_i}_of_3",
            screenshot_path=shot_path,
        )
        if det.get("is_followers_list"):
            log(
                "info",
                "followers_after_tap_detect_success",
                **_followers_after_tap_detect_log_payload(
                    det, _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT
                ),
            )
            return True, first_det_return, last_det, _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT

    log(
        "info",
        "followers_list_post_tap_refresh_done",
        phase="recovery_after_immediate_miss_max_3",
    )
    log(
        "info",
        "followers_after_tap_detect_failed",
        **_followers_after_tap_detect_log_payload(
            last_det, _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT
        ),
    )
    return False, first_det_return, last_det, _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT


def _followers_run_followers_list_open_poll_phases(
    d: u2.Device,
    tap_diag: dict[str, Any],
    source_profile_username: str,
) -> tuple[bool, dict[str, Any], dict[str, Any], int]:
    """Immediate capture+detect (no scroll), then up to 3 recovery polls if needed."""
    log(
        "info",
        "followers_list_post_tap_scroll_blocked",
        attempt_index=0,
        post_tap_poll_path_no_programmatic_scroll=True,
        note="post_tap_2s_settle_single_dump_then_detect",
    )
    paths, det_imm = _followers_after_tap_immediate_capture_and_detect(
        d, source_profile_username=source_profile_username
    )
    tap_diag["followers_after_tap_immediate_screenshot_path"] = paths.get(
        "screenshot_path"
    )
    tap_diag["followers_after_tap_immediate_xml_path"] = paths.get("xml_path")
    tap_diag["followers_list_post_tap_capture_done"] = True
    if det_imm.get("is_followers_list"):
        return True, det_imm, det_imm, _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT
    return _followers_poll_followers_list_opened_after_immediate(
        d, source_profile_username, first_det=det_imm
    )


def _log_followers_list_detect_failed(
    d: u2.Device,
    det: dict[str, Any],
    *,
    source_profile_username: str,
    stem: str,
    cap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    c = cap if cap is not None else _followers_debug_capture(d, stem)
    log(
        "warning",
        "followers_list_detect_snapshot",
        source_profile_username=source_profile_username,
        is_followers_list=det.get("is_followers_list"),
        title_match=det.get("title_match"),
        action_bar_title=det.get("action_bar_title"),
        visible_header_texts=det.get("visible_header_texts"),
        recycler_present=det.get("recycler_present"),
        scrollable_present=det.get("scrollable_present"),
        scrollable_count=det.get("scrollable_count"),
        candidate_username_count=det.get("candidate_username_count"),
        visible_usernames_sample=det.get("visible_usernames_sample"),
        screenshot_path=c.get("screenshot_path"),
        xml_path=c.get("xml_path"),
    )
    return c


def _followers_open_build_failure_meta(
    *,
    source_profile_username: str,
    failure_reason: str,
    profile_verified: bool,
    pkg_meta: dict[str, Any],
    screen_guess: str,
    tap_diag: dict[str, Any] | None,
    after_tap_screen_snapshot: dict[str, Any] | None,
    last_poll_snapshot: dict[str, Any] | None,
    cap: dict[str, Any] | None,
    open_method: str,
    used_coord_fallback: bool | None = None,
) -> dict[str, Any]:
    td = tap_diag or {}
    ucf = used_coord_fallback
    if ucf is None:
        ucf = bool(td.get("followers_coord_fallback"))
    return {
        "failure_reason": failure_reason,
        "profile_verified": bool(profile_verified),
        "source_profile_username": source_profile_username,
        "stats_band_texts": list(td.get("stats_band_texts") or []),
        "followers_stat_text": td.get("followers_stat_text"),
        "followers_stat_bounds": td.get("followers_stat_bounds"),
        "stats_band_center_y_source": td.get("stats_band_center_y_source"),
        "tap_x": td.get("tap_x"),
        "tap_y": td.get("tap_y"),
        "tap_method": td.get("tap_method"),
        "open_method": open_method,
        "current_package": pkg_meta.get("current_package"),
        "current_activity": pkg_meta.get("current_activity"),
        "current_screen_guess": screen_guess,
        "after_tap_screen_snapshot": after_tap_screen_snapshot or {},
        "last_poll_snapshot": last_poll_snapshot or {},
        "debug_screenshot_path": (cap or {}).get("screenshot_path"),
        "debug_xml_path": (cap or {}).get("xml_path"),
        "followers_stat_found": td.get("followers_stat_found"),
        "used_coord_fallback": ucf,
        "profile_stats_visible": td.get("profile_stats_visible"),
        "followers_stat_text_detected": td.get("followers_stat_text_detected"),
        "followers_stat_text_bounds": td.get("followers_stat_text_bounds"),
        "followers_stat_tap_x": td.get("followers_stat_tap_x"),
        "followers_stat_tap_y": td.get("followers_stat_tap_y"),
        "followers_stat_tap_source": td.get("followers_stat_tap_source"),
        "followers_stat_text_dump": td.get("followers_stat_text_dump"),
        "followers_stat_coordinate_retry": td.get("followers_stat_coordinate_retry"),
        "profile_rescan_swiped": td.get("profile_rescan_swiped"),
        "debug_before_scan_screenshot_path": td.get("debug_before_scan_screenshot_path"),
        "debug_before_scan_xml_path": td.get("debug_before_scan_xml_path"),
        "debug_after_fallback_screenshot_path": td.get("debug_after_fallback_screenshot_path"),
        "debug_after_fallback_xml_path": td.get("debug_after_fallback_xml_path"),
    }


def _followers_open_emit_failure(
    meta: dict[str, Any],
) -> None:
    """Guaranteed full JSON line for agents / Supabase correlation (includes nulls)."""
    print(
        json.dumps(
            {"event": "followers_list_open_failed_debug", **meta},
            ensure_ascii=False,
            default=str,
        ),
        flush=True,
    )
    log("error", "followers_list_open_failed", _include_null_fields=True, **meta)


def _followers_scroll_list_forward(d: u2.Device) -> bool:
    if _followers_abort_scroll_if_post_tap_lock("_followers_scroll_list_forward"):
        return False
    if not _followers_post_tap_capture_gate_ok():
        log(
            "info",
            "followers_post_tap_scroll_prevented",
            source="_followers_scroll_list_forward",
            reason="post_tap_immediate_capture_not_done",
        )
        return False
    if _FOLLOWERS_VISUAL_XML_STALE_EXHAUSTED:
        log(
            "info",
            "followers_post_tap_scroll_prevented",
            source="_followers_scroll_list_forward",
            reason="followers_visual_xml_stale_exhausted",
        )
        return False
    if (
        _FOLLOWERS_LAST_OPEN_DETECTION_METHOD == "visual_fallback"
        and _FOLLOWERS_LAST_ITER_CANDIDATE_COUNT == 0
        and not _FOLLOWERS_VISUAL_HAD_NONEMPTY_CANDIDATE_ROWS
    ):
        log(
            "info",
            "followers_post_tap_scroll_prevented",
            source="_followers_scroll_list_forward",
            reason="visual_fallback_zero_candidates_wait_for_xml_refresh",
        )
        return False
    log("info", "followers_list_scroll", direction="forward")
    try:
        rv = d(classNameMatches=".*RecyclerView.*")
        if rv.exists(timeout=0.25):
            _followers_log_scroll_or_swipe_about_to_run(
                d,
                source_function="_followers_scroll_list_forward",
                reason="recyclerview_scroll_vert_forward",
            )
            rv.scroll.vert.forward(steps=6)
            time.sleep(0.22)
            return True
    except Exception:
        pass
    try:
        w, h = d.window_size()
        _followers_log_scroll_or_swipe_about_to_run(
            d,
            source_function="_followers_scroll_list_forward",
            reason="fallback_vertical_swipe_followers_list",
        )
        d.swipe(w // 2, int(h * 0.72), w // 2, int(h * 0.32), 0.12)
        time.sleep(0.22)
        return True
    except Exception:
        return False


def scroll_followers_list_forward(d: u2.Device) -> bool:
    """Bounded scroll on the followers RecyclerView (or fallback swipe)."""
    return _followers_scroll_list_forward(d)


def _followers_fetch_hierarchy_xml_raw(d: u2.Device) -> str | None:
    try:
        try:
            return str(d.dump_hierarchy(compressed=False))
        except TypeError:
            return str(d.dump_hierarchy())
    except Exception:
        return None


def followers_dump_visible_nodes_for_debug(
    d: u2.Device,
    source_profile_username: str | None = None,
    *,
    hierarchy_xml: str | None = None,
) -> dict[str, Any]:
    """
    Parse accessibility XML for TextView / ImageView / Button-class nodes in the list band
    (center Y between 15% and 95% of screen height). No taps or scrolls (read-only).
    """
    try:
        h_scr = int(d.window_size()[1])
    except Exception:
        h_scr = 1920
    y_lo = int(h_scr * 0.15)
    y_hi = int(h_scr * 0.95)
    xmls = hierarchy_xml if hierarchy_xml is not None else _followers_fetch_hierarchy_xml_raw(d)
    out: dict[str, Any] = {
        "node_count": 0,
        "textview_count": 0,
        "button_count": 0,
        "imageview_count": 0,
        "sample_nodes": [],
        "nodes": [],
        "parse_error": None,
        "y_band": {"y_lo": y_lo, "y_hi": y_hi, "screen_h": h_scr},
    }
    if not xmls:
        out["parse_error"] = "no_hierarchy_xml"
        return out
    try:
        root = ET.fromstring(xmls)
    except Exception as e:
        out["parse_error"] = f"xml_parse:{e}"
        return out

    def _class_matches_list_node(cls: str) -> bool:
        c = (cls or "").lower()
        if "edittext" in c:
            return False
        return (
            "textview" in c
            or "imageview" in c
            or "imagebutton" in c
            or c.endswith("button")
            or ".button" in c
        )

    nodes_full: list[dict[str, Any]] = []
    for elem in root.iter():
        tag = str(elem.tag or "")
        if tag != "node" and not tag.endswith("}node"):
            continue
        cls = elem.get("class") or ""
        if not _class_matches_list_node(cls):
            continue
        b_raw = elem.get("bounds") or ""
        bd = _dm_parse_bounds_attr_xml(b_raw)
        if not bd:
            continue
        cy = (int(bd["top"]) + int(bd["bottom"])) // 2
        if cy <= y_lo or cy >= y_hi:
            continue
        node = {
            "className": cls,
            "text": (elem.get("text") or "")[:240],
            "resourceId": elem.get("resource-id") or "",
            "contentDescription": (elem.get("content-desc") or "")[:240],
            "bounds": dict(bd),
            "clickable": str(elem.get("clickable", "")).lower() == "true",
            "enabled": str(elem.get("enabled", "")).lower() != "false",
        }
        nodes_full.append(node)
        lc = cls.lower()
        if "textview" in lc:
            out["textview_count"] += 1
        elif "imageview" in lc or "imagebutton" in lc:
            out["imageview_count"] += 1
        elif "button" in lc:
            out["button_count"] += 1

    out["node_count"] = len(nodes_full)
    out["nodes"] = nodes_full
    out["sample_nodes"] = nodes_full[:45]
    pkg, act = _followers_pkg_activity_for_scroll_log(d)
    out["current_package"] = pkg
    out["current_activity"] = act
    out["source_profile_username"] = source_profile_username
    return out


def _followers_run_visual_open_xml_empty_diagnostic(
    d: u2.Device,
    *,
    source_profile_username: str,
    det_after: dict[str, Any],
) -> None:
    """
    Final capture + XML node summary when list was validated visually but handles are still missing.
    Sets _FOLLOWERS_ENGINE_STOP_REASON (no follow / scroll / candidate tap here).
    """
    global _FOLLOWERS_ENGINE_STOP_REASON
    engine_reason_at_entry = _FOLLOWERS_ENGINE_STOP_REASON
    cap: dict[str, Any] = {}
    try:
        cap = _followers_debug_capture(d, "followers_visual_open_xml_empty_final")
    except Exception as e:
        cap = {"screenshot_error": str(e)}
    hier_text: str | None = None
    try:
        xp = cap.get("xml_path")
        if xp:
            hier_text = Path(xp).read_text(encoding="utf-8")
    except Exception:
        hier_text = None
    if not hier_text:
        hier_text = _followers_fetch_hierarchy_xml_raw(d)

    dump = followers_dump_visible_nodes_for_debug(
        d,
        source_profile_username=source_profile_username,
        hierarchy_xml=hier_text,
    )
    pkg, act = _followers_pkg_activity_for_scroll_log(d)
    log(
        "info",
        "followers_candidate_node_dump",
        node_count=dump.get("node_count"),
        textview_count=dump.get("textview_count"),
        button_count=dump.get("button_count"),
        imageview_count=dump.get("imageview_count"),
        sample_nodes=dump.get("sample_nodes"),
        source_profile_username=source_profile_username,
        current_activity=act,
        current_package=pkg,
        parse_error=dump.get("parse_error"),
        y_band=dump.get("y_band"),
    )
    if (
        bool(getattr(config, "ENABLE_FOLLOWERS_VISUAL_CANDIDATE_DIAGNOSTIC", True))
        and _FOLLOWERS_LAST_OPEN_DETECTION_METHOD == "visual_fallback"
        and engine_reason_at_entry in FOLLOWERS_ENGINE_XML_STALE_EXIT_REASONS
        and int(det_after.get("candidate_username_count") or 0) == 0
    ):
        shot = cap.get("screenshot_path")
        if shot:
            followers_visual_candidate_diagnostic(
                d,
                screenshot_path=str(shot),
                source_profile_username=source_profile_username,
            )
    log(
        "error",
        "followers_visual_open_but_xml_empty_diagnostic",
        reason="visual_open_xml_empty",
        source_profile_username=source_profile_username,
        screenshot_path=cap.get("screenshot_path"),
        xml_path=cap.get("xml_path"),
        candidate_username_count=det_after.get("candidate_username_count"),
        visible_usernames_sample=det_after.get("visible_usernames_sample"),
        recycler_present=det_after.get("recycler_present"),
        scrollable_present=det_after.get("scrollable_present"),
        scrollable_count=det_after.get("scrollable_count"),
        current_screen_guess=det_after.get("current_screen_guess"),
        open_detection_method="visual_fallback",
        node_count=dump.get("node_count"),
        textview_count=dump.get("textview_count"),
        button_count=dump.get("button_count"),
        imageview_count=dump.get("imageview_count"),
    )
    _FOLLOWERS_ENGINE_STOP_REASON = "visual_open_xml_empty"


def _iter_followers_candidates_collect(
    d: u2.Device,
    *,
    source_profile_username: str,
    runtime_seen: set[str],
) -> list[dict[str, Any]]:
    """Parse visible @handles from current UiAutomator tree (no scroll/tap)."""
    try:
        h = int(d.window_size()[1])
    except Exception:
        h = 1920
    y_min = int(h * 0.08)
    source_key = _normalize_handle(source_profile_username or "")
    by_user: dict[str, dict[str, Any]] = {}

    try:
        for el in d(className="android.widget.TextView").all():
            try:
                inf = el.info
                raw_t = (inf.get("text") or "").strip().lstrip("@")
                if not _FOLLOWERS_HANDLE_RE.match(raw_t):
                    continue
                key = _normalize_handle(raw_t)
                if not key or key == source_key:
                    continue
                b = inf.get("bounds") or {}
                cy = (int(b.get("top", 0)) + int(b.get("bottom", 0))) // 2
                if cy < y_min or cy > int(h * 0.96):
                    continue
                c = _followers_bounds_center(b)
                if c is None:
                    continue
                rid = str(inf.get("resourceName") or "") or None
                prev = by_user.get(key)
                if prev is None or cy < prev["row_center"][1]:
                    by_user[key] = {
                        "username": raw_t,
                        "bounds": dict(b),
                        "row_center": [c[0], c[1]],
                        "already_seen_runtime": key in runtime_seen,
                        "resource_id": rid,
                    }
            except Exception:
                continue
    except Exception:
        pass

    rows = sorted(by_user.values(), key=lambda r: (r["row_center"][1], r["username"]))
    for r in rows:
        log(
            "info",
            "followers_candidate_seen",
            username=r["username"],
            source_profile_username=source_profile_username,
            already_seen_runtime=bool(r["already_seen_runtime"]),
            resource_id=r.get("resource_id"),
        )
    return rows


def iter_followers_candidates(
    d: u2.Device,
    *,
    source_profile_username: str,
    runtime_seen: set[str],
) -> list[dict[str, Any]]:
    """
    Visible follower handles on the current followers list surface.
    After visual_fallback open, first empty parse triggers wait + triple hierarchy dump + re-detect (no scroll).
    """
    global _FOLLOWERS_VISUAL_XML_RECOVERY_ATTEMPTED
    global _FOLLOWERS_VISUAL_XML_STALE_EXHAUSTED
    global _FOLLOWERS_LAST_ITER_CANDIDATE_COUNT
    global _FOLLOWERS_VISUAL_HAD_NONEMPTY_CANDIDATE_ROWS
    global _FOLLOWERS_ENGINE_STOP_REASON

    rows = _iter_followers_candidates_collect(
        d, source_profile_username=source_profile_username, runtime_seen=runtime_seen
    )

    if (
        not rows
        and _FOLLOWERS_LAST_OPEN_DETECTION_METHOD == "visual_fallback"
        and not _FOLLOWERS_VISUAL_XML_RECOVERY_ATTEMPTED
    ):
        _FOLLOWERS_VISUAL_XML_RECOVERY_ATTEMPTED = True
        log(
            "info",
            "followers_candidates_retry_after_visual_match",
            source_profile_username=source_profile_username,
            open_detection_method="visual_fallback",
            wait_before_force_refresh_s=2.5,
        )
        time.sleep(2.5)
        followers_force_hierarchy_refresh(d, source_profile_username=source_profile_username)
        det_after = detect_followers_list_screen(
            d, source_profile_username=source_profile_username
        )
        rows = _iter_followers_candidates_collect(
            d, source_profile_username=source_profile_username, runtime_seen=runtime_seen
        )
        log(
            "info",
            "followers_candidates_retry_result",
            source_profile_username=source_profile_username,
            open_detection_method="visual_fallback",
            candidate_rows_parsed=len(rows),
            candidate_username_count=det_after.get("candidate_username_count"),
            visible_usernames_sample=det_after.get("visible_usernames_sample"),
            recycler_present=det_after.get("recycler_present"),
            scrollable_present=det_after.get("scrollable_present"),
            current_screen_guess=det_after.get("current_screen_guess"),
        )
        if not rows:
            _FOLLOWERS_VISUAL_XML_STALE_EXHAUSTED = True
            _FOLLOWERS_ENGINE_STOP_REASON = "followers_xml_still_stale_after_force_refresh"
            log(
                "error",
                "followers_xml_still_stale_after_force_refresh",
                source_profile_username=source_profile_username,
                open_detection_method="visual_fallback",
                candidate_username_count=det_after.get("candidate_username_count"),
                visible_usernames_sample=det_after.get("visible_usernames_sample"),
                recycler_present=det_after.get("recycler_present"),
                scrollable_present=det_after.get("scrollable_present"),
                current_screen_guess=det_after.get("current_screen_guess"),
                candidate_rows_parsed=0,
            )
            _followers_run_visual_open_xml_empty_diagnostic(
                d,
                source_profile_username=source_profile_username,
                det_after=det_after,
            )

    _FOLLOWERS_LAST_ITER_CANDIDATE_COUNT = len(rows)
    if rows:
        _FOLLOWERS_VISUAL_HAD_NONEMPTY_CANDIDATE_ROWS = True
    return rows


def _followers_open_success_payload(
    tap_diag: dict[str, Any],
    *,
    after_tap_det: dict[str, Any],
    last_det: dict[str, Any],
    open_method: str,
    pkg_meta: dict[str, Any],
    source_profile_username: str,
    profile_verified: bool,
) -> dict[str, Any]:
    return {
        "source_profile_username": source_profile_username,
        "profile_verified": bool(profile_verified),
        "tap_x": tap_diag.get("tap_x"),
        "tap_y": tap_diag.get("tap_y"),
        "tap_method": tap_diag.get("tap_method"),
        "profile_stats_visible": tap_diag.get("profile_stats_visible"),
        "followers_stat_text_dump": tap_diag.get("followers_stat_text_dump"),
        "followers_stat_coordinate_retry": tap_diag.get("followers_stat_coordinate_retry"),
        "after_tap_screen_snapshot": after_tap_det,
        "last_poll_snapshot": last_det,
        "open_method": open_method,
        "current_package": pkg_meta.get("current_package"),
        "signals": last_det.get("signals"),
        "sample": last_det.get("visible_usernames_sample"),
        "open_detection_method": last_det.get("open_detection_method") or "xml",
    }


def open_followers_list_from_profile(
    d: u2.Device,
    source_profile_username: str,
    pkg: str | None = None,
    *,
    profile_verified: bool = False,
) -> tuple[bool, dict[str, Any]]:
    pkg = pkg or getattr(config, "INSTAGRAM_PACKAGE", "") or ""
    log(
        "info",
        "followers_list_open_started",
        source_profile_username=source_profile_username,
        package=pkg,
        profile_verified=bool(profile_verified),
        profile_verified_before_tap=bool(profile_verified),
    )
    _followers_reset_post_tap_capture_gate()
    _followers_set_post_tap_detection_lock(False)
    _followers_reset_followers_list_session_state()
    pkg_meta = _followers_current_pkg_activity(d)
    screen_guess = _guess_profile_screen(d, pkg, source_profile_username)

    if not verify_app_foreground(d, pkg):
        cap = _followers_debug_capture(d, "followers_open_not_foreground")
        tap_diag_obs: dict[str, Any] = {
            "stats_band_texts": [],
            "profile_stats_visible": [],
            "followers_stat_text_dump": [],
        }
        try:
            w, h = d.window_size()
        except Exception:
            w, h = 1080, 1920
        try:
            band = _collect_profile_stats_band_texts(d, w, h)
            tap_diag_obs["stats_band_texts"] = [
                (r.get("text") or "").strip() for r in band[:40]
            ]
            dump_nf = _followers_collect_profile_text_dump(d, w, h)
            tap_diag_obs["followers_stat_text_dump"] = dump_nf
            tap_diag_obs["profile_stats_visible"] = list(dump_nf)
        except Exception:
            pass
        fmeta = _followers_open_build_failure_meta(
            source_profile_username=source_profile_username,
            failure_reason="not_foreground",
            profile_verified=profile_verified,
            pkg_meta=pkg_meta,
            screen_guess=screen_guess,
            tap_diag=tap_diag_obs,
            after_tap_screen_snapshot={},
            last_poll_snapshot={},
            cap=cap,
            open_method="blocked_not_foreground",
            used_coord_fallback=False,
        )
        _followers_open_emit_failure(fmeta)
        return False, fmeta

    # After verify_profile success (callers pass profile_verified=True): let header/stats render.
    if profile_verified:
        time.sleep(1.2)
    else:
        time.sleep(0.35)
    pkg_meta.update(_followers_current_pkg_activity(d))
    before_scan_cap = _followers_debug_capture(d, "followers_profile_before_scan")
    log("info", "followers_profile_pre_tap_swipe_skipped", followers_profile_pre_tap_swipe_skipped=True)

    element_ok, tap_diag = _tap_profile_followers_stat(d, pre_scan=None)
    tap_diag["debug_before_scan_screenshot_path"] = before_scan_cap.get("screenshot_path")
    tap_diag["debug_before_scan_xml_path"] = before_scan_cap.get("xml_path")
    tap_diag["profile_rescan_swiped"] = False
    used_coord_fallback = False
    open_method = "element_tap"

    if not element_ok:
        _followers_reset_post_tap_capture_gate()
        coord_ok, tap_diag = _tap_followers_coord_fallback(d, tap_diag)
        used_coord_fallback = True
        open_method = "element_miss_then_coordinate"
        if not coord_ok:
            cap = _followers_debug_capture(d, "followers_stat_tap_miss")
            det_pre = detect_followers_list_screen(
                d, source_profile_username=source_profile_username
            )
            fmeta = _followers_open_build_failure_meta(
                source_profile_username=source_profile_username,
                failure_reason="followers_stat_tap_miss",
                profile_verified=profile_verified,
                pkg_meta=pkg_meta,
                screen_guess=screen_guess,
                tap_diag=tap_diag,
                after_tap_screen_snapshot=det_pre,
                last_poll_snapshot=det_pre,
                cap=cap,
                open_method=open_method,
                used_coord_fallback=used_coord_fallback,
            )
            _followers_open_emit_failure(fmeta)
            return False, fmeta

    _followers_enable_post_tap_detection_lock(source_profile_username)
    log(
        "info",
        "followers_post_tap_lock_state_check",
        lock_active=POST_TAP_FOLLOWERS_DETECTION_IN_PROGRESS,
        tap_x=tap_diag.get("tap_x"),
        tap_y=tap_diag.get("tap_y"),
        tap_method=tap_diag.get("tap_method"),
        open_method=open_method,
    )
    try:
        opened, after_tap_det, last_det, poll_attempts = _followers_run_followers_list_open_poll_phases(
            d, tap_diag, source_profile_username
        )
    except Exception:
        _followers_release_post_tap_detection_lock(source_profile_username)
        raise
    if tap_diag.get("followers_exact_rid_used"):
        pkg_post = _followers_current_pkg_activity(d)
        log(
            "info",
            "followers_exact_rid_click_result",
            current_activity=pkg_post.get("current_activity"),
            current_package=pkg_post.get("current_package"),
            followers_screen_detected_after_tap=bool(opened),
            poll_attempts=poll_attempts,
        )
    if opened:
        log(
            "info",
            "followers_list_open_success",
            source_profile_username=source_profile_username,
            profile_verified=bool(profile_verified),
            signals=last_det.get("signals"),
            sample=last_det.get("visible_usernames_sample"),
            tap_method=tap_diag.get("tap_method"),
            tap_x=tap_diag.get("tap_x"),
            tap_y=tap_diag.get("tap_y"),
            stats_band_texts=tap_diag.get("stats_band_texts"),
            followers_stat_text=tap_diag.get("followers_stat_text"),
            followers_stat_bounds=tap_diag.get("followers_stat_bounds"),
            followers_stat_text_detected=tap_diag.get("followers_stat_text_detected"),
            followers_stat_text_bounds=tap_diag.get("followers_stat_text_bounds"),
            followers_stat_tap_x=tap_diag.get("followers_stat_tap_x"),
            followers_stat_tap_y=tap_diag.get("followers_stat_tap_y"),
            followers_stat_tap_source=tap_diag.get("followers_stat_tap_source"),
            current_package=pkg_meta.get("current_package"),
            after_tap_screen_snapshot=after_tap_det,
            last_poll_snapshot=last_det,
            open_method=open_method,
            open_detection_method=last_det.get("open_detection_method") or "xml",
        )
        _followers_release_post_tap_detection_lock(source_profile_username)
        _followers_set_last_open_detection_method(
            str(last_det.get("open_detection_method") or "xml")
        )
        return True, _followers_open_success_payload(
            tap_diag,
            after_tap_det=after_tap_det,
            last_det=last_det,
            open_method=open_method,
            pkg_meta=pkg_meta,
            source_profile_username=source_profile_username,
            profile_verified=profile_verified,
        )

    if element_ok and not used_coord_fallback:
        _followers_reset_post_tap_capture_gate()
        coord_ok2, tap_diag = _tap_followers_coord_fallback(d, tap_diag)
        used_coord_fallback = True
        open_method = "element_then_coordinate_retry"
        if coord_ok2:
            _followers_enable_post_tap_detection_lock(source_profile_username)
            log(
                "info",
                "followers_post_tap_lock_state_check",
                lock_active=POST_TAP_FOLLOWERS_DETECTION_IN_PROGRESS,
                tap_x=tap_diag.get("tap_x"),
                tap_y=tap_diag.get("tap_y"),
                tap_method=tap_diag.get("tap_method"),
                open_method=open_method,
            )
            try:
                opened2, after_tap_det2, last_det2, _poll_attempts2 = _followers_run_followers_list_open_poll_phases(
                    d, tap_diag, source_profile_username
                )
            except Exception:
                _followers_release_post_tap_detection_lock(source_profile_username)
                raise
            if opened2:
                log(
                    "info",
                    "followers_list_open_success",
                    source_profile_username=source_profile_username,
                    profile_verified=bool(profile_verified),
                    signals=last_det2.get("signals"),
                    sample=last_det2.get("visible_usernames_sample"),
                    tap_method=tap_diag.get("tap_method"),
                    tap_x=tap_diag.get("tap_x"),
                    tap_y=tap_diag.get("tap_y"),
                    stats_band_texts=tap_diag.get("stats_band_texts"),
                    followers_stat_text=tap_diag.get("followers_stat_text"),
                    followers_stat_bounds=tap_diag.get("followers_stat_bounds"),
                    followers_stat_text_detected=tap_diag.get("followers_stat_text_detected"),
                    followers_stat_text_bounds=tap_diag.get("followers_stat_text_bounds"),
                    followers_stat_tap_x=tap_diag.get("followers_stat_tap_x"),
                    followers_stat_tap_y=tap_diag.get("followers_stat_tap_y"),
                    followers_stat_tap_source=tap_diag.get("followers_stat_tap_source"),
                    current_package=pkg_meta.get("current_package"),
                    after_tap_screen_snapshot=after_tap_det2,
                    last_poll_snapshot=last_det2,
                    coord_retry_after_element=True,
                    open_method=open_method,
                    open_detection_method=last_det2.get("open_detection_method") or "xml",
                )
                _followers_release_post_tap_detection_lock(source_profile_username)
                _followers_set_last_open_detection_method(
                    str(last_det2.get("open_detection_method") or "xml")
                )
                return True, _followers_open_success_payload(
                    tap_diag,
                    after_tap_det=after_tap_det2,
                    last_det=last_det2,
                    open_method=open_method,
                    pkg_meta=pkg_meta,
                    source_profile_username=source_profile_username,
                    profile_verified=profile_verified,
                )
            after_tap_det, last_det = after_tap_det2, last_det2

    cap = _followers_debug_capture(d, "followers_list_open_failed")
    _log_followers_list_detect_failed(
        d,
        last_det,
        source_profile_username=source_profile_username,
        stem="followers_list_detect_failed",
        cap=cap,
    )
    fmeta = _followers_open_build_failure_meta(
        source_profile_username=source_profile_username,
        failure_reason="screen_not_detected",
        profile_verified=profile_verified,
        pkg_meta=pkg_meta,
        screen_guess=screen_guess,
        tap_diag=tap_diag,
        after_tap_screen_snapshot=after_tap_det,
        last_poll_snapshot=last_det,
        cap=cap,
        open_method=open_method,
        used_coord_fallback=used_coord_fallback,
    )
    _followers_open_emit_failure(fmeta)
    _followers_release_post_tap_detection_lock(source_profile_username)
    try:
        _followers_profile_post_failure_debug_swipe(
            d,
            pkg_meta,
            followers_open_failure_logged=True,
        )
    except Exception:
        pass
    return False, fmeta


def open_follower_profile_from_list(
    d: u2.Device,
    candidate: dict[str, Any],
    source_profile_username: str,
    pkg: str | None = None,
) -> bool:
    pkg = pkg or getattr(config, "INSTAGRAM_PACKAGE", "") or ""
    un = str(candidate.get("username") or "").strip()
    log(
        "info",
        "follower_profile_open_started",
        follower_username=un,
        source_profile_username=source_profile_username,
        row_center=candidate.get("row_center"),
    )
    rc = candidate.get("row_center") or [0, 0]
    try:
        d.click(int(rc[0]), int(rc[1]))
    except Exception as e:
        log(
            "error",
            "follower_profile_open_failed",
            follower_username=un,
            error=str(e),
            reason="click_failed",
        )
        return False
    time.sleep(float(getattr(config, "PROFILE_POST_TAP_STABILIZE_S", 0.12)))
    ok = verify_profile(d, un)
    if ok:
        log(
            "info",
            "follower_profile_open_success",
            follower_username=un,
            source_profile_username=source_profile_username,
        )
    else:
        log(
            "error",
            "follower_profile_open_failed",
            follower_username=un,
            source_profile_username=source_profile_username,
            reason="verify_profile_failed",
        )
    return ok


def return_to_followers_list(
    d: u2.Device,
    source_profile_username: str,
    pkg: str | None = None,
    *,
    max_retries: int | None = None,
) -> tuple[bool, str]:
    """Back from follower profile to followers list; optional reopen from source profile."""
    pkg = pkg or getattr(config, "INSTAGRAM_PACKAGE", "") or ""
    retries = max_retries
    if retries is None:
        retries = int(getattr(config, "FOLLOWERS_LIST_RETURN_MAX_RETRIES", 2))
    for attempt in range(max(0, retries) + 1):
        try:
            d.press("back")
        except Exception:
            pass
        time.sleep(0.38)
        det = detect_followers_list_screen(d, source_profile_username=source_profile_username)
        if det.get("is_followers_list"):
            log(
                "info",
                "followers_list_recovered",
                source_profile_username=source_profile_username,
                attempt=attempt,
                method="back",
            )
            return True, "back"
    log(
        "warning",
        "followers_list_reopen_fallback",
        source_profile_username=source_profile_username,
    )
    if verify_profile(d, source_profile_username):
        ok_reopen, _reopen_meta = open_followers_list_from_profile(
            d, source_profile_username, pkg, profile_verified=True
        )
        if ok_reopen:
            log(
                "info",
                "followers_list_recovered",
                source_profile_username=source_profile_username,
                method="reopen_from_source_profile",
            )
            return True, "reopen_from_source_profile"
    log("error", "followers_list_return_failed", source_profile_username=source_profile_username)
    return False, "failed"


def send_dm_safe(
    d: u2.Device,
    username: str,
    draft_text: str,
    dm_state: str,
    *,
    target_row: Any = None,
) -> dict[str, Any]:
    global _LAST_DM_SEND_RESULT
    _ = target_row
    msg = str(draft_text or "")
    enable_real = bool(getattr(config, "ENABLE_REAL_DM_SEND", False))
    out: dict[str, Any] = {
        "target_username": username,
        "thread_state": dm_state,
        "enable_real_send": enable_real,
        "message_len": len(msg),
        "precheck_ok": True,
        "sent": False,
        "reason": None,
        "blocked_event": None,
        "failure_event": None,
    }

    if not enable_real:
        out["blocked_event"] = "dm_send_blocked_config_disabled"
        _LAST_DM_SEND_RESULT = dict(out)
        return out

    if dm_state == "existing_thread" and bool(
        getattr(config, "SEND_DM_SKIP_EXISTING_THREAD", True)
    ):
        out["blocked_event"] = "dm_send_blocked_existing_thread"
        _LAST_DM_SEND_RESULT = dict(out)
        return out

    composer = _dm_find_focus_composer(d)
    if composer is None:
        out["precheck_ok"] = False
        out["reason"] = "no_composer"
        _LAST_DM_SEND_RESULT = dict(out)
        return out

    try:
        cur = composer.get_text() or ""
    except Exception:
        cur = ""
    out["composer_text_len_before_send"] = len(cur)
    draft_ok = cur.strip() == msg.strip()
    out["draft_matches_before_send"] = draft_ok

    btn, status, meta = wait_for_dm_send_button_after_draft(
        d,
        composer,
        thread_state=dm_state,
        draft_matches_expected=bool(draft_ok),
    )
    out["send_button_candidate_count"] = meta.get("send_button_candidate_count", 0)
    out["coordinate_fallback_used"] = bool(meta.get("send_button_coordinate_fallback"))
    w, h = _dm_screen_size_for_dm(d)
    cb = _dm_composer_bounds_u2(composer)

    if status != "ok" or btn is None:
        out["precheck_ok"] = False
        out["reason"] = "send_button_missing"
        try:
            _ensure_debug_dirs()
            stem = f"dm_send_missing_{int(time.time() * 1000)}"
            ss_path = str(_SCREENSHOTS_DIR / f"{stem}.png")
            xml_path = str(_XML_DIR / f"{stem}.xml")
            screenshot(d, ss_path)
            try:
                hier = d.dump_hierarchy(compressed=False)
            except Exception:
                hier = d.dump_hierarchy()
            with open(xml_path, "w", encoding="utf-8") as fh:
                fh.write(hier)
            art = _dm_send_button_debug_artifacts(
                hierarchy_xml=hier,
                composer_bounds=cb or {},
                screen_w=w,
                screen_h=h,
                debug_screenshot_path=ss_path,
                debug_xml_path=xml_path,
            )
            out.update(art)
        except Exception as e:
            out["debug_screenshot_error"] = str(e)
        _LAST_DM_SEND_RESULT = dict(out)
        return out

    try:
        btn.click()
        out["sent"] = True
    except Exception as e:
        out["failure_event"] = "dm_sent_failed"
        out["reason"] = str(e)
    _LAST_DM_SEND_RESULT = dict(out)
    return out


def clear_dm_draft(d: u2.Device) -> bool:
    t0 = time.perf_counter()
    ed = _dm_find_focus_composer(d)
    if ed is None:
        _perf["dm_draft_clear_ms"] = (time.perf_counter() - t0) * 1000
        return False
    try:
        ed.click()
        time.sleep(0.05)
        try:
            ed.clear_text()
        except Exception:
            ed.set_text("")
    except Exception:
        _perf["dm_draft_clear_ms"] = (time.perf_counter() - t0) * 1000
        return False
    try:
        left = (ed.get_text() or "").strip()
    except Exception:
        left = "?"
    _perf["dm_draft_clear_ms"] = (time.perf_counter() - t0) * 1000
    return len(left) == 0


def finalize_dm_draft_before_back(d: u2.Device) -> bool:
    t0 = time.perf_counter()
    kt0 = time.perf_counter()
    try:
        _try_dismiss_keyboard_light(d)
    except Exception:
        pass
    _perf["keyboard_hide_ms"] = (time.perf_counter() - kt0) * 1000
    _perf["finalize_before_back_ms"] = (time.perf_counter() - t0) * 1000
    return True


def return_to_profile_from_dm(d: u2.Device, username: str, pkg: str | None = None) -> bool:
    t0 = time.perf_counter()
    try:
        _try_dismiss_keyboard_light(d)
    except Exception:
        pass
    pkg = pkg or config.INSTAGRAM_PACKAGE
    deadline = time.monotonic() + float(getattr(config, "DM_BACK_TO_PROFILE_MAX_WAIT_S", 3.0))
    t_back = time.perf_counter()
    presses = 0
    max_backs = 18
    while time.monotonic() < deadline and presses < max_backs:
        if verify_profile(d, username):
            _perf["back_press_ms"] = (time.perf_counter() - t_back) * 1000
            _perf["profile_detect_wait_ms"] = (time.perf_counter() - t0) * 1000
            _perf["dm_back_to_profile_ms"] = (time.perf_counter() - t0) * 1000
            return True
        try:
            d.press("back")
            presses += 1
        except Exception:
            break
        time.sleep(float(getattr(config, "DM_BACK_TO_PROFILE_POLL_S", 0.08)))
    _perf["back_press_ms"] = (time.perf_counter() - t_back) * 1000
    _perf["profile_detect_wait_ms"] = (time.perf_counter() - t0) * 1000
    _perf["dm_back_to_profile_ms"] = (time.perf_counter() - t0) * 1000
    return False


def cleanup_dm_after_send_button_missing(d, pkg=None) -> bool:
    """
    Best-effort cleanup after send button missing.
    Must not send anything.
    Goal:
    - clear draft if possible
    - hide keyboard if possible
    - go back to profile if currently in DM
    - return True/False but never raise
    """
    try:
        clear_dm_draft(d)
    except Exception:
        pass
    try:
        finalize_dm_draft_before_back(d)
    except Exception:
        pass
    try:
        p = str(pkg or config.INSTAGRAM_PACKAGE or "")
        return bool(return_to_profile_from_dm(d, "", p))
    except Exception:
        pass
    return False


def verify_app_foreground(d: u2.Device, package: str | None = None) -> bool:
    package = package or config.INSTAGRAM_PACKAGE
    try:
        cur = d.app_current()
        pkg = (cur or {}).get("package", "")
        ok = pkg == package
        log("info", "app_foreground_check", package=package, current=pkg, ok=ok)
        return ok
    except Exception as e:
        log("error", "app_foreground_check_failed", error=str(e))
        return False
