from __future__ import annotations

import types
import unittest
from unittest.mock import MagicMock, patch

import dm_sender_engine
from dm_real_send_flags import (
    resolve_outreach_dm_real_send_enabled,
    resolve_welcome_dm_real_send_enabled,
)


class DmRealSendFlagsTest(unittest.TestCase):
    def test_welcome_flag_does_not_enable_outreach(self) -> None:
        cfg = types.SimpleNamespace(
            WELCOME_DM_REAL_SEND_ENABLED=False,
            OUTREACH_DM_REAL_SEND_ENABLED=False,
        )
        env = {
            "WELCOME_DM_REAL_SEND_ENABLED": "true",
            "OUTREACH_DM_REAL_SEND_ENABLED": "false",
            "DM_SENDER_REAL_SEND_ENABLED": "true",
        }

        welcome_enabled, welcome_source = resolve_welcome_dm_real_send_enabled(
            environ=env,
            config_module=cfg,
            emit_log=False,
        )
        outreach_enabled, outreach_source = resolve_outreach_dm_real_send_enabled(
            environ=env,
            config_module=cfg,
            emit_log=False,
        )

        self.assertTrue(welcome_enabled)
        self.assertEqual(welcome_source, "env:WELCOME_DM_REAL_SEND_ENABLED")
        self.assertFalse(outreach_enabled)
        self.assertEqual(outreach_source, "env:OUTREACH_DM_REAL_SEND_ENABLED")

    def test_legacy_global_flag_is_not_domain_fallback(self) -> None:
        cfg = types.SimpleNamespace(
            WELCOME_DM_REAL_SEND_ENABLED=False,
            OUTREACH_DM_REAL_SEND_ENABLED=False,
        )
        env = {"DM_SENDER_REAL_SEND_ENABLED": "true"}

        welcome_enabled, welcome_source = resolve_welcome_dm_real_send_enabled(
            environ=env,
            config_module=cfg,
            emit_log=False,
        )
        outreach_enabled, outreach_source = resolve_outreach_dm_real_send_enabled(
            environ=env,
            config_module=cfg,
            emit_log=False,
        )

        self.assertFalse(welcome_enabled)
        self.assertEqual(welcome_source, "config.WELCOME_DM_REAL_SEND_ENABLED")
        self.assertFalse(outreach_enabled)
        self.assertEqual(outreach_source, "config.OUTREACH_DM_REAL_SEND_ENABLED")

    def test_sender_outreach_uses_outreach_flag_without_legacy_global(self) -> None:
        env = {
            "OUTREACH_DM_REAL_SEND_ENABLED": "true",
            "WELCOME_DM_REAL_SEND_ENABLED": "false",
            "DM_SENDER_REAL_SEND_ENABLED": "false",
        }
        with patch.dict("os.environ", env, clear=True):
            enabled, source = dm_sender_engine.resolve_dm_sender_real_send_enabled_for_type("outreach")

        self.assertTrue(enabled)
        self.assertEqual(source, "env:OUTREACH_DM_REAL_SEND_ENABLED")

    def test_sender_outreach_off_not_bypassed_by_legacy_global(self) -> None:
        env = {
            "OUTREACH_DM_REAL_SEND_ENABLED": "false",
            "DM_SENDER_REAL_SEND_ENABLED": "true",
        }
        with patch.dict("os.environ", env, clear=True):
            enabled, source = dm_sender_engine.resolve_dm_sender_real_send_enabled_for_type("outreach")

        self.assertFalse(enabled)
        self.assertEqual(source, "env:OUTREACH_DM_REAL_SEND_ENABLED")

    def test_sender_welcome_uses_welcome_flag_without_legacy_global(self) -> None:
        env = {
            "WELCOME_DM_REAL_SEND_ENABLED": "true",
            "OUTREACH_DM_REAL_SEND_ENABLED": "false",
            "DM_SENDER_REAL_SEND_ENABLED": "false",
        }
        with patch.dict("os.environ", env, clear=True):
            welcome_enabled, welcome_source = dm_sender_engine.resolve_dm_sender_real_send_enabled_for_type("welcome")
            outreach_enabled, outreach_source = dm_sender_engine.resolve_dm_sender_real_send_enabled_for_type("outreach")

        self.assertTrue(welcome_enabled)
        self.assertEqual(welcome_source, "env:WELCOME_DM_REAL_SEND_ENABLED")
        self.assertFalse(outreach_enabled)
        self.assertEqual(outreach_source, "env:OUTREACH_DM_REAL_SEND_ENABLED")

    def test_sender_welcome_off_blocks_welcome(self) -> None:
        with patch.dict("os.environ", {"WELCOME_DM_REAL_SEND_ENABLED": "false"}, clear=True):
            enabled, source = dm_sender_engine.resolve_dm_sender_real_send_enabled_for_type("welcome")

        self.assertFalse(enabled)
        self.assertEqual(source, "env:WELCOME_DM_REAL_SEND_ENABLED")

    def test_sender_unknown_domain_uses_explicit_legacy_path(self) -> None:
        with patch.dict("os.environ", {"DM_SENDER_REAL_SEND_ENABLED": "true"}, clear=True):
            enabled, source = dm_sender_engine.resolve_dm_sender_real_send_enabled_for_type("manual")

        self.assertTrue(enabled)
        self.assertEqual(source, "legacy:env")

    def test_dm_composer_placeholder_text_is_not_existing_draft(self) -> None:
        self.assertTrue(dm_sender_engine._is_dm_composer_placeholder_text("Message…"))
        self.assertTrue(dm_sender_engine._is_dm_composer_placeholder_text("Message..."))
        self.assertTrue(dm_sender_engine._is_dm_composer_placeholder_text("Écrire un message"))
        self.assertFalse(dm_sender_engine._is_dm_composer_placeholder_text("Salut"))

    def _perform_welcome_send_with_existing_draft(self, existing_draft: str) -> MagicMock:
        clear_mock = MagicMock()
        with (
            patch.object(dm_sender_engine, "dm_thread_shows_outgoing_message", return_value=False),
            patch.object(
                dm_sender_engine,
                "verify_welcome_dm_thread_recipient_exact",
                return_value=(True, "exact_thread_header", "recipient"),
            ),
            patch.object(dm_sender_engine, "_resolve_dm_text_composer", return_value=(object(), None)),
            patch.object(dm_sender_engine, "verify_dm_composer_safe", return_value=(True, "ok")),
            patch.object(dm_sender_engine, "read_dm_composer_text", side_effect=[existing_draft, "Salut", "Salut"]),
            patch.object(dm_sender_engine, "clear_dm_draft", clear_mock),
            patch.object(dm_sender_engine, "type_dm_draft_only", return_value=(True, {"method": "set_text"})),
            patch.object(dm_sender_engine, "verify_dm_draft_text", return_value=True),
            patch.object(dm_sender_engine, "send_dm_safe", return_value={"sent": True, "composer_text_len_before_send": 5}),
            patch.object(dm_sender_engine, "finalize_after_real_send", return_value={"back_to_profile_ok": True}),
        ):
            sent_ok, _send_out, failure_reason = dm_sender_engine._perform_real_welcome_dm_send(
                object(),
                username="recipient",
                message_body="Salut",
                thread_state="empty_new_thread",
                pkg="com.instagram.androif",
            )
        self.assertTrue(sent_ok)
        self.assertIsNone(failure_reason)
        return clear_mock

    def test_placeholder_existing_draft_is_not_cleared_before_typing(self) -> None:
        clear_mock = self._perform_welcome_send_with_existing_draft("Message…")
        clear_mock.assert_not_called()

    def test_real_existing_draft_mismatch_is_still_cleared(self) -> None:
        clear_mock = self._perform_welcome_send_with_existing_draft("old text")
        clear_mock.assert_called_once()

    def test_run_dm_sender_send_outreach_allowed_without_legacy_global(self) -> None:
        env = {
            "OUTREACH_DM_REAL_SEND_ENABLED": "true",
            "WELCOME_DM_REAL_SEND_ENABLED": "false",
            "DM_SENDER_REAL_SEND_ENABLED": "false",
        }
        with (
            patch.dict("os.environ", env, clear=True),
            patch.object(dm_sender_engine, "_resolve_reserved_by", return_value="test-worker"),
            patch.object(dm_sender_engine, "_resolve_dm_sender_only_job_id", return_value=("", "none")),
            patch.object(dm_sender_engine, "_claim_job_for_run", return_value=None),
        ):
            code, summary = dm_sender_engine.run_dm_sender_send(
                object(),
                account_id="account-1",
                account_username="cinema_catchup",
                run_id="run-1",
                max_jobs=1,
                dm_type="outreach",
            )

        self.assertEqual(code, 0)
        self.assertTrue(summary["real_send_enabled"])
        self.assertEqual(summary["real_send_source"], "env:OUTREACH_DM_REAL_SEND_ENABLED")
        self.assertEqual(summary["sender_status"], "no_jobs")

    def test_run_dm_sender_send_outreach_blocked_when_domain_off(self) -> None:
        env = {
            "OUTREACH_DM_REAL_SEND_ENABLED": "false",
            "DM_SENDER_REAL_SEND_ENABLED": "true",
        }
        with (
            patch.dict("os.environ", env, clear=True),
            patch.object(dm_sender_engine, "_resolve_reserved_by", return_value="test-worker"),
            patch.object(dm_sender_engine, "_resolve_dm_sender_only_job_id", return_value=("", "none")),
        ):
            code, summary = dm_sender_engine.run_dm_sender_send(
                object(),
                account_id="account-1",
                account_username="cinema_catchup",
                run_id="run-1",
                max_jobs=1,
                dm_type="outreach",
            )

        self.assertEqual(code, 1)
        self.assertFalse(summary["real_send_enabled"])
        self.assertEqual(summary["real_send_source"], "env:OUTREACH_DM_REAL_SEND_ENABLED")
        self.assertEqual(summary["sender_status"], "blocked_disabled")


if __name__ == "__main__":
    unittest.main()
