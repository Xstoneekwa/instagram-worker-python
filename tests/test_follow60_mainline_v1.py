from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import follow_60s_canary as follow60
import post_follow_stage_outbox as outbox


SHA = "4e05e1016497c15882286601f0b4fc837d691a68"
ACCOUNT = "11111111-1111-4111-8111-111111111111"
RUN = "22222222-2222-4222-8222-222222222222"
REQUEST = "33333333-3333-4333-8333-333333333333"


class Follow60MainlineRuntimeTests(unittest.TestCase):
    def test_normal_run_requires_no_canary_control_and_has_no_barrier(self) -> None:
        self.assertTrue(follow60.configure_mainline(
            account_id=ACCOUNT, account_username="future_account", run_id=RUN,
            request_id=REQUEST, business_session_id="session-1",
            package="com.instagram.android", worker_sha=SHA,
        ))
        follow60.install_activation_components(
            opening_composite=lambda: None, pre_tap_callback=lambda: None,
            post_cycle_callback=lambda: False, stage_receipts=lambda: None,
            barrier=lambda: False,
        )
        context = follow60.runtime_context()
        self.assertEqual(context["runtime_mode"], "mainline")
        self.assertEqual(context["binding_kind"], "mainline")
        self.assertEqual(context["control_id"], RUN)
        self.assertEqual(context["binding_version"], "FOLLOW60_MAINLINE_BINDING_V1")
        self.assertTrue(follow60.enabled_for_account(ACCOUNT))
        self.assertTrue(all(follow60.activation_component_status().values()))

    def test_mainline_is_generic_and_fail_closed_on_incomplete_binding(self) -> None:
        self.assertFalse(follow60.configure_mainline(
            account_id=ACCOUNT, account_username="new_account", run_id=RUN,
            request_id="", business_session_id="session-1",
            package="com.instagram.android", worker_sha=SHA,
        ))
        self.assertFalse(follow60.enabled())


class Follow60MainlineOutboxTests(unittest.TestCase):
    def test_mainline_uses_v3_and_never_reports_a_canary_barrier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.sqlite3"
            payload = {
                "binding_kind": "mainline", "control_id": RUN,
                "worker_sha": SHA, "proof_type": "verified",
                "like_terminal_status": "safe_skip",
                "like_terminal_reason": "no_posts_verified",
            }
            for stage in ("mute_posts_verified", "mute_stories_verified", "return_ct_exact"):
                outbox.journal_stage(
                    account_id=ACCOUNT, original_run_id=RUN, request_id=REQUEST,
                    action_id="candidate-action", stage=stage,
                    candidate_username="candidate", source_profile="source",
                    attempt_id=1, business_session_id="session-1",
                    verified_at="2026-08-04T20:00:00+00:00", payload=payload, path=path,
                )
            persist = {
                "ok": True, "binding_valid": True,
                "inserted_stages": ["mute_posts_verified", "mute_stories_verified", "return_ct_exact"],
                "duplicate_stages": [],
            }
            ledger = {
                "ok": True, "schema": "FOLLOW60_MAINLINE_CYCLE_LEDGER_V2",
                "barrier_reached": False, "next_candidate_permitted": True,
            }
            with mock.patch("supabase_client.persist_follow60_post_follow_v3", return_value=persist) as persist_rpc, \
                 mock.patch("supabase_client.ack_follow60_completed_cycle_v2", return_value=ledger) as ledger_rpc:
                result = outbox.flush_pending(
                    active_binding={
                        "binding_kind": "mainline", "account_id": ACCOUNT,
                        "run_id": RUN, "request_id": REQUEST,
                        "control_id": RUN, "worker_sha": SHA,
                    }, path=path,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["pending"], 0)
            self.assertEqual(persist_rpc.call_args.kwargs["binding_id"], RUN)
            self.assertEqual(ledger_rpc.call_args.kwargs["binding_kind"], "mainline")
            self.assertFalse(result["latest_ledger_ack"]["barrier_reached"])

    def test_partial_mute_receipts_persist_without_completed_cycle_ack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.sqlite3"
            payload = {
                "binding_kind": "mainline",
                "control_id": RUN,
                "worker_sha": SHA,
                "proof_type": "verified",
                "like_terminal_status": "safe_skip",
                "like_terminal_reason": "required_mute_verification_incomplete",
            }
            for stage in ("mute_posts_verified", "return_ct_exact"):
                outbox.journal_stage(
                    account_id=ACCOUNT,
                    original_run_id=RUN,
                    request_id=REQUEST,
                    action_id="candidate-action",
                    stage=stage,
                    candidate_username="candidate",
                    source_profile="source",
                    attempt_id=1,
                    business_session_id="session-1",
                    verified_at="2026-08-05T20:00:00+00:00",
                    payload=payload,
                    path=path,
                )
            persist = {
                "ok": True,
                "binding_valid": True,
                "inserted_stages": ["mute_posts_verified", "return_ct_exact"],
                "duplicate_stages": [],
            }
            with mock.patch(
                "supabase_client.persist_follow60_post_follow_v3",
                return_value=persist,
            ) as persist_rpc, mock.patch(
                "supabase_client.ack_follow60_completed_cycle_v2"
            ) as ledger_rpc:
                result = outbox.flush_pending(
                    active_binding={
                        "binding_kind": "mainline",
                        "account_id": ACCOUNT,
                        "run_id": RUN,
                        "request_id": REQUEST,
                        "control_id": RUN,
                        "worker_sha": SHA,
                    },
                    path=path,
                )
            self.assertFalse(result["ok"])
            self.assertEqual(
                result["reason"],
                "follow60_cycle_incomplete_missing_required_mute_stage",
            )
            self.assertTrue(result["partial_receipts_persisted"])
            self.assertEqual(
                result["missing_required_stages"], ["mute_stories_verified"]
            )
            self.assertEqual(outbox.pending_count(path), 0)
            self.assertIs(persist_rpc.call_args.kwargs["cycle_complete"], False)
            ledger_rpc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
