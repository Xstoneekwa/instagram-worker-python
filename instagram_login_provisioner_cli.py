"""Safe CLI for one isolated Instagram login provisioning attempt.

This entrypoint intentionally does not accept passwords or secret refs as
arguments. Credentials are resolved through the runtime/Vault boundary and the
SecretValue is passed to the existing executor.
"""

from __future__ import annotations

import argparse
import os
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

from instagram_account_status_publisher import publish_instagram_account_status
from instagram_credentials_runtime_access import get_instagram_credentials_for_login, redact_credentials_payload
from instagram_login_ui_probe import extract_login_screen_signals_from_hierarchy
from instagram_login_provisioner_orchestrator import (
    DEFAULT_INSTAGRAM_PACKAGE_NAME,
    DEFAULT_POST_APP_START_WAIT_MS,
    POST_EMAIL_CODE_PASSWORD_SCREENS,
    run_email_code_resume_flow,
    run_login_provisioning_flow,
)
from instagram_supabase_vault_reader import SupabaseVaultClient
from supabase_client import _request_json

ConnectFunc = Callable[[Optional[str]], Any]
CredentialsLookup = Callable[[str, str], Optional[dict[str, Any]]]
SecretReader = Callable[[str], Any]
RunFlowFunc = Callable[..., Any]
StatusPublisher = Callable[..., dict[str, Any]]
DEFAULT_LOG_JSONL = "logs/instagram_login_provisioner.jsonl"
CREDENTIALS_DIAGNOSTIC_KEYS = (
    "credentials_error_code",
    "credentials_invalid_reason",
    "credentials_stage",
    "credential_metadata_found",
    "credentials_status",
    "credentials_version",
    "secret_provider",
    "username_matches_expected",
    "secret_loaded",
    "injectable_password_only",
    "secret_value_safe_for_injection",
    "guard_would_block_revealed_value",
)
OPERATOR_SMOKE_LIFECYCLE_STATUSES = ("active", "paused", "canceled", "onboarding", "archived", "stopped", "unknown")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one safe Instagram login provisioning flow and print JSON.",
    )
    parser.add_argument("--device-serial", default=None, help="Optional explicit adb/uiautomator2 serial.")
    parser.add_argument("--expected-username", required=True, help="Expected Instagram username.")
    parser.add_argument("--account-id", required=True, help="Instagram account UUID used for credential lookup.")
    parser.add_argument("--package-name", default=None, help="Instagram package to start.")
    parser.add_argument("--expected-app-instance-id", default="", help="Optional safe app instance id for guard metadata.")
    parser.add_argument(
        "--start-app-before-probe",
        action="store_true",
        default=True,
        help="Start the package before probing (default).",
    )
    parser.add_argument(
        "--observe-current-screen-only",
        action="store_true",
        help="Diagnostic mode: skip app_start and only observe the current screen.",
    )
    parser.add_argument(
        "--post-start-wait-ms",
        type=int,
        default=DEFAULT_POST_APP_START_WAIT_MS,
        help="Bounded wait after app_start; orchestrator clamps the value.",
    )
    parser.add_argument(
        "--post-submit-timeout-ms",
        type=int,
        default=10000,
        help="Bounded post-submit settling timeout in milliseconds.",
    )
    parser.add_argument(
        "--operator-smoke-previous-account-username",
        default="",
        help="Smoke-only suggested/old username override for previous account lifecycle lookup.",
    )
    parser.add_argument(
        "--operator-smoke-active-account-username",
        default="",
        help="Smoke-only active logged-in username override; alias for the previous-account lifecycle gate.",
    )
    parser.add_argument(
        "--operator-smoke-lifecycle-status",
        choices=OPERATOR_SMOKE_LIFECYCLE_STATUSES,
        default="unknown",
        help="Smoke-only lifecycle status for the suggested/old username override.",
    )
    parser.add_argument(
        "--operator-smoke-clone-reuse-allowed",
        choices=("true", "false"),
        default="false",
        help="Smoke-only clone reuse gate for the suggested/old username override.",
    )
    parser.add_argument(
        "--operator-smoke-allow-logout-fallback",
        choices=("true", "false"),
        default="false",
        help="Smoke-only explicit gate for controlled logout fallback after old active account validation.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Route/prepare only; do not load Vault or submit.")
    parser.add_argument("--no-submit", action="store_true", help="Alias for --dry-run.")
    parser.add_argument("--publish", action="store_true", help="Explicitly allow controlled backend status publish.")
    parser.add_argument("--no-publish", action="store_true", help="Force status publishing disabled.")
    parser.add_argument("--run-id", default="", help="Optional safe run id. Defaults to a generated UUID.")
    parser.add_argument("--log-jsonl", default=DEFAULT_LOG_JSONL, help="Safe JSONL log path.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable safe JSON.")
    parser.add_argument(
        "--resume-email-code-stdin",
        action="store_true",
        help="Resume from email verification screen using one line read from stdin.",
    )
    parser.add_argument(
        "--resume-email-code-from-action",
        action="store_true",
        help="Resume by consuming one ephemeral verification code linked to a dashboard action.",
    )
    parser.add_argument(
        "--verification-action-id",
        default="",
        help="Optional dashboard action id when resuming from stored verification code.",
    )
    return parser


def run_cli_command(
    args: argparse.Namespace,
    *,
    connect_func: ConnectFunc | None = None,
    credentials_lookup: CredentialsLookup | None = None,
    secret_reader: SecretReader | None = None,
    run_flow_func: RunFlowFunc | None = None,
    status_publisher: StatusPublisher | None = None,
) -> tuple[int, dict[str, Any]]:
    run_id = _safe_run_id(getattr(args, "run_id", "") or str(uuid.uuid4()))
    _load_dotenv_if_present()
    package_required_reason = _package_name_required_reason(args)
    if package_required_reason:
        summary = _safe_summary_from_error(package_required_reason, args=args, run_id=run_id)
        _append_safe_jsonl(summary, args=args)
        return 1, summary
    try:
        device = (connect_func or _connect_uiautomator2)(str(args.device_serial or "") or None)
    except Exception:
        summary = _safe_summary_from_error("connect_failed", args=args, run_id=run_id)
        _append_safe_jsonl(summary, args=args)
        return 1, summary

    getter = _build_credentials_getter(
        credentials_lookup=credentials_lookup,
        secret_reader=secret_reader,
    )
    flow = run_flow_func or run_login_provisioning_flow
    resume_flow = run_email_code_resume_flow
    previous_account_lifecycle_lookup = _build_previous_account_lifecycle_lookup(args)
    operator_smoke_active_username = _normalize_public_username(
        getattr(args, "operator_smoke_active_account_username", "")
        or getattr(args, "operator_smoke_previous_account_username", "")
    )
    publish_enabled = _publish_enabled_from_args_env(args)
    publisher = _build_status_publisher(
        args=args,
        run_id=run_id,
        status_publisher=status_publisher or publish_instagram_account_status,
    ) if publish_enabled else None

    if bool(getattr(args, "resume_email_code_stdin", False)) or bool(getattr(args, "resume_email_code_from_action", False)):
        from instagram_credentials_runtime_access import SecretValue

        package_guard_result = _preflight_expected_package(device, args=args, run_id=run_id, resume=True)
        if package_guard_result is not None:
            summary = _safe_summary_from_result(package_guard_result, args=args, run_id=run_id)
            _append_safe_jsonl(summary, args=args)
            return 1, summary

        preflight_result = _preflight_email_code_resume(device, args=args, run_id=run_id)
        if preflight_result is not None:
            action_id = str(getattr(args, "verification_action_id", "") or "").strip()
            if bool(getattr(args, "resume_email_code_from_action", False)) and action_id:
                try:
                    from login_challenge_runtime import sync_verification_action_after_email_code_resume

                    sync_verification_action_after_email_code_resume(
                        action_id=action_id,
                        account_id=str(args.account_id or ""),
                        run_id=run_id,
                        ok=False,
                        final_outcome=str(getattr(preflight_result, "final_outcome", "") or ""),
                        failure_reason=str(getattr(preflight_result, "failure_reason", "") or ""),
                        screen_type=str((getattr(preflight_result, "safe_metadata", {}) or {}).get("screen_type") or ""),
                    )
                except Exception:
                    pass
            summary = _safe_summary_from_result(preflight_result, args=args, run_id=run_id)
            _append_safe_jsonl(summary, args=args)
            return 1, summary

        verification_code = SecretValue("")
        if bool(getattr(args, "resume_email_code_stdin", False)):
            verification_code = SecretValue(sys.stdin.readline().strip())
        result = resume_flow(
            device,
            account_id=str(args.account_id or ""),
            expected_username=str(args.expected_username or ""),
            verification_code=verification_code,
            credentials_getter=getter,
            action_id=str(getattr(args, "verification_action_id", "") or "").strip() or None,
            consume_from_action=bool(getattr(args, "resume_email_code_from_action", False)),
            run_id=run_id,
            publish_enabled=publish_enabled,
            publisher=publisher,
            package_name=_effective_package_name(args),
            run_type="login_email_code_resume",
            device_serial=str(getattr(args, "device_serial", "") or ""),
            expected_app_instance_id=str(getattr(args, "expected_app_instance_id", "") or ""),
            post_submit_timeout_ms=int(args.post_submit_timeout_ms or 0),
        )
        summary = _safe_summary_from_result(result, args=args, run_id=run_id)
        _append_safe_jsonl(summary, args=args)
        return (0 if bool(getattr(result, "ok", False)) else 1), summary

    result = flow(
        device,
        account_id=str(args.account_id or ""),
        expected_username=str(args.expected_username or ""),
        credentials_getter=getter,
        previous_account_lifecycle_lookup=previous_account_lifecycle_lookup,
        operator_smoke_active_account_username=operator_smoke_active_username or None,
        publish_enabled=publish_enabled,
        publisher=publisher,
        dry_run=bool(args.dry_run or args.no_submit or args.observe_current_screen_only),
        start_app_before_probe=bool(args.start_app_before_probe),
        observe_current_screen_only=bool(args.observe_current_screen_only),
        package_name=_effective_package_name(args),
        run_id=run_id,
        run_type="login_provisioning",
        device_serial=str(getattr(args, "device_serial", "") or ""),
        expected_app_instance_id=str(getattr(args, "expected_app_instance_id", "") or ""),
        post_start_wait_ms=int(args.post_start_wait_ms or DEFAULT_POST_APP_START_WAIT_MS),
        post_submit_timeout_ms=int(args.post_submit_timeout_ms or 0),
        operator_smoke_allow_logout_fallback=_parse_bool_choice(
            getattr(args, "operator_smoke_allow_logout_fallback", "false")
        ),
    )
    summary = _safe_summary_from_result(result, args=args, run_id=run_id)
    _append_safe_jsonl(summary, args=args)
    return (0 if bool(getattr(result, "ok", False)) else 1), summary


def _preflight_email_code_resume(
    device: Any,
    *,
    args: argparse.Namespace,
    run_id: str,
) -> Any | None:
    """Return a terminal safe result when resume preconditions are not met."""

    try:
        hierarchy = device.dump_hierarchy(compressed=False)
        signals = extract_login_screen_signals_from_hierarchy(
            hierarchy,
            expected_username=str(getattr(args, "expected_username", "") or ""),
        )
    except Exception:
        return _resume_preflight_result(
            run_id=run_id,
            reason="resume_email_code_screen_not_active",
            screen_type="unknown",
            safe_metadata={"resume_preflight_error": "screen_observe_failed"},
        )

    screen_type = str(signals.get("screen_type") or "unknown").strip() or "unknown"
    if screen_type in POST_EMAIL_CODE_PASSWORD_SCREENS:
        if bool(getattr(args, "resume_email_code_from_action", False)):
            code_state = _verification_code_action_state(
                action_id=str(getattr(args, "verification_action_id", "") or "").strip(),
                account_id=str(getattr(args, "account_id", "") or "").strip(),
            )
            if not code_state.get("ready"):
                if screen_type in {"login_form_empty", "login_form_prefilled_username"}:
                    return _resume_preflight_result(
                        run_id=run_id,
                        reason="resume_email_code_screen_not_active",
                        screen_type=screen_type,
                        safe_metadata={
                            "resume_preflight_email_code_challenge_present": False,
                            "verification_action_status": str(code_state.get("action_status") or ""),
                            "verification_submission_present": bool(code_state.get("submission_present")),
                        },
                    )
                return _resume_preflight_result(
                    run_id=run_id,
                    reason="code_missing",
                    screen_type=screen_type,
                    safe_metadata={
                        "resume_preflight_post_code_password_screen": True,
                        "verification_action_status": str(code_state.get("action_status") or ""),
                        "verification_submission_present": bool(code_state.get("submission_present")),
                    },
                )
        return None
    if screen_type != "email_code_challenge":
        return _resume_preflight_result(
            run_id=run_id,
            reason="resume_email_code_screen_not_active",
            screen_type=screen_type,
            safe_metadata={
                "resume_preflight_email_code_challenge_present": False,
                "resume_preflight_recommended_next_run": (
                    "full_login_retry" if screen_type in {"login_form_empty", "login_form_prefilled_username"} else ""
                ),
            },
        )

    if bool(getattr(args, "resume_email_code_from_action", False)):
        code_state = _verification_code_action_state(
            action_id=str(getattr(args, "verification_action_id", "") or "").strip(),
            account_id=str(getattr(args, "account_id", "") or "").strip(),
        )
        if not code_state.get("ready"):
            return _resume_preflight_result(
                run_id=run_id,
                reason="code_missing",
                screen_type=screen_type,
                safe_metadata={
                    "resume_preflight_email_code_challenge_present": True,
                    "verification_action_status": str(code_state.get("action_status") or ""),
                    "verification_submission_present": bool(code_state.get("submission_present")),
                },
            )

    return None


def _effective_package_name(args: argparse.Namespace) -> str:
    text = str(getattr(args, "package_name", "") or "").strip()
    return text or DEFAULT_INSTAGRAM_PACKAGE_NAME


def _package_name_required_reason(args: argparse.Namespace) -> str:
    serial = str(getattr(args, "device_serial", "") or "").strip()
    if not serial:
        return ""
    if serial.lower().startswith("emulator-"):
        return ""
    if bool(getattr(args, "observe_current_screen_only", False)):
        return ""
    if str(getattr(args, "package_name", "") or "").strip():
        return ""
    return "package_name_required_for_physical_clone"


def _foreground_package(device: Any) -> str:
    try:
        app_current = getattr(device, "app_current", None)
        if callable(app_current):
            current = app_current() or {}
            if isinstance(current, dict):
                return str(current.get("package") or current.get("packageName") or "").strip()
    except Exception:
        return ""
    return ""


def _preflight_expected_package(
    device: Any,
    *,
    args: argparse.Namespace,
    run_id: str,
    resume: bool,
) -> Any | None:
    expected = _effective_package_name(args)
    actual = _foreground_package(device)
    if not expected or not actual or expected == actual:
        return None
    from instagram_credentials_runtime_access import SecretValue

    return run_email_code_resume_flow(
        device,
        account_id=str(getattr(args, "account_id", "") or ""),
        expected_username=str(getattr(args, "expected_username", "") or ""),
        verification_code=SecretValue(""),
        action_id=str(getattr(args, "verification_action_id", "") or "").strip() or None,
        consume_from_action=False,
        run_id=run_id,
        publish_enabled=False,
        publisher=None,
        package_name=expected,
        run_type="login_email_code_resume" if resume else "login_provisioning",
        device_serial=str(getattr(args, "device_serial", "") or ""),
        expected_app_instance_id=str(getattr(args, "expected_app_instance_id", "") or ""),
        post_submit_timeout_ms=0,
    )


def _verification_code_action_state(*, action_id: str, account_id: str) -> dict[str, Any]:
    if not action_id or not account_id:
        return {"ready": False, "reason": "missing_action_or_account"}
    try:
        actions = _request_json(
            "GET",
            "account_dashboard_actions",
            query={
                "select": "id,status,action_type",
                "id": f"eq.{action_id}",
                "account_id": f"eq.{account_id}",
                "limit": "1",
            },
        ) or []
        submissions = _request_json(
            "GET",
            "account_verification_code_submissions",
            query={
                "select": "id,status,expires_at",
                "action_id": f"eq.{action_id}",
                "account_id": f"eq.{account_id}",
                "status": "in.(code_submitted,ready_for_resume)",
                "order": "updated_at.desc",
                "limit": "1",
            },
        ) or []
    except Exception:
        return {"ready": False, "reason": "verification_action_lookup_failed"}
    action_status = str(actions[0].get("status") or "") if actions else ""
    return {
        "ready": action_status == "code_submitted" and bool(submissions),
        "action_status": action_status,
        "submission_present": bool(submissions),
    }


def _resume_preflight_result(
    *,
    run_id: str,
    reason: str,
    screen_type: str,
    safe_metadata: dict[str, Any] | None = None,
) -> Any:
    safe_screen_type = str(screen_type or "unknown").strip() or "unknown"
    return SimpleNamespace(
        ok=False,
        completed=False,
        final_outcome="verification_pending" if reason == "code_missing" else safe_screen_type,
        final_login_status="verification_pending" if reason == "code_missing" else "logged_out",
        final_provisioning_status="login_verification_pending" if reason == "code_missing" else "login_pending",
        final_onboarding_status="verification_pending" if reason == "code_missing" else "credentials_required",
        reason=reason,
        failure_reason=reason,
        retry_count=0,
        actions_taken=["route:email_code_resume_preflight"],
        published=False,
        publish_reason="disabled",
        should_publish_status=False,
        timings={"total_ms": 0},
        warnings=[],
        safe_metadata={
            "run_id": run_id,
            "screen_type": safe_screen_type,
            "router_decision": "email_code_resume_preflight",
            "selected_route": "email_code_resume_preflight",
            "selected_route_reason": reason,
            **(safe_metadata or {}),
        },
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    code, summary = run_cli_command(args)
    rendered = _render_safe_json(summary)
    if args.json:
        print(rendered)
    else:
        _print_human_summary(summary)
    return code


def _connect_uiautomator2(serial: Optional[str]) -> Any:
    import uiautomator2 as u2

    if serial:
        return u2.connect(serial)
    return u2.connect()


def _build_credentials_getter(
    *,
    credentials_lookup: CredentialsLookup | None,
    secret_reader: SecretReader | None,
) -> Callable[[str], Any]:
    lookup = credentials_lookup or _lookup_active_instagram_credentials
    reader = secret_reader or _read_vault_secret_string

    def _getter(account_id: str) -> Any:
        return get_instagram_credentials_for_login(
            account_id=str(account_id or ""),
            credentials_lookup=lookup,
            secret_reader=reader,
        )

    return _getter


def _lookup_active_instagram_credentials(account_id: str, provider: str) -> dict[str, Any] | None:
    rows = _request_json(
        "GET",
        "account_credentials",
        query={
            "select": (
                "account_id,provider,username_at_submission,secret_provider,"
                "secret_ref,credentials_version,status,reauth_required"
            ),
            "account_id": f"eq.{str(account_id or '').strip()}",
            "provider": f"eq.{str(provider or 'instagram').strip().lower()}",
            "status": "eq.active",
            "order": "credentials_version.desc",
            "limit": "1",
        },
    )
    if isinstance(rows, list) and rows:
        row = rows[0]
        return dict(row) if isinstance(row, dict) else None
    return None


def _read_vault_secret_string(secret_ref: str) -> str:
    return SupabaseVaultClient.from_supabase_client().read_secret(secret_ref)


def _load_dotenv_if_present() -> None:
    candidates = [
        Path(os.getcwd()) / ".env",
        Path(__file__).resolve().parent / ".env",
    ]
    seen: set[str] = set()
    for path in candidates:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def _credentials_fields_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: metadata.get(key) for key in CREDENTIALS_DIAGNOSTIC_KEYS if key in metadata}


def _build_operator_smoke_previous_account_lifecycle_lookup(args: argparse.Namespace) -> Callable[[str, dict[str, Any]], dict[str, Any]] | None:
    username = _normalize_public_username(
        getattr(args, "operator_smoke_active_account_username", "")
        or getattr(args, "operator_smoke_previous_account_username", "")
    )
    if not username:
        return None
    lifecycle_status = str(getattr(args, "operator_smoke_lifecycle_status", "") or "unknown").strip().lower()
    if lifecycle_status not in OPERATOR_SMOKE_LIFECYCLE_STATUSES:
        lifecycle_status = "unknown"
    clone_reuse_allowed = str(
        getattr(args, "operator_smoke_clone_reuse_allowed", "false") or "false"
    ).strip().lower() == "true"

    def _lookup(candidate_username: str, _context: dict[str, Any]) -> dict[str, Any]:
        candidate = _normalize_public_username(candidate_username)
        if candidate != username:
            return {
                "lifecycle_status": "unknown",
                "clone_reuse_allowed": False,
                "source": "operator_smoke_override",
                "reason": "operator_smoke_override_username_mismatch",
            }
        return {
            "lifecycle_status": lifecycle_status,
            "clone_reuse_allowed": clone_reuse_allowed,
            "source": "operator_smoke_override",
            "reason": "operator_smoke_override",
        }

    return _lookup


def _build_previous_account_lifecycle_lookup(args: argparse.Namespace) -> Callable[[str, dict[str, Any]], dict[str, Any]] | None:
    operator_lookup = _build_operator_smoke_previous_account_lifecycle_lookup(args)
    if operator_lookup is not None:
        return operator_lookup
    return _build_stale_session_previous_account_lifecycle_lookup(args)


OPEN_ASSIGNMENT_STATUSES = "pending,reserved,active"
ACTIVE_REQUEST_STATUSES = "queued,claimed,starting,running"
ACTIVE_RUN_STATUSES = "queued,claimed,starting,running,active"
ACTIVE_SUBSCRIPTION_ACCOUNT_STATUSES = "active,paused"


def _build_stale_session_previous_account_lifecycle_lookup(
    args: argparse.Namespace,
) -> Callable[[str, dict[str, Any]], dict[str, Any]] | None:
    expected_app_instance_id = str(getattr(args, "expected_app_instance_id", "") or "").strip()
    target_account_id = str(getattr(args, "account_id", "") or "").strip()
    if not expected_app_instance_id or not target_account_id:
        return None

    def _lookup(candidate_username: str, context: dict[str, Any]) -> dict[str, Any]:
        candidate = _normalize_public_username(candidate_username)
        expected_username = _normalize_public_username(context.get("expected_username"))
        if not candidate or candidate == expected_username:
            return _stale_lifecycle_block("candidate_not_stale_previous_account")
        target_assignment = _first_row(
            "account_assignments",
            {
                "select": "id,account_id,app_instance_id,status",
                "account_id": f"eq.{target_account_id}",
                "app_instance_id": f"eq.{expected_app_instance_id}",
                "status": "eq.active",
                "limit": "1",
            },
        )
        if not target_assignment:
            return _stale_lifecycle_block("target_assignment_not_verified")

        old_account = _first_row(
            "ig_accounts",
            {
                "select": "id,username",
                "username": f"ilike.{candidate}",
                "limit": "1",
            },
        )
        if not old_account:
            return _stale_lifecycle_allow("deleted", "stale_account_unmanaged_or_deleted")

        old_account_id = str(old_account.get("id") or "").strip()
        if not old_account_id:
            return _stale_lifecycle_block("old_account_id_missing")
        if old_account_id == target_account_id:
            return _stale_lifecycle_block("old_account_is_target")

        protection_reason = _stale_account_protection_reason(
            old_account_id=old_account_id,
            expected_app_instance_id=expected_app_instance_id,
        )
        if protection_reason:
            return _stale_lifecycle_block(protection_reason)
        return _stale_lifecycle_allow("unmanaged", "stale_account_present_without_active_dependency")

    return _lookup


def _stale_account_protection_reason(*, old_account_id: str, expected_app_instance_id: str) -> str:
    if _first_row(
        "account_assignments",
        {
            "select": "id,status,app_instance_id",
            "account_id": f"eq.{old_account_id}",
            "status": f"in.({OPEN_ASSIGNMENT_STATUSES})",
            "limit": "1",
        },
    ):
        return "old_account_has_open_assignment"
    if _first_row(
        "client_instagram_accounts",
        {
            "select": "id,client_id,account_id",
            "account_id": f"eq.{old_account_id}",
            "limit": "1",
        },
    ):
        return "old_account_has_client_ownership"
    if _first_row(
        "client_subscription_accounts",
        {
            "select": "id,account_id,status",
            "account_id": f"eq.{old_account_id}",
            "status": f"in.({ACTIVE_SUBSCRIPTION_ACCOUNT_STATUSES})",
            "limit": "1",
        },
    ):
        return "old_account_has_active_subscription_scope"
    if _first_row(
        "account_run_requests",
        {
            "select": "id,status,account_id",
            "account_id": f"eq.{old_account_id}",
            "status": f"in.({ACTIVE_REQUEST_STATUSES})",
            "limit": "1",
        },
    ):
        return "old_account_has_active_run_request"
    if _first_row(
        "ig_runs",
        {
            "select": "id,status,account_id",
            "account_id": f"eq.{old_account_id}",
            "status": f"in.({ACTIVE_RUN_STATUSES})",
            "limit": "1",
        },
    ):
        return "old_account_has_active_run"
    return ""


def _stale_lifecycle_allow(stale_account_state: str, reason: str) -> dict[str, Any]:
    return {
        "lifecycle_status": "archived",
        "clone_reuse_allowed": True,
        "source": "stale_replacement_safety_check",
        "reason": reason,
        "stale_session_replacement_allowed": True,
        "replacement_safety_status": "allowed",
        "stale_account_state": stale_account_state,
        "connected_account_state": stale_account_state,
        "previous_account_state": stale_account_state,
    }


def _stale_lifecycle_block(reason: str) -> dict[str, Any]:
    return {
        "lifecycle_status": "unknown",
        "clone_reuse_allowed": False,
        "source": "stale_replacement_safety_check",
        "reason": reason,
        "stale_session_replacement_allowed": False,
        "replacement_safety_status": "blocked",
    }


def _first_row(table: str, query: dict[str, str]) -> dict[str, Any] | None:
    rows = _request_json("GET", table, query=query)
    if isinstance(rows, list) and rows:
        row = rows[0]
        return row if isinstance(row, dict) else None
    return None


def _normalize_public_username(value: Any) -> str:
    return str(value or "").strip().lstrip("@").lower()


def _parse_bool_choice(value: Any) -> bool:
    return str(value or "").strip().lower() == "true"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _publish_enabled_from_args_env(args: argparse.Namespace) -> bool:
    if bool(getattr(args, "no_publish", False)):
        return False
    return bool(getattr(args, "publish", False)) and _env_bool("LOGIN_PROVISIONER_PUBLISH_ENABLED", False)


def _build_status_publisher(
    *,
    args: argparse.Namespace,
    run_id: str,
    status_publisher: StatusPublisher,
) -> StatusPublisher:
    def _publisher(**payload: Any) -> dict[str, Any]:
        metadata = dict(payload.get("metadata") or {})
        metadata.update(
            {
                "source": "login_provisioner",
                "flow_name": "entry2e5p5_login_provisioner",
                "run_id": _safe_run_id(run_id),
            }
        )
        safe_payload = {
            **payload,
            "metadata": metadata,
        }
        return status_publisher(**redact_credentials_payload(safe_payload))

    return _publisher


def _safe_summary_from_error(reason: str, *, args: argparse.Namespace, run_id: str) -> dict[str, Any]:
    return _clean_summary(
        {
            "run_id": run_id,
            "flow_name": "entry2e5p5_login_provisioner",
            "expected_username": str(args.expected_username or ""),
            "device_serial": _safe_device_serial(getattr(args, "device_serial", None)),
            "ok": False,
            "completed": False,
            "final_outcome": "unknown",
            "reason": reason,
            "publish_enabled": _publish_enabled_from_args_env(args),
            "publish_attempted": False,
            "publish_result": "skipped",
            "publish_error_code": "",
            "central_orchestrator_used": False,
            "central_orchestrator_version": "",
            "selected_route": "",
            "selected_route_reason": "",
            "app_start_attempted": False,
            "app_start_ok": None,
            "screen_after_app_start": "",
            "screen_after_app_start_initial": "",
            "screen_after_app_start_final": "",
            "startup_observation_count": 0,
            "startup_wait_total_ms": 0,
            "startup_screens": [],
            "startup_final_screen_type": "",
            "startup_settling_used": False,
            "app_start_retry_attempted": False,
            "app_start_retry_count": 0,
            "app_start_retry_reason": "",
            "app_start_retry_result": "",
            "startup_after_retry_screens": [],
            "preparation_flow_used": "none",
            "screen_type": "",
            "suggested_username": "",
            "prefilled_username": "",
            "username_replaced": False,
            "username_input_confirmed": "unknown",
            "username_input_result": "",
            "router_decision": "",
            "previous_account_lifecycle_source": "",
            "previous_account_lifecycle_status": "",
            "clone_reuse_allowed": False,
            "screen_before_submit": "",
            "input_method_used": "",
            "password_field_non_empty_confirmed": False,
            "submit_executed": False,
            "post_submit_observation_count": 0,
            "post_submit_wait_total_ms": 0,
            "post_submit_timeout_ms": int(getattr(args, "post_submit_timeout_ms", 0) or 0),
            "post_submit_interval_ms": 0,
            "post_submit_loading_timeout": False,
            "post_submit_screens": [],
            "final_terminal_screen": "",
            "save_password_prompt_detected": False,
            "save_password_prompt_dismiss_attempt_count": 0,
            "save_password_prompt_dismissed": False,
            "dismiss_method": "",
            "post_dismiss_screen_type": "",
            "post_dismiss_final_observation_count": 0,
            "post_dismiss_final_screens": [],
            "post_dismiss_final_wait_total_ms": 0,
            "post_dismiss_final_screen_type": "",
            "connected_detected_after_save_prompt_dismiss": False,
            **_empty_credentials_summary_fields(),
            "would_publish": False,
            "timings": {},
            "warnings": [],
            "no_leak_summary": _no_leak_summary(),
            "package_name": _effective_package_name(args),
            "expected_package_name": _effective_package_name(args),
            "actual_foreground_package": "",
            "package_guard_checked": False,
            "package_guard_mismatch": False,
            "log_jsonl": str(getattr(args, "log_jsonl", DEFAULT_LOG_JSONL) or DEFAULT_LOG_JSONL),
        }
    )


def _safe_summary_from_result(result: Any, *, args: argparse.Namespace, run_id: str) -> dict[str, Any]:
    metadata = dict(getattr(result, "safe_metadata", {}) or {})
    password_result = dict(metadata.get("password_result") or {})
    actions_taken = list(getattr(result, "actions_taken", []) or [])
    previous_account_lifecycle = dict(metadata.get("previous_account_lifecycle") or {})
    submit_executed = bool(password_result.get("executed") or password_result.get("submit_tapped"))
    screen_before_submit = _screen_before_submit(metadata, submit_executed=submit_executed)
    summary = {
        "run_id": run_id,
        "flow_name": "entry2e5p5_login_provisioner",
        "expected_username": str(args.expected_username or ""),
        "device_serial": _safe_device_serial(getattr(args, "device_serial", None)),
        "ok": bool(getattr(result, "ok", False)),
        "completed": bool(getattr(result, "completed", False)),
        "final_outcome": str(getattr(result, "final_outcome", "") or "unknown"),
        "reason": str(getattr(result, "reason", "") or ""),
        "status_candidate": str(getattr(result, "final_login_status", "") or ""),
        "publish_enabled": bool(metadata.get("publish_enabled")),
        "publish_attempted": bool(metadata.get("publish_attempted")),
        "publish_result": str(metadata.get("publish_result") or ""),
        "publish_error_code": str(metadata.get("publish_error_code") or ""),
        "central_orchestrator_used": bool(metadata.get("central_orchestrator_used")),
        "central_orchestrator_version": str(metadata.get("central_orchestrator_version") or ""),
        "selected_route": str(metadata.get("selected_route") or ""),
        "selected_route_reason": str(metadata.get("selected_route_reason") or ""),
        "app_start_attempted": bool(metadata.get("app_start_attempted")),
        "app_start_ok": metadata.get("app_start_ok"),
        "post_logout_resume_observe_only": bool(metadata.get("post_logout_resume_observe_only")),
        "screen_after_app_start": str(metadata.get("screen_after_app_start") or ""),
        "screen_after_app_start_initial": str(metadata.get("screen_after_app_start_initial") or ""),
        "screen_after_app_start_final": str(metadata.get("screen_after_app_start_final") or ""),
        "startup_observation_count": int(metadata.get("startup_observation_count") or 0),
        "startup_wait_total_ms": int(metadata.get("startup_wait_total_ms") or 0),
        "startup_screens": list(metadata.get("startup_screens") or []),
        "startup_final_screen_type": str(metadata.get("startup_final_screen_type") or ""),
        "startup_settling_used": bool(metadata.get("startup_settling_used")),
        "app_start_retry_attempted": bool(metadata.get("app_start_retry_attempted")),
        "app_start_retry_count": int(metadata.get("app_start_retry_count") or 0),
        "app_start_retry_reason": str(metadata.get("app_start_retry_reason") or ""),
        "app_start_retry_result": str(metadata.get("app_start_retry_result") or ""),
        "startup_after_retry_screens": list(metadata.get("startup_after_retry_screens") or []),
        "post_use_another_profile_observation_count": int(
            metadata.get("post_use_another_profile_observation_count") or 0
        ),
        "post_use_another_profile_screens": list(metadata.get("post_use_another_profile_screens") or []),
        "screen_after_use_another_profile_final": str(
            metadata.get("screen_after_use_another_profile_final") or ""
        ),
        "preparation_flow_used": _preparation_flow_used(metadata, actions_taken),
        "screen_type": _summary_screen_type(metadata, actions_taken),
        "displayed_username": str(metadata.get("displayed_username") or ""),
        "password_only_username": str(metadata.get("password_only_username") or ""),
        "username_match": metadata.get("username_match"),
        "actual_logged_in_username": str(metadata.get("actual_logged_in_username") or ""),
        "active_account_username": str(metadata.get("active_account_username") or ""),
        "account_mismatch_detected": bool(metadata.get("account_mismatch_detected")),
        "active_account_lifecycle_source": str(metadata.get("active_account_lifecycle_source") or ""),
        "active_account_lifecycle_status": str(metadata.get("active_account_lifecycle_status") or ""),
        "recovery_path": str(metadata.get("recovery_path") or ""),
        "replacement_flow": str(metadata.get("replacement_flow") or ""),
        "replacement_route": str(metadata.get("replacement_route") or ""),
        "stale_session_replacement_allowed": bool(metadata.get("stale_session_replacement_allowed")),
        "replacement_safety_status": str(metadata.get("replacement_safety_status") or ""),
        "connected_account_state": str(metadata.get("connected_account_state") or ""),
        "previous_account_state": str(metadata.get("previous_account_state") or ""),
        "stale_account_state": str(metadata.get("stale_account_state") or ""),
        "connected_username": str(metadata.get("connected_username") or ""),
        "target_username": str(metadata.get("target_username") or ""),
        "controlled_logout_status": str(metadata.get("controlled_logout_status") or ""),
        "target_login_status": str(metadata.get("target_login_status") or ""),
        "identity_verification_status": str(metadata.get("identity_verification_status") or ""),
        "logout_fallback_allowed": bool(metadata.get("logout_fallback_allowed")),
        "logout_fallback_reason": str(metadata.get("logout_fallback_reason") or ""),
        "add_existing_attempted": bool(metadata.get("add_existing_attempted")),
        "add_existing_failed_reason": str(metadata.get("add_existing_failed_reason") or ""),
        "profile_opened": bool(metadata.get("profile_opened")),
        "profile_username": str(metadata.get("profile_username") or ""),
        "profile_menu_initially_missing": bool(metadata.get("profile_menu_initially_missing")),
        "profile_refresh_attempted": bool(metadata.get("profile_refresh_attempted")),
        "profile_menu_opened": bool(metadata.get("profile_menu_opened")),
        "settings_opened": bool(metadata.get("settings_opened")),
        "logout_settings_scroll_attempted": bool(metadata.get("logout_settings_scroll_attempted")),
        "logout_settings_scroll_count": int(metadata.get("logout_settings_scroll_count") or 0),
        "logout_button_visible_before_scroll": bool(metadata.get("logout_button_visible_before_scroll")),
        "logout_button_visible_after_scroll": bool(metadata.get("logout_button_visible_after_scroll")),
        "logout_button_tapped": bool(metadata.get("logout_button_tapped")),
        "logout_button_target_text": str(metadata.get("logout_button_target_text") or ""),
        "logout_button_target_method": str(metadata.get("logout_button_target_method") or ""),
        "logout_not_visible_reason": str(metadata.get("logout_not_visible_reason") or ""),
        "save_login_info_prompt_detected": bool(metadata.get("save_login_info_prompt_detected")),
        "save_login_info_not_now_tapped": bool(metadata.get("save_login_info_not_now_tapped")),
        "logout_confirmation_detected": bool(metadata.get("logout_confirmation_detected")),
        "logout_confirmation_tapped": bool(metadata.get("logout_confirmation_tapped")),
        "post_logout_observation_count": int(metadata.get("post_logout_observation_count") or 0),
        "post_logout_screens": list(metadata.get("post_logout_screens") or []),
        "post_logout_wait_total_ms": int(metadata.get("post_logout_wait_total_ms") or 0),
        "screen_after_logout_final": str(metadata.get("screen_after_logout_final") or ""),
        "post_logout_final_suggested_username": str(
            metadata.get("post_logout_final_suggested_username") or ""
        ),
        "account_switcher_opened": bool(metadata.get("account_switcher_opened")),
        "add_instagram_account_tapped": bool(metadata.get("add_instagram_account_tapped")),
        "add_account_sheet_opened": bool(metadata.get("add_account_sheet_opened")),
        "log_into_existing_account_tapped": bool(metadata.get("log_into_existing_account_tapped")),
        "post_add_existing_observation_count": int(metadata.get("post_add_existing_observation_count") or 0),
        "post_add_existing_screens": list(metadata.get("post_add_existing_screens") or []),
        "screen_after_add_existing_final": str(metadata.get("screen_after_add_existing_final") or ""),
        "available_usernames": list(metadata.get("available_usernames") or []),
        "expected_username_present": bool(metadata.get("expected_username_present")),
        "selected_account_username": str(metadata.get("selected_account_username") or ""),
        "account_picker_selection_executed": bool(metadata.get("account_picker_selection_executed")),
        "account_picker_target_resolution_method": str(
            metadata.get("account_picker_target_resolution_method") or ""
        ),
        "account_picker_target_row_count": int(metadata.get("account_picker_target_row_count") or 0),
        "account_picker_target_node_count": int(metadata.get("account_picker_target_node_count") or 0),
        "account_picker_visible_usernames_count": int(metadata.get("account_picker_visible_usernames_count") or 0),
        "account_picker_selected_row_index_if_known": metadata.get("account_picker_selected_row_index_if_known"),
        "account_picker_action_result": str(metadata.get("account_picker_action_result") or ""),
        "post_account_picker_observation_count": int(metadata.get("post_account_picker_observation_count") or 0),
        "post_account_picker_screens": list(metadata.get("post_account_picker_screens") or []),
        "screen_after_account_picker_final": str(metadata.get("screen_after_account_picker_final") or ""),
        "suggested_username": _summary_suggested_username(metadata, previous_account_lifecycle),
        "prefilled_username": str(metadata.get("prefilled_username") or ""),
        "username_field_focused_before_input": password_result.get("username_field_focused_before_input"),
        "username_clear_method": str(password_result.get("username_clear_method") or ""),
        "username_input_method": str(password_result.get("username_input_method") or ""),
        "username_replaced": bool(password_result.get("username_replaced")),
        "username_input_confirmed": str(password_result.get("username_input_confirmed") or "unknown"),
        "username_input_result": str(password_result.get("username_input_result") or ""),
        "username_input_ms": int(password_result.get("username_input_ms") or 0),
        "username_placeholder_ignored": bool(password_result.get("username_placeholder_ignored")),
        "router_decision": _router_decision(metadata, actions_taken),
        "previous_account_lifecycle_source": str(previous_account_lifecycle.get("source") or ""),
        "previous_account_lifecycle_status": str(previous_account_lifecycle.get("lifecycle_status") or ""),
        "clone_reuse_allowed": bool(previous_account_lifecycle.get("clone_reuse_allowed")),
        "screen_before_submit": screen_before_submit,
        "password_field_target_kind": str(password_result.get("password_field_target_kind") or ""),
        "password_input_method": str(password_result.get("password_input_method") or ""),
        "password_input_result": str(password_result.get("password_input_result") or ""),
        "password_confirm_method": str(password_result.get("password_confirm_method") or ""),
        "input_method_used": str(password_result.get("input_method_used") or ""),
        "password_field_focused_before_input": bool(password_result.get("password_field_focused_before_input")),
        "password_field_non_empty_confirmed": _password_non_empty_confirmed(password_result),
        "submit_executed": submit_executed,
        "password_required_dialog_detected": bool(password_result.get("password_required_dialog_detected")),
        "post_submit_observation_count": int(password_result.get("post_submit_observation_count") or 0),
        "post_submit_wait_total_ms": int(password_result.get("post_submit_wait_total_ms") or 0),
        "post_submit_timeout_ms": int(password_result.get("post_submit_timeout_ms") or 0),
        "post_submit_interval_ms": int(password_result.get("post_submit_interval_ms") or 0),
        "post_submit_loading_timeout": bool(password_result.get("post_submit_loading_timeout")),
        "post_submit_screens": list(password_result.get("post_submit_screens") or []),
        "final_terminal_screen": str(password_result.get("final_terminal_screen") or ""),
        "save_password_prompt_detected": bool(password_result.get("save_password_prompt_detected")),
        "save_password_prompt_dismissed": bool(password_result.get("save_password_prompt_dismissed")),
        "save_password_prompt_dismiss_attempt_count": int(
            password_result.get("save_password_prompt_dismiss_attempt_count") or 0
        ),
        "dismiss_method": str(password_result.get("dismiss_method") or ""),
        "samsung_pass_save_password_prompt_detected": bool(
            password_result.get("samsung_pass_save_password_prompt_detected")
        ),
        "samsung_pass_save_password_prompt_cancelled": bool(
            password_result.get("samsung_pass_save_password_prompt_cancelled")
        ),
        "instagram_save_login_info_prompt_detected": bool(
            password_result.get("instagram_save_login_info_prompt_detected")
        ),
        "instagram_save_login_info_prompt_not_now": bool(
            password_result.get("instagram_save_login_info_prompt_not_now")
        ),
        "post_dismiss_screen_type": str(password_result.get("post_dismiss_screen_type") or ""),
        "post_dismiss_final_observation_count": int(
            password_result.get("post_dismiss_final_observation_count") or 0
        ),
        "post_dismiss_final_screens": list(password_result.get("post_dismiss_final_screens") or []),
        "post_dismiss_final_wait_total_ms": int(password_result.get("post_dismiss_final_wait_total_ms") or 0),
        "post_dismiss_final_screen_type": str(password_result.get("post_dismiss_final_screen_type") or ""),
        "connected_detected_after_save_prompt_dismiss": bool(
            password_result.get("connected_detected_after_save_prompt_dismiss")
        ),
        **_credentials_fields_from_metadata(metadata),
        "retry_count": int(getattr(result, "retry_count", 0) or 0),
        "would_publish": bool(getattr(result, "should_publish_status", False)) and bool(metadata.get("publish_enabled")),
        "published": bool(getattr(result, "published", False)),
        "publish_reason": str(getattr(result, "publish_reason", "") or ""),
        "timings": dict(getattr(result, "timings", {}) or {}),
        "warnings": list(getattr(result, "warnings", []) or []),
        "no_leak_summary": _no_leak_summary(),
        "package_name": _effective_package_name(args),
        "expected_package_name": str(metadata.get("expected_package_name") or _effective_package_name(args)),
        "actual_foreground_package": str(metadata.get("actual_foreground_package") or ""),
        "package_guard_checked": bool(metadata.get("package_guard_checked")),
        "package_guard_mismatch": bool(metadata.get("package_guard_mismatch")),
        "login_package_mismatch_incident": metadata.get("login_package_mismatch_incident"),
        "login_package_mismatch_dashboard_action": metadata.get("login_package_mismatch_dashboard_action"),
        "login_package_mismatch_notifications": metadata.get("login_package_mismatch_notifications"),
        "log_jsonl": str(getattr(args, "log_jsonl", DEFAULT_LOG_JSONL) or DEFAULT_LOG_JSONL),
    }
    return _clean_summary(summary)


def _preparation_flow_used(metadata: dict[str, Any], actions_taken: list[Any]) -> str:
    actions = [str(item) for item in actions_taken]
    recovery_path = str(metadata.get("recovery_path") or "")
    screen_after_logout = str(metadata.get("screen_after_logout_final") or "")
    if recovery_path == "logout_fallback":
        if "tap_use_another_profile" in actions:
            return "logout_fallback_to_use_another_profile"
        if screen_after_logout:
            return f"logout_fallback_to_{screen_after_logout}"
    screen_after_add_existing = str(metadata.get("screen_after_add_existing_final") or "")
    if recovery_path == "add_existing_account" and screen_after_add_existing:
        return f"add_existing_account_to_{screen_after_add_existing}"
    if "tap_use_another_profile" in actions:
        return "use_another_profile_for_expected_account"
    if "tap_continue" in actions:
        return "continue_as_candidate"
    if "tap_expected_account" in actions or any("select_expected_account" in action for action in actions):
        return "select_expected_account_from_picker"
    screen = str(metadata.get("screen_after_app_start") or "")
    if screen in {"login_form_empty", "login_form_prefilled_username", "continue_password_only", "connected", "unknown"}:
        return screen
    return "none"


def _summary_screen_type(metadata: dict[str, Any], actions_taken: list[Any]) -> str:
    actions = [str(item) for item in actions_taken]
    if "tap_expected_account" in actions and str(metadata.get("screen_after_app_start") or "") == "account_picker":
        return "account_picker"
    return str(metadata.get("screen_type") or "")


def _summary_suggested_username(
    metadata: dict[str, Any],
    previous_account_lifecycle: dict[str, Any],
) -> str:
    suggested_username = str(metadata.get("suggested_username") or "").strip()
    if suggested_username:
        return suggested_username
    return str(previous_account_lifecycle.get("username") or "").strip()


def _router_decision(metadata: dict[str, Any], actions_taken: list[Any]) -> str:
    router_decision = str(metadata.get("router_decision") or "").strip()
    if router_decision:
        return router_decision
    route_actions = [str(action) for action in actions_taken if str(action).startswith("route:")]
    if route_actions:
        return route_actions[-1].split(":", 1)[1]
    return ""


def _password_non_empty_confirmed(password_result: dict[str, Any]) -> bool | str:
    value = password_result.get("password_field_non_empty_confirmed")
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return text


def _screen_before_submit(metadata: dict[str, Any], *, submit_executed: bool) -> str:
    screen_after_logout = str(metadata.get("screen_after_logout_final") or "")
    if screen_after_logout in {
        "continue_password_only",
        "login_form_empty",
        "login_form_prefilled_username",
        "continue_as_candidate",
        "account_picker",
    }:
        return screen_after_logout
    screen_after_add_existing = str(metadata.get("screen_after_add_existing_final") or "")
    if screen_after_add_existing in {
        "continue_password_only",
        "login_form_empty",
        "login_form_prefilled_username",
        "continue_as_candidate",
        "account_picker",
    }:
        return screen_after_add_existing
    screen_after_use_another = str(metadata.get("screen_after_use_another_profile_final") or "")
    if screen_after_use_another in {
        "continue_password_only",
        "login_form_empty",
        "login_form_prefilled_username",
    }:
        return screen_after_use_another
    for key in ("post_continue_final_screen_type", "post_account_picker_final_screen_type"):
        value = str(metadata.get(key) or "")
        if value in {"continue_password_only", "login_form_empty", "login_form_prefilled_username"}:
            return value
    screen = str(metadata.get("screen_after_app_start") or "")
    if screen in {"continue_password_only", "login_form_empty", "login_form_prefilled_username"}:
        return screen
    return "" if not submit_executed else "accepted_login_screen"


def _empty_credentials_summary_fields() -> dict[str, Any]:
    return {
        "credentials_error_code": "",
        "credentials_invalid_reason": "",
        "credentials_stage": "",
        "credential_metadata_found": False,
        "credentials_status": None,
        "credentials_version": None,
        "secret_provider": "",
        "username_matches_expected": None,
        "secret_loaded": False,
        "injectable_password_only": None,
        "secret_value_safe_for_injection": None,
        "guard_would_block_revealed_value": None,
    }


def _no_leak_summary() -> dict[str, bool]:
    return {
        "password_hidden": True,
        "password_length_hidden": True,
        "password_hash_hidden": True,
        "secret_ref_hidden": True,
        "vault_uuid_hidden": True,
        "token_header_hidden": True,
        "raw_xml_hidden": True,
        "screenshot_path_hidden": True,
    }


def _safe_run_id(value: str) -> str:
    raw = str(value or "").strip()
    try:
        return str(uuid.UUID(raw))
    except (TypeError, ValueError):
        return str(uuid.uuid4())


def _safe_device_serial(value: Any) -> str:
    serial = str(value or "").strip()
    if not serial:
        return ""
    if serial.startswith("emulator-"):
        return serial
    return "provided"


def _clean_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return redact_credentials_payload(summary)


def _render_safe_json(summary: dict[str, Any]) -> str:
    rendered = json.dumps(_clean_summary(summary), sort_keys=True, separators=(",", ":"))
    lowered = rendered.lower()
    forbidden_fragments = (
        "supabase_vault://",
        "authorization:",
        "bearer ",
        "service_role",
        "<node",
        "screenshot/",
        "screenshots/",
    )
    if any(fragment in lowered for fragment in forbidden_fragments):
        raise RuntimeError("safe_output_guard_blocked")
    return rendered


def _append_safe_jsonl(summary: dict[str, Any], *, args: argparse.Namespace) -> None:
    log_path = Path(str(getattr(args, "log_jsonl", DEFAULT_LOG_JSONL) or DEFAULT_LOG_JSONL))
    if any(part in {"", ".", ".."} for part in log_path.parts):
        raise RuntimeError("invalid_log_path")
    rendered = _render_safe_json(summary)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(rendered + os.linesep)


def _print_human_summary(summary: dict[str, Any]) -> None:
    print(f"final_outcome={summary.get('final_outcome')}")
    print(f"reason={summary.get('reason')}")
    print(f"run_id={summary.get('run_id')}")
    print(f"submit_executed={summary.get('submit_executed')}")
    print(f"would_publish={summary.get('would_publish')}")
    print("json=" + _render_safe_json(summary))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
