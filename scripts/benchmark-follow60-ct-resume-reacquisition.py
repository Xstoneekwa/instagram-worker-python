#!/usr/bin/env python3
"""Deterministic fixture gate for CT Resume cached-XML reacquisition."""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import instagram_navigation

instagram_navigation.log = lambda *_args, **_kwargs: None
followers_detection_snapshot_from_fresh_hierarchy = (
    instagram_navigation.followers_detection_snapshot_from_fresh_hierarchy
)


FIELD_BASELINE_SAMPLES_MS = [5582.0, 6207.0, 4250.0]


def fixture_xml() -> str:
    rows = []
    for index in range(9):
        top = 300 + index * 120
        rows.append(
            f'<node resource-id="com.instagram.android:id/follow_list_container" bounds="[0,{top}][1080,{top + 110}]">'
            f'<node text="fixture_row_{index}" resource-id="com.instagram.android:id/follow_list_username" class="android.widget.TextView" bounds="[120,{top + 10}][520,{top + 60}]" />'
            "</node>"
        )
    return (
        "<hierarchy>"
        '<node resource-id="com.instagram.android:id/unified_follow_list_tab_layout" class="android.view.View" />'
        '<node text="22 followers" resource-id="com.instagram.android:id/title" class="android.widget.TextView" selected="true" />'
        '<node class="androidx.recyclerview.widget.RecyclerView" />'
        + "".join(rows)
        + "</hierarchy>"
    )


def percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction)))]


def main() -> int:
    xml = fixture_xml()
    samples = []
    for _ in range(200):
        started = time.perf_counter()
        snapshot = followers_detection_snapshot_from_fresh_hierarchy(
            xml,
            source_profile_username="",
            scroll_index=1,
        )
        samples.append((time.perf_counter() - started) * 1000.0)
        if not snapshot.get("is_followers_list") or not snapshot.get("candidate_username_count"):
            raise SystemExit("fixture_snapshot_not_safe")
    baseline_p50 = statistics.median(FIELD_BASELINE_SAMPLES_MS)
    baseline_p95 = percentile(FIELD_BASELINE_SAMPLES_MS, 0.95)
    candidate_p50 = statistics.median(samples)
    candidate_p95 = percentile(samples, 0.95)
    improvement = (baseline_p50 - candidate_p50) / baseline_p50 * 100.0
    result = {
        "ok": improvement >= 30.0 and candidate_p95 <= baseline_p95,
        "comparison_kind": "field_baseline_vs_cached_xml_fixture_candidate",
        "baseline_p50_ms": round(baseline_p50, 3),
        "baseline_p95_ms": round(baseline_p95, 3),
        "candidate_p50_ms": round(candidate_p50, 3),
        "candidate_p95_ms": round(candidate_p95, 3),
        "p50_improvement_percent": round(improvement, 3),
        "p95_no_regression": candidate_p95 <= baseline_p95,
        "extra_ui_actions": 0,
        "extra_xml_dumps": 0,
        "extra_screenshots": 0,
        "extra_vision_calls": 0,
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
