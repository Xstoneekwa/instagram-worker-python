from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import follow_candidate_recovery
import follow_persistence_intent
import social_memory
import supabase_client


ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
ACTION_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
TARGET_ID = "44444444-4444-4444-8444-444444444444"


class DurableAmbiguousFollowCrossSessionTests(unittest.TestCase):
    def test_ambiguity_recovery_key_is_action_bound_and_replay_stable(self) -> None:
        first = follow_candidate_recovery.ambiguous_follow_recovery_key(
            "account", "@Candidate", "target", "action-1"
        )
        replay = follow_candidate_recovery.ambiguous_follow_recovery_key(
            "account", "candidate", "target", "action-1"
        )
        second_action = follow_candidate_recovery.ambiguous_follow_recovery_key(
            "account", "candidate", "target", "action-2"
        )
        self.assertEqual(first, replay)
        self.assertNotEqual(first, second_action)

    def test_backend_projection_is_ambiguous_not_failed_or_success(self) -> None:
        with patch.object(supabase_client, "merge_interacted_user_row") as merge:
            merge.return_value = {"ok": True}
            out = supabase_client.record_ambiguous_follow_mutation(
                ACCOUNT_ID,
                "Target.User",
                "source.ct",
                action_id=ACTION_ID,
                run_id=RUN_ID,
                session_id="session",
                business_session_id="business-session",
                follow_state_after="unknown",
                failure_reason="follow_verify_timeout_after_tap",
                target_id=TARGET_ID,
            )
        self.assertTrue(out["ok"])
        patch_body = merge.call_args.args[3]
        marker = patch_body["payload"]["follow_mutation_ambiguous"]
        self.assertEqual(patch_body["interaction_type"], "follow_ambiguous")
        self.assertFalse(patch_body["was_successful"])
        self.assertTrue(marker["tap_sent"])
        self.assertFalse(marker["receipt_exists"])
        self.assertFalse(marker["resolved"])
        self.assertEqual(marker["action_id"], ACTION_ID)
        self.assertNotIn("followed_at", patch_body)
        self.assertNotIn("follow_counter_delta", patch_body)

    def test_same_action_replay_uses_same_memory_row_and_queue_key(self) -> None:
        with (
            patch.object(
                follow_candidate_recovery.supabase_client,
                "record_ambiguous_follow_mutation",
                return_value={"ok": True},
            ) as marker,
            patch.object(
                follow_candidate_recovery.supabase_client,
                "enqueue_follow_candidate_recovery_v1",
                return_value={"ok": True},
            ) as enqueue,
        ):
            for _ in range(2):
                self.assertTrue(
                    follow_candidate_recovery.persist_unresolved_ambiguity(
                        account_id=ACCOUNT_ID,
                        candidate_username="Target.User",
                        source_target_id=TARGET_ID,
                        source_ct_username="source.ct",
                        original_run_id=RUN_ID,
                        original_request_id=ACTION_ID,
                        business_session_id="business-session",
                        action_id=ACTION_ID,
                        follow_state_after="unknown",
                        failure_reason="verify_unknown",
                    )
                )
        self.assertEqual(marker.call_count, 2)
        self.assertEqual(enqueue.call_count, 2)
        self.assertEqual(
            enqueue.call_args.kwargs["recovery_key"],
            follow_candidate_recovery.ambiguous_follow_recovery_key(
                ACCOUNT_ID, "Target.User", TARGET_ID, ACTION_ID
            ),
        )
        self.assertEqual(
            enqueue.call_args.kwargs["evidence"]["action_id"], ACTION_ID
        )
        self.assertFalse(enqueue.call_args.kwargs["evidence"]["follow_verified"])
        self.assertFalse(enqueue.call_args.kwargs["evidence"]["follow_receipt_exists"])

    def test_durable_marker_blocks_future_business_sessions(self) -> None:
        row = {
            "payload": {
                "follow_mutation_ambiguous": {
                    "durable": True,
                    "resolved": False,
                    "action_id": ACTION_ID,
                }
            }
        }
        out = social_memory.evaluate_follow_eligibility(
            target_username="target.user",
            source_profile="source.ct",
            db_row=row,
            runtime_followed=set(),
            runtime_unfollowed=set(),
            runtime_interacted=set(),
            runtime_skipped=set(),
            config=SimpleNamespace(
                SOCIAL_MEMORY_ENABLED=True,
                SOCIAL_MEMORY_REVISIT_COOLDOWN_DAYS=0,
                SOCIAL_MEMORY_FOLLOW_REVISIT_COOLDOWN_DAYS=0,
                SOCIAL_MEMORY_INTERACTION_COOLDOWN_DAYS=0,
                SOCIAL_MEMORY_MAX_SKIP_COUNT_DB=9999,
            ),
        )
        self.assertFalse(out.allowed)
        self.assertEqual(
            out.reason, "durable_follow_mutation_ambiguous_unreconciled"
        )

    def test_ordinary_confirmed_failure_is_not_permanent_ambiguity(self) -> None:
        out = social_memory.evaluate_follow_eligibility(
            target_username="target.user",
            source_profile="source.ct",
            db_row={
                "interaction_type": "follow_failed",
                "was_successful": False,
                "payload": {},
            },
            runtime_followed=set(),
            runtime_unfollowed=set(),
            runtime_interacted=set(),
            runtime_skipped=set(),
            config=SimpleNamespace(
                SOCIAL_MEMORY_ENABLED=True,
                SOCIAL_MEMORY_REVISIT_COOLDOWN_DAYS=0,
                SOCIAL_MEMORY_FOLLOW_REVISIT_COOLDOWN_DAYS=0,
                SOCIAL_MEMORY_INTERACTION_COOLDOWN_DAYS=0,
                SOCIAL_MEMORY_MAX_SKIP_COUNT_DB=9999,
            ),
        )
        self.assertNotEqual(
            out.reason, "durable_follow_mutation_ambiguous_unreconciled"
        )

    def test_pre_tap_stage_is_not_projected_as_ambiguous(self) -> None:
        source = Path("runner.py").read_text(encoding="utf-8")
        projection = source[
            source.index("def _project_future_v1_post_tap_follow_intents(") :
            source.index("def _recover_verified_follow_persistence_intents(")
        ]
        self.assertIn("load_account_post_tap_projection_intents", projection)
        loader = Path("follow_persistence_intent.py").read_text(encoding="utf-8")
        self.assertIn('{"tap_dispatched", "ambiguous_post_tap_v1"}', loader)
        self.assertNotIn('allowed_stages = {"physical_attempt_started"', loader)
        self.assertNotIn('allowed_stages = {"ambiguous"', loader)

    def test_post_tap_stage_survives_restart_and_projects(self) -> None:
        source = Path("runner.py").read_text(encoding="utf-8")
        self.assertIn('stage="tap_dispatched"', source)
        self.assertIn("load_account_post_tap_projection_intents", source)
        self.assertIn("future_v1_post_tap_intent_unresolved", source)
        self.assertIn('stage="ambiguity_durable_projected"', source)

    def test_projection_loader_excludes_legacy_and_applies_limit_after_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root, patch.dict(
            os.environ,
            {"FOLLOW_PERSISTENCE_INTENT_ROOT": temp_root},
        ):
            def create(run_id: str, account_id: str, stage: str) -> None:
                action_id = {
                    "run-a": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "run-b": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                    "run-c": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                }[run_id]
                follow_persistence_intent.create_mutation_intent(
                    action_id=action_id,
                    action_type="follow",
                    account_id=account_id,
                    run_id=run_id,
                    request_id=action_id,
                    candidate_username=f"candidate-{run_id}",
                )
                follow_persistence_intent.update_intent_stage(
                    run_id=run_id,
                    action_id=action_id,
                    stage=stage,
                )

            # These sort first but must not consume this account's bounded slot.
            create("run-a", "other-account", "tap_dispatched")
            create("run-b", ACCOUNT_ID, "ambiguous")
            create("run-c", ACCOUNT_ID, "ambiguous_post_tap_v1")

            rows = follow_persistence_intent.load_account_post_tap_projection_intents(
                account_id=ACCOUNT_ID,
                limit=1,
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_id"], "run-c")

            follow_persistence_intent.update_intent_stage(
                run_id="run-c",
                action_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                stage="ambiguity_durable_projected",
            )
            self.assertEqual(
                follow_persistence_intent.load_account_post_tap_projection_intents(
                    account_id=ACCOUNT_ID,
                    limit=1,
                ),
                [],
            )

    def test_resolution_requires_exact_action_and_authoritative_reason(self) -> None:
        row = {
            "payload": {
                "follow_mutation_ambiguous": {
                    "durable": True,
                    "resolved": False,
                    "action_id": ACTION_ID,
                }
            }
        }
        with (
            patch.object(supabase_client, "load_interacted_user", return_value=row),
            patch.object(supabase_client, "merge_interacted_user_row") as merge,
        ):
            mismatch = supabase_client.resolve_ambiguous_follow_mutation(
                ACCOUNT_ID,
                "target.user",
                "source.ct",
                action_id=TARGET_ID,
                resolution="canonical_follow_receipt",
            )
            unauthorized = supabase_client.resolve_ambiguous_follow_mutation(
                ACCOUNT_ID,
                "target.user",
                "source.ct",
                action_id=ACTION_ID,
                resolution="time_elapsed",
            )
        self.assertFalse(mismatch["ok"])
        self.assertFalse(unauthorized["ok"])
        merge.assert_not_called()

    def test_forward_migration_binds_action_and_keeps_ambiguity_non_success(self) -> None:
        migration = Path(
            "supabase/migrations/20260829091041_durable_ambiguous_follow_cross_session_v1.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("backend_reconciliation_requires_action_id", migration)
        self.assertIn("'action_id', v_action_id", migration)
        self.assertIn("'mutation_state', 'ambiguous'", migration)
        self.assertIn("'tap_sent', true", migration)
        self.assertIn("'receipt_exists', false", migration)
        self.assertIn("'follow_ambiguous', false", migration)
        self.assertNotIn("follow_counter_delta", migration)

    def test_protected_source_has_one_canonical_convergence_writer(self) -> None:
        source = Path("runner.py").read_text(encoding="utf-8")
        navigation = Path("instagram_navigation.py").read_text(encoding="utf-8")
        self.assertIn("def _persist_unresolved_follow_ambiguity(", source)
        self.assertIn('stage="tap_dispatched"', source)
        self.assertIn("_project_future_v1_post_tap_follow_intents", source)
        self.assertIn("and not _post_tap_unresolved", source)
        self.assertIn("FollowTapDispatchJournalUnavailable", navigation)
        self.assertIn("_tap_dispatch_ambiguity_persisted", source)


if __name__ == "__main__":
    unittest.main()
