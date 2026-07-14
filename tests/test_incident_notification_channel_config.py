import unittest
from unittest.mock import patch

import incident_notification_channel_config as channel_config


class IncidentNotificationChannelConfigTests(unittest.TestCase):
    def test_canonical_disabled_blocks_env_webhook(self) -> None:
        row = {
            "enabled": False,
            "configured": True,
            "webhook_ciphertext": "ignored",
        }
        with patch.object(channel_config, "load_channel_settings_row", return_value=row):
            with patch.object(channel_config.config, "INCIDENT_NOTIFICATIONS_CANONICAL_SETTINGS", True, create=True):
                with patch.object(channel_config.config, "INCIDENT_NOTIFICATIONS_ENV_LEGACY_FALLBACK", False, create=True):
                    with patch.object(channel_config, "_env_webhook_url", return_value="https://hooks.slack.com/services/SECRET"):
                        cfg = channel_config.resolve_effective_channel_config("slack")
        self.assertFalse(cfg["send_allowed"])
        self.assertEqual(cfg["reason"], "channel_disabled")
        self.assertEqual(cfg["source"], "canonical")

    def test_canonical_clear_webhook_is_not_configured(self) -> None:
        row = {"enabled": True, "configured": False, "webhook_ciphertext": None}
        with patch.object(channel_config, "load_channel_settings_row", return_value=row):
            with patch.object(channel_config.config, "INCIDENT_NOTIFICATIONS_CANONICAL_SETTINGS", True, create=True):
                cfg = channel_config.resolve_effective_channel_config("discord")
        self.assertFalse(cfg["send_allowed"])
        self.assertEqual(cfg["reason"], "channel_not_configured")

    def test_legacy_env_only_when_no_row_and_flag_enabled(self) -> None:
        with patch.object(channel_config, "load_channel_settings_row", return_value=None):
            with patch.object(channel_config.config, "INCIDENT_NOTIFICATIONS_CANONICAL_SETTINGS", True, create=True):
                with patch.object(channel_config.config, "INCIDENT_NOTIFICATIONS_ENV_LEGACY_FALLBACK", True, create=True):
                    with patch.object(channel_config, "_env_webhook_url", return_value="https://discord.com/api/webhooks/x"):
                        cfg = channel_config.resolve_effective_channel_config("discord")
        self.assertTrue(cfg["send_allowed"])
        self.assertEqual(cfg["source"], "env_legacy_fallback")


if __name__ == "__main__":
    unittest.main()
