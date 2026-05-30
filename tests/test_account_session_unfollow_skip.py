from __future__ import annotations

import unittest

import account_session_orchestrator as account_session


class AccountSessionUnfollowSkipTest(unittest.TestCase):
    def test_h3_real_skip_reason_for_unsupported_unfollow_mode_is_stable(self) -> None:
        reason = account_session._follow_to_unfollow_real_skip_reason(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="cinema_catchup",
            follow_exit_code=0,
            diagnostic={
                "unfollow_enabled": True,
                "unfollow_mode": "unfollow-any",
                "has_pending_unfollow": True,
            },
            real_max_actions_effective=1,
            follow_exit_gate={"follow_exit_code_allowed": True},
        )

        self.assertEqual(reason, "unfollow_skipped_mode_not_supported_for_h3_real")

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


if __name__ == "__main__":
    unittest.main()
