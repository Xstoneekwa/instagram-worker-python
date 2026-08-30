"""Shared semantic action for Instagram's post-login save-info prompt.

Instagram may expose one CTA twice in accessibility: an actionable parent and
one or more semantic descendants. The resolver consumes the caller's already
fresh hierarchy, maps every semantic match to its nearest enabled clickable
ancestor, and deduplicates by that ancestor's exact XML path. Distinct
actionable ancestors therefore remain ambiguous and fail closed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any


NOT_NOW_LABELS = frozenset({"not now", "pas maintenant", "plus tard"})


@dataclass(frozen=True)
class CanonicalSemanticActionTarget:
    path: tuple[int, ...]
    class_name: str
    text: str
    content_description: str
    resource_id: str
    bounds: tuple[int, int, int, int]


@dataclass(frozen=True)
class SaveLoginInfoDismissResult:
    dismissed: bool
    failure_reason: str = ""
    matched_control_count: int = 0
    raw_semantic_match_count: int = 0
    canonical_target_count: int = 0


def dismiss_save_login_info_prompt_once(
    d: Any,
    *,
    hierarchy_xml: str,
) -> SaveLoginInfoDismissResult:
    """Tap one uniquely-proved semantic ``Not now`` action, at most once."""

    target_result = find_unique_not_now_target(d, hierarchy_xml=hierarchy_xml)
    target = target_result.get("target")
    failure_reason = str(target_result.get("failure_reason") or "")
    raw_count = int(target_result.get("raw_semantic_match_count") or 0)
    canonical_count = int(target_result.get("canonical_target_count") or 0)
    if target is None or failure_reason:
        return SaveLoginInfoDismissResult(
            dismissed=False,
            failure_reason=failure_reason or "not_now_button_not_found",
            matched_control_count=canonical_count,
            raw_semantic_match_count=raw_count,
            canonical_target_count=canonical_count,
        )

    click = getattr(target, "click", None)
    if not callable(click):
        return SaveLoginInfoDismissResult(
            dismissed=False,
            failure_reason="save_login_info_not_now_tap_unavailable",
            matched_control_count=1,
            raw_semantic_match_count=raw_count,
            canonical_target_count=1,
        )
    try:
        click()
    except Exception:
        return SaveLoginInfoDismissResult(
            dismissed=False,
            failure_reason="save_login_info_not_now_tap_failed",
            matched_control_count=1,
            raw_semantic_match_count=raw_count,
            canonical_target_count=1,
        )
    return SaveLoginInfoDismissResult(
        dismissed=True,
        matched_control_count=1,
        raw_semantic_match_count=raw_count,
        canonical_target_count=1,
    )


def find_unique_not_now_target(d: Any, *, hierarchy_xml: str) -> dict[str, Any]:
    """Resolve semantic matches to one exact actionable ancestor or fail closed."""

    canonical = canonicalize_semantic_action_targets(hierarchy_xml)
    raw_count = int(canonical["raw_semantic_match_count"])
    targets = list(canonical["targets"])
    canonical_count = len(targets)
    common = {
        "raw_semantic_match_count": raw_count,
        "canonical_target_count": canonical_count,
        "matched_control_count": canonical_count,
    }
    if canonical.get("parse_failed"):
        return {**common, "target": None, "failure_reason": "not_now_hierarchy_unreadable"}
    if canonical_count == 0:
        return {**common, "target": None, "failure_reason": "not_now_button_not_found"}
    if canonical_count != 1:
        return {**common, "target": None, "failure_reason": "ambiguous_not_now_control"}

    selector = _resolve_unique_live_selector(d, targets[0])
    if selector is None:
        return {
            **common,
            "target": None,
            "failure_reason": "not_now_canonical_target_unaddressable",
        }
    return {**common, "target": selector, "failure_reason": ""}


def canonicalize_semantic_action_targets(hierarchy_xml: str) -> dict[str, Any]:
    """Collapse semantic descendants onto their nearest actionable ancestor.

    Node path, rather than label or geometry alone, is the canonical identity.
    This preserves real ambiguity when two separate buttons share a label.
    """

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
        parent_path = paths[parent]
        children = list(parent)
        for index, child in enumerate(children):
            parents[child] = parent
            paths[child] = (*parent_path, index)
        stack.extend(reversed(children))

    raw_matches = 0
    canonical: dict[tuple[int, ...], CanonicalSemanticActionTarget] = {}
    for node in root.iter():
        if not _is_not_now_semantic_node(node):
            continue
        raw_matches += 1
        action = _nearest_enabled_clickable_ancestor(node, parents)
        if action is None:
            continue
        target = _target_from_node(action, paths[action])
        if target is not None:
            canonical.setdefault(target.path, target)
    return {
        "raw_semantic_match_count": raw_matches,
        "targets": tuple(canonical.values()),
        "parse_failed": False,
    }


def _is_not_now_semantic_node(node: ET.Element) -> bool:
    values = {
        _normalize_label(node.get("text")),
        _normalize_label(node.get("content-desc")),
    }
    return bool(values & NOT_NOW_LABELS)


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


def _target_from_node(
    node: ET.Element,
    path: tuple[int, ...],
) -> CanonicalSemanticActionTarget | None:
    bounds = _parse_bounds(node.get("bounds"))
    if bounds is None:
        return None
    return CanonicalSemanticActionTarget(
        path=path,
        class_name=str(node.get("class") or ""),
        text=str(node.get("text") or "").strip(),
        content_description=str(node.get("content-desc") or "").strip(),
        resource_id=str(node.get("resource-id") or "").strip(),
        bounds=bounds,
    )


def _resolve_unique_live_selector(
    d: Any,
    target: CanonicalSemanticActionTarget,
) -> Any | None:
    """Address the proved ancestor semantically; never fall back to coordinates."""

    candidates: list[dict[str, str]] = []
    if _normalize_label(target.content_description) in NOT_NOW_LABELS:
        candidates.append({"description": target.content_description})
    if _normalize_label(target.text) in NOT_NOW_LABELS:
        candidates.append({"text": target.text})
    for kwargs in candidates:
        try:
            selector = d(**kwargs)
        except Exception:
            continue
        if _selector_count(selector) != 1:
            continue
        if _selector_matches_canonical_target(selector, target):
            return selector
    return None


def _selector_matches_canonical_target(
    selector: Any,
    target: CanonicalSemanticActionTarget,
) -> bool:
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
    return not target.class_name or not live_class or live_class == target.class_name


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
    return 1 if bool(exists) else 0


def _parse_bounds(value: Any) -> tuple[int, int, int, int] | None:
    match = re.fullmatch(
        r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]",
        str(value or "").strip(),
    )
    if not match:
        return None
    left, top, right, bottom = (int(item) for item in match.groups())
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _normalize_label(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())
