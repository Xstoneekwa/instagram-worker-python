from __future__ import annotations

import unittest

import follow_settings


class FollowSettingsFilterTest(unittest.TestCase):
    def test_loader_maps_threshold_values(self) -> None:
        original_getter = follow_settings.supabase_client.get_account_follow_settings
        follow_settings.supabase_client.get_account_follow_settings = lambda _account_id: {
            "account_id": "account-id",
            "dont_follow_private_accounts": False,
            "min_followers": 500,
            "max_followers": 10000,
            "min_posts": 3,
        }
        try:
            settings = follow_settings.load_follow_settings("account-id")
        finally:
            follow_settings.supabase_client.get_account_follow_settings = original_getter

        self.assertFalse(settings.dont_follow_private_accounts)
        self.assertEqual(settings.min_followers, 500)
        self.assertEqual(settings.max_followers, 10000)
        self.assertEqual(settings.min_posts, 3)
        self.assertFalse(settings.defaults_used)

    def test_null_thresholds_do_not_filter(self) -> None:
        settings = follow_settings.FollowSettings(
            account_id="account-id",
            dont_follow_private_accounts=True,
            min_followers=None,
            max_followers=None,
            min_posts=None,
            defaults_used=False,
        )

        passes, reason = follow_settings.account_follow_filter_pass(
            settings,
            {"extraction_ok": True, "followers_count": 1, "posts_count": 0},
        )

        self.assertTrue(passes)
        self.assertEqual(reason, "no_follow_filter_thresholds_configured")

    def test_followers_below_min_skips(self) -> None:
        settings = follow_settings.FollowSettings(
            account_id="account-id",
            dont_follow_private_accounts=True,
            min_followers=500,
            max_followers=None,
            min_posts=None,
            defaults_used=False,
        )

        passes, reason = follow_settings.account_follow_filter_pass(
            settings,
            {"extraction_ok": True, "followers_count": 100, "posts_count": 5},
        )

        self.assertFalse(passes)
        self.assertEqual(reason, "skip_followers_below_min")

    def test_followers_above_max_skips(self) -> None:
        settings = follow_settings.FollowSettings(
            account_id="account-id",
            dont_follow_private_accounts=True,
            min_followers=None,
            max_followers=10000,
            min_posts=None,
            defaults_used=False,
        )

        passes, reason = follow_settings.account_follow_filter_pass(
            settings,
            {"extraction_ok": True, "followers_count": 20000, "posts_count": 5},
        )

        self.assertFalse(passes)
        self.assertEqual(reason, "skip_followers_above_max")

    def test_posts_below_min_skips(self) -> None:
        settings = follow_settings.FollowSettings(
            account_id="account-id",
            dont_follow_private_accounts=True,
            min_followers=None,
            max_followers=None,
            min_posts=3,
            defaults_used=False,
        )

        passes, reason = follow_settings.account_follow_filter_pass(
            settings,
            {"extraction_ok": True, "followers_count": 1000, "posts_count": 1},
        )

        self.assertFalse(passes)
        self.assertEqual(reason, "skip_posts_below_min")

    def test_matching_metrics_pass(self) -> None:
        settings = follow_settings.FollowSettings(
            account_id="account-id",
            dont_follow_private_accounts=True,
            min_followers=500,
            max_followers=10000,
            min_posts=3,
            defaults_used=False,
        )

        passes, reason = follow_settings.account_follow_filter_pass(
            settings,
            {"extraction_ok": True, "followers_count": 750, "posts_count": 5},
        )

        self.assertTrue(passes)
        self.assertEqual(reason, "follow_filter_thresholds_pass")

    def test_metrics_extraction_failure_is_fail_open(self) -> None:
        settings = follow_settings.FollowSettings(
            account_id="account-id",
            dont_follow_private_accounts=True,
            min_followers=500,
            max_followers=None,
            min_posts=None,
            defaults_used=False,
        )

        passes, reason = follow_settings.account_follow_filter_pass(
            settings,
            {"extraction_ok": False, "followers_count": 100},
        )

        self.assertTrue(passes)
        self.assertEqual(reason, "metrics_extraction_failed_skip_filter")


if __name__ == "__main__":
    unittest.main()
