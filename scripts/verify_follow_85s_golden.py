#!/usr/bin/env python3
"""Read-only verifier for FOLLOW_85S_PERFORMANCE_GOLDEN_V1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs/golden-evidence/follow-85s/manifest.json"
DEFAULT_RUNTIME = Path("/Users/admin/phonefarm-worker-current")
EXPECTED_RUNTIME_SHA = "ff99db6d7de48d75ede439c704e770feaaec6b7c"
BASELINE_CANDIDATE_TO_CANDIDATE_S = 85.225803


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _parse_events(path: Path) -> tuple[list[dict[str, Any]], str]:
    events: list[dict[str, Any]] = []
    evidence_type = "raw"
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            start = raw_line.find("{")
            if start < 0:
                continue
            try:
                item = json.loads(raw_line[start:])
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            if item.get("evidence_type") == "reconstructed_reference_evidence":
                evidence_type = "reconstructed"
            timestamp = item.get("ts")
            if not item.get("event") or not timestamp:
                continue
            try:
                item["_dt"] = datetime.fromisoformat(str(timestamp))
            except ValueError:
                continue
            events.append(item)
    if not events:
        raise ValueError(f"No timestamped JSON events found in {path}")
    return events, evidence_type


def _stats(values: Iterable[float]) -> dict[str, float | int | None]:
    points = list(values)
    if not points:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "count": len(points),
        "mean": round(statistics.fmean(points), 6),
        "median": round(statistics.median(points), 6),
        "min": round(min(points), 6),
        "max": round(max(points), 6),
    }


def measure(path: Path) -> dict[str, Any]:
    events, evidence_type = _parse_events(path)
    opens = [event for event in events if event["event"] == "follower_profile_open_success"]
    returns = [event for event in events if event["event"] == "post_follow_return_ct_success"]
    verified = [event for event in events if event["event"] == "follow_action_verified"]
    completed = [event for event in events if event["event"] == "follow_completed"]
    follows = verified or completed

    strict_cycles: list[dict[str, Any]] = []
    point3: list[dict[str, Any]] = []
    candidate_to_candidate: list[dict[str, Any]] = []

    for index, current_open in enumerate(opens):
        next_open = opens[index + 1] if index + 1 < len(opens) else None
        matching_returns = [
            event
            for event in returns
            if event["_dt"] > current_open["_dt"]
            and (next_open is None or event["_dt"] < next_open["_dt"])
        ]
        if not matching_returns:
            continue
        current_return = matching_returns[-1]
        strict_cycles.append(
            {
                "candidate": current_open.get("follower_username", ""),
                "seconds": round((current_return["_dt"] - current_open["_dt"]).total_seconds(), 6),
            }
        )
        if next_open is None:
            continue

        between = [
            event
            for event in events
            if current_return["_dt"] < event["_dt"] < next_open["_dt"]
        ]
        scroll_events = [
            event
            for event in between
            if event["event"] == "followers_list_soft_scroll_started"
        ]
        recovery_events = [
            event
            for event in between
            if "recovery" in str(event["event"]).lower()
            and (str(event["event"]).endswith("_started") or str(event["event"]).endswith("_attempt"))
        ]
        transition = {
            "from_candidate": current_open.get("follower_username", ""),
            "to_candidate": next_open.get("follower_username", ""),
            "seconds": round((next_open["_dt"] - current_return["_dt"]).total_seconds(), 6),
            "scrolls": len(scroll_events),
            "recoveries": len(recovery_events),
        }
        point3.append(transition)
        candidate_to_candidate.append(
            {
                "from_candidate": transition["from_candidate"],
                "to_candidate": transition["to_candidate"],
                "seconds": round((next_open["_dt"] - current_open["_dt"]).total_seconds(), 6),
                "scrolls": len(scroll_events),
                "recoveries": len(recovery_events),
            }
        )

    no_scroll_point3 = [item["seconds"] for item in point3 if item["scrolls"] == 0]
    c2c_stats = _stats(item["seconds"] for item in candidate_to_candidate)
    c2c_mean = c2c_stats["mean"]
    throughput = round(3600.0 / float(c2c_mean), 6) if c2c_mean else None
    recovery_counts = Counter(
        str(event["event"])
        for event in events
        if "recovery" in str(event["event"]).lower()
        and (str(event["event"]).endswith("_started") or str(event["event"]).endswith("_attempt"))
    )

    follow_states_ok = all(str(event.get("follow_state_after", "")).lower() == "following" for event in completed)
    invariants = {
        "profile_open_count": len(opens),
        "follow_verified_count": len(follows),
        "return_ct_success_count": len(returns),
        "follow_state_after_is_following": follow_states_ok,
        "each_open_has_return_before_next": len(strict_cycles) == len(opens),
        "counts_balanced": len(opens) == len(follows) == len(returns),
    }

    comparison = "insufficient_evidence"
    if c2c_mean is not None:
        delta = float(c2c_mean) - BASELINE_CANDIDATE_TO_CANDIDATE_S
        if abs(delta) <= 1.0:
            comparison = "matches_follow_85s_baseline"
        elif delta > 1.0:
            comparison = "slower_than_follow_85s_baseline"
        else:
            comparison = "faster_than_follow_85s_baseline_requires_physical_review"

    return {
        "schema_version": 1,
        "command": "compare-run",
        "log": str(path.resolve()),
        "evidence_type": evidence_type,
        "strict_cycle_seconds": _stats(item["seconds"] for item in strict_cycles),
        "point3_seconds": {
            "all": _stats(item["seconds"] for item in point3),
            "without_scroll": _stats(no_scroll_point3),
        },
        "candidate_to_candidate_seconds": c2c_stats,
        "throughput_follows_per_hour": throughput,
        "scroll_transition_count": sum(1 for item in point3 if item["scrolls"] > 0),
        "recovery_events": dict(sorted(recovery_counts.items())),
        "functional_invariants": invariants,
        "comparison_to_follow_85s": {
            "baseline_seconds": BASELINE_CANDIDATE_TO_CANDIDATE_S,
            "delta_seconds": round(float(c2c_mean) - BASELINE_CANDIDATE_TO_CANDIDATE_S, 6) if c2c_mean else None,
            "classification": comparison,
        },
        "cycles": strict_cycles,
        "transitions": candidate_to_candidate,
    }


def verify_runtime(args: argparse.Namespace) -> int:
    runtime = Path(args.runtime_root)
    resolved = runtime.resolve(strict=True)
    result = subprocess.run(
        ["git", "-C", str(resolved), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    actual = result.stdout.strip()
    payload = {
        "command": "verify-runtime",
        "symlink": str(runtime),
        "resolved_root": str(resolved),
        "expected_sha": args.expected_sha,
        "actual_sha": actual,
        "ok": actual == args.expected_sha,
        "read_only": True,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 1


def verify_evidence(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    manifest = _load_json(manifest_path)
    errors: list[str] = []
    checked: list[dict[str, Any]] = []
    for run in manifest.get("reference_runs", []):
        evidence = run.get("evidence") or {}
        source_path = Path(str(evidence.get("source_path") or ""))
        expected_hash = str(evidence.get("sha256") or "")
        item = {
            "run_id": run.get("run_id"),
            "evidence_type": evidence.get("evidence_type"),
            "source_path": str(source_path),
            "exists": source_path.is_file(),
            "hash_ok": False,
        }
        if not item["exists"]:
            errors.append(f"missing evidence: {source_path}")
        else:
            actual_hash = _sha256(source_path)
            item["actual_sha256"] = actual_hash
            item["hash_ok"] = actual_hash == expected_hash
            if not item["hash_ok"]:
                errors.append(f"hash mismatch: {source_path}")
        checked.append(item)

    payload = {
        "command": "verify-evidence",
        "manifest": str(manifest_path),
        "checked": checked,
        "errors": errors,
        "ok": not errors,
        "read_only": True,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 1


def compare_run(args: argparse.Namespace) -> int:
    payload = measure(Path(args.log))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["functional_invariants"]["counts_balanced"] else 1


def rollback_plan(_: argparse.Namespace) -> int:
    payload = {
        "command": "rollback-plan",
        "read_only": True,
        "executed": False,
        "target_release": "/Users/admin/phonefarm-worker-releases/ff99db6-follow-persistence-rpc-v1",
        "target_sha": EXPECTED_RUNTIME_SHA,
        "preconditions": [
            "queue empty",
            "no active account_run_request or ig_run",
            "no runner.py business subprocess",
            "no active device lock or UI lease",
        ],
        "operator_plan": [
            "verify the immutable target release HEAD",
            "record the current phonefarm-worker-current target",
            "atomically repoint phonefarm-worker-current to the target release",
            "restart only the durable dispatcher if its cwd did not follow the symlink",
            "verify one dispatcher, target cwd, queue empty, and no business worker",
        ],
        "note": "This command only prints the plan and never changes the runtime.",
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    runtime = subparsers.add_parser("verify-runtime")
    runtime.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME))
    runtime.add_argument("--expected-sha", default=EXPECTED_RUNTIME_SHA)
    runtime.set_defaults(func=verify_runtime)

    evidence = subparsers.add_parser("verify-evidence")
    evidence.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    evidence.set_defaults(func=verify_evidence)

    compare = subparsers.add_parser("compare-run")
    compare.add_argument("--log", required=True)
    compare.set_defaults(func=compare_run)

    rollback = subparsers.add_parser("rollback-plan")
    rollback.set_defaults(func=rollback_plan)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"command": args.command, "ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
