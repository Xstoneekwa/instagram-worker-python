"""Resolve account assignment routing context for worker dispatch.

Entry 2C-3 v1 is intentionally read-only: this module never mutates
account_assignments, phone_app_instances, phone_clones, or phone_devices.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import supabase_client


SUPPORTED_RUN_ASSIGNMENT_TYPES: dict[str, set[str]] = {
    "outreach_session": {"outreach_only", "full_cycle"},
    "account_session": {"full_cycle"},
    "dm_welcome_session_send": {"full_cycle"},
    "login_provisioning": {"full_cycle"},
    "login_email_code_resume": {"full_cycle"},
}


def _empty_context(
    *,
    account_id: str,
    run_type: str,
    reason: str,
    require_assignment: bool,
    fallback_used: bool | None = None,
) -> dict[str, Any]:
    return {
        "assignment_found": False,
        "assignment_id": None,
        "account_id": (account_id or "").strip() or None,
        "assignment_type": None,
        "slot_kind": None,
        "starts_at": None,
        "ends_at": None,
        "device_id": None,
        "clone_id": None,
        "app_instance_id": None,
        "device_kind": None,
        "adb_serial": None,
        "device_udid": None,
        "host_machine": None,
        "hub_label": None,
        "hub_port": None,
        "pool_type": None,
        "clone_index": None,
        "clone_label": None,
        "app_instance_type": None,
        "app_instance_index": None,
        "app_instance_label": None,
        "source": "account_assignments",
        "fallback_used": (not require_assignment) if fallback_used is None else bool(fallback_used),
        "reason": reason,
        "run_type": (run_type or "").strip().lower() or None,
    }


def _parse_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _window_contains_now(starts_at: Any, ends_at: Any) -> bool:
    start = _parse_timestamp(starts_at)
    end = _parse_timestamp(ends_at)
    if start is None or end is None:
        return False
    now = datetime.now(timezone.utc)
    return start <= now <= end


def _nested(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = row.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
    return {}


def sensitive_log_fields(
    context: dict[str, Any],
    *,
    include_sensitive: bool = False,
) -> dict[str, Any]:
    """Return safe log fields for assignment dispatch events."""
    adb_serial = str(context.get("adb_serial") or "").strip()
    device_udid = str(context.get("device_udid") or "").strip()
    host_machine = str(context.get("host_machine") or "").strip()
    hub_label = str(context.get("hub_label") or "").strip()
    hub_port = str(context.get("hub_port") or "").strip()

    out = {
        "account_id": context.get("account_id"),
        "run_type": context.get("run_type"),
        "assignment_id": context.get("assignment_id"),
        "device_id": context.get("device_id"),
        "clone_id": context.get("clone_id"),
        "app_instance_id": context.get("app_instance_id"),
        "device_kind": context.get("device_kind"),
        "assignment_type": context.get("assignment_type"),
        "slot_kind": context.get("slot_kind"),
        "starts_at": context.get("starts_at"),
        "ends_at": context.get("ends_at"),
        "fallback_used": bool(context.get("fallback_used")),
        "reason": context.get("reason"),
        "adb_serial_suffix": adb_serial[-4:] if adb_serial else None,
        "adb_serial_hash": hashlib.sha256(adb_serial.encode("utf-8")).hexdigest()[:12]
        if adb_serial
        else None,
    }
    if include_sensitive:
        out.update(
            {
                "adb_serial": adb_serial or None,
                "device_udid": device_udid or None,
                "host_machine": host_machine or None,
                "hub_label": hub_label or None,
                "hub_port": hub_port or None,
            }
        )
    return out


def _evaluate_schedule_gate(account_id: str, run_type: str) -> dict[str, Any] | None:
    try:
        result = supabase_client.call_rpc(
            "evaluate_account_schedule_gate",
            {
                "p_account_id": account_id,
                "p_requested_run_type": run_type,
            },
        )
    except Exception:
        return None
    return result if isinstance(result, dict) else None


def resolve_account_assignment_runtime_context(
    account_id: str,
    run_type: str,
    *,
    require_assignment: bool = False,
    enforce_window: bool = False,
) -> dict[str, Any]:
    """Resolve device/app-instance context for an account run.

    The returned dict may contain ops-only fields for the worker process. Use
    sensitive_log_fields() before logging it.
    """
    aid = str(account_id or "").strip()
    rtype = str(run_type or "").strip().lower()

    if not aid:
        return _empty_context(
            account_id=aid,
            run_type=rtype,
            reason="missing_account_id",
            require_assignment=require_assignment,
        )

    assignment = supabase_client.load_open_account_assignment_for_dispatch(aid)
    if not assignment:
        return _empty_context(
            account_id=aid,
            run_type=rtype,
            reason="assignment_not_found",
            require_assignment=require_assignment,
        )

    assignment_type = str(assignment.get("assignment_type") or "").strip()
    allowed_types = SUPPORTED_RUN_ASSIGNMENT_TYPES.get(rtype)
    if not allowed_types or assignment_type not in allowed_types:
        ctx = _empty_context(
            account_id=aid,
            run_type=rtype,
            reason="assignment_type_incompatible",
            require_assignment=True,
            fallback_used=False,
        )
        ctx.update(
            {
                "assignment_id": assignment.get("id"),
                "assignment_type": assignment_type or None,
                "slot_kind": assignment.get("slot_kind"),
                "starts_at": assignment.get("starts_at"),
                "ends_at": assignment.get("ends_at"),
                "device_id": assignment.get("device_id"),
                "clone_id": assignment.get("clone_id"),
                "app_instance_id": assignment.get("app_instance_id"),
            }
        )
        return ctx

    if enforce_window and not _window_contains_now(
        assignment.get("starts_at"),
        assignment.get("ends_at"),
    ):
        ctx = _empty_context(
            account_id=aid,
            run_type=rtype,
            reason="assignment_window_inactive",
            require_assignment=True,
            fallback_used=False,
        )
        ctx.update(
            {
                "assignment_id": assignment.get("id"),
                "assignment_type": assignment_type or None,
                "slot_kind": assignment.get("slot_kind"),
                "starts_at": assignment.get("starts_at"),
                "ends_at": assignment.get("ends_at"),
                "device_id": assignment.get("device_id"),
                "clone_id": assignment.get("clone_id"),
                "app_instance_id": assignment.get("app_instance_id"),
            }
        )
        return ctx

    if enforce_window:
        gate = _evaluate_schedule_gate(aid, rtype)
        if gate and gate.get("ok") is False:
            reason = str(gate.get("reason") or "assignment_window_inactive")
            ctx = _empty_context(
                account_id=aid,
                run_type=rtype,
                reason=reason,
                require_assignment=True,
                fallback_used=False,
            )
            ctx.update(
                {
                    "assignment_id": assignment.get("id"),
                    "assignment_type": assignment_type or None,
                    "slot_kind": assignment.get("slot_kind"),
                    "starts_at": assignment.get("starts_at"),
                    "ends_at": assignment.get("ends_at"),
                    "device_id": assignment.get("device_id"),
                    "clone_id": assignment.get("clone_id"),
                    "app_instance_id": assignment.get("app_instance_id"),
                }
            )
            return ctx

    device = _nested(assignment, "phone_device", "phone_devices", "device")
    clone = _nested(assignment, "phone_clone", "phone_clones", "clone")
    app_instance = _nested(assignment, "phone_app_instance", "phone_app_instances", "app_instance")
    instance_index = app_instance.get("instance_index", clone.get("clone_index"))
    instance_label = app_instance.get("visible_label", clone.get("clone_label"))
    package_name = app_instance.get("package_name")
    adb_serial = str(device.get("adb_serial") or "").strip()
    if not adb_serial:
        ctx = _empty_context(
            account_id=aid,
            run_type=rtype,
            reason="assignment_device_missing_adb_serial",
            require_assignment=True,
            fallback_used=False,
        )
        ctx.update(
            {
                "assignment_id": assignment.get("id"),
                "assignment_type": assignment_type or None,
                "slot_kind": assignment.get("slot_kind"),
                "starts_at": assignment.get("starts_at"),
                "ends_at": assignment.get("ends_at"),
                "device_id": assignment.get("device_id"),
                "clone_id": assignment.get("clone_id"),
                "app_instance_id": assignment.get("app_instance_id"),
                "device_kind": device.get("device_kind"),
                "pool_type": device.get("pool_type"),
                "clone_index": clone.get("clone_index"),
                "clone_label": clone.get("clone_label"),
                "app_instance_type": app_instance.get("instance_type"),
                "app_instance_index": instance_index,
                "app_instance_label": instance_label,
                "package_name": package_name,
            }
        )
        return ctx

    return {
        "assignment_found": True,
        "assignment_id": assignment.get("id"),
        "account_id": aid,
        "assignment_type": assignment_type or None,
        "slot_kind": assignment.get("slot_kind"),
        "starts_at": assignment.get("starts_at"),
        "ends_at": assignment.get("ends_at"),
        "device_id": assignment.get("device_id"),
        "clone_id": assignment.get("clone_id"),
        "app_instance_id": assignment.get("app_instance_id"),
        "device_kind": device.get("device_kind"),
        "adb_serial": adb_serial,
        "device_udid": device.get("device_udid"),
        "host_machine": device.get("host_machine"),
        "hub_label": device.get("hub_label"),
        "hub_port": device.get("hub_port"),
        "pool_type": device.get("pool_type"),
        "clone_index": clone.get("clone_index"),
        "clone_label": clone.get("clone_label"),
        "app_instance_type": app_instance.get("instance_type"),
        "app_instance_index": instance_index,
        "app_instance_label": instance_label,
        "package_name": package_name,
        "source": "account_assignments",
        "fallback_used": False,
        "reason": "assignment_resolved",
        "run_type": rtype or None,
    }
