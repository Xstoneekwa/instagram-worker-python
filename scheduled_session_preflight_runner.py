#!/usr/bin/env python3
"""CP4 — verification-only scheduled session preflight runner."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import uiautomator2 as u2

from account_identity_guard import (
    ACCOUNT_IDENTITY_MISMATCH_REASON,
    verify_active_instagram_account_matches_expected,
)
from logs import log


PREFLIGHT_READY = "preflight_ready"
PREFLIGHT_BLOCKED = "preflight_blocked"
PREFLIGHT_RUN_TYPE = "scheduled_session_preflight"


def _connect_device(serial: str) -> u2.Device:
    return u2.connect(serial)


def _bring_package_foreground(d: u2.Device, package_name: str) -> bool:
    package = str(package_name or "").strip()
    if not package:
        return False
    try:
        d.app_start(package, stop=False)
    except Exception as exc:
        log("warning", "preflight_app_start_failed", package=package, error=str(exc)[:200])
        return False
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        try:
            current = str(d.app_current().get("package") or "").strip()
        except Exception:
            current = ""
        if current == package:
            return True
        time.sleep(0.5)
    return False


def _complete_preflight(
    *,
    preflight_id: str,
    status: str,
    reason_code: str | None,
    metadata: dict[str, Any],
    dashboard_context: dict[str, Any] | None = None,
) -> None:
    from supabase_client import call_rpc

    call_rpc(
        "complete_scheduled_session_preflight",
        {
            "p_preflight_id": preflight_id,
            "p_status": status,
            "p_reason_code": reason_code,
            "p_metadata_safe": metadata,
        },
    )
    if dashboard_context:
        from scheduled_session_preflight_control import reconcile_preflight_dashboard_action

        reconcile_preflight_dashboard_action(
            account_id=str(dashboard_context.get("account_id") or ""),
            assignment_id=str(dashboard_context.get("assignment_id") or ""),
            starts_at=str(dashboard_context.get("starts_at") or ""),
            preflight_status=status,
            reason_code=reason_code,
            source="scheduled_session_preflight_runner",
            metadata_safe={
                "request_id": dashboard_context.get("request_id"),
                "verification_only": True,
            },
        )


def run_scheduled_session_preflight(
    *,
    account_id: str,
    request_id: str,
    device_serial: str,
    package_name: str,
    expected_username: str,
    preflight_id: str,
    metadata_safe: dict[str, Any] | None = None,
) -> int:
    meta = dict(metadata_safe or {})
    dashboard_context = {
        "account_id": account_id,
        "assignment_id": str(meta.get("assignment_id") or ""),
        "starts_at": str(meta.get("scheduled_session_at") or ""),
        "request_id": request_id,
    }
    log(
        "info",
        "scheduled_session_preflight_started",
        account_id=account_id,
        request_id=request_id,
        preflight_id=preflight_id,
        run_type=PREFLIGHT_RUN_TYPE,
        verification_only=True,
    )
    if not device_serial:
        _complete_preflight(
            preflight_id=preflight_id,
            status=PREFLIGHT_BLOCKED,
            reason_code="device_serial_missing",
            metadata={"request_id": request_id},
            dashboard_context=dashboard_context,
        )
        return 12
    if not package_name:
        _complete_preflight(
            preflight_id=preflight_id,
            status=PREFLIGHT_BLOCKED,
            reason_code="expected_package_missing",
            metadata={"request_id": request_id},
            dashboard_context=dashboard_context,
        )
        return 12
    if not preflight_id:
        return 12

    d = _connect_device(device_serial)
    if not _bring_package_foreground(d, package_name):
        _complete_preflight(
            preflight_id=preflight_id,
            status=PREFLIGHT_BLOCKED,
            reason_code="expected_package_not_foreground",
            metadata={"request_id": request_id, "package_name": package_name},
            dashboard_context=dashboard_context,
        )
        return 13

    identity = verify_active_instagram_account_matches_expected(
        d,
        expected_account_username=expected_username,
        account_id=account_id,
        run_type=PREFLIGHT_RUN_TYPE,
        run_id=None,
        stage="scheduled_session_preflight_identity",
    )
    if not identity.ok:
        reason = str(identity.failure_reason or ACCOUNT_IDENTITY_MISMATCH_REASON)
        _complete_preflight(
            preflight_id=preflight_id,
            status=PREFLIGHT_BLOCKED,
            reason_code=reason,
            metadata={
                "request_id": request_id,
                "expected_account_username": expected_username,
                "actual_logged_in_username": identity.actual_logged_in_username,
                "verification_method": identity.verification_method,
            },
            dashboard_context=dashboard_context,
        )
        return 75

    _complete_preflight(
        preflight_id=preflight_id,
        status=PREFLIGHT_READY,
        reason_code=None,
        metadata={
            "request_id": request_id,
            "verification_only": True,
            "package_name": package_name,
            "expected_account_username": expected_username,
            **{k: v for k, v in meta.items() if k in {"scheduled_session_at", "scheduled_session_ends_at"}},
        },
        dashboard_context=dashboard_context,
    )
    log(
        "info",
        "scheduled_session_preflight_ready",
        account_id=account_id,
        request_id=request_id,
        preflight_id=preflight_id,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scheduled session preflight (verification-only)")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--device-serial", required=True)
    parser.add_argument("--package-name", required=True)
    parser.add_argument("--expected-username", required=True)
    parser.add_argument("--preflight-id", required=True)
    parser.add_argument("--metadata-json", default="{}")
    args = parser.parse_args(argv)
    try:
        metadata = json.loads(str(args.metadata_json or "{}"))
        if not isinstance(metadata, dict):
            metadata = {}
    except json.JSONDecodeError:
        metadata = {}
    return run_scheduled_session_preflight(
        account_id=str(args.account_id),
        request_id=str(args.request_id),
        device_serial=str(args.device_serial),
        package_name=str(args.package_name),
        expected_username=str(args.expected_username),
        preflight_id=str(args.preflight_id),
        metadata_safe=metadata,
    )


if __name__ == "__main__":
    raise SystemExit(main())
