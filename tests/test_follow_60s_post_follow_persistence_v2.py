from __future__ import annotations

import os
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


class Follow60PostFollowOutboxV2Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "outbox.sqlite3"
        self.binding = {
            "account_id": canary.CANARY_ACCOUNT_ID,
            "original_run_id": "00000000-0000-0000-0000-000000000101",
            "request_id": "00000000-0000-0000-0000-000000000102",
            "action_id": "00000000-0000-0000-0000-000000000103",
            "candidate_username": "candidate",
            "source_profile": "source_ct",
            "attempt_id": 1,
            "business_session_id": "business-session-1",
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _journal(self, stage: str, payload: dict | None = None) -> dict:
        return outbox.journal_stage(
            **self.binding,
            stage=stage,
            verified_at="2026-07-31T20:00:00+00:00",
            payload=payload or {},
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
        with mock.patch(
            "supabase_client.persist_follow_60s_post_follow_v2",
            return_value=response,
        ) as rpc:
            result = outbox.flush_pending(path=self.path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["pending"], 0)
        rpc.assert_called_once()
        kwargs = rpc.call_args.kwargs
        self.assertEqual(kwargs["run_id"], self.binding["original_run_id"])
        self.assertEqual(kwargs["request_id"], self.binding["request_id"])
        self.assertEqual(kwargs["business_session_id"], "business-session-1")
        self.assertIs(kwargs["cycle_complete"], True)
        self.assertEqual([item["stage"] for item in kwargs["stages"]], list(outbox.VALID_STAGES))

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
            result = outbox.flush_pending(path=self.path)
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
                    outbox.journal_stage(
                        **self.binding,
                        stage=stage,
                        verified_at="2026-07-31T20:00:00+00:00",
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
                ) as rpc:
                    result = outbox.flush_pending(path=path)
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
                outbox.flush_pending(path=self.path)
        self.assertEqual(outbox.pending_count(self.path), 1)
        duplicate = {
            "ok": True, "binding_valid": True,
            "inserted_stages": [], "duplicate_stages": ["like_verified"],
        }
        with mock.patch(
            "supabase_client.persist_follow_60s_post_follow_v2",
            return_value=duplicate,
        ) as rpc:
            replay = outbox.flush_pending(path=self.path)
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
            result = outbox.flush_pending(path=self.path)
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


class Follow60BindingAndPostGridV2Test(unittest.TestCase):
    def tearDown(self) -> None:
        canary.configure(
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
            account_id=canary.CANARY_ACCOUNT_ID,
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
            "account_id": canary.CANARY_ACCOUNT_ID, "run_id": "run-1",
            "request_id": "request-1", "attempt_id": 1,
            "business_session_id": "business-1",
        }
        result = account_session._run_follow_target_rotation(
            object(),
            account_id=canary.CANARY_ACCOUNT_ID,
            account_username=canary.CANARY_ACCOUNT_USERNAME,
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
                object(), account_id=canary.CANARY_ACCOUNT_ID,
                account_username=canary.CANARY_ACCOUNT_USERNAME,
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
        canary.configure(
            account_id=canary.CANARY_ACCOUNT_ID,
            account_username=canary.CANARY_ACCOUNT_USERNAME,
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
        canary.configure(
            account_id=canary.CANARY_ACCOUNT_ID,
            account_username=canary.CANARY_ACCOUNT_USERNAME,
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


if __name__ == "__main__":
    unittest.main()
