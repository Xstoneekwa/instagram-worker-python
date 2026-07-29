#!/usr/bin/env python3
"""Synthetic, no-device/no-network Gate 4B probe micro-benchmark."""

from __future__ import annotations

import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import target_availability_runtime as runtime
from target_availability_writer import TargetAvailabilityFeatureFlags


PILOT_ID = "22222222-2222-4222-8222-222222222222"
BASE = {
    "tenant_id": "11111111-1111-4111-8111-111111111111",
    "account_id": PILOT_ID,
    "target_id": "44444444-4444-4444-8444-444444444444",
    "username": "synthetic.target",
    "run_id": "synthetic-gate4b-benchmark",
}


def _measure(iterations: int, flags: TargetAvailabilityFeatureFlags) -> list[int]:
    samples: list[int] = []
    for index in range(iterations):
        started = time.perf_counter_ns()
        runtime.observe_rotation_target_loaded(**BASE, target_index=index, flags=flags)
        samples.append(time.perf_counter_ns() - started)
    return samples


def _summary(samples: list[int]) -> dict[str, float]:
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
    return {
        "mean_ns": round(statistics.mean(ordered), 2),
        "median_ns": round(statistics.median(ordered), 2),
        "p95_ns": float(ordered[p95_index]),
        "max_ns": float(max(ordered)),
    }


def main() -> int:
    iterations = 200
    with tempfile.TemporaryDirectory(prefix="gate4b-probe-benchmark-") as directory:
        status = Path(directory) / "status.json"
        environment = {
            "TARGET_AVAILABILITY_MEMORY_PROBE_ENABLED": "true",
            "TARGET_AVAILABILITY_MEMORY_PROBE_STATUS_FILE": str(status),
        }
        previous = {key: os.environ.get(key) for key in environment}
        os.environ.update(environment)
        try:
            off = _measure(iterations, TargetAvailabilityFeatureFlags())
            runtime._MEMORY_PROBE = None
            capture = _measure(
                iterations,
                TargetAvailabilityFeatureFlags(
                    target_availability_observation_capture_enabled=True,
                    account_allowlist=frozenset({PILOT_ID}),
                ),
            )
            payload = json.loads(status.read_text(encoding="utf-8"))
        finally:
            runtime._MEMORY_PROBE = None
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        result = {
            "iterations": iterations,
            "flags_off": _summary(off),
            "capture_memory": _summary(capture),
            "snapshot_size_bytes": status.stat().st_size,
            "aggregate_memory_bytes": payload["aggregate_memory_bytes"],
            "payload_retained_count": payload["current_run"]["payload_retained_count"],
            "observation_valid_count": payload["current_run"]["observation_valid_count"],
        }
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
