"""Controlled executor for safe Instagram pre-login screen actions.

Entry 2E-5F applies an already-computed login screen router decision. It can
tap only the explicit Continue / Use another profile targets, then observes the
screen once. It never types credentials, reads Vault, publishes HTTP, or decides
business overrides.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from html import unescape
from typing import Any, Callable

from instagram_login_status_classifier import FORBIDDEN_METADATA_KEYS, clean_login_probe_metadata
from instagram_login_ui_probe import extract_login_screen_signals_from_hierarchy

ACTION_CONTINUE = "tap_continue"
ACTION_USE_ANOTHER_PROFILE = "tap_use_another_profile"
ACTION_SELECT_EXPECTED_ACCOUNT = "tap_expected_account"
ACTION_OPEN_PROFILE_FROM_HOME = "tap_profile_bottom_nav"
ACTION_OPEN_HOME_FROM_PROFILE = "tap_home_bottom_nav"
ACTION_OPEN_ACCOUNT_SWITCHER = "tap_account_switcher"
ACTION_ADD_INSTAGRAM_ACCOUNT = "tap_add_instagram_account"
ACTION_LOG_INTO_EXISTING_ACCOUNT = "tap_log_into_existing_account"
ACTION_ALREADY_HAVE_PROFILE = "tap_already_have_profile"
ACTION_OPEN_PROFILE_MENU = "tap_profile_menu"
ACTION_OPEN_SETTINGS_AND_ACTIVITY = "tap_settings_and_activity"
ACTION_TAP_LOGOUT = "tap_logout"
ACTION_TAP_NOT_NOW = "tap_not_now"
ACTION_CONFIRM_LOGOUT = "tap_confirm_logout"
NO_ACTION = "no_action"

ALLOWED_DECISION_ACTIONS = {
    "open_profile_from_home": ("Profile", ACTION_OPEN_PROFILE_FROM_HOME),
    "open_home_from_profile": ("Home", ACTION_OPEN_HOME_FROM_PROFILE),
    "open_account_switcher": ("", ACTION_OPEN_ACCOUNT_SWITCHER),
    "tap_add_instagram_account": ("Add Instagram account", ACTION_ADD_INSTAGRAM_ACCOUNT),
    "tap_log_into_existing_account": ("Log into existing account", ACTION_LOG_INTO_EXISTING_ACCOUNT),
    "open_existing_profile_from_join_landing": ("I already have a profile", ACTION_ALREADY_HAVE_PROFILE),
    "open_profile_menu": ("", ACTION_OPEN_PROFILE_MENU),
    "tap_settings_and_activity": ("Settings and activity", ACTION_OPEN_SETTINGS_AND_ACTIVITY),
    "tap_logout": ("Log out", ACTION_TAP_LOGOUT),
    "tap_not_now": ("Not now", ACTION_TAP_NOT_NOW),
    "tap_confirm_logout": ("Log out", ACTION_CONFIRM_LOGOUT),
    "continue_expected_account": ("Continue", ACTION_CONTINUE),
    "use_another_profile_previous_account_stopped": ("Use another profile", ACTION_USE_ANOTHER_PROFILE),
    "select_expected_account_from_picker": ("", ACTION_SELECT_EXPECTED_ACCOUNT),
}
NO_ACTION_DECISIONS = {
    "ambiguous_expected_account_row",
    "block_wrong_active_account",
    "block_wrong_suggested_account",
    "connected_expected_account",
    "expected_account_not_listed",
    "expected_username_missing",
    "recover_old_logged_in_account",
    "start_login_form_flow",
    "start_login_form_flow_prefilled_expected",
    "start_login_form_flow_replace_username",
    "unknown_no_action",
    "username_prefilled_not_editable",
}
MAX_POST_ACTION_WAIT_MS = 1500
BOUNDS_DEDUPE_DISTANCE_PX = 24
STATUS_BAR_MAX_CENTER_Y = 220
ACCOUNT_PICKER_FAILURE_AMBIGUOUS_DUPLICATE_ROWS = "account_picker_ambiguous_duplicate_username_rows"

Timer = Callable[[], float]
Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class _BoundsRect:
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def center_x(self) -> int:
        return (self.x1 + self.x2) // 2

    @property
    def center_y(self) -> int:
        return (self.y1 + self.y2) // 2

    @property
    def area(self) -> int:
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)


@dataclass(frozen=True)
class _AccessibilityCandidate:
    label: str
    source_attr: str
    bounds: _BoundsRect
    clickable: bool
    enabled: bool
    visible: bool
    class_name: str
    score: int = 0


@dataclass(frozen=True)
class LoginActionExecutionResult:
    ok: bool
    executed: bool
    action: str
    decision: str
    reason: str
    failure_reason: str = ""
    post_action_screen_type: str = "unknown"
    post_action_probe_reason: str = ""
    post_action_signals: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def execute_login_screen_decision(
    d: Any,
    decision: Any,
    *,
    post_action_wait_ms: int = 500,
    dump_after_action: bool = True,
    tap_timeout_ms: int = 1500,
    timer: Timer | None = None,
    sleeper: Sleeper | None = None,
) -> LoginActionExecutionResult:
    """Execute one controlled pre-login action and observe the result.

    The caller owns the business decision. This executor only applies supported
    router decisions and does at most one tap, with no retry by default.
    """

    timer = timer or time.perf_counter
    sleeper = sleeper or time.sleep
    decision_value = _decision_value(decision)
    timings = _empty_timings()
    warnings: list[str] = []
    total_start = timer()
    wait_ms = _clamp_ms(post_action_wait_ms, MAX_POST_ACTION_WAIT_MS)
    tap_timeout = _clamp_ms(tap_timeout_ms, MAX_POST_ACTION_WAIT_MS)

    if wait_ms != post_action_wait_ms:
        warnings.append("post_action_wait_ms_clamped")
    if tap_timeout != tap_timeout_ms:
        warnings.append("tap_timeout_ms_clamped")

    if decision_value in NO_ACTION_DECISIONS:
        timings["total_ms"] = _elapsed_ms(total_start, timer())
        return _result(
            ok=True,
            executed=False,
            action=NO_ACTION,
            decision=decision_value,
            reason="decision_requires_no_action",
            timings=timings,
            warnings=warnings,
        )

    if decision_value not in ALLOWED_DECISION_ACTIONS:
        timings["total_ms"] = _elapsed_ms(total_start, timer())
        return _result(
            ok=False,
            executed=False,
            action=NO_ACTION,
            decision=decision_value,
            reason="unsupported_decision",
            failure_reason="unsupported_decision",
            timings=timings,
            warnings=warnings,
        )

    target_text, action = ALLOWED_DECISION_ACTIONS[decision_value]
    if action in {ACTION_SELECT_EXPECTED_ACCOUNT, ACTION_OPEN_ACCOUNT_SWITCHER}:
        target_text = _decision_target_username(decision)
    start = timer()
    selector_result = (
        _find_account_picker_target(d, target_text)
        if action == ACTION_SELECT_EXPECTED_ACCOUNT
        else _find_account_switcher_target(d, target_text)
        if action == ACTION_OPEN_ACCOUNT_SWITCHER
        else _find_profile_menu_target(d)
        if action == ACTION_OPEN_PROFILE_MENU
        else _find_logout_target(d)
        if action == ACTION_TAP_LOGOUT
        else _find_first_exact_target(d, _target_aliases_for_action(action, target_text))
        if action
        in {
            ACTION_ADD_INSTAGRAM_ACCOUNT,
            ACTION_LOG_INTO_EXISTING_ACCOUNT,
            ACTION_ALREADY_HAVE_PROFILE,
            ACTION_OPEN_SETTINGS_AND_ACTIVITY,
            ACTION_TAP_NOT_NOW,
            ACTION_CONFIRM_LOGOUT,
        }
        else _find_exact_accessibility_target(d, target_text)
    )
    timings["target_lookup_ms"] = _elapsed_ms(start, timer())
    if selector_result.get("resolution"):
        warnings.append(f"target_resolution_{selector_result['resolution']}")
    action_metadata = dict(selector_result.get("metadata") or {})

    if selector_result["failure_reason"]:
        failure_reason = selector_result["failure_reason"]
        if action == ACTION_ALREADY_HAVE_PROFILE and failure_reason == "target_button_not_found":
            failure_reason = "unsupported_login_landing"
        timings["total_ms"] = _elapsed_ms(total_start, timer())
        return _result(
            ok=False,
            executed=False,
            action=action,
            decision=decision_value,
            reason=failure_reason,
            failure_reason=failure_reason,
            timings=timings,
            warnings=warnings,
            metadata=action_metadata,
        )

    try:
        start = timer()
        _click_resolved_target(d, selector_result, tap_timeout_ms=tap_timeout)
        timings["tap_ms"] = _elapsed_ms(start, timer())
    except Exception:
        timings["tap_ms"] = _elapsed_ms(start, timer())
        timings["total_ms"] = _elapsed_ms(total_start, timer())
        if action == ACTION_SELECT_EXPECTED_ACCOUNT:
            action_metadata = {
                **action_metadata,
                "account_picker_action_result": "row_tap_failed",
            }
        return _result(
            ok=False,
            executed=False,
            action=action,
            decision=decision_value,
            reason="tap_failed",
            failure_reason="tap_failed",
            timings=timings,
            warnings=warnings,
            metadata=action_metadata,
        )

    timings["post_action_wait_ms"] = wait_ms
    if wait_ms > 0:
        sleeper(wait_ms / 1000.0)

    post_action_signals: dict[str, Any] = {}
    post_action_screen_type = "unknown"
    post_action_probe_reason = "post_action_dump_skipped"
    failure_reason = ""

    if dump_after_action:
        try:
            start = timer()
            hierarchy_xml = _dump_hierarchy_once(d)
            timings["post_action_dump_ms"] = _elapsed_ms(start, timer())
            post_action_signals = _safe_login_screen_signals(hierarchy_xml)
            post_action_screen_type = str(post_action_signals.get("screen_type") or "unknown")
            post_action_probe_reason = "post_action_observed"
        except Exception:
            timings["post_action_dump_ms"] = _elapsed_ms(start, timer())
            post_action_probe_reason = "post_action_dump_failed"
            failure_reason = "post_action_dump_failed"

    if action == ACTION_USE_ANOTHER_PROFILE and post_action_screen_type == "unknown":
        warnings.append("post_action_screen_unknown")

    if action == ACTION_SELECT_EXPECTED_ACCOUNT and not failure_reason:
        action_metadata = {
            **action_metadata,
            "account_picker_action_result": "row_tap_executed",
        }
    timings["total_ms"] = _elapsed_ms(total_start, timer())
    return _result(
        ok=not failure_reason,
        executed=True,
        action=action,
        decision=decision_value,
        reason="action_executed",
        failure_reason=failure_reason,
        post_action_screen_type=post_action_screen_type,
        post_action_probe_reason=post_action_probe_reason,
        post_action_signals=post_action_signals,
        timings=timings,
        warnings=warnings,
        metadata=action_metadata,
    )


def _decision_value(decision: Any) -> str:
    return str(getattr(decision, "decision", decision) or "").strip()


def _decision_target_username(decision: Any) -> str:
    target = str(getattr(decision, "target_username", "") or "").strip()
    if target:
        return target
    return str(getattr(decision, "expected_username", "") or "").strip().lstrip("@").lower()


def _find_exact_accessibility_target(d: Any, target_text: str) -> dict[str, Any]:
    hierarchy_xml = ""
    try:
        hierarchy_xml = _dump_hierarchy_once(d)
    except Exception:
        hierarchy_xml = ""

    if hierarchy_xml:
        hierarchy_result = _resolve_target_from_hierarchy(hierarchy_xml, target_text)
        if hierarchy_result.get("target"):
            return hierarchy_result
        if hierarchy_result["failure_reason"] == "ambiguous_target_button":
            return hierarchy_result

    return _resolve_target_from_selectors(d, target_text)


def _find_first_exact_target(d: Any, target_texts: tuple[str, ...]) -> dict[str, Any]:
    last_result = {"target": None, "failure_reason": "target_button_not_found", "resolution": "alias_not_found"}
    for target_text in target_texts:
        result = _find_exact_accessibility_target(d, target_text)
        if result.get("target") or result.get("failure_reason") == "ambiguous_target_button":
            return result
        last_result = result
    return last_result


def _target_aliases_for_action(action: str, target_text: str) -> tuple[str, ...]:
    if action == ACTION_ADD_INSTAGRAM_ACCOUNT:
        return (
            "Add Instagram account",
            "Add profile",
            "Ajouter un compte Instagram",
            "Ajouter un profil",
        )
    if action == ACTION_LOG_INTO_EXISTING_ACCOUNT:
        return (
            "Log into existing account",
            "Se connecter à un compte existant",
            "Se connecter a un compte existant",
            "Ajouter un compte existant",
        )
    if action == ACTION_OPEN_SETTINGS_AND_ACTIVITY:
        return (
            "Settings and activity",
            "Paramètres et activité",
            "Parametres et activite",
        )
    if action == ACTION_TAP_LOGOUT or action == ACTION_CONFIRM_LOGOUT:
        return (
            "Log out",
            "Logout",
            "Se déconnecter",
            "Se deconnecter",
            "Déconnexion",
            "Deconnexion",
        )
    if action == ACTION_TAP_NOT_NOW:
        return (
            "Not now",
            "Pas maintenant",
            "Plus tard",
        )
    if action == ACTION_ALREADY_HAVE_PROFILE:
        return ("I already have a profile",)
    return (target_text,)


def _find_account_picker_target(d: Any, target_username: str) -> dict[str, Any]:
    normalized_target = _normalize_username(target_username)
    if not normalized_target:
        return {
            "target": None,
            "failure_reason": "target_account_row_not_found",
            "resolution": "account_picker_empty_target",
        }
    hierarchy_xml = ""
    try:
        hierarchy_xml = _dump_hierarchy_once(d)
    except Exception:
        hierarchy_xml = ""
    if hierarchy_xml:
        hierarchy_result = _resolve_account_row_from_hierarchy(hierarchy_xml, normalized_target)
        if hierarchy_result.get("target") or hierarchy_result.get("failure_reason"):
            return hierarchy_result
    return {
        "target": None,
        "failure_reason": "target_account_row_not_found",
        "resolution": "account_picker_not_found",
    }


def _find_account_switcher_target(d: Any, target_username: str) -> dict[str, Any]:
    normalized_target = _normalize_username(target_username)
    if not normalized_target:
        return {"target": None, "failure_reason": "target_button_not_found", "resolution": "account_switcher_empty_target"}
    try:
        hierarchy_xml = _dump_hierarchy_once(d)
    except Exception:
        hierarchy_xml = ""
    candidates = [
        candidate
        for candidate in _collect_label_candidates(hierarchy_xml, normalized_target)
        if candidate.enabled and candidate.visible and candidate.bounds.area > 0
    ]
    if not candidates:
        return {"target": None, "failure_reason": "target_button_not_found", "resolution": "account_switcher_not_found"}
    deduped = _dedupe_candidates_by_bounds(candidates)
    zones = _distinct_visual_zones(deduped)
    if len(zones) > 1:
        return {"target": None, "failure_reason": "ambiguous_target_button", "resolution": "account_switcher_multiple_zones"}
    winner = _choose_best_candidate(deduped)
    return {
        "target": {"kind": "bounds", "center": (winner.bounds.center_x, winner.bounds.center_y), "label": winner.label},
        "failure_reason": "",
        "resolution": "account_switcher_username_bounds_center",
    }


def _find_profile_menu_target(d: Any) -> dict[str, Any]:
    try:
        hierarchy_xml = _dump_hierarchy_once(d)
    except Exception:
        hierarchy_xml = ""
    candidates: list[_AccessibilityCandidate] = []
    for raw_attrs in re.findall(r"<node\b([^>]*)/?>", hierarchy_xml or ""):
        attrs = _parse_node_attributes(raw_attrs)
        bounds = _parse_bounds(attrs.get("bounds", ""))
        if bounds is None:
            continue
        label = _normalize_label(attrs.get("text") or attrs.get("content-desc") or attrs.get("contentDescription") or "")
        resource_id = str(attrs.get("resource-id") or "").lower()
        lowered_label = label.lower()
        looks_like_menu = (
            any(token in lowered_label for token in ("options", "menu", "settings and activity"))
            or any(
                token in resource_id
                for token in (
                    "action_bar_button",
                    "action_bar_action",
                    "overflow",
                    "hamburger",
                    "profile_menu",
                )
            )
        )
        if not looks_like_menu:
            continue
        if not (_node_is_enabled(attrs) and _node_is_visible(attrs) and _node_is_clickable(attrs)):
            continue
        # Logical profile action-bar zone: top-right controls, not an absolute tap.
        if bounds.center_y > 360 or bounds.center_x < 650:
            continue
        candidates.append(
            _AccessibilityCandidate(
                label=label or resource_id,
                source_attr="profile-menu",
                bounds=bounds,
                clickable=True,
                enabled=True,
                visible=True,
                class_name=str(attrs.get("class", "")).split(".")[-1],
            )
        )
    if not candidates:
        return {"target": None, "failure_reason": "profile_menu_not_found", "resolution": "profile_menu_not_found"}
    deduped = _dedupe_candidates_by_bounds(candidates)
    zones = _distinct_visual_zones(deduped)
    if len(zones) > 1:
        return {"target": None, "failure_reason": "ambiguous_profile_menu", "resolution": "profile_menu_multiple_zones"}
    winner = _choose_best_candidate(deduped)
    return {
        "target": {"kind": "bounds", "center": (winner.bounds.center_x, winner.bounds.center_y), "label": winner.label},
        "failure_reason": "",
        "resolution": "profile_menu_bounds_center",
    }


def _find_logout_target(d: Any) -> dict[str, Any]:
    try:
        hierarchy_xml = _dump_hierarchy_once(d)
    except Exception:
        hierarchy_xml = ""
    candidates = [
        candidate
        for candidate in _collect_accessibility_nodes(hierarchy_xml)
        if candidate.enabled
        and candidate.visible
        and candidate.bounds.area > 0
        and candidate.bounds.center_y > STATUS_BAR_MAX_CENTER_Y
        and _is_logout_action_label(candidate.label)
    ]
    if not candidates:
        return {"target": None, "failure_reason": "target_button_not_found", "resolution": "logout_not_found"}
    deduped = _dedupe_candidates_by_bounds(candidates)
    zones = _distinct_visual_zones(deduped)
    if len(zones) > 1:
        return {"target": None, "failure_reason": "ambiguous_target_button", "resolution": "logout_multiple_zones"}
    winner = _choose_best_candidate(deduped)
    return {
        "target": {
            "kind": "bounds",
            "center": (winner.bounds.center_x, winner.bounds.center_y),
            "label": winner.label,
        },
        "failure_reason": "",
        "resolution": "logout_hierarchy_bounds_center",
        "metadata": {
            "logout_button_target_text": winner.label,
            "logout_button_target_method": "logout_hierarchy_bounds_center",
        },
    }


def _resolve_target_from_hierarchy(hierarchy_xml: str, target_text: str) -> dict[str, Any]:
    normalized_target = _normalize_label(target_text)
    if not normalized_target:
        return {"target": None, "failure_reason": "target_button_not_found", "resolution": "hierarchy_empty_label"}

    anchors = {
        "Continue": _find_anchor_center_y(hierarchy_xml, "Continue"),
        "Create new account": _find_anchor_center_y(hierarchy_xml, "Create new account"),
    }
    raw_candidates = _collect_label_candidates(hierarchy_xml, normalized_target)
    filtered = [
        candidate
        for candidate in raw_candidates
        if candidate.enabled
        and candidate.visible
        and candidate.bounds.area > 0
        and candidate.bounds.center_y > STATUS_BAR_MAX_CENTER_Y
        and _candidate_in_vertical_zone(candidate, anchors)
    ]
    if not filtered:
        return {"target": None, "failure_reason": "target_button_not_found", "resolution": "hierarchy_no_candidate"}

    deduped = _dedupe_candidates_by_bounds(_collapse_contained_label_candidates(filtered))
    if not deduped:
        return {"target": None, "failure_reason": "target_button_not_found", "resolution": "hierarchy_deduped_empty"}

    zones = _distinct_visual_zones(deduped)
    if len(zones) > 1:
        return {"target": None, "failure_reason": "ambiguous_target_button", "resolution": "hierarchy_multiple_zones"}

    winner = _choose_best_candidate(deduped)
    return {
        "target": {
            "kind": "bounds",
            "center": (winner.bounds.center_x, winner.bounds.center_y),
            "label": winner.label,
        },
        "failure_reason": "",
        "resolution": "hierarchy_bounds_center",
    }


def _resolve_target_from_selectors(d: Any, target_text: str) -> dict[str, Any]:
    for selector_kwargs in ({"text": target_text}, {"description": target_text}):
        try:
            selector = d(**selector_kwargs)
        except Exception:
            continue
        count = _selector_count(selector)
        if count == 1:
            return {
                "target": {"kind": "selector", "selector": selector},
                "failure_reason": "",
                "resolution": f"selector_{next(iter(selector_kwargs))}",
            }
        if count > 1:
            return {
                "target": None,
                "failure_reason": "ambiguous_target_button",
                "resolution": f"selector_{next(iter(selector_kwargs))}_ambiguous",
            }
    return {"target": None, "failure_reason": "target_button_not_found", "resolution": "selector_not_found"}


def _resolve_account_row_from_hierarchy(hierarchy_xml: str, normalized_target: str) -> dict[str, Any]:
    nodes = _collect_accessibility_nodes(hierarchy_xml)
    anchors = {
        "Use another profile": _find_anchor_center_y(hierarchy_xml, "Use another profile"),
        "Create new account": _find_anchor_center_y(hierarchy_xml, "Create new account"),
    }
    username_candidates = [
        candidate
        for candidate in _collect_label_candidates(hierarchy_xml, normalized_target)
        if candidate.enabled
        and candidate.visible
        and candidate.bounds.area > 0
        and candidate.bounds.center_y > STATUS_BAR_MAX_CENTER_Y
        and _normalize_username(candidate.label) == normalized_target
        and _candidate_in_account_picker_zone(candidate, anchors)
    ]
    visible_usernames = _list_visible_account_picker_usernames(hierarchy_xml, anchors)
    base_metadata = {
        "account_picker_visible_usernames_count": len(visible_usernames),
        "account_picker_target_node_count": len(username_candidates),
    }
    if not username_candidates:
        return {
            "target": None,
            "failure_reason": "target_account_row_not_found",
            "resolution": "account_picker_no_username_candidate",
            "metadata": {
                **base_metadata,
                "account_picker_target_resolution_method": "account_picker_no_username_candidate",
                "account_picker_target_row_count": 0,
                "account_picker_action_result": "expected_username_not_visible",
            },
        }
    username_rows = _cluster_username_candidates_into_rows(username_candidates)
    base_metadata["account_picker_target_row_count"] = len(username_rows)
    if len(username_rows) > 1:
        return {
            "target": None,
            "failure_reason": ACCOUNT_PICKER_FAILURE_AMBIGUOUS_DUPLICATE_ROWS,
            "resolution": "account_picker_duplicate_username_rows",
            "metadata": {
                **base_metadata,
                "account_picker_target_resolution_method": "account_picker_duplicate_username_rows",
                "account_picker_action_result": "duplicate_username_rows",
                "account_picker_selected_row_index_if_known": _account_picker_row_index(
                    visible_usernames, normalized_target
                ),
            },
        }
    row_nodes = username_rows[0]
    username_candidate = _choose_best_candidate(row_nodes)
    row_bounds = _union_bounds([node.bounds for node in row_nodes])
    row_candidate = _find_clickable_container_for_candidate(nodes, username_candidate)
    if row_candidate is None:
        row_center_x, row_center_y = row_bounds.center_x, row_bounds.center_y
        for node in nodes:
            if not node.enabled or not node.visible or not node.clickable:
                continue
            if node.bounds.area <= row_bounds.area:
                continue
            if not _bounds_contain(node.bounds, row_center_x, row_center_y):
                continue
            if _bounds_vertical_overlap(node.bounds, row_bounds):
                row_candidate = node
                break
    tap_bounds = row_candidate.bounds if row_candidate else row_bounds
    resolution_method = (
        "account_picker_row_container_bounds_center"
        if row_candidate
        else "account_picker_username_row_union_bounds_center"
    )
    return {
        "target": {
            "kind": "bounds",
            "center": (tap_bounds.center_x, tap_bounds.center_y),
            "label": username_candidate.label,
        },
        "failure_reason": "",
        "resolution": resolution_method,
        "metadata": {
            **base_metadata,
            "account_picker_target_resolution_method": resolution_method,
            "account_picker_action_result": "row_tap_ready",
            "account_picker_selected_row_index_if_known": _account_picker_row_index(
                visible_usernames, normalized_target
            ),
        },
    }


def _click_resolved_target(d: Any, selector_result: dict[str, Any], *, tap_timeout_ms: int) -> None:
    target = selector_result.get("target") or {}
    kind = str(target.get("kind") or "")
    if kind == "selector":
        _click_target(target["selector"], tap_timeout_ms=tap_timeout_ms)
        return
    if kind == "bounds":
        center = target.get("center") or ()
        if len(center) != 2:
            raise RuntimeError("bounds_center_invalid")
        click = getattr(d, "click", None)
        if not callable(click):
            raise RuntimeError("bounds_tap_unavailable")
        try:
            click(int(center[0]), int(center[1]))
        except TypeError:
            click(x=int(center[0]), y=int(center[1]))
        return
    raise RuntimeError("target_click_unavailable")


def _normalize_label(value: Any) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or "")).strip())


def _normalize_username(value: Any) -> str:
    username = str(value or "").strip().lstrip("@").lower()
    return username if re.fullmatch(r"[a-z0-9._]{1,30}", username) else ""


def _is_logout_action_label(value: Any) -> bool:
    label = _normalize_label(value).lower()
    if label in {
        "log out",
        "logout",
        "se déconnecter",
        "se deconnecter",
        "déconnexion",
        "deconnexion",
    }:
        return True
    if label.startswith("log out of ") and "your account" not in label and "?" not in label:
        return True
    if label.startswith("se déconnecter de ") or label.startswith("se deconnecter de "):
        return "?" not in label
    return False


def _parse_bounds(raw: str) -> _BoundsRect | None:
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", str(raw or "").strip())
    if not match:
        return None
    x1, y1, x2, y2 = (int(match.group(index)) for index in range(1, 5))
    if x2 <= x1 or y2 <= y1:
        return None
    return _BoundsRect(x1=x1, y1=y1, x2=x2, y2=y2)


def _parse_node_attributes(raw_attrs: str) -> dict[str, str]:
    return dict(re.findall(r'([\w-]+)="([^"]*)"', raw_attrs))


def _node_is_visible(attrs: dict[str, str]) -> bool:
    visible = attrs.get("visible-to-user", attrs.get("visible", "true"))
    return str(visible).lower() != "false"


def _node_is_enabled(attrs: dict[str, str]) -> bool:
    enabled = attrs.get("enabled", "true")
    return str(enabled).lower() != "false"


def _node_is_clickable(attrs: dict[str, str]) -> bool:
    clickable = attrs.get("clickable", "false")
    return str(clickable).lower() == "true"


def _collect_label_candidates(hierarchy_xml: str, normalized_target: str) -> list[_AccessibilityCandidate]:
    candidates: list[_AccessibilityCandidate] = []
    for raw_attrs in re.findall(r"<node\b([^>]*)/?>", hierarchy_xml or ""):
        attrs = _parse_node_attributes(raw_attrs)
        text = _normalize_label(attrs.get("text", ""))
        content_desc = _normalize_label(attrs.get("content-desc", ""))
        for label, source_attr in ((text, "text"), (content_desc, "content-desc")):
            if label != normalized_target:
                continue
            bounds = _parse_bounds(attrs.get("bounds", ""))
            if bounds is None:
                continue
            candidates.append(
                _AccessibilityCandidate(
                    label=label,
                    source_attr=source_attr,
                    bounds=bounds,
                    clickable=_node_is_clickable(attrs),
                    enabled=_node_is_enabled(attrs),
                    visible=_node_is_visible(attrs),
                    class_name=str(attrs.get("class", "")).split(".")[-1],
                )
            )
    return candidates


def _collect_accessibility_nodes(hierarchy_xml: str) -> list[_AccessibilityCandidate]:
    candidates: list[_AccessibilityCandidate] = []
    for raw_attrs in re.findall(r"<node\b([^>]*)/?>", hierarchy_xml or ""):
        attrs = _parse_node_attributes(raw_attrs)
        bounds = _parse_bounds(attrs.get("bounds", ""))
        if bounds is None:
            continue
        label = _normalize_label(attrs.get("text") or attrs.get("content-desc") or attrs.get("contentDescription") or "")
        candidates.append(
            _AccessibilityCandidate(
                label=label,
                source_attr="node",
                bounds=bounds,
                clickable=_node_is_clickable(attrs),
                enabled=_node_is_enabled(attrs),
                visible=_node_is_visible(attrs),
                class_name=str(attrs.get("class", "")).split(".")[-1],
            )
        )
    return candidates


def _find_clickable_container_for_candidate(
    nodes: list[_AccessibilityCandidate],
    candidate: _AccessibilityCandidate,
) -> _AccessibilityCandidate | None:
    containers = [
        node
        for node in nodes
        if node.enabled
        and node.visible
        and node.clickable
        and node.bounds.area > candidate.bounds.area
        and _bounds_contain(node.bounds, candidate.bounds.center_x, candidate.bounds.center_y)
    ]
    if not containers:
        return None
    return sorted(containers, key=lambda node: node.bounds.area)[0]


def _bounds_contain(bounds: _BoundsRect, x: int, y: int) -> bool:
    return bounds.x1 <= x <= bounds.x2 and bounds.y1 <= y <= bounds.y2


def _find_anchor_center_y(hierarchy_xml: str, label: str) -> int | None:
    normalized_label = _normalize_label(label)
    for raw_attrs in re.findall(r"<node\b([^>]*)/?>", hierarchy_xml or ""):
        attrs = _parse_node_attributes(raw_attrs)
        text = _normalize_label(attrs.get("text", ""))
        content_desc = _normalize_label(attrs.get("content-desc", ""))
        if text != normalized_label and content_desc != normalized_label:
            continue
        bounds = _parse_bounds(attrs.get("bounds", ""))
        if bounds is None or not _node_is_visible(attrs):
            continue
        return bounds.center_y
    return None


def _candidate_in_vertical_zone(candidate: _AccessibilityCandidate, anchors: dict[str, int | None]) -> bool:
    continue_y = anchors.get("Continue")
    create_y = anchors.get("Create new account")
    center_y = candidate.bounds.center_y
    if continue_y is not None and center_y <= continue_y:
        return False
    if create_y is not None and center_y >= create_y:
        return False
    return True


def _candidate_in_account_picker_zone(candidate: _AccessibilityCandidate, anchors: dict[str, int | None]) -> bool:
    use_another_y = anchors.get("Use another profile")
    create_y = anchors.get("Create new account")
    center_y = candidate.bounds.center_y
    if use_another_y is not None and center_y >= use_another_y:
        return False
    if create_y is not None and center_y >= create_y:
        return False
    return True


def _bounds_vertical_overlap(left: _BoundsRect, right: _BoundsRect) -> bool:
    return not (left.y2 < right.y1 or right.y2 < left.y1)


def _union_bounds(bounds_list: list[_BoundsRect]) -> _BoundsRect:
    return _BoundsRect(
        x1=min(bounds.x1 for bounds in bounds_list),
        y1=min(bounds.y1 for bounds in bounds_list),
        x2=max(bounds.x2 for bounds in bounds_list),
        y2=max(bounds.y2 for bounds in bounds_list),
    )


def _cluster_username_candidates_into_rows(
    candidates: list[_AccessibilityCandidate],
) -> list[list[_AccessibilityCandidate]]:
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda candidate: candidate.bounds.center_y)
    rows: list[list[_AccessibilityCandidate]] = []
    for candidate in ordered:
        placed = False
        for row in rows:
            if any(_bounds_vertical_overlap(candidate.bounds, other.bounds) for other in row):
                row.append(candidate)
                placed = True
                break
        if not placed:
            rows.append([candidate])
    return rows


def _list_visible_account_picker_usernames(
    hierarchy_xml: str,
    anchors: dict[str, int | None],
) -> list[str]:
    row_candidates: list[_AccessibilityCandidate] = []
    for raw_attrs in re.findall(r"<node\b([^>]*)/?>", hierarchy_xml or ""):
        attrs = _parse_node_attributes(raw_attrs)
        text = _normalize_label(attrs.get("text", ""))
        content_desc = _normalize_label(attrs.get("content-desc", ""))
        for label, source_attr in ((text, "text"), (content_desc, "content-desc")):
            normalized = _normalize_username(label)
            if not normalized:
                continue
            bounds = _parse_bounds(attrs.get("bounds", ""))
            if bounds is None:
                continue
            candidate = _AccessibilityCandidate(
                label=label,
                source_attr=source_attr,
                bounds=bounds,
                clickable=_node_is_clickable(attrs),
                enabled=_node_is_enabled(attrs),
                visible=_node_is_visible(attrs),
                class_name=str(attrs.get("class", "")).split(".")[-1],
            )
            if not (
                candidate.enabled
                and candidate.visible
                and candidate.bounds.area > 0
                and candidate.bounds.center_y > STATUS_BAR_MAX_CENTER_Y
                and _candidate_in_account_picker_zone(candidate, anchors)
            ):
                continue
            row_candidates.append(candidate)
    rows = _cluster_username_candidates_into_rows(row_candidates)
    usernames: list[str] = []
    for row in sorted(rows, key=lambda nodes: min(node.bounds.center_y for node in nodes)):
        winner = _choose_best_candidate(row)
        normalized = _normalize_username(winner.label)
        if normalized and normalized not in usernames:
            usernames.append(normalized)
    return usernames


def _account_picker_row_index(visible_usernames: list[str], normalized_target: str) -> int | None:
    try:
        return visible_usernames.index(normalized_target)
    except ValueError:
        return None


def _dedupe_candidates_by_bounds(candidates: list[_AccessibilityCandidate]) -> list[_AccessibilityCandidate]:
    grouped: list[list[_AccessibilityCandidate]] = []
    for candidate in candidates:
        placed = False
        for group in grouped:
            if _bounds_are_near(group[0].bounds, candidate.bounds):
                group.append(candidate)
                placed = True
                break
        if not placed:
            grouped.append([candidate])
    return [_choose_best_candidate(group) for group in grouped]


def _collapse_contained_label_candidates(candidates: list[_AccessibilityCandidate]) -> list[_AccessibilityCandidate]:
    clickable_containers = [candidate for candidate in candidates if candidate.clickable]
    collapsed: list[_AccessibilityCandidate] = []
    for candidate in candidates:
        if (
            not candidate.clickable
            and any(
                container.label == candidate.label
                and container.bounds.area > candidate.bounds.area
                and _bounds_contain(container.bounds, candidate.bounds.center_x, candidate.bounds.center_y)
                for container in clickable_containers
            )
        ):
            continue
        collapsed.append(candidate)
    return collapsed


def _bounds_are_near(left: _BoundsRect, right: _BoundsRect) -> bool:
    return (
        abs(left.center_x - right.center_x) <= BOUNDS_DEDUPE_DISTANCE_PX
        and abs(left.center_y - right.center_y) <= BOUNDS_DEDUPE_DISTANCE_PX
    )


def _distinct_visual_zones(candidates: list[_AccessibilityCandidate]) -> list[_AccessibilityCandidate]:
    zones: list[_AccessibilityCandidate] = []
    for candidate in candidates:
        if not any(_bounds_are_near(zone.bounds, candidate.bounds) for zone in zones):
            zones.append(candidate)
    return zones


def _choose_best_candidate(candidates: list[_AccessibilityCandidate]) -> _AccessibilityCandidate:
    def sort_key(candidate: _AccessibilityCandidate) -> tuple[int, int, int]:
        clickable_rank = 0 if candidate.clickable else 1
        text_rank = 0 if candidate.source_attr == "text" else 1
        return (clickable_rank, text_rank, candidate.bounds.area)

    return sorted(candidates, key=sort_key)[0]


def _selector_count(selector: Any) -> int:
    count_attr = getattr(selector, "count", None)
    if callable(count_attr):
        try:
            return max(0, int(count_attr))
        except TypeError:
            try:
                return max(0, int(count_attr()))
            except Exception:
                return 0
        except Exception:
            return 0
    if count_attr is not None:
        try:
            return max(0, int(count_attr))
        except Exception:
            return 0

    exists = getattr(selector, "exists", None)
    if callable(exists):
        try:
            return 1 if bool(exists()) else 0
        except Exception:
            return 0
    if exists is not None:
        return 1 if bool(exists) else 0
    return 0


def _click_target(target: Any, *, tap_timeout_ms: int) -> None:
    click = getattr(target, "click", None)
    if not callable(click):
        raise RuntimeError("target_click_unavailable")
    try:
        click(timeout=tap_timeout_ms / 1000.0)
    except TypeError:
        click()


def _dump_hierarchy_once(d: Any) -> str:
    try:
        return str(d.dump_hierarchy(compressed=False) or "")
    except TypeError:
        return str(d.dump_hierarchy() or "")


def _safe_login_screen_signals(hierarchy_xml: str | None) -> dict[str, Any]:
    signals = extract_login_screen_signals_from_hierarchy(hierarchy_xml)
    # The executor exposes the resulting screen shape, not credential-field
    # details or raw hierarchy content.
    signals.pop("has_password_field", None)
    signals.pop("continue_password_only", None)
    signals.pop("password_required", None)
    signals.pop("password_required_dialog_present", None)
    signals.pop("password_field_editable_present", None)
    signals.pop("password_field_present", None)
    signals.pop("forgot_password_present", None)
    signals.pop("ready_for_password_submit", None)
    signals.pop("save_password_prompt_present", None)
    signals.pop("google_password_manager_save_prompt", None)
    signals.pop("save_password_prompt", None)
    signals.pop("overlay_type", None)
    if signals.get("screen_type") == "continue_password_only":
        signals["screen_type"] = "login_form_ready"
    signals["available_usernames"] = [
        username
        for username in (_safe_signal_text(username) for username in list(signals.get("available_usernames") or []))
        if username
    ]
    signals["suggested_username"] = _safe_signal_text(signals.get("suggested_username"))
    return clean_login_probe_metadata(dict(signals))


def _safe_signal_text(value: Any) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if not text:
        return ""
    if any(forbidden in lowered for forbidden in FORBIDDEN_METADATA_KEYS):
        return ""
    return text


def _empty_timings() -> dict[str, int]:
    return {
        "target_lookup_ms": 0,
        "tap_ms": 0,
        "post_action_wait_ms": 0,
        "post_action_dump_ms": 0,
        "total_ms": 0,
    }


def _clamp_ms(value: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 0
    return min(maximum, max(0, parsed))


def _elapsed_ms(start: float, end: float) -> int:
    return max(0, int(round((end - start) * 1000)))


def _result(
    *,
    ok: bool,
    executed: bool,
    action: str,
    decision: str,
    reason: str,
    failure_reason: str = "",
    post_action_screen_type: str = "unknown",
    post_action_probe_reason: str = "",
    post_action_signals: dict[str, Any] | None = None,
    timings: dict[str, int] | None = None,
    warnings: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> LoginActionExecutionResult:
    metadata = clean_login_probe_metadata(
        {
            "source": "login_action_executor",
            "decision": decision,
            "action": action,
            "reason": reason,
            "failure_reason": failure_reason,
            "post_action_screen_type": post_action_screen_type,
            **dict(metadata or {}),
        }
    )
    return LoginActionExecutionResult(
        ok=ok,
        executed=executed,
        action=action,
        decision=decision,
        reason=reason,
        failure_reason=failure_reason,
        post_action_screen_type=post_action_screen_type,
        post_action_probe_reason=post_action_probe_reason,
        post_action_signals=post_action_signals or {},
        timings=timings or _empty_timings(),
        warnings=list(warnings or []),
        metadata=metadata,
    )
