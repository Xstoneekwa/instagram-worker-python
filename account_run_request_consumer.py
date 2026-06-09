"""Supervised Run Control dispatcher for account_run_requests.

RunControl-2: health-only mode (heartbeat, no claim/launch).
RunControl-4: claim + controlled subprocess launch when explicitly enabled.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import config
import runtime_heartbeat
from device import runner_subprocess_env
from account_run_control import (
    ACTIVE_REQUEST_STATUSES,
    claim_next_account_run_request,
    complete_account_run_request,
    get_account_run_request,
    insert_manual_run_audit,
    mark_account_run_request_starting,
    normalize_request_uuid,
    reconcile_linked_ig_run_terminal,
    reclaim_stale_account_run_requests,
)
from assignment_dispatch_resolver import resolve_account_assignment_runtime_context, sensitive_log_fields
from logs import log
import supabase_client


LOGIN_RUN_TYPES = frozenset({"login_provisioning", "login_email_code_resume"})


@dataclass
class DispatcherConfig:
    enabled: bool
    health_only: bool
    launch_enabled: bool
    worker_id: str
    poll_seconds: float
    lease_seconds: int
    heartbeat_seconds: float
    allowed_run_types: list[str]
    test_account_ids: set[str]
    subprocess_timeout_seconds: int
    require_assignment: bool
    enforce_assignment_window: bool


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(getattr(config, name, default))
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        try:
            return int(getattr(config, name, default))
        except (TypeError, ValueError):
            return int(default)
    try:
        return int(str(raw).strip())
    except ValueError:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        try:
            return float(getattr(config, name, default))
        except (TypeError, ValueError):
            return float(default)
    try:
        return float(str(raw).strip())
    except ValueError:
        return float(default)


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None:
        return str(getattr(config, name, default)).strip()
    value = str(raw).strip()
    return value if value else str(default)


def load_dispatcher_config() -> DispatcherConfig:
    host = socket.gethostname()
    default_worker_id = f"run-dispatcher:{host}"
    configured_worker_id = _env_str("RUN_CONTROL_DISPATCHER_WORKER_ID", default_worker_id)
    allowed_raw = _env_str(
        "RUN_CONTROL_DISPATCHER_ALLOWED_RUN_TYPES",
        "account_session,outreach_session,login_provisioning,login_email_code_resume",
    )
    allowed = [part.strip().lower() for part in allowed_raw.split(",") if part.strip()]
    test_ids_raw = _env_str("RUN_CONTROL_DISPATCHER_TEST_ACCOUNT_IDS", "")
    test_ids = {part.strip() for part in test_ids_raw.split(",") if part.strip()}
    return DispatcherConfig(
        enabled=_env_bool("RUN_CONTROL_DISPATCHER_ENABLED", False),
        health_only=_env_bool("RUN_CONTROL_DISPATCHER_HEALTH_ONLY", True),
        launch_enabled=_env_bool("RUN_CONTROL_DISPATCHER_LAUNCH_ENABLED", False),
        worker_id=configured_worker_id,
        poll_seconds=max(1.0, _env_float("RUN_CONTROL_DISPATCHER_POLL_SECONDS", 5.0)),
        lease_seconds=max(30, _env_int("RUN_CONTROL_DISPATCHER_LEASE_SECONDS", 120)),
        heartbeat_seconds=max(5.0, _env_float("RUN_CONTROL_DISPATCHER_HEARTBEAT_SECONDS", 20.0)),
        allowed_run_types=allowed,
        test_account_ids=test_ids,
        subprocess_timeout_seconds=max(60, _env_int("RUN_CONTROL_DISPATCHER_SUBPROCESS_TIMEOUT_SECONDS", 7200)),
        require_assignment=_env_bool("RUN_CONTROL_DISPATCHER_REQUIRE_ASSIGNMENT", False),
        enforce_assignment_window=_env_bool("RUN_CONTROL_DISPATCHER_ENFORCE_ASSIGNMENT_WINDOW", False),
    )


def dispatcher_is_healthy(cfg: DispatcherConfig | None = None) -> bool:
    """Return True when dispatcher is enabled and heartbeat is fresh."""
    cfg = cfg or load_dispatcher_config()
    if not cfg.enabled:
        return False
    try:
        rows = supabase_client._request_json(
            "GET",
            "worker_heartbeats",
            query={
                "select": "worker_id,status,last_seen_at",
                "worker_id": f"eq.{cfg.worker_id}",
                "limit": "1",
            },
        ) or []
    except Exception:
        return False
    if not rows:
        return False
    row = rows[0]
    status = str(row.get("status") or "").strip().lower()
    if status not in {"starting", "idle", "running"}:
        return False
    last_seen_raw = str(row.get("last_seen_at") or "").strip()
    if not last_seen_raw:
        return False
    try:
        last_seen = datetime.fromisoformat(last_seen_raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - last_seen.astimezone(timezone.utc)).total_seconds()
    return age_seconds <= max(cfg.heartbeat_seconds * 3, 60.0)


def _allow_existing_queue_on_startup() -> bool:
    return _env_bool("RUN_CONTROL_DISPATCHER_ALLOW_EXISTING_QUEUE", False)


def count_active_account_run_requests() -> int:
    """Read-only count of active queue rows for safe startup preflight."""
    try:
        rows = supabase_client._request_json(
            "GET",
            "account_run_requests",
            query={
                "select": "id",
                "status": f"in.({','.join(sorted(ACTIVE_REQUEST_STATUSES))})",
            },
        ) or []
    except Exception:
        return -1
    return len(rows)


def summarize_active_account_run_requests(limit: int = 20) -> dict[str, Any]:
    """Safe startup summary without secrets or device identifiers."""
    try:
        rows = supabase_client._request_json(
            "GET",
            "account_run_requests",
            query={
                "select": "id,account_id,status,requested_run_type,created_at",
                "status": f"in.({','.join(sorted(ACTIVE_REQUEST_STATUSES))})",
                "order": "created_at.asc",
                "limit": str(max(1, int(limit))),
            },
        ) or []
    except Exception as exc:
        return {
            "active_count": -1,
            "requests": [],
            "read_failed": True,
            "error": str(exc)[:200],
        }
    safe_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        safe_rows.append(
            {
                "request_id": normalize_request_uuid(row.get("id")),
                "account_id": normalize_request_uuid(row.get("account_id")),
                "status": str(row.get("status") or "").strip().lower() or None,
                "requested_run_type": str(row.get("requested_run_type") or "").strip().lower() or None,
                "created_at": str(row.get("created_at") or "").strip() or None,
            }
        )
    return {
        "active_count": len(safe_rows),
        "requests": safe_rows,
        "read_failed": False,
        "error": None,
    }


def evaluate_launch_mode_startup_preflight(cfg: DispatcherConfig) -> dict[str, Any]:
    """Block launch mode when an active queue exists unless explicitly overridden."""
    if cfg.health_only or not cfg.launch_enabled:
        return {
            "ok": True,
            "mode": "health_only",
            "active_count": 0,
            "allow_existing_queue": _allow_existing_queue_on_startup(),
        }

    summary = summarize_active_account_run_requests()
    active_count = int(summary.get("active_count") or 0)
    if summary.get("read_failed"):
        return {
            "ok": False,
            "reason": "active_queue_read_failed",
            "active_count": active_count,
            "allow_existing_queue": _allow_existing_queue_on_startup(),
            "requests": [],
        }

    if active_count > 0 and not _allow_existing_queue_on_startup():
        return {
            "ok": False,
            "reason": "active_queue_present",
            "active_count": active_count,
            "allow_existing_queue": False,
            "requests": list(summary.get("requests") or []),
        }

    return {
        "ok": True,
        "reason": "ready",
        "active_count": active_count,
        "allow_existing_queue": _allow_existing_queue_on_startup(),
        "requests": list(summary.get("requests") or []),
    }


def _heartbeat(cfg: DispatcherConfig, *, status: str = "idle", metadata: dict[str, Any] | None = None) -> None:
    with _force_runtime_heartbeats():
        runtime_heartbeat.heartbeat_worker(
            worker_id=cfg.worker_id,
            status=status,
            metadata={
                "component": "run_control_dispatcher",
                "health_only": cfg.health_only,
                "launch_enabled": cfg.launch_enabled,
                **(metadata or {}),
            },
            force=True,
        )


class _force_runtime_heartbeats:
    def __enter__(self) -> None:
        self._previous = bool(getattr(config, "RUNTIME_HEARTBEATS_ENABLED", False))
        config.RUNTIME_HEARTBEATS_ENABLED = True

    def __exit__(self, exc_type, exc, tb) -> None:
        config.RUNTIME_HEARTBEATS_ENABLED = self._previous


def _account_is_launch_allowed(account_id: str, cfg: DispatcherConfig) -> tuple[bool, str | None]:
    if cfg.test_account_ids and account_id not in cfg.test_account_ids:
        return False, "test_account_only"
    account = supabase_client.load_account(account_id=account_id)
    if not account:
        return False, "account_not_found"
    lifecycle = str(account.get("status") or account.get("lifecycle_status") or "active").strip().lower()
    if lifecycle in {"archived", "trashed", "canceled", "stopped", "deleted"}:
        return False, "account_not_active"
    return True, None


def _validate_assignment(account_id: str, run_type: str, cfg: DispatcherConfig) -> tuple[bool, str | None, dict[str, Any]]:
    ctx = resolve_account_assignment_runtime_context(
        account_id,
        run_type,
        require_assignment=cfg.require_assignment,
        enforce_window=cfg.enforce_assignment_window,
    )
    reason = str(ctx.get("reason") or "")
    if reason == "assignment_device_missing_adb_serial":
        return False, reason, ctx
    if cfg.require_assignment and not bool(ctx.get("assignment_found")):
        reason = str(ctx.get("reason") or "assignment_not_found")
        return False, reason, ctx
    return True, None, ctx


def _is_login_run_type(run_type: str) -> bool:
    return str(run_type or "").strip().lower() in LOGIN_RUN_TYPES


def _load_expected_username(account_id: str) -> str:
    account = supabase_client.load_account(account_id=account_id)
    if not account:
        return ""
    return str(account.get("username") or "").strip()


def _build_login_provisioner_command(
    account_id: str,
    run_type: str,
    request_id: str,
    *,
    device_serial: str | None = None,
    package_name: str | None = None,
    app_instance_id: str | None = None,
    metadata_safe: dict[str, Any] | None = None,
) -> list[str]:
    expected_username = _load_expected_username(account_id)
    cmd = [
        sys.executable,
        "-m",
        "instagram_login_provisioner_cli",
        "--account-id",
        account_id,
        "--expected-username",
        expected_username or "unknown",
        "--no-publish",
        "--json",
        "--run-id",
        request_id,
    ]
    serial = str(device_serial or "").strip()
    if serial:
        cmd.extend(["--device-serial", serial])
    package = str(package_name or "").strip()
    if package:
        cmd.extend(["--package-name", package])
    app_instance = str(app_instance_id or "").strip()
    if app_instance:
        cmd.extend(["--expected-app-instance-id", app_instance])
    meta = dict(metadata_safe or {})
    if str(run_type or "").strip().lower() == "login_email_code_resume":
        cmd.append("--resume-email-code-from-action")
        action_id = str(meta.get("action_id") or meta.get("verification_action_id") or "").strip()
        if action_id:
            cmd.extend(["--verification-action-id", action_id])
    return cmd


def _build_runner_command(
    account_id: str,
    run_type: str,
    request_id: str,
    *,
    device_serial: str | None = None,
    package_name: str | None = None,
    app_instance_id: str | None = None,
    metadata_safe: dict[str, Any] | None = None,
) -> list[str]:
    if _is_login_run_type(run_type):
        return _build_login_provisioner_command(
            account_id,
            run_type,
            request_id,
            device_serial=device_serial,
            package_name=package_name,
            app_instance_id=app_instance_id,
            metadata_safe=metadata_safe,
        )
    runner_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner.py")
    cmd = [
        sys.executable,
        runner_path,
        "--account-id",
        account_id,
        "--run-type",
        run_type,
        "--run-request-id",
        request_id,
    ]
    serial = str(device_serial or "").strip()
    if serial:
        cmd.extend(["--device-serial", serial])
    return cmd


def _safe_complete_account_run_request(
    request_id: str | None,
    worker_id: str,
    status: str,
    *,
    error_code: str | None = None,
    error_message_safe: str | None = None,
) -> dict[str, Any] | None:
    normalized_request_id = normalize_request_uuid(request_id)
    if not normalized_request_id:
        log(
            "warning",
            "run_control_skip_transition_invalid_request_id",
            transition="complete",
            terminal_status=status,
            worker_id=worker_id,
        )
        return None
    return complete_account_run_request(
        normalized_request_id,
        worker_id,
        status,
        error_code=error_code,
        error_message_safe=error_message_safe,
    )


def _audit(
    *,
    account_id: str,
    action_type: str,
    status: str,
    message: str,
    run_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    try:
        insert_manual_run_audit(
            account_id=account_id,
            action_type=action_type,
            status=status,
            message=message,
            run_id=run_id,
            payload=payload or {},
        )
    except Exception as exc:
        log("warning", "manual_run_audit_failed", action_type=action_type, error=str(exc)[:200])


def _reconcile_linked_run(
    *,
    account_id: str,
    run_id: str | None,
    terminal_status: str,
    request_id: str,
    exit_code: int | None = None,
) -> dict[str, Any]:
    result = reconcile_linked_ig_run_terminal(
        run_id=run_id,
        terminal_status=terminal_status,
        account_id=account_id,
    )
    if result.get("reconciled"):
        payload: dict[str, Any] = {
            "request_id": request_id,
            "previous_status": result.get("previous_status"),
            "terminal_status": result.get("terminal_status"),
        }
        if exit_code is not None:
            payload["exit_code"] = exit_code
        _audit(
            account_id=account_id,
            action_type="orphan_running_run_reconciled",
            status="success",
            message="Linked ig_runs row reconciled after subprocess exit.",
            run_id=str(result.get("run_id") or run_id or "") or None,
            payload=payload,
        )
        log(
            "info",
            "orphan_running_run_reconciled",
            account_id=account_id,
            request_id=request_id,
            run_id=result.get("run_id"),
            previous_status=result.get("previous_status"),
            terminal_status=result.get("terminal_status"),
        )
    return result


def _finalize_manual_run_after_subprocess(
    cfg: DispatcherConfig,
    *,
    request_id: str,
    account_id: str,
    exit_code: int,
    timed_out: bool = False,
    request_snapshot: dict[str, Any] | None = None,
) -> None:
    latest = get_account_run_request(request_id) or request_snapshot or {}
    run_id = str(latest.get("run_id") or "").strip() or None
    canceled = bool(latest.get("cancel_requested_at")) or str(latest.get("status") or "").strip().lower() == "canceled"

    if timed_out:
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "failed",
            error_code="subprocess_timeout",
            error_message_safe="Worker subprocess exceeded dispatcher timeout.",
        )
        _reconcile_linked_run(
            account_id=account_id,
            run_id=run_id,
            terminal_status="failed",
            request_id=request_id,
            exit_code=exit_code,
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_failed",
            status="failed",
            message="Worker subprocess exceeded dispatcher timeout.",
            run_id=run_id,
            payload={"request_id": request_id},
        )
        return

    if exit_code == 0:
        _safe_complete_account_run_request(request_id, cfg.worker_id, "completed")
        _reconcile_linked_run(
            account_id=account_id,
            run_id=run_id,
            terminal_status="completed",
            request_id=request_id,
            exit_code=exit_code,
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_completed",
            status="success",
            message="Manual run completed.",
            run_id=run_id,
            payload={"request_id": request_id, "exit_code": exit_code},
        )
        return

    if canceled:
        _safe_complete_account_run_request(request_id, cfg.worker_id, "canceled")
        _reconcile_linked_run(
            account_id=account_id,
            run_id=run_id,
            terminal_status="canceled",
            request_id=request_id,
            exit_code=exit_code,
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_canceled",
            status="success",
            message="Manual run canceled.",
            run_id=run_id,
            payload={"request_id": request_id, "exit_code": exit_code},
        )
        return

    _safe_complete_account_run_request(
        request_id,
        cfg.worker_id,
        "failed",
        error_code="worker_exit_nonzero",
        error_message_safe=f"Worker subprocess exited with code {exit_code}.",
    )
    _reconcile_linked_run(
        account_id=account_id,
        run_id=run_id,
        terminal_status="failed",
        request_id=request_id,
        exit_code=exit_code,
    )
    _audit(
        account_id=account_id,
        action_type="manual_run_failed",
        status="failed",
        message=f"Manual run failed with exit code {exit_code}.",
        run_id=run_id,
        payload={"request_id": request_id, "exit_code": exit_code},
    )


def _terminate_subprocess(proc: subprocess.Popen[Any]) -> int:
    proc.send_signal(signal.SIGTERM)
    try:
        return int(proc.wait(timeout=30))
    except subprocess.TimeoutExpired:
        proc.kill()
        return int(proc.wait(timeout=10))


def _wait_for_subprocess(
    cfg: DispatcherConfig,
    proc: subprocess.Popen[Any],
    *,
    request_id: str,
    account_id: str,
) -> tuple[int, bool]:
    deadline = time.monotonic() + cfg.subprocess_timeout_seconds
    while True:
        exit_code = proc.poll()
        if exit_code is not None:
            return int(exit_code), False

        latest = get_account_run_request(request_id)
        if latest and (latest.get("status") == "canceled" or latest.get("cancel_requested_at")):
            log(
                "info",
                "manual_run_subprocess_cancel_requested",
                account_id=account_id,
                request_id=request_id,
                worker_id=cfg.worker_id,
            )
            return _terminate_subprocess(proc), False

        if time.monotonic() >= deadline:
            return _terminate_subprocess(proc), True

        time.sleep(1.0)


def _handle_claimed_request(cfg: DispatcherConfig, request: dict[str, Any]) -> None:
    request_id = normalize_request_uuid(request.get("id"))
    account_id = normalize_request_uuid(request.get("account_id"))
    run_type = str(request.get("requested_run_type") or "").strip().lower()
    if not request_id or not account_id or not run_type:
        log(
            "warning",
            "run_control_skip_claimed_request_invalid_row",
            has_request_id=bool(request_id),
            has_account_id=bool(account_id),
            has_run_type=bool(run_type),
            worker_id=cfg.worker_id,
        )
        return

    allowed, block_reason = _account_is_launch_allowed(account_id, cfg)
    if not allowed:
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "blocked",
            error_code=block_reason or "blocked",
            error_message_safe=f"Run request blocked: {block_reason or 'blocked'}.",
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_blocked",
            status="blocked",
            message=f"Run request blocked: {block_reason or 'blocked'}.",
            payload={"request_id": request_id, "reason": block_reason},
        )
        return

    starting = mark_account_run_request_starting(request_id, cfg.worker_id)
    if not starting:
        return

    ok_assignment, assignment_reason, dispatch_ctx = _validate_assignment(account_id, run_type, cfg)
    safe_dispatch = sensitive_log_fields(dispatch_ctx)
    if not ok_assignment:
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "blocked",
            error_code=assignment_reason or "assignment_blocked",
            error_message_safe=f"Assignment blocked: {assignment_reason or 'assignment_blocked'}.",
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_blocked",
            status="blocked",
            message=f"Assignment blocked: {assignment_reason or 'assignment_blocked'}.",
            payload={"request_id": request_id, "assignment": safe_dispatch},
        )
        return

    adb_serial = str(dispatch_ctx.get("adb_serial") or "").strip()
    log(
        "info",
        "run_dispatcher_device_serial_resolved",
        **safe_dispatch,
        adb_serial_present=bool(adb_serial),
    )

    _audit(
        account_id=account_id,
        action_type="manual_run_claimed",
        status="success",
        message="Manual run request claimed by dispatcher.",
        payload={
            "request_id": request_id,
            "worker_id": cfg.worker_id,
            "assignment": safe_dispatch,
            "adb_serial_present": bool(adb_serial),
        },
    )

    request_metadata = dict(request.get("metadata_safe") or {})
    if _is_login_run_type(run_type) and not adb_serial:
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "blocked",
            error_code="login_device_serial_required",
            error_message_safe="Login run blocked: assigned device serial is required.",
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_blocked",
            status="blocked",
            message="Login run blocked: assigned device serial is required.",
            payload={"request_id": request_id, "run_type": run_type},
        )
        return

    if run_type == "login_email_code_resume":
        action_id = str(
            request_metadata.get("action_id") or request_metadata.get("verification_action_id") or ""
        ).strip()
        if action_id:
            try:
                from login_challenge_runtime import mark_verification_action_resume_running

                mark_verification_action_resume_running(
                    action_id=action_id,
                    account_id=account_id,
                    run_request_id=request_id,
                )
            except Exception as exc:
                log(
                    "warning",
                    "login_resume_action_state_update_failed",
                    account_id=account_id,
                    request_id=request_id,
                    error=str(exc)[:200],
                )

    cmd = _build_runner_command(
        account_id,
        run_type,
        request_id,
        device_serial=adb_serial,
        package_name=dispatch_ctx.get("package_name") or dispatch_ctx.get("app_package") or dispatch_ctx.get("package"),
        app_instance_id=dispatch_ctx.get("app_instance_id"),
        metadata_safe=request_metadata,
    )
    log(
        "info",
        "manual_run_subprocess_starting",
        account_id=account_id,
        request_id=request_id,
        run_type=run_type,
        worker_id=cfg.worker_id,
        device_id=dispatch_ctx.get("device_id"),
        app_instance_id=dispatch_ctx.get("app_instance_id"),
        adb_serial_present=bool(adb_serial),
    )
    _heartbeat(cfg, status="running", metadata={"active_request_id": request_id, "account_id": account_id})

    proc = subprocess.Popen(
        cmd,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env=runner_subprocess_env(),
    )
    exit_code, timed_out = _wait_for_subprocess(
        cfg,
        proc,
        request_id=request_id,
        account_id=account_id,
    )

    _finalize_manual_run_after_subprocess(
        cfg,
        request_id=request_id,
        account_id=account_id,
        exit_code=int(exit_code),
        timed_out=timed_out,
        request_snapshot=request,
    )


def run_once(cfg: DispatcherConfig | None = None) -> dict[str, Any]:
    cfg = cfg or load_dispatcher_config()
    if not cfg.enabled:
        return {"ok": False, "reason": "disabled"}

    _heartbeat(cfg, status="idle")
    reclaimed = reclaim_stale_account_run_requests(cfg.worker_id)

    if cfg.health_only or not cfg.launch_enabled:
        return {"ok": True, "mode": "health_only", "reclaimed": reclaimed}

    request = claim_next_account_run_request(
        cfg.worker_id,
        lease_seconds=cfg.lease_seconds,
        allowed_run_types=cfg.allowed_run_types,
    )
    if not request:
        return {"ok": True, "mode": "idle", "reclaimed": reclaimed}

    request_id = normalize_request_uuid(request.get("id"))
    if not request_id:
        log(
            "warning",
            "run_control_skip_invalid_claim_row",
            worker_id=cfg.worker_id,
        )
        return {"ok": True, "mode": "idle", "reason": "invalid_claim_row", "reclaimed": reclaimed}

    account_id = normalize_request_uuid(request.get("account_id"))
    _handle_claimed_request(cfg, request)
    return {
        "ok": True,
        "mode": "processed",
        "request_id": request_id,
        "account_id": account_id,
        "reclaimed": reclaimed,
    }


def run_forever(cfg: DispatcherConfig | None = None) -> int:
    cfg = cfg or load_dispatcher_config()
    if not cfg.enabled:
        log("error", "run_control_dispatcher_disabled")
        return 2

    preflight = evaluate_launch_mode_startup_preflight(cfg)
    if not preflight.get("ok"):
        log(
            "error",
            "run_control_dispatcher_launch_preflight_blocked",
            worker_id=cfg.worker_id,
            reason=str(preflight.get("reason") or "blocked"),
            active_count=int(preflight.get("active_count") or 0),
            allow_existing_queue=bool(preflight.get("allow_existing_queue")),
            active_requests=list(preflight.get("requests") or [])[:5],
        )
        return 3

    log(
        "info",
        "run_control_dispatcher_started",
        worker_id=cfg.worker_id,
        health_only=cfg.health_only,
        launch_enabled=cfg.launch_enabled,
        allowed_run_types=cfg.allowed_run_types,
        startup_active_count=int(preflight.get("active_count") or 0),
        startup_allow_existing_queue=bool(preflight.get("allow_existing_queue")),
    )
    _heartbeat(cfg, status="starting")

    stop = False

    def _handle_signal(signum, frame) -> None:
        nonlocal stop
        stop = True
        log("info", "run_control_dispatcher_stop_signal", signal=signum)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    last_heartbeat = 0.0
    last_loop_error_key = ""
    last_loop_error_logged_at = 0.0
    while not stop:
        try:
            run_once(cfg)
            last_loop_error_key = ""
        except Exception as exc:
            err = str(exc)[:500]
            now = time.monotonic()
            err_key = err[:200]
            if err_key != last_loop_error_key or (now - last_loop_error_logged_at) >= 60.0:
                log("error", "run_control_dispatcher_loop_failed", error=err)
                last_loop_error_key = err_key
                last_loop_error_logged_at = now
        now = time.monotonic()
        if now - last_heartbeat >= cfg.heartbeat_seconds:
            _heartbeat(cfg, status="idle")
            last_heartbeat = now
        time.sleep(cfg.poll_seconds)

    _heartbeat(cfg, status="stopping")
    log("info", "run_control_dispatcher_stopped", worker_id=cfg.worker_id)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if args and args[0] in {"--preflight", "preflight"}:
        cfg = load_dispatcher_config()
        result = evaluate_launch_mode_startup_preflight(cfg)
        print(result)
        return 0 if result.get("ok") else 3
    if args and args[0] in {"--once", "once"}:
        result = run_once()
        print(result)
        return 0 if result.get("ok") else 1
    return run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
