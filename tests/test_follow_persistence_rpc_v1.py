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


def canonical_evidence(action_id: str) -> dict:
    eligible = "2026-07-22 10:01:00.00000+00:00"
    return {
        "event": {
            "id": action_id,
            "account_id": ACCOUNT_ID,
            "run_id": RUN_ID,
            "request_id": REQUEST_ID,
            "username": "candidate",
            "event_type": "follow_verified_persisted_v1",
            "event_status": "success",
            "interaction_type": "follow",
            "interaction_status": "success",
            "payload": {
                "interaction_id": INTERACTION_ID,
                "follow_persisted": True,
                "eligible_unfollow_at": eligible,
                "audit_persisted": True,
                "counter_applied": True,
                "settings_revision_match": True,
                "settings_revision": SETTINGS_REVISION,
                "invariants_confirmed": sorted(
                    follow_persistence_rpc.REQUIRED_INVARIANTS
                ),
            },
        },
        "interaction": {
            "id": INTERACTION_ID,
            "account_id": ACCOUNT_ID,
            "username": "candidate",
            "followed_at": "2026-07-19 10:01:00+00:00",
            "eligible_unfollow_at": eligible,
            "was_successful": True,
            "followed_by_bot": True,
            "follow_status": "following",
            "interaction_status": "success",
            "payload": {"action_id": action_id},
        },
        "unfollow_settings": {
            "account_id": ACCOUNT_ID,
            "unfollow_after_days": 3,
            "updated_at": SETTINGS_REVISION,
        },
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

    def test_postgres_timestamp_shapes_are_strictly_parseable(self) -> None:
        for timestamp in (
            "2026-07-22T10:01:00.12345+00:00",
            "2026-07-22T10:01:00.123456+00:00",
            "2026-07-22T10:01:00.12345Z",
        ):
            with self.subTest(timestamp=timestamp):
                value = rpc_success("action")
                value["eligible_unfollow_at"] = timestamp
                self.assertEqual(
                    follow_persistence_rpc.validate_rpc_response(
                        value, expected_action_id="action"
                    ),
                    (True, "ok"),
                )
        value = rpc_success("action")
        value["eligible_unfollow_at"] = "2026-07-22 10:01:00"
        self.assertEqual(
            follow_persistence_rpc.validate_rpc_response(
                value, expected_action_id="action"
            )[1],
            "response_eligible_unfollow_at_invalid",
        )

    def test_canonical_evidence_requires_every_identity_and_business_invariant(self) -> None:
        action_id = follow_persistence_rpc.deterministic_action_id(
            ACCOUNT_ID, RUN_ID, "candidate"
        )
        valid = canonical_evidence(action_id)
        self.assertTrue(
            follow_persistence_rpc.validate_canonical_persistence_evidence(
                valid,
                expected_action_id=action_id,
                expected_account_id=ACCOUNT_ID,
                expected_request_id=REQUEST_ID,
                expected_run_id=RUN_ID,
                expected_username="@Candidate",
                expected_settings_revision=SETTINGS_REVISION,
            )[0]
        )
        mutations = {
            "event_missing": lambda value: value.update(event=None),
            "request_id": lambda value: value["event"].update(request_id="wrong"),
            "run_id": lambda value: value["event"].update(run_id="wrong"),
            "settings_account_id": lambda value: value["unfollow_settings"].update(
                account_id="wrong"
            ),
            "username": lambda value: value["interaction"].update(username="wrong"),
            "action_id": lambda value: value["interaction"]["payload"].update(
                action_id="wrong"
            ),
            "timestamp": lambda value: value["interaction"].update(
                eligible_unfollow_at="invalid"
            ),
            "counter": lambda value: value["event"]["payload"].update(
                counter_applied=False
            ),
            "audit": lambda value: value["event"]["payload"].update(
                audit_persisted=False
            ),
            "was_successful": lambda value: value["interaction"].update(
                was_successful=False
            ),
            "followed_by_bot": lambda value: value["interaction"].update(
                followed_by_bot=False
            ),
            "follow_status": lambda value: value["interaction"].update(
                follow_status="unfollowed"
            ),
            "eligible_contract": lambda value: value["unfollow_settings"].update(
                unfollow_after_days=4
            ),
        }
        for case, mutate in mutations.items():
            value = json.loads(json.dumps(valid))
            mutate(value)
            with self.subTest(case=case):
                matched, _reason, mismatches, reconciled = (
                    follow_persistence_rpc.validate_canonical_persistence_evidence(
                        value,
                        expected_action_id=action_id,
                        expected_account_id=ACCOUNT_ID,
                        expected_request_id=REQUEST_ID,
                        expected_run_id=RUN_ID,
                        expected_username="candidate",
                        expected_settings_revision=SETTINGS_REVISION,
                    )
                )
                self.assertFalse(matched)
                self.assertTrue(mismatches)
                self.assertIsNone(reconciled)


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

    def test_end_to_end_partial_rpc_reconciles_before_next_candidate(self) -> None:
        account_id = ACCOUNT_ID
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
        partial = rpc_success(action_id)
        partial.pop("eligible_unfollow_at")
        logs: list[tuple[str, str, dict]] = []
        next_candidate = mock.Mock(return_value="candidate_2")
        with mock.patch(
            "account_run_control.get_account_run_request",
            return_value={
                "id": REQUEST_ID,
                "account_id": account_id,
                "run_id": RUN_ID,
                "requested_run_type": "account_session",
            },
        ), mock.patch(
            "account_run_control.get_ig_run_by_id",
            return_value={
                "id": RUN_ID,
                "account_id": account_id,
                "status": "running",
            },
        ), mock.patch.object(
            runner,
            "log",
            side_effect=lambda level, event, **fields: logs.append(
                (level, event, fields)
            ),
        ):
            binding = runner._establish_follow_persistence_run_binding(
                account_id=account_id,
                run_id=RUN_ID,
                request_id=REQUEST_ID,
            )
        with mock.patch.object(
            runner, "_CURRENT_FOLLOW_PERSISTENCE_RUN_BINDING", binding
        ), mock.patch.object(
            runner, "_CURRENT_RUN_REQUEST_ID", REQUEST_ID
        ), mock.patch.object(
            supabase_client,
            "persist_verified_follow_success_rpc",
            return_value=partial,
        ) as rpc, mock.patch.object(
            supabase_client,
            "get_follow_persistence_canonical_evidence",
            return_value=canonical_evidence(action_id),
        ) as canonical_reread, mock.patch.object(
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
                    "return_method": "new_safe_method_label_not_in_legacy_allowlist",
                    "final_ct_exact": True,
                },
            )
            if ok:
                next_candidate()

        self.assertTrue(ok)
        self.assertEqual(rpc.call_args.kwargs["request_id"], REQUEST_ID)
        rpc.assert_called_once()
        canonical_reread.assert_called_once_with(
            action_id=action_id,
            account_id=account_id,
            username="candidate",
        )
        next_candidate.assert_called_once_with()
        self.assertEqual(
            follow_persistence_intent.load_nonterminal_intents(
                account_id=account_id, run_id=RUN_ID
            ),
            [],
        )
        trace = next(
            fields["phase_trace"]
            for _level, event, fields in logs
            if event == "follow_persistence_end_to_end_phase_trace"
        )
        self.assertTrue(all(trace.values()))
        self.assertIn(
            "follow_persistence_rpc_response_reconciled",
            [event for _level, event, _fields in logs],
        )
        source = Path(runner.__file__).read_text(encoding="utf-8")
        persist_pos = source.index("_critical_persist_ok = _persist_verified_follow_from_durable_intent")
        resume_pos = source.index('"post_return_ui_resume_allowed"', persist_pos)
        next_action_pos = source.index('"post_return_next_ui_action_started"', resume_pos)
        self.assertLess(persist_pos, resume_pos)
        self.assertLess(resume_pos, next_action_pos)

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
        with mock.patch.object(
            supabase_client,
            "persist_verified_follow_success_rpc",
            side_effect=supabase_client.SupabaseRestError("supabase_rest_timeout"),
        ), mock.patch.object(
            supabase_client,
            "get_follow_persistence_canonical_evidence",
            return_value=canonical_evidence(self.action_id),
        ), mock.patch.object(runner, "_timed_safe_supabase_call") as legacy:
            self.assertTrue(self._persist())
        legacy.assert_not_called()

    def test_timeout_before_commit_safe_stops_without_legacy(self) -> None:
        with mock.patch.object(
            supabase_client,
            "persist_verified_follow_success_rpc",
            side_effect=supabase_client.SupabaseRestError("supabase_rest_timeout"),
        ), mock.patch.object(
            supabase_client,
            "get_follow_persistence_canonical_evidence",
            return_value={"event": None, "interaction": None, "unfollow_settings": None},
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

    def test_partial_response_reconciles_only_from_exact_canonical_evidence(self) -> None:
        partial = rpc_success(self.action_id)
        partial["eligible_unfollow_at"] = None
        with mock.patch.object(
            supabase_client, "persist_verified_follow_success_rpc", return_value=partial
        ), mock.patch.object(
            supabase_client,
            "get_follow_persistence_canonical_evidence",
            return_value=canonical_evidence(self.action_id),
        ), mock.patch.object(runner, "_timed_safe_supabase_call") as legacy:
            self.assertTrue(self._persist())
        legacy.assert_not_called()

    def test_missing_null_and_mistyped_responses_reconcile_without_second_rpc(self) -> None:
        cases = []
        missing = rpc_success(self.action_id)
        missing.pop("eligible_unfollow_at")
        cases.append(("missing", missing))
        null = rpc_success(self.action_id)
        null["eligible_unfollow_at"] = None
        cases.append(("null", null))
        cases.append(("mistyped", ["unexpected"]))
        for case, response in cases:
            follow_persistence_intent.update_intent_stage(
                run_id=RUN_ID,
                action_id=self.action_id,
                stage="follow_physically_verified",
                followed_at=FOLLOWED_AT,
            )
            with self.subTest(case=case), mock.patch.object(
                supabase_client,
                "persist_verified_follow_success_rpc",
                return_value=response,
            ) as rpc, mock.patch.object(
                supabase_client,
                "get_follow_persistence_canonical_evidence",
                return_value=canonical_evidence(self.action_id),
            ) as reread:
                self.assertTrue(self._persist())
            rpc.assert_called_once()
            reread.assert_called_once()

    def test_partial_response_fails_closed_when_canonical_counter_is_not_applied(self) -> None:
        partial = rpc_success(self.action_id)
        partial["eligible_unfollow_at"] = None
        evidence = canonical_evidence(self.action_id)
        evidence["event"]["payload"]["counter_applied"] = False
        with mock.patch.object(
            supabase_client, "persist_verified_follow_success_rpc", return_value=partial
        ), mock.patch.object(
            supabase_client,
            "get_follow_persistence_canonical_evidence",
            return_value=evidence,
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
