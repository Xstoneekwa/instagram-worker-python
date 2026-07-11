#!/usr/bin/env python3
"""Dry-run audit for account_follow_source_settings CT contract gaps."""

from __future__ import annotations

import argparse
import json
import sys

import follow_source_rotation_settings as contract


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit follow-source rotation contract (30/4) without DB mutation.",
    )
    parser.add_argument("--account-id", action="append", default=[], help="Account UUID to audit")
    parser.add_argument("--account-username", default="", help="Optional username label for logs")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    account_ids = [str(v).strip() for v in args.account_id if str(v).strip()]
    if not account_ids:
        payload = {
            "ok": False,
            "reason": "missing_account_id",
            "message": "Pass at least one --account-id",
            "db_mutation_performed": False,
        }
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(payload["message"], file=sys.stderr)
        return 2

    results = []
    for aid in account_ids:
        audit = contract.audit_follow_source_rotation_contract(
            aid,
            account_username=str(args.account_username or ""),
        )
        results.append(audit.to_dict())

    repair_required = [row for row in results if row.get("repair_required")]
    payload = {
        "ok": True,
        "audited_count": len(results),
        "repair_required_count": len(repair_required),
        "db_mutation_performed": False,
        "dry_run": True,
        "results": results,
    }
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"audited={payload['audited_count']} repair_required={payload['repair_required_count']}")
        for row in results:
            print(
                f"- {row.get('account_id')} source={row.get('settings_source')} "
                f"follows={row.get('max_follows_per_target_per_run')} "
                f"targets={row.get('max_targets_per_run')} "
                f"repair_action={row.get('repair_action')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
