"""Navigation to the logged-in account's own profile and own followers list (V4.1)."""

from __future__ import annotations

import html
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
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
from instagram_post_verification_completion import hierarchy_proves_expected_instagram_foreground

_PROFILE_TAB_RID_SUFFIXES: tuple[str, ...] = (
    "profile_tab",
    "tab_profile",
    "bottom_bar_profile",
    "profile_avatar",
)


class OwnProfileNavigationOutcome(str, Enum):
    """Fresh destination observed after the own-profile tab click."""

    PROFILE_OPENED = "profile_opened"
    WHAT_HAPPENED_SCREEN = "what_happened_screen"
    RESTRICTION_SCREEN = "restriction_screen"
    LOADING_INTERMEDIATE = "loading_intermediate"
    HOME_FEED = "home_feed"
    LOGIN_CHALLENGE = "login_challenge"
    UNKNOWN_SCREEN = "unknown_screen"


@dataclass(frozen=True)
class OwnProfileNavigationResult:
    outcome: OwnProfileNavigationOutcome
    clicked: bool
    selector: str = ""
    hierarchy_xml: str = ""
    loading_retry_used: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome is OwnProfileNavigationOutcome.PROFILE_OPENED


_WHAT_HAPPENED_TITLES = frozenset({"what happened", "ce qui s'est passe", "ce qui s’est passe"})
_RESTRICTION_TITLE = "we added a restriction to your account"
_RESTRICTION_DETAILS = frozenset({"you can't send messages", "you cannot send messages"})
_LOGIN_CHALLENGE_MARKERS = (
    "log in to instagram",
    "enter security code",
    "authentication code",
    "help us confirm it's you",
    "help us confirm it’s you",
    "verify your account",
)
_HOME_MARKERS = frozenset({"your story", "suggested for you"})
_PROFILE_STRUCTURE_RID_MARKERS = (
    "profile_header",
    "profile_tabs_container",
    "profile_tab_layout",
    "profile_tab_icon_view",
    "media_tab",
)
_OWN_PROFILE_CONTROL_MARKERS = frozenset(
    {"edit profile", "share profile", "modifier le profil", "partager le profil"}
)


def _normalize_surface_text(value: str) -> str:
    return " ".join(html.unescape(str(value or "")).strip().casefold().split())


def _parse_destination_nodes(hierarchy_xml: str) -> list[dict[str, str]]:
    raw = str(hierarchy_xml or "").strip()
    if not raw:
        return []
    try:
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            root = ET.fromstring(f"<wrap>{raw}</wrap>")
    except Exception:
        return []
    return [
        {
            "text": str(node.get("text") or node.get("content-desc") or "").strip(),
            "resource_id": str(node.get("resource-id") or "").strip(),
            "class_name": str(node.get("class") or "").strip(),
            "selected": str(node.get("selected") or "").strip().casefold(),
        }
        for node in root.iter()
    ]


def classify_own_profile_destination(
    hierarchy_xml: str,
    *,
    expected_username: str = "",
) -> tuple[OwnProfileNavigationOutcome, dict[str, Any]]:
    """Classify one fresh post-click hierarchy without treating the click as proof."""
    nodes = _parse_destination_nodes(hierarchy_xml)
    texts = [_normalize_surface_text(node["text"]) for node in nodes if node["text"]]
    text_set = set(texts)
    evidence: dict[str, Any] = {
        "hierarchy_xml_len": len(str(hierarchy_xml or "")),
        "action_bar_title": "",
        "expected_username": str(expected_username or "").strip().lstrip("@"),
        "profile_structure_present": False,
        "own_profile_control_present": False,
        "profile_tab_selected": False,
    }

    if _RESTRICTION_TITLE in text_set or any(value in _RESTRICTION_DETAILS for value in text_set):
        return OwnProfileNavigationOutcome.RESTRICTION_SCREEN, evidence
    if any(value in _WHAT_HAPPENED_TITLES for value in text_set):
        return OwnProfileNavigationOutcome.WHAT_HAPPENED_SCREEN, evidence
    if any(marker in value for value in texts for marker in _LOGIN_CHALLENGE_MARKERS):
        return OwnProfileNavigationOutcome.LOGIN_CHALLENGE, evidence

    action_bar_title = ""
    profile_structure_present = False
    profile_tab_selected = False
    for node in nodes:
        rid = node["resource_id"].casefold()
        value = str(node["text"] or "").strip().lstrip("@")
        if "action_bar_title" in rid and value:
            action_bar_title = value
        if any(marker in rid for marker in _PROFILE_STRUCTURE_RID_MARKERS):
            profile_structure_present = True
        if rid.endswith("/profile_tab") and node["selected"] == "true":
            profile_tab_selected = True
    own_profile_control_present = any(value in _OWN_PROFILE_CONTROL_MARKERS for value in text_set)
    evidence.update(
        {
            "action_bar_title": action_bar_title,
            "profile_structure_present": profile_structure_present,
            "own_profile_control_present": own_profile_control_present,
            "profile_tab_selected": profile_tab_selected,
        }
    )

    expected = _normalize_handle(expected_username or "")
    actual = _normalize_handle(action_bar_title)
    exact_expected_title = bool(expected and actual and actual == expected)
    generic_profile_proof = bool(
        action_bar_title
        and (profile_structure_present or own_profile_control_present or profile_tab_selected)
    )
    if exact_expected_title or generic_profile_proof:
        evidence["destination_proof"] = (
            "exact_expected_action_bar_title" if exact_expected_title else "profile_surface_composite"
        )
        return OwnProfileNavigationOutcome.PROFILE_OPENED, evidence

    loading_present = any(
        value in {"loading", "loading...", "chargement", "chargement..."}
        for value in text_set
    ) or any("progressbar" in node["class_name"].casefold() for node in nodes)
    if loading_present:
        return OwnProfileNavigationOutcome.LOADING_INTERMEDIATE, evidence

    home_marker_count = len(_HOME_MARKERS.intersection(text_set))
    feed_tab_selected = any(
        node["resource_id"].casefold().endswith("/feed_tab") and node["selected"] == "true"
        for node in nodes
    )
    if home_marker_count or feed_tab_selected:
        evidence["home_marker_count"] = home_marker_count
        return OwnProfileNavigationOutcome.HOME_FEED, evidence
    return OwnProfileNavigationOutcome.UNKNOWN_SCREEN, evidence


def _dump_fresh_hierarchy(d: u2.Device) -> str:
    try:
        try:
            return str(d.dump_hierarchy(compressed=False) or "")
        except TypeError:
            return str(d.dump_hierarchy() or "")
    except Exception:
        return ""


def _instagram_package_candidates(
    d: u2.Device,
    expected_package_name: str | None = None,
) -> list[str]:
    pkgs: list[str] = []
    expected = str(expected_package_name or "").strip()
    if expected:
        pkgs.append(expected)
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


def navigate_to_own_profile_from_bottom_nav(
    d: u2.Device,
    *,
    expected_package_name: str | None = None,
    expected_username: str = "",
) -> OwnProfileNavigationResult:
    """Click own profile and return a bounded, fresh destination verdict."""
    log("info", "welcome_baseline_own_profile_open_started")
    pkg = str(
        expected_package_name
        or getattr(config, "INSTAGRAM_PACKAGE", "")
        or ""
    ).strip()
    if not verify_app_foreground(d, pkg):
        actual_package = ""
        try:
            actual_package = str((d.app_current() or {}).get("package") or "").strip()
        except Exception:
            actual_package = ""
        hierarchy = ""
        if not actual_package:
            try:
                hierarchy = str(d.dump_hierarchy(compressed=False) or "")
            except TypeError:
                hierarchy = str(d.dump_hierarchy() or "")
            except Exception:
                hierarchy = ""
        if not hierarchy_proves_expected_instagram_foreground(hierarchy, pkg):
            log(
                "info",
                "welcome_baseline_own_profile_open_failed",
                reason="not_foreground",
                actual_package=actual_package,
                hierarchy_foreground_proof=False,
            )
            return OwnProfileNavigationResult(
                outcome=OwnProfileNavigationOutcome.UNKNOWN_SCREEN,
                clicked=False,
                evidence={"failure_reason": "not_foreground"},
            )
        log(
            "info",
            "welcome_baseline_foreground_proved_by_fresh_hierarchy",
            hierarchy_foreground_proof=True,
        )

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
        for ipkg in _instagram_package_candidates(d, pkg):
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

    if not clicked:
        log("info", "welcome_baseline_own_profile_open_failed", reason="profile_tab_not_found")
        return OwnProfileNavigationResult(
            outcome=OwnProfileNavigationOutcome.UNKNOWN_SCREEN,
            clicked=False,
            evidence={"failure_reason": "profile_tab_not_found"},
        )

    settle = float(getattr(config, "WELCOME_BASELINE_OWN_PROFILE_SETTLE_S", 1.2) or 1.2)
    if settle > 0:
        time.sleep(settle)

    hierarchy = _dump_fresh_hierarchy(d)
    outcome, evidence = classify_own_profile_destination(
        hierarchy,
        expected_username=expected_username,
    )
    loading_retry_used = False
    if outcome is OwnProfileNavigationOutcome.LOADING_INTERMEDIATE:
        loading_retry_used = True
        retry_s = float(getattr(config, "OWN_PROFILE_DESTINATION_LOADING_RETRY_S", 1.0) or 1.0)
        if retry_s > 0:
            time.sleep(retry_s)
        hierarchy = _dump_fresh_hierarchy(d)
        outcome, evidence = classify_own_profile_destination(
            hierarchy,
            expected_username=expected_username,
        )

    result = OwnProfileNavigationResult(
        outcome=outcome,
        clicked=True,
        selector=click_name,
        hierarchy_xml=hierarchy,
        loading_retry_used=loading_retry_used,
        evidence=evidence,
    )

    if not result.ok:
        log(
            "info",
            "welcome_baseline_own_profile_open_failed",
            reason="destination_not_proved",
            selector=click_name,
            destination_outcome=result.outcome.value,
            loading_retry_used=loading_retry_used,
            destination_evidence=evidence,
        )
        return result

    log(
        "info",
        "welcome_baseline_own_profile_open_success",
        selector=click_name,
        destination_outcome=result.outcome.value,
        destination_evidence=evidence,
    )
    return result


def open_own_profile_from_bottom_nav(
    d: u2.Device,
    *,
    expected_package_name: str | None = None,
    expected_username: str = "",
) -> bool:
    """Compatibility wrapper that is true only after own-profile destination proof."""
    return navigate_to_own_profile_from_bottom_nav(
        d,
        expected_package_name=expected_package_name,
        expected_username=expected_username,
    ).ok


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
