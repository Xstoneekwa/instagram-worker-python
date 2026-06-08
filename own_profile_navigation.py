"""Navigation to the logged-in account's own profile and own followers list (V4.1)."""

from __future__ import annotations

import time
from typing import Any

import uiautomator2 as u2

import config
from instagram_navigation import (
    PROFILE_HEADER_FOLLOWERS_STACKED_FAMILIAR_RID,
    _collect_profile_stats_band_texts,
    _followers_profile_tabs_visible,
    _normalize_handle,
    _tap_profile_followers_stat,
    detect_followers_list_screen,
    followers_session_list_committed_open_for,
    followers_session_merge_det_for_committed_visual_surface,
    open_followers_list_from_profile,
    verify_app_foreground,
    verify_profile,
)
from logs import log

_PROFILE_TAB_RID_SUFFIXES: tuple[str, ...] = (
    "profile_tab",
    "tab_profile",
    "bottom_bar_profile",
    "profile_avatar",
)


def _instagram_package_candidates(d: u2.Device) -> list[str]:
    pkgs: list[str] = []
    try:
        cur = (d.app_current() or {}).get("package")
        if cur:
            pkgs.append(str(cur))
    except Exception:
        pass
    cfg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "").strip()
    if cfg and cfg not in pkgs:
        pkgs.append(cfg)
    if "com.instagram.android" not in pkgs:
        pkgs.append("com.instagram.android")
    return pkgs


def open_own_profile_from_bottom_nav(d: u2.Device) -> bool:
    """Open own profile via bottom navigation profile tab."""
    log("info", "welcome_baseline_own_profile_open_started")
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "")
    if not verify_app_foreground(d, pkg):
        log("info", "welcome_baseline_own_profile_open_failed", reason="not_foreground")
        return False

    clicked = False
    click_name = ""
    for suffix in _PROFILE_TAB_RID_SUFFIXES:
        try:
            sel = d(resourceIdMatches=f".*:id/{suffix}")
            if sel.wait(timeout=0.08):
                sel.click()
                clicked = True
                click_name = f"rid_matches_{suffix}"
                break
        except Exception:
            continue

    if not clicked:
        for ipkg in _instagram_package_candidates(d):
            for suffix in _PROFILE_TAB_RID_SUFFIXES:
                rid = f"{ipkg}:id/{suffix}"
                try:
                    s = d(resourceId=rid)
                    if s.wait(timeout=0.06):
                        s.click()
                        clicked = True
                        click_name = rid
                        break
                except Exception:
                    continue
            if clicked:
                break

    if not clicked:
        for name, factory in (
            ("desc_profile_en", lambda: d(descriptionContains="Profile")),
            ("desc_profile_fr", lambda: d(descriptionContains="Profil")),
            ("text_profile", lambda: d(text="Profile")),
        ):
            try:
                o = factory()
                if o.wait(timeout=0.08):
                    o.click()
                    clicked = True
                    click_name = name
                    break
            except Exception:
                continue

    if not clicked:
        try:
            w, h = d.window_size()
            d.click(int(w * 0.92), int(h * 0.94))
            click_name = "percent_fallback_bottom_right"
            clicked = True
        except Exception:
            pass

    settle = float(getattr(config, "WELCOME_BASELINE_OWN_PROFILE_SETTLE_S", 1.2) or 1.2)
    if settle > 0:
        time.sleep(settle)

    if not clicked:
        log("info", "welcome_baseline_own_profile_open_failed", reason="profile_tab_not_found")
        return False

    log(
        "info",
        "welcome_baseline_own_profile_open_success",
        selector=click_name,
    )
    return True


def _header_has_follow_others_cta(d: u2.Device) -> bool:
    """True when a Follow/Suivre CTA is visible anywhere on screen (not header-scoped)."""
    probes = (
        lambda: d(text="Follow"),
        lambda: d(text="Suivre"),
        lambda: d(textContains="Follow back"),
        lambda: d(descriptionContains="Follow"),
    )
    for fact in probes:
        try:
            if fact().exists(timeout=0.06):
                return True
        except Exception:
            continue
    return False


def _strict_own_profile_username_verified(
    d: u2.Device,
    expected_username: str,
) -> tuple[bool, dict[str, Any]]:
    """Require exact handle proof (live signals or hierarchy) before bypassing Follow CTA heuristics."""
    meta: dict[str, Any] = {}
    exp = _normalize_handle(expected_username or "")
    if not exp:
        return False, meta

    ok = verify_profile(d, expected_username)
    meta["verify_profile_ok"] = bool(ok)
    if ok:
        meta["verification_method"] = "verify_profile"
        return True, meta

    try:
        from account_identity_guard import (
            _dump_hierarchy,
            _extract_own_profile_username_from_hierarchy,
            normalize_account_username,
        )

        hierarchy = _dump_hierarchy(d)
        actual_raw, method, hier_meta = _extract_own_profile_username_from_hierarchy(hierarchy)
        meta["hierarchy_meta"] = hier_meta
        if normalize_account_username(actual_raw) == exp:
            meta["verification_method"] = f"own_profile_username_exact:{method}"
            return True, meta
    except Exception:
        pass
    return False, meta


def verify_own_profile(
    d: u2.Device,
    expected_username: str,
) -> tuple[bool, dict[str, Any]]:
    """
    Confirm we are on the logged-in account profile (not a candidate / external profile).
    """
    meta: dict[str, Any] = {
        "expected_username": str(expected_username or ""),
        "profile_tabs_visible": False,
        "stats_band_present": False,
        "follow_cta_on_header": False,
        "on_followers_list": False,
    }
    exp = _normalize_handle(expected_username or "")
    det_list = detect_followers_list_screen(
        d, source_profile_username=str(expected_username or "")
    )
    meta["on_followers_list"] = bool(det_list.get("is_followers_list"))
    if meta["on_followers_list"]:
        log(
            "info",
            "welcome_baseline_own_profile_open_failed",
            reason="still_on_followers_list",
        )
        return False, meta

    meta["profile_tabs_visible"] = bool(_followers_profile_tabs_visible(d))
    try:
        w, h = d.window_size()
        band = _collect_profile_stats_band_texts(d, w, h)
        meta["stats_band_present"] = bool(band)
    except Exception:
        meta["stats_band_present"] = False

    meta["follow_cta_on_header"] = _header_has_follow_others_cta(d)

    strict_username_ok = False
    if exp:
        strict_username_ok, strict_meta = _strict_own_profile_username_verified(
            d,
            expected_username,
        )
        meta.update(strict_meta)

    if strict_username_ok:
        if meta["follow_cta_on_header"]:
            log(
                "info",
                "welcome_baseline_follow_cta_present_on_verified_own_profile_ignored",
                expected_username=expected_username,
                verification_method=str(meta.get("verification_method") or ""),
                profile_tabs_visible=meta["profile_tabs_visible"],
                stats_band_present=meta["stats_band_present"],
            )
        log(
            "info",
            "welcome_baseline_own_profile_verified",
            expected_username=expected_username,
            profile_tabs_visible=meta["profile_tabs_visible"],
            stats_band_present=meta["stats_band_present"],
            verification_method=str(meta.get("verification_method") or ""),
            follow_cta_ignored=bool(meta["follow_cta_on_header"]),
        )
        return True, meta

    if meta["follow_cta_on_header"]:
        log(
            "info",
            "welcome_baseline_own_profile_open_failed",
            reason="follow_cta_present_not_own_profile",
        )
        return False, meta

    if not exp:
        ok = bool(meta["profile_tabs_visible"] or meta["stats_band_present"])
        if ok:
            log("info", "welcome_baseline_own_profile_verified", username_match="skipped_no_expected")
        return ok, meta

    ok = bool(meta.get("verify_profile_ok"))
    if not ok and meta["profile_tabs_visible"] and meta["stats_band_present"]:
        ok = True
        meta["verify_profile_fallback"] = "profile_chrome_without_username_match"
    if ok:
        log(
            "info",
            "welcome_baseline_own_profile_verified",
            expected_username=expected_username,
            profile_tabs_visible=meta["profile_tabs_visible"],
            stats_band_present=meta["stats_band_present"],
        )
    return bool(ok), meta


def _merge_det_if_followers_list_committed_open(
    det: dict[str, Any],
    account_username: str,
    open_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """Reuse visual_fallback proof from open_followers_list_from_profile when session is committed."""
    if not followers_session_list_committed_open_for(account_username):
        return det
    return followers_session_merge_det_for_committed_visual_surface(
        det,
        session_vf_detail_for_loop=None,
        open_list_meta=open_meta if isinstance(open_meta, dict) else None,
    )


def open_own_followers_list_from_own_profile(
    d: u2.Device,
    account_username: str,
    *,
    pkg: str | None = None,
    followers_open_wait_s: float | None = None,
) -> tuple[bool, dict[str, Any]]:
    """
    Tap Followers stat on own profile and confirm followers list surface.
    Reuses followers-list open helpers with source = own account username.
    """
    pkg = pkg or str(getattr(config, "INSTAGRAM_PACKAGE", "") or "")
    account_username = str(account_username or "").strip()
    log("info", "welcome_baseline_own_followers_open_started", account_username=account_username)

    det_pre = detect_followers_list_screen(d, source_profile_username=account_username)
    if bool(det_pre.get("is_followers_list")):
        log(
            "info",
            "welcome_baseline_own_followers_open_success",
            method="already_on_followers_list",
        )
        return True, {"open_method": "already_on_followers_list", "det": det_pre}

    ok_open, open_meta = open_followers_list_from_profile(
        d,
        account_username,
        pkg,
        profile_verified=True,
    )
    if not ok_open:
        log(
            "info",
            "welcome_baseline_own_followers_open_failed",
            reason=str((open_meta or {}).get("failure_reason") or "open_followers_list_failed"),
        )
        return False, open_meta if isinstance(open_meta, dict) else {}

    wait_s = (
        float(followers_open_wait_s)
        if followers_open_wait_s is not None
        else float(getattr(config, "WELCOME_BASELINE_FOLLOWERS_OPEN_WAIT_S", 4.0) or 4.0)
    )
    if wait_s > 0:
        time.sleep(min(wait_s, 8.0))

    det = detect_followers_list_screen(d, source_profile_username=account_username)
    det = _merge_det_if_followers_list_committed_open(det, account_username, open_meta)
    if not bool(det.get("is_followers_list")):
        ok_tap, tap_diag = _tap_profile_followers_stat(d)
        if ok_tap:
            time.sleep(min(wait_s, 6.0))
            det = detect_followers_list_screen(d, source_profile_username=account_username)
            det = _merge_det_if_followers_list_committed_open(det, account_username, open_meta)
        if not bool(det.get("is_followers_list")):
            log(
                "info",
                "welcome_baseline_own_followers_open_failed",
                reason="followers_list_not_detected",
                tap_retried=bool(ok_tap),
            )
            return False, {
                "open_meta": open_meta,
                "tap_diag": tap_diag,
                "det": det,
            }

    log(
        "info",
        "welcome_baseline_own_followers_open_success",
        open_detection_method=str(det.get("open_detection_method") or open_meta.get("open_detection_method") or ""),
        candidate_username_count=int(det.get("candidate_username_count") or 0),
    )
    log(
        "info",
        "welcome_baseline_followers_surface_verified",
        is_followers_list=True,
        signals=list(det.get("signals") or [])[:12],
        action_bar_title=str(det.get("action_bar_title") or "")[:80],
    )
    return True, {"open_meta": open_meta, "det": det}
