from __future__ import annotations

import types
import unittest

from unfollow_session_orchestrator import _effective_real_action_max_per_run
from runtime_caps import (
    resolve_follow_runtime_limits,
    resolve_unfollow_runtime_cap,
    resolve_welcome_send_limits,
)


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

    def test_follow_db_caps_are_not_limited_by_code_defaults(self) -> None:
        cfg = types.SimpleNamespace(
            FOLLOW_MAX_PER_RUN=2,
            FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN=5,
        )
        out = resolve_follow_runtime_limits(
            db_follow_per_session_limit=12,
            db_max_follow_per_run=10,
            follow_day_remaining_today=10,
            package_follow_day_cap=80,
            warmup_follow_day_cap=None,
            config_module=cfg,
            environ={},
        )

        self.assertEqual(out["effective_follow_max"], 10)
        self.assertEqual(out["effective_iterations_max"], 10)
        self.assertFalse(out["follow_code_cap_applied"])
        self.assertFalse(out["iterations_code_cap_applied"])

    def test_follow_caps_growth_with_bmb_remaining_quota_case(self) -> None:
        cfg = types.SimpleNamespace(
            FOLLOW_MAX_PER_RUN=2,
            FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN=5,
        )
        follows_today_before_run = 4
        max_actions_per_day = 12
        out = resolve_follow_runtime_limits(
            db_follow_per_session_limit=12,
            db_max_follow_per_run=10,
            follow_day_remaining_today=max_actions_per_day - follows_today_before_run,
            package_follow_day_cap=80,
            warmup_follow_day_cap=80,
            config_module=cfg,
            environ={},
        )

        self.assertEqual(out["effective_follow_max"], 8)
        self.assertEqual(out["effective_iterations_max"], 8)
        self.assertFalse(out["follow_code_cap_applied"])

    def test_follow_env_cap_can_intentionally_limit_db_caps(self) -> None:
        cfg = types.SimpleNamespace(
            FOLLOW_MAX_PER_RUN=2,
            FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN=5,
        )
        out = resolve_follow_runtime_limits(
            db_follow_per_session_limit=12,
            follow_day_remaining_today=10,
            package_follow_day_cap=80,
            config_module=cfg,
            environ={"FOLLOW_MAX_PER_RUN": "2"},
        )

        self.assertEqual(out["effective_follow_max"], 2)
        self.assertTrue(out["follow_code_cap_applied"])

    def test_follow_uses_minimum_of_db_package_warmup_and_remaining(self) -> None:
        cfg = types.SimpleNamespace(
            FOLLOW_MAX_PER_RUN=120,
            FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN=120,
        )
        out = resolve_follow_runtime_limits(
            db_follow_per_session_limit=120,
            follow_day_remaining_today=90,
            package_follow_day_cap=120,
            warmup_follow_day_cap=40,
            config_module=cfg,
            environ={},
        )

        self.assertEqual(out["effective_follow_max"], 40)
        self.assertEqual(out["source"], "min(db_session,package,warmup,day_remaining)")

    def test_unfollow_prod_normal_uses_db_session_not_env_mini_cap(self) -> None:
        out = resolve_unfollow_runtime_cap(
            db_unfollow_per_session_limit=120,
            runtime_cap_mode="prod_normal",
            runtime_safety_cap=None,
            env_real_action_max_per_run=1,
        )

        self.assertEqual(out["runtime_cap"], 120)
        self.assertEqual(out["runtime_cap_mode"], "prod_normal")
        self.assertEqual(out["runtime_cap_source"], "supabase_domain_caps")
        self.assertFalse(out["limited_by_runtime_cap"])

    def test_unfollow_mini_run_can_intentionally_lower_to_one(self) -> None:
        out = resolve_unfollow_runtime_cap(
            db_unfollow_per_session_limit=120,
            runtime_cap_mode="mini_run",
            runtime_safety_cap=1,
            env_real_action_max_per_run=120,
        )

        self.assertEqual(out["runtime_cap"], 1)
        self.assertEqual(out["runtime_cap_mode"], "mini_run")
        self.assertTrue(out["limited_by_runtime_cap"])

    def test_unfollow_effective_cap_ignores_env_mini_cap_in_prod_normal(self) -> None:
        settings = types.SimpleNamespace(
            session_limit=50,
            runtime_cap_mode="prod_normal",
            runtime_safety_cap=None,
        )

        out = _effective_real_action_max_per_run(
            settings,
            env_hard_cap=1,
            day_remaining=200,
        )

        self.assertEqual(out, 50)


if __name__ == "__main__":
    unittest.main()
