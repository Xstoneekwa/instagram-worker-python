from __future__ import annotations

import os
import unittest
from unittest.mock import patch

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
    source: str = "reveaustral",
    metric: str = "followers",
    bounds: str = "[523,336][788,496]",
    duplicate_followers: bool = False,
) -> str:
    metric_rid = {
        "followers": "profile_header_followers_stacked_familiar",
        "following": "profile_header_following_stacked_familiar",
        "posts": "profile_header_post_count_front_familiar",
    }[metric]
    metric_desc = {
        "followers": "688 followers",
        "following": "1 234 following",
        "posts": "42 posts",
    }[metric]
    extra = (
        '<node class="android.view.ViewGroup" resource-id="com.instagram.androie:id/profile_header_followers_stacked_familiar" '
        'content-desc="12 followers" bounds="[523,520][788,620]" clickable="true" />'
        if duplicate_followers
        else ""
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<hierarchy>
  <node class="android.widget.TextView" resource-id="com.instagram.androie:id/action_bar_title" text="{source}" bounds="[0,80][1080,180]" />
  <node class="android.view.ViewGroup" resource-id="com.instagram.androie:id/{metric_rid}" content-desc="{metric_desc}" bounds="{bounds}" clickable="true">
    <node class="android.widget.TextView" resource-id="com.instagram.androie:id/profile_header_familiar_followers_value" text="688" bounds="[552,359][667,420]" />
    <node class="android.widget.TextView" resource-id="com.instagram.androie:id/profile_header_familiar_followers_label" text="followers" bounds="[552,420][711,473]" />
  </node>
  {extra}
</hierarchy>"""


def followers_list_xml(
    *,
    source: str = "reveaustral",
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
        nav.followers_session_reset_list_committed_open()
        self.addCleanup(nav.followers_session_reset_list_committed_open)
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
                "reveaustral",
                "com.instagram.androie",
                profile_verified=True,
            )

        self.assertTrue(ok)
        self.assertEqual(d.clicks, [(655, 416)])
        self.assertEqual(meta["open_method"], "followers_entry_fast_path")
        post_confirm.assert_called_once()
        self.assertEqual(post_confirm.call_args.kwargs["post_tap_settle_s"], 2.0)
        self.assertIn("followers_entry_fast_path_metric_found", [event for event, _ in logs])
        self.assertIn("followers_entry_fast_path_tap_sent", [event for event, _ in logs])

    def test_followers_entry_fast_path_rejects_following_metric_and_falls_back(self) -> None:
        nav.followers_session_reset_list_committed_open()
        self.addCleanup(nav.followers_session_reset_list_committed_open)
        d = FakeFollowersEntryDevice(followers_entry_profile_xml(metric="following"))

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
                "reveaustral",
                "com.instagram.androie",
                profile_verified=True,
            )

        self.assertTrue(ok)
        self.assertEqual(meta["open_method"], "fallback_v2")
        self.assertEqual(d.clicks, [])
        fallback_v2.assert_called_once()

    def test_followers_entry_fast_path_absent_or_ambiguous_metric_falls_back(self) -> None:
        for xml in (
            followers_entry_profile_xml(metric="posts"),
            followers_entry_profile_xml(duplicate_followers=True),
        ):
            nav.followers_session_reset_list_committed_open()
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
                    "reveaustral",
                    "com.instagram.androie",
                    profile_verified=True,
                )

            self.assertTrue(ok)
            self.assertEqual(meta["open_method"], "fallback_v2")
            self.assertEqual(d.clicks, [])
            fallback_v2.assert_called_once()
        nav.followers_session_reset_list_committed_open()

    def test_followers_entry_fast_path_unsafe_bounds_falls_back_without_tap(self) -> None:
        nav.followers_session_reset_list_committed_open()
        self.addCleanup(nav.followers_session_reset_list_committed_open)
        d = FakeFollowersEntryDevice(followers_entry_profile_xml(bounds="[900,336][1040,496]"))

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
                "reveaustral",
                "com.instagram.androie",
                profile_verified=True,
            )

        self.assertTrue(ok)
        self.assertEqual(meta["open_method"], "fallback_v2")
        self.assertEqual(d.clicks, [])
        fallback_v2.assert_called_once()

    def test_followers_entry_fast_path_requires_post_tap_validation_success(self) -> None:
        nav.followers_session_reset_list_committed_open()
        self.addCleanup(nav.followers_session_reset_list_committed_open)
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
                "reveaustral",
                "com.instagram.androie",
                profile_verified=True,
            )

        self.assertFalse(ok)
        self.assertEqual(d.clicks, [(655, 416)])
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
                "reveaustral",
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
                "reveaustral",
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
                "reveaustral",
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
                "reveaustral",
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
                    "reveaustral",
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
                "reveaustral",
            )

        self.assertTrue(ok)
        self.assertEqual(last_det["open_detection_method"], "fallback_visual")
        fallback.assert_called_once()

    def test_followers_entry_post_tap_xml_fast_snapshot_supports_candidate_reuse(self) -> None:
        d = FakeFollowersPostTapDevice(followers_list_xml())
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


if __name__ == "__main__":
    unittest.main()
