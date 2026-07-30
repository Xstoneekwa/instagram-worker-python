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
from typing import Any

from logs import log


REX_ACCOUNT_ID = "b024e94e-395d-4f02-9787-81ddc679b014"

_SUBFLAG_NAMES = (
    "opening_follow_composite",
    "mute_known_depth",
    "mute_like_handoff",
    "like_fresh_cell_bounds",
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
    created_at_monotonic: float
    ttl_ms: float


@dataclass(frozen=True)
class PostGridEvidence:
    """One-capture decision for the post-grid branch."""

    account_id: str
    candidate_username: str
    package: str
    activity: str
    navigation_generation: str
    viewport_fingerprint: str
    outcome: str
    post_bounds: dict[str, int] | None
    created_at_monotonic: float
    ttl_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NextCandidateSnapshot:
    """Exact CT snapshot reusable only before any viewport mutation."""

    account_id: str
    source_profile_username: str
    package: str
    activity: str
    navigation_generation: str
    viewport_fingerprint: str
    created_at_monotonic: float
    ttl_ms: float
    detection: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Runtime:
    enabled: bool = False
    account_id: str = ""
    account_username: str = ""
    run_id: str = ""
    attempt_id: int = 1
    natural_attempt: bool = True
    package: str = ""
    ui_generation: int = 0
    subflags: dict[str, bool] = field(default_factory=dict)
    proofs: dict[str, FreshUiProof] = field(default_factory=dict)
    candidate_verdicts: dict[str, CandidateProfileVerdict] = field(default_factory=dict)
    post_grid_evidence: dict[str, PostGridEvidence] = field(default_factory=dict)
    next_candidate_snapshot: NextCandidateSnapshot | None = None
    proof_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    optimization_stats: dict[str, dict[str, Any]] = field(default_factory=dict)


_RUNTIME = _Runtime()


def configure(
    *,
    account_id: str,
    account_username: str,
    run_id: str,
    package: str,
    resume_policy: dict[str, Any] | None,
) -> bool:
    """Enable only Rex's first natural business-session attempt."""
    global _RUNTIME
    policy = dict(resume_policy or {})
    attempt_id = int(policy.get("attempt_id") or 1)
    natural = not bool(policy)
    parent = _env_bool("FOLLOW_60S_CANARY_ENABLED", True)
    enabled = bool(
        parent
        and str(account_id or "").strip() == REX_ACCOUNT_ID
        and natural
        and attempt_id == 1
    )
    subflags = {
        name: _env_bool(f"FOLLOW_60S_CANARY_{name.upper()}", True)
        for name in _SUBFLAG_NAMES
    }
    _RUNTIME = _Runtime(
        enabled=enabled,
        account_id=str(account_id or "").strip(),
        account_username=str(account_username or "").strip().lstrip("@"),
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
        package=_RUNTIME.package or None,
        subflags=subflags,
        fallback="golden_current" if not enabled else None,
    )
    return enabled


def enabled(subflag: str | None = None) -> bool:
    if not _RUNTIME.enabled:
        return False
    return True if not subflag else bool(_RUNTIME.subflags.get(subflag, False))


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
    invalidated = len(_RUNTIME.proofs)
    _RUNTIME.proofs = {
        purpose: replace(proof, invalidation_reason=reason_s)
        for purpose, proof in _RUNTIME.proofs.items()
    }
    _RUNTIME.candidate_verdicts.clear()
    _RUNTIME.post_grid_evidence.clear()
    _RUNTIME.next_candidate_snapshot = None
    log(
        "info",
        "follow_60s_fresh_ui_proofs_invalidated",
        invalidation_reason=reason_s,
        invalidated_count=invalidated,
        ui_generation=_RUNTIME.ui_generation,
    )


def stats() -> dict[str, Any]:
    return {
        "enabled": _RUNTIME.enabled,
        "ui_generation": _RUNTIME.ui_generation,
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
) -> CandidateProfileVerdict | None:
    if not enabled("mute_like_handoff"):
        return None
    candidate = str(candidate_username or "").strip().lstrip("@").lower()
    if not candidate or not all((exact_identity, sheet_closed, mute_posts_verified, mute_stories_verified)):
        record_outcome("mute_like_handoff", "rejected", reason="candidate_verdict_incomplete", fallback_used=True)
        return None
    verdict = CandidateProfileVerdict(
        account_id=_RUNTIME.account_id, candidate_username=candidate,
        package=str(package or ""), activity=str(activity or ""),
        navigation_generation=str(navigation_generation or ""), exact_identity=True,
        sheet_closed=True, mute_posts_verified=True, mute_stories_verified=True,
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
                  (verdict.activity, str(activity or ""), "activity"),
                  (verdict.navigation_generation, str(navigation_generation or ""), "navigation_generation"))
        reason = ""
        for got, want, name in checks:
            if want and got != want:
                reason = f"{name}_mismatch"
                break
        if not reason and age_ms > verdict.ttl_ms:
            reason = "ttl_expired"
    _count("candidate_profile_verdict", "rejected" if reason else "reused")
    return (None if reason else verdict), age_ms, reason


def stash_post_grid_evidence(
    *, candidate_username: str, package: str, activity: str,
    navigation_generation: str, viewport_fingerprint: str, outcome: str,
    post_bounds: dict[str, int] | None = None, ttl_ms: float = 1500.0,
    metadata: dict[str, Any] | None = None,
) -> PostGridEvidence | None:
    if not enabled("like_fresh_cell_bounds"):
        return None
    candidate = str(candidate_username or "").strip().lstrip("@").lower()
    normalized = str(outcome or "").strip()
    if not candidate or normalized not in {"safe_post", "no_posts", "ambiguous"}:
        return None
    evidence = PostGridEvidence(
        account_id=_RUNTIME.account_id, candidate_username=candidate,
        package=str(package or ""), activity=str(activity or ""),
        navigation_generation=str(navigation_generation or ""),
        viewport_fingerprint=str(viewport_fingerprint or ""), outcome=normalized,
        post_bounds=dict(post_bounds) if isinstance(post_bounds, dict) else None,
        created_at_monotonic=time.monotonic(), ttl_ms=max(1.0, float(ttl_ms)),
        metadata=dict(metadata or {}),
    )
    _RUNTIME.post_grid_evidence[candidate] = evidence
    _count("post_grid_evidence", "created")
    return evidence


def consume_post_grid_evidence(
    *, candidate_username: str, package: str = "", activity: str = "",
    navigation_generation: str = "", viewport_fingerprint: str = "",
    screen_size: tuple[int, int] | None = None,
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
        if not reason and age_ms > ev.ttl_ms:
            reason = "ttl_expired"
        if not reason and ev.outcome == "safe_post":
            ok, bounds_reason = safe_bounds(ev.post_bounds, screen_size=screen_size)
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
    _count("next_candidate_snapshot", "rejected" if reason else "reused")
    return (None if reason else snap), age_ms, reason
