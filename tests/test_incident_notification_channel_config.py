from __future__ import annotations

import base64
import hashlib
import unittest
from unittest.mock import patch

import incident_notification_channel_config as channel_config


def _encrypt_like_backend(value: str, *, key_raw: str) -> str:
    """Mirror the backend AES-256-GCM contract: base64(iv | tag | data)."""
    import os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = hashlib.sha256(key_raw.encode("utf-8")).digest()
    iv = os.urandom(12)
    aesgcm = AESGCM(key)
    sealed = aesgcm.encrypt(iv, value.encode("utf-8"), None)
    data, tag = sealed[:-16], sealed[-16:]
    return base64.b64encode(iv + tag + data).decode("ascii")


class DecryptWebhookTest(unittest.TestCase):
    def test_round_trip_with_explicit_key(self) -> None:
        secret_url = "https://hooks.slack.com/services/TEST/ONLY"
        with patch.object(
            channel_config.config,
            "INCIDENT_NOTIFICATION_WEBHOOK_ENCRYPTION_KEY",
            "unit-test-key",
            create=True,
        ):
            ciphertext = _encrypt_like_backend(secret_url, key_raw="unit-test-key")
            self.assertEqual(channel_config.decrypt_webhook_ciphertext(ciphertext), secret_url)

    def test_empty_ciphertext_returns_empty(self) -> None:
        self.assertEqual(channel_config.decrypt_webhook_ciphertext(""), "")
        self.assertEqual(channel_config.decrypt_webhook_ciphertext(None), "")


class ResolveEffectiveChannelConfigTest(unittest.TestCase):
    def test_canonical_enabled_configured_allows_send(self) -> None:
        secret_url = "https://discord.com/api/webhooks/TEST/ONLY"
        with (
            patch.object(
                channel_config.config,
                "INCIDENT_NOTIFICATION_WEBHOOK_ENCRYPTION_KEY",
                "unit-test-key",
                create=True,
            ),
            patch.object(
                channel_config,
                "load_channel_settings_row",
                return_value={
                    "enabled": True,
                    "configured": True,
                    "webhook_ciphertext": _encrypt_like_backend(
                        secret_url, key_raw="unit-test-key"
                    ),
                },
            ),
        ):
            cfg = channel_config.resolve_effective_channel_config("discord")
        self.assertTrue(cfg["send_allowed"])
        self.assertEqual(cfg["source"], "canonical")
        self.assertEqual(cfg["webhook_url"], secret_url)
        self.assertIsNone(cfg["reason"])

    def test_canonical_disabled_row_blocks_send(self) -> None:
        with patch.object(
            channel_config,
            "load_channel_settings_row",
            return_value={"enabled": False, "configured": True, "webhook_ciphertext": "x"},
        ):
            cfg = channel_config.resolve_effective_channel_config("slack")
        self.assertFalse(cfg["send_allowed"])
        self.assertEqual(cfg["reason"], "channel_disabled")

    def test_canonical_row_without_ciphertext_reports_not_configured(self) -> None:
        with patch.object(
            channel_config,
            "load_channel_settings_row",
            return_value={"enabled": True, "configured": False, "webhook_ciphertext": None},
        ):
            cfg = channel_config.resolve_effective_channel_config("slack")
        self.assertFalse(cfg["send_allowed"])
        self.assertEqual(cfg["reason"], "channel_not_configured")

    def test_missing_row_without_legacy_fallback_blocks_send(self) -> None:
        with (
            patch.object(channel_config, "load_channel_settings_row", return_value=None),
            patch.object(
                channel_config.config,
                "SLACK_WEBHOOK_URL",
                "https://hooks.slack.com/services/ENV",
                create=True,
            ),
        ):
            cfg = channel_config.resolve_effective_channel_config("slack")
            enabled = channel_config.channel_enabled_for_dispatch("slack")
        self.assertFalse(cfg["send_allowed"])
        self.assertFalse(enabled)

    def test_missing_row_with_legacy_fallback_uses_env(self) -> None:
        with (
            patch.object(channel_config, "load_channel_settings_row", return_value=None),
            patch.object(
                channel_config.config,
                "INCIDENT_NOTIFICATIONS_ENV_LEGACY_FALLBACK",
                True,
                create=True,
            ),
            patch.object(
                channel_config.config,
                "SLACK_WEBHOOK_URL",
                "https://hooks.slack.com/services/ENV",
                create=True,
            ),
        ):
            cfg = channel_config.resolve_effective_channel_config("slack")
        self.assertTrue(cfg["send_allowed"])
        self.assertEqual(cfg["source"], "env_legacy_fallback")

    def test_invalid_channel_rejected(self) -> None:
        cfg = channel_config.resolve_effective_channel_config("email")
        self.assertFalse(cfg["send_allowed"])
        self.assertEqual(cfg["reason"], "invalid_channel")


if __name__ == "__main__":
    unittest.main()
