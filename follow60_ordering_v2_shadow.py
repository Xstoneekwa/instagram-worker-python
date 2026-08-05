"""Follow60 Ordering V2 feasibility shadow.

This module is deliberately observational.  It consumes the immutable XML that
the pre-Follow mono capture already acquired and reuses the certified V1 grid
classifier.  It never acquires UI state, authorizes a tap, changes ordering, or
persists a business action.
"""

from __future__ import annotations

import hashlib
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Mapping


SCHEMA = "FOLLOW60_ORDERING_V2_SHADOW_V1"
_ENABLED_ENV = "FOLLOW60_ORDERING_V2_SHADOW_ENABLED"
_ALLOWLIST_ENV = "FOLLOW60_ORDERING_V2_SHADOW_ACCOUNT_IDS"
_TRUE = {"1", "true", "yes", "on"}
_BOUNDS_RE = re.compile(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$")


def enabled_for_account(
    account_id: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Fail closed: both the shadow flag and an explicit account allowlist are required."""
    env = os.environ if environ is None else environ
    if str(env.get(_ENABLED_ENV) or "").strip().lower() not in _TRUE:
        return False
    allowlist = {
        value.strip().lower()
        for value in str(env.get(_ALLOWLIST_ENV) or "").split(",")
        if value.strip()
    }
    account = str(account_id or "").strip().lower()
    return bool(account and allowlist and account in allowlist)


def _viewport_from_existing_xml(xml: str) -> tuple[int, int] | None:
    """Resolve the live XML coordinate frame without a device call or fixed coordinates."""
    try:
        root = ET.fromstring(str(xml or ""))
    except Exception:
        return None
    max_right = 0
    max_bottom = 0
    for node in root.iter():
        match = _BOUNDS_RE.match(str((node.attrib or {}).get("bounds") or ""))
        if not match:
            continue
        _left, _top, right, bottom = (int(value) for value in match.groups())
        max_right = max(max_right, right)
        max_bottom = max(max_bottom, bottom)
    if max_right <= 0 or max_bottom <= 0:
        return None
    return max_right, max_bottom


def classify_existing_pre_follow_capture(
    mono_capture: dict[str, Any] | None,
    *,
    account_id: str,
    run_id: str,
    request_id: str,
    candidate_username: str,
    source_profile_username: str,
    visual_candidate_id: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Classify an already-acquired capture for feasibility telemetry only."""
    if not enabled_for_account(account_id, environ=environ):
        return None
    capture = dict(mono_capture or {})
    xml = str(capture.get("xml") or "")
    viewport = _viewport_from_existing_xml(xml)
    if not xml or viewport is None:
        return {
            "schema": SCHEMA,
            "classification": "AMBIGUOUS",
            "reason": "existing_pre_follow_capture_missing_or_unframed",
            "eligible_for_ordering_v2": False,
            "acquisition_count": 0,
            "extra_screenshots": 0,
            "extra_xml": 0,
        }

    private_probe = dict(capture.get("private_probe_payload") or {})
    private_detected = bool(private_probe.get("private_profile_detected"))
    identity_exact = bool(capture.get("exact_identity"))
    public_ready = bool(capture.get("ok")) and not private_detected
    if private_detected:
        classification = "PRIVATE"
        reason = "pre_follow_mono_capture_private_positive"
        evidence: dict[str, Any] = {}
    elif not public_ready or not identity_exact:
        classification = "AMBIGUOUS"
        reason = "pre_follow_positive_profile_contract_incomplete"
        evidence = {}
    else:
        # Reuse the exact V1 parser.  The result is telemetry only and can never
        # authorize a post tap or alter the current Follow-first state machine.
        from instagram_navigation import _post_follow_post_grid_evidence_from_xml

        evidence = _post_follow_post_grid_evidence_from_xml(
            xml,
            candidate_username=candidate_username,
            ww=int(viewport[0]),
            wh=int(viewport[1]),
            profile_identity_exact=True,
            profile_origin_exact=True,
        )
        outcome = str(evidence.get("outcome") or "")
        unsafe_surface = bool(
            evidence.get("private_profile_visible")
            or evidence.get("loading_visible")
            or evidence.get("reels_or_tagged_selected")
        )
        if outcome == "NO_POSTS_POSITIVE":
            classification = "NO_POSTS"
            reason = str(evidence.get("no_posts_reason") or "no_posts_positive")
        elif (
            outcome == "POST_ROW_POSITIVE_SAFE"
            and evidence.get("tap_safe") is True
            and evidence.get("top_left_fully_visible") is True
            and bool(evidence.get("post_bounds"))
            and not unsafe_surface
        ):
            classification = "DIRECT_GRID_SAFE"
            reason = "v1_positive_top_left_grid_proof"
        elif outcome in {
            "POST_ROW_POSITIVE_BUT_CLIPPED",
            "POST_GRID_REVEAL_REQUIRED",
        }:
            classification = "BELOW_FOLD"
            reason = str(
                evidence.get("reveal_required_reason")
                or evidence.get("clipped_reason")
                or evidence.get("rejection_reason")
                or "post_grid_reveal_required"
            )
        else:
            classification = "AMBIGUOUS"
            reason = str(evidence.get("rejection_reason") or "post_grid_ambiguous")

    return {
        "schema": SCHEMA,
        "classification": classification,
        "reason": reason,
        "eligible_for_ordering_v2": classification == "DIRECT_GRID_SAFE",
        "account_id": str(account_id or ""),
        "run_id": str(run_id or ""),
        "request_id": str(request_id or ""),
        "candidate_username": str(candidate_username or "").strip().lstrip("@").lower(),
        "source_profile_username": str(source_profile_username or "").strip().lstrip("@").lower(),
        "visual_candidate_id": str(visual_candidate_id or ""),
        "capture_fingerprint": str(
            capture.get("xml_fingerprint")
            or hashlib.sha256(xml.encode("utf-8", errors="replace")).hexdigest()[:20]
        ),
        "capture_duration_ms": float(capture.get("duration_ms") or 0.0),
        "identity_exact": identity_exact,
        "profile_surface": bool(capture.get("profile_surface")),
        "follow_cta_positive": bool(capture.get("follow_cta_positive")),
        "private_detected": private_detected,
        "posts_count_positive": bool(evidence.get("post_count_positive")),
        "posts_count_zero_exact": bool(evidence.get("posts_count_zero_exact")),
        "profile_tabs_present": bool(evidence.get("profile_tabs_present")),
        "posts_tab_selected": bool(evidence.get("posts_tab_selected")),
        "top_left_fully_visible": bool(evidence.get("top_left_fully_visible")),
        "physical_cell_count": int(evidence.get("physical_cell_count") or 0),
        "v1_grid_outcome": str(evidence.get("outcome") or ""),
        "v1_grid_rejection_reason": str(evidence.get("rejection_reason") or ""),
        "navigation_generation": str(capture.get("navigation_generation") or ""),
        "ui_generation": int(capture.get("ui_generation") or 0),
        "acquisition_count": 0,
        "extra_screenshots": 0,
        "extra_xml": 0,
        "behavior_changed": False,
        "v2_default_enabled": False,
    }
