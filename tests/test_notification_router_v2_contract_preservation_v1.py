from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "supabase/migrations/20260912130000_notification_router_v2_contract_preservation_v1.sql"
BLOCKED_MIGRATION = ROOT / "supabase/migrations/20260912090000_notification_router_v2_explicit_challenge_evidence_v1.sql"


def _function(sql: str, name: str) -> str:
    match = re.search(
        rf"create or replace function public\.{re.escape(name)}\(\)"
        rf"(?P<body>[\s\S]*?)\n\$\$;",
        sql,
        re.IGNORECASE,
    )
    if match is None:
        raise AssertionError(f"missing function {name}")
    return match.group(0)


class NotificationRouterContractPreservationV1Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")
        cls.blocked_sql = BLOCKED_MIGRATION.read_text(encoding="utf-8")
        cls.auto_login = _function(cls.sql, "notification_router_auto_login_failure_v2")
        cls.general = _function(cls.sql, "notification_router_general_incident_v2")

    def test_forward_migration_does_not_edit_the_frozen_predecessor(self) -> None:
        self.assertIn("create or replace function public.notification_router_general_incident_v2()", self.blocked_sql)
        self.assertNotIn("'resolutionNote'", self.blocked_sql)
        self.assertIn("'resolutionNote'", self.general)

    def test_open_notification_preserves_active_payload_and_deep_link(self) -> None:
        self.assertIn("dashboard_url text := 'https://www.boostmybusinesses.com/instagram-dashboard/incidents?incident_id=' || new.id::text", self.general)
        for expression in (
            "'username', coalesce('@' || trim(leading '@' from new.account_username), '@non-renseigné')",
            "'summary', coalesce(new.safe_client_message, 'Une vérification opérationnelle est nécessaire.')",
            "'action', coalesce(new.action_required, 'Consulter BotApp.')",
            "'dashboardUrl', dashboard_url",
            "jsonb_build_object('incident_id', new.id, 'incident_type', new.incident_type)",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, self.general)

    def test_resolution_notification_preserves_all_active_context(self) -> None:
        self.assertIn("jsonb_strip_nulls(jsonb_build_object(", self.general)
        for expression in (
            "'resolutionNote', nullif(left(trim(coalesce(new.resolution_note, '')), 500), '')",
            "'resolutionReason', nullif(left(trim(coalesce(new.resolution_reason, '')), 160), '')",
            "'resolvedAt', new.resolved_at",
            "'operatorId', new.resolved_by",
            "'dashboardUrl', dashboard_url",
            "coalesce(new.resolved_at, new.last_seen_at, now())",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, self.general)

    def test_identity_verification_alone_is_not_challenge_evidence(self) -> None:
        self.assertIn("explicit_challenge boolean", self.auto_login)
        self.assertIn("(challenge|checkpoint)", self.auto_login)
        self.assertNotIn("(challenge|checkpoint|verification)", self.auto_login)
        self.assertRegex(
            self.auto_login,
            r"elsif explicit_challenge then\s+routed_type := 'auto_login\.challenge'",
        )
        self.assertIn(
            "elsif combined_reason ~ '(identity|verification|what_happened|connectivity|loading)'",
            self.auto_login,
        )

    def test_confirmed_mismatch_keeps_its_own_route(self) -> None:
        self.assertIn("confirmed_mismatch boolean", self.auto_login)
        self.assertRegex(
            self.auto_login,
            r"elsif confirmed_mismatch then\s+routed_type := 'auto_login\.identity_mismatch'",
        )

    def test_auto_login_ownership_suppresses_only_generic_duplicate(self) -> None:
        self.assertIn(
            "verification|mismatch|wrong_account|what_happened|connectivity|loading",
            self.general,
        )
        self.assertIn("then return new; end if;", self.general)

    def test_function_signature_and_security_contract_are_preserved(self) -> None:
        for function in (self.auto_login, self.general):
            with self.subTest(function=function.splitlines()[0]):
                self.assertRegex(function, r"returns trigger\s+language plpgsql")
                self.assertRegex(function, r"security definer\s+set search_path = ''")
        for name in (
            "notification_router_auto_login_failure_v2",
            "notification_router_general_incident_v2",
        ):
            self.assertIn(
                f"revoke all on function public.{name}()\n  from public, anon, authenticated;",
                self.sql,
            )
            self.assertIn(
                f"grant execute on function public.{name}()\n  to service_role;",
                self.sql,
            )


if __name__ == "__main__":
    unittest.main()
