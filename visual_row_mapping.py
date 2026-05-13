"""
Visual Row Mapping Engine: map followers-list screenshot rows to tap-safe candidates (no XML handles).

Uses the same low-level visual signals as ``visual_extract_followers_candidates_from_screenshot``,
but does not require UiAutomator / device for username resolution. Tap targets are left-side
(avatar / username zone), never the Follow pill.
"""

from __future__ import annotations

from typing import Any

from logs import log

# Reuse Instagram visual helpers (PIL-only path inside these helpers).
from instagram_navigation import (
    _compute_visual_candidate_tap_points_from_bounds,
    _ig_follow_button_blue_pixel,
    _scale_bounds_to_original,
    _visual_collect_follow_row_y_spans,
    _visual_downscale_rgb,
    _visual_row_left_content_variance,
    _visual_tight_blue_bounds,
)


def _vr_clamp_rect(
    im_rgb: Any, left: int, top: int, right: int, bottom: int
) -> tuple[int, int, int, int]:
    W, H = im_rgb.size
    L = max(0, min(W - 1, int(left)))
    R = max(L + 1, min(W, int(right)))
    T = max(0, min(H - 1, int(top)))
    B = max(T + 1, min(H, int(bottom)))
    return L, T, R, B


def _vr_frac_greenish_following_pixel(r: int, g: int, b: int) -> bool:
    """Muted green / grey-green typical of Instagram Following / Suivi pill."""
    if int(g) < int(r) + 8 or int(g) < int(b) + 4:
        return False
    if int(r) < 55 or int(r) > 230:
        return False
    return 70 < (int(r) + int(g) + int(b)) // 3 < 215


def visual_followers_row_cta_classify(
    im_rgb: Any,
    *,
    fb_left: int,
    fb_top: int,
    fb_right: int,
    fb_bottom: int,
    tight_bounds: dict[str, int] | None,
) -> dict[str, Any]:
    """
    PIL-only CTA bucket for the right-edge row control (Follow vs Message / Following / …).
    ``cta_followable`` is True only when pixels look like a solid Instagram Follow pill.
    """
    L0, T0, R0, B0 = _vr_clamp_rect(im_rgb, fb_left, fb_top, fb_right, fb_bottom)
    crop_full = im_rgb.crop((L0, T0, R0, B0))
    px_full = list(crop_full.getdata())
    n_full = len(px_full)
    if n_full <= 0:
        return {
            "cta_class": "unknown",
            "cta_text": "",
            "cta_followable": False,
            "frac_blue_full": 0.0,
            "frac_blue_tight": 0.0,
            "mean_lum_full": 0.0,
            "aspect_ratio_full": 0.0,
            "frac_greenish_full": 0.0,
        }
    wb_full = sum(
        1
        for r, g, b in px_full
        if _ig_follow_button_blue_pixel(int(r), int(g), int(b))
    )
    frac_full = wb_full / float(n_full)
    mean_lum_full = sum((int(r) + int(g) + int(b)) / 3.0 for r, g, b in px_full) / float(
        n_full
    )
    wf, hf = max(1, R0 - L0), max(1, B0 - T0)
    ar_full = wf / float(hf)
    fg_full = sum(1 for r, g, b in px_full if _vr_frac_greenish_following_pixel(r, g, b)) / float(
        n_full
    )

    tight_frac = 0.0
    mean_lum_tight = mean_lum_full
    ar_tight = ar_full
    if isinstance(tight_bounds, dict):
        try:
            Lt, Tt, Rt, Bt = _vr_clamp_rect(
                im_rgb,
                int(tight_bounds.get("left", 0)),
                int(tight_bounds.get("top", 0)),
                int(tight_bounds.get("right", 0)),
                int(tight_bounds.get("bottom", 0)),
            )
            if Rt > Lt and Bt > Tt:
                cpt = im_rgb.crop((Lt, Tt, Rt, Bt))
                px_t = list(cpt.getdata())
                nt = len(px_t)
                if nt > 0:
                    wb_t = sum(
                        1
                        for r, g, b in px_t
                        if _ig_follow_button_blue_pixel(int(r), int(g), int(b))
                    )
                    tight_frac = wb_t / float(nt)
                    mean_lum_tight = sum(
                        (int(r) + int(g) + int(b)) / 3.0 for r, g, b in px_t
                    ) / float(nt)
                    wtt, htt = max(1, Rt - Lt), max(1, Bt - Tt)
                    ar_tight = wtt / float(htt)
        except Exception:
            tight_frac = 0.0

    cta_class = "unknown"
    cta_text = ""

    if tight_bounds is not None and tight_frac >= 0.062:
        cta_class, cta_text = "follow", "Follow"
    elif frac_full >= 0.125 and mean_lum_full < 172.0 and ar_full < 2.45:
        cta_class, cta_text = "follow", "Follow"
    elif fg_full >= 0.048 and frac_full < 0.065 and mean_lum_full < 205.0:
        cta_class, cta_text = "following", "Following"
    elif (
        mean_lum_full > 176.0
        and frac_full < 0.052
        and ar_full >= 2.12
        and fg_full < 0.035
    ):
        cta_class, cta_text = "contact", "Contact"
    elif mean_lum_full > 174.0 and frac_full < 0.055 and ar_full >= 1.46 and fg_full < 0.035:
        cta_class, cta_text = "message", "Message"
    elif (
        mean_lum_full > 168.0
        and 0.028 <= frac_full < 0.075
        and 1.05 <= ar_full < 1.52
        and fg_full < 0.04
    ):
        cta_class, cta_text = "requested", "Requested"

    if cta_class == "follow":
        if mean_lum_full >= 199.0 and frac_full < 0.09 and tight_frac < 0.06:
            cta_class, cta_text = "connected_gray", "Connected"
        elif fg_full >= 0.03 and frac_full < 0.13 and mean_lum_full < 212.0:
            cta_class, cta_text = "following", "Following"

    cta_followable = cta_class == "follow"

    return {
        "cta_class": cta_class,
        "cta_text": cta_text,
        "cta_followable": bool(cta_followable),
        "frac_blue_full": round(float(frac_full), 4),
        "frac_blue_tight": round(float(tight_frac), 4),
        "mean_lum_full": round(float(mean_lum_full), 2),
        "mean_lum_tight": round(float(mean_lum_tight), 2),
        "aspect_ratio_full": round(float(ar_full), 3),
        "aspect_ratio_tight": round(float(ar_tight), 3),
        "frac_greenish_full": round(float(fg_full), 4),
    }


def _vr_log_row_cta(
    *,
    event: str,
    row_index: int,
    visual_candidate_id: str,
    span_index: int,
    cta: dict[str, Any],
    screenshot_path: str,
    source_profile_username: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "row_index": int(row_index),
        "visual_candidate_id": str(visual_candidate_id or ""),
        "span_index": int(span_index),
        "cta_class": str(cta.get("cta_class") or ""),
        "cta_text": str(cta.get("cta_text") or ""),
        "cta_followable": bool(cta.get("cta_followable")),
        "frac_blue_full": cta.get("frac_blue_full"),
        "frac_blue_tight": cta.get("frac_blue_tight"),
        "mean_lum_full": cta.get("mean_lum_full"),
        "aspect_ratio_full": cta.get("aspect_ratio_full"),
        "frac_greenish_full": cta.get("frac_greenish_full"),
        "screenshot_path": str(screenshot_path or ""),
        "source_profile_username": str(source_profile_username or ""),
    }
    if extra:
        payload.update(extra)
    log("info", event, **payload)


def visual_map_followers_rows_from_screenshot(
    screenshot_path: str,
    *,
    max_candidates: int = 5,
    min_confidence: float = 0.65,
) -> list[dict[str, Any]]:
    """
    Map blue-follow-button row bands to tap-safe coordinates on disk screenshot.

    Returns rows with ``tap_x`` / ``tap_y`` in **original** screenshot pixel space.
    ``follow_button_x`` / ``follow_button_y`` are informational (center of pill); never tap them here.
    """
    path = str(screenshot_path or "").strip()
    out: list[dict[str, Any]] = []
    max_candidates = max(1, min(50, int(max_candidates or 5)))

    log(
        "info",
        "followers_visual_row_mapping_started",
        screenshot_path=path,
        max_candidates=max_candidates,
        min_confidence=round(float(min_confidence), 4),
    )

    if not path:
        log(
            "info",
            "followers_visual_row_mapping_result",
            screenshot_path="",
            mapped_count=0,
            reason="no_screenshot_path",
        )
        return out

    try:
        from PIL import Image
    except Exception as e:
        log(
            "info",
            "followers_visual_row_mapping_result",
            screenshot_path=path,
            mapped_count=0,
            reason=f"pil_import_failed:{e}",
        )
        return out

    try:
        im_orig = Image.open(path)
        orig_w, orig_h = im_orig.size
        im_rgb = _visual_downscale_rgb(im_orig, max_w=480)
    except Exception as e:
        log(
            "info",
            "followers_visual_row_mapping_result",
            screenshot_path=path,
            mapped_count=0,
            reason=f"pil_open_failed:{e}",
        )
        return out

    aw, ah = im_rgb.size
    spans = _visual_collect_follow_row_y_spans(im_rgb)
    w, h = aw, ah
    cta_allowed_count = 0
    row_mapping_skip_reasons: dict[str, int] = {}

    def _bump_row_mapping_skip(reason: str) -> None:
        row_mapping_skip_reasons[reason] = row_mapping_skip_reasons.get(reason, 0) + 1

    # Original-space safe vertical band: ignore search/header + bottom nav.
    y_safe_top_o = int(orig_h * 0.12)
    y_safe_bottom_o = int(orig_h * 0.86)

    for idx, (yt, yb, peak) in enumerate(spans):
        if len(out) >= max_candidates:
            break
        pad = max(4, (yb - yt + 1) // 3)
        row_top = max(int(h * 0.052), yt - pad)
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
        if row_conf < float(min_confidence):
            continue

        cta = visual_followers_row_cta_classify(
            im_rgb,
            fb_left=fb_left,
            fb_top=yt,
            fb_right=fb_right,
            fb_bottom=yb,
            tight_bounds=tight,
        )
        candidate_id = f"vf_row_{idx}"
        _vr_log_row_cta(
            event="followers_row_cta_classified",
            row_index=idx,
            visual_candidate_id=candidate_id,
            span_index=idx,
            cta=cta,
            screenshot_path=path,
            extra={"selection_method": "visual_row_mapping"},
        )
        if not bool(cta.get("cta_followable")):
            rej = {
                "cta_reject_reason": str(cta.get("cta_class") or "unknown"),
                "selection_method": "visual_row_mapping",
            }
            _vr_log_row_cta(
                event="followers_row_cta_rejected",
                row_index=idx,
                visual_candidate_id=candidate_id,
                span_index=idx,
                cta=cta,
                screenshot_path=path,
                extra=rej,
            )
            if str(cta.get("cta_class") or "") in (
                "message",
                "contact",
                "following",
                "requested",
                "connected_gray",
                "inactive",
            ) or (
                str(cta.get("cta_class") or "") == "unknown"
                and float(cta.get("frac_blue_full") or 0.0) < 0.045
            ):
                log(
                    "info",
                    "followers_visual_candidate_rejected_already_connected",
                    row_index=idx,
                    visual_candidate_id=candidate_id,
                    cta_class=str(cta.get("cta_class") or ""),
                    cta_text=str(cta.get("cta_text") or ""),
                    reason="row_cta_non_follow_or_connected",
                    span_index=idx,
                    screenshot_path=path,
                )
            continue
        _vr_log_row_cta(
            event="followers_row_cta_allowed",
            row_index=idx,
            visual_candidate_id=candidate_id,
            span_index=idx,
            cta=cta,
            screenshot_path=path,
            extra={"selection_method": "visual_row_mapping"},
        )
        cta_allowed_count += 1

        row_o = _scale_bounds_to_original(
            approx_row_bounds, aw=aw, ah=ah, orig_w=orig_w, orig_h=orig_h
        )
        av_o = _scale_bounds_to_original(
            approx_avatar_bounds, aw=aw, ah=ah, orig_w=orig_w, orig_h=orig_h
        )
        tz_o = _scale_bounds_to_original(
            approx_username_text_zone, aw=aw, ah=ah, orig_w=orig_w, orig_h=orig_h
        )
        fb_o = _scale_bounds_to_original(
            approx_follow_button_bounds, aw=aw, ah=ah, orig_w=orig_w, orig_h=orig_h
        )

        # Tap: left side (avatar column center), never Follow column.
        tap_x = int((int(av_o["left"]) + int(av_o["right"])) // 2)
        tap_y = int((int(av_o["top"]) + int(av_o["bottom"])) // 2)

        if tap_y < y_safe_top_o or tap_y > y_safe_bottom_o:
            _bump_row_mapping_skip("tap_y_outside_safe_vertical_band")
            log(
                "info",
                "followers_visual_row_mapping_skip",
                reason="tap_y_outside_safe_vertical_band",
                tap_y=tap_y,
                y_safe_top_o=y_safe_top_o,
                y_safe_bottom_o=y_safe_bottom_o,
                span_index=idx,
            )
            continue

        # Extra guard: tap must sit left of follow button band (never on pill).
        if tap_x >= int(fb_o["left"]) - int(orig_w * 0.02):
            _bump_row_mapping_skip("tap_x_too_close_to_follow_column")
            log(
                "info",
                "followers_visual_row_mapping_skip",
                reason="tap_x_too_close_to_follow_column",
                tap_x=tap_x,
                follow_column_left=int(fb_o["left"]),
                span_index=idx,
            )
            continue

        fbcx = int((int(fb_o["left"]) + int(fb_o["right"])) // 2)
        fbcy = int((int(fb_o["top"]) + int(fb_o["bottom"])) // 2)

        row_rec: dict[str, Any] = {
            "candidate_id": candidate_id,
            "tap_x": tap_x,
            "tap_y": tap_y,
            "follow_button_x": fbcx,
            "follow_button_y": fbcy,
            "row_top": int(row_o["top"]),
            "row_bottom": int(row_o["bottom"]),
            "confidence": round(row_conf, 4),
            "source": "visual_row_mapping",
            "span_index": idx,
            "row_cta_class": str(cta.get("cta_class") or ""),
            "row_cta_text": str(cta.get("cta_text") or ""),
            "row_cta_blue_frac": max(
                float(cta.get("frac_blue_tight") or 0.0),
                float(cta.get("frac_blue_full") or 0.0),
            ),
            "approx_row_bounds": row_o,
            "approx_avatar_bounds": av_o,
            "approx_username_text_zone": tz_o,
            "approx_follow_button_bounds": fb_o,
        }
        row_rec.update(
            _compute_visual_candidate_tap_points_from_bounds(row_o, av_o, tz_o, fb_o)
        )
        out.append(row_rec)
        log(
            "info",
            "followers_visual_row_candidate_created",
            candidate_id=candidate_id,
            tap_x=tap_x,
            tap_y=tap_y,
            follow_button_x=fbcx,
            follow_button_y=fbcy,
            row_top=row_rec["row_top"],
            row_bottom=row_rec["row_bottom"],
            confidence=row_rec["confidence"],
            span_index=idx,
            screenshot_path=path,
        )

    log(
        "info",
        "followers_visual_row_mapping_result",
        screenshot_path=path,
        mapped_count=len(out),
        span_count=len(spans),
        original_size=(orig_w, orig_h),
        analysis_size=(aw, ah),
        min_confidence=round(float(min_confidence), 4),
    )
    if spans and not out:
        if cta_allowed_count > 0:
            nf_reason = "no_tap_safe_visual_candidate_after_cta_allowed"
        else:
            nf_reason = "no_row_passed_follow_cta_gate"
        log(
            "warning",
            "followers_visual_no_followable_candidate_found",
            screenshot_path=path,
            span_count=len(spans),
            mapped_count=len(out),
            cta_allowed_count=int(cta_allowed_count),
            row_mapping_skip_reasons=dict(row_mapping_skip_reasons),
            reason=nf_reason,
        )
    return out


def visual_row_mapping_to_follower_engine_candidates(
    mapped: list[dict[str, Any]],
    *,
    source_profile_username: str = "",
) -> list[dict[str, Any]]:
    """Convert mapping records into ``iter_followers_candidates``-compatible dicts."""
    built: list[dict[str, Any]] = []
    for i, m in enumerate(mapped):
        cid = str(m.get("candidate_id") or f"vf_row_{i}")
        tap_x = int(m["tap_x"])
        tap_y = int(m["tap_y"])
        row_o = dict(m.get("approx_row_bounds") or {})
        fb_o = dict(m.get("approx_follow_button_bounds") or {})
        av_o = dict(m.get("approx_avatar_bounds") or {})
        tz_o = dict(m.get("approx_username_text_zone") or {})
        tap_extra = _compute_visual_candidate_tap_points_from_bounds(row_o, av_o, tz_o, fb_o)
        uax = int(tap_extra.get("username_area_tap_x") or 0)
        uay = int(tap_extra.get("username_area_tap_y") or 0)
        row_center: list[int] = [uax, uay] if uax > 0 and uay > 0 else [tap_x, tap_y]
        built.append(
            {
                "username": cid,
                "visual_candidate_id": cid,
                "row_center": row_center,
                "username_pending_profile_read": True,
                "confidence": float(m.get("confidence") or 0.0),
                "row_index": i,
                "span_index": int(m.get("span_index") or i),
                "row_cta_class": str(m.get("row_cta_class") or ""),
                "row_cta_text": str(m.get("row_cta_text") or ""),
                "row_cta_blue_frac": float(m.get("row_cta_blue_frac") or 0.0),
                "approx_row_bounds": row_o,
                "approx_follow_button_bounds": fb_o,
                "approx_avatar_bounds": av_o,
                "approx_username_text_zone": tz_o,
                "resolved_username_hint": "",
                "selection_method": "visual_row_mapping",
                "source": str(m.get("source") or "visual_row_mapping"),
                "source_profile_username": source_profile_username,
                **tap_extra,
            }
        )
    return built
