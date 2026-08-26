from pathlib import Path
import unittest


SQL = (Path(__file__).parents[1] / "supabase/migrations/20260826004546_control_plane_reliability_v1.sql").read_text()


class ControlPlaneReliabilityV1MigrationTest(unittest.TestCase):
    def test_required_rpcs_are_service_role_only(self) -> None:
        for name in (
            "admit_account_run_attempt_v1",
            "begin_device_activity_v1",
            "mark_pre_device_safe_stop_v1",
            "certify_zero_work_and_enqueue_recovery_v1",
        ):
            self.assertIn(f"create or replace function public.{name}", SQL)
            self.assertIn(f"revoke all on function public.{name}", SQL)
            self.assertRegex(SQL, rf"grant execute on function public\.{name}\([^;]+\) to service_role")

    def test_historical_capsules_are_not_backfilled_to_v1(self) -> None:
        schema_prefix = SQL.split("create or replace function public.admit_account_run_attempt_v1", 1)[0]
        self.assertNotRegex(schema_prefix, r"update public\.account_session_resume_plans")

    def test_root_lineage_is_immutable_and_s4_unrepresentable(self) -> None:
        self.assertIn("account_run_request_lineage_immutable", SQL)
        self.assertIn("execution_attempt_no between 1 and 3", SQL)
        self.assertIn("execution_attempt_no = retry_index + 1", SQL)

    def test_recovery_requires_safe_stop_or_expired_active_lease(self) -> None:
        self.assertIn("source_not_pre_device_safe_stopped", SQL)
        self.assertIn("v_request.lease_expires_at <= v_now", SQL)
        self.assertIn("irreversible_work_state <> 'PRE_DEVICE'", SQL)

    def test_admission_rejects_legacy_runs_and_uses_current_schedule_contract(self) -> None:
        self.assertIn("legacy_run_not_admissible", SQL)
        self.assertIn("v_request.metadata_safe->>'schedule_trigger'", SQL)
        self.assertRegex(
            SQL,
            r"evaluate_account_schedule_gate\(\s*v_account\.id,\s*'account_session',",
        )

    def test_safe_stop_reason_is_db_constrained_to_transient_family(self) -> None:
        self.assertIn("safe_stop_reason_not_transient", SQL)
        self.assertIn("'control_plane_unavailable_pre_device'", SQL)

    def test_v2_scope_is_absent(self) -> None:
        for forbidden in ("health_epoch", "sqlite", "typed_dm", "post_device_recovery"):
            self.assertNotIn(forbidden, SQL.lower())


if __name__ == "__main__":
    unittest.main()
