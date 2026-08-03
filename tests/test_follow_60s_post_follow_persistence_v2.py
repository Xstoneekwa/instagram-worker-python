from __future__ import annotations

import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import follow_60s_canary as canary
import account_session_orchestrator as account_session
import instagram_navigation as nav
import post_follow_stage_outbox as outbox
import runner
from tests.follow60_generic_fixtures import (
    TEST_CANARY_ACCOUNT_ID,
    TEST_CANARY_USERNAME,
    configure_canary,
)


class Follow60PostFollowOutboxV2Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "outbox.sqlite3"
        self.binding = {
            "account_id": TEST_CANARY_ACCOUNT_ID,
            "original_run_id": "00000000-0000-0000-0000-000000000101",
            "request_id": "00000000-0000-0000-0000-000000000102",
            "action_id": "00000000-0000-0000-0000-000000000103",
            "candidate_username": "candidate",
            "source_profile": "source_ct",
            "attempt_id": 1,
            "business_session_id": "business-session-1",
        }
        self.active_binding = {
            "account_id": self.binding["account_id"],
            "run_id": self.binding["original_run_id"],
            "request_id": self.binding["request_id"],
            "control_id": "00000000-0000-0000-0000-000000000104",
            "worker_sha": "a" * 40,
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _journal(self, stage: str, payload: dict | None = None) -> dict:
        stage_payload = dict(payload or {})
        stage_payload.setdefault("control_id", self.active_binding["control_id"])
        stage_payload.setdefault("worker_sha", self.active_binding["worker_sha"])
        if stage == "return_ct_exact":
            stage_payload.setdefault("like_terminal_status", "verified")
            stage_payload.setdefault("like_terminal_reason", "like_verified")
        return outbox.journal_stage(
            **self.binding,
            stage=stage,
            verified_at="2026-07-31T20:00:00+00:00",
            payload=stage_payload,
            path=self.path,
        )

    def test_four_stages_flush_once_with_exact_binding_and_mode_0600(self) -> None:
        for stage in outbox.VALID_STAGES:
            self.assertTrue(self._journal(stage, {"liked_count": 1})["ok"])
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")
        response = {
            "ok": True,
            "binding_valid": True,
            "inserted_stages": list(outbox.VALID_STAGES),
            "duplicate_stages": [],
        }
        ledger_response = {
            "ok": True,
            "schema": "FOLLOW60_RUN_SCOPED_CYCLE_LEDGER_V1",
            "cycle_was_new": True,
            "new_cycle_count": 1,
            "max_cycles": 10,
            "barrier_reached": False,
            "next_candidate_permitted": True,
            "revision": 1,
        }
        with mock.patch(
            "supabase_client.persist_follow_60s_post_follow_v2",
            return_value=response,
        ) as rpc, mock.patch(
            "supabase_client.ack_follow_60s_completed_cycle_v1",
            return_value=ledger_response,
        ) as ledger_rpc:
            result = outbox.flush_pending(active_binding=self.active_binding, path=self.path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["pending"], 0)
        rpc.assert_called_once()
        kwargs = rpc.call_args.kwargs
        self.assertEqual(kwargs["run_id"], self.binding["original_run_id"])
        self.assertEqual(kwargs["request_id"], self.binding["request_id"])
        self.assertEqual(kwargs["business_session_id"], "business-session-1")
        self.assertIs(kwargs["cycle_complete"], True)
        self.assertEqual([item["stage"] for item in kwargs["stages"]], list(outbox.VALID_STAGES))
        ledger_rpc.assert_called_once()
        self.assertEqual(result["latest_ledger_ack"]["new_cycle_count"], 1)

    def test_receipt_schema_contains_every_durable_contract_field(self) -> None:
        receipt = self._journal("mute_posts_verified", {"proof_type": "toggle_state_exact"})
        self.assertTrue(receipt["inserted"])
        with sqlite3.connect(str(self.path)) as conn:
            columns = {
                str(row[1]) for row in conn.execute(
                    "pragma table_info(post_follow_stage_receipts)"
                ).fetchall()
            }
            row = conn.execute(
                """
                select schema_version, verified, proof_type_redacted,
                       idempotency_key, cycle_complete, delivery_status,
                       attempt_count, last_error_redacted
                from post_follow_stage_receipts
                """
            ).fetchone()
        self.assertTrue({
            "schema_version", "account_id", "original_run_id",
            "original_request_id", "action_id", "action_id_hash",
            "candidate_username", "stage", "verified", "event_at",
            "proof_type_redacted", "idempotency_key", "cycle_complete",
            "delivery_status", "attempt_count", "last_error_redacted",
        }.issubset(columns))
        self.assertEqual(row[0], outbox.OUTBOX_SCHEMA)
        self.assertEqual(row[1:3], (1, "toggle_state_exact"))
        self.assertEqual(row[4:], (0, "pending", 0, ""))

    def test_local_idempotence_and_failed_rpc_preserve_receipt(self) -> None:
        first = self._journal("like_verified", {"liked_count": 1})
        second = self._journal("like_verified", {"liked_count": 1})
        self.assertTrue(first["inserted"])
        self.assertFalse(second["inserted"])
        self.assertEqual(outbox.pending_count(self.path), 1)
        with mock.patch(
            "supabase_client.persist_follow_60s_post_follow_v2",
            side_effect=RuntimeError("db_unavailable"),
        ):
            result = outbox.flush_pending(active_binding=self.active_binding, path=self.path)
        self.assertFalse(result["ok"])
        self.assertEqual(outbox.pending_count(self.path), 1)
        with sqlite3.connect(str(self.path)) as conn:
            status, attempts, error = conn.execute(
                "select delivery_status,attempt_count,last_error_redacted "
                "from post_follow_stage_receipts"
            ).fetchone()
        self.assertEqual((status, attempts, error), ("pending", 1, "RuntimeError"))

    def test_partial_stop_prefixes_flush_only_physically_verified_stages(self) -> None:
        for stop_after in range(1, len(outbox.VALID_STAGES) + 1):
            with self.subTest(stop_after=stop_after):
                path = Path(self.tmp.name) / f"partial-{stop_after}.sqlite3"
                for stage in outbox.VALID_STAGES[:stop_after]:
                    payload = {
                        "control_id": self.active_binding["control_id"],
                        "worker_sha": self.active_binding["worker_sha"],
                    }
                    if stage == "return_ct_exact":
                        payload.update({
                            "like_terminal_status": "verified",
                            "like_terminal_reason": "like_verified",
                        })
                    outbox.journal_stage(
                        **self.binding,
                        stage=stage,
                        verified_at="2026-07-31T20:00:00+00:00",
                        payload=payload,
                        path=path,
                    )
                response = {
                    "ok": True,
                    "binding_valid": True,
                    "inserted_stages": list(outbox.VALID_STAGES[:stop_after]),
                    "duplicate_stages": [],
                }
                with mock.patch(
                    "supabase_client.persist_follow_60s_post_follow_v2",
                    return_value=response,
                ) as rpc, mock.patch(
                    "supabase_client.ack_follow_60s_completed_cycle_v1",
                    return_value={
                        "ok": True,
                        "schema": "FOLLOW60_RUN_SCOPED_CYCLE_LEDGER_V1",
                        "new_cycle_count": 1,
                        "max_cycles": 10,
                        "barrier_reached": False,
                        "next_candidate_permitted": True,
                        "revision": 1,
                    },
                ):
                    result = outbox.flush_pending(active_binding=self.active_binding, path=path)
                self.assertTrue(result["ok"])
                sent = [item["stage"] for item in rpc.call_args.kwargs["stages"]]
                self.assertEqual(sent, list(outbox.VALID_STAGES[:stop_after]))
                self.assertEqual(outbox.pending_count(path), 0)

    def test_rpc_success_then_crash_before_delete_replays_db_only_as_duplicates(self) -> None:
        self._journal("like_verified", {"liked_count": 1})
        inserted = {
            "ok": True, "binding_valid": True,
            "inserted_stages": ["like_verified"], "duplicate_stages": [],
        }
        with mock.patch(
            "supabase_client.persist_follow_60s_post_follow_v2",
            return_value=inserted,
        ), mock.patch.object(outbox, "_delete_confirmed", side_effect=RuntimeError("crash")):
            with self.assertRaisesRegex(RuntimeError, "crash"):
                outbox.flush_pending(active_binding=self.active_binding, path=self.path)
        self.assertEqual(outbox.pending_count(self.path), 1)
        duplicate = {
            "ok": True, "binding_valid": True,
            "inserted_stages": [], "duplicate_stages": ["like_verified"],
        }
        with mock.patch(
            "supabase_client.persist_follow_60s_post_follow_v2",
            return_value=duplicate,
        ) as rpc:
            replay = outbox.flush_pending(active_binding=self.active_binding, path=self.path)
        self.assertTrue(replay["ok"])
        self.assertEqual(outbox.pending_count(self.path), 0)
        self.assertNotIn("device", rpc.call_args.kwargs)

    def test_incomplete_rpc_confirmation_preserves_outbox(self) -> None:
        self._journal("mute_posts_verified")
        with mock.patch(
            "supabase_client.persist_follow_60s_post_follow_v2",
            return_value={
                "ok": True, "binding_valid": True,
                "inserted_stages": [], "duplicate_stages": [],
            },
        ):
            result = outbox.flush_pending(active_binding=self.active_binding, path=self.path)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "rpc_stage_confirmation_incomplete")
        self.assertEqual(outbox.pending_count(self.path), 1)

    def test_invalid_binding_fails_before_journal(self) -> None:
        bad = dict(self.binding)
        bad["request_id"] = ""
        with self.assertRaisesRegex(ValueError, "follow60_stage_binding_missing_or_invalid"):
            outbox.journal_stage(
                **bad,
                stage="mute_posts_verified",
                verified_at="2026-07-31T20:00:00+00:00",
                path=self.path,
            )

    def test_safe_skip_cycle_is_ledgered_after_composite_ack(self) -> None:
        self._journal("mute_posts_verified")
        self._journal("mute_stories_verified")
        self._journal("return_ct_exact", {
            "control_id": "00000000-0000-0000-0000-000000000104",
            "worker_sha": "a" * 40,
            "like_terminal_status": "safe_skip",
            "like_terminal_reason": "post_like_skipped_no_posts_yet",
        })
        response = {
            "ok": True,
            "binding_valid": True,
            "inserted_stages": [
                "mute_posts_verified", "mute_stories_verified", "return_ct_exact"
            ],
            "duplicate_stages": [],
        }
        with mock.patch(
            "supabase_client.persist_follow_60s_post_follow_v2", return_value=response,
        ), mock.patch(
            "supabase_client.ack_follow_60s_completed_cycle_v1",
            return_value={
                "ok": True, "schema": "FOLLOW60_RUN_SCOPED_CYCLE_LEDGER_V1",
                "new_cycle_count": 10, "max_cycles": 10,
                "barrier_reached": True, "next_candidate_permitted": False,
                "revision": 10,
            },
        ) as ledger_rpc:
            result = outbox.flush_pending(active_binding=self.active_binding, path=self.path)
        self.assertTrue(result["ok"])
        self.assertFalse(result["latest_ledger_ack"]["next_candidate_permitted"])
        self.assertEqual(
            ledger_rpc.call_args.kwargs["like_terminal_status"], "safe_skip"
        )

    def test_failed_safe_continue_binds_as_safe_skip_without_inventing_like(self) -> None:
        status, reason = nav._post_follow_like_terminal_binding(
            {
                "phase_outcome": "failed_safe_continue",
                "ok": False,
                "liked_count": 0,
                "post_follow_likes_recoverable_failure_count": 1,
                "skipped_reason": "suggested_surface_blocks_post_selection",
            },
            {"like_verified": False},
        )
        self.assertEqual(status, "safe_skip")
        self.assertEqual(reason, "suggested_surface_blocks_post_selection")

    def test_failed_safe_continue_cannot_mask_unacked_like(self) -> None:
        status, reason = nav._post_follow_like_terminal_binding(
            {
                "phase_outcome": "failed_safe_continue",
                "ok": False,
                "liked_count": 1,
                "post_follow_likes_recoverable_failure_count": 1,
                "skipped_reason": "viewer_not_proven",
            },
            {"like_verified": False},
        )
        self.assertEqual((status, reason), ("", ""))

    def test_cycle_is_not_deleted_when_ledger_ack_fails(self) -> None:
        for stage in outbox.VALID_STAGES:
            self._journal(stage, {"liked_count": 1})
        response = {
            "ok": True, "binding_valid": True,
            "inserted_stages": list(outbox.VALID_STAGES), "duplicate_stages": [],
        }
        with mock.patch(
            "supabase_client.persist_follow_60s_post_follow_v2", return_value=response,
        ), mock.patch(
            "supabase_client.ack_follow_60s_completed_cycle_v1",
            return_value={"ok": False, "reason": "ledger_unavailable"},
        ):
            result = outbox.flush_pending(active_binding=self.active_binding, path=self.path)
        self.assertFalse(result["ok"])
        self.assertEqual(outbox.pending_count(self.path), 4)

    def _journal_bound(
        self,
        *,
        path: Path,
        control_id: str,
        worker_sha: str | None = None,
        account_id: str | None = None,
        run_id: str | None = None,
        request_id: str | None = None,
        action_id: str | None = None,
        stage: str = "like_verified",
    ) -> dict:
        return outbox.journal_stage(
            account_id=account_id or self.binding["account_id"],
            original_run_id=run_id or self.binding["original_run_id"],
            request_id=request_id or self.binding["request_id"],
            action_id=action_id or self.binding["action_id"],
            candidate_username="candidate",
            source_profile="source_ct",
            attempt_id=1,
            business_session_id="business-session-1",
            stage=stage,
            verified_at="2026-07-31T20:00:00+00:00",
            payload={
                "control_id": control_id,
                "worker_sha": worker_sha or self.active_binding["worker_sha"],
                "liked_count": 1,
            },
            path=path,
        )

    @staticmethod
    def _rpc_success(*stages: str) -> dict:
        return {
            "ok": True,
            "binding_valid": True,
            "inserted_stages": list(stages),
            "duplicate_stages": [],
        }

    def test_legacy_emma_three_receipts_are_preserved_and_nonblocking(self) -> None:
        old_control = "00000000-0000-0000-0000-000000000999"
        for stage in (
            "mute_posts_verified", "mute_stories_verified", "return_ct_exact"
        ):
            self._journal_bound(path=self.path, control_id=old_control, stage=stage)
        with mock.patch.object(outbox, "log") as log_call, mock.patch(
            "supabase_client.persist_follow_60s_post_follow_v2"
        ) as rpc:
            result = outbox.flush_pending(
                active_binding=self.active_binding, path=self.path
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["pending"], 0)
        self.assertEqual(result["historical_pending"], 3)
        self.assertEqual(result["total_pending"], 3)
        self.assertEqual(outbox.pending_count(self.path), 3)
        rpc.assert_not_called()
        self.assertEqual(
            log_call.call_args.args[:2],
            ("info", "historical_outbox_receipt_excluded_from_active_binding"),
        )

    def test_exact_binding_replays_without_cross_account_or_history_leak(self) -> None:
        self._journal_bound(
            path=self.path,
            control_id="00000000-0000-0000-0000-000000000999",
            action_id="00000000-0000-0000-0000-000000000901",
        )
        self._journal_bound(
            path=self.path,
            control_id=self.active_binding["control_id"],
            account_id="00000000-0000-0000-0000-000000000777",
            action_id="00000000-0000-0000-0000-000000000902",
        )
        self._journal_bound(
            path=self.path,
            control_id=self.active_binding["control_id"],
        )
        with mock.patch(
            "supabase_client.persist_follow_60s_post_follow_v2",
            return_value=self._rpc_success("like_verified"),
        ) as rpc:
            result = outbox.flush_pending(
                active_binding=self.active_binding, path=self.path
            )
        self.assertTrue(result["ok"])
        rpc.assert_called_once()
        self.assertEqual(rpc.call_args.kwargs["account_id"], self.binding["account_id"])
        self.assertEqual(result["historical_pending"], 1)
        self.assertEqual(result["other_account_pending"], 1)
        self.assertEqual(result["total_pending"], 2)

    def test_exact_binding_rpc_error_remains_blocking(self) -> None:
        self._journal_bound(
            path=self.path, control_id=self.active_binding["control_id"]
        )
        with mock.patch(
            "supabase_client.persist_follow_60s_post_follow_v2",
            side_effect=RuntimeError("rpc_down"),
        ):
            result = outbox.flush_pending(
                active_binding=self.active_binding, path=self.path
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "rpc_down")
        self.assertEqual(outbox.pending_count(self.path), 1)

    def test_same_control_binding_mismatches_fail_closed(self) -> None:
        cases = (
            ("run_id", {"run_id": "00000000-0000-0000-0000-000000000201"}),
            ("request_id", {"request_id": "00000000-0000-0000-0000-000000000202"}),
            ("worker_sha", {"worker_sha": "b" * 40}),
        )
        for index, (field, overrides) in enumerate(cases):
            with self.subTest(field=field):
                path = Path(self.tmp.name) / f"mismatch-{index}.sqlite3"
                self._journal_bound(
                    path=path,
                    control_id=self.active_binding["control_id"],
                    **overrides,
                )
                with mock.patch(
                    "supabase_client.persist_follow_60s_post_follow_v2"
                ) as rpc:
                    result = outbox.flush_pending(
                        active_binding=self.active_binding, path=path
                    )
                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["reason"], f"follow60_active_binding_{field}_mismatch"
                )
                rpc.assert_not_called()
                self.assertEqual(outbox.pending_count(path), 1)

    def test_post_cycle_flush_is_scoped_to_current_action(self) -> None:
        second_action = "00000000-0000-0000-0000-000000000203"
        self._journal_bound(
            path=self.path, control_id=self.active_binding["control_id"]
        )
        self._journal_bound(
            path=self.path,
            control_id=self.active_binding["control_id"],
            action_id=second_action,
        )
        active_action_hash = outbox.action_id_hash(self.binding["action_id"])
        with mock.patch(
            "supabase_client.persist_follow_60s_post_follow_v2",
            return_value=self._rpc_success("like_verified"),
        ) as rpc:
            result = outbox.flush_pending(
                active_binding=self.active_binding,
                action_id_hash_value=active_action_hash,
                path=self.path,
            )
        self.assertTrue(result["ok"])
        rpc.assert_called_once()
        self.assertEqual(result["pending"], 0)
        self.assertEqual(result["other_action_pending"], 1)
        self.assertEqual(result["total_pending"], 1)

    def test_active_binding_is_mandatory_before_any_rpc(self) -> None:
        self._journal_bound(
            path=self.path, control_id=self.active_binding["control_id"]
        )
        with mock.patch("supabase_client.persist_follow_60s_post_follow_v2") as rpc:
            with self.assertRaisesRegex(
                ValueError, "follow60_active_outbox_binding_missing_or_invalid"
            ):
                outbox.flush_pending(active_binding={}, path=self.path)
        rpc.assert_not_called()


class Follow60BindingAndPostGridV2Test(unittest.TestCase):
    def tearDown(self) -> None:
        configure_canary(canary,
            account_id="other",
            account_username="other",
            run_id="reset",
            package="com.instagram.android",
            resume_policy=None,
        )

    def test_missing_account_session_binding_fails_before_device_action(self) -> None:
        device = mock.MagicMock()
        code = runner._run_followers_list_engine_session(
            device,
            source_profile_username="source_ct",
            account_id=TEST_CANARY_ACCOUNT_ID,
            run_id="run-1",
            supabase_mode=True,
            warm_session_used=False,
            force_stop_used=False,
            run_request_id=None,
            follow60_canary_active=True,
            follow60_canary_control={"status": "armed"},
            follow60_attempt_id=1,
            business_session_id="business-1",
        )
        self.assertEqual(code, 96)
        device.assert_not_called()

    def test_later_follow_persistence_cannot_mask_failed_post_follow_barrier(self) -> None:
        self.assertFalse(runner._critical_persistence_chain_ok(False, True))
        self.assertFalse(runner._critical_persistence_chain_ok(True, False))
        self.assertFalse(runner._critical_persistence_chain_ok(False, False))
        self.assertTrue(runner._critical_persistence_chain_ok(True, True))

    def test_account_session_rotation_propagates_exact_canary_binding(self) -> None:
        calls: list[dict] = []

        def engine(_device, **kwargs):
            calls.append(dict(kwargs))
            engine.last_session_summary = {
                "follows_completed_count": 1,
                "follow_session_outcome": "global_follow_cap_reached",
                "follow_stop_reason": "global_follow_cap_reached",
            }
            return 0

        engine.last_session_summary = {}
        control = {
            "status": "armed", "binding_valid": True,
            "account_id": TEST_CANARY_ACCOUNT_ID, "run_id": "run-1",
            "request_id": "request-1", "attempt_id": 1,
            "business_session_id": "business-1",
        }
        result = account_session._run_follow_target_rotation(
            object(),
            account_id=TEST_CANARY_ACCOUNT_ID,
            account_username=TEST_CANARY_USERNAME,
            run_id="run-1",
            follow_targets=[{"target_id": "target-1", "source_profile": "ct"}],
            run_followers_list_engine_session=engine,
            supabase_mode=False,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=1,
            run_request_id="request-1",
            follow60_canary_active=True,
            follow60_canary_control=control,
            follow60_attempt_id=1,
            business_session_id="business-1",
        )
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["run_request_id"], "request-1")
        self.assertIs(calls[0]["follow60_canary_active"], True)
        self.assertEqual(calls[0]["follow60_canary_control"], control)
        self.assertEqual(calls[0]["business_session_id"], "business-1")

    def test_dispatch_wrapper_forwards_exact_canary_binding(self) -> None:
        control = {"status": "armed", "binding_valid": True}
        with mock.patch.object(account_session, "run_account_session", return_value=0) as run:
            code = account_session.dispatch_account_session(
                object(), account_id=TEST_CANARY_ACCOUNT_ID,
                account_username=TEST_CANARY_USERNAME,
                run_id="run-1", run_request_id="request-1",
                source_profile_username="ct",
                run_followers_list_engine_session=mock.Mock(),
                supabase_mode=False,
                warm_session_used=False,
                force_stop_used=False,
                follow60_canary_active=True,
                follow60_canary_control=control,
                follow60_attempt_id=1,
                business_session_id="business-1",
            )
        self.assertEqual(code, 0)
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["run_request_id"], "request-1")
        self.assertIs(kwargs["follow60_canary_active"], True)
        self.assertEqual(kwargs["follow60_canary_control"], control)
        self.assertEqual(kwargs["business_session_id"], "business-1")

    def test_typed_evidence_preserves_every_generation_and_geometry_field(self) -> None:
        configure_canary(canary,
            account_id=TEST_CANARY_ACCOUNT_ID,
            account_username=TEST_CANARY_USERNAME,
            run_id="run-typed",
            package="com.instagram.android",
            resume_policy=None,
        )
        ev = canary.stash_post_grid_evidence(
            candidate_username="candidate",
            package_name="com.instagram.android",
            activity_name="InstagramMainActivity",
            navigation_generation="7",
            viewport_fingerprint="viewport-1",
            outcome=canary.POST_ROW_POSITIVE_SAFE,
            mute_sheet_closed=True,
            mute_posts_verified=True,
            mute_stories_verified=True,
            profile_identity_method="final_mute_close_exact_action_bar_xml",
            screen_width=1080,
            screen_height=2340,
            grid_tab_state="selected_or_physical_row",
            reels_tab_state="not_selected",
            tagged_tab_state="not_selected",
            post_count_positive=True,
            physical_post_cells=[{"left": 0, "top": 900, "right": 360, "bottom": 1260}],
            first_post_bounds={"left": 0, "top": 900, "right": 360, "bottom": 1260},
            first_post_cell_source="fresh_final_mute_close_xml_physical_cell",
            no_posts_positive=False,
        )
        self.assertIsNotNone(ev)
        consumed, _, reason = canary.consume_post_grid_evidence(
            candidate_username="candidate",
            package="com.instagram.android",
            activity="InstagramMainActivity",
            navigation_generation="7",
            viewport_fingerprint="viewport-1",
            screen_size=(1080, 2340),
        )
        self.assertEqual(reason, "")
        self.assertEqual(consumed.outcome, canary.POST_ROW_POSITIVE_SAFE)
        self.assertEqual(consumed.package_name, "com.instagram.android")
        self.assertEqual(consumed.activity_name, "InstagramMainActivity")
        self.assertEqual(len(consumed.physical_post_cells), 1)
        self.assertEqual(consumed.first_post_bounds["top"], 900)
        self.assertEqual(consumed.scroll_generation, 0)
        self.assertEqual(consumed.first_post_cell_source, "fresh_final_mute_close_xml_physical_cell")

    def test_direct_evidence_rejects_wrong_package_or_non_main_activity(self) -> None:
        configure_canary(canary,
            account_id=TEST_CANARY_ACCOUNT_ID,
            account_username=TEST_CANARY_USERNAME,
            run_id="run-typed-negative",
            package="com.instagram.android",
            resume_policy=None,
        )
        common = {
            "candidate_username": "candidate",
            "navigation_generation": "1",
            "viewport_fingerprint": "viewport-1",
            "outcome": canary.POST_ROW_POSITIVE_SAFE,
            "mute_sheet_closed": True,
            "mute_posts_verified": True,
            "mute_stories_verified": True,
            "profile_identity_method": "final_mute_close_exact_action_bar_xml",
            "screen_width": 1080,
            "screen_height": 2340,
            "grid_tab_state": "selected_or_physical_row",
            "post_count_positive": True,
            "physical_post_cells": [{"left": 0, "top": 900, "right": 360, "bottom": 1260}],
            "first_post_bounds": {"left": 0, "top": 900, "right": 360, "bottom": 1260},
        }
        self.assertIsNone(canary.stash_post_grid_evidence(
            package_name="com.instagram.clone", activity_name="InstagramMainActivity",
            **common,
        ))
        self.assertIsNone(canary.stash_post_grid_evidence(
            package_name="com.instagram.android", activity_name="TransparentModalActivity",
            **common,
        ))

    def test_clipped_classification_ttl_accepts_1350_1450_1700ms_for_reveal_only(self) -> None:
        for age_ms in (1350.0, 1450.0, 1700.0):
            configure_canary(
                canary,
                account_id=TEST_CANARY_ACCOUNT_ID,
                account_username=TEST_CANARY_USERNAME,
                run_id=f"run-clipped-{int(age_ms)}",
                package="com.instagram.android",
                resume_policy=None,
            )
            with mock.patch.object(canary.time, "monotonic", return_value=100.0):
                ev = canary.stash_post_grid_evidence(
                    candidate_username="candidate",
                    package_name="com.instagram.android",
                    activity_name="InstagramMainActivity",
                    navigation_generation="7",
                    viewport_fingerprint="viewport-1",
                    outcome=canary.POST_ROW_POSITIVE_BUT_CLIPPED,
                    mute_sheet_closed=True,
                    mute_posts_verified=True,
                    mute_stories_verified=True,
                    profile_identity_method="exact_profile",
                    screen_width=1080,
                    screen_height=2340,
                    grid_tab_state="selected",
                    post_count_positive=True,
                    physical_post_cells=[{"left": 0, "top": 2200, "right": 360, "bottom": 2330}],
                    first_post_bounds={"left": 0, "top": 2200, "right": 360, "bottom": 2330},
                    ttl_ms=3000.0,
                    classification_reveal_ttl_ms=3000.0,
                )
            self.assertIsNotNone(ev)
            with mock.patch.object(
                canary.time, "monotonic", return_value=100.0 + age_ms / 1000.0
            ):
                consumed, measured_age, reason = canary.consume_post_grid_evidence(
                    candidate_username="candidate",
                    package="com.instagram.android",
                    activity="InstagramMainActivity",
                    navigation_generation="7",
                    viewport_fingerprint="viewport-1",
                    screen_size=(1080, 2340),
                )
            self.assertEqual(reason, "")
            self.assertIsNotNone(consumed)
            self.assertAlmostEqual(measured_age, age_ms, delta=0.5)
            self.assertEqual(
                consumed.outcome, canary.POST_ROW_POSITIVE_BUT_CLIPPED
            )

    def test_clipped_classification_reveal_is_rejected_after_navigation(self) -> None:
        configure_canary(
            canary,
            account_id=TEST_CANARY_ACCOUNT_ID,
            account_username=TEST_CANARY_USERNAME,
            run_id="run-clipped-navigation",
            package="com.instagram.android",
            resume_policy=None,
        )
        canary.stash_post_grid_evidence(
            candidate_username="candidate",
            package_name="com.instagram.android",
            activity_name="InstagramMainActivity",
            outcome=canary.POST_ROW_POSITIVE_BUT_CLIPPED,
            mute_sheet_closed=True,
            mute_posts_verified=True,
            mute_stories_verified=True,
            profile_identity_method="exact_profile",
            screen_width=1080,
            screen_height=2340,
            grid_tab_state="selected",
            post_count_positive=True,
            physical_post_cells=[{"left": 0, "top": 2200, "right": 360, "bottom": 2330}],
            first_post_bounds={"left": 0, "top": 2200, "right": 360, "bottom": 2330},
            ttl_ms=3000.0,
            navigation_generation="7",
            viewport_fingerprint="viewport-1",
        )
        canary.invalidate("back_navigation")
        consumed, _, reason = canary.consume_post_grid_evidence(
            candidate_username="candidate",
            package="com.instagram.android",
            activity="InstagramMainActivity",
            screen_size=(1080, 2340),
        )
        self.assertIsNone(consumed)
        self.assertEqual(reason, "missing_evidence")

    def test_clipped_row_gets_one_reveal_one_xml_and_no_happy_path_screenshot(self) -> None:
        device = mock.MagicMock()
        device.dump_hierarchy.return_value = """<hierarchy>
          <node text="candidate"/><node resource-id="profile_tabs_container" bounds="[0,700][1080,820]"/>
          <node content-desc="Profile tab grid" selected="true" bounds="[0,700][360,820]"/>
          <node class="android.widget.ImageView" content-desc="Post thumbnail" bounds="[0,900][360,1260]"/>
        </hierarchy>"""
        clipped = {
            "outcome": canary.POST_ROW_POSITIVE_BUT_CLIPPED,
            "candidate_username": "candidate",
            "identity_exact": True,
            "profile_tabs_present": True,
            "grid_selected": True,
            "tabs_bottom": 820,
            "loading_visible": False,
            "private_profile_visible": False,
            "reels_or_tagged_selected": False,
            "suggested_overlay_visible": False,
        }
        with mock.patch.object(
            nav, "_post_follow_likes_profile_scroll_swipe", return_value={"swipe_ok": True}
        ) as reveal, mock.patch.object(
            nav, "_post_follow_likes_probe_top_left_vision_cell_meta"
        ) as vision:
            out = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device, clipped, ww=1080, wh=2340, candidate_username="candidate"
            )
        reveal.assert_called_once()
        device.dump_hierarchy.assert_called_once()
        vision.assert_not_called()
        self.assertEqual(out["outcome"], canary.POST_ROW_POSITIVE_SAFE)
        self.assertEqual(out["post_bounds_source"], "single_reveal_fresh_xml_physical_cell")

    def test_operator_capture_structure_classifies_clipped_without_fixed_coordinates(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / (
            "follow60_postgrid_clipped_after_mute_v1.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertIs(fixture["fixed_tap_coordinates"], False)
        ratios = fixture["geometry_ratios"]
        username = fixture["profile"]["candidate_token"]
        post_count = int(fixture["profile"]["post_count"])

        for width, height in ((1080, 2340), (1440, 3120)):
            with self.subTest(viewport=(width, height)):
                def y(name: str) -> int:
                    return int(round(float(ratios[name]) * height))

                cell_right = int(round(float(ratios["cell_width_x"]) * width))
                xml = f"""<hierarchy>
                  <node text="{username}"/>
                  <node text="{post_count} posts"/>
                  <node resource-id="profile_tabs_container"
                        bounds="[0,{y('tabs_top_y')}][{width},{y('tabs_bottom_y')}]"/>
                  <node content-desc="Profile tab grid" selected="true"
                        bounds="[0,{y('tabs_top_y')}][{cell_right},{y('tabs_bottom_y')}]"/>
                  <node class="android.widget.ImageView" content-desc="Post thumbnail"
                        bounds="[0,{y('partial_media_top_y')}][{cell_right},{y('partial_media_bottom_y')}]"/>
                  <node resource-id="com.instagram.android:id/main_tab_bar"
                        bounds="[0,{y('bottom_navigation_top_y')}][{width},{height}]"/>
                </hierarchy>"""
                out = nav._post_follow_post_grid_evidence_from_xml(
                    xml,
                    candidate_username=username,
                    ww=width,
                    wh=height,
                )
                self.assertEqual(out["outcome"], canary.POST_ROW_POSITIVE_BUT_CLIPPED)
                self.assertTrue(out["clipped_detected"])
                self.assertFalse(out["tap_safe"])
                self.assertEqual(out["fully_exploitable_bottom"], y("bottom_navigation_top_y"))
                self.assertEqual(out["post_bounds"]["right"], cell_right)

    def test_short_media_strip_without_full_positive_contract_stays_ambiguous(self) -> None:
        xml = """<hierarchy>
          <node text="candidate"/>
          <node resource-id="profile_tabs_container" bounds="[0,1700][1080,1900]"/>
          <node content-desc="Profile tab grid" selected="true" bounds="[0,1700][360,1900]"/>
          <node class="android.widget.ImageView" content-desc="Post thumbnail" bounds="[0,2080][360,2140]"/>
          <node resource-id="main_tab_bar" bounds="[0,2140][1080,2340]"/>
        </hierarchy>"""
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertEqual(out["outcome"], canary.POST_GRID_AMBIGUOUS_FINAL)
        self.assertEqual(out["rejection_reason"], "post_grid_ambiguous")

    def test_clipped_reacquisition_not_safe_goes_direct_golden_without_vision(self) -> None:
        device = mock.MagicMock()
        device.dump_hierarchy.return_value = """<hierarchy>
          <node text="candidate"/><node text="17 posts"/>
          <node resource-id="profile_tabs_container" bounds="[0,700][1080,820]"/>
          <node content-desc="Profile tab grid" selected="true" bounds="[0,700][360,820]"/>
        </hierarchy>"""
        clipped = {
            "outcome": canary.POST_ROW_POSITIVE_BUT_CLIPPED,
            "candidate_username": "candidate",
            "identity_exact": True,
            "profile_tabs_present": True,
            "grid_selected": True,
            "tabs_bottom": 820,
            "loading_visible": False,
            "private_profile_visible": False,
            "reels_or_tagged_selected": False,
        }
        with mock.patch.object(
            nav,
            "_post_follow_likes_profile_scroll_swipe",
            return_value={"swipe_ok": True, "scroll_distance_px": 311},
        ) as reveal, mock.patch.object(
            nav, "_post_follow_likes_probe_top_left_vision_cell_meta"
        ) as vision:
            out = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device, clipped, ww=1080, wh=2340, candidate_username="candidate"
            )
        reveal.assert_called_once()
        device.dump_hierarchy.assert_called_once()
        vision.assert_not_called()
        self.assertEqual(out["outcome"], canary.POST_GRID_AMBIGUOUS_FINAL)
        self.assertEqual(out["reveal_count_total_for_like_phase"], 1)
        self.assertEqual(out["reveal_distance_px"], 311)
        self.assertEqual(out["golden_fallback_reason"], "clipped_reacquisition_not_tap_safe")

    def test_like_phase_reveal_budget_allows_at_most_one_scroll_total(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "_followers_log_scroll_or_swipe_about_to_run", return_value=None
        ):
            nav._post_follow_like_reveal_budget_begin(initial_count=1)
            blocked = nav._post_follow_likes_profile_scroll_swipe(
                device,
                scroll_profile="reveal_moderate",
                ww=1080,
                wh=2340,
            )
            exhausted = nav._post_follow_like_reveal_budget_end()
            self.assertFalse(blocked["swipe_ok"])
            self.assertEqual(blocked["error"], "reveal_budget_exhausted")
            self.assertEqual(device.swipe.call_count, 0)
            self.assertEqual(exhausted["reveal_count_total_for_like_phase"], 1)

            nav._post_follow_like_reveal_budget_begin(initial_count=0)
            first = nav._post_follow_likes_profile_scroll_swipe(
                device,
                scroll_profile="reveal_moderate",
                ww=1080,
                wh=2340,
            )
            second = nav._post_follow_likes_profile_scroll_swipe(
                device,
                scroll_profile="reveal_moderate",
                ww=1080,
                wh=2340,
            )
            used = nav._post_follow_like_reveal_budget_end()
        self.assertTrue(first["swipe_ok"])
        self.assertFalse(second["swipe_ok"])
        self.assertEqual(device.swipe.call_count, 1)
        self.assertEqual(used["reveal_count_total_for_like_phase"], 1)
        self.assertEqual(used["reveal_attempts_blocked"], 1)


if __name__ == "__main__":
    unittest.main()
