"""Dispatcher-side Auto Restart tick caller."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from logs import log


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _tick_url() -> str:
    base = _env("INSTAGRAM_DASHBOARD_API_BASE_URL") or _env("BOTAPP_COMPASS_AI_RELAY_URL")
    if not base:
        return ""
    return f"{base.rstrip('/')}/api/instagram-dashboard/auto-restart/tick"


def should_run_auto_restart_tick(
    *,
    last_tick_monotonic: float,
    check_every_minutes: int,
    now_monotonic: float | None = None,
) -> bool:
    now = now_monotonic if now_monotonic is not None else time.monotonic()
    interval_s = max(60.0, float(check_every_minutes) * 60.0)
    return (now - last_tick_monotonic) >= interval_s


def run_auto_restart_dispatcher_tick(
    *,
    worker_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    url = _tick_url()
    token = _env("INSTAGRAM_AUTO_RESTART_TICK_TOKEN")
    if not url:
        return {"ok": False, "skipped": True, "reason": "tick_url_not_configured"}
    if not token:
        return {"ok": False, "skipped": True, "reason": "tick_token_not_configured"}

    payload = json.dumps(
        {
            "worker_id": worker_id,
            "dry_run": bool(dry_run),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-instagram-auto-restart-tick-token": token,
            "x-run-control-worker-id": worker_id,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
            parsed = json.loads(body) if body else {}
            log(
                "info",
                "auto_restart_dispatcher_tick_completed",
                worker_id=worker_id,
                dry_run=dry_run,
                status=response.status,
                enqueued_count=(parsed.get("enqueued_count") if isinstance(parsed, dict) else None),
            )
            return {"ok": True, "status": response.status, "result": parsed}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")[:500]
        log(
            "warning",
            "auto_restart_dispatcher_tick_http_error",
            worker_id=worker_id,
            status=exc.code,
            error=err_body,
        )
        return {"ok": False, "reason": "tick_http_error", "status": exc.code, "error": err_body}
    except Exception as exc:
        log(
            "warning",
            "auto_restart_dispatcher_tick_failed",
            worker_id=worker_id,
            error=str(exc)[:300],
        )
        return {"ok": False, "reason": "tick_request_failed", "error": str(exc)[:300]}
