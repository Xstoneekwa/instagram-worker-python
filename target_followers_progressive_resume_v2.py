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
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence


CHECKPOINT_NAME = "TARGET_FOLLOWERS_PROGRESSIVE_RESUME_V2"
SURFACE_FOLLOWERS = "followers"
SHADOW_FLAG = "TARGET_FOLLOWERS_RESUME_V2_SHADOW_ENABLED"
ENFORCE_FLAG = "TARGET_FOLLOWERS_RESUME_V2_ENFORCE_ENABLED"
MAX_ANCHORS = 12
MAX_DEPTH = 80
MAX_FAST_FORWARD_DEPTH = 40
DEFAULT_STALE_AFTER_SECONDS = 14 * 24 * 60 * 60

EVENTS = frozenset(
    {
        "target_followers_checkpoint_loaded",
        "target_followers_resume_plan_built",
        "target_followers_fast_forward_started",
        "target_followers_depth_transition_verified",
        "target_followers_anchor_found",
        "target_followers_anchor_missing",
        "target_followers_checkpoint_stale",
        "target_followers_checkpoint_committed",
        "target_followers_checkpoint_commit_conflict",
        "target_followers_end_reached",
        "target_followers_resume_fallback_legacy",
    }
)

_HANDLE_RE = re.compile(r"^[a-z0-9._]{1,64}$")
_SAFE_REASON_RE = re.compile(r"^[a-z0-9_:-]{1,120}$")


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


def anchor_hash(handle: object) -> str:
    normalized = normalize_handle(handle)
    return _sha256_token(normalized, prefix="a2") if normalized else ""


def normalize_visible_handles(values: Iterable[object]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        handle = normalize_handle(value)
        if handle and handle not in seen:
            seen.add(handle)
            out.append(handle)
    return tuple(out)


def viewport_fingerprint(values: Iterable[object]) -> str:
    handles = normalize_visible_handles(values)
    if not handles:
        return ""
    return _sha256_token("\x1f".join(handles), prefix="v2")


def bounded_anchor_hashes(values: Iterable[object]) -> tuple[str, ...]:
    handles = normalize_visible_handles(values)
    if len(handles) <= MAX_ANCHORS:
        selected = handles
    else:
        left = MAX_ANCHORS // 2
        selected = handles[:left] + handles[-(MAX_ANCHORS - left) :]
    return tuple(token for token in (anchor_hash(item) for item in selected) if token)


@dataclass(frozen=True)
class ResumeFlags:
    shadow_enabled: bool = False
    enforce_enabled: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ResumeFlags":
        env = environ if environ is not None else os.environ
        return cls(
            shadow_enabled=_truthy(env.get(SHADOW_FLAG)),
            enforce_enabled=_truthy(env.get(ENFORCE_FLAG)),
        )

    @property
    def enabled(self) -> bool:
        return self.shadow_enabled or self.enforce_enabled

    @property
    def mode(self) -> str:
        return "enforce" if self.enforce_enabled else "shadow"


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
    ) -> "ViewportObservation":
        handles = normalize_visible_handles(visible_handles)
        return cls(
            handles=handles,
            fingerprint=viewport_fingerprint(handles),
            anchor_hashes=bounded_anchor_hashes(handles),
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
    return TransitionVerdict(True, "validated_distinct_followers_viewport")


@dataclass(frozen=True)
class Checkpoint:
    account_id: str
    target_id: str
    target_username_normalized: str
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

    @classmethod
    def from_rpc(cls, row: Mapping[str, Any]) -> "Checkpoint":
        def anchors(name: str) -> tuple[str, ...]:
            raw = row.get(name)
            if not isinstance(raw, list):
                return ()
            return tuple(str(item)[:64] for item in raw[:MAX_ANCHORS] if str(item).startswith("a2:"))

        return cls(
            account_id=_clean_id(row.get("account_id")),
            target_id=_clean_id(row.get("target_id")),
            target_username_normalized=normalize_handle(row.get("target_username_normalized")),
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
        )

    def depth(self, mode: str) -> int:
        return self.last_safe_depth if mode == "enforce" else self.shadow_last_safe_depth

    def anchors(self, mode: str) -> tuple[str, ...]:
        return self.last_visible_anchor_hashes if mode == "enforce" else self.shadow_visible_anchor_hashes

    def fingerprint(self, mode: str) -> str:
        return self.anchor_fingerprint if mode == "enforce" else self.shadow_anchor_fingerprint


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
    expected_username = normalize_handle(target_username)
    if expected_username and checkpoint.target_username_normalized != expected_username:
        return False, "target_username_changed"
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
    depth = min(MAX_DEPTH, checkpoint.depth(mode))
    if checkpoint.status == "exhausted":
        return ResumePlan(True, mode, depth, depth, checkpoint.anchors(mode), checkpoint.checkpoint_version, checkpoint.optimistic_version, "checkpoint_exhausted")
    if depth > MAX_FAST_FORWARD_DEPTH:
        return ResumePlan(True, mode, depth, 0, checkpoint.anchors(mode), checkpoint.checkpoint_version, checkpoint.optimistic_version, "fast_forward_depth_exceeds_bound")
    # Shadow always leaves navigation to legacy. Enforce may consume this plan
    # only after an atomic claim and per-transition UI validation.
    return ResumePlan(
        use_legacy_navigation=not flags.enforce_enabled,
        mode=mode,
        previous_depth=depth,
        planned_depth=depth,
        anchor_hashes=checkpoint.anchors(mode),
        checkpoint_version=checkpoint.checkpoint_version,
        optimistic_version=checkpoint.optimistic_version,
        reason="shadow_plan_only" if mode == "shadow" else "checkpoint_ready",
    )


def find_resume_cursor(
    visible_handles: Sequence[object],
    expected_anchor_hashes: Sequence[str],
    *,
    terminally_handled: Callable[[str], bool] | None = None,
) -> tuple[int, str]:
    """Return the first safe row to evaluate without skipping viewport remainder.

    The cursor advances only through a contiguous prefix that is either anchored
    or already terminal in canonical social memory.  Any unknown row before an
    anchor forces scanning from that row.
    """
    handles = normalize_visible_handles(visible_handles)
    anchors = {str(item) for item in expected_anchor_hashes[:MAX_ANCHORS] if str(item).startswith("a2:")}
    if not handles:
        return 0, "viewport_empty"
    first_anchor_index = next((i for i, handle in enumerate(handles) if anchor_hash(handle) in anchors), None)
    if first_anchor_index is None:
        return 0, "anchor_missing"
    is_terminal = terminally_handled or (lambda _handle: False)
    cursor = 0
    while cursor < len(handles):
        handle = handles[cursor]
        if anchor_hash(handle) in anchors or bool(is_terminal(handle)):
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
                "get_target_followers_resume_checkpoint",
                {"p_account_id": account_id, "p_target_id": target_id, "p_surface": SURFACE_FOLLOWERS},
            )
        )
        return Checkpoint.from_rpc(row) if row else None

    def claim(
        self,
        *,
        account_id: str,
        target_id: str,
        target_username: str,
        run_id: str,
        mode: str,
        expected_version: int | None,
        lease_seconds: int = 180,
    ) -> dict[str, Any]:
        return self._one(
            self._rpc(
                "claim_target_followers_resume_checkpoint",
                {
                    "p_account_id": account_id,
                    "p_target_id": target_id,
                    "p_target_username_normalized": normalize_handle(target_username),
                    "p_surface": SURFACE_FOLLOWERS,
                    "p_run_id": run_id,
                    "p_mode": mode,
                    "p_expected_version": expected_version,
                    "p_lease_seconds": max(30, min(900, int(lease_seconds))),
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
        cursor_anchor: str,
        instagram_version: str,
        status: str = "active",
        reason: str = "validated_transition",
    ) -> dict[str, Any]:
        return self._one(
            self._rpc(
                "commit_target_followers_resume_checkpoint",
                {
                    "p_account_id": account_id,
                    "p_target_id": target_id,
                    "p_surface": SURFACE_FOLLOWERS,
                    "p_run_id": run_id,
                    "p_mode": mode,
                    "p_expected_version": int(expected_version),
                    "p_last_safe_depth": max(0, min(MAX_DEPTH, int(depth))),
                    "p_last_safe_anchor": str(cursor_anchor or "")[:64] or None,
                    "p_anchor_fingerprint": observation.fingerprint or None,
                    "p_last_visible_anchor_hashes": list(observation.anchor_hashes),
                    "p_last_instagram_version": str(instagram_version or "")[:80] or None,
                    "p_status": status,
                    "p_reason": _bounded_reason(reason, "validated_transition"),
                },
            )
        ) or {"ok": False, "reason": "empty_commit_response"}

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
    instagram_version: str = ""
    emit: Callable[[str, dict[str, Any]], None] | None = None
    checkpoint: Checkpoint | None = None
    plan: ResumePlan | None = None
    claimed_version: int | None = None
    reached_depth: int = 0
    current_viewport: ViewportObservation | None = None
    pending_scroll_before: ViewportObservation | None = None
    pending_previous_viewport_complete: bool = False
    pending_scroll_started_at: float = 0.0
    navigation_mutations: int = 0
    _claimed: bool = False
    _safe_stop: bool = False

    def _event(self, event: str, *, reason: str, **metadata: Any) -> None:
        if event not in EVENTS:
            raise ValueError(f"unsupported resume event: {event}")
        payload = {
            "account_id": self.account_id,
            "target_id": self.target_id,
            "run_id": self.run_id,
            "checkpoint_version": self.plan.checkpoint_version if self.plan else 1,
            "previous_depth": self.plan.previous_depth if self.plan else 0,
            "planned_depth": self.plan.planned_depth if self.plan else 0,
            "reached_depth": self.reached_depth,
            "reason": _bounded_reason(reason, "unspecified"),
            "shadow": self.flags.mode == "shadow",
            "enforce": self.flags.mode == "enforce",
            **metadata,
        }
        if self.emit:
            self.emit(event, payload)

    def load_and_plan(self) -> ResumePlan:
        if not self.flags.enabled:
            self.plan = build_resume_plan(None, flags=self.flags, account_id=self.account_id, target_id=self.target_id, target_username=self.target_username)
            return self.plan
        self.checkpoint = self.repository.get(account_id=self.account_id, target_id=self.target_id)
        self._event("target_followers_checkpoint_loaded", reason="loaded" if self.checkpoint else "checkpoint_missing")
        self.plan = build_resume_plan(
            self.checkpoint,
            flags=self.flags,
            account_id=self.account_id,
            target_id=self.target_id,
            target_username=self.target_username,
        )
        self.reached_depth = self.plan.previous_depth
        self._event("target_followers_resume_plan_built", reason=self.plan.reason, anchor_status="available" if self.plan.anchor_hashes else "missing")
        if self.plan.reason in {"checkpoint_too_old", "target_username_changed", "surface_mismatch", "account_id_mismatch", "target_id_mismatch"}:
            self._event("target_followers_checkpoint_stale", reason=self.plan.reason)
        if self.plan.use_legacy_navigation:
            self._event("target_followers_resume_fallback_legacy", reason=self.plan.reason)
        return self.plan

    def claim(self) -> bool:
        if not self.flags.enabled:
            return False
        if self.plan is None:
            self.load_and_plan()
        response = self.repository.claim(
            account_id=self.account_id,
            target_id=self.target_id,
            target_username=self.target_username,
            run_id=self.run_id,
            mode=self.flags.mode,
            expected_version=self.plan.optimistic_version if self.checkpoint else None,
        )
        if not response.get("ok"):
            self._event("target_followers_checkpoint_commit_conflict", reason=str(response.get("reason") or "claim_rejected"))
            return False
        self.claimed_version = int(response.get("optimistic_version") or 0)
        self._claimed = self.claimed_version > 0
        return self._claimed

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
        )
        if self.pending_scroll_before is None:
            self.current_viewport = observation
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
        self._event(
            "target_followers_depth_transition_verified",
            reason=verdict.reason,
            previous_depth=previous,
            reached_depth=self.reached_depth,
            viewport_fingerprint_before=before.fingerprint,
            viewport_fingerprint_after=observation.fingerprint,
            elapsed_ms=elapsed_ms,
        )
        return verdict

    def note_scroll_sent(self, *, previous_viewport_complete: bool) -> bool:
        if not self.flags.enabled or self.current_viewport is None or self._safe_stop:
            return False
        self.pending_scroll_before = self.current_viewport
        self.pending_previous_viewport_complete = bool(previous_viewport_complete)
        self.pending_scroll_started_at = time.perf_counter()
        # In shadow this is observation of a legacy mutation, not one initiated
        # by V2.  The counter therefore remains zero.
        return True

    def commit_verified_progress(self, *, cursor_handle: str = "", reason: str = "validated_transition") -> bool:
        if self._safe_stop or not self._claimed or self.claimed_version is None or self.current_viewport is None:
            return False
        response = self.repository.commit(
            account_id=self.account_id,
            target_id=self.target_id,
            run_id=self.run_id,
            mode=self.flags.mode,
            expected_version=self.claimed_version,
            depth=self.reached_depth,
            observation=self.current_viewport,
            cursor_anchor=anchor_hash(cursor_handle),
            instagram_version=self.instagram_version,
            reason=reason,
        )
        if not response.get("ok"):
            self._event("target_followers_checkpoint_commit_conflict", reason=str(response.get("reason") or "commit_rejected"))
            return False
        self.claimed_version = int(response.get("optimistic_version") or self.claimed_version + 1)
        self._event("target_followers_checkpoint_committed", reason=reason, reached_depth=self.reached_depth)
        return True

    def mark_safe_stop(self) -> None:
        self._safe_stop = True
        self.pending_scroll_before = None
        self.pending_previous_viewport_complete = False

    def mark_end_reached(self, *, reason: str = "end_reached") -> None:
        self._event("target_followers_end_reached", reason=reason)


def build_runtime_controller(
    *,
    account_id: str,
    target_id: str,
    target_username: str,
    run_id: str,
    instagram_version: str = "",
    emit: Callable[[str, dict[str, Any]], None] | None = None,
    flags: ResumeFlags | None = None,
) -> ProgressiveResumeController | None:
    """Construct a controller only when one V2 flag is explicitly enabled."""
    flags = flags or ResumeFlags.from_env()
    if not flags.enabled or not all((_clean_id(account_id), _clean_id(target_id), _clean_id(run_id))):
        return None
    import supabase_client

    return ProgressiveResumeController(
        repository=ResumeRepository(supabase_client.call_rpc),
        flags=flags,
        account_id=_clean_id(account_id),
        target_id=_clean_id(target_id),
        target_username=normalize_handle(target_username),
        run_id=_clean_id(run_id),
        instagram_version=str(instagram_version or "")[:80],
        emit=emit,
    )
