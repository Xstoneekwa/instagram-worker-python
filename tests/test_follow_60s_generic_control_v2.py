from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest import mock

import follow_60s_canary as canary
import runner
from follow_60s_canary_binding_v2 import (
    validate_armed_control,
    validate_consumer_binding,
)
from tests.follow60_generic_fixtures import (
    TEST_BUSINESS_SESSION_ID,
    TEST_CANARY_ACCOUNT_ID,
    TEST_CANARY_USERNAME,
    TEST_OTHER_ACCOUNT_ID,
    TEST_REQUEST_ID,
    TEST_WORKER_SHA,
    bound_control,
    configure_canary,
)


class Follow60GenericControlV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        canary._RUNTIME = canary._Runtime()

    def tearDown(self) -> None:
        canary._RUNTIME = canary._Runtime()

    def _configure(self, *, account_id=TEST_CANARY_ACCOUNT_ID, username=TEST_CANARY_USERNAME, control=None):
        if control is None:
            control = bound_control(account_id=account_id, expected_username=username)
        with mock.patch.dict(os.environ, {"FOLLOW_60S_CANARY_ENABLED": "1"}, clear=False):
            return configure_canary(
                canary,
                account_id=account_id,
                account_username=username,
                run_id="run-1",
                package="com.instagram.android",
                resume_policy={},
                control=control,
                request_id=TEST_REQUEST_ID,
                business_session_id=TEST_BUSINESS_SESSION_ID,
            )

    def test_any_account_with_exact_control_can_enable(self):
        self.assertTrue(self._configure())
        self.assertTrue(canary.enabled_for_account(TEST_CANARY_ACCOUNT_ID))
        self.assertFalse(canary.enabled_for_account(TEST_OTHER_ACCOUNT_ID))

    def test_second_generic_account_can_enable_with_its_own_control(self):
        control = bound_control(
            account_id=TEST_OTHER_ACCOUNT_ID,
            expected_username="second_generic_account",
        )
        self.assertTrue(
            self._configure(
                account_id=TEST_OTHER_ACCOUNT_ID,
                username="second_generic_account",
                control=control,
            )
        )

    def test_absent_control_is_golden(self):
        self.assertFalse(self._configure(control={}))
        self.assertFalse(canary.enabled_for_account(TEST_CANARY_ACCOUNT_ID))

    def test_wrong_worker_sha_is_golden(self):
        control = bound_control(worker_sha="b" * 40)
        self.assertFalse(self._configure(control=control))

    def test_baseline_from_other_account_is_golden(self):
        control = bound_control(baseline_account_id=TEST_OTHER_ACCOUNT_ID)
        self.assertFalse(self._configure(control=control))

    def test_incomplete_or_cross_package_baseline_is_golden(self):
        missing_timestamp = bound_control()
        missing_timestamp["metadata_safe"]["baseline_captured_at"] = ""
        wrong_package = bound_control()
        wrong_package["metadata_safe"]["baseline_package"] = "other.package"
        warmup_not_ready = bound_control()
        warmup_not_ready["metadata_safe"]["baseline_warmup_ready"] = False
        for control in (missing_timestamp, wrong_package, warmup_not_ready):
            canary._RUNTIME = canary._Runtime()
            self.assertFalse(self._configure(control=control))

    def test_username_mismatch_is_golden(self):
        control = bound_control(expected_username="different_account")
        self.assertFalse(self._configure(control=control))

    def test_multiple_active_controls_are_golden(self):
        control = bound_control(active_control_count=2)
        self.assertFalse(self._configure(control=control))

    def test_expired_revoked_completed_waiting_and_disabled_are_golden(self):
        cases = (
            bound_control(expires_delta_s=-1),
            bound_control(revoked_at="2026-08-01T00:00:00+00:00"),
            bound_control(completed_at="2026-08-01T00:00:00+00:00"),
            bound_control(status="waiting"),
            bound_control(status="disabled"),
        )
        for control in cases:
            with self.subTest(status=control.get("status"), metadata=control["metadata_safe"]):
                canary._RUNTIME = canary._Runtime()
                self.assertFalse(self._configure(control=control))

    def test_exhausted_ten_cycle_control_is_golden(self):
        control = bound_control(current_new_cycle_count=10, max_new_cycles=10)
        verdict = validate_armed_control(
            control,
            account_id=TEST_CANARY_ACCOUNT_ID,
            account_username=TEST_CANARY_USERNAME,
            active_worker_sha=TEST_WORKER_SHA,
            run_type="account_session",
            package="com.instagram.android",
        )
        self.assertFalse(verdict.valid)
        self.assertEqual(verdict.reason, "cycle_counter_exhausted")

    def test_ten_cycle_barrier_is_generic_and_requires_complete_cycle(self):
        due = runner._follow60_evaluation_barrier_due
        self.assertTrue(
            due(
                canary_active=True,
                stage_receipts_enabled=True,
                cycle_complete=True,
                completed_new_cycles=10,
                max_new_cycles=10,
            )
        )
        for override in (
            {"canary_active": False},
            {"stage_receipts_enabled": False},
            {"cycle_complete": False},
            {"completed_new_cycles": 9},
        ):
            values = {
                "canary_active": True,
                "stage_receipts_enabled": True,
                "cycle_complete": True,
                "completed_new_cycles": 10,
                "max_new_cycles": 10,
                **override,
            }
            with self.subTest(**override):
                self.assertFalse(due(**values))

    def test_bound_consumer_rejects_other_account_and_expiry(self):
        control = bound_control()
        good = validate_consumer_binding(
            control,
            account_id=TEST_CANARY_ACCOUNT_ID,
            active_worker_sha=TEST_WORKER_SHA,
            run_id="run-1",
            request_id=TEST_REQUEST_ID,
        )
        self.assertTrue(good.valid)
        wrong_account = validate_consumer_binding(
            control,
            account_id=TEST_OTHER_ACCOUNT_ID,
            active_worker_sha=TEST_WORKER_SHA,
            run_id="run-1",
            request_id=TEST_REQUEST_ID,
        )
        self.assertFalse(wrong_account.valid)
        expired = validate_consumer_binding(
            control,
            account_id=TEST_CANARY_ACCOUNT_ID,
            active_worker_sha=TEST_WORKER_SHA,
            run_id="run-1",
            request_id=TEST_REQUEST_ID,
            now=datetime(2100, 1, 1, tzinfo=timezone.utc),
        )
        self.assertFalse(expired.valid)
        self.assertEqual(expired.reason, "control_expired")

    def test_waiting_control_cannot_activate_but_bound_consumer_can_finalize(self):
        control = bound_control(status="waiting_operator_evaluation")
        self.assertFalse(self._configure(control=control))
        terminal = validate_consumer_binding(
            control,
            account_id=TEST_CANARY_ACCOUNT_ID,
            active_worker_sha=TEST_WORKER_SHA,
            run_id="run-1",
            request_id=TEST_REQUEST_ID,
        )
        self.assertTrue(terminal.valid)

    def test_fast_path_and_receipts_are_account_scoped_by_runtime(self):
        self.assertTrue(self._configure())
        self.assertTrue(canary.enabled_for_account(TEST_CANARY_ACCOUNT_ID))
        self.assertFalse(canary.enabled_for_account(TEST_OTHER_ACCOUNT_ID))
        self.assertTrue(canary.enabled("like_fresh_cell_bounds"))
        self.assertTrue(canary.enabled("return_candidate_handoff"))

    def test_activation_components_are_not_ready_until_concrete_handlers_installed(self):
        self.assertTrue(self._configure())
        before = canary.activation_component_status()
        self.assertFalse(before["opening_composite_active"])
        self.assertFalse(before["stage_receipts_installed"])

        def handler(*_args, **_kwargs):
            return True

        installed = canary.install_activation_components(
            opening_composite=handler,
            pre_tap_callback=handler,
            post_cycle_callback=handler,
            stage_receipts=handler,
            barrier=handler,
        )
        self.assertTrue(all(installed.values()))
        self.assertTrue(all(canary.activation_component_status().values()))

    def test_activation_components_fail_closed_on_non_callable_handler(self):
        self.assertTrue(self._configure())
        installed = canary.install_activation_components(
            opening_composite=lambda: True,
            pre_tap_callback=lambda: True,
            post_cycle_callback=lambda: True,
            stage_receipts=None,
            barrier=lambda: True,
        )
        self.assertFalse(installed["stage_receipts"])
        self.assertFalse(canary.activation_component_status()["stage_receipts_installed"])


if __name__ == "__main__":
    unittest.main()
