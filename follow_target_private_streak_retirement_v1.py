"""Run-local policy for retiring a Follow source after confirmed private profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


PRIVATE_OPEN_STREAK_RETIRE_THRESHOLD = 8
PRIVATE_OPEN_STREAK_RETIRE_REASON = "consecutive_private_opened_profiles"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PrivateProfileStreakRetirement:
    """Track one CT without allowing duplicate probes to change its streak."""

    account_id: str
    target_id: str
    target_username: str
    run_id: str
    worker_sha: str = ""
    threshold: int = PRIVATE_OPEN_STREAK_RETIRE_THRESHOLD
    current_streak: int = 0
    max_streak: int = 0
    retire_pending: bool = False
    archived: bool = False
    pending_at: str | None = None
    archived_at: str | None = None
    persistence_error: str | None = None
    _classified_candidates: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.threshold = max(1, int(self.threshold))

    def observe_opened_profile(
        self,
        *,
        candidate_key: str,
        is_private: bool,
        opened_and_confirmed: bool,
    ) -> dict[str, Any]:
        """Apply exactly one positive classification per physically opened profile."""

        key = str(candidate_key or "").strip().lower()
        if not opened_and_confirmed or not key:
            return self.summary(observation="ignored_unconfirmed")
        if key in self._classified_candidates:
            return self.summary(observation="duplicate_ignored")

        private = bool(is_private)
        self._classified_candidates[key] = private
        if self.retire_pending:
            return self.summary(observation="pending_irrevocable")
        if private:
            self.current_streak += 1
            self.max_streak = max(self.max_streak, self.current_streak)
            if self.current_streak >= self.threshold:
                self.retire_pending = True
                self.pending_at = _utc_now()
        else:
            self.current_streak = 0
        return self.summary(observation="private" if private else "public_reset")

    def mark_archived(self, *, archived_at: str | None = None) -> None:
        if not self.retire_pending:
            raise ValueError("retirement_not_pending")
        self.archived = True
        self.archived_at = str(archived_at or _utc_now())
        self.persistence_error = None

    def mark_persistence_failed(self, reason: str) -> None:
        self.persistence_error = str(reason or "private_streak_archive_failed")[:300]

    @property
    def eligible_for_new_work(self) -> bool:
        return not self.retire_pending and not self.archived

    def summary(self, *, observation: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "private_streak_account_id": self.account_id,
            "private_streak_target_id": self.target_id,
            "private_streak_target_username": self.target_username,
            "private_streak_run_id": self.run_id,
            "private_streak_worker_sha": self.worker_sha,
            "private_streak_retire_reason": PRIVATE_OPEN_STREAK_RETIRE_REASON,
            "private_streak_current": int(self.current_streak),
            "private_streak_max": int(self.max_streak),
            "private_streak_threshold": int(self.threshold),
            "private_streak_retire_pending": bool(self.retire_pending),
            "private_streak_archived": bool(self.archived),
            "private_streak_pending_at": self.pending_at,
            "private_streak_archived_at": self.archived_at,
            "private_streak_persistence_error": self.persistence_error,
            "private_streak_distinct_opened_count": len(self._classified_candidates),
        }
        if observation:
            payload["private_streak_observation"] = observation
        return payload
