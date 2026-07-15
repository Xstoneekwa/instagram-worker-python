from pathlib import Path

import instagram_navigation as nav


def _cache(xml: str) -> None:
    nav._followers_store_detect_hierarchy_xml(xml)


def test_follow_boundary_detects_suggestions_rows_without_another_probe() -> None:
    _cache(
        """<hierarchy>
        <node text="22,2K followers" selected="true" />
        <node text="See all suggestions" />
        <node text="Follow" />
        <node text="X" />
        </hierarchy>"""
    )

    result = nav.followers_suggestions_boundary_from_cached_hierarchy(
        previously_valid_followers_rows=True
    )

    assert result["is_boundary"] is True
    assert result["see_all_suggestions"] is True
    assert result["suggestion_follow_rows"] == 1
    assert result["dismiss_controls"] == 1


def test_follow_boundary_preserves_selected_followers_loading_state() -> None:
    _cache(
        """<hierarchy>
        <node text="28 followers" selected="true" />
        <node class="android.widget.ProgressBar" />
        </hierarchy>"""
    )

    result = nav.followers_suggestions_boundary_from_cached_hierarchy(
        previously_valid_followers_rows=True
    )

    assert result["is_boundary"] is True
    assert result["loading_indicator"] is True


def test_follow_engine_stops_before_committed_surface_recovery_at_boundary() -> None:
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
