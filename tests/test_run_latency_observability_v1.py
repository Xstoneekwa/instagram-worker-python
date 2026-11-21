from __future__ import annotations

import time
import os
import contextlib
import io
import json
import tempfile
from statistics import median
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from pathlib import Path

import run_latency_observability_v1 as obs


class CandidateWindowTests(unittest.TestCase):
    def setUp(self):
        obs.reset_for_tests()

    def test_post_open_skip_and_private_end_bind_to_the_correct_cycle(self):
        with patch.object(obs.time, "monotonic", return_value=1.0) as clock:
            current = {"candidate_username": "first"}
            obs.observe_runtime_event("followers_candidate_selected", current)
            obs.observe_runtime_event("post_follow_post_likes_phase_started", current)
            obs.observe_runtime_event("post_like_surface_to_scroll_gap_started", current)
            clock.return_value = 1.3
            rows = obs.observe_runtime_event("post_follow_like_perf_summary", {
                **current, "result": "skipped", "failure_reason": "no_posts",
            })
            self.assertEqual([(r["phase"], r["outcome"]) for r in rows],
                             [("post_open", "skipped"), ("like", "skipped")])
            self.assertEqual(rows[0]["duration_ms"], 300)
            clock.return_value = 5
            rows = obs.observe_runtime_event("post_return_critical_persist_completed", current)
            self.assertEqual([r["phase"] for r in rows], ["cycle"])
            private = {"candidate_username": "private"}
            obs.observe_runtime_event("followers_candidate_selected", private)
            flags = {"safe_to_skip": True, "return_ok": True, "reason": "private_account"}
            self.assertEqual(obs.observe_runtime_event("private_skip_fast_path_completed", {
                **flags, **current,
            }), [])
            clock.return_value = 6
            cycle, = obs.observe_runtime_event("private_skip_fast_path_completed", {
                **flags, **private,
            })
            self.assertEqual((cycle["phase"], cycle["outcome"]), ("cycle", "skipped"))
            clock.return_value = 8
            gap, = obs.observe_runtime_event("followers_candidate_selected", {"candidate_username": "third"})
            self.assertEqual((gap["metric"], gap["duration_ms"]), ("inter_cycle_gap", 2000))

    def test_cycle_excludes_startup_and_transition_and_counts_overlaps_once(self):
        with patch.object(obs.time, "monotonic", return_value=1.0) as monotonic:
            obs.observe_runtime_event("run_latency_rpc_timing_v1", {"duration_ms": 900})
            obs.observe_runtime_event("followers_candidate_selected", {"candidate_username": "first"})
            obs.observe_runtime_event("run_latency_rpc_timing_v1", {
                "attempt_count": 2, "backoff_ms": 25, "duration_ms": 150, "monotonic_ms": 1200,
            })
            obs.observe_runtime_event("run_latency_io_timing_v1", {
                "io_kind": "xml_capture", "duration_ms": 50, "monotonic_ms": 1150,
            })
            monotonic.return_value = 1.3
            first, = obs.observe_runtime_event("post_return_critical_persist_completed", {})
            self.assertEqual(first["rpc_total_ms"], 150)
            self.assertEqual(first["rpc_attempt_count_measured"], 2)
            self.assertEqual(first["rpc_backoff_ms_measured"], 25)
            self.assertEqual(first["exclusive_operation_ms"], {"xml_capture": 50, "rpc": 100})
            self.assertEqual(first["unclassified_ms"], 150)
            obs.observe_runtime_event("run_latency_wait_v1", {"actual_ms": 60, "monotonic_ms": 1400})
            monotonic.return_value = 1.5
            gap, = obs.observe_runtime_event("followers_candidate_selected", {"candidate_username": "second"})
            self.assertEqual(gap["exclusive_operation_ms"], {"wait": 60})
            second, = obs.observe_runtime_event("post_return_critical_persist_completed", {})
            self.assertEqual(second["wait_actual_total_ms"], 0)

    def test_stop_closes_candidate_and_old_invocation_cannot_close_current_return(self):
        selected = "followers_candidate_selected"
        started, success = "post_follow_return_ct_started", "post_follow_return_ct_success"
        obs.observe_runtime_event(selected, {"run_id": "run", "follower_username": "one"})
        obs.observe_runtime_event(started, {"follower_username": "one"})
        rows = obs.observe_runtime_event(selected, {"follower_username": "two"})
        old = next(row for row in rows if row.get("phase") == "return_ct")
        self.assertEqual(old["candidate_username"], "one")
        obs.observe_runtime_event(started, {"follower_username": "two"})
        self.assertFalse(obs.observe_runtime_event(success, {
            "follower_username": "two", "phase_invocation": old["phase_invocation"],
        }))
        stop = obs.observe_runtime_event("manual_stop_signal_received", {"run_id": "run"})
        self.assertEqual({row["phase"] for row in stop}, {"cycle", "return_ct"})
        self.assertTrue(all(row["outcome"] == "interrupted" for row in stop))
        self.assertFalse(obs.observe_runtime_event(success, {"follower_username": "two"}))
        self.assertFalse(obs.observe_runtime_event("manual_stop_signal_received", {"run_id": "run"}))

    def test_admission_gap_excludes_a_second_transport_of_the_same_candidate(self):
        token = "follower_profile_open_row_action_token_committed"
        with patch.object(obs.time, "monotonic", return_value=1.0) as mono:
            obs.observe_runtime_event("followers_candidate_selected", {"follower_username": "one"})
            first, = obs.observe_runtime_event(token, {"follower_username": "one"})
            mono.return_value = 1.2
            retry, = obs.observe_runtime_event(token, {"follower_username": "one"})
            self.assertIsNone(first["admission_gap_ms"])
            self.assertFalse(retry["first_for_candidate"])
            mono.return_value = 2.0
            obs.observe_runtime_event("followers_candidate_selected", {"follower_username": "two"})
            second, = obs.observe_runtime_event(token, {"follower_username": "two"})
            self.assertEqual(second["admission_gap_ms"], 1000)

    def test_existing_ct_rotation_events_close_on_proof_of_the_next_list(self):
        obs.observe_runtime_event("follow_target_rotation_requested", {
            "run_id": "r", "source_target": "previous.ct",
        })
        obs.observe_runtime_event("target_scan_completed", {"stop_reason": "target_budget_reached"})
        self.assertFalse(obs.observe_runtime_event(
            "follow_latency_ct_confirmed", {"source_profile_username": "next.ct"},
        ))
        obs.observe_runtime_event("follow_target_rotation_requested", {
            "run_id": "r", "from_source_target": "previous.ct", "to_source_target": "next.ct",
            "next_target_id": "next",
        })
        obs.observe_runtime_event("target_scan_started", {"source_profile_username": "next.ct"})
        rows = obs.observe_runtime_event("follow_latency_ct_confirmed", {
            "source_profile_username": "next.ct", "target_id": "next",
        })
        self.assertEqual([r["phase"] for r in rows], ["ct_rotation"])
        self.assertEqual(rows[0]["source_profile_username"], "previous.ct")

    def test_actual_ct_absence_skip_records_recovery_and_only_closes_on_bound_next_target(self):
        with patch.object(obs.time, "monotonic", return_value=1.0) as mono:
            obs.observe_runtime_event("startup_ct_row_detect_started", {"run_id": "r"})
            obs.observe_runtime_event("follow_ct_search_results_blank_after_query", {"target_username": "old.ct"})
            mono.return_value = 1.4
            recovery, = obs.observe_runtime_event("follow_ct_search_results_recovery_completed", {
                "target_username": "old.ct", "final_reason": "search_results_still_blank_after_recovery",
            })
            row, = obs.observe_runtime_event("startup_ct_row_detect_completed", {"found": False})
            self.assertEqual((recovery["phase"], recovery["outcome"], recovery["duration_ms"]),
                             ("ct_search_recovery", "failed", 400))
            self.assertEqual(row["outcome"], "failed")
            obs.observe_runtime_event("target_scan_completed", {"target_username": "old.ct"})
            mono.return_value = 1.5
            obs.observe_runtime_event("follow_target_local_failure_skipped_current_attempt", {
                "run_id": "r", "source_profile": "old.ct", "target_id": "old",
                "next_source_profile": "new.ct", "next_target_id": "new",
                "first_causal_reason": "search_results_still_blank_after_recovery",
            })
            obs.observe_runtime_event("follow_target_selected", {
                "run_id": "r", "source_profile": "new.ct", "target_id": "new",
            })
            for profile, target_id in (("old.ct", "old"), ("new.ct", "old"), ("foreign.ct", "new")):
                self.assertFalse(obs.observe_runtime_event("follow_latency_ct_confirmed", {
                    "run_id": "r", "source_profile_username": profile, "target_id": target_id,
                }))
            mono.return_value = 2.0
            rotation, = obs.observe_runtime_event("follow_latency_ct_confirmed", {
                "run_id": "r", "source_profile_username": "new.ct", "target_id": "new",
            })
            self.assertEqual((rotation["phase"], rotation["outcome"], rotation["duration_ms"]),
                             ("ct_rotation", "completed", 500))
            self.assertEqual(rotation["next_target_id"], "new")
            self.assertEqual(rotation["rotation_reason"], "search_results_still_blank_after_recovery")

    def test_interrupted_ct_rotation_preserves_ambiguity_without_an_ack(self):
        obs.observe_runtime_event("follow_ct_search_results_recovery_skipped", {
            "target_username": "old.ct", "final_reason": "search_results_ambiguous_no_exact_match",
        })
        obs.observe_runtime_event("follow_target_local_failure_skipped_current_attempt", {
            "source_profile": "old.ct", "next_source_profile": "new.ct",
            "first_causal_reason": "search_results_ambiguous_no_exact_match",
        })
        row, = obs.observe_runtime_event("manual_stop_signal_received", {})
        self.assertEqual(row["outcome"], "interrupted")
        self.assertEqual(row["rotation_reason"], "search_results_ambiguous_no_exact_match")
        self.assertFalse(obs.observe_runtime_event("follow_latency_ct_confirmed", {
            "source_profile_username": "new.ct",
        }))


def test_rpc_attempts_and_measured_backoff_use_the_existing_transport():
    import supabase_client as client

    response = unittest.mock.MagicMock()
    response.__enter__.return_value.read.return_value = b'{"ok":true}'
    clock = [2.0]

    def sleep(seconds):
        clock[0] += seconds + 0.012

    with patch.object(client, "_base_url", return_value="https://fictional.invalid"), patch.object(
        client, "_service_key", return_value="fixture-key",
    ), patch.object(client.request, "urlopen", side_effect=[TimeoutError, response]) as transport, patch.object(
        client, "_rest_retry_backoff_seconds", return_value=0.05,
    ), patch.object(client.time, "sleep", side_effect=sleep) as wait, patch.object(
        client.time, "monotonic", side_effect=lambda: clock[0],
    ), patch.object(client, "log") as log:
        assert client._request_json("GET", "fictional_table", max_retries=1) == {"ok": True}
        assert transport.call_count == 2
        wait.assert_called_once_with(0.05)
        row = log.call_args.kwargs
        assert row["attempt_count"] == 2
        assert row["backoff_ms"] == 62
        assert row["monotonic_ms"] == 2062
        assert "fixture-key" not in str(row)


def test_nonretriable_rpc_preserves_single_attempt_and_terminal_error():
    import supabase_client as client

    with patch.object(client, "_base_url", return_value="https://fictional.invalid"), patch.object(
        client, "_service_key", return_value="fixture-key",
    ), patch.object(client.request, "urlopen", side_effect=TimeoutError) as transport, patch.object(
        client.time, "sleep",
    ) as wait, patch.object(client, "log") as log:
        try:
            client.call_rpc_once("fictional_mutation", timeout_seconds=5)
        except client.SupabaseRestError:
            pass
        else:
            raise AssertionError("The original transport error was lost")
        transport.assert_called_once()
        wait.assert_not_called()
        row = log.call_args.kwargs
        assert row["attempt_count"] == 1
        assert row["backoff_ms"] == 0
        assert row["status"] == "failed"


def test_full_telemetry_cost_with_real_local_log_file():
    import logs

    class TimedFile:
        def __init__(self, sink):
            self.sink = sink
            self.elapsed = 0
            self.writes = 0
            self.flushes = 0

        def write(self, line):
            self.writes += 1
            started = time.perf_counter_ns()
            try:
                return self.sink.write(line)
            finally:
                self.elapsed += time.perf_counter_ns() - started

        def flush(self):
            self.flushes += 1
            started = time.perf_counter_ns()
            try:
                self.sink.flush()
            finally:
                self.elapsed += time.perf_counter_ns() - started

    def replay(file, enabled):
        obs.reset_for_tests()
        sink = TimedFile(file)
        serialized = 0
        raw = _FakeDevice()

        def dumps(*args, **kwargs):
            nonlocal serialized
            started = time.perf_counter_ns()
            try:
                return json.dumps(*args, **kwargs)
            finally:
                serialized += time.perf_counter_ns() - started

        with patch.dict(os.environ, {"PHONEFARM_RUN_LATENCY_OBSERVABILITY_V1": str(int(enabled))}), patch.object(
            logs, "_RUN_LOG_FILE", sink,
        ), patch.object(logs, "_FILE_DEGRADED", False), patch.object(
            logs, "json", SimpleNamespace(dumps=dumps),
        ), contextlib.redirect_stdout(io.StringIO()):
            device = obs.instrument_device(raw, emit=logs.log)
            cpu_start, wall_start = time.process_time_ns(), time.perf_counter_ns()
            for i in range(20):
                logs.log("info", "followers_candidate_selected", run_id="fixture", candidate_username=f"candidate-{i}")
                logs.log("info", "follower_profile_open_row_action_token_committed")
                device.shell("fixture")
                device.dump_hierarchy()
                device(text="Follow").click()
                logs.log("info", "post_return_critical_persist_completed")
            wall_ns = time.perf_counter_ns() - wall_start
            cpu_ns = time.process_time_ns() - cpu_start
        file.flush()
        file.seek(0)
        lines = file.readlines()
        assert raw.shell_calls == raw.ui.clicks == 20
        return {
            "cpu_ms": cpu_ns / 1e6, "wall_ms": wall_ns / 1e6,
            "serialize_ms": serialized / 1e6, "local_write_ms": sink.elapsed / 1e6,
            "bytes": sum(len(line.encode()) for line in lines),
            "events": len(lines),
            "writes": sink.writes,
            "flushes": sink.flushes,
        }

    samples = {False: [], True: []}
    with tempfile.TemporaryDirectory(prefix="telemetrie-follow446-") as tmp:
        for i in range(5):
            for enabled in (False, True) if i % 2 == 0 else (True, False):
                path = Path(tmp) / f"{i}-{enabled}.jsonl"
                with path.open("w+", encoding="utf-8") as stream:
                    samples[enabled].append(replay(stream, enabled))
    off, on = ({key: median(row[key] for row in samples[mode]) for key in samples[mode][0]}
               for mode in (False, True))
    assert off["events"] == 60
    assert off["events"] < on["events"] <= off["events"] + 20 * 6
    assert on["writes"] == on["flushes"] <= 159
    print("TELEMETRY_LOCAL_REPLAY", json.dumps({"off": off, "on": on}, sort_keys=True))


def test_real_logger_keeps_stop_and_completed_runs_isolated(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import logs

    rendezvous = Barrier(2, timeout=5)

    def drive(run, candidate, stop):
        identity = {"run_id": run, "account_id": "account-" + run, "candidate_username": candidate}
        logs.log("info", "followers_candidate_selected", **identity)
        rendezvous.wait()
        logs.log("info", "post_follow_return_ct_started", **identity)
        logs.log("info", "run_latency_operation_v1", operation="return_ct_back", io_kind="uiautomator",
                 _defer_follow_timing=True, duration_ms=7, **identity)
        rendezvous.wait()
        if stop:
            logs.log("warning", "manual_stop_signal_received", first_causal_reason="operator_stop", **identity)
        else:
            logs.log("info", "post_follow_return_ct_success", **identity)
            logs.log("info", "post_return_critical_persist_completed", **identity)

    path = tmp_path / "runs.jsonl"
    with path.open("w", encoding="utf-8") as sink, patch.object(
        logs, "_RUN_LOG_FILE", sink,
    ), patch.object(logs, "_FILE_DEGRADED", False), patch.object(
        logs, "_write_payload", wraps=logs._write_payload,
    ) as writer, contextlib.redirect_stdout(io.StringIO()):
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(drive, "a", "candidate-a", True)
            b = pool.submit(drive, "b", "candidate-b", False)
            a.result(timeout=10)
            b.result(timeout=10)
        assert writer.call_count == 7

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for run, stop in (("a", True), ("b", False)):
        group = [row for row in rows if row["run_id"] == run]
        assert all(row["candidate_username"] == "candidate-" + run for row in group)
        elapsed, = [row for row in group if row["event"] == "run_latency_operation_v1"]
        assert elapsed["duration_ms"] == 7
        terminal, = [row for row in group if row.get("phase") == "return_ct"]
        assert terminal["outcome"] == ("interrupted" if stop else "completed")
        assert terminal["source_event"] == ("manual_stop_signal_received" if stop else "post_follow_return_ct_success")


def test_timing_carrier_keeps_failed_and_skipped_rows_without_delaying_primary():
    import logs

    class Invalid:
        def __str__(self):
            raise ValueError("optional serialization failure")

    obs.reset_for_tests()
    sink = io.StringIO()
    with patch.object(logs, "_RUN_LOG_FILE", sink), patch.object(logs, "_FILE_DEGRADED", False), contextlib.redirect_stdout(io.StringIO()):
        for outcome in ("failed", "skipped"):
            logs.log("info", "run_latency_operation_v1", operation="test", outcome=outcome,
                     run_id="same-run", request_id="old-request", _defer_follow_timing=True)
        logs.log("info", "run_latency_operation_v1", operation=Invalid(),
                 run_id="same-run", request_id="old-request", _defer_follow_timing=True)
        logs.log("warning", "device_lock_renewal_not_proven", run_id="new-run",
                 request_id="new-request", _defer_follow_timing=True)
        assert [json.loads(line)["event"] for line in sink.getvalue().splitlines()] == [
            "device_lock_renewal_not_proven",
        ]
        logs.log("warning", "run_aborted", run_id="same-run", request_id="old-request")
    rows = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert [row.get("outcome") for row in rows if row["event"] == "run_latency_operation_v1"] == ["failed", "skipped"]
    assert all(row["run_id"] == "same-run" for row in rows[1:])


def test_process_exit_preserves_already_written_receipts(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "abrupt.jsonl"
    body = """
from pathlib import Path
import os, sys
import logs
logs._RUN_LOG_FILE = Path(sys.argv[1]).open("w", encoding="utf-8")
logs.log("info", "followers_candidate_selected", run_id="abrupt", candidate_username="one")
logs.log("info", "post_follow_return_ct_started", run_id="abrupt", candidate_username="one")
logs.log("info", "run_latency_operation_v1", operation="return_ct_back", io_kind="uiautomator",
         run_id="abrupt", _defer_follow_timing=True)
os._exit(0)
"""
    subprocess.run([sys.executable, "-B", "-c", body, str(path)], capture_output=True, check=True, timeout=10)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["event"] for row in rows] == ["followers_candidate_selected", "post_follow_return_ct_started"]


class _FakeUi:
    def __init__(self) -> None:
        self.clicks = 0

    def click(self) -> str:
        self.clicks += 1
        return "clicked"

    @property
    def exists(self) -> bool:
        return True


class _FakeDevice:
    def __init__(self) -> None:
        self.ui = _FakeUi()
        self.shell_calls = 0

    def __call__(self, **_selector: object) -> _FakeUi:
        return self.ui

    def shell(self, command: str) -> str:
        self.shell_calls += 1
        return command

    def dump_hierarchy(self) -> str:
        return "<hierarchy><node /></hierarchy>"


class _CallableExists:
    def __init__(self) -> None:
        self.calls = 0

    def __bool__(self) -> bool:
        self.calls += 1
        return True

    def __call__(self, timeout: float = 0) -> bool:
        self.calls += 1
        return timeout >= 0


class _FakeCallableUi:
    def __init__(self) -> None:
        self._exists = _CallableExists()

    @property
    def exists(self) -> _CallableExists:
        return self._exists


class _FakeCallableDevice:
    def __init__(self) -> None:
        self.ui = _FakeCallableUi()

    def __call__(self, **_selector: object) -> _FakeCallableUi:
        return self.ui


class RunLatencyObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        obs.reset_for_tests()

    def test_phase_duration_uses_monotonic_and_cpu(self) -> None:
        obs.observe_runtime_event("followers_candidate_selected", {"run_id": "r1"})
        rows = obs.observe_runtime_event(
            "post_return_critical_persist_completed", {"candidate_username": "candidate"}
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phase"], "cycle")
        self.assertGreaterEqual(rows[0]["duration_ms"], 0)
        self.assertIn("process_cpu_ms", rows[0])

    def test_inter_cycle_gap_is_emitted(self) -> None:
        obs.observe_runtime_event("followers_candidate_selected", {})
        obs.observe_runtime_event("post_return_critical_persist_completed", {})
        rows = obs.observe_runtime_event("followers_candidate_selected", {})
        self.assertEqual(rows[0]["metric"], "inter_cycle_gap")

    def test_context_is_bounded_to_non_sensitive_identity(self) -> None:
        obs.observe_runtime_event(
            "followers_candidate_selected",
            {"account_id": "a", "secret": "must-not-appear"},
        )
        rows = obs.observe_runtime_event("post_return_critical_persist_completed", {})
        self.assertEqual(rows[0]["account_id"], "a")
        self.assertNotIn("secret", rows[0])

    def test_existing_wait_is_timed_once(self) -> None:
        calls: list[float] = []
        events: list[tuple[str, str, dict]] = []

        def fake_sleep(value: float) -> None:
            calls.append(value)
            time.sleep(0.001)

        def emit(level: str, event: str, **fields: object) -> None:
            events.append((level, event, fields))

        obs.observed_wait(0.25, reason="settle", sleep=fake_sleep, emit=emit)
        self.assertEqual(calls, [0.25])
        self.assertEqual(events[0][1], "run_latency_wait_v1")
        self.assertEqual(events[0][2]["requested_ms"], 250.0)

    def test_device_proxy_does_not_add_calls(self) -> None:
        raw = _FakeDevice()
        events: list[dict] = []

        def emit(_level: str, _event: str, **fields: object) -> None:
            events.append(dict(fields))

        device = obs.instrument_device(raw, emit=emit)
        self.assertEqual(device.shell("echo ok"), "echo ok")
        self.assertEqual(raw.shell_calls, 1)
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["extra_phone_call"])

    def test_ui_proxy_preserves_call_and_property_semantics(self) -> None:
        raw = _FakeDevice()
        ui = obs.instrument_device(raw)(text="Follow")
        self.assertTrue(ui.exists)
        self.assertEqual(ui.click(), "clicked")
        self.assertEqual(raw.ui.clicks, 1)

    def test_callable_exists_preserves_bool_and_timeout_semantics(self) -> None:
        raw = _FakeCallableDevice()
        events: list[dict] = []
        ui = obs.instrument_device(
            raw,
            emit=lambda _level, _event, **fields: events.append(dict(fields)),
        )(text="Follow")
        self.assertTrue(bool(ui.exists))
        self.assertTrue(ui.exists(timeout=0.1))
        self.assertEqual(raw.ui._exists.calls, 2)
        self.assertEqual([row["operation"] for row in events], ["exists", "exists"])

    def test_observer_failure_surface_is_empty_for_unrelated_event(self) -> None:
        self.assertEqual(obs.observe_runtime_event("unrelated", {"payload": object()}), [])

    def test_next_candidate_closes_non_follow_cycle(self) -> None:
        obs.observe_runtime_event("followers_candidate_selected", {"candidate_username": "private-1"})
        rows = obs.observe_runtime_event(
            "followers_candidate_selected", {"candidate_username": "private-2"}
        )
        self.assertTrue(
            any(row.get("source_event") == "next_candidate_safe_boundary" for row in rows)
        )

    def test_target_scan_emits_ct_density_without_new_probe(self) -> None:
        rows = obs.observe_runtime_event(
            "target_scan_completed",
            {"candidates_seen": 10, "candidates_opened": 8, "candidates_rejected": 6},
        )
        self.assertEqual(rows[0]["metric"], "ct_profile_density")
        self.assertEqual(rows[0]["open_density_ratio"], 0.8)

    def test_new_run_clears_prior_candidate_identity(self) -> None:
        obs.observe_runtime_event(
            "followers_candidate_selected",
            {"run_id": "run-1", "candidate_username": "old-candidate"},
        )
        obs.observe_runtime_event("target_scan_started", {"run_id": "run-2", "target_username": "ct-2"})
        rows = obs.observe_runtime_event(
            "target_scan_completed",
            {"run_id": "run-2", "target_username": "ct-2", "candidates_seen": 0},
        )
        self.assertNotIn("candidate_username", rows[0])
        self.assertEqual(rows[0]["source_profile_username"], "ct-2")

    def test_cycle_aggregate_separates_wait_io_xml_and_rpc(self) -> None:
        obs.observe_runtime_event("followers_candidate_selected", {"run_id": "run-1"})
        obs.observe_runtime_event(
            "run_latency_wait_v1",
            {"requested_ms": 100, "actual_ms": 125, "overshoot_ms": 25},
        )
        obs.observe_runtime_event(
            "run_latency_io_timing_v1",
            {"io_kind": "adb", "duration_ms": 4},
        )
        obs.observe_runtime_event(
            "run_latency_io_timing_v1",
            {"io_kind": "uiautomator", "duration_ms": 6},
        )
        obs.observe_runtime_event(
            "run_latency_io_timing_v1",
            {"io_kind": "xml_capture", "duration_ms": 8, "xml_size_bytes": 31},
        )
        obs.observe_runtime_event(
            "run_latency_rpc_timing_v1",
            {"duration_ms": 12},
        )
        rows = obs.observe_runtime_event("post_return_critical_persist_completed", {})
        aggregate = rows[0]
        self.assertEqual(aggregate["wait_overshoot_total_ms"], 25)
        self.assertEqual(aggregate["adb_call_count"], 1)
        self.assertEqual(aggregate["uiautomator_call_count"], 1)
        self.assertEqual(aggregate["xml_capture_count"], 1)
        self.assertEqual(aggregate["xml_size_bytes"], 31)
        self.assertEqual(aggregate["rpc_count"], 1)

    def test_xml_capture_reports_size_without_capturing_payload(self) -> None:
        raw = _FakeDevice()
        events: list[dict] = []
        device = obs.instrument_device(
            raw,
            emit=lambda _level, _event, **fields: events.append(dict(fields)),
        )
        self.assertEqual(device.dump_hierarchy(), "<hierarchy><node /></hierarchy>")
        self.assertEqual(events[0]["io_kind"], "xml_capture")
        self.assertGreater(events[0]["xml_size_bytes"], 0)
        self.assertFalse(events[0]["payload_captured"])

    def test_instrumentation_sink_failure_cannot_change_call_result(self) -> None:
        raw = _FakeDevice()

        def broken_emit(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("telemetry unavailable")

        device = obs.instrument_device(raw, emit=broken_emit)
        self.assertEqual(device.shell("echo ok"), "echo ok")
        self.assertEqual(raw.shell_calls, 1)

    def test_direct_measurements_inherit_only_bounded_context(self) -> None:
        obs.observe_runtime_event(
            "followers_candidate_selected",
            {"run_id": "run-1", "app_instance_id": "app-1", "secret": "hidden"},
        )
        context = obs.current_context()
        self.assertEqual(context["app_instance_id"], "app-1")
        self.assertNotIn("secret", context)

    def test_first_scroll_segments_are_monotonic_non_negative_and_coherent(self) -> None:
        fields = obs.first_scroll_segment_fields(
            {
                "first_scroll_requested_monotonic_ms": 100.0,
                "first_scroll_physical_entry_monotonic_ms": 103.0,
                "first_scroll_physical_complete_monotonic_ms": 111.5,
                "first_scroll_stabilization_complete_monotonic_ms": 561.5,
                "first_scroll_verify_complete_monotonic_ms": 575.0,
            }
        )
        self.assertEqual(fields["first_scroll_segmentation_status"], "COMPLETE")
        self.assertEqual(fields["scroll_request_to_physical_ms"], 3.0)
        self.assertEqual(fields["scroll_physical_envelope_ms"], 8.5)
        self.assertEqual(fields["scroll_stabilization_ms"], 450.0)
        self.assertEqual(fields["scroll_verify_ms"], 13.5)
        segments = [
            fields["scroll_request_to_physical_ms"],
            fields["scroll_physical_envelope_ms"],
            fields["scroll_stabilization_ms"],
            fields["scroll_verify_ms"],
        ]
        self.assertTrue(fields["first_scroll_monotonic_clock"])
        self.assertTrue(all(value >= 0 for value in segments))
        self.assertAlmostEqual(sum(segments), fields["first_scroll_segmented_total_ms"])

    def test_first_scroll_missing_or_invalid_boundary_is_telemetry_safe(self) -> None:
        missing = obs.first_scroll_segment_fields(
            {"first_scroll_requested_monotonic_ms": 100.0}
        )
        self.assertEqual(missing["first_scroll_segmentation_status"], "BOUNDARY_UNAVAILABLE")
        self.assertIsNone(missing["scroll_verify_ms"])
        invalid = obs.first_scroll_segment_fields(
            {
                "first_scroll_requested_monotonic_ms": 100.0,
                "first_scroll_physical_entry_monotonic_ms": 99.0,
                "first_scroll_physical_complete_monotonic_ms": 101.0,
                "first_scroll_stabilization_complete_monotonic_ms": 102.0,
                "first_scroll_verify_complete_monotonic_ms": 103.0,
            }
        )
        self.assertEqual(invalid["first_scroll_segmentation_status"], "BOUNDARY_ORDER_INVALID")
        self.assertIsNone(invalid["scroll_request_to_physical_ms"])

        class _BrokenBoundaries(dict):
            def __getitem__(self, _key: str) -> object:
                raise RuntimeError("telemetry boundary unavailable")

        broken = obs.first_scroll_segment_fields(_BrokenBoundaries())
        self.assertEqual(broken["first_scroll_segmentation_status"], "BOUNDARY_UNAVAILABLE")

    def test_global_ct_first_scroll_timer_remains_available(self) -> None:
        obs.observe_runtime_event("ct_resume_first_scroll_started", {"run_id": "resume-1"})
        rows = obs.observe_runtime_event("ct_resume_first_scroll_completed", {})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phase"], "ct_first_scroll")
        self.assertGreaterEqual(rows[0]["duration_ms"], 0)

    def test_existing_first_scroll_wiring_adds_timestamps_not_device_calls(self) -> None:
        root = Path(__file__).resolve().parents[1]
        navigation = (root / "instagram_navigation.py").read_text(encoding="utf-8")
        start = navigation.index('reason=f"canonical_{attempt_kind}_follow_swipe"')
        end = navigation.index("attempt_diag = {", start)
        physical = navigation[start:end]
        self.assertEqual(physical.count("d.swipe("), 1)
        self.assertEqual(physical.count("time.sleep("), 1)
        self.assertEqual(physical.count("followers_refresh_detect_hierarchy_cache("), 1)
        ordered = [
            physical.index('"first_scroll_physical_entry_monotonic_ms"'),
            physical.index("d.swipe("),
            physical.index('"first_scroll_physical_complete_monotonic_ms"'),
            physical.index("time.sleep("),
            physical.index('"first_scroll_stabilization_complete_monotonic_ms"'),
            physical.index("followers_refresh_detect_hierarchy_cache("),
            physical.index("compare_instagram_list_viewports("),
            physical.index('"first_scroll_verify_complete_monotonic_ms"'),
        ]
        self.assertEqual(ordered, sorted(ordered))

        runner = (root / "runner.py").read_text(encoding="utf-8")
        resume = runner[runner.index('"ct_resume_first_scroll_started"') :]
        resume = resume[: resume.index("_ff_verdict =")]
        self.assertLess(
            resume.index('"first_scroll_requested_monotonic_ms"'),
            resume.index("scroll_followers_list_forward("),
        )
        self.assertLess(
            resume.index("scroll_followers_list_forward("),
            resume.index("first_scroll_segment_fields(_ff_diag)"),
        )




class OutreachTimingTests(unittest.TestCase):
    def setUp(self):
        obs.reset_for_tests()
        self.events=[]
        self.emit=lambda _level,event,**fields:self.events.append((event,fields))

    def device(self):
        class Ui:
            def __init__(self):self.calls=[]
            @property
            def count(self):self.calls.append('count');return 1
            @property
            def info(self):self.calls.append('info');return {'text':'never log this message'}
            def get_text(self):self.calls.append('get_text');return 'private draft'
            def click(self):self.calls.append('click');return 'clicked'
        class Raw:
            def __init__(self):self.ui=Ui();self.calls=[]
            def __call__(self,**kw):return self.ui
            def window_size(self):self.calls.append('window_size');return (1080,2340)
        return Raw()

    def test_opt_in_properties_methods_are_timed_without_extra_reads(self):
        raw=self.device();d=obs.instrument_device(raw,emit=self.emit,observe_properties=True)
        with obs.dm446_span('candidate',identity=dict(run_id='r',job_id='j',account_id='a',visit_index=0),emit=self.emit):
            self.assertEqual(d.window_size(),(1080,2340))
            ui=d(resourceId='private-selector-not-logged')
            self.assertEqual(ui.count,1);self.assertEqual(ui.info,{'text':'never log this message'})
            self.assertEqual(ui.get_text(),'private draft');self.assertEqual(ui.click(),'clicked')
        self.assertEqual(raw.calls,['window_size'])
        self.assertEqual(raw.ui.calls,['count','info','get_text','click'])
        io=[f for e,f in self.events if e=='run_latency_io_timing_v1']
        self.assertEqual([f['operation'] for f in io],['window_size','count','info','get_text','click'])
        self.assertTrue(all(f['job_id']=='j' and f['account_id']=='a' for f in io))
        self.assertEqual(io[-1]['ui_generation'],1)
        self.assertTrue(all(not f['payload_captured'] and not f['extra_phone_call'] for f in io))
        self.assertNotIn('private',str(self.events));self.assertNotIn('never log',str(self.events))

    def test_default_observer_does_not_change_property_or_window_behavior(self):
        raw=self.device();d=obs.instrument_device(raw,emit=self.emit)
        self.assertEqual(d.window_size(),(1080,2340));self.assertEqual(d().count,1)
        self.assertEqual(d().info,{'text':'never log this message'})
        self.assertEqual(self.events,[])

    def test_disabled_observer_does_not_emit_or_wrap(self):
        raw=self.device()
        with patch.dict(os.environ,{'PHONEFARM_RUN_LATENCY_OBSERVABILITY_V1':'0'}):
            self.assertIs(obs.instrument_device(raw,emit=self.emit,observe_properties=True),raw)
            with obs.dm446_span('candidate',identity=dict(run_id='r',job_id='j'),emit=self.emit):
                raw.ui.get_text()
        self.assertEqual(self.events,[]);self.assertEqual(raw.ui.calls,['get_text'])

    def test_nested_context_cannot_leak_job_or_account(self):
        for job,account in (('j1','a1'),('j2','a2')):
            with obs.dm446_span('candidate',identity=dict(run_id='r',job_id=job,account_id=account,visit_index=1,message='never'),emit=self.emit):
                with obs.dm446_span('typing',emit=self.emit):
                    obs.observe_runtime_event('unrelated',{'run_id':'r'})
                    self.assertEqual(obs.current_context()['job_id'],job)
                self.assertEqual(obs.current_context()['callsite'],'candidate')
            self.assertNotIn('job_id',obs.current_context())
        self.assertEqual([f['job_id'] for e,f in self.events],['j1','j1','j2','j2'])
        self.assertNotIn('message',str(self.events))

    def test_sink_failure_does_not_change_results_or_read_counts(self):
        raw=self.device()
        def broken(*a,**k):raise RuntimeError('sink')
        d=obs.instrument_device(raw,emit=broken,observe_properties=True)
        with obs.dm446_span('candidate',identity=dict(run_id='r',job_id='j'),emit=broken):
            self.assertEqual(d().count,1);self.assertEqual(d().get_text(),'private draft')
        self.assertEqual(raw.ui.calls,['count','get_text'])
        self.assertNotIn('job_id',obs.current_context())

    def test_getter_exception_is_not_masked_or_retried(self):
        class Broken:
            count_calls=0
            @property
            def count(self):self.count_calls+=1;raise ValueError('original')
        raw=Broken();d=obs._TimedProxy(raw,kind='uiautomator',methods=frozenset(),emit=self.emit,properties=True)
        with self.assertRaisesRegex(ValueError,'original'):
            with obs.dm446_span('candidate',identity=dict(run_id='r',job_id='j'),emit=self.emit):
                d.count
        self.assertEqual(raw.count_calls,1)
        self.assertFalse(self.events[0][1]['success'])
        self.assertNotIn('job_id',obs.current_context())

    def test_idempotent_wrapping_preserves_context_owner(self):
        raw=self.device();d=obs.instrument_device(raw,observe_properties=True)
        self.assertIs(obs.instrument_device(d,observe_properties=True),d)
        self.assertIs(vars(d)['_raw'],raw)

    def test_existing_wait_is_executed_once_at_the_same_duration(self):
        calls=[]
        with obs.dm446_span('candidate',identity=dict(run_id='r',job_id='j'),emit=self.emit):
            obs.dm446_wait(.08,reason='composer_poll',sleep=calls.append,emit=self.emit)
        self.assertEqual(calls,[.08])
        waits=[f for e,f in self.events if e=='run_latency_wait_v1']
        self.assertEqual(len(waits),1);self.assertEqual(waits[0]['requested_ms'],80)
        self.assertEqual(waits[0]['job_id'],'j')

    def test_unscoped_spans_and_waits_keep_legacy_silence(self):
        calls=[]
        with obs.dm446_span('typing',emit=self.emit):
            obs.dm446_wait(.05,reason='focus',sleep=calls.append,emit=self.emit)
        self.assertEqual(calls,[.05]);self.assertEqual(self.events,[])

    def test_clock_unavailable_does_not_suppress_the_operation(self):
        calls=[]
        with patch.object(obs.time,'monotonic',side_effect=RuntimeError('clock')):
            with obs.dm446_span('candidate',identity=dict(run_id='r'),emit=self.emit):
                result=obs._dm446_io(lambda:(calls.append('once') or 7),'get_text',self.emit)
        self.assertEqual(calls,['once']);self.assertEqual(result,7);self.assertEqual(self.events,[])


if __name__ == "__main__":
    unittest.main()
