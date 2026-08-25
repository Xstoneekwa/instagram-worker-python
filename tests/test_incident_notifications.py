from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import incident_notification_channel_config as channel_config
import incident_notifications

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_SCRIPT = REPO_ROOT / "scripts" / "dispatch_incident_notifications.py"


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


def _package_mismatch_incident(incident_id: str) -> dict:
    return {
        **_incident(incident_id),
        "incident_type": "login_package_mismatch",
        "account_username": "i_m_your_traker",
        "action_required": "Review device assignment and Instagram clone before retry.",
        "assistant_message": "Wrong app/clone detected for this account. Review device assignment before retry.",
        "admin_message": (
            "Login package mismatch detected. Expected package com.instagram.androie, "
            "actual foreground package com.instagram.android. Device RFGL***VCKE."
        ),
        "metadata": {
            "expected_package_name": "com.instagram.androie",
            "actual_foreground_package": "com.instagram.android",
            "adb_serial_masked": "RFGL***VCKE",
            "password": "do-not-leak",
            "secret_ref": "do-not-leak",
            "raw_xml": "<node />",
        },
    }


class IncidentNotificationsTest(unittest.TestCase):
    def setUp(self) -> None:
        # Default the suite to legacy env-webhook mode so pre-P2 tests keep
        # exercising the env toggles; canonical-settings tests opt back in.
        self._canonical_patch = patch.object(
            incident_notifications.config,
            "INCIDENT_NOTIFICATIONS_CANONICAL_SETTINGS",
            False,
            create=True,
        )
        self._canonical_patch.start()

    def tearDown(self) -> None:
        self._canonical_patch.stop()

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
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack", create=True),
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
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack", create=True),
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
        self.assertEqual(out["selected_channels"], ["slack"])
        self.assertEqual(out["enabled_channels"], ["slack"])
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
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack", create=True),
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

    def test_open_incident_notification_explains_two_step_recovery(self) -> None:
        incident = {
            **_incident("incident-1"),
            "device_id": "a22c6048-9dde-42db-88e1-94f4619361df",
            "reason": "account_session_phase_not_terminal",
        }
        payload = incident_notifications.build_incident_notification_payload(incident)
        self.assertEqual(payload["reason_code"], "account_session_phase_not_terminal")
        self.assertEqual(payload["detected_at"], incident["last_seen_at"])
        self.assertTrue(payload["device_id_short"])
        self.assertIn("Resolve the incident", payload["required_operator_action"])
        self.assertIn("explicitly set the account to Active", payload["text"])

    def test_package_mismatch_notification_payload_safe_for_dry_run(self) -> None:
        payload = incident_notifications.build_incident_notification_payload(_package_mismatch_incident("incident-1"))
        text = json.dumps(payload, sort_keys=True)
        self.assertIn("login_package_mismatch", text)
        self.assertIn("com.instagram.androie", text)
        self.assertIn("com.instagram.android", text)
        self.assertIn("RFGL***VCKE", text)
        self.assertNotIn("do-not-leak", text)
        self.assertNotIn("<node", text)

    def test_dashboard_link_included_when_base_url_configured(self) -> None:
        with patch.object(
            incident_notifications.config,
            "INCIDENT_NOTIFICATIONS_DASHBOARD_BASE_URL",
            "https://admin.example.com",
            create=True,
        ):
            payload = incident_notifications.build_incident_notification_payload(_incident("incident-1"))
        self.assertEqual(
            payload["dashboard_url"],
            "https://admin.example.com/instagram-dashboard/incidents?incident_id=incident-1",
        )
        self.assertNotIn("https://admin.example.com", payload["text"])
        slack = incident_notifications.build_slack_payload(payload)
        self.assertIn("<https://admin.example.com/instagram-dashboard/incidents?incident_id=incident-1|Open Incidents/Actions>", slack["text"])

    def test_dashboard_link_uses_canonical_production_url_without_override(self) -> None:
        with patch.object(
            incident_notifications.config,
            "INCIDENT_NOTIFICATIONS_DASHBOARD_BASE_URL",
            "",
            create=True,
        ):
            payload = incident_notifications.build_incident_notification_payload(_incident("incident-1"))
        self.assertEqual(
            payload["dashboard_url"],
            "https://www.boostmybusinesses.com/instagram-dashboard/incidents?incident_id=incident-1",
        )

    def test_slack_payload_builder(self) -> None:
        payload = incident_notifications.build_slack_payload({
            "title": "T",
            "text": "hello",
            "dashboard_url": "https://www.boostmybusinesses.com/instagram-dashboard/incidents",
        })
        self.assertEqual(
            payload,
            {"text": "hello\n<https://www.boostmybusinesses.com/instagram-dashboard/incidents|Open Incidents/Actions>"},
        )

    def test_discord_payload_builder(self) -> None:
        payload = incident_notifications.build_discord_payload({
            "title": "T",
            "text": "hello",
            "dashboard_url": "https://www.boostmybusinesses.com/instagram-dashboard/incidents",
        })
        self.assertEqual(
            payload,
            {"content": "hello\n[Open Incidents/Actions](https://www.boostmybusinesses.com/instagram-dashboard/incidents)"},
        )

    def test_auto_login_discord_payload_is_precise_correlated_and_redacted(self) -> None:
        incident = _incident("incident-auto-login")
        incident.update(
            {
                "severity": "error",
                "incident_type": "auto_login_failed",
                "account_username": "expected_account",
                "reason": "wrong_suggested_account_requires_admin_review",
                "failure_reason": "wrong_suggested_account_requires_admin_review",
                "action_required": "Use the safe alternate login route before retrying.",
                "admin_message": "Auto Login routing stopped safely.",
                "run_id": "22222222-2222-4222-8222-222222222222",
                "device_id": "d663648c-800c-48a6-8684-d8605218baa5",
                "metadata": {
                    "domain": "auto_login",
                    "phase": "route_suggested_account",
                    "reason_code": "wrong_suggested_account_requires_admin_review",
                    "operator_label": "Auto Login failed",
                    "retryable": True,
                    "request_id": "11111111-1111-4111-8111-111111111111",
                    "app_instance_id": "88e37799-890e-4776-8763-bb0b4180fa43",
                    "password": "must-not-leak",
                    "email_code": "123456",
                },
            }
        )
        payload = incident_notifications.build_incident_notification_payload(incident)
        discord = incident_notifications.build_discord_payload(payload)["content"]
        self.assertIn("[AUTO LOGIN ERROR] Auto Login failed", discord)
        self.assertIn("Phase: route_suggested_account", discord)
        self.assertIn("Reason: wrong_suggested_account_requires_admin_review", discord)
        self.assertIn("Request: 11111111", discord)
        self.assertIn("Run: 22222222", discord)
        self.assertIn("Retry: yes", discord)
        self.assertNotIn("run_worker_failure", discord)
        self.assertNotIn("no structured reason", discord.lower())
        self.assertNotIn("must-not-leak", discord)
        self.assertNotIn("123456", discord)

    def test_wrong_password_notification_consumes_specific_incident_without_notifier_change(self) -> None:
        incident = _incident("incident-wrong-password")
        incident.update(
            {
                "severity": "error",
                "incident_type": "auto_login_failed",
                "account_username": "autom_atism",
                "reason": "instagram_credentials_rejected",
                "failure_reason": "instagram_credentials_rejected",
                "action_required": "update_instagram_password",
                "admin_message": "Instagram rejected the active credential revision for this account.",
                "metadata": {
                    "domain": "auto_login",
                    "phase": "submit_credentials",
                    "reason_code": "instagram_credentials_rejected",
                    "operator_label": "Instagram credentials rejected",
                    "retryable": False,
                },
            }
        )

        payload = incident_notifications.build_incident_notification_payload(incident)
        text = payload["text"]
        self.assertIn("Account: autom_atism", text)
        self.assertIn("Phase: submit_credentials", text)
        self.assertIn("Reason: instagram_credentials_rejected", text)
        self.assertIn("Action: update_instagram_password", text)
        self.assertIn("Retry: no", text)

    def test_all_consultable_runtime_incidents_use_the_shared_cta_builder(self) -> None:
        for incident_type in (
            "welcome_surface_unstable",
            "followers_suggestions_boundary_recovery_failed",
            "run_worker_failure",
        ):
            incident = _incident(f"incident-{incident_type}")
            incident["incident_type"] = incident_type
            payload = incident_notifications.build_incident_notification_payload(incident)
            slack = incident_notifications.build_notification_channel_payload("slack", payload)["text"]
            discord = incident_notifications.build_notification_channel_payload("discord", payload)["content"]
            self.assertIn("|Open Incidents/Actions>", slack)
            self.assertIn("[Open Incidents/Actions](", discord)
            self.assertNotRegex(slack, r"\nhttps?://")
            self.assertNotRegex(discord, r"\nhttps?://")

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
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CANONICAL_SETTINGS", True, create=True),
            patch.object(
                channel_config,
                "load_channel_settings_row",
                return_value={"enabled": True, "configured": False, "webhook_ciphertext": None},
            ),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack", create=True),
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
        self.assertEqual(row["last_error"], "channel_not_configured")
        post.assert_not_called()

    def test_real_send_slack_success_marks_sent(self) -> None:
        incident = _incident("incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack", create=True),
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
        self.assertEqual(pending["metadata"]["dispatcher_version"], "orf-4d")
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
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack", create=True),
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
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack", create=True),
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

    def test_resolve_dispatch_channels_applies_toggles(self) -> None:
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack,discord", create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_SLACK_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DISCORD_ENABLED", False, create=True),
        ):
            allowed, enabled = incident_notifications.resolve_dispatch_channels()
        self.assertEqual(allowed, ["slack", "discord"])
        self.assertEqual(enabled, ["slack"])

    def test_slack_toggle_off_skips_slack_without_delivery_row(self) -> None:
        incident = _incident("incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack,discord", create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_SLACK_ENABLED", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DISCORD_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/SECRET", create=True),
            patch.object(incident_notifications.supabase_client, "load_account_incidents_to_notify", return_value=[incident]),
            patch.object(incident_notifications.supabase_client, "load_existing_incident_notifications_by_delivery_keys", return_value={}),
            patch.object(incident_notifications.supabase_client, "create_account_incident_notification", return_value={"id": "n1"}) as create,
            patch.object(incident_notifications.supabase_client, "update_account_incident_notification", return_value={}),
            patch.object(
                incident_notifications,
                "_post_json_webhook",
                return_value={"ok": True, "response_status": 204, "response_body_preview": ""},
            ) as post,
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertEqual(out["skipped_channel_disabled_count"], 1)
        self.assertEqual(out["sent_count"], 1)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(create.call_args.args[0]["channel"], "discord")
        post.assert_called_once()

    def test_discord_toggle_off_sends_slack_only(self) -> None:
        incident = _incident("incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack,discord", create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_SLACK_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DISCORD_ENABLED", False, create=True),
            patch.object(incident_notifications.config, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/SECRET", create=True),
            patch.object(incident_notifications.supabase_client, "load_account_incidents_to_notify", return_value=[incident]),
            patch.object(incident_notifications.supabase_client, "load_existing_incident_notifications_by_delivery_keys", return_value={}),
            patch.object(incident_notifications.supabase_client, "create_account_incident_notification", return_value={"id": "n1"}) as create,
            patch.object(incident_notifications.supabase_client, "update_account_incident_notification", return_value={}),
            patch.object(
                incident_notifications,
                "_post_json_webhook",
                return_value={"ok": True, "response_status": 200, "response_body_preview": "ok"},
            ) as post,
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertEqual(out["skipped_channel_disabled_count"], 1)
        self.assertEqual(out["sent_count"], 1)
        self.assertEqual(create.call_args.args[0]["channel"], "slack")
        post.assert_called_once()

    def test_both_channel_toggles_off_no_send(self) -> None:
        incident = _incident("incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack,discord", create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_SLACK_ENABLED", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DISCORD_ENABLED", False, create=True),
            patch.object(incident_notifications.supabase_client, "load_account_incidents_to_notify") as load,
            patch.object(incident_notifications.supabase_client, "create_account_incident_notification") as create,
            patch.object(incident_notifications, "_post_json_webhook") as post,
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertTrue(out["dispatched"])
        self.assertEqual(out["reason"], "channels_disabled")
        self.assertEqual(out["enabled_channels"], [])
        self.assertEqual(out["selected_channels"], ["slack", "discord"])
        load.assert_not_called()
        create.assert_not_called()
        post.assert_not_called()

    def test_disabled_channel_does_not_block_later_send(self) -> None:
        incident = _incident("incident-1")
        slack_key = incident_notifications.build_delivery_key("slack", "incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack", create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_SLACK_ENABLED", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DISCORD_ENABLED", True, create=True),
            patch.object(incident_notifications.supabase_client, "load_account_incidents_to_notify", return_value=[incident]),
            patch.object(incident_notifications.supabase_client, "load_existing_incident_notifications_by_delivery_keys", return_value={}),
            patch.object(incident_notifications.supabase_client, "create_account_incident_notification") as create,
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertEqual(out["reason"], "channels_disabled")
        create.assert_not_called()
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack", create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_SLACK_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/SECRET", create=True),
            patch.object(incident_notifications.supabase_client, "load_account_incidents_to_notify", return_value=[incident]),
            patch.object(incident_notifications.supabase_client, "load_existing_incident_notifications_by_delivery_keys", return_value={}),
            patch.object(incident_notifications.supabase_client, "create_account_incident_notification", return_value={"id": "n1"}),
            patch.object(incident_notifications.supabase_client, "update_account_incident_notification", return_value={}),
            patch.object(
                incident_notifications,
                "_post_json_webhook",
                return_value={"ok": True, "response_status": 200, "response_body_preview": "ok"},
            ),
        ):
            out2 = incident_notifications.dispatch_account_incident_notifications()
        self.assertEqual(out2["sent_count"], 1)

    def test_cli_disabled_exits_zero_without_dispatch(self) -> None:
        env = {**os.environ, "INCIDENT_NOTIFICATIONS_ENABLED": "false"}
        proc = subprocess.run(
            [sys.executable, str(CLI_SCRIPT)],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        summary = json.loads(proc.stdout.strip())
        self.assertEqual(summary["reason"], "disabled")
        self.assertNotIn("hooks.slack.com", proc.stdout.lower())
        self.assertNotIn("discord.com/api/webhooks", proc.stdout.lower())

    def test_cli_summary_redacts_webhook_markers(self) -> None:
        import importlib.util

        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_FAIL_OPEN", True, create=True),
            patch.object(
                incident_notifications.supabase_client,
                "load_account_incidents_to_notify",
                side_effect=RuntimeError("https://hooks.slack.com/services/SECRET failed"),
            ),
        ):
            spec = importlib.util.spec_from_file_location("dispatch_cli", CLI_SCRIPT)
            cli_mod = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(cli_mod)
            buf: list[str] = []
            with patch("builtins.print", side_effect=lambda *a, **k: buf.append(" ".join(str(x) for x in a))):
                code = cli_mod.main()
        self.assertEqual(code, 0)
        payload = json.loads(buf[-1])
        self.assertEqual(payload["reason"], "dispatch_failed")
        self.assertNotIn("hooks.slack", str(payload).lower())

    def test_duplicate_delivery_key_skips_real_send(self) -> None:
        incident = _incident("incident-1")
        key = incident_notifications.build_delivery_key("slack", "incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack", create=True),
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


class CanonicalNotifierP2Test(unittest.TestCase):
    """P2: canonical channel settings, dual-channel delivery, bounded retries."""

    def _canonical_channel_rows(self):
        return patch.object(
            channel_config,
            "resolve_effective_channel_config",
            side_effect=lambda channel: {
                "channel": channel,
                "row_present": True,
                "enabled": True,
                "configured": True,
                "webhook_url": f"https://example.invalid/{channel}",
                "source": "canonical",
                "send_allowed": True,
                "reason": None,
            },
        )

    def test_slack_and_discord_each_delivered_once(self) -> None:
        incident = _incident("incident-1")
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CANONICAL_SETTINGS", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack,discord", create=True),
            self._canonical_channel_rows(),
            patch.object(incident_notifications.supabase_client, "load_account_incidents_to_notify", return_value=[incident]),
            patch.object(incident_notifications.supabase_client, "load_existing_incident_notifications_by_delivery_keys", return_value={}),
            patch.object(
                incident_notifications.supabase_client,
                "create_account_incident_notification",
                side_effect=[{"id": "n-slack"}, {"id": "n-discord"}],
            ) as create,
            patch.object(incident_notifications.supabase_client, "update_account_incident_notification", return_value={}) as update,
            patch.object(
                incident_notifications,
                "_post_json_webhook",
                return_value={"ok": True, "response_status": 200, "response_body_preview": "ok"},
            ) as post,
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertEqual(out["sent_count"], 2)
        self.assertEqual(out["failed_count"], 0)
        self.assertEqual(out["skipped_duplicate_count"], 0)
        self.assertEqual(create.call_count, 2)
        self.assertEqual(post.call_count, 2)
        channels = sorted(row.args[0]["channel"] for row in create.call_args_list)
        self.assertEqual(channels, ["discord", "slack"])
        for call in update.call_args_list:
            self.assertEqual(call.args[1]["status"], "sent")

    def test_failed_row_retried_within_attempt_budget(self) -> None:
        incident = _incident("incident-1")
        key = incident_notifications.build_delivery_key("slack", "incident-1")
        existing = {
            key: {
                "id": "n-slack",
                "delivery_key": key,
                "status": "failed",
                "attempt_count": 1,
            }
        }
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CANONICAL_SETTINGS", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack", create=True),
            self._canonical_channel_rows(),
            patch.object(incident_notifications.supabase_client, "load_account_incidents_to_notify", return_value=[incident]),
            patch.object(incident_notifications.supabase_client, "load_existing_incident_notifications_by_delivery_keys", return_value=existing),
            patch.object(incident_notifications.supabase_client, "create_account_incident_notification") as create,
            patch.object(incident_notifications.supabase_client, "update_account_incident_notification", return_value={}) as update,
            patch.object(
                incident_notifications,
                "_post_json_webhook",
                return_value={"ok": True, "response_status": 200, "response_body_preview": "ok"},
            ),
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertEqual(out["retried_count"], 1)
        self.assertEqual(out["sent_count"], 1)
        create.assert_not_called()
        update_payload = update.call_args.args[1]
        self.assertEqual(update.call_args.args[0], "n-slack")
        self.assertEqual(update_payload["status"], "sent")
        self.assertEqual(update_payload["attempt_count"], 2)

    def test_failed_row_beyond_attempt_budget_not_retried(self) -> None:
        incident = _incident("incident-1")
        key = incident_notifications.build_delivery_key("slack", "incident-1")
        existing = {
            key: {
                "id": "n-slack",
                "delivery_key": key,
                "status": "failed",
                "attempt_count": 3,
            }
        }
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CANONICAL_SETTINGS", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack", create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_MAX_ATTEMPTS", 3, create=True),
            self._canonical_channel_rows(),
            patch.object(incident_notifications.supabase_client, "load_account_incidents_to_notify", return_value=[incident]),
            patch.object(incident_notifications.supabase_client, "load_existing_incident_notifications_by_delivery_keys", return_value=existing),
            patch.object(incident_notifications.supabase_client, "create_account_incident_notification") as create,
            patch.object(incident_notifications, "_post_json_webhook") as post,
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertEqual(out["retry_exhausted_count"], 1)
        self.assertEqual(out["retried_count"], 0)
        create.assert_not_called()
        post.assert_not_called()

    def test_sent_row_never_duplicated_per_channel(self) -> None:
        incident = _incident("incident-1")
        slack_key = incident_notifications.build_delivery_key("slack", "incident-1")
        discord_key = incident_notifications.build_delivery_key("discord", "incident-1")
        existing = {
            slack_key: {"id": "n-slack", "delivery_key": slack_key, "status": "sent", "attempt_count": 1},
            discord_key: {"id": "n-discord", "delivery_key": discord_key, "status": "sent", "attempt_count": 1},
        }
        with (
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CANONICAL_SETTINGS", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_ENABLED", True, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_DRY_RUN", False, create=True),
            patch.object(incident_notifications.config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack,discord", create=True),
            self._canonical_channel_rows(),
            patch.object(incident_notifications.supabase_client, "load_account_incidents_to_notify", return_value=[incident]),
            patch.object(incident_notifications.supabase_client, "load_existing_incident_notifications_by_delivery_keys", return_value=existing),
            patch.object(incident_notifications.supabase_client, "create_account_incident_notification") as create,
            patch.object(incident_notifications, "_post_json_webhook") as post,
        ):
            out = incident_notifications.dispatch_account_incident_notifications()
        self.assertEqual(out["skipped_duplicate_count"], 2)
        self.assertEqual(out["sent_count"], 0)
        create.assert_not_called()
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
