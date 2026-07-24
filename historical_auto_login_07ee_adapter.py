"""Mechanical bridge from the current dispatcher to the isolated 07ee CLI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from follow_source_rotation_settings import maybe_provision_follow_source_rotation_on_ready
from logs import log
import supabase_client


HISTORICAL_ENGINE_ROOT = Path(__file__).resolve().parent / "historical_auto_login_07ee"
HISTORICAL_ENGINE_CLI = HISTORICAL_ENGINE_ROOT / "instagram_login_provisioner_cli.py"


@dataclass(frozen=True)
class HistoricalAutoLoginInvocation:
    account_id: str
    request_id: str
    run_id: str
    expected_username: str
    device_serial: str
    package_name: str
    app_instance_id: str
    publish: bool
    json_output: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the isolated historical 07ee Auto Login engine.")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-username", required=True)
    parser.add_argument("--device-serial", required=True)
    parser.add_argument("--package-name", required=True)
    parser.add_argument("--expected-app-instance-id", required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def invocation_from_args(args: argparse.Namespace) -> HistoricalAutoLoginInvocation:
    return HistoricalAutoLoginInvocation(
        account_id=str(args.account_id),
        request_id=str(args.request_id),
        run_id=str(args.run_id),
        expected_username=str(args.expected_username),
        device_serial=str(args.device_serial),
        package_name=str(args.package_name),
        app_instance_id=str(args.expected_app_instance_id),
        publish=bool(args.publish),
        json_output=bool(args.json),
    )


def build_historical_command(invocation: HistoricalAutoLoginInvocation) -> list[str]:
    command = [
        sys.executable,
        str(HISTORICAL_ENGINE_CLI),
        "--account-id",
        invocation.account_id,
        "--expected-username",
        invocation.expected_username,
        "--run-id",
        invocation.run_id,
        "--device-serial",
        invocation.device_serial,
        "--package-name",
        invocation.package_name,
        "--expected-app-instance-id",
        invocation.app_instance_id,
    ]
    if invocation.publish:
        command.append("--publish")
    if invocation.json_output:
        command.append("--json")
    return command


def execute_historical_engine(
    invocation: HistoricalAutoLoginInvocation,
    *,
    run_process: Callable[..., Any] = subprocess.run,
    environ: Mapping[str, str] | None = None,
) -> int:
    command = build_historical_command(invocation)
    result = run_process(command, env=dict(os.environ if environ is None else environ), check=False)
    exit_code = int(getattr(result, "returncode", result))
    if exit_code != 0:
        return exit_code
    _provision_follow_source_defaults_after_historical_success(invocation)
    return exit_code


def _read_historical_summary(run_id: str) -> dict[str, Any]:
    log_path = Path("logs") / "instagram_login_provisioner.jsonl"
    try:
        with log_path.open("r", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip() and f'"run_id":"{run_id}"' in line]
    except (OSError, ValueError):
        return {}
    return dict(rows[-1]) if rows and isinstance(rows[-1], dict) else {}


def _provision_follow_source_defaults_after_historical_success(invocation: HistoricalAutoLoginInvocation) -> dict[str, Any]:
    """Reuse the canonical 30/4 hook only after 07ee published connected+ready."""
    summary = _read_historical_summary(invocation.run_id)
    if not (
        summary.get("ok") is True
        and summary.get("completed") is True
        and str(summary.get("final_outcome") or "") == "connected"
        and str(summary.get("status_candidate") or "") == "connected"
        and summary.get("published") is True
        and str(summary.get("publish_reason") or "") == "published_connected"
    ):
        return {"skipped": True, "reason": "historical_login_not_published_connected"}
    account = supabase_client.load_account(account_id=invocation.account_id) or {}
    if not (
        str(account.get("login_status") or "") == "connected"
        and str(account.get("provisioning_status") or "") == "ready"
    ):
        return {"skipped": True, "reason": "backend_account_not_connected_ready"}
    try:
        return maybe_provision_follow_source_rotation_on_ready(
            account_id=invocation.account_id,
            account_username=invocation.expected_username,
            final_provisioning_status="ready",
            context="historical_auto_login_07ee_published_ready",
        )
    except Exception as exc:
        log(
            "warning",
            "historical_auto_login_follow_source_provision_failed",
            account_id=invocation.account_id,
            run_id=invocation.run_id,
            error=str(exc)[:200],
        )
        return {"ok": False, "reason": "follow_source_rotation_provision_failed"}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return execute_historical_engine(invocation_from_args(args))


if __name__ == "__main__":
    raise SystemExit(main())
