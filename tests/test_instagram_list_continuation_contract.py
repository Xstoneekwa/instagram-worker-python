from __future__ import annotations

import inspect
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import account_session_orchestrator as session
import instagram_navigation as nav
from instagram_list_continuation import (
    InstagramListContinuationSignals,
    InstagramListContinuationState as State,
    canonical_follow_scroll_geometry,
    classify_instagram_list_continuation,
    compare_instagram_list_viewports,
)
import runner
import unfollow_session_orchestrator as unfollow
import welcome_scan_producer as welcome


def _node(
    *,
    text: str = "",
    rid: str = "",
    bounds: str = "[0,0][100,100]",
    selected: bool = False,
    clickable: bool = False,
    klass: str = "android.widget.TextView",
) -> str:
    return (
        f'<node text="{text}" content-desc="" resource-id="{rid}" '
        f'class="{klass}" bounds="{bounds}" selected="{str(selected).lower()}" '
        f'clickable="{str(clickable).lower()}" />'
    )


def _surface(
    *,
    rows: list[tuple[str, str]] | None = None,
    see_more: bool = False,
    suggestions: bool = False,
    suggestion_rows: list[str] | None = None,
    loading: bool = False,
) -> str:
    parts = ["<hierarchy>", _node(text="22.2K followers", selected=True, bounds="[180,120][520,200]")]
    for idx, (username, cta) in enumerate(rows or []):
        top = 250 + idx * 120
        parts.append(
            f'<node resource-id="com.instagram.android:id/follow_list_container" bounds="[0,{top}][1080,{top + 110}]">'
            + _node(
                text=username,
                rid="com.instagram.android:id/follow_list_username",
                bounds=f"[120,{top + 10}][520,{top + 60}]",
            )
            + _node(
                text=cta,
                rid="com.instagram.android:id/follow_list_row_large_follow_button",
                bounds=f"[720,{top + 10}][1030,{top + 80}]",
            )
            + "</node>"
        )
    marker_top = 1180
    if see_more:
        parts.append(_node(text="See more", bounds=f"[0,{marker_top}][300,{marker_top + 80}]", clickable=True))
        marker_top += 100
    if suggestions:
        parts.append(_node(text="Suggested for you", bounds=f"[0,{marker_top}][700,{marker_top + 80}]"))
        for idx, username in enumerate(suggestion_rows or ["suggestion_row"]):
            top = marker_top + 100 + idx * 120
            parts.append(
                f'<node resource-id="com.instagram.android:id/follow_list_container" bounds="[0,{top}][1080,{top + 110}]">'
                + _node(
                    text=username,
                    rid="com.instagram.android:id/follow_list_username",
                    bounds=f"[120,{top + 10}][520,{top + 60}]",
                )
                + _node(text="Follow", bounds=f"[720,{top + 10}][980,{top + 80}]")
                + _node(text="×", rid="com.instagram.android:id/dismiss", bounds=f"[1010,{top + 10}][1070,{top + 70}]")
                + "</node>"
            )
    if loading:
        parts.append(_node(rid="com.instagram.android:id/loading", klass="android.widget.ProgressBar"))
    parts.append("</hierarchy>")
    return "".join(parts)


class _FakeSelector:
    def __init__(self, device: "_FakeDevice", matches: bool) -> None:
        self.device = device
        self.matches = matches

    def exists(self, timeout: float = 0.0) -> bool:
        _ = timeout
        return self.matches

    def click(self) -> None:
        self.device.clicks += 1


class _FakeDevice:
    def __init__(self, after_xml: str) -> None:
        self.after_xml = after_xml
        self.clicks = 0

    def __call__(self, **kwargs):
        pattern = str(kwargs.get("textMatches") or kwargs.get("descriptionMatches") or "")
        return _FakeSelector(self, "see more" in pattern.casefold())

    def dump_hierarchy(self, compressed: bool = False) -> str:
        _ = compressed
        return self.after_xml


class _FakeFollowersEngine:
    def __init__(self, responses: list[tuple[int, dict]]) -> None:
        self.responses = responses
        self.calls: list[dict] = []
        self.last_session_summary: dict = {}

    def __call__(self, _device, **kwargs) -> int:
        self.calls.append(dict(kwargs))
        code, summary = self.responses[len(self.calls) - 1]
        self.last_session_summary = dict(summary)
        return code


def _target(index: int) -> dict:
    return {"target_id": f"target-{index}", "source_profile": f"source-{index}"}


class InstagramListContinuationContractTests(unittest.TestCase):
    def test_01_primary_rows_only(self) -> None:
        out = nav.followers_list_continuation_from_hierarchy_xml(
            _surface(rows=[("row_a", "Follow")])
        )
        self.assertEqual(out["state"], State.PRIMARY_ROWS_AVAILABLE.value)

    def test_02_primary_rows_take_priority_over_see_more(self) -> None:
        out = nav.followers_list_continuation_from_hierarchy_xml(
            _surface(rows=[("row_a", "Follow")], see_more=True)
        )
        self.assertEqual(out["state"], State.PRIMARY_ROWS_AVAILABLE.value)

    def test_03_see_more_before_suggestions_expands_after_rows_processed(self) -> None:
        out = nav.followers_list_continuation_from_hierarchy_xml(
            _surface(rows=[("row_a", "Following")], see_more=True, suggestions=True),
            processed_primary_row_ids={"row_a"},
        )
        self.assertEqual(out["state"], State.EXPAND_PRIMARY_LIST_AVAILABLE.value)
        self.assertFalse(out["is_boundary"])

    def test_04_see_more_click_loads_new_primary_rows(self) -> None:
        before = _surface(rows=[], see_more=True, suggestions=True)
        after = _surface(rows=[("row_new", "Follow")])
        nav._followers_store_detect_hierarchy_xml(before)
        device = _FakeDevice(after)
        with patch.object(nav.time, "sleep", return_value=None):
            result = nav.followers_try_expand_primary_list(device, max_attempts=2)
        self.assertTrue(result["expanded"])
        self.assertEqual(device.clicks, 1)

    def test_05_see_more_no_result_stops_after_two_selector_clicks(self) -> None:
        xml = _surface(rows=[], see_more=True, suggestions=True)
        nav._followers_store_detect_hierarchy_xml(xml)
        device = _FakeDevice(xml)
        with patch.object(nav.time, "sleep", return_value=None):
            result = nav.followers_try_expand_primary_list(device, max_attempts=2)
        self.assertFalse(result["expanded"])
        self.assertEqual(result["reason"], "see_more_no_progress")
        self.assertEqual(device.clicks, 2)

    def test_06_suggestions_without_see_more_is_boundary(self) -> None:
        out = nav.followers_list_continuation_from_hierarchy_xml(
            _surface(rows=[], suggestions=True), continuation_probe_count=1
        )
        self.assertEqual(out["state"], State.SUGGESTIONS_BOUNDARY_CONFIRMED.value)
        runner_source = inspect.getsource(runner._run_followers_list_engine_session)
        self.assertIn("continuation_probe_count=2", runner_source)
        self.assertIn("followers_refresh_detect_hierarchy_cache", runner_source)

    def test_07_gentle_next_page_keeps_anchor(self) -> None:
        result = compare_instagram_list_viewports(["a", "b", "c"], ["c", "d", "e"])
        self.assertTrue(result.continuity_proved)
        self.assertEqual(result.overlap_count, 1)

    def test_08_excessive_scroll_is_detected(self) -> None:
        result = compare_instagram_list_viewports(["a", "b"], ["x", "y"])
        self.assertTrue(result.excessive)

    def test_09_intermediate_page_skip_is_not_continuity(self) -> None:
        result = compare_instagram_list_viewports(["page1_a", "page1_b"], ["page3_a", "page3_b"])
        self.assertFalse(result.continuity_proved)
        self.assertEqual(result.reason, "no_viewport_overlap")

    def test_10_unchanged_viewport_is_no_progress(self) -> None:
        continuity = compare_instagram_list_viewports(["a", "b"], ["a", "b"])
        state = classify_instagram_list_continuation(
            InstagramListContinuationSignals(
                flow="follow",
                expected_surface_selected=True,
                primary_row_ids=("a", "b"),
                processed_primary_row_ids=("a", "b"),
                scroll_attempted=True,
                viewport_fingerprint_before=continuity.fingerprint_before,
                viewport_fingerprint_after=continuity.fingerprint_after,
            )
        )
        self.assertEqual(state, State.NO_PROGRESS)

    def test_11_already_processed_rows_are_not_new_work(self) -> None:
        state = classify_instagram_list_continuation(
            InstagramListContinuationSignals(
                flow="follow",
                expected_surface_selected=True,
                primary_row_ids=("a",),
                processed_primary_row_ids=("a",),
            )
        )
        self.assertEqual(state, State.AMBIGUOUS_SURFACE)

    def test_12_following_cta_remains_a_primary_row(self) -> None:
        out = nav.followers_list_continuation_from_hierarchy_xml(
            _surface(rows=[("row_a", "Following")])
        )
        self.assertEqual(out["primary_row_count"], 1)

    def test_13_follow_cta_remains_a_primary_row(self) -> None:
        out = nav.followers_list_continuation_from_hierarchy_xml(
            _surface(rows=[("row_a", "Follow")])
        )
        self.assertEqual(out["primary_row_count"], 1)

    def test_14_suggestion_cta_is_excluded_from_primary_rows(self) -> None:
        out = nav.followers_list_continuation_from_hierarchy_xml(
            _surface(rows=[], suggestions=True, suggestion_rows=["suggestion_a"])
        )
        self.assertEqual(out["primary_row_count"], 0)
        self.assertEqual(out["suggestion_follow_rows"], 1)

    def test_15_eight_follows_and_true_boundary_rotates(self) -> None:
        engine = _FakeFollowersEngine([
            (0, {"follows_completed_count": 8, "follow_session_outcome": "follows_completed", "follow_stop_reason": "followers_suggestions_boundary", "global_follows_goal_effective": 40}),
            (0, {"follows_completed_count": 1, "follow_session_outcome": "completed", "follow_stop_reason": "", "global_follows_goal_effective": 40}),
        ])
        result = session._run_follow_target_rotation(
            object(), account_id="account", account_username="account", run_id="run",
            follow_targets=[_target(1), _target(2), _target(3), _target(4)],
            run_followers_list_engine_session=engine, supabase_mode=False,
            warm_session_used=False, force_stop_used=False,
            max_targets_per_run=4, max_follows_per_target_per_run=30,
        )
        self.assertEqual([call["target_id"] for call in engine.calls], ["target-1", "target-2"])
        self.assertEqual(result["exhausted_targets"][0]["target_id"], "target-1")

    def test_16_eight_follows_and_see_more_does_not_rotate(self) -> None:
        engine = _FakeFollowersEngine([
            (0, {"follows_completed_count": 8, "follow_session_outcome": "follows_completed", "follow_stop_reason": "expand_primary_list_available"}),
        ])
        session._run_follow_target_rotation(
            object(), account_id="account", account_username="account", run_id="run",
            follow_targets=[_target(1), _target(2)], run_followers_list_engine_session=engine,
            supabase_mode=False, warm_session_used=False, force_stop_used=False,
            max_targets_per_run=4, max_follows_per_target_per_run=30,
        )
        self.assertEqual(len(engine.calls), 1)

    def test_17_four_target_limit_is_respected(self) -> None:
        terminal = {"follows_completed_count": 0, "follow_session_outcome": "no_followable_candidates_bounded_exploration", "follow_stop_reason": "bounded_exploration_exhausted"}
        engine = _FakeFollowersEngine([(0, terminal)] * 4)
        session._run_follow_target_rotation(
            object(), account_id="account", account_username="account", run_id="run",
            follow_targets=[_target(i) for i in range(1, 6)], run_followers_list_engine_session=engine,
            supabase_mode=False, warm_session_used=False, force_stop_used=False,
            max_targets_per_run=4, max_follows_per_target_per_run=30,
        )
        self.assertEqual(len(engine.calls), 4)

    def test_18_follow_uses_controlled_geometry_and_profile(self) -> None:
        geometry = canonical_follow_scroll_geometry(1080, 2400)
        self.assertAlmostEqual(float(geometry["distance_ratio"]), 0.24)
        source = inspect.getsource(runner._run_followers_list_engine_session)
        self.assertGreaterEqual(source.count('"canonical_controlled"'), 3)

    def test_19_unfollow_business_scroll_remains_legacy(self) -> None:
        source = inspect.getsource(unfollow._scroll_following_list_for_unfollow)
        self.assertIn("y_start = int(h * 0.78)", source)
        self.assertIn("y_end = int(h * 0.36)", source)
        self.assertNotIn("canonical_controlled", source)

    def test_20_welcome_uses_shared_classifier_without_business_change(self) -> None:
        xml = _surface(rows=[], suggestions=True)
        self.assertEqual(
            welcome._followers_suggestions_boundary(xml, previously_valid_followers_rows=True)["state"],
            nav.followers_list_continuation_from_hierarchy_xml(xml, flow="welcome_dm")["state"],
        )
        self.assertIn('scroll_profile="welcome_soft"', inspect.getsource(welcome.run_welcome_scan_producer))

    def test_21_no_real_username_is_hardcoded(self) -> None:
        source = "\n".join(
            (
                (Path(__file__).parents[1] / "instagram_list_continuation.py").read_text(encoding="utf-8"),
                inspect.getsource(nav.followers_list_continuation_from_hierarchy_xml),
                inspect.getsource(nav.followers_try_expand_primary_list),
            )
        )
        forbidden_values = (
            "_".join(("artisan", "electricien", "prefere")),
            "_".join(("mythyl", "fitness")),
            "".join(("cap", "teo", "_")),
            ".".join(("loic", "gerboud")),
        )
        for forbidden in forbidden_values:
            self.assertNotIn(forbidden, source)

    def test_22_see_more_is_clicked_by_selector_not_coordinates(self) -> None:
        source = inspect.getsource(nav.followers_try_expand_primary_list)
        self.assertIn("selector.click()", source)
        self.assertNotIn("d.click(", source)

    def test_23_no_follow_candidate_is_taken_from_suggestions(self) -> None:
        out = nav.followers_list_continuation_from_hierarchy_xml(
            _surface(rows=[], suggestions=True, suggestion_rows=["suggestion_a", "suggestion_b"])
        )
        self.assertEqual(out["primary_row_ids"], [])
        self.assertTrue(out["is_boundary"])

    def test_24_redacted_capture_viewports_preserve_expected_anchors(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "instagram_list_continuation" / "capture_sequence_redacted.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        captures = {item["capture"]: item for item in fixture["captures"]}
        for expected in fixture["expected_overlap"]:
            before_rows = captures[expected["from"]]["primary_rows"]
            after_rows = captures[expected["to"]]["primary_rows"]
            result = compare_instagram_list_viewports(
                before_rows,
                after_rows,
            )
            self.assertEqual(result.overlap_count, len(expected["rows"]))
            self.assertFalse(result.excessive)
            self.assertEqual(before_rows[-result.overlap_count :], expected["rows"])
            self.assertEqual(after_rows[: result.overlap_count], expected["rows"])


if __name__ == "__main__":
    unittest.main()
