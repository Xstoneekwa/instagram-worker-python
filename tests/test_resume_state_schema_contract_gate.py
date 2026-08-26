from __future__ import annotations

import re
import unittest
from pathlib import Path

from account_session_resume_state_contract import (
    DB_ALLOWED_RESUME_STATES,
    WORKER_EMITTABLE_RESUME_STATES,
)


ROOT = Path(__file__).resolve().parents[1]
CONTROL_PLANE_MIGRATION = ROOT / "supabase/migrations/20260826021814_control_plane_reliability_v1.sql"
PATCH3_MIGRATION = ROOT / "supabase/migrations/20260826222059_resume_plan_contract_v1.sql"


class ResumeStateSchemaContractGate(unittest.TestCase):
    def test_worker_emittable_states_are_allowed_by_database_check(self) -> None:
        sql = CONTROL_PLANE_MIGRATION.read_text()
        block = re.search(
            r"account_session_resume_plans_resume_state_check.*?check\s*\(resume_state in \((.*?)\)\)",
            sql,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(block)
        allowed = frozenset(re.findall(r"'([a-z_]+)'", block.group(1)))
        self.assertEqual(allowed, DB_ALLOWED_RESUME_STATES)
        self.assertLessEqual(WORKER_EMITTABLE_RESUME_STATES, allowed)

    def test_patch_does_not_add_business_outcome_to_state_check(self) -> None:
        sql = PATCH3_MIGRATION.read_text()
        self.assertNotRegex(
            sql,
            r"add constraint account_session_resume_plans_resume_state_check[\s\S]*partial_resumable",
        )
        self.assertIn("p_resume_state not in ('resume_requested', 'not_recoverable', 'completed')", sql)
        self.assertIn("'requests_enqueued', 0", sql)
        self.assertIn("'attempts_consumed', 0", sql)

    def test_reconciliation_is_bounded_locked_and_never_creates_s4(self) -> None:
        sql = PATCH3_MIGRATION.read_text()
        self.assertIn("for update of p skip locked", sql.lower())
        self.assertIn("limit greatest(1, least(coalesce(p_limit, 100), 100))", sql)
        self.assertIn("v_row.execution_attempt_no between 1 and 3", sql)
        self.assertIn("v_row.retry_index between 0 and 2", sql)
        self.assertNotRegex(sql, r"insert\s+into\s+public\.account_run_requests")

    def test_terminal_plan_digest_and_rpc_permissions_fail_closed(self) -> None:
        sql = PATCH3_MIGRATION.read_text()
        self.assertIn("'sha256'", sql)
        self.assertIn("p_terminal_plan_digest !~ '^[0-9a-f]{64}$'", sql)
        self.assertIn("from public, anon, authenticated", sql)
        self.assertIn("to service_role", sql)


if __name__ == "__main__":
    unittest.main()
