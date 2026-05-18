"""Adaptive followers list exploration V1 — scroll policy and telemetry."""

from __future__ import annotations

from typing import Any

import config
from logs import log


def exploration_v1_enabled() -> bool:
    return bool(getattr(config, "FOLLOWERS_EXPLORATION_V1_ENABLED", True))


def followers_scroll_soft_max_per_session() -> int:
    if exploration_v1_enabled():
        return int(
            getattr(config, "FOLLOWERS_EXPLORATION_V1_SCROLL_SOFT_MAX_PER_SESSION", 120) or 120
        )
    return int(getattr(config, "FOLLOWERS_LIST_SCROLL_MAX_PER_SESSION", 25) or 25)


def followers_progressive_max_passes() -> int:
    base = int(getattr(config, "FOLLOWERS_LIST_PROGRESSIVE_EXPLORATION_MAX_PASSES", 3) or 3)
    if not exploration_v1_enabled():
        return max(1, min(base, 10))
    v1 = int(getattr(config, "FOLLOWERS_EXPLORATION_V1_PROGRESSIVE_MAX_PASSES", 12) or 12)
    return max(1, min(max(base, v1), 24))


def init_exploration_state(visual_loop_state: dict[str, Any]) -> dict[str, Any]:
    st = visual_loop_state.get("exploration_v1")
    if not isinstance(st, dict):
        st = {}
        visual_loop_state["exploration_v1"] = st
    defaults: dict[str, Any] = {
        "consecutive_visible_skip_count": 0,
        "scrolls_since_last_actionable_candidate": 0,
        "no_new_visual_progress_count": 0,
        "window_scrolls": 0,
        "window_visible_candidates_count": 0,
        "window_followable_candidates_count": 0,
        "skipped_already_connected_count": 0,
        "skipped_seen_memory_count": 0,
        "skipped_blocked_visual_count": 0,
        "skipped_runtime_seen_count": 0,
        "skipped_other_count": 0,
        "last_visible_candidate_ids": [],
        "last_stop_reason": "",
        "accelerated_scroll_triggers": 0,
        "pending_progress_check_after_scroll": False,
    }
    for key, val in defaults.items():
        st.setdefault(key, val if not isinstance(val, list) else [])
    return st


class FollowersExplorationV1:
    """Tracks skip streaks, scroll yield, and adaptive stop signals for one followers session."""

    def __init__(
        self,
        visual_loop_state: dict[str, Any],
        *,
        source_profile_username: str = "",
    ) -> None:
        self.source_profile_username = str(source_profile_username or "")
        self.state = init_exploration_state(visual_loop_state)

    def _emit(self, event: str, **fields: Any) -> None:
        try:
            log(
                "info",
                event,
                source_profile_username=self.source_profile_username,
                **fields,
            )
        except Exception:
            pass

    def _bucket_skip(self, reason: str) -> None:
        r = str(reason or "").lower()
        if "blocked_already_connected" in r or r == "blocked_visual":
            self.state["skipped_blocked_visual_count"] = (
                int(self.state.get("skipped_blocked_visual_count") or 0) + 1
            )
        elif (
            "already_connected" in r
            or "persistent" in r
            or "recent_already_connected" in r
        ):
            self.state["skipped_already_connected_count"] = (
                int(self.state.get("skipped_already_connected_count") or 0) + 1
            )
        elif (
            "runtime_seen" in r
            or "recently_followed" in r
            or "seen_memory" in r
            or "verified_follow" in r
        ):
            self.state["skipped_seen_memory_count"] = (
                int(self.state.get("skipped_seen_memory_count") or 0) + 1
            )
        elif "runtime" in r:
            self.state["skipped_runtime_seen_count"] = (
                int(self.state.get("skipped_runtime_seen_count") or 0) + 1
            )
        else:
            self.state["skipped_other_count"] = (
                int(self.state.get("skipped_other_count") or 0) + 1
            )

    def note_visible_skip(self, reason: str) -> None:
        self.state["consecutive_visible_skip_count"] = (
            int(self.state.get("consecutive_visible_skip_count") or 0) + 1
        )
        self._bucket_skip(reason)
        self._emit(
            "followers_exploration_skip_streak_updated",
            consecutive_visible_skip_count=int(
                self.state.get("consecutive_visible_skip_count") or 0
            ),
            skip_reason=str(reason)[:120],
            skipped_already_connected_count=int(
                self.state.get("skipped_already_connected_count") or 0
            ),
            skipped_seen_memory_count=int(
                self.state.get("skipped_seen_memory_count") or 0
            ),
            skipped_blocked_visual_count=int(
                self.state.get("skipped_blocked_visual_count") or 0
            ),
        )

    def note_actionable_pick(self) -> None:
        prev = int(self.state.get("consecutive_visible_skip_count") or 0)
        if prev > 0:
            self._emit(
                "followers_exploration_skip_streak_reset",
                previous_consecutive_visible_skip_count=prev,
            )
        self.state["consecutive_visible_skip_count"] = 0
        self.state["scrolls_since_last_actionable_candidate"] = 0
        self.state["no_new_visual_progress_count"] = 0
        self.state["window_followable_candidates_count"] = (
            int(self.state.get("window_followable_candidates_count") or 0) + 1
        )

    def begin_visible_window(self, candidates: list[Any]) -> None:
        self.state["window_visible_candidates_count"] = len(candidates)
        vids = [
            str(c.get("visual_candidate_id") or "").strip()
            for c in candidates
            if str(c.get("visual_candidate_id") or "").strip()
        ]
        self.state["_pending_window_vids"] = vids

    def mark_scroll_completed_pending_check(self) -> None:
        self.state["pending_progress_check_after_scroll"] = True
        self.state["scrolls_since_last_actionable_candidate"] = (
            int(self.state.get("scrolls_since_last_actionable_candidate") or 0) + 1
        )
        self.state["window_scrolls"] = int(self.state.get("window_scrolls") or 0) + 1

    def apply_post_scroll_candidate_window(self, candidates: list[Any], *, scroll_used: int) -> None:
        if not self.state.pop("pending_progress_check_after_scroll", False):
            return
        prev_ids = set(self.state.get("last_visible_candidate_ids") or [])
        new_ids = {
            str(c.get("visual_candidate_id") or "").strip()
            for c in candidates
            if str(c.get("visual_candidate_id") or "").strip()
        }
        new_only = new_ids - prev_ids if new_ids else set()
        if new_ids:
            self.state["last_visible_candidate_ids"] = list(new_ids)[:48]
        if new_only:
            self.state["no_new_visual_progress_count"] = 0
            self._emit(
                "followers_exploration_new_candidates_after_scroll",
                new_candidate_found_after_scroll=True,
                new_visual_candidate_count=len(new_only),
                scroll_used=int(scroll_used),
                visible_candidates_count=len(candidates),
            )
        else:
            self.state["no_new_visual_progress_count"] = (
                int(self.state.get("no_new_visual_progress_count") or 0) + 1
            )
            self._emit(
                "followers_exploration_no_new_visual_progress",
                new_candidate_found_after_scroll=False,
                no_new_visual_progress_count=int(
                    self.state.get("no_new_visual_progress_count") or 0
                ),
                scroll_used=int(scroll_used),
                visible_candidates_count=len(candidates),
            )
        self.emit_state_snapshot(
            phase="post_scroll_window",
            scroll_used=int(scroll_used),
            yield_followable_per_scroll_window=(
                float(self.state.get("window_followable_candidates_count") or 0)
                / max(1, int(self.state.get("window_scrolls") or 1))
            ),
        )
        self.state["window_followable_candidates_count"] = 0
        self.state["window_scrolls"] = 0

    def should_use_accelerated_scroll(self) -> bool:
        if not exploration_v1_enabled():
            return False
        thr = int(
            getattr(config, "FOLLOWERS_EXPLORATION_V1_SKIP_STREAK_ACCEL_THRESHOLD", 3) or 3
        )
        return int(self.state.get("consecutive_visible_skip_count") or 0) >= max(1, thr)

    def choose_scroll_profile(
        self,
        *,
        base_profile: str,
        exploratory_armed: bool,
        forced_already_connected: bool = False,
    ) -> str:
        if exploratory_armed and base_profile in (
            "micro_reposition",
            "zero_follow_spans_soft",
        ):
            prof = base_profile
        elif exploration_v1_enabled() and (
            forced_already_connected or self.should_use_accelerated_scroll()
        ):
            prof = "accelerated_skip_streak"
            self.state["accelerated_scroll_triggers"] = (
                int(self.state.get("accelerated_scroll_triggers") or 0) + 1
            )
            self._emit(
                "followers_exploration_accelerated_scroll_triggered",
                scroll_profile=prof,
                consecutive_visible_skip_count=int(
                    self.state.get("consecutive_visible_skip_count") or 0
                ),
                forced_already_connected=bool(forced_already_connected),
            )
        else:
            prof = str(base_profile or "default").strip() or "default"
        self._emit(
            "followers_exploration_scroll_profile_chosen",
            scroll_profile=prof,
            consecutive_visible_skip_count=int(
                self.state.get("consecutive_visible_skip_count") or 0
            ),
            exploratory_armed=bool(exploratory_armed),
        )
        return prof

    def emit_state_snapshot(self, **extra: Any) -> None:
        self._emit(
            "followers_exploration_state_snapshot",
            consecutive_visible_skip_count=int(
                self.state.get("consecutive_visible_skip_count") or 0
            ),
            scrolls_since_last_actionable_candidate=int(
                self.state.get("scrolls_since_last_actionable_candidate") or 0
            ),
            no_new_visual_progress_count=int(
                self.state.get("no_new_visual_progress_count") or 0
            ),
            visible_candidates_count=int(
                self.state.get("window_visible_candidates_count") or 0
            ),
            followable_candidates_count=int(
                self.state.get("window_followable_candidates_count") or 0
            ),
            skipped_already_connected_count=int(
                self.state.get("skipped_already_connected_count") or 0
            ),
            skipped_seen_memory_count=int(
                self.state.get("skipped_seen_memory_count") or 0
            ),
            skipped_blocked_visual_count=int(
                self.state.get("skipped_blocked_visual_count") or 0
            ),
            accelerated_scroll_triggers=int(
                self.state.get("accelerated_scroll_triggers") or 0
            ),
            **extra,
        )

    def _suppress_stagnation_pending_initial_budget(
        self,
        *,
        follows_completed: int,
        exploration_passes_used: int,
        exploration_max_passes: int,
        list_progressive_exhausted: bool,
    ) -> bool:
        """Keep exploring when progressive budget remains and no follow was attempted yet."""
        return (
            int(follows_completed) == 0
            and not bool(list_progressive_exhausted)
            and int(exploration_max_passes) > 0
            and int(exploration_passes_used) < int(exploration_max_passes)
        )

    def should_stop_scrolling(
        self,
        *,
        scroll_used: int,
        max_scroll_soft: int,
        follows_completed: int,
        max_iter: int,
        list_progressive_exhausted: bool,
        session_elapsed_s: float,
        exploration_passes_used: int = 0,
        exploration_max_passes: int = 0,
    ) -> tuple[bool, str]:
        if not exploration_v1_enabled():
            if scroll_used >= max_scroll_soft:
                return True, "scroll_cap_legacy"
            return False, ""

        if follows_completed >= max_iter and max_iter > 0:
            return True, "session_follow_goal_met"

        no_prog_max = int(
            getattr(config, "FOLLOWERS_EXPLORATION_V1_NO_NEW_VISUAL_PROGRESS_MAX", 5) or 5
        )
        if int(self.state.get("no_new_visual_progress_count") or 0) >= max(1, no_prog_max):
            if self._suppress_stagnation_pending_initial_budget(
                follows_completed=int(follows_completed),
                exploration_passes_used=int(exploration_passes_used),
                exploration_max_passes=int(exploration_max_passes),
                list_progressive_exhausted=bool(list_progressive_exhausted),
            ):
                self._emit(
                    "followers_exploration_stagnation_stop_suppressed_pending_initial_budget",
                    stop_reason="stagnation_no_new_visual_progress",
                    follows_completed_count=int(follows_completed),
                    exploration_passes_used=int(exploration_passes_used),
                    exploration_max_passes=int(exploration_max_passes),
                    list_progressive_exploration_exhausted=bool(list_progressive_exhausted),
                    no_new_visual_progress_count=int(
                        self.state.get("no_new_visual_progress_count") or 0
                    ),
                )
                return False, ""
            return True, "stagnation_no_new_visual_progress"

        no_actionable_max = int(
            getattr(config, "FOLLOWERS_EXPLORATION_V1_NO_ACTIONABLE_SCROLL_MAX", 15) or 15
        )
        if (
            int(self.state.get("scrolls_since_last_actionable_candidate") or 0)
            >= max(1, no_actionable_max)
        ):
            return True, "stagnation_no_actionable_candidate"

        if list_progressive_exhausted and int(
            self.state.get("scrolls_since_last_actionable_candidate") or 0
        ) >= max(3, no_actionable_max // 2):
            return True, "progressive_exploration_exhausted"

        budget_s = float(getattr(config, "FOLLOWERS_EXPLORATION_V1_SESSION_BUDGET_S", 0) or 0)
        if budget_s > 0 and session_elapsed_s >= budget_s:
            return True, "session_budget_exceeded"

        abs_max = int(
            getattr(config, "FOLLOWERS_EXPLORATION_V1_SCROLL_ABSOLUTE_MAX", 200) or 200
        )
        if scroll_used >= max(1, abs_max):
            return True, "scroll_absolute_safety_cap"

        if scroll_used >= max_scroll_soft:
            if int(self.state.get("no_new_visual_progress_count") or 0) < 2:
                self._emit(
                    "followers_exploration_scroll_continued",
                    reason="past_soft_cap_still_progressing",
                    scroll_used=int(scroll_used),
                    max_scroll_soft=int(max_scroll_soft),
                )
                return False, ""
            return True, "scroll_soft_cap_with_stagnation"

        return False, ""

    def log_stop_reason(self, reason: str, **extra: Any) -> None:
        self.state["last_stop_reason"] = str(reason)[:200]
        self._emit("followers_exploration_stop_reason", stop_reason=str(reason)[:200], **extra)

    def log_end_reached_confirmed(self, **extra: Any) -> None:
        self._emit("followers_exploration_end_reached_confirmed", **extra)
