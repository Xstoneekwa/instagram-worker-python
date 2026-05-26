"""Entry 2F-2 incident -> dashboard action reconciliation CLI.

This job is intentionally outside runner/sender runtime paths. It reads active
account_incidents and asks the SQL RPC to project supported incidents into
account_dashboard_actions.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from typing import Any
from urllib import error, parse, request

import config
from logs import log

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
TIMEOUT_SECONDS = 15


class IncidentDashboardActionSyncError(RuntimeError):
    """Raised only when fail-open is disabled."""


def _enabled() -> bool:
    return bool(getattr(config, "INCIDENT_DASHBOARD_SYNC_ENABLED", False))


def _fail_open() -> bool:
    return bool(getattr(config, "INCIDENT_DASHBOARD_SYNC_FAIL_OPEN", True))


def _config_limit() -> int:
    try:
        return int(getattr(config, "INCIDENT_DASHBOARD_SYNC_LIMIT", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


def clamp_limit(value: int | str | None) -> int:
    try:
        raw = int(value) if value is not None else _config_limit()
    except (TypeError, ValueError):
        raw = DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, raw))


def _base_url() -> str:
    url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    if not url:
        raise IncidentDashboardActionSyncError("supabase_url_missing")
    return url


def _service_key() -> str:
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key:
        raise IncidentDashboardActionSyncError("supabase_service_role_key_missing")
    return key


def _request_json(
    method: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> Any:
    base = _base_url()
    key = _service_key()
    params = f"?{parse.urlencode(query or {})}" if query else ""
    url = f"{base}/rest/v1/{path}{params}"
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = request.Request(
        url=url,
        method=method,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        data=data,
    )
    try:
        with request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            raw = resp.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise IncidentDashboardActionSyncError(f"supabase_http_error:{exc.code}:{detail}") from exc
    except error.URLError as exc:
        raise IncidentDashboardActionSyncError("supabase_network_error") from exc


def _safe_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if any(marker in lowered for marker in ("bearer ", "service_role", "apikey", "authorization", "token")):
        return "supabase_request_failed"
    return text[:500]


def _empty_summary(*, enabled: bool, dry_run: bool, limit: int, run_id: str) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "dry_run": dry_run,
        "limit": limit,
        "run_id": run_id,
        "selected_count": 0,
        "synced_count": 0,
        "skipped_count": 0,
        "error_count": 0,
        "results": [],
    }


def load_candidate_incidents(limit: int) -> list[dict[str, Any]]:
    rows = _request_json(
        "GET",
        "account_incidents",
        query={
            "select": "id,incident_type,status,last_seen_at,created_at",
            "status": "in.(open,acknowledged)",
            "order": "last_seen_at.desc.nullslast,created_at.desc",
            "limit": str(clamp_limit(limit)),
        },
    )
    return [dict(row) for row in (rows or []) if str(row.get("id") or "").strip()]


def sync_incident_dashboard_action(incident_id: str, *, run_id: str) -> dict[str, Any]:
    out = _request_json(
        "POST",
        "rpc/sync_account_incident_dashboard_action",
        body={
            "p_incident_id": incident_id,
            "p_actor_type": "system",
            "p_reason": "incident_dashboard_reconciliation",
            "p_metadata": {
                "source": "incident_dashboard_action_sync",
                "run_id": run_id,
            },
        },
    )
    return dict(out or {})


def dispatch_incident_dashboard_action_sync(
    *,
    limit: int | str | None = None,
    dry_run: bool = False,
    force: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    effective_limit = clamp_limit(limit)
    rid = str(run_id or f"incident-dashboard-sync:{uuid.uuid4()}").strip()
    enabled = force or _enabled()
    summary = _empty_summary(enabled=enabled, dry_run=dry_run, limit=effective_limit, run_id=rid)
    if not enabled:
        summary["reason"] = "disabled"
        return summary

    try:
        incidents = load_candidate_incidents(effective_limit)
    except Exception as exc:
        if not _fail_open():
            raise
        summary["reason"] = "load_failed"
        summary["error_count"] = 1
        summary["error"] = _safe_error(exc)
        return summary

    summary["selected_count"] = len(incidents)
    if dry_run:
        summary["reason"] = "dry_run"
        summary["results"] = [
            {
                "incident_id": str(item.get("id") or ""),
                "incident_type": item.get("incident_type"),
                "incident_status": item.get("status"),
                "action": "dry_run",
            }
            for item in incidents
        ]
        return summary

    for incident in incidents:
        incident_id = str(incident.get("id") or "").strip()
        incident_type = str(incident.get("incident_type") or "").strip()
        try:
            result = sync_incident_dashboard_action(incident_id, run_id=rid)
            action = str(result.get("action") or "").strip()
            item = {
                "incident_id": incident_id,
                "incident_type": incident_type,
                "action": action or "unknown",
                "reason": result.get("reason"),
                "action_type": result.get("action_type"),
                "dashboard_action_id": result.get("dashboard_action_id"),
            }
            summary["results"].append({k: v for k, v in item.items() if v is not None and v != ""})
            if action == "skipped":
                summary["skipped_count"] += 1
            elif result.get("ok") is True:
                summary["synced_count"] += 1
            else:
                summary["error_count"] += 1
        except Exception as exc:
            if not _fail_open():
                raise
            summary["error_count"] += 1
            safe_error = _safe_error(exc)
            summary["results"].append(
                {
                    "incident_id": incident_id,
                    "incident_type": incident_type,
                    "action": "error",
                    "reason": safe_error,
                }
            )
            log(
                "warning",
                "incident_dashboard_action_sync_failed",
                incident_id=incident_id,
                incident_type=incident_type,
                reason=safe_error,
            )
            continue
    summary["reason"] = "completed"
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync ORF incidents to dashboard actions.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum incidents to reconcile (1..200).")
    parser.add_argument("--dry-run", action="store_true", help="List candidates without calling the sync RPC.")
    parser.add_argument("--force", action="store_true", help="Run even when INCIDENT_DASHBOARD_SYNC_ENABLED=false.")
    args = parser.parse_args(argv)
    summary = dispatch_incident_dashboard_action_sync(
        limit=args.limit,
        dry_run=bool(args.dry_run),
        force=bool(args.force),
    )
    print(json.dumps(summary, sort_keys=True))
    return 0 if int(summary.get("error_count") or 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
