from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "supabase/migrations/20260912150000_notification_router_v2_resolution_routing_parity_fix_v2.sql"
PREDECESSOR = ROOT / "supabase/migrations/20260912130000_notification_router_v2_contract_preservation_v1.sql"

PRODUCTION_OWNERSHIP = re.compile(
    r"password|challenge|identity|checkpoint|device.*login|provision"
)
CORRECTED_OWNERSHIP = re.compile(
    r"password|challenge|identity|checkpoint|device.*login|provision|"
    r"verification|mismatch|wrong_account|what_happened|connectivity|loading"
)


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


@dataclass(frozen=True)
class Incident:
    incident_type: str
    reason: str = ""
    failure_reason: str = ""

    @property
    def combined_reason(self) -> str:
        return f"{self.reason} {self.failure_reason} {self.incident_type}".lower()


def _owned(incident: Incident, pattern: re.Pattern[str]) -> bool:
    return "login" in incident.incident_type.lower() or bool(pattern.search(incident.combined_reason))


def _production_general_route(incident: Incident, status: str) -> str | None:
    if _owned(incident, PRODUCTION_OWNERSHIP):
        return None
    if status in {"resolved", "ignored"}:
        return "incident.resolved"
    if status in {"open", "acknowledged"}:
        return "incident.opened"
    return None


def _corrected_general_route(incident: Incident, status: str) -> str | None:
    if status in {"resolved", "ignored"}:
        if _owned(incident, PRODUCTION_OWNERSHIP):
            return None
        return "incident.resolved"
    if status not in {"open", "acknowledged"}:
        return None
    if _owned(incident, CORRECTED_OWNERSHIP):
        return None
    return "incident.opened"


class NotificationRouterResolutionRoutingParityFixV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")
        cls.predecessor = PREDECESSOR.read_text(encoding="utf-8")
        cls.auto_login = _function(cls.sql, "notification_router_auto_login_failure_v2")
        cls.general = _function(cls.sql, "notification_router_general_incident_v2")

    def test_new_forward_migration_leaves_frozen_predecessor_as_evidence(self) -> None:
        self.assertIn("-- Notification Router V2 forward fix.", self.predecessor)
        self.assertIn("production_auto_login_owned boolean", self.general)
        self.assertIn("corrected_auto_login_owned boolean", self.general)

    def test_terminal_branch_uses_only_production_ownership(self) -> None:
        terminal = self.general.index("if new.status in ('resolved','ignored') then")
        production_guard = self.general.index("if production_auto_login_owned then return new; end if;")
        resolved_emit = self.general.index("'incident.resolved:'")
        active_status = self.general.index("if new.status not in ('open','acknowledged') then")
        corrected_guard = self.general.index("if corrected_auto_login_owned then return new; end if;")
        self.assertLess(terminal, production_guard)
        self.assertLess(production_guard, resolved_emit)
        self.assertLess(resolved_emit, active_status)
        self.assertLess(active_status, corrected_guard)

    def test_proved_resolved_regressions_match_production(self) -> None:
        for incident_type in (
            "active_instagram_account_mismatch",
            "email_verification_code_required",
        ):
            incident = Incident(incident_type)
            with self.subTest(incident_type=incident_type):
                self.assertEqual(
                    _corrected_general_route(incident, "resolved"),
                    _production_general_route(incident, "resolved"),
                )
                self.assertEqual(_corrected_general_route(incident, "resolved"), "incident.resolved")

    def test_email_code_is_explicit_challenge_but_generic_identity_failure_is_not(self) -> None:
        self.assertRegex(
            self.auto_login,
            r"lower\(coalesce\(new\.incident_type, ''\)\) in \(\s*"
            r"'auto_login_verification_required',\s*"
            r"'email_verification_code_required'\s*\)",
        )
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

    def test_complete_transition_matrix_preserves_production_except_active_ownership_delta(self) -> None:
        scenarios = (
            Incident("active_instagram_account_mismatch"),
            Incident("email_verification_code_required"),
            Incident("run_identity_verification_failed"),
            Incident("run_worker_failure", reason="what_happened"),
            Incident("run_worker_failure", reason="connectivity"),
            Incident("run_worker_failure", reason="loading"),
            Incident("auto_login_failed"),
            Incident("login_challenge_pending"),
            Incident("instagram_ads_data_consent_popup_requires_operator"),
            Incident("assigned_instagram_package_unavailable"),
            Incident("host_storage_critical"),
        )
        for incident in scenarios:
            for status in ("open", "acknowledged", "resolved"):
                with self.subTest(incident=incident.incident_type, reason=incident.reason, status=status):
                    production = _production_general_route(incident, status)
                    corrected = _corrected_general_route(incident, status)
                    if status in {"open", "acknowledged"} and not _owned(incident, PRODUCTION_OWNERSHIP) and _owned(incident, CORRECTED_OWNERSHIP):
                        self.assertIsNone(corrected)
                    else:
                        self.assertEqual(corrected, production)

    def test_existing_login_owned_resolutions_remain_suppressed(self) -> None:
        for incident_type in (
            "auto_login_failed",
            "auto_login_identity_mismatch",
            "login_challenge_pending",
            "login_package_mismatch",
            "login_verification_code_required",
            "run_identity_verification_failed",
        ):
            incident = Incident(incident_type)
            with self.subTest(incident_type=incident_type):
                self.assertIsNone(_production_general_route(incident, "resolved"))
                self.assertIsNone(_corrected_general_route(incident, "resolved"))

    def test_resolution_payload_preserves_full_production_contract(self) -> None:
        self.assertIn("jsonb_strip_nulls(jsonb_build_object(", self.general)
        for expression in (
            "'resolutionNote', nullif(left(trim(coalesce(new.resolution_note, '')), 500), '')",
            "'resolutionReason', nullif(left(trim(coalesce(new.resolution_reason, '')), 160), '')",
            "'resolvedAt', new.resolved_at",
            "'operatorId', new.resolved_by",
            "'dashboardUrl', dashboard_url",
            "jsonb_build_object('incident_id', new.id, 'incident_type', new.incident_type)",
            "coalesce(new.resolved_at, new.last_seen_at, now())",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, self.general)

    def test_open_payload_preserves_full_production_contract(self) -> None:
        for expression in (
            "'summary', coalesce(new.safe_client_message, 'Une vérification opérationnelle est nécessaire.')",
            "'action', coalesce(new.action_required, 'Consulter BotApp.')",
            "'dashboardUrl', dashboard_url",
            "coalesce(new.last_seen_at, new.created_at)",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, self.general)

    def test_function_security_and_grant_contract_is_unchanged(self) -> None:
        for function in (self.auto_login, self.general):
            with self.subTest(function=function.splitlines()[0]):
                self.assertRegex(function, r"returns trigger\s+language plpgsql")
                self.assertRegex(function, r"security definer\s+set search_path = ''")
        for name in (
            "notification_router_auto_login_failure_v2",
            "notification_router_general_incident_v2",
        ):
            self.assertIn(
                f"revoke all on function public.{name}()\n"
                "  from public, anon, authenticated;",
                self.sql,
            )
            self.assertIn(
                f"grant execute on function public.{name}()\n"
                "  to service_role;",
                self.sql,
            )


if __name__ == "__main__":
    unittest.main()
