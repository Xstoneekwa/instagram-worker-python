"""
Heuristic Vision Validation Layer — screenshot path + structured context only.

No device taps, swipes, or profile opens. No external APIs in V1.
Callers pass ``det`` / ``nav`` / overlay hints gathered elsewhere.
"""

from __future__ import annotations

import re
from typing import Any

import config
from logs import log


def _ctx(ctx: dict | None) -> dict[str, Any]:
    return dict(ctx) if isinstance(ctx, dict) else {}


def _det(ctx: dict[str, Any]) -> dict[str, Any]:
    raw = ctx.get("det")
    return raw if isinstance(raw, dict) else {}


def _fp(ctx: dict[str, Any]) -> dict[str, Any]:
    raw = ctx.get("fingerprint") or ctx.get("fp")
    return raw if isinstance(raw, dict) else {}


def _vf(det: dict[str, Any]) -> dict[str, Any]:
    raw = det.get("visual_fallback_detail")
    return raw if isinstance(raw, dict) else {}


def _blob_headers(det: dict[str, Any]) -> str:
    parts: list[str] = []
    ab = str(det.get("action_bar_title") or "").strip()
    if ab:
        parts.append(ab)
    for h in list(det.get("visible_header_texts") or [])[:40]:
        t = str(h).strip()
        if t:
            parts.append(t)
    return " | ".join(parts)


def _following_tab_signal(blob: str) -> tuple[bool, str | None]:
    b = blob.lower()
    if re.search(r"\b\d[\d,\.\s]*\s+following\b", b) and "follower" not in b:
        return True, "numeric_following_chip"
    if re.search(r"\b\d[\d,\.\s]*\s+suivis\b", b) and "abonné" not in b:
        return True, "numeric_suivis_chip"
    if b.strip() in ("following", "suivis", "abonnements", "siguiendo"):
        return True, "tab_title_following_family"
    if "following" in b and "followers" not in b and "follower" not in b:
        if "suggested for you" not in b and "discover people" not in b:
            return True, "following_token_ambient"
    return False, None


def _followers_tab_signal(blob: str) -> bool:
    bl = blob.lower()
    if any(x in bl for x in ("followers", "abonnés", "seguidores", "подписчик")):
        return True
    return False


def _story_reel_signal(det: dict[str, Any]) -> bool:
    g = str(det.get("current_screen_guess") or "").lower()
    if any(x in g for x in ("story", "reel")):
        return True
    sigs = _vf(det).get("visual_signals") or []
    for s in sigs:
        sl = str(s).lower()
        if "story" in sl or "reel" in sl:
            return True
    return False


def _comment_composer_signal(det: dict[str, Any], fp: dict[str, Any]) -> bool:
    g = str(det.get("current_screen_guess") or "").lower()
    if any(x in g for x in ("comment", "composer")):
        return True
    sc = str(fp.get("screen_class") or "").lower()
    if "comment" in sc or "composer" in sc:
        return True
    return False


def _launcher_signal(ctx: dict[str, Any], det: dict[str, Any]) -> bool:
    if bool(ctx.get("launcher_hint")):
        return True
    nav = str(ctx.get("nav_state") or ctx.get("navigation_state") or ctx.get("state") or "")
    up = nav.upper()
    if "LAUNCHER" in up or nav == "INSTAGRAM_CLOSED":
        return True
    pkg = str(det.get("current_package") or ctx.get("current_package") or "").lower()
    if pkg and "instagram" not in pkg:
        if any(x in pkg for x in ("launcher", "nexuslauncher", "quickstep", "trebuchet")):
            return True
    return False


def _keyboard_signal(ctx: dict[str, Any], det: dict[str, Any]) -> bool:
    if bool(ctx.get("keyboard_visible")) or bool(ctx.get("ime_visible")):
        return True
    sigs = det.get("signals")
    if isinstance(sigs, list):
        for s in sigs:
            if "keyboard" in str(s).lower():
                return True
    return False


def _follow_cta_signal(ctx: dict[str, Any], det: dict[str, Any]) -> bool:
    if bool(ctx.get("follow_cta_visible")):
        return True
    vf = _vf(det)
    try:
        n = int(vf.get("visual_follow_button_count") or 0)
        if n >= 1 and bool(vf.get("visual_search_area_detected")):
            return True
    except Exception:
        pass
    return False


def _vf_parse_signal_int(signals: Any, key: str) -> int | None:
    """Parse ``left_row_bands:21`` / ``right_blue_bands:3`` style entries from ``visual_signals``."""
    if not isinstance(signals, (list, tuple)):
        return None
    prefix = f"{key}:"
    for raw in signals:
        s = str(raw)
        if s.startswith(prefix):
            try:
                return int(s.split(":", 1)[1].strip())
            except Exception:
                return None
    return None


def _forbidden_nav_or_guess_followers_override(
    det: dict[str, Any], ctx: dict[str, Any]
) -> tuple[bool, str]:
    """
    True when we must not apply ambiguous-tab visual acceptance (direct, search, explore, wrong pkg).
    """
    g = str(det.get("current_screen_guess") or "").lower()
    if any(x in g for x in ("direct", "inbox", "search", "explore", "camera")):
        return True, f"screen_guess:{g[:80]}"
    nav = str(
        ctx.get("nav_state") or ctx.get("navigation_state") or ctx.get("state") or ""
    ).lower()
    if any(x in nav for x in ("direct", "search", "explore", "inbox")):
        return True, f"nav_state:{nav[:80]}"
    pkg = str(det.get("current_package") or ctx.get("current_package") or "").lower()
    if pkg and "instagram" not in pkg:
        return True, f"non_instagram_pkg:{pkg[:80]}"
    return False, ""


def _action_bar_following_subscription_tokens(action_bar: str) -> bool:
    """True when the action bar alone looks like Following / subscriptions tab text."""
    t = str(action_bar or "").strip().lower()
    if not t:
        return False
    if t in ("following", "suivis", "abonnements", "siguiendo"):
        return True
    if re.search(r"\b\d[\d,\.\s]*\s+following\b", t) and "follower" not in t:
        return True
    if re.search(r"\b\d[\d,\.\s]*\s+suivis\b", t) and "abonné" not in t:
        return True
    return False


def _ambiguous_tab_visual_strong_followers_accept(
    *,
    expected_surface: str,
    det: dict[str, Any],
    context: dict[str, Any],
    ft_ok: bool,
    sig: dict[str, Any],
    source_profile_username: str | None,
) -> tuple[bool, str, dict[str, Any]]:
    """
    When the list is visually a followers sheet but header/tab text is ambiguous in strict mode,
    accept as ``followers_list`` if screenshot-derived cues are very strong and Following is not active.
    """
    exp = str(expected_surface or "").strip().lower()
    if exp not in (
        "followers_list",
        "followers",
        "followers_list_open",
        "visual_candidate_row",
        "followers_visual_pick",
    ):
        return False, "", {}
    if ft_ok:
        return False, "", {}
    bad_nav, nav_why = _forbidden_nav_or_guess_followers_override(det, context)
    if bad_nav:
        return False, "", {"forbidden_nav_or_guess": nav_why}
    ab_title = str(det.get("action_bar_title") or "").strip()
    if _action_bar_following_subscription_tokens(ab_title):
        return False, "", {"action_bar_following_like": True}
    if not bool(det.get("is_followers_list")):
        return False, "", {}
    vf = _vf(det)
    if not isinstance(vf, dict) or not bool(vf.get("visual_match")):
        return False, "", {}
    conf = float(vf.get("visual_confidence") or 0.0)
    if conf < 0.82:
        return False, "", {}
    rows = int(vf.get("visual_user_rows_detected") or 0)
    if rows < 12:
        return False, "", {}
    if not bool(vf.get("visual_search_area_detected")):
        return False, "", {}
    vs = vf.get("visual_signals") or []
    rb = int(vf.get("visual_follow_button_count") or 0)
    rb_sig = _vf_parse_signal_int(vs, "right_blue_bands")
    if rb_sig is not None:
        rb = max(rb, rb_sig)
    if rb < 2:
        return False, "", {}
    lb = rows
    lb_sig = _vf_parse_signal_int(vs, "left_row_bands")
    if lb_sig is not None:
        lb = max(lb, lb_sig)
    if lb < 12:
        return False, "", {}
    extra: dict[str, Any] = {
        "ambiguous_tab_visual_accept": True,
        "visual_confidence": round(conf, 4),
        "visual_user_rows_detected": rows,
        "right_blue_bands_effective": rb,
        "left_row_bands_effective": lb,
        "active_tab_text_action_bar": ab_title[:160],
    }
    try:
        log(
            "info",
            "vision_validation_ambiguous_tab_visual_accept",
            expected_surface=exp,
            is_followers_list=bool(det.get("is_followers_list")),
            visual_confidence=round(conf, 4),
            visual_user_rows_detected=int(rows),
            visual_search_area_detected=bool(vf.get("visual_search_area_detected")),
            right_blue_bands=int(rb),
            left_row_bands=int(lb),
            active_tab_text=ab_title[:160],
            following_tab_active=bool(ft_ok),
            source_profile_username=str(source_profile_username or "")[:120],
            visual_signals_sample=[str(x) for x in vs[:20]] if isinstance(vs, list) else [],
        )
    except Exception:
        pass
    return True, "followers_list_visual_strong_ambiguous_tab_accepted", {**sig, **extra}


def _followers_list_open_zero_follow_cta_structure_strong(
    det: dict[str, Any],
    context: dict[str, Any],
    *,
    ft_ok: bool,
    emit_structure_accept_log: bool = True,
) -> tuple[bool, str, dict[str, Any]]:
    """
    When no Follow pill is visible yet, still classify as followers_list if list chrome
    is very strong (aligned with rendered-strong gates in instagram_navigation).
    """
    if ft_ok:
        return False, "", {}
    bad_nav, nav_why = _forbidden_nav_or_guess_followers_override(det, context)
    if bad_nav:
        return False, "", {"forbidden_nav_or_guess": nav_why}
    ab_title = str(det.get("action_bar_title") or "").strip()
    if _action_bar_following_subscription_tokens(ab_title):
        return False, "", {"action_bar_following_like": True}
    vf = _vf(det)
    if not isinstance(vf, dict) or not vf:
        return False, "", {}
    if not bool(vf.get("visual_search_area_detected")):
        return False, "", {}
    if not bool(vf.get("visual_followers_title_hint")):
        return False, "", {}
    vs = vf.get("visual_signals") or []
    if not any(str(x) == "search_strip" for x in vs):
        return False, "", {}
    try:
        fcb = int(vf.get("visual_follow_button_count") or 0)
    except Exception:
        fcb = -1
    if fcb != 0:
        return False, "", {}
    rows = int(vf.get("visual_user_rows_detected") or 0)
    if rows < 19:
        return False, "", {}
    extra: dict[str, Any] = {
        "zero_follow_cta_list_structure_strong": True,
        "visual_user_rows_detected": rows,
        "visual_search_area_detected": True,
        "active_tab_text_action_bar": ab_title[:160],
    }
    if emit_structure_accept_log:
        try:
            log(
                "info",
                "vision_validation_zero_follow_cta_followers_list_structure_accept",
                visual_user_rows_detected=int(rows),
                visual_search_area_detected=True,
                visual_followers_title_hint=bool(vf.get("visual_followers_title_hint")),
                following_tab_active=bool(ft_ok),
                active_tab_text=ab_title[:160],
                visual_signals_sample=[str(x) for x in vs[:20]] if isinstance(vs, list) else [],
            )
        except Exception:
            pass
    return True, "followers_list_zero_follow_cta_structure_strong", extra


def _classify_surface(
    *,
    source_profile_username: str | None,
    candidate_username: str | None,
    context: dict[str, Any],
    expected_surface: str = "",
) -> tuple[str, str | None, bool, str | None, float, str, dict[str, Any]]:
    """
    Returns (surface, active_tab, danger, cta_type, confidence, reason, signals_out).
    """
    det = _det(context)
    fp = _fp(context)
    blob = _blob_headers(det)
    sig: dict[str, Any] = {
        "blob_len": len(blob),
        "title_match": bool(det.get("title_match")),
        "strict_list_open": bool(det.get("strict_list_open")),
        "is_followers_list": bool(det.get("is_followers_list")),
        "current_screen_guess": str(det.get("current_screen_guess") or "")[:120],
        "screen_class": str(fp.get("screen_class") or ""),
    }
    vf = _vf(det)
    if vf:
        sig["visual_match"] = bool(vf.get("visual_match"))
        sig["visual_confidence"] = float(vf.get("visual_confidence") or 0.0)
        sig["visual_search_area_detected"] = bool(vf.get("visual_search_area_detected"))
        sig["visual_user_rows"] = int(vf.get("visual_user_rows_detected") or 0)
        vs = vf.get("visual_signals")
        if isinstance(vs, list):
            sig["visual_signals_sample"] = [str(x) for x in vs[:12]]

    strict = bool(context.get("strict_followers_surface"))
    if strict:
        sig["strict_followers_surface"] = True

    if _launcher_signal(context, det):
        return "launcher_or_wrong_surface", None, True, None, 0.88, "launcher_or_non_instagram", sig

    if _keyboard_signal(context, det):
        return "comment_composer", None, True, "keyboard", 0.78, "keyboard_or_composer_signal", sig

    if _story_reel_signal(det):
        return "story_or_reel", None, True, None, 0.8, "story_or_reel_guess_or_visual", sig

    if _comment_composer_signal(det, fp):
        return "comment_composer", None, True, None, 0.76, "comment_or_composer_surface", sig

    ft_ok, ft_why = _following_tab_signal(blob)
    fw_tab = _followers_tab_signal(blob)

    list_like = bool(det.get("is_followers_list")) or (
        bool(vf.get("visual_match"))
        and bool(vf.get("visual_search_area_detected"))
        and int(vf.get("visual_user_rows_detected") or 0) >= 3
    )

    if list_like and ft_ok:
        sig["following_tab_evidence"] = ft_why
        return "following_list", "following", False, None, 0.74, ft_why or "following_list_heuristic", sig

    if list_like and fw_tab and not ft_ok:
        sig["followers_tab_evidence"] = True
        return "followers_list", "followers", False, None, 0.72, "followers_list_header_signals", sig

    if list_like:
        sig["ambiguous_list_chrome"] = True
        exp_hint = str(expected_surface or "").strip().lower()
        acc, acc_reason, acc_extra = _ambiguous_tab_visual_strong_followers_accept(
            expected_surface=exp_hint,
            det=det,
            context=context,
            ft_ok=ft_ok,
            sig=sig,
            source_profile_username=source_profile_username,
        )
        if acc:
            sig_out = {**sig, **acc_extra}
            return (
                "followers_list",
                "followers",
                False,
                None,
                0.91,
                acc_reason,
                sig_out,
            )
        zf_ok, zf_reason, zf_extra = _followers_list_open_zero_follow_cta_structure_strong(
            det, context, ft_ok=ft_ok
        )
        if zf_ok and exp_hint in (
            "followers_list",
            "followers",
            "followers_list_open",
        ):
            sig_out = {**sig, **zf_extra}
            return (
                "followers_list",
                "followers",
                False,
                None,
                0.87,
                zf_reason,
                sig_out,
            )
        if bool(getattr(config, "VISION_VALIDATION_STRICT_MODE", True)):
            return "unknown", None, True, None, 0.45, "list_like_but_tab_ambiguous_strict", sig
        return "followers_list", None, False, None, 0.5, "list_like_tab_unknown_non_strict", sig

    exp_follow = str(expected_surface or "").strip().lower() in (
        "followers_list",
        "followers",
        "followers_list_open",
    )
    if (not list_like) and exp_follow and not ft_ok:
        zf2_ok, zf2_reason, zf2_extra = _followers_list_open_zero_follow_cta_structure_strong(
            det, context, ft_ok=ft_ok
        )
        if zf2_ok:
            sig_out = {**sig, **zf2_extra}
            return (
                "followers_list",
                "followers",
                False,
                None,
                0.86,
                zf2_reason,
                sig_out,
            )

    guess = str(det.get("current_screen_guess") or "").lower()
    abn = str(det.get("action_bar_title") or "").strip()
    src = str(source_profile_username or "").strip()
    cand = str(candidate_username or "").strip()

    if "profile" in guess or bool(det.get("profile_tabs_absent")) is False:
        surf = "candidate_profile" if (cand and cand.lower() in abn.lower()) else "profile"
        cta = None
        if _follow_cta_signal(context, det):
            cta = "follow"
        return surf, None, False, cta, 0.55, "profile_like_guess", sig

    return "unknown", None, not bool(getattr(config, "VISION_VALIDATION_STRICT_MODE", True)), None, 0.35, "insufficient_signals", sig


def validate_instagram_surface_from_screenshot(
    screenshot_path: str,
    *,
    expected_surface: str,
    source_profile_username: str | None = None,
    candidate_username: str | None = None,
    context: dict | None = None,
) -> dict[str, Any]:
    ctx = _ctx(context)
    exp = str(expected_surface or "").strip().lower()
    log(
        "info",
        "vision_validation_started",
        screenshot_path=str(screenshot_path or "")[:240],
        expected_surface=exp,
        source_profile_username=str(source_profile_username or "")[:120],
        candidate_username=str(candidate_username or "")[:120],
        context_keys=sorted(ctx.keys())[:30],
    )

    surface, active_tab, danger, cta_type, confidence, reason, signals = _classify_surface(
        source_profile_username=source_profile_username,
        candidate_username=candidate_username,
        context=ctx,
        expected_surface=exp,
    )

    ok = True
    safe_to_continue = True

    if exp in ("followers_list", "followers", "followers_list_open"):
        if surface == "following_list" or active_tab == "following":
            ok = False
            safe_to_continue = False
            reason = f"expected_followers_got_{surface}:{reason}"
        elif surface != "followers_list" and getattr(
            config, "VISION_VALIDATION_STRICT_MODE", True
        ):
            if surface in ("unknown",) and not bool(_det(ctx).get("title_match")):
                ok = False
                safe_to_continue = False
        if danger and surface != "following_list":
            ok = False
            safe_to_continue = False

    elif exp in ("visual_candidate_row", "followers_visual_pick"):
        det_vp = _det(ctx)
        blob_vp = _blob_headers(det_vp)
        ft_vp, _ft_vp_why = _following_tab_signal(blob_vp)
        zf_vp_ok, zf_vp_reason, zf_vp_extra = (
            _followers_list_open_zero_follow_cta_structure_strong(
                det_vp,
                ctx,
                ft_ok=ft_vp,
                emit_structure_accept_log=False,
            )
        )
        if surface == "following_list" or active_tab == "following":
            ok = False
            safe_to_continue = False
            reason = f"expected_followers_got_{surface}:{reason}"
        elif surface != "followers_list" and not zf_vp_ok:
            ok = False
            safe_to_continue = False
        if ok and not _follow_cta_signal(ctx, det_vp):
            if getattr(config, "VISION_VALIDATION_STRICT_MODE", True):
                if zf_vp_ok:
                    reason = str(zf_vp_reason or "zero_follow_cta_visual_pick_structure_strong")
                    vf_vp = _vf(det_vp)
                    try:
                        log(
                            "info",
                            "vision_validation_zero_follow_cta_followers_visual_pick_structure_accept",
                            expected_surface=exp,
                            surface_classified=str(surface),
                            visual_user_rows_detected=zf_vp_extra.get(
                                "visual_user_rows_detected"
                            ),
                            visual_follow_button_count=vf_vp.get(
                                "visual_follow_button_count"
                            ),
                            visual_search_area_detected=bool(
                                vf_vp.get("visual_search_area_detected")
                            ),
                            visual_followers_title_hint=bool(
                                vf_vp.get("visual_followers_title_hint")
                            ),
                            active_tab=active_tab,
                            accept_basis=(
                                "zero_follow_cta_structure_strong_surface_override"
                                if str(surface) != "followers_list"
                                else "zero_follow_cta_structure_strong_follow_cta_bypass"
                            ),
                        )
                    except Exception:
                        pass
                    danger = False
                    surface = "followers_list"
                    if active_tab != "following":
                        active_tab = "followers"
                    if isinstance(signals, dict):
                        signals["danger_override_applied"] = True
                        signals["surface_override"] = "followers_list"
                else:
                    ok = False
                    safe_to_continue = False
                    reason = "follow_cta_not_visible_for_candidate_row"

    elif exp in ("post_follow", "post_follow_safe", "candidate_profile_post_follow"):
        if danger or surface in ("story_or_reel", "comment_composer", "launcher_or_wrong_surface"):
            ok = False
            safe_to_continue = False
        elif surface not in ("profile", "candidate_profile", "unknown"):
            ok = False
            safe_to_continue = False
            reason = f"post_follow_unexpected_surface:{surface}"

    elif exp in ("mute_post_follow", "mute_surface", "mute_v2"):
        if danger and surface not in ("profile", "candidate_profile"):
            ok = False
            safe_to_continue = False
        ov = ctx.get("overlay") if isinstance(ctx.get("overlay"), dict) else {}
        sug = bool(ov.get("suggested_for_you") or ov.get("discover_people"))
        fhs = str(ctx.get("follow_header_snapshot") or "").lower()
        following_ui = fhs in ("following", "requested") or bool(ctx.get("following_visible"))
        if sug and following_ui and surface in ("profile", "candidate_profile", "unknown"):
            ok = True
            safe_to_continue = True
            danger = False
            reason = "mute_allowed_profile_with_suggestions_and_following_cta"
        elif danger:
            ok = False
            safe_to_continue = False

    if danger and safe_to_continue and exp not in ("mute_post_follow", "mute_surface", "mute_v2"):
        safe_to_continue = False
        ok = False

    out: dict[str, Any] = {
        "ok": bool(ok),
        "surface": str(surface),
        "active_tab": active_tab,
        "safe_to_continue": bool(safe_to_continue),
        "danger": bool(danger),
        "cta_type": cta_type,
        "confidence": float(round(confidence, 4)),
        "reason": str(reason),
        "signals": signals,
    }
    log(
        "info",
        "vision_validation_result",
        screenshot_path=str(screenshot_path or "")[:240],
        expected_surface=exp,
        **{k: out[k] for k in ("ok", "surface", "active_tab", "safe_to_continue", "danger", "cta_type", "confidence", "reason")},
    )
    return out


def vision_validate_followers_list(
    screenshot_path: str,
    *,
    source_profile_username: str | None = None,
    context: dict | None = None,
) -> dict[str, Any]:
    return validate_instagram_surface_from_screenshot(
        screenshot_path,
        expected_surface="followers_list",
        source_profile_username=source_profile_username,
        context=context,
    )


def vision_validate_not_following_tab(
    screenshot_path: str,
    *,
    source_profile_username: str | None = None,
    context: dict | None = None,
) -> dict[str, Any]:
    r = validate_instagram_surface_from_screenshot(
        screenshot_path,
        expected_surface="followers_list",
        source_profile_username=source_profile_username,
        context=context,
    )
    bad = r.get("surface") == "following_list" or r.get("active_tab") == "following"
    r2 = dict(r)
    r2["ok"] = bool(not bad and r.get("ok"))
    r2["safe_to_continue"] = bool(not bad and r.get("safe_to_continue"))
    if bad:
        r2["reason"] = "active_following_tab"
    return r2


def vision_validate_candidate_row_followable(
    screenshot_path: str,
    *,
    source_profile_username: str | None = None,
    candidate_username: str | None = None,
    context: dict | None = None,
) -> dict[str, Any]:
    return validate_instagram_surface_from_screenshot(
        screenshot_path,
        expected_surface="visual_candidate_row",
        source_profile_username=source_profile_username,
        candidate_username=candidate_username,
        context=context,
    )


def vision_validate_post_follow_surface(
    screenshot_path: str,
    *,
    source_profile_username: str | None = None,
    candidate_username: str | None = None,
    context: dict | None = None,
) -> dict[str, Any]:
    return validate_instagram_surface_from_screenshot(
        screenshot_path,
        expected_surface="post_follow",
        source_profile_username=source_profile_username,
        candidate_username=candidate_username,
        context=context,
    )


def vision_validate_mute_surface(
    screenshot_path: str,
    *,
    source_profile_username: str | None = None,
    candidate_username: str | None = None,
    context: dict | None = None,
) -> dict[str, Any]:
    return validate_instagram_surface_from_screenshot(
        screenshot_path,
        expected_surface="mute_post_follow",
        source_profile_username=source_profile_username,
        candidate_username=candidate_username,
        context=context,
    )


def vision_detect_danger_surface(context: dict | None = None) -> dict[str, Any]:
    ctx = _ctx(context)
    det = _det(ctx)
    fp = _fp(ctx)
    danger = (
        _launcher_signal(ctx, det)
        or _keyboard_signal(ctx, det)
        or _story_reel_signal(det)
        or _comment_composer_signal(det, fp)
    )
    why: list[str] = []
    if _launcher_signal(ctx, det):
        why.append("launcher")
    if _keyboard_signal(ctx, det):
        why.append("keyboard")
    if _story_reel_signal(det):
        why.append("story_reel")
    if _comment_composer_signal(det, fp):
        why.append("comment_composer")
    return {"danger": bool(danger), "reasons": why}
