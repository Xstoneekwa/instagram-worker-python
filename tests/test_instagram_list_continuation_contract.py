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
    adaptive_follow_scroll_geometry,
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
    followers_tab_selected: bool = True,
    followers_count_label: str = "22.2K followers",
) -> str:
    parts = [
        "<hierarchy>",
        _node(
            text=followers_count_label,
            selected=followers_tab_selected,
            bounds="[180,120][520,200]",
        ),
    ]
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


class _SequenceDevice(_FakeDevice):
    def __init__(self, hierarchy_sequence: list[str]) -> None:
        super().__init__(hierarchy_sequence[-1])
        self.hierarchy_sequence = list(hierarchy_sequence)
        self.dump_count = 0

    def dump_hierarchy(self, compressed: bool = False) -> str:
        _ = compressed
        index = min(self.dump_count, len(self.hierarchy_sequence) - 1)
        self.dump_count += 1
        return self.hierarchy_sequence[index]


class _FakeScrollDevice:
    def __init__(self, *, width: int = 1080, height: int = 2400) -> None:
        self.width = width
        self.height = height
        self.swipes: list[tuple[int, int, int, int, float]] = []

    def window_size(self) -> tuple[int, int]:
        return self.width, self.height

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float) -> None:
        self.swipes.append((x1, y1, x2, y2, duration))


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
        self.assertEqual(
            result["reason"],
            "see_more_click_exhausted_after_bounded_recovery",
        )
        self.assertEqual(result["see_more_status"], "see_more_failed_terminal")
        self.assertEqual(device.clicks, 2)

    def test_05d_slow_loading_three_seconds_keeps_same_ct_until_rows_stable(self) -> None:
        before = _surface(rows=[], see_more=True, suggestions=True)
        loading = _surface(rows=[], loading=True)
        partial = _surface(rows=[("row_new", "Follow")], loading=True)
        stable = _surface(rows=[("row_new", "Follow")])
        nav._followers_store_detect_hierarchy_xml(before)
        device = _SequenceDevice([loading] * 7 + [partial, stable, stable])
        with patch.object(nav.time, "sleep", return_value=None):
            result = nav.followers_try_expand_primary_list(device, max_attempts=2)
        self.assertTrue(result["expanded"])
        self.assertTrue(result["loading_observed"])
        self.assertGreaterEqual(result["poll_count"], 9)
        self.assertEqual(device.clicks, 1)

    def test_05e_loading_timeout_is_terminal_only_after_full_poll_budget(self) -> None:
        before = _surface(rows=[], see_more=True, suggestions=True)
        loading = _surface(rows=[], loading=True)
        nav._followers_store_detect_hierarchy_xml(before)
        device = _SequenceDevice([loading])
        with patch.object(nav.time, "sleep", return_value=None):
            result = nav.followers_try_expand_primary_list(device, max_attempts=2)
        self.assertFalse(result["expanded"])
        self.assertEqual(
            result["failure_reason"],
            "see_more_expansion_timeout_after_observed_loading",
        )
        self.assertEqual(device.dump_count, 13)
        self.assertEqual(device.clicks, 1)

    def test_05f_first_partial_frame_is_not_accepted_until_stable(self) -> None:
        before = _surface(rows=[], see_more=True, suggestions=True)
        partial = _surface(rows=[("row_new", "Follow")], loading=True)
        nav._followers_store_detect_hierarchy_xml(before)
        device = _SequenceDevice([partial, partial])
        with patch.object(nav.time, "sleep", return_value=None):
            result = nav.followers_try_expand_primary_list(device, max_attempts=1)
        self.assertTrue(result["expanded"])
        self.assertEqual(result["poll_count"], 2)

    def test_05g_stale_xml_without_mutation_uses_single_bounded_retry(self) -> None:
        before = _surface(rows=[], see_more=True, suggestions=True)
        nav._followers_store_detect_hierarchy_xml(before)
        device = _SequenceDevice([before])
        with patch.object(nav.time, "sleep", return_value=None):
            result = nav.followers_try_expand_primary_list(device, max_attempts=2)
        self.assertFalse(result["expanded"])
        self.assertEqual(result["failure_reason"], "see_more_click_no_surface_mutation")
        self.assertEqual(device.clicks, 2)

    def test_05h_visual_fallback_uses_a_bounded_coarse_signature(self) -> None:
        before = "0" * 256
        after = "1" * 12 + "0" * 244
        self.assertEqual(nav._see_more_visual_signature_distance(before, after), 12)
        self.assertEqual(nav._see_more_visual_signature_distance(before, ""), 0)

    def test_05a_visible_text_with_clickable_parent_is_actionable(self) -> None:
        xml = (
            "<hierarchy>"
            + _node(text="22.2K followers", selected=True)
            + '<node clickable="true" bounds="[0,1180][1080,1280]">'
            + _node(text="See more", bounds="[20,1200][300,1260]")
            + "</node>"
            + _node(text="Suggested for you", bounds="[0,1300][700,1380]")
            + "</hierarchy>"
        )
        out = nav.followers_list_continuation_from_hierarchy_xml(xml)
        self.assertTrue(out["see_more_actionable"])
        self.assertEqual(
            out["see_more_detection_source"],
            "xml_text_clickable_parent",
        )
        self.assertEqual(out["state"], State.EXPAND_PRIMARY_LIST_AVAILABLE.value)

    def test_05b_stale_xml_live_accessibility_confirmation_can_expand(self) -> None:
        nav._followers_store_detect_hierarchy_xml(
            _surface(rows=[], suggestions=True)
        )
        device = _FakeDevice(_surface(rows=[("row_new", "Follow")]))
        with patch.object(nav.time, "sleep", return_value=None):
            result = nav.followers_try_expand_primary_list(device, max_attempts=2)
        self.assertTrue(result["expanded"])
        self.assertEqual(result["see_more_status"], "see_more_expanded")

    def test_05c_required_see_more_events_are_structured(self) -> None:
        before = _surface(rows=[], see_more=True, suggestions=True)
        after = _surface(rows=[("row_new", "Follow")])
        nav._followers_store_detect_hierarchy_xml(before)
        device = _FakeDevice(after)
        with patch.object(nav.time, "sleep", return_value=None), patch.object(
            nav, "log"
        ) as log_mock:
            result = nav.followers_try_expand_primary_list(
                device,
                expected_source_profile="source",
                account_id="account",
                run_id="run",
            )
        # The explicit source name requires a committed navigation surface.
        self.assertFalse(result["expanded"])
        nav._followers_store_detect_hierarchy_xml(before)
        with patch.object(nav.time, "sleep", return_value=None), patch.object(
            nav, "followers_session_list_committed_open_for", return_value=True
        ), patch.object(nav, "log") as log_mock:
            result = nav.followers_try_expand_primary_list(
                device,
                expected_source_profile="source",
                account_id="account",
                run_id="run",
            )
        self.assertTrue(result["expanded"])
        events = [str(call.args[1]) for call in log_mock.call_args_list]
        self.assertIn("see_more_detected", events)
        self.assertIn("see_more_click_attempted", events)
        self.assertIn("see_more_expansion_confirmed", events)

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

    def test_15a_rotation_keeps_rex_request_unset_and_propagates_ct_provenance(self) -> None:
        request_id = "30000000-0000-4000-8000-000000000002"
        policy = {
            "attempt_id": 2,
            "retry_index": 1,
            "phases_to_run": {"welcome": False, "follow": True, "unfollow": False},
        }
        engine = _FakeFollowersEngine(
            [
                (
                    0,
                    {
                        "follows_completed_count": 1,
                        "follow_session_outcome": "completed",
                        "follow_stop_reason": "",
                        "global_follows_goal_effective": 1,
                    },
                )
            ]
        )
        session._run_follow_target_rotation(
            object(),
            account_id="account",
            account_username="account",
            run_id="run",
            follow_targets=[_target(1)],
            run_followers_list_engine_session=engine,
            supabase_mode=False,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=1,
            max_follows_per_target_per_run=1,
            target_followers_resume_source_request_id=request_id,
            auto_restart_resume_policy=policy,
        )
        self.assertNotIn("run_request_id", engine.calls[0])
        self.assertEqual(
            engine.calls[0]["target_followers_resume_source_request_id"],
            request_id,
        )
        self.assertEqual(engine.calls[0]["auto_restart_resume_policy"], policy)
        self.assertIsNot(engine.calls[0]["auto_restart_resume_policy"], policy)

    def test_15b_dispatch_to_account_session_propagates_provenance_to_rotation(self) -> None:
        request_id = "30000000-0000-4000-8000-000000000002"
        policy = {
            "attempt_id": 2,
            "retry_index": 1,
            "phases_to_run": {"welcome": False, "follow": True, "unfollow": False},
            "quota_remaining": {"follow": 1, "unfollow": 0, "total": 1},
        }
        captured: dict = {}

        def stop_after_capture(_device, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop_after_provenance_capture")

        with (
            patch.object(session, "_abort_if_operator_stop_requested", return_value=None),
            patch.object(session, "load_account_commercial_policy_revision", return_value={}),
            patch.object(
                session,
                "_resolve_target_availability_tenant_once",
                return_value=(None, None),
            ),
            patch.object(
                session.supabase_client,
                "get_account_dm_settings",
                return_value={"welcome_enabled": False},
            ),
            patch.object(
                session,
                "resolve_welcome_dm_real_send_enabled",
                return_value=(False, "test"),
            ),
            patch.object(session, "_transition_buffer_blocks_business_actions", return_value=False),
            patch.object(session, "_operator_stop_cancel_requested", return_value=False),
            patch.object(session, "commercial_policy_boundary_blocks_phase", return_value=False),
            patch.object(session, "_follow_to_unfollow_real_enabled", return_value=False),
            patch.object(
                session,
                "_follow_to_unfollow_real_max_actions_effective",
                return_value=0,
            ),
            patch.object(
                session,
                "_resolve_follow_source_rotation_settings",
                return_value={
                    "max_follows_per_target_per_run": 1,
                    "max_targets_per_run": 1,
                    "settings_source": "test",
                    "bounds": {},
                },
            ),
            patch.object(session, "_run_follow_target_rotation", side_effect=stop_after_capture),
        ):
            with self.assertRaisesRegex(RuntimeError, "stop_after_provenance_capture"):
                session.dispatch_account_session(
                    object(),
                    account_id="00000000-0000-4000-8000-000000000001",
                    account_username="account",
                    run_id="20000000-0000-4000-8000-000000000001",
                    source_profile_username="source-1",
                    target_id="10000000-0000-4000-8000-000000000001",
                    follow_targets=[_target(1)],
                    run_followers_list_engine_session=_FakeFollowersEngine([]),
                    supabase_mode=False,
                    warm_session_used=False,
                    force_stop_used=False,
                    auto_restart_resume_policy=policy,
                    target_followers_resume_source_request_id=request_id,
                )

        self.assertNotIn("run_request_id", captured)
        self.assertEqual(
            captured["target_followers_resume_source_request_id"],
            request_id,
        )
        self.assertEqual(captured["auto_restart_resume_policy"], policy)

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

    def test_18_follow_uses_adaptive_geometry_and_preserves_short_fallback(self) -> None:
        geometry = canonical_follow_scroll_geometry(1080, 2400)
        self.assertAlmostEqual(float(geometry["distance_ratio"]), 0.24)
        adaptive = adaptive_follow_scroll_geometry(
            1080,
            2400,
            [300, 420, 540, 660, 780, 900, 1020, 1140],
        )
        self.assertTrue(adaptive["adaptive"])
        self.assertEqual(adaptive["target_new_rows"], 7)
        self.assertEqual(adaptive["target_overlap_rows"], 1)
        self.assertEqual(adaptive["median_row_spacing_px"], 120)
        self.assertAlmostEqual(float(adaptive["distance_ratio"]), 0.35)
        source = inspect.getsource(runner._run_followers_list_engine_session)
        main_scroll_handoff = source.split("_main_scroll_profile_requested", 1)[1][:900]
        self.assertIn('_main_scroll_profile = "canonical_adaptive"', main_scroll_handoff)
        self.assertNotIn('_main_scroll_profile = "canonical_controlled"', main_scroll_handoff)

    def test_19_unfollow_business_scroll_uses_shared_adaptive_contract(self) -> None:
        source = inspect.getsource(unfollow._scroll_following_list_for_unfollow)
        self.assertIn("adaptive_follow_scroll_geometry", source)
        self.assertIn("compare_instagram_list_viewports", source)
        self.assertIn('strategy = "canonical_adaptive_7_plus_1"', source)
        self.assertNotIn("y_start = int(h * 0.78)", source)
        loop_source = inspect.getsource(unfollow._run_real_unfollow_multi_loop)
        self.assertIn(
            "last_fields = {**last_fields, **progress_fields}",
            loop_source,
        )

    def test_19b_unfollow_validates_seven_new_rows_and_one_overlap(self) -> None:
        def row(username: str, center_y: int) -> dict:
            return {
                "username": username,
                "username_normalized": username,
                "row_center": [300, center_y],
            }

        before = [row(f"row_{idx}", 300 + idx * 120) for idx in range(8)]
        after = [row("row_7", 300)] + [
            row(f"row_{idx}", 300 + (idx - 7) * 120)
            for idx in range(8, 15)
        ]
        device = _FakeScrollDevice()
        with (
            patch.object(
                unfollow,
                "harvest_visible_following_rows_for_unfollow",
                return_value=(after, {"following_list_end_detected": False}),
            ),
            patch.object(
                unfollow,
                "detect_own_following_list_screen",
                return_value={"is_following_list": True},
            ),
            patch.object(unfollow.time, "sleep", return_value=None),
        ):
            result = unfollow._scroll_following_list_for_unfollow(
                device,
                account_username="owner",
                before_rows=before,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["depth_advanced"])
        self.assertEqual(result["target_new_rows"], 7)
        self.assertEqual(result["target_overlap_rows"], 1)
        self.assertEqual(result["actual_new_rows"], 7)
        self.assertEqual(result["actual_overlap"], 1)

    def test_19c_unfollow_missing_overlap_uses_one_bounded_backstep(self) -> None:
        def row(username: str, center_y: int) -> dict:
            return {
                "username": username,
                "username_normalized": username,
                "row_center": [300, center_y],
            }

        before = [row(f"row_{idx}", 300 + idx * 120) for idx in range(8)]
        jumped = [row(f"row_{idx}", 300 + (idx - 20) * 120) for idx in range(20, 28)]
        recovered = [row("row_7", 300)] + [
            row(f"row_{idx}", 300 + (idx - 7) * 120)
            for idx in range(8, 15)
        ]
        device = _FakeScrollDevice()
        with (
            patch.object(
                unfollow,
                "harvest_visible_following_rows_for_unfollow",
                side_effect=[
                    (jumped, {"following_list_end_detected": False}),
                    (recovered, {"following_list_end_detected": False}),
                ],
            ),
            patch.object(
                unfollow,
                "detect_own_following_list_screen",
                return_value={"is_following_list": True},
            ),
            patch.object(unfollow.time, "sleep", return_value=None),
        ):
            result = unfollow._scroll_following_list_for_unfollow(
                device,
                account_username="owner",
                before_rows=before,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["depth_advanced"])
        self.assertTrue(result["corrective_backstep_used"])
        self.assertEqual(result["actual_overlap"], 1)
        self.assertEqual(len(device.swipes), 2)

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

    def test_25_adaptive_geometry_targets_seven_rows_with_one_anchor(self) -> None:
        geometry = adaptive_follow_scroll_geometry(
            1080,
            2340,
            [260, 410, 560, 710, 860, 1010, 1160, 1310, 1460],
        )
        self.assertEqual(geometry["target_new_rows"], 7)
        self.assertEqual(geometry["target_overlap_rows"], 2)
        self.assertEqual(geometry["distance_px"], 1050)
        self.assertGreater(float(geometry["duration_s"]), 0.32)

        continuity = compare_instagram_list_viewports(
            [f"row_{idx}" for idx in range(8)],
            ["row_7"] + [f"row_{idx}" for idx in range(8, 15)],
        )
        self.assertTrue(continuity.continuity_proved)
        self.assertEqual(continuity.new_row_count, 7)
        self.assertEqual(continuity.overlap_count, 1)

    def test_26_adaptive_geometry_uses_median_and_a16_safe_band(self) -> None:
        geometry = adaptive_follow_scroll_geometry(
            1080,
            2340,
            [250, 447, 646, 846, 1045, 1244, 1600, 1644],
        )
        self.assertEqual(geometry["median_row_spacing_px"], 199)
        self.assertLessEqual(int(geometry["distance_px"]), int(2340 * 0.56))
        self.assertGreaterEqual(int(geometry["y_end"]), int(2340 * 0.22))

    def test_27_insufficient_measurements_use_exact_short_fallback(self) -> None:
        geometry = adaptive_follow_scroll_geometry(720, 1600, [500, 620])
        self.assertFalse(geometry["adaptive"])
        self.assertEqual(geometry["distance_ratio"], 0.24)
        self.assertEqual(geometry["fallback_reason"], "insufficient_measured_rows")

    def test_28_xml_adapter_exposes_redacted_row_centers(self) -> None:
        out = nav.followers_list_continuation_from_hierarchy_xml(
            _surface(rows=[(f"row_{idx}", "Follow") for idx in range(8)])
        )
        self.assertEqual(out["primary_row_centers_y"], [285 + 120 * idx for idx in range(8)])

    def test_29_adaptive_scroll_targets_seven_new_rows_with_one_anchor(self) -> None:
        before = _surface(rows=[(f"row_{idx}", "Following") for idx in range(8)])
        after = _surface(
            rows=[("row_7", "Following")]
            + [(f"row_{idx}", "Follow") for idx in range(8, 15)]
        )
        nav._followers_store_detect_hierarchy_xml(before)
        device = _FakeScrollDevice()
        diag: dict = {}
        with (
            patch.object(nav, "followers_refresh_detect_hierarchy_cache", return_value=after),
            patch.object(nav, "_followers_log_scroll_or_swipe_about_to_run"),
            patch.object(nav.time, "sleep", return_value=None),
        ):
            ok = nav._followers_scroll_list_forward(
                device,
                scroll_profile="canonical_adaptive",
                bypass_post_tap_capture_gate=True,
                bypass_scroll_xml_guards=True,
                scroll_diag_out=diag,
            )
        self.assertTrue(ok)
        self.assertEqual(diag["forward_attempt_count"], 1)
        self.assertEqual(diag["overlap_count"], 1)
        self.assertEqual(diag["new_primary_row_count"], 7)
        self.assertEqual(diag["target_new_rows"], 7)
        self.assertEqual(diag["target_overlap"], 1)
        self.assertEqual(diag["fully_visible_count"], 8)
        self.assertEqual(diag["partial_row_count"], 0)
        self.assertNotEqual(diag["scroll_distance_ratio"], 0.24)

    def test_29a_stolm_shape_emits_primary_state_with_two_overlap_and_seven_new(self) -> None:
        before = _surface(rows=[(f"stolm_row_{idx}", "Following") for idx in range(9)])
        after = _surface(
            rows=[(f"stolm_row_{idx}", "Following") for idx in range(7, 9)]
            + [(f"stolm_row_{idx}", "Follow") for idx in range(9, 16)]
        )
        nav._followers_store_detect_hierarchy_xml(before)
        device = _FakeScrollDevice()
        diag: dict = {}
        with (
            patch.object(nav, "followers_refresh_detect_hierarchy_cache", return_value=after),
            patch.object(nav, "_followers_log_scroll_or_swipe_about_to_run"),
            patch.object(nav.time, "sleep", return_value=None),
        ):
            ok = nav._followers_scroll_list_forward(
                device,
                scroll_profile="canonical_adaptive",
                bypass_post_tap_capture_gate=True,
                bypass_scroll_xml_guards=True,
                scroll_diag_out=diag,
            )
        self.assertTrue(ok)
        self.assertTrue(diag["depth_advanced"])
        self.assertEqual(diag["surface_state_after"], State.PRIMARY_ROWS_AVAILABLE.value)
        self.assertEqual(diag["overlap_count"], 2)
        self.assertEqual(diag["new_primary_row_count"], 7)

    def test_30_adaptive_failure_runs_short_fallback_and_reprobes(self) -> None:
        before = _surface(rows=[(f"row_{idx}", "Following") for idx in range(8)])
        after_short = _surface(
            rows=[(f"row_{idx}", "Following") for idx in range(6, 8)]
            + [(f"row_{idx}", "Follow") for idx in range(8, 14)]
        )
        nav._followers_store_detect_hierarchy_xml(before)
        device = _FakeScrollDevice()
        diag: dict = {}
        with (
            patch.object(
                nav,
                "followers_refresh_detect_hierarchy_cache",
                side_effect=[before, after_short],
            ),
            patch.object(nav, "_followers_log_scroll_or_swipe_about_to_run"),
            patch.object(nav.time, "sleep", return_value=None),
        ):
            ok = nav._followers_scroll_list_forward(
                device,
                scroll_profile="canonical_adaptive",
                bypass_post_tap_capture_gate=True,
                bypass_scroll_xml_guards=True,
                scroll_diag_out=diag,
            )
        self.assertTrue(ok)
        self.assertEqual(diag["forward_attempt_count"], 2)
        self.assertEqual(diag["forward_attempts"][1]["kind"], "short_fallback")
        self.assertEqual(diag["forward_attempts"][1]["distance_ratio"], 0.24)

    def test_31_third_recovery_is_bounded_and_can_validate_progress(self) -> None:
        before = _surface(rows=[(f"row_{idx}", "Following") for idx in range(8)])
        progressed = _surface(
            rows=[(f"row_{idx}", "Following") for idx in range(6, 8)]
            + [(f"row_{idx}", "Follow") for idx in range(8, 14)]
        )
        nav._followers_store_detect_hierarchy_xml(before)
        device = _FakeScrollDevice()
        diag: dict = {}
        with (
            patch.object(
                nav,
                "followers_refresh_detect_hierarchy_cache",
                side_effect=[before, before, progressed],
            ),
            patch.object(nav, "_followers_log_scroll_or_swipe_about_to_run"),
            patch.object(nav.time, "sleep", return_value=None),
        ):
            ok = nav._followers_scroll_list_forward(
                device,
                scroll_profile="canonical_adaptive",
                bypass_post_tap_capture_gate=True,
                bypass_scroll_xml_guards=True,
                scroll_diag_out=diag,
            )
        self.assertTrue(ok)
        self.assertEqual(diag["forward_attempt_count"], 3)
        self.assertEqual(len(device.swipes), 3)

    def test_32_safe_partial_scroll_failure_rotates_to_next_ct(self) -> None:
        engine = _FakeFollowersEngine([
            (0, {
                "follows_completed_count": 17,
                "follows_goal_effective": 40,
                "global_follows_goal_effective": 40,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "visible_window_exhausted_scroll_failed",
                "target_rotation_safe_after_scroll_failure": True,
                "scroll_failure_surface_ambiguous": False,
            }),
            (0, {
                "follows_completed_count": 23,
                "follows_goal_effective": 40,
                "global_follows_goal_effective": 40,
                "follow_session_outcome": "global_follow_cap_reached",
                "follow_stop_reason": "global_follow_cap_reached",
            }),
        ])
        result = session._run_follow_target_rotation(
            object(), account_id="account", account_username="account", run_id="run",
            follow_targets=[_target(1), _target(2)],
            run_followers_list_engine_session=engine, supabase_mode=False,
            warm_session_used=False, force_stop_used=False,
            max_targets_per_run=4, max_follows_per_target_per_run=30,
        )
        self.assertEqual(len(engine.calls), 2)
        self.assertEqual(result["global_follows_completed"], 40)
        self.assertEqual(len(result["partial_resumable_targets"]), 1)

    def test_33_duplicate_accessibility_labels_do_not_shorten_geometry(self) -> None:
        xml = _surface(
            rows=[
                ("row_0", "Following"),
                ("same_truncated_label", "Following"),
                ("same_truncated_label", "Following"),
                ("row_3", "Follow"),
                ("row_4", "Follow"),
                ("row_5", "Follow"),
                ("row_6", "Follow"),
                ("row_7", "Follow"),
            ]
        )
        out = nav.followers_list_continuation_from_hierarchy_xml(
            xml,
            viewport_height=2400,
        )
        self.assertEqual(out["primary_row_count"], 7)
        self.assertEqual(out["fully_visible_primary_row_count"], 8)
        geometry = adaptive_follow_scroll_geometry(
            1080,
            2400,
            out["fully_visible_primary_row_centers_y"],
        )
        self.assertEqual(geometry["target_new_rows"], 7)
        self.assertEqual(geometry["target_overlap_rows"], 1)

    def test_34_partial_bottom_row_is_excluded_from_safe_geometry(self) -> None:
        xml = _surface(rows=[(f"row_{idx}", "Follow") for idx in range(9)])
        xml = xml.replace(
            'bounds="[120,1220][520,1270]"',
            'bounds="[120,2180][520,2230]"',
        ).replace(
            'bounds="[720,1220][1030,1290]"',
            'bounds="[720,2180][1030,2250]"',
        )
        out = nav.followers_list_continuation_from_hierarchy_xml(
            xml,
            viewport_height=2400,
        )
        self.assertEqual(out["primary_row_count"], 9)
        self.assertEqual(out["fully_visible_primary_row_count"], 8)
        self.assertEqual(out["partial_primary_row_count"], 1)
        geometry = adaptive_follow_scroll_geometry(
            1080,
            2400,
            out["fully_visible_primary_row_centers_y"],
            partial_row_count=out["partial_primary_row_count"],
        )
        self.assertEqual(geometry["target_new_rows"], 7)
        self.assertEqual(geometry["target_overlap_rows"], 1)
        self.assertEqual(geometry["partial_row_count"], 1)

    def test_35_overlap_above_two_increases_next_gesture_once_and_bounded(self) -> None:
        centers = [300, 450, 600, 750, 900, 1050, 1200, 1350]
        baseline = adaptive_follow_scroll_geometry(1080, 2400, centers)
        overlap_two = adaptive_follow_scroll_geometry(
            1080,
            2400,
            centers,
            previous_actual_overlap=2,
        )
        overlap_three = adaptive_follow_scroll_geometry(
            1080,
            2400,
            centers,
            previous_actual_overlap=3,
        )
        self.assertEqual(overlap_two["distance_px"], baseline["distance_px"])
        self.assertGreater(overlap_three["distance_px"], baseline["distance_px"])
        self.assertLessEqual(overlap_three["distance_px"], int(2400 * 0.56))
        self.assertEqual(
            overlap_three["adaptive_adjustment_reason"],
            "previous_overlap_above_two_increase_bounded",
        )

    def test_36_zero_overlap_remains_excessive_and_is_not_reusable(self) -> None:
        continuity = compare_instagram_list_viewports(
            [f"row_{idx}" for idx in range(8)],
            [f"row_{idx}" for idx in range(8, 16)],
        )
        self.assertTrue(continuity.excessive)
        self.assertFalse(continuity.continuity_proved)
        geometry = adaptive_follow_scroll_geometry(
            1080,
            2400,
            [300, 450, 600, 750, 900, 1050, 1200, 1350],
            previous_actual_overlap=0,
        )
        self.assertEqual(
            geometry["adaptive_adjustment_reason"],
            "previous_zero_overlap_not_reusable",
        )

    def test_37_ambiguous_scroll_failure_stops_global_rotation(self) -> None:
        engine = _FakeFollowersEngine([(0, {
            "follows_completed_count": 17,
            "follows_goal_effective": 40,
            "global_follows_goal_effective": 40,
            "follow_session_outcome": "follows_completed",
            "follow_stop_reason": "visible_window_exhausted_scroll_failed",
            "target_rotation_safe_after_scroll_failure": False,
            "scroll_failure_surface_ambiguous": True,
        })])
        result = session._run_follow_target_rotation(
            object(), account_id="account", account_username="account", run_id="run",
            follow_targets=[_target(1), _target(2)],
            run_followers_list_engine_session=engine, supabase_mode=False,
            warm_session_used=False, force_stop_used=False,
            max_targets_per_run=4, max_follows_per_target_per_run=30,
        )
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(len(result["partial_resumable_targets"]), 1)
        self.assertEqual(result["summary"]["phase_status"], "partial_not_resumable")
        self.assertEqual(
            result["summary"]["follow_stop_reason"],
            "partial_ct_rotation_revalidation_failed",
        )

    def test_38_safe_partial_rotation_stops_after_two_failed_cts(self) -> None:
        safe_failure = {
            "follows_completed_count": 1,
            "follows_goal_effective": 40,
            "global_follows_goal_effective": 40,
            "follow_session_outcome": "partial_resumable",
            "follow_stop_reason": "visible_window_exhausted_scroll_failed",
            "target_rotation_safe_after_scroll_failure": True,
            "scroll_failure_surface_ambiguous": False,
        }
        engine = _FakeFollowersEngine([(0, safe_failure), (0, safe_failure), (0, safe_failure)])
        result = session._run_follow_target_rotation(
            object(), account_id="account", account_username="account", run_id="run",
            follow_targets=[_target(1), _target(2), _target(3)],
            run_followers_list_engine_session=engine, supabase_mode=False,
            warm_session_used=False, force_stop_used=False,
            max_targets_per_run=4, max_follows_per_target_per_run=30,
        )
        self.assertEqual(len(engine.calls), 2)
        self.assertEqual(len(result["partial_resumable_targets"]), 2)
        self.assertEqual(
            result["summary"]["follow_stop_reason"],
            "safe_partial_ct_failure_limit_reached",
        )

    def test_39_processed_valid_viewport_expands_see_more_before_scroll(self) -> None:
        nav._followers_store_detect_hierarchy_xml(
            _surface(
                rows=[("row_a", "Following"), ("row_b", "Following")],
                see_more=True,
                suggestions=True,
            )
        )
        decision = nav.followers_suggestions_boundary_from_cached_hierarchy(
            previously_valid_followers_rows=True,
            processed_primary_row_ids={"row_a", "row_b"},
            continuation_probe_count=1,
        )
        self.assertEqual(decision["state"], State.EXPAND_PRIMARY_LIST_AVAILABLE.value)

        source = inspect.getsource(runner._run_followers_list_engine_session)
        exhausted_offset = source.index("_visible_window_scroll_required =")
        strategy_offset = source.index(
            "_visible_window_scroll_strategy: dict[str, Any] = {}",
            exhausted_offset,
        )
        pre_scroll_contract = source[exhausted_offset:strategy_offset]
        self.assertIn(
            "followers_suggestions_boundary_from_cached_hierarchy",
            pre_scroll_contract,
        )
        self.assertIn(
            "processed_primary_row_ids=_visible_window_processed_rows",
            pre_scroll_contract,
        )
        self.assertIn("followers_try_expand_primary_list", pre_scroll_contract)
        self.assertIn("continue", pre_scroll_contract)

    def test_44_real_grouped_count_without_selected_flag_uses_committed_surface(self) -> None:
        run_rows = [
            ("myriam_flh_", "Following"),
            ("bryant_wankak79", "Message"),
            ("le.placard.de.robyn", "Message"),
            ("guerin_henri", "Following"),
            ("mathis68224", "Following"),
        ]
        xml = _surface(
            rows=run_rows,
            see_more=True,
            suggestions=True,
            followers_tab_selected=False,
            followers_count_label="9 550 followers",
        )
        out = nav.followers_list_continuation_from_hierarchy_xml(
            xml,
            processed_primary_row_ids={username for username, _ in run_rows},
            previously_valid_followers_rows=True,
        )
        self.assertFalse(out["selected_followers_tab"])
        self.assertTrue(out["followers_tab_title_visible"])
        self.assertEqual(
            out["surface_verification_source"],
            "committed_rows_and_visible_followers_title",
        )
        self.assertEqual(out["state"], State.EXPAND_PRIMARY_LIST_AVAILABLE.value)

    def test_45_unselected_grouped_count_without_prior_rows_fails_closed(self) -> None:
        xml = _surface(
            rows=[("myriam_flh_", "Following")],
            see_more=True,
            suggestions=True,
            followers_tab_selected=False,
            followers_count_label="9 550 followers",
        )
        out = nav.followers_list_continuation_from_hierarchy_xml(
            xml,
            processed_primary_row_ids={"myriam_flh_"},
            previously_valid_followers_rows=False,
        )
        self.assertEqual(out["state"], State.AMBIGUOUS_SURFACE.value)

    def test_40_mythyl_partial_revalidates_and_rotates_without_consuming_wrong_ct(self) -> None:
        engine = _FakeFollowersEngine([
            (0, {
                "follows_completed_count": 18,
                "follows_goal_effective": 40,
                "global_follows_goal_effective": 40,
                "follow_session_outcome": "partial_resumable",
                "follow_stop_reason": "visible_window_exhausted_scroll_failed",
                "target_rotation_safe_after_scroll_failure": False,
                "scroll_failure_surface_ambiguous": True,
            }),
            (0, {
                "follows_completed_count": 22,
                "follows_goal_effective": 40,
                "global_follows_goal_effective": 40,
                "follow_session_outcome": "global_follow_cap_reached",
                "follow_stop_reason": "global_follow_cap_reached",
            }),
        ])
        rotations: list[dict] = []

        def rotate(_device, **kwargs):
            rotations.append(kwargs)
            return {"ok": True, "reason": "ok", "steps_completed": ["open_followers"]}

        result = session._run_follow_target_rotation(
            object(), account_id="account", account_username="mythyl_fitness", run_id="run",
            follow_targets=[_target(1), _target(2), _target(3)],
            run_followers_list_engine_session=engine, supabase_mode=False,
            warm_session_used=False, force_stop_used=False,
            max_targets_per_run=4, max_follows_per_target_per_run=30,
            fast_rotate_to_next_target_from_followers=rotate,
        )
        self.assertEqual(len(rotations), 1)
        self.assertEqual(rotations[0]["to_source_target"], "source-2")
        self.assertEqual(len(engine.calls), 2)
        self.assertTrue(engine.calls[1]["start_from_current_followers_list"])
        self.assertEqual(result["global_follows_completed"], 40)
        self.assertEqual(result["summary"]["phase_status"], "completed")
        self.assertEqual(result["summary"]["safe_next_step"], "end_follow_phase")

    def test_41_failed_revalidation_does_not_consume_next_ct(self) -> None:
        engine = _FakeFollowersEngine([(0, {
            "follows_completed_count": 18,
            "follows_goal_effective": 40,
            "global_follows_goal_effective": 40,
            "follow_session_outcome": "partial_resumable",
            "follow_stop_reason": "visible_window_exhausted_scroll_failed",
            "target_rotation_safe_after_scroll_failure": False,
            "scroll_failure_surface_ambiguous": True,
        })])

        result = session._run_follow_target_rotation(
            object(), account_id="account", account_username="mythyl_fitness", run_id="run",
            follow_targets=[_target(1), _target(2)],
            run_followers_list_engine_session=engine, supabase_mode=False,
            warm_session_used=False, force_stop_used=False,
            max_targets_per_run=4, max_follows_per_target_per_run=30,
            fast_rotate_to_next_target_from_followers=lambda *_args, **_kwargs: {
                "ok": False,
                "reason": "current_surface_not_compatible",
                "steps_completed": [],
            },
        )
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(result["summary"]["phase_status"], "partial_not_resumable")
        self.assertEqual(result["summary"]["safe_next_step"], "end_session")

    def test_42_partial_follow_contract_blocks_unfollow_handoff(self) -> None:
        ok, reason = session._follow_exit_handoff_gate(
            0,
            {
                "phase_status": "partial_resumable",
                "scope": "current_ct",
                "safe_next_step": "rotate_next_ct",
            },
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "follow_phase_not_globally_completed")

    def test_43_only_global_follow_completion_allows_unfollow_handoff(self) -> None:
        ok, reason = session._follow_exit_handoff_gate(
            0,
            {
                "phase_status": "completed",
                "scope": "follow_phase",
                "safe_next_step": "end_follow_phase",
            },
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "follow_completed")

    def test_46_rotation_is_forbidden_while_see_more_is_pending(self) -> None:
        engine = _FakeFollowersEngine(
            [
                (
                    0,
                    {
                        "follows_completed_count": 8,
                        "follow_session_outcome": "followers_suggestions_boundary",
                        "follow_stop_reason": "followers_suggestions_boundary",
                        "global_follows_goal_effective": 40,
                        "see_more_status": "see_more_available",
                    },
                )
            ]
        )
        result = session._run_follow_target_rotation(
            object(),
            account_id="account",
            account_username="account",
            run_id="run",
            follow_targets=[_target(1), _target(2)],
            run_followers_list_engine_session=engine,
            supabase_mode=False,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=4,
            max_follows_per_target_per_run=30,
        )
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(
            result["summary"]["follow_stop_reason"],
            "see_more_pending_rotation_forbidden",
        )

    def test_47_rotation_is_allowed_after_bounded_see_more_terminal_failure(self) -> None:
        engine = _FakeFollowersEngine(
            [
                (
                    0,
                    {
                        "follows_completed_count": 8,
                        "follows_goal_effective": 40,
                        "global_follows_goal_effective": 40,
                        "follow_session_outcome": "partial_resumable",
                        "follow_stop_reason": "see_more_click_exhausted_after_bounded_recovery",
                        "see_more_status": "see_more_failed_terminal",
                    },
                ),
                (
                    0,
                    {
                        "follows_completed_count": 32,
                        "follows_goal_effective": 40,
                        "global_follows_goal_effective": 40,
                        "follow_session_outcome": "global_follow_cap_reached",
                        "follow_stop_reason": "global_follow_cap_reached",
                    },
                ),
            ]
        )
        rotations: list[dict] = []

        def rotate(_device, **kwargs):
            rotations.append(kwargs)
            return {"ok": True, "reason": "ok", "steps_completed": ["open_followers"]}

        result = session._run_follow_target_rotation(
            object(),
            account_id="account",
            account_username="account",
            run_id="run",
            follow_targets=[_target(1), _target(2)],
            run_followers_list_engine_session=engine,
            supabase_mode=False,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=4,
            max_follows_per_target_per_run=30,
            fast_rotate_to_next_target_from_followers=rotate,
        )
        self.assertEqual(len(rotations), 1)
        self.assertEqual(len(engine.calls), 2)
        self.assertEqual(result["global_follows_completed"], 40)


if __name__ == "__main__":
    unittest.main()
