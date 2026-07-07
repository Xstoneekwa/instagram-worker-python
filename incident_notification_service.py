"""Canonical long-lived incident notification service (P2).

Replaces the legacy-workspace one-shot notifier: runs from the canonical
release pointer under launchd, loops the ORF-4 outbox dispatcher, publishes a
worker heartbeat, and persists a redacted state file for health/status reads.

Never prints or logs webhook values. Never creates runs or run requests.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_env_file(path: str) -> None:
    """Minimal env-file loader (KEY=VALUE lines); never logs values."""
    env_path = Path(str(path or "").strip())
    if not path or not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


SUMMARY_STATE_KEYS = (
    "dispatched",
    "reason",
    "selected_count",
    "created_count",
    "attempted_count",
    "sent_count",
    "failed_count",
    "retried_count",
    "retry_exhausted_count",
    "skipped_duplicate_count",
    "selected_channels",
    "enabled_channels",
    "errors_count",
    "dry_run",
)


def _safe_state(summary: dict[str, Any], *, ok: bool, error: str | None = None) -> dict[str, Any]:
    state: dict[str, Any] = {"at": _utc_now_iso(), "ok": ok}
    for key in SUMMARY_STATE_KEYS:
        if key in summary:
            state[key] = summary[key]
    if error:
        state["reason"] = str(error)[:300]
    return state


def _write_state(state_file: str, state: dict[str, Any]) -> None:
    if not state_file:
        return
    try:
        path = Path(state_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def run_once(*, worker_id: str, state_file: str = "") -> dict[str, Any]:
    """One dispatch cycle + heartbeat. Import deferred so --env-file applies first."""
    import incident_notifications
    import runtime_heartbeat
    from logs import log

    try:
        summary = incident_notifications.dispatch_account_incident_notifications()
        ok = bool(summary.get("dispatched")) and not summary.get("errors_count")
    except Exception as exc:
        summary = {"dispatched": False, "reason": "dispatch_exception"}
        ok = False
        log("warning", "incident_notifier_cycle_failed", error=str(exc)[:300])
    state = _safe_state(summary, ok=ok)
    _write_state(state_file, state)
    log(
        "info",
        "incident_notifier_cycle",
        ok=ok,
        reason=summary.get("reason"),
        selected_count=summary.get("selected_count"),
        sent_count=summary.get("sent_count"),
        failed_count=summary.get("failed_count"),
        retried_count=summary.get("retried_count"),
        skipped_duplicate_count=summary.get("skipped_duplicate_count"),
        enabled_channels=summary.get("enabled_channels"),
        dry_run=summary.get("dry_run"),
    )
    runtime_heartbeat.heartbeat_worker(
        worker_id=worker_id,
        status="running" if ok else "error",
        metadata={"service": "incident-notifier", "last_cycle_ok": ok},
        force=True,
    )
    return summary


def serve(*, worker_id: str, interval_seconds: int, state_file: str = "") -> int:
    from logs import log

    stop_requested = {"stop": False}

    def _handle_signal(signum: int, _frame: Any) -> None:
        stop_requested["stop"] = True
        log("info", "incident_notifier_stop_requested", signal=int(signum))

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    log(
        "info",
        "incident_notifier_started",
        worker_id=worker_id,
        interval_seconds=interval_seconds,
        pid=os.getpid(),
    )
    while not stop_requested["stop"]:
        run_once(worker_id=worker_id, state_file=state_file)
        deadline = time.monotonic() + max(5, interval_seconds)
        while not stop_requested["stop"] and time.monotonic() < deadline:
            time.sleep(1)
    log("info", "incident_notifier_stopped", worker_id=worker_id)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canonical incident notification service.")
    parser.add_argument("--env-file", type=str, default="")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=0)
    parser.add_argument("--state-file", type=str, default="")
    parser.add_argument("--worker-id", type=str, default="")
    args = parser.parse_args(argv)

    if args.env_file:
        _load_env_file(args.env_file)

    import config

    worker_id = str(args.worker_id or "").strip() or "incident-notifier:mac-admin-01"
    interval = int(args.interval_seconds or 0) or int(
        getattr(config, "INCIDENT_NOTIFIER_INTERVAL_SECONDS", 60)
    )
    if args.serve:
        return serve(worker_id=worker_id, interval_seconds=interval, state_file=args.state_file)
    summary = run_once(worker_id=worker_id, state_file=args.state_file)
    ok = bool(summary.get("dispatched"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
