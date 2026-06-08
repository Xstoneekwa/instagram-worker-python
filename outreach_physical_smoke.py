"""Strict preflight for an isolated physical Outreach DM smoke.

This module is intentionally read-only for now: it validates that an Outreach
DM run can use the assigned phone/clone without launching the sender.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import supabase_client
from assignment_dispatch_resolver import (
    resolve_account_assignment_runtime_context,
    sensitive_log_fields,
)
from dm_template_renderer import (
    find_template_tokens,
    has_unresolved_template_tokens,
    render_dm_template,
)


ACTIVE_RUN_STATUSES = "in.(queued,pending,starting,running,in_progress,active)"
ACTIVE_REQUEST_STATUSES = "in.(queued,claimed,starting,running,in_progress)"
ACTIVE_LIVE_VIEW_STATUSES = "in.(starting,active,running)"
RUN_TYPE = "outreach_session"
REQUIRED_SUPABASE_ENV_KEYS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
ALLOWED_OUTREACH_JOB_SOURCES = {"n8n", "dashboard", "campaign", "manual"}
SAFE_JOB_METADATA_KEYS = (
    "external_request_id",
    "import_id",
    "created_by",
    "created_for",
    "source_context",
    "campaign_name",
    "note",
)
JOB_AUDIT_METADATA_KEYS = (
    "external_request_id",
    "import_id",
    "created_by",
    "created_for",
    "source_context",
)
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


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


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


def resolve_outreach_smoke_assignment(
    account_id: str,
    *,
    ig_account_settings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve assignment device/package for isolated Outreach smoke."""
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


def _resolve_outreach_template(
    account_id: str,
    dm_settings: dict[str, Any],
) -> list[dict[str, Any]]:
    template_id = str(dm_settings.get("default_outreach_template_id") or "").strip()
    if template_id:
        return _rows(
            "ig_dm_templates",
            {
                "select": "id,account_id,template_type,name,body,active,is_default",
                "id": f"eq.{template_id}",
                "account_id": f"eq.{account_id}",
                "limit": "1",
            },
        )
    return _rows(
        "ig_dm_templates",
        {
            "select": "id,account_id,template_type,name,body,active,is_default",
            "account_id": f"eq.{account_id}",
            "template_type": "eq.outreach",
            "active": "eq.true",
            "is_default": "eq.true",
            "limit": "1",
        },
    )


def _render_context_for_job(
    *,
    job: dict[str, Any],
    account_username: str,
) -> dict[str, Any]:
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    return {
        "recipient_username": str(job.get("recipient_username") or ""),
        "recipient_name": str(
            job.get("recipient_name")
            or job.get("display_name")
            or job.get("full_name")
            or metadata.get("recipient_name")
            or metadata.get("display_name")
            or metadata.get("full_name")
            or ""
        ),
        "account_username": account_username,
    }


def _safe_text_preview(value: Any, *, max_chars: int = 160) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _safe_job_metadata(metadata: Any) -> dict[str, str]:
    if not isinstance(metadata, dict):
        return {}
    out: dict[str, str] = {}
    for key in SAFE_JOB_METADATA_KEYS:
        raw = metadata.get(key)
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            out[key] = value[:500]
    return out


def _has_job_audit_metadata(metadata: Any) -> bool:
    safe = _safe_job_metadata(metadata)
    return any(str(safe.get(key) or "").strip() for key in JOB_AUDIT_METADATA_KEYS)


def collect_outreach_physical_smoke_state(
    username: str,
    *,
    environ: dict[str, str] | None = None,
    include_device: bool = True,
    job_id: str = "",
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
            "OUTREACH_DM_REAL_SEND_ENABLED": env.get("OUTREACH_DM_REAL_SEND_ENABLED"),
            "WELCOME_DM_REAL_SEND_ENABLED": env.get("WELCOME_DM_REAL_SEND_ENABLED"),
            "DM_SENDER_REAL_SEND_ENABLED": env.get("DM_SENDER_REAL_SEND_ENABLED"),
            "ACCOUNT_ASSIGNMENT_DISPATCH_RUN_TYPES": env.get("ACCOUNT_ASSIGNMENT_DISPATCH_RUN_TYPES"),
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
            "select": "welcome_enabled,outreach_enabled,default_outreach_template_id,outreach_per_session_limit,outreach_per_day_limit,total_dm_per_day_limit,outreach_skip_if_existing_thread",
            "account_id": f"eq.{account_id}",
            "limit": "1",
        },
    )
    state["active_runs"] = _rows(
        "ig_runs",
        {"select": "id,status,created_at", "account_id": f"eq.{account_id}", "status": ACTIVE_RUN_STATUSES, "limit": "10"},
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
    state["dm_jobs_reserved_running"] = _rows(
        "ig_dm_jobs",
        {
            "select": "id,status,dm_type,recipient_username,reserved_by,started_at,created_at",
            "account_id": f"eq.{account_id}",
            "status": "in.(reserved,running)",
            "limit": "20",
        },
    )
    state["outreach_jobs"] = _rows(
        "ig_dm_jobs",
        {
            "select": "id,status,dm_type,recipient_username,message_body,template_id,source,campaign_id,attempts,max_attempts,reserved_by,created_at,sent_at,finished_at,skip_reason,last_error,idempotency_key,metadata",
            "account_id": f"eq.{account_id}",
            "dm_type": "eq.outreach",
            "status": "eq.pending",
            "limit": "20",
        },
    )
    state["welcome_jobs"] = _rows(
        "ig_dm_jobs",
        {
            "select": "id,status,dm_type,recipient_username,created_at",
            "account_id": f"eq.{account_id}",
            "dm_type": "eq.welcome",
            "status": "eq.pending",
            "limit": "20",
        },
    )
    state["counter_today"] = _rows(
        "ig_account_dm_counters",
        {
            "select": "counter_date,outreach_sent_count,total_dm_sent_count,welcome_sent_count,outreach_skipped_count,failed_count",
            "account_id": f"eq.{account_id}",
            "counter_date": f"eq.{_today_utc()}",
            "limit": "1",
        },
    )

    dm_settings = state["dm_settings"][0] if state["dm_settings"] else {}
    state["outreach_template"] = _resolve_outreach_template(account_id, dm_settings)
    state["assignment"] = resolve_outreach_smoke_assignment(
        account_id,
        ig_account_settings=state.get("ig_account_settings") or [],
    )
    jid = str(job_id or "").strip()
    state["selected_job"] = _row_by_id("ig_dm_jobs", jid) if jid else None
    state["recent_sent_outreach_jobs"] = _rows(
        "ig_dm_jobs",
        {
            "select": "id,status,dm_type,recipient_username,sent_at,finished_at",
            "account_id": f"eq.{account_id}",
            "dm_type": "eq.outreach",
            "status": "eq.sent",
            "order": "sent_at.desc.nullslast",
            "limit": "50",
        },
    )
    state.update(
        _collect_device_checks(
            state.get("assignment") or {},
            include_device=include_device,
        )
    )
    return state


def validate_outreach_physical_smoke_state(
    state: dict[str, Any],
    *,
    mode: str,
    job_id: str = "",
) -> tuple[bool, list[str], dict[str, Any]]:
    mode_norm = str(mode or "dry-run").strip().lower()
    expected_job_id = str(job_id or "").strip()
    reasons: list[str] = []
    summary: dict[str, Any] = {
        "mode": mode_norm,
        "run_type": RUN_TYPE,
        "account_id": state.get("account_id"),
        "recipient_username": None,
        "job_id": expected_job_id or None,
        "job_source": None,
        "job_metadata": {},
        "job_created_by": None,
        "job_created_for": None,
        "job_created_from": None,
        "job_external_request_id": None,
        "job_import_id": None,
        "job_campaign_id": None,
        "template_id": None,
        "job_template_id": None,
        "template_body": None,
        "template_body_preview": None,
        "job_message_body_preview": None,
        "pending_outreach_job_ids": [],
        "pending_outreach_recipients": [],
        "pending_outreach_sources": [],
        "pending_outreach_metadata": [],
        "template_used_variables": [],
        "template_fallbacks_used": [],
        "template_unresolved_tokens": [],
        "job_message_unresolved_tokens": [],
        "pending_outreach_jobs_count": len(state.get("outreach_jobs") or []),
        "pending_welcome_jobs_count": len(state.get("welcome_jobs") or []),
        "dm_jobs_reserved_running_count": len(state.get("dm_jobs_reserved_running") or []),
        "max_jobs_effective": 0,
        "real_send_execution_blocked": mode_norm in {"real-send", "send-one"},
    }

    def stop_if(condition: bool, reason: str) -> None:
        if condition and reason not in reasons:
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
    stop_if(str(assignment.get("run_type") or RUN_TYPE) != RUN_TYPE, "assignment_run_type_not_outreach_session")
    stop_if(not str(assignment.get("adb_serial") or ""), "assignment_device_missing")
    stop_if(not str(assignment.get("package_name") or ""), "assignment_package_missing")

    ig_settings = (state.get("ig_account_settings") or [{}])[0] if state.get("ig_account_settings") else {}
    summary["legacy_cold_dm_enabled"] = bool(ig_settings.get("cold_dm_enabled"))
    summary["non_outreach_flow_launch_block"] = {
        "run_type": RUN_TYPE,
        "welcome_real_send_enabled": _truthy((state.get("env") or {}).get("WELCOME_DM_REAL_SEND_ENABLED")),
        "legacy_dm_sender_real_send_enabled": _truthy((state.get("env") or {}).get("DM_SENDER_REAL_SEND_ENABLED")),
    }

    dm_settings = (state.get("dm_settings") or [{}])[0] if state.get("dm_settings") else {}
    stop_if(not bool(dm_settings.get("outreach_enabled")), "outreach_disabled")
    session_limit = _as_int(dm_settings.get("outreach_per_session_limit"), 0)
    day_limit = _as_int(dm_settings.get("outreach_per_day_limit"), 0)
    total_day_limit = _as_int(dm_settings.get("total_dm_per_day_limit"), 0)
    stop_if(session_limit <= 0, "outreach_per_session_limit_insufficient")
    stop_if(day_limit <= 0, "outreach_per_day_limit_insufficient")
    stop_if(total_day_limit <= 0, "total_dm_per_day_limit_insufficient")

    templates = list(state.get("outreach_template") or [])
    template = templates[0] if len(templates) == 1 else {}
    template_id = str(template.get("id") or dm_settings.get("default_outreach_template_id") or "")
    template_body = str(template.get("body") or "")
    summary["template_id"] = template_id or None
    summary["template_body"] = template_body or None
    summary["template_body_preview"] = _safe_text_preview(template_body)
    stop_if(len(templates) != 1, "outreach_template_not_found")
    stop_if(template and not bool(template.get("active")), "outreach_template_inactive")
    stop_if(template and str(template.get("template_type") or "") != "outreach", "outreach_template_not_outreach")
    stop_if(not template_body.strip(), "outreach_template_body_empty")
    template_tokens = find_template_tokens(template_body)
    unknown_template_tokens = [name for name in template_tokens if name not in {"username", "name", "account_username"}]
    if unknown_template_tokens:
        summary["template_unresolved_tokens"] = unknown_template_tokens
        reasons.append("outreach_template_unknown_variable")

    stop_if(bool(state.get("active_runs")), "active_ig_run_exists")
    stop_if(bool(state.get("active_requests")), "active_account_run_request_exists")
    stop_if(bool(state.get("active_live_views")), "active_live_view_session_exists")
    stop_if(bool(state.get("dm_jobs_reserved_running")), "dm_job_reserved_or_running_exists")

    jobs = list(state.get("outreach_jobs") or [])
    selected_job = state.get("selected_job") if isinstance(state.get("selected_job"), dict) else None
    if expected_job_id and selected_job:
        selected_status = str(selected_job.get("status") or "")
        stop_if(str(selected_job.get("account_id") or account_id) != account_id, "job_account_mismatch")
        stop_if(str(selected_job.get("dm_type") or "") != "outreach", "outreach_job_wrong_type")
        stop_if(selected_status != "pending", "outreach_job_not_pending")
        stop_if(selected_status in {"reserved", "running"}, "outreach_job_reserved_or_running")
        stop_if(selected_status in {"sent", "skipped", "failed"}, "outreach_job_terminal")
        if selected_status == "pending" and str(selected_job.get("dm_type") or "") == "outreach":
            jobs = [selected_job]
    elif expected_job_id:
        reasons.append("job_id_not_found")

    stop_if(len(jobs) == 0, "no_pending_outreach_job")

    if jobs:
        all_job_tokens: list[str] = []
        for idx, job in enumerate(jobs):
            metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
            safe_metadata = _safe_job_metadata(metadata)
            job_source = str(job.get("source") or "").strip().lower()
            job_id = str(job.get("id") or "")
            recipient = str(job.get("recipient_username") or "").strip()
            if idx == 0:
                summary["job_id"] = job_id or summary["job_id"]
                summary["recipient_username"] = recipient or None
                summary["job_source"] = job_source or None
                summary["job_metadata"] = safe_metadata
                summary["job_created_by"] = safe_metadata.get("created_by") or None
                summary["job_created_for"] = safe_metadata.get("created_for") or None
                summary["job_created_from"] = safe_metadata.get("source_context") or None
                summary["job_external_request_id"] = safe_metadata.get("external_request_id") or None
                summary["job_import_id"] = safe_metadata.get("import_id") or None
                summary["job_campaign_id"] = str(job.get("campaign_id") or "") or None
                summary["job_template_id"] = str(job.get("template_id") or "") or None
                summary["job_message_body_preview"] = _safe_text_preview(job.get("message_body"))
            summary["pending_outreach_job_ids"].append(job_id)
            summary["pending_outreach_recipients"].append(recipient)
            summary["pending_outreach_sources"].append(job_source)
            summary["pending_outreach_metadata"].append(safe_metadata)
            stop_if(str(job.get("status") or "") != "pending", "outreach_job_not_pending")
            stop_if(str(job.get("dm_type") or "") != "outreach", "outreach_job_wrong_type")
            stop_if(not recipient, "outreach_job_missing_recipient")
            if job_source not in ALLOWED_OUTREACH_JOB_SOURCES:
                reasons.append("outreach_job_source_not_allowed")
            if not _has_job_audit_metadata(metadata):
                reasons.append("outreach_job_missing_audit_metadata")
            job_message = str(job.get("message_body") or "")
            stop_if(not job_message.strip(), "outreach_job_message_empty")
            job_tokens = find_template_tokens(job_message)
            if job_tokens:
                all_job_tokens.extend(job_tokens)
                reasons.append("outreach_job_unresolved_template_token")
                if any(name not in {"username", "name", "account_username"} for name in job_tokens):
                    reasons.append("outreach_job_unknown_template_variable")
            if template_body.strip() and not unknown_template_tokens:
                rendered = render_dm_template(
                    template_body,
                    _render_context_for_job(job=job, account_username=str(state.get("username") or "")),
                )
                if idx == 0:
                    summary["template_used_variables"] = rendered.used_variables
                    summary["template_fallbacks_used"] = rendered.fallbacks_used
                    summary["template_unresolved_tokens"] = rendered.unresolved_tokens
                if not rendered.ok:
                    reasons.append("outreach_template_render_failed")
                elif has_unresolved_template_tokens(rendered.rendered_body):
                    reasons.append("outreach_template_unresolved_token")
        if all_job_tokens:
            summary["job_message_unresolved_tokens"] = list(dict.fromkeys(all_job_tokens))
        sent_recipients = {
            str(row.get("recipient_username") or "").strip().lower()
            for row in state.get("recent_sent_outreach_jobs") or []
            if str(row.get("recipient_username") or "").strip()
        }
        for job in jobs:
            if str(job.get("recipient_username") or "").strip().lower() in sent_recipients:
                reasons.append("outreach_recipient_already_sent")

    counter_rows = list(state.get("counter_today") or [])
    counter = counter_rows[0] if len(counter_rows) == 1 else {}
    stop_if(len(counter_rows) != 1, "dm_counter_today_missing")
    stop_if(str(counter.get("counter_date") or "") not in ("", _today_utc()), "dm_counter_today_invalid_date")
    outreach_sent_today = _as_int(counter.get("outreach_sent_count"), -1)
    total_sent_today = _as_int(counter.get("total_dm_sent_count"), -1)
    stop_if(outreach_sent_today < 0, "outreach_sent_count_invalid")
    stop_if(total_sent_today < 0, "total_dm_sent_count_invalid")
    remaining_outreach_day = max(0, day_limit - max(0, outreach_sent_today))
    remaining_total_day = max(0, total_day_limit - max(0, total_sent_today))
    effective = min(session_limit, remaining_outreach_day, remaining_total_day)
    summary.update(
        {
            "outreach_per_session_limit": session_limit,
            "outreach_per_day_limit": day_limit,
            "total_dm_per_day_limit": total_day_limit,
            "outreach_sent_today": outreach_sent_today,
            "total_dm_sent_today": total_sent_today,
            "max_jobs_effective": effective,
        }
    )
    stop_if(remaining_outreach_day <= 0, "outreach_day_quota_exhausted")
    stop_if(remaining_total_day <= 0, "total_dm_day_quota_exhausted")
    stop_if(effective <= 0, "outreach_effective_cap_insufficient")
    if jobs and not expected_job_id and len(jobs) > effective:
        reasons.append("pending_outreach_jobs_exceed_effective_cap")

    env = state.get("env") or {}
    outreach_real = _truthy(env.get("OUTREACH_DM_REAL_SEND_ENABLED"))
    welcome_real = _truthy(env.get("WELCOME_DM_REAL_SEND_ENABLED"))
    legacy_real = _truthy(env.get("DM_SENDER_REAL_SEND_ENABLED"))
    if mode_norm in {"real-send", "send-one"}:
        stop_if(not outreach_real, "outreach_real_send_disabled")
    else:
        stop_if(outreach_real, "outreach_real_send_enabled_during_dry_run")
    stop_if(welcome_real, "welcome_real_send_enabled")
    stop_if(legacy_real, "legacy_dm_real_send_enabled")
    if mode_norm == "real-send":
        reasons.append("real_send_mode_not_enabled_yet")
    if mode_norm == "send-one":
        stop_if(not expected_job_id, "missing_job_id")
        reasons.append("send_one_mode_not_enabled_yet")

    current_ime = state.get("current_ime")
    if isinstance(current_ime, dict):
        ime = str(current_ime.get("stdout") or "").strip()
        stop_if("com.android.adbkeyboard/.AdbIME" not in ime, "adbkeyboard_not_active")
    package = state.get("package_installed") or {}
    if package:
        stop_if(int(package.get("exit_code") or 0) != 0 or "package:" not in str(package.get("stdout") or ""), "package_not_installed")

    return not reasons, reasons, summary


def _safe_job_summary(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job.get("id"),
        "status": job.get("status"),
        "dm_type": job.get("dm_type"),
        "recipient_username": job.get("recipient_username"),
        "template_id": job.get("template_id"),
        "source": job.get("source"),
        "campaign_id": job.get("campaign_id"),
        "attempts": job.get("attempts"),
        "reserved_by": job.get("reserved_by"),
        "created_at": job.get("created_at"),
        "sent_at": job.get("sent_at"),
        "finished_at": job.get("finished_at"),
        "last_error": job.get("last_error"),
        "skip_reason": job.get("skip_reason"),
        "metadata": _safe_job_metadata(job.get("metadata")),
    }


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
    for key in ("outreach_jobs", "welcome_jobs", "dm_jobs_reserved_running", "recent_sent_outreach_jobs"):
        if isinstance(safe.get(key), list):
            safe[key] = [_safe_job_summary(row) for row in safe[key] if isinstance(row, dict)]
    if isinstance(safe.get("selected_job"), dict):
        safe["selected_job"] = _safe_job_summary(safe["selected_job"])
    return safe


def _emit_payload(
    *,
    ok: bool,
    reasons: list[str],
    summary: dict[str, Any],
    state: dict[str, Any] | None = None,
    missing: list[str] | None = None,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight isolated physical Outreach DM smoke.")
    parser.add_argument("username")
    parser.add_argument("--mode", choices=("dry-run", "real-send", "send-one"), default="dry-run")
    parser.add_argument("--job-id", default="", help="Pinned Outreach job id for future send-one/real smoke.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-device-check", action="store_true")
    args = parser.parse_args(argv)

    summary: dict[str, Any] = {
        "mode": args.mode,
        "run_type": RUN_TYPE,
        "account_id": None,
        "recipient_username": None,
        "job_id": args.job_id or None,
        "template_id": None,
        "template_body": None,
    }

    env_ok, missing_keys, env_reason = bootstrap_supabase_env()
    if not env_ok:
        return _emit_payload(
            ok=False,
            reasons=[str(env_reason or "missing_required_env")],
            summary=summary,
            missing=missing_keys or None,
            json_mode=bool(args.json),
            mode=args.mode,
        )

    try:
        state = collect_outreach_physical_smoke_state(
            args.username,
            include_device=not args.skip_device_check,
            job_id=args.job_id,
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

    ok, reasons, summary = validate_outreach_physical_smoke_state(
        state,
        mode=args.mode,
        job_id=args.job_id,
    )
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
