from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import account_session_orchestrator as session


class FakeFollowersEngine:
    def __init__(self, responses: list[tuple[int, dict]]) -> None:
        self.responses = responses
        self.calls: list[dict] = []
        self.last_session_summary: dict = {}

    def __call__(self, _device, **kwargs):
        self.calls.append(dict(kwargs))
        idx = len(self.calls) - 1
        exit_code, summary = self.responses[idx]
        self.last_session_summary = dict(summary)
        return exit_code


def target(target_id: str, source: str, index: int) -> dict:
    return {
        "target_id": target_id,
        "source_profile": source,
        "target_index": index,
        "selection_source": "ig_targets",
    }


class FollowTargetsRuntimeP1bTest(unittest.TestCase):
    def test_exhaustion_classifier_accepts_sparse_and_bounded_exhaustion(self) -> None:
        self.assertTrue(session.is_follow_target_exhaustion_outcome(exit_code=66))
        self.assertTrue(session.is_follow_target_exhaustion_outcome(
            outcome="no_followable_candidates_bounded_exploration",
            summary={"follows_completed_count": 0},
        ))
        self.assertTrue(session.is_follow_target_exhaustion_outcome(
            reason="no_candidates_after_sparse_scrolls",
            summary={"follows_completed_count": 0},
        ))

    def test_exhaustion_classifier_rejects_non_target_errors(self) -> None:
        for reason in [
            "login_required",
            "checkpoint_required",
            "device_unavailable",
            "wrong_surface_abort",
            "credential_review_required",
            "rate_limit",
        ]:
            self.assertFalse(session.is_follow_target_exhaustion_outcome(
                exit_code=96,
                reason=reason,
                summary={"follows_completed_count": 0},
            ))

    def test_rotation_switches_after_exhaustion_and_keeps_target_attribution(self) -> None:
        engine = FakeFollowersEngine([
            (66, {
                "follows_completed_count": 0,
                "follow_session_outcome": "no_followable_candidates_bounded_exploration",
                "follow_stop_reason": "no_candidates_after_sparse_scrolls",
            }),
            (0, {
                "follows_completed_count": 1,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        logs: list[tuple[str, str, dict]] = []
        with patch.object(session, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            result = session._run_follow_target_rotation(
                object(),
                account_id="acct",
                account_username="account",
                run_id="run",
                follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
                run_followers_list_engine_session=engine,
                supabase_mode=True,
                warm_session_used=False,
                force_stop_used=False,
                max_targets_per_run=3,
            )

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(len(engine.calls), 2)
        self.assertEqual(engine.calls[0]["target_id"], "t1")
        self.assertEqual(engine.calls[0]["source_profile_username"], "source_one")
        self.assertEqual(engine.calls[1]["target_id"], "t2")
        self.assertEqual(engine.calls[1]["source_profile_username"], "source_two")
        self.assertEqual(result["summary"]["target_id"], "t2")
        self.assertIn("follow_target_switched", [event for _level, event, _kw in logs])

    def test_rotation_all_exhausted_returns_stable_stop_reason(self) -> None:
        engine = FakeFollowersEngine([
            (66, {
                "follows_completed_count": 0,
                "follow_session_outcome": "no_followable_candidates_bounded_exploration",
                "follow_stop_reason": "no_candidates_after_sparse_scrolls",
            }),
            (0, {
                "follows_completed_count": 0,
                "follow_session_outcome": "no_followable_candidates_bounded_exploration",
                "follow_stop_reason": "list_progressive_exploration_exhausted",
            }),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
        )

        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["all_targets_exhausted"])
        self.assertEqual(result["summary"]["follow_stop_reason"], "all_targets_exhausted")
        self.assertEqual(result["summary"]["follow_session_outcome"], "no_followable_candidates_all_targets")
        self.assertEqual(len(engine.calls), 2)

    def test_rotation_respects_max_targets_per_run(self) -> None:
        engine = FakeFollowersEngine([
            (66, {"follows_completed_count": 0, "follow_session_outcome": "no_followable_candidates_bounded_exploration"}),
            (66, {"follows_completed_count": 0, "follow_session_outcome": "no_followable_candidates_bounded_exploration"}),
            (0, {"follows_completed_count": 1, "follow_session_outcome": "follows_completed"}),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[
                target("t1", "source_one", 0),
                target("t2", "source_two", 1),
                target("t3", "source_three", 2),
            ],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=2,
        )

        self.assertEqual(len(engine.calls), 2)
        self.assertEqual([call["target_id"] for call in engine.calls], ["t1", "t2"])
        self.assertFalse(result["all_targets_exhausted"])
        self.assertEqual(result["summary"]["follow_stop_reason"], "max_targets_per_run_reached")

    def test_budget_reached_switches_without_exhaustion(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 2,
                "global_follows_goal_effective": 5,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 5,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        logs: list[tuple[str, str, dict]] = []
        with patch.object(session, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            result = session._run_follow_target_rotation(
                object(),
                account_id="acct",
                account_username="account",
                run_id="run",
                follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
                run_followers_list_engine_session=engine,
                supabase_mode=True,
                warm_session_used=False,
                force_stop_used=False,
                max_targets_per_run=3,
                max_follows_per_target_per_run=2,
            )

        events = [event for _level, event, _kw in logs]
        self.assertIn("follow_target_budget_reached", events)
        self.assertIn("follow_target_switched", events)
        self.assertEqual([call["target_id"] for call in engine.calls], ["t1", "t2"])
        self.assertEqual([call["target_follow_budget"] for call in engine.calls], [2, 2])
        self.assertEqual(result["summary"]["target_id"], "t2")

    def test_budget_respects_global_follow_cap(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 2,
                "global_follows_goal_effective": 5,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
            (0, {
                "follows_completed_count": 2,
                "global_follows_goal_effective": 5,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 5,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[
                target("t1", "source_one", 0),
                target("t2", "source_two", 1),
                target("t3", "source_three", 2),
            ],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
            max_follows_per_target_per_run=2,
        )

        self.assertEqual([call["target_follow_budget"] for call in engine.calls], [2, 2, 1])
        self.assertEqual(sum(item["follows_completed_count"] for item in result["attempts"]), 5)
        self.assertEqual(result["summary"]["follow_stop_reason"], "global_follow_cap_reached")
        self.assertEqual(result["global_follows_completed"], 5)

    def test_mono_target_budget_reached_stops_without_loop(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 2,
                "global_follows_goal_effective": 5,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
            (0, {"follows_completed_count": 2, "follow_session_outcome": "follows_completed"}),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[target("t1", "source_one", 0)],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
            max_follows_per_target_per_run=2,
        )

        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(result["summary"]["follow_stop_reason"], "target_budget_reached")

    def test_non_exhaustion_error_does_not_switch_target(self) -> None:
        engine = FakeFollowersEngine([
            (96, {
                "follows_completed_count": 0,
                "follow_session_outcome": "wrong_surface_abort",
                "follow_stop_reason": "wrong_surface_abort",
            }),
            (0, {"follows_completed_count": 1, "follow_session_outcome": "follows_completed"}),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
        )

        self.assertEqual(result["exit_code"], 96)
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(engine.calls[0]["target_id"], "t1")

    def test_single_target_non_exhausted_matches_p1a_behavior(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 1,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[target("t1", "source_one", 0)],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
        )

        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["all_targets_exhausted"])
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(engine.calls[0]["target_id"], "t1")

    def test_logs_do_not_include_sensitive_raw_fields(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 1,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        logs: list[tuple[str, str, dict]] = []
        with patch.object(session, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            session._run_follow_target_rotation(
                object(),
                account_id="acct",
                account_username="account",
                run_id="run",
                follow_targets=[target("t1", "source_one", 0)],
                run_followers_list_engine_session=engine,
                supabase_mode=True,
                warm_session_used=False,
                force_stop_used=False,
                max_targets_per_run=3,
            )

        serialized = repr(logs).lower()
        for forbidden in ["password", "secret", "token", "<node", "xml", "screenshot", "serial", "udid"]:
            self.assertNotIn(forbidden, serialized)

    def test_follow_source_rotation_settings_default_without_supabase_row(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch.object(
            session.supabase_client,
            "load_account_follow_source_settings",
            return_value=None,
        ):
            settings = session._resolve_follow_source_rotation_settings("acct")

        self.assertEqual(settings["settings_source"], "default")
        self.assertEqual(settings["max_follows_per_target_per_run"], 2)
        self.assertEqual(settings["max_targets_per_run"], 3)
        self.assertEqual(settings["bounds"]["max_follows_per_target_per_run"]["max"], 50)
        self.assertEqual(settings["bounds"]["max_targets_per_run"]["max"], 10)

    def test_follow_source_rotation_settings_account_row_overrides_env(self) -> None:
        with patch.dict(os.environ, {
            "FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_PER_RUN": "7",
            "FOLLOW_TARGET_ROTATION_MAX_TARGETS_PER_RUN": "2",
        }, clear=False), patch.object(
            session.supabase_client,
            "load_account_follow_source_settings",
            return_value={
                "max_follows_per_target_per_run": 30,
                "max_targets_per_run": 4,
            },
        ):
            settings = session._resolve_follow_source_rotation_settings("acct")

        self.assertEqual(settings["settings_source"], "account")
        self.assertEqual(settings["max_follows_per_target_per_run"], 30)
        self.assertEqual(settings["max_targets_per_run"], 4)

    def test_follow_source_rotation_settings_env_fallback_when_no_account_row(self) -> None:
        with patch.dict(os.environ, {
            "FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_PER_RUN": "8",
            "FOLLOW_TARGET_ROTATION_MAX_TARGETS_PER_RUN": "4",
        }, clear=False), patch.object(
            session.config,
            "FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_PER_RUN",
            8,
        ), patch.object(
            session.config,
            "FOLLOW_TARGET_ROTATION_MAX_TARGETS_PER_RUN",
            4,
        ), patch.object(
            session.supabase_client,
            "load_account_follow_source_settings",
            return_value=None,
        ):
            settings = session._resolve_follow_source_rotation_settings("acct")

        self.assertEqual(settings["settings_source"], "env")
        self.assertEqual(settings["max_follows_per_target_per_run"], 8)
        self.assertEqual(settings["max_targets_per_run"], 4)

    def test_follow_source_rotation_allows_prod_candidate_budget_without_default_change(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 30,
                "global_follows_goal_effective": 35,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
            (0, {
                "follows_completed_count": 5,
                "global_follows_goal_effective": 35,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=4,
            max_follows_per_target_per_run=30,
        )

        self.assertEqual([call["target_follow_budget"] for call in engine.calls], [30, 5])
        self.assertEqual(result["summary"]["follow_stop_reason"], "global_follow_cap_reached")


if __name__ == "__main__":
    unittest.main()
