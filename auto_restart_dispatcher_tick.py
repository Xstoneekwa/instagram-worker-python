"""Dispatcher-side Auto Restart tick caller.

Invokes the canonical backend tick route from the existing Run Control dispatcher
loop. Local cadence is capped at one attempt per minute; backend owns bucket
deduplication and check_every_minutes enforcement.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from logs import log

AUTO_RESTART_TICK_MIN_INTERVAL_SECONDS = 60.0
AUTO_RESTART_TICK_HTTP_TIMEOUT_SECONDS = 45.0
AUTO_RESTART_TICK_ROUTE = "/api/instagram-dashboard/auto-restart/tick"
AUTO_RESTART_TICK_TOKEN_ENV = "INSTAGRAM_AUTO_RESTART_TICK_TOKEN"
AUTO_RESTART_TICK_URL_ENV_CANDIDATES = (
    "INSTAGRAM_DASHBOARD_API_BASE_URL",
    "BOTAPP_COMPASS_AI_RELAY_URL",
)


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def resolve_auto_restart_tick_base_url() -> str:
    for name in AUTO_RESTART_TICK_URL_ENV_CANDIDATES:
        value = _env(name)
        if value:
            return value.rstrip("/")
    return ""


def build_auto_restart_tick_url(base_url: str | None = None) -> str:
    base = (base_url if base_url is not None else resolve_auto_restart_tick_base_url()).rstrip("/")
    if not base:
        return ""
    return f"{base}{AUTO_RESTART_TICK_ROUTE}"


def evaluate_auto_restart_tick_gate(
    *,
    worker_id: str,
    token: str | None = None,
    base_url: str | None = None,
    dispatcher_reliable: bool = True,
) -> tuple[bool, str]:
    resolved_worker_id = str(worker_id or "").strip()
    if not resolved_worker_id:
        return False, "worker_id_not_configured"

    resolved_base = base_url if base_url is not None else resolve_auto_restart_tick_base_url()
    if not resolved_base:
        return False, "tick_url_not_configured"

    resolved_token = token if token is not None else _env(AUTO_RESTART_TICK_TOKEN_ENV)
    if not resolved_token:
        return False, "tick_token_not_configured"

    if not dispatcher_reliable:
        return False, "dispatcher_unreliable"

    return True, ""


def should_run_auto_restart_tick(
    *,
    last_tick_monotonic: float,
    now_monotonic: float | None = None,
    min_interval_seconds: float = AUTO_RESTART_TICK_MIN_INTERVAL_SECONDS,
) -> bool:
    now = now_monotonic if now_monotonic is not None else time.monotonic()
    interval_s = max(60.0, float(min_interval_seconds))
    return (now - last_tick_monotonic) >= interval_s


def _redact_for_log(value: str, token: str) -> str:
    text = str(value or "")
    if not text or not token:
        return text[:500]
    return text.replace(token, "[REDACTED]")[:500]


def extract_tick_result_payload(parsed: Any) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        return {}
    data = parsed.get("data")
    if isinstance(data, dict):
        return data
    return parsed


def summarize_tick_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "skipped": payload.get("skipped"),
        "reason": payload.get("reason"),
        "deduplicated_count": payload.get("deduplicated_count"),
        "evaluated_count": payload.get("scanned_candidates"),
        "eligible_count": payload.get("eligible_candidates"),
        "blocked_count": payload.get("blocked_count"),
        "enqueued_count": payload.get("enqueued_count"),
    }


def build_auto_restart_tick_request(
    *,
    worker_id: str,
    token: str,
    url: str,
    dry_run: bool = False,
) -> urllib.request.Request:
    payload = json.dumps({"dry_run": bool(dry_run)}).encode("utf-8")
    return urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-instagram-auto-restart-tick-token": token,
            "x-run-control-worker-id": worker_id,
        },
    )


def run_auto_restart_dispatcher_tick(
    *,
    worker_id: str,
    dry_run: bool = False,
    dispatcher_reliable: bool = True,
    urlopen: Callable[..., Any] | None = None,
    token: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    resolved_worker_id = str(worker_id or "").strip()
    resolved_token = token if token is not None else _env(AUTO_RESTART_TICK_TOKEN_ENV)
    resolved_base = base_url if base_url is not None else resolve_auto_restart_tick_base_url()
    url = build_auto_restart_tick_url(resolved_base)

    can_call, gate_reason = evaluate_auto_restart_tick_gate(
        worker_id=resolved_worker_id,
        token=resolved_token,
        base_url=resolved_base,
        dispatcher_reliable=dispatcher_reliable,
    )
    if not can_call:
        return {"ok": False, "skipped": True, "reason": gate_reason}

    assert resolved_token is not None  # for type checkers after gate
    request = build_auto_restart_tick_request(
        worker_id=resolved_worker_id,
        token=resolved_token,
        url=url,
        dry_run=dry_run,
    )
    opener = urlopen or urllib.request.urlopen
    try:
        with opener(request, timeout=AUTO_RESTART_TICK_HTTP_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
            parsed = json.loads(body) if body else {}
            tick_payload = extract_tick_result_payload(parsed)
            metrics = summarize_tick_metrics(tick_payload)
            log(
                "info",
                "auto_restart_dispatcher_tick_completed",
                worker_id=resolved_worker_id,
                dry_run=dry_run,
                status=getattr(response, "status", None),
                **metrics,
            )
            return {"ok": True, "status": getattr(response, "status", None), "result": parsed}
    except urllib.error.HTTPError as exc:
        err_body = _redact_for_log(exc.read().decode("utf-8", errors="replace"), resolved_token)
        log(
            "warning",
            "auto_restart_dispatcher_tick_http_error",
            worker_id=resolved_worker_id,
            status=exc.code,
            error=err_body,
        )
        return {"ok": False, "reason": "tick_http_error", "status": exc.code, "error": err_body}
    except Exception as exc:
        log(
            "warning",
            "auto_restart_dispatcher_tick_failed",
            worker_id=resolved_worker_id,
            error=_redact_for_log(str(exc), resolved_token),
        )
        return {"ok": False, "reason": "tick_request_failed", "error": str(exc)[:300]}
