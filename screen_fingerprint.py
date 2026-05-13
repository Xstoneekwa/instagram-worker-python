"""
SCREEN FINGERPRINT ENGINE V1 — hybrid screen identity for phone-farm navigation.

Combines navigation state, action bar, package/activity, XML/vision guesses, followers-list
signals, and follow affordances. Not a replacement for navigation_engine or vision guards.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def _norm_handle(s: str) -> str:
    t = str(s or "").strip().lstrip("@").lower()
    t = re.sub(r"\s+", "", t)
    return t[:96]


def _boolish(v: Any) -> bool:
    return bool(v)


def build_screen_fingerprint(
    *,
    action_bar_title: str = "",
    current_package: str = "",
    current_activity: str = "",
    visible_texts: list[str] | None = None,
    nav_state: str = "",
    visual_signals: dict[str, Any] | None = None,
    xml_guess: str = "",
    visual_guess: str = "",
) -> dict[str, Any]:
    """
    Build a stable, comparable screen fingerprint from multiple signal families (not XML-only).

    Returns:
        fingerprint_id, screen_class, confidence (0..1), signals (dict), hash (hex).
    """
    vs = dict(visual_signals) if isinstance(visual_signals, dict) else {}
    texts = list(visible_texts or [])
    vt_tuple = tuple(sorted(str(t)[:64] for t in texts[:20]))
    vt_digest = (
        hashlib.sha256(repr(vt_tuple).encode("utf-8")).hexdigest()[:16] if vt_tuple else ""
    )

    signals: dict[str, Any] = {
        "action_bar_norm": _norm_handle(action_bar_title),
        "current_package": str(current_package or "").strip(),
        "current_activity": str(current_activity or "").strip()[:200],
        "nav_state": str(nav_state or "").strip(),
        "xml_guess": str(xml_guess or "").strip(),
        "visual_guess": str(visual_guess or "").strip(),
        "is_followers_list": _boolish(vs.get("is_followers_list")),
        "strict_list_open": _boolish(vs.get("strict_list_open")),
        "relaxed_list_open": _boolish(vs.get("relaxed_list_open")),
        "follow_header_state": str(vs.get("follow_header_state") or "").strip(),
        "raw_follow_invite_visible": _boolish(vs.get("raw_follow_invite_visible")),
        "nav_confidence": round(float(vs.get("nav_confidence") or 0.0), 4),
        "visible_texts_digest": vt_digest,
    }

    # --- screen_class (coarse, for compare / logging)
    nav_u = signals["nav_state"].upper()
    if signals["is_followers_list"] and (
        signals["strict_list_open"] or signals["relaxed_list_open"]
    ):
        screen_class = "followers_list_strong"
    elif signals["is_followers_list"]:
        screen_class = "followers_list"
    elif nav_u in ("PROFILE", "CANDIDATE_PROFILE", "PRIVATE_PROFILE"):
        screen_class = "profile_like"
    elif "SEARCH" in nav_u or "search" in signals["xml_guess"].lower():
        screen_class = "search_like"
    else:
        screen_class = "other"

    # --- confidence: more independent signals filled → higher (cap 1.0)
    score = 0.25
    if signals["current_package"]:
        score += 0.12
    if signals["nav_state"]:
        score += 0.15
    if signals["xml_guess"] or signals["visual_guess"]:
        score += 0.12
    if signals["is_followers_list"] or nav_u in (
        "PROFILE",
        "CANDIDATE_PROFILE",
        "PRIVATE_PROFILE",
    ):
        score += 0.18
    if signals["action_bar_norm"]:
        score += 0.1
    if signals["follow_header_state"] or signals["raw_follow_invite_visible"]:
        score += 0.08
    confidence = round(min(1.0, score), 4)

    canonical = json.dumps(signals, sort_keys=True, separators=(",", ":"), default=str)
    full_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    fingerprint_id = full_hash[:20]

    return {
        "fingerprint_id": fingerprint_id,
        "screen_class": screen_class,
        "confidence": float(confidence),
        "signals": dict(signals),
        "hash": full_hash,
    }


def compare_screen_fingerprints(
    fp1: dict[str, Any],
    fp2: dict[str, Any],
) -> dict[str, Any]:
    """
    Compare two fingerprints from ``build_screen_fingerprint``.

    ``same_screen`` is True when similarity is high *and* key structural signals indicate
    no meaningful transition (e.g. still on followers list with same nav/action bar).

    Returns:
        same_screen, similarity (0..1), changed_signals (list of dicts).
    """
    s1 = fp1.get("signals") if isinstance(fp1.get("signals"), dict) else {}
    s2 = fp2.get("signals") if isinstance(fp2.get("signals"), dict) else {}

    keys = sorted(set(s1.keys()) | set(s2.keys()))
    changed_signals: list[dict[str, Any]] = []

    # Weighted Jaccard-style similarity on scalar / bool fields
    weights: dict[str, float] = {
        "is_followers_list": 0.22,
        "strict_list_open": 0.06,
        "relaxed_list_open": 0.06,
        "nav_state": 0.18,
        "xml_guess": 0.1,
        "visual_guess": 0.06,
        "action_bar_norm": 0.14,
        "follow_header_state": 0.08,
        "raw_follow_invite_visible": 0.06,
        "current_package": 0.02,
        "current_activity": 0.02,
    }

    w_sum = 0.0
    w_match = 0.0
    for k in keys:
        w = float(weights.get(k, 0.04))
        v1, v2 = s1.get(k), s2.get(k)
        if v1 == v2:
            w_match += w
        else:
            changed_signals.append({"key": k, "before": v1, "after": v2})
        w_sum += w

    similarity = round(w_match / w_sum, 4) if w_sum > 0 else 0.0

    sc1 = str(fp1.get("screen_class") or "")
    sc2 = str(fp2.get("screen_class") or "")
    if sc1 != sc2:
        changed_signals.append({"key": "screen_class", "before": sc1, "after": sc2})

    both_list = bool(s1.get("is_followers_list")) and bool(s2.get("is_followers_list"))
    nav_same = str(s1.get("nav_state") or "") == str(s2.get("nav_state") or "")
    ab_same = str(s1.get("action_bar_norm") or "") == str(s2.get("action_bar_norm") or "")

    # High similarity + still on followers list + stable nav / action bar / class
    # ⇒ likely no meaningful transition away from the list surface.
    same_screen = bool(
        similarity >= 0.88
        and both_list
        and nav_same
        and ab_same
        and sc1 == sc2
    )

    return {
        "same_screen": bool(same_screen),
        "similarity": float(similarity),
        "changed_signals": changed_signals,
    }
