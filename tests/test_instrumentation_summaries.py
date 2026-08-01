from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import instagram_navigation as nav
import runner


class InstrumentationSummariesTest(unittest.TestCase):
    def test_rejection_reason_counts_aggregate_stable_reasons(self) -> None:
        tracker = runner._target_rejection_tracker("mythyllus")

        runner._target_rejection_record(
            tracker,
            reason="runtime_seen_before_follow",
            candidate_username="cand_one",
            source_phase="unit",
            emit_log=False,
        )
        runner._target_rejection_record(
            tracker,
            reason="skip_private_profile",
            candidate_username="cand_two",
            source_phase="unit",
            emit_log=False,
        )
        runner._target_rejection_record(
            tracker,
            reason="persistent_preopen_already_connected:active_follow",
            candidate_username="cand_three",
            source_phase="unit",
            emit_log=False,
        )

        self.assertEqual(
            tracker["rejection_reason_counts"],
            {"runtime_seen": 1, "private_account": 1, "already_followed": 1},
        )

    def test_lifecycle_rejection_reason_is_preserved_and_unknown_stays_unknown(self) -> None:
        tracker = runner._target_rejection_tracker("mythyllus")
        logs: list[tuple[str, str, dict]] = []
        with patch.object(
            runner,
            "log",
            side_effect=lambda level, event, **kw: logs.append((level, event, kw)),
        ):
            runner._target_rejection_record(
                tracker,
                reason="lifecycle_unfollowed_completed",
                candidate_username="known_candidate",
                source_phase="social_memory",
            )
            runner._target_rejection_record(
                tracker,
                reason="future_unclassified_reason",
                candidate_username="unknown_candidate",
                source_phase="unit",
            )

        self.assertEqual(
            tracker["rejection_reason_counts"],
            {"lifecycle_unfollowed_completed": 1, "unknown": 1},
        )
        rejection_logs = [kw for _level, event, kw in logs if event == "target_candidate_rejected"]
        self.assertEqual(rejection_logs[0]["reason"], "lifecycle_unfollowed_completed")
        self.assertEqual(rejection_logs[0]["raw_reason"], "lifecycle_unfollowed_completed")
        self.assertEqual(rejection_logs[1]["reason"], "unknown")
        self.assertEqual(rejection_logs[1]["raw_reason"], "future_unclassified_reason")

    def test_target_scan_no_candidate_summary_payload_counts(self) -> None:
        tracker = runner._target_rejection_tracker("mythyllus")
        tracker["candidates_seen_count"] = 5
        tracker["scrolls_attempted"] = 2
        tracker["last_scroll_index"] = 1
        runner._target_rejection_record(
            tracker,
            reason="no_blue_follow_spans",
            count=3,
            source_phase="picker",
            emit_log=False,
        )
        runner._target_rejection_record(
            tracker,
            reason="vision_validation_rejected",
            count=2,
            source_phase="picker",
            emit_log=False,
        )

        payload = runner._target_rejection_summary_payload(
            tracker,
            stop_reason="no_candidates_after_sparse_scrolls",
        )

        self.assertEqual(payload["target_username"], "mythyllus")
        self.assertEqual(payload["candidates_seen_count"], 5)
        self.assertEqual(payload["candidates_rejected_count"], 5)
        self.assertEqual(
            payload["rejection_reason_counts"],
            {"no_follow_button": 3, "stale_row": 2},
        )
        self.assertEqual(payload["scrolls_attempted"], 2)
        self.assertEqual(payload["last_scroll_index"], 1)
        self.assertEqual(payload["stop_reason"], "no_candidates_after_sparse_scrolls")

    def test_global_follow_cap_reason_is_not_no_candidate_or_exhausted(self) -> None:
        tracker = runner._target_rejection_tracker("mythyllus")
        runner._target_rejection_record(
            tracker,
            reason="global_follow_cap_reached",
            source_phase="before_candidate_selection",
            emit_log=False,
        )

        payload = runner._target_rejection_summary_payload(
            tracker,
            stop_reason="global_follow_cap_reached",
        )

        self.assertEqual(payload["stop_reason"], "global_follow_cap_reached")
        self.assertEqual(
            payload["rejection_reason_counts"],
            {"global_follow_cap_reached": 1},
        )
        self.assertNotIn("no_follow_button", payload["rejection_reason_counts"])
        self.assertNotIn("filter_rejected", payload["rejection_reason_counts"])

    def test_rejection_examples_are_limited_before_summary(self) -> None:
        tracker = runner._target_rejection_tracker("mythyllus")
        logs: list[tuple[str, str, dict]] = []
        with patch.object(
            runner,
            "log",
            side_effect=lambda level, event, **kw: logs.append((level, event, kw)),
        ):
            for idx in range(8):
                runner._target_rejection_record(
                    tracker,
                    reason="runtime_seen",
                    candidate_username=f"cand_{idx}",
                    source_phase="unit",
                )

        events = [event for _level, event, _kw in logs]
        self.assertEqual(events.count("target_candidate_rejected"), 3)
        self.assertEqual(tracker["rejection_reason_counts"]["runtime_seen"], 8)

    def test_post_follow_like_perf_summary_payload_reports_open_and_recovery(self) -> None:
        payload = nav._post_follow_like_perf_summary_payload(
            timings={
                "likes_total_ms": 1234.5,
                "grid_prep_0_ms": 220.0,
                "open_post_0_ms": 410.0,
            },
            post_open={"tap_to_viewer_detected_ms": 320.0},
            like_perf={"like_tap_dispatch_ms": 45.0},
            result="failed_safe_continue",
            failure_reason="post_viewer_not_detected",
            recovery_ms=480.0,
        )

        self.assertEqual(payload["total_ms"], 1234.5)
        self.assertEqual(payload["grid_detect_ms"], 220.0)
        self.assertEqual(payload["open_post_ms"], 410.0)
        self.assertEqual(payload["viewer_detect_ms"], 320.0)
        self.assertEqual(payload["like_tap_ms"], 45.0)
        self.assertEqual(payload["recovery_ms"], 480.0)
        self.assertEqual(payload["failure_reason"], "post_viewer_not_detected")

    def test_post_follow_mute_perf_summary_payload_reports_budget_and_result(self) -> None:
        payload = nav._post_follow_mute_perf_summary_payload(
            timings={
                "mute_total_ms": 1567.0,
                "open_mute_sheet_ms": 310.0,
                "labels_detect_ms": 80.0,
                "toggle_posts_ms": 410.0,
                "toggle_stories_ms": 390.0,
            },
            result="success",
            skip_reason=None,
            budget_s=2.8,
            effective_total_budget_s=2.35,
            toggle_stage_required_budget_s=1.52,
        )

        self.assertEqual(payload["total_ms"], 1567.0)
        self.assertEqual(payload["sheet_open_ms"], 310.0)
        self.assertEqual(payload["labels_detect_ms"], 80.0)
        self.assertEqual(payload["toggle_ms"], 800.0)
        self.assertEqual(payload["result"], "success")
        self.assertEqual(payload["skip_reason"], "")
        self.assertEqual(payload["toggle_stage_required_budget_s"], 1.52)

    def _run_post_follow_phase_for_mute_summary(
        self,
        *,
        follow_state_after: str = "following",
        follow_private_accounts: bool = False,
        mute_flow_on: bool = True,
        real_mute_on: bool = True,
    ) -> list[tuple[str, str, dict]]:
        logs: list[tuple[str, str, dict]] = []
        with patch.object(
            nav.config,
            "FOLLOW_PRIVATE_ACCOUNTS",
            follow_private_accounts,
            create=True,
        ), patch.object(
            nav.config,
            "ENABLE_VISUAL_FOLLOW_MUTE_FLOW",
            mute_flow_on,
            create=True,
        ), patch.object(
            nav.config,
            "ENABLE_REAL_VISUAL_MUTE_AFTER_FOLLOW",
            real_mute_on,
            create=True,
        ), patch.object(
            nav,
            "detect_followers_list_screen",
            return_value={"action_bar_title": "candidate"},
        ), patch(
            "navigation_engine.observe_instagram_state",
            return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9, "reason": "ok"},
        ), patch.object(
            nav,
            "_post_follow_overlay_ui_hints",
            return_value={},
        ), patch.object(
            nav,
            "_post_follow_screen_fingerprint",
            return_value={"fingerprint_id": "fp", "screen_class": "profile_like"},
        ), patch.object(
            nav,
            "run_post_follow_post_likes_phase",
            return_value=nav._post_follow_post_likes_out_template(),
        ), patch.object(
            nav,
            "post_follow_controlled_return_to_followers_list",
            return_value=(True, "unit", None),
        ), patch.object(
            nav,
            "log",
            side_effect=lambda level, event, **kw: logs.append((level, event, kw)),
        ):
            nav.run_visual_candidate_post_follow_phase(
                MagicMock(),
                pkg="com.instagram.android",
                source_profile_username="source",
                visual_candidate_id="vc1",
                follower_username="candidate",
                follow_success_verified=True,
                follow_state_after=follow_state_after,
                skipped_tap=False,
                det={},
            )
        return logs

    def test_post_follow_mute_early_skip_private_pending_emits_perf_summary(self) -> None:
        logs = self._run_post_follow_phase_for_mute_summary(
            follow_state_after="requested",
            follow_private_accounts=True,
        )
        summaries = [
            kw for _level, event, kw in logs if event == "post_follow_mute_perf_summary"
        ]

        self.assertEqual(len(summaries), 1)
        payload = summaries[0]
        self.assertEqual(payload["result"], "skipped")
        self.assertEqual(payload["skip_reason"], "private_follow_request_pending")
        self.assertEqual(payload["sheet_open_ms"], 0.0)
        self.assertEqual(payload["labels_detect_ms"], 0.0)
        self.assertEqual(payload["toggle_ms"], 0.0)
        self.assertIn("budget_s", payload)
        self.assertIn("effective_total_budget_s", payload)
        self.assertIn("toggle_stage_required_budget_s", payload)

    def test_post_follow_mute_early_skip_disabled_emits_perf_summary(self) -> None:
        logs = self._run_post_follow_phase_for_mute_summary(mute_flow_on=False)
        summaries = [
            kw for _level, event, kw in logs if event == "post_follow_mute_perf_summary"
        ]

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["result"], "skipped")
        self.assertEqual(summaries[0]["skip_reason"], "mute_disabled")

    def test_post_follow_mute_early_skip_summary_has_no_secret_fields(self) -> None:
        logs = self._run_post_follow_phase_for_mute_summary(
            follow_state_after="requested",
            follow_private_accounts=True,
        )
        payload = next(
            kw for _level, event, kw in logs if event == "post_follow_mute_perf_summary"
        )
        blob = " ".join(f"{key}={value}" for key, value in payload.items()).lower()

        for forbidden in ("password", "secret", "token", "bearer", "credential", "vault"):
            self.assertNotIn(forbidden, blob)


if __name__ == "__main__":
    unittest.main()
