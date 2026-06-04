from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from live_view_host_agent import LiveViewHostAgent, main
from live_view_host_agent_core import (
    LiveViewHostConfig,
    build_agent_metadata,
    frame_storage_object_path,
    local_frame_path,
    mask_serial,
    package_matches,
    parse_foreground_package,
    session_runtime_expired,
    should_claim_session,
    status_message_for_panel,
)
from live_view_host_store import frame_storage_object_path as store_frame_path


PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


class LiveViewHostAgentCoreTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        config = LiveViewHostConfig.from_env({"LIVE_VIEW_HOST_AGENT_ENABLED": "false"})
        self.assertFalse(config.enabled)

    def test_should_claim_pending_session(self) -> None:
        row = {
            "status": "pending",
            "host_id": "host-a",
            "expires_at": "2099-01-01T00:00:00Z",
        }
        ok, reason = should_claim_session(row, host_id="host-a")
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_host_mismatch_skip(self) -> None:
        row = {
            "status": "pending",
            "host_id": "host-b",
            "expires_at": "2099-01-01T00:00:00Z",
        }
        ok, reason = should_claim_session(row, host_id="host-a")
        self.assertFalse(ok)
        self.assertEqual(reason, "host_mismatch")

    def test_package_mismatch_guard(self) -> None:
        self.assertFalse(package_matches("com.instagram.androie", "com.instagram.android"))
        self.assertTrue(package_matches("com.instagram.androie", "com.instagram.androie"))

    def test_parse_foreground_package(self) -> None:
        sample = "mCurrentFocus=Window{abc u0 com.instagram.androie/com.instagram.mainactivity.MainActivity}"
        self.assertEqual(parse_foreground_package(sample), "com.instagram.androie")

    def test_session_runtime_expired(self) -> None:
        started = datetime.now(timezone.utc) - timedelta(seconds=700)
        row = {"started_at": started.replace(microsecond=0).isoformat().replace("+00:00", "Z")}
        self.assertTrue(session_runtime_expired(row, max_seconds=600))

    def test_safe_metadata_no_leak(self) -> None:
        metadata = build_agent_metadata(
            host_id="host-a",
            transport="screenshot_polling",
            capture="adb_screencap_png",
            extra={
                "username": "i_m_your_traker",
                "adb_serial": "TESTSERIAL0001",
                "token": "secret-token",
                "local_frame_file": "/tmp/frame.png",
            },
        )
        rendered = json.dumps(metadata).lower()
        self.assertEqual(metadata["username"], "i_m_your_traker")
        self.assertNotIn("adb_serial", rendered)
        self.assertNotIn("secret-token", rendered)
        self.assertNotIn("local_frame_file", rendered)

    def test_mask_serial(self) -> None:
        self.assertEqual(mask_serial("TESTSERIAL0001"), "...0001")

    def test_status_message_for_panel(self) -> None:
        self.assertEqual(status_message_for_panel("pending"), "Waiting for stream")
        self.assertEqual(
            status_message_for_panel("failed", failure_reason="live_view_wrong_package"),
            "live_view_wrong_package",
        )

    def test_frame_paths(self) -> None:
        session_id = "22222222-2222-4222-8222-222222222222"
        self.assertEqual(frame_storage_object_path(session_id), f"{session_id}/latest.png")
        self.assertEqual(store_frame_path(session_id), f"{session_id}/latest.png")
        self.assertTrue(local_frame_path(".local/live-view-frames", session_id).endswith(".png"))


class LiveViewHostAgentBehaviorTests(unittest.TestCase):
    def _config(self, **overrides: object) -> LiveViewHostConfig:
        base = {
            "enabled": True,
            "host_id": "host-a",
            "poll_interval_seconds": 0.5,
            "max_session_seconds": 600,
            "frame_dir": ".local/live-view-frames",
            "frame_upload_enabled": False,
            "storage_bucket": "live-view-frames",
            "adb_path": "adb",
        }
        base.update(overrides)
        return LiveViewHostConfig(**base)

    def test_main_disabled_exits_zero(self) -> None:
        with patch.dict(os.environ, {"LIVE_VIEW_HOST_AGENT_ENABLED": "false"}, clear=False):
            self.assertEqual(main(["--once"]), 0)

    def test_claim_fails_on_package_mismatch(self) -> None:
        agent = LiveViewHostAgent(self._config())
        row = {
            "id": "22222222-2222-4222-8222-222222222222",
            "account_id": "11111111-1111-4111-8111-111111111111",
            "device_id": "33333333-3333-4333-8333-333333333333",
            "app_instance_id": "44444444-4444-4444-8444-444444444444",
            "status": "starting",
            "metadata_safe": {},
        }
        device = {"id": row["device_id"], "adb_serial": "TESTSERIAL0001", "status": "online"}
        app_instance = {
            "id": row["app_instance_id"],
            "package_name": "com.instagram.androie",
            "status": "active",
            "is_launchable": True,
        }

        with patch("live_view_host_agent.fetch_session", return_value=row), patch(
            "live_view_host_agent.fetch_device",
            return_value=device,
        ), patch("live_view_host_agent.fetch_app_instance", return_value=app_instance), patch.object(
            agent,
            "_preflight_device",
            return_value=None,
        ), patch.object(
            agent,
            "_read_foreground_package",
            return_value="com.instagram.android",
        ), patch("live_view_host_agent.update_session") as update_session, patch(
            "live_view_host_agent.insert_audit_event",
        ) as insert_audit:
            agent._run_active_session(str(row["id"]))

        update_session.assert_called()
        failed_body = update_session.call_args.args[1]
        self.assertEqual(failed_body["status"], "failed")
        self.assertEqual(failed_body["failure_reason"], "live_view_wrong_package")
        insert_audit.assert_called()

    def test_device_offline_fails_session(self) -> None:
        agent = LiveViewHostAgent(self._config())
        row = {
            "id": "22222222-2222-4222-8222-222222222222",
            "device_id": "33333333-3333-4333-8333-333333333333",
            "app_instance_id": "44444444-4444-4444-8444-444444444444",
            "status": "starting",
        }
        device = {"id": row["device_id"], "adb_serial": "TESTSERIAL0001", "status": "offline"}
        app_instance = {"id": row["app_instance_id"], "package_name": "com.instagram.androie"}

        with patch("live_view_host_agent.fetch_session", return_value=row), patch(
            "live_view_host_agent.fetch_device",
            return_value=device,
        ), patch("live_view_host_agent.fetch_app_instance", return_value=app_instance), patch(
            "live_view_host_agent.update_session",
        ) as update_session, patch("live_view_host_agent.insert_audit_event"):
            agent._run_active_session(str(row["id"]))

        failed_body = update_session.call_args.args[1]
        self.assertEqual(failed_body["status"], "failed")
        self.assertEqual(failed_body["failure_reason"], "device_unavailable")

    def test_stop_request_exits_loop(self) -> None:
        agent = LiveViewHostAgent(self._config())
        row = {
            "id": "22222222-2222-4222-8222-222222222222",
            "status": "stopped",
            "device_id": "33333333-3333-4333-8333-333333333333",
            "app_instance_id": "44444444-4444-4444-8444-444444444444",
        }
        agent._active_session_id = str(row["id"])

        with patch("live_view_host_agent.fetch_session", return_value=row), patch(
            "live_view_host_agent.update_session",
        ) as update_session:
            agent._run_active_session(str(row["id"]))

        self.assertIsNone(agent._active_session_id)
        update_session.assert_not_called()

    def test_terminal_session_cleans_uploaded_frame_when_enabled(self) -> None:
        agent = LiveViewHostAgent(self._config(frame_upload_enabled=True))
        row = {
            "id": "22222222-2222-4222-8222-222222222222",
            "status": "stopped",
        }

        with patch("live_view_host_agent.delete_frame_png") as delete_frame:
            agent._finalize_terminal(row, reason="stopped")

        delete_frame.assert_called_once_with(str(row["id"]), bucket="live-view-frames")

    def test_shutdown_stops_active_session(self) -> None:
        agent = LiveViewHostAgent(self._config())
        row = {
            "id": "22222222-2222-4222-8222-222222222222",
            "status": "active",
            "device_id": "33333333-3333-4333-8333-333333333333",
            "app_instance_id": "44444444-4444-4444-8444-444444444444",
        }
        agent._active_session_id = str(row["id"])

        with patch("live_view_host_agent.fetch_session", return_value=row), patch(
            "live_view_host_agent.update_session",
        ) as update_session, patch("live_view_host_agent.insert_audit_event"):
            agent._stop_active_session_on_shutdown()

        self.assertIsNone(agent._active_session_id)
        stopped_body = update_session.call_args.args[1]
        self.assertEqual(stopped_body["status"], "stopped")

    def test_stop_cleans_uploaded_frame_when_enabled(self) -> None:
        agent = LiveViewHostAgent(self._config(frame_upload_enabled=True))
        row = {
            "id": "22222222-2222-4222-8222-222222222222",
            "status": "active",
            "device_id": "33333333-3333-4333-8333-333333333333",
            "app_instance_id": "44444444-4444-4444-8444-444444444444",
        }

        with patch("live_view_host_agent.update_session"), patch(
            "live_view_host_agent.delete_frame_png",
        ) as delete_frame, patch("live_view_host_agent.insert_audit_event"):
            agent._stop_session(row, reason="dashboard_stop", audit_action="agent_stopped")

        delete_frame.assert_called_once_with(str(row["id"]), bucket="live-view-frames")

    def test_ttl_expiry_marks_session_expired(self) -> None:
        agent = LiveViewHostAgent(self._config(max_session_seconds=60))
        started = datetime.now(timezone.utc) - timedelta(seconds=120)
        row = {
            "id": "22222222-2222-4222-8222-222222222222",
            "status": "active",
            "started_at": started.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "expires_at": "2099-01-01T00:00:00Z",
            "device_id": "33333333-3333-4333-8333-333333333333",
            "app_instance_id": "44444444-4444-4444-8444-444444444444",
        }
        agent._active_session_id = str(row["id"])

        with patch("live_view_host_agent.fetch_session", return_value=row), patch(
            "live_view_host_agent.update_session",
        ) as update_session, patch("live_view_host_agent.insert_audit_event"):
            agent._run_active_session(str(row["id"]))

        self.assertIsNone(agent._active_session_id)
        expired_body = update_session.call_args.args[1]
        self.assertEqual(expired_body["status"], "expired")
        self.assertEqual(expired_body["failure_reason"], "session_ttl_exceeded")

    def test_capture_loop_writes_local_frame(self) -> None:
        agent = LiveViewHostAgent(self._config(frame_upload_enabled=False))
        row = {
            "id": "22222222-2222-4222-8222-222222222222",
            "status": "starting",
            "started_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "expires_at": "2099-01-01T00:00:00Z",
            "device_id": "33333333-3333-4333-8333-333333333333",
            "app_instance_id": "44444444-4444-4444-8444-444444444444",
            "metadata_safe": {},
        }
        device = {"id": row["device_id"], "adb_serial": "TESTSERIAL0001", "status": "online"}
        app_instance = {
            "id": row["app_instance_id"],
            "package_name": "com.instagram.androie",
            "status": "active",
            "is_launchable": True,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            agent.config = self._config(frame_dir=tmpdir, frame_upload_enabled=False)
            with patch("live_view_host_agent.fetch_session", side_effect=[row, {**row, "status": "active"}]), patch(
                "live_view_host_agent.fetch_device",
                return_value=device,
            ), patch("live_view_host_agent.fetch_app_instance", return_value=app_instance), patch.object(
                agent,
                "_preflight_device",
                return_value=None,
            ), patch.object(
                agent,
                "_read_foreground_package",
                return_value="com.instagram.androie",
            ), patch.object(agent, "_capture_png", return_value=PNG_HEADER), patch(
                "live_view_host_agent.update_session",
            ) as update_session, patch("live_view_host_agent.insert_audit_event"):
                agent._run_active_session(str(row["id"]))

            frame_file = local_frame_path(tmpdir, str(row["id"]))
            self.assertTrue(os.path.exists(frame_file))
            active_body = update_session.call_args.args[1]
            self.assertEqual(active_body["status"], "active")


if __name__ == "__main__":
    unittest.main()
