from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib import error, request
from unittest import mock

import follow_persistence_intent
import follow_persistence_rpc
import runner
import supabase_client


ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"
REQUEST_ID = "33333333-3333-4333-8333-333333333333"
TARGET_ID = "44444444-4444-4444-8444-444444444444"
INTERACTION_ID = "55555555-5555-4555-8555-555555555555"
SETTINGS_REVISION = "2026-07-19T10:00:00+00:00"
FOLLOWED_AT = "2026-07-19T10:01:00+00:00"


def rpc_success(action_id: str, status: str = "created") -> dict:
    return {
        "ok": True,
        "status": status,
        "action_id": action_id,
        "interaction_id": INTERACTION_ID,
        "follow_persisted": True,
        "eligible_unfollow_at": "2026-07-22T10:01:00+00:00",
        "audit_persisted": True,
        "counter_applied": True,
        "settings_revision_match": True,
        "invariants_confirmed": sorted(follow_persistence_rpc.REQUIRED_INVARIANTS),
        "failure_reason": None,
    }


class FollowPersistenceContractTest(unittest.TestCase):
    def test_flag_defaults_off_and_accepts_explicit_true(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(follow_persistence_rpc.rpc_v1_enabled())
        with mock.patch.dict(os.environ, {"FOLLOW_PERSISTENCE_RPC_V1_ENABLED": "true"}):
            self.assertTrue(follow_persistence_rpc.rpc_v1_enabled())

    def test_action_id_is_deterministic_and_case_insensitive(self) -> None:
        lower = follow_persistence_rpc.deterministic_action_id(ACCOUNT_ID, RUN_ID, "candidate")
        upper = follow_persistence_rpc.deterministic_action_id(ACCOUNT_ID, RUN_ID, "@Candidate")
        self.assertEqual(lower, upper)

    def test_response_requires_exact_action_and_all_invariants(self) -> None:
        action_id = follow_persistence_rpc.deterministic_action_id(ACCOUNT_ID, RUN_ID, "candidate")
        self.assertEqual(
            follow_persistence_rpc.validate_rpc_response(
                rpc_success(action_id), expected_action_id=action_id
            ),
            (True, "ok"),
        )
        wrong = rpc_success("66666666-6666-4666-8666-666666666666")
        self.assertEqual(
            follow_persistence_rpc.validate_rpc_response(wrong, expected_action_id=action_id)[1],
            "response_action_id_mismatch",
        )
        partial = rpc_success(action_id)
        partial["audit_persisted"] = False
        self.assertEqual(
            follow_persistence_rpc.validate_rpc_response(partial, expected_action_id=action_id)[1],
            "response_audit_persisted_false",
        )


class FollowPersistenceIntentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ, {"FOLLOW_PERSISTENCE_INTENT_ROOT": self.tmp.name}
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _prepared(self) -> tuple[str, dict]:
        action_id = follow_persistence_rpc.deterministic_action_id(ACCOUNT_ID, RUN_ID, "Candidate")
        intent = follow_persistence_intent.create_prepared_intent(
            action_id=action_id,
            account_id=ACCOUNT_ID,
            run_id=RUN_ID,
            request_id=REQUEST_ID,
            candidate_username="@Candidate",
            source_target_id=TARGET_ID,
            source_ct_username="@Source",
            settings_revision=SETTINGS_REVISION,
        )
        return action_id, intent

    def test_intent_is_atomic_redacted_and_mode_600(self) -> None:
        action_id, intent = self._prepared()
        path = Path(self.tmp.name) / RUN_ID / f"{action_id}.json"
        self.assertEqual(intent["stage"], "prepared_before_follow_tap")
        self.assertEqual(intent["candidate_username"], "candidate")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        raw = path.read_text(encoding="utf-8")
        self.assertNotIn("xml", raw.lower())
        self.assertNotIn("service_role", raw.lower())

    def test_nonterminal_load_and_terminal_transition(self) -> None:
        action_id, _ = self._prepared()
        self.assertEqual(len(follow_persistence_intent.load_nonterminal_intents(
            account_id=ACCOUNT_ID, run_id=RUN_ID
        )), 1)
        follow_persistence_intent.update_intent_stage(
            run_id=RUN_ID,
            action_id=action_id,
            stage="follow_physically_verified",
            followed_at=FOLLOWED_AT,
        )
        self.assertEqual(len(follow_persistence_intent.load_nonterminal_intents(
            account_id=ACCOUNT_ID, run_id=RUN_ID
        )), 1)
        follow_persistence_intent.update_intent_stage(
            run_id=RUN_ID, action_id=action_id, stage="persisted"
        )
        self.assertEqual(follow_persistence_intent.load_nonterminal_intents(
            account_id=ACCOUNT_ID, run_id=RUN_ID
        ), [])


class FollowPersistenceWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ,
            {
                "FOLLOW_PERSISTENCE_INTENT_ROOT": self.tmp.name,
                "FOLLOW_PERSISTENCE_RPC_V1_ENABLED": "true",
            },
        )
        self.env.start()
        self.action_id = follow_persistence_rpc.deterministic_action_id(
            ACCOUNT_ID, RUN_ID, "candidate"
        )
        follow_persistence_intent.create_prepared_intent(
            action_id=self.action_id,
            account_id=ACCOUNT_ID,
            run_id=RUN_ID,
            request_id=REQUEST_ID,
            candidate_username="candidate",
            source_target_id=TARGET_ID,
            source_ct_username="source",
            settings_revision=SETTINGS_REVISION,
        )
        follow_persistence_intent.update_intent_stage(
            run_id=RUN_ID,
            action_id=self.action_id,
            stage="follow_physically_verified",
            followed_at=FOLLOWED_AT,
        )

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _persist(self) -> bool:
        return runner._persist_verified_follow_success_to_supabase(
            supabase_mode=True,
            account_id=ACCOUNT_ID,
            follower_un="candidate",
            source_profile_username="source",
            run_id=RUN_ID,
            follow_out={"skipped_tap": False},
            fs_af="following",
            f_st="following",
            target_id=TARGET_ID,
            phase="after_post_follow",
            request_id=REQUEST_ID,
            action_id=self.action_id,
            settings_revision_expected=SETTINGS_REVISION,
            followed_at=FOLLOWED_AT,
        )

    def test_flag_off_uses_legacy_without_rpc(self) -> None:
        with mock.patch.dict(
            os.environ, {"FOLLOW_PERSISTENCE_RPC_V1_ENABLED": "false"}
        ), mock.patch.object(
            supabase_client, "persist_verified_follow_success_rpc"
        ) as rpc, mock.patch.object(
            runner, "_timed_safe_supabase_call", return_value={"ok": True}
        ) as legacy:
            self.assertTrue(self._persist())
        rpc.assert_not_called()
        self.assertIn(
            "record_follow_interaction_outcome",
            [call.args[1] for call in legacy.call_args_list],
        )
        self.assertEqual(
            follow_persistence_intent.load_nonterminal_intents(
                account_id=ACCOUNT_ID, run_id=RUN_ID
            ),
            [],
        )

    def test_canary_flag_off_forces_account_scoped_idempotent_rpc(self) -> None:
        canary_account_id = runner.FOLLOW_60S_CANARY_ACCOUNT_ID
        canary_run_id = "77777777-7777-4777-8777-777777777777"
        action_id = follow_persistence_rpc.deterministic_action_id(
            canary_account_id, canary_run_id, "candidate"
        )
        follow_persistence_intent.create_prepared_intent(
            action_id=action_id,
            account_id=canary_account_id,
            run_id=canary_run_id,
            request_id=REQUEST_ID,
            candidate_username="candidate",
            source_target_id=TARGET_ID,
            source_ct_username="source",
            settings_revision=SETTINGS_REVISION,
        )
        follow_persistence_intent.update_intent_stage(
            run_id=canary_run_id,
            action_id=action_id,
            stage="follow_physically_verified",
            followed_at=FOLLOWED_AT,
        )
        with mock.patch.dict(
            os.environ, {"FOLLOW_PERSISTENCE_RPC_V1_ENABLED": "false"}
        ), mock.patch.object(
            supabase_client,
            "persist_verified_follow_success_rpc",
            return_value=rpc_success(action_id),
        ) as rpc, mock.patch.object(
            runner, "_timed_safe_supabase_call"
        ) as legacy:
            ok = runner._persist_verified_follow_success_to_supabase(
                supabase_mode=True,
                account_id=canary_account_id,
                follower_un="candidate",
                source_profile_username="source",
                run_id=canary_run_id,
                follow_out={"skipped_tap": False},
                fs_af="following",
                f_st="following",
                target_id=TARGET_ID,
                phase="manual_stop_before_terminal_status",
                request_id=REQUEST_ID,
                action_id=action_id,
                settings_revision_expected=SETTINGS_REVISION,
                followed_at=FOLLOWED_AT,
            )

        self.assertTrue(ok)
        rpc.assert_called_once()
        legacy.assert_not_called()
        self.assertEqual(
            follow_persistence_intent.load_nonterminal_intents(
                account_id=canary_account_id, run_id=canary_run_id
            ),
            [],
        )

    def test_dispatcher_to_post_follow_rpc_uses_one_durable_request_context(self) -> None:
        account_id = runner.FOLLOW_60S_CANARY_ACCOUNT_ID
        action_id = follow_persistence_rpc.deterministic_action_id(
            account_id, RUN_ID, "candidate"
        )
        intent = follow_persistence_intent.create_prepared_intent(
            action_id=action_id,
            account_id=account_id,
            run_id=RUN_ID,
            request_id=REQUEST_ID,
            candidate_username="candidate",
            source_target_id=TARGET_ID,
            source_ct_username="source",
            settings_revision=SETTINGS_REVISION,
        )
        intent = follow_persistence_intent.update_intent_stage(
            run_id=RUN_ID,
            action_id=action_id,
            stage="follow_physically_verified",
            followed_at=FOLLOWED_AT,
        )
        binding = {
            "account_id": account_id,
            "run_id": RUN_ID,
            "request_id": REQUEST_ID,
        }
        logs: list[tuple[str, str, dict]] = []
        with mock.patch.dict(
            os.environ, {"FOLLOW_PERSISTENCE_RPC_V1_ENABLED": "false"}
        ), mock.patch.object(
            runner, "_CURRENT_FOLLOW_PERSISTENCE_RUN_BINDING", binding
        ), mock.patch.object(
            runner, "_CURRENT_RUN_REQUEST_ID", REQUEST_ID
        ), mock.patch.object(
            supabase_client,
            "persist_verified_follow_success_rpc",
            return_value=rpc_success(action_id),
        ) as rpc, mock.patch.object(
            runner,
            "log",
            side_effect=lambda level, event, **fields: logs.append(
                (level, event, fields)
            ),
        ):
            ok = runner._persist_verified_follow_from_durable_intent(
                intent=intent,
                supabase_mode=True,
                account_id=account_id,
                follower_un="candidate",
                source_profile_username="source",
                run_id=RUN_ID,
                follow_out={"skipped_tap": False},
                fs_af="following",
                f_st="following",
                target_id=TARGET_ID,
                phase="after_post_follow",
                defer_source_follow_success=True,
                post_follow_result={
                    "mute": {
                        "ok": True,
                        "posts_verified": True,
                        "stories_verified": True,
                    },
                    "likes": {"phase_outcome": "success", "liked_count": 1},
                    "return_ok": True,
                    "return_method": "fresh_candidate_proof_one_back_then_exact_ct",
                },
            )

        self.assertTrue(ok)
        self.assertEqual(rpc.call_args.kwargs["request_id"], REQUEST_ID)
        trace = next(
            fields["phase_trace"]
            for _level, event, fields in logs
            if event == "follow_persistence_end_to_end_phase_trace"
        )
        self.assertTrue(all(trace.values()))

    def test_canonical_request_run_account_binding_is_certified_once(self) -> None:
        account_id = runner.FOLLOW_60S_CANARY_ACCOUNT_ID
        with mock.patch(
            "account_run_control.get_account_run_request",
            return_value={"id": REQUEST_ID, "account_id": account_id, "run_id": RUN_ID},
        ), mock.patch(
            "account_run_control.get_ig_run_by_id",
            return_value={"id": RUN_ID, "account_id": account_id, "status": "running"},
        ), mock.patch.object(runner, "log"):
            binding = runner._establish_follow_persistence_run_binding(
                account_id=account_id,
                run_id=RUN_ID,
                request_id=REQUEST_ID,
            )

        self.assertEqual(binding["request_id"], REQUEST_ID)
        self.assertEqual(binding["run_id"], RUN_ID)
        self.assertEqual(binding["account_id"], account_id)

    def test_missing_or_mismatched_request_context_fails_before_rpc(self) -> None:
        account_id = ACCOUNT_ID
        binding = {
            "account_id": account_id,
            "run_id": RUN_ID,
            "request_id": REQUEST_ID,
        }
        intents = follow_persistence_intent.load_nonterminal_intents(
            account_id=ACCOUNT_ID,
            run_id=RUN_ID,
        )
        self.assertEqual(len(intents), 1)
        valid = intents[0]
        missing = dict(valid or {})
        missing["request_id"] = ""
        mismatched = dict(valid or {})
        mismatched["request_id"] = "66666666-6666-4666-8666-666666666666"
        with mock.patch.object(
            runner, "_CURRENT_FOLLOW_PERSISTENCE_RUN_BINDING", binding
        ), mock.patch.object(
            supabase_client, "persist_verified_follow_success_rpc"
        ) as rpc:
            with self.assertRaisesRegex(
                RuntimeError, "follow_persistence_intent_context_missing:request_id"
            ):
                runner._validate_follow_persistence_intent_context(
                    missing,
                    account_id=account_id,
                    run_id=RUN_ID,
                    candidate_username="candidate",
                )
            with self.assertRaisesRegex(
                RuntimeError, "follow_persistence_intent_context_mismatch:request_id"
            ):
                runner._validate_follow_persistence_intent_context(
                    mismatched,
                    account_id=account_id,
                    run_id=RUN_ID,
                    candidate_username="candidate",
                )
        rpc.assert_not_called()

    def test_created_and_replay_contract_resume(self) -> None:
        for status in ("created", "idempotent_replay"):
            follow_persistence_intent.update_intent_stage(
                run_id=RUN_ID,
                action_id=self.action_id,
                stage="follow_physically_verified",
                followed_at=FOLLOWED_AT,
            )
            with mock.patch.object(
                supabase_client,
                "persist_verified_follow_success_rpc",
                return_value=rpc_success(self.action_id, status),
            ):
                self.assertTrue(self._persist())

    def test_timeout_after_commit_uses_status_lookup(self) -> None:
        payload = dict(rpc_success(self.action_id, "idempotent_replay"))
        with mock.patch.object(
            supabase_client,
            "persist_verified_follow_success_rpc",
            side_effect=supabase_client.SupabaseRestError("supabase_rest_timeout"),
        ), mock.patch.object(
            supabase_client,
            "get_follow_persistence_event",
            return_value={"event_status": "success", "payload": payload},
        ), mock.patch.object(runner, "_timed_safe_supabase_call") as legacy:
            self.assertTrue(self._persist())
        legacy.assert_not_called()

    def test_timeout_before_commit_safe_stops_without_legacy(self) -> None:
        with mock.patch.object(
            supabase_client,
            "persist_verified_follow_success_rpc",
            side_effect=supabase_client.SupabaseRestError("supabase_rest_timeout"),
        ), mock.patch.object(
            supabase_client, "get_follow_persistence_event", return_value=None
        ), mock.patch.object(runner, "_timed_safe_supabase_call") as legacy:
            self.assertFalse(self._persist())
        legacy.assert_not_called()

    def test_absent_rpc_falls_back_only_without_event(self) -> None:
        with mock.patch.object(
            supabase_client,
            "persist_verified_follow_success_rpc",
            side_effect=supabase_client.SupabaseRestError("supabase_rpc_not_available"),
        ), mock.patch.object(
            supabase_client, "get_follow_persistence_event", return_value=None
        ), mock.patch.object(
            runner, "_timed_safe_supabase_call", return_value={"ok": True}
        ) as legacy:
            self.assertTrue(self._persist())
        self.assertIn(
            "record_follow_interaction_outcome",
            [call.args[1] for call in legacy.call_args_list],
        )

    def test_partial_response_safe_stops(self) -> None:
        partial = rpc_success(self.action_id)
        partial["counter_applied"] = False
        with mock.patch.object(
            supabase_client, "persist_verified_follow_success_rpc", return_value=partial
        ), mock.patch.object(runner, "_timed_safe_supabase_call") as legacy:
            self.assertFalse(self._persist())
        legacy.assert_not_called()

    def test_recovery_requires_fresh_exact_following(self) -> None:
        with mock.patch.object(runner, "verify_profile", return_value=True), mock.patch.object(
            runner, "_follow_ui_state_snapshot", return_value="following"
        ), mock.patch.object(
            runner, "_persist_verified_follow_success_to_supabase", return_value=True
        ) as persist:
            self.assertTrue(runner._recover_verified_follow_persistence_intents(
                object(), account_id=ACCOUNT_ID, run_id=RUN_ID, supabase_mode=True
            ))
        persist.assert_called_once()

    def test_recovery_before_tap_abandons_without_profile_or_rpc(self) -> None:
        follow_persistence_intent.update_intent_stage(
            run_id=RUN_ID,
            action_id=self.action_id,
            stage="prepared_before_follow_tap",
        )
        with mock.patch.object(runner, "verify_profile") as verify, mock.patch.object(
            runner, "_persist_verified_follow_success_to_supabase"
        ) as persist:
            self.assertTrue(runner._recover_verified_follow_persistence_intents(
                object(), account_id=ACCOUNT_ID, run_id=RUN_ID, supabase_mode=True
            ))
        verify.assert_not_called()
        persist.assert_not_called()

    def test_recovery_without_following_marks_review_and_stops(self) -> None:
        with mock.patch.object(runner, "verify_profile", return_value=True), mock.patch.object(
            runner, "_follow_ui_state_snapshot", return_value="follow"
        ), mock.patch.object(
            runner, "_persist_verified_follow_success_to_supabase"
        ) as persist:
            self.assertFalse(runner._recover_verified_follow_persistence_intents(
                object(), account_id=ACCOUNT_ID, run_id=RUN_ID, supabase_mode=True
            ))
        persist.assert_not_called()


class SupabaseFollowPersistenceClientTest(unittest.TestCase):
    def test_rpc_payload_uses_versioned_function(self) -> None:
        with mock.patch.object(supabase_client, "_request_json", return_value=rpc_success("a")) as req:
            value = supabase_client.persist_verified_follow_success_rpc(
                action_id="a",
                account_id=ACCOUNT_ID,
                run_id=RUN_ID,
                request_id=REQUEST_ID,
                candidate_username="Candidate",
                source_target_id=TARGET_ID,
                source_ct_username="Source",
                followed_at=FOLLOWED_AT,
                follow_state_after="following",
                settings_revision_expected=SETTINGS_REVISION,
                verification_method="exact",
                metadata_safe={"phase": "test"},
            )
        self.assertEqual(value["ok"], True)
        self.assertEqual(req.call_args.args[:2], ("POST", "rpc/persist_verified_follow_success_v1"))
        self.assertNotIn("eligible_unfollow_at", req.call_args.kwargs["body"])

    def _http_error(self, code: int, detail: dict) -> error.HTTPError:
        return error.HTTPError(
            "https://example.invalid",
            code,
            "failure",
            {},
            io.BytesIO(json.dumps(detail).encode("utf-8")),
        )

    def test_unknown_column_and_missing_rpc_are_not_retried(self) -> None:
        cases = (
            (400, {"code": "PGRST204", "message": "Could not find the 'followed' column"},
             "supabase_schema_payload_incompatible"),
            (404, {"code": "PGRST202", "message": "Could not find the function"},
             "supabase_rpc_not_available"),
        )
        for code, detail, expected in cases:
            with self.subTest(expected=expected), mock.patch.object(
                supabase_client.request, "urlopen", side_effect=self._http_error(code, detail)
            ) as urlopen, mock.patch.dict(
                os.environ, {"SUPABASE_REST_MAX_RETRIES": "3"}
            ), mock.patch.object(supabase_client.time, "sleep") as sleep:
                with self.assertRaises(supabase_client.SupabaseRestError) as caught:
                    supabase_client._request_urlopen(
                        request.Request("https://example.invalid", method="POST"),
                        op="POST test",
                    )
            self.assertEqual(caught.exception.reason, expected)
            self.assertEqual(urlopen.call_count, 1)
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
