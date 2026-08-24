"""Global account identity preflight for account-bound Instagram flows.

Before a Supabase account run performs business actions, the logged-in Instagram
account in the active clone must match the expected account username.

Roadmap note: today this is a username-only guard, so any mismatch safe-stops.
When a stable Instagram account identifier is available, the structured result is
ready to distinguish a wrong active account from a possible username rename.
"""

from __future__ import annotations

import html
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import uiautomator2 as u2

import config
from instagram_login_status_classifier import LoginProbeOutcome
from instagram_login_ui_probe import (
    detect_login_probe_outcome_from_hierarchy,
    extract_login_screen_signals_from_hierarchy,
)
from instagram_ads_data_consent_popup import (
    IDENTITY_PENDING_REASON as ADS_DATA_CONSENT_IDENTITY_PENDING_REASON,
    POPUP_TYPE as ADS_DATA_CONSENT_POPUP_TYPE,
    classify_instagram_ads_data_consent_popup,
    publish_ads_data_consent_operator_alert,
)
from instagram_post_verification_completion import prepare_post_verification_identity_surface
from logs import log
from own_profile_navigation import open_own_profile_from_bottom_nav

ACCOUNT_IDENTITY_MISMATCH_REASON = "active_instagram_account_mismatch"
POSSIBLE_USERNAME_RENAME_REASON = "possible_username_rename_detected"
DEVICE_LOCKED_REASON = "device_locked"
DEVICE_UNLOCK_FAILED_REASON = "device_unlock_failed"
DEVICE_LOCKED_REQUIRES_OPERATOR_REASON = "device_locked_requires_operator"
PREFLIGHT_KEYGUARD_STAGE = "pre_profile_keyguard_check"
KEYGUARD_SCREEN_TYPE = "device_keyguard"
KEYGUARD_DETECTION_REASON = "android_keyguard_detected"
_PREFLIGHT_RUN_TYPE = "scheduled_session_preflight"
_KEYGUARD_SWIPE_SETTLE_SECONDS = 1.0

_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")

_LOGS_ROOT = Path(__file__).resolve().parent / "logs"
_IDENTITY_GUARD_ARTIFACTS_DIR = _LOGS_ROOT / "identity_guard"
_LOADING_RETRY_SECONDS = 2.5

RESTRICTION_FAMILY = "instagram_account_restriction"
MESSAGES_DISABLED_REASON = "instagram_account_restriction_messages_disabled"
UNKNOWN_RESTRICTION_SCOPE_REASON = "instagram_account_restriction_unknown_scope"
_RESTRICTION_TITLE_NORMALIZED = "we added a restriction to your account"
_MESSAGES_DISABLED_DETAILS = frozenset(
    {
        "you can't send messages",
        "you cannot send messages",
    }
)
_RESTRICTION_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7,
    "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12,
    "december": 12,
}
_RESTRICTION_DATE_RE = re.compile(
    r"\b(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})\b",
    re.IGNORECASE,
)

# Screens that may proceed to bottom-nav profile open (no dismiss / no bypass).
_PRE_PROFILE_CONTINUE_SCREEN_TYPES = frozenset(
    {
        "active_account_home",
        "active_account_profile",
        "settings_and_activity",
        "profile_menu_sheet",
        "unknown",
    }
)

_LOGIN_SCREEN_TYPES = frozenset(
    {
        "login_form_empty",
        "login_form_prefilled_username",
        "continue_password_only",
        "join_instagram_landing",
        "account_picker",
        "continue_as_candidate",
        "add_account_sheet",
        "account_switcher_sheet",
    }
)

_POST_LOGIN_POPUP_SCREEN_TYPES = frozenset(
    {
        "save_login_info_prompt",
        "instagram_turn_on_notifications_prompt",
        "connected_post_login_location_services_prompt",
        "android_instagram_notification_settings",
        "google_password_manager_save_prompt",
        "samsung_pass_save_password_prompt",
        "password_required_dialog",
        "logout_confirmation_prompt",
    }
)


@dataclass(frozen=True)
class AccountIdentityCheckResult:
    ok: bool
    expected_account_username: str
    actual_logged_in_username: str = ""
    expected_instagram_user_id: str = ""
    actual_instagram_user_id: str = ""
    failure_reason: str = ""
    identity_evidence: str = "username_only"
    rename_disambiguation_status: str = "stable_instagram_user_id_not_available"
    verification_method: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InstagramAccountRestriction:
    """Structured semantics parsed from one already-fresh XML surface."""

    detected: bool
    reason_code: str = ""
    restriction_family: str = ""
    restriction_scope: str = ""
    restriction_action: str = ""
    restriction_state: str = ""
    restriction_start_date: str | None = None
    restriction_end_date: str | None = None
    restriction_title_raw: str = ""
    restriction_detail_raw: str = ""
    restriction_start_raw: str = ""
    restriction_end_raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_restriction_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    text = text.replace("’", "'").replace("‘", "'")
    return " ".join(text.casefold().split())


def _restriction_xml_strings(hierarchy_xml: str) -> list[str]:
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
    values: list[str] = []
    for element in root.iter():
        for attribute in ("text", "content-desc"):
            value = html.unescape(str(element.get(attribute) or "")).strip()
            if value and value not in values:
                values.append(value)
    return values


def _first_restriction_value(values: Iterable[str], predicate) -> str:
    for value in values:
        if predicate(value):
            return value
    return ""


def _parse_restriction_date_only(raw: str) -> str | None:
    match = _RESTRICTION_DATE_RE.search(str(raw or ""))
    if not match:
        return None
    month = _RESTRICTION_MONTHS.get(match.group("month").casefold())
    if month is None:
        return None
    try:
        return date(int(match.group("year")), month, int(match.group("day"))).isoformat()
    except ValueError:
        return None


def classify_instagram_account_restriction(
    hierarchy_xml: str,
) -> InstagramAccountRestriction:
    """Classify explicit restrictions without acquiring any new device evidence."""
    values = _restriction_xml_strings(hierarchy_xml)
    title = _first_restriction_value(
        values,
        lambda value: _normalize_restriction_text(value) == _RESTRICTION_TITLE_NORMALIZED,
    )
    if not title:
        return InstagramAccountRestriction(detected=False)

    detail = _first_restriction_value(
        values,
        lambda value: _normalize_restriction_text(value) in _MESSAGES_DISABLED_DETAILS,
    )
    start_raw = _first_restriction_value(
        values,
        lambda value: bool(_RESTRICTION_DATE_RE.search(value))
        and not _normalize_restriction_text(value).startswith("ends on "),
    )
    end_raw = _first_restriction_value(
        values,
        lambda value: _normalize_restriction_text(value).startswith("ends on ")
        and bool(_RESTRICTION_DATE_RE.search(value)),
    )
    if detail:
        return InstagramAccountRestriction(
            detected=True,
            reason_code=MESSAGES_DISABLED_REASON,
            restriction_family=RESTRICTION_FAMILY,
            restriction_scope="messaging",
            restriction_action="send_messages",
            restriction_state="disabled",
            restriction_start_date=_parse_restriction_date_only(start_raw),
            restriction_end_date=_parse_restriction_date_only(end_raw),
            restriction_title_raw=title,
            restriction_detail_raw=detail,
            restriction_start_raw=start_raw,
            restriction_end_raw=end_raw,
        )
    return InstagramAccountRestriction(
        detected=True,
        reason_code=UNKNOWN_RESTRICTION_SCOPE_REASON,
        restriction_family=RESTRICTION_FAMILY,
        restriction_scope="unknown",
        restriction_action="unknown",
        restriction_state="restricted",
        restriction_start_date=_parse_restriction_date_only(start_raw),
        restriction_end_date=_parse_restriction_date_only(end_raw),
        restriction_title_raw=title,
        restriction_start_raw=start_raw,
        restriction_end_raw=end_raw,
    )


def normalize_account_username(username: str) -> str:
    return str(username or "").strip().lstrip("@").lower()


def _dump_hierarchy(d: u2.Device) -> str:
    try:
        try:
            return str(d.dump_hierarchy(compressed=False) or "")
        except TypeError:
            return str(d.dump_hierarchy() or "")
    except Exception:
        return ""


def _parse_xml_root(hierarchy_xml: str) -> ET.Element | None:
    text = str(hierarchy_xml or "").strip()
    if not text:
        return None
    try:
        try:
            return ET.fromstring(text)
        except ET.ParseError:
            return ET.fromstring(f"<wrap>{text}</wrap>")
    except Exception:
        return None


def _looks_like_handle(raw: str) -> bool:
    value = str(raw or "").strip().lstrip("@")
    return bool(value and _HANDLE_RE.match(value))


def _extract_own_profile_username_from_hierarchy(hierarchy_xml: str) -> tuple[str, str, dict[str, Any]]:
    root = _parse_xml_root(hierarchy_xml)
    meta: dict[str, Any] = {
        "action_bar_title": "",
        "candidate_texts": [],
        "hierarchy_xml_len": len(str(hierarchy_xml or "")),
    }
    if root is None:
        return "", "hierarchy_xml_parse_failed", meta

    candidates: list[tuple[int, str, str]] = []
    for el in root.iter():
        rid = str(el.get("resource-id") or "")
        rid_l = rid.lower()
        text = str(el.get("text") or el.get("content-desc") or "").strip().lstrip("@")
        if not text or not _looks_like_handle(text):
            continue
        if "action_bar_title" in rid_l:
            meta["action_bar_title"] = text
            candidates.append((0, text, "action_bar_title"))
        elif "profile_header" in rid_l and "username" in rid_l:
            candidates.append((1, text, "profile_header_username"))
        elif "username" in rid_l and "row_search" not in rid_l:
            candidates.append((2, text, "username_resource_id"))
        elif "title" in rid_l:
            candidates.append((3, text, "title_resource_id"))
    if candidates:
        candidates.sort(key=lambda item: item[0])
        meta["candidate_texts"] = [
            {"username": value, "method": method, "rank": rank}
            for rank, value, method in candidates[:8]
        ]
        _, username, method = candidates[0]
        return username, method, meta
    return "", "own_profile_username_not_found", meta


def _screen_classification_meta(
    signals: dict[str, Any],
    *,
    probe_outcome: LoginProbeOutcome | None = None,
    identity_guard_stage: str = "pre_profile_screen",
    loading_retry_used: bool = False,
) -> dict[str, Any]:
    screen_type = str(signals.get("screen_type") or "unknown")
    detection_reason = str(signals.get("detection_reason") or screen_type)
    if probe_outcome and probe_outcome not in {LoginProbeOutcome.UNKNOWN, LoginProbeOutcome.CONNECTED}:
        detection_reason = str(probe_outcome.value)
    return {
        "screen_type": screen_type,
        "detection_reason": detection_reason,
        "identity_guard_stage": identity_guard_stage,
        "hierarchy_xml_len": int(signals.get("hierarchy_xml_len") or 0),
        "transition_loading": bool(signals.get("transition_loading")),
        "loading_retry_used": loading_retry_used,
    }


def _pre_profile_blocking_reason(
    signals: dict[str, Any],
    probe_outcome: LoginProbeOutcome,
) -> tuple[str, str] | None:
    """Return (failure_reason, detection_reason) when the screen must not proceed."""
    screen_type = str(signals.get("screen_type") or "unknown")

    if probe_outcome == LoginProbeOutcome.CHECKPOINT:
        return "checkpoint", "checkpoint_signal"
    if probe_outcome in {LoginProbeOutcome.NEEDS_2FA, LoginProbeOutcome.VERIFICATION_PENDING}:
        return "login_challenge", probe_outcome.value
    if probe_outcome == LoginProbeOutcome.UNSUPPORTED_POST_SUBMIT_CHALLENGE:
        return "login_challenge", "unsupported_post_submit_challenge"
    if probe_outcome == LoginProbeOutcome.LOGGED_OUT:
        return "login_screen_detected", "login_screen_signal"

    if screen_type == "email_code_challenge":
        return "login_challenge", "email_code_challenge"
    if screen_type in _LOGIN_SCREEN_TYPES:
        return "login_screen_detected", screen_type
    if screen_type in _POST_LOGIN_POPUP_SCREEN_TYPES:
        return "post_login_popup_detected", screen_type

    if signals.get("transition_loading") and screen_type == "unknown":
        return None

    if screen_type not in _PRE_PROFILE_CONTINUE_SCREEN_TYPES:
        return "ui_not_recognized", screen_type

    return None


def _classify_screen_from_hierarchy(
    hierarchy_xml: str,
    *,
    expected_username: str = "",
) -> tuple[dict[str, Any], LoginProbeOutcome]:
    signals = extract_login_screen_signals_from_hierarchy(
        hierarchy_xml,
        expected_username=expected_username or None,
    )
    signals = dict(signals)
    signals["hierarchy_xml_len"] = len(str(hierarchy_xml or ""))
    probe_outcome = detect_login_probe_outcome_from_hierarchy(hierarchy_xml) or LoginProbeOutcome.UNKNOWN
    return signals, probe_outcome


def _capture_identity_failure_artifacts(
    d: u2.Device,
    hierarchy_xml: str,
    *,
    account_id: str | None,
    run_id: str | None,
    stage: str,
) -> dict[str, Any]:
    """Persist local-only screenshot/XML for operator forensics; never expose raw content remotely."""
    meta: dict[str, Any] = {
        "screenshot_captured": False,
        "xml_dump_captured": False,
        "screenshot_basename": "",
        "xml_basename": "",
        "hierarchy_xml_len": len(str(hierarchy_xml or "")),
    }
    try:
        _IDENTITY_GUARD_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time() * 1000)
        acct = str(account_id or "unknown")[:8]
        stem = f"identity_guard_{acct}_{stamp}"
        screenshot_path = _IDENTITY_GUARD_ARTIFACTS_DIR / f"{stem}.png"
        xml_path = _IDENTITY_GUARD_ARTIFACTS_DIR / f"{stem}.xml"
        try:
            d.screenshot(str(screenshot_path))
            meta["screenshot_captured"] = True
            meta["screenshot_basename"] = screenshot_path.name
        except Exception as exc:
            log(
                "warning",
                "identity_guard_screenshot_capture_failed",
                account_id=account_id,
                run_id=run_id,
                stage=stage,
                error=str(exc)[:200],
            )
        try:
            xml_body = str(hierarchy_xml or "")
            if not xml_body:
                xml_body = _dump_hierarchy(d)
            xml_path.write_text(xml_body, encoding="utf-8")
            meta["xml_dump_captured"] = bool(xml_body)
            meta["xml_basename"] = xml_path.name
            meta["hierarchy_xml_len"] = len(xml_body)
        except Exception as exc:
            log(
                "warning",
                "identity_guard_xml_capture_failed",
                account_id=account_id,
                run_id=run_id,
                stage=stage,
                error=str(exc)[:200],
            )
        if meta["screenshot_captured"] or meta["xml_dump_captured"]:
            log(
                "info",
                "identity_guard_failure_artifacts_captured",
                account_id=account_id,
                run_id=run_id,
                stage=stage,
                screenshot_captured=meta["screenshot_captured"],
                xml_dump_captured=meta["xml_dump_captured"],
                screenshot_basename=meta["screenshot_basename"],
                xml_basename=meta["xml_basename"],
            )
    except Exception as exc:
        log(
            "warning",
            "identity_guard_failure_artifacts_failed",
            account_id=account_id,
            run_id=run_id,
            stage=stage,
            error=str(exc)[:200],
        )
    return meta


def _device_foreground_package(d: u2.Device) -> str:
    try:
        return str(d.app_current().get("package") or "").strip()
    except Exception:
        return ""


def _wake_device_for_preflight(d: u2.Device) -> bool:
    """Best-effort wake before keyguard handling; never enters credentials."""
    woke = False
    try:
        info = d.info or {}
        if not bool(info.get("screenOn", True)):
            d.screen_on()
            woke = True
            time.sleep(0.35)
    except Exception:
        pass
    if not woke:
        try:
            d.shell("input keyevent KEYCODE_WAKEUP")
            woke = True
            time.sleep(0.35)
        except Exception:
            pass
    return woke


def _swipe_up_keyguard_once(d: u2.Device) -> None:
    try:
        width, height = d.window_size()
    except Exception:
        width, height = 1080, 2340
    center_x = max(1, int(width) // 2)
    start_y = max(1, int(height * 0.82))
    end_y = max(1, int(height * 0.18))
    d.swipe(center_x, start_y, center_x, end_y, 0.25)


def classify_keyguard_from_hierarchy(
    hierarchy_xml: str,
    *,
    foreground_package: str = "",
) -> dict[str, Any]:
    """Detect Samsung/Android keyguard without attempting unlock."""
    xml = str(hierarchy_xml or "")
    xml_l = xml.lower()
    pkg = str(foreground_package or "").strip().lower()
    systemui_present = "package=\"com.android.systemui\"" in xml_l or pkg == "com.android.systemui"
    keyguard_rid = any(
        marker in xml_l
        for marker in (
            "keyguard_indication_text",
            "keyguard_bottom_shortcut_area",
            "keyguard_status_view",
            "keyguard_punch_hole",
            "keyguard_clock_container",
            ":id/keyguard",
        )
    )
    swipe_to_open = "swipe to open" in xml_l
    keyguard_active = bool(systemui_present and (keyguard_rid or swipe_to_open))
    if pkg == "com.android.systemui" and (keyguard_rid or swipe_to_open):
        keyguard_active = True

    secure_markers = (
        "enter pin",
        "enter your pin",
        "enter password",
        "enter your password",
        "draw pattern",
        "pin_view",
        "password_entry",
        "lock_pattern",
        "bouncer_message",
        "emergency_call_button",
    )
    secure_lock_required = keyguard_active and any(marker in xml_l for marker in secure_markers)
    swipe_only_unlock_available = keyguard_active and not secure_lock_required and swipe_to_open
    return {
        "keyguard_active": keyguard_active,
        "secure_lock_required": secure_lock_required,
        "swipe_only_unlock_available": swipe_only_unlock_available,
        "foreground_package": pkg,
        "hierarchy_xml_len": len(xml),
    }


def _keyguard_preflight_meta(
    *,
    keyguard_state: dict[str, Any],
    unlock_attempted: bool,
    unlock_result: str,
    artifact_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "screen_type": KEYGUARD_SCREEN_TYPE,
        "detection_reason": KEYGUARD_DETECTION_REASON,
        "identity_guard_stage": PREFLIGHT_KEYGUARD_STAGE,
        "unlock_attempted": unlock_attempted,
        "unlock_result": unlock_result,
        "hierarchy_xml_len": int(keyguard_state.get("hierarchy_xml_len") or 0),
        "transition_loading": False,
        "loading_retry_used": False,
        **(artifact_meta or {}),
    }


def _preflight_keyguard_failure_result(
    *,
    failure_reason: str,
    unlock_attempted: bool,
    unlock_result: str,
    d: u2.Device,
    hierarchy_xml: str,
    keyguard_state: dict[str, Any],
    account_id: str | None,
    run_id: str | None,
) -> AccountIdentityCheckResult:
    artifacts = _capture_identity_failure_artifacts(
        d,
        hierarchy_xml,
        account_id=account_id,
        run_id=run_id,
        stage=PREFLIGHT_KEYGUARD_STAGE,
    )
    return AccountIdentityCheckResult(
        ok=False,
        expected_account_username="",
        failure_reason=failure_reason,
        verification_method="preflight_keyguard_check",
        meta=_keyguard_preflight_meta(
            keyguard_state=keyguard_state,
            unlock_attempted=unlock_attempted,
            unlock_result=unlock_result,
            artifact_meta=artifacts,
        ),
    )


def ensure_preflight_device_unlocked(
    d: u2.Device,
    *,
    account_id: str | None = None,
    run_id: str | None = None,
) -> AccountIdentityCheckResult | None:
    """Preflight-only keyguard hygiene: wake + one swipe-up when safe; never bypass secure lock."""
    _wake_device_for_preflight(d)
    hierarchy = _dump_hierarchy(d)
    foreground_package = _device_foreground_package(d)
    state = classify_keyguard_from_hierarchy(hierarchy, foreground_package=foreground_package)
    if not state.get("keyguard_active"):
        return None

    log(
        "info",
        "preflight_keyguard_detected",
        account_id=account_id,
        run_id=run_id,
        foreground_package=foreground_package,
        secure_lock_required=bool(state.get("secure_lock_required")),
        swipe_only_unlock_available=bool(state.get("swipe_only_unlock_available")),
    )

    if state.get("secure_lock_required"):
        result = _preflight_keyguard_failure_result(
            failure_reason=DEVICE_LOCKED_REQUIRES_OPERATOR_REASON,
            unlock_attempted=False,
            unlock_result="secure_lock_required",
            d=d,
            hierarchy_xml=hierarchy,
            keyguard_state=state,
            account_id=account_id,
            run_id=run_id,
        )
        _log_identity_failure(
            result,
            account_id=account_id,
            run_type=_PREFLIGHT_RUN_TYPE,
            run_id=run_id,
            stage=PREFLIGHT_KEYGUARD_STAGE,
        )
        return result

    if state.get("swipe_only_unlock_available"):
        _swipe_up_keyguard_once(d)
        time.sleep(_KEYGUARD_SWIPE_SETTLE_SECONDS)
        hierarchy = _dump_hierarchy(d)
        foreground_package = _device_foreground_package(d)
        state = classify_keyguard_from_hierarchy(hierarchy, foreground_package=foreground_package)
        if not state.get("keyguard_active"):
            log(
                "info",
                "preflight_keyguard_swipe_unlock_succeeded",
                account_id=account_id,
                run_id=run_id,
                foreground_package=foreground_package,
            )
            return None

    result = _preflight_keyguard_failure_result(
        failure_reason=DEVICE_UNLOCK_FAILED_REASON,
        unlock_attempted=bool(state.get("swipe_only_unlock_available")),
        unlock_result="failed",
        d=d,
        hierarchy_xml=hierarchy,
        keyguard_state=state,
        account_id=account_id,
        run_id=run_id,
    )
    _log_identity_failure(
        result,
        account_id=account_id,
        run_type=_PREFLIGHT_RUN_TYPE,
        run_id=run_id,
        stage=PREFLIGHT_KEYGUARD_STAGE,
    )
    return result


def _identity_failure_result(
    *,
    expected_raw: str,
    expected_stable_id: str,
    failure_reason: str,
    verification_method: str,
    meta: dict[str, Any] | None = None,
    actual_raw: str = "",
    identity_evidence: str = "username_only",
) -> AccountIdentityCheckResult:
    return AccountIdentityCheckResult(
        ok=False,
        expected_account_username=expected_raw,
        actual_logged_in_username=actual_raw,
        expected_instagram_user_id=expected_stable_id,
        failure_reason=failure_reason,
        identity_evidence=identity_evidence,
        verification_method=verification_method,
        meta=dict(meta or {}),
    )


def _log_identity_failure(
    result: AccountIdentityCheckResult,
    *,
    account_id: str | None,
    run_type: str | None,
    run_id: str | None,
    stage: str,
) -> None:
    event = (
        "active_instagram_account_mismatch"
        if result.failure_reason == ACCOUNT_IDENTITY_MISMATCH_REASON
        else "account_identity_verification_failed"
    )
    log(
        "error",
        event,
        account_id=account_id,
        expected_account_username=result.expected_account_username,
        expected_instagram_user_id=result.expected_instagram_user_id,
        actual_logged_in_username=result.actual_logged_in_username,
        actual_instagram_user_id="",
        run_type=run_type,
        run_id=run_id,
        stage=stage,
        reason=result.failure_reason,
        failure_reason=result.failure_reason,
        verification_method=result.verification_method,
        screen_type=result.meta.get("screen_type"),
        detection_reason=result.meta.get("detection_reason"),
        identity_guard_stage=result.meta.get("identity_guard_stage"),
    )


def _pre_classify_screen_before_profile_open(
    d: u2.Device,
    *,
    expected_raw: str,
) -> tuple[dict[str, Any], LoginProbeOutcome, str] | AccountIdentityCheckResult:
    """Classify current UI before profile navigation; optional single loading retry."""
    hierarchy = _dump_hierarchy(d)
    signals, probe_outcome = _classify_screen_from_hierarchy(hierarchy, expected_username=expected_raw)
    loading_retry_used = False

    if signals.get("transition_loading") and str(signals.get("screen_type") or "") == "unknown":
        time.sleep(_LOADING_RETRY_SECONDS)
        loading_retry_used = True
        hierarchy = _dump_hierarchy(d)
        signals, probe_outcome = _classify_screen_from_hierarchy(hierarchy, expected_username=expected_raw)
        if signals.get("transition_loading") and str(signals.get("screen_type") or "") == "unknown":
            meta = _screen_classification_meta(
                signals,
                probe_outcome=probe_outcome,
                identity_guard_stage="pre_profile_screen_loading_retry",
                loading_retry_used=loading_retry_used,
            )
            return _identity_failure_result(
                expected_raw=expected_raw,
                expected_stable_id="",
                failure_reason="ui_transition_loading",
                verification_method="pre_profile_screen_classification",
                meta=meta,
            )

    # Preserve the already-fresh hierarchy for the structured restriction
    # boundary in the caller.  A generic screen classifier must never replace
    # explicit Instagram restriction semantics with ui_not_recognized or an
    # identity-username fallback.
    if classify_instagram_account_restriction(hierarchy).detected:
        return signals, probe_outcome, hierarchy

    blocked = _pre_profile_blocking_reason(signals, probe_outcome)
    meta = _screen_classification_meta(
        signals,
        probe_outcome=probe_outcome,
        identity_guard_stage="pre_profile_screen",
        loading_retry_used=loading_retry_used,
    )
    if blocked:
        failure_reason, detection_reason = blocked
        meta["detection_reason"] = detection_reason
        return _identity_failure_result(
            expected_raw=expected_raw,
            expected_stable_id="",
            failure_reason=failure_reason,
            verification_method="pre_profile_screen_classification",
            meta=meta,
        )

    return signals, probe_outcome, hierarchy


def _publish_identity_mismatch_incident(
    result: AccountIdentityCheckResult,
    *,
    account_id: str | None,
    run_type: str | None,
    run_id: str | None,
    stage: str,
) -> None:
    """Persist identity mismatch as an observation only; never affect safe-stop."""
    try:
        import runtime_incidents

        safe_meta = {
            "action_bar_title": result.meta.get("action_bar_title"),
            "candidate_texts": result.meta.get("candidate_texts"),
            "hierarchy_xml_len": result.meta.get("hierarchy_xml_len"),
        }
        payload = runtime_incidents.build_identity_mismatch_incident(
            account_id=account_id,
            expected_username=result.expected_account_username,
            actual_username=result.actual_logged_in_username,
            run_id=run_id,
            run_type=run_type,
            stage=stage,
            expected_instagram_user_id=result.expected_instagram_user_id,
            actual_instagram_user_id=result.actual_instagram_user_id,
            verification_method=result.verification_method,
            identity_evidence=result.identity_evidence,
            rename_disambiguation_status=result.rename_disambiguation_status,
            future_possible_failure_reason=POSSIBLE_USERNAME_RENAME_REASON,
            metadata=safe_meta,
        )
        runtime_incidents.publish_account_incident(**payload)
    except Exception as exc:
        log(
            "warning",
            "identity_mismatch_incident_publish_failed",
            account_id=account_id,
            run_type=run_type,
            run_id=run_id,
            stage=stage,
            error=str(exc)[:500],
        )


def _publish_identity_mismatch_status(
    result: AccountIdentityCheckResult,
    *,
    account_id: str | None,
    run_type: str | None,
    run_id: str | None,
    stage: str,
) -> None:
    """Publish dashboard mismatch status as best-effort; never affect safe-stop."""
    if result.failure_reason != ACCOUNT_IDENTITY_MISMATCH_REASON:
        return

    external_request_id = f"identity_guard:{run_id or 'unknown'}:{account_id or 'unknown'}:mismatch"
    metadata = {
        key: value
        for key, value in {
            "source": "account_identity_guard",
            "run_id": run_id,
            "run_type": run_type,
            "stage": stage,
            "expected_account_username": result.expected_account_username,
            "actual_username": result.actual_logged_in_username,
            "guard_reason": result.failure_reason,
            "verification_method": result.verification_method,
            "identity_evidence": result.identity_evidence,
        }.items()
        if value is not None and str(value).strip() != ""
    }

    try:
        from instagram_account_status_publisher import publish_instagram_account_status

        outcome = publish_instagram_account_status(
            account_id=str(account_id or ""),
            login_status="mismatch",
            provisioning_status="blocked",
            onboarding_status="blocked",
            reason="account_identity_mismatch",
            external_request_id=external_request_id,
            metadata=metadata,
        )
        published = bool(outcome.get("published"))
        reason = str(outcome.get("reason") or ("published" if published else "unknown"))
        status_code = outcome.get("status_code")
        if published:
            event = "identity_mismatch_status_publish_succeeded"
            level = "info"
        elif reason in {"disabled", "not_configured"}:
            event = "identity_mismatch_status_publish_skipped"
            level = "info"
        else:
            event = "identity_mismatch_status_publish_failed"
            level = "warning"
        log(
            level,
            event,
            account_id=account_id,
            run_type=run_type,
            run_id=run_id,
            stage=stage,
            published=published,
            reason=reason,
            status_code=status_code,
            external_request_id=external_request_id,
        )
    except Exception as exc:
        log(
            "warning",
            "identity_mismatch_status_publish_failed",
            account_id=account_id,
            run_type=run_type,
            run_id=run_id,
            stage=stage,
            error=str(exc)[:500],
            external_request_id=external_request_id,
        )


def verify_active_instagram_account_matches_expected(
    d: u2.Device,
    *,
    expected_account_username: str,
    expected_instagram_user_id: str | None = None,
    expected_package_name: str | None = None,
    account_id: str | None = None,
    run_type: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    device_id: str | None = None,
    expected_app_instance_id: str | None = None,
    clone: str | None = None,
    stage: str = "account_identity_preflight",
) -> AccountIdentityCheckResult:
    """Open/inspect own profile and require an exact handle match."""
    expected_raw = str(expected_account_username or "").strip()
    expected = normalize_account_username(expected_raw)
    expected_stable_id = str(expected_instagram_user_id or "").strip()
    log(
        "info",
        "account_identity_check_started",
        account_id=account_id,
        expected_account_username=expected_raw,
        expected_instagram_user_id=expected_stable_id,
        run_type=run_type,
        run_id=run_id,
        stage=stage,
    )

    if not expected:
        result = AccountIdentityCheckResult(
            ok=False,
            expected_account_username=expected_raw,
            expected_instagram_user_id=expected_stable_id,
            failure_reason="expected_account_username_missing",
            verification_method="preflight_input_validation",
        )
        _log_identity_failure(
            result,
            account_id=account_id,
            run_type=run_type,
            run_id=run_id,
            stage=stage,
        )
        return result

    post_verification_metadata: dict[str, Any] = {}

    def _account_restriction_identity_boundary(
        hierarchy: str,
    ) -> AccountIdentityCheckResult | None:
        restriction = classify_instagram_account_restriction(hierarchy)
        if not restriction.detected:
            return None
        restriction_meta = {
            **restriction.to_dict(),
            **post_verification_metadata,
            "screen_type": "instagram_account_restriction",
            "detection_reason": restriction.reason_code,
            "identity_guard_stage": "account_restriction_surface",
            "identity_proof": "unavailable_due_to_restriction_surface",
            "safety_scope": "account_global",
            "fail_closed": True,
            "business_actions_allowed": False,
            "operator_action_required": True,
            "automatic_dismiss_allowed": False,
            "hierarchy_xml_len": len(str(hierarchy or "")),
        }
        result = _identity_failure_result(
            expected_raw=expected_raw,
            expected_stable_id=expected_stable_id,
            failure_reason=restriction.reason_code,
            verification_method="instagram_account_restriction_surface",
            identity_evidence="unavailable_due_to_restriction_surface",
            meta=restriction_meta,
        )
        try:
            import runtime_incidents

            payload = runtime_incidents.build_instagram_account_restriction_incident(
                account_id=account_id,
                account_username=expected_raw,
                restriction=restriction.to_dict(),
                run_id=run_id,
                run_type=run_type,
                stage=stage,
                verification_method=result.verification_method,
            )
            runtime_incidents.publish_account_incident(**payload)
        except Exception as exc:
            log(
                "warning",
                "account_restriction_incident_publish_failed",
                account_id=account_id,
                run_id=run_id,
                reason=restriction.reason_code,
                error=str(exc)[:500],
            )
        log(
            "error",
            "instagram_account_restriction_detected",
            account_id=account_id,
            run_id=run_id,
            reason=restriction.reason_code,
            restriction_family=restriction.restriction_family,
            restriction_scope=restriction.restriction_scope,
            restriction_action=restriction.restriction_action,
            restriction_state=restriction.restriction_state,
            restriction_start_date=restriction.restriction_start_date,
            restriction_end_date=restriction.restriction_end_date,
            identity_proof="unavailable_due_to_restriction_surface",
            business_actions_allowed=False,
        )
        return result

    def _ads_data_consent_identity_boundary(hierarchy: str) -> AccountIdentityCheckResult | None:
        classification = classify_instagram_ads_data_consent_popup(
            hierarchy,
            package_name=str(
                expected_package_name
                or getattr(config, "INSTAGRAM_PACKAGE", "")
                or ""
            ),
        )
        if not classification.detected:
            return None
        actual_raw, method, extraction_meta = _extract_own_profile_username_from_hierarchy(hierarchy)
        actual = normalize_account_username(actual_raw)
        exact_profile_proof = bool(
            actual
            and actual == expected
            and method in {
                "action_bar_title",
                "profile_header_username",
                "username_resource_id",
            }
        )
        alert = publish_ads_data_consent_operator_alert(
            account_id=str(account_id or ""),
            account_username=expected_raw,
            run_id=run_id,
            request_id=request_id,
            device_id=device_id,
            app_instance_id=expected_app_instance_id,
            clone=clone,
            phase="login_identity_guard",
            preceding_action="post_auth_handoff",
            identity_pending=not exact_profile_proof,
        )
        popup_meta = {
            **extraction_meta,
            **post_verification_metadata,
            "popup_type": ADS_DATA_CONSENT_POPUP_TYPE,
            "instagram_ads_data_consent_popup_detected": True,
            "operator_action_required": True,
            "automatic_cta_click_allowed": False,
            "session_authenticated": True,
            "identity_pending_popup": not exact_profile_proof,
            "popup_incident_id": alert.get("incident_id"),
            "popup_dashboard_action_id": alert.get("dashboard_action_id"),
            "popup_detected_at": alert.get("detected_at"),
            "popup_classification": classification.to_dict(),
            "profile_opened": exact_profile_proof,
        }
        if exact_profile_proof:
            log(
                "info",
                "ads_data_consent_popup_identity_exact_match",
                account_id=account_id,
                run_id=run_id,
                expected_account_username=expected_raw,
                verification_method=method,
            )
            return AccountIdentityCheckResult(
                ok=True,
                expected_account_username=expected_raw,
                actual_logged_in_username=actual_raw,
                expected_instagram_user_id=expected_stable_id,
                identity_evidence="username_exact_match_behind_ads_data_consent_popup",
                rename_disambiguation_status="not_needed_username_matched",
                verification_method=f"own_profile_username_exact_behind_popup:{method}",
                meta=popup_meta,
            )
        return _identity_failure_result(
            expected_raw=expected_raw,
            expected_stable_id=expected_stable_id,
            failure_reason=ADS_DATA_CONSENT_IDENTITY_PENDING_REASON,
            verification_method="ads_data_consent_popup_identity_pending",
            actual_raw=actual_raw,
            meta=popup_meta,
        )

    if stage == "login_provisioning_post_login_identity" or str(run_type or "").startswith("login_"):
        completion = prepare_post_verification_identity_surface(
            d,
            expected_package_name=str(
                expected_package_name
                or getattr(config, "INSTAGRAM_PACKAGE", "")
                or ""
            ),
        )
        post_verification_metadata = {
            "post_verification_screen_type": completion.screen_type,
            "post_verification_recovery_count": completion.recovery_count,
            "post_verification_recovered_screen_types": list(completion.recovered_screen_types),
            "post_verification_fingerprint_changed": completion.fingerprint_changed,
            **completion.metadata,
        }
        popup_hierarchy = str(completion.metadata.get("observed_hierarchy") or "")
        if popup_hierarchy:
            # The completion gate has already proved that no gesture was sent.
            # Reuse its exact observation instead of performing another dump.
            post_verification_metadata.pop("observed_hierarchy", None)
            restriction_identity = _account_restriction_identity_boundary(popup_hierarchy)
            if restriction_identity is not None:
                _log_identity_failure(
                    restriction_identity,
                    account_id=account_id,
                    run_type=run_type,
                    run_id=run_id,
                    stage=stage,
                )
                return restriction_identity
            popup_identity = _ads_data_consent_identity_boundary(popup_hierarchy)
            if popup_identity is not None:
                if not popup_identity.ok:
                    _log_identity_failure(
                        popup_identity,
                        account_id=account_id,
                        run_type=run_type,
                        run_id=run_id,
                        stage=stage,
                    )
                return popup_identity
        if not completion.safe_for_identity_guard:
            result = _identity_failure_result(
                expected_raw=expected_raw,
                expected_stable_id=expected_stable_id,
                failure_reason=completion.failure_reason or "post_verification_human_assistance_required",
                verification_method="post_verification_completion_gate",
                meta={
                    **post_verification_metadata,
                    "screen_type": completion.screen_type,
                    "detection_reason": completion.failure_reason,
                    "identity_guard_stage": "post_verification_completion_gate",
                },
            )
            _log_identity_failure(
                result,
                account_id=account_id,
                run_type=run_type,
                run_id=run_id,
                stage=stage,
            )
            return result

    pre_profile = _pre_classify_screen_before_profile_open(d, expected_raw=expected_raw)
    if isinstance(pre_profile, AccountIdentityCheckResult):
        hierarchy = _dump_hierarchy(d)
        pre_profile = AccountIdentityCheckResult(
            ok=pre_profile.ok,
            expected_account_username=pre_profile.expected_account_username,
            actual_logged_in_username=pre_profile.actual_logged_in_username,
            expected_instagram_user_id=expected_stable_id,
            failure_reason=pre_profile.failure_reason,
            verification_method=pre_profile.verification_method,
            meta={
                **pre_profile.meta,
                **post_verification_metadata,
                **_capture_identity_failure_artifacts(
                    d,
                    hierarchy,
                    account_id=account_id,
                    run_id=run_id,
                    stage=stage,
                ),
            },
        )
        _log_identity_failure(
            pre_profile,
            account_id=account_id,
            run_type=run_type,
            run_id=run_id,
            stage=stage,
        )
        return pre_profile

    _, _, pre_hierarchy = pre_profile
    restriction_identity = _account_restriction_identity_boundary(pre_hierarchy)
    if restriction_identity is not None:
        _log_identity_failure(
            restriction_identity,
            account_id=account_id,
            run_type=run_type,
            run_id=run_id,
            stage=stage,
        )
        return restriction_identity
    popup_identity = _ads_data_consent_identity_boundary(pre_hierarchy)
    if popup_identity is not None:
        if not popup_identity.ok:
            _log_identity_failure(
                popup_identity,
                account_id=account_id,
                run_type=run_type,
                run_id=run_id,
                stage=stage,
            )
        return popup_identity

    if not open_own_profile_from_bottom_nav(
        d,
        expected_package_name=str(
            expected_package_name
            or getattr(config, "INSTAGRAM_PACKAGE", "")
            or ""
        ),
    ):
        hierarchy = _dump_hierarchy(d)
        result = _identity_failure_result(
            expected_raw=expected_raw,
            expected_stable_id=expected_stable_id,
            failure_reason="own_profile_open_failed",
            verification_method="open_own_profile_from_bottom_nav",
            meta={
                **_screen_classification_meta(
                    {"screen_type": "own_profile_open_failed", "detection_reason": "profile_tab_not_found"},
                    identity_guard_stage="open_own_profile_from_bottom_nav",
                ),
                **post_verification_metadata,
                **_capture_identity_failure_artifacts(
                    d,
                    hierarchy,
                    account_id=account_id,
                    run_id=run_id,
                    stage=stage,
                ),
            },
        )
        _log_identity_failure(
            result,
            account_id=account_id,
            run_type=run_type,
            run_id=run_id,
            stage=stage,
        )
        return result

    hierarchy = _dump_hierarchy(d)
    restriction_identity = _account_restriction_identity_boundary(hierarchy)
    if restriction_identity is not None:
        _log_identity_failure(
            restriction_identity,
            account_id=account_id,
            run_type=run_type,
            run_id=run_id,
            stage=stage,
        )
        return restriction_identity
    popup_identity = _ads_data_consent_identity_boundary(hierarchy)
    if popup_identity is not None:
        if not popup_identity.ok:
            _log_identity_failure(
                popup_identity,
                account_id=account_id,
                run_type=run_type,
                run_id=run_id,
                stage=stage,
            )
        return popup_identity
    actual_raw, method, meta = _extract_own_profile_username_from_hierarchy(hierarchy)
    actual = normalize_account_username(actual_raw)
    if actual and actual == expected:
        result = AccountIdentityCheckResult(
            ok=True,
            expected_account_username=expected_raw,
            actual_logged_in_username=actual_raw,
            expected_instagram_user_id=expected_stable_id,
            identity_evidence="username_exact_match",
            rename_disambiguation_status=(
                "not_needed_username_matched"
                if not expected_stable_id
                else "stable_id_not_checked_username_matched"
            ),
            verification_method=f"own_profile_username_exact:{method}",
            meta={
                **meta,
                **post_verification_metadata,
                # A successful identity result is only emitted after the
                # canonical own-profile navigation above completed. Persist
                # that boundary explicitly so downstream status writers do
                # not have to infer it from a username match alone.
                "profile_opened": True,
            },
        )
        log(
            "info",
            "account_identity_check_ok",
            account_id=account_id,
            expected_account_username=expected_raw,
            expected_instagram_user_id=expected_stable_id,
            actual_logged_in_username=actual_raw,
            actual_instagram_user_id="",
            run_type=run_type,
            run_id=run_id,
            stage=stage,
            verification_method=result.verification_method,
        )
        return result

    reason = ACCOUNT_IDENTITY_MISMATCH_REASON if actual else "actual_logged_in_username_not_detected"
    screen_meta = {
        "screen_type": "own_profile_username_read",
        "detection_reason": method,
    }
    identity_stage = "post_profile_username_read"
    if not actual and run_type == _PREFLIGHT_RUN_TYPE:
        keyguard_state = classify_keyguard_from_hierarchy(
            hierarchy,
            foreground_package=_device_foreground_package(d),
        )
        if keyguard_state.get("keyguard_active"):
            reason = (
                DEVICE_LOCKED_REQUIRES_OPERATOR_REASON
                if keyguard_state.get("secure_lock_required")
                else DEVICE_LOCKED_REASON
            )
            screen_meta = {
                "screen_type": KEYGUARD_SCREEN_TYPE,
                "detection_reason": KEYGUARD_DETECTION_REASON,
            }
            identity_stage = PREFLIGHT_KEYGUARD_STAGE
            method = "preflight_keyguard_check"
    result = AccountIdentityCheckResult(
        ok=False,
        expected_account_username=expected_raw,
        actual_logged_in_username=actual_raw,
        expected_instagram_user_id=expected_stable_id,
        failure_reason=reason,
        identity_evidence="username_mismatch_stable_id_unavailable",
        rename_disambiguation_status=(
            "stable_instagram_user_id_not_available"
            if not expected_stable_id
            else "expected_stable_id_available_but_actual_stable_id_not_detected"
        ),
        verification_method=f"own_profile_username_exact_mismatch:{method}",
        meta={
            **meta,
            **post_verification_metadata,
            **_screen_classification_meta(
                screen_meta,
                identity_guard_stage=identity_stage,
            ),
            **_capture_identity_failure_artifacts(
                d,
                hierarchy,
                account_id=account_id,
                run_id=run_id,
                stage=stage,
            ),
        },
    )
    log(
        "error",
        "active_instagram_account_mismatch",
        account_id=account_id,
        expected_account_username=expected_raw,
        expected_instagram_user_id=expected_stable_id,
        actual_logged_in_username=actual_raw,
        actual_instagram_user_id="",
        run_type=run_type,
        run_id=run_id,
        stage=stage,
        reason=ACCOUNT_IDENTITY_MISMATCH_REASON,
        failure_reason=result.failure_reason,
        identity_evidence=result.identity_evidence,
        rename_disambiguation_status=result.rename_disambiguation_status,
        future_possible_failure_reason=POSSIBLE_USERNAME_RENAME_REASON,
        verification_method=result.verification_method,
        screen_type=result.meta.get("screen_type"),
        detection_reason=result.meta.get("detection_reason"),
        identity_guard_stage=result.meta.get("identity_guard_stage"),
        meta=meta,
    )
    if result.failure_reason == ACCOUNT_IDENTITY_MISMATCH_REASON:
        _publish_identity_mismatch_incident(
            result,
            account_id=account_id,
            run_type=run_type,
            run_id=run_id,
            stage=stage,
        )
        _publish_identity_mismatch_status(
            result,
            account_id=account_id,
            run_type=run_type,
            run_id=run_id,
            stage=stage,
        )
    return result
