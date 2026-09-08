"""Passive terminal projections. No device, scheduling or mutation authority."""
from __future__ import annotations

from typing import Any


def terminal_reason(*sources: dict[str, Any], fallback: str = "unknown") -> str:
    """A known causal reason always wins over an empty/default projection."""
    for key in ("canonical_stop_reason", "first_causal_reason", "root_failure_code",
                "reason_code", "follow_stop_reason", "stop_reason", "failure_reason", "reason"):
        for source in sources:
            value = str(source.get(key) or "").strip()
            if value.lower() not in {"", "unknown", "unknown_error", "none"}:
                return value
    return fallback


def project_terminal_summary(summary: dict[str, Any], *,
                             persisted_follow_count: int | None = None) -> dict[str, Any]:
    out = dict(summary)
    reason = terminal_reason(out)
    if reason != "unknown":
        out.update(first_causal_reason=reason, reason_code=reason,
                   reason=reason, follow_stop_reason=reason)
    if reason == "preventive_follow_restriction":
        out["restriction_evidence"] = (
            "corroborated_preventive"
            if out.get("restriction_evidence") == "corroborated_preventive"
            else "preventive"
        )
    raw_markers = out.get("unsafe_markers")
    markers = list(raw_markers) if isinstance(raw_markers, (list, tuple)) else []
    if reason in {"preventive_follow_restriction", "instagram_action_rate_limit"}:
        if "restriction" not in markers:
            markers.append("restriction")
        out["unsafe_markers"] = markers
        out["incident_type"] = "instagram_account_restriction"
        # Existing eligibility contract; this never authorizes execution.
        out["restart_eligibility"] = "blocked"
        out["session_termination_class"] = "blocked_restriction"
    if persisted_follow_count is not None:
        done = max(0, int(persisted_follow_count))
        out["follows_completed_count"] = done
        out["session_counters"] = {**dict(out.get("session_counters") or {}), "follows": done}
        out["actions_completed"] = {**dict(out.get("actions_completed") or {}), "follows": done}
        target = out.get("follow_quota_target")
        try:
            target = int(target) if target is not None else None
        except (ValueError, TypeError):
            target = None
        if target is not None and target >= 0:
            out["follow_quota_remaining"] = max(0, target - done)
        out["terminal_progress_source"] = "ig_interaction_events:verified_follow_success"
        out["terminal_progress_reconciled"] = True
    return out


def reconcile_terminal_progress(summary: dict[str, Any], *, read_count) -> dict[str, Any]:
    """One terminal read. Failure remains visible and never fabricates a zero."""
    out = project_terminal_summary(summary)
    try:
        count = read_count(account_id=out["account_id"], run_id=out["run_id"])
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("invalid_canonical_count")
    except Exception as exc:
        out["terminal_progress_reconciled"] = False
        out["terminal_progress_error"] = type(exc).__name__
        return out
    return project_terminal_summary(out, persisted_follow_count=count)
