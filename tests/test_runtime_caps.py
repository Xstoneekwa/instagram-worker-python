from __future__ import annotations

import types
import unittest

from runtime_caps import resolve_follow_runtime_limits, resolve_welcome_send_limits


class RuntimeCapsTest(unittest.TestCase):
    def test_welcome_hard_cap_one_wins_over_db_ten(self) -> None:
        cfg = types.SimpleNamespace(WELCOME_SESSION_SEND_MAX_JOBS=1)
        out = resolve_welcome_send_limits(
            db_welcome_per_session_limit=10,
            welcome_day_remaining_today=50,
            total_dm_day_remaining_today=50,
            config_module=cfg,
            environ={"WELCOME_SESSION_SEND_MAX_JOBS": "1"},
        )

        self.assertEqual(out["effective_welcome_send_max"], 1)
        self.assertTrue(out["hard_cap_present"])
        self.assertEqual(out["hard_cap_label"], "env:WELCOME_SESSION_SEND_MAX_JOBS")

    def test_welcome_default_cap_preserves_existing_behavior(self) -> None:
        cfg = types.SimpleNamespace(WELCOME_SESSION_SEND_MAX_JOBS=3)
        out = resolve_welcome_send_limits(
            db_welcome_per_session_limit=10,
            welcome_day_remaining_today=50,
            total_dm_day_remaining_today=50,
            config_module=cfg,
            environ={},
        )

        self.assertEqual(out["effective_welcome_send_max"], 3)
        self.assertFalse(out["hard_cap_present"])

    def test_follow_env_caps_resolve_to_one(self) -> None:
        cfg = types.SimpleNamespace(
            FOLLOW_MAX_PER_RUN=1,
            FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN=1,
        )
        out = resolve_follow_runtime_limits(
            config_module=cfg,
            environ={
                "FOLLOW_MAX_PER_RUN": "1",
                "FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN": "1",
            },
        )

        self.assertEqual(out["effective_follow_max"], 1)
        self.assertEqual(out["effective_iterations_max"], 1)
        self.assertTrue(out["env_follow_cap_present"])
        self.assertTrue(out["env_iterations_cap_present"])

    def test_follow_defaults_preserve_existing_behavior(self) -> None:
        cfg = types.SimpleNamespace(
            FOLLOW_MAX_PER_RUN=2,
            FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN=5,
        )
        out = resolve_follow_runtime_limits(config_module=cfg, environ={})

        self.assertEqual(out["effective_follow_max"], 2)
        self.assertEqual(out["effective_iterations_max"], 5)
        self.assertFalse(out["env_follow_cap_present"])
        self.assertFalse(out["env_iterations_cap_present"])


if __name__ == "__main__":
    unittest.main()
