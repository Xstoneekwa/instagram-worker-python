"""Mechanical bridge from the current dispatcher to the isolated 07ee CLI."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


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
    execve: Callable[[str, Sequence[str], Mapping[str, str]], Any] = os.execve,
    environ: Mapping[str, str] | None = None,
) -> int:
    command = build_historical_command(invocation)
    result = execve(sys.executable, command, dict(os.environ if environ is None else environ))
    return int(result) if isinstance(result, int) else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return execute_historical_engine(invocation_from_args(args))


if __name__ == "__main__":
    raise SystemExit(main())
