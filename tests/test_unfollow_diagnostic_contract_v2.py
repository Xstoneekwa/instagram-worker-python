from __future__ import annotations

import inspect
import unittest
from collections import Counter

from unfollow_diagnostic_contract_v2 import (
    CANDIDATE_EVENT,
    CANDIDATE_SCHEMA_REQUIRED_FIELDS,
    EXTRA_UI_ACQUISITION,
    SESSION_START_EVENT,
    SESSION_START_SCHEMA_REQUIRED_FIELDS,
    SESSION_TERMINAL_EVENT,
    SESSION_TERMINAL_SCHEMA_REQUIRED_FIELDS,
    VIEWPORT_EVENT,
    VIEWPORT_SCHEMA_REQUIRED_FIELDS,
    UnfollowDiagnosticSession,
    classify_candidate_lineage,
    schema_completeness,
    viewport_overlap_metrics,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        self.value += 0.00001
        return self.value


def _candidate(username: str, rank: int) -> dict[str, object]:
    return {
        "username": username,
        "username_normalized": username.lower(),
        "job_id": f"job-{rank}",
        "job_created_at": "2026-08-09T00:00:00+00:00",
        "job_status_at_session_start": "eligible",
        "followed_at": "2026-08-01T00:00:00+00:00",
        "eligible_unfollow_at": "2026-08-04T00:00:00+00:00",
        "follow_source": "bot",
        "interacted_ledger_status": "followed",
    }


class UnfollowDiagnosticContractV2Tests(unittest.TestCase):
    def _session(self) -> tuple[UnfollowDiagnosticSession, list[tuple[str, dict[str, object]]]]:
        events: list[tuple[str, dict[str, object]]] = []

        def emit(_level: str, event: str, **fields: object) -> None:
            events.append((event, fields))

        alpha = _candidate("Alpha", 1)
        beta = _candidate("Beta", 2)
        delta = _candidate("Delta", 3)
        plan = {
            "candidates": [alpha, beta],
            "diagnostic_eligible_candidates_at_start": [alpha, beta, delta],
            "eligible_total": 3,
            "backlog_actionable_remaining": 3,
            "skipped_total": 2,
            "candidate_scan_exhaustive": True,
            "plan_reason": "strict_db_eligibility",
            "scan_as_of": "2026-08-09T00:00:00+00:00",
        }
        return (
            UnfollowDiagnosticSession(
                account_id="account-1",
                request_id="request-1",
                run_id="run-1",
                business_session_id="business-1",
                worker_sha="a" * 40,
                session_attempt=1,
                plan=plan,
                protected_usernames={"protected"},
                plan_cap=2,
                session_quota=2,
                daily_remaining=7,
                time_budget_seconds=120.0,
                cleanup_reserve=12.0,
                expected_max_actions=2,
                plan_admission_required=True,
                emit=emit,
                monotonic=_Clock(),
            ),
            events,
        )

    def test_all_emitted_event_families_are_schema_complete(self) -> None:
        session, events = self._session()
        session.emit_start()
        rows = [
            {"username": "Alpha"},
            {"username": "Beta"},
            {"username": "Outside"},
            {"username": "ALPHA"},
        ]
        visible_eval = {
            "visible_eligible_matches": [{"username": "Alpha"}],
            "visible_ineligible_rows": [
                {"username": "Beta", "skip_reason": "not_in_plan_after_live_filter"},
                {"username": "Outside", "skip_reason": "not_db_eligible"},
            ],
        }
        session.observe_viewport(
            rows=rows,
            visible_eval=visible_eval,
            row_cache={},
            scroll_depth=0,
            plan_remaining=2,
            verified_count=0,
            attempted_usernames=set(),
            verified_usernames=set(),
            persisted_usernames=set(),
            harvest_meta={"loading_state": "stable"},
            safe_stop_reason="",
            viewport_fingerprint_fn=lambda values: "|".join(values),
        )
        session.record_action_attempt("Alpha")
        session.emit_action_terminal(
            username="Alpha",
            row_cache={},
            attempted=True,
            verified=True,
            persisted=True,
            failure_reason="",
        )
        terminal = session.emit_terminal(
            status="completed",
            stop_reason="quota_reached",
            row_cache={},
            plan_remaining=1,
            attempted_usernames={"alpha"},
            verified_usernames={"alpha"},
            persisted_usernames={"alpha"},
            failed_count=0,
            filtered_count=2,
            end_of_list_status="not_reached",
        )

        required = {
            SESSION_START_EVENT: SESSION_START_SCHEMA_REQUIRED_FIELDS,
            VIEWPORT_EVENT: VIEWPORT_SCHEMA_REQUIRED_FIELDS,
            CANDIDATE_EVENT: CANDIDATE_SCHEMA_REQUIRED_FIELDS,
            SESSION_TERMINAL_EVENT: SESSION_TERMINAL_SCHEMA_REQUIRED_FIELDS,
        }
        counts = Counter(event for event, _payload in events)
        for event_name in required:
            self.assertGreater(counts[event_name], 0, event_name)
        for event_name, payload in events:
            result = schema_completeness(required[event_name], payload)
            self.assertEqual(result["completeness_percent"], 100.0, (event_name, result))
            self.assertEqual(result["missing_fields"], [])

        self.assertEqual(terminal["backlog_remaining"], 2)
        self.assertEqual(
            terminal["candidate_outcome_buckets"],
            {
                "ACTION_VERIFIED": 1,
                "UI_SEEN_BUT_FILTERED": 1,
                "DB_ELIGIBLE_BUT_NOT_PLAN_ADMITTED": 1,
                "NOT_DB_ELIGIBLE_YET": 1,
            },
        )

    def test_lineage_classifier_covers_every_required_bucket(self) -> None:
        fixtures = {
            "NOT_DB_ELIGIBLE_YET": {"db_eligible_at_session_start": False},
            "INSTRUMENTATION_GAP": {"db_eligible_at_session_start": None},
            "DB_ELIGIBLE_BUT_NOT_PLAN_ADMITTED": {
                "db_eligible_at_session_start": True,
                "plan_admitted": False,
            },
            "PLAN_ADMITTED_BUT_NOT_UI_SEEN": {
                "db_eligible_at_session_start": True,
                "plan_admitted": True,
                "ui_seen": False,
            },
            "INSTAGRAM_LIST_REORDERED": {
                "db_eligible_at_session_start": True,
                "plan_admitted": True,
                "ui_seen": True,
                "list_reordered": True,
            },
            "UI_SEEN_BUT_FILTERED": {
                "db_eligible_at_session_start": True,
                "plan_admitted": True,
                "ui_seen": True,
                "filters_passed": False,
            },
            "UI_SEEN_ACTIONABLE_BUT_NOT_ATTEMPTED": {
                "db_eligible_at_session_start": True,
                "plan_admitted": True,
                "ui_seen": True,
                "filters_passed": True,
                "actionable": True,
            },
            "ACTION_ATTEMPTED_BUT_FAILED": {
                "db_eligible_at_session_start": True,
                "plan_admitted": True,
                "ui_seen": True,
                "filters_passed": True,
                "actionable": True,
                "action_attempted": True,
                "action_verified": False,
            },
            "ACTION_VERIFIED": {
                "db_eligible_at_session_start": True,
                "plan_admitted": True,
                "ui_seen": True,
                "filters_passed": True,
                "actionable": True,
                "action_attempted": True,
                "action_verified": True,
            },
        }
        for expected, fixture in fixtures.items():
            with self.subTest(expected=expected):
                self.assertEqual(classify_candidate_lineage(fixture), expected)

    def test_viewport_overlap_reports_jaccard_order_new_rows_and_duplicates(self) -> None:
        metrics = viewport_overlap_metrics(
            ["alpha", "beta", "gamma"],
            ["beta", "alpha", "delta"],
        )
        self.assertEqual(metrics["overlap_count"], 2)
        self.assertAlmostEqual(metrics["overlap_percent"], 66.6667)
        self.assertEqual(metrics["jaccard_percent"], 50.0)
        self.assertFalse(metrics["overlap_order_preserved"])
        self.assertEqual(metrics["new_username_count"], 1)

        session, events = self._session()
        session.emit_start()
        viewport, _ = session.observe_viewport(
            rows=[{"username": "Alpha"}, {"username": "alpha"}, {"username": "Beta"}],
            visible_eval={"visible_eligible_matches": [], "visible_ineligible_rows": []},
            row_cache={},
            scroll_depth=1,
            plan_remaining=2,
            verified_count=0,
            attempted_usernames=set(),
            verified_usernames=set(),
            persisted_usernames=set(),
            harvest_meta={
                "following_list_end_detected": True,
                "following_list_end_reason": "suggested_boundary",
                "suggested_for_you_visible": True,
            },
            safe_stop_reason="end_of_list",
            viewport_fingerprint_fn=lambda values: "|".join(values),
        )
        self.assertEqual(viewport["duplicate_username_count"], 1)
        self.assertFalse(viewport["viewport_fingerprint_changed"])
        self.assertEqual(viewport["end_of_list_evidence"]["reason"], "suggested_boundary")
        self.assertTrue(viewport["end_of_list_evidence"]["suggestions_detected"])
        self.assertTrue(viewport["suggestions_boundary"])
        self.assertEqual(events[-3][0], VIEWPORT_EVENT)

    def test_contract_adds_no_ui_acquisition_or_ui_dependency(self) -> None:
        self.assertEqual(
            EXTRA_UI_ACQUISITION,
            {"xml": 0, "screenshot": 0, "tap": 0, "scroll": 0, "accessibility_query": 0},
        )
        source = inspect.getsource(__import__("unfollow_diagnostic_contract_v2"))
        for forbidden in (
            "import uiautomator",
            "import instagram_navigation",
            "dump_hierarchy(",
            "screenshot(",
            ".click(",
            ".swipe(",
        ):
            self.assertNotIn(forbidden, source)

    def test_cpu_and_payload_overhead_are_measured_not_assumed(self) -> None:
        session, _events = self._session()
        session.emit_start()
        for index in range(25):
            username = "Alpha" if index % 2 == 0 else "Beta"
            session.observe_viewport(
                rows=[{"username": username}],
                visible_eval={
                    "visible_eligible_matches": [{"username": username}],
                    "visible_ineligible_rows": [],
                },
                row_cache={},
                scroll_depth=index,
                plan_remaining=2,
                verified_count=0,
                attempted_usernames=set(),
                verified_usernames=set(),
                persisted_usernames=set(),
                harvest_meta={"loading_state": "stable"},
                safe_stop_reason="",
                viewport_fingerprint_fn=lambda values: "|".join(values),
            )
        terminal = session.emit_terminal(
            status="stopped",
            stop_reason="time_budget_exhausted",
            row_cache={},
            plan_remaining=2,
            attempted_usernames=set(),
            verified_usernames=set(),
            persisted_usernames=set(),
            failed_count=0,
            filtered_count=0,
            end_of_list_status="not_reached",
        )
        self.assertGreater(terminal["diagnostic_event_count"], 25)
        self.assertGreater(terminal["diagnostic_bytes_total"], 0)
        self.assertGreaterEqual(terminal["diagnostic_overhead_total_ms"], 0.0)
        self.assertLess(terminal["diagnostic_overhead_total_ms"], 100.0)


if __name__ == "__main__":
    unittest.main()
