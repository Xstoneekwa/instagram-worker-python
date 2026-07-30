import inspect
import unittest
import time
from unittest.mock import ANY, patch

import account_session_orchestrator as account_session
import instagram_navigation as nav
import unfollow_session_orchestrator as unfollow_session
from unfollow_hybrid_strategy import (
    SEARCH_EXACT_RESULT_VISIBLE,
    SEARCH_NO_RESULTS_CONFIRMED,
    SEARCH_RESULTS_LOADING,
    SEARCH_SURFACE_UNHEALTHY,
    build_cursor_checkpoint,
    can_arm_direct_search_fallback,
    classify_search_surface_xml,
    choose_hybrid_selection,
    cursor_anchor_matches,
    exact_search_result_count,
    open_exact_profile_for_unfollow,
)


def _search_xml(*usernames: str, query=None) -> str:
    typed_query = str(query if query is not None else (usernames[-1] if usernames else ""))
    rows = "".join(
        '<node clickable="true" bounds="[0,100][500,180]">'
        f'<node resource-id="com.instagram.android:id/row_search_user_username" '
        f'text="{username}" bounds="[20,110][300,170]" /></node>'
        for username in usernames
    )
    return (
        '<hierarchy>'
        '<node class="android.widget.EditText" '
        'resource-id="com.instagram.android:id/action_bar_search_edit_text" '
        f'text="{typed_query}" bounds="[0,0][500,80]" />'
        f'{rows}</hierarchy>'
    )


def _no_results_xml(username: str = "missing_account", label: str = "No results") -> str:
    return (
        '<hierarchy><node class="android.widget.EditText" '
        'resource-id="com.instagram.android:id/action_bar_search_edit_text" '
        f'text="{username}" bounds="[0,0][500,80]" />'
        f'<node text="{label}" bounds="[0,120][500,180]" /></hierarchy>'
    )


class UnfollowHybridStrategyTests(unittest.TestCase):
    def test_preverified_stable_exact_bounds_skip_redundant_row_discovery(self) -> None:
        class Device:
            def __init__(self) -> None:
                self.clicks = []

            def window_size(self):
                raise AssertionError("same-snapshot bounds must avoid window_size RPC")

            def click(self, x, y):
                self.clicks.append((x, y))

        device = Device()
        with patch.object(
            nav,
            "find_real_account_text_element",
            side_effect=AssertionError("redundant row discovery must be skipped"),
        ), patch.object(
            nav,
            "_early_profile_transition_signal",
            return_value="profile_header",
        ), patch.object(nav.time, "sleep"):
            ok = nav.tap_account_result(
                device,
                "target",
                preverified_exact_row_bounds={
                    "left": 120,
                    "top": 300,
                    "right": 420,
                    "bottom": 380,
                },
                preverified_exact_screen_bounds={
                    "left": 0,
                    "top": 0,
                    "right": 1080,
                    "bottom": 2400,
                },
                preverified_exact_result_at_monotonic=time.monotonic(),
                preverified_exact_result_method="unfollow_direct_stable_exact_xml",
            )
        self.assertTrue(ok)
        self.assertEqual(device.clicks, [(270, 340)])

    def test_preverified_live_accessibility_bounds_are_screen_checked_and_tapped(self) -> None:
        class Device:
            def __init__(self) -> None:
                self.clicks = []

            def window_size(self):
                return 1080, 2400

            def click(self, x, y):
                self.clicks.append((x, y))

        device = Device()
        with patch.object(
            nav,
            "find_real_account_text_element",
            side_effect=AssertionError("stable live bounds must not be rediscovered"),
        ), patch.object(
            nav,
            "_early_profile_transition_signal",
            return_value="profile_header",
        ), patch.object(nav.time, "sleep"):
            ok = nav.tap_account_result(
                device,
                "faydesdjinns",
                preverified_exact_row_bounds={
                    "left": 120,
                    "top": 300,
                    "right": 420,
                    "bottom": 380,
                },
                preverified_exact_result_at_monotonic=time.monotonic(),
                preverified_exact_result_method=(
                    "unfollow_direct_stable_exact_accessibility_live"
                ),
            )
        self.assertTrue(ok)
        self.assertEqual(device.clicks, [(270, 340)])

    def test_expired_preverified_evidence_is_rejected_fail_closed(self) -> None:
        class Device:
            def __init__(self) -> None:
                self.clicks = []

            def click(self, x, y):
                self.clicks.append((x, y))

        device = Device()
        with patch.object(
            nav,
            "find_real_account_text_element",
            return_value=None,
        ), patch.object(nav, "_dump_no_real_account_row_debug"), patch.object(
            nav.time,
            "sleep",
        ), patch.object(nav, "log") as log_mock:
            ok = nav.tap_account_result(
                device,
                "target",
                preverified_exact_row_bounds={
                    "left": 120,
                    "top": 300,
                    "right": 420,
                    "bottom": 380,
                },
                preverified_exact_screen_bounds={
                    "left": 0,
                    "top": 0,
                    "right": 1080,
                    "bottom": 2400,
                },
                preverified_exact_result_at_monotonic=time.monotonic() - 2.0,
                preverified_exact_result_method="unfollow_direct_stable_exact_xml",
            )
        self.assertFalse(ok)
        self.assertEqual(device.clicks, [])
        self.assertTrue(
            any(
                call.args[1] == "unfollow_direct_preverified_exact_row_rejected"
                and call.kwargs.get("reason") == "preverified_exact_evidence_expired"
                for call in log_mock.call_args_list
            )
        )

    def test_one_remaining_keeps_progressive_primary_until_exhausted(self) -> None:
        out = choose_hybrid_selection(["one"])
        self.assertEqual(out.mode, "progressive_scan")
        self.assertEqual(out.usernames, ())

    def test_ten_remaining_keeps_progressive_primary_until_exhausted(self) -> None:
        out = choose_hybrid_selection([f"user{i}" for i in range(10)])
        self.assertEqual(out.mode, "progressive_scan")
        self.assertEqual(out.reason, "progressive_scan_primary_not_exhausted")

    def test_more_than_ten_scans_until_exhausted(self) -> None:
        names = [f"user{i}" for i in range(11)]
        self.assertEqual(choose_hybrid_selection(names).mode, "progressive_scan")
        fallback = choose_hybrid_selection(names, scan_exhausted=True)
        self.assertEqual(fallback.mode, "direct_exact")
        self.assertEqual(len(fallback.usernames), 10)

    def test_one_remaining_uses_direct_exact_only_after_progressive_exhaustion(self) -> None:
        out = choose_hybrid_selection(["one"], scan_exhausted=True)
        self.assertEqual(out.mode, "direct_exact")
        self.assertEqual(out.usernames, ("one",))

    def test_direct_fallback_arms_after_ten_recovery_five_contract(self) -> None:
        self.assertTrue(
            can_arm_direct_search_fallback(
                "ui_progressive_search_limit_after_recovery",
                remaining_count=10,
            )
        )

    def test_direct_fallback_arms_at_proved_end_of_list(self) -> None:
        self.assertTrue(
            can_arm_direct_search_fallback(
                "ui_end_of_list_with_candidates_unresolved",
                remaining_count=1,
            )
        )

    def test_stagnation_recovery_stop_does_not_arm_direct_search(self) -> None:
        self.assertFalse(
            can_arm_direct_search_fallback(
                "ui_repeated_viewport_limit_after_recovery",
                remaining_count=10,
            )
        )

    def test_adaptive_or_deadline_stop_does_not_arm_direct_search(self) -> None:
        for reason in (
            "ui_coverage_budget_exhausted",
            "session_time_budget_exhausted",
            "unsafe_marker_detected",
        ):
            with self.subTest(reason=reason):
                self.assertFalse(
                    can_arm_direct_search_fallback(reason, remaining_count=10)
                )

    def test_adaptive_budget_with_actionable_remaining_arms_direct_search(self) -> None:
        self.assertTrue(
            can_arm_direct_search_fallback(
                "ui_coverage_budget_exhausted_with_actionable_remaining",
                remaining_count=1,
            )
        )

    def test_faydes_generic_textview_exact_row_is_detected(self) -> None:
        xml = (
            '<hierarchy><node class="android.widget.EditText" '
            'resource-id="com.instagram.android:id/action_bar_search_edit_text" '
            'text="faydesdjinns" bounds="[60,40][500,100]" />'
            '<node class="android.view.ViewGroup" clickable="true" '
            'bounds="[40,130][520,230]">'
            '<node class="android.widget.TextView" text="faydesdjinns" '
            'bounds="[130,150][400,200]" /></node></hierarchy>'
        )
        out = classify_search_surface_xml(xml, "faydesdjinns")
        self.assertEqual(out["state"], SEARCH_EXACT_RESULT_VISIBLE)
        self.assertEqual(out["bounds"], {"left": 40, "top": 130, "right": 520, "bottom": 230})

    def test_comoraison_french_no_results_is_confirmed(self) -> None:
        out = classify_search_surface_xml(
            _no_results_xml("comoraisoncielesteart", "Aucun résultat."),
            "comoraisoncielesteart",
        )
        self.assertEqual(out["state"], SEARCH_NO_RESULTS_CONFIRMED)
        self.assertEqual(out["reason"], "username_not_found_confirmed")

    def test_query_mismatch_is_unhealthy_not_not_found(self) -> None:
        out = classify_search_surface_xml(
            _no_results_xml("someone_else", "Aucun résultat"),
            "target",
        )
        self.assertEqual(out["state"], SEARCH_SURFACE_UNHEALTHY)

    def test_committed_query_without_result_or_empty_state_is_loading(self) -> None:
        out = classify_search_surface_xml(_search_xml(query="target"), "target")
        self.assertEqual(out["state"], SEARCH_RESULTS_LOADING)

    def test_exact_result_without_avatar_uses_clickable_ancestor(self) -> None:
        xml = (
            '<hierarchy><node class="android.widget.EditText" '
            'resource-id="com.instagram.android:id/action_bar_search_edit_text" '
            'text="faydesdjinns" bounds="[60,40][500,100]" />'
            '<node clickable="true" bounds="[20,120][540,220]">'
            '<node clickable="false" bounds="[100,145][410,200]">'
            '<node text="faydesdjinns" bounds="[120,150][390,195]" />'
            '</node></node></hierarchy>'
        )
        out = classify_search_surface_xml(xml, "faydesdjinns")
        self.assertEqual(out["state"], SEARCH_EXACT_RESULT_VISIBLE)
        self.assertEqual(
            out["bounds"],
            {"left": 20, "top": 120, "right": 540, "bottom": 220},
        )

    def test_duplicate_xml_labels_for_one_canonical_row_are_not_ambiguous(self) -> None:
        xml = (
            '<hierarchy><node class="android.widget.EditText" '
            'resource-id="com.instagram.android:id/action_bar_search_edit_text" '
            'text="abbygracephoto.stl" bounds="[0,0][540,90]" />'
            '<node clickable="true" content-desc="abbygracephoto.stl" '
            'bounds="[0,120][540,230]">'
            '<node resource-id="com.instagram.android:id/row_search_user_username" '
            'text="abbygracephoto.stl" bounds="[110,145][430,205]" />'
            '<node text="abbygracephoto.stl" bounds="[108,143][432,207]" />'
            '</node></hierarchy>'
        )
        out = classify_search_surface_xml(xml, "abbygracephoto.stl")
        self.assertEqual(out["state"], SEARCH_EXACT_RESULT_VISIBLE)
        self.assertEqual(out["exact_match_count"], 1)
        self.assertEqual(
            out["bounds"],
            {"left": 0, "top": 120, "right": 540, "bottom": 230},
        )

    def test_two_physically_distinct_canonical_rows_remain_fail_closed(self) -> None:
        xml = (
            '<hierarchy><node class="android.widget.EditText" '
            'resource-id="com.instagram.android:id/action_bar_search_edit_text" '
            'text="target" bounds="[0,0][540,90]" />'
            '<node clickable="true" bounds="[0,120][540,220]">'
            '<node resource-id="com.instagram.android:id/row_search_user_username" '
            'text="target" bounds="[100,140][420,200]" /></node>'
            '<node clickable="true" bounds="[0,300][540,400]">'
            '<node resource-id="com.instagram.android:id/row_search_user_username" '
            'text="target" bounds="[100,320][420,380]" /></node>'
            '</hierarchy>'
        )
        out = classify_search_surface_xml(xml, "target")
        self.assertEqual(out["state"], SEARCH_SURFACE_UNHEALTHY)
        self.assertEqual(out["reason"], "multiple_exact_account_rows")
        self.assertEqual(out["exact_match_count"], 2)

    def test_one_exact_among_approximate_results_is_selected(self) -> None:
        xml = _search_xml("faydesdjinns_backup", "faydesdjinns", query="faydesdjinns")
        out = classify_search_surface_xml(xml, "faydesdjinns")
        self.assertEqual(out["state"], SEARCH_EXACT_RESULT_VISIBLE)
        self.assertEqual(out["exact_match_count"], 1)

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
                self.dumps = iter([
                    _search_xml("someone_else", query="target"),
                    _search_xml("target"),
                    _search_xml("target"),
                ])

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

    def test_stale_xml_uses_two_stable_exact_live_accessibility_observations(self) -> None:
        class Element:
            info = {
                "bounds": {
                    "left": 120,
                    "top": 300,
                    "right": 420,
                    "bottom": 380,
                }
            }

        class Device:
            def dump_hierarchy(self, compressed=False):
                del compressed
                return ""

        with patch("instagram_navigation.open_search", return_value=True), patch(
            "instagram_navigation.type_search", return_value=True
        ), patch(
            "instagram_navigation.find_real_account_text_element",
            side_effect=[Element(), Element()],
        ) as live_exact_mock, patch(
            "instagram_navigation.tap_account_result", return_value=True
        ) as tap_mock, patch(
            "unfollow_profile_probe.verify_unfollow_target_profile_strict",
            return_value={"ok": True},
        ), patch("unfollow_hybrid_strategy.time.sleep"):
            out = open_exact_profile_for_unfollow(Device(), "faydesdjinns")
        self.assertTrue(out["ok"])
        self.assertEqual(live_exact_mock.call_count, 2)
        tap_mock.assert_called_once_with(
            ANY,
            "faydesdjinns",
            preverified_exact_row_bounds={
                "left": 120,
                "top": 300,
                "right": 420,
                "bottom": 380,
            },
            preverified_exact_screen_bounds={},
            preverified_exact_result_at_monotonic=ANY,
            preverified_exact_result_method=(
                "unfollow_direct_stable_exact_accessibility_live"
            ),
        )

    def test_live_accessibility_accepts_small_compatible_bounds_shift(self) -> None:
        class Element:
            def __init__(self, left):
                self.info = {
                    "bounds": {
                        "left": left,
                        "top": 300,
                        "right": left + 300,
                        "bottom": 380,
                    }
                }

        class Device:
            def dump_hierarchy(self, compressed=False):
                del compressed
                return ""

        with patch("instagram_navigation.open_search", return_value=True), patch(
            "instagram_navigation.type_search", return_value=True
        ), patch(
            "instagram_navigation.find_real_account_text_element",
            side_effect=[Element(120), Element(140)],
        ), patch(
            "instagram_navigation.tap_account_result",
            return_value=True,
        ) as tap_mock, patch(
            "unfollow_profile_probe.verify_unfollow_target_profile_strict",
            return_value={"ok": True},
        ), patch(
            "unfollow_hybrid_strategy.time.sleep"
        ):
            out = open_exact_profile_for_unfollow(Device(), "target")
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "profile_opened")
        tap_mock.assert_called_once()

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
        self.assertEqual(out["status"], "search_surface_unhealthy")
        self.assertEqual(out["local_retry_count"], 1)
        self.assertEqual(out["reason"], "search_query_field_mismatch")

    def test_direct_search_retries_one_uncommitted_search_tab_transition(self) -> None:
        class Device:
            def dump_hierarchy(self, compressed=False):
                del compressed
                return _search_xml("target")

        with patch(
            "instagram_navigation.open_search", side_effect=[False, True]
        ) as open_search_mock, patch(
            "instagram_navigation.type_search", return_value=True
        ), patch(
            "instagram_navigation.tap_account_result", return_value=True
        ), patch(
            "unfollow_profile_probe.verify_unfollow_target_profile_strict",
            return_value={"ok": True},
        ), patch("unfollow_hybrid_strategy.time.sleep"):
            out = open_exact_profile_for_unfollow(Device(), "target")
        self.assertTrue(out["ok"])
        self.assertEqual(open_search_mock.call_count, 2)

    def test_direct_search_stops_after_one_failed_transition_retry(self) -> None:
        with patch("instagram_navigation.open_search", return_value=False) as open_search_mock:
            out = open_exact_profile_for_unfollow(object(), "target")
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "retryable")
        self.assertEqual(out["reason"], "open_search_failed_after_bounded_retry")
        self.assertEqual(open_search_mock.call_count, 2)

    def test_not_found_refreshes_and_retypes_exactly_once(self) -> None:
        class Device:
            def dump_hierarchy(self, compressed=False):
                del compressed
                return _no_results_xml()

        with patch("instagram_navigation.open_search", return_value=True), patch(
            "instagram_navigation.type_search", return_value=True
        ) as type_mock, patch("unfollow_hybrid_strategy.time.sleep"):
            out = open_exact_profile_for_unfollow(Device(), "missing_account")
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "username_not_found_confirmed")
        self.assertEqual(out["local_retry_count"], 1)
        self.assertEqual(type_mock.call_count, 2)

    def test_exact_result_on_local_retry_is_opened_without_approximation(self) -> None:
        class Device:
            def __init__(self) -> None:
                self.dumps = 0

            def dump_hierarchy(self, compressed=False):
                del compressed
                self.dumps += 1
                return _no_results_xml("target") if self.dumps <= 2 else _search_xml("target")

        with patch("instagram_navigation.open_search", return_value=True), patch(
            "instagram_navigation.type_search", return_value=True
        ) as type_mock, patch(
            "instagram_navigation.tap_account_result", return_value=True
        ) as tap_mock, patch(
            "unfollow_profile_probe.verify_unfollow_target_profile_strict",
            return_value={"ok": True},
        ), patch("unfollow_hybrid_strategy.time.sleep"):
            out = open_exact_profile_for_unfollow(Device(), "target")
        self.assertTrue(out["ok"])
        self.assertEqual(out["local_retry_count"], 1)
        self.assertEqual(type_mock.call_count, 2)
        tap_mock.assert_called_once_with(
            ANY,
            "target",
            preverified_exact_row_bounds={"left": 0, "top": 100, "right": 500, "bottom": 180},
            preverified_exact_screen_bounds={
                "left": 0,
                "top": 0,
                "right": 500,
                "bottom": 180,
            },
            preverified_exact_result_at_monotonic=ANY,
            preverified_exact_result_method="unfollow_direct_stable_exact_xml",
        )

    def test_partial_result_never_authorizes_a_tap(self) -> None:
        class Device:
            def dump_hierarchy(self, compressed=False):
                del compressed
                return _search_xml("target_backup", query="target")

        with patch("instagram_navigation.open_search", return_value=True), patch(
            "instagram_navigation.type_search", return_value=True
        ), patch("instagram_navigation.tap_account_result") as tap_mock, patch(
            "unfollow_hybrid_strategy.time.sleep"
        ):
            out = open_exact_profile_for_unfollow(Device(), "target")
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "search_surface_unhealthy")
        tap_mock.assert_not_called()

    def test_wrong_profile_after_exact_row_is_refused(self) -> None:
        class Device:
            def dump_hierarchy(self, compressed=False):
                del compressed
                return _search_xml("target")

        with patch("instagram_navigation.open_search", return_value=True), patch(
            "instagram_navigation.type_search", return_value=True
        ), patch("instagram_navigation.tap_account_result", return_value=True), patch(
            "unfollow_profile_probe.verify_unfollow_target_profile_strict",
            return_value={"ok": False, "failure_reason": "profile_identity_unconfirmed"},
        ), patch("unfollow_hybrid_strategy.time.sleep"):
            out = open_exact_profile_for_unfollow(Device(), "target")
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "ambiguous")
        self.assertEqual(out["reason"], "profile_identity_unconfirmed")

    def test_slightly_moved_exact_bounds_are_stable_and_use_latest_bounds(self) -> None:
        class Device:
            def __init__(self) -> None:
                self.dumps = iter(
                    [
                        _search_xml("target"),
                        _search_xml("target").replace(
                            'bounds="[0,100][500,180]"',
                            'bounds="[0,108][500,188]"',
                        ).replace(
                            'bounds="[20,110][300,170]"',
                            'bounds="[20,118][300,178]"',
                        ),
                    ]
                )

            def dump_hierarchy(self, compressed=False):
                del compressed
                return next(self.dumps)

        with patch("instagram_navigation.open_search", return_value=True), patch(
            "instagram_navigation.type_search", return_value=True
        ), patch("instagram_navigation.tap_account_result", return_value=True) as tap_mock, patch(
            "unfollow_profile_probe.verify_unfollow_target_profile_strict",
            return_value={"ok": True},
        ), patch("unfollow_hybrid_strategy.time.sleep"):
            out = open_exact_profile_for_unfollow(Device(), "target")
        self.assertTrue(out["ok"])
        self.assertEqual(out["row_tap_retry_count"], 0)
        self.assertEqual(
            tap_mock.call_args.kwargs["preverified_exact_row_bounds"],
            {"left": 0, "top": 108, "right": 500, "bottom": 188},
        )

    def test_failed_first_row_tap_gets_one_bounded_local_retry(self) -> None:
        class Device:
            def dump_hierarchy(self, compressed=False):
                del compressed
                return _search_xml("annmarieplanning")

        with patch("instagram_navigation.open_search", return_value=True), patch(
            "instagram_navigation.type_search", return_value=True
        ), patch(
            "instagram_navigation.tap_account_result",
            side_effect=[False, True],
        ) as tap_mock, patch(
            "unfollow_profile_probe.verify_unfollow_target_profile_strict",
            side_effect=[{"ok": False}, {"ok": True}],
        ), patch("unfollow_hybrid_strategy.time.sleep"):
            out = open_exact_profile_for_unfollow(Device(), "annmarieplanning")
        self.assertTrue(out["ok"])
        self.assertEqual(out["row_tap_retry_count"], 1)
        self.assertEqual(tap_mock.call_count, 2)

    def test_tap_without_profile_transition_gets_one_bounded_local_retry(self) -> None:
        class Device:
            def dump_hierarchy(self, compressed=False):
                del compressed
                return _search_xml("annmarieplanning")

        with patch("instagram_navigation.open_search", return_value=True), patch(
            "instagram_navigation.type_search", return_value=True
        ), patch(
            "instagram_navigation.tap_account_result",
            side_effect=[True, True],
        ) as tap_mock, patch(
            "unfollow_profile_probe.verify_unfollow_target_profile_strict",
            side_effect=[{"ok": False}, {"ok": True}],
        ), patch("unfollow_hybrid_strategy.time.sleep"):
            out = open_exact_profile_for_unfollow(Device(), "annmarieplanning")
        self.assertTrue(out["ok"])
        self.assertEqual(out["row_tap_retry_count"], 1)
        self.assertEqual(tap_mock.call_count, 2)

    def test_ten_consecutive_exact_results_all_reach_safe_tap(self) -> None:
        class Device:
            def __init__(self, username: str) -> None:
                self.username = username

            def dump_hierarchy(self, compressed=False):
                del compressed
                return _search_xml(self.username)

        with patch("instagram_navigation.open_search", return_value=True), patch(
            "instagram_navigation.type_search", return_value=True
        ), patch("instagram_navigation.tap_account_result", return_value=True) as tap_mock, patch(
            "unfollow_profile_probe.verify_unfollow_target_profile_strict",
            return_value={"ok": True},
        ), patch("unfollow_hybrid_strategy.time.sleep"):
            outcomes = [
                open_exact_profile_for_unfollow(Device(f"target_{index}"), f"target_{index}")
                for index in range(10)
            ]
        self.assertTrue(all(outcome["ok"] for outcome in outcomes))
        self.assertEqual(tap_mock.call_count, 10)

    def test_direct_exact_path_has_no_fixed_fifteen_second_sleep(self) -> None:
        source = inspect.getsource(open_exact_profile_for_unfollow)
        self.assertNotIn("sleep(15", source)
        self.assertNotIn("sleep(15.0", source)

    def test_confirmed_removed_account_fixture_is_not_marked_success(self) -> None:
        class Device:
            def dump_hierarchy(self, compressed=False):
                del compressed
                return _no_results_xml("comoraisoncielesteart", "Aucun résultat")

        with patch("instagram_navigation.open_search", return_value=True), patch(
            "instagram_navigation.type_search", return_value=True
        ), patch("unfollow_hybrid_strategy.time.sleep"):
            out = open_exact_profile_for_unfollow(Device(), "comoraisoncielesteart")
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "username_not_found_confirmed")
        self.assertEqual(out["exact_match_count"], 0)

    def test_search_remains_fallback_after_progressive_scan(self) -> None:
        before_exhaustion = choose_hybrid_selection(
            ["target"],
            scan_exhausted=False,
        )
        after_exhaustion = choose_hybrid_selection(
            ["target"],
            scan_exhausted=True,
        )
        self.assertEqual(before_exhaustion.mode, "progressive_scan")
        self.assertEqual(after_exhaustion.mode, "direct_exact")

    def test_direct_candidate_returns_to_existing_search_session(self) -> None:
        with patch.object(
            unfollow_session,
            "return_to_search_from_profile",
            return_value=True,
        ), patch.object(
            unfollow_session,
            "open_own_following_list_from_own_profile",
        ) as reopen:
            out = unfollow_session._return_after_unfollow_profile(
                object(),
                account_username="owner",
                direct_exact_search=True,
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["destination"], "search")
        self.assertTrue(out["search_session_reused"])
        reopen.assert_not_called()

    def test_direct_candidate_falls_back_to_following_when_search_return_fails(self) -> None:
        with patch.object(
            unfollow_session,
            "return_to_search_from_profile",
            return_value=False,
        ), patch.object(
            unfollow_session,
            "open_own_following_list_from_own_profile",
            return_value=(True, {"reason": "reopened"}),
        ):
            out = unfollow_session._return_after_unfollow_profile(
                object(),
                account_username="owner",
                direct_exact_search=True,
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["destination"], "following")
        self.assertFalse(out["search_session_reused"])


if __name__ == "__main__":
    unittest.main()
