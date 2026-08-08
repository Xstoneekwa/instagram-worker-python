from __future__ import annotations

import json
import os
import subprocess
import threading
import unittest
from pathlib import Path


DATABASE_URL = os.environ.get("FOLLOW60_ORDERING_V2_TEST_DATABASE_URL", "")
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260808154030_follow60_ordering_v2_behavioral_runtime_control_v1.sql"
ACCOUNT = "b024e94e-395d-4f02-9787-81ddc679b014"
OTHER_ACCOUNT = "11111111-1111-4111-8111-111111111111"
RUN = "22222222-2222-4222-8222-222222222222"
OTHER_RUN = "22222222-2222-4222-8222-222222222223"
REQUEST = "33333333-3333-4333-8333-333333333333"
OTHER_REQUEST = "33333333-3333-4333-8333-333333333334"
SESSION = "44444444-4444-4444-8444-444444444444"
OTHER_SESSION = "44444444-4444-4444-8444-444444444445"
SHA = "a" * 40


@unittest.skipUnless(DATABASE_URL, "set FOLLOW60_ORDERING_V2_TEST_DATABASE_URL")
class Follow60OrderingV2RuntimeControlPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql("create schema if not exists extensions")
        cls.sql("create extension if not exists pgcrypto with schema extensions")
        cls.sql("do $$ begin create role anon; exception when duplicate_object then null; end $$")
        cls.sql("do $$ begin create role authenticated; exception when duplicate_object then null; end $$")
        cls.sql("do $$ begin create role service_role; exception when duplicate_object then null; end $$")
        cls.sql("""
          create table if not exists public.ig_runs(
            id uuid primary key, account_id uuid not null, status text not null default 'running'
          );
          create table if not exists public.account_run_requests(
            id uuid primary key, account_id uuid not null, run_id uuid,
            requested_run_type text not null, status text not null,
            metadata_safe jsonb not null default '{}'::jsonb
          )
        """)
        subprocess.run(
            ["psql", DATABASE_URL, "-X", "-v", "ON_ERROR_STOP=1", "-f", str(MIGRATION)],
            check=True, text=True, capture_output=True,
        )

    @classmethod
    def sql(cls, statement: str) -> str:
        result = subprocess.run(
            ["psql", DATABASE_URL, "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", statement],
            check=True, text=True, capture_output=True,
        )
        return result.stdout.strip()

    def setUp(self) -> None:
        self.sql("truncate public.follow60_ordering_v2_behavioral_events, public.follow60_ordering_v2_behavioral_controls, public.account_run_requests, public.ig_runs restart identity cascade")
        self.sql(f"insert into public.ig_runs(id,account_id) values('{RUN}','{ACCOUNT}'),('{OTHER_RUN}','{ACCOUNT}')")
        self.sql(f"""
          insert into public.account_run_requests(id,account_id,run_id,requested_run_type,status,metadata_safe)
          values
          ('{REQUEST}','{ACCOUNT}','{RUN}','account_session','running','{{"manual_start":true,"trigger":"manual"}}'),
          ('{OTHER_REQUEST}','{ACCOUNT}','{OTHER_RUN}','account_session','running','{{"manual_start":true,"trigger":"manual"}}')
        """)

    def rpc(self, expression: str) -> dict:
        return json.loads(self.sql(f"select ({expression})::text"))

    def arm(self, *, account: str = ACCOUNT, sha: str = SHA) -> dict:
        return self.rpc(
            f"public.arm_follow60_ordering_v2_behavioral_control_v1('{account}','{sha}',now()+interval '1 hour','{{\"baseline_follow_count\":0}}')"
        )

    def claim(self, *, run: str = RUN, request: str = REQUEST, session: str = SESSION, account: str = ACCOUNT, sha: str = SHA) -> dict:
        return self.rpc(
            f"public.claim_follow60_ordering_v2_behavioral_binding_v1('{account}','{sha}','{run}','{request}','{session}',1)"
        )

    def event(self, binding: dict, action: str, kind: str) -> dict:
        return self.rpc(
            "public.record_follow60_ordering_v2_behavioral_event_v1("
            f"'{binding['control_id']}','{ACCOUNT}','{RUN}','{REQUEST}','{SESSION}',1,"
            f"'{binding['lease_id']}','{binding['lease_nonce']}','{SHA}','{action}','{kind}','{{}}')"
        )

    def test_pre_run_control_has_no_future_identities_and_claim_is_one_shot(self) -> None:
        armed = self.arm()
        self.assertTrue(armed["ok"])
        self.assertIsNone(armed["run_id"])
        self.assertIsNone(armed["request_id"])
        self.assertFalse(armed["binding_consumed"])
        claimed = self.claim()
        self.assertTrue(claimed["ok"])
        replay = self.claim()
        self.assertEqual("v2_runtime_binding_idempotent", replay["reason"])
        other = self.claim(run=OTHER_RUN, request=OTHER_REQUEST, session=OTHER_SESSION)
        self.assertFalse(other["ok"])
        self.assertEqual("v2_runtime_binding_already_consumed", other["reason"])

    def test_two_consumers_claim_same_control_with_one_binding(self) -> None:
        self.arm()
        outputs: list[dict] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                outputs.append(self.claim())
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        self.assertEqual(2, len(outputs))
        self.assertTrue(all(item["ok"] for item in outputs))
        self.assertEqual(1, len({item["lease_id"] for item in outputs}))
        self.assertEqual(
            ["v2_runtime_binding_idempotent", "v2_runtime_binding_idempotent"],
            sorted(item["reason"] for item in outputs),
        )

    def test_wrong_account_sha_expiry_and_non_manual_request_fail_closed(self) -> None:
        self.arm()
        self.assertEqual("v2_expected_worker_sha_mismatch", self.claim(sha="b" * 40)["reason"])
        self.sql(f"update public.account_run_requests set metadata_safe='{{}}' where id='{REQUEST}'")
        self.assertEqual("v2_manual_play_request_mismatch", self.claim()["reason"])
        self.sql("update public.follow60_ordering_v2_behavioral_controls set expires_at=now()-interval '1 second'")
        self.assertEqual("v2_control_expired", self.claim()["reason"])

    def test_mixed_sequence_counts_only_complete_and_blocks_eleven(self) -> None:
        self.arm()
        binding = self.claim()
        for index in range(10):
            action = f"v2-{index}"
            self.event(binding, action, "candidate_seen")
            self.event(binding, action, "v2_selected")
            if index < 2:
                fallback = f"v1-{index}"
                self.event(binding, fallback, "candidate_seen")
                self.event(binding, fallback, "v1_fallback")
            complete = self.event(binding, action, "v2_complete")
        self.assertTrue(complete["barrier_reached"])
        self.assertEqual(10, complete["v2_complete_count"])
        self.assertEqual(2, complete["v1_fallback_count"])
        denied = self.event(binding, "v2-11", "v2_complete")
        self.assertFalse(denied["ok"])
        self.assertEqual("v2_cycle_barrier_reached", denied["reason"])

    def test_replay_concurrency_partial_and_stop_are_honest(self) -> None:
        self.arm()
        binding = self.claim()
        outputs: list[dict] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                outputs.append(self.event(binding, "same", "candidate_seen"))
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        self.assertEqual([False, True], sorted(item["duplicate"] for item in outputs))
        self.event(binding, "partial", "v2_selected")
        self.event(binding, "partial", "v2_partial")
        self.assertEqual("v2_partial_cannot_complete", self.event(binding, "partial", "v2_complete")["reason"])
        terminal = self.rpc(
            f"public.terminalize_follow60_ordering_v2_behavioral_control_v1('{ACCOUNT}','{RUN}','{REQUEST}','stopped','operator_stop')"
        )
        self.assertEqual("stopped", terminal["status"])
        self.assertEqual(0, terminal["v2_complete_count"])

    def test_stop_at_three_seven_and_nine_preserves_exact_completed_count(self) -> None:
        for completed_count in (3, 7, 9):
            with self.subTest(completed_count=completed_count):
                self.setUp()
                self.arm()
                binding = self.claim()
                for index in range(completed_count):
                    action = f"stop-{completed_count}-{index}"
                    self.event(binding, action, "candidate_seen")
                    self.event(binding, action, "v2_selected")
                    self.event(binding, action, "v2_complete")
                self.event(binding, f"stop-{completed_count}-partial", "v2_selected")
                terminal = self.rpc(
                    f"public.terminalize_follow60_ordering_v2_behavioral_control_v1('{ACCOUNT}','{RUN}','{REQUEST}','stopped','operator_stop')"
                )
                self.assertEqual("stopped", terminal["status"])
                self.assertEqual(completed_count, terminal["v2_complete_count"])
                self.assertEqual(1, terminal["v2_partial_count"])

    def test_expired_lease_and_stale_lease_identity_fail_closed(self) -> None:
        self.arm()
        binding = self.claim()
        self.sql(
            "update public.follow60_ordering_v2_behavioral_controls "
            f"set lease_expires_at=now()-interval '1 second' where control_id='{binding['control_id']}'"
        )
        expired = self.event(binding, "expired", "candidate_seen")
        self.assertFalse(expired["ok"])
        self.assertEqual("v2_runtime_binding_inactive", expired["reason"])
        binding["lease_id"] = "55555555-5555-4555-8555-555555555555"
        stale = self.event(binding, "stale", "candidate_seen")
        self.assertFalse(stale["ok"])
        self.assertEqual("v2_runtime_binding_mismatch", stale["reason"])


if __name__ == "__main__":
    unittest.main()
