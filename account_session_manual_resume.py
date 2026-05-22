"""
Manual account-session resume command builder.

V1E is intentionally passive: it only formats an operator-facing command plan.
It does not execute runner.py, schedule a restart, write to Supabase, or trigger
Follow / Unfollow / Welcome flows.
"""

from __future__ import annotations

import shlex
from typing import Any

UNKNOWN = "unknown"
UNSUPPORTED_RUNTIME_STATUS = "not_runtime_supported_yet"


def _value(*values: Any, default: Any = UNKNOWN) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return default


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == UNKNOWN:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _as_int(value: Any) -> int | None:
    if value is None or value == UNKNOWN:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _nested(mapping: dict[str, Any] | None, *path: str) -> Any:
    current: Any = mapping or {}
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _flatten_text(value: Any, *, parent_key: str = "") -> list[str]:
    ignored_keys = {"manual_resume_block_reason", "restart_block_reason"}
    if parent_key in ignored_keys:
        return []
    if isinstance(value, dict):
        out: list[str] = []
        for key, child in value.items():
            out.extend(_flatten_text(child, parent_key=str(key)))
        return out
    if isinstance(value, (list, tuple, set)):
        out = []
        for child in value:
            out.extend(_flatten_text(child, parent_key=parent_key))
        return out
    if value is None:
        return []
    return [str(value).lower()]


def _unsafe_markers(summary: dict[str, Any], resume_plan: dict[str, Any]) -> list[str]:
    explicit = resume_plan.get("unsafe_markers")
    markers = [str(item) for item in explicit] if isinstance(explicit, list) else []
    text = " ".join(_flatten_text({"summary": summary, "resume_plan": resume_plan}))
    marker_patterns = (
        ("challenge", ("challenge", "checkpoint")),
        ("restriction", ("restriction", "restricted")),
        ("account_mismatch", ("active_instagram_account_mismatch", "account_mismatch")),
        ("action_block", ("action_block", "action block", "feedback_required")),
        ("device_offline", ("device_offline", "device offline", "adb_offline")),
    )
    for marker, patterns in marker_patterns:
        if marker not in markers and any(pattern in text for pattern in patterns):
            markers.append(marker)
    return markers


def _quota_remaining(
    summary: dict[str, Any],
    resume_plan: dict[str, Any],
    phase: str,
) -> int | None:
    from_plan = _as_int(_nested(resume_plan, "quota_remaining", phase))
    if from_plan is not None:
        return from_plan
    key_by_phase = {
        "follow": "follow_quota_remaining",
        "unfollow": "unfollow_quota_remaining",
        "welcome": "welcome_remaining",
    }
    return _as_int(summary.get(key_by_phase[phase]))


def _phases_to_resume(
    summary: dict[str, Any],
    resume_plan: dict[str, Any],
) -> tuple[dict[str, bool | str], dict[str, int], list[str]]:
    raw_phases = resume_plan.get("phases_to_run")
    phases = raw_phases if isinstance(raw_phases, dict) else {}
    out: dict[str, bool | str] = {}
    quota_overrides: dict[str, int] = {}
    unknowns: list[str] = []

    for phase in ("welcome", "follow", "unfollow"):
        raw = phases.get(phase)
        enabled = _as_bool(raw)
        if enabled is None:
            out[phase] = UNKNOWN
            unknowns.append(f"phases_to_run.{phase}")
            continue
        out[phase] = enabled
        if enabled:
            remaining = _quota_remaining(summary, resume_plan, phase)
            if remaining is None:
                unknowns.append(f"quota_remaining.{phase}")
            else:
                quota_overrides[f"{phase}_remaining"] = remaining
    return out, quota_overrides, unknowns


def _command_args(
    *,
    runner_path: str,
    account_id: str,
    account_username: str,
) -> list[str]:
    return [
        "python3",
        runner_path,
        "--run-type",
        "account_session",
        "--account-id",
        account_id,
        "--username",
        account_username,
    ]


def _command_preview(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)


def build_manual_resume_command(
    summary: dict,
    resume_plan: dict | None = None,
    settings: dict | None = None,
) -> dict:
    """
    Build an operator-facing manual resume command plan.

    The returned object is a preview only. Quota overrides are represented as
    planned_env_overrides because the worker runtime does not yet support these
    quota override flags.
    """
    safe_summary: dict[str, Any] = dict(summary or {})
    safe_plan: dict[str, Any] = dict(
        resume_plan or safe_summary.get("auto_restart_resume_plan") or {}
    )
    safe_settings: dict[str, Any] = dict(settings or {})

    account_id = str(_value(safe_summary.get("account_id"), safe_settings.get("account_id"))).strip()
    account_username = str(
        _value(
            safe_summary.get("account_username"),
            safe_summary.get("username"),
            safe_settings.get("account_username"),
        )
    ).strip()
    runner_path = str(safe_settings.get("runner_path") or "runner.py").strip()
    restart_allowed = _as_bool(
        _value(
            safe_plan.get("restart_allowed"),
            safe_summary.get("auto_restart_restart_allowed"),
            default=None,
        )
    )
    unsafe_markers = _unsafe_markers(safe_summary, safe_plan)
    phases_to_resume, quota_overrides, unknowns = _phases_to_resume(
        safe_summary,
        safe_plan,
    )

    manual_resume_allowed = False
    block_reason = ""
    safety_warnings: list[str] = []
    operator_notes: list[str] = []

    if unsafe_markers:
        block_reason = unsafe_markers[0]
        safety_warnings.append("unsafe_marker_detected:" + ",".join(unsafe_markers))
    elif restart_allowed is False:
        block_reason = "restart_not_allowed"
    elif restart_allowed is None:
        block_reason = "unknown_required_field"
        unknowns.append("restart_allowed")
    elif account_id == UNKNOWN or account_username == UNKNOWN:
        block_reason = "unknown_required_field"
        if account_id == UNKNOWN:
            unknowns.append("account_id")
        if account_username == UNKNOWN:
            unknowns.append("account_username")
    elif unknowns:
        block_reason = "unknown_required_field"
    else:
        manual_resume_allowed = True

    args: list[str] = []
    command_preview = ""
    if manual_resume_allowed:
        args = _command_args(
            runner_path=runner_path,
            account_id=account_id,
            account_username=account_username,
        )
        command_preview = _command_preview(args)
        operator_notes.append(
            "Manual resume preview only; operator must execute explicitly."
        )
        if quota_overrides:
            operator_notes.append(
                "Quota overrides are planned metadata; runtime flags are not wired yet."
            )
    else:
        operator_notes.append("Manual resume blocked: " + (block_reason or UNKNOWN))

    planned_env_overrides = {
        key: {
            "value": value,
            "status": UNSUPPORTED_RUNTIME_STATUS,
        }
        for key, value in quota_overrides.items()
    }
    if planned_env_overrides:
        safety_warnings.append("quota_override_flags_not_runtime_supported_yet")

    return {
        "manual_resume_allowed": bool(manual_resume_allowed),
        "manual_resume_block_reason": "" if manual_resume_allowed else block_reason,
        "command_preview": command_preview,
        "command_args": args,
        "env_overrides": {},
        "planned_env_overrides": planned_env_overrides,
        "phases_to_resume": phases_to_resume,
        "quota_overrides": quota_overrides,
        "operator_notes": operator_notes,
        "safety_warnings": safety_warnings,
        "unknown_required_fields": sorted(set(unknowns)),
        "unsafe_markers": unsafe_markers,
    }
