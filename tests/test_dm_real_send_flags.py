from __future__ import annotations

import types
import unittest

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


if __name__ == "__main__":
    unittest.main()
