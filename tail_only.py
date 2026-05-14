def _vision_validation_post_follow_before_mute(
    *,
    screenshot_path: str,
    det: dict[str, Any],
    nav_obs: dict[str, Any],
    overlay: dict[str, Any],
    fp: dict[str, Any],
    visual_candidate_id: str,
    source_profile_username: str,
) -> bool:
    if not _vision_validation_enabled():
        return True
    try:
        from vision_validation_layer import vision_validate_post_follow_surface
    except Exception:
        return True
    ctx = {
        "det": det if isinstance(det, dict) else {},
        "nav_state": str(nav_obs.get("state") or ""),
        "overlay": overlay if isinstance(overlay, dict) else {},
        "fingerprint": fp if isinstance(fp, dict) else {},
    }
    r = vision_validate_post_follow_surface(
        str(screenshot_path or ""),
        source_profile_username=source_profile_username,
        context=ctx,
    )
    if not r.get("safe_to_continue") or r.get("danger"):
        log(
            "warning",
            "vision_validation_post_follow_danger_surface",
            visual_candidate_id=str(visual_candidate_id or ""),
            source_profile_username=str(source_profile_username or ""),
            vision=r,
        )
        log(
            "warning",
            "vision_validation_safe_stop_required",
            phase="post_follow_pre_mute",
            visual_candidate_id=str(visual_candidate_id or ""),
            source_profile_username=str(source_profile_username or ""),
            vision=r,
        )
        return False
    return True


def _vision_validation_mute_engine_surface(
    *,
    screenshot_path: str,
    det: dict[str, Any],
    nav: dict[str, Any],
    overlay: dict[str, Any],
    fp: dict[str, Any],
    follow_header_snapshot: str,
    follow_state_after: str,
    visual_candidate_id: str,
    source_profile_username: str,
) -> bool:
    if not _vision_validation_enabled():
        return True
    try:
        from vision_validation_layer import vision_validate_mute_surface
    except Exception:
        return True
    fs = str(follow_state_after or "").strip().lower()
    following_vis = fs in ("following", "requested")
    ctx = {
        "det": det if isinstance(det, dict) else {},
        "nav_state": str(nav.get("state") or ""),
        "overlay": overlay if isinstance(overlay, dict) else {},
        "fingerprint": fp if isinstance(fp, dict) else {},
        "follow_header_snapshot": follow_header_snapshot,
        "following_visible": following_vis,
    }
    r = vision_validate_mute_surface(
        str(screenshot_path or ""),
        source_profile_username=source_profile_username,
        context=ctx,
    )
    if not r.get("safe_to_continue"):
        log(
            "warning",
            "vision_validation_rejected_danger_surface",
            phase="mute_engine_v2_pre_surface",
            visual_candidate_id=str(visual_candidate_id or ""),
            source_profile_username=str(source_profile_username or ""),
            vision=r,
        )
        return False
    return True


def _followers_entry_v2_post_tap_confirm(
    d: u2.Device,
    tap_diag: dict[str, Any],
    source_profile_username: str,
    *,
    post_tap_settle_s: float = 2.0,
) -> tuple[bool, dict[str, Any], dict[str, Any], int]:
    """Single immediate capture + one optional short re-detect; no hierarchy refresh recovery."""
    _followers_reset_post_tap_capture_gate()
    paths, det_imm = _followers_after_tap_immediate_capture_and_detect(
        d,
        source_profile_username=source_profile_username,
        settle_seconds=float(post_tap_settle_s),
    )
    det_imm = _followers_entry_v2_enrich_transition_visual_detail(
        d,
        dict(det_imm),
        paths.get("screenshot_path"),
        source_profile_username,
    )
    tap_diag["followers_after_tap_immediate_screenshot_path"] = paths.get("screenshot_path")
    tap_diag["followers_after_tap_immediate_xml_path"] = paths.get("xml_path")
    tap_diag["followers_list_post_tap_capture_done"] = True
    tap_diag["entry_engine_v2"] = True
    if det_imm.get("is_followers_list"):
        qok, qwhy = _followers_entry_v2_list_open_quality_ok(d, det_imm, tap_diag)
        if qok:
            ok_vis, vis_r = _vision_validation_followers_list_open_gate(
                screenshot_path=str(paths.get("screenshot_path") or ""),
                det=det_imm,
                tap_diag=tap_diag,
                source_profile_username=source_profile_username,
            )
            if not ok_vis:
                tap_diag["entry_v2_vision_validation_reject"] = "followers_list_surface"
                tap_diag["entry_v2_vision_validation_result"] = vis_r
                _followers_entry_v2_log_transition_visual_list_rejected(
                    d,
                    det_imm,
                    tap_diag,
                    rejection_reason="vision_validation_rejected",
                    quality_reason=str(qwhy or ""),
                    vision_validation_result=vis_r,
                    phase="immediate_vision_gate",
                )
                return False, det_imm, det_imm, _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT
            return True, det_imm, det_imm, _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT
        tap_diag["entry_v2_quality_reject_immediate"] = qwhy
        try:
            log(
                "warning",
                "followers_entry_transition_failed",
                source_profile_username=source_profile_username,
                phase="immediate_quality_gate",
                quality_reason=qwhy,
                strict_list_open=bool(det_imm.get("strict_list_open")),
                title_match=bool(det_imm.get("title_match")),
                relaxed_list_open=bool(det_imm.get("relaxed_list_open")),
            )
        except Exception:
            pass
        _followers_entry_v2_log_transition_visual_list_rejected(
            d,
            det_imm,
            tap_diag,
            rejection_reason="quality_gate_rejected",
            quality_reason=str(qwhy or ""),
            vision_validation_result="not_evaluated",
            phase="immediate_quality_gate",
        )
    time.sleep(0.42)
    det2 = detect_followers_list_screen(
        d, source_profile_username=source_profile_username
    )
    det2 = _followers_apply_visual_fallback_if_needed(
        d,
        det2,
        paths.get("screenshot_path"),
        source_profile_username,
        phase="followers_entry_v2_second_pass",
    )
    det2 = _followers_entry_v2_enrich_transition_visual_detail(
        d,
        dict(det2),
        paths.get("screenshot_path"),
        source_profile_username,
    )
    tap_diag["entry_v2_second_pass_det"] = det2
    if not det2.get("is_followers_list"):
        return False, det2, det2, _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT
    q2, qwhy2 = _followers_entry_v2_list_open_quality_ok(d, det2, tap_diag)
    if not q2:
        tap_diag["entry_v2_quality_reject_second_pass"] = qwhy2
        try:
            log(
                "warning",
                "followers_entry_transition_failed",
                source_profile_username=source_profile_username,
                phase="second_pass_quality_gate",
                quality_reason=qwhy2,
                strict_list_open=bool(det2.get("strict_list_open")),
                title_match=bool(det2.get("title_match")),
                relaxed_list_open=bool(det2.get("relaxed_list_open")),
            )
        except Exception:
            pass
        _followers_entry_v2_log_transition_visual_list_rejected(
            d,
            det2,
            tap_diag,
            rejection_reason="quality_gate_rejected",
            quality_reason=str(qwhy2 or ""),
            vision_validation_result="not_evaluated",
            phase="second_pass_quality_gate",
        )
        return False, det2, det2, _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT
    ok_vis2, vis_r2 = _vision_validation_followers_list_open_gate(
        screenshot_path=str(paths.get("screenshot_path") or ""),
        det=det2,
        tap_diag=tap_diag,
        source_profile_username=source_profile_username,
    )
    if not ok_vis2:
        tap_diag["entry_v2_vision_validation_reject_second"] = "followers_list_surface"
        tap_diag["entry_v2_vision_validation_result_second"] = vis_r2
        _followers_entry_v2_log_transition_visual_list_rejected(
            d,
            det2,
            tap_diag,
            rejection_reason="vision_validation_rejected",
            quality_reason=str(qwhy2 or ""),
            vision_validation_result=vis_r2,
            phase="second_pass_vision_gate",
        )
        return False, det2, det2, _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT
    return True, det2, det2, _FOLLOWERS_POST_TAP_DETECT_ATTEMPT_COUNT


def _followers_entry_v2_raw_metric_tap_strategies(
    bounds: dict[str, Any],
    *,
    screen_w: int,
) -> list[tuple[str, int, int]]:
    """In-bounds tap points for harvested followers metric — LTR strip: never right-biased (Following)."""
    try:
        l = int(bounds.get("left", 0))
        t = int(bounds.get("top", 0))
        r = int(bounds.get("right", 0))
        b = int(bounds.get("bottom", 0))
    except Exception:
        return []
    if r <= l or b <= t:
        return []
    sw = max(1, int(screen_w))
    max_tx = int(sw * float(_FOLLOWERS_METRIC_SCREEN_MAX_NX))
    wi = r - l
    hi = b - t
    margin = max(2, min(wi, hi) // 20)
    inner_r = min(r - margin, max_tx)
    if inner_r <= l + margin:
        return []

    def _clamp(px: float, py: float) -> tuple[int, int]:
        xi = int(round(px))
        yi = int(round(py))
        xi = max(l + margin, min(inner_r, xi))
        yi = max(t + margin, min(b - margin, yi))
        return xi, yi

    # Only center column probes — no right_center / no right-side label_area.
    raw_pts: list[tuple[str, float, float]] = [
        ("center", (l + r) / 2, (t + b) / 2),
        ("upper_center", (l + r) / 2, t + hi / 4),
        ("lower_center", (l + r) / 2, t + 3 * hi / 4),
        ("value_area", l + 0.32 * wi, t + 0.30 * hi),
        ("label_area", l + 0.36 * wi, t + 0.72 * hi),
    ]
    out: list[tuple[str, int, int]] = []
    seen_xy: set[tuple[int, int]] = set()
    for name, px, py in raw_pts:
        tx, ty = _clamp(px, py)
        key = (tx, ty)
        if key in seen_xy:
            continue
        seen_xy.add(key)
        out.append((name, tx, ty))
    return out


def _followers_entry_v2_still_on_source_profile_surface(
    det: dict[str, Any],
    *,
    source_profile_username: str,
) -> bool:
    """True when a failed tap likely left us on the source profile (safe to try another in-bounds tap)."""
    if bool(det.get("is_followers_list")):
        return False
    ab = str(det.get("action_bar_title") or "").strip()
    abn = _normalize_handle(ab) if ab else ""
    srcn = _normalize_handle(str(source_profile_username or ""))
    if abn and srcn and abn == srcn:
        return True
    guess = str(det.get("current_screen_guess") or "").lower()
    if not abn and srcn and "profile" in guess:
        return True
    return False


def _open_followers_list_from_profile_v2(
    d: u2.Device,
    source_profile_username: str,
    pkg: str,
    *,
    profile_verified: bool,
    pkg_meta: dict[str, Any],
    screen_guess: str,
    before_scan_cap: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    from navigation_engine import NavigationEngineState, observe_instagram_state

    min_conf = float(getattr(config, "FOLLOWERS_ENTRY_V2_MIN_CONFIDENCE", 0.62) or 0.62)
    try:
        w, h = d.window_size()
    except Exception:
        w, h = 1080, 1920

    entry_debug_cap = _followers_debug_capture(d, "followers_entry_surface")
    hier_followers_entry = ""
    if entry_debug_cap.get("xml_path"):
        try:
            hier_followers_entry = Path(entry_debug_cap["xml_path"]).read_text(
                encoding="utf-8"
            )
        except Exception:
            hier_followers_entry = ""
    stats_band_xml_path_val: str | None = None
    if hier_followers_entry:
        try:
            _ensure_debug_dirs()
            _snippet_path = _XML_DIR / "followers_entry_stats_band_snippet.xml"
            if _followers_stats_band_write_snippet_xml(
                hier_followers_entry, _snippet_path, int(w), int(h)
            ):
                stats_band_xml_path_val = str(_snippet_path)
        except Exception:
            stats_band_xml_path_val = None
    try:
        log(
            "info",
            "followers_entry_debug_artifacts_saved",
            source_profile_username=source_profile_username,
            screenshot_path=entry_debug_cap.get("screenshot_path"),
            xml_dump_path=entry_debug_cap.get("xml_path"),
            stats_band_xml_path=stats_band_xml_path_val,
            before_scan_screenshot_path=before_scan_cap.get("screenshot_path"),
            before_scan_xml_path=before_scan_cap.get("xml_path"),
        )
    except Exception:
        pass

    det_surface = detect_followers_list_screen(
        d, source_profile_username=source_profile_username
    )
    nav_ctx = {
        "phase": "followers_entry_surface",
        "source_profile_username": source_profile_username,
        "det": det_surface,
        "disable_followers_visual_fallback": True,
    }
    try:
        nav_s = observe_instagram_state(
            d,
            expected_package=pkg,
            last_known_state=NavigationEngineState.PROFILE.value,
            context=nav_ctx,
        )
    except Exception as e:
        nav_s = {"state": "UNKNOWN", "confidence": 0.0, "reason": str(e)}
    fp_s = _post_follow_screen_fingerprint(
        d, nav_state=str(nav_s.get("state") or ""), det=det_surface
    )
    stats_harvest_raw_node_count = len(
        _followers_stats_harvest_raw_nodes_from_hierarchy_xml(
            hier_followers_entry, int(w), int(h)
        )
    )
    band = _collect_profile_stats_band_texts(d, w, h)
    stats_band_candidates = [
        {
            "text": (r.get("text") or "").strip()[:48],
            "cx": r.get("cx"),
            "cyy": r.get("cyy"),
        }
        for r in band[:36]
    ]
    xml_stats_candidates = [
        {
            "text": (r.get("text") or "").strip()[:48],
            "content_desc": (str(r.get("content_desc") or ""))[:120],
            "bounds": r.get("bounds"),
            "resourceId": r.get("resourceId"),
        }
        for r in (_followers_collect_profile_text_dump(d, w, h)[:40])
    ]
    vis_sample = list((det_surface.get("visible_header_texts") or [])[:24])
    visual_stats_candidates = [
        {
            "text": (r.get("text") or "").strip()[:48],
            "cx": r.get("cx"),
            "cyy": r.get("cyy"),
        }
        for r in band
        if _followers_label_match(
            (r.get("raw_text") or r.get("text") or "").strip()
        )
    ][:24]
    log(
        "info",
        "followers_entry_surface_analysis_started",
        source_profile_username=source_profile_username,
        package=pkg,
        profile_verified=bool(profile_verified),
        visible_texts_sample=vis_sample,
        stats_band_candidates=stats_band_candidates,
        action_bar_title=str(det_surface.get("action_bar_title") or "")[:120],
        profile_fingerprint_id=fp_s.get("fingerprint_id"),
        profile_screen_class=fp_s.get("screen_class"),
        current_screen_guess=str(det_surface.get("current_screen_guess") or ""),
        navigation_state=str(nav_s.get("state") or ""),
        navigation_confidence=float(nav_s.get("confidence") or 0.0),
        xml_stats_candidates=xml_stats_candidates,
        visual_stats_candidates=visual_stats_candidates,
        screenshot_path=entry_debug_cap.get("screenshot_path"),
        xml_dump_path=entry_debug_cap.get("xml_path"),
        stats_band_xml_path=stats_band_xml_path_val,
        stats_harvest_raw_node_count=stats_harvest_raw_node_count,
    )

    candidates = detect_followers_entry_candidates(
        d,
        source_profile_username,
        pkg,
        width=w,
        height=h,
        hierarchy_xml=hier_followers_entry or None,
        profile_verified=profile_verified,
        profile_screen_class=str(fp_s.get("screen_class") or ""),
        profile_action_bar_title=str(det_surface.get("action_bar_title") or ""),
    )
    for c in candidates[:14]:
        log(
            "info",
            "followers_entry_candidate_detected",
            source_profile_username=source_profile_username,
            candidate=c,
        )
    eligible = [c for c in candidates if float(c.get("confidence") or 0.0) >= min_conf]
    if not eligible:
        log(
            "warning",
            "followers_entry_safe_abort",
            source_profile_username=source_profile_username,
            reason="no_candidate_meets_confidence",
            min_confidence=min_conf,
            candidate_count=len(candidates),
        )
        try:
            force_stop(d, pkg)
        except Exception as e:
            log("warning", "followers_entry_v2_force_stop_failed", error=str(e), package=pkg)
        tap_diag_empty: dict[str, Any] = {
            "followers_stat_found": False,
            "tap_method": "entry_v2_no_tap",
            "followers_stat_coordinate_retry": False,
            "followers_coord_fallback": False,
            "stats_band_texts": [(r.get("text") or "").strip() for r in band[:40]],
            "followers_stat_text_dump": _followers_collect_profile_text_dump(d, w, h),
            "profile_stats_visible": [],
            "entry_engine_v2": True,
            "entry_v2_abort": "no_candidate_meets_confidence",
        }
        cap = _followers_debug_capture(d, "followers_entry_v2_no_candidate")
        fmeta = _followers_open_build_failure_meta(
            source_profile_username=source_profile_username,
            failure_reason="entry_v2_no_stat_candidate",
            profile_verified=profile_verified,
            pkg_meta=pkg_meta,
            screen_guess=screen_guess,
            tap_diag=tap_diag_empty,
            after_tap_screen_snapshot={},
            last_poll_snapshot=dict(det_surface),
            cap=cap,
            open_method="followers_entry_engine_v2",
            used_coord_fallback=False,
        )
        _followers_open_emit_failure(fmeta)
        return False, fmeta

    col_ok: list[dict[str, Any]] = []
    for c in eligible:
        okm, whym = _followers_entry_v2_entry_candidate_followers_metric_ok(c, w=int(w))
        if okm:
            col_ok.append(c)
        else:
            try:
                log(
                    "warning",
                    "followers_entry_following_metric_rejected",
                    source_profile_username=source_profile_username,
                    candidate=c,
                    rejection_reason=whym,
                    expected_column="followers",
                )
            except Exception:
                pass
    if not col_ok:
        log(
            "warning",
            "followers_entry_safe_abort",
            source_profile_username=source_profile_username,
            reason="no_followers_column_candidate_after_filter",
            min_confidence=min_conf,
            candidate_count=len(candidates),
            eligible_after_confidence=len(eligible),
        )
        try:
            force_stop(d, pkg)
        except Exception as e:
            log("warning", "followers_entry_v2_force_stop_failed", error=str(e), package=pkg)
        tap_diag_empty: dict[str, Any] = {
            "followers_stat_found": False,
            "tap_method": "entry_v2_no_followers_column_tap",
            "followers_stat_coordinate_retry": False,
            "followers_coord_fallback": False,
            "stats_band_texts": [(r.get("text") or "").strip() for r in band[:40]],
            "followers_stat_text_dump": _followers_collect_profile_text_dump(d, w, h),
            "profile_stats_visible": [],
            "entry_engine_v2": True,
            "entry_v2_abort": "followers_metric_column_filter_empty",
        }
        cap = _followers_debug_capture(d, "followers_entry_v2_column_filter_empty")
        fmeta = _followers_open_build_failure_meta(
            source_profile_username=source_profile_username,
            failure_reason="entry_v2_followers_metric_column_reject_all",
            profile_verified=profile_verified,
            pkg_meta=pkg_meta,
            screen_guess=screen_guess,
            tap_diag=tap_diag_empty,
            after_tap_screen_snapshot={},
            last_poll_snapshot=dict(det_surface),
            cap=cap,
            open_method="followers_entry_engine_v2",
            used_coord_fallback=False,
        )
        _followers_open_emit_failure(fmeta)
        return False, fmeta

    best = max(col_ok, key=lambda x: float(x.get("confidence") or 0.0))
    log(
        "info",
        "followers_entry_followers_metric_selected",
        source_profile_username=source_profile_username,
        confidence=best.get("confidence"),
        tap_x=best.get("tap_x"),
        tap_y=best.get("tap_y"),
        source=best.get("source"),
        method=best.get("method"),
        stat_type=str(best.get("stat_type") or ""),
        bounds=best.get("bounds"),
    )
    log(
        "info",
        "followers_entry_candidate_selected",
        source_profile_username=source_profile_username,
        confidence=best.get("confidence"),
        tap_x=best.get("tap_x"),
        tap_y=best.get("tap_y"),
        source=best.get("source"),
        method=best.get("method"),
    )

    cx = int(best["tap_x"])
    cy = int(best["tap_y"])
    _fs_dump = _followers_collect_profile_text_dump(d, w, h)
    _sigs = best.get("signals") or []
    _label = ""
    if isinstance(_sigs, list):
        for _p in _sigs:
            if (
                isinstance(_p, str)
                and _p
                and not _p.startswith("score:")
                and "resource_id" not in _p
            ):
                _label = _p
                break
    if not _label:
        _label = str(best.get("method") or "")
    tap_diag: dict[str, Any] = {
        "followers_stat_found": True,
        "followers_stat_text": _label,
        "followers_stat_bounds": best.get("bounds"),
        "tap_x": cx,
        "tap_y": cy,
        "tap_method": str(best.get("method") or "entry_v2_hybrid"),
        "followers_stat_text_detected": str(best.get("method")),
        "followers_stat_text_bounds": best.get("bounds"),
        "followers_stat_tap_x": cx,
        "followers_stat_tap_y": cy,
        "followers_stat_tap_source": "followers_entry_engine_v2",
        "followers_stat_text_dump": _fs_dump,
        "stats_band_texts": [(r.get("text") or "").strip() for r in band[:40]],
        "profile_stats_visible": list(_fs_dump),
        "followers_stat_coordinate_retry": False,
        "followers_coord_fallback": False,
        "followers_exact_rid_used": str(best.get("method") or "").startswith(
            "resource_id_profile_header"
        ),
        "debug_before_scan_screenshot_path": before_scan_cap.get("screenshot_path"),
        "debug_before_scan_xml_path": before_scan_cap.get("xml_path"),
        "profile_rescan_swiped": False,
        "entry_engine_v2": True,
        "entry_v2_source_profile_username": source_profile_username,
        "entry_v2_profile_fingerprint_id_before": str(fp_s.get("fingerprint_id") or ""),
    }

    cand_method = str(best.get("method") or "")
    bd_grid = dict(best.get("bounds") or tap_diag.get("followers_stat_bounds") or {})
    strat_rows = _followers_entry_v2_raw_metric_tap_strategies(
        bd_grid, screen_w=int(w)
    )
    use_raw_grid = cand_method == "harvest_raw_clickable_followers_metric" and bool(
        strat_rows
    )

    log(
        "info",
        "followers_entry_transition_started",
        source_profile_username=source_profile_username,
        tap_x=cx,
        tap_y=cy,
        confidence=best.get("confidence"),
        candidate_method=cand_method,
        multi_tap_strategies=bool(use_raw_grid),
        bounds=bd_grid if use_raw_grid else best.get("bounds"),
    )

    def _v2_post_tap_confirm_wrapped(settle_s: float) -> tuple[bool, dict, dict, int]:
        _followers_enable_post_tap_detection_lock(source_profile_username)
        try:
            return _followers_entry_v2_post_tap_confirm(
                d,
                tap_diag,
                source_profile_username,
                post_tap_settle_s=float(settle_s),
            )
        finally:
            _followers_release_post_tap_detection_lock(source_profile_username)

    opened = False
    after_det: dict[str, Any] = {}
    last_det: dict[str, Any] = {}
    poll_n = 0

    if use_raw_grid:
        log(
            "info",
            "followers_entry_tap_strategy_started",
            source_profile_username=source_profile_username,
            candidate_method=cand_method,
            bounds=bd_grid,
            strategies=[s[0] for s in strat_rows],
        )
        max_tx_gate = int(int(w) * float(_FOLLOWERS_METRIC_SCREEN_MAX_NX))
        for si, (st_name, tx, ty) in enumerate(strat_rows):
            log(
                "info",
                "followers_entry_tap_strategy_attempt",
                source_profile_username=source_profile_username,
                strategy=st_name,
                strategy_index=int(si),
                tap_x=int(tx),
                tap_y=int(ty),
                bounds=bd_grid,
                candidate_method=cand_method,
            )
            if int(tx) > max_tx_gate:
                try:
                    log(
                        "warning",
                        "followers_entry_following_zone_rejected",
                        source_profile_username=source_profile_username,
                        reason="tap_x_over_screen_gate",
                        tap_x=int(tx),
                        tap_y=int(ty),
                        screen_width=int(w),
                        max_tap_x=int(max_tx_gate),
                        normalized_x=round(int(tx) / float(max(1, int(w))), 5),
                        strategy=st_name,
                        candidate_method=cand_method,
                    )
                except Exception:
                    pass
                continue
            try:
                d.click(int(tx), int(ty))
            except Exception as e:
                log(
                    "warning",
                    "followers_entry_tap_strategy_failed",
                    source_profile_username=source_profile_username,
                    strategy=st_name,
                    tap_x=int(tx),
                    tap_y=int(ty),
                    bounds=bd_grid,
                    action_bar_title_after=None,
                    is_followers_list=False,
                    current_screen_guess=None,
                    candidate_method=cand_method,
                    phase="tap_exception",
                    error=str(e),
                )
                continue
            tap_diag["tap_x"] = int(tx)
            tap_diag["tap_y"] = int(ty)
            tap_diag["followers_stat_tap_x"] = int(tx)
            tap_diag["followers_stat_tap_y"] = int(ty)
            settle_use = 0.88 if si == 0 else 0.48
            opened, after_det, last_det, poll_n = _v2_post_tap_confirm_wrapped(
                settle_use
            )
            ab_after = str(last_det.get("action_bar_title") or "")[:120]
            is_list = bool(last_det.get("is_followers_list"))
            guess = str(last_det.get("current_screen_guess") or "")
            still_src = _followers_entry_v2_still_on_source_profile_surface(
                last_det, source_profile_username=source_profile_username
            )
            log(
                "info",
                "followers_entry_tap_strategy_result",
                source_profile_username=source_profile_username,
                strategy=st_name,
                tap_x=int(tx),
                tap_y=int(ty),
                bounds=bd_grid,
                action_bar_title_after=ab_after,
                is_followers_list=is_list,
                current_screen_guess=guess,
                candidate_method=cand_method,
                transition_opened=bool(opened),
                still_on_source_profile=bool(still_src),
            )
            if opened:
                tap_diag["entry_v2_raw_metric_tap_strategy"] = st_name
                log(
                    "info",
                    "followers_entry_tap_strategy_success",
                    source_profile_username=source_profile_username,
                    strategy=st_name,
                    tap_x=int(tx),
                    tap_y=int(ty),
                    bounds=bd_grid,
                    action_bar_title_after=ab_after,
                    is_followers_list=is_list,
                    current_screen_guess=guess,
                    candidate_method=cand_method,
                )
                try:
                    log(
                        "info",
                        "followers_entry_followers_zone_tap",
                        source_profile_username=source_profile_username,
                        strategy=st_name,
                        tap_x=int(tx),
                        tap_y=int(ty),
                        screen_width=int(w),
                        normalized_x=round(int(tx) / float(max(1, int(w))), 5),
                        candidate_method=cand_method,
                    )
                except Exception:
                    pass
                break
            log(
                "warning",
                "followers_entry_tap_strategy_failed",
                source_profile_username=source_profile_username,
                strategy=st_name,
                tap_x=int(tx),
                tap_y=int(ty),
                bounds=bd_grid,
                action_bar_title_after=ab_after,
                is_followers_list=is_list,
                current_screen_guess=guess,
                candidate_method=cand_method,
                still_on_source_profile=bool(still_src),
            )
            if not still_src:
                log(
                    "warning",
                    "followers_entry_tap_strategy_abort_not_source_profile",
                    source_profile_username=source_profile_username,
                    strategy=st_name,
                    action_bar_title_after=ab_after,
                    current_screen_guess=guess,
                )
                break
    else:
        try:
            d.click(cx, cy)
        except Exception as e:
            log(
                "warning",
                "followers_entry_transition_failed",
                source_profile_username=source_profile_username,
                phase="tap",
                error=str(e),
            )
            try:
                force_stop(d, pkg)
            except Exception:
                pass
            cap = _followers_debug_capture(d, "followers_entry_v2_tap_failed")
            fmeta = _followers_open_build_failure_meta(
                source_profile_username=source_profile_username,
                failure_reason="entry_v2_tap_failed",
                profile_verified=profile_verified,
                pkg_meta=pkg_meta,
                screen_guess=screen_guess,
                tap_diag=tap_diag,
                after_tap_screen_snapshot={},
                last_poll_snapshot={},
                cap=cap,
                open_method="followers_entry_engine_v2",
                used_coord_fallback=False,
            )
            _followers_open_emit_failure(fmeta)
            return False, fmeta
        opened, after_det, last_det, poll_n = _v2_post_tap_confirm_wrapped(2.0)
    if opened:
        log(
            "info",
            "followers_entry_transition_confirmed",
            source_profile_username=source_profile_username,
            poll_attempts=poll_n,
            open_detection_method=last_det.get("open_detection_method"),
        )
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
            entry_engine_v2=True,
            current_package=pkg_meta.get("current_package"),
            after_tap_screen_snapshot=after_det,
            last_poll_snapshot=last_det,
            open_method="followers_entry_engine_v2",
            open_detection_method=last_det.get("open_detection_method") or "xml",
        )
        _followers_release_post_tap_detection_lock(source_profile_username)
        _followers_set_last_open_detection_method(
            str(last_det.get("open_detection_method") or "xml")
        )
        return True, _followers_open_success_payload(
            tap_diag,
            after_tap_det=after_det,
            last_det=last_det,
            open_method="followers_entry_engine_v2",
            pkg_meta=pkg_meta,
            source_profile_username=source_profile_username,
            profile_verified=profile_verified,
        )

    log(
        "warning",
        "followers_entry_transition_failed",
        source_profile_username=source_profile_username,
        is_followers_list=bool(last_det.get("is_followers_list")),
        open_detection_method=last_det.get("open_detection_method"),
    )
    log(
        "warning",
        "followers_entry_safe_abort",
        source_profile_username=source_profile_username,
        reason="transition_not_confirmed",
    )
    try:
        force_stop(d, pkg)
    except Exception as e:
        log("warning", "followers_entry_v2_force_stop_failed", error=str(e), package=pkg)
    cap = _followers_debug_capture(d, "followers_entry_v2_transition_failed")
    _log_followers_list_detect_failed(
        d,
        last_det,
        source_profile_username=source_profile_username,
        stem="followers_entry_v2_detect_failed",
        cap=cap,
    )
    fmeta = _followers_open_build_failure_meta(
        source_profile_username=source_profile_username,
        failure_reason="screen_not_detected",
        profile_verified=profile_verified,
        pkg_meta=pkg_meta,
        screen_guess=screen_guess,
        tap_diag=tap_diag,
        after_tap_screen_snapshot=after_det,
        last_poll_snapshot=last_det,
        cap=cap,
        open_method="followers_entry_engine_v2",
        used_coord_fallback=False,
    )
    _followers_open_emit_failure(fmeta)
    _followers_release_post_tap_detection_lock(source_profile_username)
    return False, fmeta



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

    if bool(getattr(config, "ENABLE_FOLLOWERS_ENTRY_ENGINE_V2", False)):
        return _open_followers_list_from_profile_v2(
            d,
            source_profile_username,
            pkg,
            profile_verified=profile_verified,
            pkg_meta=pkg_meta,
            screen_guess=screen_guess,
            before_scan_cap=before_scan_cap,
        )

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
    vcid = str(candidate.get("visual_candidate_id") or "").strip()
    is_visual = bool(vcid)

    log(
        "info",
        "follower_profile_open_started",
        follower_username=un,
        source_profile_username=source_profile_username,
        row_center=candidate.get("row_center"),
        visual_candidate_id=vcid or None,
    )

    def _pkg_and_screen_guess() -> tuple[dict[str, Any], str]:
        meta: dict[str, Any] = {}
        try:
            meta = _followers_current_pkg_activity(d) or {}
        except Exception:
            meta = {}
        csg = ""
        try:
            det = detect_followers_list_screen(
                d, source_profile_username=source_profile_username
            )
            csg = str(det.get("current_screen_guess") or "")
        except Exception:
            csg = ""
        return meta, csg

    def _action_bar_title() -> str:
        try:
            return str(read_current_profile_username_for_follow_gate(d) or "").strip()
        except Exception:
            return ""

    def _is_source_action_bar(ab: str) -> bool:
        sn = _normalize_handle(source_profile_username or "")
        an = _normalize_handle(ab or "")
        return bool(sn and an and sn == an)

    def _tap_inside_follow_bounds(tx: int, ty: int, fb: dict[str, Any]) -> bool:
        if not fb:
            return False
        try:
            fl, ft, frb, fbot = (
                int(fb["left"]),
                int(fb["top"]),
                int(fb["right"]),
                int(fb["bottom"]),
            )
            return fl <= tx < frb and ft <= ty < fbot
        except (KeyError, TypeError, ValueError):
            return False

    def _finalize_success(ab_open: str) -> bool:
        if _is_source_action_bar(ab_open):
            log(
                "error",
                "follower_profile_open_false_positive_source_profile",
                follower_username=un,
                source_profile_username=source_profile_username,
                action_bar_title=ab_open,
                visual_candidate_id=vcid or None,
            )
            return False
        log(
            "info",
            "follower_profile_open_success",
            follower_username=un,
            source_profile_username=source_profile_username,
            action_bar_title=ab_open or None,
            visual_candidate_id=vcid or None,
        )
        return True

    if not is_visual:
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
            _ab_open = _action_bar_title()
            if not _finalize_success(_ab_open):
                return False
        else:
            log(
                "error",
                "follower_profile_open_failed",
                follower_username=un,
                source_profile_username=source_profile_username,
                reason="verify_profile_failed",
            )
        return ok

    # Visual candidate: multi-tap strategies, transition wait, strict anti–source-profile guard.
    work = dict(candidate)
    row_o = dict(work.get("approx_row_bounds") or {})
    av_o = dict(work.get("approx_avatar_bounds") or {})
    tz_o = dict(work.get("approx_username_text_zone") or {})
    fb_o = dict(work.get("approx_follow_button_bounds") or {})
    if row_o and av_o and tz_o and fb_o:
        work.update(_compute_visual_candidate_tap_points_from_bounds(row_o, av_o, tz_o, fb_o))

    strategies = _visual_open_strategy_taps_from_candidate(work)
    if not strategies:
        log(
            "error",
            "visual_candidate_open_tap_strategy_failed",
            follower_username=un,
            source_profile_username=source_profile_username,
            visual_candidate_id=vcid,
            reason="visual_candidate_open_all_tap_points_failed",
            detail="no_tap_strategies_from_candidate",
        )
        return False

    try:
        wwin, hwin = d.window_size()
    except Exception:
        wwin, hwin = 1080, 1920

    settle_s = float(getattr(config, "PROFILE_POST_TAP_STABILIZE_S", 0.12) or 0.12)
    last_fail_reason = ""

    def _sample_screen_fingerprint_bundle() -> dict[str, Any]:
        from navigation_engine import NavigationEngineState, observe_instagram_state

        meta = _followers_current_pkg_activity(d) or {}
        det: dict[str, Any] = {}
        try:
            det = detect_followers_list_screen(
                d, source_profile_username=source_profile_username
            ) or {}
        except Exception:
            det = {}
        lk = NavigationEngineState.FOLLOWERS_LIST.value
        try:
            if not bool(det.get("is_followers_list")):
                lk = NavigationEngineState.UNKNOWN.value
        except Exception:
            lk = NavigationEngineState.UNKNOWN.value
        nav: dict[str, Any] = {}
        try:
            nav = observe_instagram_state(
                d,
                expected_package=pkg,
                last_known_state=lk,
                context={
                    "source_profile_username": source_profile_username,
                    "det": det,
                    "phase": "screen_fingerprint_sample",
                },
            )
        except Exception:
            nav = {}
        fh = ""
        try:
            fh = _follow_ui_state_snapshot(d)
        except Exception:
            fh = ""
        raw_inv = False
        try:
            raw_inv = _visual_raw_follow_invite_visible_quick(d)
        except Exception:
            raw_inv = False
        return build_screen_fingerprint(
            action_bar_title=_action_bar_title(),
            current_package=str(meta.get("current_package") or ""),
            current_activity=str(meta.get("current_activity") or ""),
            visible_texts=None,
            nav_state=str(nav.get("state") or ""),
            visual_signals={
                "is_followers_list": bool(det.get("is_followers_list")),
                "strict_list_open": bool(det.get("strict_list_open")),
                "relaxed_list_open": bool(det.get("relaxed_list_open")),
                "follow_header_state": fh,
                "raw_follow_invite_visible": raw_inv,
                "nav_confidence": float(nav.get("confidence") or 0.0),
            },
            xml_guess=str(det.get("current_screen_guess") or ""),
            visual_guess=str(nav.get("visual_guess") or ""),
        )

    for si, (tap_strategy, raw_x, raw_y) in enumerate(strategies):
        tx = max(2, min(int(wwin) - 3, int(raw_x)))
        ty = max(2, min(int(hwin) - 3, int(raw_y)))
        if _tap_inside_follow_bounds(tx, ty, fb_o):
            last_fail_reason = "tap_coordinates_inside_follow_button_bounds"
            log(
                "warning",
                "visual_candidate_open_tap_attempt",
                tap_strategy=tap_strategy,
                tap_x=tx,
                tap_y=ty,
                source_profile_username=source_profile_username,
                visual_candidate_id=vcid,
                skipped=True,
                skip_reason="tap_inside_follow_button_bounds",
            )
            continue

        fp_before = _sample_screen_fingerprint_bundle()
        log(
            "info",
            "screen_fingerprint_before_candidate_open",
            fingerprint_id=fp_before.get("fingerprint_id"),
            screen_class=fp_before.get("screen_class"),
            confidence=fp_before.get("confidence"),
            hash_prefix=str(fp_before.get("hash") or "")[:16],
            source_profile_username=source_profile_username,
            visual_candidate_id=vcid,
            tap_strategy=tap_strategy,
            strategy_index=si,
            signals_summary={
                "nav_state": (fp_before.get("signals") or {}).get("nav_state"),
                "is_followers_list": (fp_before.get("signals") or {}).get(
                    "is_followers_list"
                ),
                "xml_guess": (fp_before.get("signals") or {}).get("xml_guess"),
                "action_bar_norm": (fp_before.get("signals") or {}).get(
                    "action_bar_norm"
                ),
            },
        )

        try:
            d.click(tx, ty)
        except Exception as e:
            last_fail_reason = f"click_failed:{e}"
            log(
                "error",
                "follower_profile_open_failed",
                follower_username=un,
                visual_candidate_id=vcid,
                tap_strategy=tap_strategy,
                error=str(e),
                reason="click_failed",
            )
            continue

        time.sleep(settle_s)
        _wait_visual_follower_open_transition(
            d, pkg=pkg, source_profile_username=source_profile_username
        )

        fp_after = _sample_screen_fingerprint_bundle()
        log(
            "info",
            "screen_fingerprint_after_candidate_open",
            fingerprint_id=fp_after.get("fingerprint_id"),
            screen_class=fp_after.get("screen_class"),
            confidence=fp_after.get("confidence"),
            hash_prefix=str(fp_after.get("hash") or "")[:16],
            source_profile_username=source_profile_username,
            visual_candidate_id=vcid,
            tap_strategy=tap_strategy,
            strategy_index=si,
            signals_summary={
                "nav_state": (fp_after.get("signals") or {}).get("nav_state"),
                "is_followers_list": (fp_after.get("signals") or {}).get(
                    "is_followers_list"
                ),
                "xml_guess": (fp_after.get("signals") or {}).get("xml_guess"),
                "action_bar_norm": (fp_after.get("signals") or {}).get(
                    "action_bar_norm"
                ),
            },
        )

        _cmp = compare_screen_fingerprints(fp_before, fp_after)
        log(
            "info",
            "screen_fingerprint_transition_compare",
            source_profile_username=source_profile_username,
            visual_candidate_id=vcid,
            tap_strategy=tap_strategy,
            strategy_index=si,
            same_screen=bool(_cmp.get("same_screen")),
            similarity=float(_cmp.get("similarity") or 0.0),
            changed_signals_count=len(_cmp.get("changed_signals") or []),
            changed_signal_keys=[
                x.get("key")
                for x in (_cmp.get("changed_signals") or [])[:12]
                if isinstance(x, dict)
            ],
        )

        if bool(_cmp.get("same_screen")):
            last_fail_reason = "screen_fingerprint_no_transition"
            log(
                "warning",
                "screen_fingerprint_transition_rejected",
                source_profile_username=source_profile_username,
                visual_candidate_id=vcid,
                tap_strategy=tap_strategy,
                strategy_index=si,
                similarity=float(_cmp.get("similarity") or 0.0),
                fingerprint_before=fp_before.get("fingerprint_id"),
                fingerprint_after=fp_after.get("fingerprint_id"),
            )
            if si + 1 < len(strategies):
                log(
                    "info",
                    "visual_candidate_open_tap_retry",
                    tap_strategy=tap_strategy,
                    next_strategy=strategies[si + 1][0],
                    source_profile_username=source_profile_username,
                    visual_candidate_id=vcid,
                    retry_reason="screen_fingerprint_no_transition",
                )
            continue

        log(
            "info",
            "screen_fingerprint_transition_confirmed",
            source_profile_username=source_profile_username,
            visual_candidate_id=vcid,
            tap_strategy=tap_strategy,
            strategy_index=si,
            similarity=float(_cmp.get("similarity") or 0.0),
            fingerprint_before=fp_before.get("fingerprint_id"),
            fingerprint_after=fp_after.get("fingerprint_id"),
        )

        meta1, csg1 = _pkg_and_screen_guess()
        ab_after = _action_bar_title()
        fp_after = ""
        try:
            cap = visual_capture_profile_context(
                d, source_profile_username=source_profile_username
            )
            fp_after = str(
                cap.get("profile_visual_fingerprint")
                or cap.get("profile_fingerprint")
                or ""
            )
        except Exception:
            fp_after = ""

        log(
            "info",
            "visual_candidate_open_tap_attempt",
            tap_strategy=tap_strategy,
            tap_x=tx,
            tap_y=ty,
            source_profile_username=source_profile_username,
            visual_candidate_id=vcid,
            follower_username=un,
            action_bar_title_after_tap=ab_after,
            current_package=str(meta1.get("current_package") or ""),
            current_screen_guess=csg1,
            profile_visual_fingerprint_after_tap=fp_after or None,
            strategy_index=si,
            strategy_total=len(strategies),
        )

        if _is_source_action_bar(ab_after):
            last_fail_reason = "still_on_source_profile_action_bar"
            if si + 1 < len(strategies):
                log(
                    "info",
                    "visual_candidate_open_tap_retry",
                    tap_strategy=tap_strategy,
                    next_strategy=strategies[si + 1][0],
                    source_profile_username=source_profile_username,
                    visual_candidate_id=vcid,
                    action_bar_title_after_tap=ab_after,
                    retry_reason="still_on_source_profile",
                )
            continue

        ok = verify_profile(d, un)
        if not ok:
            last_fail_reason = "verify_profile_failed"
            if si + 1 < len(strategies):
                log(
                    "info",
                    "visual_candidate_open_tap_retry",
                    tap_strategy=tap_strategy,
                    next_strategy=strategies[si + 1][0],
                    source_profile_username=source_profile_username,
                    visual_candidate_id=vcid,
                    retry_reason="verify_profile_failed",
                )
            continue

        ab_verify = _action_bar_title()
        if not _finalize_success(ab_verify):
            last_fail_reason = "verify_then_source_action_bar"
            if si + 1 < len(strategies):
                log(
                    "info",
                    "visual_candidate_open_tap_retry",
                    tap_strategy=tap_strategy,
                    next_strategy=strategies[si + 1][0],
                    source_profile_username=source_profile_username,
                    visual_candidate_id=vcid,
                    retry_reason="post_verify_source_profile_title",
                )
            continue

        log(
            "info",
            "visual_candidate_open_tap_strategy_success",
            tap_strategy=tap_strategy,
            tap_x=tx,
            tap_y=ty,
            source_profile_username=source_profile_username,
            visual_candidate_id=vcid,
            action_bar_title_after_tap=ab_verify,
        )
        return True

    log(
        "error",
        "visual_candidate_open_tap_strategy_failed",
        follower_username=un,
        source_profile_username=source_profile_username,
        visual_candidate_id=vcid,
        reason="visual_candidate_open_all_tap_points_failed",
        last_fail_reason=last_fail_reason,
        strategies_tried=[s[0] for s in strategies],
    )
    log(
        "error",
        "follower_profile_open_failed",
        follower_username=un,
        source_profile_username=source_profile_username,
        reason="visual_candidate_open_all_tap_points_failed",
        visual_candidate_id=vcid,
    )
    return False


def _post_follow_overlay_ui_hints(d: u2.Device) -> dict[str, Any]:
    """Lightweight sheet/popup hints for post-follow observation (no OCR)."""
    hints: dict[str, Any] = {}
    try:
        if d(textContains="Posts").exists(timeout=0.06) and d(
            textContains="Stories"
        ).exists(timeout=0.05):
            hints["likely_mute_toggle_sheet"] = True
    except Exception:
        pass
    for needle, key in (
        ("Suggested for you", "suggested_for_you"),
        ("Discover people", "discover_people"),
        ("Turn On Notifications", "turn_on_notifications"),
        ("notifications from", "notification_prompt"),
    ):
        try:
            if d(textContains=needle).exists(timeout=0.04):
                hints[key] = True
        except Exception:
            continue
    return hints


def _post_follow_screen_fingerprint(
    d: u2.Device,
    *,
    nav_state: str,
    det: dict[str, Any] | None,
) -> dict[str, Any]:
    try:
        meta = _followers_current_pkg_activity(d) or {}
        try:
            ab = read_current_profile_username_for_follow_gate(d)
        except Exception:
            ab = ""
        try:
            fh = _follow_ui_state_snapshot(d)
        except Exception:
            fh = ""
        det2 = det if isinstance(det, dict) else {}
        return build_screen_fingerprint(
            action_bar_title=ab,
            current_package=str(meta.get("current_package") or ""),
            current_activity=str(meta.get("current_activity") or ""),
            visible_texts=None,
            nav_state=nav_state,
            visual_signals={
                "is_followers_list": bool(det2.get("is_followers_list")),
                "strict_list_open": bool(det2.get("strict_list_open")),
                "relaxed_list_open": bool(det2.get("relaxed_list_open")),
                "follow_header_state": fh,
                "raw_follow_invite_visible": False,
                "nav_confidence": 0.0,
            },
            xml_guess=str(det2.get("current_screen_guess") or ""),
            visual_guess="",
        )
    except Exception:
        return {}


def _poll_follow_state_for_reconcile(
    d: u2.Device, *, rounds: int = 5, delay_s: float = 0.1
) -> str:
    """Short multi-poll header follow-state (handles slow UI after tap)."""
    last = "unknown"
    for i in range(max(1, int(rounds))):
        try:
            last = _follow_ui_state_snapshot(d)
        except Exception:
            last = "unknown"
        if last in ("following", "requested"):
            return last
        if i + 1 < rounds:
            time.sleep(float(delay_s))
    return last


def visual_follow_post_action_reconcile(
    d: u2.Device,
    *,
    follow_out: dict[str, Any],
    profile_already_open: bool,
    visual_candidate_id: str = "",
    mute_phase_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Reconcile ``perform_follow_safe`` / runner outcome with on-screen follow + mute signals.

    If the UI shows Following/Requested, mute completed, or success events were emitted,
    ``inferred_follow_success`` is True even when ``follow_out.ok`` was False (e.g. code 33/34).
    """
    vcid = str(visual_candidate_id or "").strip()
    log(
        "info",
        "visual_follow_post_action_reconcile_started",
        visual_candidate_id=vcid,
        profile_already_open=bool(profile_already_open),
        failure_code=int(follow_out.get("failure_code") or 0),
        follow_out_ok=bool(follow_out.get("ok")),
    )

    events = list(follow_out.get("events") or [])
    fc = int(follow_out.get("failure_code") or 0)
    ok = bool(follow_out.get("ok"))
    _tap_ev_names = frozenset(
        {"follow_tap_sent", "follow_action_exact_follow_tap_sent"}
    )
    had_tap = any(
        isinstance(e, (list, tuple)) and len(e) >= 1 and e[0] in _tap_ev_names
        for e in events
    )
    verify_ev = any(
        isinstance(e, (list, tuple))
        and len(e) >= 1
        and e[0]
        in (
            "follow_verify_success",
            "follow_action_verified",
            "follow_action_exact_follow_verified",
        )
        for e in events
    )
    hints = _post_follow_overlay_ui_hints(d)
    mute_sheet_visible = bool(hints.get("likely_mute_toggle_sheet"))
    mute_started = (
        mute_sheet_visible
        or bool((mute_phase_result or {}).get("mute_started"))
        or bool(follow_out.get("mute_started"))
    )
    mute_success = bool((mute_phase_result or {}).get("ok")) if isinstance(
        mute_phase_result, dict
    ) else False
    if (
        not mute_success
        and isinstance(mute_phase_result, dict)
        and str(mute_phase_result.get("phase") or "") == "mute_toggles"
    ):
        mute_success = bool(mute_phase_result.get("ok"))

    current_follow_state = _poll_follow_state_for_reconcile(d)
    inferred = False
    reason = "no_reconcile_match"

    if ok:
        inferred = True
        reason = "follow_out_already_ok"
    elif verify_ev:
        inferred = True
        reason = "follow_success_events_present"
    elif current_follow_state in ("following", "requested"):
        inferred = True
        reason = "header_state_following_or_requested"
    elif mute_success:
        inferred = True
        reason = "mute_phase_reported_success_implies_follow"
    elif mute_started and had_tap and current_follow_state != "follow":
        inferred = True
        reason = "mute_started_after_follow_tap_non_invite_state"
    elif had_tap and current_follow_state == "following":
        inferred = True
        reason = "tap_sent_and_following_header"

    out = {
        "follow_out_ok": ok,
        "failure_code": fc,
        "current_follow_state": current_follow_state,
        "mute_started": mute_started,
        "mute_success": mute_success,
        "inferred_follow_success": bool(inferred),
        "reason": reason,
        "had_tap_sent": had_tap,
        "verify_success_events": verify_ev,
    }
    log(
        "info",
        "visual_follow_post_action_reconcile_result",
        visual_candidate_id=vcid,
        **out,
    )
    return out


def _post_mute_state_checkpoint(
    d: u2.Device,
    *,
    pkg: str,
    source_profile_username: str,
    visual_candidate_id: str,
) -> dict[str, Any]:
    """
    After mute toggles: dismiss residual sheets and observe state before return CT.
    """
    from navigation_engine import NavigationEngineState, observe_instagram_state

    vcid = str(visual_candidate_id or "").strip()
    src = str(source_profile_username or "").strip()
    pkg = pkg or str(getattr(config, "INSTAGRAM_PACKAGE", "") or "")
    log(
        "info",
        "post_mute_state_checkpoint_started",
        visual_candidate_id=vcid,
        source_profile_username=src,
    )
    closed = 0
    last_err = ""
    for attempt in range(1, 5):
        hints = _post_follow_overlay_ui_hints(d)
        if not hints.get("likely_mute_toggle_sheet"):
            break
        try:
            d.press("back")
            time.sleep(0.38)
            closed += 1
            log(
                "info",
                "post_mute_overlay_closed",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=attempt,
            )
        except Exception as e:
            last_err = str(e)
            log(
                "warning",
                "post_mute_overlay_close_failed",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=attempt,
                error=last_err,
            )
            break
    nav_obs: dict[str, Any] = {}
    try:
        det_ck = detect_followers_list_screen(d, source_profile_username=src)
    except Exception:
        det_ck = {}
    try:
        nav_obs = observe_instagram_state(
            d,
            expected_package=pkg,
            last_known_state=NavigationEngineState.CANDIDATE_PROFILE.value,
            context={
                "phase": "post_mute_checkpoint",
                "visual_candidate_id": vcid,
                "source_profile_username": src,
                "det": det_ck,
                "disable_followers_visual_fallback": True,
                "expected_state": "CANDIDATE_PROFILE",
            },
        )
    except Exception as e:
        nav_obs = {"state": "UNKNOWN", "confidence": 0.0, "reason": str(e)}
    still_sheet = bool(_post_follow_overlay_ui_hints(d).get("likely_mute_toggle_sheet"))
    log(
        "info",
        "post_mute_state_checkpoint_result",
        visual_candidate_id=vcid,
        source_profile_username=src,
        overlay_back_presses=closed,
        navigation_state=str(nav_obs.get("state") or ""),
        navigation_confidence=float(nav_obs.get("confidence") or 0.0),
        mute_sheet_still_visible=still_sheet,
    )
    return {
        "ok": not still_sheet,
        "overlay_presses": closed,
        "navigation_observed": nav_obs,
        "mute_sheet_still_visible": still_sheet,
    }


# --- Post-follow CT return: drift-safe recovery (no media taps, no exploratory swipes) ---

_POST_FOLLOW_DRIFT_GUESS_SUBSTR: tuple[str, ...] = (
    "story",
    "stories",
    "reel",
    "comment",
    "composer",
    "message",
    "direct",
    "inbox",
    "post_view",
    "media",
    "viewer",
    "permalink",
    "share",
    "dm",
    "thread",
    "reply",
    "feed",
    "explore",
)
_POST_FOLLOW_DRIFT_TEXT_SUBSTR: tuple[str, ...] = (
    "send message",
    "send a message",
    "message…",
    "write a message",
    "add a comment",
    "post a comment",
    "view comments",
    "comment as",
    "your story",
    "liked a story",
    "reacted to",
    "new direct message",
    "direct message",
    "write a comment",
    "reply to",
    "story reactions",
)


def _post_follow_return_ct_sample_visible_texts(
    d: u2.Device, *, max_items: int = 28, max_len: int = 72
) -> list[str]:
    out: list[str] = []
    try:
        for el in d(className="android.widget.TextView").all():
            try:
                t = (el.info.get("text") or "").strip()
                if len(t) > 1:
                    out.append(t[:max_len])
                if len(out) >= max_items:
                    break
            except Exception:
                continue
    except Exception:
        return out
    return out


def _post_follow_return_ct_drift_surface_reasons(
    *,
    nav: dict[str, Any],
    last_det: dict[str, Any],
    ok_list_confirmed: bool,
    texts_sample: list[str],
) -> list[str]:
    """Return human-readable drift reasons; empty means no dangerous surface for this guard."""
    from navigation_engine import NavigationEngineState

    if ok_list_confirmed:
        return []

    reasons: list[str] = []
    st = str(nav.get("state") or "")
    cf = float(nav.get("confidence") or 0.0)
    guess_blob = " ".join(
        [
            str(last_det.get("current_screen_guess") or ""),
            str(nav.get("xml_guess") or ""),
        ]
    ).lower()
    texts_blob = " ".join(texts_sample).lower()

    if st == NavigationEngineState.SEARCH.value:
        reasons.append("navigation_search")
    if st == NavigationEngineState.SEARCH_RESULTS.value:
        reasons.append("navigation_search_results")
    if st == NavigationEngineState.UNKNOWN.value and cf < 0.35:
        reasons.append("navigation_unknown_low_confidence")

    for frag in _POST_FOLLOW_DRIFT_GUESS_SUBSTR:
        if frag in guess_blob:
            reasons.append(f"current_screen_guess_or_xml:{frag}")
            break
    for frag in _POST_FOLLOW_DRIFT_TEXT_SUBSTR:
        if frag in texts_blob:
            reasons.append(f"visible_text:{frag}")
            break

    return reasons


def post_follow_controlled_return_to_followers_list(
    d: u2.Device,
    *,
    pkg: str,
    source_profile_username: str,
    follower_username: str,
    visual_candidate_id: str,
    det: dict[str, Any] | None,
    max_rounds: int = 4,
    compact_after_follow_verified_mute: bool = False,
    compact_reason: str | None = None,
) -> tuple[bool, str, str | None]:
    """
    Return to the CT followers list after follow / mute.

    Safety: no taps on feed/media/stories, comment threads, or message composers; no swipes
    or exploratory navigation while recovery is uncertain. Drift surfaces get at most one
    controlled ``back`` plus re-observe before abort.

    When ``compact_after_follow_verified_mute`` is True (follow vérifié + candidat visuel),
    the return uses a **strict** path: observe → liste CT confirmée, sinon **un** ``back``
    sûr → ré-observation → liste ou abort compact (pas de ``return_to_followers_list`` multi
    backs, pas de hierarchy refresh / reopen profile). Un profil PROFILE « étranger »
    (handle barre ≠ source) déclenche toujours au plus un ``back`` puis
    ``fast_abort_foreign_profile`` si la liste CT n’est pas confirmée.
    """
    from navigation_engine import NavigationEngineState, observe_instagram_state

    pkg = pkg or str(getattr(config, "INSTAGRAM_PACKAGE", "") or "")
    vcid = str(visual_candidate_id or "").strip()
    src = str(source_profile_username or "").strip()
    cand = str(follower_username or "").strip()
    compact = bool(compact_after_follow_verified_mute)
    eff_max_rounds = 1 if compact else max(1, int(max_rounds))
    drift_max = int(
        getattr(config, "POST_FOLLOW_RETURN_CT_DRIFT_ABORT_STREAK", 4) or 4
    )
    if compact:
        drift_max = 1
    drift_streak = 0
    budget_s = float(
        getattr(config, "POST_FOLLOW_RETURN_CT_ROUND_BUDGET_S", 10.0) or 10.0
    )
    budget_s = max(8.0, min(12.0, budget_s))
    if compact:
        budget_s = min(budget_s, 5.0)
    allow_reopen = bool(
        getattr(config, "POST_FOLLOW_RETURN_CT_ALLOW_HIERARCHY_PROFILE_REOPEN", False)
    )
    if compact:
        allow_reopen = False
    back_max = max(
        0,
        int(getattr(config, "POST_FOLLOW_RETURN_CT_BACK_MAX_RETRIES", 1) or 1),
    )

    def _list_confirmed() -> tuple[bool, dict[str, Any]]:
        try:
            det_l = detect_followers_list_screen(
                d, source_profile_username=src
            )
        except Exception:
            det_l = {}
        is_list = bool(det_l.get("is_followers_list"))
        try:
            ct_ok = verify_followers_list_surface_is_ct_account(
                d,
                source_profile_username=src,
                follower_candidate_username=cand or None,
            )
        except Exception:
            ct_ok = is_list
        return bool(is_list and ct_ok), det_l

    def _over_budget(round_t0: float) -> bool:
        return (time.monotonic() - round_t0) >= budget_s

    def _landing_ok(nav2: dict[str, Any]) -> bool:
        ok_l, _d = _list_confirmed()
        if ok_l:
            return True
        st2 = str(nav2.get("state") or "")
        if st2 in (
            NavigationEngineState.CANDIDATE_PROFILE.value,
            NavigationEngineState.PRIVATE_PROFILE.value,
            NavigationEngineState.MUTE_SHEET.value,
            NavigationEngineState.FOLLOWERS_LIST.value,
        ):
            return True
        if st2 == NavigationEngineState.PROFILE.value:
            try:
                return bool(verify_profile(d, src))
            except Exception:
                return False
        return False

    ok0, det0 = _list_confirmed()
    if ok0:
        log(
            "info",
            "post_follow_return_ct_visual_confirmed",
            visual_candidate_id=vcid,
            source_profile_username=src,
            attempt=0,
            note="already_on_confirmed_followers_list",
            action_bar_title=str(det0.get("action_bar_title") or "")[:120],
        )
        return True, "already_on_followers_list", None

    last_det: dict[str, Any] = dict(det) if isinstance(det, dict) else {}
    how_last = ""

    if compact:
        req_mr = max(1, int(max_rounds))
        if req_mr > 1 or eff_max_rounds != 1:
            log(
                "warning",
                "post_follow_return_ct_compact_contract_violation",
                visual_candidate_id=vcid,
                source_profile_username=src,
                requested_max_rounds=req_mr,
                effective_max_rounds=int(eff_max_rounds),
                compact_reason=str(compact_reason or ""),
            )
            return (
                False,
                "compact_abort_contract_violation",
                "post_follow_return_ct_compact_contract_violation",
            )

        round_t0 = time.monotonic()
        log(
            "info",
            "post_follow_return_ct_compact_mode_enabled",
            visual_candidate_id=vcid,
            source_profile_username=src,
            compact_reason=str(compact_reason or ""),
            round_budget_s=round(budget_s, 2),
            effective_max_rounds=1,
        )
        round_idx = 1
        log(
            "info",
            "post_follow_return_ct_attempt",
            visual_candidate_id=vcid,
            source_profile_username=src,
            attempt=round_idx,
            max_rounds=1,
            round_budget_s=round(budget_s, 2),
            compact_after_follow_verified_mute=True,
        )
        try:
            last_det = detect_followers_list_screen(d, source_profile_username=src)
        except Exception:
            last_det = {}
        nav_ctx: dict[str, Any] = {
            "phase": "post_follow_return_compact",
            "visual_candidate_id": vcid,
            "source_profile_username": src,
            "det": last_det,
            "disable_followers_visual_fallback": False,
        }
        try:
            nav = observe_instagram_state(
                d,
                expected_package=pkg,
                last_known_state=NavigationEngineState.CANDIDATE_PROFILE.value,
                context=nav_ctx,
            )
        except Exception as e:
            nav = {"state": "UNKNOWN", "confidence": 0.0, "reason": str(e)}
        log(
            "info",
            "post_follow_return_ct_state_observed",
            visual_candidate_id=vcid,
            source_profile_username=src,
            attempt=round_idx,
            observe_sequence="initial",
            navigation_state=str(nav.get("state") or ""),
            navigation_confidence=float(nav.get("confidence") or 0.0),
            navigation_reason=str(nav.get("reason") or ""),
            xml_guess=str(nav.get("xml_guess") or ""),
        )

        ok_list_now, det_now = _list_confirmed()
        if ok_list_now:
            log(
                "info",
                "post_follow_return_ct_visual_confirmed",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=round_idx,
                how="compact_initial_list_confirmed",
                action_bar_title=str(det_now.get("action_bar_title") or "")[:120],
            )
            return True, "compact_initial_list_confirmed", None

        st_nav = str(nav.get("state") or "")
        cf_nav = float(nav.get("confidence") or 0.0)
        ab_raw = str(last_det.get("action_bar_title") or "").strip()
        ab_n = _normalize_handle(ab_raw) if ab_raw else ""
        src_n = _normalize_handle(src)
        foreign_profile = (
            st_nav == NavigationEngineState.PROFILE.value
            and bool(ab_n)
            and ab_n != src_n
            and not ok_list_now
        )
        if foreign_profile:
            log(
                "warning",
                "post_follow_return_ct_profile_not_source_detected",
                visual_candidate_id=vcid,
                source_profile_username=src,
                action_bar_title=ab_raw[:120],
                navigation_state=st_nav,
                navigation_confidence=cf_nav,
                xml_guess=str(nav.get("xml_guess") or ""),
            )
            if verify_app_foreground(d, pkg):
                try:
                    d.press("back")
                    time.sleep(0.42)
                except Exception:
                    pass
                try:
                    last_det = detect_followers_list_screen(
                        d, source_profile_username=src
                    )
                except Exception:
                    last_det = {}
                ok_fb, det_fb = _list_confirmed()
                if ok_fb:
                    log(
                        "info",
                        "post_follow_return_ct_visual_confirmed",
                        visual_candidate_id=vcid,
                        source_profile_username=src,
                        attempt=round_idx,
                        how="foreign_profile_one_safe_back_then_list",
                        action_bar_title=str(
                            det_fb.get("action_bar_title") or ""
                        )[:120],
                    )
                    return True, "foreign_profile_one_safe_back_then_list", None
            log(
                "warning",
                "post_follow_return_ct_fast_abort_after_foreign_profile",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=round_idx,
                action_bar_title=ab_raw[:120],
                navigation_state=st_nav,
                xml_guess=str(nav.get("xml_guess") or ""),
            )
            return (
                False,
                "fast_abort_foreign_profile_after_post_follow",
                "post_follow_return_ct_fast_abort_after_foreign_profile",
            )

        if _over_budget(round_t0):
            log(
                "warning",
                "post_follow_return_ct_compact_abort_no_list_confirmed",
                visual_candidate_id=vcid,
                source_profile_username=src,
                phase="before_compact_safe_back",
                budget_s=budget_s,
            )
            return (
                False,
                "compact_abort_no_list_confirmed",
                "post_follow_return_ct_compact_abort_no_list_confirmed",
            )

        log(
            "info",
            "post_follow_return_ct_compact_safe_back_only",
            visual_candidate_id=vcid,
            source_profile_username=src,
            attempt=round_idx,
        )
        if not verify_app_foreground(d, pkg):
            log(
                "warning",
                "post_follow_return_ct_compact_abort_no_list_confirmed",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=round_idx,
                skip_reason="not_instagram_foreground_safe",
            )
            return (
                False,
                "compact_abort_no_list_confirmed",
                "post_follow_return_ct_compact_abort_no_list_confirmed",
            )
        try:
            d.press("back")
            time.sleep(0.38)
        except Exception as e:
            log(
                "warning",
                "post_follow_return_ct_compact_abort_no_list_confirmed",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=round_idx,
                back_error=str(e),
            )
            return (
                False,
                "compact_abort_no_list_confirmed",
                "post_follow_return_ct_compact_abort_no_list_confirmed",
            )

        try:
            last_det = detect_followers_list_screen(d, source_profile_username=src)
        except Exception:
            last_det = {}
        nav_ctx2 = dict(nav_ctx)
        nav_ctx2["det"] = last_det
        try:
            nav2 = observe_instagram_state(
                d,
                expected_package=pkg,
                last_known_state=NavigationEngineState.CANDIDATE_PROFILE.value,
                context=nav_ctx2,
            )
        except Exception as e2:
            nav2 = {"state": "UNKNOWN", "confidence": 0.0, "reason": str(e2)}
        log(
            "info",
            "post_follow_return_ct_state_observed",
            visual_candidate_id=vcid,
            source_profile_username=src,
            attempt=round_idx,
            observe_sequence="after_safe_back",
            navigation_state=str(nav2.get("state") or ""),
            navigation_confidence=float(nav2.get("confidence") or 0.0),
            navigation_reason=str(nav2.get("reason") or ""),
            xml_guess=str(nav2.get("xml_guess") or ""),
        )

        ok_after, det_after = _list_confirmed()
        if ok_after:
            log(
                "info",
                "post_follow_return_ct_visual_confirmed",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=round_idx,
                how="compact_safe_back_then_list",
                action_bar_title=str(det_after.get("action_bar_title") or "")[:120],
            )
            return True, "compact_safe_back_then_list", None

        log(
            "warning",
            "post_follow_return_ct_compact_abort_no_list_confirmed",
            visual_candidate_id=vcid,
            source_profile_username=src,
            attempt=round_idx,
            navigation_state_after=str(nav2.get("state") or ""),
        )
        return (
            False,
            "compact_abort_no_list_confirmed",
            "post_follow_return_ct_compact_abort_no_list_confirmed",
        )

    for round_idx in range(1, eff_max_rounds + 1):
        round_t0 = time.monotonic()
        log(
            "info",
            "post_follow_return_ct_attempt",
            visual_candidate_id=vcid,
            source_profile_username=src,
            attempt=round_idx,
            max_rounds=int(eff_max_rounds),
            round_budget_s=round(budget_s, 2),
            compact_after_follow_verified_mute=compact,
        )
        try:
            last_det = detect_followers_list_screen(d, source_profile_username=src)
        except Exception:
            last_det = {}

        nav_ctx: dict[str, Any] = {
            "phase": "post_follow_return",
            "visual_candidate_id": vcid,
            "source_profile_username": src,
            "det": last_det,
            "disable_followers_visual_fallback": False,
        }
        try:
            nav = observe_instagram_state(
                d,
                expected_package=pkg,
                last_known_state=NavigationEngineState.CANDIDATE_PROFILE.value,
                context=nav_ctx,
            )
        except Exception as e:
            nav = {"state": "UNKNOWN", "confidence": 0.0, "reason": str(e)}
        log(
            "info",
            "post_follow_return_ct_state_observed",
            visual_candidate_id=vcid,
            source_profile_username=src,
            attempt=round_idx,
            navigation_state=str(nav.get("state") or ""),
            navigation_confidence=float(nav.get("confidence") or 0.0),
            navigation_reason=str(nav.get("reason") or ""),
            xml_guess=str(nav.get("xml_guess") or ""),
        )

        ok_list_now, det_now = _list_confirmed()
        if ok_list_now:
            drift_streak = 0
            log(
                "info",
                "post_follow_return_ct_visual_confirmed",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=round_idx,
                how="confirmed_list_after_observe",
                action_bar_title=str(det_now.get("action_bar_title") or "")[:120],
            )
            return True, "confirmed_list_after_observe", None

        st_nav = str(nav.get("state") or "")
        cf_nav = float(nav.get("confidence") or 0.0)

        vis_sample = _post_follow_return_ct_sample_visible_texts(d)
        drift_reasons = _post_follow_return_ct_drift_surface_reasons(
            nav=nav,
            last_det=last_det,
            ok_list_confirmed=False,
            texts_sample=vis_sample,
        )

        if drift_reasons:
            log(
                "warning",
                "post_follow_return_ct_drift_surface_detected",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=round_idx,
                navigation_state=st_nav,
                navigation_confidence=cf_nav,
                xml_guess=str(nav.get("xml_guess") or ""),
                current_screen_guess=str(last_det.get("current_screen_guess") or ""),
                action_bar_title=str(last_det.get("action_bar_title") or "")[:120],
                visible_texts_sample=vis_sample[:16],
                drift_reasons=drift_reasons,
            )
            allow_back = verify_app_foreground(d, pkg)
            if allow_back:
                log(
                    "info",
                    "post_follow_return_ct_safe_back_started",
                    visual_candidate_id=vcid,
                    source_profile_username=src,
                    attempt=round_idx,
                    max_presses=1,
                )
                back_ok = False
                back_err = ""
                try:
                    d.press("back")
                    time.sleep(0.42)
                    back_ok = True
                except Exception as e:
                    back_err = str(e)
                try:
                    last_det = detect_followers_list_screen(
                        d, source_profile_username=src
                    )
                except Exception:
                    last_det = {}
                nav_ctx2 = dict(nav_ctx)
                nav_ctx2["det"] = last_det
                try:
                    nav2 = observe_instagram_state(
                        d,
                        expected_package=pkg,
                        last_known_state=NavigationEngineState.CANDIDATE_PROFILE.value,
                        context=nav_ctx2,
                    )
                except Exception as e2:
                    nav2 = {"state": "UNKNOWN", "confidence": 0.0, "reason": str(e2)}
                ok_after, det_after = _list_confirmed()
                landed = ok_after or _landing_ok(nav2)
                log(
                    "info",
                    "post_follow_return_ct_safe_back_result",
                    visual_candidate_id=vcid,
                    source_profile_username=src,
                    attempt=round_idx,
                    back_sent=bool(back_ok),
                    back_error=back_err or None,
                    list_confirmed_after=bool(ok_after),
                    navigation_state_after=str(nav2.get("state") or ""),
                    landed_acceptable=bool(landed),
                )
                if ok_after:
                    log(
                        "info",
                        "post_follow_return_ct_visual_confirmed",
                        visual_candidate_id=vcid,
                        source_profile_username=src,
                        attempt=round_idx,
                        how="safe_back_then_list",
                        action_bar_title=str(det_after.get("action_bar_title") or "")[:120],
                    )
                    return True, "safe_back_then_list", None
                if landed:
                    drift_streak = 0
                    continue
            else:
                log(
                    "info",
                    "post_follow_return_ct_safe_back_result",
                    visual_candidate_id=vcid,
                    source_profile_username=src,
                    attempt=round_idx,
                    back_sent=False,
                    skipped_reason="not_instagram_foreground_safe",
                )
            log(
                "warning",
                "post_follow_return_ct_aborted_to_prevent_drift",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=round_idx,
                mode="drift_surface",
                navigation_state=st_nav,
                drift_reasons=drift_reasons,
            )
            return (
                False,
                "aborted_drift_prevention",
                "post_follow_return_ct_aborted_to_prevent_drift",
            )

        bad_drift = (
            (st_nav == NavigationEngineState.UNKNOWN.value and cf_nav < 0.42)
            or st_nav
            in (
                NavigationEngineState.LAUNCHER_WRONG_SURFACE.value,
                NavigationEngineState.INSTAGRAM_CLOSED.value,
                NavigationEngineState.SEARCH.value,
                NavigationEngineState.SEARCH_RESULTS.value,
            )
        )
        if bad_drift:
            drift_streak += 1
        else:
            drift_streak = 0
        if drift_streak >= max(1, drift_max):
            log(
                "warning",
                "post_follow_return_ct_aborted_to_prevent_drift",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=round_idx,
                drift_streak=drift_streak,
                drift_max=drift_max,
                navigation_state=st_nav,
                navigation_confidence=cf_nav,
                mode="streak_guard",
            )
            return (
                False,
                "aborted_drift_prevention",
                "post_follow_return_ct_aborted_to_prevent_drift",
            )

        if _over_budget(round_t0):
            log(
                "warning",
                "post_follow_return_ct_round_budget_exceeded",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=round_idx,
                budget_s=budget_s,
            )
            return (
                False,
                "round_budget_exceeded",
                "post_follow_return_ct_round_budget_exceeded",
            )

        if bool(last_det.get("is_followers_list")):
            try:
                ct_only = verify_followers_list_surface_is_ct_account(
                    d,
                    source_profile_username=src,
                    follower_candidate_username=cand or None,
                )
            except Exception:
                ct_only = False
            if not ct_only:
                log(
                    "info",
                    "post_follow_return_ct_recovery_attempt",
                    visual_candidate_id=vcid,
                    source_profile_username=src,
                    attempt=round_idx,
                    kind="wrong_profile_followers_surface",
                )
            else:
                log(
                    "info",
                    "post_follow_return_ct_visual_confirmed",
                    visual_candidate_id=vcid,
                    source_profile_username=src,
                    attempt=round_idx,
                    how="detect_without_back",
                    action_bar_title=str(last_det.get("action_bar_title") or "")[:120],
                )
                return True, "detect_without_back", None

        if _post_follow_overlay_ui_hints(d).get("likely_mute_toggle_sheet"):
            if _over_budget(round_t0):
                log(
                    "warning",
                    "post_follow_return_ct_round_budget_exceeded",
                    visual_candidate_id=vcid,
                    source_profile_username=src,
                    attempt=round_idx,
                    budget_s=budget_s,
                    phase="before_mute_sheet_dismiss",
                )
                return (
                    False,
                    "round_budget_exceeded",
                    "post_follow_return_ct_round_budget_exceeded",
                )
            try:
                if d(text="Posts").exists(timeout=0.07) and d(text="Stories").exists(
                    timeout=0.05
                ):
                    d.press("back")
                    time.sleep(0.42)
                    log(
                        "info",
                        "post_follow_return_ct_recovery_attempt",
                        visual_candidate_id=vcid,
                        source_profile_username=src,
                        attempt=round_idx,
                        kind="dismiss_mute_sheet_back",
                    )
            except Exception:
                pass

        if _over_budget(round_t0):
            log(
                "warning",
                "post_follow_return_ct_round_budget_exceeded",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=round_idx,
                budget_s=budget_s,
                phase="before_return_to_followers_list",
            )
            return (
                False,
                "round_budget_exceeded",
                "post_follow_return_ct_round_budget_exceeded",
            )

        try:
            ok_r, how_last = return_to_followers_list(
                d,
                src,
                pkg,
                max_retries=back_max,
            )
        except Exception as e:
            ok_r, how_last = False, f"exception:{e}"

        ok1, det1 = _list_confirmed()
        if ok_r and ok1:
            log(
                "info",
                "post_follow_return_ct_visual_confirmed",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=round_idx,
                how=str(how_last or ""),
                action_bar_title=str(det1.get("action_bar_title") or "")[:120],
            )
            return True, str(how_last or "back"), None
        if ok1:
            log(
                "info",
                "post_follow_return_ct_visual_confirmed",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=round_idx,
                how=str(how_last or "ambiguous_nav"),
            )
            return True, str(how_last or "back"), None

        if allow_reopen and not _over_budget(round_t0):
            log(
                "info",
                "post_follow_return_ct_recovery_attempt",
                visual_candidate_id=vcid,
                source_profile_username=src,
                attempt=round_idx,
                kind="hierarchy_refresh_and_reopen_list",
                return_ok=bool(ok_r),
                how=str(how_last or ""),
            )
            try:
                followers_force_hierarchy_refresh(d, src or None)
            except Exception:
                pass
            time.sleep(0.2)
            try:
                if verify_profile(d, src):
                    ok_re, _meta = open_followers_list_from_profile(
                        d, src, pkg, profile_verified=True
                    )
                    if ok_re:
                        ok2, det2 = _list_confirmed()
                        if ok2:
                            log(
                                "info",
                                "post_follow_return_ct_visual_confirmed",
                                visual_candidate_id=vcid,
                                source_profile_username=src,
                                attempt=round_idx,
                                how="reopen_from_source_profile",
                            )
                            return True, "reopen_from_source_profile", None
            except Exception:
                pass

    fail_reason = "post_follow_return_recovery_exhausted"
    try:
        nav_final = observe_instagram_state(
            d,
            expected_package=pkg,
            last_known_state=NavigationEngineState.CANDIDATE_PROFILE.value,
            context={
                "phase": "post_follow_return_classify",
                "visual_candidate_id": vcid,
                "source_profile_username": src,
                "det": last_det,
                "disable_followers_visual_fallback": False,
            },
        )
        st = str(nav_final.get("state") or "")
        cf = float(nav_final.get("confidence") or 0.0)
        if st == NavigationEngineState.UNKNOWN.value and cf < 0.42:
            fail_reason = "post_follow_return_lost_state"
        is_list = bool(last_det.get("is_followers_list"))
        if is_list:
            try:
                ct_bad = not verify_followers_list_surface_is_ct_account(
                    d,
                    source_profile_username=src,
                    follower_candidate_username=cand or None,
                )
            except Exception:
                ct_bad = True
            if ct_bad:
                fail_reason = "post_follow_return_wrong_profile"
        elif st != NavigationEngineState.FOLLOWERS_LIST.value:
            fail_reason = "post_follow_return_followers_not_confirmed"
        if not str(how_last or "").strip() and not is_list:
            fail_reason = "post_follow_return_back_stack_failed"
    except Exception:
        pass

    return False, str(how_last or "failed"), fail_reason


# --- Mute Engine V2: bounded, non-recovery mute after verified follow (best-effort) ---

_MUTE_ENGINE_V2_BUDGET_S = 4.0

_MUTE_V2_REJECT_SUBSTR: tuple[str, ...] = (
    "contact",
    "message",
    "suggested",
    "invite",
    "follow back",
    "add friend",
    "composer",
    "direct",
    "thread",
)

_MUTE_V2_FOLLOWING_STATE_LABELS: tuple[str, ...] = (
    "Following",
    "Requested",
    "Suivi(e)",
    "Suivi",
    "Abonné(e)",
    "Abonné",
    "Siguiendo",
    "Gefolgt",
    "Demandé",
    "Demande envoyée",
)


def _mute_engine_v2_remaining_s(t0: float) -> float:
    return max(0.0, _MUTE_ENGINE_V2_BUDGET_S - (time.perf_counter() - t0))


def _mute_engine_v2_text_rejected(txt: str) -> bool:
    t = (txt or "").strip().lower()
    if not t:
        return True
    for sub in _MUTE_V2_REJECT_SUBSTR:
        if sub in t:
            return True
    if t in ("follow", "suivre", "seguir", "folgen", "abonar", "folgen"):
        return True
    return False


def _mute_engine_v2_following_header_text_ok(txt: str) -> bool:
    """True for profile header states: Following / Requested / localized, not Follow / Contact."""
    t = (txt or "").strip()
    if not t:
        return False
    low = t.lower()
    if "contact" in low or ("message" in low and "requested" not in low):
        return False
    if "follow back" in low or low in ("follow", "follow "):
        return False
    if low.startswith("follow ") and not low.startswith("following"):
        return False
    if _mute_engine_v2_text_rejected(t):
        return False
    for lab in _MUTE_V2_FOLLOWING_STATE_LABELS:
        ll = lab.lower()
        if low == ll or low.startswith(ll) or low.startswith(ll + " "):
            return True
    return False


def _mute_engine_v2_pick_following_cta(
    d: u2.Device,
    *,
    ww: int,
    wh: int,
    t0: float,
    visual_candidate_id: str = "",
    source_profile_username: str = "",
) -> tuple[Any | None, str]:
    rem = _mute_engine_v2_remaining_s(t0)
    vcid = str(visual_candidate_id or "").strip()
    src = str(source_profile_username or "").strip()
    base_log: dict[str, Any] = {
        "visual_candidate_id": vcid,
        "source_profile_username": src,
        "window_w": int(ww),
        "window_h": int(wh),
        "budget_remaining_s": round(float(rem), 4),
    }
    log("info", "mute_engine_v2_following_button_search_started", **base_log)
    ex_to = max(0.05, min(0.14, rem * 0.32))
    y_header_max = int(wh * 0.52)
    y_header_min = int(wh * 0.07)

    def _element_passes(el: Any, *, pick_reason: str) -> tuple[Any | None, str]:
        try:
            raw_txt = str(el.info.get("text") or "").strip()
            cd = str(el.info.get("contentDescription") or "").strip()
            rid = str(el.info.get("resourceName") or el.info.get("resourceId") or "")
            b = el.info.get("bounds") or {}
        except Exception:
            return None, ""
        merged = raw_txt or cd
        rl = rid.lower()
        if "contact" in rl or "message" in rl or "invite" in rl:
            log(
                "info",
                "mute_engine_v2_following_button_rejected",
                **base_log,
                reason="resource_id_blocked",
                candidate_text=merged[:120],
                resource_id=rid[:160],
                pick_reason=pick_reason,
            )
            return None, ""
        if not _mute_engine_v2_following_header_text_ok(merged):
            log(
                "info",
                "mute_engine_v2_following_button_rejected",
                **base_log,
                reason="not_following_state_label",
                candidate_text=merged[:120],
                content_desc=cd[:120],
                pick_reason=pick_reason,
            )
            return None, ""
        try:
            cy = (int(b.get("top", 0)) + int(b.get("bottom", 0))) // 2
            rx = int(b.get("right", 0))
        except Exception:
            return None, ""
        if cy < y_header_min or cy > y_header_max:
            log(
                "info",
                "mute_engine_v2_following_button_rejected",
                **base_log,
                reason="y_outside_header_cta_band",
                cy=int(cy),
                pick_reason=pick_reason,
            )
            return None, ""
        if rx < int(ww * 0.16):
            log(
                "info",
                "mute_engine_v2_following_button_rejected",
                **base_log,
                reason="too_far_left",
                pick_reason=pick_reason,
            )
            return None, ""
        log(
            "info",
            "mute_engine_v2_following_button_candidate",
            **base_log,
            pick_reason=pick_reason,
            candidate_text=merged[:120],
            bounds={
                "left": b.get("left"),
                "top": b.get("top"),
                "right": b.get("right"),
                "bottom": b.get("bottom"),
            },
        )
        return el, pick_reason

    for lab in _MUTE_V2_FOLLOWING_STATE_LABELS:
        if _mute_engine_v2_remaining_s(t0) < 0.05:
            break
        try:
            el = d(text=lab)
            if not el.exists(timeout=ex_to):
                continue
            try:
                raw_txt = str(el.info.get("text") or "").strip()
            except Exception:
                raw_txt = lab
            if raw_txt != lab and _normalize_handle(raw_txt) != _normalize_handle(lab):
                if not _mute_engine_v2_following_header_text_ok(raw_txt):
                    log(
                        "info",
                        "mute_engine_v2_following_button_rejected",
                        **base_log,
                        reason="exact_label_text_mismatch",
                        candidate_text=raw_txt[:120],
                        expected_label=lab,
                        pick_reason=f"text_exact:{lab}",
                    )
                    continue
            if _mute_engine_v2_text_rejected(raw_txt):
                continue
            picked, pr = _element_passes(el, pick_reason=f"text_exact:{lab}")
            if picked is not None:
                return picked, pr
        except Exception:
            continue

    for probe_name, factory in (
        ("textStartsWith_Following", lambda: d(textStartsWith="Following")),
        ("textContains_Following", lambda: d(textContains="Following")),
    ):
        if _mute_engine_v2_remaining_s(t0) < 0.06:
            break
        try:
            sel = factory()
            if not sel.exists(timeout=ex_to):
                continue
            for el in sel.all()[:12]:
                picked, pr = _element_passes(el, pick_reason=probe_name)
                if picked is not None:
                    return picked, pr
        except Exception:
            continue

    try:
        sel = d(descriptionContains="Following")
        if sel.exists(timeout=max(0.05, ex_to * 0.9)):
            for el in sel.all()[:8]:
                picked, pr = _element_passes(el, pick_reason="descriptionContains:Following")
                if picked is not None:
                    return picked, pr
    except Exception:
        pass

    try:
        for el in d(classNameMatches=r".*(Button|TextView)", clickable=True).all()[:55]:
            try:
                raw_txt = str(el.info.get("text") or "").strip()
                cd = str(el.info.get("contentDescription") or "").strip()
                merged = raw_txt or cd
            except Exception:
                continue
            if not merged or not _mute_engine_v2_following_header_text_ok(merged):
                continue
            picked, pr = _element_passes(el, pick_reason="clickable_header_band_scan")
            if picked is not None:
                return picked, pr
    except Exception:
        pass

    log(
        "warning",
        "mute_engine_v2_following_button_rejected",
        **base_log,
        reason="exhausted_search_no_cta",
    )
    return None, ""


_MUTE_V2_DANGEROUS_SCREEN_GUESS_FRAGMENTS: tuple[str, ...] = (
    "story",
    "reel",
    "comment",
    "composer",
    "direct",
    "feed",
    "explore",
    "inbox",
)


def _mute_engine_v2_build_lightweight_profile_det(
    d: u2.Device,
    *,
    det_hint: dict[str, Any] | None,
    pkg: str,
) -> dict[str, Any]:
    """Minimal ``det`` for mute V2 when follow state + title already prove target profile (skips heavy XML)."""
    out: dict[str, Any] = {
        "is_followers_list": False,
        "strict_list_open": False,
        "relaxed_list_open": False,
        "action_bar_title": "",
        "current_screen_guess": "likely_profile",
        "profile_tabs_absent": False,
        "candidate_username_count": 0,
        "visible_header_texts": [],
        "current_package": None,
        "current_activity": None,
        "current_package_expected": str(pkg or "").strip(),
    }
    hint = det_hint if isinstance(det_hint, dict) else {}
    ab = str(hint.get("action_bar_title") or "").strip()
    try:
        cur = d.app_current() or {}
        out["current_package"] = cur.get("package")
        out["current_activity"] = cur.get("activity")
    except Exception:
        pass
    try:
        ab_el = d(resourceIdMatches=r".*:id/action_bar_title.*")
        if ab_el.exists(timeout=0.11):
            live = str(ab_el.get_text() or "").strip()
            if live:
                ab = live
    except Exception:
        pass
    out["action_bar_title"] = ab
    cg = str(hint.get("current_screen_guess") or "").strip()
    if cg:
        out["current_screen_guess"] = cg
    return out


def _mute_engine_v2_following_label_visible_quick(
    d: u2.Device, *, timeout_s: float
) -> bool:
    wt = max(0.04, min(0.09, float(timeout_s)))
    for lab in _MUTE_V2_FOLLOWING_STATE_LABELS:
        try:
            if d(text=lab).exists(timeout=wt):
                return True
        except Exception:
            continue
    return False


def _mute_engine_v2_keyboard_focus_edit_text(d: u2.Device, *, timeout_s: float) -> bool:
    try:
        wt = max(0.03, min(0.07, float(timeout_s)))
        ed = d(className="android.widget.EditText", focused=True)
        return bool(ed.exists(timeout=wt))
    except Exception:
        return False


def _mute_engine_v2_truly_dangerous_surface(
    d: u2.Device,
    *,
    pkg: str,
    nav: dict[str, Any],
    det: dict[str, Any],
    fp: dict[str, Any] | None,
    overlay: dict[str, Any],
    remaining_s: float,
) -> tuple[bool, str]:
    """
    Surfaces that must block mute regardless of inline "Suggested for you" on profile.
    """
    from navigation_engine import NavigationEngineState

    if bool(overlay.get("likely_mute_toggle_sheet")):
        return False, ""

    rem = max(0.06, float(remaining_s))
    st = str(nav.get("state") or "")

    exp = str(pkg or "").strip()
    try:
        cur = d.app_current() or {}
        fg = str(cur.get("package") or "")
    except Exception:
        fg = ""
    if exp and fg and fg != exp:
        return True, "wrong_foreground_package"

    if st in (
        NavigationEngineState.LAUNCHER_WRONG_SURFACE.value,
        NavigationEngineState.INSTAGRAM_CLOSED.value,
    ):
        return True, f"bad_nav_state:{st}"

    guess = str(det.get("current_screen_guess") or "").lower()
    if guess:
        for frag in _MUTE_V2_DANGEROUS_SCREEN_GUESS_FRAGMENTS:
            if frag in guess:
                return True, f"screen_guess_drift:{frag}"

    if _mute_engine_v2_keyboard_focus_edit_text(d, timeout_s=min(0.07, rem * 0.12)):
        return True, "keyboard_focus_edittext"

    sc = str((fp or {}).get("screen_class") or "").strip().lower()
    if sc in ("search_like", "followers_list", "followers_list_strong"):
        return True, f"fingerprint_screen_class:{sc}"

    return False, ""


def _mute_engine_v2_resolve_effective_candidate_username(
    *,
    follower_username: str,
    action_bar_title: str,
    source_profile_username: str,
    det_hint: dict[str, Any] | None,
    visual_candidate_id: str,
) -> tuple[str, str]:
    """
    Handle for the opened profile row. ``follower_username`` may be empty in visual mode;
    fall back to action bar / hint / id-shaped handles.
    """
    fu = str(follower_username or "").strip()
    if fu:
        return fu, "follower_username"
    ab = str(action_bar_title or "").strip()
    src = str(source_profile_username or "").strip()
    if ab and src and _normalize_handle(ab) != _normalize_handle(src):
        return ab, "action_bar_title_fallback"
    if ab and not src:
        return ab, "action_bar_title_fallback"
    hint = det_hint if isinstance(det_hint, dict) else {}
    for key in (
        "follower_username",
        "candidate_username",
        "visual_candidate_username",
        "target_username",
        "opened_profile_username",
        "profile_username",
    ):
        v = str(hint.get(key) or "").strip()
        if v and (not src or _normalize_handle(v) != _normalize_handle(src)):
            return v, f"det_hint:{key}"
    vcid = str(visual_candidate_id or "").strip()
    if vcid and re.match(r"^[A-Za-z0-9._]{1,30}$", vcid):
        if not src or _normalize_handle(vcid) != _normalize_handle(src):
            return vcid, "visual_candidate_id"
    return "", "unresolved"


def _mute_engine_v2_surface_unstable(
    d: u2.Device,
    *,
    nav: dict[str, Any],
    overlay: dict[str, Any],
    det: dict[str, Any],
    fp: dict[str, Any] | None,
    follower_username: str,
    follow_state_after: str,
    pkg: str,
    budget_remaining_s: float,
    visual_candidate_id: str = "",
    source_profile_username: str = "",
    det_hint: dict[str, Any] | None = None,
    follow_header_snapshot: str = "",
) -> tuple[bool, str]:
    from navigation_engine import NavigationEngineState

    st = str(nav.get("state") or "")
    allowed = frozenset(
        {
            NavigationEngineState.PROFILE.value,
            NavigationEngineState.CANDIDATE_PROFILE.value,
            NavigationEngineState.PRIVATE_PROFILE.value,
        }
    )
    if st not in allowed:
        return True, f"navigation_not_profile:{st}"
    ab = str(det.get("action_bar_title") or "").strip()
    if not ab:
        return True, "empty_action_bar_title"

    rem_b = max(0.08, float(budget_remaining_s))
    dangerous, dwhy = _mute_engine_v2_truly_dangerous_surface(
        d,
        pkg=pkg,
        nav=nav,
        det=det,
        fp=fp,
        overlay=overlay,
        remaining_s=rem_b,
    )

    ov_sug = bool(overlay.get("suggested_for_you"))
    ov_dis = bool(overlay.get("discover_people"))
    if ov_sug or ov_dis:
        ovt = (
            "both"
            if (ov_sug and ov_dis)
            else ("suggested_for_you" if ov_sug else "discover_people")
        )
        fu_raw = str(follower_username or "").strip()
        src_stripped = str(source_profile_username or "").strip()
        eff, res_src = _mute_engine_v2_resolve_effective_candidate_username(
            follower_username=fu_raw,
            action_bar_title=ab,
            source_profile_username=src_stripped,
            det_hint=det_hint,
            visual_candidate_id=str(visual_candidate_id or ""),
        )
        log(
            "info",
            "mute_engine_v2_effective_candidate_handle_resolved",
            follower_username=fu_raw,
            action_bar_title=ab[:120],
            source_profile_username=src_stripped[:120],
            effective_candidate_username=str(eff or "")[:120],
            resolution_source=res_src,
            visual_candidate_id=str(visual_candidate_id or ""),
        )

        fs_low = str(follow_state_after or "").strip().lower()
        fhs = str(follow_header_snapshot or "").strip().lower()
        fs_from_state = fs_low in ("following", "requested")
        fs_from_header = fhs in ("following", "requested")
        fs_vis = (
            fs_from_state
            or fs_from_header
            or _mute_engine_v2_following_label_visible_quick(
                d, timeout_s=min(0.11, rem_b * 0.28)
            )
        )

        src_norm = _normalize_handle(src_stripped) if src_stripped else ""
        ab_norm = _normalize_handle(ab)
        on_candidate_not_owner = bool(ab) and (not src_norm or ab_norm != src_norm)

        title_ok_explicit = bool(fu_raw) and bool(ab) and (
            _normalize_handle(fu_raw) == ab_norm
        )
        profile_overlay_safe = (
            st in allowed
            and not dangerous
            and fs_vis
            and on_candidate_not_owner
            and (not bool(fu_raw) or title_ok_explicit)
        )

        gate_title_source = ""
        if profile_overlay_safe:
            if bool(fu_raw) and title_ok_explicit:
                gate_title_source = "follower_username_match"
            else:
                gate_title_source = "action_bar_title_fallback"

        base_common: dict[str, Any] = {
            "overlay_type": ovt,
            "action_bar_title": ab[:120],
            "navigation_state": st,
            "following_visible": bool(fs_vis),
            "dangerous_surface": bool(dangerous),
            "dangerous_reason": dwhy or "",
            "visual_candidate_id": str(visual_candidate_id or ""),
            "source_profile_username": src_stripped[:120],
            "follower_username": fu_raw,
            "effective_candidate_username": str(eff or "")[:120],
            "effective_resolution_source": res_src,
        }
        log("info", "mute_engine_v2_overlay_surface_detected", **base_common)

        if dangerous:
            log(
                "warning",
                "mute_engine_v2_overlay_surface_rejected",
                **base_common,
                allow_mute=False,
                safe_profile_overlay_surface=False,
                rejection_reason="dangerous_context",
            )
            return True, dwhy if dwhy else "dangerous_context"

        if profile_overlay_safe:
            log(
                "info",
                "mute_engine_v2_overlay_surface_allowed",
                **base_common,
                allow_mute=True,
                safe_profile_overlay_surface=True,
                profile_gate_title_ok=True,
                profile_gate_title_source=gate_title_source,
            )
        else:
            log(
                "warning",
                "mute_engine_v2_overlay_surface_rejected",
                **base_common,
                allow_mute=False,
                safe_profile_overlay_surface=False,
                rejection_reason="overlay_suggestion_profile_gate_failed",
                profile_gate_title_ok=bool(title_ok_explicit),
                profile_gate_following_visible=bool(fs_vis),
                profile_gate_on_candidate_not_owner=bool(on_candidate_not_owner),
                profile_gate_follow_header_snapshot=fhs[:32],
            )
            return True, "overlay_suggestion_surface"
    elif dangerous:
        return True, dwhy if dwhy else "dangerous_context"

    if overlay.get("notification_prompt") or overlay.get("turn_on_notifications"):
        return True, "overlay_system_prompt"
    if overlay.get("likely_mute_toggle_sheet"):
        try:
            p0 = bool(d(text="Posts").exists(timeout=0.04)) or bool(
                d(text="Publications").exists(timeout=0.04)
            )
            s0 = bool(d(text="Stories").exists(timeout=0.04))
        except Exception:
            p0 = s0 = False
        if not (p0 and s0):
            return True, "mute_sheet_partial_or_unknown"

    return False, ""


def _mute_engine_v2_tap_toggle_short(
    d: u2.Device, labels: tuple[str, ...], ww: int, t0: float
) -> tuple[bool, bool, str]:
    """Returns (tapped_or_already_on, is_already_on, reason)."""
    rem = _mute_engine_v2_remaining_s(t0)
    if rem < 0.08:
        return False, False, "budget"
    wt = max(0.05, min(0.22, rem * 0.35))
    el: Any | None = None
    for lab in labels:
        try:
            cand = d(text=lab)
            if cand.wait(timeout=wt):
                el = cand
                break
        except Exception:
            continue
    if el is None:
        return False, False, "toggle_label_not_found"
    chk = _visual_switch_checked_near_row(d, el)
    if chk is True:
        return True, True, ""
    try:
        b = el.info.get("bounds") or {}
        cy = (int(b["top"]) + int(b["bottom"])) // 2
        tap_x = min(ww - 6, max(int(ww * 0.88), int(b.get("right", 0)) + 72))
        d.click(int(tap_x), int(cy))
    except Exception as e:
        return False, False, f"tap_failed:{e}"
    return True, False, ""


def run_mute_engine_v2(
    d: u2.Device,
    *,
    pkg: str,
    source_profile_username: str,
    visual_candidate_id: str,
    follower_username: str,
    follow_state_after: str,
    det_hint: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Short, non-exploratory mute after verified follow. No return_to_followers_list,
    no hierarchy refresh, no profile reopen, no scroll/swipe recovery.
    """
    from navigation_engine import NavigationEngineState, observe_instagram_state

    t_all = time.perf_counter()
    timings: dict[str, float] = {}
    vcid = str(visual_candidate_id or "").strip()
    src = str(source_profile_username or "").strip()
    pkg = str(pkg or getattr(config, "INSTAGRAM_PACKAGE", "") or "")
    fs_after = str(follow_state_after or "").strip()
    raw_inv = False
    try:
        raw_inv = bool(_visual_raw_follow_invite_visible_quick(d))
    except Exception:
        raw_inv = False

    log(
        "info",
        "mute_engine_v2_started",
        visual_candidate_id=vcid,
        source_profile_username=src,
        follower_username=str(follower_username or "").strip(),
        follow_state_after=fs_after,
        budget_s=_MUTE_ENGINE_V2_BUDGET_S,
    )

    def _abort(reason: str, *, fr: str | None = None) -> dict[str, Any]:
        timings["mute_total_ms"] = round((time.perf_counter() - t_all) * 1000, 2)
        log(
            "warning",
            "mute_engine_v2_safe_abort",
            visual_candidate_id=vcid,
            source_profile_username=src,
            reason=reason,
            failure_reason=fr or reason,
            timings_ms=dict(timings),
        )
        return {
            "ok": False,
            "partial": False,
            "skipped": False,
            "failure_reason": fr or reason,
            "outcome": "safe_abort",
            "abort_reason": reason,
            "mute_engine_v2": True,
            "timings_ms": dict(timings),
        }

    if _mute_engine_v2_remaining_s(t_all) < 0.25:
        return _abort("mute_budget_exceeded", fr="mute_budget_exceeded")

    t_obs = time.perf_counter()
    light_profile_det = False
    det: dict[str, Any] = {}
    fu2 = str(follower_username or "").strip()
    fs_low = str(follow_state_after or "").strip().lower()
    if fs_low in ("following", "requested") and fu2:
        try:
            det_try = _mute_engine_v2_build_lightweight_profile_det(
                d, det_hint=det_hint, pkg=pkg
            )
            ab_try = str(det_try.get("action_bar_title") or "").strip()
            if ab_try and _normalize_handle(ab_try) == _normalize_handle(fu2):
                det = det_try
                light_profile_det = True
        except Exception:
            pass
    if not light_profile_det:
        try:
            det = detect_followers_list_screen(d, source_profile_username=src)
        except Exception:
            det = {}
    timings["mute_engine_v2_screen_det_ms"] = round(
        (time.perf_counter() - t_obs) * 1000, 2
    )
    if isinstance(det_hint, dict) and det_hint:
        if not str(det.get("action_bar_title") or "").strip():
            abh = str(det_hint.get("action_bar_title") or "").strip()
            if abh:
                det = {**det, "action_bar_title": abh}
        if not str(det.get("current_screen_guess") or "").strip():
            cg = str(det_hint.get("current_screen_guess") or "").strip()
            if cg:
                det = {**det, "current_screen_guess": cg}

    nav_ctx: dict[str, Any] = {
        "phase": "mute_engine_v2",
        "visual_candidate_id": vcid,
        "source_profile_username": src,
        "det": det,
        "disable_followers_visual_fallback": True,
    }
    t_nav = time.perf_counter()
    try:
        nav = observe_instagram_state(
            d,
            expected_package=pkg,
            last_known_state=NavigationEngineState.CANDIDATE_PROFILE.value,
            context=nav_ctx,
        )
    except Exception as e:
        nav = {"state": "UNKNOWN", "confidence": 0.0, "reason": str(e)}
    timings["observe_nav_ms"] = round((time.perf_counter() - t_nav) * 1000, 2)

    t_ov = time.perf_counter()
    overlay = _post_follow_overlay_ui_hints(d)
    fp = _post_follow_screen_fingerprint(
        d, nav_state=str(nav.get("state") or ""), det=det
    )
    timings["mute_engine_v2_overlay_fp_ms"] = round(
        (time.perf_counter() - t_ov) * 1000, 2
    )
    fhs = ""
    try:
        fhs = str(_follow_ui_state_snapshot(d) or "").strip()
    except Exception:
        fhs = ""
    ui_snapshot = {
        "action_bar_title": str(det.get("action_bar_title") or "")[:120],
        "visible_header_texts": list((det.get("visible_header_texts") or [])[:14]),
        "raw_follow_invite_visible": raw_inv,
        "follow_header_snapshot": fhs,
        "fingerprint_id": fp.get("fingerprint_id"),
        "screen_class": fp.get("screen_class"),
        "light_profile_det": light_profile_det,
    }
    log(
        "info",
        "mute_engine_v2_state_observed",
        visual_candidate_id=vcid,
        source_profile_username=src,
        navigation_state=str(nav.get("state") or ""),
        navigation_confidence=float(nav.get("confidence") or 0.0),
        xml_guess=str(nav.get("xml_guess") or det.get("current_screen_guess") or ""),
        **ui_snapshot,
    )
    timings["observe_state_ms"] = round((time.perf_counter() - t_obs) * 1000, 2)

    if not _vision_validation_mute_engine_surface(
        screenshot_path="",
        det=det,
        nav=nav,
        overlay=overlay,
        fp=fp if isinstance(fp, dict) else {},
        follow_header_snapshot=fhs,
        follow_state_after=fs_after,
        visual_candidate_id=vcid,
        source_profile_username=src,
    ):
        return _abort(
            "vision_validation_mute_surface_rejected",
            fr="vision_validation_mute_surface_rejected",
        )

    bad, why = _mute_engine_v2_surface_unstable(
        d,
        nav=nav,
        overlay=overlay,
        det=det,
        fp=fp if isinstance(fp, dict) else None,
        follower_username=follower_username,
        follow_state_after=follow_state_after,
        pkg=pkg,
        budget_remaining_s=_mute_engine_v2_remaining_s(t_all),
        visual_candidate_id=vcid,
        source_profile_username=src,
        det_hint=det_hint if isinstance(det_hint, dict) else None,
        follow_header_snapshot=fhs,
    )
    if bad:
        return _abort("unstable_post_follow_surface", fr=why)

    try:
        ww, wh = d.window_size()
    except Exception:
        ww, wh = 1080, 2400

    skip_following = False
    try:
        if overlay.get("likely_mute_toggle_sheet"):
            p0 = bool(d(text="Posts").exists(timeout=0.06)) or bool(
                d(text="Publications").exists(timeout=0.05)
            )
            s0 = bool(d(text="Stories").exists(timeout=0.06))
            if p0 and s0:
                skip_following = True
                log(
                    "info",
                    "mute_engine_v2_sheet_detected",
                    visual_candidate_id=vcid,
                    source_profile_username=src,
                    phase="precheck",
                    posts_visible=True,
                    stories_visible=True,
                    note="sheet_already_visible_skip_following_tap",
                )
    except Exception:
        pass

    t_follow = time.perf_counter()
    following_method = ""
    if not skip_following:
        btn, following_method = _mute_engine_v2_pick_following_cta(
            d,
            ww=ww,
            wh=wh,
            t0=t_all,
            visual_candidate_id=vcid,
            source_profile_username=src,
        )
        if btn is None:
            log(
                "warning",
                "mute_engine_v2_following_button_missing",
                visual_candidate_id=vcid,
                source_profile_username=src,
                navigation_state=str(nav.get("state") or ""),
                action_bar_title=str(det.get("action_bar_title") or "")[:120],
            )
            return _abort("following_button_not_found", fr="following_button_not_found")
        log(
            "info",
            "mute_engine_v2_following_button_detected",
            visual_candidate_id=vcid,
            source_profile_username=src,
            method=following_method,
        )
        try:
            btn.click()
        except Exception as e:
            return _abort("following_click_failed", fr=str(e))
        time.sleep(min(0.38, max(0.12, _mute_engine_v2_remaining_s(t_all) * 0.25)))

    timings["following_detect_ms"] = round((time.perf_counter() - t_follow) * 1000, 2)

    if _mute_engine_v2_remaining_s(t_all) < 0.2:
        return _abort("mute_budget_exceeded", fr="mute_budget_exceeded")

    t_sheet = time.perf_counter()
    if not skip_following:
        mute_el, mute_lab = _visual_find_mute_row_first_sheet(d)
        if mute_el is None:
            return _abort("mute_row_not_found", fr="mute_row_not_found")
        try:
            mute_el.click()
        except Exception as e:
            return _abort("mute_row_click_failed", fr=str(e))
        time.sleep(min(0.42, max(0.12, _mute_engine_v2_remaining_s(t_all) * 0.28)))

    posts_v = bool(d(text="Posts").exists(timeout=min(0.35, _mute_engine_v2_remaining_s(t_all) * 0.45)))
    if not posts_v:
        posts_v = bool(
            d(text="Publications").exists(
                timeout=min(0.22, max(0.05, _mute_engine_v2_remaining_s(t_all) * 0.35))
            )
        )
    stories_v = bool(
        d(text="Stories").exists(
            timeout=min(0.35, max(0.05, _mute_engine_v2_remaining_s(t_all) * 0.45))
        )
    )
    timings["mute_sheet_open_ms"] = round((time.perf_counter() - t_sheet) * 1000, 2)

    if not posts_v or not stories_v:
        return _abort("posts_or_stories_labels_missing", fr="posts_or_stories_labels_missing")

    log(
        "info",
        "mute_engine_v2_sheet_detected",
        visual_candidate_id=vcid,
        source_profile_username=src,
        posts_visible=posts_v,
        stories_visible=stories_v,
    )

    want_posts = bool(getattr(config, "VISUAL_MUTE_POSTS_AFTER_FOLLOW", True))
    want_stories = bool(getattr(config, "VISUAL_MUTE_STORIES_AFTER_FOLLOW", True))
    posts_labels = ("Posts", "Publications")
    stories_labels = ("Stories", "Historias", "Storie")

    posts_ok = not want_posts
    stories_ok = not want_stories
    posts_tapped = False
    stories_tapped = False

    if want_posts:
        t_tp = time.perf_counter()
        tapped_p, already_p, rsn_p = _mute_engine_v2_tap_toggle_short(
            d, posts_labels, ww, t_all
        )
        posts_tapped = bool(tapped_p and not already_p)
        if already_p:
            posts_ok = True
        elif tapped_p:
            vto = min(0.55, max(0.12, _mute_engine_v2_remaining_s(t_all) * 0.55))
            posts_ok = bool(
                _visual_verify_toggle_on_for_labels(d, posts_labels, timeout_s=vto)
            )
        timings["toggle_posts_ms"] = round((time.perf_counter() - t_tp) * 1000, 2)
        if posts_ok:
            log(
                "info",
                "mute_engine_v2_posts_toggle_detected",
                visual_candidate_id=vcid,
                source_profile_username=src,
                posts_verified=True,
            )

    if want_stories:
        t_ts = time.perf_counter()
        tapped_s, already_s, rsn_s = _mute_engine_v2_tap_toggle_short(
            d, stories_labels, ww, t_all
        )
        stories_tapped = bool(tapped_s and not already_s)
        if already_s:
            stories_ok = True
        elif tapped_s:
            vto = min(0.55, max(0.12, _mute_engine_v2_remaining_s(t_all) * 0.55))
            stories_ok = bool(
                _visual_verify_toggle_on_for_labels(d, stories_labels, timeout_s=vto)
            )
        timings["toggle_stories_ms"] = round((time.perf_counter() - t_ts) * 1000, 2)
        if stories_ok:
            log(
                "info",
                "mute_engine_v2_stories_toggle_detected",
                visual_candidate_id=vcid,
                source_profile_username=src,
                stories_verified=True,
            )

    timings["mute_total_ms"] = round((time.perf_counter() - t_all) * 1000, 2)

    if want_posts and want_stories:
        if posts_ok and stories_ok:
            log(
                "info",
                "mute_engine_v2_success",
                visual_candidate_id=vcid,
                source_profile_username=src,
                timings_ms=dict(timings),
            )
            return {
                "ok": True,
                "partial": False,
                "skipped": False,
                "failure_reason": None,
                "outcome": "success",
                "abort_reason": None,
                "mute_engine_v2": True,
                "posts_verified": True,
                "stories_verified": True,
                "posts_tapped": posts_tapped,
                "stories_tapped": stories_tapped,
                "mute_row_label": "",
                "timings_ms": dict(timings),
            }
        if posts_ok or stories_ok:
            log(
                "warning",
                "mute_engine_v2_partial_success",
                visual_candidate_id=vcid,
                source_profile_username=src,
                posts_verified=bool(posts_ok),
                stories_verified=bool(stories_ok),
                timings_ms=dict(timings),
            )
            return {
                "ok": True,
                "partial": True,
                "skipped": False,
                "failure_reason": "mute_partial_one_toggle",
                "outcome": "partial_success",
                "abort_reason": None,
                "mute_engine_v2": True,
                "posts_verified": bool(posts_ok),
                "stories_verified": bool(stories_ok),
                "posts_tapped": posts_tapped,
                "stories_tapped": stories_tapped,
                "timings_ms": dict(timings),
            }
        log(
            "warning",
            "mute_engine_v2_safe_abort",
            visual_candidate_id=vcid,
            source_profile_username=src,
            reason="toggle_verify_failed",
            failure_reason="toggle_verify_failed",
            posts_verified=False,
            stories_verified=False,
            timings_ms=dict(timings),
        )
        return {
            "ok": False,
            "partial": False,
            "skipped": False,
            "failure_reason": "toggle_verify_failed",
            "outcome": "safe_abort",
            "abort_reason": "toggle_verify_failed",
            "mute_engine_v2": True,
            "timings_ms": dict(timings),
        }

    # only one of want_posts / want_stories
    if (want_posts and posts_ok) or (want_stories and stories_ok):
        log(
            "info",
            "mute_engine_v2_success",
            visual_candidate_id=vcid,
            source_profile_username=src,
            timings_ms=dict(timings),
        )
        return {
            "ok": True,
            "partial": False,
            "skipped": False,
            "failure_reason": None,
            "outcome": "success",
            "abort_reason": None,
            "mute_engine_v2": True,
            "posts_verified": bool(posts_ok),
            "stories_verified": bool(stories_ok),
            "posts_tapped": posts_tapped,
            "stories_tapped": stories_tapped,
            "timings_ms": dict(timings),
        }
    log(
        "warning",
        "mute_engine_v2_safe_abort",
        visual_candidate_id=vcid,
        source_profile_username=src,
        reason="toggle_verify_failed",
        failure_reason="toggle_verify_failed",
        timings_ms=dict(timings),
    )
    return {
        "ok": False,
        "partial": False,
        "skipped": False,
        "failure_reason": "toggle_verify_failed",
        "outcome": "safe_abort",
        "abort_reason": "toggle_verify_failed",
        "mute_engine_v2": True,
        "timings_ms": dict(timings),
    }


def run_visual_candidate_post_follow_phase(
    d: u2.Device,
    *,
    pkg: str,
    source_profile_username: str,
    visual_candidate_id: str,
    follower_username: str,
    follow_success_verified: bool,
    follow_state_after: str,
    skipped_tap: bool,
    det: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Post-follow: observe UI, optional real mute, controlled return to CT followers list.
    Does not modify follow / harvester / row mapping.
    """
    from navigation_engine import NavigationEngineState, observe_instagram_state

    pkg = pkg or str(getattr(config, "INSTAGRAM_PACKAGE", "") or "")
    vcid = str(visual_candidate_id or "").strip()
    src = str(source_profile_username or "").strip()
    cand = str(follower_username or "").strip()
    fs_after = str(follow_state_after or "").strip()

    mute_out: dict[str, Any] = {
        "ok": False,
        "skipped": True,
        "skipped_reason": None,
        "failure_reason": None,
        "mute_started": False,
    }

    log(
        "info",
        "post_follow_flow_started",
        visual_candidate_id=vcid,
        source_profile_username=src,
        follower_username=cand,
        follow_success_verified=bool(follow_success_verified),
        follow_state_after=fs_after,
        skipped_tap=bool(skipped_tap),
    )

    det_use: dict[str, Any] = dict(det) if isinstance(det, dict) else {}
    try:
        det_fresh = detect_followers_list_screen(d, source_profile_username=src)
        if isinstance(det_fresh, dict) and det_fresh:
            det_use = det_fresh
    except Exception:
        pass

    nav_obs: dict[str, Any] = {}
    try:
        nav_obs = observe_instagram_state(
            d,
            expected_package=pkg,
            last_known_state=NavigationEngineState.CANDIDATE_PROFILE.value,
            context={
                "phase": "post_follow_observe",
                "visual_candidate_id": vcid,
                "source_profile_username": src,
                "det": det_use,
                "disable_followers_visual_fallback": True,
                "expected_state": "CANDIDATE_PROFILE",
            },
        )
    except Exception as e:
        nav_obs = {"state": "UNKNOWN", "confidence": 0.0, "reason": str(e)}

    overlay = _post_follow_overlay_ui_hints(d)
    fp = _post_follow_screen_fingerprint(
        d, nav_state=str(nav_obs.get("state") or ""), det=det_use
    )
    obs_reason = "ok"
    if overlay.get("likely_mute_toggle_sheet"):
        obs_reason = "overlay_mute_sheet_like"
    elif any(
        overlay.get(k)
        for k in (
            "suggested_for_you",
            "discover_people",
            "notification_prompt",
            "turn_on_notifications",
        )
    ):
        obs_reason = "overlay_suggestion_or_system_prompt"
    elif str(nav_obs.get("state") or "") == NavigationEngineState.MUTE_SHEET.value:
        obs_reason = "navigation_mute_sheet"

    log(
        "info",
        "post_follow_state_observed",
        visual_candidate_id=vcid,
        source_profile_username=src,
        navigation_state=str(nav_obs.get("state") or ""),
        navigation_confidence=float(nav_obs.get("confidence") or 0.0),
        navigation_reason=str(nav_obs.get("reason") or ""),
        observation_note=obs_reason,
        overlay_hints=overlay,
        fingerprint_id=fp.get("fingerprint_id"),
        screen_class=fp.get("screen_class"),
        action_bar_title=str(det_use.get("action_bar_title") or "")[:120],
    )

    flow_on = bool(getattr(config, "ENABLE_VISUAL_FOLLOW_MUTE_FLOW", False))
    real_mute = bool(getattr(config, "ENABLE_REAL_VISUAL_MUTE_AFTER_FOLLOW", False))
    follow_priv = bool(getattr(config, "FOLLOW_PRIVATE_ACCOUNTS", False))
    pending_rq = fs_after == "requested"

    should_mute = bool(
        follow_success_verified
        and flow_on
        and real_mute
        and not skipped_tap
        and not (pending_rq and follow_priv)
    )
    mute_decision_reason = "mute_eligible"
    if not follow_success_verified:
        mute_decision_reason = "follow_not_verified_in_runner_context"
        should_mute = False
    elif not flow_on or not real_mute:
        mute_decision_reason = "mute_disabled_by_config"
        should_mute = False
    elif skipped_tap:
        mute_decision_reason = "skipped_tap_already_connected"
        should_mute = False
    elif pending_rq and follow_priv:
        mute_decision_reason = "private_follow_request_pending_skip_mute"
        should_mute = False

    log(
        "info",
        "post_follow_mute_decision",
        visual_candidate_id=vcid,
        source_profile_username=src,
        should_mute=should_mute,
        reason=mute_decision_reason,
        ENABLE_VISUAL_FOLLOW_MUTE_FLOW=flow_on,
        ENABLE_REAL_VISUAL_MUTE_AFTER_FOLLOW=real_mute,
    )

    if should_mute:
        if not _vision_validation_post_follow_before_mute(
            screenshot_path="",
            det=det_use,
            nav_obs=nav_obs,
            overlay=overlay,
            fp=fp if isinstance(fp, dict) else {},
            visual_candidate_id=vcid,
            source_profile_username=src,
        ):
            should_mute = False
            mute_decision_reason = "vision_validation_post_follow_blocked"

    if not follow_success_verified:
        log(
            "info",
            "post_follow_mute_skipped",
            visual_candidate_id=vcid,
            source_profile_username=src,
            reason="follow_not_verified",
        )
    elif not flow_on or not real_mute:
        log(
            "info",
            "post_follow_mute_skipped",
            visual_candidate_id=vcid,
            source_profile_username=src,
            reason="mute_disabled_by_config",
        )
    elif skipped_tap:
        log(
            "info",
            "post_follow_mute_skipped",
            visual_candidate_id=vcid,
            source_profile_username=src,
            reason="already_following_skipped_tap",
        )
    elif pending_rq and follow_priv:
        log(
            "info",
            "post_follow_mute_skipped",
            visual_candidate_id=vcid,
            source_profile_username=src,
            reason="private_follow_request_pending",
        )
    elif should_mute:
        mute_out["mute_started"] = True
        log(
            "info",
            "post_follow_mute_started",
            visual_candidate_id=vcid,
            source_profile_username=src,
            engine="mute_engine_v2",
        )
        v2 = run_mute_engine_v2(
            d,
            pkg=pkg,
            source_profile_username=src,
            visual_candidate_id=vcid,
            follower_username=cand,
            follow_state_after=fs_after,
            det_hint=det_use if isinstance(det_use, dict) else None,
        )
        outcome = str(v2.get("outcome") or "")
        mute_out = {
            "ok": bool(v2.get("ok")),
            "skipped": False,
            "skipped_reason": None,
            "failure_reason": v2.get("failure_reason"),
            "mute_started": True,
            "mute_engine_v2": True,
            "mute_v2_outcome": outcome,
            "mute_v2_abort_reason": v2.get("abort_reason"),
            "mute_v2_partial": bool(v2.get("partial")),
            "timings_ms": v2.get("timings_ms") or {},
        }
        if outcome == "success":
            log(
                "info",
                "post_follow_mute_success",
                visual_candidate_id=vcid,
                source_profile_username=src,
                mute_engine_v2=True,
                timings_ms=v2.get("timings_ms") or {},
            )
            _post_mute_state_checkpoint(
                d,
                pkg=pkg,
                source_profile_username=src,
                visual_candidate_id=vcid,
            )
        elif outcome == "partial_success":
            log(
                "info",
                "post_follow_mute_success",
                visual_candidate_id=vcid,
                source_profile_username=src,
                mute_engine_v2=True,
                mute_partial=True,
                timings_ms=v2.get("timings_ms") or {},
            )
        else:
            log(
                "warning",
                "post_follow_mute_failed",
                visual_candidate_id=vcid,
                source_profile_username=src,
                phase="mute_engine_v2",
                failure_reason=str(v2.get("failure_reason") or outcome or ""),
                mute_v2_outcome=outcome,
                mute_v2_abort_reason=v2.get("abort_reason"),
                timings_ms=v2.get("timings_ms") or {},
            )

    mute_ok = bool(mute_out.get("ok"))
    mute_attempted = bool(mute_out.get("mute_started"))
    compact_post_follow_return = bool(follow_success_verified and bool(vcid))
    if compact_post_follow_return:
        if should_mute and mute_ok and bool(mute_out.get("mute_v2_partial")):
            compact_reason_str = "follow_verified_mute_partial"
        elif should_mute and mute_ok:
            compact_reason_str = "follow_verified_mute_success"
        elif should_mute and mute_attempted:
            compact_reason_str = "follow_verified_mute_attempted"
        elif should_mute:
            compact_reason_str = "follow_verified_mute_attempted"
        else:
            compact_reason_str = "follow_verified_visual_candidate"
    else:
        compact_reason_str = ""

    log(
        "info",
        "post_follow_return_ct_started",
        visual_candidate_id=vcid,
        source_profile_username=src,
        follower_username=cand,
        compact_after_follow_verified_mute=compact_post_follow_return,
        compact_reason=compact_reason_str or None,
        mute_attempted=mute_attempted,
        mute_ok=mute_ok,
        should_mute=bool(should_mute),
        follow_success_verified=bool(follow_success_verified),
    )
    ok_ret, how_ret, fail_re = post_follow_controlled_return_to_followers_list(
        d,
        pkg=pkg,
        source_profile_username=src,
        follower_username=cand,
        visual_candidate_id=vcid,
        det=det_use,
        max_rounds=1 if compact_post_follow_return else 4,
        compact_after_follow_verified_mute=compact_post_follow_return,
        compact_reason=compact_reason_str if compact_post_follow_return else None,
    )
    if ok_ret:
        log(
            "info",
            "post_follow_return_ct_success",
            visual_candidate_id=vcid,
            source_profile_username=src,
            how=str(how_ret or ""),
        )
    else:
        log(
            "warning",
            "post_follow_return_ct_failed",
            visual_candidate_id=vcid,
            source_profile_username=src,
            failure_reason=str(fail_re or ""),
            how=str(how_ret or ""),
        )

    return {
        "mute": mute_out,
        "return_ok": bool(ok_ret),
        "return_how": str(how_ret or ""),
        "return_failure_reason": fail_re,
        "follow_success_verified": bool(follow_success_verified),
        "navigation_observed": nav_obs,
        "overlay_hints": overlay,
    }


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


def visual_flow_final_return_to_ct_followers_list(
    d: u2.Device,
    *,
    source_profile_username: str,
    pkg: str,
    candidate_username: str | None = None,
    source_account_context: str | None = None,
) -> dict[str, Any]:
    """
    Leave an opened follower-candidate profile and restore the source (CT) followers list.
    Thin wrapper over return_to_followers_list; never raises.
    """
    _ = source_account_context
    try:
        log(
            "info",
            "visual_flow_final_return_to_ct_followers_list_started",
            source_profile_username=source_profile_username,
            candidate_username=candidate_username,
        )
        ok, how = return_to_followers_list(d, source_profile_username, pkg or "")
        out = {
            "final_return_ok": bool(ok),
            "how": str(how or ""),
            "restart_required": False,
            "reset_ok": False,
            "reset_performed": False,
        }
        log(
            "info",
            "visual_flow_final_return_to_ct_followers_list_complete",
            source_profile_username=source_profile_username,
            candidate_username=candidate_username,
            **out,
        )
        return out
    except Exception as e:
        log(
            "warning",
            "visual_flow_final_return_to_ct_followers_list_failed",
            source_profile_username=source_profile_username,
            candidate_username=candidate_username,
            error=str(e),
        )
        return {
            "final_return_ok": False,
            "how": "exception",
            "restart_required": False,
            "reset_ok": False,
            "reset_performed": False,
        }


def verify_followers_list_surface_is_ct_account(
    d: u2.Device,
    *,
    source_profile_username: str,
    follower_candidate_username: str | None = None,
) -> bool:
    """
    Best-effort: True if current screen looks like the CT source account's followers list.
    Never raises. Uses detect_followers_list_screen + action bar / header hints.
    """
    try:
        det = detect_followers_list_screen(d, source_profile_username=source_profile_username)
        if not bool(det.get("is_followers_list")):
            return False

        src = _normalize_handle(source_profile_username or "")
        ab_raw = str(det.get("action_bar_title") or "").strip()
        ab = _normalize_handle(ab_raw)
        cand = _normalize_handle(follower_candidate_username or "")

        if cand and ab and ab == cand:
            return False

        if not src:
            return True

        if ab and ab == src:
            return True

        if ab and ab != src:
            return False

        for t in (det.get("visible_header_texts") or [])[:30]:
            if _normalize_handle(str(t)) == src:
                return True

        return bool(det.get("title_match")) or bool(det.get("strict_list_open"))
    except Exception:
        return True


def reset_instagram_to_canonical_state(
    d: u2.Device,
    *,
    reason: str,
    source_profile_username: str = "",
    source_account_context: str | None = None,
) -> dict[str, Any]:
    """
    Best-effort cold restart of Instagram (force-stop → launch → foreground check).
    Never raises; returns a dict with at least ``ok`` for runner compatibility.
    """
    _ = source_account_context
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "com.instagram.android")
    out: dict[str, Any] = {
        "ok": False,
        "reason": str(reason or ""),
        "reset_performed": False,
        "package": pkg,
        "source_profile_username": source_profile_username,
        "foreground_after_reset": False,
    }
    try:
        log(
            "info",
            "instagram_canonical_reset_started",
            reset_reason=out["reason"],
            package=pkg,
            source_profile_username=source_profile_username,
        )
        code, _stdout, _stderr = shell(d, f"am force-stop {pkg}")
        out["force_stop_exit_code"] = int(code)
        time.sleep(0.4)
        try:
            d.app_start(pkg, stop=False)
        except Exception as e_app:
            out["app_start_error"] = str(e_app)
            shell(d, f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
        time.sleep(0.85)
        fg = bool(verify_app_foreground(d, pkg))
        out["foreground_after_reset"] = fg
        out["reset_performed"] = True
        out["ok"] = fg
        if fg:
            try:
                invalidate_search_surface_cache(reason=f"canonical_reset:{reason}")
            except Exception:
                pass
        log(
            "info",
            "instagram_canonical_reset_complete",
            ok=out["ok"],
            reset_reason=out["reason"],
            foreground_after_reset=fg,
        )
        return out
    except Exception as e:
        out["error"] = str(e)
        log(
            "warning",
            "instagram_canonical_reset_failed",
            error=str(e),
            reset_reason=out["reason"],
            package=pkg,
        )
        return out


def recover_instagram_search_surface_after_launcher_mixup(
    d: u2.Device,
    *,
    phase: str,
    detail: str,
    source_profile_username: str = "",
    source_account_context: str | None = None,
) -> bool:
    """
    Cold-restart Instagram after we detected launcher/universal-search confusion.
    Caller should call open_search afterward. Does not log wrong_search_surface_detected
    (caller already did when applicable).
    """
    invalidate_search_surface_cache("wrong_search_surface_launcher_mixup")
    rr = reset_instagram_to_canonical_state(
        d,
        reason=f"launcher_search_surface_mixup:{phase}:{detail}",
        source_profile_username=source_profile_username,
        source_account_context=source_account_context,
    )
    if not rr.get("ok"):
        log(
            "error",
            "search_surface_wrong_app_launcher",
            phase=phase,
            stage="canonical_reset_failed",
            reset_reason=rr.get("reason"),
            foreground_package=_current_foreground_package(d),
        )
        return False
    log(
        "info",
        "instagram_recovery_after_launcher_search",
        phase=phase,
        detail=detail,
        foreground_after_reset=bool(rr.get("foreground_after_reset")),
    )
    return True


def ensure_global_search_surface(
    d: u2.Device,
    *,
    intended_username: str = "",
    source_profile_username: str = "",
    source_account_context: str = "",
) -> dict[str, Any]:
    """
    Ensure Instagram is foreground and the bottom-nav Search surface is usable.
    Compatible with runner / canonical-reset reentry (keys: ok, reason).
    """
    _ = source_account_context
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "com.instagram.android")
    meta: dict[str, Any] = {
        "ok": False,
        "reason": "init",
        "package": pkg,
        "intended_username": intended_username,
        "source_profile_username": source_profile_username,
    }
    try:
        log(
            "info",
            "ensure_global_search_surface_started",
            package=pkg,
            intended_username=intended_username,
            source_profile_username=source_profile_username,
        )
        if not verify_app_foreground(d, pkg):
            try:
                d.app_start(pkg, stop=False)
            except Exception:
                shell(d, f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
            time.sleep(0.55)
            if not verify_app_foreground(d, pkg):
                meta["reason"] = "instagram_not_foreground"
                return meta
        if open_search(d):
            meta["ok"] = True
            meta["reason"] = "open_search_ok"
            log(
                "info",
                "instagram_search_surface_verified",
                phase="ensure_global_search_surface",
                detail="open_search_ok",
            )
            return meta
        if is_lightweight_search_screen(d, pkg):
            if apply_search_surface_reuse_metrics(d, pkg, "ensure_global_fallback"):
                meta["ok"] = True
                meta["reason"] = "lightweight_search_screen"
                log(
                    "info",
                    "instagram_search_surface_verified",
                    phase="ensure_global_search_surface",
                    detail="lightweight_search_screen",
                )
                return meta
        meta["reason"] = "open_search_failed"
        return meta
    except Exception as e:
        meta["reason"] = f"exception:{type(e).__name__}"
        meta["error"] = str(e)
        return meta


def visual_profile_metrics_pass_filter(metrics: dict[str, Any]) -> tuple[bool, str]:
    """
    (passes, reason). When extraction is weak or thresholds are unset, default pass (do not skip target).
    Thresholds: optional VISUAL_PROFILE_METRICS_* on config.
    """
    if not isinstance(metrics, dict):
        return True, "metrics_invalid_skip_filter"

    min_followers = getattr(config, "VISUAL_PROFILE_METRICS_MIN_FOLLOWERS", None)
    max_followers = getattr(config, "VISUAL_PROFILE_METRICS_MAX_FOLLOWERS", None)
    min_posts = getattr(config, "VISUAL_PROFILE_METRICS_MIN_POSTS", None)
    max_posts = getattr(config, "VISUAL_PROFILE_METRICS_MAX_POSTS", None)
    min_following = getattr(config, "VISUAL_PROFILE_METRICS_MIN_FOLLOWING", None)
    max_following = getattr(config, "VISUAL_PROFILE_METRICS_MAX_FOLLOWING", None)

    active_thresholds = [
        x is not None
        for x in (
            min_followers,
            max_followers,
            min_posts,
            max_posts,
            min_following,
            max_following,
        )
    ]
    if not any(active_thresholds):
        return True, "no_metrics_thresholds_configured"

    if not metrics.get("extraction_ok", False):
        return True, "metrics_extraction_failed_skip_filter"

    fc = metrics.get("followers_count")
    pc = metrics.get("posts_count")
    flc = metrics.get("following_count")

    def _chk(
        val: Any,
        lo: Any,
        hi: Any,
        below_reason: str,
        above_reason: str,
    ) -> tuple[bool, str] | None:
        if val is None:
            return None
        try:
            v = int(val)
        except (TypeError, ValueError):
            return None
        if lo is not None and v < int(lo):
            return False, below_reason
        if hi is not None and v > int(hi):
            return False, above_reason
        return None

    for val, lo, hi, br, ar in (
        (fc, min_followers, max_followers, "below_min_followers", "above_max_followers"),
        (pc, min_posts, max_posts, "below_min_posts", "above_max_posts"),
        (flc, min_following, max_following, "below_min_following", "above_max_following"),
    ):
        res = _chk(val, lo, hi, br, ar)
        if res is not None:
            ok_r, reason_r = res
            if not ok_r:
                return False, reason_r
    return True, "metrics_thresholds_pass"


def visual_profile_stats_posts_count(d: u2.Device) -> int | None:
    """Posts count from header stats strip; None if unknown."""
    try:
        m = visual_extract_profile_metrics(d, source_profile_username="")
        pc = m.get("posts_count")
        return int(pc) if pc is not None else None
    except Exception:
        return None


def read_current_profile_username_for_follow_gate(d: u2.Device) -> str:
    """Action-bar / header username on current profile screen (best-effort)."""
    try:
        return str(_visual_read_action_bar_username(d) or "").strip().lstrip("@")
    except Exception:
        return ""


def _visual_raw_follow_invite_visible_quick(d: u2.Device) -> bool:
    """Lightweight Follow / Suivre probe (mirrors runner CT-list raw invite helper)."""
    try:
        if d(text="Follow").exists(timeout=0.07):
            return True
        if d(text="Suivre").exists(timeout=0.05):
            return True
        if d(textContains="Follow").exists(timeout=0.05):
            return True
        if d(description="Follow").exists(timeout=0.04):
            return True
    except Exception:
        return False
    return False


def visual_candidate_follow_pre_follow_screen_guard(
    d: u2.Device,
    *,
    source_profile_username: str,
    pkg: str,
    pick: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Before ``perform_follow_safe(profile_already_open=True)``, confirm we are not back on the
    source (CT) profile or followers list, and that a follow invite is visible on the current surface.
    """
    from navigation_engine import NavigationEngineState, observe_instagram_state

    src_raw = str(source_profile_username or "").strip()
    p = dict(pick) if isinstance(pick, dict) else {}
    vcid = str(p.get("visual_candidate_id") or "").strip()
    exp_pkg = str(pkg or getattr(config, "INSTAGRAM_PACKAGE", "") or "").strip()

    ab_title = ""
    try:
        ab_title = read_current_profile_username_for_follow_gate(d)
    except Exception:
        ab_title = ""

    sn = _normalize_handle(src_raw)
    an = _normalize_handle(ab_title)

    out: dict[str, Any] = {
        "ok": False,
        "reason": "init",
        "action_bar_title": ab_title,
        "navigation_state": "",
        "navigation_confidence": 0.0,
        "navigation_reason": "",
        "follow_header_state": "",
        "followers_list_xml_hint": False,
        "raw_follow_invite_visible": False,
        "visual_candidate_id": vcid,
        "source_profile_username": src_raw,
    }

    nav = observe_instagram_state(
        d,
        expected_package=exp_pkg,
        last_known_state=NavigationEngineState.CANDIDATE_PROFILE.value,
        context={
            "phase": "visual_follow_pre_follow_guard",
            "visual_candidate_id": vcid,
            "source_profile_username": src_raw,
            "disable_followers_visual_fallback": True,
            "expected_state": "CANDIDATE_PROFILE",
        },
    )
    out["navigation_state"] = str(nav.get("state") or "")
    out["navigation_confidence"] = float(nav.get("confidence") or 0.0)
    out["navigation_reason"] = str(nav.get("reason") or "")

    try:
        det_fresh = detect_followers_list_screen(
            d, source_profile_username=src_raw
        )
        out["followers_list_xml_hint"] = bool(
            det_fresh.get("is_followers_list")
            and (
                bool(det_fresh.get("strict_list_open"))
                or bool(det_fresh.get("relaxed_list_open"))
            )
        )
    except Exception:
        out["followers_list_xml_hint"] = False

    try:
        out["follow_header_state"] = _follow_ui_state_snapshot(d)
    except Exception:
        out["follow_header_state"] = "unknown"

    out["raw_follow_invite_visible"] = _visual_raw_follow_invite_visible_quick(d)

    if sn and an and sn == an:
        out["ok"] = False
        out["reason"] = "current_screen_is_source_profile"
        return out

    if out["followers_list_xml_hint"]:
        out["ok"] = False
        out["reason"] = "current_screen_is_followers_list"
        return out

    if (
        out["navigation_state"] == NavigationEngineState.FOLLOWERS_LIST.value
        and out["navigation_confidence"] >= 0.45
    ):
        out["ok"] = False
        out["reason"] = "navigation_says_followers_list"
        return out

    if not out["raw_follow_invite_visible"] and out["follow_header_state"] != "follow":
        out["ok"] = False
        out["reason"] = "follow_invite_not_visible"
        return out

    out["ok"] = True
    out["reason"] = "candidate_profile_surface_ok"
    return out


def reacquire_target_profile_for_follow(
    d: u2.Device,
    *,
    target_username: str,
    source_profile_username: str,
    source_account_context: str = "",
    follower_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Re-open the follower row profile when navigation drifted before follow/mute.
    """
    _ = source_account_context
    out: dict[str, Any] = {"ok": False, "method": "none"}
    try:
        tgt = _normalize_handle(target_username or "")
        cur = _normalize_handle(read_current_profile_username_for_follow_gate(d))
        if tgt and cur and cur == tgt:
            out["ok"] = True
            out["method"] = "already_on_target_profile"
            return out

        fc = follower_candidate if isinstance(follower_candidate, dict) else None
        if not fc or not str(fc.get("username") or "").strip():
            out["method"] = "missing_follower_candidate"
            return out

        pkg = getattr(config, "INSTAGRAM_PACKAGE", "") or ""
        if open_follower_profile_from_list(d, fc, source_profile_username, pkg):
            out["ok"] = True
            out["method"] = "follower_row_reopen"
        else:
            out["method"] = "open_follower_profile_failed"
        return out
    except Exception as e:
        out["method"] = "exception"
        out["error"] = str(e)
        return out


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

