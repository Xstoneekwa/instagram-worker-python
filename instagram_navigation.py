"""Safe Instagram navigation (search → Accounts → profile). No social actions."""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Callable

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


def instagram_warm_session_eligible(d: u2.Device, pkg: str) -> tuple[bool, str]:
    """Skip force-stop when IG is foreground and UI looks healthy."""
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


def is_lightweight_search_screen(d: u2.Device, pkg: str) -> bool:
    """
    True if IG search entry is likely active: foreground, top-band EditText, optional search tab selected.
    No XML / dump.
    """
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


def return_to_search_from_profile(d: u2.Device, pkg: str) -> bool:
    """
    One back + poll for lightweight search. Sets search_back_to_search_ms.
    On success applies search surface reuse metrics (skip open_search).
    """
    global _perf
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


def verify_app_foreground(d: u2.Device, package: str) -> bool:
    try:
        cur = d.app_current()
        pkg = (cur or {}).get("package", "")
        ok = pkg == package
        log("info", "app_foreground_check", package=package, current=pkg, ok=ok)
        return ok
    except Exception as e:
        log("error", "app_foreground_check_failed", error=str(e))
        return False
