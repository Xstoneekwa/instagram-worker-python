from __future__ import annotations

import time
import unittest
from pathlib import Path

import run_latency_observability_v1 as obs


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


if __name__ == "__main__":
    unittest.main()
