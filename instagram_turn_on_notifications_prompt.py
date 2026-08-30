"""Fail-closed semantic action for Instagram's notification onboarding prompt."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any


NOTIFICATION_ACTION_LABELS = frozenset(
    {
        "next",
        "skip",
        "not now",
        "suivant",
        "ignorer",
        "pas maintenant",
    }
)


@dataclass(frozen=True)
class CanonicalNotificationActionTarget:
    path: tuple[int, ...]
    class_name: str
    text: str
    content_description: str
    bounds: tuple[int, int, int, int]


@dataclass(frozen=True)
class NotificationPromptActionResult:
    acted: bool
    failure_reason: str = ""
    raw_semantic_match_count: int = 0
    canonical_target_count: int = 0


def activate_notification_prompt_action_once(
    d: Any,
    *,
    hierarchy_xml: str,
) -> NotificationPromptActionResult:
    """Click one uniquely proved structural CTA, at most once."""

    resolved = find_unique_notification_action_target(d, hierarchy_xml=hierarchy_xml)
    target = resolved.get("target")
    failure_reason = str(resolved.get("failure_reason") or "")
    raw_count = int(resolved.get("raw_semantic_match_count") or 0)
    canonical_count = int(resolved.get("canonical_target_count") or 0)
    if target is None or failure_reason:
        return NotificationPromptActionResult(
            acted=False,
            failure_reason=failure_reason or "notification_action_not_found",
            raw_semantic_match_count=raw_count,
            canonical_target_count=canonical_count,
        )
    click = getattr(target, "click", None)
    if not callable(click):
        return NotificationPromptActionResult(
            acted=False,
            failure_reason="notification_action_tap_unavailable",
            raw_semantic_match_count=raw_count,
            canonical_target_count=canonical_count,
        )
    try:
        click()
    except Exception:
        return NotificationPromptActionResult(
            acted=False,
            failure_reason="notification_action_tap_failed",
            raw_semantic_match_count=raw_count,
            canonical_target_count=canonical_count,
        )
    return NotificationPromptActionResult(
        acted=True,
        raw_semantic_match_count=raw_count,
        canonical_target_count=canonical_count,
    )


def find_unique_notification_action_target(d: Any, *, hierarchy_xml: str) -> dict[str, Any]:
    canonical = canonicalize_notification_action_targets(hierarchy_xml)
    raw_count = int(canonical["raw_semantic_match_count"])
    targets = list(canonical["targets"])
    canonical_count = len(targets)
    common = {
        "raw_semantic_match_count": raw_count,
        "canonical_target_count": canonical_count,
    }
    if canonical.get("parse_failed"):
        return {**common, "target": None, "failure_reason": "notification_hierarchy_unreadable"}
    if canonical_count == 0:
        return {**common, "target": None, "failure_reason": "notification_action_not_found"}
    if canonical_count != 1:
        return {**common, "target": None, "failure_reason": "ambiguous_notification_action"}
    selector = _resolve_unique_live_selector(d, targets[0])
    if selector is None:
        return {
            **common,
            "target": None,
            "failure_reason": "notification_canonical_target_unaddressable",
        }
    return {**common, "target": selector, "failure_reason": ""}


def canonicalize_notification_action_targets(hierarchy_xml: str) -> dict[str, Any]:
    raw = str(hierarchy_xml or "").strip()
    if not raw:
        return {"raw_semantic_match_count": 0, "targets": (), "parse_failed": True}
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        try:
            root = ET.fromstring(f"<wrap>{raw}</wrap>")
        except ET.ParseError:
            return {"raw_semantic_match_count": 0, "targets": (), "parse_failed": True}

    parents: dict[ET.Element, ET.Element] = {}
    paths: dict[ET.Element, tuple[int, ...]] = {root: ()}
    stack = [root]
    while stack:
        parent = stack.pop()
        children = list(parent)
        for index, child in enumerate(children):
            parents[child] = parent
            paths[child] = (*paths[parent], index)
        stack.extend(reversed(children))

    raw_matches = 0
    canonical: dict[tuple[int, ...], CanonicalNotificationActionTarget] = {}
    for node in root.iter():
        if not _is_notification_action_semantic_node(node):
            continue
        raw_matches += 1
        action = _nearest_enabled_clickable_ancestor(node, parents)
        if action is None:
            continue
        bounds = _parse_bounds(action.get("bounds"))
        if bounds is None:
            continue
        target = CanonicalNotificationActionTarget(
            path=paths[action],
            class_name=str(action.get("class") or ""),
            text=str(action.get("text") or "").strip(),
            content_description=str(action.get("content-desc") or "").strip(),
            bounds=bounds,
        )
        canonical.setdefault(target.path, target)
    return {
        "raw_semantic_match_count": raw_matches,
        "targets": tuple(canonical.values()),
        "parse_failed": False,
    }


def _is_notification_action_semantic_node(node: ET.Element) -> bool:
    return bool(
        {
            _normalize_label(node.get("text")),
            _normalize_label(node.get("content-desc")),
        }
        & NOTIFICATION_ACTION_LABELS
    )


def _nearest_enabled_clickable_ancestor(
    node: ET.Element,
    parents: dict[ET.Element, ET.Element],
) -> ET.Element | None:
    current: ET.Element | None = node
    while current is not None:
        if (
            str(current.get("clickable") or "").casefold() == "true"
            and str(current.get("enabled") or "true").casefold() == "true"
            and _parse_bounds(current.get("bounds")) is not None
        ):
            return current
        current = parents.get(current)
    return None


def _resolve_unique_live_selector(d: Any, target: CanonicalNotificationActionTarget) -> Any | None:
    structural: dict[str, Any] = {"clickable": True, "enabled": True}
    if target.class_name:
        structural["className"] = target.class_name
    candidates: list[dict[str, Any]] = []
    if _normalize_label(target.content_description) in NOTIFICATION_ACTION_LABELS:
        candidates.append({"description": target.content_description, **structural})
    if _normalize_label(target.text) in NOTIFICATION_ACTION_LABELS:
        candidates.append({"text": target.text, **structural})
    for kwargs in candidates:
        try:
            selector = d(**kwargs)
        except Exception:
            continue
        if _selector_count(selector) != 1:
            continue
        if _selector_matches_target(selector, target):
            return selector
    return None


def _selector_matches_target(selector: Any, target: CanonicalNotificationActionTarget) -> bool:
    try:
        info = getattr(selector, "info", None)
        if callable(info):
            info = info()
    except Exception:
        return False
    if not isinstance(info, dict):
        return False
    bounds = info.get("bounds")
    if isinstance(bounds, dict):
        try:
            live_bounds = (
                int(bounds["left"]),
                int(bounds["top"]),
                int(bounds["right"]),
                int(bounds["bottom"]),
            )
        except (KeyError, TypeError, ValueError):
            return False
    else:
        live_bounds = _parse_bounds(str(bounds or ""))
    if live_bounds != target.bounds:
        return False
    live_class = str(info.get("className") or info.get("class") or "")
    if target.class_name and live_class and live_class != target.class_name:
        return False
    if info.get("clickable") is not True or info.get("enabled") is not True:
        return False
    return True


def _selector_count(selector: Any) -> int:
    count = getattr(selector, "count", None)
    try:
        return max(0, int(count() if callable(count) else count))
    except (TypeError, ValueError):
        return 0


def _normalize_label(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _parse_bounds(value: Any) -> tuple[int, int, int, int] | None:
    match = re.fullmatch(
        r"\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]",
        str(value or "").strip(),
    )
    if not match:
        return None
    left, top, right, bottom = (int(item) for item in match.groups())
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom
