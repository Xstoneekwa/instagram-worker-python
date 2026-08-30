"""Shared semantic action for Instagram's post-login save-info prompt.

The prompt is security-sensitive because the neighbouring action stores login
information.  Callers must therefore prove one unique, localized ``Not now``
control and may tap it only once.  No coordinate or generic Back fallback is
allowed here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


NOT_NOW_SELECTORS: tuple[dict[str, str], ...] = (
    {"text": "Not now"},
    {"description": "Not now"},
    {"text": "Pas maintenant"},
    {"description": "Pas maintenant"},
    {"text": "Plus tard"},
    {"description": "Plus tard"},
)


@dataclass(frozen=True)
class SaveLoginInfoDismissResult:
    dismissed: bool
    failure_reason: str = ""
    matched_control_count: int = 0


def dismiss_save_login_info_prompt_once(d: Any) -> SaveLoginInfoDismissResult:
    """Tap one uniquely-proved semantic ``Not now`` control, at most once."""

    target_result = find_unique_not_now_target(d)
    target = target_result.get("target")
    failure_reason = str(target_result.get("failure_reason") or "")
    matched_count = int(target_result.get("matched_control_count") or 0)
    if target is None or failure_reason:
        return SaveLoginInfoDismissResult(
            dismissed=False,
            failure_reason=failure_reason or "not_now_button_not_found",
            matched_control_count=matched_count,
        )

    click = getattr(target, "click", None)
    if not callable(click):
        return SaveLoginInfoDismissResult(
            dismissed=False,
            failure_reason="save_login_info_not_now_tap_unavailable",
            matched_control_count=1,
        )
    try:
        click()
    except Exception:
        return SaveLoginInfoDismissResult(
            dismissed=False,
            failure_reason="save_login_info_not_now_tap_failed",
            matched_control_count=1,
        )
    return SaveLoginInfoDismissResult(dismissed=True, matched_control_count=1)


def find_unique_not_now_target(d: Any) -> dict[str, Any]:
    """Resolve selector aliases to exactly one physical actionable node."""

    matches: dict[tuple[str, str], Any] = {}
    for selector_kwargs in NOT_NOW_SELECTORS:
        try:
            selector = d(**selector_kwargs)
        except Exception:
            continue
        count = _selector_count(selector)
        if count > 1:
            return {
                "target": None,
                "failure_reason": "ambiguous_not_now_control",
                "matched_control_count": count,
            }
        if count != 1:
            continue
        key = _physical_target_key(selector)
        matches.setdefault(key, selector)

    if not matches:
        return {
            "target": None,
            "failure_reason": "not_now_button_not_found",
            "matched_control_count": 0,
        }
    if len(matches) != 1:
        return {
            "target": None,
            "failure_reason": "ambiguous_not_now_control",
            "matched_control_count": len(matches),
        }
    return {
        "target": next(iter(matches.values())),
        "failure_reason": "",
        "matched_control_count": 1,
    }


def _selector_count(selector: Any) -> int:
    count_attr = getattr(selector, "count", None)
    if callable(count_attr):
        try:
            return max(0, int(count_attr()))
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
            return 1 if bool(exists(timeout=0)) else 0
        except TypeError:
            try:
                return 1 if bool(exists()) else 0
            except Exception:
                return 0
        except Exception:
            return 0
    if exists is not None:
        return 1 if bool(exists) else 0
    return 0


def _physical_target_key(target: Any) -> tuple[str, str]:
    """Deduplicate text/description aliases that resolve to the same UI node."""

    try:
        info = getattr(target, "info", None)
        if callable(info):
            info = info()
    except Exception:
        info = None
    if isinstance(info, dict) and info:
        stable = {
            key: info.get(key)
            for key in (
                "bounds",
                "resourceName",
                "resourceId",
                "className",
                "text",
                "contentDescription",
                "packageName",
            )
            if info.get(key) not in (None, "")
        }
        if stable:
            return ("node", json.dumps(stable, sort_keys=True, separators=(",", ":")))
    return ("object", str(id(target)))
