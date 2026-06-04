"""Live View host agent — LV-Web-2A screenshot polling (view-only, disabled by default).

Isolated from runner.py, account_session, dispatcher, provisioning, Follow/DM/Outreach.
Launch manually for smoke tests only.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from live_view_host_agent_core import (
    LiveViewHostConfig,
    build_agent_metadata,
    is_session_expired,
    local_frame_path,
    mask_serial,
    merge_metadata_safe,
    package_matches,
    parse_foreground_package,
    session_is_terminal,
    session_runtime_expired,
    should_claim_session,
    utc_now_iso,
)
from live_view_host_store import (
    account_has_active_run,
    claim_pending_session,
    delete_frame_png,
    fetch_account,
    fetch_app_instance,
    fetch_device,
    fetch_device_heartbeat,
    fetch_session,
    insert_audit_event,
    list_active_sessions_for_device,
    list_pending_sessions,
    update_session,
    upload_frame_png,
)
from logs import log


class LiveViewHostAgent:
    def __init__(self, config: LiveViewHostConfig) -> None:
        self.config = config
        self._shutdown = False
        self._active_session_id: str | None = None

    def request_shutdown(self) -> None:
        self._shutdown = True

    def run_forever(self) -> int:
        if not self.config.enabled:
            log("info", "live_view_host_agent_disabled", host_id=self.config.host_id or None)
            return 0
        if not self.config.host_id:
            log("error", "live_view_host_agent_missing_host_id")
            return 2

        Path(self.config.frame_dir).mkdir(parents=True, exist_ok=True)
        log(
            "info",
            "live_view_host_agent_started",
            host_id=self.config.host_id,
            poll_interval_seconds=self.config.poll_interval_seconds,
            frame_upload_enabled=self.config.frame_upload_enabled,
        )

        while not self._shutdown:
            try:
                if self._active_session_id:
                    self._run_active_session(self._active_session_id)
                else:
                    self._poll_pending()
            except Exception as exc:
                log("error", "live_view_host_agent_loop_error", error=str(exc)[:240])
            time.sleep(self.config.poll_interval_seconds)
        self._stop_active_session_on_shutdown()
        return 0

    def _stop_active_session_on_shutdown(self) -> None:
        if not self._active_session_id:
            return
        session_id = self._active_session_id
        self._active_session_id = None
        try:
            row = fetch_session(session_id)
            if row and not session_is_terminal(row):
                self._stop_session(row, reason="agent_shutdown", audit_action="agent_stopped")
        except Exception as exc:
            log("warning", "live_view_agent_shutdown_stop_failed", session_id=session_id, error=str(exc)[:180])

    def _cleanup_frame(self, session_id: str) -> None:
        if not self.config.frame_upload_enabled:
            return
        try:
            delete_frame_png(session_id, bucket=self.config.storage_bucket)
        except Exception as exc:
            log("warning", "live_view_frame_cleanup_failed", session_id=session_id, error=str(exc)[:180])

    def _poll_pending(self) -> None:
        pending = list_pending_sessions(self.config.host_id)
        for row in pending:
            if self._shutdown:
                return
            ok, reason = should_claim_session(row, host_id=self.config.host_id)
            if not ok:
                if reason == "expired":
                    self._expire_session(row)
                continue
            if self._try_claim(row):
                self._active_session_id = str(row.get("id"))
                return

    def _try_claim(self, row: dict[str, Any]) -> bool:
        session_id = str(row.get("id") or "")
        device_id = str(row.get("device_id") or "")
        if not session_id or not device_id:
            return False

        conflicts = list_active_sessions_for_device(device_id, exclude_session_id=session_id)
        if conflicts:
            self._fail_session(
                row,
                reason="live_view_phone_busy",
                audit_action="agent_failed",
                message="Another live view session is active on this phone.",
            )
            return False

        device = fetch_device(device_id)
        app_instance = fetch_app_instance(str(row.get("app_instance_id") or ""))
        account = fetch_account(str(row.get("account_id") or ""))
        if not device or not app_instance or not account:
            self._fail_session(row, reason="device_unavailable", audit_action="agent_failed")
            return False

        preflight_error = self._preflight_device(device, app_instance)
        if preflight_error:
            self._fail_session(row, reason=preflight_error, audit_action="agent_failed")
            return False

        now = utc_now_iso()
        existing_meta = row.get("metadata_safe") if isinstance(row.get("metadata_safe"), dict) else {}
        metadata = merge_metadata_safe(
            existing_meta,
            build_agent_metadata(
                host_id=self.config.host_id,
                transport="screenshot_polling",
                capture="adb_screencap_png",
                extra={
                    "username": account.get("username"),
                    "package_name": app_instance.get("package_name"),
                },
            ),
        )
        claimed = claim_pending_session(session_id, metadata_safe=metadata, started_at=now)
        if not claimed:
            return False

        insert_audit_event({
            "session_id": session_id,
            "event_type": "agent_starting",
            "account_id": row.get("account_id"),
            "device_id": row.get("device_id"),
            "app_instance_id": row.get("app_instance_id"),
            "action": "agent_starting",
            "metadata_safe": build_agent_metadata(
                host_id=self.config.host_id,
                transport="screenshot_polling",
                capture="adb_screencap_png",
            ),
        })
        log(
            "info",
            "live_view_session_claimed",
            session_id=session_id,
            account_id=row.get("account_id"),
            username=account.get("username"),
            package_name=app_instance.get("package_name"),
            device_label=device.get("name") or device.get("device_name"),
            adb_serial_masked=mask_serial(str(device.get("adb_serial") or "")),
        )
        return True

    def _run_active_session(self, session_id: str) -> None:
        row = fetch_session(session_id)
        if not row:
            self._active_session_id = None
            return

        if session_is_terminal(row) or is_session_expired(row):
            self._finalize_terminal(row, reason="expired" if is_session_expired(row) else str(row.get("status")))
            self._active_session_id = None
            return

        if session_runtime_expired(row, max_seconds=self.config.max_session_seconds):
            self._expire_session(row, failure_reason="session_ttl_exceeded")
            self._active_session_id = None
            return

        status = str(row.get("status") or "").strip().lower()
        if status == "stopped":
            self._stop_session(row, reason="dashboard_stop", audit_action="agent_stopped")
            self._active_session_id = None
            return

        device = fetch_device(str(row.get("device_id") or ""))
        app_instance = fetch_app_instance(str(row.get("app_instance_id") or ""))
        if not device or not app_instance:
            self._fail_session(row, reason="device_unavailable", audit_action="agent_failed")
            self._active_session_id = None
            return

        preflight_error = self._preflight_device(device, app_instance)
        if preflight_error:
            self._fail_session(row, reason=preflight_error, audit_action="agent_failed")
            self._active_session_id = None
            return

        expected_package = str(app_instance.get("package_name") or "").strip()
        foreground = self._read_foreground_package(str(device.get("adb_serial") or ""))
        if not package_matches(expected_package, foreground):
            self._fail_session(
                row,
                reason="live_view_wrong_package",
                audit_action="agent_failed",
                message="Expected clone is not in foreground.",
            )
            self._active_session_id = None
            return

        png = self._capture_png(str(device.get("adb_serial") or ""))
        if not png:
            self._fail_session(row, reason="capture_failed", audit_action="agent_failed")
            self._active_session_id = None
            return

        frame_path = local_frame_path(self.config.frame_dir, session_id)
        Path(frame_path).write_bytes(png)
        if self.config.frame_upload_enabled:
            try:
                upload_frame_png(session_id, png, bucket=self.config.storage_bucket)
            except Exception as exc:
                log(
                    "warning",
                    "live_view_frame_upload_failed",
                    session_id=session_id,
                    error=str(exc)[:180],
                )

        now = utc_now_iso()
        metadata = merge_metadata_safe(
            row.get("metadata_safe") if isinstance(row.get("metadata_safe"), dict) else {},
            build_agent_metadata(
                host_id=self.config.host_id,
                transport="screenshot_polling",
                capture="adb_screencap_png",
                extra={"last_frame_at": now, "updated_at": now},
            ),
        )
        body: dict[str, Any] = {
            "updated_at": now,
            "metadata_safe": metadata,
        }
        if status != "active":
            body["status"] = "active"
            insert_audit_event({
                "session_id": session_id,
                "event_type": "agent_active",
                "account_id": row.get("account_id"),
                "device_id": row.get("device_id"),
                "app_instance_id": row.get("app_instance_id"),
                "action": "agent_active",
                "metadata_safe": build_agent_metadata(
                    host_id=self.config.host_id,
                    transport="screenshot_polling",
                    capture="adb_screencap_png",
                ),
            })
            log("info", "live_view_session_active", session_id=session_id)
        update_session(session_id, body)

        refreshed = fetch_session(session_id)
        if refreshed and str(refreshed.get("status") or "").lower() == "stopped":
            self._stop_session(refreshed, reason="dashboard_stop", audit_action="agent_stopped")
            self._active_session_id = None

    def _preflight_device(self, device: dict[str, Any], app_instance: dict[str, Any]) -> str | None:
        adb_serial = str(device.get("adb_serial") or "").strip()
        if not adb_serial:
            return "device_unavailable"
        device_status = str(device.get("status") or "").strip().lower()
        if device_status in {"offline", "unauthorized", "maintenance", "disabled"}:
            return "device_unavailable"
        app_status = str(app_instance.get("status") or "").strip().lower()
        if app_status == "disabled" or app_instance.get("is_launchable") is False:
            return "device_unavailable"
        heartbeat = fetch_device_heartbeat(str(device.get("id") or ""))
        if heartbeat:
            hb_status = str(heartbeat.get("status") or "").strip().lower()
            if hb_status in {"offline", "unauthorized", "unknown"}:
                return "device_unavailable"
        if not self._adb_online(adb_serial):
            return "device_unavailable"
        return None

    def _adb_online(self, adb_serial: str) -> bool:
        code, out, _ = self._adb_run(adb_serial, ["get-state"], timeout_s=8.0)
        return code == 0 and (out or "").strip().lower() == "device"

    def _read_foreground_package(self, adb_serial: str) -> str | None:
        for argv in (
            ["shell", "dumpsys", "activity", "activities"],
            ["shell", "dumpsys", "window", "windows"],
            ["shell", "dumpsys", "activity", "top"],
        ):
            code, out, _ = self._adb_run(adb_serial, argv, timeout_s=12.0)
            if code != 0:
                continue
            package_name = parse_foreground_package(out)
            if package_name:
                return package_name
        return None

    def _capture_png(self, adb_serial: str) -> bytes | None:
        code, out, err = self._adb_run(adb_serial, ["exec-out", "screencap", "-p"], timeout_s=20.0, binary=True)
        if code != 0 or not out:
            log(
                "warning",
                "live_view_capture_failed",
                adb_serial_masked=mask_serial(adb_serial),
                error=(err or "")[:120],
            )
            return None
        if not out.startswith(b"\x89PNG"):
            return None
        return out

    def _adb_run(
        self,
        adb_serial: str,
        argv: list[str],
        *,
        timeout_s: float = 20.0,
        binary: bool = False,
    ) -> tuple[int, str | bytes, str]:
        cmd = [self.config.adb_path]
        if adb_serial:
            cmd.extend(["-s", adb_serial])
        cmd.extend(argv)
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=not binary,
                timeout=timeout_s,
                check=False,
            )
            stdout = completed.stdout if completed.stdout is not None else (b"" if binary else "")
            stderr = completed.stderr if isinstance(completed.stderr, str) else str(completed.stderr or "")
            return int(completed.returncode), stdout, stderr
        except Exception as exc:
            return 1, b"" if binary else "", str(exc)

    def _expire_session(self, row: dict[str, Any], *, failure_reason: str = "session_expired") -> None:
        session_id = str(row.get("id") or "")
        if not session_id:
            return
        update_session(session_id, {
            "status": "expired",
            "stopped_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "failure_reason": failure_reason,
        })
        self._cleanup_frame(session_id)
        insert_audit_event({
            "session_id": session_id,
            "event_type": "agent_stopped",
            "account_id": row.get("account_id"),
            "device_id": row.get("device_id"),
            "app_instance_id": row.get("app_instance_id"),
            "action": "agent_stopped",
            "metadata_safe": build_agent_metadata(
                host_id=self.config.host_id,
                transport="screenshot_polling",
                capture="adb_screencap_png",
                reason=failure_reason,
            ),
        })

    def _fail_session(
        self,
        row: dict[str, Any],
        *,
        reason: str,
        audit_action: str,
        message: str | None = None,
    ) -> None:
        session_id = str(row.get("id") or "")
        if not session_id:
            return
        update_session(session_id, {
            "status": "failed",
            "failure_reason": reason,
            "stopped_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        })
        self._cleanup_frame(session_id)
        insert_audit_event({
            "session_id": session_id,
            "event_type": audit_action,
            "account_id": row.get("account_id"),
            "device_id": row.get("device_id"),
            "app_instance_id": row.get("app_instance_id"),
            "action": audit_action,
            "metadata_safe": build_agent_metadata(
                host_id=self.config.host_id,
                transport="screenshot_polling",
                capture="adb_screencap_png",
                reason=reason,
            ),
        })
        log(
            "warning",
            "live_view_session_failed",
            session_id=session_id,
            reason=reason,
            message=message,
        )

    def _stop_session(self, row: dict[str, Any], *, reason: str, audit_action: str) -> None:
        session_id = str(row.get("id") or "")
        if not session_id:
            return
        update_session(session_id, {
            "status": "stopped",
            "stopped_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        })
        self._cleanup_frame(session_id)
        insert_audit_event({
            "session_id": session_id,
            "event_type": audit_action,
            "account_id": row.get("account_id"),
            "device_id": row.get("device_id"),
            "app_instance_id": row.get("app_instance_id"),
            "action": audit_action,
            "metadata_safe": build_agent_metadata(
                host_id=self.config.host_id,
                transport="screenshot_polling",
                capture="adb_screencap_png",
                reason=reason,
            ),
        })
        log("info", "live_view_session_stopped", session_id=session_id, reason=reason)

    def _finalize_terminal(self, row: dict[str, Any], *, reason: str) -> None:
        session_id = str(row.get("id") or "")
        if session_id:
            self._cleanup_frame(session_id)
            log("info", "live_view_session_terminal", session_id=session_id, reason=reason)


def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Live View host agent (LV-Web-2A screenshot polling)")
    parser.add_argument("--once", action="store_true", help="Poll once then exit")
    parser.add_argument("--env-file", default=".env", help="Optional .env file to load")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    load_env_file(args.env_file)
    config = LiveViewHostConfig.from_env()
    agent = LiveViewHostAgent(config)

    def _handle_signal(_signum: int, _frame: object) -> None:
        agent.request_shutdown()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once:
        if not config.enabled:
            log("info", "live_view_host_agent_disabled", host_id=config.host_id or None)
            return 0
        agent._poll_pending()
        if agent._active_session_id:
            agent._run_active_session(agent._active_session_id)
        return 0

    return agent.run_forever()


if __name__ == "__main__":
    sys.exit(main())
