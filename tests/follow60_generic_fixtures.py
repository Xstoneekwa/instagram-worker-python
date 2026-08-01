"""Account-neutral fixtures for Follow 60 canary tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from follow_60s_canary_binding_v2 import BINDING_VERSION


TEST_CANARY_ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
TEST_OTHER_ACCOUNT_ID = "22222222-2222-4222-8222-222222222222"
TEST_CANARY_USERNAME = "generic_canary_account"
TEST_WORKER_SHA = "a" * 40
TEST_CONTROL_ID = "33333333-3333-4333-8333-333333333333"
TEST_REQUEST_ID = "44444444-4444-4444-8444-444444444444"
TEST_BUSINESS_SESSION_ID = "generic-business-session"


def bound_control(
    *,
    account_id: str = TEST_CANARY_ACCOUNT_ID,
    expected_username: str = TEST_CANARY_USERNAME,
    worker_sha: str = TEST_WORKER_SHA,
    baseline_account_id: str | None = None,
    baseline_release_sha: str | None = None,
    status: str = "armed",
    run_id: str = "run-1",
    request_id: str = TEST_REQUEST_ID,
    attempt_id: int = 1,
    business_session_id: str = TEST_BUSINESS_SESSION_ID,
    active_control_count: int = 1,
    expires_delta_s: int = 3600,
    binding_valid: bool = True,
    revoked_at: str = "",
    completed_at: str = "",
    current_new_cycle_count: int = 0,
    max_new_cycles: int = 10,
    expected_run_type: str = "account_session",
    expected_package: str = "com.instagram.android",
    binding_version: str = BINDING_VERSION,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "account_id": account_id,
        "status": status,
        "baseline_follow_count": 0,
        "evaluation_increment": max_new_cycles,
        "target_follow_count": max_new_cycles,
        "run_id": run_id,
        "request_id": request_id,
        "attempt_id": attempt_id,
        "business_session_id": business_session_id,
        "binding_valid": binding_valid,
        "metadata_safe": {
            "control_id": TEST_CONTROL_ID,
            "expected_username": expected_username,
            "expected_worker_sha": worker_sha,
            "baseline_release_sha": (
                worker_sha if baseline_release_sha is None else baseline_release_sha
            ),
            "baseline_account_id": (
                account_id if baseline_account_id is None else baseline_account_id
            ),
            "baseline_captured_at": (now - timedelta(minutes=2)).isoformat(),
            "baseline_timezone": "Africa/Johannesburg",
            "baseline_package": expected_package,
            "baseline_warmup_ready": True,
            "armed_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(seconds=expires_delta_s)).isoformat(),
            "expected_package": expected_package,
            "expected_run_type": expected_run_type,
            "binding_version": binding_version,
            "idempotency_key": "generic-control-test-key",
            "created_by": "unit-test",
            "source": "unit_test",
            "revoked_at": revoked_at,
            "completed_at": completed_at,
            "active_control_count": active_control_count,
            "current_new_cycle_count": current_new_cycle_count,
        },
    }


def configure_canary(module: Any, **kwargs: Any) -> bool:
    account_id = str(kwargs.get("account_id") or "")
    run_id = str(kwargs.get("run_id") or "run-1")
    resume_policy = dict(kwargs.get("resume_policy") or {})
    attempt_id = int(resume_policy.get("attempt_id") or (2 if resume_policy else 1))
    request_id = str(kwargs.pop("request_id", TEST_REQUEST_ID))
    business_session_id = str(
        kwargs.pop("business_session_id", TEST_BUSINESS_SESSION_ID)
    )
    control = kwargs.pop(
        "control",
        bound_control(
            account_id=TEST_CANARY_ACCOUNT_ID,
            run_id=run_id,
            request_id=request_id,
            attempt_id=attempt_id,
            business_session_id=business_session_id,
            expected_package=str(kwargs.get("package") or ""),
        ),
    )
    return bool(
        module.configure(
            **kwargs,
            control=control,
            worker_sha=TEST_WORKER_SHA,
            run_type="account_session",
            request_id=request_id,
            business_session_id=business_session_id,
        )
    )
