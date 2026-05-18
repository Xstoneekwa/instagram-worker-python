#!/usr/bin/env python3
"""Parse Welcome DM run logs for performance audit (read-only)."""
from __future__ import annotations

import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / "runs"

RUN_IDS = [
    "e91c2042-d40d-4d5b-97ca-e9aaf73d0c57",  # clean 2 jobs
    "0abd3a65-a61e-447c-b0c7-15eb8aeeeb71",  # V4.5-C 3 jobs
    "1841a1ef-ccab-4bc9-80a4-d2d9e56fc54b",  # pre-fix claim empty
    "756d3c81-c5b5-40f4-bd9a-aeca345e797d",
    "dea9f913-e300-4d9f-a6c5-52ad6cfd95b7",  # yelemamayele crash era
    "77e3b395-3319-43c4-970a-bf7ec4e509b6",  # espehair fail
    "bce76ac0-cfed-428a-879b-a93b34e82063",  # espehair success V4.5-B
    "a33f0c95-c969-46e1-9c26-6cc05a42efa0",
]


def find_log(run_id: str) -> Path | None:
    for p in RUNS.glob("*.log"):
        if run_id[:8] in p.name:
            return p
    return None


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def load_events(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def delta_ms(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return (b - a).total_seconds() * 1000.0


def stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 1),
        "min": round(min(vals), 1),
        "max": round(max(vals), 1),
        "median": round(statistics.median(vals), 1),
    }


def analyze_run(path: Path) -> dict:
    evs = load_events(path)
    by_event: dict[str, list[dict]] = {}
    for e in evs:
        by_event.setdefault(e.get("event", ""), []).append(e)

    def first_ts(name: str) -> datetime | None:
        xs = by_event.get(name) or []
        return parse_ts(xs[0]["ts"]) if xs else None

    def last_ts(name: str) -> datetime | None:
        xs = by_event.get(name) or []
        return parse_ts(xs[-1]["ts"]) if xs else None

    scan_start = first_ts("welcome_session_scan_phase_started")
    scan_end = first_ts("welcome_session_scan_phase_completed")
    sender_start = first_ts("welcome_session_sender_phase_started")
    sender_summary = (by_event.get("welcome_list_sender_summary") or [{}])[-1]
    session_summary = (by_event.get("welcome_session_send_summary") or [{}])[-1]

    scan_breakdown = {}
    markers = [
        ("welcome_scan_started", "scan_inner_start"),
        ("welcome_scan_own_profile_open_started", "own_profile_open_start"),
        ("welcome_scan_own_profile_open_done", "own_profile_open_done"),
        ("welcome_scan_followers_list_open_started", "followers_open_start"),
        ("welcome_scan_followers_list_open_done", "followers_open_done"),
        ("welcome_scan_followers_surface_verified", "followers_verified"),
        ("welcome_scan_harvest_started", "harvest_start"),
        ("welcome_scan_harvest_done", "harvest_done"),
        ("welcome_scan_enqueue_batch_started", "enqueue_start"),
        ("welcome_scan_enqueue_batch_done", "enqueue_done"),
        ("welcome_scan_completed", "scan_completed"),
    ]
    ts_map = {}
    for ev_name, key in markers:
        t = first_ts(ev_name)
        if t:
            ts_map[key] = t
    if scan_start and scan_end:
        scan_breakdown["total_ms"] = delta_ms(scan_start, scan_end)
    prev = scan_start
    for key in [k for _, k in markers]:
        t = ts_map.get(key)
        if prev and t:
            scan_breakdown[f"to_{key}_ms"] = delta_ms(prev, t)
            prev = t

    # fallback: use welcome_scan_producer events if names differ
    for e in evs:
        ev = e.get("event", "")
        if "welcome_scan" in ev or "welcome_scan_producer" in ev:
            if "total_ms" in e and ev.endswith("_summary"):
                scan_breakdown["producer_summary_total_ms"] = e.get("total_ms")

    jobs = []
    claimed_idxs = [
        i
        for i, e in enumerate(evs)
        if e.get("event") == "dm_sender_job_claimed"
        and str(e.get("job_id") or "").strip()
    ]
    for idx in claimed_idxs:
        claim = evs[idx]
        job_id = claim.get("job_id")
        recipient = claim.get("recipient_username") or ""
        t_claim = parse_ts(claim["ts"])

        # find job end
        t_end = None
        end_ev = None
        for e in evs[idx + 1 :]:
            if e.get("event") in (
                "welcome_list_sender_job_completed_sent",
                "welcome_list_sender_job_completed_skipped",
                "welcome_list_sender_job_failed_retry_scheduled",
            ) and e.get("job_id") == job_id:
                t_end = parse_ts(e["ts"])
                end_ev = e.get("event")
                break

        job = {
            "recipient": recipient,
            "job_id": job_id[:8],
            "outcome": end_ev or "unknown",
            "wall_ms": delta_ms(t_claim, t_end),
            "dm_send_ms_logged": None,
            "list_nav_ms_logged": None,
        }
        if t_end:
            for e in evs[idx + 1 :]:
                if e.get("event") == end_ev and e.get("job_id") == job_id:
                    job["dm_send_ms_logged"] = e.get("dm_send_ms")
                    break

        # phases within job
        def span(start_ev: str, end_ev: str, **filters) -> float | None:
            t0 = t1 = None
            for e in evs[idx:]:
                if e.get("event") == start_ev and all(
                    e.get(k) == v for k, v in filters.items()
                ):
                    t0 = parse_ts(e["ts"])
                if t0 and e.get("event") == end_ev and all(
                    e.get(k) == v for k, v in filters.items()
                ):
                    t1 = parse_ts(e["ts"])
                    break
            return delta_ms(t0, t1)

        # profile open
        for e in evs[idx:]:
            if e.get("event") == "welcome_list_sender_profile_opened" and (
                not recipient or e.get("username") == recipient
            ):
                job["to_profile_open_ms"] = delta_ms(t_claim, parse_ts(e["ts"]))
                break

        for e in evs[idx:]:
            if e.get("event") == "welcome_list_sender_dm_thread_opened" and (
                not recipient or e.get("username") == recipient
            ):
                job["to_dm_thread_open_ms"] = delta_ms(t_claim, parse_ts(e["ts"]))
                job["thread_state"] = e.get("thread_state")
                break

        # thread detect window
        t_td_start = t_td_comp = t_td_state = None
        for e in evs[idx:]:
            u = e.get("username") or e.get("recipient_username")
            if recipient and u and u != recipient:
                continue
            ev = e.get("event", "")
            if ev == "welcome_dm_thread_detect_started":
                t_td_start = parse_ts(e["ts"])
            if ev == "dm_thread_composer_signal_seen":
                t_td_comp = parse_ts(e["ts"])
            if ev == "dm_thread_state_detected":
                t_td_state = parse_ts(e["ts"])
        job["thread_detect_ms"] = delta_ms(t_td_start, t_td_comp or t_td_state)
        job["thread_detect_to_state_ms"] = delta_ms(t_td_start, t_td_state)

        # send sub-phases
        t_send_start = t_typed = t_send_btn = t_verified = None
        for e in evs[idx:]:
            if e.get("username") != recipient and e.get("recipient_username") != recipient:
                if e.get("event", "").startswith("dm_sender") and recipient:
                    continue
            ev = e.get("event", "")
            if ev == "dm_sender_real_send_started" or ev == "dm_sender_typing_started":
                t_send_start = parse_ts(e["ts"])
            if ev == "dm_sender_real_send_draft_typed":
                t_typed = parse_ts(e["ts"])
            if ev == "dm_sender_real_send_button_tapped":
                t_send_btn = parse_ts(e["ts"])
            if ev == "dm_sender_real_send_verified":
                t_verified = parse_ts(e["ts"])
        job["typing_ms"] = delta_ms(t_send_start or t_td_state, t_typed)
        job["send_tap_ms"] = delta_ms(t_typed, t_send_btn)
        job["post_send_verify_ms"] = delta_ms(t_send_btn, t_verified)

        # return followers
        back_ms = None
        extra_back = None
        poll_attempt = None
        for e in evs[idx:]:
            if e.get("username") == recipient:
                if e.get("event") == "welcome_list_sender_back_profile_to_followers_done":
                    back_ms = e.get("ms")
                    extra_back = e.get("extra_back_tapped")
                if e.get("event") == "welcome_list_sender_back_profile_to_followers_poll_success":
                    poll_attempt = e.get("poll_attempt")
        job["back_to_followers_ms"] = back_ms
        job["extra_back_tapped"] = extra_back
        job["poll_attempt"] = poll_attempt

        # dm -> profile back
        for e in evs[idx:]:
            if e.get("username") == recipient and e.get("event") == "welcome_list_sender_back_dm_to_profile_done":
                job["back_dm_to_profile_ms"] = e.get("ms")
                break

        jobs.append(job)

    extra_back_cases = [j for j in jobs if j.get("extra_back_tapped") is True]
    fast_back = [j for j in jobs if j.get("extra_back_tapped") is False and j.get("back_to_followers_ms")]

    return {
        "path": path.name,
        "scan_ms": (by_event.get("welcome_session_scan_phase_completed") or [{}])[-1].get(
            "scan_total_ms"
        ),
        "sender_ms": sender_summary.get("total_ms"),
        "list_nav_total_ms": sender_summary.get("list_navigation_total_ms"),
        "dm_send_total_ms": sender_summary.get("dm_send_total_ms"),
        "session_ms": session_summary.get("total_ms"),
        "jobs_count": sender_summary.get("jobs_claimed_count"),
        "scan_breakdown": scan_breakdown,
        "jobs": jobs,
        "extra_back_count": len(extra_back_cases),
        "back_ms_stats": stats([float(j["back_to_followers_ms"]) for j in fast_back if j.get("back_to_followers_ms")]),
        "back_ms_extra_stats": stats(
            [float(j["back_to_followers_ms"]) for j in extra_back_cases if j.get("back_to_followers_ms")]
        ),
        "dm_send_logged_stats": stats(
            [float(j["dm_send_ms_logged"]) for j in jobs if j.get("dm_send_ms_logged")]
        ),
        "wall_job_stats": stats([float(j["wall_ms"]) for j in jobs if j.get("wall_ms")]),
        "thread_detect_stats": stats(
            [float(j["thread_detect_ms"]) for j in jobs if j.get("thread_detect_ms")]
        ),
    }


def main() -> None:
    results = []
    for rid in RUN_IDS:
        p = find_log(rid)
        if not p:
            print(f"MISSING {rid}", file=sys.stderr)
            continue
        results.append(analyze_run(p))

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
