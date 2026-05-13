"""V1 hybrid navigation state observation (non-invasive; callers integrate incrementally)."""

from __future__ import annotations

from enum import Enum
from typing import Any

import uiautomator2 as u2

from instagram_navigation import detect_followers_list_screen
from logs import log


class NavigationEngineState(str, Enum):
    """Coarse navigation states for Instagram automation."""

    UNKNOWN = "UNKNOWN"
    INSTAGRAM_CLOSED = "INSTAGRAM_CLOSED"
    SEARCH = "SEARCH"
    SEARCH_RESULTS = "SEARCH_RESULTS"
    PROFILE = "PROFILE"
    FOLLOWERS_LIST = "FOLLOWERS_LIST"
    CANDIDATE_PROFILE = "CANDIDATE_PROFILE"
    PRIVATE_PROFILE = "PRIVATE_PROFILE"
    MUTE_SHEET = "MUTE_SHEET"
    ACTION_BLOCK = "ACTION_BLOCK"
    LAUNCHER_WRONG_SURFACE = "LAUNCHER_WRONG_SURFACE"


_LAUNCHER_PACKAGE_FRAGMENTS = (
    "launcher",
    "nexuslauncher",
    "launcher3",
    "quickstep",
    "trebuchet",
)


def _foreground_package(d: u2.Device) -> str:
    try:
        cur = d.app_current()
        return str((cur or {}).get("package") or "").strip()
    except Exception:
        return ""


def _is_likely_launcher_package(pkg: str) -> bool:
    p = (pkg or "").lower()
    if not p:
        return False
    return any(x in p for x in _LAUNCHER_PACKAGE_FRAGMENTS)


def _det_visual_detail(det: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(det, dict):
        return {}
    vf = det.get("visual_fallback_detail")
    return vf if isinstance(vf, dict) else {}


def _session_visual_detail(ctx: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ctx, dict):
        return {}
    vf = ctx.get("session_visual_fallback_detail")
    return vf if isinstance(vf, dict) else {}


def observe_instagram_state(
    d: u2.Device,
    *,
    expected_package: str,
    last_known_state: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Best-effort hybrid state: foreground package + optional hierarchy ``det`` + context visuals.

    ``context`` may include:
    - ``det``: dict from ``detect_followers_list_screen`` (preferred when supplied).
    - ``source_profile_username``: str — used to obtain ``det`` when missing.
    - ``session_visual_fallback_detail``: dict — open-list visual proof from session.
    - ``private_profile_hint``: bool — optional hint from caller flow.
    - ``mute_sheet_hint``: bool — optional hint from caller flow.
    - ``disable_followers_visual_fallback``: bool — when True, ignore session/det visual
      followers-list proof so post–profile-open observation is not biased toward FOLLOWERS_LIST.
    - ``phase``: str — e.g. ``candidate_profile_analysis`` for logging.
    - ``expected_state``: str — optional hint (PROFILE / CANDIDATE_PROFILE).
    """
    ctx = dict(context) if isinstance(context, dict) else {}
    _disable_followers_vf = bool(ctx.get("disable_followers_visual_fallback"))
    exp = str(expected_package or "").strip()
    fg = _foreground_package(d)

    signals: dict[str, Any] = {
        "foreground_package": fg,
        "expected_package": exp,
    }

    out: dict[str, Any] = {
        "state": NavigationEngineState.UNKNOWN.value,
        "confidence": 0.0,
        "signals": signals,
        "reason": "init",
        "xml_guess": "",
        "visual_guess": "",
        "foreground_package": fg,
    }

    if not exp:
        out["reason"] = "missing_expected_package"
        return out

    if not fg:
        out["state"] = NavigationEngineState.INSTAGRAM_CLOSED.value
        out["confidence"] = 0.55
        out["reason"] = "empty_foreground_package"
        return out

    if fg != exp:
        if _is_likely_launcher_package(fg):
            out["state"] = NavigationEngineState.LAUNCHER_WRONG_SURFACE.value
            out["confidence"] = 0.88
            out["reason"] = "foreground_not_instagram_launcher_like"
        else:
            out["state"] = NavigationEngineState.UNKNOWN.value
            out["confidence"] = 0.45
            out["reason"] = "foreground_not_expected_package"
        signals["foreground_mismatch"] = True
        return out

    det: dict[str, Any] | None = None
    raw_det = ctx.get("det")
    if isinstance(raw_det, dict):
        det = raw_det
    else:
        src = str(ctx.get("source_profile_username") or "").strip()
        if src:
            try:
                det = detect_followers_list_screen(
                    d, source_profile_username=src
                )
            except Exception as e:
                out["reason"] = f"detect_followers_list_screen_failed:{type(e).__name__}"
                log("debug", "navigation_engine_detect_failed", error=str(e))
                det = None

    xml_guess = ""
    if isinstance(det, dict):
        xml_guess = str(det.get("current_screen_guess") or "").strip()
        out["xml_guess"] = xml_guess
        signals["is_followers_list"] = bool(det.get("is_followers_list"))
        signals["strict_list_open"] = bool(det.get("strict_list_open"))
        signals["relaxed_list_open"] = bool(det.get("relaxed_list_open"))
        signals["candidate_username_count"] = int(det.get("candidate_username_count") or 0)
        signals["title_match"] = bool(det.get("title_match"))

    if _disable_followers_vf:
        log(
            "info",
            "navigation_followers_visual_fallback_disabled",
            phase=str(ctx.get("phase") or ""),
            expected_state=str(ctx.get("expected_state") or ""),
            visual_candidate_id=str(ctx.get("visual_candidate_id") or ""),
            source_profile_username=str(ctx.get("source_profile_username") or ""),
            xml_guess=xml_guess,
        )
        signals["followers_visual_fallback_disabled"] = True
        vf_det: dict[str, Any] = {}
        vf_sess: dict[str, Any] = {}
    else:
        vf_det = _det_visual_detail(det)
        vf_sess = _session_visual_detail(ctx)
    vf = vf_det if (vf_det.get("visual_match")) else vf_sess
    if not _disable_followers_vf and not vf.get("visual_match") and vf_sess.get("visual_match"):
        vf = vf_sess

    visual_match = bool(vf.get("visual_match"))
    v_rows = int(vf.get("visual_user_rows_detected") or 0)
    v_btns = int(vf.get("visual_follow_button_count") or 0)
    v_conf = float(vf.get("visual_confidence") or 0.0)

    out["visual_guess"] = "followers_list_visual" if visual_match else ""
    signals["visual_match"] = visual_match
    signals["visual_user_rows_detected"] = v_rows
    signals["visual_follow_button_count"] = v_btns
    signals["visual_confidence"] = round(v_conf, 4)

    if ctx.get("mute_sheet_hint"):
        out["state"] = NavigationEngineState.MUTE_SHEET.value
        out["confidence"] = 0.72
        out["reason"] = "context_mute_sheet_hint"
        return out

    if ctx.get("private_profile_hint"):
        out["state"] = NavigationEngineState.PRIVATE_PROFILE.value
        out["confidence"] = 0.7
        out["reason"] = "context_private_profile_hint"
        return out

    followers_conf = 0.0
    followers_reason_parts: list[str] = []

    if isinstance(det, dict) and bool(det.get("is_followers_list")):
        followers_conf = 0.52
        followers_reason_parts.append("det_is_followers_list")
        if bool(det.get("strict_list_open")):
            followers_conf += 0.22
            followers_reason_parts.append("strict_list_open")
        elif bool(det.get("relaxed_list_open")):
            followers_conf += 0.12
            followers_reason_parts.append("relaxed_list_open")
        if int(det.get("candidate_username_count") or 0) >= 1:
            followers_conf += 0.08
            followers_reason_parts.append("has_xml_candidates")

    if (
        visual_match
        and v_conf >= 0.65
        and (v_rows >= 2 or v_btns >= 1)
        and not _disable_followers_vf
    ):
        followers_conf = max(followers_conf, 0.62 + min(0.28, v_conf * 0.25))
        followers_reason_parts.append("visual_fallback_strong")
        if xml_guess == "likely_profile" and isinstance(det, dict) and not bool(
            det.get("strict_list_open")
        ):
            signals["visual_overrides_xml_profile_guess"] = True

    if (
        not _disable_followers_vf
        and last_known_state == NavigationEngineState.FOLLOWERS_LIST.value
        and followers_conf > 0.35
        and followers_conf < 0.75
    ):
        followers_conf = min(0.82, followers_conf + 0.08)
        followers_reason_parts.append("hysteresis_last_followers_list")

    followers_conf = max(0.0, min(1.0, followers_conf))

    if followers_conf >= 0.4:
        out["state"] = NavigationEngineState.FOLLOWERS_LIST.value
        out["confidence"] = followers_conf
        out["reason"] = "+".join(followers_reason_parts) if followers_reason_parts else "followers_heuristic"

    elif isinstance(det, dict):
        if xml_guess == "likely_profile" or bool(det.get("profile_tabs_absent")) is False:
            out["state"] = NavigationEngineState.PROFILE.value
            out["confidence"] = 0.42
            out["reason"] = "xml_guess_likely_profile"
        elif "search" in xml_guess.lower():
            out["state"] = NavigationEngineState.SEARCH_RESULTS.value
            out["confidence"] = 0.4
            out["reason"] = "xml_guess_searchish"
        else:
            out["state"] = NavigationEngineState.UNKNOWN.value
            out["confidence"] = 0.35
            out["reason"] = "ambiguous_det"

    if (
        not _disable_followers_vf
        and out["state"] == NavigationEngineState.UNKNOWN.value
        and last_known_state == NavigationEngineState.FOLLOWERS_LIST.value
        and visual_match
        and v_conf >= 0.55
    ):
        out["state"] = NavigationEngineState.FOLLOWERS_LIST.value
        out["confidence"] = max(float(out["confidence"]), 0.58)
        out["reason"] = "hysteresis_unknown_to_followers_last_state_visual"

    if _disable_followers_vf and out["state"] == NavigationEngineState.FOLLOWERS_LIST.value:
        if xml_guess == "likely_profile":
            _esp = str(ctx.get("expected_state") or "").strip().upper()
            if _esp == "PROFILE":
                out["state"] = NavigationEngineState.PROFILE.value
                out["confidence"] = max(0.55, min(0.75, 0.62))
            else:
                out["state"] = NavigationEngineState.CANDIDATE_PROFILE.value
                out["confidence"] = max(0.55, min(0.75, 0.64))
            out["reason"] = "candidate_profile_phase_xml_profile_hint"
            out["visual_guess"] = ""
            signals["candidate_profile_phase"] = True

    return out
