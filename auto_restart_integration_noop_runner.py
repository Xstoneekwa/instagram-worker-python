#!/usr/bin/env python3
"""Integration-only no-op runner for Auto Restart harness (no ADB / UI)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import urlparse

from auto_restart_account_session_bootstrap import bootstrap_account_session_resume_integration

INTEGRATION_WORKER_ID = "run-dispatcher:integration-mac"


def _fail(message: str, code: int = 2) -> None:
    print(json.dumps({"ok": False, "error": message}), flush=True)
    raise SystemExit(code)


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _assert_loopback_supabase() -> None:
    url = str(os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip()
    if not url:
        _fail("SUPABASE_URL loopback required")
    host = (urlparse(url).hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        _fail(f"non-loopback SUPABASE_URL forbidden: {host}")


def _assert_integration_guards() -> None:
    if not _env_bool("RUN_CONTROL_INTEGRATION_NOOP_RUNNER"):
        _fail("RUN_CONTROL_INTEGRATION_NOOP_RUNNER is required")
    if not _env_bool("RUN_CONTROL_INTEGRATION_MODE"):
        _fail("RUN_CONTROL_INTEGRATION_MODE is required")
    if not _env_bool("RUN_CONTROL_INTEGRATION_NO_ADB"):
        _fail("RUN_CONTROL_INTEGRATION_NO_ADB must be enabled")
    worker = str(os.environ.get("RUN_CONTROL_DISPATCHER_WORKER_ID") or "").strip()
    if worker and worker != INTEGRATION_WORKER_ID:
        _fail(f"unexpected RUN_CONTROL_DISPATCHER_WORKER_ID: {worker}")
    for forbidden in ("ANDROID_SERIAL", "ADB_SERIAL", "INSTAGRAM_PACKAGE", "U2_DEVICE"):
        if os.environ.get(forbidden, "").strip():
            _fail(f"forbidden env in integration noop: {forbidden}")
    _assert_loopback_supabase()


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto Restart integration no-op runner")
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--account-id", required=True)
    args = parser.parse_args()

    _assert_integration_guards()
    proof = bootstrap_account_session_resume_integration(
        request_id=args.request_id,
        account_id=args.account_id,
    )
    print(json.dumps(proof, separators=(",", ":"), sort_keys=True), flush=True)
    return int(proof.get("exit_code") or (0 if proof.get("ok") else 2))


if __name__ == "__main__":
    raise SystemExit(main())
