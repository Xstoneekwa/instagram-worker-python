"""Account-scoped Follow 60s canary and fresh UI proof contract.

The module is deliberately dependency-light so navigation code can use it without
creating import cycles.  Disabled is the exact legacy behaviour: callers simply
continue through their existing Golden path.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from logs import log
from follow_60s_canary_binding_v2 import (
    ONE_SHOT_CONTRACT_SCHEMA,
    Follow60CanaryBindingV2,
    validate_runtime_binding,
)


POST_ROW_POSITIVE_SAFE = "POST_ROW_POSITIVE_SAFE"
POST_ROW_POSITIVE_BUT_CLIPPED = "POST_ROW_POSITIVE_BUT_CLIPPED"
POST_GRID_AMBIGUOUS_FINAL = "POST_GRID_AMBIGUOUS_FINAL"
NO_POSTS_POSITIVE = "NO_POSTS_POSITIVE"
POST_GRID_CANONICAL_OUTCOMES = frozenset(
    {
        POST_ROW_POSITIVE_SAFE,
        POST_ROW_POSITIVE_BUT_CLIPPED,
        POST_GRID_AMBIGUOUS_FINAL,
        NO_POSTS_POSITIVE,
    }
)

_SUBFLAG_NAMES = (
    "opening_follow_composite",
    "mute_known_depth",
    "mute_like_handoff",
    "like_fresh_cell_bounds",
    "like_single_row_first_scroll",
    "viewer_a2_only",
    "single_capture_no_posts",
    "return_candidate_handoff",
    "post_return_snapshot_reuse",
)


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _one_shot_resume_allowed(
    policy: dict[str, Any], *, account_id: str
) -> tuple[bool, str]:
    """Authorize only an explicit, frozen, source-linked Follow-only retry."""
    if not policy:
        return False, "not_auto_restart_resume"
    source_run_id = str(policy.get("prior_run_id") or "").strip()
    if not source_run_id:
        return False, "source_run_missing"
    if policy.get("restart_allowed") is not True:
        return False, "restart_not_allowed"
    request_meta = dict(policy.get("request_metadata") or {})
    if str(request_meta.get("source") or "") != "auto_restart_tick":
        return False, "source_not_auto_restart_tick"
    if str(request_meta.get("recovery_mode") or "") != "human_confirmed_resume":
        return False, "recovery_mode_mismatch"
    if policy.get("restriction_preflight_only") is True:
        return False, "restriction_preflight_not_allowed"
    if not str(policy.get("resume_plan_id") or "").strip():
        return False, "resume_plan_id_missing"
    if not str(policy.get("incident_id") or "").strip():
        return False, "incident_id_missing"
    phases = dict(policy.get("phases_to_run") or {})
    if phases.get("follow") is not True or phases.get("welcome") is not False or phases.get("unfollow") is not False:
        return False, "phase_scope_mismatch"
    quota = dict(policy.get("quota_remaining") or {})
    try:
        follow_quota = int(quota.get("follow") or 0)
    except (TypeError, ValueError):
        return False, "invalid_remaining_follow_quota"
    if follow_quota <= 0 or follow_quota > 50:
        return False, "remaining_follow_quota_out_of_bounds"
    frozen = dict(policy.get("frozen_phase_plan") or {})
    contract = dict(frozen.get("follow_60s_canary_contract") or {})
    if str(frozen.get("account_id") or "") != str(account_id or "").strip():
        return False, "frozen_account_mismatch"
    if frozen.get("package_contract_ready") is not True:
        return False, "package_contract_not_ready"
    if str(contract.get("schema") or "") != ONE_SHOT_CONTRACT_SCHEMA:
        return False, "canary_contract_missing"
    if str(contract.get("source_run_id") or "").strip() != source_run_id:
        return False, "canary_contract_source_mismatch"
    try:
        contract_quota = int(contract.get("follow_quota") or 0)
    except (TypeError, ValueError):
        return False, "canary_contract_quota_invalid"
    if contract_quota != follow_quota:
        return False, "canary_contract_quota_mismatch"
    if str(contract.get("golden_fallback_policy") or "") != "proof_rejection_only":
        return False, "golden_fallback_policy_mismatch"
    expires_raw = str(contract.get("expires_at") or "").strip()
    if not expires_raw:
        return False, "one_shot_expiry_missing"
    try:
        expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
    except ValueError:
        return False, "one_shot_expiry_invalid"
    if expires_at.tzinfo is None:
        return False, "one_shot_expiry_timezone_missing"
    if datetime.now(timezone.utc) >= expires_at:
        return False, "one_shot_expired"
    return True, ""


@dataclass(frozen=True)
class FreshUiProof:
    account_id: str
    subject_username: str
    target_username: str
    package: str
    activity: str
    surface: str
    bounds: dict[str, int] | None
    xml_generation: str
    created_at_monotonic: float
    detection_source: str
    ttl_ms: float
    invalidation_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateProfileVerdict:
    """Immutable result of the single post-Mute UI-proof consumption."""

    account_id: str
    candidate_username: str
    package: str
    activity: str
    navigation_generation: str
    exact_identity: bool
    sheet_closed: bool
    mute_posts_verified: bool
    mute_stories_verified: bool
    viewport_fingerprint: str
    post_grid_outcome: str
    post_bounds: dict[str, int] | None
    no_posts_positive: bool
    invalidation_counter: int
    created_at_monotonic: float
    ttl_ms: float


@dataclass(frozen=True)
class CoordinateFrameV1:
    """Deterministic mapping between the device window and hierarchy bounds."""

    source: str
    raw_window_size: tuple[int, int]
    hierarchy_viewport_size: tuple[int, int]
    app_content_rect: dict[str, int]
    system_insets: dict[str, int]
    canonical_content_size: tuple[int, int]
    orientation: str
    density: float
    transform_version: str
    transform_hash: str
    captured_at: float
    navigation_generation: str
    scroll_generation: int

    @property
    def version(self) -> str:
        return self.transform_version

    @property
    def raw_width(self) -> int:
        return int(self.raw_window_size[0])

    @property
    def raw_height(self) -> int:
        return int(self.raw_window_size[1])

    @property
    def canonical_width(self) -> int:
        return int(self.canonical_content_size[0])

    @property
    def canonical_height(self) -> int:
        return int(self.canonical_content_size[1])


def build_coordinate_frame_v1(
    *,
    source: str,
    raw_width: int,
    raw_height: int,
    canonical_width: int,
    canonical_height: int,
    inset_left: int = 0,
    inset_top: int = 0,
    inset_right: int = 0,
    inset_bottom: int = 0,
    orientation: str = "portrait",
    density: float = 0.0,
    captured_at: float | None = None,
    navigation_generation: str = "",
    scroll_generation: int = 0,
) -> CoordinateFrameV1 | None:
    """Build a trusted frame only from exact, internally consistent geometry."""
    raw_w, raw_h = int(raw_width or 0), int(raw_height or 0)
    canonical_w, canonical_h = int(canonical_width or 0), int(canonical_height or 0)
    left, top = max(0, int(inset_left or 0)), max(0, int(inset_top or 0))
    right, bottom = max(0, int(inset_right or 0)), max(0, int(inset_bottom or 0))
    normalized_source = str(source or "")
    if normalized_source not in {"raw_window_exact", "hierarchy_coordinate_frame"}:
        return None
    if min(raw_w, raw_h, canonical_w, canonical_h) <= 0:
        return None
    if raw_w != left + canonical_w + right:
        return None
    if raw_h != top + canonical_h + bottom:
        return None
    version = "coordinate_frame_v1"
    orientation_value = str(orientation or "portrait")
    density_value = max(0.0, float(density or 0.0))
    material = "|".join(
        str(value)
        for value in (
            version, normalized_source, raw_w, raw_h, canonical_w,
            canonical_h, left, top, right, bottom, orientation_value,
            f"{density_value:.6f}",
        )
    )
    return CoordinateFrameV1(
        source=normalized_source,
        raw_window_size=(raw_w, raw_h),
        hierarchy_viewport_size=(canonical_w, canonical_h),
        app_content_rect={
            "left": left,
            "top": top,
            "right": left + canonical_w,
            "bottom": top + canonical_h,
        },
        system_insets={
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
        },
        canonical_content_size=(canonical_w, canonical_h),
        orientation=orientation_value,
        density=density_value,
        transform_version=version,
        transform_hash=hashlib.sha256(material.encode("utf-8")).hexdigest()[:24],
        captured_at=float(time.monotonic() if captured_at is None else captured_at),
        navigation_generation=str(navigation_generation or ""),
        scroll_generation=max(0, int(scroll_generation or 0)),
    )


def validate_coordinate_frame_v1(
    frame: CoordinateFrameV1 | dict[str, Any] | None,
    *,
    consumer_size: tuple[int, int] | None,
    navigation_generation: str = "",
    scroll_generation: int | None = None,
    consumer_orientation: str = "",
    consumer_density: float | None = None,
) -> tuple[bool, str]:
    """Validate an exact transform; no numeric tolerance is permitted."""
    if frame is None or consumer_size is None:
        return False, "coordinate_frame_untrusted"
    payload = asdict(frame) if isinstance(frame, CoordinateFrameV1) else dict(frame)
    def _pair(value: Any) -> tuple[int, int]:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return int(value[0]), int(value[1])
        return 0, 0

    raw_size = _pair(payload.get("raw_window_size"))
    hierarchy_size = _pair(payload.get("hierarchy_viewport_size"))
    canonical_size = _pair(payload.get("canonical_content_size"))
    app_rect = dict(payload.get("app_content_rect") or {})
    insets = dict(payload.get("system_insets") or {})
    rebuilt = build_coordinate_frame_v1(
        source=str(payload.get("source") or ""),
        raw_width=raw_size[0],
        raw_height=raw_size[1],
        canonical_width=canonical_size[0],
        canonical_height=canonical_size[1],
        inset_left=int(insets.get("left") or 0),
        inset_top=int(insets.get("top") or 0),
        inset_right=int(insets.get("right") or 0),
        inset_bottom=int(insets.get("bottom") or 0),
        orientation=str(payload.get("orientation") or "portrait"),
        density=float(payload.get("density") or 0.0),
        captured_at=float(payload.get("captured_at") or 0.0),
        navigation_generation=str(payload.get("navigation_generation") or ""),
        scroll_generation=int(payload.get("scroll_generation") or 0),
    )
    frame_version = str(payload.get("transform_version") or "")
    if not frame_version:
        return False, "coordinate_frame_untrusted"
    if frame_version != "coordinate_frame_v1":
        return False, "coordinate_frame_transform_version_mismatch"
    if rebuilt is None or rebuilt.transform_hash != str(payload.get("transform_hash") or ""):
        return False, "coordinate_frame_untrusted"
    if hierarchy_size != rebuilt.hierarchy_viewport_size:
        return False, "coordinate_frame_viewport_mismatch"
    if app_rect != rebuilt.app_content_rect:
        return False, "coordinate_frame_inset_mismatch"
    if consumer_orientation and str(consumer_orientation) != rebuilt.orientation:
        return False, "coordinate_frame_viewport_mismatch"
    if (
        consumer_density is not None
        and float(consumer_density) != float(rebuilt.density)
    ):
        return False, "coordinate_frame_viewport_mismatch"
    if (
        navigation_generation
        and rebuilt.navigation_generation
        and str(navigation_generation) != rebuilt.navigation_generation
    ):
        return False, "coordinate_frame_stale"
    if (
        scroll_generation is not None
        and int(scroll_generation) != rebuilt.scroll_generation
    ):
        return False, "coordinate_frame_stale"
    consumer_w, consumer_h = int(consumer_size[0]), int(consumer_size[1])
    if consumer_w != rebuilt.raw_width:
        return False, "coordinate_frame_viewport_mismatch"
    if consumer_h != rebuilt.raw_height:
        expected_insets = int(rebuilt.system_insets.get("top") or 0) + int(
            rebuilt.system_insets.get("bottom") or 0
        )
        if consumer_h == rebuilt.canonical_height and expected_insets > 0:
            return False, "coordinate_frame_inset_mismatch"
        return False, "coordinate_frame_viewport_mismatch"
    if rebuilt.source == "raw_window_exact":
        return True, "coordinate_frame_exact_match"
    return True, "coordinate_frame_normalized_match"


@dataclass(frozen=True)
class PostGridEvidence:
    """Immutable, fully typed final decision produced at Mute-sheet close."""

    account_id: str
    candidate_username: str
    package_name: str
    activity_name: str
    mute_sheet_closed: bool
    mute_posts_verified: bool
    mute_stories_verified: bool
    profile_identity_method: str
    navigation_generation: str
    navigation_counter: int
    scroll_generation: int
    viewport_fingerprint: str
    screen_width: int
    screen_height: int
    grid_tab_state: str
    reels_tab_state: str
    tagged_tab_state: str
    post_count_positive: bool
    physical_post_cells: tuple[dict[str, int], ...]
    first_post_bounds: dict[str, int] | None
    first_post_cell_source: str
    outcome: str
    no_posts_positive: bool
    invalidation_counter: int
    created_at_monotonic: float
    ttl_ms: float
    invalidation_reason: str = ""
    rejection_reason: str = ""
    producer_screen_width: int = 0
    producer_screen_height: int = 0
    screen_dimensions_source: str = ""
    screen_inset_top: int = 0
    screen_inset_bottom: int = 0
    reveal_count_total_for_like_phase: int = 0
    coordinate_frame: dict[str, Any] = field(default_factory=dict)

    @property
    def post_bounds(self) -> dict[str, int] | None:
        return self.first_post_bounds

    @property
    def package(self) -> str:
        return self.package_name

    @property
    def activity(self) -> str:
        return self.activity_name

    @property
    def physical_cells(self) -> tuple[dict[str, int], ...]:
        return self.physical_post_cells

    @property
    def first_post_cell_bounds(self) -> dict[str, int] | None:
        return self.first_post_bounds

    @property
    def grid_visible(self) -> bool:
        return self.outcome in {
            POST_ROW_POSITIVE_SAFE,
            POST_ROW_POSITIVE_BUT_CLIPPED,
        }

    @property
    def visible_post_count(self) -> int:
        return len(self.physical_post_cells)


@dataclass(frozen=True)
class NextCandidateSnapshot:
    """Exact CT snapshot reusable only before any viewport mutation."""

    account_id: str
    source_profile_username: str
    package: str
    activity: str
    navigation_generation: str
    viewport_fingerprint: str
    navigation_counter: int
    scroll_counter: int
    invalidation_counter: int
    dedup_fingerprint: str
    created_at_monotonic: float
    ttl_ms: float
    detection: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Runtime:
    enabled: bool = False
    account_id: str = ""
    account_username: str = ""
    control_id: str = ""
    binding_version: str = ""
    run_id: str = ""
    attempt_id: int = 1
    natural_attempt: bool = True
    package: str = ""
    ui_generation: int = 0
    navigation_counter: int = 0
    scroll_counter: int = 0
    invalidation_counter: int = 0
    subflags: dict[str, bool] = field(default_factory=dict)
    proofs: dict[str, FreshUiProof] = field(default_factory=dict)
    candidate_verdicts: dict[str, CandidateProfileVerdict] = field(default_factory=dict)
    post_grid_evidence: dict[str, PostGridEvidence] = field(default_factory=dict)
    next_candidate_snapshot: NextCandidateSnapshot | None = None
    proof_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    optimization_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    terminal_optimization_outcomes: dict[str, str] = field(default_factory=dict)


_RUNTIME = _Runtime()


def configure(
    *,
    account_id: str,
    account_username: str,
    run_id: str,
    package: str,
    resume_policy: dict[str, Any] | None,
    control: dict[str, Any] | None = None,
    worker_sha: str = "",
    run_type: str = "account_session",
    request_id: str = "",
    business_session_id: str = "",
) -> bool:
    """Enable only the account selected by a fully bound canonical control."""
    global _RUNTIME
    policy = dict(resume_policy or {})
    one_shot_resume, one_shot_reject = _one_shot_resume_allowed(
        policy, account_id=str(account_id or "").strip()
    )
    attempt_id = int(policy.get("attempt_id") or (2 if one_shot_resume else 1))
    natural = not bool(policy)
    parent = _env_bool("FOLLOW_60S_CANARY_ENABLED", True)
    verdict = validate_runtime_binding(
        control,
        account_id=str(account_id or "").strip(),
        account_username=str(account_username or "").strip(),
        active_worker_sha=str(worker_sha or "").strip(),
        run_type=str(run_type or "").strip(),
        package=str(package or "").strip(),
        run_id=str(run_id or "").strip(),
        request_id=str(request_id or "").strip(),
        attempt_id=attempt_id,
        business_session_id=str(business_session_id or "").strip(),
    )
    enabled = bool(
        parent
        and verdict.valid
        and ((natural and attempt_id == 1) or one_shot_resume)
    )
    binding: Follow60CanaryBindingV2 | None = verdict.binding
    subflags = {
        name: _env_bool(f"FOLLOW_60S_CANARY_{name.upper()}", True)
        for name in _SUBFLAG_NAMES
    }
    _RUNTIME = _Runtime(
        enabled=enabled,
        account_id=str(account_id or "").strip(),
        account_username=str(account_username or "").strip().lstrip("@"),
        control_id=(binding.control_id if binding else ""),
        binding_version=(binding.binding_version if binding else ""),
        run_id=str(run_id or "").strip(),
        attempt_id=attempt_id,
        natural_attempt=natural,
        package=str(package or "").strip(),
        subflags=subflags,
    )
    log(
        "info",
        "follow_60s_canary_runtime_configured",
        enabled=enabled,
        account_id=_RUNTIME.account_id or None,
        account_username=_RUNTIME.account_username or None,
        run_id=_RUNTIME.run_id or None,
        attempt_id=attempt_id,
        natural_attempt=natural,
        auto_restart_resume=bool(policy),
        one_shot_canary_resume=one_shot_resume,
        one_shot_source_run_id=(str(policy.get("prior_run_id") or "").strip() if one_shot_resume else None),
        one_shot_expires_at=(
            str(
                dict(
                    dict(policy.get("frozen_phase_plan") or {}).get(
                        "follow_60s_canary_contract"
                    )
                    or {}
                ).get("expires_at")
                or ""
            )
            if one_shot_resume
            else None
        ),
        one_shot_rejection_reason=(one_shot_reject if policy and not one_shot_resume else None),
        package=_RUNTIME.package or None,
        subflags=subflags,
        fallback="golden_current" if not enabled else None,
        control_id=_RUNTIME.control_id or None,
        binding_version=_RUNTIME.binding_version or None,
        binding_rejection_reason=(None if verdict.valid else verdict.reason),
    )
    return enabled


def enabled(subflag: str | None = None) -> bool:
    if not _RUNTIME.enabled:
        return False
    return True if not subflag else bool(_RUNTIME.subflags.get(subflag, False))


def enabled_for_account(account_id: str | None) -> bool:
    """Return true only for the account in the validated runtime binding."""
    return bool(
        _RUNTIME.enabled
        and _RUNTIME.account_id
        and _RUNTIME.account_id == str(account_id or "").strip()
    )


def runtime_context() -> dict[str, Any]:
    return {
        "enabled": _RUNTIME.enabled,
        "account_id": _RUNTIME.account_id,
        "account_username": _RUNTIME.account_username,
        "run_id": _RUNTIME.run_id,
        "attempt_id": _RUNTIME.attempt_id,
        "natural_attempt": _RUNTIME.natural_attempt,
        "package": _RUNTIME.package,
        "ui_generation": _RUNTIME.ui_generation,
        "navigation_counter": _RUNTIME.navigation_counter,
        "scroll_counter": _RUNTIME.scroll_counter,
        "invalidation_counter": _RUNTIME.invalidation_counter,
        "subflags": dict(_RUNTIME.subflags),
    }


def xml_fingerprint(xml: str) -> str:
    value = str(xml or "")
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:20] if value else ""


def _count(purpose: str, key: str) -> None:
    bucket = _RUNTIME.proof_stats.setdefault(str(purpose), {})
    bucket[key] = int(bucket.get(key) or 0) + 1


def stash(
    purpose: str,
    *,
    subject_username: str,
    target_username: str,
    package: str,
    activity: str,
    surface: str,
    detection_source: str,
    ttl_ms: float,
    bounds: dict[str, int] | None = None,
    xml: str = "",
    metadata: dict[str, Any] | None = None,
) -> FreshUiProof | None:
    if not enabled():
        return None
    proof = FreshUiProof(
        account_id=_RUNTIME.account_id,
        subject_username=str(subject_username or "").strip().lstrip("@").lower(),
        target_username=str(target_username or "").strip().lstrip("@").lower(),
        package=str(package or "").strip(),
        activity=str(activity or "").strip(),
        surface=str(surface or "").strip(),
        bounds=dict(bounds) if isinstance(bounds, dict) else None,
        xml_generation=f"{_RUNTIME.ui_generation}:{xml_fingerprint(xml)}",
        created_at_monotonic=time.monotonic(),
        detection_source=str(detection_source or "").strip(),
        ttl_ms=max(1.0, float(ttl_ms)),
        metadata=dict(metadata or {}),
    )
    _RUNTIME.proofs[str(purpose)] = proof
    _count(purpose, "stashed")
    log(
        "info",
        "follow_60s_fresh_ui_proof_stashed",
        purpose=str(purpose),
        proof=asdict(proof),
        proof_age_ms=0.0,
        fallback_used=False,
    )
    return proof


def consume(
    purpose: str,
    *,
    subject_username: str = "",
    target_username: str = "",
    package: str = "",
    activity: str = "",
    surface: str = "",
    require_safe_bounds: bool = False,
    screen_size: tuple[int, int] | None = None,
    consume_once: bool = False,
    metadata_equals: dict[str, Any] | None = None,
) -> tuple[FreshUiProof | None, float, str]:
    proof = _RUNTIME.proofs.get(str(purpose)) if enabled() else None
    reason = ""
    age_ms = 0.0
    if proof is None:
        reason = "missing_proof"
    else:
        age_ms = max(0.0, (time.monotonic() - proof.created_at_monotonic) * 1000.0)
        expected = {
            "account_id": _RUNTIME.account_id,
            "subject_username": str(subject_username or "").strip().lstrip("@").lower(),
            "target_username": str(target_username or "").strip().lstrip("@").lower(),
            "package": str(package or "").strip(),
            "activity": str(activity or "").strip(),
            "surface": str(surface or "").strip(),
        }
        for key, want in expected.items():
            got = str(getattr(proof, key) or "")
            if want and got != want:
                reason = f"{key}_mismatch"
                break
        if not reason and proof.invalidation_reason:
            reason = f"invalidated:{proof.invalidation_reason}"
        if not reason and age_ms > proof.ttl_ms:
            reason = "ttl_expired"
        if not reason and not proof.xml_generation.startswith(f"{_RUNTIME.ui_generation}:"):
            reason = "ui_generation_mismatch"
        if not reason and metadata_equals:
            for key, want in metadata_equals.items():
                if want is None or str(want) == "":
                    continue
                if str(proof.metadata.get(key) or "") != str(want):
                    reason = f"metadata_{key}_mismatch"
                    break
        if not reason and require_safe_bounds:
            ok, bounds_reason = safe_bounds(proof.bounds, screen_size=screen_size)
            if not ok:
                reason = bounds_reason
    if reason:
        _count(purpose, "rejected")
        log(
            "info",
            "follow_60s_fresh_ui_proof_rejected",
            purpose=str(purpose),
            proof_age_ms=round(age_ms, 2),
            rejection_reason=reason,
            fallback_used=True,
            dumps=0,
            screenshots=0,
            retries=0,
        )
        return None, age_ms, reason
    _count(purpose, "reused")
    if consume_once:
        _RUNTIME.proofs.pop(str(purpose), None)
    log(
        "info",
        "follow_60s_fresh_ui_proof_reused",
        purpose=str(purpose),
        proof_age_ms=round(age_ms, 2),
        rejection_reason="",
        fallback_used=False,
        dumps=0,
        screenshots=0,
        retries=0,
    )
    return proof, age_ms, ""


def record_outcome(
    feature: str,
    status: str,
    *,
    age_ms: float | None = None,
    reason: str = "",
    fallback_used: bool = False,
    dumps: int = 0,
    screenshots: int = 0,
    retries: int = 0,
    estimated_gain_ms: float = 0.0,
) -> None:
    """Emit one account-scoped optimization decision and aggregate final stats."""
    if not enabled(feature):
        return
    normalized = str(status or "rejected").strip().lower()
    if normalized not in {"used", "rejected", "fallback"}:
        normalized = "rejected"
    bucket = _RUNTIME.optimization_stats.setdefault(
        str(feature),
        {
            "used": 0,
            "rejected": 0,
            "fallback": 0,
            "dumps": 0,
            "screenshots": 0,
            "retries": 0,
            "estimated_gain_ms": 0.0,
        },
    )
    bucket[normalized] = int(bucket.get(normalized) or 0) + 1
    if fallback_used and normalized != "fallback":
        bucket["fallback"] = int(bucket.get("fallback") or 0) + 1
    bucket["dumps"] = int(bucket.get("dumps") or 0) + max(0, int(dumps))
    bucket["screenshots"] = int(bucket.get("screenshots") or 0) + max(
        0, int(screenshots)
    )
    bucket["retries"] = int(bucket.get("retries") or 0) + max(0, int(retries))
    bucket["estimated_gain_ms"] = round(
        float(bucket.get("estimated_gain_ms") or 0.0)
        + max(0.0, float(estimated_gain_ms or 0.0)),
        2,
    )
    log(
        "info",
        "follow_60s_optimization_status",
        feature=str(feature),
        status=normalized,
        proof_age_ms=None if age_ms is None else round(float(age_ms), 2),
        rejection_reason=str(reason or "") if normalized != "used" else "",
        fallback_used=bool(fallback_used),
        dumps=max(0, int(dumps)),
        screenshots=max(0, int(screenshots)),
        retries=max(0, int(retries)),
        estimated_gain_ms=round(max(0.0, float(estimated_gain_ms or 0.0)), 2),
    )


def record_terminal_outcome(
    feature: str,
    *,
    candidate_username: str,
    status: str,
    age_ms: float | None = None,
    reason: str = "",
    fallback_used: bool = False,
    estimated_gain_ms: float = 0.0,
) -> bool:
    """Publish exactly one terminal optimization verdict per candidate/feature."""
    if not enabled(feature):
        return False
    candidate = str(candidate_username or "").strip().lstrip("@").lower()
    if not candidate:
        return False
    key = f"{feature}:{candidate}"
    prior = _RUNTIME.terminal_optimization_outcomes.get(key)
    if prior:
        log(
            "info",
            "follow_60s_terminal_optimization_outcome_deduplicated",
            feature=str(feature),
            candidate_username=candidate,
            prior_status=prior,
            ignored_status=str(status or ""),
            ignored_reason=str(reason or ""),
        )
        return False
    normalized = str(status or "rejected").strip().lower()
    if normalized not in {"used", "rejected", "fallback"}:
        normalized = "rejected"
    _RUNTIME.terminal_optimization_outcomes[key] = normalized
    record_outcome(
        feature,
        normalized,
        age_ms=age_ms,
        reason=reason,
        fallback_used=fallback_used,
        estimated_gain_ms=estimated_gain_ms,
    )
    log(
        "info",
        "follow_60s_terminal_optimization_outcome",
        feature=str(feature),
        candidate_username=candidate,
        status=normalized,
        rejection_reason=str(reason or "") if normalized != "used" else "",
        fallback_used=bool(fallback_used),
    )
    return True


def safe_bounds(
    bounds: dict[str, int] | None,
    *,
    screen_size: tuple[int, int] | None,
) -> tuple[bool, str]:
    """Same conservative geometry/fresh-hit principle as Unfollow exact-row proof."""
    if not isinstance(bounds, dict) or screen_size is None:
        return False, "bounds_or_screen_missing"
    try:
        left, top, right, bottom = (int(bounds[k]) for k in ("left", "top", "right", "bottom"))
        width, height = int(screen_size[0]), int(screen_size[1])
    except (KeyError, TypeError, ValueError):
        return False, "bounds_parse_failed"
    if not (width > 0 and height > 0 and 0 <= left < right <= width and 0 <= top < bottom <= height):
        return False, "bounds_geometry_invalid"
    cx, cy = (left + right) // 2, (top + bottom) // 2
    if cx > int(width * 0.72) or cy > int(height * 0.90):
        return False, "bounds_hit_region_unsafe"
    return True, ""


def invalidate(reason: str, *, bump_generation: bool = True) -> None:
    if not enabled():
        return
    reason_s = str(reason or "unknown_ui_mutation")
    if bump_generation:
        _RUNTIME.ui_generation += 1
    _RUNTIME.invalidation_counter += 1
    lowered_reason = reason_s.lower()
    geometry_only = "scroll" in lowered_reason or "swipe" in lowered_reason
    if geometry_only:
        _RUNTIME.scroll_counter += 1
    else:
        _RUNTIME.navigation_counter += 1
    invalidated = len(_RUNTIME.proofs)
    _RUNTIME.proofs = {
        purpose: replace(proof, invalidation_reason=reason_s)
        for purpose, proof in _RUNTIME.proofs.items()
    }
    if geometry_only:
        # A same-profile scroll invalidates geometry, not the already proven
        # identity and Mute semantics for this candidate.
        _RUNTIME.candidate_verdicts = {
            key: replace(
                verdict,
                navigation_generation="",
                viewport_fingerprint="",
                post_grid_outcome="",
                post_bounds=None,
                invalidation_counter=_RUNTIME.invalidation_counter,
            )
            for key, verdict in _RUNTIME.candidate_verdicts.items()
        }
    else:
        _RUNTIME.candidate_verdicts.clear()
    _RUNTIME.post_grid_evidence.clear()
    _RUNTIME.next_candidate_snapshot = None
    log(
        "info",
        "follow_60s_fresh_ui_proofs_invalidated",
        invalidation_reason=reason_s,
        invalidated_count=invalidated,
        ui_generation=_RUNTIME.ui_generation,
        navigation_counter=_RUNTIME.navigation_counter,
        scroll_counter=_RUNTIME.scroll_counter,
        invalidation_counter=_RUNTIME.invalidation_counter,
    )


def stats() -> dict[str, Any]:
    return {
        "enabled": _RUNTIME.enabled,
        "ui_generation": _RUNTIME.ui_generation,
        "navigation_counter": _RUNTIME.navigation_counter,
        "scroll_counter": _RUNTIME.scroll_counter,
        "invalidation_counter": _RUNTIME.invalidation_counter,
        "proof_counts": {key: dict(value) for key, value in _RUNTIME.proof_stats.items()},
        "optimization_counts": {
            key: dict(value) for key, value in _RUNTIME.optimization_stats.items()
        },
        "live_proof_count": len(_RUNTIME.proofs),
        "live_candidate_verdict_count": len(_RUNTIME.candidate_verdicts),
        "live_post_grid_evidence_count": len(_RUNTIME.post_grid_evidence),
        "live_next_candidate_snapshot": _RUNTIME.next_candidate_snapshot is not None,
    }


def create_candidate_profile_verdict(
    *, candidate_username: str, package: str, activity: str,
    navigation_generation: str, exact_identity: bool, sheet_closed: bool,
    mute_posts_verified: bool, mute_stories_verified: bool, ttl_ms: float = 2500.0,
    viewport_fingerprint: str = "", post_grid_outcome: str = "",
    post_bounds: dict[str, int] | None = None, no_posts_positive: bool = False,
) -> CandidateProfileVerdict | None:
    if not enabled("mute_like_handoff"):
        return None
    candidate = str(candidate_username or "").strip().lstrip("@").lower()
    if not candidate or not all((exact_identity, sheet_closed, mute_posts_verified, mute_stories_verified)):
        log(
            "info",
            "mute_to_like_candidate_verdict_rejected_nonterminal",
            candidate_username=candidate or None,
            rejection_reason="candidate_verdict_incomplete",
            final_outcome_deferred=True,
        )
        return None
    verdict = CandidateProfileVerdict(
        account_id=_RUNTIME.account_id, candidate_username=candidate,
        package=str(package or ""), activity=str(activity or ""),
        navigation_generation=str(navigation_generation or ""), exact_identity=True,
        sheet_closed=True, mute_posts_verified=True, mute_stories_verified=True,
        viewport_fingerprint=str(viewport_fingerprint or ""),
        post_grid_outcome=str(post_grid_outcome or ""),
        post_bounds=dict(post_bounds) if isinstance(post_bounds, dict) else None,
        no_posts_positive=bool(no_posts_positive),
        invalidation_counter=_RUNTIME.invalidation_counter,
        created_at_monotonic=time.monotonic(), ttl_ms=max(1.0, float(ttl_ms)),
    )
    _RUNTIME.candidate_verdicts[candidate] = verdict
    _count("candidate_profile_verdict", "created")
    return verdict


def get_candidate_profile_verdict(
    *, candidate_username: str, package: str = "", activity: str = "",
    navigation_generation: str = "",
) -> tuple[CandidateProfileVerdict | None, float, str]:
    candidate = str(candidate_username or "").strip().lstrip("@").lower()
    verdict = _RUNTIME.candidate_verdicts.get(candidate) if enabled("mute_like_handoff") else None
    age_ms = 0.0
    reason = "missing_verdict"
    if verdict is not None:
        age_ms = max(0.0, (time.monotonic() - verdict.created_at_monotonic) * 1000.0)
        checks = ((verdict.account_id, _RUNTIME.account_id, "account"),
                  (verdict.candidate_username, candidate, "candidate"),
                  (verdict.package, str(package or ""), "package"),
                  (verdict.activity, str(activity or ""), "activity"))
        reason = ""
        for got, want, name in checks:
            if want and got != want:
                reason = f"{name}_mismatch"
                break
        if (
            not reason
            and verdict.navigation_generation
            and str(navigation_generation or "")
            and verdict.navigation_generation != str(navigation_generation or "")
        ):
            reason = "navigation_generation_mismatch"
        if not reason and age_ms > verdict.ttl_ms:
            reason = "ttl_expired"
        if not reason and verdict.invalidation_counter != _RUNTIME.invalidation_counter:
            reason = "invalidation_counter_mismatch"
    _count("candidate_profile_verdict", "rejected" if reason else "reused")
    return (None if reason else verdict), age_ms, reason


def stash_post_grid_evidence(
    *, candidate_username: str, package_name: str, activity_name: str,
    navigation_generation: str, viewport_fingerprint: str, outcome: str,
    mute_sheet_closed: bool, mute_posts_verified: bool,
    mute_stories_verified: bool, profile_identity_method: str,
    screen_width: int, screen_height: int, grid_tab_state: str,
    reels_tab_state: str = "not_selected", tagged_tab_state: str = "not_selected",
    post_count_positive: bool = False,
    physical_post_cells: list[dict[str, int]] | tuple[dict[str, int], ...] = (),
    first_post_bounds: dict[str, int] | None = None,
    first_post_cell_source: str = "", no_posts_positive: bool = False,
    rejection_reason: str = "", ttl_ms: float = 3000.0,
    producer_screen_width: int = 0, producer_screen_height: int = 0,
    screen_dimensions_source: str = "", screen_inset_top: int = 0,
    screen_inset_bottom: int = 0,
    reveal_count_total_for_like_phase: int = 0,
    coordinate_frame: CoordinateFrameV1 | dict[str, Any] | None = None,
) -> PostGridEvidence | None:
    if not enabled("like_fresh_cell_bounds"):
        return None
    candidate = str(candidate_username or "").strip().lstrip("@").lower()
    normalized = str(outcome or "").strip()
    if not candidate or normalized not in POST_GRID_CANONICAL_OUTCOMES:
        return None
    package_exact = str(package_name or "").strip() == str(_RUNTIME.package or "").strip()
    activity_compatible = "InstagramMainActivity" in str(activity_name or "")
    if not package_exact or not activity_compatible:
        return None
    if not all((mute_sheet_closed, mute_posts_verified, mute_stories_verified)):
        return None
    cells = tuple(
        dict(cell) for cell in physical_post_cells if isinstance(cell, dict)
    )
    bounds = (
        dict(first_post_bounds)
        if isinstance(first_post_bounds, dict)
        else None
    )
    post_count_positive = bool(post_count_positive or cells)
    typed_frame = (
        asdict(coordinate_frame)
        if isinstance(coordinate_frame, CoordinateFrameV1)
        else dict(coordinate_frame or {})
    )
    if not typed_frame:
        legacy_frame = build_coordinate_frame_v1(
            source=str(screen_dimensions_source or "raw_window_exact"),
            raw_width=int(producer_screen_width or screen_width),
            raw_height=int(producer_screen_height or screen_height),
            canonical_width=int(screen_width),
            canonical_height=int(screen_height),
            inset_top=int(screen_inset_top or 0),
            inset_bottom=int(screen_inset_bottom or 0),
        )
        typed_frame = asdict(legacy_frame) if legacy_frame is not None else {}
    evidence = PostGridEvidence(
        account_id=_RUNTIME.account_id, candidate_username=candidate,
        package_name=str(package_name or ""), activity_name=str(activity_name or ""),
        mute_sheet_closed=True,
        mute_posts_verified=True,
        mute_stories_verified=True,
        profile_identity_method=str(profile_identity_method or ""),
        navigation_generation=str(navigation_generation or ""),
        navigation_counter=_RUNTIME.navigation_counter,
        scroll_generation=_RUNTIME.scroll_counter,
        viewport_fingerprint=str(viewport_fingerprint or ""),
        screen_width=max(1, int(screen_width)),
        screen_height=max(1, int(screen_height)),
        grid_tab_state=str(grid_tab_state or ""),
        reels_tab_state=str(reels_tab_state or ""),
        tagged_tab_state=str(tagged_tab_state or ""),
        post_count_positive=bool(post_count_positive),
        physical_post_cells=cells,
        first_post_bounds=bounds,
        first_post_cell_source=str(first_post_cell_source or ""),
        outcome=normalized,
        no_posts_positive=bool(
            normalized == NO_POSTS_POSITIVE and no_posts_positive
        ),
        invalidation_counter=_RUNTIME.invalidation_counter,
        created_at_monotonic=time.monotonic(), ttl_ms=max(1.0, float(ttl_ms)),
        rejection_reason=str(rejection_reason or ""),
        producer_screen_width=max(1, int(producer_screen_width or screen_width)),
        producer_screen_height=max(1, int(producer_screen_height or screen_height)),
        screen_dimensions_source=str(screen_dimensions_source or "raw_window_exact"),
        screen_inset_top=max(0, int(screen_inset_top or 0)),
        screen_inset_bottom=max(0, int(screen_inset_bottom or 0)),
        reveal_count_total_for_like_phase=max(
            0, int(reveal_count_total_for_like_phase or 0)
        ),
        coordinate_frame=typed_frame,
    )
    _RUNTIME.post_grid_evidence[candidate] = evidence
    _count("post_grid_evidence", "created")
    return evidence


def consume_post_grid_evidence(
    *, candidate_username: str, package: str = "", activity: str = "",
    navigation_generation: str = "", viewport_fingerprint: str = "",
    screen_size: tuple[int, int] | None = None,
    screen_dimensions_source: str = "",
    producer_fingerprint: str = "",
    screen_orientation: str = "",
    screen_density: float | None = None,
) -> tuple[PostGridEvidence | None, float, str]:
    candidate = str(candidate_username or "").strip().lstrip("@").lower()
    ev = _RUNTIME.post_grid_evidence.pop(candidate, None) if enabled("like_fresh_cell_bounds") else None
    age_ms = 0.0
    reason = "missing_evidence"
    if ev is not None:
        age_ms = max(0.0, (time.monotonic() - ev.created_at_monotonic) * 1000.0)
        reason = ""
        for got, want, name in ((ev.account_id, _RUNTIME.account_id, "account"),
                                (ev.package, str(package or ""), "package"),
                                (ev.activity, str(activity or ""), "activity"),
                                (ev.navigation_generation, str(navigation_generation or ""), "navigation_generation"),
                                (ev.viewport_fingerprint, str(viewport_fingerprint or ""), "viewport")):
            if want and got != want:
                reason = f"{name}_mismatch"
                break
        if not reason and ev.package_name != str(_RUNTIME.package or ""):
            reason = "package_not_instagram_exact"
        if not reason and "InstagramMainActivity" not in ev.activity_name:
            reason = "activity_not_instagram_main"
        if not reason and age_ms > ev.ttl_ms:
            reason = "coordinate_frame_stale"
        if not reason and ev.invalidation_counter != _RUNTIME.invalidation_counter:
            reason = "invalidation_counter_mismatch"
        if not reason and ev.navigation_counter != _RUNTIME.navigation_counter:
            reason = "navigation_counter_mismatch"
        if not reason and ev.scroll_generation != _RUNTIME.scroll_counter:
            reason = "scroll_generation_mismatch"
        dimensions_reason = "coordinate_frame_untrusted"
        if not reason:
            frame_ok, frame_reason = validate_coordinate_frame_v1(
                ev.coordinate_frame,
                consumer_size=screen_size,
                navigation_generation=str(navigation_generation or ""),
                scroll_generation=_RUNTIME.scroll_counter,
                consumer_orientation=str(screen_orientation or ""),
                consumer_density=screen_density,
            )
            if not frame_ok:
                reason = frame_reason
            else:
                consumer_w, consumer_h = int(screen_size[0]), int(screen_size[1])
                producer_w = int(ev.producer_screen_width or ev.screen_width)
                producer_h = int(ev.producer_screen_height or ev.screen_height)
                canonical_w, canonical_h = int(ev.screen_width), int(ev.screen_height)
                fingerprint_value = str(
                    producer_fingerprint or viewport_fingerprint or ""
                )
                # A producer fingerprint strengthens continuity when present.
                # Older/Golden-compatible producers may not emit one; exact
                # trusted coordinate-frame equality remains sufficient then.
                fingerprint_trusted = bool(
                    (not ev.viewport_fingerprint and not fingerprint_value)
                    or (
                        bool(fingerprint_value)
                        and fingerprint_value == ev.viewport_fingerprint
                    )
                )
                if not fingerprint_trusted:
                    reason = "coordinate_frame_untrusted"
                else:
                    dimensions_reason = frame_reason
                log(
                    "info",
                    "follow_60s_post_grid_screen_dimensions_checked",
                    candidate_username=candidate,
                    producer_raw_width=producer_w,
                    producer_raw_height=producer_h,
                    producer_canonical_width=canonical_w,
                    producer_canonical_height=canonical_h,
                    producer_source=ev.screen_dimensions_source,
                    producer_inset_top=int(ev.screen_inset_top),
                    producer_inset_bottom=int(ev.screen_inset_bottom),
                    consumer_width=consumer_w,
                    consumer_height=consumer_h,
                    consumer_source=str(screen_dimensions_source or ""),
                    viewport_fingerprint_match=fingerprint_trusted,
                    coordinate_frame_version=str(
                        ev.coordinate_frame.get("transform_version") or ""
                    ),
                    coordinate_frame_hash=str(ev.coordinate_frame.get("transform_hash") or ""),
                    dimensions_reason=(reason or dimensions_reason),
                    accepted=not bool(reason),
                )
        if not reason and ev.outcome == NO_POSTS_POSITIVE and not ev.no_posts_positive:
            reason = "no_posts_not_positive"
        if not reason and ev.outcome == POST_ROW_POSITIVE_SAFE:
            ok, bounds_reason = safe_bounds(
                ev.first_post_cell_bounds,
                screen_size=(int(ev.screen_width), int(ev.screen_height)),
            )
            if not ok:
                reason = bounds_reason
    _count("post_grid_evidence", "rejected" if reason else "reused")
    return (None if reason else ev), age_ms, reason


def stash_next_candidate_snapshot(
    *, source_profile_username: str, package: str, activity: str,
    navigation_generation: str, viewport_fingerprint: str,
    detection: dict[str, Any], ttl_ms: float = 2500.0,
) -> NextCandidateSnapshot | None:
    if not enabled("post_return_snapshot_reuse") or not detection:
        return None
    snapshot = NextCandidateSnapshot(
        account_id=_RUNTIME.account_id,
        source_profile_username=str(source_profile_username or "").strip().lstrip("@").lower(),
        package=str(package or ""), activity=str(activity or ""),
        navigation_generation=str(navigation_generation or ""),
        viewport_fingerprint=str(viewport_fingerprint or ""),
        navigation_counter=_RUNTIME.navigation_counter,
        scroll_counter=_RUNTIME.scroll_counter,
        invalidation_counter=_RUNTIME.invalidation_counter,
        dedup_fingerprint=str(
            detection.get("dedup_fingerprint")
            or detection.get("candidate_fingerprint")
            or detection.get("list_fingerprint")
            or ""
        ),
        created_at_monotonic=time.monotonic(), ttl_ms=max(1.0, float(ttl_ms)),
        detection=dict(detection),
    )
    _RUNTIME.next_candidate_snapshot = snapshot
    _count("next_candidate_snapshot", "created")
    return snapshot


def consume_next_candidate_snapshot(
    *, source_profile_username: str, package: str = "", activity: str = "",
    navigation_generation: str = "", viewport_fingerprint: str = "",
) -> tuple[NextCandidateSnapshot | None, float, str]:
    snap = _RUNTIME.next_candidate_snapshot if enabled("post_return_snapshot_reuse") else None
    _RUNTIME.next_candidate_snapshot = None
    age_ms = 0.0
    reason = "missing_snapshot"
    if snap is not None:
        age_ms = max(0.0, (time.monotonic() - snap.created_at_monotonic) * 1000.0)
        reason = ""
        expected_source = str(source_profile_username or "").strip().lstrip("@").lower()
        for got, want, name in ((snap.account_id, _RUNTIME.account_id, "account"),
                                (snap.source_profile_username, expected_source, "source"),
                                (snap.package, str(package or ""), "package"),
                                (snap.activity, str(activity or ""), "activity"),
                                (snap.navigation_generation, str(navigation_generation or ""), "navigation_generation"),
                                (snap.viewport_fingerprint, str(viewport_fingerprint or ""), "viewport")):
            if want and got != want:
                reason = f"{name}_mismatch"
                break
        if not reason and age_ms > snap.ttl_ms:
            reason = "ttl_expired"
        if not reason and snap.navigation_counter != _RUNTIME.navigation_counter:
            reason = "navigation_counter_mismatch"
        if not reason and snap.scroll_counter != _RUNTIME.scroll_counter:
            reason = "scroll_counter_mismatch"
        if not reason and snap.invalidation_counter != _RUNTIME.invalidation_counter:
            reason = "invalidation_counter_mismatch"
    _count("next_candidate_snapshot", "rejected" if reason else "reused")
    return (None if reason else snap), age_ms, reason
