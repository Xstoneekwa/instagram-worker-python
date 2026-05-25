from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import incident_notifications


def _incident(
    incident_id: str,
    *,
    severity: str = "critical",
    status: str = "open",
    last_seen_at: str = "2026-05-25T12:00:00+00:00",
) -> dict:
    return {
        "id": incident_id,
        "severity": severity,
        "status": status,
        "incident_type": "active_instagram_account_mismatch",
        "account_id": "42c625c2-e761-4100-8a9d-7ae1373de97d",
        "account_username": "cinema_catchup",
        "occurrence_count": 2,
        "last_seen_at": last_seen_at,
        "action_required": "Verify logged-in Instagram account.",
        "assistant_message": "Verify active Instagram account.",
        "admin_message": "Expected cinema_catchup but detected other.",
        "run_id": "50a1ef82-5b4c-47c3-ab4c-eefa4b325338",
    }


class IncidentNotificationsTest(unittest.TestCase):
    def test_disabled_no_op(self) -> None:
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", False, create=True),
            patch.object(
                incident_notifications.supabase_client,
                "load_account_incidents_to_notify",
            ) as load,
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertFalse(out["dispatched"])
        self.assertEqual(out["reason"], "disabled")
        load.assert_not_called()

    def test_parse_channels(self) -> None:
        self.assertEqual(
            incident_notifications.parse_notification_channels("slack, discord,slack,unknown"),
            ["slack", "discord"],
        )
        self.assertEqual(incident_notifications.parse_notification_channels([]), ["slack"])

    def test_severity_filter(self) -> None:
        self.assertTrue(incident_notifications.is_severity_at_least("critical", "warning"))
        self.assertTrue(incident_notifications.is_severity_at_least("warning", "warning"))
        self.assertFalse(incident_notifications.is_severity_at_least("info", "warning"))

    def test_ignored_resolved_skipped(self) -> None:
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", True, create=True),
            patch.object(
                incident_notifications.supabase_client,
                "load_account_incidents_to_notify",
                return_value=[_incident("ignored-1", status="ignored"), _incident("resolved-1", status="resolved")],
            ),
            patch.object(
                incident_notifications.supabase_client,
                "load_existing_incident_notifications_by_delivery_keys",
                return_value={},
            ),
            patch.object(
                incident_notifications.supabase_client,
                "create_account_incident_notification",
            ) as create,
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertTrue(out["dispatched"])
        self.assertEqual(out["selected_count"], 0)
        create.assert_not_called()

    def test_delivery_key_generated(self) -> None:
        self.assertEqual(
            incident_notifications.build_delivery_key("Slack", "incident-1"),
            "slack:incident-1:opened",
        )

    def test_duplicate_delivery_skipped(self) -> None:
        incident = _incident("incident-1")
        key = incident_notifications.build_delivery_key("slack", "incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", True, create=True),
            patch.object(
                incident_notifications.supabase_client,
                "load_account_incidents_to_notify",
                return_value=[incident],
            ),
            patch.object(
                incident_notifications.supabase_client,
                "load_existing_incident_notifications_by_delivery_keys",
                return_value={key: {"delivery_key": key}},
            ),
            patch.object(
                incident_notifications.supabase_client,
                "create_account_incident_notification",
            ) as create,
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertEqual(out["skipped_duplicate_count"], 1)
        self.assertEqual(out["created_count"], 0)
        create.assert_not_called()

    def test_dry_run_creates_skipped_notification_payload(self) -> None:
        incident = _incident("incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack", create=True),
            patch.object(
                incident_notifications.supabase_client,
                "load_account_incidents_to_notify",
                return_value=[incident],
            ),
            patch.object(
                incident_notifications.supabase_client,
                "load_existing_incident_notifications_by_delivery_keys",
                return_value={},
            ),
            patch.object(
                incident_notifications.supabase_client,
                "create_account_incident_notification",
                return_value={"id": "notification-1"},
            ) as create,
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertTrue(out["dispatched"])
        self.assertEqual(out["created_count"], 1)
        row = create.call_args.args[0]
        self.assertEqual(row["status"], "skipped")
        self.assertEqual(row["target"], "dry-run")
        self.assertEqual(row["attempt_count"], 0)
        self.assertTrue(row["metadata"]["dry_run"])
        self.assertEqual(row["metadata"]["dispatcher_version"], "orf-4b")
        self.assertIn("channel_payload", row["payload"])

    def test_max_per_run_respected(self) -> None:
        incidents = [
            _incident("incident-1", severity="warning"),
            _incident("incident-2", severity="critical"),
            _incident("incident-3", severity="error"),
        ]
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", True, create=True),
            patch.object(
                incident_notifications.supabase_client,
                "load_account_incidents_to_notify",
                return_value=incidents,
            ),
            patch.object(
                incident_notifications.supabase_client,
                "load_existing_incident_notifications_by_delivery_keys",
                return_value={},
            ),
            patch.object(
                incident_notifications.supabase_client,
                "create_account_incident_notification",
                return_value={"id": "notification-1"},
            ) as create,
        ):
            out = incident_notifications.dispatch_account_incident_notifications(max_per_run=2)
        self.assertEqual(out["selected_count"], 2)
        self.assertEqual(create.call_count, 2)
        first_row = create.call_args_list[0].args[0]
        self.assertIn("[CRITICAL]", first_row["payload"]["message"]["title"])

    def test_redaction_prevents_secrets(self) -> None:
        payload = incident_notifications.build_incident_notification_payload(
            {
                **_incident("incident-1"),
                "admin_message": "service_role token should not leak",
                "metadata": {"raw_xml": "<node />", "safe": "ok"},
            }
        )
        text = str(payload).lower()
        self.assertNotIn("service_role token should not leak", text)
        self.assertNotIn("<node", text)

    def test_slack_payload_builder(self) -> None:
        payload = incident_notifications.build_slack_payload({"title": "T", "text": "hello"})
        self.assertEqual(payload, {"text": "hello"})

    def test_discord_payload_builder(self) -> None:
        payload = incident_notifications.build_discord_payload({"title": "T", "text": "hello"})
        self.assertEqual(payload, {"content": "hello"})

    def test_fail_open_on_supabase_error(self) -> None:
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_FAIL_OPEN", True, create=True),
            patch.object(
                incident_notifications.supabase_client,
                "load_account_incidents_to_notify",
                side_effect=RuntimeError("db down"),
            ),
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertFalse(out["dispatched"])
        self.assertEqual(out["reason"], "dispatch_failed")
        self.assertEqual(out["errors_count"], 1)

    def test_no_webhook_http_call_in_dry_run(self) -> None:
        incident = _incident("incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", True, create=True),
            patch.object(
                incident_notifications.supabase_client,
                "load_account_incidents_to_notify",
                return_value=[incident],
            ),
            patch.object(
                incident_notifications.supabase_client,
                "load_existing_incident_notifications_by_delivery_keys",
                return_value={},
            ),
            patch.object(
                incident_notifications.supabase_client,
                "create_account_incident_notification",
                return_value={"id": "notification-1"},
            ),
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertTrue(out["dry_run"])
        self.assertFalse(hasattr(incident_notifications, "requests"))
        self.assertFalse(hasattr(incident_notifications, "httpx"))

    def test_dry_run_false_missing_webhook_creates_failed_without_http(self) -> None:
        incident = _incident("incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "SLACK_WEBHOOK_URL", "", create=True),
            patch.object(
                incident_notifications.supabase_client,
                "load_account_incidents_to_notify",
                return_value=[incident],
            ),
            patch.object(
                incident_notifications.supabase_client,
                "load_existing_incident_notifications_by_delivery_keys",
                return_value={},
            ),
            patch.object(
                incident_notifications.supabase_client,
                "create_account_incident_notification",
                return_value={"id": "notification-1"},
            ) as create,
            patch.object(incident_notifications, "_post_json_webhook") as post,
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertTrue(out["dispatched"])
        self.assertFalse(out["dry_run"])
        self.assertEqual(out["failed_count"], 1)
        row = create.call_args.args[0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["last_error"], "config_missing_webhook")
        post.assert_not_called()

    def test_real_send_slack_success_marks_sent(self) -> None:
        incident = _incident("incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(
                incident_notifications.config,
                "SLACK_WEBHOOK_URL",
                "https://hooks.slack.com/services/SECRET",
                create=True,
            ),
            patch.object(
                incident_notifications.supabase_client,
                "load_account_incidents_to_notify",
                return_value=[incident],
            ),
            patch.object(
                incident_notifications.supabase_client,
                "load_existing_incident_notifications_by_delivery_keys",
                return_value={},
            ),
            patch.object(
                incident_notifications.supabase_client,
                "create_account_incident_notification",
                return_value={"id": "notification-1"},
            ) as create,
            patch.object(
                incident_notifications.supabase_client,
                "update_account_incident_notification",
                return_value={"id": "notification-1", "status": "sent"},
            ) as update,
            patch.object(
                incident_notifications,
                "_post_json_webhook",
                return_value={"ok": True, "response_status": 200, "response_body_preview": "ok"},
            ) as post,
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertTrue(out["dispatched"])
        self.assertFalse(out["dry_run"])
        self.assertTrue(out["real_send_enabled"])
        self.assertEqual(out["sent_count"], 1)
        self.assertEqual(out["attempted_count"], 1)
        pending = create.call_args.args[0]
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["target"], "slack")
        self.assertFalse(pending["metadata"]["dry_run"])
        self.assertEqual(pending["metadata"]["dispatcher_version"], "orf-4c")
        update_payload = update.call_args.args[1]
        self.assertEqual(update_payload["status"], "sent")
        self.assertEqual(update_payload["response_status"], 200)
        self.assertNotIn("hooks.slack", str(out).lower())
        self.assertNotIn("hooks.slack", str(pending).lower())
        post.assert_called_once()

    def test_real_send_discord_204_marks_sent(self) -> None:
        incident = _incident("incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/SECRET", create=True),
            patch.object(
                incident_notifications.supabase_client,
                "load_account_incidents_to_notify",
                return_value=[incident],
            ),
            patch.object(
                incident_notifications.supabase_client,
                "load_existing_incident_notifications_by_delivery_keys",
                return_value={},
            ),
            patch.object(
                incident_notifications.supabase_client,
                "create_account_incident_notification",
                return_value={"id": "notification-1"},
            ),
            patch.object(
                incident_notifications.supabase_client,
                "update_account_incident_notification",
                return_value={"id": "notification-1", "status": "sent"},
            ) as update,
            patch.object(
                incident_notifications,
                "_post_json_webhook",
                return_value={"ok": True, "response_status": 204, "response_body_preview": ""},
            ),
        ):
            out = incident_notifications.dispatch_account_incident_notifications(channels=["discord"])
        self.assertEqual(out["sent_count"], 1)
        self.assertEqual(update.call_args.args[1]["status"], "sent")
        self.assertEqual(update.call_args.args[1]["response_status"], 204)

    def test_real_send_http_500_marks_failed(self) -> None:
        incident = _incident("incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/SECRET", create=True),
            patch.object(incident_notifications.supabase_client, "load_account_incidents_to_notify", return_value=[incident]),
            patch.object(incident_notifications.supabase_client, "load_existing_incident_notifications_by_delivery_keys", return_value={}),
            patch.object(incident_notifications.supabase_client, "create_account_incident_notification", return_value={"id": "notification-1"}),
            patch.object(incident_notifications.supabase_client, "update_account_incident_notification", return_value={}) as update,
            patch.object(
                incident_notifications,
                "_post_json_webhook",
                return_value={
                    "ok": False,
                    "reason": "http_status_500",
                    "response_status": 500,
                    "response_body_preview": "server error",
                    "last_error": "http_status_500",
                },
            ),
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertEqual(out["failed_count"], 1)
        update_payload = update.call_args.args[1]
        self.assertEqual(update_payload["status"], "failed")
        self.assertEqual(update_payload["response_status"], 500)
        self.assertEqual(update_payload["last_error"], "http_status_500")

    def test_real_send_exception_marks_failed_fail_open(self) -> None:
        incident = _incident("incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_FAIL_OPEN", True, create=True),
            patch.object(incident_notifications.config, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/SECRET", create=True),
            patch.object(incident_notifications.supabase_client, "load_account_incidents_to_notify", return_value=[incident]),
            patch.object(incident_notifications.supabase_client, "load_existing_incident_notifications_by_delivery_keys", return_value={}),
            patch.object(incident_notifications.supabase_client, "create_account_incident_notification", return_value={"id": "notification-1"}),
            patch.object(incident_notifications.supabase_client, "update_account_incident_notification", return_value={}) as update,
            patch.object(
                incident_notifications,
                "_post_json_webhook",
                return_value={
                    "ok": False,
                    "reason": "webhook_request_failed",
                    "last_error": "https://hooks.slack.com/services/SECRET timed out",
                },
            ),
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertTrue(out["dispatched"])
        self.assertEqual(out["failed_count"], 1)
        update_payload = update.call_args.args[1]
        self.assertEqual(update_payload["status"], "failed")
        self.assertNotIn("hooks.slack", str(update_payload).lower())

    def test_post_json_webhook_sends_user_agent_header(self) -> None:
        with (
            patch.object(incident_notifications.urlrequest, "Request") as mock_request,
            patch.object(incident_notifications.urlrequest, "urlopen") as mock_urlopen,
        ):
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.__exit__.return_value = False
            mock_resp.status = 204
            mock_resp.read.return_value = b""
            mock_urlopen.return_value = mock_resp
            incident_notifications._post_json_webhook(
                "https://discord.com/api/webhooks/test",
                {"content": "x"},
                5,
            )
        headers = mock_request.call_args.kwargs["headers"]
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(
            headers["User-Agent"],
            "PhoneFarmIncidentNotifier/1.0 (+https://localhost)",
        )

    def test_response_preview_and_last_error_redacted_truncated(self) -> None:
        long_secret = "https://hooks.slack.com/services/SECRET " + ("x" * 800)
        self.assertEqual(incident_notifications._truncate_redact(long_secret), "[redacted]")
        self.assertLessEqual(len(incident_notifications._truncate_redact("x" * 800)), 500)

    def test_duplicate_delivery_key_skips_real_send(self) -> None:
        incident = _incident("incident-1")
        key = incident_notifications.build_delivery_key("slack", "incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/SECRET", create=True),
            patch.object(incident_notifications.supabase_client, "load_account_incidents_to_notify", return_value=[incident]),
            patch.object(
                incident_notifications.supabase_client,
                "load_existing_incident_notifications_by_delivery_keys",
                return_value={key: {"delivery_key": key}},
            ),
            patch.object(incident_notifications.supabase_client, "create_account_incident_notification") as create,
            patch.object(incident_notifications, "_post_json_webhook") as post,
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertEqual(out["skipped_duplicate_count"], 1)
        create.assert_not_called()
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
