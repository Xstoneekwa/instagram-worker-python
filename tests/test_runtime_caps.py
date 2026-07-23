from __future__ import annotations

import types
import unittest
from unittest.mock import patch

import supabase_client
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

    def test_follow_canonical_session_cap_wins_over_legacy(self) -> None:
        cfg = types.SimpleNamespace(
            FOLLOW_MAX_PER_RUN=2,
            FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN=5,
        )
        out = resolve_follow_runtime_limits(
            db_follow_per_session_limit=20,
            db_max_follow_per_run=10,
            follow_day_remaining_today=120,
            package_follow_day_cap=120,
            package_follow_session_cap=20,
            warmup_follow_day_cap=120,
            config_module=cfg,
            environ={},
        )

        self.assertEqual(out["effective_follow_max"], 20)
        self.assertFalse(out["legacy_fallback_used"])

    def test_follow_saved_account_session_cap_25_reaches_worker_resolver(self) -> None:
        out = resolve_follow_runtime_limits(
            db_follow_per_session_limit=25,
            db_max_follow_per_run=10,
            account_follow_day_cap=120,
            follow_day_remaining_today=120,
            package_follow_day_cap=120,
            package_follow_session_cap=120,
            warmup_follow_day_cap=120,
            environ={},
        )

        self.assertEqual(out["account_follow_session_cap"], 25)
        self.assertEqual(out["effective_follow_session_cap"], 25)
        self.assertFalse(out["legacy_fallback_used"])

    def test_follow_legacy_session_cap_is_fallback_when_canonical_missing(self) -> None:
        cfg = types.SimpleNamespace(
            FOLLOW_MAX_PER_RUN=2,
            FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN=5,
        )
        out = resolve_follow_runtime_limits(
            db_follow_per_session_limit=None,
            db_max_follow_per_run=10,
            follow_day_remaining_today=120,
            package_follow_day_cap=120,
            package_follow_session_cap=20,
            warmup_follow_day_cap=120,
            config_module=cfg,
            environ={},
        )

        self.assertEqual(out["effective_follow_max"], 10)
        self.assertTrue(out["legacy_fallback_used"])

    def test_follow_invalid_canonical_session_cap_uses_legacy_fallback(self) -> None:
        out = resolve_follow_runtime_limits(
            db_follow_per_session_limit="invalid",
            db_max_follow_per_run=10,
            follow_day_remaining_today=120,
            package_follow_day_cap=120,
            package_follow_session_cap=20,
            warmup_follow_day_cap=120,
            environ={},
        )

        self.assertEqual(out["effective_follow_max"], 10)
        self.assertTrue(out["legacy_fallback_used"])

    def test_follow_package_session_cap_can_limit_canonical_account_cap(self) -> None:
        out = resolve_follow_runtime_limits(
            db_follow_per_session_limit=20,
            db_max_follow_per_run=10,
            follow_day_remaining_today=120,
            package_follow_day_cap=120,
            package_follow_session_cap=15,
            warmup_follow_day_cap=120,
            environ={},
        )

        self.assertEqual(out["effective_follow_max"], 15)
        self.assertEqual(out["limiting_source"], "package_session_cap")

    def test_follow_remaining_day_can_limit_session(self) -> None:
        out = resolve_follow_runtime_limits(
            db_follow_per_session_limit=20,
            account_follow_day_cap=120,
            follow_day_remaining_today=7,
            package_follow_day_cap=120,
            package_follow_session_cap=20,
            warmup_follow_day_cap=120,
            environ={},
        )

        self.assertEqual(out["effective_follow_max"], 7)
        self.assertEqual(out["effective_follow_day_cap"], 120)
        self.assertEqual(out["remaining_effective_day_quota"], 7)

    def test_follow_warmup_day_one_limits_session(self) -> None:
        out = resolve_follow_runtime_limits(
            db_follow_per_session_limit=20,
            follow_day_remaining_today=120,
            package_follow_day_cap=120,
            package_follow_session_cap=20,
            warmup_follow_day_cap=10,
            environ={},
        )

        self.assertEqual(out["effective_follow_max"], 10)

    def test_follow_warmup_sequence_respects_configured_account_cap(self) -> None:
        for warmup_cap, expected in ((10, 10), (20, 20), (40, 40), (80, 50)):
            with self.subTest(warmup_cap=warmup_cap):
                out = resolve_follow_runtime_limits(
                    db_follow_per_session_limit=50,
                    db_max_follow_per_run=10,
                    account_follow_day_cap=120,
                    follow_day_remaining_today=120,
                    package_follow_day_cap=80,
                    package_follow_session_cap=80,
                    warmup_follow_day_cap=warmup_cap,
                    environ={},
                )

                self.assertEqual(out["account_follow_session_cap"], 50)
                self.assertEqual(out["effective_follow_max"], expected)

    def test_follow_day_four_package_cap_limits_higher_account_cap(self) -> None:
        out = resolve_follow_runtime_limits(
            db_follow_per_session_limit=120,
            account_follow_day_cap=120,
            follow_day_remaining_today=120,
            package_follow_day_cap=80,
            package_follow_session_cap=80,
            warmup_follow_day_cap=80,
            environ={},
        )

        self.assertEqual(out["effective_follow_max"], 80)

    def test_follow_multi_package_matrix_respects_maxima_and_warmup(self) -> None:
        packages = (
            ("growth", 80),
            ("pro", 120),
            ("premium", 120),
            ("internal_test", 20),
        )
        for package_code, package_cap in packages:
            for warmup_cap in (10, 20, 40, package_cap):
                with self.subTest(package_code=package_code, warmup_cap=warmup_cap):
                    configured = min(50, package_cap)
                    out = resolve_follow_runtime_limits(
                        db_follow_per_session_limit=configured,
                        account_follow_day_cap=configured,
                        follow_day_remaining_today=configured,
                        package_follow_day_cap=package_cap,
                        package_follow_session_cap=package_cap,
                        warmup_follow_day_cap=warmup_cap,
                        environ={},
                    )
                    self.assertEqual(out["effective_follow_max"], min(configured, warmup_cap))

    def test_follow_lower_account_override_wins_below_warmup(self) -> None:
        out = resolve_follow_runtime_limits(
            db_follow_per_session_limit=9,
            account_follow_day_cap=9,
            follow_day_remaining_today=9,
            package_follow_day_cap=80,
            package_follow_session_cap=80,
            warmup_follow_day_cap=10,
            environ={},
        )

        self.assertEqual(out["effective_follow_max"], 9)

    def test_follow_requested_above_warmup_never_exceeds_warmup(self) -> None:
        out = resolve_follow_runtime_limits(
            db_follow_per_session_limit=11,
            account_follow_day_cap=80,
            follow_day_remaining_today=80,
            package_follow_day_cap=80,
            package_follow_session_cap=80,
            warmup_follow_day_cap=10,
            environ={},
        )

        self.assertEqual(out["effective_follow_max"], 10)

    def test_follow_ops_hard_caps_are_final_worker_limits(self) -> None:
        out = resolve_follow_runtime_limits(
            db_follow_per_session_limit=80,
            account_follow_day_cap=80,
            follow_day_remaining_today=80,
            package_follow_day_cap=80,
            package_follow_session_cap=80,
            warmup_follow_day_cap=80,
            ops_hard_day_cap=30,
            ops_hard_session_cap=12,
            environ={},
        )

        self.assertEqual(out["effective_follow_day_cap"], 30)
        self.assertEqual(out["effective_follow_max"], 12)
        self.assertEqual(out["limiting_source"], "ops_hard_session_cap")

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
        self.assertEqual(
            out["source"],
            "day=min(configured_day,package_day,warmup,ops_hard_day);session=min(configured_session,package_session,warmup,ops_hard_session,remaining_effective_day_quota)",
        )

    def test_follow_daily_counter_uses_verified_events_and_sast_boundary(self) -> None:
        captured: dict[str, object] = {}

        def fake_request(method: str, table: str, *, query: dict[str, str]):
            captured.update({"method": method, "table": table, "query": query})
            return [{"id": "event-1", "event_at": query["event_at"]}]

        with patch.object(supabase_client, "_request_json", side_effect=fake_request):
            count = supabase_client.count_successful_follows_today("account-a")

        self.assertEqual(count, 1)
        self.assertEqual(captured["table"], "ig_interaction_events")
        query = captured["query"]
        self.assertIsInstance(query, dict)
        assert isinstance(query, dict)
        self.assertEqual(query["interaction_type"], "eq.follow")
        self.assertEqual(query["interaction_status"], "eq.success")
        self.assertEqual(query["event_type"], "eq.follow_verified")
        self.assertEqual(query["run_id"], "not.is.null")
        self.assertIn("T22:00:00+00:00", query["event_at"])

    def test_follow_runtime_inputs_fall_back_to_package_defaults(self) -> None:
        summary = {
            "package_defaults": {"follow_day": 50, "follow_session": 25},
            "package_caps": {"follow_day": 80, "follow_session": 80},
            "effective_caps_preview": {"follow_day": 10, "warmup_follow_day_cap": 10},
            "warmup_day": 1,
        }
        with patch.object(supabase_client, "_request_json", return_value=[]):
            with patch.object(supabase_client, "get_account_package_summary", return_value=summary):
                with patch.object(supabase_client, "count_successful_follows_today", return_value=0):
                    out = supabase_client.get_follow_runtime_cap_inputs("account-a")

        self.assertEqual(out["db_follow_per_session_limit"], 25)
        self.assertEqual(out["max_actions_per_day_from_db"], 50)
        self.assertEqual(out["package_follow_day_cap"], 80)
        self.assertEqual(out["package_follow_session_cap"], 80)
        self.assertEqual(out["follow_day_remaining_today"], 10)

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
