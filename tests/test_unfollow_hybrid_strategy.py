import unittest
from unittest.mock import patch

import account_session_orchestrator as account_session
import instagram_navigation as nav
from unfollow_hybrid_strategy import (
    build_cursor_checkpoint,
    choose_hybrid_selection,
    cursor_anchor_matches,
    exact_search_result_count,
    open_exact_profile_for_unfollow,
)


def _search_xml(*usernames: str) -> str:
    rows = "".join(
        f'<node resource-id="com.instagram.android:id/row_search_user_username" '
        f'text="{username}" bounds="[0,0][100,50]" />'
        for username in usernames
    )
    return f'<hierarchy>{rows}</hierarchy>'


class UnfollowHybridStrategyTests(unittest.TestCase):
    def test_one_remaining_uses_direct_exact(self) -> None:
        out = choose_hybrid_selection(["one"])
        self.assertEqual(out.mode, "direct_exact")
        self.assertEqual(out.usernames, ("one",))

    def test_ten_remaining_uses_direct_exact(self) -> None:
        out = choose_hybrid_selection([f"user{i}" for i in range(10)])
        self.assertEqual(out.mode, "direct_exact")
        self.assertEqual(len(out.usernames), 10)

    def test_more_than_ten_scans_until_exhausted(self) -> None:
        names = [f"user{i}" for i in range(11)]
        self.assertEqual(choose_hybrid_selection(names).mode, "progressive_scan")
        fallback = choose_hybrid_selection(names, scan_exhausted=True)
        self.assertEqual(fallback.mode, "direct_exact")
        self.assertEqual(len(fallback.usernames), 10)

    def test_exact_result_is_fail_closed_when_ambiguous(self) -> None:
        xml = _search_xml("target", "target")
        self.assertEqual(exact_search_result_count(xml, "target"), 2)

    def test_renamed_or_missing_username_has_zero_exact_results(self) -> None:
        self.assertEqual(exact_search_result_count(_search_xml("new_name"), "old_name"), 0)

    def test_ambiguous_direct_candidate_remains_resumable(self) -> None:
        out = choose_hybrid_selection(["target"], already_direct_searched=["target"])
        self.assertEqual(out.mode, "partial_resumable")
        self.assertEqual(out.reason, "direct_exact_candidates_unresolved")

    def test_cursor_uses_hmac_anchors_and_restores(self) -> None:
        with patch.dict(
            "os.environ",
            {"TARGET_FOLLOWERS_RESUME_V2_HMAC_SECRET": "x" * 32},
        ):
            checkpoint = build_cursor_checkpoint(["one", "two"], depth=7, generation=3)
            self.assertEqual(checkpoint["cursor_schema"], "UNFOLLOW_CURSOR_V2")
            self.assertEqual(checkpoint["depth"], 7)
            self.assertTrue(all(str(value).startswith("a3:") for value in checkpoint["anchor_hashes"]))
            self.assertTrue(cursor_anchor_matches(["zero", "two"], checkpoint))

    def test_see_more_accessibility_button_variant_is_canonical(self) -> None:
        xml = (
            '<hierarchy><node selected="true" text="120 followers" bounds="[0,0][100,50]" />'
            '<node content-desc="See more, button" clickable="true" bounds="[0,100][100,150]" />'
            '</hierarchy>'
        )
        out = nav.followers_list_continuation_from_hierarchy_xml(xml)
        self.assertTrue(out["see_more_visible"])
        self.assertEqual(out["state"], "EXPAND_PRIMARY_LIST_AVAILABLE")

    def test_candidates_exhausted_is_terminal(self) -> None:
        contract = account_session._phase_terminal_contract(
            welcome="not_planned",
            follow="completed",
            unfollow="candidates_exhausted",
            outreach="not_planned",
        )
        self.assertTrue(contract["ok"])

    def test_direct_search_waits_for_a_late_exact_result(self) -> None:
        class Device:
            def __init__(self) -> None:
                self.dumps = iter([_search_xml("someone_else"), _search_xml("target")])

            def dump_hierarchy(self, compressed=False):
                del compressed
                return next(self.dumps)

        with patch("instagram_navigation.open_search", return_value=True), patch(
            "instagram_navigation.type_search", return_value=True
        ), patch("instagram_navigation.tap_account_result", return_value=True), patch(
            "unfollow_profile_probe.verify_unfollow_target_profile_strict",
            return_value={"ok": True},
        ), patch("unfollow_hybrid_strategy.time.sleep"):
            out = open_exact_profile_for_unfollow(Device(), "target")
        self.assertTrue(out["ok"])
        self.assertEqual(out["exact_match_count"], 1)

    def test_direct_search_requires_two_committed_missing_surfaces(self) -> None:
        class Device:
            def __init__(self) -> None:
                self.dumps = iter([_search_xml("new_name"), _search_xml("new_name")])

            def dump_hierarchy(self, compressed=False):
                del compressed
                return next(self.dumps)

        with patch("instagram_navigation.open_search", return_value=True), patch(
            "instagram_navigation.type_search", return_value=True
        ), patch("unfollow_hybrid_strategy.time.sleep"):
            out = open_exact_profile_for_unfollow(Device(), "old_name")
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "unavailable")
        self.assertEqual(out["confirmed_surface_count"], 2)


if __name__ == "__main__":
    unittest.main()
