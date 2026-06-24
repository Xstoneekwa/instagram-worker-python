"""CLI for bounded orphan login-challenge recovery (no credentials)."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any, Optional

from assignment_dispatch_resolver import resolve_account_assignment_runtime_context
from device import connect_device
from login_orphan_challenge_recovery import run_orphan_challenge_recovery_flow
from supabase_client import load_account


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recover orphan login challenge screen with one bounded back action.")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--device-serial", default=None)
    parser.add_argument("--package-name", default="")
    parser.add_argument("--expected-app-instance-id", default="")
    parser.add_argument("--assignment-id", default="")
    parser.add_argument("--credentials-version", type=int, default=None)
    parser.add_argument("--assignment-updated-at", default="")
    parser.add_argument("--json", action="store_true", default=False)
    return parser


def _result_payload(result: Any) -> dict[str, Any]:
    payload = asdict(result)
    payload["safe_metadata"] = dict(result.metadata or {})
    return payload


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    account_id = str(args.account_id or "").strip()
    account = load_account(account_id=account_id) or {}
    expected_username = str(account.get("username") or "").strip()

    ctx = resolve_account_assignment_runtime_context(
        account_id,
        "login_orphan_challenge_recovery",
        require_assignment=True,
        enforce_window=False,
    )
    if str(ctx.get("reason") or "") not in {"", "ok", "assignment_found"} and not ctx.get("assignment_found"):
        print(
            json.dumps(
                {
                    "ok": False,
                    "final_outcome": "blocked",
                    "reason": str(ctx.get("reason") or "assignment_not_found"),
                    "recovery_state": "recovery_blocked",
                }
            ),
            file=sys.stderr,
        )
        return 1

    device_serial = str(args.device_serial or ctx.get("adb_serial") or "").strip() or None
    package_name = str(args.package_name or ctx.get("package_name") or "").strip()
    app_instance_id = str(args.expected_app_instance_id or ctx.get("app_instance_id") or "").strip()
    assignment_id = str(args.assignment_id or ctx.get("assignment_id") or "").strip()

    device = connect_device(device_serial)
    result = run_orphan_challenge_recovery_flow(
        device,
        account_id=account_id,
        expected_username=expected_username,
        expected_package=package_name,
        expected_app_instance_id=app_instance_id or None,
        assignment_id=assignment_id or None,
        credentials_version=args.credentials_version,
        assignment_updated_at=str(args.assignment_updated_at or ctx.get("assignment_updated_at") or "") or None,
        run_id=str(args.run_id or "").strip(),
    )
    payload = _result_payload(result)
    print(json.dumps(payload, ensure_ascii=False))
    if result.final_outcome == "restored":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
