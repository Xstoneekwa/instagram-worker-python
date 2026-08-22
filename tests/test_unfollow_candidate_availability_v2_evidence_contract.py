from pathlib import Path
import unittest


MIGRATION = Path(
    "supabase/migrations/"
    "20260822211317_repair_unfollow_candidate_availability_v2_evidence_contract.sql"
)


def migration_sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


class UnfollowCandidateAvailabilityV2EvidenceContractTests(unittest.TestCase):
    def test_first_healthy_exact_absence_is_a_24_hour_retryable_hold(self):
        sql = migration_sql()
        self.assertIn("'temporary_unavailable', v_reason", sql)
        self.assertIn("not_found_attempt_count", sql)
        self.assertIn("v_now + interval '24 hours'", sql)
        self.assertIn("terminal_at", sql)

    def test_same_run_replay_and_early_different_run_cannot_become_second_proof(self):
        sql = migration_sql()
        replay = sql.index("v_existing.source_run_id = p_source_run_id")
        hold = sql.index("v_now < v_existing.next_retry_at")
        terminal_update = sql.index("status = 'username_not_found_confirmed'", hold)
        self.assertLess(replay, hold)
        self.assertLess(hold, terminal_update)
        self.assertIn("'idempotent_replay', true", sql)
        self.assertIn("'hold_not_elapsed', true", sql)

    def test_only_second_independent_post_hold_proof_terminalizes(self):
        sql = migration_sql()
        self.assertIn("not_found_attempt_count = least(not_found_attempt_count + 1, 10)", sql)
        self.assertIn("source_run_id = p_source_run_id", sql)
        self.assertIn("next_retry_at = null", sql)
        self.assertIn("terminal_at = v_now", sql)

    def test_detailed_confirmed_reasons_are_preserved(self):
        sql = migration_sql()
        self.assertIn("username_not_found_confirmed_suggestion_only_stable", sql)
        self.assertIn("username_not_found_confirmed_non_match_only_stable", sql)
        self.assertIn("reason = v_reason", sql)
        self.assertNotIn("v_reason := 'username_not_found_confirmed'", sql)

    def test_technical_or_unhealthy_observation_cannot_terminalize_healthy_evidence(self):
        sql = migration_sql()
        self.assertIn("'healthy_absence_evidence_preserved', true", sql)
        self.assertIn("status = 'search_surface_unhealthy'", sql)
        self.assertIn("terminal_at = null", sql)

    def test_rpc_remains_service_role_only_and_security_definer(self):
        sql = migration_sql()
        self.assertIn("security definer", sql)
        self.assertIn("service_role_required", sql)
        self.assertIn("from public, anon, authenticated", sql)
        self.assertIn("to service_role", sql)


if __name__ == "__main__":
    unittest.main()
