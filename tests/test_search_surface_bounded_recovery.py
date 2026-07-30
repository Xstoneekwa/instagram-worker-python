from __future__ import annotations

import unittest
from unittest.mock import patch

import instagram_navigation as nav


class SearchSurfaceBoundedRecoveryTest(unittest.TestCase):
    def test_transient_missing_edittext_retries_once_with_exact_selector_only(self) -> None:
        with patch.object(nav, "verify_app_foreground", return_value=True), patch.object(
            nav, "open_search", side_effect=[False, True]
        ) as open_search, patch.object(
            nav, "is_lightweight_search_screen", return_value=False
        ), patch.object(
            nav, "mark_search_surface_fresh_for_follow_ct"
        ) as mark_fresh, patch.object(
            nav, "_startup_timing_log"
        ), patch.object(
            nav, "log"
        ), patch.object(
            nav.time, "sleep"
        ):
            result = nav.ensure_global_search_surface(
                object(),
                intended_username="baocanteenlille",
                source_profile_username="baocanteenlille",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "open_search_bounded_retry_ok")
        self.assertTrue(result["recovery_used"])
        self.assertEqual(open_search.call_count, 2)
        retry_kwargs = open_search.call_args_list[1].kwargs
        self.assertEqual(retry_kwargs["_surface_recovery_depth"], 1)
        self.assertFalse(retry_kwargs["allow_percent_fallback"])
        self.assertEqual(
            retry_kwargs["caller_context"],
            "ensure_global_search_surface_bounded_retry",
        )
        mark_fresh.assert_called_once()

    def test_bounded_retry_exhaustion_keeps_existing_fail_closed_contract(self) -> None:
        with patch.object(nav, "verify_app_foreground", return_value=True), patch.object(
            nav, "open_search", side_effect=[False, False]
        ) as open_search, patch.object(
            nav, "is_lightweight_search_screen", return_value=False
        ), patch.object(
            nav, "mark_search_surface_fresh_for_follow_ct"
        ) as mark_fresh, patch.object(
            nav, "_startup_timing_log"
        ), patch.object(
            nav, "log"
        ), patch.object(
            nav.time, "sleep"
        ):
            result = nav.ensure_global_search_surface(
                object(), intended_username="baocanteenlille"
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "open_search_failed")
        self.assertEqual(open_search.call_count, 2)
        mark_fresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
