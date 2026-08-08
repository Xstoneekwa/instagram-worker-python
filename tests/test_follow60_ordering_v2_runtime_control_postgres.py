from __future__ import annotations

import json
import os
import subprocess
import threading
import unittest
from pathlib import Path


DATABASE_URL = os.environ.get("FOLLOW60_ORDERING_V2_TEST_DATABASE_URL", "")
ROOT = Path(__file__).resolve().parents[1]
BASE_MIGRATION = ROOT / "supabase/migrations/20260808154030_follow60_ordering_v2_behavioral_runtime_control_v1.sql"
VARIABLE_MIGRATION = ROOT / "supabase/migrations/20260808201850_follow60_ordering_v2_variable_canary_barrier_v1.sql"
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
          );
          create table if not exists public.ig_accounts(
            id uuid primary key
          );
          create table if not exists public.ig_account_settings(
            account_id uuid primary key,
            follow_limit integer,
            max_follow_per_run integer,
            max_actions_per_day integer
          );
          create table if not exists public.account_package_summary(
            account_id uuid primary key,
            package_caps jsonb not null,
            effective_caps_preview jsonb not null
          );
          create table if not exists public.ig_interaction_events(
            id bigint generated always as identity primary key,
            account_id uuid not null,
            run_id uuid,
            interaction_type text not null,
            interaction_status text not null,
            event_type text not null,
            event_at timestamptz not null default now()
          );
        """)
        for migration in (BASE_MIGRATION, VARIABLE_MIGRATION):
            subprocess.run(
                ["psql", DATABASE_URL, "-X", "-v", "ON_ERROR_STOP=1", "-f", str(migration)],
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
        self.sql("truncate public.follow60_ordering_v2_behavioral_events, public.follow60_ordering_v2_behavioral_controls, public.account_run_requests, public.ig_runs, public.ig_interaction_events, public.account_package_summary, public.ig_account_settings, public.ig_accounts restart identity cascade")
        self.sql(f"insert into public.ig_accounts(id) values('{ACCOUNT}')")
        self.set_quota(day_cap=20, session_cap=20, consumed=0)
        self.sql(f"insert into public.ig_runs(id,account_id) values('{RUN}','{ACCOUNT}'),('{OTHER_RUN}','{ACCOUNT}')")
        self.sql(f"""
          insert into public.account_run_requests(id,account_id,run_id,requested_run_type,status,metadata_safe)
          values
          ('{REQUEST}','{ACCOUNT}','{RUN}','account_session','running','{{"manual_start":true,"trigger":"manual"}}'),
          ('{OTHER_REQUEST}','{ACCOUNT}','{OTHER_RUN}','account_session','running','{{"manual_start":true,"trigger":"manual"}}')
        """)

    def rpc(self, expression: str) -> dict:
        return json.loads(self.sql(f"select ({expression})::text"))

    def set_quota(self, *, day_cap: int, session_cap: int, consumed: int) -> None:
        self.sql(f"""
          insert into public.ig_account_settings(account_id,follow_limit,max_follow_per_run,max_actions_per_day)
          values('{ACCOUNT}',{session_cap},{session_cap},{day_cap})
          on conflict(account_id) do update set
            follow_limit=excluded.follow_limit,
            max_follow_per_run=excluded.max_follow_per_run,
            max_actions_per_day=excluded.max_actions_per_day;
          insert into public.account_package_summary(account_id,package_caps,effective_caps_preview)
          values(
            '{ACCOUNT}',
            '{{"follow_day":{day_cap},"follow_session":{session_cap}}}',
            '{{"follow_day":{day_cap},"follow_session":{session_cap},"warmup_follow_day_cap":{day_cap}}}'
          )
          on conflict(account_id) do update set
            package_caps=excluded.package_caps,
            effective_caps_preview=excluded.effective_caps_preview;
          delete from public.ig_interaction_events where account_id='{ACCOUNT}';
          insert into public.ig_interaction_events(
            account_id,run_id,interaction_type,interaction_status,event_type,event_at
          )
          select '{ACCOUNT}','{RUN}','follow','success','follow_verified',now()
          from generate_series(1,{consumed});
        """)

    def arm(
        self,
        *,
        account: str = ACCOUNT,
        sha: str = SHA,
        requested: int = 10,
        expires: str = "now()+interval '1 hour'",
    ) -> dict:
        return self.rpc(
            f"public.arm_follow60_ordering_v2_behavioral_control_v1('{account}','{sha}',{expires},'{{\"baseline_follow_count\":0}}',{requested})"
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
        self.assertEqual(10, armed["max_v2_cycles"])
        self.assertEqual(20, armed["canonical_follow_remaining_at_arm"])
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

    def test_arm_validates_account_sha_expiry_and_requested_minimum(self) -> None:
        self.assertEqual("v2_account_not_found", self.arm(account=OTHER_ACCOUNT)["reason"])
        self.assertEqual("v2_pre_run_identity_invalid", self.arm(sha="bad")["reason"])
        self.assertEqual("v2_control_expiry_invalid", self.arm(expires="now()-interval '1 second'")["reason"])
        self.assertEqual("v2_requested_max_invalid", self.arm(requested=0)["reason"])

    def test_server_resolves_requested_barrier_against_canonical_remaining(self) -> None:
        cases = (
            (20, 0, 10, True, 10),
            (20, 11, 10, True, 9),
            (20, 11, 9, True, 9),
            (20, 19, 10, True, 1),
            (20, 20, 10, False, 0),
            (20, 0, 11, True, 10),
        )
        for day_cap, consumed, requested, ok, resolved in cases:
            with self.subTest(consumed=consumed, requested=requested):
                self.setUp()
                self.set_quota(day_cap=day_cap, session_cap=20, consumed=consumed)
                armed = self.arm(requested=requested)
                self.assertEqual(ok, armed["ok"])
                if ok:
                    self.assertEqual(resolved, armed["max_v2_cycles"])
                    self.assertEqual(consumed, armed["baseline"]["baseline_follow_count"])
                    self.assertEqual(requested, armed["baseline"]["requested_max_v2_cycles"])
                    self.assertEqual(10, armed["baseline"]["behavioral_canary_hard_max"])
                else:
                    self.assertEqual("v2_canonical_follow_quota_exhausted", armed["reason"])

    def test_concurrent_arm_is_single_and_replay_is_idempotent(self) -> None:
        outputs: list[dict] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                outputs.append(self.arm())
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
        self.assertEqual(1, len({item["control_id"] for item in outputs}))
        self.assertEqual(
            ["v2_pre_run_control_armed", "v2_pre_run_control_idempotent"],
            sorted(item["reason"] for item in outputs),
        )

    def test_canceled_control_is_not_reused_and_historical_ten_is_valid(self) -> None:
        first = self.arm()
        self.sql(
            "update public.follow60_ordering_v2_behavioral_controls "
            f"set status='canceled' where control_id='{first['control_id']}'"
        )
        second = self.arm()
        self.assertNotEqual(first["control_id"], second["control_id"])
        self.assertEqual(
            "10",
            self.sql(
                "select max_v2_cycles::text from public.follow60_ordering_v2_behavioral_controls "
                f"where control_id='{first['control_id']}'"
            ),
        )

    def test_rpc_is_security_definer_least_privilege_and_rls_stays_enabled(self) -> None:
        identity = (
            "public.arm_follow60_ordering_v2_behavioral_control_v1"
            "(uuid,text,timestamptz,jsonb,integer)"
        )
        self.assertEqual(
            "true|search_path=\"\"",
            self.sql(
                "select prosecdef::text || '|' || coalesce(array_to_string(proconfig,','),'') "
                f"from pg_proc where oid='{identity}'::regprocedure"
            ),
        )
        privileges = self.sql(
            "select "
            f"has_function_privilege('anon','{identity}','execute')::int || ',' || "
            f"has_function_privilege('authenticated','{identity}','execute')::int || ',' || "
            f"has_function_privilege('service_role','{identity}','execute')::int"
        )
        self.assertEqual("0,0,1", privileges)
        self.assertEqual(
            "true",
            self.sql(
                "select relrowsecurity::text from pg_class "
                "where oid='public.follow60_ordering_v2_behavioral_controls'::regclass"
            ),
        )

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

    def test_nine_cycle_barrier_is_exact_and_v1_fallback_does_not_consume_it(self) -> None:
        self.set_quota(day_cap=20, session_cap=20, consumed=11)
        armed = self.arm(requested=10)
        self.assertEqual(9, armed["max_v2_cycles"])
        binding = self.claim()
        for index in range(9):
            action = f"v2-nine-{index}"
            self.event(binding, action, "candidate_seen")
            self.event(binding, action, "v2_selected")
            self.event(binding, f"fallback-nine-{index}", "v1_fallback")
            complete = self.event(binding, action, "v2_complete")
            self.assertEqual(index + 1, complete["v2_complete_count"])
        self.assertTrue(complete["barrier_reached"])
        self.assertEqual(9, complete["v1_fallback_count"])
        denied = self.event(binding, "v2-ten", "v2_complete")
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
