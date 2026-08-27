"""Canonical outcome -> resume lifecycle -> Auto Restart contract.

``partial_resumable`` is a business outcome.  It is deliberately retained in
the JSON phase plan and is never emitted into the database ``resume_state``
column.  A safe, incomplete terminal attempt becomes ``resume_requested``;
the next natural control-plane tick may then evaluate (but does not blindly
enqueue) the frozen phase plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CONTRACT_VERSION = "golden_resume_plan_contract_v1"

# Public Python lifecycle API. Keep one definition; legacy store users re-export it.
RESUME_STATE_RESUME_REQUESTED = "resume_requested"

DB_ALLOWED_RESUME_STATES = frozenset(
    {
        "run_active",
        "pre_device_stopped",
        "recovery_enqueued",
        "awaiting_human_resume_authorization",
        RESUME_STATE_RESUME_REQUESTED,
        "resume_succeeded",
        "not_recoverable",
        "completed",
    }
)

WORKER_EMITTABLE_RESUME_STATES = frozenset(
    {
        "run_active",
        "awaiting_human_resume_authorization",
        RESUME_STATE_RESUME_REQUESTED,
        "resume_succeeded",
        "not_recoverable",
        "completed",
    }
)

PARTIAL_RESUMABLE_OUTCOMES = frozenset(
    {"partial_resumable", "partial_safe_stopped"}
)


@dataclass(frozen=True)
class ResumePlanTransition:
    outcome: str
    resume_stage: str
    resume_state: str
    auto_restart_decision: str
    restart_allowed: bool
    restart_block_reason: str


def _positive_remaining_quota(plan: dict[str, Any]) -> bool:
    quota = plan.get("quota_remaining")
    if not isinstance(quota, dict):
        return False
    total = quota.get("total")
    if isinstance(total, bool):
        return False
    if isinstance(total, (int, float)):
        return int(total) > 0
    return any(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and int(value) > 0
        for value in quota.values()
    )


def _has_planned_phase(plan: dict[str, Any]) -> bool:
    phases = plan.get("phases_to_run")
    return isinstance(phases, dict) and any(
        phases.get(name) is True for name in ("welcome", "follow", "unfollow")
    )


def _has_no_unsafe_markers(plan: dict[str, Any]) -> bool:
    markers = plan.get("unsafe_markers")
    return markers in (None, [])


def resolve_end_of_session_transition(
    *, session_plan: dict[str, Any] | None, session_status: str | None
) -> ResumePlanTransition:
    """Return the only lifecycle transition allowed for a terminal outcome."""
    plan = dict(session_plan or {})
    outcome = str(plan.get("session_termination_class") or session_status or "").strip().lower()
    restart_allowed = plan.get("restart_allowed") is True

    if (
        restart_allowed
        and outcome in PARTIAL_RESUMABLE_OUTCOMES
        and _positive_remaining_quota(plan)
        and _has_planned_phase(plan)
        and _has_no_unsafe_markers(plan)
    ):
        return ResumePlanTransition(
            outcome=outcome,
            resume_stage="phases",
            resume_state=RESUME_STATE_RESUME_REQUESTED,
            auto_restart_decision="schedule_resume",
            restart_allowed=True,
            restart_block_reason=str(plan.get("restart_block_reason") or ""),
        )

    if str(session_status or "").strip().lower() == "success" and not restart_allowed:
        return ResumePlanTransition(
            outcome=outcome or "completed",
            resume_stage="completed",
            resume_state="completed",
            auto_restart_decision="not_needed",
            restart_allowed=False,
            restart_block_reason=str(plan.get("restart_block_reason") or "session_completed"),
        )

    return ResumePlanTransition(
        outcome=outcome or "unclassified_terminal_outcome",
        resume_stage="completed",
        resume_state="not_recoverable",
        auto_restart_decision="blocked",
        restart_allowed=False,
        restart_block_reason="resume_plan_reconciliation_required",
    )


def assert_resume_state_contract() -> None:
    """Permanent machine gate: every Worker emission is DB-valid."""
    unknown = WORKER_EMITTABLE_RESUME_STATES - DB_ALLOWED_RESUME_STATES
    if unknown:
        raise AssertionError(f"worker_resume_states_not_db_allowed:{sorted(unknown)}")
    if "partial_resumable" in WORKER_EMITTABLE_RESUME_STATES:
        raise AssertionError("business_outcome_must_not_be_resume_state")


assert_resume_state_contract()
