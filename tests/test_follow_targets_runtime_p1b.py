from __future__ import annotations

import os
import time
import unittest
from unittest.mock import Mock, patch

import account_session_orchestrator as session
import instagram_navigation as nav
import runner


class FakeFollowersEngine:
    def __init__(self, responses: list[tuple[int, dict]]) -> None:
        self.responses = responses
        self.calls: list[dict] = []
        self.last_session_summary: dict = {}

    def __call__(self, _device, **kwargs):
        self.calls.append(dict(kwargs))
        idx = len(self.calls) - 1
        exit_code, summary = self.responses[idx]
        self.last_session_summary = dict(summary)
        return exit_code


class FakeDevice:
    def __init__(self) -> None:
        self.presses: list[str] = []

    def press(self, key: str) -> None:
        self.presses.append(key)


def _strong_followers_open_meta(to_target: str, *, action_bar_title: str | None = None) -> dict:
    title = action_bar_title if action_bar_title is not None else to_target
    snap = {
        "is_followers_list": True,
        "own_unified_followers_list_detected": True,
        "action_bar_title": title,
        "open_detection_method": "own_unified_follow_list",
        "candidate_username_count": 8,
        "follow_list_username_count": 8,
        "visible_header_texts": ["8 followers"],
        "signals": [
            "own_unified_followers_list_detected",
            "selected_followers_tab",
            "follow_list_username",
            "follow_list_container",
            "list_chrome_recycler_or_listview",
        ],
    }
    return {
        "source_profile_username": to_target,
        "profile_verified": True,
        "open_detection_method": "own_unified_follow_list",
        "signals": ["own_unified_followers_list_detected"],
        "after_tap_screen_snapshot": dict(snap),
        "last_poll_snapshot": dict(snap),
    }


def _strong_followers_rotation_proof(to_target: str, *, accepted_age_ms: float = 0.0) -> dict:
    meta = _strong_followers_open_meta(to_target)
    proof_details = {
        "source_profile_username": to_target,
        "action_bar_title": to_target,
        "profile_verified": True,
        "open_detection_method": "own_unified_follow_list",
    }
    proof = runner._fast_rotation_followers_list_proof_payload(
        meta,
        proof_details=proof_details,
        to_target=to_target,
    )
    proof["proof_accepted_monotonic"] = time.perf_counter() - (float(accepted_age_ms) / 1000.0)
    return proof


def _recent_search_probe(to_target: str = "source_b") -> dict:
    return {
        "is_global_search": True,
        "is_recent_search_surface": True,
        "is_local_followers_search": False,
        "is_lightweight_search": True,
        "surface_type": "recent_search",
        "surface_reason": "ok",
        "has_search_bar": True,
        "has_recent_label": True,
        "has_recent_targets": True,
        "has_explore_grid": False,
        "next_target_visible_in_recent": to_target == "source_b",
        "is_global_search_empty": False,
        "duration_ms": 1.0,
    }


def _previous_search_results_probe(from_target: str = "source_a") -> dict:
    return {
        "is_global_search": True,
        "is_previous_search_results_surface": True,
        "is_recent_search_surface": False,
        "is_local_followers_search": False,
        "is_lightweight_search": True,
        "surface_type": "previous_target_search_results",
        "surface_reason": "previous_target_search_results",
        "has_search_bar": True,
        "search_query_text": from_target,
        "previous_query_visible": True,
        "previous_target_visible": True,
        "previous_target_result_visible": True,
        "has_account_results": True,
        "account_result_count": 1,
        "has_recent_label": False,
        "has_recent_targets": False,
        "has_explore_grid": False,
        "next_target_visible_in_recent": False,
        "is_global_search_empty": False,
        "duration_ms": 1.0,
    }


def _explore_grid_probe() -> dict:
    return {
        "is_global_search": True,
        "is_recent_search_surface": False,
        "is_local_followers_search": False,
        "is_lightweight_search": True,
        "surface_type": "explore_grid_search",
        "surface_reason": "explore_grid_search_surface",
        "has_search_bar": True,
        "has_recent_label": False,
        "has_recent_targets": False,
        "has_explore_grid": True,
        "next_target_visible_in_recent": False,
        "is_global_search_empty": True,
        "duration_ms": 1.0,
    }


def _global_search_empty_probe() -> dict:
    return {
        "is_global_search": True,
        "is_recent_search_surface": False,
        "is_local_followers_search": False,
        "is_lightweight_search": True,
        "surface_type": "global_search_empty",
        "surface_reason": "global_search_recent_missing",
        "has_search_bar": True,
        "has_recent_label": False,
        "has_recent_targets": False,
        "has_explore_grid": False,
        "next_target_visible_in_recent": False,
        "is_global_search_empty": True,
        "duration_ms": 1.0,
    }


class FakeSearchElement:
    def __init__(self, text: str, rid: str) -> None:
        self._text = text
        self.info = {
            "bounds": {"left": 1, "top": 2, "right": 3, "bottom": 4},
            "resourceName": rid,
            "resourceId": rid,
        }

    def get_text(self) -> str:
        return self._text


class FakeSearchSelector:
    def __init__(self, elements: list[FakeSearchElement] | None = None) -> None:
        self._elements = list(elements or [])

    def all(self) -> list[FakeSearchElement]:
        return list(self._elements)

    def wait(self, timeout: float = 0.0) -> bool:
        return bool(self._elements)


class FakeSearchDevice:
    def __init__(
        self,
        package: str,
        responses: dict[
            tuple[str, str],
            list[FakeSearchElement] | list[list[FakeSearchElement]],
        ],
    ) -> None:
        self.package = package
        self.responses = responses
        self.calls: list[tuple[str, str]] = []
        self.call_counts: dict[tuple[str, str], int] = {}

    def app_current(self) -> dict:
        return {"package": self.package}

    def __call__(self, **kwargs):
        if "resourceId" in kwargs:
            key = ("resourceId", str(kwargs["resourceId"]))
        elif "resourceIdMatches" in kwargs:
            key = ("resourceIdMatches", str(kwargs["resourceIdMatches"]))
        else:
            key = ("other", str(kwargs))
        self.calls.append(key)
        count = self.call_counts.get(key, 0)
        self.call_counts[key] = count + 1
        response = self.responses.get(key, [])
        if response and isinstance(response[0], list):
            sequence = response  # type: ignore[assignment]
            elements = sequence[count] if count < len(sequence) else sequence[-1]
        else:
            elements = response
        return FakeSearchSelector(elements)  # type: ignore[arg-type]


class FakeFollowersEntryDevice:
    def __init__(self, hierarchy_xml: str) -> None:
        self.hierarchy_xml = hierarchy_xml
        self.clicks: list[tuple[int, int]] = []

    def window_size(self) -> tuple[int, int]:
        return 1080, 2340

    def dump_hierarchy(self, compressed: bool = False) -> str:
        return self.hierarchy_xml

    def click(self, x: int, y: int) -> None:
        self.clicks.append((int(x), int(y)))


class FakeFollowersPostTapDevice:
    def __init__(
        self,
        hierarchy_xml: str,
        *,
        package: str = "com.instagram.androie",
        activity: str = "com.instagram.mainactivity.InstagramMainActivity",
    ) -> None:
        self.hierarchy_xml = hierarchy_xml
        self.package = package
        self.activity = activity
        self.dump_calls = 0

    def app_current(self) -> dict:
        return {"package": self.package, "activity": self.activity}

    def dump_hierarchy(self, compressed: bool = False) -> str:
        self.dump_calls += 1
        return self.hierarchy_xml


def followers_entry_profile_xml(
    *,
    source: str = "cafecuba_geneve",
    metric: str = "followers",
    bounds: str = "[535,336][794,496]",
    duplicate_followers: bool = False,
) -> str:
    metric_rid = {
        "followers": "profile_header_followers_stacked_familiar",
        "following": "profile_header_following_stacked_familiar",
        "posts": "profile_header_post_count_front_familiar",
    }[metric]
    metric_desc = {
        "followers": "5 443 followers",
        "following": "6 682 following",
        "posts": "1 060 posts",
    }[metric]
    extra = (
        '<node class="android.view.ViewGroup" resource-id="com.instagram.androie:id/profile_header_followers_stacked_familiar" '
        'content-desc="12 followers" bounds="[535,520][794,620]" clickable="true" />'
        if duplicate_followers
        else ""
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<hierarchy>
  <node class="android.widget.TextView" resource-id="com.instagram.androie:id/action_bar_title" text="{source}" bounds="[0,80][1080,180]" />
  <node class="android.view.ViewGroup" resource-id="com.instagram.androie:id/{metric_rid}" content-desc="{metric_desc}" bounds="{bounds}" clickable="true">
    <node class="android.widget.TextView" resource-id="com.instagram.androie:id/profile_header_familiar_followers_value" text="5 443" bounds="[552,359][667,420]" />
    <node class="android.widget.TextView" resource-id="com.instagram.androie:id/profile_header_familiar_followers_label" text="followers" bounds="[552,420][711,473]" />
  </node>
  {extra}
</hierarchy>"""


def followers_list_xml(
    *,
    source: str = "cafecuba_geneve",
    selected_followers_tab: bool = True,
    include_container: bool = True,
    include_recycler: bool = True,
    candidate_count: int = 3,
) -> str:
    selected = ' selected="true"' if selected_followers_tab else ""
    container = (
        '<node class="android.view.ViewGroup" resource-id="com.instagram.androie:id/follow_list_container" bounds="[0,250][1080,2200]">'
        if include_container
        else '<node class="android.view.ViewGroup" bounds="[0,250][1080,2200]">'
    )
    recycler = (
        '<node class="androidx.recyclerview.widget.RecyclerView" bounds="[0,360][1080,2200]">'
        if include_recycler
        else '<node class="android.view.ViewGroup" bounds="[0,360][1080,2200]">'
    )
    rows = "\n".join(
        f'<node class="android.widget.TextView" resource-id="com.instagram.androie:id/follow_list_username" text="candidate_{i}" bounds="[80,{430 + i * 120}][500,{480 + i * 120}]" />'
        for i in range(candidate_count)
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<hierarchy>
  <node class="android.widget.TextView" resource-id="com.instagram.androie:id/action_bar_title" text="{source}" bounds="[0,80][1080,180]" />
  <node class="android.widget.HorizontalScrollView" resource-id="com.instagram.androie:id/unified_follow_list_tab_layout" bounds="[0,180][1080,300]">
    <node class="android.widget.TextView" resource-id="com.instagram.androie:id/title" text="10K followers"{selected} bounds="[0,180][540,300]" />
  </node>
  {container}
    {recycler}
      {rows}
    </node>
  </node>
</hierarchy>"""


def post_tap_xml_fast_diag() -> dict:
    return {
        "entry_engine_v2": True,
        "semantic_followers_metric": True,
        "post_tap_xml_fast_deadline_ms": 1.0,
        "post_tap_xml_fast_interval_ms": 1.0,
    }


def strong_followers_snapshot_meta(
    *,
    source_profile_username: str = "reveaustral",
    candidate_username_count: int = 8,
    open_detection_method: str = "own_unified_follow_list",
    signals: list[str] | None = None,
) -> dict:
    snap = {
        "is_followers_list": True,
        "open_detection_method": open_detection_method,
        "candidate_username_count": candidate_username_count,
        "visible_header_texts": ["688 followers"],
        "visible_usernames_sample": ["emmanuel_wildnature"],
        "signals": signals
        if signals is not None
        else [
            "own_unified_followers_list_detected",
            "selected_followers_tab",
            "follow_list_username",
            "follow_list_container",
            "list_chrome_recycler_or_listview",
            "own_unified:hierarchy_xml",
        ],
    }
    return {
        "source_profile_username": source_profile_username,
        "open_detection_method": open_detection_method,
        "last_poll_snapshot": dict(snap),
        "after_tap_screen_snapshot": dict(snap),
    }


def target(target_id: str, source: str, index: int) -> dict:
    return {
        "target_id": target_id,
        "source_profile": source,
        "target_index": index,
        "selection_source": "ig_targets",
    }


class FollowTargetsRuntimeP1bTest(unittest.TestCase):
    def setUp(self) -> None:
        nav.followers_session_reset_list_committed_open()
        self.recorded_metrics: list[tuple[str, dict]] = []
        self._record_metric_patcher = patch.object(
            session,
            "_record_follow_target_metric",
            side_effect=lambda event, **kw: self.recorded_metrics.append((event, kw)),
        )
        self._record_metric_patcher.start()

    def tearDown(self) -> None:
        self._record_metric_patcher.stop()
        nav.followers_session_reset_list_committed_open()

    def test_follow_ct_clone_exact_rid_short_circuits_when_exact_match_found(self) -> None:
        rid = "com.instagram.androie:id/row_search_user_username"
        d = FakeSearchDevice(
            "com.instagram.androie",
            {("resourceId", rid): [FakeSearchElement("relive.group", rid)]},
        )
        trace = {
            "follow_ct": True,
            "username": "relive.group",
            "expected_package": "com.instagram.androie",
            "poll_index": 1,
            "fast_accept": True,
            "trace_state": {},
        }
        logs: list[tuple[str, dict]] = []

        with patch.object(nav.config, "INSTAGRAM_PACKAGE", "com.instagram.androie"), patch.object(
            nav, "log", side_effect=lambda _level, event, **kw: logs.append((event, kw))
        ):
            out = nav._collect_raw_row_search_elements(d, trace_context=trace)

        self.assertEqual(len(out), 1)
        self.assertEqual(d.calls, [("resourceId", rid)])
        self.assertIn("ct_row_detect_selector_short_circuited", [event for event, _ in logs])

    def test_follow_ct_clone_exact_rid_non_exact_keeps_fallbacks(self) -> None:
        clone_rid = "com.instagram.androie:id/row_search_user_username"
        android_rid = "com.instagram.android:id/row_search_user_username"
        d = FakeSearchDevice(
            "com.instagram.androie",
            {("resourceId", clone_rid): [FakeSearchElement("someone_else", clone_rid)]},
        )
        trace = {
            "follow_ct": True,
            "username": "relive.group",
            "expected_package": "com.instagram.androie",
            "poll_index": 1,
            "fast_accept": True,
            "trace_state": {},
        }

        with patch.object(nav.config, "INSTAGRAM_PACKAGE", "com.instagram.androie"):
            out = nav._collect_raw_row_search_elements(d, trace_context=trace)

        self.assertEqual(len(out), 1)
        self.assertIn(("resourceId", clone_rid), d.calls)
        self.assertIn(("resourceId", android_rid), d.calls)
        self.assertIn(("resourceIdMatches", r".*/id/row_search_user_username"), d.calls)

    def test_follow_ct_clone_exact_rid_absent_keeps_fallbacks(self) -> None:
        clone_rid = "com.instagram.androie:id/row_search_user_username"
        android_rid = "com.instagram.android:id/row_search_user_username"
        d = FakeSearchDevice("com.instagram.androie", {})
        trace = {
            "follow_ct": True,
            "username": "relive.group",
            "expected_package": "com.instagram.androie",
            "poll_index": 1,
            "fast_accept": True,
            "trace_state": {},
        }

        with patch.object(nav.config, "INSTAGRAM_PACKAGE", "com.instagram.androie"):
            out = nav._collect_raw_row_search_elements(d, trace_context=trace)

        self.assertEqual(out, [])
        self.assertIn(("resourceId", clone_rid), d.calls)
        self.assertIn(("resourceId", android_rid), d.calls)
        self.assertIn(("resourceIdMatches", r".*/id/row_search_user_username"), d.calls)

    def test_follow_ct_package_resource_id_clone_exact_short_circuits(self) -> None:
        clone_rid = "com.instagram.androie:id/row_search_user_username"
        android_rid = "com.instagram.android:id/row_search_user_username"
        other_clone_rid = "com.instagram.androii:id/row_search_user_username"
        d = FakeSearchDevice(
            "com.instagram.androie",
            {
                ("resourceId", clone_rid): [
                    [],
                    [FakeSearchElement("relive.group", clone_rid)],
                ],
            },
        )
        trace = {
            "follow_ct": True,
            "username": "relive.group",
            "expected_package": "com.instagram.androie",
            "poll_index": 1,
            "fast_accept": True,
            "trace_state": {},
        }
        logs: list[tuple[str, dict]] = []

        with patch.object(nav.config, "INSTAGRAM_PACKAGE", "com.instagram.androie"), patch.object(
            nav, "log", side_effect=lambda _level, event, **kw: logs.append((event, kw))
        ):
            out = nav._collect_raw_row_search_elements(d, trace_context=trace)

        self.assertEqual(len(out), 1)
        self.assertEqual(d.calls.count(("resourceId", clone_rid)), 2)
        self.assertIn(("resourceId", android_rid), d.calls)
        self.assertNotIn(("resourceId", other_clone_rid), d.calls)
        short_circuit_logs = [
            kw for event, kw in logs if event == "ct_row_detect_selector_short_circuited"
        ]
        self.assertEqual(len(short_circuit_logs), 1)
        self.assertEqual(
            short_circuit_logs[0].get("selector_source"),
            "package_resource_id_clone",
        )

    def test_follow_ct_package_resource_id_clone_non_exact_keeps_following_selectors(self) -> None:
        clone_rid = "com.instagram.androie:id/row_search_user_username"
        other_clone_rid = "com.instagram.androii:id/row_search_user_username"
        d = FakeSearchDevice(
            "com.instagram.androie",
            {
                ("resourceId", clone_rid): [
                    [],
                    [FakeSearchElement("someone_else", clone_rid)],
                ],
            },
        )
        trace = {
            "follow_ct": True,
            "username": "relive.group",
            "expected_package": "com.instagram.androie",
            "poll_index": 1,
            "fast_accept": True,
            "trace_state": {},
        }

        with patch.object(nav.config, "INSTAGRAM_PACKAGE", "com.instagram.androie"):
            out = nav._collect_raw_row_search_elements(d, trace_context=trace)

        self.assertEqual(len(out), 1)
        self.assertIn(("resourceId", other_clone_rid), d.calls)

    def test_follow_ct_standard_package_exact_rid_still_short_circuits(self) -> None:
        rid = "com.instagram.android:id/row_search_user_username"
        d = FakeSearchDevice(
            "com.instagram.android",
            {("resourceId", rid): [FakeSearchElement("relive.group", rid)]},
        )
        trace = {
            "follow_ct": True,
            "username": "relive.group",
            "expected_package": "com.instagram.android",
            "poll_index": 1,
            "fast_accept": True,
            "trace_state": {},
        }

        with patch.object(nav.config, "INSTAGRAM_PACKAGE", "com.instagram.android"):
            out = nav._collect_raw_row_search_elements(d, trace_context=trace)

        self.assertEqual(len(out), 1)
        self.assertEqual(d.calls, [("resourceId", rid)])

    def test_followers_entry_fast_path_exact_clone_xml_taps_followers(self) -> None:
        d = FakeFollowersEntryDevice(followers_entry_profile_xml())
        det = {
            "is_followers_list": True,
            "open_detection_method": "own_unified_follow_list",
            "signals": ["own_unified_followers_list_detected"],
            "visible_usernames_sample": [],
        }
        logs: list[tuple[str, dict]] = []

        with patch.object(nav.config, "ENABLE_FOLLOWERS_ENTRY_ENGINE_V2", True), patch.object(
            nav, "verify_app_foreground", return_value=True
        ), patch.object(
            nav, "_followers_current_pkg_activity", return_value={"current_package": "com.instagram.androie"}
        ), patch.object(
            nav, "_guess_profile_screen", return_value="likely_profile"
        ), patch.object(
            nav, "_followers_debug_capture", side_effect=AssertionError("debug capture skipped")
        ), patch.object(
            nav, "_followers_entry_v2_post_tap_confirm", return_value=(True, det, det, 1)
        ) as post_confirm, patch.object(
            nav, "log", side_effect=lambda _level, event, **kw: logs.append((event, kw))
        ):
            ok, meta = nav.open_followers_list_from_profile(
                d,
                "cafecuba_geneve",
                "com.instagram.androie",
                profile_verified=True,
            )

        self.assertTrue(ok)
        self.assertEqual(d.clicks, [(664, 416)])
        self.assertEqual(meta["open_method"], "followers_entry_fast_path")
        post_confirm.assert_called_once()
        self.assertEqual(post_confirm.call_args.kwargs["post_tap_settle_s"], 2.0)
        self.assertIn("followers_entry_fast_path_metric_found", [event for event, _ in logs])
        self.assertIn("followers_entry_fast_path_tap_sent", [event for event, _ in logs])

    def test_followers_entry_fast_path_rejects_following_metric_and_falls_back(self) -> None:
        d = FakeFollowersEntryDevice(followers_entry_profile_xml(metric="following"))
        logs: list[tuple[str, dict]] = []

        with patch.object(nav.config, "ENABLE_FOLLOWERS_ENTRY_ENGINE_V2", True), patch.object(
            nav, "verify_app_foreground", return_value=True
        ), patch.object(
            nav, "_followers_current_pkg_activity", return_value={"current_package": "com.instagram.androie"}
        ), patch.object(
            nav, "_guess_profile_screen", return_value="likely_profile"
        ), patch.object(
            nav.time, "sleep", return_value=None
        ), patch.object(
            nav, "_followers_debug_capture", return_value={}
        ), patch.object(
            nav, "_open_followers_list_from_profile_v2", return_value=(True, {"open_method": "fallback_v2"})
        ) as fallback_v2, patch.object(
            nav, "log", side_effect=lambda _level, event, **kw: logs.append((event, kw))
        ):
            ok, meta = nav.open_followers_list_from_profile(
                d,
                "cafecuba_geneve",
                "com.instagram.androie",
                profile_verified=True,
            )

        self.assertTrue(ok)
        self.assertEqual(meta["open_method"], "fallback_v2")
        self.assertEqual(d.clicks, [])
        fallback_v2.assert_called_once()
        skipped = [kw for event, kw in logs if event == "followers_entry_fast_path_skipped"]
        self.assertEqual(skipped[-1]["reason"], "exact_followers_metric_absent")

    def test_followers_entry_fast_path_absent_or_ambiguous_metric_falls_back(self) -> None:
        for xml in (
            followers_entry_profile_xml(metric="posts"),
            followers_entry_profile_xml(duplicate_followers=True),
        ):
            d = FakeFollowersEntryDevice(xml)
            with patch.object(nav.config, "ENABLE_FOLLOWERS_ENTRY_ENGINE_V2", True), patch.object(
                nav, "verify_app_foreground", return_value=True
            ), patch.object(
                nav, "_followers_current_pkg_activity", return_value={"current_package": "com.instagram.androie"}
            ), patch.object(
                nav, "_guess_profile_screen", return_value="likely_profile"
            ), patch.object(
                nav.time, "sleep", return_value=None
            ), patch.object(
                nav, "_followers_debug_capture", return_value={}
            ), patch.object(
                nav, "_open_followers_list_from_profile_v2", return_value=(True, {"open_method": "fallback_v2"})
            ) as fallback_v2:
                ok, meta = nav.open_followers_list_from_profile(
                    d,
                    "cafecuba_geneve",
                    "com.instagram.androie",
                    profile_verified=True,
                )

            self.assertTrue(ok)
            self.assertEqual(meta["open_method"], "fallback_v2")
            self.assertEqual(d.clicks, [])
            fallback_v2.assert_called_once()

    def test_followers_entry_fast_path_unsafe_bounds_falls_back_without_tap(self) -> None:
        d = FakeFollowersEntryDevice(
            followers_entry_profile_xml(bounds="[900,336][1040,496]")
        )

        with patch.object(nav.config, "ENABLE_FOLLOWERS_ENTRY_ENGINE_V2", True), patch.object(
            nav, "verify_app_foreground", return_value=True
        ), patch.object(
            nav, "_followers_current_pkg_activity", return_value={"current_package": "com.instagram.androie"}
        ), patch.object(
            nav, "_guess_profile_screen", return_value="likely_profile"
        ), patch.object(
            nav.time, "sleep", return_value=None
        ), patch.object(
            nav, "_followers_debug_capture", return_value={}
        ), patch.object(
            nav, "_open_followers_list_from_profile_v2", return_value=(True, {"open_method": "fallback_v2"})
        ) as fallback_v2:
            ok, meta = nav.open_followers_list_from_profile(
                d,
                "cafecuba_geneve",
                "com.instagram.androie",
                profile_verified=True,
            )

        self.assertTrue(ok)
        self.assertEqual(meta["open_method"], "fallback_v2")
        self.assertEqual(d.clicks, [])
        fallback_v2.assert_called_once()

    def test_followers_entry_fast_path_requires_post_tap_validation_success(self) -> None:
        d = FakeFollowersEntryDevice(followers_entry_profile_xml())
        det = {"is_followers_list": False, "open_detection_method": ""}

        with patch.object(nav.config, "ENABLE_FOLLOWERS_ENTRY_ENGINE_V2", True), patch.object(
            nav, "verify_app_foreground", return_value=True
        ), patch.object(
            nav, "_followers_current_pkg_activity", return_value={"current_package": "com.instagram.androie"}
        ), patch.object(
            nav, "_guess_profile_screen", return_value="likely_profile"
        ), patch.object(
            nav, "_followers_entry_v2_post_tap_confirm", return_value=(False, det, det, 1)
        ) as post_confirm:
            ok, meta = nav.open_followers_list_from_profile(
                d,
                "cafecuba_geneve",
                "com.instagram.androie",
                profile_verified=True,
            )

        self.assertFalse(ok)
        self.assertEqual(d.clicks, [(664, 416)])
        self.assertEqual(meta["failure_reason"], "entry_fast_path_transition_not_confirmed")
        post_confirm.assert_called_once()
        self.assertEqual(post_confirm.call_args.kwargs["post_tap_settle_s"], 2.0)

    def test_followers_entry_post_tap_xml_fast_confirms_without_vision(self) -> None:
        d = FakeFollowersPostTapDevice(followers_list_xml())
        tap_diag = post_tap_xml_fast_diag()
        logs: list[tuple[str, dict]] = []

        with patch.object(nav.config, "INSTAGRAM_PACKAGE", "com.instagram.androie"), patch.object(
            nav, "_followers_after_tap_immediate_capture_and_detect", side_effect=AssertionError("fallback skipped")
        ), patch.object(
            nav, "_vision_validation_followers_list_open_gate", side_effect=AssertionError("vision skipped")
        ), patch.object(
            nav, "log", side_effect=lambda _level, event, **kw: logs.append((event, kw))
        ):
            ok, after_det, last_det, attempts = nav._followers_entry_v2_post_tap_confirm(
                d,
                tap_diag,
                "cafecuba_geneve",
            )

        self.assertTrue(ok)
        self.assertEqual(attempts, 1)
        self.assertEqual(d.dump_calls, 1)
        self.assertEqual(last_det["open_detection_method"], "own_unified_follow_list")
        self.assertEqual(last_det["candidate_username_count"], 3)
        self.assertEqual(after_det, last_det)
        self.assertTrue(tap_diag["followers_entry_post_tap_xml_fast_confirmed"])
        self.assertIn(
            "followers_entry_post_tap_xml_fast_confirmed",
            [event for event, _ in logs],
        )

    def test_followers_entry_post_tap_xml_fast_zero_candidates_falls_back(self) -> None:
        d = FakeFollowersPostTapDevice(followers_list_xml(candidate_count=0))
        tap_diag = post_tap_xml_fast_diag()
        fallback_det = {"is_followers_list": True, "open_detection_method": "fallback_visual"}

        with patch.object(nav.config, "INSTAGRAM_PACKAGE", "com.instagram.androie"), patch.object(
            nav, "_followers_after_tap_immediate_capture_and_detect",
            return_value=({"screenshot_path": "shot.png", "xml_path": "x.xml"}, fallback_det),
        ) as fallback, patch.object(
            nav, "_followers_entry_v2_enrich_transition_visual_detail",
            return_value=fallback_det,
        ), patch.object(
            nav, "_followers_entry_v2_list_open_quality_ok", return_value=(True, "ok")
        ), patch.object(
            nav, "_vision_validation_followers_list_open_gate", return_value=(True, "ok")
        ), patch.object(nav.time, "sleep", return_value=None):
            ok, _, last_det, _ = nav._followers_entry_v2_post_tap_confirm(
                d,
                tap_diag,
                "cafecuba_geneve",
            )

        self.assertTrue(ok)
        self.assertEqual(last_det["open_detection_method"], "fallback_visual")
        fallback.assert_called_once()

    def test_followers_entry_post_tap_xml_fast_wrong_source_falls_back(self) -> None:
        d = FakeFollowersPostTapDevice(followers_list_xml(source="other_source"))
        tap_diag = post_tap_xml_fast_diag()
        fallback_det = {"is_followers_list": True, "open_detection_method": "fallback_visual"}

        with patch.object(nav.config, "INSTAGRAM_PACKAGE", "com.instagram.androie"), patch.object(
            nav, "_followers_after_tap_immediate_capture_and_detect",
            return_value=({"screenshot_path": "shot.png", "xml_path": "x.xml"}, fallback_det),
        ) as fallback, patch.object(
            nav, "_followers_entry_v2_enrich_transition_visual_detail",
            return_value=fallback_det,
        ), patch.object(
            nav, "_followers_entry_v2_list_open_quality_ok", return_value=(True, "ok")
        ), patch.object(
            nav, "_vision_validation_followers_list_open_gate", return_value=(True, "ok")
        ), patch.object(nav.time, "sleep", return_value=None):
            ok, _, last_det, _ = nav._followers_entry_v2_post_tap_confirm(
                d,
                tap_diag,
                "cafecuba_geneve",
            )

        self.assertTrue(ok)
        self.assertEqual(last_det["open_detection_method"], "fallback_visual")
        fallback.assert_called_once()

    def test_followers_entry_post_tap_xml_fast_missing_selected_tab_falls_back(self) -> None:
        d = FakeFollowersPostTapDevice(followers_list_xml(selected_followers_tab=False))
        tap_diag = post_tap_xml_fast_diag()
        fallback_det = {"is_followers_list": True, "open_detection_method": "fallback_visual"}

        with patch.object(nav.config, "INSTAGRAM_PACKAGE", "com.instagram.androie"), patch.object(
            nav, "_followers_after_tap_immediate_capture_and_detect",
            return_value=({"screenshot_path": "shot.png", "xml_path": "x.xml"}, fallback_det),
        ) as fallback, patch.object(
            nav, "_followers_entry_v2_enrich_transition_visual_detail",
            return_value=fallback_det,
        ), patch.object(
            nav, "_followers_entry_v2_list_open_quality_ok", return_value=(True, "ok")
        ), patch.object(
            nav, "_vision_validation_followers_list_open_gate", return_value=(True, "ok")
        ), patch.object(nav.time, "sleep", return_value=None):
            ok, _, last_det, _ = nav._followers_entry_v2_post_tap_confirm(
                d,
                tap_diag,
                "cafecuba_geneve",
            )

        self.assertTrue(ok)
        self.assertEqual(last_det["open_detection_method"], "fallback_visual")
        fallback.assert_called_once()

    def test_followers_entry_post_tap_xml_fast_missing_container_or_chrome_falls_back(self) -> None:
        for xml in (
            followers_list_xml(include_container=False),
            followers_list_xml(include_recycler=False),
        ):
            d = FakeFollowersPostTapDevice(xml)
            tap_diag = post_tap_xml_fast_diag()
            fallback_det = {"is_followers_list": True, "open_detection_method": "fallback_visual"}

            with patch.object(nav.config, "INSTAGRAM_PACKAGE", "com.instagram.androie"), patch.object(
                nav, "_followers_after_tap_immediate_capture_and_detect",
                return_value=({"screenshot_path": "shot.png", "xml_path": "x.xml"}, fallback_det),
            ) as fallback, patch.object(
                nav, "_followers_entry_v2_enrich_transition_visual_detail",
                return_value=fallback_det,
            ), patch.object(
                nav, "_followers_entry_v2_list_open_quality_ok", return_value=(True, "ok")
            ), patch.object(
                nav, "_vision_validation_followers_list_open_gate", return_value=(True, "ok")
            ), patch.object(nav.time, "sleep", return_value=None):
                ok, _, last_det, _ = nav._followers_entry_v2_post_tap_confirm(
                    d,
                    tap_diag,
                    "cafecuba_geneve",
                )

            self.assertTrue(ok)
            self.assertEqual(last_det["open_detection_method"], "fallback_visual")
            fallback.assert_called_once()

    def test_followers_entry_post_tap_xml_fast_foreground_mismatch_falls_back(self) -> None:
        d = FakeFollowersPostTapDevice(followers_list_xml(), package="com.other.app")
        tap_diag = post_tap_xml_fast_diag()
        fallback_det = {"is_followers_list": True, "open_detection_method": "fallback_visual"}

        with patch.object(nav.config, "INSTAGRAM_PACKAGE", "com.instagram.androie"), patch.object(
            nav, "_followers_after_tap_immediate_capture_and_detect",
            return_value=({"screenshot_path": "shot.png", "xml_path": "x.xml"}, fallback_det),
        ) as fallback, patch.object(
            nav, "_followers_entry_v2_enrich_transition_visual_detail",
            return_value=fallback_det,
        ), patch.object(
            nav, "_followers_entry_v2_list_open_quality_ok", return_value=(True, "ok")
        ), patch.object(
            nav, "_vision_validation_followers_list_open_gate", return_value=(True, "ok")
        ), patch.object(nav.time, "sleep", return_value=None):
            ok, _, last_det, _ = nav._followers_entry_v2_post_tap_confirm(
                d,
                tap_diag,
                "cafecuba_geneve",
            )

        self.assertTrue(ok)
        self.assertEqual(last_det["open_detection_method"], "fallback_visual")
        fallback.assert_called_once()

    def test_followers_entry_post_tap_xml_fast_snapshot_supports_candidate_reuse(self) -> None:
        d = FakeFollowersPostTapDevice(followers_list_xml(source="reveaustral"))
        tap_diag = post_tap_xml_fast_diag()

        with patch.object(nav.config, "INSTAGRAM_PACKAGE", "com.instagram.androie"):
            ok, after_det, last_det, _ = nav._followers_entry_v2_post_tap_confirm(
                d,
                tap_diag,
                "reveaustral",
            )
        open_list_meta = {
            "source_profile_username": "reveaustral",
            "open_detection_method": last_det.get("open_detection_method"),
            "last_poll_snapshot": last_det,
            "after_tap_screen_snapshot": after_det,
        }
        reuse_det, reason = runner._candidate_selection_snapshot_reuse_candidate(
            open_list_meta,
            source_profile_username="reveaustral",
            snapshot_age_ms=100.0,
        )

        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertIsNotNone(reuse_det)
        self.assertEqual(reuse_det["open_detection_method"], "own_unified_follow_list")

    def test_candidate_selection_snapshot_reuse_accepts_strong_snapshot(self) -> None:
        det, reason = runner._candidate_selection_snapshot_reuse_candidate(
            strong_followers_snapshot_meta(),
            source_profile_username="reveaustral",
            snapshot_age_ms=1500.0,
        )

        self.assertEqual(reason, "")
        self.assertIsNotNone(det)
        self.assertTrue(det["is_followers_list"])
        self.assertEqual(det["open_detection_method"], "own_unified_follow_list")
        self.assertEqual(det["candidate_username_count"], 8)

    def test_candidate_selection_snapshot_reuse_falls_back_when_absent_or_stale(self) -> None:
        det_absent, reason_absent = runner._candidate_selection_snapshot_reuse_candidate(
            {},
            source_profile_username="reveaustral",
            snapshot_age_ms=100.0,
        )
        det_stale, reason_stale = runner._candidate_selection_snapshot_reuse_candidate(
            strong_followers_snapshot_meta(),
            source_profile_username="reveaustral",
            snapshot_age_ms=9000.0,
        )

        self.assertIsNone(det_absent)
        self.assertEqual(reason_absent, "snapshot_absent")
        self.assertIsNone(det_stale)
        self.assertEqual(reason_stale, "snapshot_stale")

    def test_candidate_selection_snapshot_reuse_falls_back_on_wrong_source(self) -> None:
        det, reason = runner._candidate_selection_snapshot_reuse_candidate(
            strong_followers_snapshot_meta(source_profile_username="other_source"),
            source_profile_username="reveaustral",
            snapshot_age_ms=100.0,
        )

        self.assertIsNone(det)
        self.assertEqual(reason, "source_profile_mismatch")

    def test_candidate_selection_snapshot_reuse_falls_back_on_zero_candidates(self) -> None:
        det, reason = runner._candidate_selection_snapshot_reuse_candidate(
            strong_followers_snapshot_meta(candidate_username_count=0),
            source_profile_username="reveaustral",
            snapshot_age_ms=100.0,
        )

        self.assertIsNone(det)
        self.assertEqual(reason, "candidate_username_count_zero")

    def test_candidate_selection_snapshot_reuse_requires_list_signals(self) -> None:
        det, reason = runner._candidate_selection_snapshot_reuse_candidate(
            strong_followers_snapshot_meta(signals=["selected_followers_tab"]),
            source_profile_username="reveaustral",
            snapshot_age_ms=100.0,
        )

        self.assertIsNone(det)
        self.assertEqual(reason, "missing_list_signals")

    def test_candidate_selection_snapshot_reuse_does_not_change_candidate_filters(self) -> None:
        following_row = {"row_cta_xml_class": "following", "row_cta_xml_text": "Following"}
        follow_row = {"row_cta_xml_class": "follow", "row_cta_xml_text": "Follow"}

        det, reason = runner._candidate_selection_snapshot_reuse_candidate(
            strong_followers_snapshot_meta(),
            source_profile_username="reveaustral",
            snapshot_age_ms=100.0,
        )

        self.assertIsNotNone(det)
        self.assertEqual(reason, "")
        self.assertEqual(following_row["row_cta_xml_class"], "following")
        self.assertEqual(follow_row["row_cta_xml_class"], "follow")

    def test_exhaustion_classifier_accepts_sparse_and_bounded_exhaustion(self) -> None:
        self.assertTrue(session.is_follow_target_exhaustion_outcome(exit_code=66))
        self.assertTrue(session.is_follow_target_exhaustion_outcome(
            outcome="no_followable_candidates_bounded_exploration",
            summary={"follows_completed_count": 0},
        ))
        self.assertTrue(session.is_follow_target_exhaustion_outcome(
            reason="no_candidates_after_sparse_scrolls",
            summary={"follows_completed_count": 0},
        ))

    def test_exhaustion_classifier_rejects_non_target_errors(self) -> None:
        for reason in [
            "login_required",
            "checkpoint_required",
            "device_unavailable",
            "wrong_surface_abort",
            "credential_review_required",
            "rate_limit",
        ]:
            self.assertFalse(session.is_follow_target_exhaustion_outcome(
                exit_code=96,
                reason=reason,
                summary={"follows_completed_count": 0},
            ))

    def test_rotation_switches_after_exhaustion_and_keeps_target_attribution(self) -> None:
        engine = FakeFollowersEngine([
            (66, {
                "follows_completed_count": 0,
                "follow_session_outcome": "no_followable_candidates_bounded_exploration",
                "follow_stop_reason": "no_candidates_after_sparse_scrolls",
            }),
            (0, {
                "follows_completed_count": 1,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        logs: list[tuple[str, str, dict]] = []
        with patch.object(session, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            result = session._run_follow_target_rotation(
                object(),
                account_id="acct",
                account_username="account",
                run_id="run",
                follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
                run_followers_list_engine_session=engine,
                supabase_mode=True,
                warm_session_used=False,
                force_stop_used=False,
                max_targets_per_run=3,
            )

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(len(engine.calls), 2)
        self.assertEqual(engine.calls[0]["target_id"], "t1")
        self.assertEqual(engine.calls[0]["source_profile_username"], "source_one")
        self.assertEqual(engine.calls[1]["target_id"], "t2")
        self.assertEqual(engine.calls[1]["source_profile_username"], "source_two")
        self.assertEqual(result["summary"]["target_id"], "t2")
        self.assertIn("follow_target_switched", [event for _level, event, _kw in logs])

    def test_rotation_all_exhausted_returns_stable_stop_reason(self) -> None:
        engine = FakeFollowersEngine([
            (66, {
                "follows_completed_count": 0,
                "follow_session_outcome": "no_followable_candidates_bounded_exploration",
                "follow_stop_reason": "no_candidates_after_sparse_scrolls",
            }),
            (0, {
                "follows_completed_count": 0,
                "follow_session_outcome": "no_followable_candidates_bounded_exploration",
                "follow_stop_reason": "list_progressive_exploration_exhausted",
            }),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
        )

        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["all_targets_exhausted"])
        self.assertEqual(result["summary"]["follow_stop_reason"], "all_targets_exhausted")
        self.assertEqual(result["summary"]["follow_session_outcome"], "no_followable_candidates_all_targets")
        self.assertEqual(len(engine.calls), 2)

    def test_rotation_respects_max_targets_per_run(self) -> None:
        engine = FakeFollowersEngine([
            (66, {"follows_completed_count": 0, "follow_session_outcome": "no_followable_candidates_bounded_exploration"}),
            (66, {"follows_completed_count": 0, "follow_session_outcome": "no_followable_candidates_bounded_exploration"}),
            (0, {"follows_completed_count": 1, "follow_session_outcome": "follows_completed"}),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[
                target("t1", "source_one", 0),
                target("t2", "source_two", 1),
                target("t3", "source_three", 2),
            ],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=2,
        )

        self.assertEqual(len(engine.calls), 2)
        self.assertEqual([call["target_id"] for call in engine.calls], ["t1", "t2"])
        self.assertFalse(result["all_targets_exhausted"])
        self.assertEqual(result["summary"]["follow_stop_reason"], "max_targets_per_run_reached")

    def test_budget_reached_switches_without_exhaustion(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 2,
                "global_follows_goal_effective": 5,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 5,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        logs: list[tuple[str, str, dict]] = []
        with patch.object(session, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            result = session._run_follow_target_rotation(
                object(),
                account_id="acct",
                account_username="account",
                run_id="run",
                follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
                run_followers_list_engine_session=engine,
                supabase_mode=True,
                warm_session_used=False,
                force_stop_used=False,
                max_targets_per_run=3,
                max_follows_per_target_per_run=2,
            )

        events = [event for _level, event, _kw in logs]
        self.assertIn("follow_target_budget_reached", events)
        self.assertIn("follow_target_switched", events)
        self.assertEqual([call["target_id"] for call in engine.calls], ["t1", "t2"])
        self.assertEqual([call["target_follow_budget"] for call in engine.calls], [2, 2])
        self.assertEqual(result["summary"]["target_id"], "t2")

    def test_global_follow_cap_after_success_stops_before_next_target(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 2,
                "global_follows_goal_effective": 2,
                "follow_session_outcome": "global_follow_cap_reached",
                "follow_stop_reason": "global_follow_cap_reached",
                "candidates_not_scanned_due_to_cap": True,
            }),
        ])
        logs: list[tuple[str, str, dict]] = []
        with patch.object(session, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            result = session._run_follow_target_rotation(
                object(),
                account_id="acct",
                account_username="account",
                run_id="run",
                follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
                run_followers_list_engine_session=engine,
                supabase_mode=True,
                warm_session_used=False,
                force_stop_used=False,
                max_targets_per_run=3,
                max_follows_per_target_per_run=2,
            )

        events = [event for _level, event, _kw in logs]
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(engine.calls[0]["target_id"], "t1")
        self.assertEqual(result["reason"], "global_follow_cap_reached")
        self.assertEqual(result["summary"]["follow_stop_reason"], "global_follow_cap_reached")
        self.assertEqual(result["summary"]["follows_completed_count"], 2)
        self.assertTrue(result["summary"]["candidates_not_scanned_due_to_cap"])
        self.assertEqual(result["exhausted_targets"], [])
        self.assertIn("target_rotation_stopped_global_cap", events)
        self.assertIn("run_follow_phase_completed_due_to_cap", events)
        self.assertNotIn("follow_target_switched", events)
        self.assertNotIn("follow_target_exhausted", events)

    def test_global_follow_cap_not_recorded_as_target_exhausted_metric(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 0,
                "global_follows_completed": 2,
                "follows_done": 2,
                "cap": 2,
                "global_follows_goal_effective": 2,
                "follow_session_outcome": "global_follow_cap_reached",
                "follow_stop_reason": "global_follow_cap_reached",
                "candidates_not_scanned_due_to_cap": True,
            }),
        ])
        with patch.object(session, "_record_follow_target_metric") as record_metric:
            result = session._run_follow_target_rotation(
                object(),
                account_id="acct",
                account_username="account",
                run_id="run",
                follow_targets=[target("t1", "mythyllus", 0), target("t2", "healthup.sw", 1)],
                run_followers_list_engine_session=engine,
                supabase_mode=True,
                warm_session_used=False,
                force_stop_used=False,
                max_targets_per_run=3,
                max_follows_per_target_per_run=1,
            )

        metric_names = [call.args[0] for call in record_metric.call_args_list]
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(result["reason"], "global_follow_cap_reached")
        self.assertEqual(result["summary"]["current_target"], "mythyllus")
        self.assertEqual(result["summary"]["follows_completed_count"], 2)
        self.assertEqual(result["summary"]["global_follows_completed"], 2)
        self.assertEqual(result["exhausted_targets"], [])
        self.assertNotIn("target_exhausted", metric_names)
        self.assertNotIn("runtime_error_non_exhaustion", metric_names)

    def test_target_budget_guard_blocks_second_candidate_for_budget_one(self) -> None:
        self.assertFalse(runner.should_stop_for_target_follow_budget(0, 1))
        self.assertTrue(runner.should_stop_for_target_follow_budget(1, 1))
        self.assertTrue(runner.should_stop_for_target_follow_budget(2, 1))
        self.assertFalse(runner.should_stop_for_target_follow_budget(3, None))

    def test_global_follow_cap_guard(self) -> None:
        self.assertFalse(runner.should_stop_for_global_follow_cap(0, 2))
        self.assertFalse(runner.should_stop_for_global_follow_cap(1, 2))
        self.assertTrue(runner.should_stop_for_global_follow_cap(2, 2))
        self.assertTrue(runner.should_stop_for_global_follow_cap(3, 2))
        self.assertFalse(runner.should_stop_for_global_follow_cap(3, None))

    def test_fast_rotation_happy_path_opens_next_followers(self) -> None:
        d = FakeDevice()
        global_probe = {
            "is_global_search": True,
            "is_local_followers_search": False,
            "is_lightweight_search": True,
            "surface_type": "global_search",
            "surface_reason": "ok",
            "duration_ms": 1.0,
        }
        with patch.object(runner, "followers_surface_quick_revalidate", side_effect=[
            (True, {"open_detection_method": "xml"}),
            (True, {"open_detection_method": "xml"}),
        ]) as reval, patch.object(
            runner, "verify_profile", side_effect=[True, True]
        ) as verify_profile, patch.object(
            runner, "_fast_rotation_probe_search_surface", return_value=global_probe
        ), patch.object(
            runner, "type_search", return_value=True
        ) as type_search, patch.object(
            runner, "open_accounts_tab", return_value=False
        ), patch.object(
            runner, "tap_account_result", return_value=True
        ) as tap_account, patch.object(
            runner, "open_followers_list_from_profile", return_value=(True, {"open_detection_method": "xml"})
        ) as open_followers:
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "ok")
        self.assertEqual(d.presses, ["back", "back"])
        self.assertIn("back_search", result["steps_completed"])
        self.assertIn("open_followers", result["steps_completed"])
        self.assertEqual(reval.call_count, 2)
        verify_profile.assert_any_call(d, "source_a")
        verify_profile.assert_any_call(d, "source_b")
        type_search.assert_called_once()
        tap_account.assert_called_once()
        open_followers.assert_called_once()

    def test_fast_rotation_accepts_recent_search_surface_with_ct_visible(self) -> None:
        d = FakeDevice()
        logs: list[tuple[str, str, dict]] = []
        with patch.object(runner, "followers_surface_quick_revalidate", side_effect=[
            (True, {"open_detection_method": "xml"}),
            (True, {"open_detection_method": "xml"}),
        ]), patch.object(
            runner, "verify_profile", side_effect=[True, True]
        ), patch.object(
            runner, "_fast_rotation_probe_search_surface", return_value=_recent_search_probe()
        ), patch.object(
            runner, "type_search", return_value=True
        ) as type_search, patch.object(
            runner, "open_accounts_tab", return_value=False
        ), patch.object(
            runner, "tap_account_result", return_value=True
        ), patch.object(
            runner,
            "open_followers_list_from_profile",
            return_value=(True, {"open_detection_method": "xml"}),
        ), patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(d.presses, ["back", "back"])
        type_search.assert_called_once()
        self.assertIn(
            "follow_target_fast_rotation_search_recent_surface_detected",
            [event for _level, event, _kw in logs],
        )

    def test_fast_rotation_accepts_previous_search_results_surface(self) -> None:
        d = FakeDevice()
        logs: list[tuple[str, str, dict]] = []
        with patch.object(runner, "followers_surface_quick_revalidate", return_value=(True, {})), patch.object(
            runner, "verify_profile", return_value=True
        ), patch.object(
            runner, "_fast_rotation_probe_search_surface", return_value=_previous_search_results_probe()
        ), patch.object(
            runner, "type_search", return_value=True
        ) as type_search, patch.object(
            runner, "open_accounts_tab", return_value=False
        ), patch.object(
            runner, "tap_account_result", return_value=True
        ), patch.object(
            runner,
            "open_followers_list_from_profile",
            return_value=(True, _strong_followers_open_meta("source_b")),
        ), patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(d.presses, ["back", "back"])
        type_search.assert_called_once()
        events = [event for _level, event, _kw in logs]
        self.assertIn("follow_target_fast_rotation_previous_search_results_detected", events)
        self.assertIn("follow_target_fast_rotation_previous_search_results_used", events)
        self.assertNotIn("follow_target_fast_rotation_fallback_standard", events)

    def test_fast_rotation_accepts_previous_query_with_account_results(self) -> None:
        d = FakeDevice()
        probe = _previous_search_results_probe("source_a")
        probe.update(
            {
                "previous_target_visible": False,
                "previous_target_result_visible": False,
                "previous_query_visible": True,
                "has_account_results": True,
            }
        )
        logs: list[tuple[str, str, dict]] = []
        with patch.object(runner, "followers_surface_quick_revalidate", return_value=(True, {})), patch.object(
            runner, "verify_profile", return_value=True
        ), patch.object(
            runner, "_fast_rotation_probe_search_surface", return_value=probe
        ), patch.object(
            runner, "type_search", return_value=True
        ) as type_search, patch.object(
            runner, "open_accounts_tab", return_value=False
        ), patch.object(
            runner, "tap_account_result", return_value=True
        ), patch.object(
            runner,
            "open_followers_list_from_profile",
            return_value=(True, _strong_followers_open_meta("source_b")),
        ), patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertTrue(result["ok"])
        type_search.assert_called_once()
        self.assertIn(
            "follow_target_fast_rotation_previous_search_results_used",
            [event for _level, event, _kw in logs],
        )

    def test_fast_rotation_rejects_explore_grid_search_empty(self) -> None:
        d = FakeDevice()
        logs: list[tuple[str, str, dict]] = []
        with patch.object(runner, "followers_surface_quick_revalidate", return_value=(True, {})), patch.object(
            runner, "verify_profile", return_value=True
        ), patch.object(
            runner, "_fast_rotation_probe_search_surface", return_value=_explore_grid_probe()
        ), patch.object(
            runner, "_fast_rotation_recover_global_search_surface", return_value=(False, "recent_search_recovery_failed")
        ), patch.object(
            runner, "type_search", return_value=True
        ) as type_search, patch.object(
            runner, "tap_account_result", return_value=True
        ), patch.object(
            runner,
            "open_followers_list_from_profile",
            return_value=(True, _strong_followers_open_meta("source_b")),
        ), patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "wrong_search_surface_rejected")
        type_search.assert_not_called()
        events = [event for _level, event, _kw in logs]
        self.assertIn("follow_target_fast_rotation_explore_grid_surface_detected", events)
        self.assertIn("follow_target_fast_rotation_wrong_search_surface_rejected", events)

    def test_fast_rotation_explore_grid_after_back_back_uses_extra_back_to_recent(self) -> None:
        d = FakeDevice()
        logs: list[tuple[str, str, dict]] = []
        with patch.object(runner, "followers_surface_quick_revalidate", side_effect=[
            (True, {"open_detection_method": "xml"}),
            (True, {"open_detection_method": "xml"}),
        ]), patch.object(
            runner, "verify_profile", side_effect=[True, True]
        ), patch.object(
            runner,
            "_fast_rotation_probe_search_surface",
            side_effect=[_explore_grid_probe(), _recent_search_probe()],
        ), patch.object(
            runner, "type_search", return_value=True
        ) as type_search, patch.object(
            runner, "open_accounts_tab", return_value=False
        ), patch.object(
            runner, "tap_account_result", return_value=True
        ), patch.object(
            runner,
            "open_followers_list_from_profile",
            return_value=(True, {"open_detection_method": "xml"}),
        ), patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(d.presses, ["back", "back", "back"])
        type_search.assert_called_once()
        events = [event for _level, event, _kw in logs]
        self.assertIn("follow_target_fast_rotation_explore_grid_surface_detected", events)
        self.assertIn("follow_target_fast_rotation_back_to_recent_search_success", events)

    def test_fast_rotation_global_search_empty_after_back_back_uses_controlled_switcher(
        self,
    ) -> None:
        d = FakeDevice()
        logs: list[tuple[str, str, dict]] = []
        with patch.object(runner, "followers_surface_quick_revalidate", side_effect=[
            (True, {"open_detection_method": "xml"}),
            (True, {"open_detection_method": "xml"}),
        ]), patch.object(
            runner, "verify_profile", side_effect=[True, True]
        ), patch.object(
            runner,
            "_fast_rotation_probe_search_surface",
            return_value=_global_search_empty_probe(),
        ), patch.object(
            runner, "type_search", return_value=True
        ) as type_search, patch.object(
            runner, "open_accounts_tab", return_value=False
        ), patch.object(
            runner, "tap_account_result", return_value=True
        ), patch.object(
            runner,
            "open_followers_list_from_profile",
            return_value=(True, {"open_detection_method": "xml"}),
        ), patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(d.presses, ["back", "back"])
        type_search.assert_called_once()
        events = [event for _level, event, _kw in logs]
        self.assertIn("follow_target_switcher_controlled_empty_search_ready", events)
        self.assertIn("follow_target_switcher_type_next_target_started", events)
        self.assertIn("follow_target_switcher_exact_target_row_found", events)
        self.assertIn("follow_target_switcher_followers_open_success", events)
        self.assertNotIn("follow_target_fast_rotation_third_back_started", events)
        self.assertNotIn("follow_target_fast_rotation_fallback_standard", events)

    def test_fast_rotation_controlled_empty_search_does_not_fallback_standard(
        self,
    ) -> None:
        d = FakeDevice()
        logs: list[tuple[str, str, dict]] = []
        with patch.object(runner, "followers_surface_quick_revalidate", return_value=(True, {"open_detection_method": "xml"})), patch.object(
            runner, "verify_profile", return_value=True
        ), patch.object(
            runner,
            "_fast_rotation_probe_search_surface",
            return_value=_global_search_empty_probe(),
        ), patch.object(
            runner, "type_search", return_value=True
        ) as type_search, patch.object(
            runner, "tap_account_result", return_value=True
        ), patch.object(
            runner,
            "open_followers_list_from_profile",
            return_value=(True, _strong_followers_open_meta("source_b")),
        ), patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "ok")
        type_search.assert_called_once()
        events = [event for _level, event, _kw in logs]
        self.assertIn("follow_target_fast_rotation_controlled_empty_search_used", events)
        self.assertIn("follow_target_switcher_controlled_empty_search_ready", events)
        self.assertNotIn("follow_target_fast_rotation_fallback_standard", events)

    def test_fast_rotation_controlled_empty_requires_exact_next_target_row(self) -> None:
        d = FakeDevice()
        logs: list[tuple[str, str, dict]] = []
        with patch.object(runner, "followers_surface_quick_revalidate", return_value=(True, {"open_detection_method": "xml"})), patch.object(
            runner, "verify_profile", return_value=True
        ), patch.object(
            runner,
            "_fast_rotation_probe_search_surface",
            return_value=_global_search_empty_probe(),
        ), patch.object(
            runner, "type_search", return_value=True
        ) as type_search, patch.object(
            runner, "tap_account_result", return_value=False
        ) as tap_account, patch.object(
            runner,
            "open_followers_list_from_profile",
            return_value=(True, _strong_followers_open_meta("source_b")),
        ) as open_followers, patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "open_next_target_failed")
        type_search.assert_called_once()
        tap_account.assert_called_once()
        open_followers.assert_not_called()
        events = [event for _level, event, _kw in logs]
        self.assertIn("follow_target_switcher_controlled_empty_search_ready", events)
        self.assertIn("follow_target_switcher_type_next_target_started", events)
        self.assertNotIn("follow_target_switcher_exact_target_row_found", events)

    def test_fast_rotation_local_search_recovers_to_global_without_standard_fallback(
        self,
    ) -> None:
        d = FakeDevice()
        local_probe = {
            "is_global_search": False,
            "is_local_followers_search": True,
            "is_lightweight_search": True,
            "surface_type": "local_followers_search",
            "surface_reason": "followers_list_local_search_surface",
            "duration_ms": 1.0,
        }
        global_probe = {
            "is_global_search": True,
            "is_local_followers_search": False,
            "is_lightweight_search": True,
            "surface_type": "global_search",
            "surface_reason": "ok",
            "duration_ms": 1.0,
        }
        with patch.object(
            runner, "followers_surface_quick_revalidate", side_effect=[(True, {}), (True, {})]
        ), patch.object(runner, "verify_profile", side_effect=[True, True]), patch.object(
            runner,
            "_fast_rotation_probe_search_surface",
            side_effect=[local_probe, global_probe],
        ), patch.object(
            runner,
            "_fast_rotation_recover_global_search_surface",
            return_value=(True, "ensure_global_search_surface"),
        ) as recover, patch.object(
            runner, "type_search", return_value=True
        ) as type_search, patch.object(
            runner, "open_accounts_tab", return_value=False
        ), patch.object(
            runner, "tap_account_result", return_value=True
        ), patch.object(
            runner,
            "open_followers_list_from_profile",
            return_value=(True, {"open_detection_method": "xml"}),
        ):
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertTrue(result["ok"])
        recover.assert_called_once()
        type_search.assert_called_once()

    def test_fast_rotation_search_validation_fail_does_not_type_next_target(self) -> None:
        d = FakeDevice()
        local_probe = {
            "is_global_search": False,
            "is_local_followers_search": True,
            "is_lightweight_search": True,
            "surface_type": "local_followers_search",
            "surface_reason": "followers_list_local_search_surface",
            "duration_ms": 1.0,
        }
        with patch.object(runner, "followers_surface_quick_revalidate", return_value=(True, {})), patch.object(
            runner, "verify_profile", return_value=True
        ), patch.object(
            runner, "_fast_rotation_probe_search_surface", return_value=local_probe
        ), patch.object(
            runner,
            "_fast_rotation_recover_global_search_surface",
            return_value=(False, "global_search_recovery_failed"),
        ), patch.object(
            runner, "type_search", return_value=True
        ) as type_search, patch.object(
            runner, "tap_account_result", return_value=True
        ) as tap_account:
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "back_to_search_not_validated")
        type_search.assert_not_called()
        tap_account.assert_not_called()

    def test_fast_rotation_wrong_result_profile_validation_fails_closed(self) -> None:
        d = FakeDevice()
        global_probe = {
            "is_global_search": True,
            "is_local_followers_search": False,
            "is_lightweight_search": True,
            "surface_type": "global_search",
            "surface_reason": "ok",
            "duration_ms": 1.0,
        }
        with patch.object(runner, "followers_surface_quick_revalidate", return_value=(True, {})), patch.object(
            runner, "verify_profile", side_effect=[True, False]
        ), patch.object(
            runner, "_fast_rotation_probe_search_surface", return_value=global_probe
        ), patch.object(
            runner, "type_search", return_value=True
        ), patch.object(
            runner, "open_accounts_tab", return_value=False
        ), patch.object(
            runner, "tap_account_result", return_value=True
        ), patch.object(
            runner, "open_followers_list_from_profile", return_value=(True, {})
        ) as open_followers:
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "next_profile_not_validated")
        open_followers.assert_not_called()

    def test_fast_rotation_followers_open_fail_returns_stable_reason(self) -> None:
        d = FakeDevice()
        global_probe = {
            "is_global_search": True,
            "is_local_followers_search": False,
            "is_lightweight_search": True,
            "surface_type": "global_search",
            "surface_reason": "ok",
            "duration_ms": 1.0,
        }
        with patch.object(runner, "followers_surface_quick_revalidate", return_value=(True, {})), patch.object(
            runner, "verify_profile", side_effect=[True, True]
        ), patch.object(
            runner, "_fast_rotation_probe_search_surface", return_value=global_probe
        ), patch.object(
            runner, "type_search", return_value=True
        ), patch.object(
            runner, "open_accounts_tab", return_value=False
        ), patch.object(
            runner, "tap_account_result", return_value=True
        ), patch.object(
            runner,
            "open_followers_list_from_profile",
            return_value=(False, {"failure_reason": "followers_surface_not_validated"}),
        ):
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "followers_surface_not_validated")

    def test_fast_rotation_followers_list_strong_open_proof_accepts_own_unified(self) -> None:
        ok, reason, details = runner._fast_rotation_followers_list_strong_open_proof(
            _strong_followers_open_meta("relive.group"),
            to_target="relive.group",
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")
        self.assertEqual(details["open_detection_method"], "own_unified_follow_list")
        self.assertTrue(details["profile_verified"])

    def test_fast_rotation_followers_list_strong_open_proof_rejects_mismatch(self) -> None:
        ok, reason, _details = runner._fast_rotation_followers_list_strong_open_proof(
            _strong_followers_open_meta("relive.group", action_bar_title="other_ct"),
            to_target="relive.group",
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "action_bar_title_mismatch")

    def test_fast_rotation_strong_proof_skips_post_open_revalidate(self) -> None:
        d = FakeDevice()
        global_probe = {
            "is_global_search": True,
            "is_local_followers_search": False,
            "is_lightweight_search": True,
            "surface_type": "global_search",
            "surface_reason": "ok",
            "duration_ms": 1.0,
        }
        logs: list[tuple[str, str, dict]] = []
        with patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))), patch.object(
            runner, "followers_surface_quick_revalidate", return_value=(True, {})
        ) as reval, patch.object(
            runner, "verify_profile", side_effect=[True, True]
        ), patch.object(
            runner, "_fast_rotation_probe_search_surface", return_value=global_probe
        ), patch.object(
            runner, "type_search", return_value=True
        ), patch.object(
            runner, "open_accounts_tab", return_value=False
        ), patch.object(
            runner, "tap_account_result", return_value=True
        ), patch.object(
            runner,
            "open_followers_list_from_profile",
            return_value=(True, _strong_followers_open_meta("source_b")),
        ):
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(reval.call_count, 1)
        events = [event for _level, event, _kw in logs]
        self.assertIn("follow_target_fast_rotation_followers_list_proof_accepted", events)
        self.assertNotIn("follow_target_fast_rotation_followers_list_proof_rejected", events)

    def test_fast_rotation_strong_proof_succeeds_when_post_open_revalidate_would_fail(
        self,
    ) -> None:
        d = FakeDevice()
        global_probe = {
            "is_global_search": True,
            "is_local_followers_search": False,
            "is_lightweight_search": True,
            "surface_type": "global_search",
            "surface_reason": "ok",
            "duration_ms": 1.0,
        }
        with patch.object(
            runner,
            "followers_surface_quick_revalidate",
            side_effect=[
                (True, {}),
                (False, {"reason": "time_budget_exceeded"}),
            ],
        ) as reval, patch.object(
            runner, "verify_profile", side_effect=[True, True]
        ), patch.object(
            runner, "_fast_rotation_probe_search_surface", return_value=global_probe
        ), patch.object(
            runner, "type_search", return_value=True
        ), patch.object(
            runner, "open_accounts_tab", return_value=False
        ), patch.object(
            runner, "tap_account_result", return_value=True
        ), patch.object(
            runner,
            "open_followers_list_from_profile",
            return_value=(True, _strong_followers_open_meta("source_b")),
        ):
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(reval.call_count, 1)

    def test_fast_rotation_weak_proof_uses_revalidate_and_can_fail(self) -> None:
        d = FakeDevice()
        global_probe = {
            "is_global_search": True,
            "is_local_followers_search": False,
            "is_lightweight_search": True,
            "surface_type": "global_search",
            "surface_reason": "ok",
            "duration_ms": 1.0,
        }
        logs: list[tuple[str, str, dict]] = []
        with patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))), patch.object(
            runner,
            "followers_surface_quick_revalidate",
            side_effect=[(True, {}), (False, {"reason": "time_budget_exceeded"})],
        ) as reval, patch.object(
            runner, "verify_profile", side_effect=[True, True]
        ), patch.object(
            runner, "_fast_rotation_probe_search_surface", return_value=global_probe
        ), patch.object(
            runner, "type_search", return_value=True
        ), patch.object(
            runner, "open_accounts_tab", return_value=False
        ), patch.object(
            runner, "tap_account_result", return_value=True
        ), patch.object(
            runner,
            "open_followers_list_from_profile",
            return_value=(True, {"open_detection_method": "xml", "profile_verified": True, "source_profile_username": "source_b"}),
        ):
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "time_budget_exceeded")
        self.assertEqual(reval.call_count, 2)
        events = [event for _level, event, _kw in logs]
        self.assertIn("follow_target_fast_rotation_followers_list_proof_rejected", events)
        self.assertNotIn("follow_target_fast_rotation_followers_list_proof_accepted", events)

    def test_scan_start_surface_proof_reuses_fresh_strong_followers_list(self) -> None:
        proof = _strong_followers_rotation_proof("source_b")
        # The prior physical run exposed current_screen_guess=likely_profile despite
        # strong own-unified followers signals; strong proof must win this handoff.
        proof["last_poll_snapshot"]["current_screen_guess"] = "likely_profile"
        ok, reason, proof_meta, age_ms = runner._follow_target_scan_start_surface_proof(
            {"fast_target_rotation": {"followers_list_proof": proof}},
            source_profile_username="source_b",
            max_age_ms=15000.0,
        )

        self.assertTrue(ok)
        self.assertEqual(reason, "ok")
        self.assertGreaterEqual(age_ms, 0.0)
        self.assertEqual(proof_meta["open_detection_method"], "own_unified_follow_list")

    def test_scan_start_surface_proof_rejects_target_mismatch(self) -> None:
        proof = _strong_followers_rotation_proof("source_b")
        ok, reason, _proof_meta, _age_ms = runner._follow_target_scan_start_surface_proof(
            {"fast_target_rotation": {"followers_list_proof": proof}},
            source_profile_username="source_c",
            max_age_ms=15000.0,
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "source_profile_username_mismatch")

    def test_scan_start_surface_proof_rejects_stale_proof(self) -> None:
        proof = _strong_followers_rotation_proof("source_b", accepted_age_ms=10.0)
        ok, reason, _proof_meta, age_ms = runner._follow_target_scan_start_surface_proof(
            {"fast_target_rotation": {"followers_list_proof": proof}},
            source_profile_username="source_b",
            max_age_ms=1.0,
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "proof_stale")
        self.assertGreater(age_ms, 1.0)

    def test_scan_start_surface_proof_rejects_absent_proof(self) -> None:
        ok, reason, _proof_meta, age_ms = runner._follow_target_scan_start_surface_proof(
            {"fast_target_rotation": {"ok": True}},
            source_profile_username="source_b",
            max_age_ms=15000.0,
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "followers_list_proof_missing")
        self.assertEqual(age_ms, -1.0)

    def test_scan_start_surface_proof_supports_candidate_snapshot_reuse(self) -> None:
        proof = _strong_followers_rotation_proof("source_b")
        reuse_det, reuse_reason = runner._candidate_selection_snapshot_reuse_candidate(
            proof,
            source_profile_username="source_b",
            snapshot_age_ms=500.0,
        )

        self.assertIsNotNone(reuse_det)
        self.assertEqual(reuse_reason, "")
        self.assertEqual(reuse_det["open_detection_method"], "own_unified_follow_list")

    def test_fast_rotation_proof_transmitted_to_next_target_scan(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 3,
                "follow_session_outcome": "target_budget_reached",
                "follow_stop_reason": "target_budget_reached",
            }),
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 3,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        fast_rotate = Mock(return_value={
            "ok": True,
            "reason": "ok",
            "from_source_target": "source_one",
            "to_source_target": "source_two",
            "steps_completed": ["back_search", "open_next", "open_followers"],
            "elapsed_ms": 31,
            "followers_list_proof": _strong_followers_rotation_proof("source_two"),
        })

        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
            run_followers_list_engine_session=engine,
            fast_rotate_to_next_target_from_followers=fast_rotate,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=2,
            max_follows_per_target_per_run=1,
        )

        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(engine.calls[1]["start_from_current_followers_list"])
        proof_meta = engine.calls[1]["prevalidated_followers_list_meta"]
        self.assertTrue(proof_meta["fast_target_rotation_prevalidated"])
        self.assertIn("followers_list_proof", proof_meta["fast_target_rotation"])
        self.assertEqual(
            proof_meta["fast_target_rotation"]["followers_list_proof"]["source_profile_username"],
            "source_two",
        )

    def test_budget_one_rotates_across_three_targets(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 3,
                "follow_session_outcome": "target_budget_reached",
                "follow_stop_reason": "target_budget_reached",
            }),
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 3,
                "follow_session_outcome": "target_budget_reached",
                "follow_stop_reason": "target_budget_reached",
            }),
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 3,
                "follow_session_outcome": "target_budget_reached",
                "follow_stop_reason": "target_budget_reached",
            }),
        ])
        logs: list[tuple[str, str, dict]] = []
        with patch.object(session, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            result = session._run_follow_target_rotation(
                object(),
                account_id="acct",
                account_username="account",
                run_id="run",
                follow_targets=[
                    target("t1", "source_one", 0),
                    target("t2", "source_two", 1),
                    target("t3", "source_three", 2),
                ],
                run_followers_list_engine_session=engine,
                supabase_mode=True,
                warm_session_used=False,
                force_stop_used=False,
                max_targets_per_run=3,
                max_follows_per_target_per_run=1,
            )

        self.assertEqual([call["target_id"] for call in engine.calls], ["t1", "t2", "t3"])
        self.assertEqual([call["target_follow_budget"] for call in engine.calls], [1, 1, 1])
        self.assertEqual([item["follows_completed_count"] for item in result["attempts"]], [1, 1, 1])
        self.assertEqual(result["global_follows_completed"], 3)
        events = [event for _level, event, _kw in logs]
        self.assertGreaterEqual(events.count("follow_target_budget_reached"), 3)
        self.assertIn("follow_target_rotation_requested", events)
        self.assertIn("follow_target_rotation_completed", events)

    def test_fast_rotation_prevalidates_next_target_followers_list(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 3,
                "follow_session_outcome": "target_budget_reached",
                "follow_stop_reason": "target_budget_reached",
            }),
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 3,
                "follow_session_outcome": "target_budget_reached",
                "follow_stop_reason": "target_budget_reached",
            }),
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 3,
                "follow_session_outcome": "target_budget_reached",
                "follow_stop_reason": "target_budget_reached",
            }),
        ])
        fast_rotate = Mock(return_value={
            "ok": True,
            "reason": "ok",
            "from_source_target": "source_one",
            "to_source_target": "source_two",
            "steps_completed": ["back_search", "open_followers"],
            "elapsed_ms": 12,
        })
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[
                target("t1", "source_one", 0),
                target("t2", "source_two", 1),
                target("t3", "source_three", 2),
            ],
            run_followers_list_engine_session=engine,
            fast_rotate_to_next_target_from_followers=fast_rotate,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
            max_follows_per_target_per_run=1,
        )

        self.assertEqual([call["target_id"] for call in engine.calls], ["t1", "t2", "t3"])
        self.assertFalse(engine.calls[0].get("start_from_current_followers_list", False))
        self.assertTrue(engine.calls[1]["start_from_current_followers_list"])
        self.assertTrue(engine.calls[2]["start_from_current_followers_list"])
        self.assertEqual(fast_rotate.call_count, 2)
        self.assertEqual(result["global_follows_completed"], 3)

    def test_fast_rotation_failure_falls_back_to_standard_next_target_not_old_target(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 3,
                "follow_session_outcome": "target_budget_reached",
                "follow_stop_reason": "target_budget_reached",
            }),
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 3,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        fast_rotate = Mock(return_value={
            "ok": False,
            "reason": "back_to_search_not_validated",
            "from_source_target": "source_one",
            "to_source_target": "source_two",
            "steps_completed": ["back_profile"],
            "elapsed_ms": 9,
        })
        logs: list[tuple[str, str, dict]] = []
        with patch.object(session, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            result = session._run_follow_target_rotation(
                object(),
                account_id="acct",
                account_username="account",
                run_id="run",
                follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
                run_followers_list_engine_session=engine,
                fast_rotate_to_next_target_from_followers=fast_rotate,
                supabase_mode=True,
                warm_session_used=False,
                force_stop_used=False,
                max_targets_per_run=3,
                max_follows_per_target_per_run=1,
            )

        self.assertEqual([call["target_id"] for call in engine.calls], ["t1", "t2"])
        self.assertFalse(engine.calls[1].get("start_from_current_followers_list", False))
        self.assertEqual(result["summary"]["target_id"], "t2")
        self.assertIn("follow_target_fast_rotation_fallback_standard", [event for _level, event, _kw in logs])

    def test_global_cap_one_with_target_budget_two_allows_only_one_follow(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 1,
                "follow_session_outcome": "global_follow_cap_reached",
                "follow_stop_reason": "global_follow_cap_reached",
            }),
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 1,
                "follow_session_outcome": "follows_completed",
            }),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
            max_follows_per_target_per_run=2,
        )

        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(result["global_follows_completed"], 1)
        self.assertEqual(result["summary"]["follow_stop_reason"], "global_follow_cap_reached")

    def test_global_cap_two_with_budget_one_uses_two_targets(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 2,
                "follow_session_outcome": "target_budget_reached",
                "follow_stop_reason": "target_budget_reached",
            }),
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 2,
                "follow_session_outcome": "target_budget_reached",
                "follow_stop_reason": "target_budget_reached",
            }),
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 2,
                "follow_session_outcome": "target_budget_reached",
                "follow_stop_reason": "target_budget_reached",
            }),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[
                target("t1", "source_one", 0),
                target("t2", "source_two", 1),
                target("t3", "source_three", 2),
            ],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
            max_follows_per_target_per_run=1,
        )

        self.assertEqual([call["target_id"] for call in engine.calls], ["t1", "t2"])
        self.assertEqual([call["target_follow_budget"] for call in engine.calls], [1, 1])
        self.assertEqual(result["global_follows_completed"], 2)
        self.assertEqual(result["summary"]["follow_stop_reason"], "global_follow_cap_reached")

    def test_prod_budget_two_allows_two_follows_on_same_target(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 2,
                "global_follows_goal_effective": 5,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 5,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
            max_follows_per_target_per_run=2,
        )

        self.assertEqual([call["target_id"] for call in engine.calls], ["t1", "t2"])
        self.assertEqual([call["target_follow_budget"] for call in engine.calls], [2, 2])
        self.assertEqual([item["follows_completed_count"] for item in result["attempts"]], [2, 1])

    def test_follow_source_rotation_settings_partial_null_row_uses_safe_fallback(self) -> None:
        logs: list[tuple[str, str, dict]] = []
        with patch.object(session, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))), patch.object(
            session.config,
            "FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_PER_RUN",
            2,
        ), patch.object(
            session.config,
            "FOLLOW_TARGET_ROTATION_MAX_TARGETS_PER_RUN",
            3,
        ), patch.object(
            session.supabase_client,
            "load_account_follow_source_settings",
            return_value={
                "max_follows_per_target_per_run": None,
                "max_targets_per_run": 4,
            },
        ):
            settings = session._resolve_follow_source_rotation_settings("acct")

        self.assertEqual(settings["settings_source"], "account_with_fallback")
        self.assertEqual(settings["max_follows_per_target_per_run"], 2)
        self.assertEqual(settings["max_targets_per_run"], 4)
        self.assertEqual(
            sum(1 for _level, event, _kw in logs if event == "follow_source_rotation_setting_fallback_used"),
            1,
        )

    def test_follow_source_rotation_settings_invalid_zero_values_use_safe_fallback(self) -> None:
        logs: list[tuple[str, str, dict]] = []
        with patch.object(session, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))), patch.object(
            session.config,
            "FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_PER_RUN",
            2,
        ), patch.object(
            session.config,
            "FOLLOW_TARGET_ROTATION_MAX_TARGETS_PER_RUN",
            3,
        ), patch.object(
            session.supabase_client,
            "load_account_follow_source_settings",
            return_value={
                "max_follows_per_target_per_run": 0,
                "max_targets_per_run": -1,
            },
        ):
            settings = session._resolve_follow_source_rotation_settings("acct")

        self.assertEqual(settings["settings_source"], "account_with_fallback")
        self.assertEqual(settings["max_follows_per_target_per_run"], 2)
        self.assertEqual(settings["max_targets_per_run"], 3)

    def test_target_without_candidates_does_not_increment_follows(self) -> None:
        engine = FakeFollowersEngine([
            (66, {
                "follows_completed_count": 0,
                "follow_session_outcome": "no_followable_candidates_bounded_exploration",
                "follow_stop_reason": "no_candidates_after_sparse_scrolls",
            }),
            (0, {
                "follows_completed_count": 1,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
            max_follows_per_target_per_run=1,
        )

        self.assertEqual([call["target_id"] for call in engine.calls], ["t1", "t2"])
        self.assertEqual([item["follows_completed_count"] for item in result["attempts"]], [0, 1])
        self.assertFalse(result["all_targets_exhausted"])

    def test_rotation_respects_max_three_targets(self) -> None:
        engine = FakeFollowersEngine([
            (66, {"follows_completed_count": 0, "follow_session_outcome": "no_followable_candidates_bounded_exploration"}),
            (66, {"follows_completed_count": 0, "follow_session_outcome": "no_followable_candidates_bounded_exploration"}),
            (66, {"follows_completed_count": 0, "follow_session_outcome": "no_followable_candidates_bounded_exploration"}),
            (0, {"follows_completed_count": 1, "follow_session_outcome": "follows_completed"}),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[
                target("t1", "source_one", 0),
                target("t2", "source_two", 1),
                target("t3", "source_three", 2),
                target("t4", "source_four", 3),
            ],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
        )

        self.assertEqual([call["target_id"] for call in engine.calls], ["t1", "t2", "t3"])
        self.assertEqual(result["summary"]["follow_stop_reason"], "max_targets_per_run_reached")

    def test_budget_respects_global_follow_cap(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 2,
                "global_follows_goal_effective": 5,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
            (0, {
                "follows_completed_count": 2,
                "global_follows_goal_effective": 5,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
            (0, {
                "follows_completed_count": 1,
                "global_follows_goal_effective": 5,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[
                target("t1", "source_one", 0),
                target("t2", "source_two", 1),
                target("t3", "source_three", 2),
            ],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
            max_follows_per_target_per_run=2,
        )

        self.assertEqual([call["target_follow_budget"] for call in engine.calls], [2, 2, 1])
        self.assertEqual(sum(item["follows_completed_count"] for item in result["attempts"]), 5)
        self.assertEqual(result["summary"]["follow_stop_reason"], "global_follow_cap_reached")
        self.assertEqual(result["global_follows_completed"], 5)

    def test_mono_target_budget_reached_stops_without_loop(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 2,
                "global_follows_goal_effective": 5,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
            (0, {"follows_completed_count": 2, "follow_session_outcome": "follows_completed"}),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[target("t1", "source_one", 0)],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
            max_follows_per_target_per_run=2,
        )

        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(result["summary"]["follow_stop_reason"], "target_budget_reached")

    def test_non_exhaustion_error_does_not_switch_target(self) -> None:
        engine = FakeFollowersEngine([
            (96, {
                "follows_completed_count": 0,
                "follow_session_outcome": "wrong_surface_abort",
                "follow_stop_reason": "wrong_surface_abort",
            }),
            (0, {"follows_completed_count": 1, "follow_session_outcome": "follows_completed"}),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
        )

        self.assertEqual(result["exit_code"], 96)
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(engine.calls[0]["target_id"], "t1")

    def test_single_target_non_exhausted_matches_p1a_behavior(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 1,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[target("t1", "source_one", 0)],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=3,
        )

        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["all_targets_exhausted"])
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(engine.calls[0]["target_id"], "t1")

    def test_logs_do_not_include_sensitive_raw_fields(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 1,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        logs: list[tuple[str, str, dict]] = []
        with patch.object(session, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            session._run_follow_target_rotation(
                object(),
                account_id="acct",
                account_username="account",
                run_id="run",
                follow_targets=[target("t1", "source_one", 0)],
                run_followers_list_engine_session=engine,
                supabase_mode=True,
                warm_session_used=False,
                force_stop_used=False,
                max_targets_per_run=3,
            )

        serialized = repr(logs).lower()
        for forbidden in ["password", "secret", "token", "<node", "xml", "screenshot", "serial", "udid"]:
            self.assertNotIn(forbidden, serialized)

    def test_follow_source_rotation_settings_default_without_supabase_row(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch.object(
            session.supabase_client,
            "load_account_follow_source_settings",
            return_value=None,
        ):
            settings = session._resolve_follow_source_rotation_settings("acct")

        self.assertEqual(settings["settings_source"], "default")
        self.assertEqual(settings["max_follows_per_target_per_run"], 2)
        self.assertEqual(settings["max_targets_per_run"], 3)
        self.assertEqual(settings["bounds"]["max_follows_per_target_per_run"]["max"], 50)
        self.assertEqual(settings["bounds"]["max_targets_per_run"]["max"], 10)

    def test_follow_source_rotation_settings_account_row_overrides_env(self) -> None:
        with patch.dict(os.environ, {
            "FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_PER_RUN": "7",
            "FOLLOW_TARGET_ROTATION_MAX_TARGETS_PER_RUN": "2",
        }, clear=False), patch.object(
            session.supabase_client,
            "load_account_follow_source_settings",
            return_value={
                "max_follows_per_target_per_run": 30,
                "max_targets_per_run": 4,
            },
        ):
            settings = session._resolve_follow_source_rotation_settings("acct")

        self.assertEqual(settings["settings_source"], "account")
        self.assertEqual(settings["max_follows_per_target_per_run"], 30)
        self.assertEqual(settings["max_targets_per_run"], 4)

    def test_follow_source_rotation_settings_env_fallback_when_no_account_row(self) -> None:
        with patch.dict(os.environ, {
            "FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_PER_RUN": "8",
            "FOLLOW_TARGET_ROTATION_MAX_TARGETS_PER_RUN": "4",
        }, clear=False), patch.object(
            session.config,
            "FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_PER_RUN",
            8,
        ), patch.object(
            session.config,
            "FOLLOW_TARGET_ROTATION_MAX_TARGETS_PER_RUN",
            4,
        ), patch.object(
            session.supabase_client,
            "load_account_follow_source_settings",
            return_value=None,
        ):
            settings = session._resolve_follow_source_rotation_settings("acct")

        self.assertEqual(settings["settings_source"], "env")
        self.assertEqual(settings["max_follows_per_target_per_run"], 8)
        self.assertEqual(settings["max_targets_per_run"], 4)

    def test_follow_source_rotation_allows_prod_candidate_budget_without_default_change(self) -> None:
        engine = FakeFollowersEngine([
            (0, {
                "follows_completed_count": 30,
                "global_follows_goal_effective": 35,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
            (0, {
                "follows_completed_count": 5,
                "global_follows_goal_effective": 35,
                "follow_session_outcome": "follows_completed",
                "follow_stop_reason": "",
            }),
        ])
        result = session._run_follow_target_rotation(
            object(),
            account_id="acct",
            account_username="account",
            run_id="run",
            follow_targets=[target("t1", "source_one", 0), target("t2", "source_two", 1)],
            run_followers_list_engine_session=engine,
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=4,
            max_follows_per_target_per_run=30,
        )

        self.assertEqual([call["target_follow_budget"] for call in engine.calls], [30, 5])
        self.assertEqual(result["summary"]["follow_stop_reason"], "global_follow_cap_reached")


class FollowTargetRotationPendingTests(unittest.TestCase):
    def setUp(self) -> None:
        runner.reset_follow_target_rotation_pending()

    def test_pre_follow_gap_log_emits_expected_fields_without_secrets(self) -> None:
        logs: list[tuple[str, str, dict]] = []
        with patch.object(
            runner,
            "log",
            side_effect=lambda level, event, **kw: logs.append((level, event, kw)),
        ):
            runner._pre_follow_gap_log(
                "pre_follow_gap_checkpoint",
                target_username="ct_one",
                candidate_username="cand_one",
                source_profile_username="ct_one",
                visual_candidate_id="vc-1",
                phase="social_memory_guard",
                duration_ms=12.34,
                probe_count=2,
                used_cached_context=True,
                fallback_used=False,
                surface_type="candidate_profile",
                safe_to_tap=True,
                follow_state="follow",
                pending_state=False,
                is_private=False,
                reason="unit",
                auth_token="should_not_log",
                password="should_not_log",
                client_secret="should_not_log",
            )

        self.assertEqual(len(logs), 1)
        _level, event, fields = logs[0]
        self.assertEqual(event, "pre_follow_gap_checkpoint")
        for key in (
            "target_username",
            "candidate_username",
            "phase",
            "duration_ms",
            "probe_count",
            "used_cached_context",
            "fallback_used",
            "surface_type",
            "safe_to_tap",
            "follow_state",
            "pending_state",
            "is_private",
            "reason",
        ):
            self.assertIn(key, fields)
        self.assertNotIn("auth_token", fields)
        self.assertNotIn("password", fields)
        self.assertNotIn("client_secret", fields)

    def test_rotation_pending_set_and_scoped_to_target(self) -> None:
        logs: list[tuple[str, str, dict]] = []
        with patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            runner.set_follow_target_rotation_pending(
                target_username="ct_one",
                reason="target_budget_reached_after_follow_count",
                target_follow_count=1,
                max_follows_per_target_per_run=1,
                global_follow_remaining=1,
                candidate_username="cand_one",
            )
        state = runner.get_follow_target_rotation_pending_state()
        self.assertTrue(state["rotation_pending"])
        self.assertEqual(state["target_username"], "ct_one")
        self.assertEqual(state["target_follow_count"], 1)
        self.assertTrue(runner.is_follow_target_rotation_pending(target_username="ct_one"))
        self.assertFalse(runner.is_follow_target_rotation_pending(target_username="ct_two"))
        self.assertIn("follow_target_rotation_pending_set", [event for _level, event, _kw in logs])

    def test_rotation_pending_block_logs_click(self) -> None:
        logs: list[tuple[str, str, dict]] = []
        runner.set_follow_target_rotation_pending(
            target_username="ct_one",
            reason="test",
        )
        with patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            blocked = runner._follow_target_rotation_pending_block(
                action="open_profile",
                target_username="ct_one",
                candidate_username="cand_two",
                reason="rotation_pending_before_open_follower_profile",
            )
        self.assertTrue(blocked)
        self.assertIn(
            "follow_target_candidate_click_blocked_rotation_pending",
            [event for _level, event, _kw in logs],
        )

    def test_should_stop_for_target_budget_guard(self) -> None:
        self.assertTrue(runner.should_stop_for_target_follow_budget(1, 1))
        self.assertFalse(runner.should_stop_for_target_follow_budget(1, 2))
        self.assertFalse(runner.should_stop_for_target_follow_budget(0, 1))

    def test_rotation_pending_not_set_twice(self) -> None:
        logs: list[tuple[str, str, dict]] = []
        with patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))):
            runner.set_follow_target_rotation_pending(target_username="ct_one", reason="first")
            runner.set_follow_target_rotation_pending(target_username="ct_one", reason="second")
        self.assertEqual(
            [event for _level, event, _kw in logs].count("follow_target_rotation_pending_set"),
            1,
        )

    def test_rotation_pending_blocks_scan_action(self) -> None:
        runner.set_follow_target_rotation_pending(target_username="ct_one", reason="budget")
        with patch.object(runner, "log") as mock_log:
            blocked = runner._follow_target_rotation_pending_block(
                action="scan",
                target_username="ct_one",
                reason="rotation_pending_before_candidate_selection",
            )
        self.assertTrue(blocked)
        mock_log.assert_called_once()
        self.assertEqual(mock_log.call_args[0][1], "follow_target_candidate_scan_blocked_rotation_pending")

    def test_rotation_pending_no_block_when_under_budget(self) -> None:
        self.assertFalse(runner.should_stop_for_target_follow_budget(1, 2))
        self.assertFalse(runner.is_follow_target_rotation_pending(target_username="ct_one"))
        self.assertFalse(
            runner._follow_target_rotation_pending_block(
                action="scan",
                target_username="ct_one",
                reason="under_budget",
            )
        )

    def test_snapshot_invalidation_clears_picker_refresh(self) -> None:
        logs: list[tuple[str, str, dict]] = []
        visual_state = {
            "post_return_picker_refresh_pending": True,
            "post_return_picker_refresh_meta": {"username": "cand_two"},
        }
        open_meta: dict = {"injection_evidence": {"rows": [1]}}
        with patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))), patch.object(
            runner, "invalidate_followers_injection_evidence"
        ) as invalidate:
            runner._invalidate_follow_target_snapshot_after_budget(
                open_meta,
                visual_state,
                source_profile_username="ct_one",
                reason="target_budget_reached",
            )
        self.assertFalse(visual_state.get("post_return_picker_refresh_pending"))
        self.assertNotIn("post_return_picker_refresh_meta", visual_state)
        invalidate.assert_called_once()
        self.assertIn(
            "follow_target_snapshot_invalidated_budget_reached",
            [event for _level, event, _kw in logs],
        )

    def test_fast_rotation_precheck_accepts_committed_followers_list(self) -> None:
        d = FakeDevice()
        global_probe = {
            "is_global_search": False,
            "is_recent_search_surface": True,
            "is_local_followers_search": False,
            "is_lightweight_search": True,
            "surface_type": "recent_search",
            "surface_reason": "ok",
            "duration_ms": 1.0,
        }
        with patch.object(
            runner, "followers_session_list_committed_open_for", return_value=True
        ) as committed, patch.object(
            runner, "followers_session_committed_open_age_ms", return_value=120.0
        ), patch.object(
            runner, "followers_session_committed_meta", return_value={"followers_list_committed_source": "post_follow"}
        ), patch.object(
            runner, "followers_surface_quick_revalidate", return_value=(True, {"open_detection_method": "xml"})
        ) as reval, patch.object(
            runner, "verify_profile", side_effect=[True, True]
        ), patch.object(
            runner, "_fast_rotation_probe_search_surface", return_value=global_probe
        ), patch.object(
            runner, "type_search", return_value=True
        ), patch.object(
            runner, "open_accounts_tab", return_value=False
        ), patch.object(
            runner, "tap_account_result", return_value=True
        ), patch.object(
            runner, "open_followers_list_from_profile", return_value=(True, {"open_detection_method": "own_unified_follow_list"})
        ):
            result = runner.fast_rotate_to_next_target_from_followers(
                d,
                account_id="acct",
                run_id="run",
                from_source_target="source_a",
                to_source_target="source_b",
            )

        self.assertTrue(result["ok"])
        committed.assert_called_with("source_a")
        self.assertEqual(reval.call_count, 1)
        self.assertEqual(reval.call_args_list[0].kwargs.get("source_profile_username"), "source_b")
        self.assertEqual(d.presses, ["back", "back"])


class DeferredPostReturnPersistTests(unittest.TestCase):
    def setUp(self) -> None:
        runner._DEFERRED_FOLLOW_ACTION_LOG_FLUSHES.clear()
        runner._DEFERRED_POST_RETURN_PERSIST_STEPS.clear()

    def tearDown(self) -> None:
        runner._DEFERRED_FOLLOW_ACTION_LOG_FLUSHES.clear()
        runner._DEFERRED_POST_RETURN_PERSIST_STEPS.clear()

    def test_deferred_action_logs_flush_before_completed_status(self) -> None:
        logs: list[tuple[str, str, dict]] = []
        runner._schedule_deferred_follow_action_log_flush(
            events=[("follow_tap_sent", {"safe": True})],
            run_id="run",
            account_id="acct",
            target_username="cand_one",
            source_profile_username="ct_one",
            candidate_username="cand_one",
            supabase_mode=True,
        )
        with patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))), patch.object(
            runner.supabase_client, "insert_action_log", return_value={"ok": True}
        ) as insert_log, patch.object(
            runner.supabase_client, "update_run_status", return_value={"ok": True}
        ) as update_status:
            runner._update_run_status_safe(
                run_id="run",
                status="completed",
                totals={"total": 1, "success": 1, "failed": 0},
                performance_summary={},
            )

        insert_log.assert_called_once()
        update_status.assert_called_once()
        self.assertEqual(update_status.call_args.kwargs["status"], "completed")
        events = [event for _level, event, _kw in logs]
        self.assertIn("post_return_deferred_persist_started", events)
        self.assertIn("post_return_deferred_persist_completed", events)
        self.assertIn("run_status_updated", events)

    def test_deferred_action_logs_none_return_counts_as_success(self) -> None:
        logs: list[tuple[str, str, dict]] = []
        runner._schedule_deferred_follow_action_log_flush(
            events=[("follow_tap_sent", {"safe": True})],
            run_id="run",
            account_id="acct",
            target_username="cand_one",
            source_profile_username="ct_one",
            candidate_username="cand_one",
            supabase_mode=True,
        )
        with patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))), patch.object(
            runner.supabase_client, "insert_action_log", return_value=None
        ), patch.object(
            runner.supabase_client, "update_run_status", return_value={"ok": True}
        ) as update_status:
            runner._update_run_status_safe(
                run_id="run",
                status="completed",
                totals={"total": 1, "success": 1, "failed": 0},
                performance_summary={},
            )

        update_status.assert_called_once()
        self.assertEqual(update_status.call_args.kwargs["status"], "completed")
        events = [event for _level, event, _kw in logs]
        self.assertIn("post_return_deferred_persist_completed", events)
        self.assertNotIn("post_return_deferred_persist_failed", events)

    def test_deferred_action_log_failure_blocks_completed_status(self) -> None:
        logs: list[tuple[str, str, dict]] = []
        runner._schedule_deferred_follow_action_log_flush(
            events=[("follow_tap_sent", {"safe": True})],
            run_id="run",
            account_id="acct",
            target_username="cand_one",
            source_profile_username="ct_one",
            candidate_username="cand_one",
            supabase_mode=True,
        )
        with patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))), patch.object(
            runner.supabase_client, "insert_action_log", side_effect=RuntimeError("boom")
        ), patch.object(
            runner.supabase_client, "update_run_status", return_value={"ok": True}
        ) as update_status:
            runner._update_run_status_safe(
                run_id="run",
                status="completed",
                totals={"total": 1, "success": 1, "failed": 0},
                performance_summary={},
            )

        update_status.assert_called_once()
        self.assertEqual(update_status.call_args.kwargs["status"], "failed")
        events = [event for _level, event, _kw in logs]
        self.assertIn("post_return_deferred_persist_failed", events)
        self.assertIn("run_completed_blocked_deferred_persist_failed", events)

    def test_deferred_post_return_step_flushes_before_completed_status(self) -> None:
        logs: list[tuple[str, str, dict]] = []
        runner._schedule_deferred_post_return_supabase_step(
            step="record_mute_interaction_success",
            fn_name="record_mute_interaction_success",
            args=("acct", "cand_one", "ct_one"),
            kwargs={"run_id": "run", "muted_posts": True, "muted_stories": True},
            run_id="run",
            account_id="acct",
            source_profile_username="ct_one",
            candidate_username="cand_one",
            reason="test",
        )
        with patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))), patch.object(
            runner.supabase_client, "record_mute_interaction_success", return_value={"ok": True}, create=True
        ) as mute_persist, patch.object(
            runner.supabase_client, "update_run_status", return_value={"ok": True}
        ) as update_status:
            runner._update_run_status_safe(
                run_id="run",
                status="completed",
                totals={"total": 1, "success": 1, "failed": 0},
                performance_summary={},
            )

        mute_persist.assert_called_once()
        update_status.assert_called_once()
        self.assertEqual(update_status.call_args.kwargs["status"], "completed")
        events = [event for _level, event, _kw in logs]
        self.assertIn("post_return_deferred_step_started", events)
        self.assertIn("post_return_deferred_step_completed", events)

    def test_deferred_post_return_step_failure_blocks_completed_status(self) -> None:
        logs: list[tuple[str, str, dict]] = []
        runner._schedule_deferred_post_return_supabase_step(
            step="record_post_like_interaction_success",
            fn_name="record_post_like_interaction_success",
            args=("acct", "cand_one", "ct_one"),
            kwargs={"run_id": "run", "liked_count": 1},
            run_id="run",
            account_id="acct",
            source_profile_username="ct_one",
            candidate_username="cand_one",
            reason="test",
        )
        with patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))), patch.object(
            runner.supabase_client, "record_post_like_interaction_success", side_effect=RuntimeError("boom"), create=True
        ), patch.object(
            runner.supabase_client, "update_run_status", return_value={"ok": True}
        ) as update_status:
            runner._update_run_status_safe(
                run_id="run",
                status="completed",
                totals={"total": 1, "success": 1, "failed": 0},
                performance_summary={},
            )

        update_status.assert_called_once()
        self.assertEqual(update_status.call_args.kwargs["status"], "failed")
        events = [event for _level, event, _kw in logs]
        self.assertIn("post_return_deferred_step_failed", events)
        self.assertIn("run_completed_blocked_deferred_persist_failed", events)

    def test_follow_source_success_is_deferred_until_completed_flush(self) -> None:
        logs: list[tuple[str, str, dict]] = []
        follow_out = {"ok": True, "skipped_tap": False}
        with patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))), patch.object(
            runner.supabase_client, "record_follow_interaction_outcome", return_value={"ok": True}, create=True
        ) as follow_outcome, patch.object(
            runner.supabase_client, "record_follow_source_follow_success", return_value={"ok": True}, create=True
        ) as follow_source:
            ok = runner._persist_verified_follow_success_to_supabase(
                supabase_mode=True,
                account_id="acct",
                follower_un="cand_one",
                source_profile_username="ct_one",
                run_id="run",
                follow_out=follow_out,
                fs_af="following",
                f_st="following",
                target_id="target",
                phase="after_post_follow",
                defer_source_follow_success=True,
            )

        self.assertTrue(ok)
        follow_outcome.assert_called_once()
        follow_source.assert_not_called()
        self.assertEqual(len(runner._DEFERRED_POST_RETURN_PERSIST_STEPS), 1)
        events = [event for _level, event, _kw in logs]
        self.assertIn("post_return_critical_persist_step_started", events)
        self.assertIn("post_return_critical_persist_step_completed", events)
        self.assertIn("post_return_deferred_step_scheduled", events)

    def test_critical_follow_persist_failure_does_not_schedule_deferred_source(self) -> None:
        logs: list[tuple[str, str, dict]] = []
        follow_out = {"ok": True, "skipped_tap": False}
        with patch.object(runner, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))), patch.object(
            runner.supabase_client, "record_follow_interaction_outcome", return_value={"ok": False}, create=True
        ), patch.object(
            runner.supabase_client, "record_follow_source_follow_success", return_value={"ok": True}, create=True
        ) as follow_source:
            ok = runner._persist_verified_follow_success_to_supabase(
                supabase_mode=True,
                account_id="acct",
                follower_un="cand_one",
                source_profile_username="ct_one",
                run_id="run",
                follow_out=follow_out,
                fs_af="following",
                f_st="following",
                target_id="target",
                phase="after_post_follow",
                defer_source_follow_success=True,
            )

        self.assertFalse(ok)
        follow_source.assert_not_called()
        self.assertEqual(len(runner._DEFERRED_POST_RETURN_PERSIST_STEPS), 0)


if __name__ == "__main__":
    unittest.main()
