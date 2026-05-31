from __future__ import annotations

import unittest
from types import SimpleNamespace

import account_session_orchestrator as account_session


class AccountSessionUnfollowSkipTest(unittest.TestCase):
    def test_prod_normal_enables_handoff_from_domain_settings(self) -> None:
        original_loader = account_session.load_unfollow_settings
        original_flag = getattr(account_session.config, "ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED", False)
        account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED = False
        account_session.load_unfollow_settings = lambda *_args, **_kwargs: SimpleNamespace(
            enabled=True,
            mode="unfollow-any",
            session_limit=120,
            day_limit=120,
            runtime_cap_mode="prod_normal",
        )
        try:
            self.assertTrue(account_session._follow_to_unfollow_real_enabled("account-id"))
        finally:
            account_session.load_unfollow_settings = original_loader
            account_session.config.ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED = original_flag

    def test_h3_real_allows_unfollow_any_without_db_pending_candidate(self) -> None:
        reason = account_session._follow_to_unfollow_real_skip_reason(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="cinema_catchup",
            follow_exit_code=0,
            diagnostic={
                "unfollow_enabled": True,
                "unfollow_mode": "unfollow-any",
                "has_pending_unfollow": False,
                "handoff_would_run": True,
            },
            real_max_actions_effective=1,
            follow_exit_gate={"follow_exit_code_allowed": True},
        )

        self.assertEqual(reason, "")

    def test_h3_real_rejects_unfollow_any_when_cap_exhausted(self) -> None:
        reason = account_session._follow_to_unfollow_real_skip_reason(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="cinema_catchup",
            follow_exit_code=0,
            diagnostic={
                "unfollow_enabled": True,
                "unfollow_mode": "unfollow-any",
                "has_pending_unfollow": True,
            },
            real_max_actions_effective=0,
            follow_exit_gate={"follow_exit_code_allowed": True},
        )

        self.assertEqual(reason, "unfollow_any_cap_exhausted")

    def test_h3_real_skip_reason_for_no_candidate_is_stable(self) -> None:
        reason = account_session._follow_to_unfollow_real_skip_reason(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="cinema_catchup",
            follow_exit_code=0,
            diagnostic={
                "unfollow_enabled": True,
                "unfollow_mode": "unfollow",
                "has_pending_unfollow": False,
            },
            real_max_actions_effective=1,
            follow_exit_gate={"follow_exit_code_allowed": True},
        )

        self.assertEqual(reason, "unfollow_skipped_no_safe_candidate")

    def test_h3_real_summary_maps_unfollow_any_no_candidate_reason(self) -> None:
        summary = account_session._real_summary_from_unfollow_summary(
            enabled=True,
            executed=True,
            exit_code=0,
            unfollow_summary={
                "unfollow_mode": "unfollow-any",
                "status": "no_visible_eligible_unfollow_target",
                "unfollow_actions_sent": 0,
                "unfollow_actions_verified": 0,
            },
            real_max_actions_requested=1,
            real_max_actions_effective=1,
            real_hard_max=1,
            follow_exit_gate={"follow_exit_code_allowed": True},
        )

        self.assertEqual(summary["skip_reason"], "unfollow_any_no_safe_candidate")
        self.assertEqual(summary["failure_reason"], "unfollow_any_no_safe_candidate")


if __name__ == "__main__":
    unittest.main()
