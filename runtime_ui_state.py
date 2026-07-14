"""Runtime UI state hard-stop publisher (navigation / worker path)."""

from __future__ import annotations

from typing import Any

from auto_restart_hard_stop import execute_auto_restart_hard_stop
from logs import log


def publish_ui_state_unknown_hard_stop(
    *,
    account_id: str,
    run_id: str | None = None,
    device_id: str | None = None,
    app_instance_id: str | None = None,
    execution_worker_id: str | None = None,
    trigger_source: str | None = None,
    client_id: str | None = None,
    account_username: str | None = None,
    observed_screen: str | None = None,
    navigation_state: str | None = None,
    evidence: dict[str, Any] | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """Signal ui_state_unknown through the production hard-stop publisher path."""
    safe_evidence = {
        **(evidence or {}),
        "signal": "ui_state_unknown",
        "observed_screen": (observed_screen or "unknown")[:120],
        "navigation_state": (navigation_state or "unknown")[:120],
    }
    result = execute_auto_restart_hard_stop(
        account_id=account_id,
        reason="ui_state_unknown_blocked",
        run_id=run_id,
        device_id=device_id,
        app_instance_id=app_instance_id,
        execution_worker_id=execution_worker_id,
        trigger_source=trigger_source or "runtime_ui_state",
        client_id=client_id,
        account_username=account_username,
        evidence=safe_evidence,
        cancel_pending=True,
        worker_id=worker_id,
    )
    log(
        "warning",
        "runtime_ui_state_unknown_hard_stop",
        account_id=account_id,
        run_id=run_id,
        executed=bool(result.get("executed")),
        incident_id=(result.get("incident") or {}).get("incident_id"),
    )
    return result
