"""
Follow physical flow — explicit state contract for candidate follow / post-follow.

Single source of truth for blocking decisions and allowed actions. Modules
(runner trace, screen guard, private gate, mute, like) read/write this context
instead of scattering ad-hoc dict flags.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from logs import log


class FollowPhysicalState(str, Enum):
    """Ordered phases for one candidate on one CT followers-list iteration."""

    IDLE = "idle"
    CANDIDATE_PROFILE_OPENED = "candidate_profile_opened"
    CANDIDATE_IDENTITY_CONFIRMED = "candidate_identity_confirmed"
    PRIVATE_CHECK_DONE = "private_check_done"
    FOLLOW_ALLOWED = "follow_allowed"
    FOLLOW_BLOCKED = "follow_blocked"
    FOLLOW_SENT = "follow_sent"
    FOLLOW_VERIFIED = "follow_verified"
    POST_FOLLOW_DECISION = "post_follow_decision"
    MUTE_SHEET_OPENED = "mute_sheet_opened"
    MUTE_SKIPPED = "mute_skipped"
    MUTE_DONE_OR_SKIPPED = "mute_done_or_skipped"
    SHEET_DISMISSED = "sheet_dismissed"
    POST_GRID_READY = "post_grid_ready"
    POST_GRID_BLOCKED = "post_grid_blocked"
    LIKE_SURFACE_READY = "like_surface_ready"
    LIKE_DONE_OR_SKIPPED = "like_done_or_skipped"
    RETURNED_TO_CT = "returned_to_ct"
    TARGET_ROTATION_DECIDED = "target_rotation_decided"


# States from which perform_follow_safe is permitted.
_FOLLOW_TAP_ALLOWED_STATES = frozenset(
    {
        FollowPhysicalState.FOLLOW_ALLOWED,
    }
)

# States from which like grid probe is permitted.
_LIKE_PROBE_ALLOWED_STATES = frozenset(
    {
        FollowPhysicalState.LIKE_SURFACE_READY,
        FollowPhysicalState.SHEET_DISMISSED,
    }
)


@dataclass
class FollowContext:
    """Per-candidate runtime contract carried through pre-follow and post-follow."""

    follower_username: str = ""
    source_profile_username: str = ""
    visual_candidate_id: str = ""
    current_state: FollowPhysicalState = FollowPhysicalState.IDLE
    candidate_allowed_to_follow: bool = True
    blocked_reason: str = ""
    private_detected: bool = False
    source_of_decision: str = "initial"
    mute_sheet_visible: bool = False
    mute_sheet_dismissed: bool = False
    mute_sheet_still_open: bool = False
    followers_list_visible: bool = False
    profile_candidate_visible: bool = False
    like_surface_ready: bool = False
    suggested_overlay_visible: bool = False
    state_entered_mono: float = field(default_factory=time.monotonic)
    transition_history: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def new(
        cls,
        *,
        follower_username: str = "",
        source_profile_username: str = "",
        visual_candidate_id: str = "",
    ) -> FollowContext:
        ctx = cls(
            follower_username=str(follower_username or "").strip(),
            source_profile_username=str(source_profile_username or "").strip(),
            visual_candidate_id=str(visual_candidate_id or "").strip(),
            current_state=FollowPhysicalState.IDLE,
            candidate_allowed_to_follow=True,
            source_of_decision="initial",
        )
        ctx._emit_transition(
            from_state=FollowPhysicalState.IDLE,
            to_state=FollowPhysicalState.IDLE,
            reason="context_created",
        )
        return ctx

    def to_decision_dict(self) -> dict[str, Any]:
        """Backward-compatible dict for legacy call sites."""
        return {
            "candidate_allowed_to_follow": bool(self.candidate_allowed_to_follow),
            "blocked_reason": str(self.blocked_reason or ""),
            "private_detected": bool(self.private_detected),
            "source_of_decision": str(self.source_of_decision or ""),
            "follower_username": str(self.follower_username or ""),
            "visual_candidate_id": str(self.visual_candidate_id or ""),
            "current_state": str(self.current_state.value),
        }

    @classmethod
    def from_decision_dict(cls, decision: dict[str, Any] | None) -> FollowContext:
        if not isinstance(decision, dict):
            return cls.new()
        st_raw = str(decision.get("current_state") or FollowPhysicalState.IDLE.value)
        try:
            st = FollowPhysicalState(st_raw)
        except ValueError:
            st = FollowPhysicalState.IDLE
        return cls(
            follower_username=str(decision.get("follower_username") or ""),
            visual_candidate_id=str(decision.get("visual_candidate_id") or ""),
            source_profile_username=str(decision.get("source_profile_username") or ""),
            current_state=st,
            candidate_allowed_to_follow=bool(
                decision.get("candidate_allowed_to_follow", True)
            ),
            blocked_reason=str(decision.get("blocked_reason") or ""),
            private_detected=bool(decision.get("private_detected")),
            source_of_decision=str(decision.get("source_of_decision") or "legacy"),
        )

    @classmethod
    def from_follow_verified(
        cls,
        *,
        follower_username: str = "",
        source_profile_username: str = "",
        visual_candidate_id: str = "",
        follow_state_after: str = "",
    ) -> FollowContext:
        ctx = cls.new(
            follower_username=follower_username,
            source_profile_username=source_profile_username,
            visual_candidate_id=visual_candidate_id,
        )
        ctx.transition(
            FollowPhysicalState.FOLLOW_VERIFIED,
            reason="follow_verified_context_carry_over",
            extra={"follow_state_after": str(follow_state_after or "")},
        )
        return ctx

    def _emit_transition(
        self,
        *,
        from_state: FollowPhysicalState,
        to_state: FollowPhysicalState,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        elapsed_ms = round(
            (time.monotonic() - float(self.state_entered_mono)) * 1000.0, 2
        )
        rec = {
            "from_state": from_state.value,
            "to_state": to_state.value,
            "reason": str(reason or ""),
            "elapsed_in_state_ms": elapsed_ms,
            "follower_username": self.follower_username,
            "source_profile_username": self.source_profile_username,
            "visual_candidate_id": self.visual_candidate_id,
            "candidate_allowed_to_follow": self.candidate_allowed_to_follow,
            "private_detected": self.private_detected,
        }
        if extra:
            rec.update(extra)
        self.transition_history.append(rec)
        try:
            log("info", "follow_state_transition", **rec)
        except Exception:
            pass
        self.current_state = to_state
        self.state_entered_mono = time.monotonic()

    def transition(
        self,
        to_state: FollowPhysicalState,
        *,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> FollowContext:
        if to_state == self.current_state and not extra:
            return self
        self._emit_transition(
            from_state=self.current_state,
            to_state=to_state,
            reason=reason,
            extra=extra,
        )
        return self

    def mark_profile_opened(self, *, reason: str = "follower_profile_open_success") -> FollowContext:
        return self.transition(FollowPhysicalState.CANDIDATE_PROFILE_OPENED, reason=reason)

    def mark_identity_confirmed(self, *, reason: str = "profile_username_resolved") -> FollowContext:
        return self.transition(
            FollowPhysicalState.CANDIDATE_IDENTITY_CONFIRMED, reason=reason
        )

    def block_follow(
        self,
        *,
        reason: str,
        source: str,
        private_detected: bool = False,
    ) -> FollowContext:
        self.candidate_allowed_to_follow = False
        self.blocked_reason = str(reason or "follow_blocked")
        self.private_detected = bool(private_detected) or self.private_detected
        self.source_of_decision = str(source or "unknown")
        return self.transition(
            FollowPhysicalState.FOLLOW_BLOCKED,
            reason=self.blocked_reason,
            extra={
                "source_of_decision": self.source_of_decision,
                "private_detected": self.private_detected,
            },
        )

    def allow_follow(self, *, source: str = "pre_follow_gates_passed") -> FollowContext:
        if self.private_detected:
            return self.block_follow(
                reason="private_account",
                source=source,
                private_detected=True,
            )
        self.candidate_allowed_to_follow = True
        self.blocked_reason = ""
        self.source_of_decision = str(source or "")
        return self.transition(
            FollowPhysicalState.FOLLOW_ALLOWED,
            reason="follow_allowed",
            extra={"source_of_decision": self.source_of_decision},
        )

    def apply_nav_trace_decision(
        self,
        nav_obs: dict[str, Any] | None,
        *,
        source: str = "candidate_profile_analysis",
    ) -> FollowContext:
        """Ingest visual_candidate_next_action_decision fields from trace."""
        if not isinstance(nav_obs, dict):
            return self
        if bool(nav_obs.get("private_detected")):
            return self.block_follow(
                reason="private_account",
                source=str(nav_obs.get("source_of_decision") or source),
                private_detected=True,
            )
        allowed = nav_obs.get("candidate_allowed_to_follow")
        if allowed is False:
            return self.block_follow(
                reason=str(nav_obs.get("blocked_reason") or "should_follow_false"),
                source=str(nav_obs.get("source_of_decision") or source),
                private_detected=bool(nav_obs.get("private_detected")),
            )
        if allowed is True and not self.private_detected:
            self.transition(
                FollowPhysicalState.PRIVATE_CHECK_DONE,
                reason="trace_private_not_detected",
            )
        return self

    def apply_private_gate_result(
        self,
        gate: dict[str, Any] | None,
        *,
        source: str = "pre_follow_private_gate",
    ) -> FollowContext:
        if not isinstance(gate, dict):
            return self
        if bool(gate.get("reject")) or bool(gate.get("private_profile_detected")):
            return self.block_follow(
                reason="private_account",
                source=source,
                private_detected=True,
            )
        return self.transition(FollowPhysicalState.PRIVATE_CHECK_DONE, reason="private_gate_passed")

    def blocks_follow(self) -> bool:
        return not bool(self.candidate_allowed_to_follow)

    def assert_can_perform_follow_safe(self) -> tuple[bool, str]:
        """
        Terminal guard before perform_follow_safe.
        Returns (ok, stable_reason).
        """
        if self.blocks_follow():
            reason = str(self.blocked_reason or "follow_blocked")
            if self.private_detected:
                reason = "private_account"
            return False, reason
        if self.current_state not in _FOLLOW_TAP_ALLOWED_STATES:
            return False, f"invalid_state_for_follow_tap:{self.current_state.value}"
        return True, ""

    def mark_follow_sent(self, *, reason: str = "follow_tap_sent") -> FollowContext:
        return self.transition(FollowPhysicalState.FOLLOW_SENT, reason=reason)

    def mark_follow_verified(self, *, reason: str = "follow_verify_success") -> FollowContext:
        return self.transition(FollowPhysicalState.FOLLOW_VERIFIED, reason=reason)

    def apply_mute_sheet_precheck(
        self,
        *,
        sheet_visible: bool,
        dismissed: bool,
        still_open: bool,
    ) -> FollowContext:
        self.mute_sheet_visible = bool(sheet_visible)
        self.mute_sheet_dismissed = bool(dismissed)
        self.mute_sheet_still_open = bool(still_open)
        if not sheet_visible:
            return self.transition(
                FollowPhysicalState.SHEET_DISMISSED,
                reason="mute_sheet_not_visible",
            )
        self.transition(FollowPhysicalState.MUTE_SHEET_OPENED, reason="mute_sheet_visible")
        if dismissed and not still_open:
            return self.transition(
                FollowPhysicalState.SHEET_DISMISSED,
                reason="mute_sheet_dismiss_completed",
            )
        if still_open:
            self.like_surface_ready = False
            return self.transition(
                FollowPhysicalState.LIKE_DONE_OR_SKIPPED,
                reason="mute_sheet_still_open",
            )
        return self

    def mark_post_follow_decision(
        self, *, reason: str = "post_follow_started", extra: dict[str, Any] | None = None
    ) -> FollowContext:
        return self.transition(
            FollowPhysicalState.POST_FOLLOW_DECISION,
            reason=reason,
            extra=extra,
        )

    def mark_mute_skipped(self, *, reason: str) -> FollowContext:
        return self.transition(FollowPhysicalState.MUTE_SKIPPED, reason=reason)

    def mark_mute_done_or_skipped(self, *, reason: str) -> FollowContext:
        return self.transition(FollowPhysicalState.MUTE_DONE_OR_SKIPPED, reason=reason)

    def mark_post_grid_blocked(self, *, reason: str) -> FollowContext:
        self.like_surface_ready = False
        return self.transition(FollowPhysicalState.POST_GRID_BLOCKED, reason=reason)

    def mark_post_grid_ready(self, *, reason: str = "post_grid_ready") -> FollowContext:
        self.like_surface_ready = True
        return self.transition(FollowPhysicalState.POST_GRID_READY, reason=reason)

    def apply_like_surface_precheck(
        self,
        *,
        followers_list_visible: bool,
        profile_candidate_visible: bool,
        suggested_overlay_visible: bool = False,
        skip_reason: str = "",
    ) -> FollowContext:
        self.followers_list_visible = bool(followers_list_visible) or (
            str(skip_reason or "") == "followers_list_visible_before_like"
        )
        self.profile_candidate_visible = bool(profile_candidate_visible)
        self.suggested_overlay_visible = bool(suggested_overlay_visible)
        if skip_reason:
            self.like_surface_ready = False
            return self.transition(
                FollowPhysicalState.POST_GRID_BLOCKED,
                reason=skip_reason,
            )
        if not profile_candidate_visible:
            self.like_surface_ready = False
            return self.transition(
                FollowPhysicalState.POST_GRID_BLOCKED,
                reason="candidate_profile_not_visible_before_like",
            )
        self.like_surface_ready = True
        return self.transition(
            FollowPhysicalState.POST_GRID_READY,
            reason="post_grid_ready",
        )

    def assert_can_probe_like_grid(self) -> tuple[bool, str]:
        if self.current_state in {
            FollowPhysicalState.LIKE_DONE_OR_SKIPPED,
            FollowPhysicalState.POST_GRID_BLOCKED,
        }:
            if self.transition_history:
                return False, str(self.transition_history[-1].get("reason") or "like_skipped")
            return False, "like_skipped"
        if self.mute_sheet_still_open or (
            self.mute_sheet_visible and not self.mute_sheet_dismissed
        ):
            return False, "mute_sheet_still_open"
        if self.followers_list_visible:
            return False, "followers_list_visible_before_like"
        if not self.profile_candidate_visible:
            return False, "candidate_profile_not_visible_before_like"
        if (
            self.current_state not in _LIKE_PROBE_ALLOWED_STATES
            and self.current_state != FollowPhysicalState.POST_GRID_READY
            and not self.like_surface_ready
        ):
            return False, f"invalid_state_for_like_probe:{self.current_state.value}"
        return True, ""

    def mark_like_done_or_skipped(self, *, reason: str) -> FollowContext:
        self.like_surface_ready = False
        return self.transition(FollowPhysicalState.LIKE_DONE_OR_SKIPPED, reason=reason)

    def mark_returned_to_ct(self, *, reason: str = "return_ct_ok") -> FollowContext:
        return self.transition(FollowPhysicalState.RETURNED_TO_CT, reason=reason)

    def mark_target_rotation(self, *, reason: str) -> FollowContext:
        return self.transition(FollowPhysicalState.TARGET_ROTATION_DECIDED, reason=reason)


# --- Legacy dict-style helpers (runner tests / gradual migration) ---


def new_candidate_follow_decision(
    *,
    follower_username: str = "",
    visual_candidate_id: str = "",
) -> dict[str, Any]:
    return FollowContext.new(
        follower_username=follower_username,
        visual_candidate_id=visual_candidate_id,
    ).to_decision_dict()


def _sync_decision_dict(decision: dict[str, Any], ctx: FollowContext) -> dict[str, Any]:
    """Keep legacy in-place dict mutation semantics for runner pipeline."""
    decision.clear()
    decision.update(ctx.to_decision_dict())
    return decision


def candidate_follow_decision_block(
    decision: dict[str, Any] | FollowContext,
    *,
    reason: str,
    source: str,
    private_detected: bool = False,
) -> dict[str, Any]:
    if isinstance(decision, FollowContext):
        decision.block_follow(
            reason=reason, source=source, private_detected=private_detected
        )
        return decision.to_decision_dict()
    ctx = FollowContext.from_decision_dict(decision)
    ctx.block_follow(reason=reason, source=source, private_detected=private_detected)
    return _sync_decision_dict(decision, ctx)


def candidate_follow_decision_apply_source(
    decision: dict[str, Any] | FollowContext,
    source_decision: dict[str, Any] | None,
    *,
    source: str,
) -> dict[str, Any]:
    if isinstance(decision, FollowContext):
        decision.apply_nav_trace_decision(source_decision, source=source)
        return decision.to_decision_dict()
    ctx = FollowContext.from_decision_dict(decision)
    ctx.apply_nav_trace_decision(source_decision, source=source)
    return _sync_decision_dict(decision, ctx)


def candidate_follow_decision_blocks_follow(decision: dict[str, Any] | FollowContext | None) -> bool:
    if isinstance(decision, FollowContext):
        return decision.blocks_follow()
    if not isinstance(decision, dict):
        return False
    return bool(decision.get("candidate_allowed_to_follow") is False)


def evaluate_like_precheck_contract(
    *,
    sheet_precheck: dict[str, Any] | None = None,
    surface_precheck: dict[str, Any] | None = None,
    follower_username: str = "",
    source_profile_username: str = "",
    visual_candidate_id: str = "",
    initial_context: FollowContext | None = None,
) -> tuple[FollowContext, bool, str]:
    """
    Build contract state from mute-sheet and surface precheck payloads.
    Returns (ctx, can_probe_grid, skip_reason).
    """
    ctx = initial_context or FollowContext.from_follow_verified(
        follower_username=follower_username,
        source_profile_username=source_profile_username,
        visual_candidate_id=visual_candidate_id,
        follow_state_after="",
    )
    if follower_username and not ctx.follower_username:
        ctx.follower_username = str(follower_username or "")
    if source_profile_username and not ctx.source_profile_username:
        ctx.source_profile_username = str(source_profile_username or "")
    if visual_candidate_id and not ctx.visual_candidate_id:
        ctx.visual_candidate_id = str(visual_candidate_id or "")
    ctx.mark_post_follow_decision(reason="post_follow_started")
    sp = sheet_precheck if isinstance(sheet_precheck, dict) else {}
    ctx.apply_mute_sheet_precheck(
        sheet_visible=bool(sp.get("sheet_visible")),
        dismissed=bool(sp.get("dismissed")),
        still_open=bool(sp.get("still_open")),
    )
    if bool(sp.get("skip_like")):
        ok, reason = ctx.assert_can_probe_like_grid()
        return ctx, ok, str(sp.get("skip_reason") or reason or "mute_sheet_still_open")
    surf = surface_precheck if isinstance(surface_precheck, dict) else {}
    skip = str(surf.get("skip_reason") or "")
    ctx.apply_like_surface_precheck(
        followers_list_visible=bool(surf.get("followers_list_visible")),
        profile_candidate_visible=bool(surf.get("profile_candidate_visible")),
        suggested_overlay_visible=bool(surf.get("suggested_overlay_visible")),
        skip_reason=skip,
    )
    ok, reason = ctx.assert_can_probe_like_grid()
    if not ok:
        return ctx, False, reason
    return ctx, True, ""
