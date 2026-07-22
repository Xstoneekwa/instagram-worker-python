"""Offline, aggregate-only replay for progressive Followers resume evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_redacted_corpus(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != "target_followers_resume_replay_v1":
        raise ValueError("unsupported replay schema")
    if data.get("contains_raw_usernames") is not False:
        raise ValueError("replay corpus must be username-redacted")
    return data


def compare_legacy_to_theoretical_v2(corpus: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in corpus.get("targets", []):
        legacy_scrolls = sum(int(value or 0) for value in target.get("observed_scroll_events", []))
        has_depth_proof = bool(target.get("has_validated_depth_transitions"))
        rows.append(
            {
                "target_alias": str(target.get("target_alias") or "unknown"),
                "runs_analyzed": int(target.get("run_count") or 0),
                "repeated_candidates_avoided": None,
                "legacy_scrolls": legacy_scrolls,
                "resume_scrolls": None if not has_depth_proof else int(target.get("theoretical_resume_scrolls") or 0),
                "estimated_seconds_saved": None,
                "confidence": "partial_no_historical_depth" if not has_depth_proof else "validated",
            }
        )
    return rows
