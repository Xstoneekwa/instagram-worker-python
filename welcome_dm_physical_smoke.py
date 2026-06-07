"""Strict preflight for an isolated physical Welcome DM smoke.

This module is intentionally read-only: it validates that a Welcome DM run can
use the assigned phone/clone without enabling the full account_session flow.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import supabase_client
from assignment_dispatch_resolver import (
    resolve_account_assignment_runtime_context,
    sensitive_log_fields,
)


ACTIVE_RUN_STATUSES = "in.(queued,pending,starting,running,in_progress,active)"
ACTIVE_REQUEST_STATUSES = "in.(queued,claimed,starting,running,in_progress)"
ACTIVE_DM_JOB_STATUSES = "in.(pending,reserved,running)"
ACTIVE_LIVE_VIEW_STATUSES = "in.(starting,active,running)"
RUN_TYPE = "dm_welcome_session_send"
SEND_ONE_RUN_TYPE = "dm_welcome_sender_one_smoke"
REQUIRED_SUPABASE_ENV_KEYS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
SENSITIVE_ENV_KEY_MARKERS = (
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_ANON_KEY",
    "SERVICE_ROLE",
    "API_KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
)


def load_env_file(path: str) -> None:
    """Load simple KEY=VALUE lines without printing or overriding existing env vars."""
    env_path = Path(path)
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def bootstrap_supabase_env() -> tuple[bool, list[str], str | None]:
    """Load FOLLOW_SMOKE_ENV_FILE when set, then verify required Supabase env."""
    env_file = str(os.environ.get("FOLLOW_SMOKE_ENV_FILE") or "").strip()
    if env_file:
        if not os.path.isfile(env_file):
            return False, [], "env_file_not_found"
        try:
            load_env_file(env_file)
        except OSError:
            return False, [], "env_file_invalid"

    missing = [
        key
        for key in REQUIRED_SUPABASE_ENV_KEYS
        if not str(os.environ.get(key) or "").strip()
    ]
    if missing:
        return False, missing, "missing_required_env"
    return True, [], None


def _truthy(raw: Any) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _as_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _rows(table: str, query: dict[str, str]) -> list[dict[str, Any]]:
    value = supabase_client._request_json("GET", table, query=query) or []
    return value if isinstance(value, list) else []


def _row_by_id(table: str, row_id: str) -> dict[str, Any] | None:
    rows = _rows(table, {"select": "*", "id": f"eq.{row_id}", "limit": "1"})
    return rows[0] if rows else None


def _adb_text(argv: list[str], *, timeout_s: float = 12.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)
    except Exception as exc:
        return 99, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def resolve_welcome_smoke_assignment(
    account_id: str,
    *,
    ig_account_settings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve assignment device/package for isolated Welcome smoke.

    Smoke preflight reuses the account assignment without enforcing schedule
    windows and without launching account_session.
    """
    assignment = resolve_account_assignment_runtime_context(
        account_id,
        RUN_TYPE,
        require_assignment=True,
        enforce_window=False,
    )
    if not bool(assignment.get("assignment_found")):
        return assignment

    enriched = dict(assignment)
    package_name = str(enriched.get("package_name") or "").strip()
    settings = ig_account_settings[0] if ig_account_settings else {}
    settings_package = str(settings.get("app_package") or "").strip()
    if package_name:
        enriched["package_name_source"] = "assignment.app_instance"
    elif settings_package:
        enriched["package_name"] = settings_package
        enriched["package_name_source"] = "ig_account_settings.app_package"
    return enriched


def _collect_device_checks(
    assignment: dict[str, Any],
    *,
    include_device: bool,
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    if not include_device:
        return checks

    adb_serial = str(assignment.get("adb_serial") or "").strip()
    package_name = str(assignment.get("package_name") or "").strip()
    code, stdout, stderr = _adb_text(["adb", "devices"])
    checks["adb_devices"] = {"exit_code": code, "stdout": stdout, "stderr": stderr}
    if not adb_serial:
        return checks

    if package_name:
        code, stdout, stderr = _adb_text(
            ["adb", "-s", adb_serial, "shell", "pm", "path", package_name]
        )
        checks["package_installed"] = {"exit_code": code, "stdout": stdout, "stderr": stderr}
    code, stdout, stderr = _adb_text(
        [
            "adb",
            "-s",
            adb_serial,
            "shell",
            "settings",
            "get",
            "secure",
            "default_input_method",
        ]
    )
    checks["current_ime"] = {"exit_code": code, "stdout": stdout, "stderr": stderr}
    return checks


def collect_welcome_physical_smoke_state(
    username: str,
    *,
    environ: dict[str, str] | None = None,
    include_device: bool = True,
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    account_rows = _rows("ig_accounts", {"select": "id,username", "username": f"eq.{username}", "limit": "2"})
    account = account_rows[0] if len(account_rows) == 1 else {}
    account_id = str(account.get("id") or "")

    state: dict[str, Any] = {
        "username": username,
        "account_rows_count": len(account_rows),
        "account": account,
        "account_id": account_id,
        "env": {
            "WELCOME_DM_REAL_SEND_ENABLED": env.get("WELCOME_DM_REAL_SEND_ENABLED"),
            "OUTREACH_DM_REAL_SEND_ENABLED": env.get("OUTREACH_DM_REAL_SEND_ENABLED"),
            "DM_SENDER_REAL_SEND_ENABLED": env.get("DM_SENDER_REAL_SEND_ENABLED"),
        },
    }
    if not account_id:
        return state

    state["client_status"] = _rows(
        "client_instagram_accounts",
        {
            "select": "login_status,provisioning_status,onboarding_status",
            "account_id": f"eq.{account_id}",
            "limit": "1",
        },
    )
    state["ig_account_settings"] = _rows(
        "ig_account_settings",
        {
            "select": "follow_enabled,like_enabled,mute_posts_after_follow,mute_stories_after_follow,welcome_dm_enabled,cold_dm_enabled,unfollow_enabled,app_package",
            "account_id": f"eq.{account_id}",
            "limit": "1",
        },
    )
    state["dm_settings"] = _rows(
        "ig_account_dm_settings",
        {
            "select": "welcome_enabled,outreach_enabled,welcome_per_session_limit,welcome_per_day_limit,total_dm_per_day_limit,welcome_template_id,welcome_baseline_completed_at",
            "account_id": f"eq.{account_id}",
            "limit": "1",
        },
    )
    state["active_runs"] = _rows(
        "ig_runs",
        {"select": "id,status", "account_id": f"eq.{account_id}", "status": ACTIVE_RUN_STATUSES, "limit": "10"},
    )
    state["active_requests"] = _rows(
        "account_run_requests",
        {
            "select": "id,status,requested_run_type,run_id",
            "account_id": f"eq.{account_id}",
            "status": ACTIVE_REQUEST_STATUSES,
            "limit": "10",
        },
    )
    state["active_live_views"] = _rows(
        "live_view_sessions",
        {
            "select": "id,status,device_id,app_instance_id",
            "account_id": f"eq.{account_id}",
            "status": ACTIVE_LIVE_VIEW_STATUSES,
            "limit": "10",
        },
    )
    state["welcome_jobs"] = _rows(
        "ig_dm_jobs",
        {
            "select": "id,status,dm_type,recipient_username,message_body,template_id,source,attempts,max_attempts,reserved_by,created_at",
            "account_id": f"eq.{account_id}",
            "dm_type": "eq.welcome",
            "status": ACTIVE_DM_JOB_STATUSES,
            "limit": "10",
        },
    )
    state["outreach_jobs"] = _rows(
        "ig_dm_jobs",
        {
            "select": "id,status,dm_type,recipient_username,attempts,reserved_by,created_at",
            "account_id": f"eq.{account_id}",
            "dm_type": "eq.outreach",
            "status": ACTIVE_DM_JOB_STATUSES,
            "limit": "10",
        },
    )
    dm_settings = state["dm_settings"][0] if state["dm_settings"] else {}
    template_id = str(dm_settings.get("welcome_template_id") or "")
    state["welcome_template"] = (
        _rows(
            "ig_dm_templates",
            {
                "select": "id,account_id,template_type,name,body,active,is_default",
                "id": f"eq.{template_id}",
                "account_id": f"eq.{account_id}",
                "limit": "1",
            },
        )
        if template_id
        else []
    )
    state["assignment"] = resolve_welcome_smoke_assignment(
        account_id,
        ig_account_settings=state.get("ig_account_settings") or [],
    )
    state.update(
        _collect_device_checks(
            state.get("assignment") or {},
            include_device=include_device,
        )
    )
    return state


def validate_welcome_physical_smoke_state(
    state: dict[str, Any],
    *,
    mode: str,
) -> tuple[bool, list[str], dict[str, Any]]:
    mode_norm = str(mode or "dry-run").strip().lower()
    reasons: list[str] = []
    summary: dict[str, Any] = {
        "mode": mode_norm,
        "run_type": RUN_TYPE,
        "account_id": state.get("account_id"),
        "recipient_username": None,
        "job_id": None,
        "template_id": None,
        "template_body": None,
    }

    def stop_if(condition: bool, reason: str) -> None:
        if condition:
            reasons.append(reason)

    stop_if(int(state.get("account_rows_count") or 0) != 1, "account_not_found_or_ambiguous")
    account_id = str(state.get("account_id") or "")
    stop_if(not account_id, "account_id_missing")

    client = (state.get("client_status") or [{}])[0] if state.get("client_status") else {}
    stop_if(str(client.get("login_status") or "") != "connected", "login_status_not_connected")
    stop_if(str(client.get("provisioning_status") or "") != "ready", "provisioning_status_not_ready")
    stop_if(str(client.get("onboarding_status") or "") != "ready", "onboarding_status_not_ready")

    assignment = state.get("assignment") or {}
    stop_if(not bool(assignment.get("assignment_found")), "assignment_not_resolved")
    stop_if(str(assignment.get("reason") or "") != "assignment_resolved", "assignment_not_resolved")
    stop_if(not str(assignment.get("adb_serial") or ""), "assignment_device_missing")
    stop_if(not str(assignment.get("package_name") or ""), "assignment_package_missing")

    ig_settings = (state.get("ig_account_settings") or [{}])[0] if state.get("ig_account_settings") else {}
    stop_if(bool(ig_settings.get("follow_enabled")), "follow_enabled")
    stop_if(bool(ig_settings.get("like_enabled")), "like_enabled")
    stop_if(bool(ig_settings.get("mute_posts_after_follow")), "mute_posts_enabled")
    stop_if(bool(ig_settings.get("mute_stories_after_follow")), "mute_stories_enabled")
    stop_if(bool(ig_settings.get("cold_dm_enabled")), "outreach_enabled")
    stop_if(bool(ig_settings.get("unfollow_enabled")), "unfollow_enabled")
    stop_if(not bool(ig_settings.get("welcome_dm_enabled")), "welcome_dm_disabled")

    dm_settings = (state.get("dm_settings") or [{}])[0] if state.get("dm_settings") else {}
    stop_if(not bool(dm_settings.get("welcome_enabled")), "dm_welcome_disabled")
    stop_if(bool(dm_settings.get("outreach_enabled")), "dm_outreach_enabled")
    stop_if(_as_int(dm_settings.get("welcome_per_session_limit"), 0) != 1, "welcome_per_session_limit_not_1")
    template_id = str(dm_settings.get("welcome_template_id") or "")
    summary["template_id"] = template_id or None
    stop_if(not template_id, "welcome_template_missing")

    templates = list(state.get("welcome_template") or [])
    template = templates[0] if len(templates) == 1 else {}
    stop_if(len(templates) != 1, "welcome_template_not_found")
    stop_if(template and not bool(template.get("active")), "welcome_template_inactive")
    stop_if(template and str(template.get("template_type") or "") != "welcome", "welcome_template_not_welcome")
    template_body = str(template.get("body") or "")
    summary["template_body"] = template_body or None
    stop_if(not template_body.strip(), "welcome_template_body_empty")

    stop_if(bool(state.get("active_runs")), "active_ig_run_exists")
    stop_if(bool(state.get("active_requests")), "active_account_run_request_exists")
    stop_if(bool(state.get("active_live_views")), "active_live_view_session_exists")
    stop_if(bool(state.get("outreach_jobs")), "active_outreach_job_exists")

    jobs = list(state.get("welcome_jobs") or [])
    stop_if(len(jobs) == 0, "no_pending_welcome_job")
    stop_if(len(jobs) > 1, "multiple_pending_welcome_jobs")
    if len(jobs) == 1:
        job = jobs[0]
        summary["job_id"] = str(job.get("id") or "") or None
        summary["recipient_username"] = str(job.get("recipient_username") or "") or None
        stop_if(str(job.get("status") or "") != "pending", "welcome_job_not_pending")
        stop_if(str(job.get("dm_type") or "") != "welcome", "welcome_job_wrong_type")
        stop_if(not str(job.get("recipient_username") or "").strip(), "welcome_job_missing_recipient")
        stop_if(not str(job.get("message_body") or "").strip(), "welcome_job_message_empty")

    manual_smoke_baseline_allowed = _manual_smoke_baseline_bypass_allowed(
        state=state,
        ig_settings=ig_settings,
        dm_settings=dm_settings,
        template=template,
        jobs=jobs,
    )
    if not str(dm_settings.get("welcome_baseline_completed_at") or ""):
        stop_if(
            not manual_smoke_baseline_allowed,
            "welcome_baseline_not_completed",
        )

    env = state.get("env") or {}
    welcome_real = _truthy(env.get("WELCOME_DM_REAL_SEND_ENABLED"))
    outreach_real = _truthy(env.get("OUTREACH_DM_REAL_SEND_ENABLED"))
    legacy_real = _truthy(env.get("DM_SENDER_REAL_SEND_ENABLED"))
    if mode_norm == "real-send":
        stop_if(not welcome_real, "welcome_real_send_disabled")
    else:
        stop_if(welcome_real, "welcome_real_send_enabled_during_dry_run")
    stop_if(outreach_real, "outreach_real_send_enabled")
    stop_if(legacy_real, "legacy_dm_real_send_enabled")

    current_ime = state.get("current_ime")
    if isinstance(current_ime, dict):
        ime = str(current_ime.get("stdout") or "").strip()
        stop_if("com.android.adbkeyboard/.AdbIME" not in ime, "adbkeyboard_not_active")
    package = state.get("package_installed") or {}
    if package:
        stop_if(int(package.get("exit_code") or 0) != 0 or "package:" not in str(package.get("stdout") or ""), "package_not_installed")

    return not reasons, reasons, summary


def validate_send_one_smoke_state(
    state: dict[str, Any],
    *,
    job_id: str,
) -> tuple[bool, list[str], dict[str, Any], dict[str, Any] | None]:
    ok, reasons, summary = validate_welcome_physical_smoke_state(state, mode="real-send")
    summary = dict(summary)
    summary["mode"] = "send-one"
    summary["run_type"] = SEND_ONE_RUN_TYPE
    expected_job_id = str(job_id or "").strip()
    if not expected_job_id:
        reasons.append("missing_job_id")
        return False, reasons, summary, None

    jobs = list(state.get("welcome_jobs") or [])
    job = jobs[0] if len(jobs) == 1 else None
    if not isinstance(job, dict) or str(job.get("id") or "") != expected_job_id:
        reasons.append("job_id_mismatch")
        job = None
    else:
        summary["job_id"] = expected_job_id
        summary["recipient_username"] = str(job.get("recipient_username") or "") or None
        summary["message_body"] = str(job.get("message_body") or "") or None

    return not reasons, reasons, summary, job


def attach_send_one_job_state(state: dict[str, Any], *, job_id: str) -> dict[str, Any]:
    """For send-one, include the selected job even when it is no longer pending."""
    jid = str(job_id or "").strip()
    if not jid:
        return state
    jobs = list(state.get("welcome_jobs") or [])
    if any(str(job.get("id") or "") == jid for job in jobs if isinstance(job, dict)):
        return state
    job = _row_by_id("ig_dm_jobs", jid)
    if not isinstance(job, dict):
        return state
    if str(job.get("account_id") or "") != str(state.get("account_id") or ""):
        return state
    if str(job.get("dm_type") or "") != "welcome":
        return state
    updated = dict(state)
    updated["welcome_jobs"] = jobs + [job]
    return updated


def execute_send_one_smoke(
    *,
    state: dict[str, Any],
    job: dict[str, Any],
    account_username: str,
) -> tuple[int, dict[str, Any]]:
    import config
    import uiautomator2 as u2
    from device import app_start
    from dm_sender_engine import run_dm_sender_send
    from logs import log

    assignment = state.get("assignment") or {}
    adb_serial = str(assignment.get("adb_serial") or "").strip()
    package_name = str(assignment.get("package_name") or "").strip()
    account_id = str(state.get("account_id") or "").strip()
    if not adb_serial:
        return 10, {"sender_status": "blocked", "failure_reason": "assignment_device_missing"}
    if not package_name:
        return 10, {"sender_status": "blocked", "failure_reason": "assignment_package_missing"}

    config.INSTAGRAM_PACKAGE = package_name
    d = u2.connect(adb_serial)
    run_id = f"welcome_sender_one_smoke_{int(time.time())}"
    log(
        "info",
        "welcome_dm_sender_one_smoke_started",
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        job_id=str(job.get("id") or ""),
        recipient_username=str(job.get("recipient_username") or ""),
        package_name=package_name,
        adb_serial_suffix=adb_serial[-4:] if adb_serial else None,
    )
    app_start(d, package_name, wait=True)
    code, sender_summary = run_dm_sender_send(
        d,
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        max_jobs=1,
        dm_type="welcome",
        prepared_jobs=[dict(job)],
        settings_override=(state.get("dm_settings") or [{}])[0],
    )
    final_job = _row_by_id("ig_dm_jobs", str(job.get("id") or "")) or {}
    log(
        "info",
        "welcome_dm_sender_one_smoke_completed",
        account_id=account_id,
        run_id=run_id,
        job_id=str(job.get("id") or ""),
        exit_code=code,
        sender_status=sender_summary.get("sender_status"),
        final_job_status=final_job.get("status"),
        sent_at=final_job.get("sent_at"),
    )
    return int(code), {
        "run_id": run_id,
        "sender_summary": sender_summary,
        "final_job": _safe_job_summary(final_job),
    }


def _manual_smoke_baseline_bypass_allowed(
    *,
    state: dict[str, Any],
    ig_settings: dict[str, Any],
    dm_settings: dict[str, Any],
    template: dict[str, Any],
    jobs: list[dict[str, Any]],
) -> bool:
    """Allow missing baseline only for one explicit manual Welcome smoke job."""
    if bool(ig_settings.get("follow_enabled")):
        return False
    if bool(ig_settings.get("like_enabled")):
        return False
    if bool(ig_settings.get("mute_posts_after_follow")):
        return False
    if bool(ig_settings.get("mute_stories_after_follow")):
        return False
    if bool(ig_settings.get("cold_dm_enabled")) or bool(ig_settings.get("unfollow_enabled")):
        return False
    if not bool(ig_settings.get("welcome_dm_enabled")):
        return False
    if not bool(dm_settings.get("welcome_enabled")):
        return False
    if bool(dm_settings.get("outreach_enabled")):
        return False
    if _as_int(dm_settings.get("welcome_per_session_limit"), 0) != 1:
        return False
    if not template or str(template.get("template_type") or "") != "welcome":
        return False
    if not bool(template.get("active")) or not str(template.get("body") or "").strip():
        return False
    if state.get("active_runs") or state.get("active_requests") or state.get("active_live_views"):
        return False
    if state.get("outreach_jobs"):
        return False
    if len(jobs) != 1:
        return False
    job = jobs[0]
    if str(job.get("status") or "") != "pending":
        return False
    if str(job.get("dm_type") or "") != "welcome":
        return False
    if str(job.get("source") or "") != "manual":
        return False
    if not str(job.get("recipient_username") or "").strip():
        return False
    if not str(job.get("message_body") or "").strip():
        return False
    return True


def _safe_job_summary(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job.get("id"),
        "status": job.get("status"),
        "dm_type": job.get("dm_type"),
        "recipient_username": job.get("recipient_username"),
        "message_body": job.get("message_body"),
        "template_id": job.get("template_id"),
        "source": job.get("source"),
        "attempts": job.get("attempts"),
        "reserved_by": job.get("reserved_by"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "sent_at": job.get("sent_at"),
        "last_error": job.get("last_error"),
        "skip_reason": job.get("skip_reason"),
    }


def _emit_payload(
    *,
    ok: bool,
    reasons: list[str],
    summary: dict[str, Any],
    state: dict[str, Any] | None = None,
    missing: list[str] | None = None,
    execution: dict[str, Any] | None = None,
    json_mode: bool,
    mode: str,
) -> int:
    payload: dict[str, Any] = {
        "ok": ok,
        "reasons": reasons,
        "summary": summary,
        "state": _redacted_state(state or {}),
    }
    if missing:
        payload["missing"] = missing
    if execution is not None:
        payload["execution"] = execution
    if json_mode:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif ok:
        print(
            f"OK mode={mode} run_type={RUN_TYPE} recipient={summary.get('recipient_username')}"
        )
    else:
        print("STOP reason=" + ",".join(reasons))
    return 0 if ok else 10


def _output_contains_sensitive_values(output: str, environ: dict[str, str] | None = None) -> bool:
    env = dict(os.environ if environ is None else environ)
    for key, value in env.items():
        if not value:
            continue
        upper = key.upper()
        if any(marker in upper for marker in SENSITIVE_ENV_KEY_MARKERS):
            if value in output:
                return True
    return False


def _redacted_state(state: dict[str, Any]) -> dict[str, Any]:
    safe = dict(state)
    if safe.get("env"):
        safe["env"] = {
            key: ("<set>" if value not in (None, "") else "")
            for key, value in dict(safe["env"]).items()
        }
    if isinstance(safe.get("assignment"), dict):
        assignment = dict(safe["assignment"])
        safe["assignment"] = {
            **sensitive_log_fields(assignment, include_sensitive=False),
            "package_name": assignment.get("package_name"),
            "app_instance_label": assignment.get("app_instance_label"),
        }
    return safe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight isolated physical Welcome DM smoke.")
    parser.add_argument("username")
    parser.add_argument("--mode", choices=("dry-run", "real-send", "send-one"), default="dry-run")
    parser.add_argument("--job-id", default="", help="Required for --mode send-one.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-device-check", action="store_true")
    args = parser.parse_args(argv)

    summary: dict[str, Any] = {
        "mode": args.mode,
        "run_type": RUN_TYPE,
        "account_id": None,
        "recipient_username": None,
        "job_id": None,
        "template_id": None,
        "template_body": None,
    }

    env_ok, missing_keys, env_reason = bootstrap_supabase_env()
    if not env_ok:
        reasons = [str(env_reason or "missing_required_env")]
        return _emit_payload(
            ok=False,
            reasons=reasons,
            summary=summary,
            missing=missing_keys or None,
            json_mode=bool(args.json),
            mode=args.mode,
        )

    try:
        state = collect_welcome_physical_smoke_state(
            args.username,
            include_device=not args.skip_device_check,
        )
    except RuntimeError as exc:
        message = str(exc)
        if "SUPABASE_URL is not set" in message:
            return _emit_payload(
                ok=False,
                reasons=["missing_required_env"],
                summary=summary,
                missing=["SUPABASE_URL"],
                json_mode=bool(args.json),
                mode=args.mode,
            )
        if "SUPABASE_SERVICE_ROLE_KEY is not set" in message:
            return _emit_payload(
                ok=False,
                reasons=["missing_required_env"],
                summary=summary,
                missing=["SUPABASE_SERVICE_ROLE_KEY"],
                json_mode=bool(args.json),
                mode=args.mode,
            )
        return _emit_payload(
            ok=False,
            reasons=["preflight_runtime_error"],
            summary=summary,
            json_mode=bool(args.json),
            mode=args.mode,
        )

    if args.mode == "send-one":
        state = attach_send_one_job_state(state, job_id=args.job_id)
        ok, reasons, summary, job = validate_send_one_smoke_state(
            state,
            job_id=args.job_id,
        )
        if not ok or not job:
            return _emit_payload(
                ok=False,
                reasons=reasons,
                summary=summary,
                state=state,
                json_mode=bool(args.json),
                mode=args.mode,
            )
        code, execution = execute_send_one_smoke(
            state=state,
            job=job,
            account_username=args.username,
        )
        return _emit_payload(
            ok=code == 0,
            reasons=[] if code == 0 else ["send_one_failed"],
            summary=summary,
            state=state,
            execution=execution,
            json_mode=bool(args.json),
            mode=args.mode,
        )

    ok, reasons, summary = validate_welcome_physical_smoke_state(state, mode=args.mode)
    return _emit_payload(
        ok=ok,
        reasons=reasons,
        summary=summary,
        state=state,
        json_mode=bool(args.json),
        mode=args.mode,
    )


if __name__ == "__main__":
    raise SystemExit(main())
