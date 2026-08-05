from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path
from unittest import mock

import follow60_ordering_v2_shadow as shadow
import logs
import runner


ACCOUNT = "b024e94e-395d-4f02-9787-81ddc679b014"
RUN = "439cea5e-d428-4742-9235-4cee667e02fd"
REQUEST = "d0b97a79-4b5f-4283-a6ea-8816e3e68185"
BUSINESS = "24ef78d0-955c-4ba0-8cbe-7c4e12073fc5"
TARGET = "target-berkeleyparis"
WORKER_SHA = "d" * 40


def _env(*, enabled: bool = True, account_ids: str = ACCOUNT) -> dict[str, str]:
    return {
        "FOLLOW60_ORDERING_V2_SHADOW_ENABLED": "1" if enabled else "0",
        "FOLLOW60_ORDERING_V2_SHADOW_ACCOUNT_IDS": account_ids,
    }


def _direct_xml(candidate: str = "candidate") -> str:
    return f"""<hierarchy>
    <node text="{candidate}"/><node text="8 posts"/>
    <node resource-id="profile_tabs_container" bounds="[0,700][1080,850]">
      <node resource-id="profile_tab_icon_view" content-desc="Grid view"
            selected="true" bounds="[0,700][360,850]"/>
    </node>
    <node class="android.widget.ImageView" resource-id="profile_grid_media_0"
          content-desc="Post thumbnail, row 1, column 1" bounds="[0,900][360,1260]"/>
    <node class="android.widget.ImageView" resource-id="profile_grid_media_1"
          content-desc="Post thumbnail, row 1, column 2" bounds="[360,900][720,1260]"/>
    <node resource-id="bottom_navigation" bounds="[0,2200][1080,2340]"/>
    </hierarchy>"""


def _capture(xml: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "xml": xml,
        "xml_fingerprint": "fixture-only",
        "duration_ms": 100.0,
        "exact_identity": True,
        "profile_surface": True,
        "follow_cta_positive": True,
        "navigation_generation": "nav:1",
        "ui_generation": 1,
        "private_probe_payload": {"private_profile_detected": False},
    }
    payload.update(overrides)
    return payload


def _classify(xml: str, **capture_overrides: object) -> dict[str, object]:
    result = shadow.classify_existing_pre_follow_capture(
        _capture(xml, **capture_overrides),
        account_id=ACCOUNT,
        run_id=RUN,
        request_id=REQUEST,
        candidate_username="candidate",
        source_profile_username="source_ct",
        visual_candidate_id="xml_list:candidate",
        environ=_env(),
    )
    assert result is not None
    return result


def _begin(candidate: str = "candidate", **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "account_id": ACCOUNT,
        "run_id": RUN,
        "request_id": REQUEST,
        "business_session_id": BUSINESS,
        "attempt_id": 1,
        "binding_kind": "mainline",
        "worker_sha": WORKER_SHA,
        "target_id": TARGET,
        "candidate_username": candidate,
        "source_profile_username": "source_ct",
        "visual_candidate_id": f"xml_list:{candidate}",
        "action_id": f"xml_list:{candidate}",
        "business_evidence": {
            "filter_evaluated": True,
            "filter_passed": True,
            "filter_reason": "candidate_reached_v1_follow_gate",
            "eligibility_passed": True,
            "eligibility_reason": "candidate_reached_v1_follow_gate",
            "follow_budget_available": True,
            "configured_global_budget": 40,
            "effective_global_budget": 40,
            "effective_target_budget": 10,
        },
        "expected_binding": {
            "account_id": ACCOUNT,
            "run_id": RUN,
            "request_id": REQUEST,
            "business_session_id": BUSINESS,
            "attempt_id": 1,
            "binding_kind": "mainline",
            "worker_sha": WORKER_SHA,
            "target_id": TARGET,
            "candidate_username": candidate,
        },
        "environ": _env(),
    }
    kwargs.update(overrides)
    out = shadow.begin_candidate_shadow(_capture(_direct_xml(candidate)), **kwargs)
    assert out is not None
    return out


class Follow60OrderingV2ShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        shadow.reset_shadow_state_for_tests()

    def tearDown(self) -> None:
        shadow.reset_shadow_state_for_tests()

    def test_disabled_by_default_and_allowlist_is_mandatory(self) -> None:
        self.assertFalse(shadow.enabled_for_account(ACCOUNT, environ={}))
        self.assertFalse(shadow.enabled_for_account(ACCOUNT, environ=_env(account_ids="")))
        self.assertFalse(shadow.enabled_for_account(ACCOUNT, environ=_env(account_ids="other")))
        self.assertTrue(shadow.enabled_for_account(ACCOUNT, environ=_env()))

    def test_direct_grid_safe_requires_complete_positive_contract(self) -> None:
        out = _classify(_direct_xml())
        self.assertEqual("DIRECT_GRID_SAFE", out["classification"])
        self.assertTrue(out["eligible_for_ordering_v2"])
        geometry = out["post_grid_geometry"]
        self.assertEqual(8, geometry["posts_count"])
        self.assertEqual("existing_pre_follow_mono_xml_profile_count", geometry["posts_count_source"])
        self.assertTrue(geometry["first_row_fully_visible"])
        self.assertTrue(geometry["absolute_top_left_unique"])
        self.assertEqual(1, geometry["absolute_top_left_row"])
        self.assertEqual(1, geometry["absolute_top_left_column"])
        self.assertEqual(0, out["acquisition_count"])
        self.assertFalse(out["behavior_changed"])

    def test_ambiguous_below_fold_overlaps_no_posts_private_and_loading_fail_closed(self) -> None:
        below = """<hierarchy><node bounds="[0,0][1080,2340]"/>
        <node text="candidate"/><node text="14 posts"/><node text="Suggested for you"/>
        <node resource-id="profile_highlights_tray" content-desc="Story highlights"/>
        <node content-desc="Profile tab grid" selected="true"/></hierarchy>"""
        self.assertEqual("BELOW_FOLD", _classify(below)["classification"])
        overlap = _classify(_direct_xml().replace(
            '<node class="android.widget.ImageView" resource-id="profile_grid_media_0"',
            '<node text="Suggested for you" bounds="[0,850][1080,1100]"/>'
            '<node class="android.widget.ImageView" resource-id="profile_grid_media_0"',
        ))
        self.assertNotEqual("DIRECT_GRID_SAFE", overlap["classification"])
        no_posts = """<hierarchy><node bounds="[0,0][1080,2340]"/>
        <node text="candidate"/><node text="0 posts"/>
        <node resource-id="profile_tabs_container" bounds="[0,700][1080,850]">
        <node resource-id="profile_tab_icon_view" content-desc="Grid view" selected="true"/>
        </node><node text="No posts yet"/></hierarchy>"""
        self.assertEqual("NO_POSTS", _classify(no_posts)["classification"])
        private = _classify(
            '<hierarchy><node bounds="[0,0][1080,2340]"/></hierarchy>',
            ok=False,
            private_probe_payload={"private_profile_detected": True},
        )
        self.assertEqual("PRIVATE", private["classification"])
        loading = _classify(
            '<hierarchy><node bounds="[0,0][1080,2340]"/><node text="Loading"/></hierarchy>'
        )
        self.assertNotEqual("DIRECT_GRID_SAFE", loading["classification"])

    def test_reels_and_tagged_selected_are_never_direct_grid_safe(self) -> None:
        for label in ("Reels", "Tagged"):
            xml = _direct_xml().replace(
                '<node resource-id="profile_tab_icon_view" content-desc="Grid view"\n'
                '            selected="true" bounds="[0,700][360,850]"/>',
                '<node resource-id="profile_tab_icon_view" content-desc="Grid view"\n'
                '            selected="false" bounds="[0,700][360,850]"/>'
                f'<node resource-id="profile_tab_icon_view" content-desc="{label}" '
                'selected="true" bounds="[360,700][720,850]"/>',
            )
            out = _classify(xml)
            self.assertNotEqual("DIRECT_GRID_SAFE", out["classification"], label)

    def test_v5_reject_and_golden_fail_paths_are_copied_without_actions(self) -> None:
        _begin()
        shadow.observe_runtime_event(
            "follow_60s_post_grid_evidence_fallback_golden_direct",
            {"follower_username": "candidate", "rejection_reason": "grid_ambiguous"},
        )
        shadow.observe_runtime_event(
            "post_follow_like_v5_rejected",
            {"follower_username": "candidate", "reason": "story_or_highlight_surface"},
        )
        terminal = shadow.finalize_active_contexts(
            status="partial_error", reason="golden_open_failed", account_id=ACCOUNT, run_id=RUN
        )[0]
        self.assertEqual("V5_REJECT", terminal["v1_actual_execution"]["selected_path"])
        self.assertEqual("rejected", terminal["v1_actual_execution"]["v5_result"])
        self.assertIn("golden_fallback", terminal["v1_actual_execution"]["avoidable_stages"])
        self.assertTrue(all(value == 0 for value in terminal["operation_counters"].values()))

    def test_one_terminal_event_contains_binding_v1_path_reentry_cpu_and_zero_operations(self) -> None:
        _begin()
        events = [
            ("follow_action_verified", {"target_username": "candidate", "follow_state_after": "following"}),
            ("post_follow_post_like_open_started", {"follower_username": "candidate"}),
            ("follow_60s_direct_post_cell_tap_sent", {"follower_username": "candidate"}),
            ("post_follow_viewer_detection_strategy", {
                "candidate_username": "candidate", "viewer_confirmed": True,
                "final_strategy": "a2", "a2_elapsed_ms": 520.0,
            }),
            ("post_follow_open_like_proof_stashed", {
                "follower_username": "candidate", "proof_method": "ui_description_exact_like",
            }),
            ("visual_post_like_verify_completed", {
                "follower_username": "candidate", "liked_verified": True, "verify_total_ms": 600.0,
            }),
            ("post_follow_post_likes_return_to_profile_success", {
                "follower_username": "candidate", "return_to_profile_total_ms": 900.0,
            }),
            ("follow_60s_return_candidate_proof_used", {
                "follower_username": "candidate", "back_count": 1,
                "final_ct_exact": True, "ct_poll_elapsed_ms": 1200.0,
            }),
            ("follow_60s_stage_journaled_v2", {
                "candidate_username": "candidate", "stage": "like_verified", "ok": True,
            }),
            ("candidate_local_persistence_summary", {
                "candidate_username": "candidate", "composite_flush_ok": True,
                "critical_rpc_acknowledged": True, "next_candidate_allowed": True,
            }),
        ]
        for name, fields in events:
            self.assertEqual([], shadow.observe_runtime_event(name, fields))
        terminal = shadow.observe_runtime_event(
            "visual_candidate_profile_return_ct_success",
            {"follower_username": "candidate", "return_method": "fresh_candidate_proof_one_back_then_exact_ct"},
        )
        self.assertEqual(1, len(terminal))
        event = terminal[0]
        self.assertEqual(shadow.SCHEMA, event["schema_version"])
        self.assertEqual("completed", event["event_status"])
        self.assertEqual(ACCOUNT, event["account_id"])
        self.assertEqual(REQUEST, event["request_id"])
        self.assertEqual(RUN, event["run_id"])
        self.assertEqual(BUSINESS, event["business_session_id"])
        self.assertEqual(TARGET, event["target_id"])
        self.assertEqual("candidate", event["candidate_username"])
        self.assertEqual("SAFE_DIRECT", event["v1_actual_execution"]["selected_path"])
        self.assertTrue(event["v1_actual_execution"]["viewer_opened"])
        self.assertEqual("verified", event["v1_actual_execution"]["like_result"])
        self.assertEqual("exact", event["v1_actual_execution"]["return_ct_result"])
        self.assertEqual("REENTRY_LEVEL_1_EVIDENCE_AVAILABLE", event["reentry_evidence"]["classification"])
        self.assertGreater(event["cpu_timings"]["total_shadow_cpu_ns"], 0)
        self.assertTrue(all(value == 0 for value in event["operation_counters"].values()))
        self.assertEqual([], shadow.observe_runtime_event(
            "visual_candidate_profile_return_ct_success", {"follower_username": "candidate"}
        ))

    def test_manual_stop_at_six_boundaries_is_honest_unique_and_cleans_context(self) -> None:
        boundaries = (
            None,
            ("follow_action_verified", {"target_username": "candidate", "follow_state_after": "following"}),
            ("follow_60s_stage_journaled_v2", {"candidate_username": "candidate", "stage": "mute_posts_verified", "ok": True}),
            ("post_follow_post_like_open_started", {"follower_username": "candidate"}),
            ("visual_post_like_verify_completed", {"follower_username": "candidate", "liked_verified": True}),
            ("post_follow_return_ct_started", {"follower_username": "candidate"}),
        )
        for index, boundary in enumerate(boundaries):
            shadow.reset_shadow_state_for_tests()
            candidate = f"candidate{index}"
            _begin(candidate)
            if boundary is not None:
                name, fields = boundary
                fields = dict(fields)
                for key in ("candidate_username", "follower_username", "target_username"):
                    if key in fields:
                        fields[key] = candidate
                shadow.observe_runtime_event(name, fields)
            terminal = shadow.finalize_active_contexts(
                status="partial_manual_stop", reason="manual_stop_signal",
                account_id=ACCOUNT, run_id=RUN,
            )
            self.assertEqual(1, len(terminal))
            self.assertEqual("partial_manual_stop", terminal[0]["event_status"])
            self.assertFalse(terminal[0]["v1_actual_execution"]["cycle_complete"])
            self.assertEqual([], shadow.finalize_active_contexts(
                status="partial_manual_stop", reason="duplicate", account_id=ACCOUNT, run_id=RUN
            ))

    def test_binding_mismatch_is_terminal_non_blocking(self) -> None:
        _begin(expected_binding={"business_session_id": "wrong", "target_id": "wrong"})
        terminal = shadow.finalize_active_contexts(
            status="partial_worker_stop", reason="run_end", account_id=ACCOUNT, run_id=RUN
        )
        self.assertEqual(1, len(terminal))
        self.assertEqual("shadow_internal_error_non_blocking", terminal[0]["event_status"])
        self.assertIn("business_session_id_mismatch", terminal[0]["event_terminal_reason"])
        self.assertFalse(terminal[0]["safety_assertions"]["shadow_failure_blocks_v1"])

    def test_logger_emits_original_then_one_terminal_event_without_recursion(self) -> None:
        _begin()
        with mock.patch.object(logs, "_write_payload") as writer:
            logs.log(
                "info", "visual_candidate_profile_return_ct_success",
                follower_username="candidate", return_method="exact",
            )
        self.assertEqual(2, writer.call_count)
        self.assertEqual("visual_candidate_profile_return_ct_success", writer.call_args_list[0].args[0]["event"])
        self.assertEqual(shadow.PUBLIC_EVENT, writer.call_args_list[1].args[0]["event"])

    def test_new_candidate_flushes_previous_partial_once_and_never_reuses_context(self) -> None:
        _begin("candidate_a")
        _begin("candidate_b")
        pending = shadow.observe_runtime_event(
            "follow_action_verified", {"target_username": "candidate_b", "follow_state_after": "following"}
        )
        self.assertEqual(1, len(pending))
        self.assertEqual("candidate_a", pending[0]["candidate_username"])
        self.assertEqual("partial_error", pending[0]["event_status"])
        terminal = shadow.observe_runtime_event(
            "visual_candidate_profile_return_ct_success", {"follower_username": "candidate_b"}
        )
        self.assertEqual(1, len(terminal))
        self.assertEqual("candidate_b", terminal[0]["candidate_username"])

    def test_shadow_off_keeps_original_log_payload_only(self) -> None:
        with mock.patch.object(logs, "_write_payload") as writer:
            logs.log("info", "v1_event", candidate_username="candidate")
        self.assertEqual(1, writer.call_count)
        self.assertEqual(
            {"level": "info", "event": "v1_event", "candidate_username": "candidate"},
            {k: v for k, v in writer.call_args.args[0].items() if k != "ts"},
        )

    def test_runner_bridge_is_observational_and_fail_open(self) -> None:
        with mock.patch.object(shadow, "begin_candidate_shadow", return_value={"classification": "AMBIGUOUS"}):
            out = runner._record_follow60_ordering_v2_shadow_from_existing_capture(
                {"xml": "<hierarchy/>"}, account_id=ACCOUNT, run_id=RUN,
                request_id=REQUEST, candidate_username="candidate",
                source_profile_username="source_ct", visual_candidate_id="xml_list:candidate",
            )
        self.assertEqual("AMBIGUOUS", out["classification"])
        with mock.patch.object(shadow, "begin_candidate_shadow", side_effect=RuntimeError("shadow-only")), mock.patch.object(runner, "log"):
            self.assertIsNone(runner._record_follow60_ordering_v2_shadow_from_existing_capture(
                None, account_id=ACCOUNT, run_id=RUN, request_id=REQUEST,
                candidate_username="candidate", source_profile_username="source_ct",
                visual_candidate_id="xml_list:candidate",
            ))

    def test_no_forbidden_ui_acquisition_calls_or_imports(self) -> None:
        source = Path(shadow.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse({"uiautomator2", "device", "PIL", "cv2"} & imported)
        forbidden_calls = {
            "screenshot", "dump_hierarchy", "click", "tap", "swipe",
            "sleep", "poll", "query_accessibility",
        }
        calls = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
        self.assertFalse(forbidden_calls & calls)

    def test_fixture_essential_field_completeness_is_at_least_95_percent(self) -> None:
        _begin()
        terminal = shadow.observe_runtime_event(
            "visual_candidate_profile_return_ct_success", {"follower_username": "candidate"}
        )[0]
        values = [
            terminal["account_id"], terminal["request_id"], terminal["run_id"],
            terminal["business_session_id"], terminal["attempt_id"], terminal["binding_kind"],
            terminal["worker_sha"], terminal["target_id"], terminal["candidate_username"],
            terminal["visual_candidate_id"], terminal["action_id"], terminal["candidate_index"],
            terminal["business_eligibility"]["filter_passed"],
            terminal["business_eligibility"]["eligibility_passed"],
            terminal["business_eligibility"]["follow_budget_available"],
            terminal["initial_surface"]["identity_exact"], terminal["initial_surface"]["profile_public"],
            terminal["initial_surface"]["follow_cta_state"],
            terminal["post_grid_geometry"]["posts_count"],
            terminal["post_grid_geometry"]["posts_count_source"],
            terminal["post_grid_geometry"]["posts_tab_selected"],
            terminal["post_grid_geometry"]["visible_post_cell_count"],
            terminal["post_grid_geometry"]["first_row_fully_visible"],
            terminal["post_grid_geometry"]["absolute_top_left_unique"],
            terminal["post_grid_geometry"]["absolute_top_left_row"],
            terminal["post_grid_geometry"]["absolute_top_left_column"],
            terminal["classification"]["initial"], terminal["classification"]["initial_reason"],
            terminal["opened_at_monotonic_ns"], terminal["terminal_at_monotonic_ns"],
            terminal["operation_counters"]["shadow_extra_screenshot_count"],
            terminal["operation_counters"]["shadow_extra_xml_count"],
            terminal["operation_counters"]["shadow_extra_poll_count"],
            terminal["operation_counters"]["shadow_extra_tap_count"],
            terminal["operation_counters"]["shadow_extra_accessibility_query_count"],
            terminal["operation_counters"]["shadow_path_override_count"],
            terminal["operation_counters"]["shadow_intent_created_count"],
            terminal["cpu_timings"]["total_shadow_cpu_ns"],
        ]
        present = sum(value not in (None, "", "unknown") for value in values)
        self.assertGreaterEqual(100.0 * present / len(values), 95.0)


if __name__ == "__main__":
    unittest.main()
