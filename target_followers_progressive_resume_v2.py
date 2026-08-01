"""Persistent, evidence-gated resume checkpoints for target Followers lists.

The module is deliberately independent from the follow business flow.  It can
observe the legacy navigation in shadow mode and provides the state machine
needed by a future, separately approved enforcement integration.

Safety invariants:

* a depth unit is a proven transition between two distinct viewports of the
  expected target's committed Followers list;
* sending a swipe never advances a checkpoint;
* shadow and enforce progress are stored separately;
* an unclaimed or stale optimistic version can never be committed;
* anchors are bounded hashes, never raw follower lists, XML, or screenshots.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence


CHECKPOINT_NAME = "TARGET_FOLLOWERS_PROGRESSIVE_RESUME_V2"
SURFACE_FOLLOWERS = "followers"
SHADOW_FLAG = "TARGET_FOLLOWERS_RESUME_V2_SHADOW_ENABLED"
SHADOW_ACCOUNT_IDS_FLAG = "TARGET_FOLLOWERS_RESUME_V2_SHADOW_ACCOUNT_IDS"
ENFORCE_FLAG = "TARGET_FOLLOWERS_RESUME_V2_ENFORCE_ENABLED"
HMAC_SECRET_FLAG = "TARGET_FOLLOWERS_RESUME_V2_HMAC_SECRET"
MAX_SHADOW_ACCOUNT_IDS = 32
MAX_ANCHORS = 12
MAX_DEPTH = 80
MAX_FAST_FORWARD_DEPTH = 40
DEFAULT_STALE_AFTER_SECONDS = 14 * 24 * 60 * 60
DEFAULT_LEASE_SECONDS = 60 * 60
MAX_LEASE_SECONDS = 2 * 60 * 60
RENEWAL_MARGIN_SECONDS = 10 * 60

EVENTS = frozenset(
    {
        "target_followers_checkpoint_loaded",
        "resume_plan_built",
        "fast_forward_started",
        "depth_transition_verified",
        "anchor_found",
        "anchor_not_found",
        "v2_gate_evaluated",
        "v2_controller_initialized",
        "checkpoint_claimed",
        "lease_renewed",
        "lease_reclaimed",
        "checkpoint_committed",
        "ct_resume_first_pass_progress",
        "checkpoint_flush_completed",
        "checkpoint_not_committed",
        "checkpoint_conflict",
        "checkpoint_invalidated",
        "lease_released",
        "v2_failed_open",
        "end_reached",
        "resume_fallback_legacy",
        "depth_transition_rejected",
    }
)

NO_COMMIT_REASONS = frozenset(
    {
        "no_safe_progress",
        "continuity_unproven",
        "lease_invalid",
        "target_identity_changed",
        "suggestions_boundary_reached",
        "commit_conflict",
        "run_terminal_before_checkpoint_flush",
    }
)

_LEGACY_PRIMARY_ROWS_AVAILABLE = "PRIMARY_ROWS_AVAILABLE"
_LEGACY_SUGGESTIONS_BOUNDARY = "SUGGESTIONS_BOUNDARY_CONFIRMED"
_GENERIC_FOLLOWERS_TITLES = frozenset(
    {"followers", "follower", "seguidores", "followerinnen"}
)

_HANDLE_RE = re.compile(r"^[a-z0-9._]{1,64}$")
_SAFE_REASON_RE = re.compile(r"^[a-z0-9_:-]{1,120}$")
_FULL_RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_LEGACY_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{20}$")


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _clean_id(value: object) -> str:
    return str(value or "").strip()


def normalize_handle(value: object) -> str:
    handle = str(value or "").strip().lower().lstrip("@")
    return handle if _HANDLE_RE.fullmatch(handle) else ""


def _bounded_reason(value: object, default: str) -> str:
    reason = str(value or "").strip().lower().replace(" ", "_")[:120]
    return reason if _SAFE_REASON_RE.fullmatch(reason) else default


def _parse_timestamp(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sha256_token(value: str, *, prefix: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def stable_id_hash(value: object) -> str:
    normalized = _clean_id(value)
    return _sha256_token(normalized, prefix="id2") if normalized else ""


def _hmac_secret(secret: object | None = None) -> bytes:
    raw = str(secret if secret is not None else os.environ.get(HMAC_SECRET_FLAG, "")).strip()
    return raw.encode("utf-8") if len(raw) >= 32 else b""


def _hmac_token(value: str, *, prefix: str, secret: object | None = None) -> str:
    key = _hmac_secret(secret)
    if not key or not value:
        return ""
    digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{prefix}:{digest}"


def anchor_hash(handle: object, *, secret: object | None = None) -> str:
    normalized = normalize_handle(handle)
    return _hmac_token(normalized, prefix="a3", secret=secret) if normalized else ""


def normalize_visible_handles(values: Iterable[object]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        handle = normalize_handle(value)
        if handle and handle not in seen:
            seen.add(handle)
            out.append(handle)
    return tuple(out)


def legacy_viewport_fingerprint(values: Iterable[object]) -> str:
    """Mirror the shared list-continuation fingerprint without exposing rows.

    The legacy scroll engine produces this SHA-256 prefix from the physical
    XML rows before and after a gesture.  Recomputing it from the next normal
    candidate collection binds that strong scroll proof to the viewport that
    the shadow controller is about to checkpoint.
    """
    handles = normalize_visible_handles(values)
    if not handles:
        return ""
    return hashlib.sha256("\x1f".join(handles).encode("utf-8")).hexdigest()[:20]


def viewport_continuity_counts(
    before_values: Iterable[object],
    after_values: Iterable[object],
) -> tuple[int, int]:
    """Return positional edge overlap and genuinely new unique rows.

    A repeated username somewhere else in the viewport is not continuity.  A
    safe forward transition retains a suffix of the old viewport as the prefix
    of the new one and exposes at least one previously unseen row.
    """
    before = normalize_visible_handles(before_values)
    after = normalize_visible_handles(after_values)
    overlap = 0
    for count in range(min(len(before), len(after)), 0, -1):
        if before[-count:] == after[:count]:
            overlap = count
            break
    new_unique_rows = len(set(after).difference(before))
    return overlap, new_unique_rows


def target_surface_identity_proved(
    detection: Mapping[str, Any] | None,
    *,
    expected_target: object,
    session_committed: bool = False,
) -> bool:
    """Pure fail-closed proof that a Followers list belongs to the expected CT.

    `followers_session_list_committed_open_for` is a useful corroborating
    signal, but it is intentionally cleared while a candidate profile is open
    and is not restored by every valid own-unified return path.  The fresh
    detector's exact action-bar/header identity is therefore accepted as an
    independent proof.  A generic ``Followers`` title alone is never enough.
    """
    if not isinstance(detection, Mapping) or not bool(
        detection.get("is_followers_list")
    ):
        return False
    expected = normalize_handle(expected_target)
    if not expected:
        return False
    action_bar = normalize_handle(detection.get("action_bar_title"))
    if action_bar:
        if action_bar == expected:
            return True
        if action_bar not in _GENERIC_FOLLOWERS_TITLES:
            return False
    for value in list(detection.get("visible_header_texts") or ())[:30]:
        if normalize_handle(value) == expected:
            return True
    return bool(session_committed)


def viewport_fingerprint(values: Iterable[object], *, secret: object | None = None) -> str:
    handles = normalize_visible_handles(values)
    if not handles:
        return ""
    return _hmac_token("\x1f".join(handles), prefix="v3", secret=secret)


def bounded_anchor_hashes(values: Iterable[object], *, secret: object | None = None) -> tuple[str, ...]:
    handles = normalize_visible_handles(values)
    if len(handles) <= MAX_ANCHORS:
        selected = handles
    else:
        left = MAX_ANCHORS // 2
        selected = handles[:left] + handles[-(MAX_ANCHORS - left) :]
    return tuple(token for token in (anchor_hash(item, secret=secret) for item in selected) if token)


def parse_account_id_allowlist(raw: object) -> tuple[str, ...]:
    """Parse a bounded UUID allowlist; malformed or oversized input fails closed."""
    values = [item.strip().lower() for item in str(raw or "").split(",") if item.strip()]
    if len(values) > MAX_SHADOW_ACCOUNT_IDS:
        return ()
    parsed: list[str] = []
    for value in values:
        try:
            canonical = str(uuid.UUID(value))
        except (ValueError, AttributeError, TypeError):
            return ()
        if canonical not in parsed:
            parsed.append(canonical)
    return tuple(parsed)


@dataclass(frozen=True)
class ResumeFlags:
    shadow_enabled: bool = False
    enforce_enabled: bool = False
    shadow_account_ids: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ResumeFlags":
        env = environ if environ is not None else os.environ
        return cls(
            shadow_enabled=_truthy(env.get(SHADOW_FLAG)),
            enforce_enabled=_truthy(env.get(ENFORCE_FLAG)),
            shadow_account_ids=parse_account_id_allowlist(env.get(SHADOW_ACCOUNT_IDS_FLAG)),
        )

    @property
    def enabled(self) -> bool:
        return self.shadow_enabled or self.enforce_enabled

    @property
    def mode(self) -> str:
        return "enforce" if self.enforce_enabled else "shadow"

    def shadow_allowed_for(self, account_id: str) -> bool:
        """First-rollout gate: shadow only, explicit account UUID, never enforce."""
        return bool(
            self.shadow_enabled
            and not self.enforce_enabled
            and _clean_id(account_id).lower() in self.shadow_account_ids
        )

    def rollout_allowed_for(self, account_id: str) -> bool:
        """Apply shadow or enforce only to the existing UUID rollout set."""
        return bool(
            self.enabled
            and _clean_id(account_id).lower() in self.shadow_account_ids
        )


@dataclass(frozen=True)
class ViewportObservation:
    handles: tuple[str, ...]
    fingerprint: str
    anchor_hashes: tuple[str, ...]
    followers_surface_confirmed: bool
    expected_target_confirmed: bool
    list_moved: bool
    recoverable: bool
    ambiguous_surface: bool = False

    @classmethod
    def build(
        cls,
        visible_handles: Iterable[object],
        *,
        followers_surface_confirmed: bool,
        expected_target_confirmed: bool,
        list_moved: bool,
        recoverable: bool,
        ambiguous_surface: bool = False,
        hmac_secret: object | None = None,
    ) -> "ViewportObservation":
        handles = normalize_visible_handles(visible_handles)
        return cls(
            handles=handles,
            fingerprint=viewport_fingerprint(handles, secret=hmac_secret),
            anchor_hashes=bounded_anchor_hashes(handles, secret=hmac_secret),
            followers_surface_confirmed=bool(followers_surface_confirmed),
            expected_target_confirmed=bool(expected_target_confirmed),
            list_moved=bool(list_moved),
            recoverable=bool(recoverable),
            ambiguous_surface=bool(ambiguous_surface),
        )


@dataclass(frozen=True)
class TransitionVerdict:
    verified: bool
    reason: str


@dataclass(frozen=True)
class LegacyScrollEvidence:
    observed_scroll_index: int
    overlap_count: int
    new_unique_rows: int
    fingerprint_before: str
    fingerprint_after: str
    surface_state_after: str


def validate_depth_transition(
    before: ViewportObservation | None,
    after: ViewportObservation | None,
) -> TransitionVerdict:
    if before is None or after is None:
        return TransitionVerdict(False, "viewport_evidence_missing")
    if not before.followers_surface_confirmed or not after.followers_surface_confirmed:
        return TransitionVerdict(False, "followers_surface_unconfirmed")
    if not before.expected_target_confirmed or not after.expected_target_confirmed:
        return TransitionVerdict(False, "expected_target_unconfirmed")
    if before.ambiguous_surface or after.ambiguous_surface:
        return TransitionVerdict(False, "ambiguous_surface")
    if not after.recoverable:
        return TransitionVerdict(False, "surface_not_recoverable")
    if not after.list_moved:
        return TransitionVerdict(False, "scroll_no_movement")
    if not before.fingerprint or not after.fingerprint:
        return TransitionVerdict(False, "viewport_fingerprint_missing")
    if before.fingerprint == after.fingerprint:
        return TransitionVerdict(False, "viewport_fingerprint_unchanged")
    overlap_count, new_unique_rows = viewport_continuity_counts(
        before.handles,
        after.handles,
    )
    if overlap_count <= 0:
        return TransitionVerdict(False, "viewport_overlap_missing")
    if new_unique_rows <= 0:
        return TransitionVerdict(False, "viewport_new_rows_missing")
    return TransitionVerdict(True, "validated_distinct_followers_viewport")


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    account_id: str
    target_id: str
    surface: str
    checkpoint_version: int
    last_safe_depth: int
    last_safe_anchor: str
    anchor_fingerprint: str
    last_visible_anchor_hashes: tuple[str, ...]
    shadow_last_safe_depth: int
    shadow_last_safe_anchor: str
    shadow_anchor_fingerprint: str
    shadow_visible_anchor_hashes: tuple[str, ...]
    status: str
    optimistic_version: int
    last_run_id: str
    updated_at: datetime | None
    last_instagram_version: str
    invalidation_reason: str
    lease_owner_run_id: str
    lease_mode: str
    lease_expires_at: datetime | None
    lease_heartbeat_at: datetime | None
    lease_generation: int
    last_verified_at: datetime | None
    end_reached: bool

    @classmethod
    def from_rpc(cls, row: Mapping[str, Any]) -> "Checkpoint":
        def anchors(name: str) -> tuple[str, ...]:
            raw = row.get(name)
            if not isinstance(raw, list):
                return ()
            return tuple(str(item)[:64] for item in raw[:MAX_ANCHORS] if str(item).startswith("a3:"))

        return cls(
            checkpoint_id=_clean_id(row.get("id")),
            account_id=_clean_id(row.get("account_id")),
            target_id=_clean_id(row.get("target_id")),
            surface=str(row.get("surface") or ""),
            checkpoint_version=max(1, int(row.get("checkpoint_version") or 1)),
            last_safe_depth=max(0, int(row.get("last_safe_depth") or 0)),
            last_safe_anchor=str(row.get("last_safe_anchor") or "")[:64],
            anchor_fingerprint=str(row.get("anchor_fingerprint") or "")[:64],
            last_visible_anchor_hashes=anchors("last_visible_anchor_hashes"),
            shadow_last_safe_depth=max(0, int(row.get("shadow_last_safe_depth") or 0)),
            shadow_last_safe_anchor=str(row.get("shadow_last_safe_anchor") or "")[:64],
            shadow_anchor_fingerprint=str(row.get("shadow_anchor_fingerprint") or "")[:64],
            shadow_visible_anchor_hashes=anchors("shadow_visible_anchor_hashes"),
            status=str(row.get("status") or "active"),
            optimistic_version=max(1, int(row.get("optimistic_version") or 1)),
            last_run_id=_clean_id(row.get("last_run_id")),
            updated_at=_parse_timestamp(row.get("updated_at")),
            last_instagram_version=str(row.get("last_instagram_version") or "")[:80],
            invalidation_reason=str(row.get("invalidation_reason") or "")[:120],
            lease_owner_run_id=_clean_id(row.get("lease_owner_run_id")),
            lease_mode=str(row.get("lease_mode") or ""),
            lease_expires_at=_parse_timestamp(row.get("lease_expires_at")),
            lease_heartbeat_at=_parse_timestamp(row.get("lease_heartbeat_at")),
            lease_generation=max(0, int(row.get("lease_generation") or 0)),
            last_verified_at=_parse_timestamp(row.get("last_verified_at")),
            end_reached=bool(row.get("end_reached")),
        )

    def depth(self, mode: str) -> int:
        return self.last_safe_depth if mode == "enforce" else self.shadow_last_safe_depth

    def anchors(self, mode: str) -> tuple[str, ...]:
        return self.last_visible_anchor_hashes if mode == "enforce" else self.shadow_visible_anchor_hashes

    def fingerprint(self, mode: str) -> str:
        return self.anchor_fingerprint if mode == "enforce" else self.shadow_anchor_fingerprint

    def promoted_shadow_ready_for_enforce(self) -> bool:
        """Allow a certified V3 shadow checkpoint to seed first enforcement.

        Enforce-owned fields remain authoritative once they exist.  Promotion
        is deliberately narrow: a V3 checkpoint, positive bounded depth,
        anchors, a completed verification timestamp, and no invalidation.
        """
        return bool(
            self.checkpoint_version >= 3
            and self.last_safe_depth == 0
            and self.shadow_last_safe_depth > 0
            and self.shadow_visible_anchor_hashes
            and self.last_verified_at is not None
            and not self.invalidation_reason
        )

    def resume_depth(self, mode: str) -> int:
        if mode == "enforce" and self.promoted_shadow_ready_for_enforce():
            return self.shadow_last_safe_depth
        return self.depth(mode)

    def resume_anchors(self, mode: str) -> tuple[str, ...]:
        if mode == "enforce" and self.promoted_shadow_ready_for_enforce():
            return self.shadow_visible_anchor_hashes
        return self.anchors(mode)


@dataclass(frozen=True)
class ResumePlan:
    use_legacy_navigation: bool
    mode: str
    previous_depth: int
    planned_depth: int
    anchor_hashes: tuple[str, ...]
    checkpoint_version: int
    optimistic_version: int
    reason: str


def validate_checkpoint(
    checkpoint: Checkpoint,
    *,
    account_id: str,
    target_id: str,
    target_username: str,
    now: datetime | None = None,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> tuple[bool, str]:
    if checkpoint.account_id != _clean_id(account_id):
        return False, "account_id_mismatch"
    if checkpoint.target_id != _clean_id(target_id):
        return False, "target_id_mismatch"
    if checkpoint.surface != SURFACE_FOLLOWERS:
        return False, "surface_mismatch"
    if checkpoint.status not in {"active", "exhausted"}:
        return False, f"checkpoint_status_{checkpoint.status or 'invalid'}"
    if checkpoint.updated_at:
        current = now or datetime.now(timezone.utc)
        if (current - checkpoint.updated_at).total_seconds() > max(60, stale_after_seconds):
            return False, "checkpoint_too_old"
    return True, "checkpoint_valid"


def build_resume_plan(
    checkpoint: Checkpoint | None,
    *,
    flags: ResumeFlags,
    account_id: str,
    target_id: str,
    target_username: str,
    now: datetime | None = None,
) -> ResumePlan:
    mode = flags.mode
    if not flags.enabled:
        return ResumePlan(True, mode, 0, 0, (), 1, 0, "feature_disabled")
    if checkpoint is None:
        return ResumePlan(True, mode, 0, 0, (), 1, 0, "checkpoint_missing")
    valid, reason = validate_checkpoint(
        checkpoint,
        account_id=account_id,
        target_id=target_id,
        target_username=target_username,
        now=now,
    )
    if not valid:
        return ResumePlan(True, mode, 0, 0, (), checkpoint.checkpoint_version, checkpoint.optimistic_version, reason)
    depth = min(MAX_DEPTH, checkpoint.resume_depth(mode))
    resume_anchors = checkpoint.resume_anchors(mode)
    if checkpoint.status == "exhausted":
        return ResumePlan(True, mode, depth, depth, resume_anchors, checkpoint.checkpoint_version, checkpoint.optimistic_version, "checkpoint_exhausted")
    if depth > MAX_FAST_FORWARD_DEPTH:
        return ResumePlan(True, mode, depth, 0, resume_anchors, checkpoint.checkpoint_version, checkpoint.optimistic_version, "fast_forward_depth_exceeds_bound")
    # Shadow always leaves navigation to legacy. Enforce may consume this plan
    # only after an atomic claim and per-transition UI validation.
    return ResumePlan(
        use_legacy_navigation=not flags.enforce_enabled,
        mode=mode,
        previous_depth=depth,
        planned_depth=depth,
        anchor_hashes=resume_anchors,
        checkpoint_version=checkpoint.checkpoint_version,
        optimistic_version=checkpoint.optimistic_version,
        reason=(
            "shadow_plan_promoted_for_enforce"
            if mode == "enforce" and checkpoint.promoted_shadow_ready_for_enforce()
            else ("shadow_plan_only" if mode == "shadow" else "checkpoint_ready")
        ),
    )


def find_resume_cursor(
    visible_handles: Sequence[object],
    expected_anchor_hashes: Sequence[str],
    *,
    terminally_handled: Callable[[str], bool] | None = None,
    hmac_secret: object | None = None,
) -> tuple[int, str]:
    """Return the first safe row to evaluate without skipping viewport remainder.

    The cursor advances only through a contiguous prefix that is either anchored
    or already terminal in canonical social memory.  Any unknown row before an
    anchor forces scanning from that row.
    """
    handles = normalize_visible_handles(visible_handles)
    anchors = {str(item) for item in expected_anchor_hashes[:MAX_ANCHORS] if str(item).startswith("a3:")}
    if not handles:
        return 0, "viewport_empty"
    first_anchor_index = next((i for i, handle in enumerate(handles) if anchor_hash(handle, secret=hmac_secret) in anchors), None)
    if first_anchor_index is None:
        return 0, "anchor_missing"
    is_terminal = terminally_handled or (lambda _handle: False)
    cursor = 0
    while cursor < len(handles):
        handle = handles[cursor]
        if anchor_hash(handle, secret=hmac_secret) in anchors or bool(is_terminal(handle)):
            cursor += 1
            continue
        break
    return cursor, "anchor_found" if cursor > first_anchor_index else "anchor_found_unhandled_prefix"


class ResumeRepository:
    """Thin service-role RPC adapter; no direct table writes are permitted."""

    def __init__(self, rpc: Callable[[str, dict[str, Any]], Any]) -> None:
        self._rpc = rpc

    @staticmethod
    def _one(value: Any) -> dict[str, Any] | None:
        if isinstance(value, list):
            value = value[0] if value else None
        return dict(value) if isinstance(value, dict) else None

    def get(self, *, account_id: str, target_id: str) -> Checkpoint | None:
        row = self._one(
            self._rpc(
                "get_target_followers_resume_checkpoint_v3",
                {"p_account_id": account_id, "p_target_id": target_id, "p_surface": SURFACE_FOLLOWERS},
            )
        )
        return Checkpoint.from_rpc(row) if row else None

    def claim(
        self,
        *,
        account_id: str,
        target_id: str,
        run_id: str,
        mode: str,
        expected_version: int | None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> dict[str, Any]:
        return self._one(
            self._rpc(
                "claim_target_followers_resume_checkpoint_v3",
                {
                    "p_account_id": account_id,
                    "p_target_id": target_id,
                    "p_surface": SURFACE_FOLLOWERS,
                    "p_run_id": run_id,
                    "p_mode": mode,
                    "p_expected_version": expected_version,
                    "p_lease_seconds": max(300, min(MAX_LEASE_SECONDS, int(lease_seconds))),
                },
            )
        ) or {"ok": False, "reason": "empty_claim_response"}

    def commit(
        self,
        *,
        account_id: str,
        target_id: str,
        run_id: str,
        mode: str,
        expected_version: int,
        depth: int,
        observation: ViewportObservation,
        commit_context: LegacyScrollEvidence,
        source_request_id: str,
        source_attempt_id: int,
        release_sha: str,
        cursor_anchor: str,
        instagram_version: str,
        status: str = "active",
        end_reached: bool = False,
        reason: str = "validated_transition",
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> dict[str, Any]:
        request_id = _clean_id(source_request_id)
        try:
            request_id = str(uuid.UUID(request_id))
        except (ValueError, AttributeError, TypeError):
            return {"ok": False, "reason": "commit_context_invalid"}
        canonical_release_sha = str(release_sha or "").strip().lower()
        before_fingerprint = str(commit_context.fingerprint_before or "")
        after_fingerprint = str(commit_context.fingerprint_after or "")
        try:
            attempt_id = int(source_attempt_id)
            observed_scroll_index = int(commit_context.observed_scroll_index)
            overlap_count = int(commit_context.overlap_count)
            new_unique_rows = int(commit_context.new_unique_rows)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "commit_context_invalid"}
        if (
            attempt_id < 1
            or not _FULL_RELEASE_SHA_RE.fullmatch(canonical_release_sha)
            or observed_scroll_index < 1
            or overlap_count < 1
            or new_unique_rows < 1
            or not _LEGACY_FINGERPRINT_RE.fullmatch(before_fingerprint)
            or not _LEGACY_FINGERPRINT_RE.fullmatch(after_fingerprint)
            or before_fingerprint == after_fingerprint
        ):
            return {"ok": False, "reason": "commit_context_invalid"}
        return self._one(
            self._rpc(
                "commit_target_followers_resume_checkpoint_v4",
                {
                    "p_account_id": account_id,
                    "p_target_id": target_id,
                    "p_surface": SURFACE_FOLLOWERS,
                    "p_run_id": run_id,
                    "p_mode": mode,
                    "p_expected_version": int(expected_version),
                    "p_last_safe_depth": max(0, min(MAX_DEPTH, int(depth))),
                    "p_commit_context": {
                        "source_request_id": request_id,
                        "source_attempt_id": attempt_id,
                        "release_sha": canonical_release_sha,
                        "observed_scroll_index": observed_scroll_index,
                        "overlap_count": overlap_count,
                        "new_unique_rows": new_unique_rows,
                        "viewport_fingerprint_before": before_fingerprint,
                        "viewport_fingerprint_after": after_fingerprint,
                    },
                    "p_last_safe_anchor": str(cursor_anchor or "")[:64] or None,
                    "p_anchor_fingerprint": observation.fingerprint or None,
                    "p_last_visible_anchor_hashes": list(observation.anchor_hashes),
                    "p_last_instagram_version": str(instagram_version or "")[:80] or None,
                    "p_status": status,
                    "p_end_reached": bool(end_reached),
                    "p_reason": _bounded_reason(reason, "validated_transition"),
                    "p_lease_seconds": max(300, min(MAX_LEASE_SECONDS, int(lease_seconds))),
                },
            )
        ) or {"ok": False, "reason": "empty_commit_response"}

    def commit_first_pass_progress(
        self,
        *,
        account_id: str,
        target_id: str,
        run_id: str,
        mode: str,
        expected_version: int,
        observation: ViewportObservation,
        evaluated_anchor_hashes: Sequence[str],
        source_request_id: str,
        source_attempt_id: int,
        release_sha: str,
        reason: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Commit a proven evaluated prefix without inventing scroll depth."""
        request_id = _clean_id(source_request_id)
        try:
            request_id = str(uuid.UUID(request_id))
        except (ValueError, AttributeError, TypeError):
            return {"ok": False, "reason": "commit_context_invalid"}
        canonical_release_sha = str(release_sha or "").strip().lower()
        anchors = tuple(str(item) for item in evaluated_anchor_hashes[:MAX_ANCHORS])
        try:
            attempt_id = int(source_attempt_id)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "commit_context_invalid"}
        if (
            attempt_id < 1
            or not _FULL_RELEASE_SHA_RE.fullmatch(canonical_release_sha)
            or not anchors
            or any(not item.startswith("a3:") for item in anchors)
            or not observation.fingerprint.startswith("v3:")
        ):
            return {"ok": False, "reason": "commit_context_invalid"}
        return self._one(
            self._rpc(
                "commit_target_followers_resume_first_pass_progress_v5",
                {
                    "p_account_id": account_id,
                    "p_target_id": target_id,
                    "p_surface": SURFACE_FOLLOWERS,
                    "p_run_id": run_id,
                    "p_mode": mode,
                    "p_expected_version": int(expected_version),
                    "p_commit_context": {
                        "source_request_id": request_id,
                        "source_attempt_id": attempt_id,
                        "release_sha": canonical_release_sha,
                        "evaluated_count": len(anchors),
                    },
                    "p_last_safe_anchor": anchors[-1],
                    "p_anchor_fingerprint": observation.fingerprint,
                    "p_evaluated_anchor_hashes": list(anchors),
                    "p_reason": _bounded_reason(reason, "first_pass_evaluated_prefix"),
                    "p_lease_seconds": max(
                        300, min(MAX_LEASE_SECONDS, int(lease_seconds))
                    ),
                },
            )
        ) or {"ok": False, "reason": "empty_commit_response"}

    def renew(
        self,
        *,
        account_id: str,
        target_id: str,
        run_id: str,
        mode: str,
        expected_version: int,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> dict[str, Any]:
        return self._one(
            self._rpc(
                "renew_target_followers_resume_checkpoint_v3",
                {
                    "p_account_id": account_id,
                    "p_target_id": target_id,
                    "p_surface": SURFACE_FOLLOWERS,
                    "p_run_id": run_id,
                    "p_mode": mode,
                    "p_expected_version": int(expected_version),
                    "p_lease_seconds": max(300, min(MAX_LEASE_SECONDS, int(lease_seconds))),
                },
            )
        ) or {"ok": False, "reason": "empty_renew_response"}

    def release(self, *, account_id: str, target_id: str, run_id: str, mode: str) -> dict[str, Any]:
        return self._one(
            self._rpc(
                "release_target_followers_resume_checkpoint_v3",
                {
                    "p_account_id": account_id,
                    "p_target_id": target_id,
                    "p_surface": SURFACE_FOLLOWERS,
                    "p_run_id": run_id,
                    "p_mode": mode,
                },
            )
        ) or {"ok": False, "reason": "empty_release_response"}

    def invalidate(self, **params: Any) -> dict[str, Any]:
        return self._one(self._rpc("invalidate_target_followers_resume_checkpoint", params)) or {"ok": False, "reason": "empty_invalidate_response"}

    def reset(self, **params: Any) -> dict[str, Any]:
        return self._one(self._rpc("reset_target_followers_resume_checkpoint", params)) or {"ok": False, "reason": "empty_reset_response"}


@dataclass
class ProgressiveResumeController:
    repository: ResumeRepository
    flags: ResumeFlags
    account_id: str
    target_id: str
    target_username: str
    run_id: str
    hmac_secret: str = field(repr=False)
    instagram_version: str = ""
    emit: Callable[[str, dict[str, Any]], None] | None = None
    checkpoint: Checkpoint | None = None
    plan: ResumePlan | None = None
    claimed_version: int | None = None
    lease_expires_at: datetime | None = None
    reached_depth: int = 0
    current_viewport: ViewportObservation | None = None
    pending_scroll_before: ViewportObservation | None = None
    pending_previous_viewport_complete: bool = False
    pending_scroll_started_at: float = 0.0
    pending_legacy_scroll: LegacyScrollEvidence | None = None
    navigation_mutations: int = 0
    checkpoint_depth_before: int = 0
    last_committed_depth: int = 0
    commit_count: int = 0
    last_cursor_handle: str = field(default="", repr=False)
    last_overlap_count: int = 0
    last_new_unique_rows: int = 0
    last_observed_scroll_index: int = 0
    last_verified_commit_context: LegacyScrollEvidence | None = None
    first_pass_evaluated_handles: tuple[str, ...] = field(default=(), repr=False)
    first_pass_evaluated_anchor_hashes: tuple[str, ...] = field(
        default=(), repr=False
    )
    first_pass_observation: ViewportObservation | None = field(
        default=None, repr=False
    )
    first_pass_commit_count: int = 0
    last_no_commit_reason: str = "no_safe_progress"
    source_request_id: str = ""
    source_attempt_id: int = 1
    release_sha: str = ""
    _claimed: bool = False
    _safe_stop: bool = False
    _anchor_proposal_emitted: bool = False
    _released: bool = False
    _no_commit_event_emitted: bool = False
    cas_reloads: int = 0
    cas_retries: int = 0

    def _event(self, event: str, *, reason: str, **metadata: Any) -> None:
        if event not in EVENTS:
            raise ValueError(f"unsupported resume event: {event}")
        divergence = (
            self.reached_depth - self.plan.planned_depth
            if self.plan
            else self.reached_depth
        )
        if self._released:
            lease_status = "released"
        elif self._lease_is_valid():
            lease_status = "active"
        elif self._claimed:
            lease_status = "invalid"
        else:
            lease_status = "unclaimed"
        payload = {
            "account_id": self.account_id,
            "target_id_hash": stable_id_hash(self.target_id),
            "run_id": self.run_id,
            "checkpoint_version": self.plan.checkpoint_version if self.plan else 1,
            "previous_depth": self.plan.previous_depth if self.plan else 0,
            "planned_depth": self.plan.planned_depth if self.plan else 0,
            "reached_depth": self.reached_depth,
            "checkpoint_found": self.checkpoint is not None,
            "checkpoint_loaded": self.checkpoint is not None,
            "checkpoint_depth_before": self.checkpoint_depth_before,
            "checkpoint_depth_after": self.last_committed_depth,
            "legacy_start_depth": 0 if self.flags.mode == "shadow" else self.checkpoint_depth_before,
            "proposed_resume_depth": self.plan.planned_depth if self.plan else 0,
            "shadow_divergence": divergence,
            "divergence": divergence,
            "commit_status": "committed" if self.commit_count > 0 else "not_committed",
            "lease_status": lease_status,
            "source_request_id": self.source_request_id or None,
            "source_attempt_id": max(1, int(self.source_attempt_id or 1)),
            "release_sha": self.release_sha or None,
            "reason": _bounded_reason(reason, "unspecified"),
            "shadow": self.flags.mode == "shadow",
            "enforce": self.flags.mode == "enforce",
            "legacy_authority": self.flags.mode == "shadow",
            **metadata,
        }
        if self.emit:
            self.emit(event, payload)

    def load_and_plan(self) -> ResumePlan:
        if not self.flags.enabled:
            self.plan = build_resume_plan(None, flags=self.flags, account_id=self.account_id, target_id=self.target_id, target_username=self.target_username)
            self.checkpoint_depth_before = self.plan.previous_depth
            self.last_committed_depth = self.plan.previous_depth
            self.reached_depth = 0
            return self.plan
        rpc_started_at = time.perf_counter()
        self.checkpoint = self.repository.get(account_id=self.account_id, target_id=self.target_id)
        rpc_duration_ms = round(
            (time.perf_counter() - rpc_started_at) * 1000.0,
            2,
        )
        self.plan = build_resume_plan(
            self.checkpoint,
            flags=self.flags,
            account_id=self.account_id,
            target_id=self.target_id,
            target_username=self.target_username,
        )
        self.checkpoint_depth_before = self.plan.previous_depth
        self.last_committed_depth = self.plan.previous_depth
        # Both modes begin at physical depth zero. Enforce must prove every
        # bounded transition before reaching the stored depth; treating the
        # checkpoint depth as already physical would create a false jump.
        self.reached_depth = 0
        self._event(
            "target_followers_checkpoint_loaded",
            reason="loaded" if self.checkpoint else "checkpoint_missing",
            rpc_duration_ms=rpc_duration_ms,
        )
        self._event(
            "resume_plan_built",
            reason=self.plan.reason,
            anchor_status="available" if self.plan.anchor_hashes else "missing",
            resume_anchor_hash=(
                self.plan.anchor_hashes[-1] if self.plan.anchor_hashes else None
            ),
            anchor_overlap_count=None,
            theoretical_fast_forward_depth=self.plan.planned_depth,
            theoretical_scrolls_avoided=self.plan.planned_depth,
        )
        if self.plan.planned_depth > 0:
            self._event(
                "fast_forward_started",
                reason=(
                    "theoretical_shadow_only"
                    if self.flags.mode == "shadow"
                    else "checkpoint_enforce_bounded"
                ),
                theoretical=self.flags.mode == "shadow",
            )
        if self.plan.use_legacy_navigation:
            self._event("resume_fallback_legacy", reason=self.plan.reason)
        return self.plan

    def claim(self) -> bool:
        if not self.flags.enabled:
            return False
        if self.plan is None:
            self.load_and_plan()
        rpc_started_at = time.perf_counter()
        response = self.repository.claim(
            account_id=self.account_id,
            target_id=self.target_id,
            run_id=self.run_id,
            mode=self.flags.mode,
            expected_version=self.plan.optimistic_version if self.checkpoint else None,
        )
        rpc_duration_ms = round((time.perf_counter() - rpc_started_at) * 1000.0, 2)
        if not response.get("ok"):
            self._event(
                "checkpoint_conflict",
                reason=str(response.get("reason") or "claim_rejected"),
                rpc_duration_ms=rpc_duration_ms,
                operation="claim",
            )
            return False
        self.claimed_version = int(response.get("optimistic_version") or 0)
        self.lease_expires_at = _parse_timestamp(response.get("lease_expires_at"))
        self._claimed = self.claimed_version > 0
        if self._claimed:
            reclaimed = str(response.get("reason") or "") == "reclaimed"
            self._event(
                "lease_reclaimed" if reclaimed else "checkpoint_claimed",
                reason="lease_reclaimed" if reclaimed else "claimed",
                rpc_duration_ms=rpc_duration_ms,
                optimistic_version=self.claimed_version,
                lease_expires_at=self.lease_expires_at.isoformat() if self.lease_expires_at else None,
            )
        return self._claimed

    def _lease_is_valid(self) -> bool:
        if not self._claimed or self.claimed_version is None:
            return False
        if self.lease_expires_at is None:
            return False
        return self.lease_expires_at > datetime.now(timezone.utc)

    def _note_no_commit(self, reason: str, **metadata: Any) -> None:
        stable = reason if reason in NO_COMMIT_REASONS else "continuity_unproven"
        self.last_no_commit_reason = stable
        self._event("checkpoint_not_committed", reason=stable, **metadata)
        self._no_commit_event_emitted = True

    def _reject_transition(self, reason: str, **metadata: Any) -> TransitionVerdict:
        stable = reason if reason in NO_COMMIT_REASONS else "continuity_unproven"
        self.last_no_commit_reason = stable
        self._event("depth_transition_rejected", reason=stable, **metadata)
        return TransitionVerdict(False, stable)

    def note_legacy_scroll_progress(
        self,
        scroll_diagnostics: Mapping[str, Any] | None,
        *,
        observed_scroll_index: int,
        followers_surface_confirmed: bool,
        expected_target_confirmed: bool,
    ) -> TransitionVerdict:
        """Stage a proven legacy scroll for the next fresh viewport observation.

        This consumes only telemetry already produced by the authoritative
        legacy scroll.  It emits no gesture and does not alter navigation.
        The transition advances depth only after the next normal candidate
        collection matches the diagnostic after-fingerprint.
        """
        diag = dict(scroll_diagnostics or {})
        surface_state = str(diag.get("surface_state_after") or "")
        overlap_count = max(0, int(diag.get("overlap_count") or 0))
        new_unique_rows = max(0, int(diag.get("new_primary_row_count") or 0))
        fingerprint_before = str(diag.get("viewport_fingerprint_before") or "")
        fingerprint_after = str(diag.get("viewport_fingerprint_after") or "")
        common = {
            "observed_scroll_index": max(0, int(observed_scroll_index or 0)),
            "overlap_count": overlap_count,
            "new_unique_rows": new_unique_rows,
            "viewport_fingerprint_before": fingerprint_before,
            "viewport_fingerprint_after": fingerprint_after,
            "surface_state_after": surface_state,
            "commit_eligibility": False,
        }
        if self._safe_stop:
            return self._reject_transition("continuity_unproven", **common)
        if not self._lease_is_valid():
            return self._reject_transition("lease_invalid", **common)
        if not expected_target_confirmed:
            return self._reject_transition("target_identity_changed", **common)
        if surface_state == _LEGACY_SUGGESTIONS_BOUNDARY:
            return self._reject_transition("suggestions_boundary_reached", **common)
        if not followers_surface_confirmed or surface_state != _LEGACY_PRIMARY_ROWS_AVAILABLE:
            return self._reject_transition("continuity_unproven", **common)
        if not bool(diag.get("depth_advanced")):
            return self._reject_transition("continuity_unproven", **common)
        if overlap_count <= 0 or new_unique_rows <= 0:
            return self._reject_transition("continuity_unproven", **common)
        if (
            not fingerprint_before
            or not fingerprint_after
            or fingerprint_before == fingerprint_after
        ):
            return self._reject_transition("continuity_unproven", **common)
        if self.current_viewport is None:
            return self._reject_transition("continuity_unproven", **common)
        current_legacy_fingerprint = legacy_viewport_fingerprint(
            self.current_viewport.handles
        )
        if current_legacy_fingerprint != fingerprint_before:
            return self._reject_transition(
                "continuity_unproven",
                **common,
                current_viewport_fingerprint=current_legacy_fingerprint,
            )
        self.pending_legacy_scroll = LegacyScrollEvidence(
            observed_scroll_index=max(0, int(observed_scroll_index or 0)),
            overlap_count=overlap_count,
            new_unique_rows=new_unique_rows,
            fingerprint_before=fingerprint_before,
            fingerprint_after=fingerprint_after,
            surface_state_after=surface_state,
        )
        return TransitionVerdict(False, "legacy_scroll_proof_staged")

    def observe_viewport(
        self,
        visible_handles: Iterable[object],
        *,
        followers_surface_confirmed: bool,
        expected_target_confirmed: bool,
        list_moved: bool = True,
        recoverable: bool = True,
        ambiguous_surface: bool = False,
    ) -> TransitionVerdict:
        observation = ViewportObservation.build(
            visible_handles,
            followers_surface_confirmed=followers_surface_confirmed,
            expected_target_confirmed=expected_target_confirmed,
            list_moved=list_moved,
            recoverable=recoverable,
            ambiguous_surface=ambiguous_surface,
            hmac_secret=self.hmac_secret,
        )
        if self._safe_stop:
            return self._reject_transition("continuity_unproven", v2_safe_stop=True)
        if observation.handles:
            self.last_cursor_handle = observation.handles[-1]
        if self.pending_legacy_scroll is not None:
            evidence = self.pending_legacy_scroll
            self.pending_legacy_scroll = None
            common = {
                "observed_scroll_index": evidence.observed_scroll_index,
                "overlap_count": evidence.overlap_count,
                "new_unique_rows": evidence.new_unique_rows,
                "viewport_fingerprint_before": evidence.fingerprint_before,
                "viewport_fingerprint_after": evidence.fingerprint_after,
                "surface_state_after": evidence.surface_state_after,
                "commit_eligibility": False,
            }
            if not self._lease_is_valid():
                self.current_viewport = observation
                return self._reject_transition("lease_invalid", **common)
            if not expected_target_confirmed:
                self.current_viewport = observation
                return self._reject_transition("target_identity_changed", **common)
            if not followers_surface_confirmed or ambiguous_surface or not recoverable:
                self.current_viewport = observation
                return self._reject_transition("continuity_unproven", **common)
            actual_after_fingerprint = legacy_viewport_fingerprint(observation.handles)
            if actual_after_fingerprint != evidence.fingerprint_after:
                self.current_viewport = observation
                return self._reject_transition(
                    "continuity_unproven",
                    **common,
                    observed_viewport_fingerprint=actual_after_fingerprint,
                )
            previous = self.reached_depth
            self.reached_depth = min(MAX_DEPTH, self.reached_depth + 1)
            self.current_viewport = observation
            self.last_overlap_count = evidence.overlap_count
            self.last_new_unique_rows = evidence.new_unique_rows
            self.last_observed_scroll_index = evidence.observed_scroll_index
            self.last_verified_commit_context = evidence
            self.last_no_commit_reason = "no_safe_progress"
            self._event(
                "depth_transition_verified",
                reason="validated_legacy_overlap_transition",
                previous_safe_depth=previous,
                observed_scroll_index=evidence.observed_scroll_index,
                overlap_count=evidence.overlap_count,
                new_unique_rows=evidence.new_unique_rows,
                viewport_fingerprint_before=evidence.fingerprint_before,
                viewport_fingerprint_after=evidence.fingerprint_after,
                anchor_hash=(
                    observation.anchor_hashes[-1]
                    if observation.anchor_hashes
                    else None
                ),
                proposed_safe_depth=self.reached_depth,
                commit_eligibility=bool(
                    self.reached_depth > self.last_committed_depth
                    and self._lease_is_valid()
                ),
            )
            return TransitionVerdict(True, "validated_legacy_overlap_transition")
        if self.pending_scroll_before is None:
            self.current_viewport = observation
            if not self._anchor_proposal_emitted and self.plan is not None:
                cursor, anchor_reason = find_resume_cursor(
                    observation.handles,
                    self.plan.anchor_hashes,
                    hmac_secret=self.hmac_secret,
                )
                anchor_event = (
                    "anchor_found"
                    if anchor_reason.startswith("anchor_found")
                    else "anchor_not_found"
                )
                self._event(
                    anchor_event,
                    reason=anchor_reason,
                    proposed_cursor=int(cursor),
                    anchor_overlap_count=len(
                        set(observation.anchor_hashes).intersection(
                            self.plan.anchor_hashes
                        )
                    ),
                    resume_anchor_hash=(
                        self.plan.anchor_hashes[-1]
                        if self.plan.anchor_hashes
                        else None
                    ),
                    theoretical_scrolls_avoided=self.plan.planned_depth,
                    theoretical=True,
                )
                self._anchor_proposal_emitted = True
            return TransitionVerdict(False, "baseline_viewport_recorded")
        before = self.pending_scroll_before
        verdict = validate_depth_transition(before, observation)
        elapsed_ms = round(max(0.0, time.perf_counter() - self.pending_scroll_started_at) * 1000.0, 2)
        self.pending_scroll_before = None
        previous_viewport_complete = self.pending_previous_viewport_complete
        self.pending_previous_viewport_complete = False
        self.pending_scroll_started_at = 0.0
        if not verdict.verified:
            self.current_viewport = observation
            return verdict
        if not previous_viewport_complete:
            self.current_viewport = observation
            return TransitionVerdict(False, "previous_viewport_not_fully_evaluated")
        previous = self.reached_depth
        self.reached_depth = min(MAX_DEPTH, self.reached_depth + 1)
        self.current_viewport = observation
        overlap_count, new_unique_rows = viewport_continuity_counts(
            before.handles,
            observation.handles,
        )
        self.last_overlap_count = overlap_count
        self.last_new_unique_rows = new_unique_rows
        self.last_observed_scroll_index = self.reached_depth
        self.last_verified_commit_context = LegacyScrollEvidence(
            observed_scroll_index=self.reached_depth,
            overlap_count=overlap_count,
            new_unique_rows=new_unique_rows,
            fingerprint_before=legacy_viewport_fingerprint(before.handles),
            fingerprint_after=legacy_viewport_fingerprint(observation.handles),
            surface_state_after=_LEGACY_PRIMARY_ROWS_AVAILABLE,
        )
        self._event(
            "depth_transition_verified",
            reason=verdict.reason,
            previous_depth=previous,
            reached_depth=self.reached_depth,
            viewport_fingerprint_before=before.fingerprint,
            viewport_fingerprint_after=observation.fingerprint,
            overlap_count=overlap_count,
            new_unique_rows=new_unique_rows,
            anchor_hash=(
                observation.anchor_hashes[-1]
                if observation.anchor_hashes
                else None
            ),
            proposed_safe_depth=self.reached_depth,
            commit_eligibility=bool(
                self.reached_depth > self.last_committed_depth
                and self._lease_is_valid()
            ),
            elapsed_ms=elapsed_ms,
        )
        return verdict

    def note_first_pass_evaluated_prefix(
        self,
        visible_handles: Iterable[object],
        *,
        terminally_handled: Callable[[str], bool],
        reason: str = "first_pass_evaluated_prefix",
    ) -> int:
        """Stage only the contiguous, positively handled viewport prefix.

        This never changes ``reached_depth``.  A gap ends the prefix so a
        second pass can never skip an unexamined row.
        """
        if (
            not self.flags.enabled
            or not self._lease_is_valid()
            or self.current_viewport is None
            or self.reached_depth != 0
            or self.checkpoint_depth_before != 0
            or not self.current_viewport.followers_surface_confirmed
            or not self.current_viewport.expected_target_confirmed
            or self.current_viewport.ambiguous_surface
        ):
            return 0
        normalized = normalize_visible_handles(visible_handles)
        if normalized != self.current_viewport.handles:
            return 0
        prefix: list[str] = []
        for handle in normalized[:MAX_ANCHORS]:
            if not bool(terminally_handled(handle)):
                break
            prefix.append(handle)
        if len(prefix) <= len(self.first_pass_evaluated_handles):
            return len(self.first_pass_evaluated_handles)
        observation = ViewportObservation.build(
            prefix,
            followers_surface_confirmed=True,
            expected_target_confirmed=True,
            list_moved=False,
            recoverable=True,
            ambiguous_surface=False,
            hmac_secret=self.hmac_secret,
        )
        if not observation.fingerprint or not observation.anchor_hashes:
            return len(self.first_pass_evaluated_handles)
        self.first_pass_evaluated_handles = tuple(prefix)
        self.first_pass_evaluated_anchor_hashes = observation.anchor_hashes
        self.first_pass_observation = observation
        self._event(
            "ct_resume_first_pass_progress",
            reason=reason,
            evaluated_count=len(prefix),
            evaluated_anchor_count=len(observation.anchor_hashes),
            anchor_hash=observation.anchor_hashes[-1],
            anchor_fingerprint=observation.fingerprint,
            depth_unchanged=True,
            commit_eligibility=True,
        )
        return len(prefix)

    def commit_first_pass_progress(
        self, *, reason: str = "first_pass_evaluated_prefix"
    ) -> bool:
        if (
            self._safe_stop
            or not self._claimed
            or self.claimed_version is None
            or self.first_pass_observation is None
            or not self.first_pass_evaluated_anchor_hashes
            or self.reached_depth != 0
            or self.checkpoint_depth_before != 0
        ):
            return False
        if not self._renew_if_due():
            self._note_no_commit("lease_invalid", operation="first_pass_commit")
            return False
        started = time.perf_counter()
        response = self.repository.commit_first_pass_progress(
            account_id=self.account_id,
            target_id=self.target_id,
            run_id=self.run_id,
            mode=self.flags.mode,
            expected_version=int(self.claimed_version),
            observation=self.first_pass_observation,
            evaluated_anchor_hashes=self.first_pass_evaluated_anchor_hashes,
            source_request_id=self.source_request_id,
            source_attempt_id=self.source_attempt_id,
            release_sha=self.release_sha,
            reason=reason,
        )
        rpc_duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
        if not response.get("ok") or response.get("provenance_persisted") is not True:
            self._event(
                "checkpoint_conflict",
                reason=str(response.get("reason") or "first_pass_commit_rejected"),
                operation="first_pass_commit",
                rpc_duration_ms=rpc_duration_ms,
            )
            return False
        try:
            commit_event_id = str(uuid.UUID(str(response.get("commit_event_id") or "")))
        except (ValueError, AttributeError, TypeError):
            self._event(
                "checkpoint_conflict",
                reason="commit_provenance_event_missing_or_invalid",
                operation="first_pass_commit",
            )
            return False
        self.claimed_version = int(
            response.get("optimistic_version") or self.claimed_version + 1
        )
        self.lease_expires_at = (
            _parse_timestamp(response.get("lease_expires_at"))
            or self.lease_expires_at
        )
        self.first_pass_commit_count += 1
        self.commit_count += 1
        self._event(
            "checkpoint_committed",
            reason=reason,
            reached_depth=0,
            first_pass_evaluated_count=len(self.first_pass_evaluated_handles),
            first_pass_boundary=True,
            optimistic_version=self.claimed_version,
            commit_event_id=commit_event_id,
            provenance_persisted=True,
            ct_resume_commit_reason=reason,
            ct_resume_checkpoint_after={
                "depth": 0,
                "evaluated_count": len(self.first_pass_evaluated_handles),
                "anchor_count": len(self.first_pass_evaluated_anchor_hashes),
                "optimistic_version": self.claimed_version,
            },
            rpc_duration_ms=rpc_duration_ms,
        )
        return True

    def note_scroll_sent(self, *, previous_viewport_complete: bool) -> bool:
        if not self.flags.enabled or self.current_viewport is None or self._safe_stop:
            return False
        self.pending_scroll_before = self.current_viewport
        self.pending_previous_viewport_complete = bool(previous_viewport_complete)
        self.pending_scroll_started_at = time.perf_counter()
        # In shadow this is observation of a legacy mutation, not one initiated
        # by V2.  The counter therefore remains zero.
        return True

    def enforce_cursor_for_viewport(
        self,
        visible_handles: Iterable[object],
        *,
        terminally_handled: Callable[[str], bool] | None = None,
    ) -> tuple[int, str]:
        """Return the safe overlap cursor after physical fast-forward.

        No cursor is emitted until the lease is live, the requested physical
        depth has been reached and a checkpoint anchor is present.  Rejection
        therefore falls back to evaluating the current viewport from row zero.
        """
        if (
            self.flags.mode != "enforce"
            or self.plan is None
            or self.plan.use_legacy_navigation
            or not self._lease_is_valid()
        ):
            return 0, "enforce_not_ready"
        if self.reached_depth < self.plan.planned_depth:
            return 0, "fast_forward_incomplete"
        cursor, reason = find_resume_cursor(
            tuple(visible_handles),
            self.plan.anchor_hashes,
            terminally_handled=terminally_handled,
            hmac_secret=self.hmac_secret,
        )
        if not reason.startswith("anchor_found"):
            self._event(
                "anchor_not_found",
                reason=reason,
                resume_result="safe_legacy_fallback_current_viewport",
            )
            return 0, reason
        self._event(
            "anchor_found",
            reason=reason,
            overlap_count=cursor,
            physical_scrolls=self.reached_depth,
            usernames_reprocessed=max(0, len(normalize_visible_handles(visible_handles)) - cursor),
            usernames_skipped_by_checkpoint=cursor,
            resume_result="enforce_applied",
        )
        return cursor, reason

    def _renew_if_due(self) -> bool:
        if self.claimed_version is None:
            return False
        now = datetime.now(timezone.utc)
        if self.lease_expires_at is None or self.lease_expires_at <= now:
            self.last_no_commit_reason = "lease_invalid"
            return False
        if self.lease_expires_at and (self.lease_expires_at - now).total_seconds() > RENEWAL_MARGIN_SECONDS:
            return True
        started = time.perf_counter()
        response = self.repository.renew(
            account_id=self.account_id,
            target_id=self.target_id,
            run_id=self.run_id,
            mode=self.flags.mode,
            expected_version=self.claimed_version,
        )
        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
        if not response.get("ok"):
            self._event(
                "checkpoint_conflict",
                reason=str(response.get("reason") or "renew_rejected"),
                operation="renew",
                rpc_duration_ms=duration_ms,
            )
            return False
        self.claimed_version = int(response.get("optimistic_version") or self.claimed_version + 1)
        self.lease_expires_at = _parse_timestamp(response.get("lease_expires_at"))
        reclaimed = str(response.get("reason") or "") == "reclaimed"
        self._event(
            "lease_reclaimed" if reclaimed else "lease_renewed",
            reason="lease_reclaimed_same_run" if reclaimed else "lease_renewed",
            optimistic_version=self.claimed_version,
            lease_expires_at=self.lease_expires_at.isoformat() if self.lease_expires_at else None,
            rpc_duration_ms=duration_ms,
        )
        return True

    def _commit_once(self, *, cursor_handle: str, reason: str) -> tuple[dict[str, Any], float]:
        if self.last_verified_commit_context is None:
            return {"ok": False, "reason": "commit_context_missing"}, 0.0
        started = time.perf_counter()
        try:
            response = self.repository.commit(
                account_id=self.account_id,
                target_id=self.target_id,
                run_id=self.run_id,
                mode=self.flags.mode,
                expected_version=int(self.claimed_version or 0),
                depth=self.reached_depth,
                observation=self.current_viewport,
                commit_context=self.last_verified_commit_context,
                source_request_id=self.source_request_id,
                source_attempt_id=self.source_attempt_id,
                release_sha=self.release_sha,
                cursor_anchor=anchor_hash(cursor_handle, secret=self.hmac_secret),
                instagram_version=self.instagram_version,
                reason=reason,
            )
        except Exception as exc:
            response = {
                "ok": False,
                "reason": "checkpoint_commit_rpc_exception",
                "error_type": type(exc).__name__,
            }
        return response, round((time.perf_counter() - started) * 1000.0, 2)

    def commit_verified_progress(self, *, cursor_handle: str = "", reason: str = "validated_transition") -> bool:
        if self._safe_stop or not self._claimed or self.claimed_version is None or self.current_viewport is None:
            return False
        if self.reached_depth <= self.last_committed_depth:
            self.last_no_commit_reason = "no_safe_progress"
            return False
        if self.last_verified_commit_context is None:
            self._note_no_commit("continuity_unproven", operation="commit")
            self.mark_safe_stop()
            return False
        if not self._renew_if_due():
            stable = (
                "lease_invalid"
                if self.last_no_commit_reason == "lease_invalid"
                else "commit_conflict"
            )
            self._note_no_commit(stable, operation="commit")
            self._event("v2_failed_open", reason="lease_renew_failed", operation="commit")
            self.mark_safe_stop()
            return False
        cursor = normalize_handle(cursor_handle) or self.last_cursor_handle
        response, rpc_duration_ms = self._commit_once(cursor_handle=cursor, reason=reason)
        if not response.get("ok"):
            failure_reason = str(response.get("reason") or "commit_rejected")
            if failure_reason == "optimistic_version_conflict" and self.cas_reloads < 1:
                self.cas_reloads += 1
                latest = self.repository.get(account_id=self.account_id, target_id=self.target_id)
                if latest is not None and latest.lease_owner_run_id == self.run_id and latest.lease_mode == self.flags.mode:
                    if latest.depth(self.flags.mode) >= self.reached_depth:
                        self.claimed_version = latest.optimistic_version
                        self.lease_expires_at = latest.lease_expires_at
                        self._event(
                            "checkpoint_conflict",
                            reason="same_run_depth_provenance_unverified",
                            reached_depth=self.reached_depth,
                            optimistic_version=self.claimed_version,
                            operation="commit",
                        )
                        self.last_no_commit_reason = "commit_conflict"
                        self.mark_safe_stop()
                        return False
                    self.claimed_version = latest.optimistic_version
                    self.lease_expires_at = latest.lease_expires_at
                    if self.cas_retries < 1:
                        self.cas_retries += 1
                        response, rpc_duration_ms = self._commit_once(
                            cursor_handle=cursor,
                            reason=reason,
                        )
            if not response.get("ok"):
                failure_reason = str(response.get("reason") or failure_reason)
                self._event(
                    "checkpoint_conflict",
                    reason=failure_reason,
                    rpc_duration_ms=rpc_duration_ms,
                    operation="commit",
                    cas_reloads=self.cas_reloads,
                    cas_retries=self.cas_retries,
                )
                self.last_no_commit_reason = "commit_conflict"
                self._event("v2_failed_open", reason="checkpoint_commit_failed", operation="commit")
                self.mark_safe_stop()
                return False
        if response.get("provenance_persisted") is not True:
            self.last_no_commit_reason = "commit_conflict"
            self._event(
                "checkpoint_conflict",
                reason="commit_provenance_not_persisted",
                rpc_duration_ms=rpc_duration_ms,
                operation="commit",
            )
            self._event(
                "v2_failed_open",
                reason="checkpoint_commit_provenance_missing",
                operation="commit",
            )
            self.mark_safe_stop()
            return False
        raw_commit_event_id = response.get("commit_event_id")
        try:
            commit_event_id = str(uuid.UUID(str(raw_commit_event_id or "")))
        except (ValueError, AttributeError, TypeError):
            self.last_no_commit_reason = "commit_conflict"
            self._event(
                "checkpoint_conflict",
                reason="commit_provenance_event_missing_or_invalid",
                rpc_duration_ms=rpc_duration_ms,
                operation="commit",
            )
            self._event(
                "v2_failed_open",
                reason="commit_provenance_event_missing_or_invalid",
                operation="commit",
            )
            self.mark_safe_stop()
            return False
        commit_context = self.last_verified_commit_context
        self.claimed_version = int(response.get("optimistic_version") or self.claimed_version + 1)
        self.lease_expires_at = _parse_timestamp(response.get("lease_expires_at")) or self.lease_expires_at
        if bool(response.get("lease_reclaimed")):
            self._event("lease_reclaimed", reason="lease_reclaimed_before_commit", optimistic_version=self.claimed_version)
        self.last_committed_depth = self.reached_depth
        self.commit_count += 1
        self.last_no_commit_reason = "no_safe_progress"
        self._no_commit_event_emitted = False
        self._event(
            "checkpoint_committed",
            reason=reason,
            reached_depth=self.reached_depth,
            rpc_duration_ms=rpc_duration_ms,
            optimistic_version=self.claimed_version,
            commit_event_id=commit_event_id,
            provenance_persisted=True,
            overlap_count=commit_context.overlap_count,
            new_unique_rows=commit_context.new_unique_rows,
            observed_scroll_index=commit_context.observed_scroll_index,
            viewport_fingerprint_before=commit_context.fingerprint_before,
            viewport_fingerprint_after=commit_context.fingerprint_after,
            ct_resume_commit_reason=reason,
            ct_resume_checkpoint_after={
                "depth": self.reached_depth,
                "anchor_count": len(
                    self.current_viewport.anchor_hashes
                    if self.current_viewport is not None
                    else ()
                ),
                "optimistic_version": self.claimed_version,
            },
        )
        self.last_verified_commit_context = None
        return True

    def flush_verified_progress(self, *, boundary: str) -> bool:
        """Idempotently persist pending safe depth before a clean boundary."""
        clean_boundary = _bounded_reason(boundary, "terminal_clean")
        if self.reached_depth > self.last_committed_depth:
            if not self._lease_is_valid():
                self._note_no_commit(
                    "lease_invalid",
                    flush_boundary=clean_boundary,
                    pending_depth=self.reached_depth,
                    committed_depth=self.last_committed_depth,
                )
                return False
            if self.current_viewport is None:
                self._note_no_commit(
                    "continuity_unproven",
                    flush_boundary=clean_boundary,
                    pending_depth=self.reached_depth,
                    committed_depth=self.last_committed_depth,
                )
                return False
            if not self.commit_verified_progress(
                cursor_handle=self.last_cursor_handle,
                reason=f"flush_{clean_boundary}",
            ):
                stable = (
                    self.last_no_commit_reason
                    if self.last_no_commit_reason in NO_COMMIT_REASONS
                    else "commit_conflict"
                )
                if not self._no_commit_event_emitted:
                    self._note_no_commit(
                        stable,
                        flush_boundary=clean_boundary,
                        pending_depth=self.reached_depth,
                        committed_depth=self.last_committed_depth,
                    )
                return False
            self._event(
                "checkpoint_flush_completed",
                reason=clean_boundary,
                flush_boundary=clean_boundary,
                committed_depth=self.last_committed_depth,
                commit_performed=True,
            )
            return True
        if (
            self.reached_depth == 0
            and self.checkpoint_depth_before == 0
            and self.first_pass_observation is not None
            and self.first_pass_evaluated_anchor_hashes
            and self.first_pass_commit_count == 0
        ):
            if self.commit_first_pass_progress(
                reason=f"first_pass_{clean_boundary}"
            ):
                self._event(
                    "checkpoint_flush_completed",
                    reason=clean_boundary,
                    flush_boundary=clean_boundary,
                    committed_depth=0,
                    first_pass_evaluated_count=len(
                        self.first_pass_evaluated_handles
                    ),
                    commit_performed=True,
                )
                return True
            return False
        if self.commit_count > 0 or self.last_committed_depth > self.checkpoint_depth_before:
            self._event(
                "checkpoint_flush_completed",
                reason="already_committed",
                flush_boundary=clean_boundary,
                committed_depth=self.last_committed_depth,
                commit_performed=False,
            )
            return True
        stable = (
            self.last_no_commit_reason
            if self.last_no_commit_reason in NO_COMMIT_REASONS
            else "no_safe_progress"
        )
        if not self._no_commit_event_emitted:
            self._note_no_commit(
                stable,
                flush_boundary=clean_boundary,
                pending_depth=self.reached_depth,
                committed_depth=self.last_committed_depth,
            )
        return stable == "no_safe_progress"

    def abandon_before_release(
        self, *, reason: str = "run_terminal_before_checkpoint_flush"
    ) -> None:
        stable = reason if reason in NO_COMMIT_REASONS else "run_terminal_before_checkpoint_flush"
        if self.reached_depth > self.last_committed_depth or self.commit_count == 0:
            self._note_no_commit(
                stable,
                pending_depth=self.reached_depth,
                committed_depth=self.last_committed_depth,
            )
        self.mark_safe_stop()

    def release(self) -> bool:
        if self._released or not self._claimed:
            self.last_verified_commit_context = None
            return True
        if self.reached_depth > self.last_committed_depth and not self._no_commit_event_emitted:
            self._note_no_commit(
                "run_terminal_before_checkpoint_flush",
                pending_depth=self.reached_depth,
                committed_depth=self.last_committed_depth,
            )
        elif self.commit_count == 0 and not self._no_commit_event_emitted:
            self._note_no_commit(
                self.last_no_commit_reason,
                pending_depth=self.reached_depth,
                committed_depth=self.last_committed_depth,
            )
        started = time.perf_counter()
        try:
            response = self.repository.release(
                account_id=self.account_id,
                target_id=self.target_id,
                run_id=self.run_id,
                mode=self.flags.mode,
            )
        except Exception as exc:
            response = {
                "ok": False,
                "reason": "lease_release_rpc_exception",
                "error_type": type(exc).__name__,
            }
        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
        self.last_verified_commit_context = None
        if not response.get("ok"):
            self._event("v2_failed_open", reason=str(response.get("reason") or "lease_release_failed"), operation="release", rpc_duration_ms=duration_ms)
            return False
        self._released = True
        self._claimed = False
        self.claimed_version = int(response.get("optimistic_version") or self.claimed_version or 0)
        self.lease_expires_at = None
        self._event("lease_released", reason=str(response.get("reason") or "released"), rpc_duration_ms=duration_ms, optimistic_version=self.claimed_version)
        return True

    def mark_safe_stop(self) -> None:
        self._safe_stop = True
        self.pending_scroll_before = None
        self.pending_previous_viewport_complete = False
        self.pending_legacy_scroll = None
        self.last_verified_commit_context = None

    def mark_end_reached(self, *, reason: str = "end_reached") -> None:
        self._event("end_reached", reason=reason)


def build_runtime_controller(
    *,
    account_id: str,
    target_id: str,
    target_username: str,
    run_id: str,
    source_request_id: str = "",
    source_attempt_id: int = 1,
    release_sha: str = "",
    instagram_version: str = "",
    emit: Callable[[str, dict[str, Any]], None] | None = None,
    flags: ResumeFlags | None = None,
    rpc_call: Callable[[str, dict[str, Any]], Any] | None = None,
    hmac_secret: str | None = None,
) -> ProgressiveResumeController | None:
    """Construct only for an explicitly allowlisted rollout account.

    The gate runs before importing the Supabase client, guaranteeing that an
    ineligible account performs no V2 RPC and creates no V2 event/checkpoint.
    Enforce reuses the exact same bounded UUID allowlist as Shadow.
    """
    flags = flags or ResumeFlags.from_env()
    secret = str(hmac_secret if hmac_secret is not None else os.environ.get(HMAC_SECRET_FLAG, "")).strip()
    if not flags.rollout_allowed_for(account_id) or not all(
        (_clean_id(account_id), _clean_id(target_id), _clean_id(run_id))
    ):
        return None
    if len(secret) < 32:
        if emit:
            emit(
                "v2_failed_open",
                {
                    "account_id": _clean_id(account_id),
                    "target_id_hash": stable_id_hash(target_id),
                    "run_id": _clean_id(run_id),
                    "reason": "hmac_secret_missing_or_too_short",
                    "shadow": flags.mode == "shadow",
                    "enforce": flags.mode == "enforce",
                },
            )
        return None
    request_id = _clean_id(source_request_id)
    try:
        request_id = str(uuid.UUID(request_id))
    except (ValueError, AttributeError, TypeError):
        request_id = ""
    try:
        attempt_id = int(source_attempt_id)
    except (TypeError, ValueError):
        attempt_id = 0
    canonical_release_sha = str(release_sha or "").strip().lower()
    if (
        not request_id
        or attempt_id < 1
        or not _FULL_RELEASE_SHA_RE.fullmatch(canonical_release_sha)
    ):
        if emit:
            emit(
                "v2_failed_open",
                {
                    "account_id": _clean_id(account_id),
                    "target_id_hash": stable_id_hash(target_id),
                    "run_id": _clean_id(run_id),
                    "reason": "checkpoint_provenance_invalid",
                    "shadow": flags.mode == "shadow",
                    "enforce": flags.mode == "enforce",
                },
            )
        return None
    if rpc_call is None:
        import supabase_client

        rpc_call = supabase_client.call_rpc_shadow

    controller = ProgressiveResumeController(
        repository=ResumeRepository(rpc_call),
        flags=flags,
        account_id=_clean_id(account_id),
        target_id=_clean_id(target_id),
        target_username=normalize_handle(target_username),
        run_id=_clean_id(run_id),
        source_request_id=request_id,
        source_attempt_id=attempt_id,
        release_sha=canonical_release_sha,
        hmac_secret=secret,
        instagram_version=str(instagram_version or "")[:80],
        emit=emit,
    )
    controller._event("v2_gate_evaluated", reason="allowlisted_shadow_account")
    controller._event("v2_controller_initialized", reason="controller_available")
    return controller
