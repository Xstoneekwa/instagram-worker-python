"""Persistent Auto Restart device locks backed by Supabase RPCs."""

from __future__ import annotations

import os
from typing import Any

import supabase_client
from logs import log


def _load_device_lock(device_id: str) -> dict[str, Any] | None:
    rows = supabase_client._request_json(
        "GET",
        "auto_restart_device_locks",
        query={
            "select": "device_id,worker_id,request_id,account_id,lease_id,heartbeat_at,lease_expires_at",
            "device_id": f"eq.{device_id}",
            "limit": "1",
        },
        request_timeout=5.0,
        max_retries=0,
    ) or []
    return dict(rows[0]) if rows else None


def _lease_seconds() -> int:
    raw = os.environ.get("AUTO_RESTART_DEVICE_LOCK_SECONDS", "900")
    try:
        return max(60, min(3600, int(str(raw).strip())))
    except ValueError:
        return 900


def acquire_device_lock(*, device_id: str, worker_id: str, account_id: str,
                        app_instance_id: str | None, reason: str = "auto_restart") -> dict[str, Any]:
    """Pre-request acquisition is superseded by the atomic enqueue boundary."""
    return {"ok": False, "acquired": False, "reason": "device_lock_atomic_enqueue_required"}


def bind_device_lock_to_request(*, device_id: str, worker_id: str,
                                request_id: str) -> dict[str, Any]:
    return {"ok": False, "bound": False, "reason": "device_lock_atomic_enqueue_required"}


def renew_device_lock(*, device_id: str, worker_id: str, request_id: str,
                      expected_lease_id: str | None = None,
                      account_id: str | None = None) -> dict[str, Any]:
    """Renew only the caller's captured generation; never infer it from a read."""
    from datetime import datetime, timezone
    from uuid import UUID

    denied = {"ok": False, "renewed": False, "reconciled": False}
    try:
        token = str(UUID(expected_lease_id)) if isinstance(expected_lease_id, str) else ""
    except ValueError:
        token = ""
    if not token or not isinstance(account_id, str) or not account_id.strip():
        return {**denied, "reason": "device_lock_expected_identity_required"}
    if not device_id or not worker_id or not request_id:
        return {**denied, "reason": "device_lock_expected_identity_required"}
    started = datetime.now(timezone.utc)
    expected = {"device_id": device_id, "worker_id": worker_id,
                "request_id": request_id, "account_id": account_id, "lease_id": token}

    def exact(row):
        return isinstance(row, dict) and all(row.get(key) == value for key, value in expected.items())

    try:
        result = supabase_client.call_rpc_once(
            "auto_restart_renew_device_lock",
            {"p_device_id": device_id, "p_worker_id": worker_id,
             "p_request_id": request_id, "p_lease_seconds": _lease_seconds(),
             "p_expected_lease_id": token, "p_account_id": account_id},
            timeout_seconds=5.0,
        )
    except Exception:
        # A lost response is not permission to POST again or adopt the current token.
        try:
            row = _load_device_lock(device_id)
            if not exact(row):
                return {**denied, "reason": "device_lock_reconciliation_identity_mismatch"}
            heartbeat = datetime.fromisoformat(str(row.get("heartbeat_at", "")).replace("Z", "+00:00"))
            expires = datetime.fromisoformat(str(row.get("lease_expires_at", "")).replace("Z", "+00:00"))
            if heartbeat.tzinfo is None or expires.tzinfo is None or heartbeat < started or expires <= datetime.now(timezone.utc):
                return {**denied, "reason": "device_lock_renewal_not_proven"}
        except Exception:
            return {**denied, "reason": "device_lock_renewal_not_proven"}
        return {"ok": True, "renewed": True, "reconciled": True, **expected}
    if isinstance(result, dict) and result.get("ok") is False:
        return {**result, **denied}
    if not exact(result) or result.get("ok") is not True or result.get("renewed") is not True:
        return {**denied, "reason": "device_lock_renewal_response_mismatch"}
    return result



def transfer_device_lock(*, device_id: str, request_id: str, new_worker_id: str,
                         previous_worker_id: str | None = None,
                         expected_lease_id: str | None = None,
                         account_id: str | None = None,
                         lease_seconds: int | None = None) -> dict[str, Any]:
    denied = {"ok": False, "transferred": False}
    if not _valid_device_lock_identity(device_id, request_id, account_id, previous_worker_id, expected_lease_id):
        return {**denied, "reason": "device_lock_expected_identity_required"}
    if not isinstance(new_worker_id, str) or not new_worker_id.strip():
        return {**denied, "reason": "device_lock_expected_identity_required"}
    if type(lease_seconds) is not int or not 60 <= lease_seconds <= 3600:
        return {**denied, "reason": "device_lock_lease_duration_required"}
    expected = dict(device_id=device_id, request_id=request_id, account_id=account_id,
                    worker_id=new_worker_id)
    params = dict(p_device_id=device_id, p_request_id=request_id,
                  p_new_worker_id=new_worker_id, p_previous_worker_id=previous_worker_id,
                  p_expected_lease_id=expected_lease_id, p_account_id=account_id,
                  p_lease_seconds=lease_seconds)
    try:
        result = supabase_client.call_rpc_once("auto_restart_transfer_device_lock", params, timeout_seconds=5.0)
    except Exception:
        result = _recover_device_lock_operation("transfer", params)
    if not isinstance(result, dict) or result.get("ok") is not True or result.get("transferred") is not True:
        return {**denied, "reason": (result or {}).get("reason", "device_lock_transfer_not_proven") if isinstance(result, dict) else "device_lock_transfer_not_proven"}
    if (any(result.get(k) != v for k, v in expected.items())
        or not _valid_device_lock_identity(device_id, request_id, account_id, new_worker_id, result.get("lease_id"))
        or result.get("generation") != result.get("lease_id")
        or result.get("lease_id") == expected_lease_id
        or result.get("lease_seconds") != lease_seconds):
        return {**denied, "reason": "device_lock_transfer_response_mismatch"}
    return result


def release_device_lock(*, device_id: str, worker_id: str,
                        request_id: str | None = None,
                        expected_lease_id: str | None = None,
                        account_id: str | None = None) -> dict[str, Any]:
    denied = {"ok": False, "released": False}
    if not _valid_device_lock_identity(device_id, request_id, account_id, worker_id, expected_lease_id):
        return {**denied, "reason": "device_lock_expected_identity_required"}
    expected = dict(device_id=device_id, worker_id=worker_id, request_id=request_id,
                    lease_id=expected_lease_id, account_id=account_id, generation=expected_lease_id)
    params = dict(p_device_id=device_id, p_worker_id=worker_id, p_request_id=request_id,
                  p_expected_lease_id=expected_lease_id, p_account_id=account_id)
    try:
        result = supabase_client.call_rpc_once("auto_restart_release_device_lock", params, timeout_seconds=5.0)
    except Exception:
        result = _recover_device_lock_operation("release", params)
    if (not isinstance(result, dict) or result.get("ok") is not True
        or result.get("released") is not True or any(result.get(k) != v for k, v in expected.items())):
        return {**denied, "reason": result.get("reason", "device_lock_release_not_proven") if isinstance(result, dict) else "device_lock_release_not_proven"}
    return result


def release_device_lock_for_request(*, request_id: str, worker_id: str,
                                    device_id: str | None = None,
                                    expected_lease_id: str | None = None,
                                    account_id: str | None = None) -> dict[str, Any]:
    """Only the captured request binding can authorize cleanup; never query a successor."""
    return release_device_lock(device_id=device_id, worker_id=worker_id, request_id=request_id,
                               expected_lease_id=expected_lease_id, account_id=account_id)


def reconcile_stale_device_ui_leases(*, grace_seconds: int = 0) -> dict[str, Any]:
    """Bulk timeout-based ownership mutation is not a generation-fenced recovery."""
    return {"ok": False, "reconciled": 0, "reason": "device_lock_exact_reconciliation_required"}


def _valid_device_lock_identity(device_id, request_id, account_id, worker_id, lease_id):
    from uuid import UUID
    try:
        return (isinstance(worker_id, str) and bool(worker_id.strip())
                and all(isinstance(value, str) and str(UUID(value)) == value
                        for value in (device_id, request_id, account_id, lease_id)))
    except (ValueError, TypeError, AttributeError):
        return False


def device_lock_binding_from_request(request, *, account_id, request_id, device_id=None):
    """Copy the binding returned by atomic enqueue/claim, not the live lock row."""
    metadata = request.get("metadata_safe")
    binding = metadata.get("device_lock_binding") if isinstance(metadata, dict) else None
    if not isinstance(binding, dict):
        raise ValueError("device_lock_binding_required")
    binding = dict(binding)
    if (binding.get("account_id") != account_id or binding.get("request_id") != request_id
        or device_id is not None and binding.get("device_id") != device_id
        or binding.get("generation") != binding.get("lease_id")
        or not _valid_device_lock_identity(binding.get("device_id"), request_id, account_id,
                                           binding.get("worker_id"), binding.get("lease_id"))
        or type(binding.get("lease_seconds")) is not int
        or not 60 <= binding["lease_seconds"] <= 3600):
        raise ValueError("device_lock_binding_mismatch")
    return binding


def _recover_device_lock_operation(operation, params):
    """Read a receipt for this exact operation. No current-token adoption or retry POST."""
    try:
        result = supabase_client.call_rpc_once(
            "fleet_device_lock_operation_receipt_v1",
            {"p_operation": operation, "p_expected": params}, timeout_seconds=5.0)
        return result if isinstance(result, dict) else {"ok": False}
    except Exception:
        return {"ok": False, "reason": "device_lock_operation_not_proven"}
