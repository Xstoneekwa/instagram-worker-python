"""Visible Following-list row harvest for Unfollow Phase 2A probe.

XML/accessibility first. This module only observes rows; it never taps row CTAs.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

import uiautomator2 as u2

from logs import log

_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def normalize_unfollow_username(username: str) -> str:
    return str(username or "").strip().lstrip("@").lower()


def classify_following_row_cta(raw: str) -> str:
    """Classify the right-side CTA on a Following-list row."""
    text = str(raw or "").strip().lower()
    if not text:
        return "unknown"
    if "follow back" in text or "suivre en retour" in text:
        return "follow_back"
    if text in ("follow", "suivre"):
        return "follow"
    if text in ("message", "envoyer un message"):
        return "message"
    if text in ("following", "suivi(e)", "abonné", "abonnee", "abonné(e)"):
        return "following"
    if "following" in text and "followers" not in text:
        return "following"
    if text in ("requested", "demandé"):
        return "unknown"
    return "unknown"


def _dump_hierarchy(d: u2.Device) -> str:
    try:
        try:
            return str(d.dump_hierarchy(compressed=False) or "")
        except TypeError:
            return str(d.dump_hierarchy() or "")
    except Exception:
        return ""


def _parse_bounds(raw: str | None) -> dict[str, int]:
    text = str(raw or "")
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", text)
    if not m:
        return {}
    return {
        "left": int(m.group(1)),
        "top": int(m.group(2)),
        "right": int(m.group(3)),
        "bottom": int(m.group(4)),
    }


def _center(bounds: dict[str, int]) -> list[int]:
    if not bounds:
        return [0, 0]
    return [
        (int(bounds.get("left", 0)) + int(bounds.get("right", 0))) // 2,
        (int(bounds.get("top", 0)) + int(bounds.get("bottom", 0))) // 2,
    ]


def _looks_like_username(raw: str) -> bool:
    value = str(raw or "").strip().lstrip("@")
    return bool(value and _HANDLE_RE.match(value))


def harvest_visible_following_rows_for_unfollow(
    d: u2.Device,
    *,
    account_username: str = "",
    hierarchy_xml: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Harvest visible rows from the owner Following list without acting on them."""
    source_key = normalize_unfollow_username(account_username)
    hierarchy = str(hierarchy_xml or "").strip() or _dump_hierarchy(d)
    rows: list[dict[str, Any]] = []
    cta_counts: dict[str, int] = {
        "following": 0,
        "message": 0,
        "follow": 0,
        "follow_back": 0,
        "unknown": 0,
    }

    if not hierarchy.strip():
        meta = {
            "visible_rows_count": 0,
            "row_cta_counts": cta_counts,
            "hierarchy_xml_len": 0,
            "extraction_source": "empty_hierarchy",
        }
        log("info", "unfollow_following_rows_harvested", **meta)
        return [], meta

    try:
        try:
            root = ET.fromstring(hierarchy)
        except ET.ParseError:
            root = ET.fromstring(f"<wrap>{hierarchy}</wrap>")
    except Exception as exc:
        meta = {
            "visible_rows_count": 0,
            "row_cta_counts": cta_counts,
            "hierarchy_xml_len": len(hierarchy),
            "extraction_source": "xml_parse_failed",
            "error": str(exc)[:200],
        }
        log("info", "unfollow_following_rows_harvested", **meta)
        return [], meta

    by_key: dict[str, dict[str, Any]] = {}
    doc_order = 0
    for el in root.iter():
        rid = str(el.get("resource-id") or "")
        if "follow_list_container" not in rid:
            continue

        username = ""
        username_bounds: dict[str, int] = {}
        cta_text = ""
        cta_bounds: dict[str, int] = {}
        row_bounds = _parse_bounds(el.get("bounds"))

        for sub in el.iter():
            srid = str(sub.get("resource-id") or "")
            if "follow_list_username" in srid:
                cand = str(sub.get("text") or "").strip().lstrip("@")
                if _looks_like_username(cand):
                    username = cand
                    username_bounds = _parse_bounds(sub.get("bounds"))
            elif "follow_list_row_large_follow_button" in srid:
                cta_text = str(sub.get("text") or sub.get("content-desc") or "").strip()
                cta_bounds = _parse_bounds(sub.get("bounds"))

        username_key = normalize_unfollow_username(username)
        if not username_key or username_key == source_key or username_key in by_key:
            continue

        cta_class = classify_following_row_cta(cta_text)
        cta_counts[cta_class] = int(cta_counts.get(cta_class, 0)) + 1
        bounds = username_bounds or row_bounds
        by_key[username_key] = {
            "username": username,
            "username_normalized": username_key,
            "row_index": doc_order,
            "username_bounds": dict(username_bounds),
            "cta_text": cta_text,
            "cta_bounds": dict(cta_bounds),
            "row_cta_class": cta_class,
            "row_bounds": dict(row_bounds),
            "row_center": _center(bounds),
            "extraction_source": "own_following_unified_follow_list_xml",
        }
        doc_order += 1

    rows = sorted(by_key.values(), key=lambda row: int((row.get("row_bounds") or {}).get("top", 0)))
    for idx, row in enumerate(rows):
        row["row_index"] = idx
        log(
            "info",
            "unfollow_following_row_seen",
            username=str(row.get("username") or ""),
            username_normalized=str(row.get("username_normalized") or ""),
            row_index=idx,
            cta_text=str(row.get("cta_text") or "")[:80],
            row_cta_class=str(row.get("row_cta_class") or "unknown"),
            extraction_source=str(row.get("extraction_source") or ""),
        )

    meta = {
        "visible_rows_count": len(rows),
        "row_cta_counts": cta_counts,
        "hierarchy_xml_len": len(hierarchy),
        "extraction_source": "own_following_unified_follow_list_xml" if rows else "no_rows",
        "sample_usernames": [str(r.get("username") or "") for r in rows[:12]],
    }
    log("info", "unfollow_following_rows_harvested", **meta)
    return rows, meta
