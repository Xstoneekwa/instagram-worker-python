"""Local Followers Segment B injection screenshot freshness (V3.2-B2)."""

from __future__ import annotations

import os
import time
from typing import Any

import config
from logs import log

_META_PATH = "latest_followers_injection_screenshot_path"
_META_TS = "latest_followers_injection_capture_ts"
_META_REASON = "latest_followers_injection_capture_reason"
_META_SCROLL = "latest_followers_injection_scroll_used"
_META_SOURCE = "latest_followers_injection_source_profile_username"

_VLS_LIGHT_OK = "committed_light_revalidate_ok"
_VLS_INVALID_REASON = "followers_injection_evidence_invalidation_reason"


def _norm_source(username: str) -> str:
    return str(username or "").strip().lstrip("@").lower()


def injection_evidence_max_age_ms() -> float:
    return float(getattr(config, "FOLLOWERS_INJECTION_EVIDENCE_MAX_AGE_MS", 5000) or 5000)


def committed_skip_full_detect_max_age_ms() -> float:
    return float(
        getattr(config, "FOLLOWERS_COMMITTED_SKIP_FULL_DETECT_MAX_AGE_MS", 30000) or 30000
    )


def post_return_promoted_evidence_max_age_ms() -> float:
    return float(
        getattr(config, "FOLLOWERS_POST_RETURN_PROMOTED_EVIDENCE_MAX_AGE_MS", 5000) or 5000
    )


def note_followers_injection_capture(
    open_list_meta: dict[str, Any] | None,
    *,
    screenshot_path: str,
    capture_reason: str,
    source_profile_username: str = "",
    scroll_used: int = 0,
    visual_loop_state: dict[str, Any] | None = None,
) -> None:
    if not isinstance(open_list_meta, dict):
        return
    path = str(screenshot_path or "").strip()
    if not path:
        return
    open_list_meta[_META_PATH] = path
    open_list_meta[_META_TS] = float(time.perf_counter())
    open_list_meta[_META_REASON] = str(capture_reason or "other").strip()[:80] or "other"
    open_list_meta[_META_SCROLL] = int(scroll_used)
    open_list_meta[_META_SOURCE] = _norm_source(source_profile_username)
    if isinstance(visual_loop_state, dict):
        visual_loop_state.pop(_VLS_INVALID_REASON, None)


def invalidate_followers_injection_evidence(
    open_list_meta: dict[str, Any] | None,
    visual_loop_state: dict[str, Any] | None,
    *,
    reason: str,
    source_profile_username: str = "",
) -> None:
    if isinstance(open_list_meta, dict):
        open_list_meta[_META_TS] = 0.0
    if isinstance(visual_loop_state, dict):
        visual_loop_state[_VLS_LIGHT_OK] = False
        visual_loop_state[_VLS_INVALID_REASON] = str(reason or "invalidated")[:160]
    try:
        log(
            "info",
            "followers_followers_injection_evidence_invalidated",
            source_profile_username=str(source_profile_username or "")[:120],
            reason=str(reason or "")[:160],
            screenshot_path=str((open_list_meta or {}).get(_META_PATH) or "")[:400],
        )
    except Exception:
        pass


def injection_evidence_age_ms(open_list_meta: dict[str, Any] | None) -> float:
    if not isinstance(open_list_meta, dict):
        return -1.0
    ts = float(open_list_meta.get(_META_TS) or 0.0)
    if ts <= 0.0:
        return -1.0
    return max(0.0, (time.perf_counter() - ts) * 1000.0)


def injection_evidence_is_usable(
    open_list_meta: dict[str, Any] | None,
    *,
    source_profile_username: str,
    max_age_ms: float | None = None,
    visual_loop_state: dict[str, Any] | None = None,
) -> bool:
    if not isinstance(open_list_meta, dict):
        return False
    if isinstance(visual_loop_state, dict) and str(
        visual_loop_state.get(_VLS_INVALID_REASON) or ""
    ).strip():
        return False
    path = str(open_list_meta.get(_META_PATH) or "").strip()
    if not path or not os.path.isfile(path):
        return False
    bound = float(max_age_ms if max_age_ms is not None else injection_evidence_max_age_ms())
    age = injection_evidence_age_ms(open_list_meta)
    if age < 0.0 or age > bound:
        return False
    meta_src = str(open_list_meta.get(_META_SOURCE) or "").strip()
    want = _norm_source(source_profile_username)
    if meta_src and want and meta_src != want:
        return False
    return True


def should_skip_post_return_injection_capture(
    open_list_meta: dict[str, Any] | None,
    visual_loop_state: dict[str, Any] | None,
    *,
    source_profile_username: str,
) -> tuple[bool, dict[str, Any]]:
    meta: dict[str, Any] = {"skip": False}
    if not isinstance(open_list_meta, dict):
        return False, meta
    if not bool((visual_loop_state or {}).get("post_return_picker_refresh_pending")):
        return False, meta
    reason = str(open_list_meta.get(_META_REASON) or "").strip()
    if reason not in ("post_return_promoted", "post_return", "post_return_injection"):
        return False, meta
    max_age = post_return_promoted_evidence_max_age_ms()
    if not injection_evidence_is_usable(
        open_list_meta,
        source_profile_username=source_profile_username,
        max_age_ms=max_age,
        visual_loop_state=visual_loop_state,
    ):
        return False, meta
    age = injection_evidence_age_ms(open_list_meta)
    meta = {
        "skip": True,
        "evidence_age_ms": round(age, 2),
        "evidence_capture_reason": reason,
        "screenshot_path": str(open_list_meta.get(_META_PATH) or "")[:400],
    }
    return True, meta


def should_skip_committed_loop_top_detect(
    open_list_meta: dict[str, Any] | None,
    visual_loop_state: dict[str, Any] | None,
    *,
    source_profile_username: str,
    committed_age_ms: float,
    list_committed_open: bool,
) -> tuple[bool, dict[str, Any]]:
    meta: dict[str, Any] = {"skip": False}
    if not list_committed_open:
        return False, meta
    if float(committed_age_ms) < 0.0 or float(committed_age_ms) > committed_skip_full_detect_max_age_ms():
        return False, meta
    if not isinstance(visual_loop_state, dict) or not bool(
        visual_loop_state.get(_VLS_LIGHT_OK)
    ):
        return False, meta
    if not injection_evidence_is_usable(
        open_list_meta,
        source_profile_username=source_profile_username,
        max_age_ms=injection_evidence_max_age_ms(),
        visual_loop_state=visual_loop_state,
    ):
        return False, meta
    age = injection_evidence_age_ms(open_list_meta)
    meta = {
        "skip": True,
        "evidence_age_ms": round(age, 2),
        "committed_age_ms": round(float(committed_age_ms), 2),
        "evidence_capture_reason": str(open_list_meta.get(_META_REASON) or "")[:80]
        if isinstance(open_list_meta, dict)
        else "",
        "screenshot_path": str(open_list_meta.get(_META_PATH) or "")[:400]
        if isinstance(open_list_meta, dict)
        else "",
    }
    return True, meta


def set_committed_light_revalidate_ok(visual_loop_state: dict[str, Any] | None, *, ok: bool) -> None:
    if isinstance(visual_loop_state, dict):
        visual_loop_state[_VLS_LIGHT_OK] = bool(ok)


_PICKER_INTERNAL_GATE_SKIP_PHASES: frozenset[str] = frozenset({"followers_engine_inject"})


def should_skip_picker_internal_vision_gate(
    open_list_meta: dict[str, Any] | None,
    visual_loop_state: dict[str, Any] | None,
    *,
    source_profile_username: str,
    screenshot_path: str,
    picker_phase: str | None = None,
    list_committed_open: bool = False,
    committed_age_ms: float | None = None,
) -> tuple[bool, dict[str, Any]]:
    """
    V3.2-D1: skip redundant picker-internal vision gate when committed surface was
    light-revalidated on the same fresh injection screenshot (Segment B inject path).
    """
    meta: dict[str, Any] = {"skip": False}
    phase = str(picker_phase or "").strip()
    if phase not in _PICKER_INTERNAL_GATE_SKIP_PHASES:
        return False, meta
    if not list_committed_open:
        return False, meta
    c_age = float(committed_age_ms if committed_age_ms is not None else -1.0)
    if c_age < 0.0 or c_age > committed_skip_full_detect_max_age_ms():
        return False, meta
    if not isinstance(visual_loop_state, dict) or not bool(
        visual_loop_state.get(_VLS_LIGHT_OK)
    ):
        return False, meta
    path = str(screenshot_path or "").strip()
    inj_path = ""
    if isinstance(open_list_meta, dict):
        inj_path = str(open_list_meta.get(_META_PATH) or "").strip()
    if not path or not inj_path or path != inj_path or not os.path.isfile(path):
        return False, meta
    if not injection_evidence_is_usable(
        open_list_meta,
        source_profile_username=source_profile_username,
        max_age_ms=injection_evidence_max_age_ms(),
        visual_loop_state=visual_loop_state,
    ):
        return False, meta
    ev_age = injection_evidence_age_ms(open_list_meta)
    meta = {
        "skip": True,
        "evidence_age_ms": round(ev_age, 2),
        "committed_age_ms": round(c_age, 2),
        "screenshot_path": path[:400],
        "reason": "same_fresh_committed_evidence_already_revalidated",
    }
    return True, meta
