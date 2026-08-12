"""Supervised Run Control dispatcher for account_run_requests.

RunControl-2: health-only mode (heartbeat, no claim/launch).
RunControl-4: claim + controlled subprocess launch when explicitly enabled.
"""

from __future__ import annotations

import contextlib
import os
import json
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from concurrent.futures import Future, ThreadPoolExecutor
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
    finalize_auto_login_failure,
    insert_manual_run_audit,
    link_account_run_request_run,
    mark_account_run_request_starting,
    normalize_request_uuid,
    reconcile_linked_ig_run_terminal,
    reclaim_stale_account_run_requests,
)
from auto_login_failure_contract import normalize_auto_login_failure
from account_session_phase_notification import (
    build_account_session_phase_notification_summary,
)
from assignment_dispatch_resolver import resolve_account_assignment_runtime_context
from account_commercial_policy import evaluate_queued_run_commercial_policy, sensitive_log_fields
from auto_restart_dispatcher_tick import (
    consume_auto_restart_startup_tick_skip_once,
    run_auto_restart_dispatcher_tick,
    should_run_auto_restart_tick,
)
from auto_restart_device_lock import acquire_device_lock, release_device_lock, release_device_lock_for_request, renew_device_lock, transfer_device_lock
from auto_restart_runtime import (
    is_auto_restart_request,
    is_hard_stop_reason,
    log_auto_restart_claim_validation,
    runner_env_for_resume_policy,
    validate_auto_restart_request_at_claim,
)
from logs import log
import incident_notifications
import runtime_incidents
import supabase_client
import deferred_projection_outbox
import follow_persistence_receipt_replay
from follow60_ordering_v2_behavioral_canary_v1 import (
    behavioral_runtime_scope_for_account,
)
from worker_runtime_identity import (
    WorkerRuntimeIdentity,
    WorkerRuntimeIdentityError,
    bind_worker_runtime_identity,
    export_worker_runtime_identity,
    resolve_worker_runtime_identity,
    worker_runtime_identity_env,
)
from account_protection_lists import (
    SNAPSHOT_ENV as ACCOUNT_PROTECTION_SNAPSHOT_ENV,
    load_snapshot_for_run,
    serialize_snapshot,
)
from runtime_incident_matrix import (
    build_run_failure_incident_payload,
    classify_recoverable_python_retry,
    classify_terminal_run_failure,
)

LOGIN_RUN_TYPES = frozenset({"login_provisioning", "login_email_code_resume"})
ORPHAN_RECOVERY_RUN_TYPE = "login_orphan_challenge_recovery"
DEVICE_BOUND_RUN_TYPES = frozenset({
    "account_session",
    "outreach_session",
    "scheduled_session_preflight",
    "login_provisioning",
    "login_email_code_resume",
    "login_orphan_challenge_recovery",
})
PREFLIGHT_RUN_TYPE = "scheduled_session_preflight"
_last_integration_noop_proof: dict[str, Any] | None = None
_CERTIFIED_RUNTIME_IDENTITY: WorkerRuntimeIdentity | None = None


def _runner_runtime_identity_env(request_id: str) -> dict[str, str]:
    """Freeze the consumer-certified identity into the runner subprocess env."""

    identity = _CERTIFIED_RUNTIME_IDENTITY
    if identity is None:
        raise WorkerRuntimeIdentityError(
            "identity_not_propagated",
            stage="consumer_runner_handoff",
            diagnostics={"consumer_identity_certified": False},
        )
    bound = bind_worker_runtime_identity(
        identity,
        request_id=request_id,
        consumer_pid=os.getpid(),
    )
    return worker_runtime_identity_env(bound)


def _follow60_control_applies(
    *, account_id: str, run_id: str | None, request_id: str
) -> bool:
    """Resolve special terminal handling from the canonical bound control."""
    if not account_id or not run_id or not request_id:
        return False
    try:
        from follow_60s_canary_binding_v2 import validate_consumer_binding

        control = supabase_client.get_follow_60s_canary_control_v1(account_id)
        verdict = validate_consumer_binding(
            control,
            account_id=account_id,
            active_worker_sha=str(os.environ.get("WORKER_GIT_SHA") or ""),
            run_id=str(run_id),
            request_id=str(request_id),
        )
        if not verdict.valid and control:
            log(
                "warning", "follow60_consumer_binding_rejected",
                account_id=account_id, run_id=run_id, request_id=request_id,
                reason=verdict.reason, fallback="golden_current",
            )
        return bool(verdict.valid)
    except Exception as exc:
        log(
            "warning", "follow60_consumer_binding_read_failed",
            account_id=account_id, run_id=run_id, request_id=request_id,
            error_type=type(exc).__name__, fallback="golden_current",
        )
        return False


def _integration_mode_enabled() -> bool:
    return _env_bool("RUN_CONTROL_INTEGRATION_MODE", False)


def _integration_noop_runner_enabled() -> bool:
    return _integration_mode_enabled() and _env_bool("RUN_CONTROL_INTEGRATION_NOOP_RUNNER", False)


def _is_loopback_supabase_url(url: str) -> bool:
    lowered = str(url or "").strip().lower()
    return any(host in lowered for host in ("127.0.0.1", "localhost", "[::1]", "::1"))


def _assert_integration_safety() -> None:
    if not _integration_mode_enabled():
        return
    url = str(os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip()
    if not url or not _is_loopback_supabase_url(url):
        raise RuntimeError("integration mode requires loopback SUPABASE_URL")
    if _integration_noop_runner_enabled():
        for forbidden in ("ANDROID_SERIAL", "ADB_SERIAL", "U2_DEVICE"):
            if str(os.environ.get(forbidden) or "").strip():
                raise RuntimeError(f"integration noop forbids env {forbidden}")


def _is_device_bound_run_type(run_type: str) -> bool:
    return str(run_type or "").strip().lower() in DEVICE_BOUND_RUN_TYPES


def _pending_manual_lock_worker_id(request_id: str) -> str:
    return f"pending-request:{request_id}"


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
    max_consecutive_loop_errors: int = 10
    max_concurrent_subprocesses: int = 4
    # Stable physical-host identity shared with the device heartbeat service.
    # It must not depend on DHCP/mDNS's mutable socket hostname.
    host_machine: str = ""


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
        "account_session,outreach_session,login_provisioning,login_email_code_resume,login_orphan_challenge_recovery,scheduled_session_preflight",
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
        max_consecutive_loop_errors=max(1, _env_int("RUN_CONTROL_DISPATCHER_MAX_CONSECUTIVE_LOOP_ERRORS", 5)),
        max_concurrent_subprocesses=max(
            1,
            _env_int("RUN_CONTROL_DISPATCHER_MAX_CONCURRENT_SUBPROCESSES", 4),
        ),
        host_machine=_env_str("RUN_CONTROL_DISPATCHER_HOST_MACHINE", host),
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


def _classify_queue_read_failure(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, supabase_client.SupabaseRestError):
        return str(exc.reason or "active_queue_read_failed"), str(exc)[:200]
    err = str(exc)
    lowered = err.lower()
    if "timed out" in lowered:
        return "supabase_rest_timeout", err[:200]
    if "401" in err:
        return "supabase_auth_401", err[:200]
    if "403" in err:
        return "supabase_auth_403", err[:200]
    if "dns" in lowered or "name or service not known" in lowered:
        return "supabase_dns_failed", err[:200]
    if "ssl" in lowered or "tls" in lowered:
        return "supabase_tls_failed", err[:200]
    return "active_queue_read_failed", err[:200]


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
        reason, err = _classify_queue_read_failure(exc)
        return {
            "active_count": -1,
            "requests": [],
            "read_failed": True,
            "error": err,
            "supabase_reason": reason,
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


def reconcile_requests_with_terminal_runs(
    cfg: DispatcherConfig,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    """Idempotently close active queue rows whose linked run is terminal.

    A terminal ``ig_runs`` row is only published after Runner cleanup.  It is
    therefore the durable recovery marker when the dispatch Future is lost or
    the dispatcher restarts between child exit and request completion.
    """
    try:
        requests = supabase_client._request_json(
            "GET",
            "account_run_requests",
            query={
                "select": "id,account_id,status,run_id,requested_run_type",
                "status": f"in.({','.join(sorted(ACTIVE_REQUEST_STATUSES))})",
                "run_id": "not.is.null",
                "order": "created_at.asc",
                "limit": str(max(1, int(limit))),
            },
        ) or []
        run_ids = [
            str(row.get("run_id") or "").strip()
            for row in requests
            if isinstance(row, dict) and str(row.get("run_id") or "").strip()
        ]
        if not run_ids:
            return {"ok": True, "observed": 0, "terminalized": 0}
        runs = supabase_client._request_json(
            "GET",
            "ig_runs",
            query={
                "select": "id,status,finished_at,updated_at",
                "id": f"in.({','.join(run_ids)})",
            },
        ) or []
    except Exception as exc:
        log(
            "warning",
            "terminal_run_request_reconciliation_read_failed",
            worker_id=cfg.worker_id,
            error=str(exc)[:200],
        )
        return {"ok": False, "observed": 0, "terminalized": 0, "error": str(exc)[:200]}

    terminal_runs = {
        str(row.get("id") or "").strip(): dict(row)
        for row in runs
        if isinstance(row, dict)
        and str(row.get("status") or "").strip().lower()
        in {"completed", "failed", "stopped", "canceled", "blocked", "aborted"}
    }
    terminalized = 0
    for request in requests:
        if not isinstance(request, dict):
            continue
        request_id = normalize_request_uuid(request.get("id"))
        run_id = str(request.get("run_id") or "").strip()
        run = terminal_runs.get(run_id)
        if not request_id or not run:
            continue
        run_status = str(run.get("status") or "").strip().lower()
        request_status = {
            "completed": "completed",
            "stopped": "canceled",
            "canceled": "canceled",
            "blocked": "blocked",
        }.get(run_status, "failed")
        try:
            result = _safe_complete_account_run_request(
                request_id,
                cfg.worker_id,
                request_status,
                error_code=(
                    None
                    if request_status in {"completed", "canceled"}
                    else f"linked_run_{run_status}"
                ),
                error_message_safe=(
                    None
                    if request_status in {"completed", "canceled"}
                    else "Linked worker session reached a terminal failure state."
                ),
            )
        except Exception as exc:
            log(
                "warning",
                "terminal_run_request_reconciliation_write_failed",
                worker_id=cfg.worker_id,
                request_id=request_id,
                run_id=run_id,
                run_status=run_status,
                error=str(exc)[:200],
            )
            continue
        if not result:
            log(
                "warning",
                "terminal_run_request_reconciliation_not_persisted",
                worker_id=cfg.worker_id,
                request_id=request_id,
                run_id=run_id,
                run_status=run_status,
                request_status=request_status,
            )
            continue
        terminalized += 1
        log(
            "info",
            "terminal_run_request_reconciled",
            worker_id=cfg.worker_id,
            request_id=request_id,
            run_id=run_id,
            run_status=run_status,
            request_status=request_status,
            persisted=True,
        )
    return {"ok": True, "observed": len(requests), "terminalized": terminalized}


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
            "reason": str(summary.get("supabase_reason") or "active_queue_read_failed"),
            "active_count": active_count,
            "allow_existing_queue": _allow_existing_queue_on_startup(),
            "requests": [],
            "error": summary.get("error"),
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
            host_machine=cfg.host_machine or socket.gethostname(),
            metadata={
                "component": "run_control_dispatcher",
                "dispatcher_worker_id": cfg.worker_id,
                "canonical_host_machine": cfg.host_machine or socket.gethostname(),
                "observed_socket_hostname": socket.gethostname(),
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
        if _integration_noop_runner_enabled():
            ctx = dict(ctx)
            ctx["adb_serial"] = "integration-noop"
            return True, None, ctx
        return False, reason, ctx
    if cfg.require_assignment and not bool(ctx.get("assignment_found")):
        reason = str(ctx.get("reason") or "assignment_not_found")
        return False, reason, ctx
    return True, None, ctx


def _is_login_run_type(run_type: str) -> bool:
    return str(run_type or "").strip().lower() in LOGIN_RUN_TYPES


AUTO_LOGIN_APP_INSTANCE_BINDING_VERSION = "auto_login_app_instance_v1"


def _validate_login_request_binding(
    metadata_safe: dict[str, Any],
    dispatch_ctx: dict[str, Any],
) -> tuple[bool, str | None, dict[str, Any]]:
    """Verify the immutable server binding against fresh assignment context."""
    metadata = dict(metadata_safe or {})
    required_strings = (
        "assignment_id",
        "device_id",
        "app_instance_id",
        "package_name",
    )
    binding: dict[str, Any] = {
        "binding_version": str(metadata.get("binding_version") or "").strip(),
        **{key: str(metadata.get(key) or "").strip() for key in required_strings},
        "clone_index": metadata.get("clone_index"),
    }
    try:
        binding["clone_index"] = int(binding["clone_index"])
    except (TypeError, ValueError):
        return False, "auto_login_app_instance_binding_missing", binding

    if (
        binding["binding_version"] != AUTO_LOGIN_APP_INSTANCE_BINDING_VERSION
        or any(not binding[key] for key in required_strings)
        or not re.fullmatch(r"[A-Za-z0-9_.]+", binding["package_name"])
        or binding["clone_index"] < 0
    ):
        return False, "auto_login_app_instance_binding_missing", binding

    resolved_package = str(
        dispatch_ctx.get("package_name")
        or dispatch_ctx.get("app_package")
        or dispatch_ctx.get("package")
        or ""
    ).strip()
    resolved_index = dispatch_ctx.get("app_instance_index")
    if resolved_index is None:
        resolved_index = dispatch_ctx.get("clone_index")
    try:
        resolved_index = int(resolved_index)
    except (TypeError, ValueError):
        resolved_index = None

    comparisons = {
        "assignment_id": str(dispatch_ctx.get("assignment_id") or "").strip(),
        "device_id": str(dispatch_ctx.get("device_id") or "").strip(),
        "app_instance_id": str(dispatch_ctx.get("app_instance_id") or "").strip(),
        "package_name": resolved_package,
        "clone_index": resolved_index,
    }
    if any(binding[key] != comparisons[key] for key in comparisons):
        return False, "assigned_instagram_app_instance_mismatch", binding
    return True, None, binding


def _is_orphan_recovery_run_type(run_type: str) -> bool:
    return str(run_type or "").strip().lower() == ORPHAN_RECOVERY_RUN_TYPE


def _is_scheduled_session_preflight_run_type(run_type: str) -> bool:
    return str(run_type or "").strip().lower() == PREFLIGHT_RUN_TYPE


def _build_scheduled_session_preflight_command(
    account_id: str,
    request_id: str,
    *,
    device_serial: str | None = None,
    package_name: str | None = None,
    expected_username: str | None = None,
    metadata_safe: dict[str, Any] | None = None,
) -> list[str]:
    meta = dict(metadata_safe or {})
    username = str(expected_username or meta.get("expected_username") or "").strip()
    if not username:
        username = _load_expected_username(account_id)
    runner_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scheduled_session_preflight_runner.py")
    return [
        sys.executable,
        runner_path,
        "--account-id",
        account_id,
        "--request-id",
        request_id,
        "--device-serial",
        str(device_serial or "").strip(),
        "--package-name",
        str(package_name or "").strip(),
        "--expected-username",
        username,
        "--preflight-id",
        str(meta.get("preflight_id") or "").strip(),
        "--metadata-json",
        json.dumps(meta, separators=(",", ":"), sort_keys=True),
    ]


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
    run_request_id: str | None = None,
    device_serial: str | None = None,
    package_name: str | None = None,
    app_instance_id: str | None = None,
    metadata_safe: dict[str, Any] | None = None,
) -> list[str]:
    package = str(package_name or "").strip()
    if not package:
        raise ValueError("auto_login_package_binding_required")
    app_instance = str(app_instance_id or "").strip()
    if not app_instance:
        raise ValueError("auto_login_app_instance_binding_required")
    expected_username = _load_expected_username(account_id)
    normalized_run_type = str(run_type or "").strip().lower()
    # Every login path enters the same authoritative engine.  The historical
    # 07ee tree is retained only as forensic source material; routing a live
    # request through it would silently drop modern recovery and challenge
    # contracts added to the canonical implementation.
    cli_module = "instagram_login_provisioner_cli"
    cmd = [
        sys.executable,
        "-m",
        cli_module,
        "--account-id",
        account_id,
        "--expected-username",
        expected_username or "unknown",
        "--publish",
        "--json",
        "--run-id",
        request_id,
    ]
    serial = str(device_serial or "").strip()
    if serial:
        cmd.extend(["--device-serial", serial])
    cmd.extend(["--package-name", package])
    cmd.extend(["--expected-app-instance-id", app_instance])
    meta = dict(metadata_safe or {})
    if normalized_run_type == "login_email_code_resume":
        cmd.append("--resume-email-code-from-action")
        action_id = str(meta.get("action_id") or meta.get("verification_action_id") or "").strip()
        if action_id:
            cmd.extend(["--verification-action-id", action_id])
    return cmd


def _build_orphan_recovery_command(
    account_id: str,
    request_id: str,
    *,
    device_serial: str | None = None,
    package_name: str | None = None,
    app_instance_id: str | None = None,
    assignment_id: str | None = None,
    credentials_version: int | None = None,
    assignment_updated_at: str | None = None,
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "login_orphan_challenge_recovery_cli",
        "--account-id",
        account_id,
        "--run-id",
        request_id,
        "--json",
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
    assignment = str(assignment_id or "").strip()
    if assignment:
        cmd.extend(["--assignment-id", assignment])
    if credentials_version is not None:
        cmd.extend(["--credentials-version", str(int(credentials_version))])
    updated_at = str(assignment_updated_at or "").strip()
    if updated_at:
        cmd.extend(["--assignment-updated-at", updated_at])
    return cmd


def _build_runner_command(
    account_id: str,
    run_type: str,
    request_id: str,
    *,
    run_request_id: str | None = None,
    device_serial: str | None = None,
    package_name: str | None = None,
    app_instance_id: str | None = None,
    metadata_safe: dict[str, Any] | None = None,
) -> list[str]:
    if _integration_noop_runner_enabled():
        if _is_login_run_type(run_type) or _is_orphan_recovery_run_type(run_type):
            raise RuntimeError("integration noop runner cannot execute login flows")
        noop_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto_restart_integration_noop_runner.py")
        return [
            sys.executable,
            noop_path,
            "--request-id",
            request_id,
            "--account-id",
            account_id,
        ]
    if _is_login_run_type(run_type):
        return _build_login_provisioner_command(
            account_id,
            run_type,
            request_id,
            run_request_id=run_request_id,
            device_serial=device_serial,
            package_name=package_name,
            app_instance_id=app_instance_id,
            metadata_safe=metadata_safe,
        )
    if _is_scheduled_session_preflight_run_type(run_type):
        return _build_scheduled_session_preflight_command(
            account_id,
            request_id,
            device_serial=device_serial,
            package_name=package_name,
            metadata_safe=metadata_safe,
        )
    if _is_orphan_recovery_run_type(run_type):
        meta = dict(metadata_safe or {})
        credentials_version = meta.get("credentials_version")
        return _build_orphan_recovery_command(
            account_id,
            request_id,
            device_serial=device_serial,
            package_name=package_name,
            app_instance_id=app_instance_id,
            assignment_id=str(meta.get("assignment_id") or ""),
            credentials_version=int(credentials_version) if credentials_version is not None else None,
            assignment_updated_at=str(meta.get("assignment_updated_at") or ""),
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
    package = str(package_name or "").strip()
    if package:
        cmd.extend(["--package-name", package])
    app_instance = str(app_instance_id or "").strip()
    if app_instance:
        cmd.extend(["--expected-app-instance-id", app_instance])
    return cmd


def _login_provisioner_env() -> dict[str, str]:
    env = runner_subprocess_env()
    env["LOGIN_PROVISIONER_PUBLISH_ENABLED"] = "true"
    return env


def _account_session_deadline_env(
    metadata_safe: dict[str, Any] | None,
    dispatch_ctx: dict[str, Any] | None,
) -> dict[str, str]:
    """Resolve the scheduler window once and pass it unchanged to the runner."""
    from session_transition_buffer import resolve_business_action_deadline

    metadata = dict(metadata_safe or {})
    dispatch = dict(dispatch_ctx or {})
    deadline = resolve_business_action_deadline(metadata, dispatch)
    starts_at = str(
        metadata.get("scheduled_session_start")
        or metadata.get("scheduled_session_at")
        or dispatch.get("starts_at")
        or ""
    ).strip()
    ends_at = str(
        metadata.get("scheduled_session_end")
        or metadata.get("scheduled_session_ends_at")
        or dispatch.get("ends_at")
        or ""
    ).strip()
    out: dict[str, str] = {}
    if deadline:
        out["BUSINESS_ACTION_DEADLINE"] = deadline
    if starts_at:
        out["SCHEDULED_SESSION_START"] = starts_at
    if ends_at:
        out["SCHEDULED_SESSION_END"] = ends_at
    return out


def _create_and_link_login_run(account_id: str, request_id: str, worker_id: str) -> str | None:
    try:
        run = supabase_client.create_run(account_id=account_id)
        run_id = str(run.get("id") or "").strip()
        if not run_id:
            return None
        linked = link_account_run_request_run(request_id, worker_id, run_id)
        if not linked:
            return None
        _audit(
            account_id=account_id,
            action_type="manual_run_started",
            status="success",
            message="Manual login run linked to ig_runs.",
            run_id=run_id,
            payload={"request_id": request_id, "run_type": "login_provisioning"},
        )
        return run_id
    except Exception as exc:
        log(
            "warning",
            "login_run_link_create_failed",
            account_id=account_id,
            request_id=request_id,
            error=str(exc)[:200],
        )
        return None


def _safe_login_provisioner_summary_for_audit(run_id: str | None) -> dict[str, Any]:
    safe_run_id = str(run_id or "").strip()
    if not safe_run_id:
        return {}
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "instagram_login_provisioner.jsonl")
    try:
        with open(log_path, "r", encoding="utf-8") as handle:
            matches = [
                json.loads(line)
                for line in handle
                if line.strip() and f'"run_id":"{safe_run_id}"' in line
            ]
    except Exception as exc:
        log("warning", "login_provisioner_summary_read_failed", run_id=safe_run_id, error=str(exc)[:200])
        return {}
    if not matches:
        return {}
    summary = dict(matches[-1])
    allowed_keys = {
        "run_id",
        "ok",
        "completed",
        "final_outcome",
        "reason",
        "failure_reason",
        "status_candidate",
        "published",
        "publish_enabled",
        "publish_result",
        "publish_reason",
        "publish_error_code",
        "app_start_attempted",
        "app_start_ok",
        "package_name",
        "expected_package_name",
        "actual_foreground_package",
        "package_guard_mismatch",
        "package_guard_checked",
        "transient_foreground_recovery_attempted",
        "transient_foreground_recovery_succeeded",
        "transient_foreground_packages_seen",
        "screen_after_app_start",
        "screen_after_app_start_initial",
        "screen_after_app_start_final",
        "screen_type",
        "screen_before_submit",
        "selected_route",
        "selected_route_reason",
        "final_terminal_screen",
        "post_submit_screens",
        "submit_executed",
        "would_submit_password",
        "login_form_empty_detected",
        "login_form_after_join_landing_detected",
        "credentials_error_code",
        "credentials_stage",
        "credentials_invalid_reason",
        "credential_metadata_found",
        "secret_loaded",
        "username_input_result",
        "password_input_result",
        "username_field_focused_before_input",
        "password_field_focused_before_input",
        "warnings",
        "dashboard_action_type",
        "final_login_status",
        "final_provisioning_status",
        "final_onboarding_status",
        "email_code_challenge_detected",
        "challenge_type",
    }
    return {key: summary.get(key) for key in allowed_keys if key in summary}


def _log_auto_login_foreground_package_result(
    *,
    account_id: str,
    request_id: str,
    request_metadata: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    if not summary.get("package_guard_checked"):
        return
    package_mismatch = bool(summary.get("package_guard_mismatch"))
    binding = dict(request_metadata or {})
    log(
        "error" if package_mismatch else "info",
        "auto_login_app_instance_mismatch"
        if package_mismatch
        else "auto_login_foreground_package_verified",
        account_id=account_id,
        request_id=request_id,
        assignment_id=binding.get("assignment_id"),
        device_id=binding.get("device_id"),
        app_instance_id=binding.get("app_instance_id"),
        expected_package=summary.get("expected_package_name") or binding.get("package_name"),
        observed_package=summary.get("actual_foreground_package"),
        clone_index=binding.get("clone_index"),
        phase="open_instagram",
        reason=(
            "assigned_instagram_app_instance_mismatch"
            if package_mismatch
            else "foreground_package_verified"
        ),
    )


LOGIN_VERIFICATION_PAUSE_OUTCOMES = frozenset(
    {
        "verification_pending",
        "needs_2fa",
        "checkpoint",
        "unsupported_post_submit_challenge",
    }
)

LOGIN_VERIFICATION_PAUSE_ACTIONS = frozenset(
    {
        "enter_email_verification_code",
        "complete_two_factor",
        "resolve_checkpoint",
        "review_login_challenge",
    }
)

LOGIN_PROVISIONING_BLOCKED_REASONS = frozenset(
    {
        "orphan_challenge_provenance_weak",
        "pre_input_challenge_orphan",
        "historical_provenance_partial",
        "historical_app_instance_mismatch",
        "historical_assignment_mismatch",
        "historical_run_mismatch",
        "historical_challenge_expired",
        "assignment_changed_since_challenge",
    }
)

CLIENT_SAFE_ORPHAN_CHALLENGE_MESSAGE = (
    "Sign-in requires a security verification before it can continue."
)


def _is_login_verification_pause_summary(summary: dict[str, Any]) -> bool:
    if not summary:
        return False
    final_outcome = str(summary.get("final_outcome") or "").strip().lower()
    if final_outcome in LOGIN_VERIFICATION_PAUSE_OUTCOMES:
        return True
    dashboard_action_type = str(summary.get("dashboard_action_type") or "").strip()
    return dashboard_action_type in LOGIN_VERIFICATION_PAUSE_ACTIONS


def _is_login_provisioning_blocked_summary(summary: dict[str, Any]) -> bool:
    if not summary:
        return False
    if str(summary.get("final_outcome") or "").strip().lower() != "blocked":
        return False
    reason = str(summary.get("reason") or summary.get("failure_reason") or "").strip().lower()
    return reason in LOGIN_PROVISIONING_BLOCKED_REASONS or reason.startswith("orphan_challenge")


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


def _terminalize_auto_login_failure(
    *,
    cfg: DispatcherConfig,
    request_id: str,
    account_id: str,
    run_id: str | None,
    internal_reason: str,
    phase: str | None,
    exit_code: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Persist a terminal Auto Login state before incident/notification work.

    The only persistence path is one PostgreSQL transaction. The call is
    retried once because the RPC is idempotent for an already-failed request;
    no split request/run fallback is allowed.
    """
    contract = normalize_auto_login_failure(
        internal_reason,
        phase=phase,
        correction_deployed=True,
    )
    safe_summary = {
        "domain": "auto_login",
        "reason_code": contract.persisted_error_code,
        "phase": contract.phase,
        "retryable": contract.retryable,
        "severity": contract.severity,
        "operator_message": contract.operator_message,
        "recommended_action": contract.recommended_action,
        "client_safe_message": contract.client_safe_message,
        "request_id": request_id,
        "run_id": run_id,
        "account_id": account_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operator_action_required": True,
    }
    if not run_id:
        raise RuntimeError("auto_login_terminalization_run_id_missing")
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            terminal = finalize_auto_login_failure(
                request_id=request_id,
                worker_id=cfg.worker_id,
                account_id=account_id,
                run_id=run_id,
                persisted_error_code=contract.persisted_error_code,
                error_message_safe="Auto Login could not be completed. Review the correlated incident.",
                internal_worker_reason=contract.internal_worker_reason,
                phase=contract.phase,
                exit_code=exit_code,
            )
            return terminal, safe_summary
        except Exception as exc:
            last_error = exc
            log(
                "warning",
                "auto_login_atomic_terminalization_retry_required",
                request_id=request_id,
                run_id=run_id,
                error_code=contract.persisted_error_code,
                attempt=attempt + 1,
                error_type=type(exc).__name__,
            )
            if attempt == 0:
                time.sleep(0.2)
    raise RuntimeError("auto_login_atomic_terminalization_failed") from last_error


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
    is_follow60_canary = _follow60_control_applies(
        account_id=account_id, run_id=run_id, request_id=request_id
    )
    mapped_terminal_status = (
        "stopped"
        if str(terminal_status or "").strip().lower() in {"canceled", "cancelled"}
        else str(terminal_status or "").strip().lower()
    )
    if mapped_terminal_status not in {
        "completed", "failed", "stopped", "canceled", "blocked", "aborted"
    }:
        mapped_terminal_status = "failed"

    # Dedicated V2 controls are independent from the generic Follow60
    # evaluation control.  The RPC is an idempotent no-op for every unbound
    # run and preserves exact 3/7/9 counters on an operator Stop.
    v2_runtime_scoped, _v2_runtime_scope_reason = (
        behavioral_runtime_scope_for_account(account_id)
    )
    if run_id and v2_runtime_scoped:
        try:
            v2_terminal = (
                supabase_client.terminalize_follow60_ordering_v2_behavioral_control_v1(
                    account_id=account_id,
                    run_id=str(run_id),
                    request_id=request_id,
                    terminal_status=mapped_terminal_status,
                    reason=f"run_terminal_{mapped_terminal_status}",
                )
            )
            if v2_terminal.get("reason") != "v2_control_not_bound_noop":
                log(
                    "info", "follow60_ordering_v2_control_terminalized",
                    account_id=account_id, run_id=run_id, request_id=request_id,
                    terminal_status=mapped_terminal_status,
                    control_status=v2_terminal.get("status"),
                    v2_complete_count=v2_terminal.get("v2_complete_count"),
                    v1_fallback_count=v2_terminal.get("v1_fallback_count"),
                )
        except Exception as v2_terminal_exc:
            log(
                "error", "follow60_ordering_v2_control_terminalization_failed",
                account_id=account_id, run_id=run_id, request_id=request_id,
                reason=str(v2_terminal_exc)[:200],
            )

    # Every run is reconciled from durable canonical events.  This makes an
    # operator Stop and a natural terminal exit equivalent for counters and is
    # idempotent under consumer recovery/replay.
    if run_id:
        try:
            canonical = supabase_client.reconcile_ig_run_canonical_totals_v1(
                run_id=str(run_id),
                account_id=account_id,
                terminal_status=mapped_terminal_status,
                metadata_safe={
                    "request_id": request_id,
                    "exit_code": exit_code,
                    "follow60_bound": bool(is_follow60_canary),
                },
            )
            if canonical.get("ok") is True:
                result = {
                    "reconciled": True,
                    "reason": "canonical_event_reconciliation",
                    "run_id": run_id,
                    "terminal_status": mapped_terminal_status,
                    "previous_status": None,
                    "canonical": canonical,
                }
                if is_follow60_canary:
                    try:
                        from follow_60s_canary_binding_v2 import parse_control

                        control = supabase_client.get_follow_60s_canary_control_v1(account_id)
                        binding = parse_control(control)
                        control_terminal_status: str | None
                        if mapped_terminal_status in {"failed", "blocked", "aborted"}:
                            control_terminal_status = "activation_failed"
                        elif mapped_terminal_status in {"stopped", "canceled"}:
                            control_terminal_status = (
                                None
                                if binding.control_status == "waiting_operator_evaluation"
                                else "canceled"
                            )
                        elif mapped_terminal_status == "completed":
                            control_terminal_status = (
                                None
                                if binding.control_status == "waiting_operator_evaluation"
                                else "completed"
                            )
                        else:
                            control_terminal_status = "activation_failed"
                        if binding.control_id and control_terminal_status:
                            supabase_client.terminalize_follow_60s_canary_control_v1(
                                control_id=binding.control_id,
                                account_id=account_id,
                                run_id=str(run_id),
                                request_id=request_id,
                                status=control_terminal_status,
                                reason=f"run_terminal_{mapped_terminal_status}",
                                metadata_safe={"canonical_totals_reconciled": True},
                            )
                    except Exception as control_exc:
                        log(
                            "error", "follow60_control_terminalization_failed",
                            account_id=account_id, run_id=run_id,
                            request_id=request_id, reason=str(control_exc)[:200],
                        )
                log(
                    "info", "ig_run_canonical_totals_reconciled",
                    account_id=account_id, run_id=run_id, request_id=request_id,
                    terminal_status=mapped_terminal_status,
                    total_follow=canonical.get("total_follow"),
                    total_like=canonical.get("total_like"),
                    source="canonical_event_reconciliation",
                )
                return result
        except Exception as canonical_exc:
            log(
                "error" if is_follow60_canary else "warning",
                "ig_run_canonical_totals_reconciliation_failed",
                account_id=account_id, run_id=run_id, request_id=request_id,
                reason=str(canonical_exc)[:240],
                follow60_bound=bool(is_follow60_canary),
            )

    canary_terminal_payload: dict[str, Any] | None = None
    if is_follow60_canary:
        try:
            events = supabase_client._request_json(
                "GET",
                "ig_interaction_events",
                query={
                    "select": "id,event_type,event_status,username,payload,stage_idempotency_key,event_at",
                    "account_id": f"eq.{account_id}",
                    "run_id": f"eq.{run_id}",
                    "event_status": "in.(success,partial)",
                    "limit": "10000",
                },
            ) or []
            follows: set[str] = set()
            likes: dict[str, int] = {}
            stages: set[str] = set()
            for row in events if isinstance(events, list) else []:
                event_type = str(row.get("event_type") or "")
                username = str(row.get("username") or "").strip().lower()
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                if event_type in {"follow_verified", "follow_verified_persisted_v1"} and username:
                    follows.add(username)
                if event_type in {"post_like_success", "post_like_verified"}:
                    key = str(row.get("stage_idempotency_key") or row.get("id") or "")
                    likes[key] = max(1, int(payload.get("liked_count") or 1))
                stage_key = str(row.get("stage_idempotency_key") or "")
                if stage_key:
                    stages.add(stage_key)
            trace_path = Path("/tmp/phonefarm-follow60-stop-traces") / f"{run_id}.json"
            stop_trace: dict[str, Any] = {}
            if trace_path.is_file():
                stop_trace = json.loads(trace_path.read_text(encoding="utf-8"))
            summary = {
                "reason": "canonical_follow60_terminal_reconciliation_v1",
                "session_counters": {
                    "follows": len(follows),
                    "likes": sum(likes.values()),
                    "stages_persisted": len(stages),
                },
                "stop_trace": stop_trace,
                "terminalized_after_worker_exit": True,
            }
            canary_terminal_payload = {
                "totals": {
                    "total": len(follows) + sum(likes.values()),
                    "success": len(follows) + sum(likes.values()),
                    "failed": 0,
                },
                "summary": summary,
                "total_follow": len(follows),
                "total_like": sum(likes.values()),
            }
            log(
                "info", "follow_60s_terminal_summary_rebuilt_before_db_terminal",
                account_id=account_id, request_id=request_id, run_id=run_id,
                total_follow=len(follows), total_like=sum(likes.values()),
                stop_trace=stop_trace,
            )
        except Exception as exc:
            log(
                "error", "follow_60s_terminal_totals_rebuild_failed",
                account_id=account_id, request_id=request_id, run_id=run_id,
                reason=str(exc)[:240],
            )
            # Fail closed: never claim DB terminalization for this canary until
            # canonical receipts and the post-exit stop trace are readable.
            return {
                "reconciled": False,
                "reason": "follow_60s_canonical_summary_unavailable",
                "run_id": run_id,
                "terminal_status": None,
                "previous_status": None,
            }

    if is_follow60_canary and canary_terminal_payload is not None:
        rows = supabase_client._request_json(
            "GET",
            "ig_runs",
            query={
                "select": "id,account_id,status",
                "id": f"eq.{run_id}",
                "limit": "1",
            },
        ) or []
        row = dict(rows[0]) if isinstance(rows, list) and rows else {}
        previous_status = str(row.get("status") or "").strip().lower()
        if not row:
            result = {
                "reconciled": False,
                "reason": "run_not_found",
                "run_id": run_id,
                "terminal_status": None,
                "previous_status": None,
            }
        elif str(row.get("account_id") or "") != account_id:
            result = {
                "reconciled": False,
                "reason": "account_mismatch",
                "run_id": run_id,
                "terminal_status": None,
                "previous_status": previous_status or None,
            }
        elif previous_status in {"completed", "failed", "stopped", "canceled", "blocked", "aborted"}:
            result = {
                "reconciled": False,
                "reason": "already_terminal",
                "run_id": run_id,
                "terminal_status": previous_status,
                "previous_status": previous_status,
            }
        else:
            mapped_status = (
                "stopped"
                if str(terminal_status or "").strip().lower() in {"canceled", "cancelled"}
                else str(terminal_status or "").strip().lower()
            )
            if mapped_status not in {"completed", "failed", "stopped", "canceled", "blocked", "aborted"}:
                mapped_status = "failed"
            # One PATCH writes canonical totals, stop trace and the terminal
            # status together.  This is deliberately not preceded by the
            # generic status-only reconciler.
            supabase_client.update_run_status(
                str(run_id),
                mapped_status,
                dict(canary_terminal_payload["totals"]),
                dict(canary_terminal_payload["summary"]),
            )
            result = {
                "reconciled": True,
                "reason": "follow_60s_atomic_terminal_reconciliation",
                "run_id": run_id,
                "terminal_status": mapped_status,
                "previous_status": previous_status or None,
            }
            log(
                "info", "follow_60s_terminal_totals_rebuilt_from_canonical_events",
                account_id=account_id, request_id=request_id, run_id=run_id,
                total_follow=canary_terminal_payload["total_follow"],
                total_like=canary_terminal_payload["total_like"],
                db_terminal_status=mapped_status,
            )
    else:
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


def _upsert_operator_review_action(
    *,
    decision: IncidentDecision,
    incident_id: str,
    account_id: str,
    request_id: str,
    run_id: str | None,
) -> dict[str, Any]:
    if not decision.requires_operator_review:
        return {}
    return supabase_client.call_rpc(
        "upsert_account_dashboard_action",
        {
            "p_account_id": account_id,
            "p_client_id": None,
            "p_incident_id": incident_id,
            "p_action_type": "operator_review_required",
            "p_status": "pending_verification",
            "p_title": decision.operator_label or "Runtime failure requires operator review",
            "p_dedupe_key": f"account:{account_id}:run:{run_id or request_id}:dashboard_action:operator_review_required",
            "p_safe_client_message": None,
            "p_admin_message": decision.admin_message or decision.action_required,
            "p_assistant_message": decision.action_required or "Human review is required before the next launch.",
            "p_action_label": "Mark reviewed",
            "p_action_deep_link": "/instagram-dashboard/incidents",
            "p_severity": decision.severity,
            "p_audience": "admin",
            "p_requires_client_action": False,
            "p_blocking_campaign": decision.blocking_campaign,
            "p_metadata": {
                "source": "run_dispatcher",
                "request_id": request_id,
                "run_id": run_id,
                "reason": decision.reason_code,
                "incident_type": decision.incident_type,
                "review_workflow": "canonical_operator_review",
            },
        },
    )


def _update_incident_recovery_state(
    *,
    incident_id: str,
    state: str,
    original_run_id: str | None,
    resume_run_id: str | None,
) -> None:
    """Best-effort enrichment for the existing human-resume incident."""
    iid = str(incident_id or "").strip()
    if not iid:
        return
    try:
        rows = supabase_client._request_json(
            "GET",
            "account_incidents",
            query={"select": "id,metadata", "id": f"eq.{iid}", "limit": "1"},
        ) or []
        existing = rows[0].get("metadata") if rows and isinstance(rows[0], dict) else {}
        metadata = dict(existing) if isinstance(existing, dict) else {}
        metadata.update(
            {
                "recovery_state": str(state or "").strip(),
                "original_run_id": str(original_run_id or "").strip() or None,
                "resume_run_id": str(resume_run_id or "").strip() or None,
            }
        )
        supabase_client._request_json(
            "PATCH",
            "account_incidents",
            query={"id": f"eq.{iid}"},
            body={"metadata": metadata, "updated_at": datetime.now(timezone.utc).isoformat()},
            prefer_representation=False,
        )
    except Exception as exc:
        log(
            "warning",
            "incident_recovery_state_update_failed",
            incident_id=iid,
            state=state,
            error=str(exc)[:200],
        )


def _publish_run_failure_incident(
    *,
    request_id: str,
    account_id: str,
    run_id: str | None,
    run_type: str | None,
    exit_code: int,
    timed_out: bool,
    canceled: bool,
    worker_summary: dict[str, Any] | None = None,
    request_snapshot: dict[str, Any] | None = None,
    request_metadata: dict[str, Any] | None = None,
    cleanup_completed: bool | None = None,
    lock_released: bool | None = None,
) -> None:
    try:
        effective_request_metadata = dict(
            request_metadata
            or (request_snapshot or {}).get("metadata_safe")
            or {}
        )
        run_status: str | None = None
        performance_summary: dict[str, Any] | None = dict(worker_summary or {}) or None
        if run_id:
            try:
                run_row = supabase_client.load_run_row(run_id) or {}
                run_status = str(run_row.get("status") or "").strip() or None
                raw_summary = run_row.get("performance_summary")
                if performance_summary is None and isinstance(raw_summary, dict):
                    performance_summary = raw_summary
            except Exception as exc:
                log(
                    "warning",
                    "run_incident_summary_load_failed",
                    account_id=account_id,
                    request_id=request_id,
                    run_id=run_id,
                    error=str(exc)[:300],
                )
        if performance_summary is None and _is_login_run_type(run_type):
            request_reason = str((request_snapshot or {}).get("error_code") or "").strip()
            if request_reason and request_reason not in {
                "worker_exit_nonzero",
                "run_worker_failure",
                "unknown_error",
                "no_structured_reason",
            }:
                auto_login_metadata = dict((request_snapshot or {}).get("metadata_safe") or {})
                performance_summary = {
                    "domain": "auto_login",
                    "run_type": run_type,
                    "reason_code": request_reason,
                    "phase": str(auto_login_metadata.get("phase") or "").strip() or None,
                    "request_id": request_id,
                    "run_id": run_id,
                }
        retry_decision = classify_recoverable_python_retry(
            performance_summary=performance_summary,
            request_metadata=effective_request_metadata,
            run_id=run_id,
            cleanup_completed=cleanup_completed,
            lock_released=lock_released,
        )
        if retry_decision.applies and (
            retry_decision.restart_allowed or retry_decision.retries_exhausted
        ):
            plan = (
                performance_summary.get("auto_restart_resume_plan")
                if isinstance(performance_summary, dict)
                else {}
            )
            plan = dict(plan) if isinstance(plan, dict) else {}
            from account_session_resume_plan_store import (
                record_automatic_retry_terminal_state,
            )

            persisted = record_automatic_retry_terminal_state(
                run_id=str(run_id or ""),
                retry_decision=retry_decision,
                cleanup_completed=cleanup_completed is True,
                lock_released=lock_released is True,
                quota_remaining=plan.get("quota_remaining")
                if isinstance(plan.get("quota_remaining"), dict)
                else {},
                phases_to_run=plan.get("phases_to_run")
                if isinstance(plan.get("phases_to_run"), dict)
                else {},
                scheduled_at=str(effective_request_metadata.get("scheduled_at") or "")
                or None,
                claimed_at=str(effective_request_metadata.get("claimed_at") or "")
                or None,
            )
            if retry_decision.retries_exhausted:
                try:
                    supabase_client.insert_runtime_event(
                        {
                            "event_type": retry_decision.event_type,
                            "severity": "warning",
                            "visibility": "admin_only",
                            "account_id": account_id,
                            "run_id": run_id,
                            "job_id": request_id,
                            "source": "run_dispatcher",
                            "reason": "auto_restart_retries_exhausted",
                            "message": "Recoverable Python retries exhausted.",
                            "metadata": {
                                "business_session_id": retry_decision.business_session_id,
                                "attempt_id": retry_decision.attempt_id,
                                "retry_index": retry_decision.retry_index,
                                "root_failure_code": retry_decision.root_failure_code,
                                "failure_signature": retry_decision.failure_signature,
                                "retries_completed": "2/2",
                            },
                        }
                    )
                except Exception as exc:
                    log(
                        "warning",
                        "recoverable_python_retries_exhausted_event_failed",
                        account_id=account_id,
                        run_id=run_id,
                        error=str(exc)[:200],
                    )
                try:
                    account_username = supabase_client.get_account_username(account_id) or "unknown"
                except Exception:
                    account_username = "unknown"
                incident_notifications.dispatch_python_retries_exhausted_notification(
                    account_id=account_id,
                    account_username=account_username,
                    run_id=run_id,
                    request_id=request_id,
                    business_session_id=retry_decision.business_session_id,
                    phase=str((performance_summary or {}).get("failure_phase") or "unfollow"),
                    root_failure_code=retry_decision.root_failure_code,
                    failure_signature=retry_decision.failure_signature,
                    quota_remaining=plan.get("quota_remaining")
                    if isinstance(plan.get("quota_remaining"), dict)
                    else {},
                    cleanup_completed=cleanup_completed is True,
                )
            log(
                "info",
                retry_decision.event_type,
                account_id=account_id,
                request_id=request_id,
                run_id=run_id,
                business_session_id=retry_decision.business_session_id,
                attempt_id=retry_decision.attempt_id,
                retry_index=retry_decision.retry_index,
                restart_allowed=retry_decision.restart_allowed,
                retries_exhausted=retry_decision.retries_exhausted,
                persisted=bool(persisted.get("persisted")),
            )
            return

        decision = classify_terminal_run_failure(
            exit_code=exit_code,
            timed_out=timed_out,
            run_status=run_status,
            canceled=canceled,
            performance_summary=performance_summary,
            run_type=run_type,
        )
        log(
            "info",
            "run_incident_classified",
            account_id=account_id,
            request_id=request_id,
            run_id=run_id,
            run_type=run_type or None,
            exit_code=exit_code,
            timed_out=timed_out,
            **decision.to_log_fields(),
        )
        if not decision.should_publish:
            return
        try:
            account_username = supabase_client.get_account_username(account_id) or None
        except Exception:
            account_username = None
        original_run_ref = (
            str(
                effective_request_metadata.get("original_run_id")
                or effective_request_metadata.get("prior_run_id")
                or ""
            ).strip()
            if str(effective_request_metadata.get("recovery_mode") or "")
            == "human_confirmed_resume"
            else ""
        )
        payload = build_run_failure_incident_payload(
            decision,
            account_id=account_id,
            account_username=account_username,
            run_id=run_id,
            run_request_id=request_id,
            run_type=run_type,
            dedupe_run_ref=original_run_ref or None,
            device_id=str((request_snapshot or {}).get("device_id") or "").strip() or None,
            app_instance_id=str((request_snapshot or {}).get("app_instance_id") or "").strip() or None,
        )
        if run_type == "account_session":
            phase_summary = build_account_session_phase_notification_summary(
                performance_summary,
                effective_request_metadata,
                primary_failure_reason=str(payload.get("failure_reason") or ""),
            )
            payload_metadata = payload.get("metadata")
            if isinstance(payload_metadata, dict):
                payload_metadata["phase_summary"] = phase_summary
        if original_run_ref and run_id:
            # Preserve the original incident's dedupe lineage without replacing
            # the authoritative physical run.  Recovery finalization is
            # intentionally latest-run scoped and must resolve the resume plan
            # that actually failed.
            payload["metadata"]["resume_run_id"] = run_id
            payload["metadata"]["original_run_id"] = original_run_ref
        result = runtime_incidents.publish_account_incident(**payload)
        incident_id = str(result.get("incident_id") or "").strip()
        if run_id and incident_id:
            if str(effective_request_metadata.get("recovery_mode") or "") == "human_confirmed_resume":
                original_run_id = str(
                    effective_request_metadata.get("original_run_id")
                    or effective_request_metadata.get("prior_run_id")
                    or ""
                ).strip()
                from account_session_resume_plan_store import mark_resume_outcome

                mark_resume_outcome(
                    original_run_id=original_run_id,
                    succeeded=False,
                    reason_code=decision.reason_code,
                )
                _update_incident_recovery_state(
                    incident_id=str(effective_request_metadata.get("incident_id") or incident_id),
                    state="reintervention_required",
                    original_run_id=original_run_id,
                    resume_run_id=run_id,
                )
            else:
                from account_session_resume_plan_store import record_terminal_failure

                record_terminal_failure(
                    run_id=run_id,
                    incident_type=decision.incident_type,
                    reason_code=decision.reason_code,
                    incident_id=incident_id,
                )
        action_id = None
        if (
            incident_id
            and decision.requires_operator_review
            and str(effective_request_metadata.get("recovery_mode") or "")
            != "human_confirmed_resume"
        ):
            action = _upsert_operator_review_action(
                decision=decision,
                incident_id=incident_id,
                account_id=account_id,
                request_id=request_id,
                run_id=run_id,
            )
            action_id = action.get("id") if isinstance(action, dict) else None
            if action_id:
                _audit(
                    account_id=account_id,
                    action_type="operator_review_action_created",
                    status="success",
                    message="Canonical operator review action created for a runtime incident.",
                    run_id=run_id,
                    payload={
                        "incident_id": incident_id,
                        "dashboard_action_id": str(action_id),
                        "request_id": request_id,
                        "incident_type": decision.incident_type,
                        "reason": decision.reason_code,
                        "blocking_campaign": decision.blocking_campaign,
                    },
                )
                # Auto Login uses the canonical incident notifier so one
                # failure cannot emit both a precise incident notification and
                # a second generic operator-review notification.
                if decision.metadata_safe.get("domain") != "auto_login":
                    incident_notifications.dispatch_operator_review_action_notification(
                        event="created",
                        action_id=str(action_id),
                        incident_id=incident_id,
                        account_id=account_id,
                        account_username=account_username or "unknown",
                        reason=decision.admin_message or decision.reason_code,
                        final_status="pending_verification",
                        operator_id="system",
                    )
        log(
            "info",
            "run_incident_publish_result",
            account_id=account_id,
            request_id=request_id,
            run_id=run_id,
            incident_type=decision.incident_type,
            reason_code=decision.reason_code,
            published=bool(result.get("published")),
            publish_reason=result.get("reason"),
            incident_id=incident_id or None,
            dashboard_action_id=action_id,
            occurrence_count=result.get("occurrence_count"),
        )
    except Exception as exc:
        log(
            "warning",
            "run_incident_publish_unexpected_error",
            account_id=account_id,
            request_id=request_id,
            run_id=run_id,
            error=str(exc)[:300],
        )


def _finalize_manual_run_after_subprocess(
    cfg: DispatcherConfig,
    *,
    request_id: str,
    account_id: str,
    exit_code: int,
    timed_out: bool = False,
    request_snapshot: dict[str, Any] | None = None,
    cleanup_completed: bool | None = None,
    lock_released: bool | None = None,
) -> None:
    latest = get_account_run_request(request_id) or request_snapshot or {}
    run_id = str(latest.get("run_id") or "").strip() or None
    run_type = str(
        latest.get("requested_run_type")
        or (request_snapshot or {}).get("requested_run_type")
        or ""
    ).strip().lower()
    canceled = bool(latest.get("cancel_requested_at")) or str(latest.get("status") or "").strip().lower() == "canceled"
    request_metadata = dict(
        latest.get("metadata_safe")
        or (request_snapshot or {}).get("metadata_safe")
        or {}
    )
    if latest.get("claimed_at"):
        request_metadata.setdefault("claimed_at", latest.get("claimed_at"))

    if timed_out:
        if _is_login_run_type(run_type):
            _terminal_result, timeout_summary = _terminalize_auto_login_failure(
                cfg=cfg,
                request_id=request_id,
                account_id=account_id,
                run_id=run_id,
                internal_reason="subprocess_timeout",
                phase="cleanup",
                exit_code=exit_code,
            )
            timeout_summary["run_type"] = run_type
        else:
            timeout_summary = {}
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
        _publish_run_failure_incident(
            request_id=request_id,
            account_id=account_id,
            run_id=run_id,
            run_type=run_type,
            exit_code=exit_code,
            timed_out=True,
            canceled=canceled,
            worker_summary=timeout_summary or None,
            request_snapshot=request_snapshot,
            request_metadata=request_metadata,
            cleanup_completed=cleanup_completed,
            lock_released=lock_released,
        )
        return

    # Cancellation is authoritative even if the child handles SIGTERM and
    # exits with code 0.  Terminalize the durable phase-plan projection too;
    # otherwise a canceled request/run can leave a stale ``run_active`` row
    # indefinitely and make future restart diagnostics contradictory.
    if canceled:
        _safe_complete_account_run_request(request_id, cfg.worker_id, "canceled")
        _reconcile_linked_run(
            account_id=account_id,
            run_id=run_id,
            terminal_status="canceled",
            request_id=request_id,
            exit_code=exit_code,
        )
        if run_id and run_type == "account_session":
            from account_session_resume_plan_store import record_end_of_session

            record_end_of_session(
                run_id=run_id,
                session_plan={
                    "restart_allowed": False,
                    "restart_block_reason": "operator_canceled",
                    "terminal_reason_code": "operator_canceled",
                },
                session_status="success",
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

    if exit_code == 0:
        if _is_orphan_recovery_run_type(run_type):
            _safe_complete_account_run_request(
                request_id,
                cfg.worker_id,
                "completed",
                error_code="login_surface_restored",
                error_message_safe="Login surface restored.",
            )
            _audit(
                account_id=account_id,
                action_type="login_orphan_recovery_completed",
                status="success",
                message="Orphan login challenge recovery restored login surface.",
                run_id=run_id,
                payload={"request_id": request_id, "exit_code": exit_code, "recovery_state": "login_surface_restored"},
            )
            return
        if run_type == "account_session":
            run_row = supabase_client.load_run_row(run_id or "") or {}
            performance_summary = run_row.get("performance_summary")
            contract = (
                performance_summary.get("phase_terminal_contract")
                if isinstance(performance_summary, dict)
                else None
            )
            if not isinstance(contract, dict) or contract.get("ok") is not True:
                reason = "account_session_phase_not_terminal"
                _safe_complete_account_run_request(
                    request_id,
                    cfg.worker_id,
                    "failed",
                    error_code=reason,
                    error_message_safe="Account session stopped before every planned phase reached a terminal state.",
                )
                _reconcile_linked_run(
                    account_id=account_id,
                    run_id=run_id,
                    terminal_status="failed",
                    request_id=request_id,
                    exit_code=1,
                )
                _audit(
                    account_id=account_id,
                    action_type="account_session_terminal_contract_failed",
                    status="failed",
                    message="Account session terminal contract was not satisfied.",
                    run_id=run_id,
                    payload={"request_id": request_id, "phase_terminal_contract": contract},
                )
                _publish_run_failure_incident(
                    request_id=request_id,
                    account_id=account_id,
                    run_id=run_id,
                    run_type=run_type,
                    exit_code=1,
                    timed_out=False,
                    canceled=False,
                    request_metadata=request_metadata,
                    cleanup_completed=cleanup_completed,
                    lock_released=lock_released,
                )
                return
        _safe_complete_account_run_request(request_id, cfg.worker_id, "completed")
        _reconcile_linked_run(
            account_id=account_id,
            run_id=run_id,
            terminal_status="completed",
            request_id=request_id,
            exit_code=exit_code,
        )
        summary = _safe_login_provisioner_summary_for_audit(run_id or request_id)
        if _is_login_run_type(run_type):
            _log_auto_login_foreground_package_result(
                account_id=account_id,
                request_id=request_id,
                request_metadata=request_metadata,
                summary=summary,
            )
        _audit(
            account_id=account_id,
            action_type="manual_run_completed",
            status="success",
            message="Manual run completed.",
            run_id=run_id,
            payload={
                "request_id": request_id,
                "exit_code": exit_code,
                **({"login_provisioner_summary": summary} if summary else {}),
            },
        )
        if (
            is_auto_restart_request(request_metadata)
            and str(request_metadata.get("failure_category") or "")
            == "recoverable_python_runtime_failure"
            and run_id
        ):
            from account_session_resume_plan_store import mark_automatic_retry_success

            mark_automatic_retry_success(
                run_id=run_id,
                request_metadata=request_metadata,
            )
        return

    summary = _safe_login_provisioner_summary_for_audit(run_id or request_id)
    if _is_login_run_type(run_type):
        _log_auto_login_foreground_package_result(
            account_id=account_id,
            request_id=request_id,
            request_metadata=request_metadata,
            summary=summary,
        )
    if _is_orphan_recovery_run_type(run_type):
        from login_orphan_recovery_state import resolve_orphan_recovery_state

        recovery_state = resolve_orphan_recovery_state(account_id)
        terminal_status = "blocked" if recovery_state.get("state") != "login_surface_restored" else "completed"
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            terminal_status,
            error_code=str(recovery_state.get("state") or "recovery_blocked"),
            error_message_safe="Orphan login challenge recovery stopped without restoring login surface.",
        )
        _audit(
            account_id=account_id,
            action_type="login_orphan_recovery_blocked",
            status="blocked",
            message="Orphan login challenge recovery blocked.",
            run_id=run_id,
            payload={
                "request_id": request_id,
                "exit_code": exit_code,
                "recovery_state": recovery_state,
            },
        )
        return
    if _is_login_verification_pause_summary(summary):
        _audit(
            account_id=account_id,
            action_type="manual_run_verification_paused",
            status="paused",
            message="Login provisioning paused awaiting client verification.",
            run_id=run_id,
            payload={
                "request_id": request_id,
                "exit_code": exit_code,
                **({"login_provisioner_summary": summary} if summary else {}),
            },
        )
        log(
            "info",
            "manual_run_verification_paused",
            account_id=account_id,
            request_id=request_id,
            run_id=run_id,
            worker_id=cfg.worker_id,
            exit_code=exit_code,
            final_outcome=str(summary.get("final_outcome") or ""),
            dashboard_action_type=str(summary.get("dashboard_action_type") or ""),
        )
        return

    if _is_login_provisioning_blocked_summary(summary):
        blocked_reason = str(summary.get("failure_reason") or summary.get("reason") or "orphan_challenge_provenance_weak")
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "blocked",
            error_code="orphan_challenge_provenance_weak",
            error_message_safe=CLIENT_SAFE_ORPHAN_CHALLENGE_MESSAGE,
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
            action_type="manual_run_blocked",
            status="blocked",
            message="Login provisioning blocked pending canonical challenge recovery.",
            run_id=run_id,
            payload={
                "request_id": request_id,
                "exit_code": exit_code,
                "blocked_reason": blocked_reason,
                **({"login_provisioner_summary": summary} if summary else {}),
            },
        )
        return

    structured_summary = dict(summary or {})
    is_auto_login = _is_login_run_type(run_type)
    internal_reason = str(
        structured_summary.get("reason_code")
        or structured_summary.get("failure_reason")
        or structured_summary.get("reason")
        or ""
    ).strip()
    if is_auto_login:
        terminal_result, structured_summary = _terminalize_auto_login_failure(
            cfg=cfg,
            request_id=request_id,
            account_id=account_id,
            run_id=run_id,
            internal_reason=internal_reason or "unclassified_auto_login_failure",
            phase=str(summary.get("phase") or "").strip() or None,
            exit_code=exit_code,
        )
        structured_summary.update(
            {
                "run_type": run_type,
                "device_id": str((request_snapshot or {}).get("device_id") or "").strip() or None,
                "app_instance_id": str((request_snapshot or {}).get("app_instance_id") or "").strip() or None,
            }
        )
    else:
        terminal_result = _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "failed",
            error_code="worker_exit_nonzero",
            error_message_safe=f"Worker subprocess exited with code {exit_code}.",
        ) or {}
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
        payload={
            "request_id": request_id,
            "exit_code": exit_code,
            "terminalization": {
                "request_status": terminal_result.get("request_status"),
                "run_status": terminal_result.get("run_status"),
                "persisted_error_code": terminal_result.get("persisted_error_code"),
                "contract": terminal_result.get("reason"),
            },
            **({"login_provisioner_summary": structured_summary} if is_auto_login else {}),
        },
    )
    _publish_run_failure_incident(
        request_id=request_id,
        account_id=account_id,
        run_id=run_id,
        run_type=run_type,
        exit_code=exit_code,
        timed_out=False,
        canceled=canceled,
        worker_summary=structured_summary if is_auto_login else None,
        request_snapshot=request_snapshot,
        request_metadata=request_metadata,
        cleanup_completed=cleanup_completed,
        lock_released=lock_released,
    )


def _terminate_subprocess(
    proc: subprocess.Popen[Any], *, graceful_timeout_seconds: float = 30.0
) -> int:
    proc.send_signal(signal.SIGTERM)
    try:
        return int(proc.wait(timeout=max(1.0, float(graceful_timeout_seconds))))
    except subprocess.TimeoutExpired:
        proc.kill()
        return int(proc.wait(timeout=10))


def _wait_for_subprocess(
    cfg: DispatcherConfig,
    proc: subprocess.Popen[Any],
    *,
    request_id: str,
    account_id: str,
    device_id: str | None = None,
    device_lock_renewal: bool = False,
) -> tuple[int, bool]:
    deadline = time.monotonic() + cfg.subprocess_timeout_seconds
    next_lock_renew = time.monotonic()
    lock_renew_interval = max(30.0, float(cfg.heartbeat_seconds) * 2.0)
    last_control_plane_error_logged_at: float | None = None
    while True:
        exit_code = proc.poll()
        if exit_code is not None:
            return int(exit_code), False

        try:
            latest = get_account_run_request(request_id)
        except Exception as exc:
            # A control-plane read failure must not detach the child.  The old
            # behaviour raised out of this loop, discarded the dispatch Future,
            # and left the request running even when the orphaned Runner later
            # published a terminal ig_runs status.
            latest = None
            now = time.monotonic()
            if (
                last_control_plane_error_logged_at is None
                or now - last_control_plane_error_logged_at >= 60.0
            ):
                log(
                    "warning",
                    "manual_run_control_plane_read_failed_while_child_active",
                    account_id=account_id,
                    request_id=request_id,
                    worker_id=cfg.worker_id,
                    error=str(exc)[:200],
                )
                last_control_plane_error_logged_at = now
        if latest and (latest.get("status") == "canceled" or latest.get("cancel_requested_at")):
            log(
                "info",
                "manual_run_subprocess_cancel_requested",
                account_id=account_id,
                request_id=request_id,
                worker_id=cfg.worker_id,
            )
            # The scoped Follow 60s canary may need to replay one verified Follow intent and
            # flush bounded deferred projections. Keep every other account on
            # the exact Golden 30-second termination contract.
            if _follow60_control_applies(
                account_id=account_id,
                run_id=str(latest.get("run_id") or ""),
                request_id=request_id,
            ):
                return _terminate_subprocess(
                    proc, graceful_timeout_seconds=90.0
                ), False
            return _terminate_subprocess(proc), False

        if device_lock_renewal and device_id and time.monotonic() >= next_lock_renew:
            try:
                renew_device_lock(
                    device_id=device_id,
                    worker_id=cfg.worker_id,
                    request_id=request_id,
                )
            except Exception as exc:
                log(
                    "warning",
                    "manual_run_device_lock_renew_failed_while_child_active",
                    account_id=account_id,
                    request_id=request_id,
                    worker_id=cfg.worker_id,
                    error=str(exc)[:200],
                )
            next_lock_renew = time.monotonic() + lock_renew_interval

        if time.monotonic() >= deadline:
            return _terminate_subprocess(proc), True

        time.sleep(1.0)


def _publish_auto_login_dispatch_failure(
    *,
    request: dict[str, Any],
    request_id: str,
    account_id: str,
    run_type: str,
    reason_code: str,
    phase: str,
    device_id: str | None = None,
    app_instance_id: str | None = None,
) -> None:
    """Publish one structured Auto Login incident before a worker result exists."""
    if not _is_login_run_type(run_type):
        return
    snapshot = {
        **request,
        "error_code": reason_code,
        "device_id": device_id or request.get("device_id"),
        "app_instance_id": app_instance_id or request.get("app_instance_id"),
        "metadata_safe": {
            **dict(request.get("metadata_safe") or {}),
            "phase": phase,
        },
    }
    _publish_run_failure_incident(
        request_id=request_id,
        account_id=account_id,
        run_id=None,
        run_type=run_type,
        exit_code=1,
        timed_out=False,
        canceled=False,
        worker_summary={
            "domain": "auto_login",
            "run_type": run_type,
            "phase": phase,
            "reason_code": reason_code,
            "request_id": request_id,
            "run_id": None,
            "account_id": account_id,
            "device_id": snapshot.get("device_id"),
            "app_instance_id": snapshot.get("app_instance_id"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        request_snapshot=snapshot,
    )


PACKAGE_RUNTIME_CONTRACT_REASONS = {
    "assignment_package_mismatch",
    "app_instance_package_mismatch",
    "clone_package_mismatch",
    "package_settings_incomplete",
    "runtime_profile_mismatch",
}


def _load_package_runtime_contract(account_id: str) -> tuple[bool, str, dict[str, Any]]:
    """Fail closed before any device lock or subprocess when the DB contract is incomplete."""
    try:
        value = supabase_client.call_rpc(
            "account_package_runtime_contract_status",
            {"p_account_id": account_id},
        )
    except Exception as exc:
        log(
            "error",
            "package_runtime_contract_preflight_failed",
            account_id=account_id,
            reason="package_settings_incomplete",
            error_type=type(exc).__name__,
        )
        return False, "package_settings_incomplete", {}
    contract = dict(value) if isinstance(value, dict) else {}
    reason = str(contract.get("reason") or "package_settings_incomplete").strip()
    if reason not in PACKAGE_RUNTIME_CONTRACT_REASONS and reason != "ready":
        reason = "package_settings_incomplete"
    ok = contract.get("ok") is True and reason == "ready"
    log(
        "info" if ok else "warning",
        "package_runtime_contract_preflight_completed",
        account_id=account_id,
        ok=ok,
        reason=reason,
        commercial_package_code=contract.get("commercial_package_code"),
        runtime_profile=contract.get("runtime_profile"),
    )
    return ok, reason, contract


def _load_account_protection_snapshot(account_id: str) -> tuple[bool, str, str, dict[str, Any]]:
    """Load exactly once before device access and freeze the result for the subprocess."""
    try:
        snapshot = load_snapshot_for_run(account_id, supabase_client.call_rpc)
        serialized = serialize_snapshot(snapshot)
        metadata = {
            "protection_lists_source": "canonical_v1",
            "protection_lists_storage": snapshot.source,
            "lists_loaded_at": snapshot.loaded_at,
            "lists_version": dict(snapshot.versions),
            "blacklist_count": len(snapshot.interaction_blacklist),
            "interaction_blacklist_version": snapshot.versions["interaction_blacklist"],
            "interaction_blacklist_count": len(snapshot.interaction_blacklist),
            "unfollow_whitelist_version": snapshot.versions["unfollow_whitelist"],
            "unfollow_whitelist_count": len(snapshot.unfollow_whitelist),
        }
        log("info", "account_protection_lists_snapshot_loaded", account_id=account_id, **metadata)
        return True, "ready", serialized, metadata
    except Exception as exc:
        detail = str(exc)
        reason = (
            "unfollow_whitelist_load_failed"
            if "unfollow_whitelist" in detail and "interaction_blacklist" not in detail
            else "interaction_blacklist_load_failed"
        )
        log(
            "error",
            "account_protection_lists_snapshot_failed",
            account_id=account_id,
            reason=reason,
            error_type=type(exc).__name__,
        )
        return False, reason, "", {}


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
        failure_reason = block_reason or "auto_login_not_ready"
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "blocked",
            error_code=failure_reason,
            error_message_safe=f"Run request blocked: {failure_reason}.",
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_blocked",
            status="blocked",
            message=f"Run request blocked: {failure_reason}.",
            payload={"request_id": request_id, "reason": block_reason},
        )
        _publish_auto_login_dispatch_failure(
            request=request,
            request_id=request_id,
            account_id=account_id,
            run_type=run_type,
            reason_code=failure_reason,
            phase="request",
        )
        return

    contract_ok, contract_reason, _contract = _load_package_runtime_contract(account_id)
    if not contract_ok:
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "blocked",
            error_code=contract_reason,
            error_message_safe=f"Package runtime contract blocked: {contract_reason}.",
        )
        _audit(
            account_id=account_id,
            action_type="package_runtime_contract_blocked",
            status="blocked",
            message=f"Run request blocked before device access: {contract_reason}.",
            payload={"request_id": request_id, "reason": contract_reason},
        )
        _publish_auto_login_dispatch_failure(
            request=request,
            request_id=request_id,
            account_id=account_id,
            run_type=run_type,
            reason_code=contract_reason,
            phase="package_runtime_contract",
        )
        return

    starting = mark_account_run_request_starting(request_id, cfg.worker_id)
    if not starting:
        return

    ok_assignment, assignment_reason, dispatch_ctx = _validate_assignment(account_id, run_type, cfg)
    safe_dispatch = sensitive_log_fields(dispatch_ctx)
    if not ok_assignment:
        error_code = assignment_reason or "assignment_blocked"
        if (
            _is_scheduled_session_preflight_run_type(run_type)
            and error_code == "assignment_device_missing_adb_serial"
        ):
            from scheduled_session_preflight_control import terminalize_scheduled_session_preflight_request

            terminalize_scheduled_session_preflight_request(
                request=request,
                worker_id=cfg.worker_id,
                preflight_status="preflight_blocked",
                reason_code="device_serial_missing",
            )
            error_code = "device_serial_missing"
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "blocked",
            error_code=error_code,
            error_message_safe=f"Assignment blocked: {error_code}.",
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_blocked",
            status="blocked",
            message=f"Assignment blocked: {error_code}.",
            payload={"request_id": request_id, "assignment": safe_dispatch},
        )
        _publish_auto_login_dispatch_failure(
            request=request,
            request_id=request_id,
            account_id=account_id,
            run_type=run_type,
            reason_code=error_code,
            phase="request",
            device_id=str(dispatch_ctx.get("device_id") or "").strip() or None,
            app_instance_id=str(dispatch_ctx.get("app_instance_id") or "").strip() or None,
        )
        return

    protection_snapshot_json = ""
    protection_metadata: dict[str, Any] = {}
    if run_type in {"account_session", "outreach_session"}:
        protection_ok, protection_reason, protection_snapshot_json, protection_metadata = _load_account_protection_snapshot(account_id)
        if not protection_ok:
            _safe_complete_account_run_request(
                request_id,
                cfg.worker_id,
                "blocked",
                error_code=protection_reason,
                error_message_safe="Run request blocked before device access: account protection lists unavailable.",
            )
            _audit(
                account_id=account_id,
                action_type="account_protection_lists_blocked",
                status="blocked",
                message="Run request blocked before device access: account protection lists unavailable.",
                payload={"request_id": request_id, "reason": protection_reason},
            )
            return

    request_metadata = {
        **dict(request.get("metadata_safe") or {}),
        **protection_metadata,
    }
    login_binding: dict[str, Any] | None = None
    if _is_login_run_type(run_type):
        binding_ok, binding_reason, login_binding = _validate_login_request_binding(
            request_metadata,
            dispatch_ctx,
        )
        if not binding_ok:
            reason = binding_reason or "auto_login_app_instance_binding_missing"
            event = (
                "auto_login_app_instance_binding_missing"
                if reason == "auto_login_app_instance_binding_missing"
                else "auto_login_app_instance_mismatch"
            )
            log(
                "error",
                event,
                account_id=account_id,
                request_id=request_id,
                assignment_id=(login_binding or {}).get("assignment_id") or None,
                device_id=(login_binding or {}).get("device_id") or None,
                app_instance_id=(login_binding or {}).get("app_instance_id") or None,
                expected_package=(login_binding or {}).get("package_name") or None,
                clone_index=(login_binding or {}).get("clone_index"),
                phase="request_binding",
                reason=reason,
            )
            _safe_complete_account_run_request(
                request_id,
                cfg.worker_id,
                "blocked",
                error_code=reason,
                error_message_safe=(
                    "Auto Login blocked: assigned Instagram app instance binding is missing."
                    if reason == "auto_login_app_instance_binding_missing"
                    else "Auto Login blocked: assigned Instagram app instance binding changed."
                ),
            )
            _audit(
                account_id=account_id,
                action_type="auto_login_app_instance_binding_blocked",
                status="blocked",
                message="Auto Login app-instance binding failed closed before launch.",
                payload={"request_id": request_id, "reason": reason},
            )
            _publish_auto_login_dispatch_failure(
                request=request,
                request_id=request_id,
                account_id=account_id,
                run_type=run_type,
                reason_code=reason,
                phase="request_binding",
                device_id=(login_binding or {}).get("device_id") or None,
                app_instance_id=(login_binding or {}).get("app_instance_id") or None,
            )
            return
        log(
            "info",
            "auto_login_app_instance_binding_resolved",
            account_id=account_id,
            request_id=request_id,
            assignment_id=login_binding.get("assignment_id"),
            device_id=login_binding.get("device_id"),
            app_instance_id=login_binding.get("app_instance_id"),
            expected_package=login_binding.get("package_name"),
            clone_index=login_binding.get("clone_index"),
            phase="request_binding",
            reason="binding_verified",
        )
    policy_ok, policy_reason, policy_ctx = evaluate_queued_run_commercial_policy(
        account_id,
        request_metadata,
        request_created_at=str(request.get("created_at") or "").strip() or None,
    )
    if not policy_ok:
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "blocked",
            error_code=policy_reason or "commercial_policy_revision_changed",
            error_message_safe="Run request blocked: commercial package changed since queue.",
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_blocked",
            status="blocked",
            message="Run request blocked: commercial package changed since queue.",
            payload={"request_id": request_id, "policy": policy_ctx},
        )
        _publish_auto_login_dispatch_failure(
            request=request,
            request_id=request_id,
            account_id=account_id,
            run_type=run_type,
            reason_code=policy_reason or "commercial_policy_revision_changed",
            phase="request",
            device_id=str(dispatch_ctx.get("device_id") or "").strip() or None,
            app_instance_id=str(dispatch_ctx.get("app_instance_id") or "").strip() or None,
        )
        return

    if _is_scheduled_session_preflight_run_type(run_type):
        from scheduled_session_preflight_control import (
            evaluate_scheduled_session_preflight_claim,
            terminalize_scheduled_session_preflight_request,
        )

        ok_preflight, terminal_status, terminal_reason = evaluate_scheduled_session_preflight_claim(request=request)
        if not ok_preflight:
            terminalize_scheduled_session_preflight_request(
                request=request,
                worker_id=cfg.worker_id,
                preflight_status=str(terminal_status or "preflight_invalidated"),
                reason_code=str(terminal_reason or "preflight_unavailable"),
            )
            _audit(
                account_id=account_id,
                action_type="manual_run_blocked",
                status="blocked",
                message=f"Scheduled session preflight terminalized: {terminal_reason or terminal_status}.",
                payload={"request_id": request_id, "run_type": run_type, "reason": terminal_reason},
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
            "commercial_policy": policy_ctx,
        },
    )

    auto_restart_policy: dict[str, Any] | None = None
    device_id = str(dispatch_ctx.get("device_id") or "").strip()
    device_lock_active = False
    lock_owner_worker_id = cfg.worker_id
    if is_auto_restart_request(request_metadata):
        ok_resume, resume_reason, auto_restart_policy = validate_auto_restart_request_at_claim(
            account_id=account_id,
            metadata=request_metadata,
        )
        log_auto_restart_claim_validation(
            account_id=account_id,
            request_id=request_id,
            ok=ok_resume,
            reason=resume_reason,
        )
        if not ok_resume:
            if is_hard_stop_reason(resume_reason):
                from auto_restart_hard_stop import execute_auto_restart_hard_stop

                execute_auto_restart_hard_stop(
                    account_id=account_id,
                    reason=resume_reason,
                    request_id=request_id,
                    device_id=device_id or None,
                    clone_id=str(dispatch_ctx.get("clone_id") or "").strip() or None,
                    app_instance_id=str(dispatch_ctx.get("app_instance_id") or "").strip() or None,
                    execution_worker_id=str(request_metadata.get("execution_worker_id") or "").strip() or None,
                    trigger_source=str(request_metadata.get("trigger_source") or "").strip() or None,
                    worker_id=cfg.worker_id,
                    evidence={"metadata_redacted": True, "claim_validation": resume_reason, "execution_worker_id": cfg.worker_id, "worker_id": cfg.worker_id},
                    cancel_pending=False,
                )
            _safe_complete_account_run_request(
                request_id,
                cfg.worker_id,
                "blocked",
                error_code=resume_reason or "resume_runtime_not_supported",
                error_message_safe=f"Auto Restart blocked: {resume_reason or 'resume_runtime_not_supported'}.",
            )
            if device_id:
                tick_worker = str(request_metadata.get("worker_id") or "").strip() or cfg.worker_id
                release_device_lock(device_id=device_id, worker_id=tick_worker, request_id=request_id)
            else:
                tick_worker = str(request_metadata.get("worker_id") or "").strip() or cfg.worker_id
                release_device_lock_for_request(request_id=request_id, worker_id=tick_worker)
            return
        if device_id:
            transfer_device_lock(
                device_id=device_id,
                request_id=request_id,
                new_worker_id=cfg.worker_id,
            )
            renew_device_lock(device_id=device_id, worker_id=cfg.worker_id, request_id=request_id)
            device_lock_active = True
            lock_owner_worker_id = cfg.worker_id
    elif device_id and _is_device_bound_run_type(run_type):
        transfer_result = transfer_device_lock(
            device_id=device_id,
            request_id=request_id,
            new_worker_id=cfg.worker_id,
        )
        if not bool((transfer_result or {}).get("transferred")):
            pending_worker = _pending_manual_lock_worker_id(request_id)
            acquired = acquire_device_lock(
                device_id=device_id,
                worker_id=cfg.worker_id,
                account_id=account_id,
                app_instance_id=str(dispatch_ctx.get("app_instance_id") or "").strip() or None,
                reason="manual_run",
            )
            if not bool((acquired or {}).get("acquired")):
                _safe_complete_account_run_request(
                    request_id,
                    cfg.worker_id,
                    "blocked",
                    error_code="device_lock_held",
                    error_message_safe="Manual run blocked: assigned phone is reserved by another active session.",
                )
                _audit(
                    account_id=account_id,
                    action_type="manual_run_blocked",
                    status="blocked",
                    message="Manual run blocked because the assigned phone is reserved.",
                    payload={"request_id": request_id, "run_type": run_type, "device_id": device_id},
                )
                _publish_auto_login_dispatch_failure(
                    request=request,
                    request_id=request_id,
                    account_id=account_id,
                    run_type=run_type,
                    reason_code="device_lock_held",
                    phase="device_lock",
                    device_id=device_id,
                    app_instance_id=str(dispatch_ctx.get("app_instance_id") or "").strip() or None,
                )
                release_device_lock(device_id=device_id, worker_id=pending_worker, request_id=request_id)
                return
            transfer_device_lock(
                device_id=device_id,
                request_id=request_id,
                new_worker_id=cfg.worker_id,
            )
        renew_device_lock(device_id=device_id, worker_id=cfg.worker_id, request_id=request_id)
        device_lock_active = True
        lock_owner_worker_id = cfg.worker_id

    if (_is_login_run_type(run_type) or _is_orphan_recovery_run_type(run_type)) and not adb_serial:
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
        _publish_auto_login_dispatch_failure(
            request=request,
            request_id=request_id,
            account_id=account_id,
            run_type=run_type,
            reason_code="login_device_serial_required",
            phase="open_instagram",
            device_id=device_id or None,
            app_instance_id=str(dispatch_ctx.get("app_instance_id") or "").strip() or None,
        )
        if device_lock_active and device_id:
            release_device_lock(device_id=device_id, worker_id=lock_owner_worker_id, request_id=request_id)
        return

    if _is_scheduled_session_preflight_run_type(run_type) and not adb_serial:
        from scheduled_session_preflight_control import terminalize_scheduled_session_preflight_request

        terminalize_scheduled_session_preflight_request(
            request=request,
            worker_id=cfg.worker_id,
            preflight_status="preflight_blocked",
            reason_code="device_serial_missing",
        )
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "blocked",
            error_code="device_serial_missing",
            error_message_safe="Scheduled session preflight blocked: device serial missing.",
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_blocked",
            status="blocked",
            message="Scheduled session preflight blocked: device serial missing.",
            payload={"request_id": request_id, "run_type": run_type, "assignment": safe_dispatch},
        )
        if device_lock_active and device_id:
            release_device_lock(device_id=device_id, worker_id=lock_owner_worker_id, request_id=request_id)
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

    linked_login_run_id = None
    if _is_login_run_type(run_type):
        linked_login_run_id = _create_and_link_login_run(account_id, request_id, cfg.worker_id)

    cmd = _build_runner_command(
        account_id,
        run_type,
        linked_login_run_id or request_id,
        run_request_id=request_id,
        device_serial=adb_serial,
        package_name=(login_binding or {}).get("package_name")
        if _is_login_run_type(run_type)
        else dispatch_ctx.get("package_name") or dispatch_ctx.get("app_package") or dispatch_ctx.get("package"),
        app_instance_id=(login_binding or {}).get("app_instance_id")
        if _is_login_run_type(run_type)
        else dispatch_ctx.get("app_instance_id"),
        metadata_safe={
            **request_metadata,
            "assignment_id": dispatch_ctx.get("assignment_id"),
            "assignment_updated_at": dispatch_ctx.get("assignment_updated_at"),
            "credentials_version": dispatch_ctx.get("credentials_version"),
        },
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
    if _is_login_run_type(run_type):
        log(
            "info",
            "auto_login_app_launch_requested",
            account_id=account_id,
            request_id=request_id,
            assignment_id=(login_binding or {}).get("assignment_id"),
            device_id=(login_binding or {}).get("device_id"),
            app_instance_id=(login_binding or {}).get("app_instance_id"),
            expected_package=(login_binding or {}).get("package_name"),
            clone_index=(login_binding or {}).get("clone_index"),
            phase="open_instagram",
            reason="explicit_bound_package_launch",
        )
    _heartbeat(cfg, status="running", metadata={"active_request_id": request_id, "account_id": account_id})

    subprocess_env = (
        _login_provisioner_env()
        if _is_login_run_type(run_type) or _is_orphan_recovery_run_type(run_type)
        else runner_subprocess_env()
    )
    runtime_identity_env = _runner_runtime_identity_env(request_id)
    subprocess_env = {**subprocess_env, **runtime_identity_env}
    log(
        "info",
        "worker_runtime_identity_propagated_to_runner",
        account_id=account_id,
        request_id=request_id,
        worker_sha=runtime_identity_env.get("WORKER_GIT_SHA"),
        runtime_root=runtime_identity_env.get("WORKER_RUNTIME_ROOT"),
        identity_source=runtime_identity_env.get("WORKER_GIT_SHA_SOURCE"),
        consumer_pid=os.getpid(),
        identity_transport_present=True,
    )
    if protection_snapshot_json:
        subprocess_env = {
            **subprocess_env,
            ACCOUNT_PROTECTION_SNAPSHOT_ENV: protection_snapshot_json,
            "ACCOUNT_PROTECTION_LISTS_REQUIRED": "1",
        }
        log(
            "info",
            "account_protection_lists_snapshot_propagated",
            account_id=account_id,
            request_id=request_id,
            run_type=run_type,
            **protection_metadata,
        )
    if run_type == "account_session":
        deadline_env = _account_session_deadline_env(request_metadata, dispatch_ctx)
        subprocess_env = {**subprocess_env, **deadline_env}
        log(
            "info",
            "account_session_deadline_propagated",
            account_id=account_id,
            request_id=request_id,
            session_deadline=deadline_env.get("BUSINESS_ACTION_DEADLINE"),
            scheduled_session_start=deadline_env.get("SCHEDULED_SESSION_START"),
            scheduled_session_end=deadline_env.get("SCHEDULED_SESSION_END"),
            deadline_source=(
                "scheduler_request_metadata"
                if deadline_env.get("BUSINESS_ACTION_DEADLINE")
                else "fallback_required"
            ),
        )
    if auto_restart_policy:
        subprocess_env = {**subprocess_env, **runner_env_for_resume_policy(auto_restart_policy)}

    global _last_integration_noop_proof
    _last_integration_noop_proof = None
    if _integration_noop_runner_enabled():
        subprocess_env = {
            **os.environ,
            **subprocess_env,
            "RUN_CONTROL_INTEGRATION_MODE": "1",
            "RUN_CONTROL_INTEGRATION_NOOP_RUNNER": "1",
            "RUN_CONTROL_INTEGRATION_NO_ADB": "1",
        }
        completed = subprocess.run(
            cmd,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=subprocess_env,
            capture_output=True,
            text=True,
        )
        for line in (completed.stdout or "").splitlines():
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                parsed = json.loads(stripped)
                if parsed.get("runner") == "auto_restart_integration_noop":
                    _last_integration_noop_proof = parsed
                    break
            except json.JSONDecodeError:
                continue
        exit_code = int(completed.returncode)
        timed_out = False
    else:
        proc = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=subprocess_env,
        )
        exit_code, timed_out = _wait_for_subprocess(
            cfg,
            proc,
            request_id=request_id,
            account_id=account_id,
            device_id=device_id if device_lock_active else None,
            device_lock_renewal=device_lock_active,
        )

    lock_released = False
    if device_id and device_lock_active:
        if _is_scheduled_session_preflight_run_type(run_type) and int(exit_code) == 0:
            renew_device_lock(
                device_id=device_id,
                worker_id=lock_owner_worker_id,
                request_id=request_id,
            )
        else:
            release_result = release_device_lock(
                device_id=device_id,
                worker_id=lock_owner_worker_id,
                request_id=request_id,
            )
            lock_released = bool((release_result or {}).get("released"))

    _finalize_manual_run_after_subprocess(
        cfg,
        request_id=request_id,
        account_id=account_id,
        exit_code=int(exit_code),
        timed_out=timed_out,
        request_snapshot={
            **request,
            "device_id": dispatch_ctx.get("device_id"),
            "app_instance_id": dispatch_ctx.get("app_instance_id"),
        },
        # Every normal runner return passes through _return_with_cleanup. A
        # timeout/forced termination cannot make that guarantee.
        cleanup_completed=not timed_out,
        lock_released=lock_released,
    )


def _claim_next_dispatch_request(
    cfg: DispatcherConfig,
    *,
    heartbeat_status: str = "idle",
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not cfg.enabled:
        return None, {"ok": False, "reason": "disabled"}

    _heartbeat(cfg, status=heartbeat_status)
    reclaimed = reclaim_stale_account_run_requests(cfg.worker_id)
    try:
        from scheduled_session_preflight_control import reconcile_stale_scheduled_session_preflight_requests

        reconciled = reconcile_stale_scheduled_session_preflight_requests(
            worker_id=cfg.worker_id,
            allowed_run_types=cfg.allowed_run_types,
        )
        if reconciled:
            log(
                "info",
                "scheduled_session_preflight_stale_reconciled",
                worker_id=cfg.worker_id,
                reconciled_count=reconciled,
            )
    except Exception as exc:
        log(
            "warning",
            "scheduled_session_preflight_reconcile_failed",
            worker_id=cfg.worker_id,
            error=str(exc)[:200],
        )

    if cfg.health_only or not cfg.launch_enabled:
        return None, {"ok": True, "mode": "health_only", "reclaimed": reclaimed}

    request = claim_next_account_run_request(
        cfg.worker_id,
        lease_seconds=cfg.lease_seconds,
        allowed_run_types=cfg.allowed_run_types,
    )
    if not request:
        return None, {"ok": True, "mode": "idle", "reclaimed": reclaimed}

    request_id = normalize_request_uuid(request.get("id"))
    if not request_id:
        log(
            "warning",
            "run_control_skip_invalid_claim_row",
            worker_id=cfg.worker_id,
        )
        return None, {
            "ok": True,
            "mode": "idle",
            "reason": "invalid_claim_row",
            "reclaimed": reclaimed,
        }

    account_id = normalize_request_uuid(request.get("account_id"))
    result: dict[str, Any] = {
        "ok": True,
        "mode": "processed",
        "request_id": request_id,
        "account_id": account_id,
        "reclaimed": reclaimed,
    }
    if _last_integration_noop_proof:
        result["noop_proof"] = _last_integration_noop_proof
    return request, result


def run_once(cfg: DispatcherConfig | None = None) -> dict[str, Any]:
    _assert_integration_safety()
    cfg = cfg or load_dispatcher_config()
    request, result = _claim_next_dispatch_request(cfg)
    if request is not None:
        _handle_claimed_request(cfg, request)
    return result


def evaluate_launch_mode_startup_preflight_with_retries(cfg: DispatcherConfig) -> dict[str, Any]:
    max_attempts = max(1, _env_int("RUN_CONTROL_DISPATCHER_PREFLIGHT_RETRIES", 3))
    backoff_s = max(0.0, _env_float("RUN_CONTROL_DISPATCHER_PREFLIGHT_RETRY_SECONDS", 2.0))
    last: dict[str, Any] = {"ok": False, "reason": "active_queue_read_failed"}
    for attempt in range(1, max_attempts + 1):
        last = evaluate_launch_mode_startup_preflight(cfg)
        if last.get("ok"):
            return last
        reason = str(last.get("reason") or "")
        if reason == "active_queue_present":
            return last
        if not reason.startswith("supabase_"):
            return last
        if attempt < max_attempts:
            log(
                "warning",
                "run_control_dispatcher_preflight_retry",
                worker_id=cfg.worker_id,
                attempt=attempt,
                max_attempts=max_attempts,
                reason=reason,
                error=str(last.get("error") or "")[:200],
            )
            if backoff_s > 0:
                time.sleep(backoff_s * attempt)
    return last


def _collect_completed_dispatch_tasks(
    active_tasks: set[Future[None]],
) -> None:
    completed_tasks = {task for task in active_tasks if task.done()}
    active_tasks.difference_update(completed_tasks)
    for task in completed_tasks:
        task.result()


def _submit_dispatch_task_if_capacity(
    executor: ThreadPoolExecutor,
    active_tasks: set[Future[None]],
    cfg: DispatcherConfig,
    request: dict[str, Any],
) -> bool:
    if len(active_tasks) >= cfg.max_concurrent_subprocesses:
        return False
    active_tasks.add(executor.submit(_handle_claimed_request, cfg, request))
    return True


def initialize_auto_restart_tick_state(
    *,
    worker_id: str,
    now_monotonic: float | None = None,
) -> float:
    """Return the initial cadence marker after consuming any one-shot guard."""

    startup_tick_guard = consume_auto_restart_startup_tick_skip_once()
    if startup_tick_guard.get("consumed"):
        initial_tick_monotonic = (
            now_monotonic if now_monotonic is not None else time.monotonic()
        )
        log(
            "info",
            "auto_restart_startup_tick_skipped",
            worker_id=worker_id,
            one_shot=True,
            token_consumed=True,
            token_cleanup_ok=bool(startup_tick_guard.get("cleanup_ok")),
        )
        return initial_tick_monotonic

    guard_reason = str(startup_tick_guard.get("reason") or "")
    if guard_reason not in {"guard_absent", "guard_not_configured"}:
        log(
            "warning",
            "auto_restart_startup_tick_skip_guard_invalid",
            worker_id=worker_id,
            reason=guard_reason,
        )
    return 0.0


def run_forever(cfg: DispatcherConfig | None = None) -> int:
    cfg = cfg or load_dispatcher_config()
    if not cfg.enabled:
        log("error", "run_control_dispatcher_disabled")
        return 2

    # Recover terminal sessions before startup queue preflight so stale active
    # rows cannot permanently prevent a safe dispatcher restart.
    reconcile_requests_with_terminal_runs(cfg)
    preflight = evaluate_launch_mode_startup_preflight_with_retries(cfg)
    if not preflight.get("ok"):
        reason = str(preflight.get("reason") or "blocked")
        log(
            "error",
            "run_control_dispatcher_launch_preflight_blocked",
            worker_id=cfg.worker_id,
            reason=reason,
            active_count=int(preflight.get("active_count") or 0),
            allow_existing_queue=bool(preflight.get("allow_existing_queue")),
            active_requests=list(preflight.get("requests") or [])[:5],
            error=str(preflight.get("error") or "")[:200],
        )
        if reason.startswith("supabase_"):
            try:
                _heartbeat(
                    cfg,
                    status=f"unhealthy:{reason}",
                    metadata={"preflight_blocked": True, "supabase_reason": reason},
                )
            except Exception as heartbeat_exc:
                log(
                    "warning",
                    "run_control_dispatcher_unhealthy_heartbeat_failed",
                    worker_id=cfg.worker_id,
                    reason=reason,
                    error=str(heartbeat_exc)[:200],
                )
        return 3

    receipt_replay = follow_persistence_receipt_replay.replay_verified_receipts(
        limit=100,
        time_budget_seconds=8.0,
    )
    if not bool(receipt_replay.get("ok")):
        log(
            "error",
            "candidate_local_receipt_replay_blocked_dispatcher_startup",
            device_actions_started=False,
            **receipt_replay,
        )
        return 4
    log(
        "info",
        "candidate_local_receipt_replay_completed_dispatcher_startup",
        device_actions_started=False,
        **receipt_replay,
    )

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
    last_auto_restart_tick = initialize_auto_restart_tick_state(worker_id=cfg.worker_id)
    last_loop_error_key = ""
    last_loop_error_logged_at = 0.0
    consecutive_loop_errors = 0
    active_tasks: set[Future[None]] = set()
    executor = ThreadPoolExecutor(
        max_workers=cfg.max_concurrent_subprocesses,
        thread_name_prefix="run-dispatch",
    )
    try:
        while not stop:
            try:
                _collect_completed_dispatch_tasks(active_tasks)

                reconcile_requests_with_terminal_runs(cfg)

                # Reconcile secondary projections independently from business
                # terminalization.  The bounded durable queue prevents a
                # transient Supabase outage from leaving a session running.
                deferred_projection_outbox.drain(limit=25, time_budget_seconds=4.0)

                now_loop = time.monotonic()
                if should_run_auto_restart_tick(
                    last_tick_monotonic=last_auto_restart_tick,
                    now_monotonic=now_loop,
                ):
                    last_auto_restart_tick = now_loop
                    tick_result = run_auto_restart_dispatcher_tick(
                        worker_id=cfg.worker_id,
                        dispatcher_reliable=consecutive_loop_errors == 0,
                    )
                    if tick_result.get("ok"):
                        log(
                            "info",
                            "auto_restart_dispatcher_tick_observed",
                            worker_id=cfg.worker_id,
                            skipped=bool(tick_result.get("skipped")),
                        )
                    elif tick_result.get("skipped"):
                        log(
                            "info",
                            "auto_restart_dispatcher_tick_skipped",
                            worker_id=cfg.worker_id,
                            reason=str(tick_result.get("reason") or "skipped"),
                        )
                if len(active_tasks) < cfg.max_concurrent_subprocesses:
                    request, _claim_result = _claim_next_dispatch_request(
                        cfg,
                        heartbeat_status="running" if active_tasks else "idle",
                    )
                    if request is not None:
                        _submit_dispatch_task_if_capacity(
                            executor,
                            active_tasks,
                            cfg,
                            request,
                        )
                last_loop_error_key = ""
                consecutive_loop_errors = 0
            except Exception as exc:
                consecutive_loop_errors += 1
                err = str(exc)[:500]
                now = time.monotonic()
                err_key = err[:200]
                if err_key != last_loop_error_key or (now - last_loop_error_logged_at) >= 60.0:
                    log(
                        "error",
                        "run_control_dispatcher_loop_failed",
                        error=err,
                        consecutive_loop_errors=consecutive_loop_errors,
                        max_consecutive_loop_errors=cfg.max_consecutive_loop_errors,
                    )
                    last_loop_error_key = err_key
                    last_loop_error_logged_at = now
                if consecutive_loop_errors >= cfg.max_consecutive_loop_errors:
                    log(
                        "error",
                        "run_control_dispatcher_exit_after_repeated_loop_errors",
                        worker_id=cfg.worker_id,
                        consecutive_loop_errors=consecutive_loop_errors,
                    )
                    return 4
            now = time.monotonic()
            if now - last_heartbeat >= cfg.heartbeat_seconds:
                _heartbeat(
                    cfg,
                    status="running" if active_tasks else "idle",
                    metadata={"active_subprocesses": len(active_tasks)},
                )
                last_heartbeat = now
            # Five seconds leaves a second five-second window for one transient
            # reconciliation retry while meeting the 10-second terminalization
            # target under a healthy control plane.
            time.sleep(min(cfg.poll_seconds, 5.0))
    finally:
        executor.shutdown(wait=True)

    _heartbeat(cfg, status="stopping")
    log("info", "run_control_dispatcher_stopped", worker_id=cfg.worker_id)
    return 0


def main(argv: list[str] | None = None) -> int:
    global _CERTIFIED_RUNTIME_IDENTITY
    args = list(argv or sys.argv[1:])
    machine_json_preflight = bool(
        args
        and args[0] in {"--preflight", "preflight"}
        and "--json" in args
    )
    runtime_identity = resolve_worker_runtime_identity(Path(__file__).resolve().parent)
    _CERTIFIED_RUNTIME_IDENTITY = bind_worker_runtime_identity(
        runtime_identity,
        consumer_pid=os.getpid(),
    )
    export_worker_runtime_identity(_CERTIFIED_RUNTIME_IDENTITY)
    identity_log_context = (
        contextlib.redirect_stdout(sys.stderr)
        if machine_json_preflight
        else contextlib.nullcontext()
    )
    with identity_log_context:
        log(
            "info",
            "worker_runtime_identity_resolved",
            worker_sha=runtime_identity.worker_sha,
            runtime_root=runtime_identity.runtime_root,
            identity_source=runtime_identity.source,
            runtime_root_ok=True,
        )
    if args and args[0] in {"--preflight", "preflight"}:
        cfg = load_dispatcher_config()
        if "--json" in args:
            with contextlib.redirect_stdout(sys.stderr):
                result = evaluate_launch_mode_startup_preflight_with_retries(cfg)
            print(json.dumps(result, default=str, sort_keys=True))
        else:
            result = evaluate_launch_mode_startup_preflight_with_retries(cfg)
            print(result)
        return 0 if result.get("ok") else 3
    if args and args[0] in {"--once", "once"}:
        result = run_once()
        print(result)
        return 0 if result.get("ok") else 1
    return run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
