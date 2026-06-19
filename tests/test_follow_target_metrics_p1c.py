from __future__ import annotations

import pathlib
import unittest
from unittest.mock import patch

import account_session_orchestrator as session
import supabase_client


class FollowTargetMetricsP1cTest(unittest.TestCase):
    def test_migration_contract_contains_metrics_columns_indexes_and_fk(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        sql = (root / "supabase/migrations/20260602020100_p1c_follow_target_metrics.sql").read_text()

        for fragment in [
            "follows_sent_count integer not null default 0",
            "followbacks_count integer not null default 0",
            "followback_ratio numeric",
            "last_selected_at timestamptz",
            "last_used_at timestamptz",
            "last_successful_candidate_at timestamptz",
            "last_exhausted_at timestamptz",
            "exhaustion_reason text",
            "cooldown_until timestamptz",
            "metrics_updated_at timestamptz",
            "target_id uuid references public.ig_targets(id) on delete set null",
            "ig_targets_account_last_used_p1c_idx",
            "ig_targets_account_cooldown_until_p1c_idx",
            "ig_targets_account_last_exhausted_p1c_idx",
            "ig_interaction_events_target_id_p1c_idx",
            "ig_interaction_events_account_target_event_at_p1c_idx",
            "follows_sent_count >= 0",
            "followbacks_count >= 0",
            "followback_ratio is null or followback_ratio >= 0",
            "alter table public.ig_interaction_events enable row level security",
            "revoke all on public.ig_interaction_events from anon",
            "grant all on public.ig_interaction_events to service_role",
            "increment_ig_target_follows_sent_p1c",
            "grant execute on function public.increment_ig_target_follows_sent_p1c",
        ]:
            self.assertIn(fragment, sql)

        self.assertNotIn("poor_performance", sql)
        self.assertNotIn("archived_at = now()", sql)
        self.assertNotIn("drop ", sql.lower())

    def test_target_selected_updates_usage_timestamps_and_event(self) -> None:
        with (
            patch.object(supabase_client, "_request_json_tolerate_unknown_columns") as patch_row,
            patch.object(supabase_client, "record_interaction_event", return_value={"ok": True}) as event,
        ):
            out = supabase_client.record_follow_source_target_selected(
                account_id="acct",
                target_id="target-id",
                source_profile="source_one",
                run_id="run-id",
            )

        self.assertTrue(out["ok"])
        body = patch_row.call_args.kwargs["body"]
        self.assertIn("last_selected_at", body)
        self.assertIn("last_used_at", body)
        self.assertIn("metrics_updated_at", body)
        self.assertNotIn("last_exhausted_at", body)
        self.assertEqual(event.call_args.kwargs["target_id"], "target-id")
        self.assertEqual(event.call_args.kwargs["event_type"], "target_selected")

    def test_follow_success_increments_follows_sent_and_records_event(self) -> None:
        with (
            patch.object(supabase_client, "_request_json", side_effect=RuntimeError("rpc missing")),
            patch.object(supabase_client, "load_target_by_id", return_value={"follows_sent_count": 2}),
            patch.object(supabase_client, "_request_json_tolerate_unknown_columns") as patch_row,
            patch.object(supabase_client, "record_interaction_event", return_value={"ok": True}) as event,
        ):
            out = supabase_client.record_follow_source_follow_success(
                account_id="acct",
                target_id="target-id",
                source_profile="source_one",
                candidate_username="candidate_one",
                run_id="run-id",
                outcome="following",
            )

        self.assertTrue(out["ok"])
        body = patch_row.call_args.kwargs["body"]
        self.assertEqual(body["follows_sent_count"], 3)
        self.assertIn("last_successful_candidate_at", body)
        self.assertIn("last_used_at", body)
        self.assertEqual(event.call_args.kwargs["event_type"], "follow_sent")
        self.assertEqual(event.call_args.kwargs["target_id"], "target-id")

    def test_follow_success_uses_atomic_rpc_when_available(self) -> None:
        with (
            patch.object(
                supabase_client,
                "_request_json",
                return_value=[{"follows_sent_count": 4, "followback_ratio": 25}],
            ) as rpc,
            patch.object(supabase_client, "load_target_by_id") as load_target,
            patch.object(supabase_client, "_request_json_tolerate_unknown_columns") as patch_row,
            patch.object(supabase_client, "record_interaction_event", return_value={"ok": True}) as event,
        ):
            out = supabase_client.record_follow_source_follow_success(
                account_id="00000000-0000-0000-0000-000000000001",
                target_id="00000000-0000-0000-0000-000000000002",
                source_profile="source_one",
                candidate_username="candidate_one",
                run_id="00000000-0000-0000-0000-000000000003",
                outcome="following",
            )

        self.assertTrue(out["ok"])
        self.assertEqual(out["applied"], "rpc_increment")
        self.assertEqual(out["follows_sent_count"], 4)
        self.assertEqual(rpc.call_args.args[:2], ("POST", "rpc/increment_ig_target_follows_sent_p1c"))
        load_target.assert_not_called()
        patch_row.assert_not_called()
        self.assertEqual(event.call_args.kwargs["payload"]["metrics_applied"], "rpc_increment")

    def test_missing_target_id_does_not_update_metrics(self) -> None:
        with (
            patch.object(supabase_client, "load_target_by_id") as load_target,
            patch.object(supabase_client, "_request_json_tolerate_unknown_columns") as patch_row,
            patch.object(supabase_client, "log") as log,
        ):
            out = supabase_client.record_follow_source_follow_success(
                account_id="acct",
                target_id=None,
                source_profile="source_one",
                candidate_username="candidate_one",
                run_id="run-id",
            )

        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "missing_target_id_for_metrics")
        load_target.assert_not_called()
        patch_row.assert_not_called()
        self.assertEqual(log.call_args.args[:2], ("warning", "missing_target_id_for_metrics"))

    def test_target_exhausted_updates_reason_without_status_change(self) -> None:
        with (
            patch.object(supabase_client, "_request_json_tolerate_unknown_columns") as patch_row,
            patch.object(supabase_client, "record_interaction_event", return_value={"ok": True}) as event,
        ):
            out = supabase_client.record_follow_source_target_exhausted(
                account_id="acct",
                target_id="target-id",
                source_profile="source_one",
                reason="no_candidates_after_sparse_scrolls",
                run_id="run-id",
                outcome="no_followable_candidates_bounded_exploration",
            )

        self.assertTrue(out["ok"])
        body = patch_row.call_args.kwargs["body"]
        self.assertIn("last_exhausted_at", body)
        self.assertEqual(body["exhaustion_reason"], "no_candidates_after_sparse_scrolls")
        self.assertNotIn("status", body)
        self.assertNotIn("archived_at", body)
        self.assertEqual(event.call_args.kwargs["event_type"], "target_exhausted")

    def test_budget_reached_updates_usage_without_exhaustion(self) -> None:
        with (
            patch.object(supabase_client, "_request_json_tolerate_unknown_columns") as patch_row,
            patch.object(supabase_client, "record_interaction_event", return_value={"ok": True}) as event,
        ):
            out = supabase_client.record_follow_source_target_budget_reached(
                account_id="acct",
                target_id="target-id",
                source_profile="source_one",
                run_id="run-id",
                target_follows_completed=2,
                target_budget=2,
            )

        self.assertTrue(out["ok"])
        body = patch_row.call_args.kwargs["body"]
        self.assertIn("last_used_at", body)
        self.assertNotIn("last_exhausted_at", body)
        self.assertNotIn("exhaustion_reason", body)
        self.assertEqual(event.call_args.kwargs["event_type"], "target_budget_reached")

    def test_non_exhaustion_runtime_error_records_event_only(self) -> None:
        with (
            patch.object(supabase_client, "_request_json_tolerate_unknown_columns") as patch_row,
            patch.object(supabase_client, "record_interaction_event", return_value={"ok": True}) as event,
        ):
            out = supabase_client.record_follow_source_runtime_error_non_exhaustion(
                account_id="acct",
                target_id="target-id",
                source_profile="source_one",
                reason="login_required",
                run_id="run-id",
                outcome="login_required",
            )

        self.assertTrue(out["ok"])
        patch_row.assert_not_called()
        self.assertEqual(event.call_args.kwargs["event_type"], "target_runtime_error_non_exhaustion")
        self.assertEqual(event.call_args.kwargs["event_status"], "failed")

    def test_cooldown_and_exhaustion_are_display_only_for_p1c_eligibility(self) -> None:
        ok, reason = supabase_client._is_eligible_follow_target_row({
            "id": "target-id",
            "target_username": "source_one",
            "status": "valid",
            "quality_status": "eligible",
            "verification_status": "found",
            "last_exhausted_at": "2026-06-02T00:00:00Z",
            "cooldown_until": "2099-01-01T00:00:00Z",
        })

        self.assertTrue(ok)
        self.assertEqual(reason, "eligible")

    def test_fbr_classifier_keeps_pending_and_insufficient_non_destructive(self) -> None:
        pending = supabase_client.classify_follow_source_performance(
            follows_sent_count=0,
            followbacks_count=0,
        )
        insufficient = supabase_client.classify_follow_source_performance(
            follows_sent_count=99,
            followbacks_count=1,
        )

        self.assertEqual(pending["status"], "pending")
        self.assertIsNone(pending["followback_ratio"])
        self.assertFalse(pending["auto_archive"])
        self.assertEqual(insufficient["status"], "insufficient_data")
        self.assertLess(insufficient["followback_ratio"], 8)
        self.assertFalse(insufficient["auto_archive"])
        self.assertFalse(insufficient["review_candidate"])

    def test_fbr_classifier_uses_p1c_thresholds_after_minimum_sample(self) -> None:
        bad = supabase_client.classify_follow_source_performance(
            follows_sent_count=100,
            followbacks_count=8,
        )
        avg = supabase_client.classify_follow_source_performance(
            follows_sent_count=100,
            followback_ratio=8.1,
        )
        good = supabase_client.classify_follow_source_performance(
            follows_sent_count=100,
            followbacks_count=15,
        )

        self.assertEqual(bad["status"], "bad")
        self.assertTrue(bad["review_candidate"])
        self.assertFalse(bad["auto_archive"])
        self.assertEqual(avg["status"], "avg")
        self.assertFalse(avg["auto_archive"])
        self.assertEqual(good["status"], "good")
        self.assertFalse(good["auto_archive"])

    def test_rotation_records_selected_budget_and_exhaustion_metrics(self) -> None:
        calls: list[tuple[str, dict]] = []

        class Engine:
            def __init__(self) -> None:
                self.calls = 0
                self.last_session_summary = {}

            def __call__(self, _device, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    self.last_session_summary = {
                        "follows_completed_count": 2,
                        "global_follows_goal_effective": 5,
                        "follow_session_outcome": "follows_completed",
                    }
                    return 0
                self.last_session_summary = {
                    "follows_completed_count": 0,
                    "follow_session_outcome": "no_followable_candidates_bounded_exploration",
                    "follow_stop_reason": "no_candidates_after_sparse_scrolls",
                }
                return 66

        with patch.object(
            session,
            "_record_follow_target_metric",
            side_effect=lambda event, **kwargs: calls.append((event, kwargs)) or {"ok": True},
        ):
            session._run_follow_target_rotation(
                object(),
                account_id="acct",
                account_username="account",
                run_id="run-id",
                follow_targets=[
                    {"target_id": "target-1", "source_profile": "source_one", "target_index": 0},
                    {"target_id": "target-2", "source_profile": "source_two", "target_index": 1},
                ],
                run_followers_list_engine_session=Engine(),
                supabase_mode=True,
                warm_session_used=False,
                force_stop_used=False,
                max_targets_per_run=2,
                max_follows_per_target_per_run=2,
            )

        self.assertEqual([event for event, _kwargs in calls], [
            "target_selected",
            "target_budget_reached",
            "target_selected",
            "target_exhausted",
        ])

    def test_sync_followbacks_migration_contract(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        sql = (root / "supabase/migrations/20260615190000_sync_target_followbacks_count.sql").read_text()
        for fragment in [
            "sync_ig_target_followbacks_count",
            "sync_ig_account_target_followbacks",
            "backfill_ig_target_followbacks",
            "followbacks_metrics_reliable_at",
            "source_target_id",
            "source_target_username",
        ]:
            self.assertIn(fragment, sql)

    def test_sync_target_followbacks_count_calls_rpc(self) -> None:
        with patch.object(
            supabase_client,
            "call_rpc",
            return_value={"ok": True, "target_id": "target-id", "followbacks_count": 3},
        ) as rpc:
            out = supabase_client.sync_target_followbacks_count("target-id")
        self.assertTrue(out["ok"])
        rpc.assert_called_once_with(
            "sync_ig_target_followbacks_count",
            {"p_target_id": "target-id"},
        )

    def test_mark_followbacks_triggers_account_sync_when_matches(self) -> None:
        with (
            patch.object(supabase_client, "_request_json_tolerate_unknown_columns") as patch_rows,
            patch.object(supabase_client, "sync_account_target_followbacks_count", return_value={"ok": True, "synced_targets": 1}) as sync,
        ):
            patch_rows.return_value = [{"id": "iu-1", "username": "follower_one"}]
            out = supabase_client.mark_followbacks_from_seen_followers(
                "acct",
                ["follower_one"],
                source="followers_scan",
            )
        self.assertTrue(out["ok"])
        sync.assert_called_once_with("acct")
        self.assertEqual(out["target_followbacks_sync"]["synced_targets"], 1)


if __name__ == "__main__":
    unittest.main()
