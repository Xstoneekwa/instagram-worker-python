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
from typing import Any, Callable, Optional

from instagram_credentials_runtime_access import get_instagram_credentials_for_login, redact_credentials_payload
from instagram_login_provisioner_orchestrator import (
    DEFAULT_INSTAGRAM_PACKAGE_NAME,
    DEFAULT_POST_APP_START_WAIT_MS,
    run_login_provisioning_flow,
)
from instagram_supabase_vault_reader import SupabaseVaultClient
from supabase_client import _request_json

ConnectFunc = Callable[[Optional[str]], Any]
CredentialsLookup = Callable[[str, str], Optional[dict[str, Any]]]
SecretReader = Callable[[str], Any]
RunFlowFunc = Callable[..., Any]
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one safe Instagram login provisioning flow and print JSON.",
    )
    parser.add_argument("--device-serial", default=None, help="Optional explicit adb/uiautomator2 serial.")
    parser.add_argument("--expected-username", required=True, help="Expected Instagram username.")
    parser.add_argument("--account-id", required=True, help="Instagram account UUID used for credential lookup.")
    parser.add_argument("--package-name", default=DEFAULT_INSTAGRAM_PACKAGE_NAME, help="Instagram package to start.")
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
    parser.add_argument("--dry-run", action="store_true", help="Route/prepare only; do not load Vault or submit.")
    parser.add_argument("--no-submit", action="store_true", help="Alias for --dry-run.")
    parser.add_argument("--no-publish", action="store_true", default=True, help="Keep status publishing disabled.")
    parser.add_argument("--run-id", default="", help="Optional safe run id. Defaults to a generated UUID.")
    parser.add_argument("--log-jsonl", default=DEFAULT_LOG_JSONL, help="Safe JSONL log path.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable safe JSON.")
    return parser


def run_cli_command(
    args: argparse.Namespace,
    *,
    connect_func: ConnectFunc | None = None,
    credentials_lookup: CredentialsLookup | None = None,
    secret_reader: SecretReader | None = None,
    run_flow_func: RunFlowFunc | None = None,
) -> tuple[int, dict[str, Any]]:
    run_id = _safe_run_id(getattr(args, "run_id", "") or str(uuid.uuid4()))
    _load_dotenv_if_present()
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
    result = flow(
        device,
        account_id=str(args.account_id or ""),
        expected_username=str(args.expected_username or ""),
        credentials_getter=getter,
        publish_enabled=False,
        publisher=None,
        dry_run=bool(args.dry_run or args.no_submit),
        start_app_before_probe=bool(args.start_app_before_probe),
        observe_current_screen_only=bool(args.observe_current_screen_only),
        package_name=str(args.package_name or DEFAULT_INSTAGRAM_PACKAGE_NAME),
        post_start_wait_ms=int(args.post_start_wait_ms or DEFAULT_POST_APP_START_WAIT_MS),
        post_submit_timeout_ms=int(args.post_submit_timeout_ms or 0),
    )
    summary = _safe_summary_from_result(result, args=args, run_id=run_id)
    _append_safe_jsonl(summary, args=args)
    return (0 if bool(getattr(result, "ok", False)) else 1), summary


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
            "preparation_flow_used": "none",
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
            **_empty_credentials_summary_fields(),
            "would_publish": False,
            "timings": {},
            "warnings": [],
            "no_leak_summary": _no_leak_summary(),
            "package_name": str(args.package_name or DEFAULT_INSTAGRAM_PACKAGE_NAME),
            "log_jsonl": str(getattr(args, "log_jsonl", DEFAULT_LOG_JSONL) or DEFAULT_LOG_JSONL),
        }
    )


def _safe_summary_from_result(result: Any, *, args: argparse.Namespace, run_id: str) -> dict[str, Any]:
    metadata = dict(getattr(result, "safe_metadata", {}) or {})
    password_result = dict(metadata.get("password_result") or {})
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
        "app_start_attempted": bool(metadata.get("app_start_attempted")),
        "app_start_ok": metadata.get("app_start_ok"),
        "screen_after_app_start": str(metadata.get("screen_after_app_start") or ""),
        "screen_after_app_start_initial": str(metadata.get("screen_after_app_start_initial") or ""),
        "screen_after_app_start_final": str(metadata.get("screen_after_app_start_final") or ""),
        "startup_observation_count": int(metadata.get("startup_observation_count") or 0),
        "startup_wait_total_ms": int(metadata.get("startup_wait_total_ms") or 0),
        "startup_screens": list(metadata.get("startup_screens") or []),
        "startup_final_screen_type": str(metadata.get("startup_final_screen_type") or ""),
        "startup_settling_used": bool(metadata.get("startup_settling_used")),
        "preparation_flow_used": _preparation_flow_used(metadata, getattr(result, "actions_taken", []) or []),
        "screen_before_submit": screen_before_submit,
        "input_method_used": str(password_result.get("input_method_used") or ""),
        "password_field_focused_before_input": bool(password_result.get("password_field_focused_before_input")),
        "password_field_non_empty_confirmed": bool(password_result.get("password_field_non_empty_confirmed")),
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
        "post_dismiss_screen_type": str(password_result.get("post_dismiss_screen_type") or ""),
        **_credentials_fields_from_metadata(metadata),
        "retry_count": int(getattr(result, "retry_count", 0) or 0),
        "would_publish": False,
        "published": bool(getattr(result, "published", False)),
        "publish_reason": str(getattr(result, "publish_reason", "") or ""),
        "timings": dict(getattr(result, "timings", {}) or {}),
        "warnings": list(getattr(result, "warnings", []) or []),
        "no_leak_summary": _no_leak_summary(),
        "package_name": str(args.package_name or DEFAULT_INSTAGRAM_PACKAGE_NAME),
        "log_jsonl": str(getattr(args, "log_jsonl", DEFAULT_LOG_JSONL) or DEFAULT_LOG_JSONL),
    }
    return _clean_summary(summary)


def _preparation_flow_used(metadata: dict[str, Any], actions_taken: list[Any]) -> str:
    actions = [str(item) for item in actions_taken]
    if "tap_continue" in actions:
        return "continue_as_candidate"
    if any("select_expected_account" in action for action in actions):
        return "account_picker"
    screen = str(metadata.get("screen_after_app_start") or "")
    if screen in {"login_form_empty", "continue_password_only", "connected", "unknown"}:
        return screen
    return "none"


def _screen_before_submit(metadata: dict[str, Any], *, submit_executed: bool) -> str:
    for key in ("post_continue_final_screen_type", "post_account_picker_final_screen_type"):
        value = str(metadata.get(key) or "")
        if value in {"continue_password_only", "login_form_empty"}:
            return value
    screen = str(metadata.get("screen_after_app_start") or "")
    if screen in {"continue_password_only", "login_form_empty"}:
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
