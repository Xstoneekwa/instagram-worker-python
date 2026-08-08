"""Account-scoped Follow60 Ordering V2 behavioral canary contracts.

This module owns routing and proof transport only.  It deliberately has no
device dependency: the runner supplies the already-certified V1 evidence and
the existing navigation engines execute every physical action.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping


SCHEMA = "FOLLOW60_ORDERING_V2_BEHAVIORAL_CANARY_V1"
CANARY_TYPE = "FOLLOW60_ORDERING_V2_BEHAVIORAL_CANARY_V1"
DEFAULT_MAX_NEW_CYCLES = 10
ENABLED_ENV = "FOLLOW60_ORDERING_V2_BEHAVIORAL_ENABLED"
ALLOWLIST_ENV = "FOLLOW60_ORDERING_V2_BEHAVIORAL_ACCOUNT_IDS"
_TRUE = {"1", "true", "yes", "on"}
_BOUNDS_RE = re.compile(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$")
_POST_COUNT_RE = re.compile(r"\b([0-9][0-9., ]*)\s+(?:posts?|publications?)\b", re.I)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return _text(value).lstrip("@").lower()


def _sha(value: Any) -> str:
    return _text(value).lower()


def _flag_on(environ: Mapping[str, str]) -> bool:
    return _text(environ.get(ENABLED_ENV)).lower() in _TRUE


def _allowlist(environ: Mapping[str, str]) -> set[str]:
    values: set[str] = set()
    for item in _text(environ.get(ALLOWLIST_ENV)).split(","):
        candidate = item.strip().lower()
        if not candidate:
            continue
        try:
            values.add(str(uuid.UUID(candidate)))
        except (ValueError, AttributeError):
            return set()
    return values


def behavioral_runtime_scope_for_account(
    account_id: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    """Fail closed unless one and only one valid account is allowlisted."""

    env = os.environ if environ is None else environ
    if not _flag_on(env):
        return False, "v2_behavioral_disabled"
    allowed = _allowlist(env)
    if len(allowed) != 1:
        return False, "v2_allowlist_must_contain_exactly_one_valid_account"
    try:
        current = str(uuid.UUID(_text(account_id)))
    except (ValueError, AttributeError):
        return False, "v2_account_id_invalid"
    if current not in allowed:
        return False, "v2_account_not_allowlisted"
    return True, "v2_behavioral_scope_valid"


@dataclass(frozen=True)
class BehavioralCanaryBindingV1:
    schema: str
    control_id: str
    account_id: str
    run_id: str
    request_id: str
    business_session_id: str
    attempt_id: int
    expected_worker_sha: str
    actual_worker_sha: str
    canary_type: str
    max_new_cycles: int
    baseline_follow_count: int
    expires_at_epoch_s: float
    lease_id: str
    lease_nonce: str
    lease_expires_at_epoch_s: float
    claimed_at_epoch_s: float
    candidate_seen_count: int
    v2_selected_count: int
    v2_complete_count: int
    v2_partial_count: int
    v1_fallback_count: int
    status: str

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def validate_behavioral_canary_binding(
    control: Mapping[str, Any] | None,
    *,
    account_id: str,
    run_id: str,
    request_id: str,
    business_session_id: str,
    attempt_id: int,
    worker_sha: str,
    completed_v2_cycles: int | None = None,
    now_epoch_s: float | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[BehavioralCanaryBindingV1 | None, str]:
    """Validate the full canary boundary.  Any missing signal routes to V1."""

    env = os.environ if environ is None else environ
    scope_ok, scope_reason = behavioral_runtime_scope_for_account(
        account_id, environ=env
    )
    if not scope_ok:
        return None, scope_reason
    raw = dict(control or {})
    if _text(raw.get("schema")) != SCHEMA:
        return None, "v2_control_schema_invalid"
    if _text(raw.get("status")) != "running":
        return None, "v2_control_not_active"
    if _text(raw.get("canary_type")) != CANARY_TYPE:
        return None, "v2_canary_type_invalid"
    expected = _sha(raw.get("expected_worker_sha") or raw.get("worker_sha"))
    actual = _sha(worker_sha)
    if len(expected) != 40 or expected != actual:
        return None, "v2_expected_worker_sha_mismatch"
    if _sha(raw.get("actual_worker_sha")) != actual:
        return None, "v2_actual_worker_sha_mismatch"
    exact = (
        ("account_id", _text(account_id)),
        ("run_id", _text(run_id)),
        ("request_id", _text(request_id)),
        ("business_session_id", _text(business_session_id)),
    )
    for field, current in exact:
        if not current or _text(raw.get(field)) != current:
            return None, f"v2_{field}_mismatch"
    if int(raw.get("attempt_id") or 0) != int(attempt_id or 0) or int(attempt_id or 0) < 1:
        return None, "v2_attempt_id_mismatch"
    max_cycles = int(raw.get("max_new_cycles") or 0)
    if max_cycles < 1 or max_cycles > DEFAULT_MAX_NEW_CYCLES:
        return None, "v2_max_new_cycles_invalid"
    authoritative_complete = int(raw.get("v2_complete_count") or 0)
    if completed_v2_cycles is not None and int(completed_v2_cycles) != authoritative_complete:
        return None, "v2_complete_count_not_authoritative"
    expiry = float(raw.get("expires_at_epoch_s") or 0.0)
    now = time.time() if now_epoch_s is None else float(now_epoch_s)
    if expiry <= now:
        return None, "v2_control_expired"
    lease_expiry = float(raw.get("lease_expires_at_epoch_s") or 0.0)
    if lease_expiry <= now:
        return None, "v2_control_lease_expired"
    claimed_at = float(raw.get("claimed_at_epoch_s") or 0.0)
    if claimed_at <= 0.0 or claimed_at > now + 5.0:
        return None, "v2_claimed_at_invalid"
    required = {
        "control_id": _text(raw.get("control_id") or raw.get("id")),
        "lease_id": _text(raw.get("lease_id")),
        "lease_nonce": _text(raw.get("lease_nonce")),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        return None, f"v2_control_missing_{missing[0]}"
    if raw.get("lease_consumed") is True or raw.get("canceled") is True:
        return None, "v2_control_lease_unavailable"
    return BehavioralCanaryBindingV1(
        schema=SCHEMA,
        control_id=required["control_id"],
        account_id=_text(account_id),
        run_id=_text(run_id),
        request_id=_text(request_id),
        business_session_id=_text(business_session_id),
        attempt_id=int(attempt_id),
        expected_worker_sha=actual,
        actual_worker_sha=_sha(raw.get("actual_worker_sha")),
        canary_type=CANARY_TYPE,
        max_new_cycles=max_cycles,
        baseline_follow_count=int(raw.get("baseline_follow_count") or 0),
        expires_at_epoch_s=expiry,
        lease_id=required["lease_id"],
        lease_nonce=required["lease_nonce"],
        lease_expires_at_epoch_s=lease_expiry,
        claimed_at_epoch_s=claimed_at,
        candidate_seen_count=int(raw.get("candidate_seen_count") or 0),
        v2_selected_count=int(raw.get("v2_selected_count") or 0),
        v2_complete_count=authoritative_complete,
        v2_partial_count=int(raw.get("v2_partial_count") or 0),
        v1_fallback_count=int(raw.get("v1_fallback_count") or 0),
        status=_text(raw.get("status")),
    ), "v2_binding_valid"


@dataclass(frozen=True)
class StableCandidateProofV2:
    schema: str
    account_id: str
    request_id: str
    run_id: str
    business_session_id: str
    attempt_id: int
    worker_sha: str
    binding_kind: str
    control_id: str
    target_id: str
    candidate_username: str
    action_id: str
    candidate_identity_exact: bool
    filter_verdict: str
    eligibility_verdict: str
    follow_budget_reserved: bool
    private_detected: bool
    posts_count: int
    posts_count_source: str
    posts_tab_identity: str
    direct_grid_safe: bool
    top_left_identity: str
    post_grid_evidence: dict[str, Any]
    viewer_provenance: str
    v5_candidate_provenance: str
    proof_hash: str

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _viewport_from_xml(xml: str) -> tuple[int, int] | None:
    try:
        root = ET.fromstring(xml)
    except Exception:
        return None
    width = height = 0
    for node in root.iter():
        bounds = _text((node.attrib or {}).get("bounds"))
        match = _BOUNDS_RE.match(bounds)
        if match is None:
            continue
        _left, _top, right, bottom = (int(value) for value in match.groups())
        width = max(width, right)
        height = max(height, bottom)
    return (width, height) if width > 0 and height > 0 else None


def _selected_posts_tab_exact_from_xml(xml: str) -> bool:
    """Require an explicit selected Posts/Grid tab from the existing capture.

    Physical cells alone are not enough to authorize the behavioral route: a
    stale row can coexist with a transition to Reels/Tagged.  This check does
    not acquire anything; it tightens the immutable mono-XML proof.
    """

    try:
        root = ET.fromstring(xml)
    except Exception:
        return False
    for node in root.iter():
        attrs = dict(node.attrib or {})
        if _text(attrs.get("selected")).lower() != "true":
            continue
        blob = " ".join(
            _text(attrs.get(key)).lower()
            for key in ("resource-id", "content-desc", "text")
        )
        if any(marker in blob for marker in ("reel", "tagged", "tagu", "identifi")):
            continue
        if any(marker in blob for marker in ("grid", "posts", "publication")):
            return True
    return False


def _direct_grid_rejection_reason_v2(
    grid: Mapping[str, Any],
    *,
    selected_posts_tab_exact: bool,
) -> str:
    """Return the first field-level reason blocking ``POST_FIRST_V2``.

    Region *presence* is deliberately not a blocker.  Only a region that is
    not structurally separated from the post grid may overlap the absolute
    top-left cell.  The terminal viewer V5 guard remains mandatory after tap.
    """

    if grid.get("private_profile_visible"):
        return "v2_private_profile_surface"
    if grid.get("loading_visible"):
        return "v2_loading_or_challenge_surface"
    if str(grid.get("tagged_tab_state") or "") == "selected":
        return "v2_tagged_tab_selected"
    if str(grid.get("reels_tab_state") or "") == "selected":
        return "v2_reels_tab_selected"
    if grid.get("reels_or_tagged_selected"):
        return "v2_non_posts_tab_selected"
    if grid.get("post_count_positive") is not True:
        return "v2_posts_count_not_positive"
    if not (grid.get("posts_tab_selected") is True or grid.get("grid_selected") is True):
        return "v2_posts_tab_not_selected"
    if not selected_posts_tab_exact:
        return "v2_posts_tab_identity_not_exact"
    cells = [
        dict(item)
        for item in list(grid.get("physical_cells") or [])
        if isinstance(item, Mapping)
    ]
    top_left = [
        item
        for item in cells
        if int(item.get("absolute_row_index") or 0) == 1
        and int(item.get("absolute_column_index") or 0) == 1
    ]
    if not cells:
        return "v2_physical_post_cell_missing"
    if len(top_left) != 1:
        return "v2_absolute_top_left_not_unique"
    if grid.get("absolute_top_left_origin_proven") is not True:
        return "v2_absolute_top_left_origin_not_proven"
    if grid.get("top_left_fully_visible") is not True:
        return "v2_absolute_top_left_not_fully_visible"
    if not isinstance(grid.get("post_bounds"), Mapping):
        return "v2_absolute_top_left_bounds_missing"
    if (
        bool(grid.get("suggested_region_detected"))
        and grid.get("suggested_region_separate") is not True
    ):
        return "v2_suggested_region_overlaps_post_grid"
    if (
        bool(grid.get("highlights_region_detected"))
        and grid.get("highlights_region_separate") is not True
    ):
        return "v2_highlights_region_overlaps_post_grid"
    if str(grid.get("outcome") or "") != "POST_ROW_POSITIVE_SAFE":
        return str(grid.get("rejection_reason") or "v2_grid_outcome_not_tap_safe")
    return ""


def build_stable_candidate_proof_v2(
    *,
    binding: BehavioralCanaryBindingV1,
    mono_capture: Mapping[str, Any],
    business_evidence: Mapping[str, Any],
    target_id: str,
    candidate_username: str,
    action_id: str,
    binding_kind: str,
) -> tuple[StableCandidateProofV2 | None, str]:
    """Build the only proof that may select POST_FIRST_V2.

    It reuses the immutable mono XML already acquired by V1.  No device call,
    screenshot, reveal, or secondary classification is performed here.
    """

    capture = dict(mono_capture or {})
    xml = _text(capture.get("xml"))
    viewport = _viewport_from_xml(xml)
    if not xml or viewport is None:
        return None, "v2_existing_mono_capture_missing"
    if not bool(capture.get("ok")) or not bool(capture.get("exact_identity")):
        return None, "v2_candidate_identity_not_exact"
    if bool(dict(capture.get("private_probe_payload") or {}).get("private_profile_detected")):
        return None, "v2_private_profile"
    if not bool(business_evidence.get("filter_passed")):
        return None, "v2_filter_not_passed"
    if not bool(business_evidence.get("eligibility_passed")):
        return None, "v2_eligibility_not_passed"
    if not bool(business_evidence.get("follow_budget_available")):
        return None, "v2_follow_budget_unavailable"
    package = _text(capture.get("package"))
    activity = _text(capture.get("activity"))
    if (
        capture.get("package_exact") is not True
        or not package
        or "instagram" not in activity.lower()
        or "mainactivity" not in activity.lower()
    ):
        return None, "v2_profile_surface_package_activity_not_exact"
    generation_fields = (
        "navigation_counter",
        "scroll_counter",
        "ui_generation",
    )
    if any(field not in capture for field in generation_fields):
        return None, "v2_profile_surface_generation_missing"
    try:
        if any(int(capture[field]) < 0 for field in generation_fields):
            return None, "v2_profile_surface_generation_invalid"
    except (TypeError, ValueError):
        return None, "v2_profile_surface_generation_invalid"

    from instagram_navigation import _post_follow_post_grid_evidence_from_xml

    grid = _post_follow_post_grid_evidence_from_xml(
        xml,
        candidate_username=candidate_username,
        ww=int(viewport[0]),
        wh=int(viewport[1]),
        profile_identity_exact=True,
        profile_origin_exact=True,
    )
    rejection_reason = _direct_grid_rejection_reason_v2(
        grid,
        selected_posts_tab_exact=_selected_posts_tab_exact_from_xml(xml),
    )
    if rejection_reason:
        return None, rejection_reason
    posts_count = int(grid.get("posts_count") or grid.get("post_count") or 0)
    posts_count_source = _text(
        grid.get("posts_count_source") or grid.get("post_count_source")
    )
    if posts_count <= 0:
        count_match = _POST_COUNT_RE.search(xml)
        if count_match is not None:
            digits = re.sub(r"[^0-9]", "", count_match.group(1))
            posts_count = int(digits or 0)
            posts_count_source = "existing_pre_follow_mono_xml_profile_count"
    material = "|".join(
        (
            binding.account_id,
            binding.run_id,
            binding.request_id,
            _norm(candidate_username),
            _text(action_id),
            hashlib.sha256(xml.encode("utf-8", errors="replace")).hexdigest(),
        )
    )
    proof_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
    grid_payload = dict(grid)
    grid_payload["source_xml_fingerprint"] = str(
        capture.get("xml_fingerprint")
        or hashlib.sha256(xml.encode("utf-8", errors="replace")).hexdigest()
    )
    grid_payload["screen_width"] = int(viewport[0])
    grid_payload["screen_height"] = int(viewport[1])
    grid_payload["candidate_username"] = _norm(candidate_username)
    # Transport the already-certified profile surface into PostOpenIntentV2.
    # No device acquisition or hierarchy pass is allowed between routing and
    # the one-shot live package/activity/generation validation at dispatch.
    grid_payload["package"] = package
    grid_payload["activity"] = activity
    grid_payload["final_proof_package"] = package
    grid_payload["final_proof_activity"] = activity
    grid_payload["coordinate_frame"] = dict(
        grid_payload.get("coordinate_frame")
        or grid_payload.get("frame")
        or {}
    )
    grid_payload["ordering_v2_initial_proof"] = True
    grid_payload["proof_navigation_counter"] = int(
        capture.get("navigation_counter") or 0
    )
    grid_payload["proof_scroll_counter"] = int(
        capture.get("scroll_counter") or 0
    )
    grid_payload["proof_ui_generation"] = int(
        capture.get("ui_generation") or 0
    )
    return StableCandidateProofV2(
        schema=SCHEMA,
        account_id=binding.account_id,
        request_id=binding.request_id,
        run_id=binding.run_id,
        business_session_id=binding.business_session_id,
        attempt_id=binding.attempt_id,
        worker_sha=binding.expected_worker_sha,
        binding_kind=_text(binding_kind) or "mainline",
        control_id=binding.control_id,
        target_id=_text(target_id),
        candidate_username=_norm(candidate_username),
        action_id=_text(action_id),
        candidate_identity_exact=True,
        filter_verdict=_text(business_evidence.get("filter_reason")) or "passed",
        eligibility_verdict=_text(business_evidence.get("eligibility_reason")) or "passed",
        follow_budget_reserved=True,
        private_detected=False,
        posts_count=posts_count,
        posts_count_source=posts_count_source,
        posts_tab_identity="posts_tab_selected",
        direct_grid_safe=True,
        top_left_identity="absolute_row_1_column_1_unique",
        post_grid_evidence=grid_payload,
        viewer_provenance=f"{proof_hash}:viewer",
        v5_candidate_provenance=f"{proof_hash}:v5",
        proof_hash=proof_hash,
    ), "v2_direct_grid_safe"


@dataclass(frozen=True)
class DeferredFollowIntentV2:
    schema: str
    account_id: str
    run_id: str
    request_id: str
    action_id: str
    candidate_username: str
    worker_sha: str
    control_id: str
    initial_cta_identity: str
    follow_budget_reserved: bool
    stable_proof_hash: str
    intent_nonce: str
    created_at_monotonic: float
    pre_post_bounds_invalidated: bool = True

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def create_deferred_follow_intent_v2(
    proof: StableCandidateProofV2,
    *,
    initial_cta_identity: str,
) -> DeferredFollowIntentV2:
    if not proof.direct_grid_safe or not proof.follow_budget_reserved:
        raise ValueError("v2_deferred_follow_intent_not_authorized")
    if _text(initial_cta_identity) != "follow":
        raise ValueError("v2_deferred_follow_cta_not_follow")
    return DeferredFollowIntentV2(
        schema=SCHEMA,
        account_id=proof.account_id,
        run_id=proof.run_id,
        request_id=proof.request_id,
        action_id=proof.action_id,
        candidate_username=proof.candidate_username,
        worker_sha=proof.worker_sha,
        control_id=proof.control_id,
        initial_cta_identity="follow",
        follow_budget_reserved=True,
        stable_proof_hash=proof.proof_hash,
        intent_nonce=secrets.token_hex(16),
        created_at_monotonic=time.monotonic(),
        pre_post_bounds_invalidated=True,
    )


@dataclass(frozen=True)
class ProfileReentryProofV2:
    schema: str
    candidate_username: str
    package: str
    activity: str
    surface: str
    action_bar_title: str
    cta_state: str
    cta_bounds: dict[str, int]
    cta_resource_id: str
    cta_text: str
    overlay_or_challenge: bool
    navigation_generation: str
    ui_generation: int
    captured_at_monotonic: float
    deferred_intent_nonce: str
    stable_proof_hash: str

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def build_profile_reentry_proof_v2(
    deferred: DeferredFollowIntentV2,
    stable: StableCandidateProofV2,
    *,
    live: Mapping[str, Any],
    expected_package: str,
) -> tuple[ProfileReentryProofV2 | None, str]:
    """Accept only fresh volatile Level-1 evidence acquired after Back."""

    if deferred.stable_proof_hash != stable.proof_hash:
        return None, "v2_reentry_stable_proof_mismatch"
    if not deferred.pre_post_bounds_invalidated:
        return None, "v2_pre_post_bounds_not_invalidated"
    package = _text(live.get("package"))
    activity = _text(live.get("activity"))
    if package != _text(expected_package) or not activity:
        return None, "v2_reentry_package_or_activity_mismatch"
    if _text(live.get("surface")) != "candidate_profile":
        return None, "v2_reentry_surface_not_candidate_profile"
    if _norm(live.get("candidate_username") or live.get("action_bar_title")) != stable.candidate_username:
        return None, "v2_reentry_wrong_profile"
    if bool(live.get("overlay_or_challenge")):
        return None, "v2_reentry_overlay_or_challenge"
    if _text(live.get("cta_state")) != "follow":
        return None, "v2_reentry_follow_cta_missing"
    bounds = dict(live.get("cta_bounds") or {})
    try:
        left, top, right, bottom = (int(bounds[key]) for key in ("left", "top", "right", "bottom"))
    except (KeyError, TypeError, ValueError):
        return None, "v2_reentry_follow_bounds_missing"
    if not (0 <= left < right and 0 <= top < bottom):
        return None, "v2_reentry_follow_bounds_invalid"
    captured = float(live.get("captured_at_monotonic") or 0.0)
    if captured <= deferred.created_at_monotonic:
        return None, "v2_reentry_not_fresh_after_post"
    if not _text(live.get("navigation_generation")) or int(live.get("ui_generation") or 0) < 1:
        return None, "v2_reentry_generation_missing"
    return ProfileReentryProofV2(
        schema=SCHEMA,
        candidate_username=stable.candidate_username,
        package=package,
        activity=activity,
        surface="candidate_profile",
        action_bar_title=_norm(live.get("action_bar_title") or live.get("candidate_username")),
        cta_state="follow",
        cta_bounds={"left": left, "top": top, "right": right, "bottom": bottom},
        cta_resource_id=_text(live.get("resource_id")),
        cta_text=_text(live.get("text")) or "Follow",
        overlay_or_challenge=False,
        navigation_generation=_text(live.get("navigation_generation")),
        ui_generation=int(live.get("ui_generation")),
        captured_at_monotonic=captured,
        deferred_intent_nonce=deferred.intent_nonce,
        stable_proof_hash=stable.proof_hash,
    ), "v2_reentry_level1_valid"


def build_fresh_follow_tap_context_v2(
    proof: ProfileReentryProofV2,
    *,
    source_profile_username: str,
    visual_candidate_id: str,
    private_probe_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Translate fresh reentry evidence to the unchanged Follow engine input."""

    private = dict(private_probe_payload or {})
    if bool(private.get("private_profile_detected")):
        raise ValueError("v2_reentry_private_profile")
    return {
        "kind": "pre_follow_tap_context_v1",
        "follower_username": proof.candidate_username,
        "source_profile_username": _text(source_profile_username),
        "visual_candidate_id": _text(visual_candidate_id),
        "action_bar_title": proof.action_bar_title,
        "navigation_state": "CANDIDATE_PROFILE",
        "navigation_confidence": 1.0,
        "raw_follow_invite_visible": True,
        "followers_list_xml_hint": False,
        "follow_header_state": "follow",
        "requested": False,
        "following": False,
        "screen_guard_ok": True,
        "private_gate": {
            "reject": False,
            "private_profile_detected": False,
            "probe_ms": max(0.001, float(private.get("probe_ms") or 0.001)),
            "probe_reused": True,
            "reason": "stable_candidate_private_proof_reused",
        },
        "private_probe_payload": {
            **private,
            "private_profile_detected": False,
            "probe_ms": max(0.001, float(private.get("probe_ms") or 0.001)),
        },
        "ordering_v2_reentry": proof.payload(),
        "fresh_cta_bounds": dict(proof.cta_bounds),
        "captured_at_mono": proof.captured_at_monotonic,
    }


@dataclass
class CandidateCyclePlanV2:
    selected_path: str
    binding: BehavioralCanaryBindingV1
    stable_proof: StableCandidateProofV2
    deferred_follow: DeferredFollowIntentV2
    started_at_monotonic: float
    post_action_started: bool = False
    like_terminal: bool = False
    follow_terminal: bool = False

    @property
    def may_fallback_to_v1(self) -> bool:
        return not self.post_action_started


class Follow60OrderingV2OrchestratorV1:
    """Small ordering coordinator; all physical work remains in V1 engines."""

    def __init__(self, *, ledger_apply: Callable[[str, Mapping[str, Any]], Mapping[str, Any]]):
        self._ledger_apply = ledger_apply

    def begin(self, plan: CandidateCyclePlanV2) -> None:
        self._ledger_apply("profile_certified", {"proof_hash": plan.stable_proof.proof_hash})

    def execute_post_first(
        self,
        plan: CandidateCyclePlanV2,
        *,
        post_like_engine: Callable[[dict[str, Any]], Mapping[str, Any]],
    ) -> dict[str, Any]:
        out = dict(post_like_engine({
            "schema": SCHEMA,
            "stable_proof": plan.stable_proof.payload(),
            "deferred_follow": plan.deferred_follow.payload(),
            "post_grid_evidence": dict(plan.stable_proof.post_grid_evidence),
        }) or {})
        plan.post_action_started = bool(
            out.get("post_opened")
            or out.get("post_tap_sent")
            or out.get("physical_action_started")
            or int(out.get("attempted_count") or 0) > 0
        )
        if bool(out.get("post_opened")):
            self._ledger_apply("post_opened", {"method": "SAFE", "v5_required": True})
        else:
            return {**out, "ok": False, "reason": _text(out.get("reason")) or "v2_post_open_failed"}
        liked = int(out.get("liked_count") or 0) > 0
        safely_skipped = bool(out.get("like_skipped_safely"))
        if liked:
            self._ledger_apply("like_verified", {"liked_count": int(out.get("liked_count") or 0)})
            plan.like_terminal = True
        elif safely_skipped:
            self._ledger_apply("like_skipped", {"reason": _text(out.get("skipped_reason"))})
            plan.like_terminal = True
        else:
            return {**out, "ok": False, "reason": _text(out.get("reason")) or "v2_like_not_terminal"}
        return {**out, "ok": True}


def route_candidate_v2(
    *,
    binding: BehavioralCanaryBindingV1 | None,
    stable_proof: StableCandidateProofV2 | None,
    completed_v2_cycles: int | None = None,
) -> tuple[str, str]:
    if binding is None:
        return "FOLLOW60_V1", "v2_binding_absent_or_invalid"
    complete = binding.v2_complete_count
    if completed_v2_cycles is not None:
        complete = int(completed_v2_cycles)
    if complete >= binding.max_new_cycles:
        return "CANARY_BARRIER_REACHED", "v2_cycle_barrier_reached"
    if stable_proof is None or not stable_proof.direct_grid_safe:
        return "FOLLOW60_V1", "candidate_not_direct_grid_safe"
    return "POST_FIRST_V2", "v2_binding_and_direct_grid_safe"
