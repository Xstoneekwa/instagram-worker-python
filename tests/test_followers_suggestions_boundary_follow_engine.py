from pathlib import Path
import unittest

import instagram_navigation as nav
import runner


def _cache(xml: str) -> None:
    nav._followers_store_detect_hierarchy_xml(xml)


class FollowersSuggestionsBoundaryTests(unittest.TestCase):
    """Synthetic contract fixtures; these are not physical Instagram captures."""

    def test_follow_boundary_detects_suggestions_rows_without_another_probe(self) -> None:
        xml = """<hierarchy>
        <node text="22,2K followers" selected="true" bounds="[0,100][1080,200]" />
        <node text="See all suggestions" bounds="[0,500][700,560]" />
        <node text="Follow" bounds="[700,600][980,680]" />
        <node text="X" bounds="[980,600][1080,680]" />
        </hierarchy>"""
        _cache(xml)
        result = nav.followers_suggestions_boundary_from_cached_hierarchy(
            previously_valid_followers_rows=True
        )
        self.assertIs(result["is_boundary"], True)
        self.assertIs(result["see_all_suggestions"], True)
        self.assertEqual(result["suggestion_follow_rows"], 1)
        self.assertEqual(result["dismiss_controls"], 1)
        self.assertEqual(result["primary_row_ids"], [])
        self.assertEqual(result["state"], "SUGGESTIONS_BOUNDARY_CONFIRMED")
        self.assertEqual(nav._followers_cached_hierarchy_xml(), xml)

    def test_follow_boundary_preserves_selected_followers_loading_state(self) -> None:
        for structural, expected in ((False, "AMBIGUOUS_SURFACE"), (True, "TRANSIENT_NO_ROWS")):
            with self.subTest(structural=structural):
                _cache("""<hierarchy>
                <node text="28 followers" selected="true" />
                <node class="android.widget.ProgressBar" />
                </hierarchy>""")
                result = nav.followers_suggestions_boundary_from_cached_hierarchy(
                    previously_valid_followers_rows=True,
                    structural_end_contract_enabled=structural,
                )
                self.assertIs(result["loading_indicator"], True)
                self.assertIs(result["is_boundary"], False)
                self.assertEqual(result["state"], expected)
                self.assertFalse(runner._followers_ct_rotation_allowed(result))

    def test_missing_bounds_cannot_attribute_follow_to_suggestions(self) -> None:
        _cache("""<hierarchy>
        <node text="22,2K followers" selected="true" />
        <node text="See all suggestions" />
        <node text="Follow" /><node text="X" />
        </hierarchy>""")
        result = nav.followers_suggestions_boundary_from_cached_hierarchy(
            previously_valid_followers_rows=True
        )
        self.assertEqual(result["suggestion_follow_rows"], 0)
        self.assertIs(result["see_all_suggestions"], True)
        self.assertEqual(result["dismiss_controls"], 1)

    def test_cta_above_suggestions_is_not_counted_as_suggested(self) -> None:
        _cache("""<hierarchy>
        <node text="28 followers" selected="true" bounds="[0,100][1080,200]" />
        <node text="Follow" bounds="[700,300][980,380]" />
        <node text="See all suggestions" bounds="[0,600][700,660]" />
        </hierarchy>""")
        result = nav.followers_suggestions_boundary_from_cached_hierarchy(
            previously_valid_followers_rows=True
        )
        self.assertEqual(result["suggestion_follow_rows"], 0)
        self.assertEqual(result["dismiss_controls"], 0)

    def test_primary_rows_remain_separate_from_suggested_rows(self) -> None:
        _cache("""<hierarchy>
        <node text="28 followers" selected="true" bounds="[0,100][1080,200]" />
        <node bounds="[0,250][1080,400]">
          <node resource-id="com.instagram.android:id/follow_list_username" text="primary_user" bounds="[100,270][600,330]" />
          <node text="Follow" bounds="[700,270][980,330]" />
        </node>
        <node text="See all suggestions" bounds="[0,500][700,560]" />
        <node bounds="[0,600][1080,750]">
          <node resource-id="com.instagram.android:id/follow_list_username" text="suggested_user" bounds="[100,620][600,680]" />
          <node text="Follow" bounds="[700,620][980,680]" />
          <node text="X" bounds="[980,620][1080,680]" />
        </node>
        </hierarchy>""")
        result = nav.followers_suggestions_boundary_from_cached_hierarchy(
            previously_valid_followers_rows=True
        )
        self.assertEqual(result["primary_row_ids"], ["primary_user"])
        self.assertEqual(result["primary_row_count"], 1)
        self.assertEqual(result["suggestion_follow_rows"], 1)
        self.assertEqual(result["state"], "PRIMARY_ROWS_AVAILABLE")
        self.assertIs(result["is_boundary"], False)
        self.assertFalse(runner._followers_ct_rotation_allowed(result))

    def test_follow_engine_stops_before_committed_surface_recovery_at_boundary(self) -> None:
        source = Path("runner.py").read_text(encoding="utf-8")
        boundary = source.index(
            "suggestions_boundary = followers_suggestions_boundary_from_cached_hierarchy"
        )
        committed_recovery = source.index(
            "if followers_session_list_committed_open_for(source_profile_username):",
            boundary,
        )
        block = source[boundary:committed_recovery]
        assert 'action="stop_scrolling_and_complete_target"' in block
        assert "_followers_loop_finally_stop = \"followers_suggestions_boundary\"" in block
        assert "break" in block
        assert "return_to_followers_list" not in block
        assert "time.sleep" not in block
