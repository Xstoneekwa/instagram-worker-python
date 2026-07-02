"""Shared Auto Restart account_session bootstrap (no UI / no ADB integration path)."""

from __future__ import annotations

import os
from typing import Any

from auto_restart_runtime import (
    AUTO_RESTART_RESUME_PLAN_ENV,
    AUTO_RESTART_RESUME_PLAN_SCHEMA,
    load_resume_policy_from_env,
    phase_enabled,
)
from logs import log


class IntegrationNoUiViolation(RuntimeError):
    """Raised when integration mode attempts a UI/ADB action."""


class IntegrationNoUiDevice:
    """Strict fake device: any UI interaction is a harness failure."""

    def __getattr__(self, name: str):
        raise IntegrationNoUiViolation(f"integration_no_ui_forbidden:{name}")


def _integration_no_adb_enabled() -> bool:
    return os.environ.get("RUN_CONTROL_INTEGRATION_NO_ADB", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def assert_integration_no_ui_guards() -> None:
    if not _integration_no_adb_enabled():
        raise IntegrationNoUiViolation("RUN_CONTROL_INTEGRATION_NO_ADB required")
    for forbidden in ("ANDROID_SERIAL", "ADB_SERIAL", "INSTAGRAM_PACKAGE", "U2_DEVICE"):
        if os.environ.get(forbidden, "").strip():
            raise IntegrationNoUiViolation(f"forbidden_env:{forbidden}")


def apply_auto_restart_resume_policy_gates(
    policy: dict[str, Any],
    *,
    account_id: str,
    request_id: str,
) -> dict[str, bool]:
    """Mirror orchestrator phase gating without dispatching UI actions."""
    welcome_enabled = phase_enabled("welcome", default=False, policy=policy)
    follow_enabled = phase_enabled("follow", default=True, policy=policy)
    unfollow_enabled = phase_enabled("unfollow", default=False, policy=policy)
    log(
        "info",
        "auto_restart_resume_policy_applied",
        account_id=account_id,
        request_id=request_id,
        prior_run_id=policy.get("prior_run_id"),
        phases_to_run=policy.get("phases_to_run"),
        integration_no_ui=True,
    )
    return {
        "welcome": welcome_enabled,
        "follow": follow_enabled,
        "unfollow": unfollow_enabled,
    }


def bootstrap_account_session_resume_integration(
    *,
    request_id: str,
    account_id: str,
) -> dict[str, Any]:
    """
    Reach the same resume-plan load/validation/gating point as runner.py account_session
    without starting UI, ADB, or dispatch_account_session.
    """
    assert_integration_no_ui_guards()
    # Touch fake device guard once so accidental orchestrator dispatch would fail fast.
    _ = IntegrationNoUiDevice()

    policy = load_resume_policy_from_env()
    if not policy:
        return {
            "ok": False,
            "reason": "unknown_required_field",
            "request_id": request_id,
            "account_id": account_id,
            "no_ui_asserted": True,
            "exit_code": 2,
        }

    schema = str(policy.get("schema") or AUTO_RESTART_RESUME_PLAN_SCHEMA)
    if schema != AUTO_RESTART_RESUME_PLAN_SCHEMA:
        return {
            "ok": False,
            "reason": "resume_plan_invalid",
            "request_id": request_id,
            "account_id": account_id,
            "no_ui_asserted": True,
            "exit_code": 2,
        }

    phases = dict(policy.get("phases_to_run") or {})
    quota = dict(policy.get("quota_remaining") or {})
    applied = apply_auto_restart_resume_policy_gates(
        policy,
        account_id=account_id,
        request_id=request_id,
    )
    execution_worker_id = str(
        (policy.get("request_metadata") or {}).get("execution_worker_id")
        or os.environ.get("RUN_CONTROL_DISPATCHER_WORKER_ID")
        or ""
    ).strip()

    return {
        "ok": True,
        "runner": "auto_restart_integration_noop",
        "request_id": request_id,
        "account_id": account_id,
        "resume_plan_env": AUTO_RESTART_RESUME_PLAN_ENV,
        "resume_plan_version": policy.get("resume_plan_version") or 1,
        "resume_plan_schema": schema,
        "phases_received": phases,
        "phases_applied": applied,
        "quota_remaining": quota,
        "execution_worker_id": execution_worker_id or None,
        "prior_run_id": policy.get("prior_run_id"),
        "no_ui_asserted": True,
        "no_adb_asserted": True,
        "exit_code": 0,
    }
