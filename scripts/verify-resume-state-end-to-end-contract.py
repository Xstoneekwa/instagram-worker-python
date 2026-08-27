#!/usr/bin/env python3
"""Offline certification gate. Missing backend or any failed fixture is fatal.

Never calls a dispatcher, tick, device or production database. Evidence must be
incorporated into the newly signed protected manifest before promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from account_session_resume_state_contract import DB_ALLOWED_RESUME_STATES
from follow60_lock_v3 import RESUME_CONTRACT_SOURCE_FILES


def forbid_network(event, _args):
    if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "socket.bind"}:
        raise RuntimeError("resume_contract_gate_forbids_network")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-root", required=True)
    parser.add_argument("--node", default="node")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    backend = Path(args.backend_root).resolve(strict=True)
    if subprocess.check_output(["git", "-C", str(backend), "status", "--porcelain", "--untracked-files=no"], text=True).strip():
        raise SystemExit("backend_contract_requires_clean_tracked_source")
    if any(key.startswith("SUPABASE_") or key in {"DATABASE_URL", "NEXT_PUBLIC_SUPABASE_URL"} for key in os.environ):
        raise SystemExit("run_gate_with_clean_environment_no_database_credentials")
    sys.addaudithook(forbid_network)
    groups = {
        "RESUME_STATE_SCHEMA_CONTRACT_GATE": ["tests.test_resume_state_schema_contract_gate"],
        "RESUME_STATE_PYTHON_API_CONTRACT_GATE": ["tests.test_resume_state_python_api_contract"],
        "RESUME_STATE_END_TO_END_CONTRACT_GATE": [
            "tests.test_resume_state_end_to_end_contract", "tests.test_human_confirmed_resume_claim",
            "tests.test_account_session_resume_state_contract", "tests.test_account_session_resume_plan_store",
            "tests.test_auto_restart_runtime", "tests.test_auto_restart_hard_stop",
        ],
    }
    results = {}
    for name, modules in groups.items():
        stream = io.StringIO()
        suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        results[name] = {"status": "PASS" if result.wasSuccessful() and result.testsRun else "FAIL",
                         "tests": result.testsRun, "errors": len(result.errors),
                         "failures": len(result.failures), "log": stream.getvalue()}
    clean_env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "TMPDIR"}}
    child = subprocess.run([args.node, str(ROOT / "tests/resume-state-backend-contract.mjs"),
                            str(backend), json.dumps(sorted(DB_ALLOWED_RESUME_STATES))],
                           cwd=backend, env=clean_env, capture_output=True, text=True, timeout=60)
    try:
        backend_result = json.loads(child.stdout) if child.returncode == 0 else {"status": "FAIL"}
    except ValueError:
        backend_result = {"status": "FAIL"}
    backend_result["stderr"] = child.stderr
    backend_result["exit_code"] = child.returncode
    if backend_result.get("status") != "PASS":
        results["RESUME_STATE_END_TO_END_CONTRACT_GATE"]["status"] = "FAIL"
    status = "PASS" if all(item["status"] == "PASS" for item in results.values()) else "FAIL"
    evidence = {
        "schema": "RESUME_STATE_END_TO_END_CONTRACT_V1", "status": status,
        "gates": {key: value["status"] for key, value in results.items()}, "tests": results,
        "source_files": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                         for name in RESUME_CONTRACT_SOURCE_FILES},
        "backend_sha": subprocess.check_output(["git", "-C", str(backend), "rev-parse", "HEAD"], text=True).strip(),
        "backend": backend_result, "production_mutations": 0,
    }
    with Path(args.output).open("x", encoding="utf-8") as handle:
        json.dump(evidence, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({"status": status, "gates": evidence["gates"], "output": args.output}))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
