from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import unfollow_session_orchestrator as unfollow


class UnfollowAnyProbeRoutingTest(unittest.TestCase):
    def _settings(self, mode: str) -> SimpleNamespace:
        return SimpleNamespace(mode=mode, enabled=True, after_days=3)

    def test_unfollow_any_probe_routes_to_any_visible_evaluator(self) -> None:
        rows = [{"username": "safe.row", "row_cta_class": "following"}]
        any_eval = {
            "visible_eligible_matches": [{"username": "safe.row", "username_normalized": "safe.row"}],
            "visible_eligible_matches_count": 1,
            "visible_eligible_matches_usernames": ["safe.row"],
            "visible_eligibility_skip_counts": {},
        }

        with (
            patch.object(unfollow, "_evaluate_visible_unfollow_any_with_session_cache", return_value=any_eval) as any_mock,
            patch.object(unfollow, "_evaluate_visible_unfollow_with_session_cache") as strict_mock,
        ):
            visible_eval, candidates, _fields, any_mode = unfollow._evaluate_visible_rows_for_unfollow_probe(
                "acct",
                "owner",
                rows,
                settings=self._settings("unfollow-any"),
                row_cache={},
            )

        self.assertTrue(any_mode)
        self.assertEqual(visible_eval, any_eval)
        self.assertIn("safe.row", candidates)
        any_mock.assert_called_once()
        strict_mock.assert_not_called()

    def test_standard_unfollow_probe_uses_strict_visible_evaluator(self) -> None:
        strict_eval = {
            "visible_eligible_matches": [],
            "visible_eligible_matches_count": 0,
            "visible_eligible_matches_usernames": [],
            "visible_eligibility_skip_counts": {},
        }

        with (
            patch.object(unfollow, "_evaluate_visible_unfollow_any_with_session_cache") as any_mock,
            patch.object(unfollow, "_evaluate_visible_unfollow_with_session_cache", return_value=strict_eval) as strict_mock,
        ):
            visible_eval, _candidates, _fields, any_mode = unfollow._evaluate_visible_rows_for_unfollow_probe(
                "acct",
                "owner",
                [{"username": "strict.row"}],
                settings=self._settings("unfollow"),
                row_cache={},
            )

        self.assertFalse(any_mode)
        self.assertEqual(visible_eval, strict_eval)
        strict_mock.assert_called_once()
        any_mock.assert_not_called()

    def test_unsupported_mode_stays_on_strict_evaluator_for_block_reason(self) -> None:
        strict_eval = {
            "visible_eligible_matches": [],
            "visible_ineligible_rows": [
                {"username": "row", "username_normalized": "row", "skip_reason": "unfollow_mode_not_supported"}
            ],
            "visible_eligible_matches_count": 0,
            "visible_eligible_matches_usernames": [],
            "visible_eligibility_skip_counts": {"unfollow_mode_not_supported": 1},
        }

        with patch.object(
            unfollow,
            "_evaluate_visible_unfollow_with_session_cache",
            return_value=strict_eval,
        ) as strict_mock:
            visible_eval, _candidates, _fields, any_mode = unfollow._evaluate_visible_rows_for_unfollow_probe(
                "acct",
                "owner",
                [{"username": "row"}],
                settings=self._settings("unsupported-mode"),
                row_cache={},
            )

        self.assertFalse(any_mode)
        self.assertEqual(visible_eval["visible_eligibility_skip_counts"]["unfollow_mode_not_supported"], 1)
        strict_mock.assert_called_once()

    def test_unfollow_any_visible_evaluator_rejects_unsafe_rows_and_keeps_safe_rows(self) -> None:
        rows = [
            {"username": "owner", "username_normalized": "owner", "row_cta_class": "following", "row_index": 0},
            {"username": "follow.cta", "username_normalized": "follow.cta", "row_cta_class": "follow", "row_index": 1},
            {
                "username": "followback.cta",
                "username_normalized": "followback.cta",
                "row_cta_class": "follow_back",
                "row_index": 2,
            },
            {"username": "done.row", "username_normalized": "done.row", "row_cta_class": "following", "row_index": 3},
            {"username": "safe.row", "username_normalized": "safe.row", "row_cta_class": "following", "row_index": 4},
        ]

        with patch.object(unfollow.supabase_client, "fetch_visible_unfollow_eligibility_rows", return_value={}):
            out = unfollow._evaluate_visible_unfollow_any_with_session_cache(
                "acct",
                rows,
                account_username="owner",
                row_cache={},
                completed_usernames={"done.row"},
            )

        self.assertEqual(out["visible_eligible_matches_usernames"], ["safe.row"])
        self.assertEqual(out["any_mode_visible_candidates_count"], 1)
        self.assertEqual(out["visible_eligibility_skip_counts"]["own_account"], 1)
        self.assertEqual(out["visible_eligibility_skip_counts"]["row_cta_follow"], 1)
        self.assertEqual(out["visible_eligibility_skip_counts"]["row_cta_follow_back"], 1)
        self.assertEqual(out["visible_eligibility_skip_counts"]["already_completed_in_run"], 1)


if __name__ == "__main__":
    unittest.main()
