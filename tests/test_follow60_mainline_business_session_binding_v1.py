from __future__ import annotations

import os
import unittest
import uuid
from unittest import mock

import account_session_orchestrator as account_session
from follow60_business_session_binding_v1 import (
    BUSINESS_SESSION_BINDING_VERSION,
    build_candidate_stage_binding,
    create_mainline_business_session_binding,
    validate_business_session_binding,
)


WORKER_SHA = "5" * 40
RUN_ID = "00000000-0000-4000-8000-000000000201"
REQUEST_ID = "00000000-0000-4000-8000-000000000202"
ACCOUNT_IDS = (
    "83de9cc9-5c37-42d1-9edc-c924352b17b1",
    "0d299d1e-46ee-49d2-8a84-4f928f2bb182",
    "f0e2bf3b-6340-431c-aafd-1b9c7aee207a",
    "b01c4bab-4285-4dbd-803c-c502266fe0d9",
    "83c0ec81-79f8-4898-9822-6139638cdcbe",
    "ba73eda4-d22a-4b93-9683-2af7b8aab764",
    "dfe78a92-3a51-435e-8911-ed10c93a4d82",
    "df707a97-bfb1-4286-bf09-25090a7b3207",
    "b024e94e-395d-4f02-9787-81ddc679b014",
)


def _binding(account_id: str, *, business_session_id: str = "session-mainline-1") -> dict:
    return create_mainline_business_session_binding(
        business_session_id=business_session_id,
        account_id=account_id,
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        attempt_id=1,
        worker_sha=WORKER_SHA,
    ).to_dict()


class Follow60MainlineBusinessSessionBindingV1Tests(unittest.TestCase):
    def test_binding_is_immutable_and_candidate_stage_preserves_session_identity(self) -> None:
        base = create_mainline_business_session_binding(
            business_session_id="session-mainline-1",
            account_id=ACCOUNT_IDS[0],
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            attempt_id=1,
            worker_sha=WORKER_SHA,
        )
        self.assertEqual(base.version, BUSINESS_SESSION_BINDING_VERSION)
        stages = [
            build_candidate_stage_binding(
                base,
                action_id=f"action-{index}",
                candidate_username=f"candidate-{index}",
            )
            for index in range(10)
        ]
        self.assertEqual(
            {stage["business_session_id"] for stage in stages},
            {"session-mainline-1"},
        )
        self.assertEqual({stage["run_id"] for stage in stages}, {RUN_ID})
        self.assertEqual({stage["request_id"] for stage in stages}, {REQUEST_ID})
        self.assertEqual(len({stage["action_id"] for stage in stages}), 10)

    def test_new_run_and_different_account_do_not_reuse_identity(self) -> None:
        first = _binding(ACCOUNT_IDS[0], business_session_id=str(uuid.uuid4()))
        second = _binding(ACCOUNT_IDS[0], business_session_id=str(uuid.uuid4()))
        other = _binding(ACCOUNT_IDS[1], business_session_id=str(uuid.uuid4()))
        self.assertNotEqual(first["business_session_id"], second["business_session_id"])
        self.assertNotEqual(first["business_session_id"], other["business_session_id"])
        self.assertNotEqual(first["account_id"], other["account_id"])

    def test_validation_fails_closed_for_every_scope_mismatch(self) -> None:
        base = _binding(ACCOUNT_IDS[0])
        cases = (
            ({"account_id": ACCOUNT_IDS[1]}, "business_session_account_mismatch"),
            ({"request_id": "different"}, "business_session_request_mismatch"),
            ({"run_id": "different"}, "business_session_run_mismatch"),
            ({"attempt_id": 2}, "business_session_attempt_mismatch"),
            ({"worker_sha": "6" * 40}, "business_session_worker_mismatch"),
            ({"binding_kind": "canary"}, "business_session_binding_kind_mismatch"),
            ({"business_session_id": "different"}, "business_session_id_mismatch"),
        )
        for overrides, expected_reason in cases:
            kwargs = {
                "account_id": ACCOUNT_IDS[0],
                "request_id": REQUEST_ID,
                "run_id": RUN_ID,
                "attempt_id": 1,
                "worker_sha": WORKER_SHA,
                "business_session_id": "session-mainline-1",
            }
            kwargs.update(overrides)
            with self.subTest(reason=expected_reason):
                validated, reason = validate_business_session_binding(base, **kwargs)
                self.assertIsNone(validated)
                self.assertEqual(reason, expected_reason)

    def test_all_nine_accounts_reach_engine_with_exact_mainline_binding(self) -> None:
        for account_id in ACCOUNT_IDS:
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
            binding = _binding(account_id)
            with mock.patch.dict(os.environ, {"WORKER_GIT_SHA": WORKER_SHA}):
                result = account_session._run_follow_target_rotation(
                    object(),
                    account_id=account_id,
                    account_username="account",
                    run_id=RUN_ID,
                    follow_targets=[{"target_id": "target-1", "source_profile": "ct"}],
                    run_followers_list_engine_session=engine,
                    supabase_mode=False,
                    warm_session_used=False,
                    force_stop_used=False,
                    max_targets_per_run=1,
                    run_request_id=REQUEST_ID,
                    follow60_mainline_active=True,
                    follow60_business_session_binding=binding,
                    follow60_attempt_id=1,
                    business_session_id="session-mainline-1",
                )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(len(calls), 1)
            self.assertIs(calls[0]["follow60_mainline_active"], True)
            self.assertEqual(calls[0]["follow60_business_session_binding"], binding)
            self.assertEqual(calls[0]["business_session_id"], "session-mainline-1")
            self.assertNotIn("follow60_canary_control", calls[0])

    def test_invalid_binding_blocks_before_engine_or_device(self) -> None:
        engine = mock.Mock()
        with mock.patch.dict(os.environ, {"WORKER_GIT_SHA": WORKER_SHA}):
            result = account_session._run_follow_target_rotation(
                mock.Mock(),
                account_id=ACCOUNT_IDS[0],
                account_username="account",
                run_id=RUN_ID,
                follow_targets=[{"target_id": "target-1", "source_profile": "ct"}],
                run_followers_list_engine_session=engine,
                supabase_mode=False,
                warm_session_used=False,
                force_stop_used=False,
                max_targets_per_run=1,
                run_request_id=REQUEST_ID,
                follow60_mainline_active=True,
                follow60_business_session_binding=None,
                follow60_attempt_id=1,
                business_session_id="session-mainline-1",
            )
        self.assertEqual(result["exit_code"], 96)
        self.assertEqual(result["reason"], "business_session_binding_missing")
        engine.assert_not_called()

    def test_dispatch_wrapper_keeps_mainline_binding_separate_from_canary(self) -> None:
        binding = _binding(ACCOUNT_IDS[0])
        with mock.patch.object(account_session, "run_account_session", return_value=0) as run:
            code = account_session.dispatch_account_session(
                object(),
                account_id=ACCOUNT_IDS[0],
                account_username="account",
                run_id=RUN_ID,
                run_request_id=REQUEST_ID,
                source_profile_username="ct",
                run_followers_list_engine_session=mock.Mock(),
                supabase_mode=False,
                warm_session_used=False,
                force_stop_used=False,
                follow60_canary_active=False,
                follow60_canary_control={},
                follow60_mainline_active=True,
                follow60_business_session_binding=binding,
                follow60_attempt_id=1,
                business_session_id="session-mainline-1",
            )
        self.assertEqual(code, 0)
        kwargs = run.call_args.kwargs
        self.assertIs(kwargs["follow60_mainline_active"], True)
        self.assertEqual(kwargs["follow60_business_session_binding"], binding)
        self.assertEqual(kwargs["follow60_canary_control"], {})


if __name__ == "__main__":
    unittest.main()
