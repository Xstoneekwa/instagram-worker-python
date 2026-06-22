"""Contract tests for client actor_type allowlist migration (SQL file only, no DB)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20260622225616_create_account_run_request_allow_client_actor.sql"
)
PRIOR_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20260604120000_login_challenge_run_control.sql"
)


class CreateAccountRunRequestClientActorMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = MIGRATION_PATH.read_text(encoding="utf-8")
        self.prior_sql = PRIOR_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_migration_file_exists(self) -> None:
        self.assertTrue(MIGRATION_PATH.is_file())

    def test_client_actor_type_accepted(self) -> None:
        self.assertRegex(
            self.sql,
            r"if v_actor_type not in \([^)]*'client'[^)]*\)",
        )

    def test_prior_actor_types_remain_accepted(self) -> None:
        for actor in ("admin", "assistant", "ops", "system", "internal"):
            self.assertIn(f"'{actor}'", self.sql)

    def test_unknown_actor_type_still_rejected(self) -> None:
        self.assertIn("invalid_actor_type", self.sql)
        self.assertNotIn("'public'", self.sql)
        self.assertNotIn("'anonymous'", self.sql)

    def test_prior_migration_not_modified(self) -> None:
        self.assertNotIn("'client'", self.prior_sql)
        self.assertIn("invalid_actor_type", self.prior_sql)

    def test_service_role_grant_not_broadened_in_migration(self) -> None:
        self.assertNotIn("grant execute", self.sql.lower())
        self.assertNotIn("to authenticated", self.sql.lower())
        self.assertNotIn("to anon", self.sql.lower())

    def test_table_actor_type_check_includes_client(self) -> None:
        self.assertIn("account_run_requests_actor_type_check", self.sql)
        self.assertRegex(
            self.sql,
            r"check \(actor_type in \([^)]*'client'[^)]*\)\)",
        )

    def test_actor_type_is_audit_only_comment_present(self) -> None:
        self.assertIn("audit", self.sql.lower())


if __name__ == "__main__":
    unittest.main()
